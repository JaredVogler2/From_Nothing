"""Delay-dollar study: methods (a)-(d) for every unit of one shift's obligation on a FORECAST plan.

Usage: study.py N LO,HI OUTPREFIX [--variants] [--d1 all|N|0] [--d2 N] [--joint K] [--procs 2] [--exec]

(a) the scheduler's cost of delay (urgency.cost_of_delay), as used by the plan (seed) and recomputed on the plan;
(b) precedence-only total float on the job's own aircraft: penalty(C + max(0, D - TF)) - penalty(C);
(c) schedule-graph right shift: precedence (typed, lagged) plus same-shift person-sequence edges, phases re-fitted into
    the line's shift windows (a phase that no longer fits moves whole to the next open shift); c0 = precedence-only
    right shift with shift windows (no person edges);
(d1) exact counterfactual: floor the phase at the next shift of its line and re-decode with the plan's priority keys;
(d2) repair-mode counterfactual: incumbent placements as repair hints, the floor, then the repair's two decodes.
"""
from __future__ import annotations

import argparse
import gc
import heapq
import json
import multiprocessing as mp
import os
import random
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import replace
from fractions import Fraction

from common import SCRATCH, build, cur_rss_mb, rss_mb

import focus.engine.max.optimizer.greedy as G
from focus.domain import EdgeType, PhaseKind, Purpose, to_minute
from focus.economics.registry import EconomicsRegistry
from focus.engine.max.optimizer.greedy import DefendedTask
from focus.engine.max.optimizer.neighborhoods import PriorityState
from focus.engine.max.optimizer.orchestrator import MaxOptimizer
from focus.engine.max.optimizer.urgency import cost_of_delay
from focus.engine.model import EngineContext
from focus.engine.projections import completion_minutes

# ------------------------------------------------------------------ the earliest-start floor injection (in-process only)
EXTRA_FLOORS: dict[str, int] = {}
_orig_floors = G._floors


def _floors(ctx, result, phase):
    s, e, notes, cond = _orig_floors(ctx, result, phase)
    f = EXTRA_FLOORS.get(phase.key.id)
    if f is not None and f > s:
        s = f
    return s, e, notes, cond


G._floors = _floors

ap = argparse.ArgumentParser()
ap.add_argument("n", type=int)
ap.add_argument("team_size")
ap.add_argument("out")
ap.add_argument("--variants", action="store_true")
ap.add_argument("--d1", default="0")
ap.add_argument("--d2", type=int, default=0)
ap.add_argument("--joint", type=int, default=0)
ap.add_argument("--procs", type=int, default=2)
ap.add_argument("--exec", action="store_true", help="also plan EXECUTABLE and report unit overlap")
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--late-share", type=float, default=None)
ap.add_argument("--units", default="first", choices=["first", "horizon"])
ap.add_argument("--per-stratum", type=int, default=10)
ap.add_argument("--late-max", type=int, default=None)
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
OUT = SCRATCH / "out"
OUT.mkdir(exist_ok=True)
T: dict[str, float] = {}
t0 = time.time()
fleet = {}
if args.late_share is not None:
    fleet["late_part_share"] = args.late_share
if args.late_max is not None:
    fleet["late_delays"] = ((1.0, 1, args.late_max),)
services, snapshot, shift0, facts, spec = build(args.n, (lo, hi), **fleet)
T["build_admit_s"] = time.time() - t0
policy = services.policy
S0, S1 = shift0.start, shift0.end
meta: dict = {"fleet_overrides": {k: str(v) for k, v in fleet.items()}, "n": args.n, "team_size": [lo, hi], "jobs": len(snapshot.jobs), "phases": len(snapshot.phases), "edges": len(snapshot.edges),
              "facts": {k: facts[k] for k in ("scale", "qa_per_shift", "mechanics_per_shift", "late_parts")}, "shift0": [S0, S1]}

