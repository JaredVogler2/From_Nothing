"""Lifecycle, disruption and excusal contracts (S03 EVENT/EVALUATION, S14)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..domain import DomainError, EvidenceRef, aware_utc

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
    "SEQUENCE_VIOLATION",
    "OOS_DISCRETIONARY",
})

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


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    id: str
    source_key: str
    work_key: str
    kind: str
    effective_at: datetime
    recorded_at: datetime
    observed_at: datetime
    ingested_at: datetime
    source_sequence: int
    source_revision: str
    amount: int | None
    person: str | None
    unit_ids: tuple[str, ...]
    verified: bool
    evidence: tuple[EvidenceRef, ...]
    supersedes: str | None

    def __post_init__(self) -> None:
        if self.kind not in LIFECYCLE_KINDS:
            raise DomainError("BAD_EVENT_KIND", f"unknown lifecycle kind {self.kind!r}")
        for name in ("effective_at", "recorded_at", "observed_at", "ingested_at"):
            object.__setattr__(self, name, aware_utc(getattr(self, name), name))
        if self.kind == "REPORTED_COMPLETION" and self.verified:
            raise DomainError("REPORTED_NOT_VERIFIED", "a reported observation is a claim, not verified completion")


@dataclass(frozen=True, slots=True)
class SequenceViolation:
    id: str
    job: str
    phase: str
    effective_at: datetime
    incomplete_predecessors: tuple[str, ...]
    planned: bool
    affected_person_minutes: int
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class DisruptionClaim:
    id: str
    goal_id: str
    units: tuple[str, ...]
    cause: str
    origin_team: str | None
    executing_team: str | None
    evidence: tuple[str, ...]
    notes: str
    status: str                 # SUBMITTED | WITHDRAWN
    attribution: str            # AUTO | MANUAL
    reported_by: str
    reported_at: datetime

    def __post_init__(self) -> None:
        if self.cause not in DISRUPTION_CAUSES:
            raise DomainError("BAD_CAUSE", f"unknown disruption cause {self.cause!r}")
        if not self.units:
            raise DomainError("NO_UNITS", "a claim names at least one frozen earning unit")
        object.__setattr__(self, "reported_at", aware_utc(self.reported_at, "reported_at"))


@dataclass(frozen=True, slots=True)
class ExcusalDecision:
    id: str
    claim_ids: tuple[str, ...]
    approved_units: tuple[str, ...]
    decision: str               # APPROVED | REJECTED | PENDING
    authority: str
    decision_at: datetime
    policy_id: str
    reason: str
    supersedes: str | None

    def __post_init__(self) -> None:
        if self.decision not in ("APPROVED", "REJECTED", "PENDING"):
            raise DomainError("BAD_DECISION", f"unknown decision {self.decision!r}")
        if self.decision != "APPROVED" and self.approved_units:
            raise DomainError("UNITS_WITHOUT_APPROVAL", "only an approval names approved units")
        object.__setattr__(self, "decision_at", aware_utc(self.decision_at, "decision_at"))
