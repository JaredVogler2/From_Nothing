"""SB: what the scheduler's improvement search buys, and what the shipped time budget leaves of it. Investigation only.

Usage: search_budget.py run OUT MOVES REPAIRS [fixture|flat|ticket] [--aircraft N] [--seed S] [--fleet-seed F]
                            [--late-parts few|standard]
       search_budget.py compare OUT.json NAME [NAME ...]

Runs with the same ``--aircraft``, ``--fleet-seed`` and ``--late-parts`` plan one shift of the same fleet with the same
data: the same customer and minor model on every line, the same work, people, parts and seed. The fleet is whichever
aircraft have open tasks (DECISIONS.md D-32), so one fleet is one example. Another ``--fleet-seed`` gives another:

* its customers and minor models are drawn at random from the open orders that have a rate, in proportion to the
  orders (without ``--fleet-seed`` the fleet is the proportional split the generator makes by default);
* its task networks, team sizes, days in station, late parts and rework are drawn again.

``--late-parts few`` (the default, as in the 10-aircraft runs) makes 2% of parts late by one to three days;
``standard`` keeps the generator's own share and delays, as in the stored 50-aircraft run. What differs between the
runs of one fleet:

* the bounds of the improvement search after the first pass: ``MOVES`` neighbourhood moves and ``REPAIRS`` delay
  repairs. ``0 0`` is the first pass alone: the neutral seed and the economic redispatch;
* the daily rates the plan is made with: ``fixture`` (the owner's airline economic proxy, DECISIONS.md D-31), ``flat``
  (every aircraft at FLAT_RATE: nothing for the first pass to order by) or ``ticket`` (the superseded ticket-revenue
  figures, which lie further apart).

``compare`` reads each forecast's late days, its late cost at its own rates and at the fixture's rates, and the seconds
each stage took, and sets the first pass beside ``optimizer.budget_seconds``: the shipped budget counts from the start
of the run, so a first pass that takes longer leaves the search no time. A plan made with other rates that is cheaper
at the fixture's rates than the plan made with them shows the search did not find the lowest cost.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import resource
import sqlite3
import sys
import time
from datetime import datetime

from common import APP, SCRATCH  # noqa: F401  (sets TMPDIR and sys.path)

import focus.sim.runner as R
from focus.data import fullscale_fixture as F
from focus.policy import load_policy
from focus.sim.execute import FloorSpec, TeamTraits

FLAT_RATE = 65_000
LATE_PARTS = {"few": {"late_part_share": 0.02, "late_delays": ((1.0, 1, 3),)}, "standard": {}}


def drawn(terms: list[tuple], aircraft: int, fleet_seed: int) -> tuple[tuple, ...]:
    """The terms with ``aircraft`` aircraft drawn at random over the customers and minor models that have open orders
    and a rate, in proportion to the orders. The count drawn stands where the open orders stood, so the generator's
    split gives exactly this fleet."""
    priced = [i for i, t in enumerate(terms) if t[3] > 0 and t[4] is not None]
    picks = random.Random(fleet_seed).choices(priced, weights=[terms[i][3] for i in priced], k=aircraft)
    return tuple((code, name, model, picks.count(i), rate) for i, (code, name, model, _, rate) in enumerate(terms))


def run(out: str, moves: int, repairs: int, rates: str, aircraft: int, seed: int, fleet_seed: int | None, late_parts: str = "few") -> None:
    terms = None                                                           # the fixture's own split and rates
    if fleet_seed is not None:
        terms = drawn(F.penalty_terms(), aircraft, fleet_seed)
    if rates != "fixture":
        with F.OPEN_ORDERS.open(encoding="utf-8", newline="") as fh:
            ticket = {(r["customer"], int(r["minor_model"])): int(r["ticket_revenue_usd_per_day"]) for r in csv.DictReader(fh)}
        other = (lambda name, model: FLAT_RATE) if rates == "flat" else (lambda name, model: ticket[(name, model)])
        # a pair without a rate in the fixture keeps none, so the fleet is the same under every set of rates
        terms = tuple((code, name, model, orders, None if rate is None else other(name, model))
                      for code, name, model, orders, rate in (terms or F.penalty_terms()))
    team_size = (5, 10) if aircraft <= 10 else (10, 30)
    fleet = F.FleetSpec(n_aircraft=aircraft, team_size=team_size, customer_models=terms, **LATE_PARTS[late_parts],
                        **({} if fleet_seed is None else {"seed": fleet_seed}))
    spec = R.SimSpec(fleet=fleet, floor=FloorSpec(deviation=0.0, pull_ahead=0.35), days=1, shifts=1, purposes=("EXECUTABLE", "FORECAST"),
                     max_moves=moves, max_repairs=repairs, seed=seed, cutoff_lead_minutes=20)
    db = SCRATCH / "out" / f"{out}.db"
    if db.exists():
        db.unlink()
    started = time.time()
    R.run(spec, db, log=lambda message: None, traits=TeamTraits())
    summary = {"out": out, "rates": rates, "moves": moves, "repairs": repairs, "aircraft": aircraft, "seed": seed, "fleet_seed": fleet_seed,
               "late_parts": late_parts, "seconds": round(time.time() - started, 1),
               "peak_memory_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}
    (SCRATCH / "out" / f"{out}.summary.json").write_text(json.dumps(summary))
    print(json.dumps(summary))


def plan(name: str) -> dict:
    con = sqlite3.connect(f"file:{SCRATCH / 'out' / name}.db?mode=ro", uri=True)
    rows = con.execute("SELECT line, status, late_days, cost_usd, completion_utc, due_utc FROM plan_aircraft "
                       "WHERE purpose = 'FORECAST' AND shift_no = 0 ORDER BY line").fetchall()
    customer = {line: (code, model) for line, code, model in con.execute("SELECT LineNumber, CustomerCode, MinorModel FROM src_econ_aircraft_mapping")}
    rate = {(code, model): float(value) for code, model, value in con.execute("SELECT CustomerCode, MinorModel, DailyRate FROM src_econ_term")}
    stages: dict[str, dict[str, dict]] = {}
    for purpose, stage, ms, candidates, accepted in con.execute(
            "SELECT purpose, stage, runtime_ms, candidates, accepted FROM method_stage WHERE shift_no = 0 ORDER BY purpose, seq"):
        stages.setdefault(purpose, {})[stage] = {"seconds": round(ms / 1000, 1), "candidates": candidates, "accepted": accepted}
    summary = json.loads((SCRATCH / "out" / f"{name}.summary.json").read_text())
    first = {p: round(s.get("neutral_seed", {}).get("seconds", 0) + s.get("economic_redispatch", {}).get("seconds", 0), 1) for p, s in stages.items()}
    # the stored late days are rounded to a thousandth of a day; the finish and the due time give them to the minute
    late = {line: max(0.0, (datetime.fromisoformat(finish) - datetime.fromisoformat(due)).total_seconds() / 86_400 if finish and due else days or 0.0)
            for line, _, days, _, finish, due in rows}
    return {"name": name, "rates": summary["rates"], "moves": summary["moves"], "repairs": summary["repairs"], "aircraft": len(rows),
            "seed": summary.get("seed"), "fleet_seed": summary.get("fleet_seed"), "late_parts": summary.get("late_parts", "few"),
            "run_seconds": summary.get("seconds"), "peak_memory_mb": summary.get("peak_memory_mb"),
            "aircraft_with_unknown_finish": sum(1 for _, status, days, *_ in rows if status == "unknown" or days is None),
            "aircraft_late": sum(1 for days in late.values() if days > 0),
            "late_days": {str(line): round(days, 3) for line, days in late.items()}, "total_late_days": round(sum(late.values()), 2),
            "late_cost_usd": round(sum(row[3] or 0.0 for row in rows)), "first_pass_seconds": first,
            "search_seconds": {p: s.get("neighborhoods", {}).get("seconds", 0) for p, s in stages.items()}, "stages": stages,
            "_customer": customer, "_rate": {line: rate.get(customer[line]) for line in customer}, "_late": late}


def compare(result: str, names: list[str]) -> None:
    plans = [plan(n) for n in names]
    budget = load_policy(APP / "config" / "policies").int("optimizer.budget_seconds")
    assert all(p["_customer"] == plans[0]["_customer"] for p in plans), "the fleets differ"
    fixture = next((p["_rate"] for p in plans if p["rates"] == "fixture"), None)
    for p in plans:
        base = next(q for q in plans if q["rates"] == p["rates"])                         # the first plan given with the same rates
        p["late_cost_against_the_first_plan_percent"] = round(100 * (p["late_cost_usd"] / base["late_cost_usd"] - 1), 1) if base["late_cost_usd"] else None
        p["late_cost_at_fixture_rates_usd"] = None if fixture is None else round(sum(days * fixture[line] for line, days in p["_late"].items()))
        p["first_pass_within_the_shipped_budget"] = {purpose: seconds < budget for purpose, seconds in p["first_pass_seconds"].items()}
    cheapest = min((p for p in plans if p["late_cost_at_fixture_rates_usd"] is not None), key=lambda p: p["late_cost_at_fixture_rates_usd"], default=None)
    mix: dict[str, int] = {}
    for code, model in plans[0]["_customer"].values():
        mix[f"{code} 787-{model}"] = mix.get(f"{code} 787-{model}", 0) + 1
    rates = sorted(v for v in (fixture or {}).values() if v is not None)
    fleet = {"aircraft": plans[0]["aircraft"], "seed": plans[0]["seed"], "fleet_seed": plans[0]["fleet_seed"], "late_parts": plans[0]["late_parts"],
             "customers_and_models": dict(sorted(mix.items())), "lowest_daily_rate_usd": rates[0] if rates else None,
             "highest_daily_rate_usd": rates[-1] if rates else None}
    for p in plans:
        for key in ("_customer", "_rate", "_late"):
            p.pop(key)
    json.dump({"shipped_budget_seconds": budget, "flat_rate_usd": FLAT_RATE, "fleet": fleet,
               "cheapest_plan_at_fixture_rates": cheapest and cheapest["name"], "plans": plans}, open(result, "w"), indent=1)
    print(f"shipped optimizer.budget_seconds: {budget}; aircraft {fleet['aircraft']}, seed {fleet['seed']}, fleet seed {fleet['fleet_seed']}, "
          f"{fleet['late_parts']} late parts; {len(mix)} customers and models, {sum(1 for p in [plans[0]] for d in p['late_days'].values() if d > 0)} "
          f"aircraft late in the first plan\n")
    print(f"{'plan':16s} {'rates':>8s} {'search':>7s} {'late d':>7s} {'late cost':>11s} {'vs first':>9s} {'at fixture rates':>17s} | first pass s (fc, ex) | search s (fc, ex)")
    for p in plans:
        f, s = p["first_pass_seconds"], p["search_seconds"]
        at = "" if p["late_cost_at_fixture_rates_usd"] is None else f"{p['late_cost_at_fixture_rates_usd']:,d}"
        print(f"{p['name']:16s} {p['rates']:>8s} {str(p['moves']) + '/' + str(p['repairs']):>7s} {p['total_late_days']:7.2f} {p['late_cost_usd']:11,d} "
              f"{p['late_cost_against_the_first_plan_percent']:+8.1f}% {at:>17s} | {f.get('FORECAST', 0):8.1f} {f.get('EXECUTABLE', 0):8.1f}  "
              f"| {s.get('FORECAST', 0):7.1f} {s.get('EXECUTABLE', 0):7.1f}")
    if cheapest is not None:
        print(f"\ncheapest at the fixture's rates: {cheapest['name']} (planned with {cheapest['rates']} rates, search {cheapest['moves']}/{cheapest['repairs']})")
    if plans[0]["aircraft"] <= 12:
        lines = sorted(plans[0]["late_days"], key=int)
        print("\nlate days by line:   " + " ".join(f"{line:>6s}" for line in lines))
        for p in plans:
            print(f"  {p['name']:18s} " + " ".join(f"{p['late_days'][line]:6.2f}" for line in lines))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        ap = argparse.ArgumentParser()
        ap.add_argument("out")
        ap.add_argument("moves", type=int)
        ap.add_argument("repairs", type=int)
        ap.add_argument("rates", nargs="?", default="fixture", choices=("fixture", "flat", "ticket"))
        ap.add_argument("--aircraft", type=int, default=10)
        ap.add_argument("--seed", type=int, default=20261026)
        ap.add_argument("--fleet-seed", type=int, default=None, help="another fleet: other customers, networks, teams and late parts")
        ap.add_argument("--late-parts", default="few", choices=tuple(LATE_PARTS))
        a = ap.parse_args(sys.argv[2:])
        run(a.out, a.moves, a.repairs, a.rates, a.aircraft, a.seed, a.fleet_seed, a.late_parts)
    else:
        compare(sys.argv[2], sys.argv[3:])
