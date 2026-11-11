"""T5: what each level controls. On the 50-aircraft, 42-shift floor: where the planned minutes went (done, missed without an
excuse, excused by cause), who caused the excused misses, and what a superintendent's and the site's numbers look like under
three rules: the teams' excusals pooled as they are, excusals whose cause lies inside the group charged to the group, and no
excusals at all. Superintendent groups are hypothetical: six groups of five teams, by team number.

Usage: perf_levels.py SIM_DB OUT.json
"""
import json, sqlite3, sys
from collections import defaultdict

con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
teams = sorted(t for (t,) in con.execute("select distinct team from perf_team_shift") if t != "QA")
group = {t: f"S{i // 5 + 1}" for i, t in enumerate(teams)}
group["QA"] = "QA"
tot = defaultdict(float)
by_cause = defaultdict(float)
internal_to_group = defaultdict(float)       # excused minutes whose origin team is in the same superintendent group
internal_to_site = 0.0
g = defaultdict(lambda: defaultdict(float))
for team, status, relief, cause, origin, effort in con.execute(
        "select team, status, relief, coalesce(claim_cause, cause, 'NO_REASON'), origin_team, effort from perf_unit"):
    if team == "QA":
        continue
    G = g[group[team]]
    tot["planned"] += effort; G["B"] += effort
    if status == "EARNED":
        tot["done"] += effort; G["Cp"] += effort
    elif relief:
        tot["excused"] += effort; by_cause[cause] += effort; G["E"] += effort
        if origin is not None:
            internal_to_site += effort
            if group.get(origin) == group[team]:
                G["E_internal"] += effort; internal_to_group[cause] += effort
    else:
        tot["unexcused"] += effort; by_cause["unexcused:" + cause] += effort
print("planned minutes", int(tot["planned"]), "| done", f"{tot['done'] / tot['planned']:.2%}", "| excused", f"{tot['excused'] / tot['planned']:.2%}",
      "| missed without excuse", f"{tot['unexcused'] / tot['planned']:.2%}")
lost = tot["excused"] + tot["unexcused"]
print("of the planned minutes not done:", f"{tot['excused'] / lost:.1%} excused, {tot['unexcused'] / lost:.1%} not")
for c, m in sorted(by_cause.items(), key=lambda kv: -kv[1]):
    print(f"  {c:40s} {int(m):8d} min  {m / lost:6.1%} of the loss")
print("excused minutes caused by another production team on the same floor:", int(internal_to_site), f"({internal_to_site / tot['excused']:.1%} of excused)")
print("  ... of which the causing team is under the same (hypothetical) superintendent:", int(sum(internal_to_group.values())),
      f"({sum(internal_to_group.values()) / tot['excused']:.1%} of excused)")
rows = {}
print(f"\n{'group':6s} {'teams excused':>14s} {'own causes charged':>19s} {'no excusals':>12s}")
for G, v in sorted(g.items()):
    a = 100 * v["Cp"] / (v["B"] - v["E"])
    b = 100 * v["Cp"] / (v["B"] - v["E"] + v["E_internal"])
    c = 100 * v["Cp"] / v["B"]
    rows[G] = {"pooled_team_excusals": round(a, 2), "internal_causes_charged": round(b, 2), "no_excusals": round(c, 2)}
    print(f"{G:6s} {a:14.2f} {b:19.2f} {c:12.2f}")
site = {"pooled_team_excusals": round(100 * tot["done"] / (tot["planned"] - tot["excused"]), 2),
        "internal_causes_charged": round(100 * tot["done"] / (tot["planned"] - tot["excused"] + internal_to_site), 2),
        "no_excusals": round(100 * tot["done"] / tot["planned"], 2)}
print(f"{'site':6s} {site['pooled_team_excusals']:14.2f} {site['internal_causes_charged']:19.2f} {site['no_excusals']:12.2f}")
json.dump({"totals": dict(tot), "loss_by_cause": dict(by_cause), "excused_caused_on_the_floor": internal_to_site, "groups": rows, "site": site},
          open(sys.argv[2], "w"), indent=1)
