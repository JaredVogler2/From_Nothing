"""Compare paired floor scenarios (offplan_paired.py): production and scoring effects of off-plan work. Investigation only.

Usage: offplan_paired_analyze.py OUT NAME [NAME ...]    (reads out/NAME.db and out/NAME.summary.json; NAME like N0_s6)

Per run: planned goal effort, verified completions, off-plan by kind, open work left at the last cutoff, jobs completed,
misses by cause, approved relief, cross-team collateral and team scores (current formula and rule R5 of
offplan_scenarios.py, with ground-truth and production-feasible classification). Pairs: PA_sX and DV_sX against N0_sX.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from statistics import mean

from common import SCRATCH

WA, WH, RATE = 0.70, 0.30, 0.10
BANDS = (("A", 90), ("B", 80), ("C", 70), ("D", 60))


def grade(s):
    for g, lo in BANDS:
        if s >= lo:
            return g
    return "F"


def clamp(x):
    return min(100.0, max(0.0, x))


def run_metrics(name: str) -> dict:
    db = sqlite3.connect(f"file:{SCRATCH / 'out' / (name + '.db')}?mode=ro", uri=True)
    summ = json.loads((SCRATCH / "out" / f"{name}.summary.json").read_text())
    shifts = [r[0] for r in db.execute("SELECT DISTINCT shift_no FROM perf_team_shift ORDER BY 1")]
    last_calc = db.execute("SELECT MAX(shift_no) FROM calc_job").fetchone()[0]
    m: dict = {"name": name, "shifts": len(shifts), "deviation": summ["deviation"], "pull_ahead": summ["pull_ahead"], "seed": summ["seed"],
               "seconds": summ["seconds"]}
    tot = db.execute("SELECT SUM(B0), SUM(B), SUM(E), SUM(Cp), SUM(U), SUM(oos_effort) FROM perf_team_shift").fetchone()
    m.update(B0=tot[0], B=tot[1], E=tot[2], D=tot[1] - tot[2], Cp=tot[3], U=tot[4], oos=tot[5])
    m["offplan_by_kind"] = {k: e for k, e in db.execute("SELECT kind, SUM(effort) FROM exec_offplan GROUP BY kind")}
    m["throughput_spm"] = m["Cp"] + m["U"]
    m["open_work_spm_at_last_cutoff"] = db.execute("SELECT SUM(work_minutes*crew) FROM calc_job WHERE shift_no=?", (last_calc,)).fetchone()[0]
    m["open_work_spm_at_first_cutoff"] = db.execute("SELECT SUM(work_minutes*crew) FROM calc_job WHERE shift_no=0").fetchone()[0]
    m["open_jobs_at_last_cutoff"] = db.execute("SELECT COUNT(*) FROM calc_job WHERE shift_no=?", (last_calc,)).fetchone()[0]
    m["last_calc_shift"] = last_calc
    m["jobs_completed"] = sum(s.get("jobs_completed", 0) for s in summ["per_shift"])
    m["jobs_completed_before_last_cutoff"] = sum(s.get("jobs_completed", 0) for s in summ["per_shift"] if s["shift"] < last_calc)
    m["throughput_before_last_cutoff"] = db.execute("SELECT SUM(Cp)+SUM(U) FROM perf_team_shift WHERE shift_no<?", (last_calc,)).fetchone()[0]
    m["missed_by_cause_spm"] = {c or "none": e for c, e in db.execute("SELECT cause, SUM(effort) FROM perf_unit WHERE status='MISSED' GROUP BY cause")}
    m["claims_spm"] = {f"{c}:{d or 'PROPOSED'}": e for c, d, e in db.execute("SELECT cause, decision, SUM(effort) FROM perf_claim GROUP BY 1, 2")}
    m["deviations"] = db.execute("SELECT COUNT(*), COALESCE(SUM(effort),0) FROM perf_unit WHERE cause='deviation'").fetchone()
    m["exec_slipped_by_cause"] = {c: n for c, n in db.execute("SELECT cause, COUNT(*) FROM exec_assignment WHERE outcome='slipped' GROUP BY cause")}
    # aircraft progress: open work per line at the last cutoff
    m["open_work_by_line"] = {line: w for line, w in db.execute("SELECT line, SUM(work_minutes*crew) FROM calc_job WHERE shift_no=? GROUP BY line", (last_calc,))}
    # scores
    off = defaultdict(Counter)
    for s, t, kind, e in db.execute("SELECT shift_no, team, kind, SUM(effort) FROM exec_offplan GROUP BY 1, 2, 3"):
        off[(s, t)][kind] += e
    dev = {(s, t): e for s, t, e in db.execute("SELECT shift_no, team, SUM(effort) FROM perf_unit WHERE cause='deviation' GROUP BY 1, 2")}
    # dispatch records: a dispatched assignment that was ready, had no disruption on record and was not worked (production can see
    # this for every assignment of the latest accepted plan, frozen goal unit or not)
    dev_dispatch = {(s, t) for s, t in db.execute("SELECT DISTINCT shift_no, team FROM exec_assignment WHERE cause='deviation'")}
    m["utilization_mean"] = round(db.execute("SELECT AVG(utilization) FROM perf_team_shift").fetchone()[0], 4)
    m["worked_hours"] = round(db.execute("SELECT SUM(actual_hours) FROM perf_team_shift").fetchone()[0], 1)
    m["available_hours"] = round(db.execute("SELECT SUM(available_hours) FROM perf_team_shift").fetchone()[0], 1)
    conf = Counter()
    for (s, t), o in off.items():
        for kind, truth in (("pull_ahead", "additive"), ("deviation", "substitutive")):
            if o[kind]:
                conf[("P2_goal_units", truth, "substitutive" if dev.get((s, t)) else "additive")] += o[kind]
                conf[("P2_dispatch", truth, "substitutive" if (s, t) in dev_dispatch else "additive")] += o[kind]
    m["classifiers_spm"] = {f"{r}:{a}->{b}": e for (r, a, b), e in sorted(conf.items())}
    moved = {(s, t): e for s, t, e in db.execute("SELECT shift_no, team, SUM(effort) FROM perf_unit WHERE cause='replanned' GROUP BY 1, 2")}
    r0, r5, r5p, r5d, gaps = [], [], [], [], []
    for s, t, B, E, Cp, U, oos in db.execute("SELECT shift_no, team, B, E, Cp, U, oos_effort FROM perf_team_shift"):
        D = B - E
        if D <= 0:
            continue
        o = off[(s, t)]
        O = RATE * oos
        A = min(1, Cp / D)
        H = Cp / (Cp + U) if Cp + U else 0
        s0 = clamp(100 * (WA * A + WH * H) - 100 * O / D)
        # R5, ground truth: pull_ahead additive, deviation substitutive, outside_goals planned (dispatched); moved-out frozen relieved
        D5 = D - moved.get((s, t), 0) + o["outside_goals"]
        Cp5 = Cp + o["outside_goals"]
        if D5 <= 0:
            continue
        s5 = clamp(100 * (WA * min(1, (Cp5 + o["pull_ahead"]) / D5) + WH * min(1, Cp5 / D5)) - 100 * O / D5)
        # R5 with the production-feasible classifier P2: off-plan is substitutive when the team-shift has an unexplained dispatched miss
        add_p = 0 if dev.get((s, t)) else o["pull_ahead"] + o["deviation"]
        s5p = clamp(100 * (WA * min(1, (Cp5 + add_p) / D5) + WH * min(1, Cp5 / D5)) - 100 * O / D5)
        add_d = 0 if (s, t) in dev_dispatch else o["pull_ahead"] + o["deviation"]
        s5d = clamp(100 * (WA * min(1, (Cp5 + add_d) / D5) + WH * min(1, Cp5 / D5)) - 100 * O / D5)
        r0.append(s0)
        r5.append(s5)
        r5p.append(s5p)
        r5d.append(s5d)
        gaps.append(s5 - s0)
    m["scores"] = {"team_shifts": len(r0), "R0_mean": round(mean(r0), 2), "R5_mean": round(mean(r5), 2), "R5_P2_mean": round(mean(r5p), 2),
                   "R0_grades": dict(Counter(grade(x) for x in r0)), "R5_grades": dict(Counter(grade(x) for x in r5)),
                   "R5_minus_R0_mean": round(mean(gaps), 2), "R5_vs_R5P2_max_abs_diff": round(max(abs(a - b) for a, b in zip(r5, r5p)), 3),
                   "R5_vs_R5P2dispatch_max_abs_diff": round(max(abs(a - b) for a, b in zip(r5, r5d)), 3), "R5_P2dispatch_mean": round(mean(r5d), 2)}
    return m


out_name, names = sys.argv[1], sys.argv[2:]
runs = {n: run_metrics(n) for n in names}
pairs = {}
for n, m in runs.items():
    if n.startswith("N0"):
        continue
    base = runs.get("N0" + n[2:])
    if base is None:
        continue
    d = {k: (m[k] - base[k]) for k in ("B0", "D", "Cp", "U", "worked_hours", "throughput_spm", "throughput_before_last_cutoff", "open_work_spm_at_last_cutoff",
                                       "open_jobs_at_last_cutoff", "jobs_completed", "jobs_completed_before_last_cutoff")}
    d["offplan_minus_base_offplan"] = m["U"] - base["U"]
    cross = lambda r: sum(v for k, v in r["claims_spm"].items() if k.startswith("CROSS_TEAM_PREDECESSOR:APPROVED"))
    d["cross_team_relief_spm"] = cross(m) - cross(base)
    d["deviations"] = m["deviations"][0] - base["deviations"][0]
    d["displaced_spm"] = m["deviations"][1] - base["deviations"][1]
    # value per off-plan minute: how much less open work there is at the last cutoff per off-plan minute done
    if n.startswith("PA") and d["offplan_minus_base_offplan"]:
        d["open_work_reduction_per_offplan_minute"] = round(-d["open_work_spm_at_last_cutoff"] / d["offplan_minus_base_offplan"], 3)
        d["throughput_gain_per_offplan_minute"] = round(d["throughput_spm"] / d["offplan_minus_base_offplan"], 3)
    if n.startswith("DV") and d["displaced_spm"]:
        d["open_work_change_per_displaced_minute"] = round(d["open_work_spm_at_last_cutoff"] / d["displaced_spm"], 3)
        d["cross_team_relief_per_displaced_minute"] = round(d["cross_team_relief_spm"] / d["displaced_spm"], 3)
        d["throughput_change_per_displaced_minute"] = round(d["throughput_spm"] / d["displaced_spm"], 3)
    pairs[f"{n} - N0{n[2:]}"] = d
res = {"runs": runs, "pairs": pairs}
(SCRATCH / "out" / f"{out_name}.json").write_text(json.dumps(res, indent=1, default=str))
for n, m in runs.items():
    print(n, json.dumps({k: m[k] for k in ("B0", "D", "Cp", "U", "offplan_by_kind", "throughput_spm", "open_work_spm_at_last_cutoff", "jobs_completed",
                                             "deviations", "missed_by_cause_spm", "utilization_mean", "worked_hours", "classifiers_spm", "scores")}, default=str))
print()
for k, d in pairs.items():
    print(k, json.dumps(d))
