"""CP: a floor that chooses its own work, as a crew chasing job counts does. Investigation only.

Usage: cherry_pick.py N LO,HI SHIFTS OUT --pick {plan,hardest,random,gating,non_gating} [--share 0.25] [--fill easy|none] [--seed S]

Every production team leaves ``--share`` of its dispatched effort undone by choice each shift and, with ``--fill easy``,
works the shortest ready tasks of the coming hours instead. ``--pick`` says which planned tasks it leaves:

* ``plan``: none (the floor works its plan);
* ``hardest``: the tasks with the most standard person-minutes (easy work first);
* ``random``: any;
* ``gating``: first the tasks that other planned work of this shift or the next waits on;
* ``non_gating``: first the tasks nothing planned waits on (a team with too few of its kind leaves some of the other).

The same fleet, the same seed and the same share under every pick, so the floors differ only in which jobs were left.
The forecast is planned every shift, so each floor's delivery dates and costs can be compared. A person starts a task
only after finishing the previous one on every floor. Writes OUT.db and OUT.summary.json.
"""
from __future__ import annotations

import argparse
import json
import time

from common import SCRATCH, rss_mb  # noqa: F401  (sets TMPDIR and sys.path)

import focus.sim.runner as R
from focus.data.fullscale_fixture import FLEET_FACTS, FleetSpec, build_fullscale_fixture
from focus.sim.execute import FloorSpec, TeamTraits

ap = argparse.ArgumentParser()
ap.add_argument("n", type=int)
ap.add_argument("team_size")
ap.add_argument("shifts", type=int)
ap.add_argument("out")
ap.add_argument("--pick", required=True, choices=("plan", "hardest", "random", "gating", "non_gating"))
ap.add_argument("--share", type=float, default=0.25)
ap.add_argument("--fill", default="easy", choices=("easy", "none"))
ap.add_argument("--seed", type=int, default=20261026)
ap.add_argument("--late-share", type=float, default=0.02)
ap.add_argument("--late-max", type=int, default=3)
ap.add_argument("--lead", type=int, default=20)
ap.add_argument("--no-forecast", action="store_true")
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
fleet = FleetSpec(n_aircraft=args.n, team_size=(lo, hi), late_part_share=args.late_share, late_delays=((1.0, 1, args.late_max),))
build_fullscale_fixture(fleet)
teams = sorted(t for t in FLEET_FACTS["team_sizes"] if t != "QA")
traits = TeamTraits(skip={} if args.pick == "plan" else {t: args.share for t in teams}, skip_pick="hardest" if args.pick == "plan" else args.pick,
                    fill=args.fill)
spec = R.SimSpec(fleet=fleet, floor=FloorSpec(deviation=0.0, pull_ahead=0.35), days=(args.shifts + 2) // 3, shifts=args.shifts,
                 purposes=("EXECUTABLE",) if args.no_forecast else ("EXECUTABLE", "FORECAST"), max_moves=0, max_repairs=0, seed=args.seed,
                 cutoff_lead_minutes=args.lead)
db_path = SCRATCH / "out" / f"{args.out}.db"
if db_path.exists():
    db_path.unlink()
t = time.time()
res = R.run(spec, db_path, log=lambda m: print(m, flush=True), traits=traits)
summary = {"out": args.out, "pick": args.pick, "share": args.share, "fill": args.fill, "seed": args.seed, "shifts": args.shifts, "n": args.n,
           "team_size": [lo, hi], "seconds": round(time.time() - t, 1), "peak_rss_mb": round(rss_mb()), "per_shift": res["shifts"]}
(SCRATCH / "out" / f"{args.out}.summary.json").write_text(json.dumps(summary, default=str))
print(json.dumps({k: summary[k] for k in ("out", "seconds", "peak_rss_mb")}))
