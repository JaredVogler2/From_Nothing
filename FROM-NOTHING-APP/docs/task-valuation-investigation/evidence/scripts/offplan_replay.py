"""Off-plan credit replay on a simulation database (read-only). Investigation only.

Usage: offplan_replay.py DB OUT

For every team-shift of the run:
* off-plan effort split by the simulation's ground-truth kind: ``pull_ahead`` (idle team pulls ready work forward: additive),
  ``deviation`` (a ready dispatched unit is replaced by unplanned work: substitutive) and ``outside_goals`` (work the latest
  dispatched plan assigned that is not a unit of the day's frozen goals: the floor followed the plan);
* the score under the current canonical formula and under candidate rules;
* production-feasible classifiers of off-plan work against the ground truth;
* the collateral of substitution: other units whose missed-unit claims cite a displaced unit as the blocker;
* the planned-work side of re-planning: frozen units the latest plan moved out of the shift ('replanned' misses).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from statistics import mean, median

WA, WH, RATE = 0.70, 0.30, 0.10            # config/policies/grading-demo-v1.json (demo defaults)
BANDS = (("A", 90), ("B", 80), ("C", 70), ("D", 60))


def grade(s: float | None) -> str | None:
    if s is None:
        return None
    for g, lo in BANDS:
        if s >= lo:
            return g
    return "F"


def clamp(x: float) -> float:
    return min(100.0, max(0.0, x))


path, name = sys.argv[1], sys.argv[2]
db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
cols = ("shift_no", "team", "B0", "B", "E", "Cp", "U", "oos_effort", "available_hours", "actual_hours", "present", "attainment",
        "adherence", "score", "grade", "missed_deviation", "units", "earned")
ts = {(r[0], r[1]): dict(zip(cols, r)) for r in db.execute(f"SELECT {', '.join(cols)} FROM perf_team_shift")}
off: dict[tuple, Counter] = defaultdict(Counter)
off_n: dict[tuple, Counter] = defaultdict(Counter)
for s, t, kind, e, n in db.execute("SELECT shift_no, team, kind, SUM(effort), COUNT(*) FROM exec_offplan GROUP BY 1, 2, 3"):
    off[(s, t)][kind] += e
    off_n[(s, t)][kind] += n
miss: dict[tuple, Counter] = defaultdict(Counter)
for s, t, cause, relief, e in db.execute("SELECT shift_no, team, cause, relief, SUM(effort) FROM perf_unit WHERE status='MISSED' GROUP BY 1, 2, 3, 4"):
    miss[(s, t)][(cause, relief)] += e
out: dict = {"db": path, "team_shifts": len(ts)}

# ---------------------------------------------------------------- consistency: U equals the off-plan rows
bad = [(k, v["U"], sum(off[k].values())) for k, v in ts.items() if v["U"] != sum(off[k].values())]
out["u_mismatch_team_shifts"] = len(bad)

# ---------------------------------------------------------------- volumes
tot = Counter()
for k, v in ts.items():
    tot["B"] += v["B"]
    tot["D"] += v["B"] - v["E"]
    tot["Cp"] += v["Cp"]
    for kind, e in off[k].items():
        tot[kind] += e
out["volume_spm"] = dict(tot)
out["offplan_share_of_output"] = round(sum(tot[x] for x in ("pull_ahead", "deviation", "outside_goals")) /
                                       (tot["Cp"] + sum(tot[x] for x in ("pull_ahead", "deviation", "outside_goals"))), 5)
out["team_shifts_with_offplan"] = {kind: sum(1 for k in ts if off[k][kind] > 0) for kind in ("pull_ahead", "deviation", "outside_goals")}
out["team_shifts_with_any_offplan"] = sum(1 for k in ts if sum(off[k].values()) > 0)
replanned = sum(e for k in ts for (c, r), e in miss[k].items() if c == "replanned")
out["replanned_missed_spm"] = replanned
out["replanned_team_shifts"] = sum(1 for k in ts if any(c == "replanned" for (c, r) in miss[k]))


# ---------------------------------------------------------------- rules
def components(v, k):
    D = v["B"] - v["E"]
    o = off[k]
    return D, v["Cp"], o["pull_ahead"], o["deviation"], o["outside_goals"], RATE * v["oos_effort"]


def r_current(D, Cp, pa, dv, og, O):
    U = pa + dv + og
    A = min(1, Cp / D)
    H = Cp / (Cp + U) if Cp + U > 0 else 0
    return clamp(100 * (WA * A + WH * H) - 100 * O / D)


def r_no_offplan(D, Cp, pa, dv, og, O):          # off-plan ignored in both terms: H is 1 whenever Cp > 0
    A = min(1, Cp / D)
    H = 1 if Cp > 0 else 0
    return clamp(100 * (WA * A + WH * H) - 100 * O / D)


def r_attain_only(D, Cp, pa, dv, og, O):          # adherence weight moved to attainment
    return clamp(100 * min(1, Cp / D) - 100 * O / D)


def make_flat(delta):                               # every off-plan minute counts delta in attainment, nothing in adherence
    def f(D, Cp, pa, dv, og, O):
        U = pa + dv + og
        A = min(1, (Cp + delta * U) / D)
        H = 1 if Cp + U > 0 else 0
        return clamp(100 * (WA * A + WH * H) - 100 * O / D)
    return f


def make_cond(d_add, d_sub, d_replan=1.0):         # ground-truth conditional credit
    def f(D, Cp, pa, dv, og, O):
        A = min(1, (Cp + d_add * pa + d_sub * dv + d_replan * og) / D)
        H = 1 if Cp + pa + dv + og > 0 else 0
        return clamp(100 * (WA * A + WH * H) - 100 * O / D)
    return f


rules = {"R0_current": r_current, "R1_offplan_ignored": r_no_offplan, "R2_attainment_only": r_attain_only}
for d in (0.25, 0.5, 0.75, 1.0):
    rules[f"R3_flat_{d}"] = make_flat(d)
rules["R4_cond_add1_sub0"] = make_cond(1.0, 0.0)
rules["R4_cond_add0.8_sub0"] = make_cond(0.8, 0.0)


def make_r5(w):
    """R5 of offplan_scenarios.py with ground truth: output attainment (planned + additive) at w, plan attainment at 1 - w;
    dispatched-not-frozen work counts as planned; frozen work the latest plan moved out ('replanned') is relieved."""
    def f(D, Cp, pa, dv, og, O, moved=0):
        D5 = D - moved + og
        Cp5 = Cp + og
        if D5 <= 0:
            return None
        return clamp(100 * (w * min(1, (Cp5 + pa) / D5) + (1 - w) * min(1, Cp5 / D5)) - 100 * O / D5)
    return f


R5 = {f"R5_w{w}": make_r5(w) for w in (0.7, 0.8, 0.9)}

scores: dict[str, dict] = {r: {} for r in rules}
for k, v in ts.items():
    D, Cp, pa, dv, og, O = components(v, k)
    if D <= 0:
        continue
    for r, f in rules.items():
        scores[r][k] = f(D, Cp, pa, dv, og, O)
    moved = sum(e for (c, rr), e in miss[k].items() if c == "replanned")
    for r, f in R5.items():
        v5 = f(D, Cp, pa, dv, og, O, moved)
        if v5 is not None:
            scores.setdefault(r, {})[k] = v5
# the stored score must equal the replayed current score (same formula, rounding aside)
diffs = [abs(scores["R0_current"][k] - ts[k]["score"]) for k in scores["R0_current"] if ts[k]["score"] is not None]
out["replay_vs_stored_max_abs_diff"] = round(max(diffs), 4) if diffs else None


def summarize(rule):
    sc = scores[rule]
    g = Counter(grade(s) for s in sc.values())
    by_kind = {}
    for kind in ("pull_ahead", "deviation", "outside_goals", "none"):
        keys = [k for k in sc if (off[k][kind] > 0 if kind != "none" else sum(off[k].values()) == 0)]
        if keys:
            by_kind[kind] = {"team_shifts": len(keys), "mean": round(mean(sc[k] for k in keys), 2),
                             "mean_minus_current": round(mean(sc[k] - scores["R0_current"][k] for k in keys), 2),
                             "min_minus_current": round(min(sc[k] - scores["R0_current"][k] for k in keys), 2),
                             "max_minus_current": round(max(sc[k] - scores["R0_current"][k] for k in keys), 2)}
    changed = sum(1 for k in sc if grade(sc[k]) != grade(scores["R0_current"][k]))
    return {"mean": round(mean(sc.values()), 2), "grades": dict(sorted(g.items())), "grade_changed_vs_current": changed, "by_kind": by_kind}


for r in R5:
    rules[r] = R5[r]
out["rules"] = {r: summarize(r) for r in rules}


def spearman(a, b):
    import numpy as np
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


common_keys = [k for k in scores["R0_current"] if k in scores["R5_w0.7"]]
out["rank_R0_vs_R5_team_shifts"] = round(spearman([scores["R0_current"][k] for k in common_keys], [scores["R5_w0.7"][k] for k in common_keys]), 4)
# whole-run team totals: pooled components (as rollups pool), R0 and R5 at w = 0.7
team = defaultdict(lambda: Counter())
for k, v in ts.items():
    t = team[k[1]]
    t["D"] += v["B"] - v["E"]
    t["Cp"] += v["Cp"]
    t["O"] += RATE * v["oos_effort"]
    for kind, e in off[k].items():
        t[kind] += e
    t["moved"] += sum(e for (c, rr), e in miss[k].items() if c == "replanned")
r0t = {n: r_current(t["D"], t["Cp"], t["pull_ahead"], t["deviation"], t["outside_goals"], t["O"]) for n, t in team.items()}
r5t = {n: R5["R5_w0.7"](t["D"], t["Cp"], t["pull_ahead"], t["deviation"], t["outside_goals"], t["O"], t["moved"]) for n, t in team.items()}
names = sorted(team)
out["teams_whole_run"] = {"teams": len(names), "rank_R0_vs_R5": round(spearman([r0t[n] for n in names], [r5t[n] for n in names]), 4),
                          "R0": {n: round(r0t[n], 2) for n in names}, "R5_w0.7": {n: round(r5t[n], 2) for n in names}}

# ---------------------------------------------------------------- what the current adherence term measures
H_vals = [ts[k]["adherence"] for k in scores["R0_current"] if ts[k]["adherence"] is not None]
out["current_adherence"] = {"team_shifts": len(H_vals), "equal_to_1": sum(1 for h in H_vals if h == 1.0),
                            "below_1": sum(1 for h in H_vals if h < 1.0), "min": min(H_vals) if H_vals else None}
loss = []
for k in scores["R0_current"]:
    if sum(off[k].values()) > 0:
        loss.append(scores["R1_offplan_ignored"][k] - scores["R0_current"][k])
out["current_penalty_points_for_offplan"] = {"team_shifts": len(loss), "mean": round(mean(loss), 3) if loss else None,
                                             "median": round(median(loss), 3) if loss else None, "max": round(max(loss), 3) if loss else None}
# pure additive: teams that did pull-ahead work only (no deviation) and lost points for it
pa_only = [k for k in scores["R0_current"] if off[k]["pull_ahead"] > 0 and off[k]["deviation"] == 0]
out["pull_ahead_only_team_shifts"] = {"n": len(pa_only),
                                      "points_lost_mean": round(mean(scores["R1_offplan_ignored"][k] - scores["R0_current"][k] for k in pa_only), 3) if pa_only else None,
                                      "grade_drops": sum(1 for k in pa_only if grade(scores["R0_current"][k]) != grade(scores["R1_offplan_ignored"][k]))}

# ---------------------------------------------------------------- production-feasible classifiers versus ground truth
# ground truth per off-plan minute: pull_ahead additive, deviation substitutive (outside_goals is identified exactly in
# production: the latest accepted plan dispatched it, so it is not classified here)
conf = {"P1_no_unexcused_shortfall": Counter(), "P2_no_unexplained_dispatched_miss": Counter()}
for k, v in ts.items():
    D, Cp = v["B"] - v["E"], v["Cp"]
    unexplained = sum(e for (c, r), e in miss[k].items() if c == "deviation")   # in production: dispatched, ready, no disruption on record
    p1_additive = Cp >= D
    p2_additive = unexplained == 0
    for kind, truth in (("pull_ahead", "additive"), ("deviation", "substitutive")):
        e = off[k][kind]
        if not e:
            continue
        conf["P1_no_unexcused_shortfall"][(truth, "additive" if p1_additive else "substitutive")] += e
        conf["P2_no_unexplained_dispatched_miss"][(truth, "additive" if p2_additive else "substitutive")] += e
out["classifiers_spm"] = {name_: {f"{a}->{b}": e for (a, b), e in sorted(c.items())} for name_, c in conf.items()}

# why P1 calls pull-ahead substitutive: the unexcused shortfall's causes in those team-shifts
why = Counter()
for k, v in ts.items():
    if off[k]["pull_ahead"] > 0 and v["Cp"] < v["B"] - v["E"]:
        for (c, r), e in miss[k].items():
            if not r:
                why[c] += e
out["p1_false_substitutive_shortfall_causes_spm"] = dict(why.most_common())

# ---------------------------------------------------------------- collateral of substitution
dev_units = db.execute("SELECT shift_no, team, job_id, phase, effort FROM perf_unit WHERE cause='deviation'").fetchall()
dev_jobs = {(s, j): (t, e) for s, t, j, p, e in dev_units}
coll = Counter()
coll_n = Counter()
for s, team, cause, decision, units, effort, evidence in db.execute(
        "SELECT shift_no, team, cause, decision, units, effort, evidence FROM perf_claim WHERE evidence LIKE 'PHASE:%'"):
    job = evidence.split(":", 1)[1].split(".")[0]
    hit = dev_jobs.get((s, job))
    if hit is None:
        continue
    key = f"{cause}:{decision or 'PROPOSED'}:{'other_team' if team != hit[0] else 'same_team'}"
    coll[key] += effort
    coll_n[key] += units
out["substitution"] = {"deviations": len(dev_units), "displaced_planned_spm": sum(e for *_, e in dev_units),
                       "offplan_done_instead_spm": tot["deviation"], "collateral_claims_spm": dict(coll), "collateral_units": dict(coll_n),
                       "collateral_spm_per_displaced_spm": round(sum(coll.values()) / max(1, sum(e for *_, e in dev_units)), 4)}

# ---------------------------------------------------------------- idle capacity of teams that pulled work ahead
idle = []
for k, v in ts.items():
    if off[k]["pull_ahead"] > 0 and v["available_hours"]:
        idle.append(v["available_hours"] - v["actual_hours"])
out["pull_ahead_team_idle_hours_after"] = {"n": len(idle), "mean": round(mean(idle), 2) if idle else None,
                                           "median": round(median(idle), 2) if idle else None}
(open(name, "w")).write(json.dumps(out, indent=1, default=str))
print(json.dumps(out, indent=1, default=str))
