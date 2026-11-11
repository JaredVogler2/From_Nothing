"""KT: a floor where teams differ by construction, to see which performance measure finds the difference.

Usage: known_truth.py N LO,HI SHIFTS OUT [--seed S] [--slow 1.4] [--fast 0.75] [--swap 0.6]

Thirty production teams are put, by a seeded shuffle, into six groups of five:

* ``slow``: every actual duration is multiplied by --slow;
* ``fast``: by --fast;
* ``swaps``: with probability --swap per shift the team works an unplanned ready task in place of a planned one;
* ``works_ahead``: an idle team always pulls ready work forward;
* ``never_ahead``: it never does;
* ``baseline``: the floor's defaults (no swaps, pulls ahead in 35% of idle shifts).

Everything else is the paired-floor setup of ``offplan_paired.py``: the same fleet, the floor works the executable plan,
no forecast. A person starts a task only after finishing the previous one (``TeamTraits.crew_sequencing``), so a slow
team can run out of shift. Writes OUT.db and OUT.truth.json (each team's group).
"""
from __future__ import annotations

import argparse
import json
import random
import time

from common import SCRATCH, rss_mb  # noqa: F401  (sets TMPDIR and sys.path)

import focus.sim.runner as R
from focus.data.fullscale_fixture import FLEET_FACTS, FleetSpec, build_fullscale_fixture
from focus.sim.execute import FloorSpec, TeamTraits

GROUPS = ("slow", "fast", "swaps", "works_ahead", "never_ahead", "baseline")

ap = argparse.ArgumentParser()
ap.add_argument("n", type=int)
ap.add_argument("team_size")
ap.add_argument("shifts", type=int)
ap.add_argument("out")
ap.add_argument("--seed", type=int, default=20261026)
ap.add_argument("--slow", type=float, default=1.4)
ap.add_argument("--fast", type=float, default=0.75)
ap.add_argument("--swap", type=float, default=0.6)
ap.add_argument("--late-share", type=float, default=0.02)
ap.add_argument("--late-max", type=int, default=3)
ap.add_argument("--lead", type=int, default=20)
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
fleet = FleetSpec(n_aircraft=args.n, team_size=(lo, hi), late_part_share=args.late_share, late_delays=((1.0, 1, args.late_max),))
build_fullscale_fixture(fleet)
teams = sorted(t for t in FLEET_FACTS["team_sizes"] if t != "QA")
order = list(teams)
random.Random(args.seed * 31 + 7).shuffle(order)
per = len(order) // len(GROUPS)
group = {team: GROUPS[min(i // per, len(GROUPS) - 1)] for i, team in enumerate(order)}
traits = TeamTraits(pace={t: args.slow if g == "slow" else args.fast for t, g in group.items() if g in ("slow", "fast")},
                    deviation={t: args.swap for t, g in group.items() if g == "swaps"},
                    pull_ahead={t: 1.0 if g == "works_ahead" else 0.0 for t, g in group.items() if g in ("works_ahead", "never_ahead")})
spec = R.SimSpec(fleet=fleet, floor=FloorSpec(deviation=0.0, pull_ahead=0.35), days=(args.shifts + 2) // 3, shifts=args.shifts,
                 purposes=("EXECUTABLE",), max_moves=0, max_repairs=0, seed=args.seed, cutoff_lead_minutes=args.lead)
db_path = SCRATCH / "out" / f"{args.out}.db"
if db_path.exists():
    db_path.unlink()
t = time.time()
res = R.run(spec, db_path, log=lambda m: print(m, flush=True), traits=traits)
truth = {"out": args.out, "seed": args.seed, "shifts": args.shifts, "slow": args.slow, "fast": args.fast, "swap": args.swap, "group": group,
         "team_sizes": {t: FLEET_FACTS["team_sizes"][t] for t in teams}, "seconds": round(time.time() - t, 1), "peak_rss_mb": round(rss_mb())}
(SCRATCH / "out" / f"{args.out}.truth.json").write_text(json.dumps(truth, indent=1))
print(json.dumps({k: truth[k] for k in ("out", "seconds", "peak_rss_mb")}))
