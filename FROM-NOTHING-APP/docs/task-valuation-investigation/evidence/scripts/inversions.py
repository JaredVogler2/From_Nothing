"""How often the planned order inverts a per-unit value, and what isotonic (PAV) evening-out would cost.

Usage: inversions.py N LO,HI OUT [--late-share x --late-max d] [--study out/B5.json] [--purposes EXECUTABLE,FORECAST]

Per purpose plan (seed + cost-of-delay redispatch, no search, no incumbent), units = crewed placements starting in each of the
cutoff day's three shift windows. Groups: team-shift, and person-shift sequences. Order: planned start, ties by value
(highest first). Values: V1 standard person-minutes; V2 = V1 x criticality weight (1 for $0, 2 for nonzero below the P90
of nonzero, 3 for the top decile of nonzero); V3 = delay dollars + $0.01 per person-minute. Delay dollars come from the
scheduler's cost of delay (a) recomputed on the plan, for a one-shift slip; for the FORECAST first shift also from the
exact counterfactual (d1) of the study JSON when given.
"""
from __future__ import annotations

import argparse
import gc
import json
import statistics
import time
from collections import Counter, defaultdict

from common import SCRATCH, build, rss_mb

from focus.calendar import build_shift
from focus.domain import EdgeType, Purpose, to_minute
from focus.engine.max.optimizer.orchestrator import MaxOptimizer
from focus.engine.max.optimizer.urgency import cost_of_delay
from focus.engine.model import EngineContext
from focus.engine.projections import completion_minutes

ap = argparse.ArgumentParser()
ap.add_argument("n", type=int)
ap.add_argument("team_size")
ap.add_argument("out")
ap.add_argument("--late-share", type=float, default=None)
ap.add_argument("--late-max", type=int, default=None)
ap.add_argument("--study", default=None)
ap.add_argument("--purposes", default="EXECUTABLE,FORECAST")
args = ap.parse_args()
lo, hi = (int(x) for x in args.team_size.split(","))
fleet = {}
if args.late_share is not None:
    fleet["late_part_share"] = args.late_share
if args.late_max is not None:
    fleet["late_delays"] = ((1.0, 1, args.late_max),)
services, snapshot, shift0, facts, spec = build(args.n, (lo, hi), **fleet)
policy = services.policy
day = spec.cutoff_day
from focus.calendar import ShiftPolicy  # noqa: E402

_sp = ShiftPolicy.from_policy(policy)
windows = {k: (s.start, s.end) for k in (1, 2, 3) for s in [build_shift(day, k, _sp, services.settings.site)]}
study = json.load(open(args.study)) if args.study else None
d1_of = {r["pid"]: r["d1_usd"] for r in study["rows"] if "d1_usd" in r} if study else {}


