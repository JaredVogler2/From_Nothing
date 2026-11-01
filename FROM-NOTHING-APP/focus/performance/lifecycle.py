"""Ordered lifecycle facts, goals of record, earning-unit facts and sequence violations (S14 OBSERVATIONS/OOS, P17).

**Goals of record (B0).** For every obligation day, the first accepted EXECUTABLE plan (verified
end to end) whose obligation set covers that day freezes B0 for every team-shift of the day. A
team-shift without units in that plan has B0 = 0. Goals frozen for the same team-shift by later
replans are proposals only: their new work counts only through an approved ``GoalAmendment``.
Original units never shrink on replan, cancellation, reopen or transfer.

**Revisions.** Revisions are applied before ordering:

* the latest source revision of a key supersedes earlier revisions;
* ``VOIDED`` removes a fact (a void can itself be voided);
* ``CORRECTION`` amends timing and verification, and the corrected fact becomes known only at the
  correction's own clocks.

Superseded facts stay stored; replaying an earlier ``as_of`` gives back what was known then.

**Ordered lifecycle.** Each phase replays its own facts and its job's administrative facts, in
effective order:

* Only a *verified* completion or a verified segment earning unit (``PROGRESS``) earns credit. A
  reported completion, terminal status, cancellation, detab or disappearance never does.
* Cancel (or detab) before completion earns nothing.
* A verified completion followed by an administrative cancel keeps or loses its credit per
  ``lifecycle.cancel_after_verified_completion``.
* Reopen revokes credit until re-verification.
* Terminal status or disappearance without verified completion is UNKNOWN: the denominator is
  never deleted and no credit is assumed.

**Credit (once only).** A verified completion credits exactly one goal-of-record unit set. The
goal is chosen by where the completion falls:

1. inside the goal's shift window;
2. otherwise inside its authorized OT window;
3. otherwise before the window (early).

A completion that lands in no goal's credit window is offplan (U) for the team-shift whose window
contains it. Earning, relief and offplan sets are disjoint by construction.

**Sequence violations.** An actual start (start event, actual interval or implied by completion)
that precedes satisfaction of a hard FS/SS predecessor in the admitted graph is recorded as a
``SequenceViolation``. So is a finish before an FF predecessor finished. The record keeps the
effective time and the incomplete prerequisites, expanding AND releases to their incomplete
leaves. Classification uses what was known when the work started:

* ``DIRECTED`` when an explicit directive exists;
* ``STARTED_WITH_KNOWN_INCOMPLETE_PREDECESSOR`` (discretionary);
* ``RELIED_ON_RECORDED_COMPLETION`` when a later void or reopen exposed the gap.

Records are immutable, so later predecessor completion never erases them. Readiness and dispatch
stay governed by the planning graph, independent of any score or charge.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..calendar import ShiftPolicy, build_shift, parse_shift_id, shift_containing
from ..contracts.evaluation import OffplanFact, OOSFact, UnitFact
from ..contracts.events import (REVISION_KINDS, ActualInterval, AttendanceEvidence, DefectEvidence, DisruptionClaim, ExcusalDecision, GoalAmendment,
                                HoldingEvidence, LifecycleEvent, ObservationRun, SequenceViolation, ShiftWindow)
from ..contracts.planning import EarningUnit, FrozenGoal, PlanningSnapshot
from ..domain import DomainError, EdgeType, PhaseKind, ReleaseStatus, digest_id, from_minute, to_minute

CANCEL_POLICIES = ("PRIOR_VERIFIED_COMPLETION_EARNS", "CANCELLATION_REVOKES_CREDIT")
REOPEN_POLICIES = ("REOPEN_REVOKES_UNTIL_REVERIFIED",)
CREDIT_WINDOW_POLICIES = ("SHIFT_WALL_PLUS_AUTHORIZED_OT",)


# ---------------------------------------------------------------------- shift windows

def shift_window(policy: Any, shift_id: str) -> ShiftWindow:
    """The team-shift window [start, end) and the planned-credit deadline (authorized OT window end)."""
    site, day, number = parse_shift_id(shift_id)
    shift = build_shift(day, number, ShiftPolicy.from_policy(policy), site)
    deadline = shift.ot_window[1] if shift.ot_window else shift.end
    return ShiftWindow(shift_id, day, number, from_minute(shift.start), from_minute(shift.end), from_minute(deadline))


def containing_shift_id(policy: Any, at: datetime, site: str) -> str | None:
    shift = shift_containing(to_minute(at), ShiftPolicy.from_policy(policy), site)
    return None if shift is None else shift.id


# ---------------------------------------------------------------------- goals of record

@dataclass(frozen=True)
class GoalRecord:
    goal_id: str
    plan_id: str                                  # the obligation plan of record for the shift day
    team: str
    shift_id: str
    window: ShiftWindow
    goal: FrozenGoal | None                       # None: the obligation plan froze no units here (B0 = 0)
    amendments: tuple[GoalAmendment, ...] = ()
    later_goal_ids: tuple[str, ...] = ()          # same team-shift frozen by later replans: proposals, never B0

    @property
    def original_units(self) -> tuple[EarningUnit, ...]:
        return self.goal.earning_units if self.goal is not None else ()

    @property
    def addition_units(self) -> tuple[EarningUnit, ...]:
        return tuple(u for a in self.amendments for u in a.units)

    @property
    def units(self) -> tuple[EarningUnit, ...]:
        return self.original_units + self.addition_units

    @property
    def B0(self) -> int:
        return sum(u.standard_person_minutes for u in self.original_units)

    @property
    def origin_of(self) -> dict[str, str]:
        out = {u.id: "ORIGINAL" for u in self.original_units}
        out.update({u.id: "AMENDMENT" for u in self.addition_units})
        return out


@dataclass
class GoalRegistry:
    """Goals of record for one scope, derived only from verified accepted EXECUTABLE publications."""

    scope: str
    site: str
    policy: Any
    day_plan: dict[date, str] = field(default_factory=dict)              # obligation day -> plan id of record
    plan_snapshot: dict[str, str] = field(default_factory=dict)          # plan id -> snapshot id
    records: dict[tuple[str, str], GoalRecord] = field(default_factory=dict)
    goals: dict[str, FrozenGoal] = field(default_factory=dict)           # every accepted goal (records and proposals)
    unit_start: dict[str, datetime] = field(default_factory=dict)        # unit id -> planned start of its frozen assignment
    unit_end: dict[str, datetime] = field(default_factory=dict)          # unit id -> planned end of its frozen assignment
    unit_crew: dict[str, tuple[str, ...]] = field(default_factory=dict)  # unit id -> the planned crew of its frozen assignment
    plan_knowledge: dict[str, datetime] = field(default_factory=dict)    # plan id -> the source cutoff it was planned on
    committed_units: set[str] = field(default_factory=set)               # units of incumbent-defended (committed) assignments
    rejected: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, services: Any, *, signer: Any = None) -> "GoalRegistry":
        from ..data.acquisition import get_run
        from ..publication.repository import repository_for
        from ..publication.service import accepted_plans

        settings = services.settings
        reg = cls(settings.scope_key, settings.site, services.policy)
        plans, bad = accepted_plans(services, settings.scope_key, "EXECUTABLE", signer=signer)
        reg.rejected = list(bad)
        repo = repository_for(services)
        proposals: dict[tuple[str, str], list[str]] = defaultdict(list)
        for current in plans:
            artifact, published = current.artifact, current.published
            reg.plan_snapshot[published.id] = artifact.snapshot_id
            source = get_run(services.repositories, published.source_run_id)
            reg.plan_knowledge[published.id] = published.accepted_at if source is None else source[0].cutoff
            goals = {g.id: g for g in repo.goals(published.frozen_goal_ids)}
            if set(goals) != set(published.frozen_goal_ids) or any(g.hash != g.computed_hash() for g in goals.values()):
                reg.rejected.append((published.id, "SECURITY_FAILURE: frozen goals missing or altered"))
                continue
            if {g.id for g in artifact.frozen_goals} != set(goals):
                reg.rejected.append((published.id, "SECURITY_FAILURE: stored goals differ from the signed artifact"))
                continue
            reg.goals.update(goals)
            new_days = [d for d in artifact.obligation_dates if d not in reg.day_plan]
            for d in new_days:
                reg.day_plan[d] = published.id
            starts = {a.id: a.start for a in artifact.candidate.assignments}
            ends = {a.id: a.end for a in artifact.candidate.assignments}
            crews = {a.id: tuple(r.person for r in a.crew) for a in artifact.candidate.assignments}
            committed = {a.id for a in artifact.candidate.assignments if a.committed}
            for g in goals.values():
                key = (g.team, g.shift_id)
                if g.shift_start_date in new_days:
                    reg.records[key] = GoalRecord(g.id, published.id, g.team, g.shift_id, shift_window(reg.policy, g.shift_id), g)
                    for u in g.earning_units:
                        if u.assignment_id in starts:
                            reg.unit_start[u.id] = starts[u.assignment_id]
                            reg.unit_end[u.id] = ends[u.assignment_id]
                            reg.unit_crew[u.id] = crews[u.assignment_id]
                        if u.assignment_id in committed:
                            reg.committed_units.add(u.id)
                else:
                    proposals[key].append(g.id)
        for key, ids in proposals.items():
            if key in reg.records:
                reg.records[key] = replace(reg.records[key], later_goal_ids=tuple(ids))
        return reg

    @classmethod
    def from_display(cls, services: Any, plans: list[Any], rejected: list[tuple[str, str]] | None = None) -> "GoalRegistry":
        """The same registry as ``load``, from accepted plans verified through their display receipts (``focus.display``).

        ``plans`` are ``display.store.VerifiedPlan`` records in acceptance order. The signed display summary carries each
        plan's obligation dates, frozen goal ids and snapshot; the ``goals:<date>`` parts carry the planned start, end,
        crew and commitment of every frozen unit's assignment. No artifact is decoded.
        """
        from ..data.acquisition import get_run
        from ..display.store import DisplayError, load_part
        from ..publication.repository import repository_for

        settings = services.settings
        reg = cls(settings.scope_key, settings.site, services.policy)
        reg.rejected = list(rejected or ())
        repo = repository_for(services)
        proposals: dict[tuple[str, str], list[str]] = defaultdict(list)
        for vp in plans:
            published, summary = vp.published, vp.summary
            reg.plan_snapshot[published.id] = summary.snapshot_id
            source = get_run(services.repositories, published.source_run_id)
            reg.plan_knowledge[published.id] = published.accepted_at if source is None else source[0].cutoff
            goals = {g.id: g for g in repo.goals(published.frozen_goal_ids)}
            if set(goals) != set(published.frozen_goal_ids) or any(g.hash != g.computed_hash() for g in goals.values()):
                reg.rejected.append((published.id, "SECURITY_FAILURE: frozen goals missing or altered"))
                continue
            if set(summary.frozen_goal_ids) != set(goals):
                reg.rejected.append((published.id, "SECURITY_FAILURE: stored goals differ from the signed artifact"))
                continue
            reg.goals.update(goals)
            new_days = [d for d in summary.obligation_dates if d not in reg.day_plan]
            for d in new_days:
                reg.day_plan[d] = published.id
            facts: dict[str, Any] = {}
            for d in new_days:
                ref = vp.manifest.part(f"goals:{d.isoformat()}")
                if ref is None:
                    continue
                try:
                    part = load_part(services.repositories, ref)
                except DisplayError as exc:
                    reg.rejected.append((published.id, f"SECURITY_FAILURE: {exc.code}: {exc.message}"))
                    continue
                facts.update({f.assignment_id: f for f in part.facts})
            for g in goals.values():
                key = (g.team, g.shift_id)
                if g.shift_start_date in new_days:
                    reg.records[key] = GoalRecord(g.id, published.id, g.team, g.shift_id, shift_window(reg.policy, g.shift_id), g)
                    for u in g.earning_units:
                        f = facts.get(u.assignment_id)
                        if f is not None:
                            reg.unit_start[u.id] = f.start
                            reg.unit_end[u.id] = f.end
                            reg.unit_crew[u.id] = f.crew
                            if f.committed:
                                reg.committed_units.add(u.id)
                else:
                    proposals[key].append(g.id)
        for key, ids in proposals.items():
            if key in reg.records:
                reg.records[key] = replace(reg.records[key], later_goal_ids=tuple(ids))
        return reg

    def goal_id_for(self, team: str, shift_id: str) -> str | None:
        record = self.records.get((team, shift_id))
        if record is not None:
            return record.goal_id
        try:
            _, day, _ = parse_shift_id(shift_id)
        except ValueError:
            return None
        plan_id = self.day_plan.get(day)
        return None if plan_id is None else digest_id("GOAL", plan_id, team, shift_id)

    def record(self, team: str, shift_id: str) -> GoalRecord | None:
        """The record of a team-shift, including a B0 = 0 record when the day's obligation plan froze nothing for it."""
        found = self.records.get((team, shift_id))
        if found is not None:
            return found
        goal_id = self.goal_id_for(team, shift_id)
        if goal_id is None:
            return None
        _, day, _ = parse_shift_id(shift_id)
        return GoalRecord(goal_id, self.day_plan[day], team, shift_id, shift_window(self.policy, shift_id), None)

    def by_goal_id(self) -> dict[str, GoalRecord]:
        return {r.goal_id: r for r in self.records.values()}


