"""Display package storage: content-addressed parts, manifests, the signed plan receipt, and the verified reader.

**Writing** happens in the pipeline step that already holds the whole object:

* ``write_snapshot_display`` at acquisition, before the source head moves (the source role);
* ``write_plan_display`` and ``sign_plan_display`` in ``publication.service.publish``, after the readback validation and
  before the plan head moves (the signer role).

Parts are stored once under the SHA-256 of their canonical JSON. Manifests and receipts are keyed by the snapshot or
plan id, written once, and a retry must reproduce them exactly.

**Reading** never loads a snapshot or a plan artifact:

* a snapshot manifest must name the source run's snapshot id and hash;
* a plan's acceptance receipt must verify with the trusted key, and its display receipt must verify under the display
  domain and agree with the acceptance on plan, payload hash, scope, purpose, snapshot and policy; the manifest must
  hash to the signed value;
* every part must hash to the value its manifest names.

Any failure makes nothing current and says why. Verified parts are kept in a bounded LRU keyed by their hash, sized by
their canonical bytes (``FOCUS_DISPLAY_CACHE_MB``, default 192).
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from ..codec import CodecError, canonical_dumps, content_hash, from_jsonable, open_envelope, seal_bytes, sha256_hex
from ..data.store import ImmutableConflict
from .build import plan_parts, snapshot_parts
from .records import BUCKETS, BUILDER, DISPLAY_SCHEMA, PART_TYPES, CandidateDigest, DisplayManifest, DisplayReceipt, PartRef

PARTS = "display_parts"
MANIFESTS = "display_manifests"
RECEIPTS = "display_receipts"
DIGESTS = "candidate_digests"


class DisplayError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------- writing

def _put_part(repos: Any, sha: str, kind: str, payload: bytes, created_at: datetime) -> int:
    raw = repos.get_raw(PARTS, sha)
    if raw is not None:
        return len(raw)                                     # content-addressed: the same hash is the same part
    data = seal_bytes(kind, sha, payload, created_at=created_at)
    try:
        repos.store.put_immutable(PARTS, sha, data)
    except ImmutableConflict:
        pass                                                # another writer stored the same part first
    return len(data)


def write_parts(repos: Any, parts: Iterable[tuple[str, Any]], created_at: datetime) -> tuple[PartRef, ...]:
    """Store each part once under its content hash, as it is built; only the references are kept. Each part is encoded
    once: its canonical bytes give both its hash and the envelope's payload (``codec.seal_bytes``)."""
    refs = []
    for name, record in parts:
        payload = canonical_dumps(record)
        sha = sha256_hex(payload)
        kind = type(record).__name__
        refs.append(PartRef(name, sha, kind, _put_part(repos, sha, kind, payload, created_at)))
        del payload
    return tuple(refs)


def _put_once(repos: Any, collection: str, record_id: str, type_name: str, cls: type, record: Any, created_at: datetime, **meta: Any) -> None:
    """Write a manifest or receipt once; a retry must reproduce exactly the same content."""
    raw = repos.get_raw(collection, record_id)
    if raw is not None:
        existing = from_jsonable(cls, open_envelope(raw, type_name).payload)
        if content_hash(existing) != content_hash(record):
            raise DisplayError("DISPLAY_CONFLICT", f"{type_name} {record_id} already holds different content")
        return
    try:
        repos.put_record(collection, type_name, record_id, record, created_at=created_at, **meta)
    except ImmutableConflict:
        _put_once(repos, collection, record_id, type_name, cls, record, created_at, **meta)


