"""T5 and T11: priority classes on one FORECAST plan; stability under due-date shifts and under a different valid plan; cost.

Usage: plan_classes.py N LO,HI OUT [--late-share X --late-max D] [--K 9]

* classes of every first-shift unit on the economic-redispatch plan and on the neutral seed plan (both valid decodes of the
  same snapshot; the seed is the scheduler's own first candidate);
* class changes when every due date moves -1 day and +1 day (rates do not enter the class: only zero versus nonzero matters);
* wall time of the graph build and of the two probes per unit, peak memory.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter

from common import SCRATCH, build, rss_mb
from classes_core import ScheduleGraph, klass

from focus.domain import PhaseKind, Purpose, to_minute
from focus.engine.max.optimizer.orchestrator import MaxOptimizer
from focus.engine.model import EngineContext

ap = argparse.ArgumentParser()
ap.add_argument("n", type=int)
ap.add_argument("team_size")
ap.add_argument("out")
ap.add_argument("--late-share", type=float, default=None)
ap.add_argument("--late-max", type=int, default=None)
ap.add_argument("--K", type=int, default=9)
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
fleet = {}
if args.late_share is not None:
    fleet["late_part_share"] = args.late_share
if args.late_max is not None:
    fleet["late_delays"] = ((1.0, 1, args.late_max),)
T: dict[str, float] = {}
t0 = time.time()
services, snapshot, shift0, facts, spec = build(args.n, (lo, hi), **fleet)
T["build_admit_s"] = round(time.time() - t0, 1)
policy = services.policy
S0, S1 = shift0.start, shift0.end
t = time.time()
ctx = EngineContext(snapshot, policy, Purpose.FORECAST, native=True)
T["ctx_s"] = round(time.time() - t, 1)
opt = MaxOptimizer(ctx, gate=False, max_moves=0, max_repairs=0, budget_seconds=7200)
t = time.time()
run = opt.run()
T["plan_s"] = round(time.time() - t, 1)
known = {line for line in ctx.economics if opt.registry.supported(line)[0]}
due = {line: to_minute(e.due_at) for line, e in ctx.economics.items() if e.due_at is not None}
plans = {"redispatch": run.best, "seed": run.baseline}
result = {"n": args.n, "team_size": [lo, hi], "fleet": {k: str(v) for k, v in fleet.items()}, "K": args.K,
          "phases": len(snapshot.phases), "jobs": len(snapshot.jobs), "redispatch_accepted": bool(run.urgency),
          "known_economics": len(known), "plans": {}}
classes: dict[str, dict[str, dict[str, str]]] = {}
for name, ev in plans.items():
    t = time.time()
    G = ScheduleGraph(ctx, ev.decode.placements, ev.completions, frozenset(ev.summary.unknown_lines))
    tb = time.time() - t
    units = [pid for pid, p in ev.decode.placements.items() if p.crew and S0 <= p.start < S1 and ctx.phases[pid].kind is not PhaseKind.RELEASE]
    t = time.time()
    probes = {pid: G.probe(pid, args.K) for pid in units}
    tp = time.time() - t
    scen = {"base": due, "due_minus_1d": {k: v - 1440 for k, v in due.items()}, "due_plus_1d": {k: v + 1440 for k, v in due.items()}}
    cls = {s: {pid: klass(probes[pid], G.P[pid].line, d, G.comp, G.unknown, known) for pid in units} for s, d in scen.items()}
    classes[name] = cls
    changed = {s: sum(1 for pid in units if cls[s][pid] != cls["base"][pid]) for s in scen if s != "base"}
    lines_state = {str(line): ("unknown" if line in G.unknown else "late" if (G.comp.get(line) or 0) > due.get(line, 10 ** 12) else "early")
                   for line in sorted(G.comp)}
    result["plans"][name] = {"units": len(units), "graph_build_s": round(tb, 2), "probe_all_units_s": round(tp, 2),
                             "probe_per_unit_ms": round(1000 * tp / max(1, len(units)), 2),
                             "classes": {s: dict(Counter(c.values())) for s, c in cls.items()},
                             "changed_vs_base": changed,
                             "changed_share": {s: round(v / max(1, len(units)), 4) for s, v in changed.items()},
                             "aircraft_state": dict(Counter(lines_state.values())), "controllable_usd": float(ev.summary.controllable_known_cents) / 100}
common = sorted(set(classes["redispatch"]["base"]) & set(classes["seed"]["base"]))
agree = sum(1 for pid in common if classes["redispatch"]["base"][pid] == classes["seed"]["base"][pid])
pairs = Counter((classes["seed"]["base"][pid], classes["redispatch"]["base"][pid]) for pid in common)
result["seed_vs_redispatch"] = {"common_units": len(common), "agree": agree, "agree_share": round(agree / max(1, len(common)), 4),
                                "transitions_seed_to_redispatch": {f"{a}->{b}": n for (a, b), n in sorted(pairs.items())}}
result["timings"] = T
result["peak_rss_mb"] = round(rss_mb())
(SCRATCH / "out" / f"{args.out}.json").write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