# ---------------------------------------------------------------------- revisions and ordered lifecycle

def resolve_revisions(events: Iterable[LifecycleEvent]) -> tuple[list[LifecycleEvent], dict[str, str]]:
    """Effective facts after source revisions, voids and corrections, plus a note for every fact set aside."""
    from ..data.duration_gauge import revision_rank

    notes: dict[str, str] = {}
    latest: dict[str, LifecycleEvent] = {}
    for e in sorted(events, key=lambda x: x.id):
        held = latest.get(e.source_key)
        if held is None:
            latest[e.source_key] = e
            continue
        newer, older = (e, held) if (revision_rank(e.source_revision), e.source_sequence, e.recorded_at, e.id) > \
            (revision_rank(held.source_revision), held.source_sequence, held.recorded_at, held.id) else (held, e)
        latest[e.source_key] = newer
        notes[older.id] = f"SUPERSEDED_BY_REVISION:{newer.id}"
    voiders: dict[str, list[LifecycleEvent]] = defaultdict(list)
    for e in latest.values():
        if e.kind == "VOIDED":
            voiders[e.supersedes or ""].append(e)
    memo: dict[str, bool] = {}

    def voided(key: str, trail: frozenset[str] = frozenset()) -> bool:
        if key in memo:
            return memo[key]
        if key in trail:
            return False                                          # a void cycle never removes anything
        result = any(not voided(v.source_key, trail | {key}) for v in voiders.get(key, ()))
        memo[key] = result
        return result

    corrections: dict[str, list[LifecycleEvent]] = defaultdict(list)
    for e in latest.values():
        if e.kind == "CORRECTION" and not voided(e.source_key):
            corrections[e.supersedes or ""].append(e)
    out: list[LifecycleEvent] = []
    for key, e in sorted(latest.items()):
        if e.kind in REVISION_KINDS:
            continue
        if voided(key):
            notes[e.id] = "VOIDED"
            continue
        fixes = corrections.get(key)
        if fixes:
            c = max(fixes, key=lambda x: (x.recorded_at, x.source_sequence, revision_rank(x.source_revision), x.id))
            try:
                e = replace(e, effective_at=c.effective_at, recorded_at=c.recorded_at, observed_at=c.observed_at, ingested_at=max(e.ingested_at, c.ingested_at),
                            person=c.person or e.person, verified=c.verified, verified_by=c.verified_by,
                            segments=c.segments if (e.kind == "PROGRESS" and c.segments) else e.segments, evidence=e.evidence + c.evidence,
                            detail=f"{e.detail} [corrected by {c.source_key}]"[:500])
                notes[e.id] = f"CORRECTED_BY:{c.id}"
            except DomainError as exc:
                notes[c.id] = f"CORRECTION_INVALID:{exc.code}"
        out.append(e)
    return out, notes


