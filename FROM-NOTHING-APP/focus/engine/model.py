"""Engine context: one read-only view of an immutable PlanningSnapshot for one purpose (S12 MAX step 1).

Every engine path (seed, redispatch, repair, continuity, neighborhoods, commitment defense)
reads the same context: phase graph and hard typed edges, release floors, same-run CS facts,
line calendars and eligible shifts, pure pools and their purpose-specific members and caps,
person windows, eligibility, and CPM tails. Purpose decides membership and readiness:

* EXECUTABLE: only executable pool members and their caps; jobs with executable-only gaps,
  conditional part releases and synthetic people are not placeable.
* FORECAST: forecast members (nominal and virtual included) and forecast caps; conditional
  part ETAs act as floors, and every such assignment is labelled conditional.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from ..calendar import CalendarService, ShiftInstance, ShiftPolicy, build_shift, shifts_between
from ..contracts.planning import Edge, Phase, PlanningSnapshot, Pool
from ..domain import Disposition, DutyKind, EdgeType, PhaseKind, Purpose, ReleaseStatus, minute_ceil, to_minute
from ..eligibility import Eligibility
from ..policy import Policy
from ..simulation.pools import is_synthetic_person
from .ledger import DutyLedger, PersonWindow, ToolCalendar
from .travel import TravelPolicy

CONCURRENCY_TYPES = (EdgeType.CONCURRENT, EdgeType.NONCONCURRENT)


def shift_id_of(site: str, day: date, number: int) -> str:
    return f"{site}:{day.isoformat()}:S{number}"


@dataclass(frozen=True)
class Placeability:
    placeable: bool
    satisfied: bool                # verified-complete anchor
    conditional: bool              # forecast-only evidence involved
    reasons: tuple[str, ...]


class EngineContext:
    def __init__(self, snapshot: PlanningSnapshot, policy: Policy, purpose: Purpose, *,
                 tool_capacity: Mapping[str, int] | None = None, tool_requirements: Mapping[str, tuple[str, ...]] | None = None):
        if snapshot.policy_hash != policy.hash:
            raise ValueError(f"snapshot policy {snapshot.policy_hash[:12]} differs from engine policy {policy.hash[:12]}")
        self.snapshot = snapshot
        self.policy = policy
        self.purpose = purpose
        self.site = snapshot.site
        self.cutoff_minute = minute_ceil(snapshot.cutoff)
        self.shift_policy = ShiftPolicy.from_policy(policy)
        self.travel = TravelPolicy.from_policy(policy)
        self.counted_kinds = frozenset(DutyKind(k) for k in policy.seq("activation.counted_duty_kinds"))
        self.crew_during_ip = policy.str("qa.crew_during_ip")
        self.tool_capacity = dict(tool_capacity or {})
        self.tool_requirements = dict(tool_requirements or {})
        self.jobs = {j.id: j for j in snapshot.jobs}
        self.phases: dict[str, Phase] = {p.key.id: p for p in snapshot.phases}
        self.releases = {r.id: r for r in snapshot.releases}
        self.dispositions = {d.job: d for d in snapshot.dispositions}
        self.durations = {d.job: d for d in snapshot.durations}
        self.people = {p.id: p for p in snapshot.people}
        self.pools: dict[str, Pool] = {p.id: p for p in snapshot.pools}
        self.pool_by_key = {(p.key.team, p.key.start_date, p.key.shift_number): p for p in snapshot.pools}
        self.calendar = CalendarService({c.line: c for c in snapshot.calendars}, self.shift_policy, self.site)
        self.cs = {(f.line, f.station): f for f in snapshot.cs_facts}
        self.economics = {e.line: e for e in snapshot.economics}
        self.eligibility = Eligibility(snapshot.holdings, {r.job: r for r in snapshot.requirements},
                                       source_run_id=snapshot.source_run_id, policy_hash=snapshot.policy_hash)
        # ---- hard typed edges between phases / milestones
        self.preds: dict[str, list[Edge]] = defaultdict(list)
        self.succs: dict[str, list[Edge]] = defaultdict(list)
        self.unsupported_edges: list[Edge] = []
        for e in snapshot.edges:
            if not e.hard:
                continue
            if e.type in CONCURRENCY_TYPES:
                self.unsupported_edges.append(e)
                continue
            self.preds[e.succ_phase].append(e)
            self.succs[e.pred_phase].append(e)
        self.job_phases: dict[str, list[Phase]] = defaultdict(list)
        for p in snapshot.phases:
            self.job_phases[p.job].append(p)
        for plist in self.job_phases.values():
            plist.sort(key=lambda p: (p.kind is PhaseKind.RELEASE, p.key.id))
        # ---- pools, members, windows and caps for the purpose
        self.pool_caps: dict[str, int] = {}
        self._members: dict[str, tuple[str, ...]] = {}
        for pool in snapshot.pools:
            if purpose is Purpose.EXECUTABLE:
                members = tuple(m for m in pool.executable_members if m in self.people and not is_synthetic_person(self.people[m]))
                cap = pool.cap_executable
            else:
                members = tuple(pool.forecast_members)
                cap = pool.cap_forecast
            self._members[pool.id] = members
            self.pool_caps[pool.id] = cap
        member_sets = {pid: set(m) for pid, m in self._members.items()}
        self.windows: dict[tuple[str, str], PersonWindow] = {}
        for av in snapshot.availability:
            pool = self.pools.get(av.pool)
            if pool is None or av.person not in member_sets.get(av.pool, ()):
                continue
            sid = shift_id_of(pool.key.site, pool.key.start_date, pool.key.shift_number)
            self.windows[(av.person, sid)] = PersonWindow(
                person=av.person, shift_id=sid, pool_id=pool.id,
                regular=tuple(i.as_minutes() for i in av.intervals), ot=tuple(i.as_minutes() for i in av.ot_intervals),
                regular_cap=av.regular_cap, ot_cap=av.ot_cap)
        # ---- eligible shifts per line (bounded calendar, S3 rule)
        self._line_shifts: dict[int, list[ShiftInstance]] = {}
        # ---- scarcity: certificates demanded per team
        self.team_certs: dict[str, frozenset[str]] = {}
        demanded: dict[str, set[str]] = defaultdict(set)
        for p in snapshot.phases:
            for d in p.role_demands:
                demanded[d.team] |= set(d.bundle.all_codes)
        self.team_certs = {t: frozenset(c) for t, c in demanded.items()}
        self.tails = self._tails()

    # ---------------------------------------------------------------- readiness
    def placeability(self, job_id: str) -> Placeability:
        disp = self.dispositions.get(job_id)
        if disp is None:
            return Placeability(False, False, False, ("DISPOSITION_MISSING",))
        if disp.disposition is Disposition.VERIFIED_COMPLETE:
            return Placeability(False, True, False, disp.reasons)
        if disp.disposition is not Disposition.ADMITTED:
            return Placeability(False, False, False, disp.reasons or (disp.disposition.value,))
        if disp.executable_reasons:
            if self.purpose is Purpose.EXECUTABLE:
                return Placeability(False, False, False, disp.executable_reasons)
            return Placeability(True, False, True, disp.executable_reasons)
        return Placeability(True, False, bool(disp.reasons), disp.reasons)

    def release_floor(self, release_id: str) -> tuple[int | None, bool, str]:
        """(floor minute, conditional, reason) for a zero-work release milestone."""
        rel = self.releases.get(release_id)
        if rel is None:
            return None, False, f"RELEASE_UNKNOWN: {release_id}"
        if rel.status is ReleaseStatus.CONFIRMED and rel.ready_at is not None:
            return minute_ceil(rel.ready_at), False, "CONFIRMED"
        if self.purpose is Purpose.EXECUTABLE:
            return None, False, f"PART_NOT_RECEIVED: {rel.label} has no confirmed receipt"
        if rel.status is ReleaseStatus.CONDITIONAL and rel.forecast_at is not None:
            return minute_ceil(rel.forecast_at), True, f"conditional forecast floor ({rel.precision})"
        return None, False, f"PART_RELEASE_UNKNOWN: {rel.label}"

    def job_floor(self, job_id: str) -> tuple[int, list[str]]:
        job = self.jobs[job_id]
        floor = self.cutoff_minute
        notes: list[str] = []
        if job.earliest is not None:
            floor = max(floor, minute_ceil(job.earliest))
        facts = self.cs.get((job.line, job.station))
        if facts is not None and facts.earliest_at is not None:
            floor = max(floor, to_minute(facts.earliest_at))
            notes.append(f"CS{facts.station} earliest")
        return floor, notes

    # ---------------------------------------------------------------- resources
    def line_shifts(self, line: int) -> list[ShiftInstance]:
        cached = self._line_shifts.get(line)
        if cached is None:
            bounds = self.calendar.bounds(line)
            cached = []
            if bounds is not None:
                for s in shifts_between(bounds[0], bounds[1], self.shift_policy, self.site):
                    if self.calendar.eligible(line, s).allowed:
                        cached.append(s)
            self._line_shifts[line] = cached
        return cached

    def pool_for(self, team: str, shift: ShiftInstance) -> Pool | None:
        return self.pool_by_key.get((team, shift.start_date, shift.number))

    def pool_members(self, pool_id: str) -> tuple[str, ...]:
        return self._members.get(pool_id, ())

    def qualifies(self, person: str, bundle, start: int, end: int) -> bool:
        if not bundle.groups:
            return True
        return self.eligibility.index.check_bundle(person, bundle, start, end).allowed

    def usable_applicable(self, person: str, team: str, start: int, end: int) -> int:
        return len(self.eligibility.index.usable_certs(person, start, end, self.team_certs.get(team, frozenset())))

    def new_ledger(self) -> DutyLedger:
        ledger = DutyLedger(self.windows, self.travel, self.pool_caps, self.counted_kinds, ToolCalendar(self.tool_capacity))
        for ci in self.snapshot.carry_in:
            start, end = to_minute(ci.start), to_minute(ci.end)
            shift = None
            for (person, sid), window in self.windows.items():
                if person == ci.person and window.span[0] <= start < window.span[1]:
                    shift = (sid, window.pool_id)
                    break
            sid, pool_id = shift if shift else (f"carry:{ci.id}", None)
            ledger.add_locked(f"CI:{ci.id}", ci.person, start, end, ci.kind, ci.line, sid, pool_id)
        return ledger

    def shift(self, day: date, number: int) -> ShiftInstance:
        return build_shift(day, number, self.shift_policy, self.site)

    # ---------------------------------------------------------------- CPM tails
    def _tails(self) -> dict[str, int]:
        nodes = set(self.phases) | set(self.releases)
        indeg = {n: 0 for n in nodes}
        for n in nodes:
            for e in self.succs.get(n, ()):
                if e.succ_phase in indeg:
                    indeg[e.succ_phase] += 1
        order: list[str] = []
        ready = sorted(n for n, d in indeg.items() if d == 0)
        while ready:
            n = ready.pop()
            order.append(n)
            for e in self.succs.get(n, ()):
                if e.succ_phase in indeg:
                    indeg[e.succ_phase] -= 1
                    if indeg[e.succ_phase] == 0:
                        ready.append(e.succ_phase)
        tails: dict[str, int] = {}
        for n in reversed(order):
            own = self.phases[n].work_minutes if n in self.phases else 0
            tails[n] = own + max((tails.get(e.succ_phase, 0) for e in self.succs.get(n, ())), default=0)
        for n in nodes:
            tails.setdefault(n, self.phases[n].work_minutes if n in self.phases else 0)
        return tails