def write_snapshot_display(repos: Any, snapshot: Any) -> DisplayManifest:
    """Build and store an admitted snapshot's parts and manifest (idempotent)."""
    raw = repos.get_raw(MANIFESTS, snapshot.id)
    if raw is not None:
        existing = from_jsonable(DisplayManifest, open_envelope(raw, "DisplayManifest").payload)
        if existing.subject == "SNAPSHOT" and existing.snapshot_hash == snapshot.hash and existing.builder == BUILDER:
            return existing
    refs = write_parts(repos, snapshot_parts(snapshot), snapshot.created_at)
    manifest = DisplayManifest(DISPLAY_SCHEMA, "SNAPSHOT", snapshot.id, snapshot.id, snapshot.hash, snapshot.policy_hash, snapshot.source_run_id,
                               None, None, BUCKETS, BUILDER, refs)
    _put_once(repos, MANIFESTS, snapshot.id, "DisplayManifest", DisplayManifest, manifest, snapshot.created_at,
              source_run_id=snapshot.source_run_id, policy_hash=snapshot.policy_hash)
    return manifest


def write_plan_display(repos: Any, snapshot: Any, artifact: Any, plan_id: str, payload_sha256: str, created_at: datetime) -> tuple[DisplayManifest, str]:
    """Build and store an accepted plan's parts and manifest; returns the manifest and the hash the signer signs."""
    refs = write_parts(repos, plan_parts(snapshot, artifact, plan_id), created_at)
    manifest = DisplayManifest(DISPLAY_SCHEMA, "PLAN", plan_id, artifact.snapshot_id, artifact.snapshot_hash, artifact.policy_hash,
                               artifact.source_run_id, artifact.purpose.value, payload_sha256, BUCKETS, BUILDER, refs)
    _put_once(repos, MANIFESTS, plan_id, "DisplayManifest", DisplayManifest, manifest, created_at, source_run_id=artifact.source_run_id,
              policy_hash=artifact.policy_hash, purpose=artifact.purpose.value)
    return manifest, content_hash(manifest)


def sign_plan_display(signer: Any, *, plan_id: str, manifest_sha256: str, payload_sha256: str, scope: str, purpose: Any, snapshot_hash: str,
                      policy_hash: str) -> DisplayReceipt:
    signature = signer.sign_display(scope, purpose.value, plan_id, payload_sha256, snapshot_hash, policy_hash, manifest_sha256)
    return DisplayReceipt(plan_id, manifest_sha256, payload_sha256, scope, purpose, snapshot_hash, policy_hash, signer.key_id, signature,
                          bool(signer.test_key))


def put_display_receipt(repos: Any, receipt: DisplayReceipt, created_at: datetime) -> None:
    _put_once(repos, RECEIPTS, receipt.plan_id, "DisplayReceipt", DisplayReceipt, receipt, created_at, policy_hash=receipt.policy_hash,
              purpose=receipt.purpose.value)


def publish_plan_display(repos: Any, signer: Any, snapshot: Any, artifact: Any, plan_id: str, payload_sha256: str,
                         created_at: datetime) -> DisplayReceipt:
    """Parts, manifest and the signed receipt of one accepted plan, in that order (called before the head moves)."""
    _, manifest_sha = write_plan_display(repos, snapshot, artifact, plan_id, payload_sha256, created_at)
    receipt = sign_plan_display(signer, plan_id=plan_id, manifest_sha256=manifest_sha, payload_sha256=payload_sha256, scope=artifact.scope,
                                purpose=artifact.purpose, snapshot_hash=artifact.snapshot_hash, policy_hash=artifact.policy_hash)
    put_display_receipt(repos, receipt, created_at)
    return receipt


def candidate_digest(plan: Any) -> CandidateDigest:
    from collections import Counter

    return CandidateDigest(plan.id, plan.purpose, plan.created_at, plan.snapshot_id, plan.source_run_id, len(plan.assignments), len(plan.duties),
                           dict(sorted(Counter(o.disposition.value for o in plan.job_dispositions).items())),
                           dict(sorted(Counter(r.outcome for r in plan.commitment_results).items())), plan.algorithm_status,
                           plan.global_optimality_proven, plan.costs)