@dataclass(frozen=True)
class PhaseLifecycle:
    phase: str
    job: str
    disposition: str                     # OPEN | COMPLETE | CANCELLED | DETABBED | TERMINAL_UNVERIFIED | DISAPPEARED
    unknown: bool
    started_at: datetime | None
    completed_at: datetime | None        # verified full completion currently credited
    completion_event: str | None
    segments: dict[int, tuple[datetime, str]]
    cancelled_after_completion: bool
    events: tuple[str, ...]
    reasons: tuple[str, ...]


def _order(e: LifecycleEvent) -> tuple:
    return (e.effective_at, e.source_sequence, e.recorded_at, e.id)


def phase_lifecycle(phase: str, job: str, events: list[LifecycleEvent], policy: Any) -> PhaseLifecycle:
    cancel_policy = policy.str("lifecycle.cancel_after_verified_completion")
    if cancel_policy not in CANCEL_POLICIES:
        raise DomainError("POLICY_VALUE_UNSUPPORTED", f"lifecycle.cancel_after_verified_completion={cancel_policy}")
    if policy.str("lifecycle.reopen_after_completion") not in REOPEN_POLICIES:
        raise DomainError("POLICY_VALUE_UNSUPPORTED", "lifecycle.reopen_after_completion")
    state, unknown = "OPEN", False
    started: datetime | None = None
    full: tuple[datetime, str] | None = None
    segs: dict[int, tuple[datetime, str]] = {}
    after_cancel = False
    reasons: list[str] = []
    ordered = sorted(events, key=_order)
    for e in ordered:
        k, t = e.kind, e.effective_at
        if k in ("STARTED", "SEQUENCE_VIOLATION"):
            started = t if started is None else min(started, t)
        elif k == "PROGRESS":
            if not e.verified:
                reasons.append("PROGRESS_UNVERIFIED_NOT_CREDITED")
            elif state in ("CANCELLED", "DETABBED"):
                reasons.append("PROGRESS_AFTER_CANCELLATION_NOT_CREDITED")
            else:
                for s in e.segments:
                    segs.setdefault(s, (t, e.id))
                started = t if started is None else min(started, t)
        elif k == "COMPLETED":
            if not e.verified:
                reasons.append("COMPLETION_UNVERIFIED_NOT_CREDITED")
            elif state in ("CANCELLED", "DETABBED"):
                reasons.append("COMPLETED_AFTER_CANCELLATION_NOT_CREDITED")
            elif full is not None:
                reasons.append("DUPLICATE_COMPLETION_IGNORED")
            else:
                full, state, unknown = (t, e.id), "COMPLETE", False
                started = t if started is None else min(started, t)
        elif k == "REPORTED_COMPLETION":
            reasons.append("REPORTED_COMPLETION_IS_A_CLAIM")
        elif k in ("CANCELLED", "DETABBED"):
            if full is not None or segs:
                if cancel_policy == "PRIOR_VERIFIED_COMPLETION_EARNS":
                    reasons.append(f"{k}_AFTER_VERIFIED_COMPLETION_KEEPS_PRIOR_CREDIT")
                    after_cancel = True
                    if full is None:
                        state = k                         # the uncompleted remainder is cancelled; verified segments stay
                else:
                    full, segs, state = None, {}, k
                    reasons.append(f"{k}_REVOKES_PRIOR_CREDIT")
            else:
                state, unknown = k, False
                reasons.append(f"{k}_BEFORE_COMPLETION_EARNS_NOTHING")
        elif k == "TERMINAL_STATUS":
            if full is None:
                state, unknown = "TERMINAL_UNVERIFIED", True
                reasons.append("TERMINAL_STATUS_WITHOUT_VERIFIED_COMPLETION")
        elif k == "REOPENED":
            if full is not None or segs:
                full, segs = None, {}
                reasons.append("REOPENED_REVOKES_CREDIT_UNTIL_REVERIFIED")
            elif state in ("CANCELLED", "DETABBED"):
                reasons.append("REINSTATED")
            state, unknown, after_cancel = "OPEN", False, False
        elif k == "DISAPPEARED":
            if full is None and state not in ("CANCELLED", "DETABBED"):
                state, unknown = "DISAPPEARED", True
                reasons.append("DISAPPEARED_WITHOUT_TERMINAL_FACT")
    return PhaseLifecycle(phase, job, state, unknown, started, full[0] if full else None, full[1] if full else None, dict(segs),
                          after_cancel, tuple(e.id for e in ordered), tuple(dict.fromkeys(reasons)))


