"""T7: one productivity tree. Earned standard hours per available labor hour splits exactly into factors with different
owners. Site level, by week, on the 50-aircraft, 42-shift floor (production teams; QA apart).

Usage: perf_tree.py SIM_DB OUT.json
"""
import json, sys
from collections import defaultdict
from perf_measures import load

recs = [r for r in load(sys.argv[1], with_weights=False) if r["team"] != "QA"]


def tree(rows):
    t = defaultdict(float)
    for r in rows:
        for k in ("B", "E", "Cp", "U", "available_h", "actual_h"):
            t[k] += r[k] or 0.0
    A, B, E, Cp, U, H = t["available_h"] * 60, t["B"], t["E"], t["Cp"], t["U"], t["actual_h"] * 60
    out = {"available_hours": round(A / 60), "productivity": (Cp + U) / A,                 # earned standard minutes per available minute
           "load": B / A,                                                                    # how much of the available time the plan asked for
           "not_excused_away": (B - E) / B,                                                  # share of the plan left after approved excuses
           "team_execution": Cp / (B - E),                                                   # what the teams did of what was left to them
           "extra_work": (Cp + U) / Cp,                                                      # work beyond the plan
           "time_on_work": H / A, "efficiency": (Cp + U) / H}                                # the same productivity as busy time x pace
    out["check_plan_side"] = out["load"] * out["not_excused_away"] * out["team_execution"] * out["extra_work"]
    out["check_time_side"] = out["time_on_work"] * out["efficiency"]
    return out


weeks = sorted({r["week"] for r in recs})
res = {"all": tree(recs), **{f"week_{w}": tree([r for r in recs if r["week"] == w]) for w in weeks}}
keys = ("productivity", "load", "not_excused_away", "team_execution", "extra_work", "time_on_work", "efficiency")
print(f"{'period':8s} " + " ".join(f"{k[:16]:>16s}" for k in keys))
for name, v in res.items():
    print(f"{name:8s} " + " ".join(f"{v[k]:16.4f}" for k in keys) + f"   checks {v['check_plan_side']:.4f} {v['check_time_side']:.4f}")
if len(weeks) >= 2:
    a, b = res[f"week_{weeks[0]}"], res[f"week_{weeks[1]}"]
    print("\nweek-over-week change, as factors (a product of the plan-side factors equals the productivity change):")
    print("  productivity x", round(b["productivity"] / a["productivity"], 4), "= load x", round(b["load"] / a["load"], 4), "* not excused x",
          round(b["not_excused_away"] / a["not_excused_away"], 4), "* team execution x", round(b["team_execution"] / a["team_execution"], 4),
          "* extra work x", round(b["extra_work"] / a["extra_work"], 4))
# by shift number: the same tree
for n in (1, 2, 3):
    v = tree([r for r in recs if r["number"] == n])
    print(f"shift {n}: " + " ".join(f"{k[:12]}={v[k]:.3f}" for k in keys))
json.dump(res, open(sys.argv[2], "w"), indent=1)
