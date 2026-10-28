"""Planning contracts: snapshot, phases, candidate plans, receipts, publications (S03)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

from ..codec import content_hash
from ..domain import (
    AvailabilityBasis,
    DomainError,
    DutyKind,
    Disposition,
    EdgeType,
    EvidenceRef,
    Holding,
    Interval,
    JobKey,
    PersonKey,
    PersonOrigin,
    PhaseKey,
    PhaseKind,
    PoolKey,
    Purpose,
    ReleaseKind,
    ReleaseStatus,
    RequirementBundle,
    RequirementStatus,
    aware_utc,
)


def record_hash(record: Any, exclude: tuple[str, ...] = ("hash",)) -> str:
    """Canonical hash of a dataclass record excluding its own hash/signature fields."""
    body = {f.name: getattr(record, f.name) for f in fields(record) if f.name not in exclude}
    return content_hash(body)


# ---------------------------------------------------------------------------
# source-derived records
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FlowBudget:
    job_number: str
    line: int
    critical_flow_minutes: int | None
    total_mechanics: int | None
    primary_mechanic_count: int | None
    primary_mechanic_duration: int | None
    assist_count: int | None
    assist_labor_minutes: int | None
    total_labor_minutes: int | None
    duration_source: str | None
    load_timestamp: str | None


@dataclass(frozen=True, slots=True)
class InspectionHold:
    """A sourced in-process QA hold point at an exact production-minute position."""

    after_work_minutes: int
    inspector_minutes: int
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class InspectionTrigger:
    final_required: bool
    ip_required: bool
    ip_holds: tuple[InspectionHold, ...]
    ip_hold_status: str                 # SOURCED | NONE | UNKNOWN_LOCATION
    other_buyoffs: tuple[str, ...]      # parallel buyoff kinds feeding AND release
    counts: dict[str, int]
    basis: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class Job:
    key: JobKey
    status: str
    team: str | None
    station: str | None
    model: str | None
    customer: str | None
    description: str | None
    parent_native: str | None
    parent_key: JobKey | None
    rework_class: str | None
    defect_origin_team: str | None
    raw_budget: FlowBudget | None
    earliest: datetime | None
    actual_start: datetime | None
    actual_complete: datetime | None
    performed_elapsed: int | None
    remaining_elapsed: int | None
    inspection_trigger: InspectionTrigger | None
    is_rework: bool | None
    planning_system: str | None
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def id(self) -> str:
        return self.key.id

    @property
    def line(self) -> int:
        return self.key.line


@dataclass(frozen=True, slots=True)
class RoleDemand:
    bundle: RequirementBundle
    team: str
    requirement_status: RequirementStatus
    basis: str


@dataclass(frozen=True, slots=True)
class Phase:
    key: PhaseKey
    job: str
    line: int
    kind: PhaseKind
    ordinal: int
    work_minutes: int
    standard_person_minutes: int
    parent_elapsed_minutes: int
    role_demands: tuple[RoleDemand, ...]
    team: str | None
    status: str                       # OPEN | COMPLETE
    actual_start: datetime | None
    actual_end: datetime | None
    conditional_reasons: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    label: str

    @property
    def id(self) -> str:
        return self.key.id

    def __post_init__(self) -> None:
        if self.work_minutes < 0 or self.standard_person_minutes < 0:
            raise DomainError("NEGATIVE_MINUTES", f"phase {self.label} has negative minutes")
        if self.kind is PhaseKind.RELEASE and self.work_minutes != 0:
            raise DomainError("RELEASE_HAS_WORK", "release milestones carry zero work")
        if self.kind is not PhaseKind.RELEASE and self.status == "OPEN" and self.work_minutes <= 0 and not self.blocked_reasons:
            raise DomainError("NONPOSITIVE_WORK", f"open phase {self.label} needs positive work minutes")


@dataclass(frozen=True, slots=True)
class Edge:
    id: str
    pred_phase: str
    succ_phase: str
    type: EdgeType
    lag_minutes: int
    hard: bool
    basis: str
    raw_type: str | None
    raw_hard: str | None
    concurrency_mode: str | None
    effective_interval: Interval | None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ReleaseMilestone:
    id: str
    kind: ReleaseKind
    scope_keys: tuple[str, ...]
    forecast_at: datetime | None
    ready_at: datetime | None
    status: ReleaseStatus
    precision: str                     # TIMESTAMP | DATE | NONE
    label: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class DurationEstimate:
    id: str
    job: str
    elapsed_minutes: int
    precise_elapsed: Decimal | None
    standard_person_minutes: int
    measurement_basis: str
    stats_id: str | None
    statistic: str | None
    crew_profile: str
    crew_size: int
    method: str
    uncertainty: str
    source_hash: str
    policy_id: str


@dataclass(frozen=True, slots=True)
class JobRequirement:
    job: str
    status: RequirementStatus
    bundle: RequirementBundle | None
    basis: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class Person:
    key: PersonKey
    display_name: str
    origin: PersonOrigin
    synthetic: bool
    namespace: str
    purpose_scope: str                 # ANY | FORECAST_ONLY
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def id(self) -> str:
        return self.key.id

    def __post_init__(self) -> None:
        if self.origin is PersonOrigin.SYNTHETIC_VIRTUAL and (not self.synthetic or self.purpose_scope != "FORECAST_ONLY"):
            raise DomainError("SYNTHETIC_ORIGIN", "virtual people must stay synthetic and forecast-only")


@dataclass(frozen=True, slots=True)
class Availability:
    id: str
    person: str
    pool: str
    intervals: tuple[Interval, ...]
    other_duty: tuple[Interval, ...]
    ot_intervals: tuple[Interval, ...]
    regular_cap: int
    ot_cap: int
    admitted_presence: bool
    basis: AvailabilityBasis
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class Pool:
    """Pure pool: one team, one shift start date, one shift number, one source run.

    ``executable_members`` have admitted roster/attendance availability; ``forecast_members``
    additionally include nominal (inferred) and synthetic virtual people. Each purpose has its
    own denominator N and activation cap floor(85*N/100) computed on distinct admitted people.
    """

    key: PoolKey
    members: tuple[str, ...]
    executable_members: tuple[str, ...]
    forecast_members: tuple[str, ...]
    synthetic: bool
    n_executable: int
    cap_executable: int
    n_forecast: int
    cap_forecast: int
    basis: str
    excluded: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.key.id

    def denominator(self, purpose: Purpose) -> int:
        return self.n_executable if purpose is Purpose.EXECUTABLE else self.n_forecast

    def cap(self, purpose: Purpose) -> int:
        return self.cap_executable if purpose is Purpose.EXECUTABLE else self.cap_forecast

    def eligible_members(self, purpose: Purpose) -> tuple[str, ...]:
        return self.executable_members if purpose is Purpose.EXECUTABLE else self.forecast_members


@dataclass(frozen=True, slots=True)
class TeamInferenceRecord:
    person: str
    inferred_team: str | None
    evidence_count: int
    effort_minutes: Decimal
    top_share: Decimal | None
    runner_up_team: str | None
    runner_up_count: int | None
    is_tie: bool
    basis: str
    window_start: datetime | None
    window_end: datetime | None
    algorithm_version: str
    effective_team: str | None
    effective_basis: str
    stale: bool = False


@dataclass(frozen=True, slots=True)
class CalendarDay:
    day: date
    status: str
    position: str | None
    load_id: int
    last_modified: date | None


@dataclass(frozen=True, slots=True)
class LineCalendar:
    line: int
    status: str                        # ADMITTED | CONFLICT | MISSING
    generation: int | None
    min_date: date | None
    max_date: date | None
    days: tuple[CalendarDay, ...]
    holes: tuple[date, ...]
    reasons: tuple[str, ...]
    revision: str


@dataclass(frozen=True, slots=True)
class RawCSRow:
    row_id: str
    mbu: str | None
    control_station: int
    line: int
    load_ref_pos: str | None
    load_offset: int | None
    cs_op_load: date | None
    unload_ref_pos: str | None
    unload_offset: int | None
    cs_op_unload: date | None
    last_modified: date
    load_id: int
    load_ts: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class CSFacts:
    line: int
    station: str
    mbu: str | None
    status: str                        # ADMITTED | GAP | CONFLICT | POLICY_MISSING
    earliest_at: datetime | None
    gate_date: date | None
    hard_latest_at: datetime | None
    due_proxy: date | None
    raw_load_id: int | None
    raw_offset_fields: dict[str, Any]
    conversion_policy: str
    source_run_id: str
    reasons: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class Shortage:
    record_id: int
    line: int
    used_on: str | None
    ecd: date | None
    team: str | None
    reason_code: str | None
    created: date | None
    resolved_job: str | None
    root_job: str | None
    affected_jobs: tuple[str, ...]
    status: str                        # LINKED | UNRESOLVED_USED_ON | UNKNOWN_ECD | RECEIVED | REOPENED
    release_id: str | None
    reasons: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ReworkLink:
    line: int
    child_job: str
    root_job: str | None
    parent_job: str | None
    rework_class: str | None
    child_status: str | None
    status: str                        # LINKED | QUARANTINED | UNRESOLVED
    defect_origin_team: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AircraftEconomics:
    line: int
    status: str                        # KNOWN | UNKNOWN | UNSUPPORTED
    authority: str                     # ASSUMED | ACTUAL | UNKNOWN
    term_id: str | None
    selector_type: str | None
    currency: str | None
    daily_rate_cents: Fraction | None
    cap_cents: Fraction | None
    original_sale_cents: Fraction | None
    accrued_floor_cents: Fraction | None
    due_at: datetime | None
    due_basis: str | None
    curve: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CarryIn:
    id: str
    person: str
    start: datetime
    end: datetime
    kind: DutyKind
    line: int | None
    job: str | None
    shift_id: str | None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadinessGap:
    code: str
    scope: str
    detail: str
    capability_effect: str            # BLOCKS_EXECUTABLE | BLOCKS_ALL | CONDITIONAL_FORECAST | INFO


@dataclass(frozen=True, slots=True)
class JobDisposition:
    job: str
    disposition: Disposition
    reasons: tuple[str, ...]
    executable_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AircraftCensus:
    line: int
    site: str
    program: str
    model: str | None
    customer: str | None
    status_counts: dict[str, int]
    jobs_total: int
    admitted: int
    blocked: int
    quarantined: int
    excluded: int
    verified_complete: int
    scope_basis: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanningSnapshot:
    id: str
    source_run_id: str
    scope_key: str
    site: str
    program: str
    cutoff: datetime
    created_at: datetime
    policy_id: str
    policy_hash: str
    manifest_hash: str
    census: tuple[AircraftCensus, ...]
    jobs: tuple[Job, ...]
    dispositions: tuple[JobDisposition, ...]
    phases: tuple[Phase, ...]
    edges: tuple[Edge, ...]
    releases: tuple[ReleaseMilestone, ...]
    durations: tuple[DurationEstimate, ...]
    requirements: tuple[JobRequirement, ...]
    people: tuple[Person, ...]
    holdings: tuple[Holding, ...]
    pools: tuple[Pool, ...]
    availability: tuple[Availability, ...]
    calendars: tuple[LineCalendar, ...]
    raw_cs_rows: tuple[RawCSRow, ...]
    cs_facts: tuple[CSFacts, ...]
    shortages: tuple[Shortage, ...]
    rework_links: tuple[ReworkLink, ...]
    economics: tuple[AircraftEconomics, ...]
    carry_in: tuple[CarryIn, ...]
    inference: tuple[TeamInferenceRecord, ...]
    shift_ids: tuple[str, ...]
    gaps: tuple[ReadinessGap, ...]
    statistics_ids: tuple[str, ...]
    dataset_lineage: dict[str, str]
    incumbent_id: str | None
    hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cutoff", aware_utc(self.cutoff, "snapshot cutoff"))
        object.__setattr__(self, "created_at", aware_utc(self.created_at, "snapshot created_at"))

    def computed_hash(self) -> str:
        return record_hash(self)

    def verify(self) -> None:
        if self.computed_hash() != self.hash:
            raise DomainError("SNAPSHOT_HASH_MISMATCH", f"snapshot {self.id} content hash mismatch")


def seal_snapshot(snapshot: PlanningSnapshot) -> PlanningSnapshot:
    return replace(snapshot, hash=record_hash(snapshot))


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CrewRole:
    person: str
    role: str
    holding_evidence_ids: tuple[str, ...]
    availability_id: str


@dataclass(frozen=True, slots=True)
class Assignment:
    id: str
    phase: str
    job: str
    line: int
    kind: PhaseKind
    start: datetime
    end: datetime
    segments: tuple[Interval, ...]
    work_minutes: int
    shift_id: str | None
    pool_key: str | None
    crew: tuple[CrewRole, ...]
    purpose: Purpose
    predecessor_release_ids: tuple[str, ...]
    duration_estimate_id: str | None
    conditional_reasons: tuple[str, ...]
    committed: bool
    commitment_outcome: str | None


@dataclass(frozen=True, slots=True)
class Duty:
    id: str
    person: str
    start: datetime
    end: datetime
    kind: DutyKind
    aircraft_from: int | None
    aircraft_to: int | None
    assignment_id: str | None
    shift_id: str | None
    regular_minutes: int
    ot_minutes: int
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class JobOutcome:
    job: str
    disposition: Disposition
    reasons: tuple[str, ...]
    start: datetime | None
    end: datetime | None
    conditional: bool


@dataclass(frozen=True, slots=True)
class AircraftCompletion:
    line: int
    status: str                        # known | conditional | unknown
    completion_at: datetime | None
    required_jobs: int
    scheduled_jobs: int
    unresolved_jobs: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LineCost:
    line: int
    status: str                        # known | conditional | unknown | unsupported
    authority: str
    completion_at: datetime | None
    due_at: datetime | None
    delay_minutes: int | None
    total_cents: Fraction | None
    unavoidable_cents: Fraction | None
    controllable_cents: Fraction | None
    marginal_day_cents: Fraction | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CostSummary:
    lines: tuple[LineCost, ...]
    known_total_cents: Fraction
    conditional_total_cents: Fraction
    controllable_known_cents: Fraction
    unknown_lines: tuple[int, ...]
    complete: bool
    currency: str


@dataclass(frozen=True, slots=True)
class CommitmentResult:
    phase: str
    job: str
    incumbent_assignment_id: str
    day_offset: int
    outcome: str                       # KEPT | CREW_CHANGED | MOVED | DEFERRED | UNSAFE | OUT_OF_HORIZON | COMPLETED
    attempt1: str
    attempt2: str
    reason: str
    new_assignment_id: str | None


@dataclass(frozen=True, slots=True)
class StageMetric:
    stage: str
    runtime_ms: int
    candidates: int
    accepted: int
    note: str


@dataclass(frozen=True, slots=True)
class AlgorithmStatus:
    name: str
    version: str
    seed: int
    budget_seconds: float
    deadline_reached: bool
    proof_status: str
    stages: tuple[StageMetric, ...]
    weights: dict[str, str]
    baseline_comparison: dict[str, Any]
    paths_used: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    id: str
    snapshot_id: str
    snapshot_hash: str
    policy_hash: str
    source_run_id: str
    scope: str
    purpose: Purpose
    created_at: datetime
    incumbent_id: str | None
    assignments: tuple[Assignment, ...]
    duties: tuple[Duty, ...]
    job_dispositions: tuple[JobOutcome, ...]
    aircraft_completion: tuple[AircraftCompletion, ...]
    costs: CostSummary
    commitment_results: tuple[CommitmentResult, ...]
    algorithm_status: AlgorithmStatus
    global_optimality_proven: bool
    hash: str

    def computed_hash(self) -> str:
        return record_hash(self)


def seal_candidate(plan: CandidatePlan) -> CandidatePlan:
    return replace(plan, hash=record_hash(plan))


@dataclass(frozen=True, slots=True)
class Violation:
    check: str
    code: str
    entity: str
    detail: str


@dataclass(frozen=True, slots=True)
class ValidationReceipt:
    id: str
    candidate_id: str
    kind: str                          # FINAL | DETACHED | READBACK | DISPATCH
    snapshot_hash: str
    policy_hash: str
    validator_version: str
    check_counts: dict[str, int]
    coverage: dict[str, Any]
    violations: tuple[Violation, ...]
    required_skips: tuple[str, ...]
    plan_hash: str
    payload_sha256: str | None
    valid: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# publication and goals
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EarningUnit:
    id: str
    assignment_id: str
    job: str
    phase: str
    segment: int
    team: str
    shift_id: str
    standard_person_minutes: int
    due_owner: str
    allocation: str


@dataclass(frozen=True, slots=True)
class GateContext:
    basis: str                         # ACTUAL_CS_GATE | BASELINE_PROXY | ALREADY_ADJUSTED_PROXY | UNAVAILABLE
    gate_date: date | None
    effective_gate_date: date | None
    valuation_date: date
    behind_days: int | None
    behind_raw: Fraction | None
    base_value: Fraction
    bonus_value: Fraction | None


@dataclass(frozen=True, slots=True)
class FrozenGoal:
    id: str
    plan_id: str
    shift_id: str
    team: str
    shift_start_date: date
    shift_number: int
    earning_units: tuple[EarningUnit, ...]
    standard_person_minutes: int
    gate_contexts: dict[str, GateContext]
    hash: str


@dataclass(frozen=True, slots=True)
class AcceptanceReceipt:
    plan_id: str
    stage_id: str
    readback_validation_id: str
    final_validation_id: str
    detached_validation_id: str
    payload_sha256: str
    source_run_id: str
    snapshot_hash: str
    policy_hash: str
    scope: str
    purpose: Purpose
    key_id: str
    signature_hex: str
    fixture_readback: bool
    accepted_at: datetime
    head_version: int


@dataclass(frozen=True, slots=True)
class PublishedPlan:
    id: str
    candidate_id: str
    payload_hash: str
    snapshot_id: str
    source_run_id: str
    policy_hash: str
    purpose: Purpose
    scope: str
    acceptance_receipt: AcceptanceReceipt
    key_id: str
    signature: str
    frozen_goal_ids: tuple[str, ...]
    accepted_at: datetime
