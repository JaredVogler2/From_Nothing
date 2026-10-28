"""Validator input: an independent source view of one sealed snapshot (S13, P15).

The view is built from two things only:

* the ``PlanningSnapshot``: source facts plus the input authorities' admitted derivations, all
  bound to one source run and verified by hash;
* the policy.

It never reads what a candidate says about itself: certificate evidence ids, duration estimate
ids, release ids, conditional reasons, committed flags, caps. It never imports the optimizer
(``focus.engine``). The one shared rule authority is the calendar geometry
(``focus.calendar.build_shift``: shift instants, protected windows, the no-start coordinate and
DST). Everything else is re-derived here:

* whole-interval certificate holding;
* the S3 workday exception;
* purpose membership and the activation cap on the distinct admitted denominator;
* the synthetic-origin rule;
* purpose-specific release floors.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from ..calendar import ShiftInstance, ShiftPolicy, build_shift, parse_shift_id
from ..contracts.planning import (CSFacts, Edge, Job, JobDisposition, JobRequirement, LineCalendar, Person, Phase, PlanningSnapshot, Pool,
                                  RawCSRow, ReleaseMilestone)
from ..domain import Disposition, EdgeType, Holding, PersonOrigin, PhaseKind, Purpose, ReleaseStatus, RequirementBundle, minute_ceil, minute_floor
from ..policy import Policy

VALIDATOR_VERSION = "focus-independent-validator-1.0"
TYPED_EDGES = (EdgeType.FS, EdgeType.SS, EdgeType.FF, EdgeType.SF)
CONCURRENCY_EDGES = (EdgeType.CONCURRENT, EdgeType.NONCONCURRENT)
VIRTUAL_NAMESPACE = "synthetic-virtual-support"      # S07 origin namespace of virtual support people
WORKING = "WORKING"


@dataclass(frozen=True)
class NodeTime:
    start: int
    end: int
    conditional: bool
    basis: str                       # PLACED | SATISFIED | RELEASE_CONFIRMED | RELEASE_CONDITIONAL | DERIVED_RELEASE


@dataclass
class ValidationInput:
    snapshot: PlanningSnapshot
    policy: Policy
    purpose: Purpose
    shift_policy: ShiftPolicy
    cutoff: int
    jobs: dict[str, Job] = field(default_factory=dict)
    phases: dict[str, Phase] = field(default_factory=dict)
    job_phases: dict[str, list[Phase]] = field(default_factory=dict)
    dispositions: dict[str, JobDisposition] = field(default_factory=dict)
    releases: dict[str, ReleaseMilestone] = field(default_factory=dict)
    hard_in: dict[str, list[Edge]] = field(default_factory=dict)
    hard_out: dict[str, list[Edge]] = field(default_factory=dict)
    concurrency: list[Edge] = field(default_factory=list)
    calendars: dict[int, LineCalendar] = field(default_factory=dict)
    day_status: dict[int, dict[date, str]] = field(default_factory=dict)
    cs: dict[tuple[int, str], CSFacts] = field(default_factory=dict)
    raw_cs: dict[tuple[int, str], list[RawCSRow]] = field(default_factory=dict)
    people: dict[str, Person] = field(default_factory=dict)
    pools: dict[str, Pool] = field(default_factory=dict)
    pool_by_key: dict[tuple[str, date, int], Pool] = field(default_factory=dict)
    availability: dict[tuple[str, str], object] = field(default_factory=dict)
    holdings: dict[str, dict[str, list[Holding]]] = field(default_factory=dict)
    requirements: dict[str, JobRequirement] = field(default_factory=dict)
    durations: dict[str, object] = field(default_factory=dict)

    # ------------------------------------------------------------------ construction
    @classmethod
    def build(cls, snapshot: PlanningSnapshot, policy: Policy, purpose: Purpose) -> "ValidationInput":
        inp = cls(snapshot=snapshot, policy=policy, purpose=purpose, shift_policy=ShiftPolicy.from_policy(policy),
                  cutoff=minute_ceil(snapshot.cutoff))
        inp.jobs = {j.id: j for j in snapshot.jobs}
        inp.phases = {p.key.id: p for p in snapshot.phases}
        job_phases: dict[str, list[Phase]] = defaultdict(list)
        for p in snapshot.phases:
            job_phases[p.job].append(p)
        inp.job_phases = dict(job_phases)
        inp.dispositions = {d.job: d for d in snapshot.dispositions}
        inp.releases = {r.id: r for r in snapshot.releases}
        hard_in: dict[str, list[Edge]] = defaultdict(list)
        hard_out: dict[str, list[Edge]] = defaultdict(list)
        for e in snapshot.edges:
            if not e.hard:
                continue
            if e.type in CONCURRENCY_EDGES:
                inp.concurrency.append(e)
                continue
            hard_in[e.succ_phase].append(e)
            hard_out[e.pred_phase].append(e)
        inp.hard_in, inp.hard_out = dict(hard_in), dict(hard_out)
        inp.calendars = {c.line: c for c in snapshot.calendars}
        inp.day_status = {c.line: {d.day: d.status for d in c.days} for c in snapshot.calendars}
        inp.cs = {(f.line, f.station): f for f in snapshot.cs_facts}
        raw: dict[tuple[int, str], list[RawCSRow]] = defaultdict(list)
        for r in snapshot.raw_cs_rows:
            raw[(r.line, str(r.control_station))].append(r)
        inp.raw_cs = dict(raw)
        inp.people = {p.id: p for p in snapshot.people}
        inp.pools = {p.id: p for p in snapshot.pools}
        inp.pool_by_key = {(p.key.team, p.key.start_date, p.key.shift_number): p for p in snapshot.pools}
        inp.availability = {(a.person, a.pool): a for a in snapshot.availability}
        holdings: dict[str, dict[str, list[Holding]]] = defaultdict(lambda: defaultdict(list))
        for h in snapshot.holdings:
            holdings[h.person][h.cert].append(h)
        inp.holdings = {person: dict(certs) for person, certs in holdings.items()}
        inp.requirements = {r.job: r for r in snapshot.requirements}
        inp.durations = {d.job: d for d in snapshot.durations}
        return inp

    # ------------------------------------------------------------------ people and pools
    def synthetic(self, person_id: str) -> bool:
        """Origin evidence decides (S07): a spoofed flag never makes a virtual person real; an unknown person is not real."""
        p = self.people.get(person_id)
        if p is None:
            return True
        return (p.origin is PersonOrigin.SYNTHETIC_VIRTUAL or p.synthetic or p.purpose_scope == "FORECAST_ONLY"
                or p.key.authority_namespace == VIRTUAL_NAMESPACE)

    def admitted_members(self, pool: Pool) -> frozenset[str]:
        """Distinct people admitted to the pool for this purpose. Synthetic people are never executable."""
        if self.purpose is Purpose.EXECUTABLE:
            return frozenset(m for m in pool.executable_members if m in self.people and not self.synthetic(m))
        return frozenset(m for m in pool.forecast_members if m in self.people)

    def activation_cap(self, pool: Pool) -> int:
        """floor(numerator * N / denominator) on the distinct admitted denominator; no minimum-one exception."""
        n = len(self.admitted_members(pool))
        cap = (self.policy.int("activation.fraction_numerator") * n) // self.policy.int("activation.fraction_denominator")
        if self.policy.bool("activation.minimum_one_exception") and n > 0:
            cap = max(cap, 1)
        return cap

    # ------------------------------------------------------------------ certificates
    def holds(self, person: str, cert: str, start: int, end: int) -> tuple[bool, str]:
        """Whole-interval holding of one certificate: ACTIVE, known validity covering [start, end), not revoked before end."""
        items = self.holdings.get(person, {}).get(cert)
        if not items:
            return False, "NOT_HELD"
        reasons: list[str] = []
        for h in items:
            if h.status != "ACTIVE":
                reasons.append(f"STATUS_{h.status}")
                continue
            if h.validity not in ("BOUNDED", "PERPETUAL") or h.valid_from is None:
                reasons.append("VALIDITY_UNKNOWN")
                continue
            if minute_floor(h.valid_from) > start:
                reasons.append("NOT_YET_VALID")
                continue
            if h.validity == "BOUNDED" and (h.valid_to is None or minute_floor(h.valid_to) < end):
                reasons.append("EXPIRED")
                continue
            if h.revoked_at is not None and minute_floor(h.revoked_at) < end:
                reasons.append("REVOKED")
                continue
            return True, "VALID"
        return False, sorted(reasons)[0]

    def bundle_held(self, person: str, bundle: RequirementBundle, start: int, end: int) -> tuple[bool, str]:
        missing = []
        for group in bundle.groups:
            why = []
            for cert in group:
                ok, reason = self.holds(person, cert, start, end)
                if ok:
                    break
                why.append(f"{cert}:{reason}")
            else:
                missing.append("(" + " OR ".join(why) + ")")
        return (not missing), " AND ".join(missing)

    # ------------------------------------------------------------------ calendar
    def shift(self, shift_id: str | None) -> ShiftInstance | None:
        if not shift_id:
            return None
        try:
            site, day, number = parse_shift_id(shift_id)
            if site != self.snapshot.site:
                return None
            return build_shift(day, number, self.shift_policy, site)
        except (ValueError, TypeError):
            return None

    def _status(self, line: int, day: date) -> str | None:
        cal = self.calendars.get(line)
        if cal is None or cal.status != "ADMITTED" or cal.min_date is None or cal.max_date is None:
            return None
        if day < cal.min_date or day > cal.max_date:
            return None
        status = self.day_status[line].get(day)
        return None if status in (None, "UNKNOWN") else status

    def workday(self, line: int, day: date, number: int) -> tuple[bool, str, str]:
        """Independent S3 rule on the line's own admitted, bounded generation."""
        cal = self.calendars.get(line)
        if cal is None or cal.status != "ADMITTED":
            return False, "CALENDAR_NOT_ADMITTED", f"line {line} has no admitted calendar generation"
        if cal.min_date is None or cal.max_date is None or not (cal.min_date <= day <= cal.max_date):
            return False, "OUTSIDE_CALENDAR_BOUNDS", f"{day.isoformat()} is outside {cal.min_date}..{cal.max_date} for line {line}"
        status = self._status(line, day)
        if status is None:
            return False, "CALENDAR_DATE_UNKNOWN", f"line {line} {day.isoformat()} has no admitted status"
        if status == WORKING:
            return True, "WORKDAY", f"{day.isoformat()} WORKING"
        if number != 3:
            return False, "NONWORKING_DAY", f"S{number} on line {line} {day.isoformat()} which is {status}"
        nxt = self._status(line, day + timedelta(days=1))
        if nxt == WORKING:
            return True, "S3_EXCEPTION", f"S3 on {status} {day.isoformat()}: next day WORKING"
        return False, "S3_NONWORKING", f"S3 on {status} {day.isoformat()}: next day {nxt}"

    # ------------------------------------------------------------------ readiness
    def placeable(self, job_id: str) -> tuple[bool, bool, str]:
        """(placeable, conditional, reason) for this purpose from the snapshot disposition."""
        disp = self.dispositions.get(job_id)
        if disp is None:
            return False, False, "DISPOSITION_MISSING"
        if disp.disposition is not Disposition.ADMITTED:
            return False, False, disp.disposition.value
        if disp.executable_reasons:
            if self.purpose is Purpose.EXECUTABLE:
                return False, False, "; ".join(disp.executable_reasons)
            return True, True, "; ".join(disp.executable_reasons)
        return True, self.purpose is not Purpose.EXECUTABLE and bool(disp.reasons), "; ".join(disp.reasons)

    def satisfied(self, phase: Phase) -> int | None:
        """Verified-complete anchor minute, or None."""
        disp = self.dispositions.get(phase.job)
        if (disp is not None and disp.disposition is Disposition.VERIFIED_COMPLETE) or phase.status == "COMPLETE":
            if phase.actual_end is not None:
                return min(minute_ceil(phase.actual_end), self.cutoff)
            return self.cutoff
        return None

    def release_time(self, release: ReleaseMilestone) -> NodeTime | None:
        if release.status is ReleaseStatus.CONFIRMED and release.ready_at is not None:
            t = minute_ceil(release.ready_at)
            return NodeTime(t, t, False, "RELEASE_CONFIRMED")
        if self.purpose is Purpose.EXECUTABLE:
            return None
        if release.status is ReleaseStatus.CONDITIONAL and release.forecast_at is not None:
            t = minute_ceil(release.forecast_at)
            return NodeTime(t, t, True, "RELEASE_CONDITIONAL")
        return None

    def ancestors(self, node: str, memo: dict[str, frozenset[str]]) -> frozenset[str]:
        """Every hard-edge ancestor of a node (iterative, cycle-safe)."""
        if node in memo:
            return memo[node]
        seen: set[str] = set()
        stack = [e.pred_phase for e in self.hard_in.get(node, ())]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in memo:
                seen |= memo[n]
                continue
            stack.extend(e.pred_phase for e in self.hard_in.get(n, ()))
        memo[node] = frozenset(seen)
        return memo[node]

    def known_node(self, node: str) -> bool:
        return node in self.phases or node in self.releases


def overlap_minutes(a: int, b: int, windows: Iterable[tuple[int, int]]) -> int:
    return sum(max(0, min(b, y) - max(a, x)) for x, y in windows)


def is_release_phase(phase: Phase) -> bool:
    return phase.kind is PhaseKind.RELEASE
