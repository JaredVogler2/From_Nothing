"""Lifecycle, actual, disruption, excusal and observation contracts (S03 EVENT/EVALUATION, S06, S14, P17).

Claims, reviewed relief and verified actual work are distinct, revisioned facts:

* ``LifecycleEvent``: an append-only physical or administrative fact about work. It keeps four
  clocks (effective, source-recorded, observed and ingested), the source sequence and revision,
  and the exact job/phase/person. Verified physical completion is a different kind from
  cancellation, detab, terminal status and disappearance. ``VOIDED`` and ``CORRECTION`` append
  revisions; nothing rewrites history.
* ``ActualInterval``: a raw actual clock interval of one owner on one phase.
* ``SequenceViolation``: actual out-of-sequence work. It records the effective time and the
  incomplete prerequisites, and is never erased by later predecessor completion.
* ``DisruptionClaim``: auto attribution or a manual claim, with evidence. Never relief by itself.
* ``ExcusalDecision``: the reviewed decision, with authority, appeal and supersession.
* ``GoalAmendment``: an approved feasible emergency addition. The original frozen units are
  never mutated.
* ``DefectEvidence``: explicit defect-origin evidence, kept separate from DAG direction and from
  the executing team.
* ``ObservationRun``: one collection, with per-feed coverage and quarantine receipts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..domain import DomainError, EvidenceRef, aware_utc
from .planning import EarningUnit

LIFECYCLE_KINDS = frozenset({
    "STARTED",
    "PROGRESS",            # verified segment earning-unit completion
    "COMPLETED",           # verified physical completion
    "REPORTED_COMPLETION", # a claim only, never verified credit
    "CANCELLED",
    "DETABBED",
    "TERMINAL_STATUS",
    "REOPENED",
    "VOIDED",
    "CORRECTION",
    "SEQUENCE_VIOLATION",  # a source-asserted out-of-sequence fact
    "OOS_DISCRETIONARY",   # source classification of a violation as discretionary
    "DISAPPEARED",         # derived: the work left the source census without any terminal fact
})
PHYSICAL_KINDS = frozenset({"STARTED", "PROGRESS", "COMPLETED", "REPORTED_COMPLETION"})
ADMINISTRATIVE_KINDS = frozenset({"CANCELLED", "DETABBED", "TERMINAL_STATUS", "REOPENED", "DISAPPEARED"})
REVISION_KINDS = frozenset({"VOIDED", "CORRECTION"})
CLASSIFICATION_KINDS = frozenset({"SEQUENCE_VIOLATION", "OOS_DISCRETIONARY"})
EVENT_ORIGINS = frozenset({"SOURCE", "REPORTED", "DERIVED"})

DISRUPTION_CAUSES = (
    "LATE_PART",
    "CROSS_TEAM_PREDECESSOR",
    "SAME_TEAM_PREDECESSOR",
    "CAPACITY_SHORTAGE",
    "SKILL_SHORTAGE",
    "DURATION_OVERRUN",
    "REWORK_INJECTION",
    "PLAN_CHURN",
)

NOMINALLY_EXCUSABLE = frozenset({
    "LATE_PART",
    "CROSS_TEAM_PREDECESSOR",
    "CAPACITY_SHORTAGE",
    "SKILL_SHORTAGE",
    "REWORK_INJECTION",
})

CLAIM_STATUSES = ("PROPOSED", "SUBMITTED", "WITHDRAWN")   # PROPOSED: auto attribution awaiting a person's notes
CLAIM_ATTRIBUTIONS = ("AUTO", "MANUAL")
DECISIONS = ("APPROVED", "REJECTED", "PENDING")
REVIEW_TRACKS = ("STANDARD", "PLAN_CHURN")


def _utc(obj: object, *names: str) -> None:
    for name in names:
        value = getattr(obj, name)
        if value is not None:
            object.__setattr__(obj, name, aware_utc(value, name))


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    id: str
    scope: str
    feed: str                      # lifecycle_events | reported_observations | derived
    source_key: str                # stable source event key
    origin: str                    # SOURCE | REPORTED | DERIVED
    kind: str
    job: str
    phase: str | None              # None: a job-level administrative fact that applies to every phase
    segments: tuple[int, ...]      # PROGRESS (and its CORRECTION): verified segment earning units of the accepted assignment
    effective_at: datetime
    recorded_at: datetime
    observed_at: datetime
    ingested_at: datetime
    source_sequence: int
    source_revision: str
    person: str | None
    verified: bool
    verified_by: str | None
    directive: str | None          # explicit authority that directed an out-of-sequence start (STARTED only)
    supersedes: str | None         # VOIDED / CORRECTION: the source key (feed:key) of the event it revises
    evidence: tuple[EvidenceRef, ...]
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in LIFECYCLE_KINDS:
            raise DomainError("BAD_EVENT_KIND", f"unknown lifecycle kind {self.kind!r}")
        if self.origin not in EVENT_ORIGINS:
            raise DomainError("BAD_EVENT_ORIGIN", f"unknown event origin {self.origin!r}")
        _utc(self, "effective_at", "recorded_at", "observed_at", "ingested_at")
        if self.observed_at > self.ingested_at:
            raise DomainError("INGESTED_BEFORE_OBSERVED", "an event cannot be ingested before it was observed")
        if self.recorded_at > self.observed_at:
            raise DomainError("OBSERVED_BEFORE_RECORDED", "an event cannot be observed before the source recorded it")
        if self.kind in PHYSICAL_KINDS and self.effective_at > self.recorded_at:
            raise DomainError("EFFECTIVE_AFTER_RECORDED", "a physical fact cannot be recorded before it happened")
        if self.kind in PHYSICAL_KINDS and self.phase is None:
            raise DomainError("PHASE_REQUIRED", f"{self.kind} names the exact phase")
        if self.kind == "REPORTED_COMPLETION" and self.verified:
            raise DomainError("REPORTED_NOT_VERIFIED", "a reported observation is a claim, not verified completion")
        if self.origin == "REPORTED" and (self.kind != "REPORTED_COMPLETION" or self.verified):
            raise DomainError("REPORTED_NOT_VERIFIED", "a reported observation records only an unverified completion claim")
        if self.kind == "DISAPPEARED" and self.origin != "DERIVED":
            raise DomainError("DERIVED_ONLY", "disappearance is derived from the source census, never asserted")
        if len(set(self.segments)) != len(self.segments) or any(s < 0 for s in self.segments):
            raise DomainError("BAD_SEGMENTS", "segment earning units are distinct and non-negative")
        if self.kind == "PROGRESS" and not self.segments:
            raise DomainError("SEGMENTS_REQUIRED", "PROGRESS names the verified segment earning units")
        if self.segments and self.kind not in ("PROGRESS", "CORRECTION"):
            raise DomainError("SEGMENTS_UNEXPECTED", "only PROGRESS (or its correction) names segment earning units")
        if self.kind in REVISION_KINDS and not self.supersedes:
            raise DomainError("SUPERSEDES_REQUIRED", f"{self.kind} names the event it revises")
        if self.kind not in REVISION_KINDS and self.supersedes:
            raise DomainError("SUPERSEDES_UNEXPECTED", "only VOIDED/CORRECTION revise another event")
        if self.directive is not None and self.kind != "STARTED":
            raise DomainError("DIRECTIVE_UNEXPECTED", "only a start can be directed out of sequence")
        if self.verified and not self.verified_by:
            raise DomainError("VERIFIER_REQUIRED", "verified work names its verifying authority")
        if self.source_sequence < 0:
            raise DomainError("OUT_OF_RANGE", "source sequence must be non-negative")


@dataclass(frozen=True, slots=True)
class ActualInterval:
    id: str
    scope: str
    source_key: str
    job: str
    phase: str
    person: str
    start: datetime
    end: datetime
    source_revision: str
    voided: bool
    observed_at: datetime
    ingested_at: datetime
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        _utc(self, "start", "end", "observed_at", "ingested_at")
        if not self.start < self.end:
            raise DomainError("EMPTY_INTERVAL", "an actual interval needs start < end")
        if self.observed_at > self.ingested_at:
            raise DomainError("INGESTED_BEFORE_OBSERVED", "an interval cannot be ingested before it was observed")

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True, slots=True)
class SequenceViolation:
    """Actual out-of-sequence work: the effective time and every prerequisite incomplete at that time."""

    id: str
    scope: str
    job: str
    phase: str
    effective_at: datetime                       # the actual start (or finish, for FF) that violated the sequence
    trigger: str                                 # START | FINISH
    trigger_ref: str                             # the start event / interval / completion that proves it
    incomplete_predecessors: tuple[str, ...]     # phase or release ids not satisfied at effective_at
    known_incomplete: tuple[str, ...]            # of those, the ones already known incomplete when the work started
    discretionary: bool | None                   # None: unknown classification
    classification: str                          # STARTED_WITH_KNOWN_INCOMPLETE_PREDECESSOR | DIRECTED | RELIED_ON_RECORDED_COMPLETION | SOURCE_ASSERTED
    directive: str | None
    graph_snapshot_id: str
    origin: str                                  # DERIVED | SOURCE
    detected_at: datetime
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _utc(self, "effective_at", "detected_at")
        if not self.incomplete_predecessors:
            raise DomainError("NO_PREDECESSOR", "a sequence violation names its incomplete prerequisites")
        if self.trigger not in ("START", "FINISH"):
            raise DomainError("BAD_TRIGGER", f"unknown violation trigger {self.trigger!r}")


@dataclass(frozen=True, slots=True)
class DefectEvidence:
    """Explicit defect-origin evidence for rework. The origin team is a source fact, never a DAG guess."""

    id: str
    scope: str
    job: str                       # the rework job
    root_job: str | None
    rework_class: str              # NC | PU | CI
    origin_team: str
    evidence_ref: str
    observed_at: datetime
    ingested_at: datetime

    def __post_init__(self) -> None:
        _utc(self, "observed_at", "ingested_at")
        if not self.origin_team or not self.evidence_ref:
            raise DomainError("ORIGIN_EVIDENCE_REQUIRED", "defect origin needs an origin team and an evidence reference")


@dataclass(frozen=True, slots=True)
class DisruptionClaim:
    id: str
    scope: str
    goal_id: str
    units: tuple[str, ...]
    cause: str
    origin_team: str | None
    executing_team: str | None
    evidence: tuple[str, ...]
    notes: str
    status: str                    # PROPOSED | SUBMITTED | WITHDRAWN
    attribution: str               # AUTO | MANUAL
    reported_by: str
    reported_at: datetime
    ingested_at: datetime
    source_key: str
    supersedes: str | None

    def __post_init__(self) -> None:
        if self.cause not in DISRUPTION_CAUSES:
            raise DomainError("BAD_CAUSE", f"unknown disruption cause {self.cause!r}")
        if not self.units or len(set(self.units)) != len(self.units):
            raise DomainError("NO_UNITS", "a claim names distinct frozen earning units")
        if self.status not in CLAIM_STATUSES:
            raise DomainError("BAD_CLAIM_STATUS", f"unknown claim status {self.status!r}")
        if self.attribution not in CLAIM_ATTRIBUTIONS:
            raise DomainError("BAD_ATTRIBUTION", f"unknown attribution {self.attribution!r}")
        _utc(self, "reported_at", "ingested_at")


@dataclass(frozen=True, slots=True)
class ExcusalDecision:
    id: str
    scope: str
    goal_id: str
    claim_ids: tuple[str, ...]
    approved_units: tuple[str, ...]
    decision: str                  # APPROVED | REJECTED | PENDING
    authority: str
    authority_role: str
    decision_at: datetime
    policy_id: str
    reason: str
    review_track: str              # STANDARD | PLAN_CHURN
    exception: bool                # an approval of a cause outside the five nominal categories
    supersedes: str | None         # appeal / superseding decision
    ingested_at: datetime
    source_key: str

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise DomainError("BAD_DECISION", f"unknown decision {self.decision!r}")
        if self.decision != "APPROVED" and self.approved_units:
            raise DomainError("UNITS_WITHOUT_APPROVAL", "only an approval names approved units")
        if self.decision == "APPROVED" and not self.approved_units:
            raise DomainError("APPROVAL_WITHOUT_UNITS", "an approval names the missed units it relieves")
        if not self.claim_ids:
            raise DomainError("NO_CLAIMS", "a decision reviews at least one claim")
        if self.review_track not in REVIEW_TRACKS:
            raise DomainError("BAD_REVIEW_TRACK", f"unknown review track {self.review_track!r}")
        _utc(self, "decision_at", "ingested_at")


@dataclass(frozen=True, slots=True)
class GoalAmendment:
    """An approved feasible emergency addition to one frozen goal (B = B0 + additions)."""

    id: str
    scope: str
    goal_id: str
    units: tuple[EarningUnit, ...]
    standard_person_minutes: int
    authority: str
    decision_at: datetime
    reason: str
    feasibility_ref: str
    effort_basis: str
    ingested_at: datetime
    source_key: str

    def __post_init__(self) -> None:
        if not self.units:
            raise DomainError("NO_UNITS", "an amendment adds at least one earning unit")
        if not self.feasibility_ref:
            raise DomainError("FEASIBILITY_REQUIRED", "only a feasible emergency addition amends a goal")
        if sum(u.standard_person_minutes for u in self.units) != self.standard_person_minutes:
            raise DomainError("EFFORT_MISMATCH", "amendment effort must equal its units' frozen effort")
        _utc(self, "decision_at", "ingested_at")


@dataclass(frozen=True, slots=True)
class QuarantinedRow:
    feed: str
    source_key: str
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class FeedReceipt:
    feed: str
    status: str                    # ROWS | CERTIFIED_EMPTY | EMPTY_UNCERTIFIED | FAILED | NOT_PROBED
    complete: bool
    rows: int
    admitted: int
    duplicates: int
    quarantined: int
    covered_through: datetime | None
    content_sha: str
    reason: str

    def __post_init__(self) -> None:
        _utc(self, "covered_through")

    @property
    def usable(self) -> bool:
        return self.complete and self.status in ("ROWS", "CERTIFIED_EMPTY")


@dataclass(frozen=True, slots=True)
class ObservationRun:
    id: str
    scope: str
    mode: str
    as_of: datetime
    graph_snapshot_id: str | None
    feeds: tuple[FeedReceipt, ...]
    record_ids: dict[str, tuple[str, ...]]       # collection -> ids admitted by this run (new or replayed)
    quarantine: tuple[QuarantinedRow, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _utc(self, "as_of", "created_at")

    def feed(self, name: str) -> FeedReceipt | None:
        return next((f for f in self.feeds if f.feed == name), None)


@dataclass(frozen=True, slots=True)
class IEReview:
    """An IE standard/role/data review proposal. It never rewrites an issued goal or score."""

    id: str
    scope: str
    job: str
    standard_revision: str
    proposed_correction: str
    reason: str
    requested_by: str
    requested_at: datetime
    status: str                    # PROPOSED
    affects_issued_goals: bool

    def __post_init__(self) -> None:
        _utc(self, "requested_at")


@dataclass(frozen=True, slots=True)
class ShiftWindow:
    shift_id: str
    start_date: date
    number: int
    start: datetime
    end: datetime
    credit_deadline: datetime

    def __post_init__(self) -> None:
        _utc(self, "start", "end", "credit_deadline")
