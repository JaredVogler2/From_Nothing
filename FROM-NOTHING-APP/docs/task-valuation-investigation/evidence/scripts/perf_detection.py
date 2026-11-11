"""T4: detection power. Real execution on the stored 50-aircraft, 42-shift floor (every team equal by construction), plus an
injected, known shortfall: a share p of a chosen team's completed planned jobs is turned into unexcused misses.

(a) Across teams: 6 of 30 teams are made worse. How often does a measure, pooled over N shifts, put a worse team below a
    normal one (AUC: 0.5 = cannot tell, 1.0 = always)?
(b) Over time: one team is made worse in the second of two periods of W shifts. How often is its change between the periods
    lower than an unchanged team's change (AUC)? And how large is the change between two periods when nothing changed?


Usage: perf_detection.py SIM_DB OUT.json
"""
import json, random, sqlite3, sys
from collections import defaultdict
from perf_measures import LABEL, OOS_RATE, W as RATE, load, weights

DB = sys.argv[1]
M = ("score", "att_excused", "att_raw", "ppc_count", "throughput", "per_available_hour", "points_sum", "points_per_available_hour")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
w = weights(con)
recs = {(r["team"], r["shift"]): r for r in load(DB) if r["team"] != "QA"}
teams = sorted({t for t, _ in recs})
shifts = sorted({s for _, s in recs})
earned = defaultdict(list)                                   # (team, shift) -> [(effort, points)] of earned planned units
for s, team, line, effort in con.execute("select shift_no, team, line, effort from perf_unit where status='EARNED'"):
    earned[(team, s)].append((effort, effort * (1.0 + w.get((s, line), 0.0))))


def pooled(team, window, loss=None):
    """Each measure for one team pooled over the shifts in ``window``; ``loss`` maps shift -> (minutes, jobs, points) removed."""
    t = defaultdict(float)
    for s in window:
        r = recs.get((team, s))
        if r is None:
            continue
        for k in ("B", "E", "Cp", "U", "oos", "units", "earned", "relieved", "available_h", "points_sum", "db_units", "db_minutes"):
            t[k] += r[k] or 0.0
        if loss and s in loss:
            dm, dj, dp = loss[s]
            t["Cp"] -= dm; t["earned"] -= dj; t["points_sum"] -= dp
    D = t["B"] - t["E"]
    if D <= 0 or t["B"] <= 0 or not t["available_h"]:
        return None
    return {"score": max(0.0, 100 * (RATE * min(1, (t["Cp"] + t["U"]) / D) + (1 - RATE) * min(1, t["Cp"] / D)) - 100 * OOS_RATE * t["oos"] / D),
            "att_excused": 100 * min(1.0, t["Cp"] / D), "att_raw": 100 * min(1.0, t["Cp"] / (t["B"] - t["db_minutes"])),
            "ppc_count": 100 * t["earned"] / max(1.0, t["units"] - t["relieved"] - t["db_units"]), "throughput": t["Cp"] + t["U"],
            "per_available_hour": (t["Cp"] + t["U"]) / 60 / t["available_h"], "points_sum": t["points_sum"],
            "points_per_available_hour": t["points_sum"] / 60 / t["available_h"]}


def inject(team, window, p, rng):
    loss = {}
    for s in window:
        dm = dj = dp = 0.0
        for effort, pts in earned.get((team, s), ()):
            if rng.random() < p:
                dm += effort; dj += 1; dp += pts
        if dj:
            loss[s] = (dm, dj, dp)
    return loss


def auc(low, high):
    """P(a value from ``low`` is below a value from ``high``), ties counted half."""
    n = wins = 0.0
    for a in low:
        for b in high:
            n += 1
            wins += 1.0 if a < b else 0.5 if a == b else 0.0
    return wins / n if n else None


rng = random.Random(20261111)
TRIALS = 200
res = {"database": DB.rsplit("/", 1)[-1], "trials": TRIALS, "across_teams": {}, "over_time": {},
       "no_change_band": {}}