def pav_nonincreasing(v):
    """L2 isotonic regression, non-increasing, unit weights (the total is preserved). Returns (fitted, block sizes)."""
    blocks = []                      # [sum, count]
    for x in v:
        blocks.append([x, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    out, sizes = [], []
    for s, c in blocks:
        out += [s / c] * c
        sizes.append(c)
    return out, sizes


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


def analyse(groups: dict, value_of, cause_of) -> dict:
    """groups: key -> list of unit dicts. value_of(u) -> float."""
    n_groups = n_inv = n_units = n_changed = 0
    rel_all, rel_changed, largest, pairs_inv, pairs_tot = [], [], [], 0, 0
    causes = Counter()
    for key, us in groups.items():
        if len(us) < 2:
            continue
        order = sorted(us, key=lambda u: (u["start"], -value_of(u), u["pid"]))
        v = [value_of(u) for u in order]
        n_groups += 1
        n_units += len(v)
        inv = any(v[i + 1] > v[i] + 1e-9 for i in range(len(v) - 1))
        n_inv += inv
        for i in range(len(v) - 1):
            if v[i + 1] > v[i] + 1e-9:
                causes[cause_of(order[i + 1])] += 1
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                if order[j]["start"] != order[i]["start"]:
                    pairs_tot += 1
                    pairs_inv += v[j] > v[i] + 1e-9
        fit, sizes = pav_nonincreasing(v)
        largest.append(max(sizes))
        for a, b in zip(v, fit):
            rel = abs(b - a) / a if a > 0 else (0.0 if abs(b - a) < 1e-9 else float("inf"))
            changed = abs(b - a) > 1e-9 * max(1.0, abs(a))
            n_changed += changed
            rel_all.append(rel)
            if changed:
                rel_changed.append(rel)
    return {"groups": n_groups, "groups_with_inversion": n_inv, "share_groups_inverted": round(n_inv / n_groups, 3) if n_groups else None,
            "units": n_units, "units_changed": n_changed, "share_units_changed": round(n_changed / n_units, 3) if n_units else None,
            "rel_change_median_all": pct(rel_all, 0.5), "rel_change_p90_all": pct(rel_all, 0.9),
            "rel_change_median_changed": pct(rel_changed, 0.5), "rel_change_p90_changed": pct(rel_changed, 0.9),
            "largest_block_median": pct(largest, 0.5), "largest_block_p90": pct(largest, 0.9), "largest_block_max": max(largest) if largest else None,
            "pair_inversion_rate": round(pairs_inv / pairs_tot, 3) if pairs_tot else None, "causes_of_later_higher_unit": dict(causes.most_common())}


result = {"n": args.n, "fleet": {k: str(v) for k, v in fleet.items()}, "windows": windows}
for name in args.purposes.split(","):
    purpose = Purpose(name)
    t = time.time()
    ctx = EngineContext(snapshot, policy, purpose, native=True)
    opt = MaxOptimizer(ctx, gate=False, max_moves=0, max_repairs=0, budget_seconds=3600)
    run = opt.run()
    best = run.best
    d = best.decode
    reg = opt.registry
    w = reg.derive_priority_weights(completion_minutes(best.completions))
    cod = cost_of_delay(ctx, reg, d, w)
    units = []
    for pid, p in d.placements.items():
        if not p.crew:
            continue
        k = next((k for k, (a, b) in windows.items() if a <= p.start < b), None)
        if k is None:
            continue
        ph = ctx.phases[pid]
        ss = ctx.line_shifts(p.line)
        idx = next(i for i, s in enumerate(ss) if s.id == p.shift_id)
        D = (ss[idx + 1].start if idx + 1 < len(ss) else p.end + 480) - p.start
        # why the unit starts when it does: its binding floor, or a wait for crew/capacity
        floor, notes = ctx.job_floor(p.job)
        job = ctx.jobs[p.job]
        facts_cs = ctx.cs.get((job.line, job.station))
        bind = "cutoff" if floor == ctx.cutoff_minute else ("station_window" if facts_cs and facts_cs.earliest_at and to_minute(facts_cs.earliest_at) == floor else "job_earliest")
        for e in ctx.preds.get(pid, ()):
            q = e.pred_phase
            if q in d.placements:
                ps, pe = d.placements[q].start, d.placements[q].end
            elif q in d.milestones:
                ps = pe = d.milestones[q]
            elif q in d.satisfied:
                ps = pe = d.satisfied[q]
            else:
                continue
            f = (pe + e.lag_minutes) if e.type is EdgeType.FS else (ps + e.lag_minutes) if e.type is EdgeType.SS else None
            if f is not None and f > floor:
                floor = f
                bind = "part_release" if q in ctx.releases else ("precedence_same_job" if q in d.placements and d.placements[q].job == p.job
                                                                  else "precedence" if q in d.placements else "verified_complete_pred")
        cause = bind if p.start <= floor else "crew_or_capacity_wait"
        a_usd = cod.get(pid, 0) / 1e6 / 100 * D / 1440
        units.append({"pid": pid, "team": ph.role_demands[0].team, "shift_no": k, "start": p.start, "crew": p.crew, "spm": ph.standard_person_minutes,
                      "a_usd": a_usd, "d1_usd": d1_of.get(pid), "cause": cause, "wait": d.waits.get(pid, 0)})
    a_nz = sorted(u["a_usd"] for u in units if u["a_usd"] > 0)
    p90a = pct(a_nz, 0.9) if a_nz else None

    def wgt(x, p90):
        return 1 if not x or x <= 0 else (3 if p90 is not None and x >= p90 else 2)

    team_groups, person_groups = defaultdict(list), defaultdict(list)
    for u in units:
        team_groups[(u["team"], u["shift_no"])].append(u)
        for person in u["crew"]:
            person_groups[(person, u["shift_no"])].append(u)
    vals = {"V1_spm": lambda u: u["spm"],
            "V2_spm_x_weight(a)": lambda u: u["spm"] * wgt(u["a_usd"], p90a),
            "V3_delay_usd(a)+floor": lambda u: u["a_usd"] + 0.01 * u["spm"]}
    out = {"seconds": round(time.time() - t, 1), "units": len(units), "team_shifts": len(team_groups), "person_shifts": len(person_groups),
           "units_with_nonzero_a": len(a_nz), "a_p90_nonzero_usd": p90a, "unknown_aircraft": len(best.summary.unknown_lines),
           "redispatch_kept": bool(run.urgency), "causes_all_units": dict(Counter(u["cause"] for u in units).most_common()),
           "units_waited": sum(1 for u in units if u["wait"] > 0), "by_value": {}}
    if purpose is Purpose.FORECAST and d1_of:
        first = [u for u in units if u["shift_no"] == 1 and u["d1_usd"] is not None]
        d_nz = sorted(u["d1_usd"] for u in first if u["d1_usd"] > 0)
        p90d = pct(d_nz, 0.9) if d_nz else None
        tg, pg = defaultdict(list), defaultdict(list)
        for u in first:
            tg[(u["team"], 1)].append(u)
            for person in u["crew"]:
                pg[(person, 1)].append(u)
        out["d1_first_shift"] = {"units": len(first), "nonzero": len(d_nz), "negative": sum(1 for u in first if u["d1_usd"] < 0)}
        for label, fn in {"V1_spm": lambda u: u["spm"], "V2_spm_x_weight(d1)": lambda u: u["spm"] * wgt(max(0.0, u["d1_usd"]), p90d),
                          "V3_delay_usd(d1)+floor": lambda u: max(0.0, u["d1_usd"]) + 0.01 * u["spm"]}.items():
            out["by_value"][f"first_shift:{label}"] = {"team_shift": analyse(tg, fn, lambda u: u["cause"]),
                                                       "person_shift": analyse(pg, fn, lambda u: u["cause"])}
    for label, fn in vals.items():
        out["by_value"][label] = {"team_shift": analyse(team_groups, fn, lambda u: u["cause"]),
                                  "person_shift": analyse(person_groups, fn, lambda u: u["cause"])}
    result[name] = out
    print(name, json.dumps({k: v for k, v in out.items() if k != "by_value"}), flush=True)
    for label, r in out["by_value"].items():
        ts, ps = r["team_shift"], r["person_shift"]
        print(f"  {label:32s} team-shift inv {ts['share_groups_inverted']} changed {ts['share_units_changed']} rel med/p90 "
              f"{ts['rel_change_median_all']}/{ts['rel_change_p90_all']} (changed {ts['rel_change_median_changed']}/{ts['rel_change_p90_changed']}) "
              f"block med/p90/max {ts['largest_block_median']}/{ts['largest_block_p90']}/{ts['largest_block_max']} pairs {ts['pair_inversion_rate']} | "
              f"person inv {ps['share_groups_inverted']} changed {ps['share_units_changed']} rel p90 {ps['rel_change_p90_all']} block max {ps['largest_block_max']}",
              flush=True)
    del ctx, opt, run, best, d
    gc.collect()
result["peak_rss_mb"] = round(rss_mb())
json.dump(result, open(SCRATCH / "out" / f"{args.out}.json", "w"), default=str)
print("peak MB", result["peak_rss_mb"])
