"""Priority classes (model G) on a published schedule, for the T5/T8/T9/T11 measurements. Investigation code only.

The schedule graph is the plan's placements with typed, lagged precedence edges plus each person's consecutive same-shift
duties (the right shift (c) of study.py). For a unit x placed in shift s of its line:

* r1 = right shift of x to the start of the next shift of its line (x not done this shift);
* rK = right shift of x to the start of the shift K shifts later (default K = 9, three days).

Each right shift returns the new completion of every aircraft it moves. Under a due-date vector, a slip is *priced* when some
aircraft with known economics ends after max(old completion, due). Class: CRITICAL when r1 is priced, AT_RISK when rK is
priced, FLOAT otherwise, UNKNOWN when x's own aircraft has unknown economics or completion. Storing the moved completions
lets the class be re-evaluated under perturbed due dates without re-propagating.
"""
from __future__ import annotations

import heapq
from bisect import bisect_right
from collections import defaultdict
from typing import Any

from focus.domain import EdgeType, PhaseKind, to_minute


class ScheduleGraph:
    def __init__(self, ctx: Any, placements: dict, completions: dict, unknown_lines: frozenset):
        self.ctx, self.P = ctx, placements
        self.comp = {line: (to_minute(c.completion_at) if c.completion_at is not None else None) for line, c in completions.items()}
        self.unknown = frozenset(unknown_lines)
        P = placements
        self.OT = ctx.shift_policy.ot_minutes
        travel = ctx.travel.changed_aircraft_minutes
        lines = sorted({p.line for p in P.values()})
        self.line_shifts = {line: ctx.line_shifts(line) for line in lines}
        self.line_ends = {line: [s.end for s in ss] for line, ss in self.line_shifts.items()}
        self.shift_pos = {line: {s.id: i for i, s in enumerate(ss)} for line, ss in self.line_shifts.items()}
        self.prec_succ, self.prec_pred = defaultdict(list), defaultdict(list)
        for pid in P:
            for e in ctx.succs.get(pid, ()):
                if e.succ_phase in P:
                    self.prec_succ[pid].append((e.succ_phase, e.type, e.lag_minutes))
                    self.prec_pred[e.succ_phase].append((pid, e.type, e.lag_minutes))
        self.pers_succ, self.pers_pred = defaultdict(list), defaultdict(list)
        duties = defaultdict(list)
        for pid, p in P.items():
            for person in p.crew:
                duties[(person, p.shift_id)].append((p.start, p.end, pid))
        for items in duties.values():
            items.sort()
            for (_, _, a), (_, _, b) in zip(items, items[1:]):
                lag = travel if P[a].line != P[b].line else 0
                self.pers_succ[a].append((b, lag))
                self.pers_pred[b].append((a, lag))
        indeg = {pid: 0 for pid in P}
        for pid in P:
            for s, _, _ in self.prec_succ[pid]:
                indeg[s] += 1
            for s, _ in self.pers_succ[pid]:
                indeg[s] += 1
        ready = [(P[p].start, p) for p, d in indeg.items() if d == 0]
        heapq.heapify(ready)
        self.topo = {}
        while ready:
            _, u = heapq.heappop(ready)
            self.topo[u] = len(self.topo)
            for s in [x for x, _, _ in self.prec_succ[u]] + [x for x, _ in self.pers_succ[u]]:
                indeg[s] -= 1
                if indeg[s] == 0:
                    heapq.heappush(ready, (P[s].start, s))
        for pid in P:
            self.topo.setdefault(pid, len(self.topo))
        self.rel_line = {}
        for job_id, phases in ctx.job_phases.items():
            rel = next((p for p in phases if p.kind is PhaseKind.RELEASE), None)
            if rel is not None and rel.key.id in P and not ctx.placeability(job_id).satisfied:
                self.rel_line[rel.key.id] = P[rel.key.id].line

    def fit(self, pid: str, floor: int, end_floor: int = 0):
        p = self.P[pid]
        work = self.ctx.phases[pid].work_minutes
        ss = self.line_shifts[p.line]
        i = bisect_right(self.line_ends[p.line], floor)
        while i < len(ss):
            s = ss[i]
            i += 1
            if s.end <= floor or s.cutoff_instant < floor:
                continue
            t1 = s.next_open(max(floor, s.start))
            if t1 is None or not s.valid_start(t1):
                continue
            for ot in (0, self.OT):
                r = s.advance(t1, work, ot)
                if r is not None:
                    return t1, max(r[0], end_floor), s.id
        return None

    def right_shift(self, x: str, floor: int):
        P = self.P
        r = self.fit(x, floor)
        if r is None:
            return None
        new = {x: r}
        heap = [(self.topo[s], s) for s, _, _ in self.prec_succ[x]] + [(self.topo[s], s) for s, _ in self.pers_succ[x]]
        heapq.heapify(heap)
        seen = {x}
        while heap:
            _, v = heapq.heappop(heap)
            if v in seen:
                continue
            seen.add(v)
            sf = ef = 0
            for u, typ, lag in self.prec_pred[v]:
                if u not in new:
                    continue
                us, ue = new[u][0], new[u][1]
                if typ is EdgeType.FS:
                    sf = max(sf, ue + lag)
                elif typ is EdgeType.SS:
                    sf = max(sf, us + lag)
                elif typ is EdgeType.FF:
                    ef = max(ef, ue + lag)
                elif typ is EdgeType.SF:
                    ef = max(ef, us + lag)
            for u, lag in self.pers_pred[v]:
                if u in new and new[u][2] == P[u].shift_id:
                    sf = max(sf, new[u][1] + lag)
            p = P[v]
            if sf <= p.start and ef <= p.end:
                continue
            if p.kind is PhaseKind.RELEASE:
                tt = max(p.start, sf, ef)
                new[v] = (tt, tt, None)
            else:
                rr = self.fit(v, max(p.start, sf), ef)
                if rr is None:
                    return None
                new[v] = rr
            for s, _, _ in self.prec_succ[v]:
                heapq.heappush(heap, (self.topo[s], s))
            for s, _ in self.pers_succ[v]:
                heapq.heappush(heap, (self.topo[s], s))
        return new

    def moved_completions(self, new) -> dict[int, int]:
        out: dict[int, int] = {}
        for v, (_, e, _) in new.items():
            line = self.rel_line.get(v)
            if line is not None and e > (out.get(line) or self.comp.get(line) or 0):
                out[line] = e
        return {line: c for line, c in out.items() if self.comp.get(line) is not None and c > self.comp[line]}

    def floors(self, x: str, K: int):
        p = self.P[x]
        ss = self.line_shifts[p.line]
        i = self.shift_pos[p.line].get(p.shift_id)
        if i is None:
            return None, None
        f1 = ss[i + 1].start if i + 1 < len(ss) else None
        fK = ss[i + K].start if i + K < len(ss) else None
        return f1, fK

    def probe(self, x: str, K: int = 9) -> dict:
        """Moved completions for the next-shift and K-shift slips of x (None: cannot be re-fitted inside the calendar)."""
        f1, fK = self.floors(x, K)
        out = {}
        for name, f in (("r1", f1), ("rK", fK)):
            if f is None:
                out[name] = None
                continue
            new = self.right_shift(x, f)
            out[name] = None if new is None else self.moved_completions(new)
        return out


def klass(probe: dict, own_line: int, due: dict, comp: dict, unknown: frozenset, known_econ: set) -> str:
    if own_line in unknown or own_line not in known_econ or comp.get(own_line) is None:
        return "UNKNOWN"

    def priced(moved):
        if moved is None:
            return True                    # cannot be re-fitted inside the admitted calendar: treat as priced (conservative)
        return any(line in known_econ and line not in unknown and c > max(comp[line], due[line]) for line, c in moved.items())

    if priced(probe["r1"]):
        return "CRITICAL"
    if priced(probe["rK"]):
        return "AT_RISK"
    return "FLOAT"