# ------------------------------------------------------------------ optional EXECUTABLE plan: its first-shift units
exec_units: set[str] = set()
if args.exec:
    t = time.time()
    ctx_e = EngineContext(snapshot, policy, Purpose.EXECUTABLE, native=True)
    run_e = MaxOptimizer(ctx_e, gate=False, max_moves=0, max_repairs=0, budget_seconds=3600).run()
    exec_units = {pid for pid, p in run_e.best.decode.placements.items() if p.crew and S0 <= p.start < S1}
    meta["executable"] = {"seconds": round(time.time() - t, 1), "first_shift_units": len(exec_units),
                          "unknown_aircraft": len(run_e.best.summary.unknown_lines),
                          "controllable_known_cents": float(run_e.best.summary.controllable_known_cents),
                          "redispatch_accepted": bool(run_e.urgency)}
    del ctx_e, run_e
    gc.collect()

# ------------------------------------------------------------------ the FORECAST plan (seed + cost-of-delay redispatch)
t = time.time()
ctx = EngineContext(snapshot, policy, Purpose.FORECAST, native=True)
T["ctx_s"] = time.time() - t
variants: dict[int, str] = {}
if args.variants:
    # plan once with the sourced economics, then make one aircraft early, one at its cap and one with unknown economics
    opt0 = MaxOptimizer(ctx, gate=False, max_moves=0, max_repairs=0, budget_seconds=3600)
    run0 = opt0.run()
    comp0 = completion_minutes(run0.best.completions)
    lines = sorted(ctx.economics)
    reg0 = EconomicsRegistry(ctx.economics, policy)
    from focus.domain import from_minute
    early, capped, unknown = lines[2], lines[3], lines[4]
    e = ctx.economics[early]
    ctx.economics[early] = replace(e, due_at=from_minute(comp0[early] + 10 * 1440))
    variants[early] = "early(due=completion+10d)"
    e = ctx.economics[capped]
    ctx.economics[capped] = replace(e, cap_cents=reg0.penalty(capped, comp0[capped] - 1440))
    variants[capped] = "at_cap(cap=penalty at completion)"
    e = ctx.economics[unknown]
    ctx.economics[unknown] = replace(e, status="UNKNOWN", daily_rate_cents=None, reasons=e.reasons + ("STUDY: term removed",))
    variants[unknown] = "unknown_economics"
    del opt0, run0
    gc.collect()
opt = MaxOptimizer(ctx, gate=False, max_moves=0, max_repairs=0, budget_seconds=3600)
t = time.time()
run = opt.run()
T["plan_s"] = time.time() - t
best = run.best
reg = opt.registry
URG = dict(run.urgency) if run.urgency else None        # the order the plan was decoded with (None: neutral seed kept)
meta["plan_stages"] = [(s.stage, s.runtime_ms, s.accepted) for s in run.stages]
meta["redispatch_accepted"] = URG is not None
# the cost of delay the scheduler computed from the seed (whether or not the redispatch was kept)
w_seed = reg.derive_priority_weights(completion_minutes(run.baseline.completions))
cod_seed = cost_of_delay(ctx, reg, run.baseline.decode, w_seed)
t = time.time()
w_plan = reg.derive_priority_weights(completion_minutes(best.completions))
cod_plan = cost_of_delay(ctx, reg, best.decode, w_plan)
T["a_plan_all_s"] = time.time() - t

