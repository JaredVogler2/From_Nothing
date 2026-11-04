"""Calibration of the off-plan credit rule (R5) and the grade bands from simulation runs. Investigation only.

Usage: calibrate.py OUT RUN [RUN ...]     (each RUN names out/RUN.db and out/RUN.summary.json; behavior = RUN.split("_")[1])

Per run: production (verified output, jobs completed, open work at the last cutoff, other teams blocked, worked hours) and
team-shift scores under:
* R0, the current canonical formula (stored score);
* R5(w), w in 0.5..1.0: grade = 100 (w * min(1, (Cp + U_add)/D) + (1 - w) * min(1, Cp/D)) - 100 O / D, with off-plan work
  classified by the production-feasible rule P2-dispatch (substitutive when the team-shift left a ready dispatched
  assignment undone with no disruption on record: cause 'deviation'; additive otherwise);
* R5(w, q): the same with a share q of substitutive minutes misread as additive (classifier misses).
Plus the output index X = (Cp + U_add)/D (uncapped), the unexcused shortfall s = 1 - Cp/D, DONE_BEFORE_SHIFT effort,
and the team's load (planned standard minutes over available minutes).

Across runs (same seed against the N0 run): production change versus score change for every rule; sign agreement and
Spearman over behaviors; the largest w whose swap behaviors still lose score at each q.
Bands: score distributions of well-run behaviors, grades under candidate band sets, grade flips between a team's
consecutive shifts, pooled daily grades. Fairness: correlation of off-plan credit with load. Boundary: work finished in
the last cutoff-lead minutes of a shift and DONE_BEFORE_SHIFT units of the next.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

import numpy as np

from common import SCRATCH

WS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
QS = (0.0, 0.05, 0.15, 0.30)
RATE = 0.10
BAND_SETS = {"demo 90/80/70/60": (90, 80, 70, 60), "strict 95/90/85/80": (95, 90, 85, 80), "today's effective 85.7/71.4/57.1/42.9":
             (85.7, 71.4, 57.1, 42.9), "wide 92/85/75/65": (92, 85, 75, 65)}
WELL_RUN = ("N0", "PL", "PH", "DF")


def grade(score, bands):
    for g, lo in zip("ABCD", bands):
        if score >= lo - 1e-9:
            return g
    return "F"


def clamp(x):
    return min(100.0, max(0.0, x))


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def load_run(name: str) -> dict:
    db = sqlite3.connect(f"file:{SCRATCH / 'out' / (name + '.db')}?mode=ro", uri=True)
    summ = json.loads((SCRATCH / "out" / f"{name}.summary.json").read_text())
    q = lambda sql, *a: db.execute(sql, a).fetchall()
    off = defaultdict(Counter)
    for s, t, kind, e in q("SELECT shift_no, team, kind, SUM(effort) FROM exec_offplan GROUP BY 1, 2, 3"):
        off[(s, t)][kind] += e
    swapped = {(s, t) for s, t in q("SELECT DISTINCT shift_no, team FROM exec_assignment WHERE cause = 'deviation'")}
    before = {(s, t): e for s, t, e in q("SELECT shift_no, team, SUM(effort) FROM perf_unit WHERE status = 'DONE_BEFORE_SHIFT' GROUP BY 1, 2")}
    displaced = {(s, t): e for s, t, e in q("SELECT shift_no, team, SUM(effort) FROM perf_unit WHERE cause = 'deviation' GROUP BY 1, 2")}
    day_of = {k: (d, n) for k, d, n in q("SELECT shift_no, production_day, shift_number FROM sim_shift")}
    rows = []
    for s, t, B0, B, E, Cp, U, oos, avail, worked, present in q(
            "SELECT shift_no, team, B0, B, E, Cp, U, oos_effort, available_hours, actual_hours, present FROM perf_team_shift"):
        D = B - E
        o = off[(s, t)]
        sub_truth = o["deviation"]
        add_truth = o["pull_ahead"]
        other = o["outside_goals"]
        sub_p2 = (o["pull_ahead"] + o["deviation"]) if (s, t) in swapped else 0
        add_p2 = 0 if (s, t) in swapped else (o["pull_ahead"] + o["deviation"])
        rows.append({"shift": s, "team": t, "day": day_of[s][0], "number": day_of[s][1], "B0": B0, "B": B, "E": E, "D": D, "Cp": Cp, "U": U,
                     "O": RATE * oos, "add": add_p2, "sub": sub_p2, "add_truth": add_truth, "sub_truth": sub_truth, "other": other,
                     "before": before.get((s, t), 0), "displaced": displaced.get((s, t), 0),
                     "load": (B0 / (avail * 60)) if avail else None, "worked_h": worked, "avail_h": avail})
    prod = {
        "throughput": q("SELECT SUM(Cp) + SUM(U) FROM perf_team_shift")[0][0],
        "jobs": sum(x.get("jobs_completed", 0) for x in summ["per_shift"]),
        "open_work_last": q("SELECT SUM(work_minutes*crew) FROM calc_job WHERE shift_no = (SELECT MAX(shift_no) FROM calc_job)")[0][0],
        "cross_team_relief": q("SELECT COALESCE(SUM(effort), 0) FROM perf_claim WHERE cause = 'CROSS_TEAM_PREDECESSOR' AND decision = 'APPROVED'")[0][0],
        "worked_h": q("SELECT SUM(actual_hours) FROM perf_team_shift")[0][0],
        "offplan_by_kind": {k: e for k, e in q("SELECT kind, SUM(effort) FROM exec_offplan GROUP BY kind")},
        "swaps": q("SELECT COUNT(*) FROM exec_assignment WHERE cause = 'deviation'")[0][0],
        "done_before_units": q("SELECT COUNT(*), COALESCE(SUM(effort), 0) FROM perf_unit WHERE status = 'DONE_BEFORE_SHIFT'")[0],
        "late_plans": q("SELECT COUNT(*) FROM exec_event WHERE kind = 'plan_accepted_late'")[0][0],
        "replanned_missed": q("SELECT COALESCE(SUM(effort), 0) FROM perf_unit WHERE cause = 'replanned'")[0][0],
        "stored_score_mean": q("SELECT AVG(score) FROM perf_team_shift WHERE score IS NOT NULL")[0][0],
    }
    # the shift boundary: completions in the last cutoff-lead minutes of each shift
    lead = summ.get("lead", 15)
    ends = {k: datetime.fromisoformat(e) for k, e in q("SELECT shift_no, end_utc FROM sim_shift")}
    late_done = total_done = 0
    for k, end in q("SELECT shift_no, actual_end FROM exec_assignment WHERE outcome = 'completed'"):
        total_done += 1
        late_done += datetime.fromisoformat(end) > ends[k] - timedelta(minutes=lead)
    prod["completions_in_last_lead_minutes"] = {"lead": lead, "late": late_done, "all": total_done,
                                                "share": round(late_done / max(1, total_done), 4)}
    return {"name": name, "behavior": name.split("_")[1], "seed": name.split("_")[-1], "rows": rows, "prod": prod, "summary": summ}


def r5(r, w, q=0.0):
    if r["D"] <= 0:
        return None
    add = r["add"] + q * r["sub"]
    return clamp(100 * (w * min(1, (r["Cp"] + add) / r["D"]) + (1 - w) * min(1, r["Cp"] / r["D"])) - 100 * r["O"] / r["D"])


def r0(r):
    if r["D"] <= 0:
        return None
    A = min(1, r["Cp"] / r["D"])
    H = r["Cp"] / (r["Cp"] + r["U"]) if r["Cp"] + r["U"] else 0
    return clamp(100 * (0.7 * A + 0.3 * H) - 100 * r["O"] / r["D"])


def pooled(rows, w=None, q=0.0):
    """One score over many team-shifts: components summed first (the rollup rule, D-25)."""
    tot = Counter()
    for r in rows:
        for k in ("D", "Cp", "U", "O", "add", "sub"):
            tot[k] += r[k]
    fake = dict(tot)
    return r0(fake) if w is None else r5(fake, w, q)


def run_scores(run):
    rows = [r for r in run["rows"] if r["D"] > 0]
    out = {"team_shifts": len(rows), "R0_mean": mean(r0(r) for r in rows), "R0_pooled": pooled(rows)}
    for w in WS:
        for q in QS:
            out[f"R5_{w}_{q}_mean"] = mean(r5(r, w, q) for r in rows)
            out[f"R5_{w}_{q}_pooled"] = pooled(rows, w, q)
    out["output_index_pooled"] = sum(r["Cp"] + r["add"] for r in rows) / sum(r["D"] for r in rows)
    out["shortfall_pooled"] = 1 - sum(r["Cp"] for r in rows) / sum(r["D"] for r in rows)
    return out


def main():
    out_name, names = sys.argv[1], sys.argv[2:]
    runs = [load_run(n) for n in names]
    res: dict = {"runs": {}}
    for run in runs:
        res["runs"][run["name"]] = {"behavior": run["behavior"], "seed": run["seed"], "prod": run["prod"], "scores": run_scores(run),
                                    "summary": {k: run["summary"].get(k) for k in ("deviation", "pull_ahead", "swap_pick", "horizon", "late_plans",
                                                                                     "lead", "seconds")}}
    # ---------------------------------------------------------------- alignment against N0 of the same seed
    base = {run["seed"]: run for run in runs if run["behavior"] == "N0"}
    deltas = []
    for run in runs:
        b = base.get(run["seed"])
        if b is None or run is b:
            continue
        P, Pb = run["prod"], b["prod"]
        S, Sb = res["runs"][run["name"]]["scores"], res["runs"][b["name"]]["scores"]
        d = {"run": run["name"], "behavior": run["behavior"], "seed": run["seed"],
             "d_throughput": P["throughput"] - Pb["throughput"], "d_jobs": P["jobs"] - Pb["jobs"],
             "d_open_work": P["open_work_last"] - Pb["open_work_last"], "d_cross_team_relief": P["cross_team_relief"] - Pb["cross_team_relief"],
             "d_R0_pooled": S["R0_pooled"] - Sb["R0_pooled"], "d_output_index": S["output_index_pooled"] - Sb["output_index_pooled"]}
        for w in WS:
            for q in QS:
                d[f"d_R5_{w}_{q}"] = S[f"R5_{w}_{q}_pooled"] - Sb[f"R5_{w}_{q}_pooled"]
        deltas.append(d)
    res["deltas"] = deltas
    align = {}
    if deltas:
        prodv = [d["d_throughput"] for d in deltas]
        for key in ["d_R0_pooled", "d_output_index"] + [f"d_R5_{w}_{q}" for w in WS for q in QS]:
            vals = [d[key] for d in deltas]
            agree = sum(1 for p, v in zip(prodv, vals) if (p > 0 and v > 1e-9) or (p < 0 and v < -1e-9) or (abs(p) < 1 and abs(v) <= 1e-9))
            align[key] = {"spearman_vs_throughput": spearman(prodv, vals), "sign_agreement": f"{agree}/{len(vals)}"}
    res["alignment"] = align
    # the largest w at which every swap behavior still loses score, at each classifier miss rate
    swaps = [d for d in deltas if d["behavior"] in ("SE", "SC", "SF")]
    res["swap_proof_w"] = {str(q): max([w for w in WS if all(d[f"d_R5_{w}_{q}"] < 0 for d in swaps)] or [None]) for q in QS} if swaps else {}
    res["swap_score_change"] = {d["run"]: {f"w{w}_q{q}": round(d[f"d_R5_{w}_{q}"], 3) for w in (0.7, 0.8, 0.9, 1.0) for q in QS} for d in swaps}
    # does a swap pay the team that makes it? Each swap team-shift against the same team-shift had it done the displaced
    # planned work instead of the swapped-in work, with a share q of the swapped-in minutes misread as additive
    team_gain: dict = {}
    swap_rows = [r for run in runs for r in run["rows"] if r["D"] > 0 and r["displaced"] > 0]
    for w in WS:
        for q in QS:
            gains = []
            for r in swap_rows:
                actual = r5(r, w, q)
                counter = r5({**r, "Cp": r["Cp"] + r["displaced"], "sub": 0}, w, 0.0)
                gains.append(actual - counter)
            if gains:
                team_gain[f"w{w}_q{q}"] = {"swap_team_shifts": len(gains), "share_that_pays": round(sum(g > 1e-9 for g in gains) / len(gains), 4),
                                           "mean_gain": round(mean(gains), 3), "max_gain": round(max(gains), 3)}
    res["swap_gain_per_team_shift"] = team_gain
    # ---------------------------------------------------------------- bands: well-run team-shifts and grade flips
    bands: dict = {}
    well = [r for run in runs if run["behavior"] in WELL_RUN for r in run["rows"] if r["D"] > 0]
    for w in (0.7, 0.8, 0.9):
        sc = sorted(r5(r, w) for r in well)
        bands[f"well_run_R5_{w}_percentiles"] = {p: round(float(np.percentile(sc, p)), 2) for p in (1, 5, 10, 25, 50)} if sc else {}
        bands[f"well_run_R5_{w}_grades"] = {label: dict(Counter(grade(x, b) for x in sc)) for label, b in BAND_SETS.items()}
    sf = sorted(1 - r["Cp"] / r["D"] for r in well)
    bands["well_run_shortfall_percentiles"] = {p: round(float(np.percentile(sf, p)), 4) for p in (50, 75, 90, 95, 99)} if sf else {}
    # grade flips: the same team's consecutive shifts within one run, R5 at w = 0.8
    flips = {label: [0, 0] for label in BAND_SETS}
    for run in runs:
        if run["behavior"] not in WELL_RUN:
            continue
        by_team = defaultdict(list)
        for r in run["rows"]:
            if r["D"] > 0:
                by_team[r["team"]].append((r["shift"], r5(r, 0.8)))
        for seq in by_team.values():
            seq.sort()
            for (_, a), (_, b) in zip(seq, seq[1:]):
                for label, bs in BAND_SETS.items():
                    flips[label][0] += grade(a, bs) != grade(b, bs)
                    flips[label][1] += 1
    bands["grade_flip_rate_consecutive_shifts_R5_0.8"] = {label: round(f / max(1, n), 4) for label, (f, n) in flips.items()}
    # pooled daily grades per team (the day rollup), R5 at w = 0.8
    daily = defaultdict(list)
    for run in runs:
        if run["behavior"] not in WELL_RUN:
            continue
        for r in run["rows"]:
            if r["D"] > 0:
                daily[(run["name"], r["team"], r["day"])].append(r)
    dsc = [pooled(v, 0.8) for v in daily.values()]
    bands["well_run_daily_pooled_R5_0.8"] = {"n": len(dsc), "percentiles": {p: round(float(np.percentile(dsc, p)), 2) for p in (1, 5, 10, 25, 50)}} if dsc else {}
    bands["shortfall_to_score"] = "without off-plan work R5 = 100 x (1 - unexcused shortfall) - OOS charge, for any w"
    res["bands"] = bands
    # ---------------------------------------------------------------- fairness: off-plan credit against load
    fair = {}
    for run in runs:
        rows = [r for r in run["rows"] if r["D"] > 0 and r["load"] is not None]
        if not rows:
            continue
        gain = [r5(r, 0.8) - clamp(100 * min(1, r["Cp"] / r["D"]) - 100 * r["O"] / r["D"]) for r in rows]
        x = [(r["Cp"] + r["add"]) / r["D"] for r in rows]
        loads = [r["load"] for r in rows]
        fair[run["name"]] = {"corr_load_vs_grade_gain": spearman(loads, gain), "corr_load_vs_output_index": spearman(loads, x),
                             "mean_load": round(mean(loads), 3), "grade_gain_mean": round(mean(gain), 3)}
    res["fairness"] = fair
    (SCRATCH / "out" / f"{out_name}.json").write_text(json.dumps(res, indent=1, default=str))
    # ---------------------------------------------------------------- printout
    print("run          beh  thru     jobs  openwork   xteam  swaps  off(pa/dv/og)        dbs_units  late%  R0     R5w.8  X")
    for run in runs:
        P, S = run["prod"], res["runs"][run["name"]]["scores"]
        o = P["offplan_by_kind"]
        print(f"{run['name']:<12} {run['behavior']:<4} {P['throughput']:>8} {P['jobs']:>5} {P['open_work_last']:>9} {P['cross_team_relief']:>6} "
              f"{P['swaps']:>5}  {o.get('pull_ahead', 0):>6}/{o.get('deviation', 0):>5}/{o.get('outside_goals', 0):>5}  {P['done_before_units'][0]:>4}/{P['done_before_units'][1]:>6} "
              f"{P['completions_in_last_lead_minutes']['share']:.3f} {S['R0_pooled']:.2f}  {S['R5_0.8_0.0_pooled']:.2f}  {S['output_index_pooled']:.3f}")
    print("\nalignment (score change vs throughput change, against N0 of the same seed):")
    for key in ["d_R0_pooled", "d_output_index"] + [f"d_R5_{w}_0.0" for w in WS]:
        if key in align:
            print(f"  {key:<16} spearman {align[key]['spearman_vs_throughput']}  sign agreement {align[key]['sign_agreement']}")
    print("\nlargest w at which every swap behavior still loses score, by classifier miss rate:", json.dumps(res["swap_proof_w"]))
    print("\nper swap team-shift, does the swap pay (share that pays / mean gain):")
    for key, v in team_gain.items():
        print(f"  {key:<10} {v['share_that_pays']:.3f}  {v['mean_gain']:+.3f}  max {v['max_gain']:+.3f}  (n={v['swap_team_shifts']})")
    print("\nbands:", json.dumps(bands, indent=1))
    print("\nfairness:", json.dumps(fair, indent=1))


if __name__ == "__main__":
    main()