def lifecycles(effective: list[LifecycleEvent], phases_by_job: dict[str, set[str]], policy: Any) -> dict[str, PhaseLifecycle]:
    by_phase: dict[str, list[LifecycleEvent]] = defaultdict(list)
    job_level: dict[str, list[LifecycleEvent]] = defaultdict(list)
    for e in effective:
        if e.phase is None:
            job_level[e.job].append(e)
        else:
            by_phase[e.phase].append(e)
    out: dict[str, PhaseLifecycle] = {}
    names = set(by_phase)
    for job, phases in phases_by_job.items():
        if job in job_level:
            names |= phases
    for phase in sorted(names):
        job = phase.rsplit(".", 2)[0]
        out[phase] = phase_lifecycle(phase, job, by_phase.get(phase, []) + job_level.get(job, []), policy)
    return out


# ---------------------------------------------------------------------- admitted graph

@dataclass
class GraphView:
    snapshot_id: str
    cutoff: datetime
    phases: dict[str, Any]
    hard_in: dict[str, list[Any]]
    releases: dict[str, Any]
    job_ids: frozenset[str]
    rework_origin: dict[str, tuple[str, str]]          # rework child job -> (explicit defect-origin team, class)
    rework_jobs: frozenset[str]                        # every linked rework child (origin known or not)

    @classmethod
    def of(cls, snapshot: PlanningSnapshot) -> "GraphView":
        hard_in: dict[str, list[Any]] = defaultdict(list)
        for e in snapshot.edges:
            if e.hard:
                hard_in[e.succ_phase].append(e)
        origin = {r.child_job: (r.defect_origin_team, r.rework_class or "") for r in snapshot.rework_links
                  if r.status == "LINKED" and r.defect_origin_team}
        return cls(snapshot.id, snapshot.cutoff, {p.key.id: p for p in snapshot.phases}, dict(hard_in), {r.id: r for r in snapshot.releases},
                   frozenset(j.id for j in snapshot.jobs), origin, frozenset(r.child_job for r in snapshot.rework_links if r.status == "LINKED"))

    def team(self, phase: str) -> str | None:
        p = self.phases.get(phase)
        if p is None:
            return None
        return p.role_demands[0].team if p.role_demands else p.team

    def standard_effort(self, phase: str) -> int | None:
        p = self.phases.get(phase)
        return None if p is None or p.kind is PhaseKind.RELEASE else p.standard_person_minutes