def write_candidate_digest(repos: Any, plan: Any) -> None:
    """The scheduler's small digest of a stored candidate, so a plan view never loads the candidate itself (idempotent)."""
    if repos.get_raw(DIGESTS, plan.id) is None:
        _put_once(repos, DIGESTS, plan.id, "CandidateDigest", CandidateDigest, candidate_digest(plan), plan.created_at,
                  source_run_id=plan.source_run_id, policy_hash=plan.policy_hash, purpose=plan.purpose.value)


def has_plan_display(repos: Any, plan_id: str) -> bool:
    return repos.get_raw(RECEIPTS, plan_id) is not None and repos.get_raw(MANIFESTS, plan_id) is not None


# ---------------------------------------------------------------------- reading

def _cache_bytes() -> int:
    try:
        mb = int(os.environ.get("FOCUS_DISPLAY_CACHE_MB", "192"))
    except ValueError:
        mb = 192
    return max(8, mb) * 1_000_000


class PartCache:
    """LRU of verified parts by content hash, bounded by their stored bytes."""

    def __init__(self, budget: int):
        self.budget = budget
        self.size = 0
        self._items: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._lock = threading.Lock()
        self.loads = 0

    def get(self, sha: str) -> Any:
        with self._lock:
            item = self._items.get(sha)
            if item is None:
                return None
            self._items.move_to_end(sha)
            return item[0]

    def put(self, sha: str, record: Any, size: int) -> None:
        with self._lock:
            if sha in self._items:
                return
            self._items[sha] = (record, size)
            self.size += size
            while self.size > self.budget and len(self._items) > 1:
                _, (_, freed) = self._items.popitem(last=False)
                self.size -= freed

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.size = 0


PART_CACHE = PartCache(_cache_bytes())


def load_part(repos: Any, ref: PartRef) -> Any:
    """One verified part: the stored envelope, its content hash against the manifest, then a strict typed decode."""
    found = PART_CACHE.get(ref.sha256)
    if found is not None:
        return found
    cls = PART_TYPES.get(ref.kind)
    if cls is None:
        raise DisplayError("PART_TYPE_UNKNOWN", f"part {ref.name} has unknown type {ref.kind}")
    raw = repos.get_raw(PARTS, ref.sha256)
    if raw is None:
        raise DisplayError("PART_MISSING", f"part {ref.name} ({ref.sha256[:12]}) is not stored")
    try:
        env = open_envelope(raw, ref.kind)
    except CodecError as exc:
        raise DisplayError("PART_UNREADABLE", f"part {ref.name}: {exc.code}") from None
    if env.id != ref.sha256 or content_hash(env.payload) != ref.sha256:
        raise DisplayError("PART_HASH_MISMATCH", f"part {ref.name} does not hash to its manifest entry")
    try:
        record = from_jsonable(cls, env.payload, f"$<{ref.kind}:{ref.name}>")
    except (CodecError, ValueError, TypeError) as exc:
        raise DisplayError("PART_UNDECODABLE", f"part {ref.name}: {exc}") from None
    PART_CACHE.loads += 1
    PART_CACHE.put(ref.sha256, record, len(raw))
    return record


def _manifest(repos: Any, subject_id: str) -> DisplayManifest | None:
    raw = repos.get_raw(MANIFESTS, subject_id)
    if raw is None:
        return None
    return from_jsonable(DisplayManifest, open_envelope(raw, "DisplayManifest").payload)


def snapshot_manifest(repos: Any, snapshot_id: str, snapshot_hash: str | None) -> tuple[DisplayManifest | None, str]:
    """The display manifest of a source run's admitted snapshot, bound to the run's snapshot id and hash."""
    try:
        manifest = _manifest(repos, snapshot_id)
    except (CodecError, ValueError, TypeError) as exc:
        return None, f"DISPLAY_UNREADABLE: snapshot manifest ({getattr(exc, 'code', type(exc).__name__)})"
    if manifest is None:
        return None, "DISPLAY_NOT_BUILT"
    if (manifest.schema_version != DISPLAY_SCHEMA or manifest.subject != "SNAPSHOT" or manifest.subject_id != snapshot_id
            or snapshot_hash is None or manifest.snapshot_hash != snapshot_hash):
        return None, "DISPLAY_MISMATCH: the snapshot manifest does not name the source run's snapshot"
    return manifest, "OK"


