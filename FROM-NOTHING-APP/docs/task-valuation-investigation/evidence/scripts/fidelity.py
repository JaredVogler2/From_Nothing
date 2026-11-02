"""Rank agreement of candidate valuation signals with the exact one-shift counterfactual (d1) on a study sample."""
import json, sys
import numpy as np

def rank(x):
    x = np.asarray(x, dtype=float)
    order = x.argsort(kind="mergesort")
    r = np.empty(len(x)); r[order] = np.arange(len(x))
    # average ties
    vals, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(vals)); np.add.at(sums, inv, r)
    return (sums / counts)[inv]

def spearman(a, b):
    ra, rb = rank(a), rank(b)
    return float(np.corrcoef(ra, rb)[0, 1])

d = json.load(open(sys.argv[1]))
rows = [r for r in d["rows"] if r.get("d1_usd") is not None]
d1 = [r["d1_usd"] for r in rows]
signals = {
    "A effort (standard person-minutes)": [r["spm"] for r in rows],
    "B1 CPM tail (pure work minutes)": [r["tail"] for r in rows],
    "B2 negative precedence float (-TF)": [-r["TF"] for r in rows],
    "C FOCUS cost_of_delay on plan (a)": [r["a_plan_usd"] for r in rows],
    "D-proxy precedence float $ (b)": [r["b_usd"] for r in rows],
    "D-proxy schedule-graph right shift $ (c)": [r["c_usd"] for r in rows],
    "F effort x band(c) [1, 2 if c>0, 3 if c top decile]": None,
}
c = np.array([r["c_usd"] for r in rows]); pos = c[c > 0]
p90 = np.percentile(pos, 90) if len(pos) else 0
signals["F effort x band(c) [1, 2 if c>0, 3 if c top decile]"] = [r["spm"] * (3 if r["c_usd"] >= p90 and r["c_usd"] > 0 else 2 if r["c_usd"] > 0 else 1) for r in rows]
nz = np.array(d1) != 0
top = set(np.argsort(-np.array(d1))[: max(1, len(d1) // 10)])
print(f"{sys.argv[1]}: n={len(rows)} exact nonzero={int(nz.sum())} negative={int((np.array(d1) < 0).sum())}")
for name, s in signals.items():
    s = np.array(s, dtype=float)
    agree = float(((s != 0) == nz).mean()) if name.startswith(("C", "D")) else None
    topk = len(top & set(np.argsort(-s)[: len(top)])) / len(top)
    print(f"  {name:55s} spearman {spearman(s, d1):+.3f}  top-10% overlap {topk:.2f}" + (f"  zero/nonzero agreement {agree:.2f}" if agree is not None else ""))
