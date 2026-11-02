"""Priority class of off-plan work versus planned work in a shadow run (shadow_sim.py). Investigation only.

Usage: shadow_offplan_classes.py OUT   (reads out/OUT.classes.jsonl and out/OUT.db)

Each off-plan completion at shift k is looked up in publication k's classes (the FORECAST plan of that shift, phases
starting within 24 hours of the shift start). Planned goal units are looked up the same way. Effort-weighted shares.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict

from common import SCRATCH

name = sys.argv[1]
recs = [json.loads(x) for x in (SCRATCH / "out" / f"{name}.classes.jsonl").read_text().splitlines() if x.strip()]
cls = {r["k"]: {u["phase"]: u["class"] for u in r["units"]} for r in recs}
db = sqlite3.connect(f"file:{SCRATCH / 'out' / (name + '.db')}?mode=ro", uri=True)
groups: dict[str, Counter] = defaultdict(Counter)
for k, phase, effort in db.execute("SELECT shift_no, phase, effort FROM perf_unit"):
    if k in cls:
        groups["planned_goal_units"][cls[k].get(phase, "UNMAPPED")] += effort
for k, phase, effort, kind in db.execute("SELECT shift_no, phase, effort, kind FROM exec_offplan"):
    if k in cls:
        groups[f"offplan_{kind}"][cls[k].get(phase, "UNMAPPED")] += effort
out = {"shifts_classified": len(cls),
       "effort_by_class": {g: dict(c) for g, c in groups.items()},
       "share_by_class": {g: {k: round(v / sum(c.values()), 4) for k, v in c.items()} for g, c in groups.items() if sum(c.values())}}
(SCRATCH / "out" / f"{name}.offplan_classes.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