# determinism check: the plan re-decoded with the same keys
t = time.time()
again = opt.evaluate(opt.decode(PriorityState(), URG))
T["decode_eval_s"] = time.time() - t
meta["deterministic"] = again.signature == best.signature and again.summary.controllable_known_cents == best.summary.controllable_known_cents
del again
base_ctrl = best.summary.controllable_known_cents
base_unknown = frozenset(best.summary.unknown_lines)
P = best.decode.placements
comp = {line: (to_minute(c.completion_at) if c.completion_at is not None else None) for line, c in best.completions.items()}
cost_status = {line: c.status for line, c in best.costs.items()}
aircraft = {}
for line in sorted(best.costs):
    c = best.costs[line]
    e = ctx.economics.get(line)
    cat = variants.get(line) or ("unknown_economics" if c.status in ("unknown", "unsupported") and (e is None or e.status != "KNOWN") else
                                 "unknown_completion" if c.status == "unknown" else ("late" if (c.delay_minutes or 0) > 0 else "early"))
    aircraft[line] = {"status": c.status, "category": cat, "completion": comp.get(line), "due": to_minute(e.due_at) if e and e.due_at else None,
                      "delay_days": None if c.delay_minutes is None else c.delay_minutes / 1440,
                      "rate_usd": float(e.daily_rate_cents) / 100 if e and e.daily_rate_cents is not None else None,
                      "cap_usd": float(e.cap_cents) / 100 if e and e.cap_cents is not None else None,
                      "controllable_usd": None if c.controllable_cents is None else float(c.controllable_cents) / 100,
                      "marginal_day_usd": None if c.marginal_day_cents is None else float(c.marginal_day_cents) / 100}
meta["aircraft"] = aircraft
meta["plan"] = {"placed": len(P), "deferred": len(best.decode.deferred), "controllable_usd": float(base_ctrl) / 100,
                "unknown": sorted(base_unknown), "concurrency_edges": sum(len(v) for v in ctx.concurrency.values()),
                "hard_latest": sum(1 for f in ctx.cs.values() if f.hard_latest_at is not None)}
print(json.dumps({k: meta[k] for k in ("plan", "redispatch_accepted", "deterministic")}), flush=True)

# ------------------------------------------------------------------ the schedule graph
t = time.time()
OT = ctx.shift_policy.ot_minutes
TRAVEL = ctx.travel.changed_aircraft_minutes
line_shifts = {line: ctx.line_shifts(line) for line in aircraft}
line_ends = {line: [s.end for s in ss] for line, ss in line_shifts.items()}
shift_index = {line: {s.id: i for i, s in enumerate(ss)} for line, ss in line_shifts.items()}
prec_succ: dict[str, list] = defaultdict(list)
prec_pred: dict[str, list] = defaultdict(list)
for pid in P:
    for e in ctx.succs.get(pid, ()):
        if e.succ_phase in P:
            prec_succ[pid].append((e.succ_phase, e.type, e.lag_minutes))
            prec_pred[e.succ_phase].append((pid, e.type, e.lag_minutes))
pers_succ: dict[str, list] = defaultdict(list)
pers_pred: dict[str, list] = defaultdict(list)
duties: dict[tuple, list] = defaultdict(list)
for pid, p in P.items():
    for person in p.crew:
        duties[(person, p.shift_id)].append((p.start, p.end, pid))
n_pers = 0
for key, items in duties.items():
    items.sort()
    for (a_s, a_e, a), (b_s, b_e, b) in zip(items, items[1:]):
        lag = TRAVEL if P[a].line != P[b].line else 0
        pers_succ[a].append((b, lag))
        pers_pred[b].append((a, lag))
        n_pers += 1
# global topological order of the combined graph
indeg = {pid: 0 for pid in P}
for pid in P:
    for s, _, _ in prec_succ[pid]:
        indeg[s] += 1
    for s, _ in pers_succ[pid]:
        indeg[s] += 1
ready = sorted((P[p].start, p) for p, d in indeg.items() if d == 0)
heapq.heapify(ready)
topo: dict[str, int] = {}
while ready:
    _, u = heapq.heappop(ready)
    topo[u] = len(topo)
    for s in [x for x, _, _ in prec_succ[u]] + [x for x, _ in pers_succ[u]]:
        indeg[s] -= 1
        if indeg[s] == 0:
            heapq.heappush(ready, (P[s].start, s))
cyclic = len(P) - len(topo)
if cyclic:      # drop person edges into nodes left in a cycle (none expected)
    for pid in P:
        if pid not in topo:
            topo[pid] = len(topo)
T["graph_build_s"] = time.time() - t
meta["graph"] = {"prec_edges": sum(len(v) for v in prec_succ.values()), "person_edges": n_pers, "cyclic_nodes": cyclic}

