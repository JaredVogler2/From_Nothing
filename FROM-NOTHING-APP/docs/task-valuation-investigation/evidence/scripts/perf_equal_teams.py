"""T1: the equal-teams test. Every team in the simulation behaves the same by construction (one FloorSpec for the floor).
A measure that still ranks teams the same way in two halves of the period is ranking something else: size, load or work mix.

Usage: perf_equal_teams.py SIM_DB OUT.json
"""
import json, sys
from collections import defaultdict
from perf_measures import LABEL, MEASURES, load, mean, pearson, spearman

path, out = sys.argv[1], sys.argv[2]
recs = [r for r in load(path) if r["team"] != "QA"]                       # QA is a support team with a different job; reported apart
teams = sorted({r["team"] for r in recs})
size = {t: mean([r["present"] for r in recs if r["team"] == t]) for t in teams}
loadf = {t: mean([r["load"] for r in recs if r["team"] == t]) for t in teams}
res = {"database": path.rsplit("/", 1)[-1], "teams": len(teams), "team_shifts": len(recs), "shifts": len({r["shift"] for r in recs}), "measures": {}}
for m in MEASURES:
    by = defaultdict(lambda: ([], []))
    allv = []
    for r in recs:
        if r[m] is None:
            continue
        by[r["team"]][r["shift"] % 2].append(r[m])
        allv.append((r["team"], r[m]))
    odd = [mean(by[t][1]) for t in teams]
    even = [mean(by[t][0]) for t in teams]
    half = pearson(odd, even)
    full = None if half is None else 2 * half / (1 + half) if half > -1 else None      # Spearman-Brown: reliability of the whole period
    tm = {t: mean(by[t][0] + by[t][1]) for t in teams}
    overall = mean([v for _, v in allv])
    total = sum((v - overall) ** 2 for _, v in allv)
    between = sum((len(by[t][0]) + len(by[t][1])) * (tm[t] - overall) ** 2 for t in teams if tm[t] is not None)
    vals = [tm[t] for t in teams]
    res["measures"][m] = {"label": LABEL[m], "mean": round(overall, 3), "team_low": round(min(v for v in vals if v is not None), 3),
                          "team_high": round(max(v for v in vals if v is not None), 3),
                          "between_team_share_of_variance": round(between / total, 4) if total else None,
                          "split_half_r": None if half is None else round(half, 3), "reliability_full_period": None if full is None else round(full, 3),
                          "r_with_team_size": None if pearson(vals, [size[t] for t in teams]) is None else round(pearson(vals, [size[t] for t in teams]), 3),
                          "r_with_plan_load": None if pearson(vals, [loadf[t] for t in teams]) is None else round(pearson(vals, [loadf[t] for t in teams]), 3)}
json.dump(res, open(out, "w"), indent=1)
print(f"{res['database']}: {res['teams']} teams, {res['shifts']} shifts, {res['team_shifts']} team-shifts")
print(f"{'measure':52s} {'mean':>9s} {'low':>9s} {'high':>9s} {'between':>8s} {'half r':>7s} {'reliab':>7s} {'r size':>7s} {'r load':>7s}")
for m in MEASURES:
    v = res["measures"][m]
    f = lambda x: "   n/a" if x is None else f"{x:7.3f}"
    print(f"{LABEL[m][:52]:52s} {v['mean']:9.2f} {v['team_low']:9.2f} {v['team_high']:9.2f} {f(v['between_team_share_of_variance']):>8s} {f(v['split_half_r'])} {f(v['reliability_full_period'])} {f(v['r_with_team_size'])} {f(v['r_with_plan_load'])}")