def current_graph(services: Any) -> GraphView | None:
    """The admitted graph of the current source head (memoized per immutable run and policy)."""
    from ..data.acquisition import snapshot_for_run, source_head_name

    head = services.repositories.head(source_head_name(services.settings.scope_key))
    if head is None:
        return None
    cache = services.extras.setdefault("graph_views", {})
    key = (head.record_id, services.policy.hash)
    if key not in cache:
        cache.clear()
        cache[key] = GraphView.of(snapshot_for_run(services, head.record_id))
    return cache[key]


class Satisfaction:
    """When each graph node became satisfied, from verified lifecycle facts, confirmed releases and verified anchors."""

    def __init__(self, graph: GraphView, cycles: dict[str, PhaseLifecycle], *, known_at: datetime | None = None):
        self.graph, self.cycles, self.known_at = graph, cycles, known_at
        self._memo: dict[str, datetime | None] = {}

    def at(self, node: str, trail: frozenset[str] = frozenset()) -> datetime | None:
        if node in self._memo:
            return self._memo[node]
        if node in trail:
            return None
        result: datetime | None = None
        release = self.graph.releases.get(node)
        phase = self.graph.phases.get(node)
        if release is not None:
            if release.status is ReleaseStatus.CONFIRMED and release.ready_at is not None:
                result = release.ready_at
        elif phase is not None:
            anchor = None
            if phase.status == "COMPLETE":
                anchor = phase.actual_end or self.graph.cutoff
            cycle = self.cycles.get(node)
            if cycle is not None and cycle.completed_at is not None:
                anchor = cycle.completed_at if anchor is None else min(anchor, cycle.completed_at)
            if anchor is None and phase.kind is PhaseKind.RELEASE:
                preds = self.graph.hard_in.get(node, [])
                times = [self.at(e.pred_phase, trail | {node}) for e in preds]
                if preds and all(t is not None for t in times):
                    anchor = max(t for t in times if t is not None)
            result = anchor
        if result is not None and self.known_at is not None and result > self.known_at:
            result = None
        self._memo[node] = result
        return result

    def leaves(self, node: str, by: datetime, trail: frozenset[str] = frozenset()) -> list[str]:
        """The concrete unsatisfied prerequisites behind ``node`` at ``by`` (AND releases expanded)."""
        phase = self.graph.phases.get(node)
        if phase is None or phase.kind is not PhaseKind.RELEASE or node in trail:
            return [node]
        out: list[str] = []
        for e in self.graph.hard_in.get(node, []):
            t = self.at(e.pred_phase)
            if t is None or t > by:
                out.extend(self.leaves(e.pred_phase, by, trail | {node}))
        return out or [node]


def _ok(sat: datetime | None, lag: int, at: datetime) -> bool:
    return sat is not None and sat + timedelta(minutes=lag) <= at


