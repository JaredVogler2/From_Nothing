"""T8 (do priority classes predict delivery movement?), T9 (fairness), class stability and mapping, from a shadow run.

Usage: shadow_analyze.py OUT   (reads out/OUT.classes.jsonl and out/OUT.db)
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from collections import Counter, defaultdict

import numpy as np

from common import SCRATCH

name = sys.argv[1]
recs = [json.loads(line) for line in (SCRATCH / "out" / f"{name}.classes.jsonl").read_text().splitlines() if line.strip()]
cls = {r["k"]: {u["phase"]: u["class"] for u in r["units"]} for r in recs}
comp = {r["k"]: {int(k): v for k, v in r["completion"].items()} for r in recs}
db = sqlite3.connect(SCRATCH / "out" / f"{name}.db")
units = db.execute("SELECT shift_no, team, phase, line, effort, status, relief FROM perf_unit").fetchall()
out: dict = {"shifts_classified": len(recs), "classify_seconds_per_shift": [r["seconds"] for r in recs],
             "class_counts_per_shift": [dict(Counter(u["class"] for u in r["units"])) for r in recs]}

# ---------------------------------------------------------------- mapping of executed goal units to their publication's class
mapped = Counter()
for k, team, phase, line, effort, status, relief in units:
    mapped[cls.get(k, {}).get(phase, "UNMAPPED")] += effort
total = sum(mapped.values())
out["goal_effort_by_class"] = {c: round(v / total, 4) for c, v in mapped.items()} if total else {}

# ---------------------------------------------------------------- T8: missed effort by class versus the next plan's completion change
rows = []
lines = sorted({line for c in comp.values() for line in c})
for k in sorted(comp):
    if k + 1 not in comp:
        continue
    missed = defaultdict(lambda: defaultdict(int))
    for kk, team, phase, line, effort, status, relief in units:
        if kk == k and status == "MISSED":
            missed[line][cls.get(k, {}).get(phase, "UNMAPPED")] += effort
    for line in lines:
        c0, c1 = comp[k].get(line), comp[k + 1].get(line)
        if c0 is None or c1 is None:
            continue
        m = missed[line]
        rows.append({"k": k, "line": line, "dC_min": c1 - c0, "crit": m["CRITICAL"], "atrisk": m["AT_RISK"], "float": m["FLOAT"],
                     "unknown": m["UNKNOWN"], "unmapped": m["UNMAPPED"]})
out["t8_aircraft_shifts"] = len(rows)


def mean(xs):
    return float(np.mean(xs)) if xs else None


for key in ("crit", "atrisk", "float"):
    with_ = [r["dC_min"] for r in rows if r[key] > 0]
    without = [r["dC_min"] for r in rows if r[key] == 0]
    diffs = []
    rng = random.Random(7)
    if with_ and without:
        for _ in range(2000):
            a = [rng.choice(with_) for _ in with_]
            b = [rng.choice(without) for _ in without]
            diffs.append(np.mean(a) - np.mean(b))
    out[f"t8_{key}"] = {"n_with": len(with_), "n_without": len(without), "mean_dC_with_min": mean(with_), "mean_dC_without_min": mean(without),
                        "diff_ci95_min": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))] if diffs else None}
if rows:
    X = np.array([[1.0, r["crit"] / 60, r["atrisk"] / 60, r["float"] / 60, (r["unknown"] + r["unmapped"]) / 60] for r in rows])
    y = np.array([r["dC_min"] for r in rows], dtype=float)
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    boots = []
    rng = np.random.default_rng(7)
    for _ in range(2000):
        idx = rng.integers(0, len(rows), len(rows))
        boots.append(np.linalg.lstsq(X[idx], y[idx], rcond=None)[0])
    boots = np.array(boots)
    out["t8_ols_minutes_per_missed_hour"] = {name_: {"coef": round(float(coef[i]), 2), "ci95": [round(float(np.percentile(boots[:, i], 2.5)), 2),
                                                                                            round(float(np.percentile(boots[:, i], 97.5)), 2)]}
                                             for i, name_ in enumerate(["intercept", "critical", "at_risk", "float", "unknown_or_unmapped"])}

# ---------------------------------------------------------------- T9: fairness of an importance-weighted earning versus effort attainment
W = {"CRITICAL": 3, "AT_RISK": 2, "FLOAT": 1, "UNKNOWN": 1, "UNMAPPED": 1}
ts = defaultdict(lambda: {"B": 0, "E": 0, "Cp": 0, "Bw": 0, "Ew": 0, "Cpw": 0, "prio": 0, "prio_n": 0, "prio_done": 0, "prio_rel": 0})
for k, team, phase, line, effort, status, relief in units:
    c = cls.get(k, {}).get(phase, "UNMAPPED")
    w = W[c]
    t = ts[(k, team)]
    t["B"] += effort
    t["Bw"] += w * effort
    if status == "MISSED" and relief:
        t["E"] += effort
        t["Ew"] += w * effort
    if status == "EARNED":
        t["Cp"] += effort
        t["Cpw"] += w * effort
    if c in ("CRITICAL", "AT_RISK"):
        t["prio"] += effort
        t["prio_n"] += 1
        t["prio_done"] += status == "EARNED"
        t["prio_rel"] += bool(status == "MISSED" and relief)
A, Aw, share, cov, A_cov = [], [], [], [], []
for t in ts.values():
    d, dw = t["B"] - t["E"], t["Bw"] - t["Ew"]
    if d <= 0 or dw <= 0:
        continue
    a, aw = min(1, t["Cp"] / d), min(1, t["Cpw"] / dw)
    A.append(a)
    Aw.append(aw)
    share.append(t["prio"] / t["B"])
    den = t["prio_n"] - t["prio_rel"]
    if den > 0:
        cov.append(t["prio_done"] / den)
        A_cov.append(a)


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def pearson(x, y):
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


diff = [b - a for a, b in zip(A, Aw)]
out["t9"] = {"team_shifts": len(A), "spearman_A_vs_weighted": spearman(A, Aw),
             "share_with_abs_diff_gt_0_05": round(sum(1 for x in diff if abs(x) > 0.05) / max(1, len(diff)), 4),
             "corr_priority_share_vs_weighted_minus_A": pearson(share, diff),
             "team_shifts_with_priority_units": len(cov), "team_shifts_coverage_na": len(A) - len(cov),
             "corr_coverage_vs_attainment": pearson(cov, A_cov),
             "priority_share_mean": mean(share), "priority_share_max": max(share) if share else None}

# ---------------------------------------------------------------- class stability for phases classified in consecutive publications
same = total_pairs = 0
trans = Counter()
for k in sorted(cls):
    if k + 1 not in cls:
        continue
    for ph, c in cls[k].items():
        c2 = cls[k + 1].get(ph)
        if c2 is not None:
            total_pairs += 1
            same += c == c2
            trans[f"{c}->{c2}"] += 1
out["stability_consecutive"] = {"pairs": total_pairs, "same_share": round(same / max(1, total_pairs), 4), "transitions": dict(trans.most_common(8))}
(SCRATCH / "out" / f"{name}.analysis.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
