"""T8/T9 shadow study: the production simulation with priority classes recorded at every FORECAST publication.

Usage: shadow_sim.py N LO,HI DAYS OUT [--late-share X --late-max D] [--K 9]

Runs focus.sim.runner.run unchanged except that, in this process only, its imported ``plan`` is wrapped: after each FORECAST
plan, every placed phase starting within 24 hours of the shift start is classified (classes_core) and written to
OUT.classes.jsonl with the plan's per-aircraft completion. Nothing else changes: the floor works the EXECUTABLE plan and
the goals of record as always, and the simulation database (OUT.db) records units, outcomes and team scores.
"""
from __future__ import annotations

import argparse
import json
import time

from common import SCRATCH, rss_mb  # noqa: F401  (sets TMPDIR and sys.path)
from classes_core import ScheduleGraph, klass

import focus.sim.runner as R
from focus.data.fullscale_fixture import FleetSpec
from focus.domain import PhaseKind, to_minute

ap = argparse.ArgumentParser()
ap.add_argument("n", type=int)
ap.add_argument("team_size")
ap.add_argument("days", type=int)
ap.add_argument("out")
ap.add_argument("--late-share", type=float, default=None)
ap.add_argument("--late-max", type=int, default=None)
ap.add_argument("--K", type=int, default=9)
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
fleet = {"n_aircraft": args.n, "team_size": (lo, hi)}
if args.late_share is not None:
    fleet["late_part_share"] = args.late_share
if args.late_max is not None:
    fleet["late_delays"] = ((1.0, 1, args.late_max),)
out_jsonl = SCRATCH / "out" / f"{args.out}.classes.jsonl"
out_jsonl.write_text("")
state = {"k": 0}
_orig = R.plan


def hooked(services, snapshot, purpose, **kw):
    sp = _orig(services, snapshot, purpose, **kw)
    if purpose.value != "FORECAST":
        return sp
    t = time.time()
    ctx, ev = sp.context, sp.run.best
    start = to_minute(kw["created_at"]) + spec.cutoff_lead_minutes
    G = ScheduleGraph(ctx, ev.decode.placements, ev.completions, frozenset(ev.summary.unknown_lines))
    known = {line for line in ctx.economics if sp.run.best.costs.get(line) is not None and sp.run.best.costs[line].status in ("known", "conditional")}
    due = {line: to_minute(e.due_at) for line, e in ctx.economics.items() if e.due_at is not None}
    rows = []
    for pid, p in ev.decode.placements.items():
        phase = ctx.phases.get(pid)
        if not p.crew or phase is None or phase.kind is PhaseKind.RELEASE or not (start <= p.start < start + 1440):
            continue
        pr = G.probe(pid, args.K)
        rows.append({"phase": pid, "line": p.line, "team": phase.role_demands[0].team if phase.role_demands else None,
                     "effort": phase.standard_person_minutes, "start": p.start, "class": klass(pr, p.line, due, G.comp, G.unknown, known)})
    rec = {"k": state["k"], "shift_start": start, "units": rows, "seconds": round(time.time() - t, 2),
           "completion": {str(line): c for line, c in G.comp.items()}, "due": {str(k): v for k, v in due.items()},
           "unknown": sorted(G.unknown), "controllable_usd": float(ev.summary.controllable_known_cents) / 100}
    with out_jsonl.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    state["k"] += 1
    return sp


R.plan = hooked
spec = R.SimSpec(fleet=FleetSpec(**fleet), days=args.days, max_moves=0, max_repairs=0)
db_path = SCRATCH / "out" / f"{args.out}.db"
if db_path.exists():
    db_path.unlink()
res = R.run(spec, db_path, log=lambda m: print(m, flush=True))
print(json.dumps({"sim_id": res["sim_id"], "shifts": len(res["shifts"]), "peak_rss_mb": round(rss_mb())}))