def derive_sequence_violations(view: "ObservationView", graph: GraphView | None, *, detected_at: datetime) -> list[SequenceViolation]:
    if graph is None:
        return []
    starts: dict[str, list[tuple[datetime, str, str | None]]] = defaultdict(list)
    for e in view.effective:
        if e.phase is None:
            continue
        if e.kind in ("STARTED", "SEQUENCE_VIOLATION") and e.origin != "REPORTED":
            starts[e.phase].append((e.effective_at, e.id, e.directive))
        elif e.kind in ("PROGRESS", "COMPLETED") and e.verified:
            starts[e.phase].append((e.effective_at, e.id, None))
    for i in view.intervals:
        starts[i.phase].append((i.start, i.id, None))
    corroboration: dict[str, list[str]] = defaultdict(list)
    for e in view.effective:
        if e.kind == "OOS_DISCRETIONARY" and e.phase is not None:
            corroboration[e.phase].append(e.id)
    now = Satisfaction(graph, view.lifecycles)
    out: list[SequenceViolation] = []
    for phase in sorted(starts):
        if phase not in graph.phases:
            continue
        t0, ref, directive = min(starts[phase], key=lambda s: (s[0], s[1]))
        start_times = {p: min(x[0] for x in xs) for p, xs in starts.items()}
        incomplete: list[str] = []
        for e in graph.hard_in.get(phase, []):
            if e.type is EdgeType.FS and not _ok(now.at(e.pred_phase), e.lag_minutes, t0):
                incomplete.extend(now.leaves(e.pred_phase, t0))
            elif e.type is EdgeType.SS:
                pred_start = start_times.get(e.pred_phase) or now.at(e.pred_phase)
                if not _ok(pred_start, e.lag_minutes, t0):
                    incomplete.append(e.pred_phase)
        if incomplete:
            out.append(_violation(view, graph, phase, "START", t0, ref, directive, incomplete, corroboration.get(phase, []), detected_at))
        cycle = view.lifecycles.get(phase)
        if cycle is not None and cycle.completed_at is not None:
            tc = cycle.completed_at
            ff = [x for e in graph.hard_in.get(phase, []) if e.type is EdgeType.FF and not _ok(now.at(e.pred_phase), e.lag_minutes, tc)
                  for x in now.leaves(e.pred_phase, tc)]
            if ff:
                out.append(_violation(view, graph, phase, "FINISH", tc, cycle.completion_event or "", None, ff, corroboration.get(phase, []), detected_at))
    return out


def _violation(view: "ObservationView", graph: GraphView, phase: str, trigger: str, at: datetime, ref: str, directive: str | None,
               incomplete: list[str], corroboration: list[str], detected_at: datetime) -> SequenceViolation:
    known_events = [e for e in view.events if e.recorded_at <= at]
    known_effective, _ = resolve_revisions(known_events)
    known_cycles = lifecycles(known_effective, view.phases_by_job, view.policy)
    known = Satisfaction(graph, known_cycles, known_at=at)
    incomplete = sorted(dict.fromkeys(incomplete))
    known_incomplete = tuple(p for p in incomplete if known.at(p) is None)
    if directive:
        discretionary, why = False, "DIRECTED"
    elif known_incomplete:
        discretionary, why = True, "STARTED_WITH_KNOWN_INCOMPLETE_PREDECESSOR" if trigger == "START" else "FINISHED_WITH_KNOWN_INCOMPLETE_PREDECESSOR"
    else:
        discretionary, why = False, "RELIED_ON_RECORDED_COMPLETION"
    return SequenceViolation(
        id=digest_id("OOS", view.scope, phase, trigger, ref), scope=view.scope, job=phase.rsplit(".", 2)[0], phase=phase, effective_at=at,
        trigger=trigger, trigger_ref=ref, incomplete_predecessors=tuple(incomplete), known_incomplete=known_incomplete, discretionary=discretionary,
        classification=why, directive=directive, graph_snapshot_id=graph.snapshot_id, origin="DERIVED", detected_at=detected_at,
        evidence=tuple([ref] + sorted(corroboration)))


def derive_disappearances(view: "ObservationView", graph: GraphView, *, ingested_at: datetime) -> list[LifecycleEvent]:
    """Planned work whose job left the admitted census with no completion/cancel/terminal fact: disposition becomes UNKNOWN."""
    out: list[LifecycleEvent] = []
    at = min(graph.cutoff, ingested_at)
    planned: dict[str, set[str]] = defaultdict(set)
    for record in view.records():
        for unit in record.units:
            if unit.job not in graph.job_ids:
                planned[unit.job].add(unit.phase)
    for job, phases in sorted(planned.items()):
        def resolved(p: str) -> bool:
            c = view.lifecycles.get(p)
            return c is not None and (c.completed_at is not None or c.disposition in ("CANCELLED", "DETABBED", "TERMINAL_UNVERIFIED", "DISAPPEARED"))

        if all(resolved(p) for p in phases):
            continue
        out.append(LifecycleEvent(
            id=digest_id("EVT", view.scope, "derived", "DISAPPEARED", job, graph.snapshot_id), scope=view.scope, feed="derived",
            source_key=f"derived:DISAPPEARED:{job}:{graph.snapshot_id}", origin="DERIVED", kind="DISAPPEARED", job=job, phase=None,
            segments=(), effective_at=at, recorded_at=at, observed_at=at, ingested_at=ingested_at, source_sequence=0,
            source_revision=graph.snapshot_id, person=None, verified=False, verified_by=None, directive=None, supersedes=None, evidence=(),
            detail=f"job absent from admitted census {graph.snapshot_id} with unresolved planned work and no verified completion or terminal fact"))
    return out


# ---------------------------------------------------------------------- the evaluation-ready view

@dataclass(frozen=True)
class Credit:
    unit_id: str
    goal_id: str
    at: datetime
    event_id: str


def select_goal(at: datetime, candidates: list[GoalRecord]) -> GoalRecord | None:
    """The one goal a completion at ``at`` credits: shift window, then authorized OT window, then the next planned (early)."""
    proper = [r for r in candidates if r.window.start <= at < r.window.end]
    if proper:
        return min(proper, key=lambda r: (r.window.start, r.goal_id))
    grace = [r for r in candidates if r.window.end <= at < r.window.credit_deadline]
    if grace:
        return min(grace, key=lambda r: (r.window.start, r.goal_id))
    early = [r for r in candidates if at < r.window.start]
    if early:
        return min(early, key=lambda r: (r.window.start, r.goal_id))
    return None


