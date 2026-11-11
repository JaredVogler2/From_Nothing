"""T8: how much a pooled score moves between two equal periods when nothing changed, by level (team, a five-team
superintendent group, the site) and by period length. 50 aircraft, 42 shifts, every team equal by construction.

Usage: perf_bands.py SIM_DB OUT.json
"""
import json, sys
from collections import defaultdict
from perf_measures import OOS_RATE, W, load

recs = [r for r in load(sys.argv[1], with_weights=False) if r["team"] != "QA"]
teams = sorted({r["team"] for r in recs})
shifts = sorted({r["shift"] for r in recs})
group = {t: f"S{i // 5 + 1}" for i, t in enumerate(teams)}
by = defaultdict(list)
for r in recs:
    by[("team", r["team"])].append(r); by[("superintendent", group[r["team"]])].append(r); by[("site", "site")].append(r)


def measures(rows, window):
    t = defaultdict(float)
    for r in rows:
        if r["shift"] in window:
            for k in ("B", "E", "Cp", "U", "oos", "available_h", "actual_h"):
                t[k] += r[k] or 0.0
    D = t["B"] - t["E"]
    if D <= 0:
        return None
    return {"score": 100 * (W * min(1, (t["Cp"] + t["U"]) / D) + (1 - W) * min(1, t["Cp"] / D)) - 100 * OOS_RATE * t["oos"] / D,
            "plan_delivered": 100 * t["Cp"] / t["B"], "productivity": 100 * (t["Cp"] + t["U"]) / 60 / t["available_h"],
            "efficiency": 100 * (t["Cp"] + t["U"]) / 60 / t["actual_h"], "load": 100 * t["B"] / 60 / t["available_h"]}


res = {}
print(f"{'level':15s} {'period':>9s} | 90% of changes with nothing changed are within (points)")
for level in ("team", "superintendent", "site"):
    for wlen, name in ((1, "shift"), (3, "day"), (9, "3 days"), (15, "5 days"), (21, "7 days")):
        ch = defaultdict(list)
        for (lv, key), rows in by.items():
            if lv != level:
                continue
            for start in range(0, len(shifts) - 2 * wlen + 1, max(1, wlen // 3) if wlen >= 3 else 1):
                a, b = measures(rows, set(shifts[start:start + wlen])), measures(rows, set(shifts[start + wlen:start + 2 * wlen]))
                if a and b:
                    for m in a:
                        ch[m].append(abs(b[m] - a[m]))
        band = {m: round(sorted(v)[int(0.9 * (len(v) - 1))], 2) for m, v in ch.items()}
        res[f"{level}|{name}"] = band
        print(f"{level:15s} {name:>9s} | " + "  ".join(f"{m}=±{band[m]:.2f}" for m in ("score", "plan_delivered", "productivity", "efficiency", "load")))
json.dump(res, open(sys.argv[2], "w"), indent=1)