@dataclass(frozen=True)
class VerifiedPlan:
    """An accepted plan verified through its signed acceptance and display receipts (no artifact is read)."""

    published: Any                          # contracts.planning.PublishedPlan
    receipt: Any                            # contracts.planning.AcceptanceReceipt
    display: DisplayReceipt
    manifest: DisplayManifest
    summary: Any                            # records.PlanSummary
    head_version: int


def verify_plan_display(services: Any, plan_id: str, *, scope: str, purpose: str, signer: Any = None,
                        head_version: int | None = None) -> tuple[VerifiedPlan | None, str]:
    """One accepted plan for display, verified end to end without its artifact (see the module docstring)."""
    from ..publication.repository import repository_for
    from ..publication.signing import resolve_signer

    repo = repository_for(services)
    repos = services.repositories
    try:
        published, receipt = repo.published(plan_id), repo.receipt(plan_id)
    except Exception as exc:  # noqa: BLE001 - unreadable or tampered storage is a security failure, never an accepted plan
        return None, f"SECURITY_FAILURE: stored acceptance is unreadable ({getattr(exc, 'code', type(exc).__name__)})"
    if published is None or receipt is None:
        return None, "SECURITY_FAILURE: head names an incomplete acceptance"
    if receipt.payload_sha256 != published.payload_hash or receipt.plan_id != plan_id:
        return None, "SECURITY_FAILURE: payload hash does not match the signed receipt"
    if published.scope != scope or published.purpose.value != purpose or receipt.scope != scope or receipt.purpose.value != purpose:
        return None, "SECURITY_FAILURE: wrong scope or purpose"
    signer = signer or resolve_signer(services.settings)[0]
    if signer is None or not signer.verify(receipt.signature_hex, receipt.key_id, receipt.scope, receipt.purpose.value, receipt.snapshot_hash,
                                           receipt.policy_hash, receipt.payload_sha256) or published.signature != receipt.signature_hex:
        return None, "SECURITY_FAILURE: signature does not verify with the trusted key"
    try:
        display = repos.get_typed(RECEIPTS, plan_id, "DisplayReceipt", DisplayReceipt)
        manifest = _manifest(repos, plan_id)
    except (CodecError, ValueError, TypeError) as exc:
        return None, f"SECURITY_FAILURE: display records are unreadable ({getattr(exc, 'code', type(exc).__name__)})"
    if display is None or manifest is None:
        return None, "DISPLAY_NOT_BUILT: the accepted plan has no display package (python -m focus.cli display --plan)"
    if (display.plan_id, display.payload_sha256, display.scope, display.purpose.value, display.snapshot_hash, display.policy_hash,
            display.key_id) != (plan_id, receipt.payload_sha256, scope, purpose, receipt.snapshot_hash, receipt.policy_hash, receipt.key_id):
        return None, "SECURITY_FAILURE: the display receipt does not match the acceptance"
    if not signer.verify_display(display.signature_hex, display.key_id, scope, purpose, plan_id, display.payload_sha256, display.snapshot_hash,
                                 display.policy_hash, display.manifest_sha256):
        return None, "SECURITY_FAILURE: the display signature does not verify with the trusted key"
    if content_hash(manifest) != display.manifest_sha256:
        return None, "SECURITY_FAILURE: the display manifest differs from the signed one"
    if (manifest.schema_version != DISPLAY_SCHEMA or manifest.subject != "PLAN" or manifest.subject_id != plan_id
            or manifest.payload_sha256 != receipt.payload_sha256 or manifest.snapshot_hash != receipt.snapshot_hash):
        return None, "SECURITY_FAILURE: the display manifest does not name this plan"
    ref = manifest.part("summary")
    if ref is None:
        return None, "SECURITY_FAILURE: the display manifest has no summary"
    try:
        summary = load_part(repos, ref)
    except DisplayError as exc:
        return None, f"SECURITY_FAILURE: {exc.code}: {exc.message}"
    if (summary.plan_id, summary.purpose.value, summary.snapshot_hash, summary.policy_hash) != (plan_id, purpose, receipt.snapshot_hash,
                                                                                                 receipt.policy_hash):
        return None, "SECURITY_FAILURE: the display summary bindings differ from the receipt"
    return VerifiedPlan(published, receipt, display, manifest, summary, receipt.head_version if head_version is None else head_version), "OK"


