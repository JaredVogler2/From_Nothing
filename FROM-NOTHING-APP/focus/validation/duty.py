"""Independent duty reconstruction from the final plan and the source snapshot (S11, S13 V6/V7/V8, P11).

The validator never trusts the engine's own duty records or feasibility flags. It rebuilds each
person's duty from:

* the plan's assignments (crew, work segments, aircraft, shift);
* the snapshot's admitted availability (regular and authorized OT windows, caps), actual carry-in
  duty and pools (purpose-specific members and activation caps);
* the policy's transfer rules, parsed here independently.

From these it checks:

* **V6 non-overlap.** No person is in two places at once across all shift and day labels.
* **V7 transfers.** Between consecutive located duties in a shift, a contiguous open slot of the
  required transfer minutes must fit; A -> B -> A counts two transfers. An omitted transfer is
  ``TRANSFER_MISSING``.
* **V7 duty budget.** WORK + TRAVEL + WAIT + OTHER inside regular windows is at most the regular
  cap, and inside authorized OT windows at most the OT cap; duty outside admitted windows is a
  violation.
* **V8 activation.** The union of people with duty in each pool is at most ``floor(85*N/100)`` on
  the distinct admitted denominator, recomputed here (``ValidationInput.activation_cap``); the
  pool's stored cap is never trusted.

This module deliberately does not import ``focus.engine``; it shares only the calendar/policy
authority, as S13 requires.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from ..contracts.planning import CandidatePlan, PlanningSnapshot, Violation
from ..domain import DutyKind, to_minute
from ..policy import Policy
from .input import ValidationInput


@dataclass(frozen=True)
class ReconstructedDuty:
    person: str
    shift_id: str
    start: int
    end: int
    kind: str                 # WORK | TRAVEL | WAIT | OTHER
    line: int | None
    source: str               # assignment id / carry-in id / derived


@dataclass
class DutyReconstruction:
    duties: list[ReconstructedDuty] = field(default_factory=list)
    travel_minutes: int = 0
    violations: list[Violation] = field(default_factory=list)
    checks: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _overlap(a: int, b: int, windows: Iterable[tuple[int, int]]) -> int:
    return sum(max(0, min(b, y) - max(a, x)) for x, y in windows)


def reconstruct(snapshot: PlanningSnapshot, policy: Policy, plan: CandidatePlan, inp: ValidationInput | None = None) -> DutyReconstruction:
    out = DutyReconstruction()
    inp = inp or ValidationInput.build(snapshot, policy, plan.purpose)
    same = policy.int("travel.same_aircraft_minutes")
    changed = policy.int("travel.changed_aircraft_minutes")
    unknown = policy.optional("travel.unknown_origin_minutes")
    pools = inp.pools
    members_of = {pid: inp.admitted_members(pool) for pid, pool in pools.items()}
    windows: dict[tuple[str, str], tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...], int, int, str]] = {}
    for av in snapshot.availability:
        pool = pools.get(av.pool)
        if pool is None:
            continue
        if av.person not in members_of[av.pool]:
            continue
        sid = f"{pool.key.site}:{pool.key.start_date.isoformat()}:S{pool.key.shift_number}"
        windows[(av.person, sid)] = (tuple(i.as_minutes() for i in av.intervals), tuple(i.as_minutes() for i in av.ot_intervals),
                                     av.regular_cap, av.ot_cap, pool.id)
    located: dict[tuple[str, str], list[ReconstructedDuty]] = defaultdict(list)
    everything: dict[str, list[ReconstructedDuty]] = defaultdict(list)
    for a in plan.assignments:
        for role in a.crew:
            for seg in a.segments:
                d = ReconstructedDuty(role.person, a.shift_id or "", to_minute(seg.start), to_minute(seg.end), "WORK", a.line, a.id)
                located[(role.person, d.shift_id)].append(d)
                everything[role.person].append(d)
    for duty in plan.duties:
        if duty.kind is DutyKind.WAIT:
            d = ReconstructedDuty(duty.person, duty.shift_id or "", to_minute(duty.start), to_minute(duty.end), "WAIT", duty.aircraft_to,
                                  duty.assignment_id or duty.id)
            located[(d.person, d.shift_id)].append(d)
            everything[d.person].append(d)
    for ci in snapshot.carry_in:
        start, end = to_minute(ci.start), to_minute(ci.end)
        sid = next((s for (p, s), w in windows.items() if p == ci.person and min(x for x, _ in w[0] + w[1]) <= start < max(y for _, y in w[0] + w[1])),
                   f"carry:{ci.id}")
        d = ReconstructedDuty(ci.person, sid, start, end, "OTHER", ci.line, f"carry-in {ci.id}")
        located[(ci.person, sid)].append(d)
        everything[ci.person].append(d)
    # ---- transfers between consecutive located duties in each shift (A->B->A is two transfers)
    for (person, sid), duties in located.items():
        duties.sort(key=lambda d: (d.start, d.end))
        merged: list[ReconstructedDuty] = []
        for d in duties:                     # consecutive segments of one assignment form one located stay
            if merged and merged[-1].source == d.source and merged[-1].line == d.line and merged[-1].kind == d.kind:
                merged[-1] = ReconstructedDuty(person, sid, merged[-1].start, d.end, d.kind, d.line, d.source)
            else:
                merged.append(d)
        w = windows.get((person, sid))
        open_windows = sorted((w[0] + w[1]) if w else ())
        for prev, nxt in zip(merged, merged[1:]):
            out.checks["TRANSFER"] += 1
            if nxt.kind == "OTHER":
                continue                     # physical carry-in history: arrival is a fact, not a plan
            if prev.line is None:
                minutes = unknown
            else:
                minutes = same if prev.line == nxt.line else changed
            if minutes is None:
                out.violations.append(Violation("V7", "TRAVEL_ORIGIN_UNKNOWN", person, f"{prev.source} -> {nxt.source}: no bounded policy"))
                continue
            if minutes == 0:
                continue
            slot = None
            for x, y in open_windows:
                s = max(prev.end, x)
                if s + minutes <= min(y, nxt.start):
                    slot = (s, s + minutes)
                    break
            if slot is None:
                out.violations.append(Violation("V7", "TRANSFER_MISSING", person,
                                                f"{minutes} min transfer from line {prev.line} to {nxt.line} does not fit between {prev.end} and {nxt.start}"))
                continue
            t = ReconstructedDuty(person, sid, slot[0], slot[1], "TRAVEL", nxt.line, "derived")
            out.duties.append(t)
            everything[person].append(t)
            out.travel_minutes += minutes
        out.duties.extend(duties)
    # ---- V6 non-overlap across labels
    for person, duties in everything.items():
        duties.sort(key=lambda d: (d.start, d.end))
        for a, b in zip(duties, duties[1:]):
            out.checks["V6"] += 1
            if b.start < a.end and not (a.source == b.source and a.kind == b.kind):
                out.violations.append(Violation("V6", "PERSON_OVERLAP", person, f"{a.kind} {a.source} [{a.start},{a.end}) overlaps {b.kind} {b.source} [{b.start},{b.end})"))
    # ---- V7 duty budget and windows
    by_shift: dict[tuple[str, str], list[ReconstructedDuty]] = defaultdict(list)
    for d in out.duties:
        by_shift[(d.person, d.shift_id)].append(d)
    for (person, sid), duties in by_shift.items():
        w = windows.get((person, sid))
        out.checks["V7"] += 1
        if w is None:
            if any(d.kind != "OTHER" for d in duties):
                out.violations.append(Violation("V7", "NO_ADMITTED_WINDOW", person, f"duty in {sid} without admitted availability"))
            continue
        regular_w, ot_w, regular_cap, ot_cap, _ = w
        regular = ot = 0
        for d in duties:
            in_ot = _overlap(d.start, d.end, ot_w)
            in_reg = _overlap(d.start, d.end, regular_w)
            if d.kind == "OTHER":
                regular += (d.end - d.start) - in_ot
                ot += in_ot
                continue
            if in_ot + in_reg != d.end - d.start:
                out.violations.append(Violation("V7", "OUTSIDE_ADMITTED_WINDOW", person, f"{d.kind} {d.source} [{d.start},{d.end}) in {sid}"))
            regular += in_reg
            ot += in_ot
        if regular > regular_cap:
            out.violations.append(Violation("V7", "REGULAR_CAP", person, f"{regular} > {regular_cap} in {sid}"))
        if ot > ot_cap:
            out.violations.append(Violation("V7", "OT_CAP", person, f"{ot} > {ot_cap} in {sid}"))
    # ---- V8 activation per pool
    active: dict[str, set[str]] = defaultdict(set)
    for (person, sid), duties in by_shift.items():
        w = windows.get((person, sid))
        if w is not None and duties:
            active[w[4]].add(person)
    for pool_id, people in active.items():
        cap = inp.activation_cap(pools[pool_id])
        out.checks["V8"] += 1
        if len(people) > cap:
            out.violations.append(Violation("V8", "ACTIVATION_CAP", pool_id, f"{len(people)} activated > cap {cap}"))
    return out


def duty_violations(snapshot: PlanningSnapshot, policy: Policy, plan: CandidatePlan) -> list[Violation]:
    return reconstruct(snapshot, policy, plan).violations
