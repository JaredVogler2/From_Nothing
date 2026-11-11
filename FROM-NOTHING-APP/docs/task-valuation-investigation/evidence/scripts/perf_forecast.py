"""T2: what moves the delivery forecast from one shift to the next (50 aircraft, 42 shifts)? Fleet totals per shift:
planned minutes missed without excuse, missed with an approved excuse (by cause), extra minutes; against the change in the
fleet's forecast late days and modeled cost between this shift's forecast and the next.

Usage: perf_forecast.py SIM_DB OUT.json            (a run with FORECAST plans)
"""
import json, sqlite3, sys
from collections import defaultdict
from perf_measures import pearson, spearman

con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
fc = defaultdict(lambda: [0.0, 0.0, 0, 0])
for s, status, late, cost in con.execute("select shift_no, status, late_days, cost_usd from plan_aircraft where purpose='FORECAST'"):
    f = fc[s]
    if status == "unknown" or late is None:
        f[3] += 1
    else:
        f[0] += max(0.0, late); f[1] += cost or 0.0; f[2] += 1
shift = defaultdict(lambda: defaultdict(float))
for s, status, relief, cause, effort in con.execute("select shift_no, status, relief, coalesce(claim_cause, cause, 'NONE'), effort from perf_unit"):
    d = shift[s]
    d["planned"] += effort
    if status == "EARNED":
        d["done"] += effort
    elif relief:
        d["excused"] += effort; d["excused:" + cause] += effort
    else:
        d["unexcused"] += effort
for s, u in con.execute("select shift_no, sum(U) from perf_team_shift group by 1"):
    shift[s]["extra"] = u or 0.0
ks = sorted(k for k in fc if k + 1 in fc)
rows = []
for k in ks:
    a, b = fc[k], fc[k + 1]
    if a[3] != b[3]:
        continue                                   # a different set of priced aircraft: the totals are not comparable
    rows.append({"shift": k, "d_late_days": b[0] - a[0], "d_cost": b[1] - a[1], **{x: shift[k][x] for x in ("planned", "done", "excused", "unexcused", "extra")},
                 "missed": shift[k]["excused"] + shift[k]["unexcused"], "net_missed": shift[k]["excused"] + shift[k]["unexcused"] - shift[k]["extra"],
                 **{c: shift[k][c] for c in shift[k] if c.startswith("excused:")}})
print(len(rows), "shift-to-shift comparisons with the same priced aircraft; unknown aircraft per shift:", sorted({fc[k][3] for k in fc}))
print("fleet per shift: planned", round(sum(r['planned'] for r in rows) / len(rows)), "missed excused", round(sum(r['excused'] for r in rows) / len(rows)),
      "missed unexcused", round(sum(r['unexcused'] for r in rows) / len(rows)), "extra", round(sum(r['extra'] for r in rows) / len(rows)))
print("change in forecast late days per shift: mean", round(sum(r['d_late_days'] for r in rows) / len(rows), 3), "min", round(min(r['d_late_days'] for r in rows), 2),
      "max", round(max(r['d_late_days'] for r in rows), 2))
res = {}
for x in ("missed", "excused", "unexcused", "extra", "net_missed", "done"):
    res[x] = {"pearson_late_days": pearson([r[x] for r in rows], [r["d_late_days"] for r in rows]), "spearman_late_days": spearman([r[x] for r in rows], [r["d_late_days"] for r in rows]),
              "pearson_cost": pearson([r[x] for r in rows], [r["d_cost"] for r in rows])}
    print(f"{x:12s} r with change in late days {res[x]['pearson_late_days']:+.3f} (rank {res[x]['spearman_late_days']:+.3f}); with change in cost {res[x]['pearson_cost']:+.3f}")
causes = sorted({c for r in rows for c in r if c.startswith("excused:")})
for c in causes:
    v = [r.get(c, 0.0) for r in rows]
    print(f"  {c:38s} mean {sum(v) / len(v):7.0f} min/shift; r with change in late days {pearson(v, [r['d_late_days'] for r in rows]) or 0:+.3f}")
json.dump({"rows": rows, "correlations": res}, open(sys.argv[2], "w"), indent=1)