# ---------------------------------------------------------------- (a) across teams
for p in (0.02, 0.05, 0.10):
    for n in (1, 3, 15, 42):
        acc = defaultdict(list)
        for _ in range(TRIALS):
            start = rng.randrange(0, len(shifts) - n + 1)
            window = shifts[start:start + n]
            weak = set(rng.sample(teams, 6))
            vals = {t: pooled(t, window, inject(t, window, p, rng) if t in weak else None) for t in teams}
            for m in M:
                lo = [vals[t][m] for t in weak if vals[t] is not None]
                hi = [vals[t][m] for t in teams if t not in weak and vals[t] is not None]
                a = auc(lo, hi)
                if a is not None:
                    acc[m].append(a)
        res["across_teams"][f"p{p}_n{n}"] = {m: round(sum(v) / len(v), 3) for m, v in acc.items()}
# ---------------------------------------------------------------- (b) over time, and the band when nothing changed
for wlen in (3, 9, 15, 21):
    null, rel = defaultdict(list), defaultdict(list)
    for start in range(0, len(shifts) - 2 * wlen + 1, 3):
        p1, p2 = shifts[start:start + wlen], shifts[start + wlen:start + 2 * wlen]
        for t in teams:
            a, b = pooled(t, p1), pooled(t, p2)
            if a and b:
                for m in M:
                    null[m].append(b[m] - a[m])
                    if a[m]:
                        rel[m].append(abs(b[m] - a[m]) / abs(a[m]))            # the change as a share of the first period's own level
    band = {}
    for m in M:
        v, r = sorted(abs(x) for x in null[m]), sorted(rel[m])
        band[m] = {"median_abs_change": round(v[len(v) // 2], 4), "p90_abs_change": round(v[int(0.9 * (len(v) - 1))], 4),
                   "p90_as_share_of_level": round(r[int(0.9 * (len(r) - 1))], 4) if r else None, "pairs": len(v)}
    res["no_change_band"][f"w{wlen}"] = band
    for p in (0.02, 0.05, 0.10):
        acc = defaultdict(list)
        for _ in range(TRIALS):
            start = rng.randrange(0, len(shifts) - 2 * wlen + 1)
            p1, p2 = shifts[start:start + wlen], shifts[start + wlen:start + 2 * wlen]
            worse = rng.choice(teams)
            d = {}
            for t in teams:
                a, b = pooled(t, p1), pooled(t, p2, inject(t, p2, p, rng) if t == worse else None)
                if a and b:
                    d[t] = {m: b[m] - a[m] for m in M}
            if worse not in d:
                continue
            for m in M:
                acc[m].append(auc([d[worse][m]], [d[t][m] for t in d if t != worse]))
        res["over_time"][f"p{p}_w{wlen}"] = {m: round(sum(v) / len(v), 3) for m, v in acc.items()}
json.dump(res, open(sys.argv[2], "w"), indent=1)
short = {"score": "score", "att_excused": "att_exc", "att_raw": "att_raw", "ppc_count": "count", "throughput": "thruput", "per_available_hour": "perAvH",
         "points_sum": "points", "points_per_available_hour": "ptsAvH"}
print("(a) ACROSS TEAMS: chance a truly worse team ranks below a normal one (0.5 = cannot tell)")
print(f"{'shortfall / shifts':20s} " + " ".join(f"{short[m]:>8s}" for m in M))
for k, v in res["across_teams"].items():
    print(f"{k:20s} " + " ".join(f"{v[m]:8.3f}" for m in M))
print("\n(b) OVER TIME: chance the changed team's period-to-period change is below an unchanged team's")
print(f"{'shortfall / window':20s} " + " ".join(f"{short[m]:>8s}" for m in M))
for k, v in res["over_time"].items():
    print(f"{k:20s} " + " ".join(f"{v[m]:8.3f}" for m in M))
print("\nNO CHANGE: 90% of period-to-period changes are smaller than this (points, or share of the measure's level)")
for k, band in res["no_change_band"].items():
    print(f"{k:6s} " + " ".join(f"{short[m]}={band[m]['p90_abs_change']:.2f}({band[m]['p90_as_share_of_level']:.1%})" for m in M))
