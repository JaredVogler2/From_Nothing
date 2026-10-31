"""Atomic source runs and immutable planning snapshots (S05 ATOMIC ACQUISITION, S06, P04).

1. Create a CAPTURING source run with scope, cutoff and idempotency key.
2. Capture every dataset privately, preserving raw types, clocks and keys.
3. Compute per-dataset canonical content/schema/query hashes and count receipts.
4. Read every dataset back (detached) and verify run lineage and hashes. A
   dataset from another run, or a forged hash, fails admission.
5. Admit the coherent snapshot; the census must reconcile. A complete-empty
   dataset needs an affirmative receipt.
6. Mark COMPLETE, then move the scoped source head LAST with compare-and-set.
   A failed or rejected run never moves the head.
7. Replaying the same idempotency key reconciles the existing run (e.g. a
   crash before the head update) instead of duplicating facts.

The head only moves forward in cutoff time. An older run completing late is
retained as history, never promoted over a newer one.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

from ..codec import CodecError, content_hash, from_jsonable, open_envelope, parse_utc, strict_loads
from ..contracts.planning import PlanningSnapshot
from ..domain import aware_utc
from .datasets import DATASETS
from .fixture_reader import Capture, CaptureError, FixtureSourceReader, LiveSourceReader
from .snapshot_builder import AdmissionError, build_snapshot
from .sourcetime import policy_from_receipts
from .store import StoreError

UTC = timezone.utc
RUNS = "source_runs"
DATASETS_COLLECTION = "datasets"
SNAPSHOTS = "snapshots"
MANIFESTS = "manifests"


def source_head_name(scope: str) -> str:
    return f"source/{scope}"


@dataclass(frozen=True)
class DatasetReceipt:
    name: str
    dataset_id: str
    status: str
    row_count: int
    query_sha: str
    schema_sha: str
    content_sha: str
    captured_at: datetime
    required: bool
    producer_receipt: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class SourceRunRecord:
    id: str
    scope: str
    idempotency_key: str
    mode: str
    status: str
    cutoff: datetime
    capture_start: datetime
    capture_end: datetime | None
    datasets: tuple[DatasetReceipt, ...]
    manifest_sha: str | None
    snapshot_id: str | None
    snapshot_hash: str | None
    rejection: dict[str, Any] | None
    policy_hash: str
    statistics_ids: tuple[str, ...]


@dataclass(frozen=True)
class DatasetPayload:
    id: str
    run_id: str
    name: str
    query_hash: str
    schema_hash: str
    content_hash: str
    captured_at: datetime
    upstream_revision: str | None
    raw_timezone: str | None
    row_count: int
    status: str
    complete_receipt: dict[str, Any]
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AcquisitionOutcome:
    run_id: str
    status: str
    replayed: bool
    snapshot_id: str | None
    head_moved: bool
    head_version: int | None
    rejection: dict[str, Any] | None
    datasets: dict[str, str]
    notes: tuple[str, ...]


def _now(services: Any) -> datetime:
    return services.clock.now()


def get_run(repos: Any, run_id: str) -> tuple[SourceRunRecord, int] | None:
    rec = repos.store.get_state(RUNS, run_id)
    if rec is None:
        return None
    return from_jsonable(SourceRunRecord, strict_loads(rec.data)), rec.version


def _save_run(repos: Any, run: SourceRunRecord, expected_version: int) -> int:
    from ..codec import canonical_dumps

    return repos.store.put_state(RUNS, run.id, canonical_dumps(run), expected_version).version


def acquire(services: Any, reader: Any, *, cutoff: datetime, idempotency_key: str,
            fault: Callable[[str], None] | None = None) -> AcquisitionOutcome:
    fault = fault or (lambda point: None)
    repos = services.repositories
    settings = services.settings
    policy = services.policy
    scope = settings.scope_key
    cutoff = aware_utc(cutoff, "cutoff")
    with repos.store.lock(f"acquire-{scope}", ttl_seconds=600, wait_seconds=120):
        existing_id = repos.store.idempotency_get("source_idempotency", f"{scope}|{idempotency_key}")
        if existing_id:
            found = get_run(repos, existing_id)
            if found is None:
                raise StoreError("IDEMPOTENCY_ORPHAN", "idempotency key bound to a missing source run")
            run, version = found
            if run.cutoff != cutoff:
                raise StoreError("IDEMPOTENCY_CONFLICT", "idempotency key reused with a different cutoff")
            if run.status == "COMPLETE":
                moved, head_version = _promote(repos, run, fault)
                return AcquisitionOutcome(run.id, run.status, True, run.snapshot_id, moved, head_version, None,
                                          {d.name: d.status for d in run.datasets}, ("replay reconciled existing COMPLETE run",))
            if run.status == "REJECTED":
                return AcquisitionOutcome(run.id, run.status, True, None, False, None, run.rejection,
                                          {d.name: d.status for d in run.datasets}, ("replay of a rejected run; use a new idempotency key",))
            replayed = True
        else:
            now = _now(services)
            run = SourceRunRecord(
                id="SR-" + uuid.uuid4().hex[:20], scope=scope, idempotency_key=idempotency_key, mode=settings.mode, status="CAPTURING",
                cutoff=cutoff, capture_start=now, capture_end=None, datasets=(), manifest_sha=None, snapshot_id=None, snapshot_hash=None,
                rejection=None, policy_hash=policy.hash, statistics_ids=(),
            )
            bound = repos.store.idempotency_put("source_idempotency", f"{scope}|{idempotency_key}", run.id)
            if bound != run.id:
                return acquire(services, reader, cutoff=cutoff, idempotency_key=idempotency_key, fault=fault)
            version = _save_run(repos, run, 0)
            replayed = False
        captured = {d.name for d in run.datasets}
        tz_map = dict(settings.source_timezone_map)
        # fixture captures filter naive load timestamps at the cutoff in the fixture producer's zone
        reader_tz = tz_map if tz_map else ({"*": "America/New_York"} if settings.mode == "fixture" else {})
        for name, spec in DATASETS.items():
            if name in captured:
                continue
            try:
                capture = reader.capture(spec, cutoff, reader_tz, _now(services))
            except (CaptureError, CodecError, OSError, ValueError) as exc:
                code = getattr(exc, "code", type(exc).__name__)
                message = getattr(exc, "message", str(exc))
                if spec.required_for_run:
                    run = replace(run, status="REJECTED", capture_end=_now(services),
                                  rejection={"code": "MANDATORY_DATASET_FAILED", "dataset": name, "detail": f"{code}: {message}"})
                    _save_run(repos, run, version)
                    return AcquisitionOutcome(run.id, "REJECTED", replayed, None, False, None, run.rejection,
                                              {d.name: d.status for d in run.datasets} | {name: "FAILED"}, ())
                receipt = DatasetReceipt(name, f"{run.id}.{name}", "FAILED", 0, "", "", "", _now(services), False, {}, f"{code}: {message}")
                run = replace(run, datasets=run.datasets + (receipt,))
                version = _save_run(repos, run, version)
                continue
            dataset_id = f"{run.id}.{name}"
            payload = DatasetPayload(
                id=dataset_id, run_id=run.id, name=name, query_hash=capture.query_sha, schema_hash=capture.schema_sha,
                content_hash=capture.content_sha, captured_at=capture.captured_at,
                upstream_revision=str(capture.receipt.get("upstream_revision")) if capture.receipt.get("upstream_revision") else None,
                raw_timezone=str(capture.receipt.get("source_timezone")) if capture.receipt.get("source_timezone") else None,
                row_count=len(capture.rows), status=capture.status, complete_receipt=dict(capture.receipt), columns=capture.columns, rows=capture.rows,
            )
            repos.put_record(DATASETS_COLLECTION, "SourceDataset", dataset_id, payload, source_run_id=run.id, policy_hash=policy.hash)
            receipt = DatasetReceipt(name, dataset_id, capture.status, len(capture.rows), capture.query_sha, capture.schema_sha,
                                     capture.content_sha, capture.captured_at, spec.required_for_run, dict(capture.receipt), None)
            run = replace(run, datasets=run.datasets + (receipt,))
            version = _save_run(repos, run, version)
            fault(f"captured:{name}")
        # ---- detached readback + admission ----
        try:
            datasets, lineage, statuses, receipts = _readback(repos, run)
            tzp = policy_from_receipts(receipts, tz_map, settings.mode)
            manifest = {
                "run_id": run.id, "scope": scope, "cutoff": run.cutoff, "policy_hash": policy.hash,
                "datasets": {d.name: {"content_sha": d.content_sha, "schema_sha": d.schema_sha, "query_sha": d.query_sha,
                                      "rows": d.row_count, "status": d.status} for d in run.datasets},
            }
            manifest_sha = content_hash(manifest)
            build = build_snapshot(run_id=run.id, scope_key=scope, site=settings.site, program=settings.program, mode=settings.mode,
                                   cutoff=run.cutoff, created_at=_now(services), datasets=datasets, dataset_status=statuses,
                                   dataset_ids={d.name: d.dataset_id for d in run.datasets}, dataset_lineage=lineage,
                                   manifest_hash=manifest_sha, tzp=tzp, policy=policy,
                                   dataset_queries={d.name: d.query_sha for d in run.datasets}, dataset_receipts=receipts,
                                   dataset_captured={d.name: d.captured_at for d in run.datasets if d.status != "FAILED"})
        except AdmissionError as exc:
            run = replace(run, status="REJECTED", capture_end=_now(services), rejection={"code": exc.code, "detail": exc.message})
            _save_run(repos, run, version)
            return AcquisitionOutcome(run.id, "REJECTED", replayed, None, False, None, run.rejection, {d.name: d.status for d in run.datasets}, ())
        snapshot = build.snapshot
        repos.put_record(MANIFESTS, "SourceManifest", run.id, manifest, source_run_id=run.id, policy_hash=policy.hash)
        repos.put_record(SNAPSHOTS, "PlanningSnapshot", snapshot.id, snapshot, source_run_id=run.id, policy_hash=policy.hash)
        for stat in build.statistics:
            repos.put_record("statistics", "DurationStatistics", stat.id, stat, source_run_id=run.id, policy_hash=policy.hash)
        fault("before_complete")
        run = replace(run, status="COMPLETE", capture_end=_now(services), manifest_sha=manifest_sha, snapshot_id=snapshot.id,
                      snapshot_hash=snapshot.hash, statistics_ids=snapshot.statistics_ids)
        version = _save_run(repos, run, version)
        moved, head_version = _promote(repos, run, fault)
        return AcquisitionOutcome(run.id, "COMPLETE", replayed, snapshot.id, moved, head_version, None,
                                  {d.name: d.status for d in run.datasets}, tuple(build.notes))


def _promote(repos: Any, run: SourceRunRecord, fault: Callable[[str], None]) -> tuple[bool, int | None]:
    """Move the scoped head LAST and only forward in cutoff time."""
    if run.status != "COMPLETE":
        raise StoreError("HEAD_REQUIRES_COMPLETE", "only an admitted COMPLETE run can become the source head")
    name = source_head_name(run.scope)
    head = repos.head(name)
    if head is not None:
        if head.record_id == run.id:
            return False, head.version
        current = get_run(repos, head.record_id)
        if current is not None and current[0].cutoff > run.cutoff:
            return False, head.version
    fault("before_head")
    moved = repos.move_head(name, run.id, head.version if head else 0)
    fault("after_head")
    return True, moved.version


def _readback(repos: Any, run: SourceRunRecord) -> tuple[dict[str, tuple[dict[str, Any], ...]], dict[str, str], dict[str, str], dict[str, dict[str, Any]]]:
    datasets: dict[str, tuple[dict[str, Any], ...]] = {}
    lineage: dict[str, str] = {}
    statuses: dict[str, str] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for receipt in run.datasets:
        statuses[receipt.name] = receipt.status
        if receipt.status == "FAILED":
            continue
        raw = repos.get_raw(DATASETS_COLLECTION, receipt.dataset_id)
        if raw is None:
            raise AdmissionError("DATASET_READBACK_MISSING", f"{receipt.name} was not durably stored")
        try:
            env = open_envelope(raw, "SourceDataset")
            payload = from_jsonable(DatasetPayload, env.payload)
        except CodecError as exc:
            raise AdmissionError("DATASET_READBACK_INVALID", f"{receipt.name}: {exc.message}") from None
        if env.source_run_id != run.id or payload.run_id != run.id:
            raise AdmissionError("DATASET_FOREIGN_RUN", f"{receipt.name} belongs to run {payload.run_id}, not {run.id}")
        recomputed = content_hash({"columns": list(payload.columns), "rows": list(payload.rows)})
        if recomputed != payload.content_hash or recomputed != receipt.content_sha:
            raise AdmissionError("DATASET_HASH_MISMATCH", f"{receipt.name}: recomputed content hash does not match the receipt")
        if receipt.name == "cs_windows":
            declared = payload.complete_receipt.get("active_load_ids")
            if isinstance(declared, dict):
                for row in payload.rows:
                    want = declared.get(str(row["LINE_NUMBER"]))
                    if want is not None and int(want) != int(row["LOAD_ID"]):
                        raise AdmissionError("CS_GENERATION_MISMATCH", f"CS row for line {row['LINE_NUMBER']} has load {row['LOAD_ID']}, producer declared {want}")
        datasets[receipt.name] = tuple(payload.rows)
        lineage[receipt.name] = recomputed
        receipts[receipt.name] = dict(payload.complete_receipt)
    return datasets, lineage, statuses, receipts


# ---------------------------------------------------------------------------
# readers used by later stages
# ---------------------------------------------------------------------------

def load_run_datasets(repos: Any, run_id: str) -> tuple[dict[str, tuple[dict[str, Any], ...]], SourceRunRecord | None]:
    found = get_run(repos, run_id)
    if found is None:
        return {}, None
    run, _ = found
    datasets, _, _, _ = _readback(repos, run)
    return datasets, run


def load_snapshot(repos: Any, snapshot_id: str) -> PlanningSnapshot | None:
    raw = repos.get_raw(SNAPSHOTS, snapshot_id)
    if raw is None:
        return None
    env = open_envelope(raw, "PlanningSnapshot")
    snap = from_jsonable(PlanningSnapshot, env.payload)
    snap.verify()
    return snap


def snapshot_for_run(services: Any, run_id: str, policy: Any = None, *, store: bool = True) -> PlanningSnapshot:
    """The snapshot of an admitted run for ``policy`` (default: active policy).

    The run's own snapshot is loaded when the policy matches. A different policy
    (e.g. a P90 toggle) rebuilds a new snapshot deterministically from the same
    immutable datasets and stores it under its own id; the original is unchanged.
    ``store=False`` (isolated scenarios) rebuilds without writing anything: the
    caller keeps the copy in its own namespace.
    """
    repos = services.repositories
    policy = policy or services.policy
    found = get_run(repos, run_id)
    if found is None:
        raise StoreError("SOURCE_RUN_NOT_FOUND", f"source run {run_id} not found")
    run, _ = found
    if run.status != "COMPLETE":
        raise StoreError("SOURCE_RUN_NOT_COMPLETE", f"source run {run_id} is {run.status}")
    if run.snapshot_id and run.policy_hash == policy.hash:
        snap = load_snapshot(repos, run.snapshot_id)
        if snap is not None:
            return snap
    datasets, lineage, statuses, receipts = _readback(repos, run)
    tzp = policy_from_receipts(receipts, dict(services.settings.source_timezone_map), run.mode)
    manifest_sha = run.manifest_sha or ""
    build = build_snapshot(run_id=run.id, scope_key=run.scope, site=services.settings.site, program=services.settings.program, mode=run.mode,
                           cutoff=run.cutoff, created_at=run.capture_end or run.cutoff, datasets=datasets, dataset_status=statuses,
                           dataset_ids={d.name: d.dataset_id for d in run.datasets}, dataset_lineage=lineage, manifest_hash=manifest_sha,
                           tzp=tzp, policy=policy, dataset_queries={d.name: d.query_sha for d in run.datasets}, dataset_receipts=receipts,
                           dataset_captured={d.name: d.captured_at for d in run.datasets if d.status != "FAILED"})
    snap = build.snapshot
    if not store:
        return snap
    if repos.get_raw(SNAPSHOTS, snap.id) is None:
        repos.put_record(SNAPSHOTS, "PlanningSnapshot", snap.id, snap, source_run_id=run.id, policy_hash=policy.hash)
    for stat in build.statistics:
        if repos.get_raw("statistics", stat.id) is None:
            repos.put_record("statistics", "DurationStatistics", stat.id, stat, source_run_id=run.id, policy_hash=policy.hash)
    return snap


def current_source(services: Any) -> tuple[SourceRunRecord | None, PlanningSnapshot | None]:
    repos = services.repositories
    if not repos.available:
        return None, None
    head = repos.head(source_head_name(services.settings.scope_key))
    if head is None:
        return None, None
    found = get_run(repos, head.record_id)
    if found is None:
        return None, None
    run = found[0]
    snap = load_snapshot(repos, run.snapshot_id) if run.snapshot_id else None
    return run, snap


def make_reader(services: Any, scenario: str | None = None) -> Any:
    settings = services.settings
    if settings.mode == "fixture":
        return FixtureSourceReader(settings.fixture_inputs_dir / (scenario or settings.scenario_id))
    from .sql import SqlReader

    return LiveSourceReader(settings.app_root, SqlReader(settings), settings.scope_key)


def cli_acquire(services: Any, args: Any) -> dict[str, Any]:
    from ..clock import FIXTURE_CUTOFF

    if args.cutoff:
        cutoff = parse_utc(args.cutoff)
    elif services.settings.mode == "fixture":
        cutoff = FIXTURE_CUTOFF
    else:
        cutoff = services.clock.now()
    key = args.idempotency_key or f"acquire-{cutoff.strftime('%Y%m%dT%H%M%SZ')}"
    outcome = acquire(services, make_reader(services, args.scenario), cutoff=cutoff, idempotency_key=key)
    status = "ok" if outcome.status == "COMPLETE" else "error"
    return {
        "status": status,
        "source_run_id": outcome.run_id,
        "run_status": outcome.status,
        "replayed": outcome.replayed,
        "snapshot_id": outcome.snapshot_id,
        "head_moved": outcome.head_moved,
        "head_version": outcome.head_version,
        "rejection": outcome.rejection,
        "datasets": outcome.datasets,
        "notes": list(outcome.notes)[:50],
    }