release_of = {}           # job -> its RELEASE phase id (open jobs with a placed release)
for job_id, phases, rel in opt.open_releases:
    release_of[job_id] = rel.key.id
rel_line = {rid: P[rid].line for rid in release_of.values() if rid in P}


def fit(pid: str, floor: int, end_floor: int = 0):
    """The first start at or after ``floor`` whose shift can hold the phase whole (regular, then authorized OT)."""
    p = P[pid]
    work = ctx.phases[pid].work_minutes
    ss = line_shifts[p.line]
    i = bisect_right(line_ends[p.line], floor)
    while i < len(ss):
        s = ss[i]
        i += 1
        if s.end <= floor or s.cutoff_instant < floor:
            continue
        t1 = s.next_open(max(floor, s.start))
        if t1 is None or not s.valid_start(t1):
            continue
        for ot in (0, OT):
            r = s.advance(t1, work, ot)
            if r is not None:
                end = max(r[0], end_floor)
                return t1, end, s.id
    return None


def times(u, new):
    r = new.get(u)
    if r is not None:
        return r[0], r[1]
    p = P[u]
    return p.start, p.end


def right_shift(x: str, F: int, person: bool = True):
    """New (start, end, shift) of every phase a slip of ``x`` to ``F`` pushes; None when ``x`` cannot be re-fitted."""
    r = fit(x, F)
    if r is None:
        return None
    new = {x: r}
    heap = [(topo[s], s) for s, _, _ in prec_succ[x]] + ([(topo[s], s) for s, _ in pers_succ[x]] if person else [])
    heapq.heapify(heap)
    seen = {x}
    while heap:
        _, v = heapq.heappop(heap)
        if v in seen:
            continue
        seen.add(v)
        sf = ef = 0
        for u, typ, lag in prec_pred[v]:
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
        if person:
            for u, lag in pers_pred[v]:
                if u in new and new[u][2] == P[u].shift_id:        # the crew is still in that shift
                    sf = max(sf, new[u][1] + lag)
        p = P[v]
        if sf <= p.start and ef <= p.end:
            continue
        if p.kind is PhaseKind.RELEASE:
            tt = max(p.start, sf, ef)
            new[v] = (tt, tt, None)
        else:
            rr = fit(v, max(p.start, sf), ef)
            if rr is None:
                return None
            new[v] = rr
        for s, _, _ in prec_succ[v]:
            heapq.heappush(heap, (topo[s], s))
        if person:
            for s, _ in pers_succ[v]:
                heapq.heappush(heap, (topo[s], s))
    return new


def money_of_shift(new) -> tuple[Fraction | None, int, dict]:
    """Objective difference of a right shift: sum over aircraft with known cost of penalty(new completion) - penalty(old)."""
    moved: dict[int, int] = {}
    for v, (s, e, _) in new.items():
        line = rel_line.get(v)
        if line is not None and e > (moved.get(line) or comp.get(line) or 0):
            moved[line] = e
    total = Fraction(0)
    per = {}
    for line, c_new in moved.items():
        c_old = comp.get(line)
        if c_old is None or line in base_unknown or c_new <= c_old:
            continue
        d = reg.penalty(line, c_new) - reg.penalty(line, c_old)
        total += d
        per[line] = [float(d) / 100, c_new - c_old]
    return total, len(moved), per


# ------------------------------------------------------------------ (b) precedence-only total float (backward pass per aircraft)
t = time.time()
LS: dict[str, int] = {}
for v in sorted(P, key=lambda q: -topo[q]):
    p = P[v]
    dur = p.end - p.start
    best_ls = None
    for s, typ, lag in prec_succ[v]:
        if P[s].line != p.line:
            continue
        ls_s = LS[s]
        dur_s = P[s].end - P[s].start
        if typ is EdgeType.FS:
            b = ls_s - lag - dur
        elif typ is EdgeType.SS:
            b = ls_s - lag
        elif typ is EdgeType.FF:
            b = ls_s + dur_s - lag - dur
        else:
            b = ls_s + dur_s - lag
        best_ls = b if best_ls is None else min(best_ls, b)
    c_a = comp.get(p.line)
    if best_ls is None:
        best_ls = (c_a if c_a is not None else 10 ** 9) - (0 if p.kind is PhaseKind.RELEASE else dur)
    LS[v] = best_ls