# ---------------------------------------------------------------------- backfill (CLI)

def cli_display(services: Any, args: Any) -> dict[str, Any]:
    """Build the display package of an admitted snapshot (source role) or an accepted plan (signer role)."""
    from ..data.acquisition import get_run, load_snapshot, source_head_name
    from ..publication.repository import repository_for
    from ..publication.serializer import decode_artifact, payload_sha256
    from ..publication.signing import resolve_signer

    repos = services.repositories
    out: dict[str, Any] = {"status": "ok", "built": []}
    snapshot_id = args.snapshot
    if args.current_source:
        head = repos.head(source_head_name(services.settings.scope_key))
        found = get_run(repos, head.record_id) if head is not None else None
        if found is None or not found[0].snapshot_id:
            return {"status": "invalid", "error": {"code": "NO_SOURCE_RUN", "message": "no admitted source run"}}
        snapshot_id = found[0].snapshot_id
    if snapshot_id:
        snap = load_snapshot(repos, snapshot_id)
        if snap is None:
            return {"status": "invalid", "error": {"code": "SNAPSHOT_NOT_FOUND", "message": snapshot_id}}
        manifest = write_snapshot_display(repos, snap)
        out["built"].append({"snapshot_id": snap.id, "parts": len(manifest.parts), "bytes": sum(p.size for p in manifest.parts)})
    plan_ids = list(args.plan or ())
    if args.current_plans:
        repo = repository_for(services)
        plan_ids += [h.record_id for h in (repo.head(services.settings.scope_key, p) for p in ("EXECUTABLE", "FORECAST")) if h is not None]
    if plan_ids:
        signer, why = resolve_signer(services.settings)
        if signer is None:
            return {"status": "unavailable", "error": {"code": "SIGNER_UNAVAILABLE", "message": why}}
        repo = repository_for(services)
        for plan_id in plan_ids:
            published, payload = repo.published(plan_id), repo.payload(plan_id)
            if published is None or payload is None:
                return {"status": "invalid", "error": {"code": "PLAN_NOT_FOUND", "message": plan_id}}
            data = payload.payload_text.encode("utf-8")
            if payload_sha256(data) != published.payload_hash:
                return {"status": "invalid", "error": {"code": "SECURITY_FAILURE", "message": f"{plan_id}: payload hash mismatch"}}
            artifact = decode_artifact(data)
            del data
            snap = load_snapshot(repos, artifact.snapshot_id)
            if snap is None:
                return {"status": "invalid", "error": {"code": "SNAPSHOT_NOT_FOUND", "message": artifact.snapshot_id}}
            receipt = publish_plan_display(repos, signer, snap, artifact, plan_id, published.payload_hash, published.accepted_at)
            out["built"].append({"plan_id": plan_id, "manifest_sha256": receipt.manifest_sha256})
    if not out["built"]:
        return {"status": "invalid", "error": {"code": "NOTHING_TO_BUILD", "message": "name --snapshot, --plan, --current-source or --current-plans"}}
    return out
