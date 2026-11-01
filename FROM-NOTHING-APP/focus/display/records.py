"""Display package records: small typed parts the web reads instead of whole snapshots and plan artifacts.

A part is a slice of the admitted snapshot or of an accepted plan, built in the pipeline step that already holds the
whole object in memory. Parts hold the same contract records the snapshot and plan hold (jobs, phases, edges,
assignments, duties, ...) with their evidence references dropped, so the web's projections run on them unchanged.

Every part is stored once under the SHA-256 of its canonical JSON (``content_hash``). A manifest names a revision's
parts by that hash:

* **Snapshot manifest** (``SNAPSHOT``), written at acquisition before the source head moves. It is bound to the source
  run's snapshot id and hash, the same trust the snapshot itself has.
* **Plan manifest** (``PLAN``), written by the signer before the plan head moves. A ``DisplayReceipt`` signs the
  manifest's hash together with the plan id, its payload hash, scope, purpose, snapshot and policy (domain
  ``FOCUS_DISPLAY_V1``), so the web checks the plan's display without reading or decoding the plan artifact.

Part names (``BUCKETS`` job buckets share jobs by their id):

==============================  ======================================================================================
``summary``                     snapshot or plan header and the small fleet-wide records
``line:<n>``                    one aircraft: its jobs and dispositions (snapshot), its job outcomes (plan)
``jobs:<b>``                    one job bucket: the jobs' phases, graph neighborhood, releases, durations and
                                requirements (snapshot); their assignments and outcomes (plan)
``pools:<team>:<date>``         one team's pools and availability on one shift start date (snapshot)
``work:<team>:<n>``             one team's phases on one aircraft with their dispatch facts (snapshot)
``labels:<b>``                  one job bucket's phase labels (snapshot)
``releases``                    every release milestone (snapshot)
``holds``                       every inspection-hold phase (snapshot); the plan's assignments of them (plan)
``team-day:<team>:<date>``      one team's assignments of one day with their duties, phases and jobs (plan)
``goals:<date>``                the frozen-goal unit facts of one obligation day (executable plan)
``commitments``                 the plan's task-commitment results with their phases and jobs (plan)
==============================  ======================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..contracts.planning import (AircraftCensus, AircraftEconomics, AlgorithmStatus, Assignment, Availability, CarryIn,
                                  CommitmentResult, CostSummary, CSFacts, Duty, DurationEstimate, Edge, Job, JobDisposition, JobOutcome,
                                  JobRequirement, LineCalendar, Person, Phase, Pool, ReadinessGap, ReleaseMilestone, Shortage,
                                  TeamInferenceRecord)
from ..domain import Holding, Purpose

DISPLAY_SCHEMA = 1
BUILDER = "focus-display-v1"
BUCKETS = 1024


def bucket_of(job_id: str, buckets: int = BUCKETS) -> int:
    """The job bucket of a job id. Job ids are digests (``J`` + hex), so their leading hex digits spread evenly."""
    digits = "".join(c for c in job_id[1:6] if c in "0123456789abcdef")
    return (int(digits, 16) if digits else sum(job_id.encode())) % buckets


def job_of_phase(phase_id: str) -> str:
    """The job id of a phase id (``<job id>.<KIND>.<ordinal>``)."""
    return phase_id.split(".", 1)[0]


# ---------------------------------------------------------------------- manifest and receipt

@dataclass(frozen=True, slots=True)
class PartRef:
    name: str
    sha256: str
    kind: str                          # the part's record type
    size: int                          # canonical JSON bytes


@dataclass(frozen=True, slots=True)
class DisplayManifest:
    schema_version: int
    subject: str                       # SNAPSHOT | PLAN
    subject_id: str                    # the snapshot id or the plan id
    snapshot_id: str
    snapshot_hash: str
    policy_hash: str
    source_run_id: str
    purpose: str | None                # plans only
    payload_sha256: str | None         # plans only: the accepted artifact's signed payload hash
    buckets: int
    builder: str
    parts: tuple[PartRef, ...]

    def part(self, name: str) -> PartRef | None:
        return _index(self).get(name)

    def names(self, prefix: str) -> list[str]:
        return sorted(n for n in _index(self) if n.startswith(prefix))


_INDEXES: dict[int, tuple[Any, dict[str, PartRef]]] = {}


def _index(manifest: DisplayManifest) -> dict[str, PartRef]:
    found = _INDEXES.get(id(manifest))
    if found is not None and found[0] is manifest:
        return found[1]
    index = {p.name: p for p in manifest.parts}
    if len(_INDEXES) > 256:
        _INDEXES.clear()
    _INDEXES[id(manifest)] = (manifest, index)
    return index


@dataclass(frozen=True, slots=True)
class DisplayReceipt:
    """The signer's binding of a plan's display manifest to the accepted plan (domain ``FOCUS_DISPLAY_V1``)."""

    plan_id: str
    manifest_sha256: str
    payload_sha256: str
    scope: str
    purpose: Purpose
    snapshot_hash: str
    policy_hash: str
    key_id: str
    signature_hex: str
    fixture: bool                      # signed with the published fixture TEST key


# ---------------------------------------------------------------------- snapshot parts

@dataclass(frozen=True, slots=True)
class PoolIndex:
    """One pool without its member lists (the members are in its ``pools:<team>:<date>`` part)."""

    pool_id: str
    team: str
    start_date: date
    shift_number: int
    synthetic: bool
    n_executable: int
    cap_executable: int
    n_forecast: int
    cap_forecast: int
    members: int
    executable_members: int


@dataclass(frozen=True, slots=True)
class LineCount:
    """Counts of one aircraft's jobs by team and disposition, and of its shortages by status (paging without loading)."""

    line: int
    team: str | None
    disposition: str | None
    jobs: int


