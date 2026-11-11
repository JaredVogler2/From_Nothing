"""AW: how much would a team's attainment change if each job were weighted by its aircraft's criticality (D-29)?

Usage: aircraft_weighted.py SIM_DB OUT.json            (read-only on a stored simulation database)

Criticality of an aircraft in a shift = the modeled cost of one more day late at its projected finish in that shift's
FORECAST plan, as a share of the fleet's highest (``focus/valuation/criticality.py``; here rebuilt from the stored
``plan_aircraft`` and ``calc_aircraft`` rows). A job's points are its standard minutes times a multiplier:

* a1:   1 + 1 x criticality     (a job on the most critical aircraft counts double)
* a3:   1 + 3 x criticality     (four times)
* pure: criticality alone       (a job on an aircraft with no cost pressure counts nothing)

Attainment is earned points over expected points less relieved points, per team-shift, exactly as the minute attainment
A = Cp / (B - E). The output compares each weighting with the minute attainment.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict

db, out_path = sys.argv[1], sys.argv[2]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rate = {(s, line): r for s, line, r in con.execute("select shift_no, line, daily_penalty_usd from calc_aircraft")}
plan = {(s, line): (st, late) for s, line, st, late in
        con.execute("select shift_no, line, status, late_days from plan_aircraft where purpose='FORECAST'")}
states: dict[str, int] = defaultdict(int)
weight: dict[tuple[int, int], float | None] = {}
for s in sorted({s for s, _ in rate}):
    cost: dict[int, float | None] = {}
    for (ss, line), r in rate.items():
        if ss != s:
            continue
        st, late = plan.get((s, line), ("unknown", None))
        if st == "unknown" or late is None:
            cost[line] = None                                   # finish unknown: criticality unknown, never zero
            states["FINISH_UNKNOWN"] += 1
        elif late > 0:
            cost[line] = r                                      # late: a further day costs the daily rate
            states["LATE"] += 1
        else:
            cost[line] = r * max(0.0, min(1.0, late + 1.0))     # on time: only the part of the next day past due
            states["ON_TIME"] += 1
    peak = max((c for c in cost.values() if c), default=0.0)
    for line, c in cost.items():
        weight[(s, line)] = None if c is None else (c / peak if peak else 0.0)

MULT = {"minutes": lambda w: 1.0, "a1": lambda w: 1.0 + w, "a3": lambda w: 1.0 + 3.0 * w, "pure": lambda w: w}
agg: dict[tuple[int, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
inside: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(list)
units = 0
for s, team, line, effort, status, relief in con.execute("select shift_no, team, line, effort, status, relief from perf_unit"):
    units += 1
    w = weight.get((s, line))
    if w is not None:
        inside[(s, team)].append((w, effort))
    for name, m in MULT.items():
        points = effort * m(w or 0.0)                           # an unknown criticality adds nothing to a job's minutes
        a = agg[(s, team)]
        a[f"B_{name}"] += points
        if status == "EARNED":
            a[f"Cp_{name}"] += points
        elif relief:
            a[f"E_{name}"] += points


def attainment(a: dict[str, float], name: str) -> float | None:
    d = a[f"B_{name}"] - a[f"E_{name}"]
    return None if d <= 0 else round(100.0 * min(1.0, a[f"Cp_{name}"] / d), 6)      # rounded: equal results must tie in the ranks


def spearman(x: list[float], y: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r, i = [0.0] * len(v), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry, n = rank(x), rank(y), len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / den if den else float("nan")


rows = [{name: attainment(a, name) for name in MULT} for a in agg.values()]
rows = [r for r in rows if all(v is not None for v in r.values())]
short = [r for r in rows if r["minutes"] < 99.999]


def compare(sample: list[dict[str, float]], name: str) -> dict[str, float]:
    diff = sorted(abs(r[name] - r["minutes"]) for r in sample)
    return {"mean_abs_points": round(sum(diff) / len(diff), 3), "p95_abs_points": round(diff[int(0.95 * len(diff))], 3),
            "max_abs_points": round(diff[-1], 2), "share_moved_1_point": round(sum(1 for d in diff if d >= 1) / len(diff), 4),
            "share_moved_5_points": round(sum(1 for d in diff if d >= 5) / len(diff), 4),
            "spearman_with_minutes": round(spearman([r["minutes"] for r in sample], [r[name] for r in sample]), 4)}


spread = []
for items in inside.values():
    total = sum(e for _, e in items)
    mean = sum(w * e for w, e in items) / total
    spread.append((sum(e * (w - mean) ** 2 for w, e in items) / total) ** 0.5)
spread.sort()
result = {"database": db.rsplit("/", 1)[-1], "units": units, "team_shifts": len(rows), "aircraft_shift_states": dict(states),
          "team_shifts_at_100_in_minutes": len(rows) - len(short),
          "all_team_shifts": {name: compare(rows, name) for name in MULT if name != "minutes"},
          "team_shifts_below_100": {"count": len(short), **{name: compare(short, name) for name in MULT if name != "minutes"}},
          "criticality_spread_inside_a_team_shift": {"median_sd": round(spread[len(spread) // 2], 3), "p90_sd": round(spread[int(0.9 * len(spread))], 3)}}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=1)
print(json.dumps(result, indent=1))
