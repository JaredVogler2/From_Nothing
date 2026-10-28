"""Observation collection: append-only lifecycle facts, raw actual intervals and review feeds (S14 OBSERVATIONS, P17).

``collect_observations(services, as_of=...)`` builds one atomic, evaluation-ready observation run:

1. **Capture.** Each feed in ``FEEDS`` is captured at ``as_of``. Only rows already observed are
   visible. The fixture reader uses ``fixtures/inputs/<scenario>/observations``; the live reader
   uses the ``queries/actuals`` drafts and reports ``NOT_PROBED`` until an installed producer
   exists. An empty feed counts as covered only with an affirmative producer receipt; anything
   else is ``EMPTY_UNCERTIFIED`` and withholds official evaluation.
2. **Normalize.** Rows become typed facts: ``LifecycleEvent``, ``ActualInterval``,
   ``DefectEvidence``, ``GoalAmendment``, ``DisruptionClaim`` and ``ExcusalDecision``. Natural keys
   resolve to the same deterministic job, phase and person identities the source admission uses.
   A malformed or unsupported row is quarantined with a stable reason code; it never defaults.
3. **Append.** Records are write-once. Ids derive from the source key and revision. A replayed
   row is a counted duplicate. The same key and revision with different content is a quarantined
   ``REVISION_CONTENT_CONFLICT``: the first ingestion stays the as-known truth, and a new revision
   or an explicit ``VOIDED`` / ``CORRECTION`` row is how a source corrects itself.
4. **Derive.** Sequence violations come from actual starts against the admitted graph.
   Disappearance comes from the current census. Automatic attribution yields claims, never relief.
5. **Receipt.** An ``ObservationRun`` holds per-feed coverage and quarantine, and the
   ``observation/<scope>`` head moves forward by compare-and-set.

Ingestion time is the run's ``as_of``, never the wall clock in fixture mode, so collection replays
deterministically. A reported observation (``worker_reported_observation``) is recorded as an
unverified claim, never as verified completion.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from ..codec import CodecError, content_hash, from_jsonable, open_envelope, parse_utc, sha256_hex, strict_loads, to_jsonable
from ..contracts.events import (ActualInterval, DefectEvidence, DisruptionClaim, ExcusalDecision, FeedReceipt, GoalAmendment, IEReview,
                                LifecycleEvent, ObservationRun, QuarantinedRow, SequenceViolation)
from ..contracts.planning import EarningUnit
from ..data.store import ImmutableConflict, VersionConflict
from ..data.workforce import person_namespace
from ..domain import DomainError, EvidenceRef, JobKey, PersonKey, PhaseKind, aware_utc, digest_id

EVENTS = "lifecycle_events"
INTERVALS = "actual_intervals"
DEFECTS = "defect_evidence"
AMENDMENTS = "goal_amendments"
CLAIMS = "disruption_claims"
DECISIONS = "excusal_decisions"
VIOLATIONS = "sequence_violations"
RUNS = "observation_runs"
IE_REVIEWS = "ie_reviews"

FEED_STATUSES = ("ROWS", "CERTIFIED_EMPTY", "EMPTY_UNCERTIFIED", "FAILED", "NOT_PROBED")


def observation_head(scope: str) -> str:
    return f"observation/{scope}"


@dataclass(frozen=True, slots=True)
class FeedSpec:
    name: str
    columns: tuple[str, ...]
    collection: str
    required: bool                 # required coverage for an official/demonstration evaluation
    description: str
    cutoff_column: str = "OBSERVED_AT_UTC"

    @property
    def query_file(self) -> str:
        return f"queries/actuals/{self.name}.sql"


def _cols(text: str) -> tuple[str, ...]:
    return tuple(c.strip() for c in text.split(",") if c.strip())


FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec("lifecycle_events", _cols(
        "SOURCE_EVENT_KEY,SOURCE_SEQUENCE,SOURCE_REVISION,LINE_NUMBER,SOI,PHASE_KIND,PHASE_ORDINAL,SEGMENTS,KIND,PERSON_KEY,"
        "EFFECTIVE_AT_UTC,RECORDED_AT_UTC,OBSERVED_AT_UTC,VERIFIED,VERIFIED_BY,DIRECTIVE_REF,SUPERSEDES_EVENT_KEY,EVIDENCE_REF,DETAIL"),
        EVENTS, True, "Append-only lifecycle facts with effective/recorded/observed clocks and exact job/phase/person identity."),
    FeedSpec("actual_intervals", _cols(
        "SOURCE_EVENT_KEY,SOURCE_REVISION,LINE_NUMBER,SOI,PHASE_KIND,PHASE_ORDINAL,PERSON_KEY,START_UTC,END_UTC,IS_VOIDED,OBSERVED_AT_UTC,EVIDENCE_REF"),
        INTERVALS, True, "Raw actual clock intervals per owner (union method evidence; never completion credit)."),
    FeedSpec("defect_evidence", _cols("SOURCE_EVENT_KEY,LINE_NUMBER,SOI,ROOT_SOI,REWORK_CLASS,DEFECT_ORIGIN_TEAM,EVIDENCE_REF,OBSERVED_AT_UTC"),
             DEFECTS, False, "Explicit defect-origin evidence for rework observed after the planning cutoff."),
    FeedSpec("goal_amendments", _cols(
        "AMENDMENT_KEY,TEAM,SHIFT_DATE,SHIFT_NUMBER,WORK_REF,STANDARD_PERSON_MINUTES,EFFORT_BASIS,FEASIBILITY_REF,AUTHORITY,DECISION_AT_UTC,REASON,"
        "OBSERVED_AT_UTC"), AMENDMENTS, True, "Approved feasible emergency additions (B = B0 + additions; original units retained)."),
    FeedSpec("disruption_claims", _cols(
        "CLAIM_KEY,TEAM,SHIFT_DATE,SHIFT_NUMBER,WORK_REFS,CAUSE,ORIGIN_TEAM,EXECUTING_TEAM,EVIDENCE_REFS,NOTES,STATUS,REPORTED_BY,REPORTED_AT_UTC,"
        "OBSERVED_AT_UTC,SUPERSEDES_CLAIM_KEY"), CLAIMS, True, "Manual disruption claims: evidence for review, never relief by themselves."),
    FeedSpec("excusal_decisions", _cols(
        "DECISION_KEY,TEAM,SHIFT_DATE,SHIFT_NUMBER,CLAIM_KEYS,APPROVED_WORK_REFS,DECISION,AUTHORITY,AUTHORITY_ROLE,DECISION_AT_UTC,REASON,"
        "REVIEW_TRACK,SUPERSEDES_DECISION_KEY,OBSERVED_AT_UTC"), DECISIONS, True, "Reviewed excusal decisions, appeals and superseding decisions."),
)
FEED_BY_NAME = {f.name: f for f in FEEDS}
REQUIRED_FEEDS = tuple(f.name for f in FEEDS if f.required)


# ---------------------------------------------------------------------- readers

@dataclass(frozen=True)
class FeedCapture:
    name: str
    status: str
    rows: tuple[dict[str, Any], ...]
    total_rows: int
    complete: bool
    covered_through: datetime | None
    reason: str

    @property
    def content_sha(self) -> str:
        return content_hash({"feed": self.name, "rows": list(self.rows)})


def _visible(row: Mapping[str, Any], spec: FeedSpec, as_of: datetime) -> bool:
    raw = row.get(spec.cutoff_column)
    if not isinstance(raw, str):
        return True            # malformed rows are kept so that they are quarantined with a reason, not silently dropped
    try:
        return parse_utc(raw) <= as_of
    except CodecError:
        return True


class FixtureObservationReader:
    """Static ``observations/<feed>.json`` files; rows become visible once observed."""

    kind = "fixture"

    def __init__(self, inputs_dir: Path, overrides: Mapping[str, Mapping[str, Any]] | None = None):
        self.dir = Path(inputs_dir) / "observations"
        self._overrides = dict(overrides or {})

    def manifest(self) -> dict[str, Any]:
        path = self.dir / "manifest.json"
        return strict_loads(path.read_bytes()) if path.exists() else {}

    def capture(self, spec: FeedSpec, as_of: datetime) -> FeedCapture:
        if spec.name in self._overrides:
            data: Any = dict(self._overrides[spec.name])
        else:
            path = self.dir / f"{spec.name}.json"
            if not path.exists():
                return FeedCapture(spec.name, "FAILED", (), 0, False, None, "FEED_MISSING: no producer file")
            try:
                data = strict_loads(path.read_bytes())
            except CodecError as exc:
                return FeedCapture(spec.name, "FAILED", (), 0, False, None, f"FEED_INVALID: {exc.message}")
        if not isinstance(data, dict) or data.get("dataset") != spec.name or data.get("schema_version") != 1:
            return FeedCapture(spec.name, "FAILED", (), 0, False, None, "FEED_INVALID: header mismatch")
        if tuple(data.get("columns", ())) != spec.columns:
            return FeedCapture(spec.name, "FAILED", (), 0, False, None, f"SCHEMA_DRIFT: columns differ from the {spec.name} contract")
        all_rows = list(data.get("rows", ()))
        for ordinal, row in enumerate(all_rows):
            if not isinstance(row, dict) or set(row) != set(spec.columns):
                return FeedCapture(spec.name, "FAILED", (), len(all_rows), False, None, f"SCHEMA_DRIFT: row {ordinal} keys differ from the contract")
        receipt = data.get("receipt") or {}
        complete = bool(receipt.get("complete"))
        covered: datetime | None = None
        if isinstance(receipt.get("covered_through_utc"), str):
            try:
                covered = min(parse_utc(receipt["covered_through_utc"]), as_of)
            except CodecError:
                return FeedCapture(spec.name, "FAILED", (), len(all_rows), False, None, "RECEIPT_INVALID: covered_through_utc")
        rows = tuple(r for r in all_rows if _visible(r, spec, as_of))
        if not complete:
            return FeedCapture(spec.name, "FAILED", rows, len(all_rows), False, covered, "RECEIPT_INCOMPLETE: producer receipt is not complete")
        if rows:
            status = "ROWS"
        elif receipt.get("certified_empty") or covered is not None:
            status = "CERTIFIED_EMPTY"     # the producer affirms completeness through covered_through: nothing observed yet
        else:
            status = "EMPTY_UNCERTIFIED"
        return FeedCapture(spec.name, status, rows, len(all_rows), complete, covered, "")


class LiveObservationReader:
    """Live observation producers are proposed FOCUS_FN tables (queries/actuals drafts): not probed until installed."""

    kind = "live"

    def __init__(self, app_root: Path):
        self.app_root = Path(app_root)

    def manifest(self) -> dict[str, Any]:
        return {}

    def capture(self, spec: FeedSpec, as_of: datetime) -> FeedCapture:
        present = (self.app_root / spec.query_file).exists()
        reason = ("NOT_INSTALLED: the FOCUS_FN observation producer behind " + spec.query_file + " is a migration draft"
                  if present else f"QUERY_MISSING: {spec.query_file}")
        return FeedCapture(spec.name, "NOT_PROBED", (), 0, False, None, reason)


def make_observation_reader(services: Any, scenario: str | None = None) -> Any:
    settings = services.settings
    if settings.mode == "fixture":
        return FixtureObservationReader(settings.fixture_inputs_dir / (scenario or settings.scenario_id))
    return LiveObservationReader(settings.app_root)


# ---------------------------------------------------------------------- append-only store

_TYPES: dict[str, tuple[str, type, str]] = {
    EVENTS: ("LifecycleEvent", LifecycleEvent, "ingested_at"),
    INTERVALS: ("ActualInterval", ActualInterval, "ingested_at"),
    DEFECTS: ("DefectEvidence", DefectEvidence, "ingested_at"),
    AMENDMENTS: ("GoalAmendment", GoalAmendment, "ingested_at"),
    CLAIMS: ("DisruptionClaim", DisruptionClaim, "ingested_at"),
    DECISIONS: ("ExcusalDecision", ExcusalDecision, "ingested_at"),
    VIOLATIONS: ("SequenceViolation", SequenceViolation, "detected_at"),
    RUNS: ("ObservationRun", ObservationRun, "as_of"),
    IE_REVIEWS: ("IEReview", IEReview, "requested_at"),
}


def _substance(record: Any) -> str:
    """Content identity without the ingestion clock: a replay of the same fact is a duplicate, not a conflict."""
    data = to_jsonable(record)
    for name in ("ingested_at", "detected_at"):
        data.pop(name, None)
    return content_hash(data)


class ObservationStore:
    """Typed write-once access to the observation collections (one authority for fixture and SQL artifact stores)."""

    def __init__(self, repositories: Any, scope: str):
        self.repos = repositories
        self.scope = scope
        self._cache: dict[tuple[str, str], Any] = {}

    def get(self, collection: str, record_id: str) -> Any:
        key = (collection, record_id)
        if key in self._cache:
            return self._cache[key]
        raw = self.repos.get_raw(collection, record_id)
        if raw is None:
            return None
        type_name, cls, _ = _TYPES[collection]
        env = open_envelope(raw, type_name)
        record = from_jsonable(cls, env.payload, f"$<{type_name}:{record_id}>")
        self._cache[key] = record
        return record

    def all(self, collection: str) -> list[Any]:
        out = []
        for rid in sorted(self.repos.list_ids(collection)):
            record = self.get(collection, rid)
            if record is not None and getattr(record, "scope", self.scope) == self.scope:
                out.append(record)
        return out

    def known(self, collection: str, as_of: datetime) -> list[Any]:
        """Facts FOCUS knew at ``as_of`` (ingestion/detection clock), for as-known replay."""
        clock = _TYPES[collection][2]
        return [r for r in self.all(collection) if getattr(r, clock) <= as_of]

    def put(self, collection: str, record: Any) -> str:
        """``NEW`` | ``DUPLICATE`` | ``CONFLICT``. Records are immutable; the first ingestion is the as-known truth."""
        existing = self.get(collection, record.id)
        if existing is not None:
            return "DUPLICATE" if _substance(existing) == _substance(record) else "CONFLICT"
        type_name, _, clock = _TYPES[collection]
        try:
            self.repos.put_record(collection, type_name, record.id, record, created_at=getattr(record, clock))
        except ImmutableConflict:
            self._cache.pop((collection, record.id), None)
            existing = self.get(collection, record.id)
            return "DUPLICATE" if existing is not None and _substance(existing) == _substance(record) else "CONFLICT"
        self._cache[(collection, record.id)] = record
        return "NEW"

    def latest_run(self, as_of: datetime | None = None) -> ObservationRun | None:
        runs = self.all(RUNS) if as_of is None else self.known(RUNS, as_of)
        return max(runs, key=lambda r: (r.as_of, r.id)) if runs else None


# ---------------------------------------------------------------------- row normalization

class RowError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _text(row: Mapping[str, Any], col: str) -> str:
    value = row.get(col)
    if not isinstance(value, str) or not value.strip():
        raise RowError("FIELD_REQUIRED", f"{col} must be non-empty text")
    return value


def _opt_text(row: Mapping[str, Any], col: str) -> str | None:
    value = row.get(col)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise RowError("TYPE_MISMATCH", f"{col} must be text")
    return value


def _int(row: Mapping[str, Any], col: str, minimum: int | None = None) -> int:
    value = row.get(col)
    if isinstance(value, bool) or value is None:
        raise RowError("FIELD_REQUIRED", f"{col} must be an integer")
    if isinstance(value, str):
        if not value.strip().lstrip("+-").isdigit():
            raise RowError("TYPE_MISMATCH", f"{col} {value!r} is not an integer")
        value = int(value)
    if not isinstance(value, int):
        raise RowError("TYPE_MISMATCH", f"{col} must be an integer")
    if minimum is not None and value < minimum:
        raise RowError("OUT_OF_RANGE", f"{col} must be >= {minimum}")
    return value


def _bool(row: Mapping[str, Any], col: str) -> bool:
    value = row.get(col)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.upper() in ("Y", "N", "TRUE", "FALSE", "1", "0"):
        return value.upper() in ("Y", "TRUE", "1")
    raise RowError("TYPE_MISMATCH", f"{col} must be a boolean or Y/N")


def _utc(row: Mapping[str, Any], col: str) -> datetime:
    value = row.get(col)
    try:
        return parse_utc(value)  # type: ignore[arg-type]
    except CodecError as exc:
        raise RowError(exc.code, f"{col}: {exc.message}") from None


def _date(row: Mapping[str, Any], col: str) -> date:
    value = row.get(col)
    try:
        return date.fromisoformat(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise RowError("BAD_DATE", f"{col} {value!r} is not an ISO date") from None


def _json_obj(row: Mapping[str, Any], col: str) -> dict[str, Any]:
    value = row.get(col)
    if isinstance(value, str):
        try:
            value = strict_loads(value)
        except CodecError as exc:
            raise RowError("BAD_JSON", f"{col}: {exc.message}") from None
    if not isinstance(value, dict):
        raise RowError("TYPE_MISMATCH", f"{col} must be a JSON object")
    return value


def _json_list(row: Mapping[str, Any], col: str) -> list[Any]:
    value = row.get(col)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = strict_loads(value)
        except CodecError as exc:
            raise RowError("BAD_JSON", f"{col}: {exc.message}") from None
    if not isinstance(value, list):
        raise RowError("TYPE_MISMATCH", f"{col} must be a JSON array")
    return value


@dataclass(frozen=True, slots=True)
class WorkRef:
    """A natural-key reference to exact work: line/SOI/phase kind/ordinal and optionally one segment earning unit."""

    line: int
    soi: str
    kind: PhaseKind
    ordinal: int
    segment: int | None


def parse_work_refs(raw: list[Any]) -> tuple[WorkRef, ...]:
    out = []
    for item in raw:
        if not isinstance(item, dict) or not {"line", "soi", "kind", "ordinal"} <= set(item) or set(item) - {"line", "soi", "kind", "ordinal", "segment"}:
            raise RowError("BAD_WORK_REF", f"work reference {item!r} needs exactly line/soi/kind/ordinal[/segment]")
        line, soi, kind, ordinal, segment = item["line"], item["soi"], item["kind"], item["ordinal"], item.get("segment")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1 or not isinstance(soi, str) or not soi:
            raise RowError("BAD_WORK_REF", f"work reference {item!r} has an invalid line/SOI")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise RowError("BAD_WORK_REF", f"work reference {item!r} has an invalid ordinal")
        if segment is not None and (isinstance(segment, bool) or not isinstance(segment, int) or segment < 0):
            raise RowError("BAD_WORK_REF", f"work reference {item!r} has an invalid segment")
        try:
            pk = PhaseKind(kind)
        except ValueError:
            raise RowError("BAD_PHASE_KIND", f"unknown phase kind {kind!r}") from None
        out.append(WorkRef(line, soi, pk, ordinal, segment))
    if len(set(out)) != len(out):
        raise RowError("DUPLICATE_WORK_REF", "a work reference appears twice")
    return tuple(out)


class Identity:
    """Natural keys -> the deterministic ids that source admission assigns (never a strip/casefold join)."""

    def __init__(self, settings: Any):
        self.site, self.program, self.namespace = settings.site, settings.program, person_namespace(settings.mode)

    def job(self, line: Any, soi: Any) -> str:
        try:
            return JobKey(self.site, self.program, line, soi).id
        except DomainError as exc:
            raise RowError(exc.code, exc.message) from None

    def phase(self, job: str, kind: Any, ordinal: Any) -> str:
        try:
            pk = PhaseKind(kind)
        except ValueError:
            raise RowError("BAD_PHASE_KIND", f"unknown phase kind {kind!r}") from None
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise RowError("BAD_ORDINAL", f"phase ordinal {ordinal!r} must be a non-negative integer")
        return f"{job}.{pk.value}.{ordinal}"

    def person(self, native: Any) -> str | None:
        if native is None or native == "":
            return None
        if not isinstance(native, str):
            raise RowError("TYPE_MISMATCH", "PERSON_KEY must be text")
        return PersonKey(native, self.namespace).id

    def ref_phase(self, ref: WorkRef) -> str:
        return self.phase(self.job(ref.line, ref.soi), ref.kind.value, ref.ordinal)


def shift_id_of(site: str, day: date, number: int) -> str:
    if number not in (1, 2, 3):
        raise RowError("BAD_SHIFT", f"shift number {number} must be 1, 2 or 3")
    return f"{site}:{day.isoformat()}:S{number}"


def event_from_row(row: Mapping[str, Any], ident: Identity, scope: str, as_of: datetime) -> LifecycleEvent:
    key = _text(row, "SOURCE_EVENT_KEY")
    revision = _text(row, "SOURCE_REVISION")
    kind = _text(row, "KIND")
    if kind == "DISAPPEARED":
        raise RowError("DERIVED_ONLY", "disappearance is derived from the census, never accepted from a feed")
    job = ident.job(row.get("LINE_NUMBER"), row.get("SOI"))
    phase = None if row.get("PHASE_KIND") is None else ident.phase(job, row.get("PHASE_KIND"), row.get("PHASE_ORDINAL"))
    segments = _json_list(row, "SEGMENTS")
    if any(isinstance(s, bool) or not isinstance(s, int) for s in segments):
        raise RowError("BAD_SEGMENTS", "SEGMENTS must be integer segment ordinals")
    target = _opt_text(row, "SUPERSEDES_EVENT_KEY")
    evidence = EvidenceRef("lifecycle_events", key, revision=revision, source_clock=_opt_text(row, "RECORDED_AT_UTC"),
                           content_hash=sha256_hex(_opt_text(row, "EVIDENCE_REF") or ""))
    try:
        return LifecycleEvent(
            id=digest_id("EVT", scope, "lifecycle_events", key, revision), scope=scope, feed="lifecycle_events", source_key=f"lifecycle_events:{key}",
            origin="SOURCE", kind=kind, job=job, phase=phase, segments=tuple(segments), effective_at=_utc(row, "EFFECTIVE_AT_UTC"),
            recorded_at=_utc(row, "RECORDED_AT_UTC"), observed_at=_utc(row, "OBSERVED_AT_UTC"), ingested_at=as_of,
            source_sequence=_int(row, "SOURCE_SEQUENCE", 0), source_revision=revision, person=ident.person(row.get("PERSON_KEY")),
            verified=_bool(row, "VERIFIED"), verified_by=_opt_text(row, "VERIFIED_BY"), directive=_opt_text(row, "DIRECTIVE_REF"),
            supersedes=None if target is None else f"lifecycle_events:{target}", evidence=(evidence,),
            detail=(_opt_text(row, "DETAIL") or "")[:500])
    except DomainError as exc:
        raise RowError(exc.code, exc.message) from None


def interval_from_row(row: Mapping[str, Any], ident: Identity, scope: str, as_of: datetime) -> ActualInterval:
    key = _text(row, "SOURCE_EVENT_KEY")
    revision = _text(row, "SOURCE_REVISION")
    job = ident.job(row.get("LINE_NUMBER"), row.get("SOI"))
    person = ident.person(_text(row, "PERSON_KEY"))
    try:
        return ActualInterval(
            id=digest_id("ACT", scope, key, revision), scope=scope, source_key=f"actual_intervals:{key}", job=job,
            phase=ident.phase(job, row.get("PHASE_KIND"), row.get("PHASE_ORDINAL")), person=person or "", start=_utc(row, "START_UTC"),
            end=_utc(row, "END_UTC"), source_revision=revision, voided=_bool(row, "IS_VOIDED"), observed_at=_utc(row, "OBSERVED_AT_UTC"),
            ingested_at=as_of, evidence=(EvidenceRef("actual_intervals", key, revision=revision, content_hash=sha256_hex(_opt_text(row, "EVIDENCE_REF") or "")),))
    except DomainError as exc:
        raise RowError(exc.code, exc.message) from None


def defect_from_row(row: Mapping[str, Any], ident: Identity, scope: str, as_of: datetime) -> DefectEvidence:
    key = _text(row, "SOURCE_EVENT_KEY")
    cls = _text(row, "REWORK_CLASS")
    if cls not in ("NC", "PU", "CI"):
        raise RowError("BAD_REWORK_CLASS", f"rework class {cls!r} is not NC/PU/CI")
    line = row.get("LINE_NUMBER")
    root = _opt_text(row, "ROOT_SOI")
    try:
        return DefectEvidence(digest_id("DEF", scope, key), scope, ident.job(line, row.get("SOI")), None if root is None else ident.job(line, root), cls,
                              _opt_text(row, "DEFECT_ORIGIN_TEAM") or "", _opt_text(row, "EVIDENCE_REF") or "", _utc(row, "OBSERVED_AT_UTC"), as_of)
    except DomainError as exc:
        raise RowError(exc.code, exc.message) from None


def latest_revisions(items: list[Any]) -> list[Any]:
    """The latest revision of each source key (numeric revisions outrank text, as in the clock admission)."""
    from ..data.duration_gauge import revision_rank

    latest: dict[str, Any] = {}
    for item in items:
        held = latest.get(item.source_key)
        if held is None or (revision_rank(item.source_revision), item.id) > (revision_rank(held.source_revision), held.id):
            latest[item.source_key] = item
    return [latest[k] for k in sorted(latest)]


# ---------------------------------------------------------------------- the collector

@dataclass
class _Run:
    services: Any
    as_of: datetime
    store: ObservationStore
    ident: Identity
    scope: str
    quarantine: list[QuarantinedRow] = field(default_factory=list)
    ids: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    counts: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    def reject(self, feed: str, key: str, code: str, detail: str) -> None:
        self.quarantine.append(QuarantinedRow(feed, key, code, detail[:400]))
        self.counts[feed]["quarantined"] += 1

    def append(self, feed: str, collection: str, record: Any, key: str) -> bool:
        outcome = self.store.put(collection, record)
        if outcome == "CONFLICT":
            self.reject(feed, key, "REVISION_CONTENT_CONFLICT", f"{record.id} was first ingested with different content; the first ingestion stands")
            return False
        self.counts[feed]["duplicates" if outcome == "DUPLICATE" else "admitted"] += 1
        self.ids[collection].append(record.id)
        return True


def _row_key(spec: FeedSpec, row: Mapping[str, Any]) -> str:
    for col in ("SOURCE_EVENT_KEY", "AMENDMENT_KEY", "CLAIM_KEY", "DECISION_KEY"):
        if col in spec.columns:
            value = row.get(col)
            return value if isinstance(value, str) and value else f"<row {content_hash(dict(row))[:12]}>"
    return f"<row {content_hash(dict(row))[:12]}>"


def collect_observations(services: Any, *, as_of: datetime | None = None, reader: Any = None, scenario: str | None = None) -> ObservationRun:
    from . import disruption, lifecycle

    as_of = aware_utc(as_of or services.clock.now(), "as_of")
    repos = services.repositories
    settings = services.settings
    scope = settings.scope_key
    reader = reader or make_observation_reader(services, scenario)
    with repos.store.lock(f"observe-{scope}", ttl_seconds=600, wait_seconds=120):
        run = _Run(services, as_of, ObservationStore(repos, scope), Identity(settings), scope)
        captures = {spec.name: reader.capture(spec, as_of) for spec in FEEDS}

        for row in captures["lifecycle_events"].rows:
            key = _row_key(FEED_BY_NAME["lifecycle_events"], row)
            try:
                run.append("lifecycle_events", EVENTS, event_from_row(row, run.ident, scope, as_of), key)
            except RowError as exc:
                run.reject("lifecycle_events", key, exc.code, exc.detail)
        for row in captures["actual_intervals"].rows:
            key = _row_key(FEED_BY_NAME["actual_intervals"], row)
            try:
                run.append("actual_intervals", INTERVALS, interval_from_row(row, run.ident, scope, as_of), key)
            except RowError as exc:
                run.reject("actual_intervals", key, exc.code, exc.detail)
        for row in captures["defect_evidence"].rows:
            key = _row_key(FEED_BY_NAME["defect_evidence"], row)
            try:
                run.append("defect_evidence", DEFECTS, defect_from_row(row, run.ident, scope, as_of), key)
            except RowError as exc:
                run.reject("defect_evidence", key, exc.code, exc.detail)

        registry = lifecycle.GoalRegistry.load(services)
        graph = lifecycle.current_graph(services)
        for row in captures["goal_amendments"].rows:
            key = _row_key(FEED_BY_NAME["goal_amendments"], row)
            try:
                run.append("goal_amendments", AMENDMENTS, amendment_from_row(row, run.ident, registry, run.store, scope, as_of), key)
            except RowError as exc:
                run.reject("goal_amendments", key, exc.code, exc.detail)

        intake = disruption.Intake(services, as_of, run.store, registry=registry, graph=graph)
        for row in captures["disruption_claims"].rows:
            key = _row_key(FEED_BY_NAME["disruption_claims"], row)
            try:
                run.append("disruption_claims", CLAIMS, intake.claim_from_row(row, run.ident), key)
            except RowError as exc:
                run.reject("disruption_claims", key, exc.code, exc.detail)
        for row in captures["excusal_decisions"].rows:
            key = _row_key(FEED_BY_NAME["excusal_decisions"], row)
            try:
                run.append("excusal_decisions", DECISIONS, intake.decision_from_row(row, run.ident), key)
            except RowError as exc:
                run.reject("excusal_decisions", key, exc.code, exc.detail)

        view = lifecycle.ObservationView.build(services, as_of, store=run.store, registry=registry, graph=graph)
        for violation in lifecycle.derive_sequence_violations(view, graph, detected_at=as_of):
            outcome = run.store.put(VIOLATIONS, violation)
            if outcome == "CONFLICT":
                run.reject("derived", violation.id, "VIOLATION_REDERIVATION_DIFFERS", "the original sequence violation stands unchanged")
            else:
                run.ids[VIOLATIONS].append(violation.id)
                run.counts["derived"]["violations_new" if outcome == "NEW" else "violations_known"] += 1
        if graph is not None:
            for event in lifecycle.derive_disappearances(view, graph, ingested_at=as_of):
                run.append("derived", EVENTS, event, event.source_key)
        view = lifecycle.ObservationView.build(services, as_of, store=run.store, registry=registry, graph=graph)
        for claim in disruption.auto_attribute(services, view):
            run.append("derived", CLAIMS, claim, claim.source_key)

        receipts = []
        for spec in FEEDS:
            cap = captures[spec.name]
            c = run.counts[spec.name]
            receipts.append(FeedReceipt(spec.name, cap.status, cap.complete, len(cap.rows), c["admitted"], c["duplicates"], c["quarantined"],
                                        cap.covered_through, cap.content_sha, cap.reason))
        record_ids = {k: tuple(sorted(set(v))) for k, v in sorted(run.ids.items())}
        obs = ObservationRun(
            id=digest_id("OBS", scope, as_of, settings.mode, [(r.feed, r.status, r.content_sha) for r in receipts], record_ids),
            scope=scope, mode=settings.mode, as_of=as_of, graph_snapshot_id=None if graph is None else graph.snapshot_id, feeds=tuple(receipts),
            record_ids=record_ids, quarantine=tuple(sorted(run.quarantine, key=lambda q: (q.feed, q.source_key, q.code))), created_at=as_of)
        run.store.put(RUNS, obs)
        _advance_head(repos, run.store, scope, obs)
        return obs


def _advance_head(repos: Any, store: ObservationStore, scope: str, obs: ObservationRun) -> None:
    name = observation_head(scope)
    for _ in range(5):
        head = repos.head(name)
        if head is not None:
            current = store.get(RUNS, head.record_id)
            if current is not None and (current.as_of, current.id) >= (obs.as_of, obs.id):
                return                                   # a later collection already leads; replay never moves the head back
        try:
            repos.move_head(name, obs.id, 0 if head is None else head.version)
            return
        except VersionConflict:
            continue


def amendment_from_row(row: Mapping[str, Any], ident: Identity, registry: Any, store: ObservationStore, scope: str, as_of: datetime) -> GoalAmendment:
    key = _text(row, "AMENDMENT_KEY")
    team = _text(row, "TEAM")
    shift_id = shift_id_of(ident.site, _date(row, "SHIFT_DATE"), _int(row, "SHIFT_NUMBER", 1))
    goal_id = registry.goal_id_for(team, shift_id)
    if goal_id is None:
        raise RowError("GOAL_NOT_FOUND", f"no accepted obligation plan covers {team} {shift_id}")
    ref = parse_work_refs([_json_obj(row, "WORK_REF")])[0]
    if ref.segment is not None:
        raise RowError("BAD_WORK_REF", "an amendment adds exactly one whole phase")
    phase = ident.ref_phase(ref)
    record = registry.records.get((team, shift_id))
    if record is not None and any(u.phase == phase for u in record.original_units):
        raise RowError("ALREADY_IN_GOAL", "the phase is already an original frozen unit of this goal; amendments only add work")
    for prior in store.all(AMENDMENTS):
        if prior.goal_id == goal_id and prior.source_key != f"goal_amendments:{key}" and any(u.phase == phase for u in prior.units):
            raise RowError("ALREADY_AMENDED", f"amendment {prior.source_key} already added this phase")
    effort = _int(row, "STANDARD_PERSON_MINUTES", 1)
    basis = _text(row, "EFFORT_BASIS")
    job = phase.rsplit(".", 2)[0]
    unit = EarningUnit(digest_id("EU", goal_id, "AMD", key, phase, 0), f"AMD:{key}", job, phase, 0, team, shift_id, effort, team, "EMERGENCY_ADDITION")
    try:
        return GoalAmendment(digest_id("AMD", scope, key), scope, goal_id, (unit,), effort, _text(row, "AUTHORITY"), _utc(row, "DECISION_AT_UTC"),
                             _text(row, "REASON"), _opt_text(row, "FEASIBILITY_REF") or "", basis, as_of, f"goal_amendments:{key}")
    except DomainError as exc:
        raise RowError(exc.code, exc.message) from None


# ---------------------------------------------------------------------- coverage for evaluation

def coverage_reasons(run: ObservationRun | None, deadline: datetime, required: tuple[str, ...] = REQUIRED_FEEDS) -> list[str]:
    """Why observation coverage cannot support an official result for work due by ``deadline`` (empty: covered)."""
    if run is None:
        return ["OBSERVATIONS_NOT_COLLECTED: no observation run covers this evaluation"]
    out = []
    for name in required:
        receipt = run.feed(name)
        if receipt is None or not receipt.usable:
            status = "MISSING" if receipt is None else receipt.status
            out.append(f"COVERAGE_{name.upper()}_{status}: {receipt.reason if receipt else 'feed not captured'}".rstrip(": "))
        elif receipt.covered_through is None or receipt.covered_through < deadline:
            through = "unknown" if receipt.covered_through is None else receipt.covered_through.isoformat()
            out.append(f"COVERAGE_{name.upper()}_BEHIND: covered through {through}, needs {deadline.isoformat()}")
    return out


def freshness(run: ObservationRun | None, now: datetime, max_age_hours: int) -> str:
    if run is None:
        return "MISSING"
    return "STALE" if now - run.as_of > timedelta(hours=max_age_hours) else "FRESH"


# ---------------------------------------------------------------------- reported observations (claims, never verified)

def record_reported_observation(services: Any, *, request_id: str, requested_by: str, job_id: str, phase_id: str | None, kind: str,
                                effective_at: datetime, notes: str, at: datetime) -> LifecycleEvent:
    scope = services.settings.scope_key
    if not isinstance(job_id, str) or not job_id.startswith("J"):
        raise DomainError("BAD_JOB_ID", "job_id must be a FOCUS job id")
    phase = phase_id or f"{job_id}.{PhaseKind.WORK.value}.0"
    if not phase.startswith(job_id + "."):
        raise DomainError("PHASE_JOB_MISMATCH", "the phase does not belong to the job")
    effective_at = aware_utc(effective_at, "effective_at")
    if effective_at > at:
        raise DomainError("EFFECTIVE_IN_FUTURE", "a reported observation cannot describe the future")
    event = LifecycleEvent(
        id=digest_id("EVT", scope, "reported_observations", request_id), scope=scope, feed="reported_observations",
        source_key=f"reported_observations:{request_id}", origin="REPORTED", kind="REPORTED_COMPLETION", job=job_id, phase=phase, segments=(),
        effective_at=effective_at, recorded_at=at, observed_at=at, ingested_at=at, source_sequence=0, source_revision="1", person=None,
        verified=False, verified_by=None, directive=None, supersedes=None, evidence=(),
        detail=f"reported {kind} by {requested_by}: {notes}"[:500])
    store = ObservationStore(services.repositories, scope)
    if store.put(EVENTS, event) == "CONFLICT":
        raise DomainError("REVISION_CONTENT_CONFLICT", "this request id was already recorded with different content")
    return event


def worker_reported_observation(services: Any, run: Any) -> dict[str, Any]:
    req = run.request
    try:
        effective = parse_utc(req["effective_at"])
    except (KeyError, CodecError) as exc:
        raise DomainError("BAD_REQUEST", f"effective_at: {exc}") from None
    event = record_reported_observation(services, request_id=run.id, requested_by=run.requested_by, job_id=req.get("job_id"),
                                        phase_id=req.get("phase_id"), kind=str(req.get("kind", "COMPLETED")), effective_at=effective,
                                        notes=str(req.get("notes", ""))[:400], at=run.created_at)
    return {"status": "recorded", "event_id": event.id, "kind": event.kind, "verified": False,
            "note": "a reported observation is a claim; only source-verified completion earns credit"}


# ---------------------------------------------------------------------- CLI

def run_summary(run: ObservationRun) -> dict[str, Any]:
    return {
        "observation_run_id": run.id, "as_of": run.as_of.isoformat(), "mode": run.mode, "graph_snapshot_id": run.graph_snapshot_id,
        "feeds": [{"feed": f.feed, "status": f.status, "complete": f.complete, "rows": f.rows, "admitted": f.admitted, "duplicates": f.duplicates,
                   "quarantined": f.quarantined, "covered_through": None if f.covered_through is None else f.covered_through.isoformat(),
                   "reason": f.reason} for f in run.feeds],
        "records": {k: len(v) for k, v in run.record_ids.items()},
        "quarantine": [{"feed": q.feed, "key": q.source_key, "code": q.code, "detail": q.detail} for q in run.quarantine],
    }


def cli_collect_observations(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    if args.mode != services.settings.mode:
        return {"status": "invalid", "error": {"code": "MODE_MISMATCH", "message": f"runtime mode is {services.settings.mode}"}}
    as_of = None
    if getattr(args, "as_of", None):
        try:
            as_of = parse_utc(args.as_of)
        except CodecError as exc:
            return {"status": "invalid", "error": {"code": exc.code, "message": exc.message}}
    run = collect_observations(services, as_of=as_of, scenario=args.scenario)
    usable = all((run.feed(n) is not None and run.feed(n).usable) for n in REQUIRED_FEEDS)  # type: ignore[union-attr]
    return {"status": "ok" if usable else "unknown", **run_summary(run)}


def manifest_points(services: Any, scenario: str | None = None) -> list[datetime]:
    """Fixture collection points declared by the scenario manifest (deterministic demo replay)."""
    reader = make_observation_reader(services, scenario)
    raw = reader.manifest().get("collection_points_utc", [])
    return [parse_utc(x) for x in raw]