@dataclass(frozen=True, slots=True)
class StatusCount:
    line: int
    status: str
    count: int


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    snapshot_id: str
    snapshot_hash: str
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
    cs_facts: tuple[CSFacts, ...]
    calendars: tuple[LineCalendar, ...]
    economics: tuple[AircraftEconomics, ...]
    gaps: tuple[ReadinessGap, ...]
    people: tuple[Person, ...]
    holdings: tuple[Holding, ...]
    inference: tuple[TeamInferenceRecord, ...]
    carry_in: tuple[CarryIn, ...]
    shift_ids: tuple[str, ...]
    pools: tuple[PoolIndex, ...]
    person_teams: dict[str, tuple[str, ...]]           # person id -> teams of every pool listing them as a member
    person_pool_teams: dict[str, tuple[str, ...]]      # ... as a member, executable or forecast member (plan duties follow these)
    job_counts: tuple[LineCount, ...]
    shortage_counts: tuple[StatusCount, ...]           # shortages by aircraft and status
    teams: tuple[str, ...]                             # every team of a pool, phase or job


@dataclass(frozen=True, slots=True)
class SnapshotLine:
    line: int
    jobs: tuple[Job, ...]
    dispositions: tuple[JobDisposition, ...]
    shortages: tuple[Shortage, ...]
    releases: tuple[ReleaseMilestone, ...]             # releases the line's shortages name
    other_jobs: tuple[Job, ...]                        # jobs of another line its shortages name


@dataclass(frozen=True, slots=True)
class SnapshotStandards:
    """One aircraft's duration standards and certificate requirements (the IE view's per-aircraft page)."""

    line: int
    durations: tuple[DurationEstimate, ...]
    requirements: tuple[JobRequirement, ...]


@dataclass(frozen=True, slots=True)
class SnapshotJobs:
    """One job bucket: its jobs with every record a job's detail, dispatch or validation slice reads."""

    bucket: int
    jobs: tuple[Job, ...]
    dispositions: tuple[JobDisposition, ...]
    phases: tuple[Phase, ...]
    edges: tuple[Edge, ...]                            # every hard edge touching one of the bucket's phases
    other_phases: tuple[Phase, ...]                    # edge ends outside the bucket
    other_jobs: tuple[Job, ...]                        # their jobs, and jobs the bucket's shortages name
    releases: tuple[ReleaseMilestone, ...]             # releases scoped to the jobs or ending one of the edges
    durations: tuple[DurationEstimate, ...]
    requirements: tuple[JobRequirement, ...]
    shortages: tuple[Shortage, ...]                    # shortages naming one of the jobs


@dataclass(frozen=True, slots=True)
class SnapshotPools:
    team: str
    start_date: date
    pools: tuple[Pool, ...]
    availability: tuple[Availability, ...]


@dataclass(frozen=True, slots=True)
class PhaseFacts:
    """Dispatch facts of one phase that need the whole graph: the release ancestors up to verified completion."""

    phase: str
    release_ancestors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SnapshotWork:
    """One team's phases on one aircraft with every record the dispatch rule reads for them."""

    team: str
    line: int
    phases: tuple[Phase, ...]                          # phases whose demand (or team) is this team
    jobs: tuple[Job, ...]
    dispositions: tuple[JobDisposition, ...]
    edges: tuple[Edge, ...]                            # hard edges into those phases
    predecessors: tuple[Phase, ...]                    # their phase predecessors outside ``phases``
    predecessor_dispositions: tuple[JobDisposition, ...]
    releases: tuple[ReleaseMilestone, ...]             # release predecessors and release ancestors
    facts: tuple[PhaseFacts, ...]
    shortages: tuple[Shortage, ...]                    # shortages naming one of the jobs


