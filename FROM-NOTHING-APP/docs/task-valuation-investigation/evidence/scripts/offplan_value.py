"""What is a standard minute of unplanned work worth, against a planned one? Engine counterfactuals on one FORECAST plan.

Usage: offplan_value.py N LO,HI OUT [--sample 80] [--late-share X --late-max D] [--procs 2]

* Pull-ahead value of an unscheduled job: re-decode the plan (same priority keys) with the job marked verified-complete at
  the cutoff, as if a team had done it off-plan this shift; value = controllable modeled cost(plan) - cost(counterfactual).
  Candidates: jobs whose first phase is placed one to nine shifts ahead but whose earliest start is at or before the next
  shift (ready work waiting for capacity), sampled across aircraft.
* On-time value of a planned unit: re-decode with the unit's phase floored at the next shift (the d1 of study.py).
* Both per standard person-minute, with the count of aircraft whose completion moved and of other aircraft affected.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import time
from dataclasses import replace

from common import SCRATCH, build, rss_mb

import focus.engine.max.optimizer.greedy as G
from focus.domain import Disposition, PhaseKind, Purpose, to_minute
from focus.engine.max.optimizer.neighborhoods import PriorityState
from focus.engine.max.optimizer.orchestrator import MaxOptimizer
from focus.engine.model import EngineContext

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
ap.add_argument("--sample", type=int, default=80)
ap.add_argument("--late-share", type=float, default=None)
ap.add_argument("--late-max", type=int, default=None)
ap.add_argument("--procs", type=int, default=2)
ap.add_argument("--seed", type=int, default=11)
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
fleet = {}
if args.late_share is not None:
    fleet["late_part_share"] = args.late_share
if args.late_max is not None:
    fleet["late_delays"] = ((1.0, 1, args.late_max),)
t0 = time.time()
services, snapshot, shift0, facts, spec = build(args.n, (lo, hi), **fleet)
policy = services.policy
S0, S1 = shift0.start, shift0.end
ctx = EngineContext(snapshot, policy, Purpose.FORECAST, native=True)
opt = MaxOptimizer(ctx, gate=False, max_moves=0, max_repairs=0, budget_seconds=7200)
run = opt.run()
URG = dict(run.urgency) if run.urgency else None
best = run.best
P = best.decode.placements
base_ctrl = best.summary.controllable_known_cents
base_unknown = frozenset(best.summary.unknown_lines)
comp = {line: (to_minute(c.completion_at) if c.completion_at else None) for line, c in best.completions.items()}
due = {line: to_minute(e.due_at) for line, e in ctx.economics.items() if e.due_at is not None}
plan_s = time.time() - t0

# line shift lists, to find "one to nine shifts ahead"
line_shifts = {line: ctx.line_shifts(line) for line in sorted({p.line for p in P.values()})}


def shift_rank(p) -> int | None:
    ss = line_shifts[p.line]
    for i, s in enumerate(ss):
        if s.id == p.shift_id:
            return i
    return None


first_rank = {line: next((i for i, s in enumerate(ss) if s.end > S0), 0) for line, ss in line_shifts.items()}
waits = best.decode.waits
# pull-ahead candidates: open jobs whose earliest placed phase is 1-9 shifts ahead and was ready by the next shift
cands = []
for job_id, phases in ctx.job_phases.items():
    if ctx.placeability(job_id).satisfied:
        continue
    placed = [P[p.key.id] for p in phases if p.key.id in P and p.kind is not PhaseKind.RELEASE and P[p.key.id].crew]
    if not placed:
        continue
    first = min(placed, key=lambda q: q.start)
    r = shift_rank(first)
    if r is None:
        continue
    ahead = r - first_rank[first.line]
    earliest = first.start - waits.get(first.phase_id, 0)
    if 1 <= ahead <= 9 and earliest <= S1:
        spm = sum(ctx.phases[q.phase_id].standard_person_minutes for q in placed)
        cands.append((job_id, first.line, ahead, spm, earliest))
rng = random.Random(args.seed)
by_line: dict[int, list] = {}
for c in cands:
    by_line.setdefault(c[1], []).append(c)
sample = []
while len(sample) < min(args.sample, len(cands)):
    for line in sorted(by_line):
        if by_line[line] and len(sample) < args.sample:
            sample.append(by_line[line].pop(rng.randrange(len(by_line[line]))))
# planned first-shift units for the on-time comparison
planned = [pid for pid, p in P.items() if p.crew and S0 <= p.start < S1 and ctx.phases[pid].kind is not PhaseKind.RELEASE]
planned_sample = rng.sample(planned, min(args.sample, len(planned)))


def evaluate_now():
    ev = opt.evaluate(opt.decode(PriorityState(), URG))
    moved = {line: (to_minute(c.completion_at) - comp[line]) for line, c in ev.completions.items()
             if c.completion_at is not None and comp.get(line) is not None and to_minute(c.completion_at) != comp[line]}
    return float(base_ctrl - ev.summary.controllable_known_cents) / 100, moved, sorted(set(ev.summary.unknown_lines) ^ base_unknown)


def pull_job(item):
    job_id, line, ahead, spm, earliest = item
    disp = ctx.dispositions[job_id]
    ctx.dispositions[job_id] = replace(disp, disposition=Disposition.VERIFIED_COMPLETE)
    try:
        value, moved, unk = evaluate_now()
    finally:
        ctx.dispositions[job_id] = disp
    return {"job": job_id, "line": line, "shifts_ahead": ahead, "spm": spm, "value_usd": value, "per_spm": value / spm if spm else None,
            "moved_min": {str(k): v for k, v in moved.items()}, "other_aircraft": sum(1 for k in moved if k != line), "unknown_changed": unk}


def slip_unit(pid):
    p = P[pid]
    ss = line_shifts[p.line]
    r = shift_rank(p)
    floor = ss[r + 1].start if r is not None and r + 1 < len(ss) else None
    if floor is None:
        return None
    EXTRA_FLOORS[pid] = floor
    try:
        value, moved, unk = evaluate_now()
    finally:
        EXTRA_FLOORS.pop(pid, None)
    spm = ctx.phases[pid].standard_person_minutes
    return {"phase": pid, "line": p.line, "spm": spm, "on_time_value_usd": -value, "per_spm": (-value / spm) if spm else None,
            "moved_min": {str(k): v for k, v in moved.items()}, "unknown_changed": unk}


def pmap(fn, items):
    if args.procs <= 1:
        return [fn(x) for x in items]
    with mp.get_context("fork").Pool(args.procs) as pool:
        return pool.map(fn, items)


t = time.time()
pulls = pmap(pull_job, sample)
t_pull = time.time() - t
t = time.time()
slips = [r for r in pmap(slip_unit, planned_sample) if r is not None]
t_slip = time.time() - t
out = {"n": args.n, "team_size": [lo, hi], "fleet": {k: str(v) for k, v in fleet.items()}, "plan_s": round(plan_s, 1),
       "controllable_usd": float(base_ctrl) / 100, "aircraft_late": sum(1 for l, c in comp.items() if c and due.get(l) and c > due[l]),
       "candidates": len(cands), "pull_sample": len(pulls), "planned_sample": len(slips), "pull_s": round(t_pull, 1), "slip_s": round(t_slip, 1),
       "pulls": pulls, "slips": slips, "peak_rss_mb": round(rss_mb())}
(SCRATCH / "out" / f"{args.out}.json").write_text(json.dumps(out))


def summary(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {}
    vs = sorted(vals)
    return {"n": len(vs), "nonzero": sum(1 for v in vs if v != 0), "positive": sum(1 for v in vs if v > 0), "negative": sum(1 for v in vs if v < 0),
            "mean": sum(vs) / len(vs), "median": vs[len(vs) // 2], "max": vs[-1], "min": vs[0]}


print(json.dumps({k: out[k] for k in ("n", "fleet", "plan_s", "controllable_usd", "aircraft_late", "candidates", "pull_sample", "planned_sample",
                                      "pull_s", "slip_s", "peak_rss_mb")}))
print("pull-ahead value USD:", json.dumps(summary([p["value_usd"] for p in pulls])))
print("pull-ahead value per standard minute:", json.dumps(summary([p["per_spm"] for p in pulls])))
print("on-time value USD (planned first-shift unit):", json.dumps(summary([s["on_time_value_usd"] for s in slips])))
print("on-time value per standard minute:", json.dumps(summary([s["per_spm"] for s in slips])))
print("pulls moving another aircraft:", sum(1 for p in pulls if p["other_aircraft"]), "of", len(pulls))
