"""T6: the known-truth floors (known_truth.py). Teams were made slow, fast, prone to swapping planned work, always or never
working ahead when idle, or left as the baseline. Which measure, pooled over the run, finds each group?

Usage: perf_known_truth.py RUN_DIR OUT.json KT_s6 KT_s7   (the runs written by known_truth.py)
"""
import json, sys
from collections import defaultdict
from perf_measures import OOS_RATE, W, load

OUT, RESULT, runs = sys.argv[1], sys.argv[2], sys.argv[3:]
M = ("score", "att_excused", "att_raw", "ppc_count", "output_index", "throughput", "per_available_hour", "efficiency")
GROUPS = ("slow", "fast", "swaps", "works_ahead", "never_ahead", "baseline")
EXPECT = {"slow": -1, "fast": +1, "swaps": -1, "works_ahead": +1, "never_ahead": -1}        # the direction a fair measure should move


def team_measures(path, first=None, last=None):
    recs = [r for r in load(path, with_weights=False) if r["team"] != "QA" and (first is None or first <= r["shift"] <= last)]
    t = defaultdict(lambda: defaultdict(float))
    for r in recs:
        for k in ("B", "E", "Cp", "U", "oos", "units", "earned", "relieved", "available_h", "actual_h", "planned_h", "db_units", "db_minutes"):
            t[r["team"]][k] += r[k] or 0.0
    out = {}
    for team, v in t.items():
        D = v["B"] - v["E"]
        out[team] = {"score": max(0.0, 100 * (W * min(1, (v["Cp"] + v["U"]) / D) + (1 - W) * min(1, v["Cp"] / D)) - 100 * OOS_RATE * v["oos"] / D),
                     "att_excused": 100 * min(1, v["Cp"] / D), "att_raw": 100 * v["Cp"] / (v["B"] - v["db_minutes"]),
                     "ppc_count": 100 * v["earned"] / (v["units"] - v["relieved"] - v["db_units"]),
                     "output_index": 100 * (v["Cp"] + v["U"]) / D, "throughput": v["Cp"] + v["U"],
                     "per_available_hour": (v["Cp"] + v["U"]) / 60 / v["available_h"], "efficiency": (v["Cp"] + v["U"]) / 60 / v["actual_h"],
                     "load": v["planned_h"] / v["available_h"], "extra_share": 100 * v["U"] / D, "size": v["available_h"]}
    return out


def auc(a, b):
    n = wins = 0.0
    for x in a:
        for y in b:
            n += 1; wins += 1.0 if x > y else 0.5 if x == y else 0.0
    return wins / n


res = {"runs": {}, "auc_vs_baseline": {}}
pooled = defaultdict(lambda: defaultdict(list))
for name in runs:
    truth = json.load(open(f"{OUT}/{name}.truth.json"))
    tm = team_measures(f"{OUT}/{name}.db")
    res["runs"][name] = {"truth": {k: truth[k] for k in ("seed", "shifts", "slow", "fast", "swap")}, "groups": {}}
    for g in GROUPS:
        members = [t for t, gg in truth["group"].items() if gg == g and t in tm]
        res["runs"][name]["groups"][g] = {m: round(sum(tm[t][m] for t in members) / len(members), 3) for m in M + ("load", "extra_share")}
        for t in members:
            for m in M + ("load", "extra_share"):
                pooled[g][m].append(tm[t][m])
print(f"{'group':12s} " + " ".join(f"{m[:9]:>9s}" for m in M + ("load", "extra_share")))
for g in GROUPS:
    print(f"{g:12s} " + " ".join(f"{sum(pooled[g][m]) / len(pooled[g][m]):9.3f}" for m in M + ("load", "extra_share")) + f"   (n={len(pooled[g]['score'])})")
print("\nChance a team of the group scores ABOVE a baseline team (0.5 = the measure cannot tell; expected side in brackets)")
print(f"{'group':18s} " + " ".join(f"{m[:9]:>9s}" for m in M))
for g in GROUPS[:-1]:
    row = {m: auc(pooled[g][m], pooled["baseline"][m]) for m in M}
    res["auc_vs_baseline"][g] = {m: round(v, 3) for m, v in row.items()}
    print(f"{g + (' [+]' if EXPECT[g] > 0 else ' [-]'):18s} " + " ".join(f"{row[m]:9.3f}" for m in M))
json.dump(res, open(RESULT, "w"), indent=1)