@dataclass(frozen=True, slots=True)
class SnapshotLabels:
    """Phase labels of one job bucket (performance, turnover and explanations label phases of any team)."""

    bucket: int
    labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class SnapshotReleases:
    releases: tuple[ReleaseMilestone, ...]             # every release milestone (scenario inputs)


@dataclass(frozen=True, slots=True)
class SnapshotHolds:
    phases: tuple[Phase, ...]                          # every IP_QA, FINAL_QA and OTHER_BUYOFF phase
    jobs: tuple[Job, ...]


# ---------------------------------------------------------------------- plan parts

@dataclass(frozen=True, slots=True)
class BurndownPoint:
    day: date
    finishing: int
    remaining: int


@dataclass(frozen=True, slots=True)
class CompletionRow:
    """An aircraft's modeled completion without its unresolved job list (projections show only its count)."""

    line: int
    status: str
    completion_at: datetime | None
    required_jobs: int
    scheduled_jobs: int
    unresolved_count: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanSummary:
    plan_id: str
    candidate_id: str
    scope: str
    purpose: Purpose
    capability: str
    source_run_id: str
    snapshot_id: str
    snapshot_hash: str
    policy_id: str
    policy_hash: str
    timezone: str
    created_at: datetime
    incumbent_id: str | None
    candidate_hash: str
    final_validation_id: str
    obligation_dates: tuple[date, ...]
    frozen_goal_ids: tuple[str, ...]
    algorithm_status: AlgorithmStatus
    global_optimality_proven: bool
    costs: CostSummary
    completion: tuple[CompletionRow, ...]
    commitment_count: int
    commitment_outcomes: dict[str, int]
    assignment_count: int
    duty_count: int
    disposition_counts: dict[str, int]
    open_jobs: int                                     # burndown: jobs neither verified complete nor excluded
    burndown: tuple[BurndownPoint, ...]
    never_finishing: int
    unscheduled_reason_codes: dict[str, int]
    conditional_reason_codes: dict[str, int]           # the twin's conditional reasons, per assignment
    lines: tuple[int, ...]
    team_days: tuple[str, ...]                         # "<team>:<date>" of every team-day part


@dataclass(frozen=True, slots=True)
class PlanCommitments:
    results: tuple[CommitmentResult, ...]
    phases: tuple[Phase, ...]                          # the plan snapshot's phases and jobs of the results
    jobs: tuple[Job, ...]


@dataclass(frozen=True, slots=True)
class PlanLine:
    line: int
    outcomes: tuple[JobOutcome, ...]


@dataclass(frozen=True, slots=True)
class PlanJobs:
    bucket: int
    assignments: tuple[Assignment, ...]
    outcomes: tuple[JobOutcome, ...]


@dataclass(frozen=True, slots=True)
class PlanTeamDay:
    """One team's assignments of one shift start date (by phase demand in the plan's snapshot), the duties of the people
    in its pools that day, the assignments those duties serve, and the plan snapshot's phases and jobs for them."""

    team: str
    day: date | None
    assignments: tuple[Assignment, ...]
    duties: tuple[Duty, ...]
    phases: tuple[Phase, ...]
    jobs: tuple[Job, ...]


@dataclass(frozen=True, slots=True)
class UnitFact:
    """The planned assignment behind a frozen earning unit."""

    assignment_id: str
    start: datetime
    end: datetime
    crew: tuple[str, ...]
    committed: bool


@dataclass(frozen=True, slots=True)
class PlanGoals:
    day: date
    facts: tuple[UnitFact, ...]


@dataclass(frozen=True, slots=True)
class PlanHolds:
    assignments: tuple[Assignment, ...]                # assignments of inspection-hold phases


@dataclass(frozen=True, slots=True)
class CandidateDigest:
    """The counts and status a plan view shows for a candidate, written beside it by the scheduler (``candidate_digests``)."""

    candidate_id: str
    purpose: Purpose
    created_at: datetime
    snapshot_id: str
    source_run_id: str
    assignment_count: int
    duty_count: int
    disposition_counts: dict[str, int]
    commitment_outcomes: dict[str, int]
    algorithm_status: AlgorithmStatus
    global_optimality_proven: bool
    costs: CostSummary


PART_TYPES: dict[str, type] = {cls.__name__: cls for cls in (
    SnapshotSummary, SnapshotLine, SnapshotStandards, SnapshotJobs, SnapshotLabels, SnapshotReleases, SnapshotPools, SnapshotWork, SnapshotHolds,
    PlanSummary, PlanCommitments, PlanLine, PlanJobs, PlanTeamDay, PlanGoals, PlanHolds)}

