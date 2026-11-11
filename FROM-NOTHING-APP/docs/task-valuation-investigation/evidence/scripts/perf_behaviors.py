"""T3: which floor-level measure follows production when the floor's behavior changes (the 18 paired runs of report 10).

Usage: perf_behaviors.py RUN_DIR CALIB.json OUT.json   (RUN_DIR holds the C_<behavior>_<seed>.db runs of report 10)
"""
import json, sys
from perf_measures import OOS_RATE, W, load, spearman

OUT, CALIB, RESULT = sys.argv[1], sys.argv[2], sys.argv[3]
calib = json.load(open(CALIB))["runs"]
BEH = ("N0", "PL", "PH", "PF", "SE", "SC", "SF", "DF", "DL")


def floor(path):
    recs = load(path, with_weights=False)
    t = {k: sum(r[k] or 0 for r in recs) for k in ("B", "E", "Cp", "U", "oos", "units", "earned", "relieved", "available_h", "actual_h", "db_units", "db_minutes")}
    D = t["B"] - t["E"]
    return {"score": 100 * (W * min(1, (t["Cp"] + t["U"]) / D) + (1 - W) * min(1, t["Cp"] / D)) - 100 * OOS_RATE * t["oos"] / D,
            "att_excused": 100 * t["Cp"] / D, "att_raw": 100 * t["Cp"] / (t["B"] - t["db_minutes"]),
            "ppc_count": 100 * t["earned"] / (t["units"] - t["relieved"] - t["db_units"]),
            "throughput": t["Cp"] + t["U"], "per_available_hour": (t["Cp"] + t["U"]) / 60 / t["available_h"],
            "efficiency": (t["Cp"] + t["U"]) / 60 / t["actual_h"], "output_index": 100 * (t["Cp"] + t["U"]) / D,
            "mean_team_shift_score": sum(r["score"] for r in recs if r["score"] is not None) / sum(1 for r in recs if r["score"] is not None)}


rows = {}
for seed in ("s6", "s7"):
    for b in BEH:
        name = f"C_{b}_{seed}"
        f = floor(f"{OUT}/{name}.db")
        prod = calib[name]["prod"]
        rows[name] = {**f, "jobs": prod["jobs"], "open_work": prod["open_work_last"], "cross_team_relief": prod["cross_team_relief"],
                      "output": prod["throughput"]}
deltas = []
for seed in ("s6", "s7"):
    base = rows[f"C_N0_{seed}"]
    for b in BEH[1:]:
        r = rows[f"C_{b}_{seed}"]
        deltas.append({"run": f"{b}_{seed}", **{k: r[k] - base[k] for k in r}})
print(f"{'run':8s} {'jobs':>6s} {'output':>8s} {'open':>8s} {'xteam':>6s} | {'score':>6s} {'att_ex':>7s} {'att_raw':>7s} {'count':>6s} {'perAvH':>7s} {'effic':>6s} {'outIdx':>7s}")
for d in deltas:
    print(f"{d['run']:8s} {d['jobs']:6.0f} {d['output']:8.0f} {d['open_work']:8.0f} {d['cross_team_relief']:6.0f} | {d['score']:6.2f} {d['att_excused']:7.2f} {d['att_raw']:7.2f} "
          f"{d['ppc_count']:6.2f} {d['per_available_hour']:7.4f} {d['efficiency']:6.3f} {d['output_index']:7.2f}")
measures = ("score", "mean_team_shift_score", "att_excused", "att_raw", "ppc_count", "throughput", "per_available_hour", "efficiency", "output_index")
res = {}
print()
print(f"{'measure':24s} {'rho jobs':>9s} {'rho output':>11s} {'rho -open work':>15s} {'sign jobs':>10s}")
for m in measures:
    x = [d[m] for d in deltas]
    rj, ro, rw = spearman(x, [d["jobs"] for d in deltas]), spearman(x, [d["output"] for d in deltas]), spearman(x, [-d["open_work"] for d in deltas])
    sign = sum(1 for d in deltas if (d[m] > 0) == (d["jobs"] > 0))
    res[m] = {"spearman_jobs": round(rj, 3), "spearman_output": round(ro, 3), "spearman_less_open_work": round(rw, 3), "sign_agreement_jobs": f"{sign}/{len(deltas)}"}
    print(f"{m:24s} {rj:9.3f} {ro:11.3f} {rw:15.3f} {sign:>7d}/{len(deltas)}")
json.dump({"levels": rows, "deltas": deltas, "alignment": res}, open(RESULT, "w"), indent=1)