T["b_all_s"] = time.time() - t

# ------------------------------------------------------------------ the units of one shift's obligation
def _tfb(tf):
    return "critical" if tf <= 60 else ("near" if tf <= 1440 else "slack")


if args.units == "first":
    units = sorted(pid for pid, p in P.items() if p.crew and S0 <= p.start < S1)
else:
    _rng = random.Random(args.seed + 1)
    _strata = defaultdict(list)
    for pid, p in P.items():
        if p.crew:
            _strata[(_tfb(LS[pid] - p.start), p.line)].append(pid)
    units = []
    for key in sorted(_strata):
        pool = sorted(_strata[key])
        _rng.shuffle(pool)
        units += pool[:args.per_stratum]
    meta["horizon_strata"] = {f"{k[0]}:{k[1]}": len(v) for k, v in sorted(_strata.items())}
    units = sorted(units)
next_floor = {}
for pid in units:
    p = P[pid]
    i = shift_index[p.line].get(p.shift_id)
    ss = line_shifts[p.line]
    next_floor[pid] = ss[i + 1].start if i is not None and i + 1 < len(ss) else p.end + 480
meta["units"] = len(units)
meta["exec_overlap"] = len(set(units) & exec_units) if exec_units else None

rows = []
t_b = t_c = t_c0 = 0.0
for pid in units:
    p = P[pid]
    ph = ctx.phases[pid]
    F = next_floor[pid]
    D = F - p.start
    TF = LS[pid] - p.start
    c_a = comp.get(p.line)
    tb = time.perf_counter()
    if c_a is None or p.line in base_unknown:
        b_usd = None
    else:
        b_usd = float(reg.penalty(p.line, c_a + max(0, D - TF)) - reg.penalty(p.line, c_a)) / 100
    t_b += time.perf_counter() - tb
    tc = time.perf_counter()
    new = right_shift(pid, F, person=True)
    c_usd, c_lines, c_per = (None, 0, {}) if new is None else money_of_shift(new)
    c_moved = 0 if new is None else len(new)
    t_c += time.perf_counter() - tc
    tc = time.perf_counter()
    new0 = right_shift(pid, F, person=False)
    c0_usd = None if new0 is None else money_of_shift(new0)[0]
    t_c0 += time.perf_counter() - tc
    w = w_seed.get(p.line)
    rows.append({"pid": pid, "job": p.job, "line": p.line, "team": ph.role_demands[0].team, "kind": p.kind.value, "start": p.start,
                 "end": p.end, "shift": p.shift_id, "work": ph.work_minutes, "spm": ph.standard_person_minutes, "crew": len(p.crew),
                 "category": aircraft[p.line]["category"], "D": D, "TF": TF, "tail": ctx.tails.get(pid, 0),
                 "a_seed_raw": cod_seed.get(pid, 0), "a_plan_raw": cod_plan.get(pid, 0),
                 "a_seed_usd": cod_seed.get(pid, 0) / 1e6 / 100 * D / 1440, "a_plan_usd": cod_plan.get(pid, 0) / 1e6 / 100 * D / 1440,
                 "weight_seed_usd_day": None if w is None or w.marginal_cents is None else float(w.marginal_cents) / 100,
                 "b_usd": b_usd, "c_usd": None if c_usd is None else float(c_usd) / 100, "c0_usd": None if c0_usd is None else float(c0_usd) / 100,
                 "c_moved": c_moved, "c_lines": c_lines, "c_per": c_per, "in_exec": pid in exec_units})