@dataclass
class ObservationView:
    services: Any
    policy: Any
    scope: str
    site: str
    as_of: datetime
    registry: GoalRegistry
    events: list[LifecycleEvent]
    effective: list[LifecycleEvent]
    notes: dict[str, str]
    lifecycles: dict[str, PhaseLifecycle]
    phases_by_job: dict[str, set[str]]
    intervals: list[ActualInterval]
    invalid_refs: frozenset[str]
    defects: list[DefectEvidence]
    amendments: dict[str, tuple[GoalAmendment, ...]]
    claims: list[DisruptionClaim]
    decisions: list[ExcusalDecision]
    violations: list[SequenceViolation]
    run: ObservationRun | None
    graph: GraphView | None
    attendance: list[AttendanceEvidence] = field(default_factory=list)
    holding_events: list[HoldingEvidence] = field(default_factory=list)
    credits: dict[str, Credit] = field(default_factory=dict)
    offplan: list[OffplanFact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _records: list[GoalRecord] | None = field(default=None, repr=False)

    @classmethod
    def build(cls, services: Any, as_of: datetime, *, store: Any = None, registry: GoalRegistry | None = None,
              graph: GraphView | None | str = "current") -> "ObservationView":
        from .actuals import (AMENDMENTS, ATTENDANCE, CLAIMS, DECISIONS, DEFECTS, EVENTS, HOLDINGS, INTERVALS, VIOLATIONS, ObservationStore,
                              latest_revisions)

        settings = services.settings
        store = store or ObservationStore(services.repositories, settings.scope_key)
        registry = registry or GoalRegistry.load(services)
        policy = services.policy
        events = store.known(EVENTS, as_of)
        effective, notes = resolve_revisions(events)
        all_intervals = store.known(INTERVALS, as_of)
        latest_iv = latest_revisions(all_intervals)
        intervals = [i for i in latest_iv if not i.voided]
        effective_ids = {e.id for e in effective}
        invalid = {e.id for e in events if e.id not in effective_ids} | {i.id for i in all_intervals if i not in intervals}
        amendments: dict[str, list[GoalAmendment]] = defaultdict(list)
        for a in store.known(AMENDMENTS, as_of):
            amendments[a.goal_id].append(a)
        phases_by_job: dict[str, set[str]] = defaultdict(set)
        for record in registry.records.values():
            for u in record.units:
                phases_by_job[u.job].add(u.phase)
        for items in amendments.values():
            for a in items:
                for u in a.units:
                    phases_by_job[u.job].add(u.phase)
        for e in effective:
            if e.phase is not None:
                phases_by_job[e.job].add(e.phase)
        errors: list[str] = []
        try:
            cycles = lifecycles(effective, phases_by_job, policy)
        except DomainError as exc:
            cycles, errors = {}, [f"{exc.code}: {exc.message}"]
        view = cls(services, policy, settings.scope_key, settings.site, as_of, registry, events, effective, notes, cycles, dict(phases_by_job),
                   intervals, frozenset(invalid), store.known(DEFECTS, as_of), {k: tuple(sorted(v, key=lambda a: a.id)) for k, v in amendments.items()},
                   store.known(CLAIMS, as_of), store.known(DECISIONS, as_of), store.known(VIOLATIONS, as_of), store.latest_run(as_of),
                   current_graph(services) if graph == "current" else graph, attendance=store.known(ATTENDANCE, as_of),  # type: ignore[arg-type]
                   holding_events=store.known(HOLDINGS, as_of), errors=errors)
        view._credit()
        return view

    # -- goal records with their known amendments (fixed once the view is built)
    def records(self) -> list[GoalRecord]:
        if self._records is not None:
            return list(self._records)
        out = []
        known = set()
        for record in self.registry.records.values():
            known.add(record.goal_id)
            out.append(replace(record, amendments=self.amendments.get(record.goal_id, ())))
        for goal_id, items in self.amendments.items():
            if goal_id in known or not items:
                continue
            unit = items[0].units[0]
            base = self.registry.record(unit.team, unit.shift_id)
            if base is not None and base.goal_id == goal_id:
                out.append(replace(base, amendments=items))
        self._records = sorted(out, key=lambda r: (r.window.start, r.team, r.goal_id))
        return list(self._records)

    def record(self, team: str, shift_id: str) -> GoalRecord | None:
        base = self.registry.record(team, shift_id)
        if base is None:
            return None
        return replace(base, amendments=self.amendments.get(base.goal_id, ()))

    def record_by_id(self, goal_id: str) -> GoalRecord | None:
        return next((r for r in self.records() if r.goal_id == goal_id), None)

    # -- once-only credit and offplan
    def _credit(self) -> None:
        records = self.records()
        by_phase: dict[str, list[GoalRecord]] = defaultdict(list)
        by_segment: dict[tuple[str, int], list[GoalRecord]] = defaultdict(list)
        for r in records:
            for u in r.units:
                if r not in by_phase[u.phase]:
                    by_phase[u.phase].append(r)
                by_segment[(u.phase, u.segment)].append(r)
        credited_phases: set[tuple[str, datetime]] = set()
        for phase, cycle in sorted(self.lifecycles.items()):
            if cycle.completed_at is not None:
                chosen = select_goal(cycle.completed_at, by_phase.get(phase, []))
                if chosen is not None:
                    for u in chosen.units:
                        if u.phase == phase and u.id not in self.credits:
                            self.credits[u.id] = Credit(u.id, chosen.goal_id, cycle.completed_at, cycle.completion_event or "")
                    credited_phases.add((phase, cycle.completed_at))
                else:
                    self._offplan(phase, cycle.completed_at, cycle.completion_event or "", by_phase.get(phase, []))
            for seg, (at, event_id) in sorted(cycle.segments.items()):
                chosen = select_goal(at, by_segment.get((phase, seg), []))
                if chosen is None:
                    continue                                   # an unplanned segment is not an earning unit: no credit, no offplan value
                for u in chosen.units:
                    if u.phase == phase and u.segment == seg and u.id not in self.credits:
                        self.credits[u.id] = Credit(u.id, chosen.goal_id, at, event_id)

    def _offplan(self, phase: str, at: datetime, event_id: str, planned: list[GoalRecord]) -> None:
        shift_id = containing_shift_id(self.policy, at, self.site)
        if shift_id is None:
            return
        reasons: list[str] = []
        prior = [r for r in planned if r.window.credit_deadline <= at]
        if prior:
            latest = max(prior, key=lambda r: (r.window.start, r.goal_id))
            units = [u for u in latest.units if u.phase == phase and u.id not in self.credits]
            effort: int | None = sum(u.standard_person_minutes for u in units)
            team: str | None = units[0].team if units else next((u.team for u in latest.units if u.phase == phase), None)
            reasons.append(f"LATE_FOR_GOAL:{latest.goal_id}")
        else:
            team = self.graph.team(phase) if self.graph else None
            effort = self.graph.standard_effort(phase) if self.graph else None
            if effort is None:
                reasons.append("STANDARD_EFFORT_UNKNOWN")
            reasons.append("NOT_IN_ANY_GOAL_OF_RECORD")
        if team is None:
            reasons.append("TEAM_UNKNOWN")
            team = "UNKNOWN"
        self.offplan.append(OffplanFact(digest_id("OFP", self.scope, phase, at, event_id), team, shift_id, phase.rsplit(".", 2)[0], phase, effort, at,
                                        event_id, tuple(reasons)))

    def offplan_for(self, team: str, shift_id: str) -> list[OffplanFact]:
        return [o for o in self.offplan if o.team == team and o.shift_id == shift_id]

    # -- unit facts
    def unit_facts(self, record: GoalRecord) -> list[UnitFact]:
        origin = record.origin_of
        relief = self.relief_units(record)
        charged = {u for o in self.oos_for(record) if o.charged for u in o.unit_ids}
        out = []
        for u in record.units:
            cycle = self.lifecycles.get(u.phase)
            credit = self.credits.get(u.id)
            reasons: list[str] = list(cycle.reasons) if cycle else []
            if credit is not None:
                status = "EARNED"
            elif cycle is not None and cycle.unknown:
                status = "UNKNOWN"
            elif record.window.credit_deadline > self.as_of:
                status = "PENDING"
            else:
                status = "MISSED"
                if cycle is not None and cycle.completed_at is not None:
                    reasons.append("COMPLETION_CREDITED_ELSEWHERE_OR_LATE")
                if cycle is not None and cycle.disposition in ("CANCELLED", "DETABBED"):
                    reasons.append("WITHDRAWN_WORK_STAYS_IN_B0")
            out.append(UnitFact(u.id, record.goal_id, u.job, u.phase, u.segment, u.team, u.standard_person_minutes, origin[u.id], status,
                                credit.at if credit else None, credit.event_id if credit else None, status == "MISSED" and u.id in relief,
                                u.id in charged, tuple(dict.fromkeys(reasons))))
        return out

    def relief_units(self, record: GoalRecord) -> set[str]:
        from .disruption import approved_units

        missed = {u.id for u in record.units if u.id not in self.credits}
        return approved_units(self, record.goal_id) & missed

    # -- out-of-sequence charges (once per planned unit)
    def oos_for(self, record: GoalRecord) -> list[OOSFact]:
        records = self.records()
        out: list[OOSFact] = []
        charged: set[str] = set()
        for v in sorted(self.violations, key=lambda x: (x.effective_at, x.id)):
            units = [u for u in record.units if u.phase == v.phase]
            if not units:
                continue
            chosen = select_goal(v.effective_at, [r for r in records if any(u.phase == v.phase for u in r.units)])
            if chosen is None or chosen.goal_id != record.goal_id:
                continue
            if v.trigger_ref in self.invalid_refs:
                out.append(OOSFact(v.id, v.phase, tuple(u.id for u in units), 0, v.discretionary, True, False, "TRIGGER_EVIDENCE_VOIDED"))
                continue
            fresh = [u for u in units if u.id not in charged]
            effort = sum(u.standard_person_minutes for u in fresh)
            if v.discretionary is True and fresh:
                charged.update(u.id for u in fresh)
                out.append(OOSFact(v.id, v.phase, tuple(u.id for u in fresh), effort, True, True, True, v.classification))
            elif v.discretionary is None:
                out.append(OOSFact(v.id, v.phase, tuple(u.id for u in fresh), effort, None, True, False, "CLASSIFICATION_UNKNOWN"))
            else:
                reason = v.classification if v.discretionary is False else "ALREADY_CHARGED_ONCE"
                out.append(OOSFact(v.id, v.phase, tuple(u.id for u in units), 0, v.discretionary, True, False, reason))
        return out

    def offplan_oos(self, team: str, shift_id: str) -> list[str]:
        """Violations on offplan work: the effort is already in U, so no second numeric charge."""
        phases = {o.phase for o in self.offplan_for(team, shift_id)}
        return sorted(v.id for v in self.violations if v.phase in phases)
