"""SB: what the scheduler's improvement search buys, and what the shipped time budget leaves of it. Investigation only.

Usage: search_budget.py run OUT MOVES REPAIRS [flat]    (one planned shift of the 10-aircraft fleet, seed 20261026)
       search_budget.py compare OUT.json NAME [NAME ...]

Every run plans the same fleet with the same data: only the bounds of the improvement search after the first pass
differ (``MOVES`` neighbourhood moves and ``REPAIRS`` delay repairs; 0 0 is the first pass alone: the neutral seed and the
economic redispatch). ``compare`` reads each forecast's late days and late cost, and the seconds each stage took, and
sets the first pass beside ``optimizer.budget_seconds``: the shipped budget counts from the start of the run, so a first
pass that takes longer leaves the search no time.

``flat`` prices every customer and minor model at the same daily rate (FLAT_RATE) instead of the fixture's rates. The
first pass orders work by each aircraft's rate, so this is the case where the rates give it nothing to order by. Costs
are comparable only between runs with the same rates.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time

from common import APP, SCRATCH  # noqa: F401  (sets TMPDIR and sys.path)

import focus.sim.runner as R
from focus.data.fullscale_fixture import FleetSpec, penalty_terms
from focus.policy import load_policy
from focus.sim.execute import FloorSpec, TeamTraits

SEED = 20261026
FLAT_RATE = 250_000


def run(out: str, moves: int, repairs: int, rates: str = "fixture") -> None:
    terms = None if rates == "fixture" else tuple((code, name, model, orders, FLAT_RATE) for code, name, model, orders, _ in penalty_terms())
    fleet = FleetSpec(n_aircraft=10, team_size=(5, 10), late_part_share=0.02, late_delays=((1.0, 1, 3),), customer_models=terms)
    spec = R.SimSpec(fleet=fleet, floor=FloorSpec(deviation=0.0, pull_ahead=0.35), days=1, shifts=1, purposes=("EXECUTABLE", "FORECAST"),
                     max_moves=moves, max_repairs=repairs, seed=SEED, cutoff_lead_minutes=20)
    db = SCRATCH / "out" / f"{out}.db"
    if db.exists():
        db.unlink()
    started = time.time()
    R.run(spec, db, log=lambda message: None, traits=TeamTraits())
    (SCRATCH / "out" / f"{out}.summary.json").write_text(json.dumps({"out": out, "moves": moves, "repairs": repairs, "rates": rates,
                                                                      "seconds": round(time.time() - started, 1)}))
    print(json.dumps({"out": out, "seconds": round(time.time() - started, 1)}))


def plan(name: str) -> dict:
    con = sqlite3.connect(f"file:{SCRATCH / 'out' / name}.db?mode=ro", uri=True)
    rows = con.execute("SELECT line, status, late_days, cost_usd FROM plan_aircraft WHERE purpose = 'FORECAST' AND shift_no = 0 ORDER BY line").fetchall()
    stages: dict[str, dict[str, dict]] = {}
    for purpose, stage, ms, candidates, accepted in con.execute(
            "SELECT purpose, stage, runtime_ms, candidates, accepted FROM method_stage WHERE shift_no = 0 ORDER BY purpose, seq"):
        stages.setdefault(purpose, {})[stage] = {"seconds": round(ms / 1000, 1), "candidates": candidates, "accepted": accepted}
    summary = json.loads((SCRATCH / "out" / f"{name}.summary.json").read_text())
    first = {p: round(s.get("neutral_seed", {}).get("seconds", 0) + s.get("economic_redispatch", {}).get("seconds", 0), 1) for p, s in stages.items()}
    return {"name": name, "rates": summary.get("rates", "fixture"), "moves": summary["moves"], "repairs": summary["repairs"],
            "aircraft": len(rows), "aircraft_with_unknown_finish": sum(1 for _, status, late, _ in rows if status == "unknown" or late is None),
            "late_days": {str(line): round(max(0.0, late or 0.0), 3) for line, _, late, _ in rows},
            "total_late_days": round(sum(max(0.0, late or 0.0) for _, _, late, _ in rows), 2),
            "late_cost_usd": round(sum(cost or 0.0 for *_, cost in rows)), "first_pass_seconds": first,
            "search_seconds": {p: s.get("neighborhoods", {}).get("seconds", 0) for p, s in stages.items()}, "stages": stages}


def compare(result: str, names: list[str]) -> None:
    plans = [plan(n) for n in names]
    budget = load_policy(APP / "config" / "policies").int("optimizer.budget_seconds")
    for p in plans:
        base = next(q for q in plans if q["rates"] == p["rates"])                         # the first plan given with the same rates
        p["late_cost_against_the_first_plan_percent"] = round(100 * (p["late_cost_usd"] / base["late_cost_usd"] - 1), 1) if base["late_cost_usd"] else None
        p["first_pass_within_the_shipped_budget"] = {purpose: seconds < budget for purpose, seconds in p["first_pass_seconds"].items()}
    json.dump({"shipped_budget_seconds": budget, "plans": plans}, open(result, "w"), indent=1)
    print(f"shipped optimizer.budget_seconds: {budget}\n")
    print(f"{'plan':12s} {'rates':>8s} {'search':>7s} {'late days':>9s} {'late cost':>12s} {'vs first':>9s} | first pass s (forecast, executable) | search s (forecast, executable)")
    for p in plans:
        f, s = p["first_pass_seconds"], p["search_seconds"]
        print(f"{p['name']:12s} {p['rates']:>8s} {str(p['moves']) + '/' + str(p['repairs']):>7s} {p['total_late_days']:9.2f} {p['late_cost_usd']:12,d} "
              f"{p['late_cost_against_the_first_plan_percent']:+8.1f}% | {f.get('FORECAST', 0):8.1f} {f.get('EXECUTABLE', 0):8.1f}"
              f"            | {s.get('FORECAST', 0):8.1f} {s.get('EXECUTABLE', 0):8.1f}")
    lines = sorted(plans[0]["late_days"], key=int)
    print("\nlate days by line:   " + " ".join(f"{line:>6s}" for line in lines))
    for p in plans:
        print(f"  {p['name']:18s} " + " ".join(f"{p['late_days'][line]:6.2f}" for line in lines))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), *sys.argv[5:6])
    else:
        compare(sys.argv[2], sys.argv[3:])