T["b_units_s"], T["c_units_s"], T["c0_units_s"] = t_b, t_c, t_c0
print(f"units={len(units)} b {t_b:.2f}s c {t_c:.2f}s c0 {t_c0:.2f}s rss {cur_rss_mb():.0f} MB", flush=True)

# ------------------------------------------------------------------ (d) exact counterfactuals
def tf_bucket(tf):
    return "critical" if tf <= 60 else ("near" if tf <= 1440 else "slack")


rng = random.Random(args.seed)
by_stratum = defaultdict(list)
for r in rows:
    by_stratum[(tf_bucket(r["TF"]), r["category"])].append(r["pid"])
if args.d1 == "all":
    d1_units = list(units)
else:
    k = int(args.d1)
    d1_units = []
    if k:
        strata = sorted(by_stratum)
        per = max(1, k // max(1, len(strata)))
        for s in strata:
            pool = sorted(by_stratum[s])
            rng.shuffle(pool)
            d1_units += pool[:per]
        rest = sorted(set(units) - set(d1_units))
        rng.shuffle(rest)
        d1_units += rest[:max(0, k - len(d1_units))]
d2_units = sorted(d1_units)[:0]
if args.d2:
    pool = sorted(d1_units) if d1_units else sorted(units)
    rng.shuffle(pool)
    d2_units = pool[:args.d2]
HINTS = {pid: DefendedTask(pid, p.assignment_id, p.shift_id, p.start, p.crew, 0) for pid, p in P.items() if p.crew and p.assignment_id}


def d1_job(pids):
    if isinstance(pids, str):
        pids = [pids]
    EXTRA_FLOORS.clear()
    for q in pids:
        EXTRA_FLOORS[q] = next_floor[q]
    t1 = time.perf_counter()
    ev = opt.evaluate(opt.decode(PriorityState(), URG))
    sec = time.perf_counter() - t1
    EXTRA_FLOORS.clear()
    unk = frozenset(ev.summary.unknown_lines)
    per = {}
    for line, c in ev.costs.items():
        b = best.costs[line]
        if c.controllable_cents is not None and b.controllable_cents is not None and c.controllable_cents != b.controllable_cents:
            per[line] = float(c.controllable_cents - b.controllable_cents) / 100
    moved = sum(1 for q, pl in ev.decode.placements.items() if q in P and (pl.start != P[q].start or pl.crew != P[q].crew))
    shifts = {}
    for line, c in ev.completions.items():
        new_c = to_minute(c.completion_at) if c.completion_at is not None else None
        if new_c != comp.get(line):
            shifts[line] = None if (new_c is None or comp.get(line) is None) else new_c - comp[line]
    q0 = pids[0]
    return {"pids": list(pids), "d1_usd": float(ev.summary.controllable_known_cents - base_ctrl) / 100, "d1_unknown_delta": len(unk) - len(base_unknown),
            "d1_unknown_changed": unk != base_unknown, "d1_per": per, "d1_shift": shifts, "d1_moved": moved, "d1_s": sec,
            "new_start": ev.decode.placements[q0].start if q0 in ev.decode.placements else None}


def d2_job(pid):
    EXTRA_FLOORS.clear()
    if pid is not None:
        EXTRA_FLOORS[pid] = next_floor[pid]
    t1 = time.perf_counter()
    o2 = MaxOptimizer(ctx, gate=False, max_moves=0, max_repairs=0, budget_seconds=3600, hints=HINTS, repair=True)
    r2 = o2.run()
    sec = time.perf_counter() - t1
    EXTRA_FLOORS.clear()
    ev = r2.best
    return {"pid": pid, "ctrl_cents": float(ev.summary.controllable_known_cents), "unknown": sorted(ev.summary.unknown_lines), "d2_s": sec,
            "kept": ev.decode.kept, "rehomed": ev.decode.rehomed, "redispatch_kept": bool(r2.urgency)}


def pmap(fn, items, procs):
    if not items:
        return []
    if procs <= 1:
        return [fn(x) for x in items]
    with mp.get_context("fork").Pool(procs) as pool:
        return list(pool.imap(fn, items, chunksize=1))


t = time.time()
d1 = pmap(d1_job, d1_units, args.procs)
T["d1_wall_s"] = time.time() - t
T["d1_n"] = len(d1)
by_pid = {r["pids"][0]: r for r in d1}
for r in rows:
    x = by_pid.get(r["pid"])
    if x:
        r.update({k: v for k, v in x.items() if k != "pids"})
print(f"d1: {len(d1)} counterfactuals in {T['d1_wall_s']:.1f}s wall", flush=True)

# repair-mode counterfactuals: baseline repair (no floor) first
if d2_units:
    t = time.time()
    res = pmap(d2_job, [None] + d2_units, args.procs)
    T["d2_wall_s"] = time.time() - t
    T["d2_n"] = len(d2_units)
    b2 = res[0]
    meta["d2_baseline"] = {"ctrl_usd": b2["ctrl_cents"] / 100, "plan_ctrl_usd": float(base_ctrl) / 100, "kept": b2["kept"], "rehomed": b2["rehomed"],
                           "unknown": b2["unknown"], "redispatch_kept": b2["redispatch_kept"], "seconds": b2["d2_s"]}
    by2 = {x["pid"]: x for x in res[1:]}
    for r in rows:
        x = by2.get(r["pid"])
        if x:
            r.update({"d2_usd": (x["ctrl_cents"] - b2["ctrl_cents"]) / 100, "d2_vs_plan_usd": (x["ctrl_cents"] - float(base_ctrl)) / 100,
                      "d2_unknown_changed": x["unknown"] != b2["unknown"], "d2_s": x["d2_s"], "d2_kept": x["kept"], "d2_rehomed": x["rehomed"]})
    print(f"d2: {len(d2_units)} in {T['d2_wall_s']:.1f}s wall; baseline {meta['d2_baseline']}", flush=True)

# ------------------------------------------------------------------ joint counterfactuals (non-additivity)
joint = []
if args.joint:
    have = {r["pid"]: r for r in rows if "d1_usd" in r}
    sets = []
    # (i) per aircraft: its critical units of the shift delayed together
    for line in sorted(aircraft):
        crit = sorted(pid for pid, r in have.items() if r["line"] == line and r["TF"] <= 60)
        if len(crit) >= 2:
            sets.append(("aircraft_critical", crit[:12]))
    # (ii) random "missed" sets: about 3 % of the shift's units, from the units with a d1 value
    pool = sorted(have)
    for k in range(args.joint):
        rng.shuffle(pool)
        size = max(2, round(0.03 * len(units)))
        sets.append(("random_missed", sorted(pool[:size])))
    # (iii) chains: units of one job or linked by precedence on one aircraft
    for line in sorted(aircraft):
        mine = sorted((have[p]["start"], p) for p in have if have[p]["line"] == line)
        if len(mine) >= 3:
            sets.append(("aircraft_all_sampled", [p for _, p in mine][:10]))
    res = pmap(d1_job, [s for _, s in sets], args.procs)
    for (kind, s), x in zip(sets, res):
        joint.append({"kind": kind, "pids": s, "sum_individual_usd": sum(have[p]["d1_usd"] for p in s), "joint_usd": x["d1_usd"],
                      "max_individual_usd": max(have[p]["d1_usd"] for p in s), "unknown_changed": x["d1_unknown_changed"],
                      "sum_c_usd": sum(have[p]["c_usd"] or 0 for p in s), "sum_b_usd": sum(have[p]["b_usd"] or 0 for p in s)})
    print(f"joint: {len(joint)} sets", flush=True)

meta["timings"] = {k: round(v, 3) for k, v in T.items()}
meta["peak_rss_mb"] = round(rss_mb())
with open(OUT / f"{args.out}.json", "w") as fh:
    json.dump({"meta": meta, "rows": rows, "joint": joint}, fh, default=str)
print(json.dumps(meta["timings"]), "peak MB", meta["peak_rss_mb"], flush=True)
