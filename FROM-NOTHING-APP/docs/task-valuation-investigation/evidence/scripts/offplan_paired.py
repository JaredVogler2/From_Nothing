"""Paired floor scenarios: what off-plan work does to production. Investigation only.

Usage: offplan_paired.py N LO,HI SHIFTS OUT --deviation X --pull-ahead Y [--seed S] [--late-share X --late-max D]

Runs focus.sim.runner.run unchanged except for the floor spec and ``purposes=("EXECUTABLE",)``: the floor works the
executable plan as always; no forecast is planned (it does not change what the floor does). Every scenario starts from the
same synthetic fleet; the seed only changes the floor's random draws. Writes OUT.db and OUT.summary.json.
"""
from __future__ import annotations

import argparse
import json
import time

from common import SCRATCH, rss_mb  # noqa: F401  (sets TMPDIR and sys.path)

import focus.sim.runner as R
from focus.data.fullscale_fixture import FleetSpec
from focus.sim.execute import FloorSpec

ap = argparse.ArgumentParser()
ap.add_argument("n", type=int)
ap.add_argument("team_size")
ap.add_argument("shifts", type=int)
ap.add_argument("out")
ap.add_argument("--deviation", type=float, required=True)
ap.add_argument("--pull-ahead", type=float, required=True)
ap.add_argument("--seed", type=int, default=20261026)
ap.add_argument("--late-share", type=float, default=None)
ap.add_argument("--late-max", type=int, default=None)
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
fleet = {"n_aircraft": args.n, "team_size": (lo, hi)}
if args.late_share is not None:
    fleet["late_part_share"] = args.late_share
if args.late_max is not None:
    fleet["late_delays"] = ((1.0, 1, args.late_max),)
spec = R.SimSpec(fleet=FleetSpec(**fleet), floor=FloorSpec(deviation=args.deviation, pull_ahead=args.pull_ahead),
                 days=(args.shifts + 2) // 3, shifts=args.shifts, purposes=("EXECUTABLE",), max_moves=0, max_repairs=0, seed=args.seed)
db_path = SCRATCH / "out" / f"{args.out}.db"
if db_path.exists():
    db_path.unlink()
t = time.time()
res = R.run(spec, db_path, log=lambda m: print(m, flush=True))
summary = {"out": args.out, "n": args.n, "team_size": [lo, hi], "shifts": args.shifts, "deviation": args.deviation, "pull_ahead": args.pull_ahead,
           "seed": args.seed, "fleet": {k: str(v) for k, v in fleet.items()}, "seconds": round(time.time() - t, 1), "peak_rss_mb": round(rss_mb()),
           "per_shift": res["shifts"]}
(SCRATCH / "out" / f"{args.out}.summary.json").write_text(json.dumps(summary, default=str))
print(json.dumps({k: summary[k] for k in ("out", "seconds", "peak_rss_mb")}))
