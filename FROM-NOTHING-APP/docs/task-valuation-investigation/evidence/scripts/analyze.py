"""Summaries of a study JSON: distribution, agreement of (a)-(c) with the exact counterfactual, variant pricing, joint sets."""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict

import numpy as np

path = sys.argv[1]
d = json.load(open(path))
meta, rows, joint = d["meta"], d["rows"], d["joint"]
A = meta["aircraft"]
print(f"== {path}: n={meta['n']} units={meta['units']} fleet={meta.get('fleet_overrides')} plan={meta['plan']['controllable_usd']:,.0f} USD "
      f"redispatch={meta['redispatch_accepted']} deterministic={meta['deterministic']} exec_overlap={meta.get('exec_overlap')} exec={meta.get('executable')}")
for line, a in A.items():
    print(f"  {line} {a['category']:30s} late {a['delay_days'] if a['delay_days'] is None else round(a['delay_days'], 2)} d rate {a['rate_usd']} "
          f"ctrl {a['controllable_usd']}")
print("  graph", meta["graph"], "timings", meta["timings"], "peak MB", meta["peak_rss_mb"])
units_by_line = Counter(r["line"] for r in rows)
print("  units per aircraft", dict(sorted(units_by_line.items())))
tf = [r["TF"] for r in rows]
print(f"  precedence float TF (days): min {min(tf)/1440:.2f} median {np.median(tf)/1440:.2f} max {max(tf)/1440:.2f}; D (min) median {np.median([r['D'] for r in rows])}")

METHODS = ["a_seed_usd", "a_plan_usd", "b_usd", "c0_usd", "c_usd", "d1_usd", "d2_usd"]


def conc(vals):
    v = np.array(sorted([x for x in vals if x is not None], reverse=True))
    if len(v) == 0:
        return {}
    pos = v[v > 0]
    tot = pos.sum()
    k1, k10 = max(1, math.ceil(0.01 * len(v))), max(1, math.ceil(0.10 * len(v)))
    return {"n": len(v), "zero_share": round(float((v == 0).mean()), 3), "neg_share": round(float((v < 0).mean()), 3),
            "pos_share": round(float((v > 0).mean()), 3), "sum_pos": round(float(tot)), "sum_all": round(float(v.sum())),
            "top1pct_share": round(float(v[:k1].clip(min=0).sum() / tot), 3) if tot > 0 else None,
            "top10pct_share": round(float(v[:k10].clip(min=0).sum() / tot), 3) if tot > 0 else None,
            "max": round(float(v[0])), "min": round(float(v[-1])), "median": round(float(np.median(v)))}


print("\n-- distribution over the shift's units (USD for a one-shift slip)")
for m in METHODS:
    c = conc([r.get(m) for r in rows])
    if c:
        print(f"  {m:11s} {c}")


def rank(x):
    x = np.asarray(x, float)
    order = x.argsort(kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    # average ties
    vals, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(vals))
    np.add.at(sums, inv, r)
    return (sums / counts)[inv]


def corr(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if x.std() == 0 or y.std() == 0:
        return None
    return round(float(np.corrcoef(x, y)[0, 1]), 3)


print("\n-- agreement with the exact counterfactual d1 (units with d1)")
S = [r for r in rows if r.get("d1_usd") is not None]
if S:
    truth = np.array([r["d1_usd"] for r in S])
    print(f"  units {len(S)}; d1 nonzero {int((truth != 0).sum())} (pos {int((truth > 0).sum())}, neg {int((truth < 0).sum())}); "
          f"unknown set changed {sum(r['d1_unknown_changed'] for r in S)}; mean d1 {truth.mean():,.0f}; d1 decode s median {np.median([r['d1_s'] for r in S]):.2f}")
    ktop = max(1, math.ceil(0.1 * len(S)))
    top_truth = set(np.argsort(-truth, kind="mergesort")[:ktop]) if (truth > 0).any() else set()
    for m in ["a_seed_usd", "a_plan_usd", "b_usd", "c0_usd", "c_usd", "d2_usd"]:
        pairs = [(r.get(m), r["d1_usd"]) for r in S if r.get(m) is not None]
        if len(pairs) < 3:
            continue
        x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs])
        mae = float(np.abs(x - y).mean())
        sp = corr(rank(x), rank(y))
        pe = corr(x, y)
        both_zero = float(((x == 0) & (y == 0)).mean())
        flag = float(((x != 0) == (y != 0)).mean())
        idx = [i for i, r in enumerate(S) if r.get(m) is not None]
        xs = np.array([S[i][m] for i in idx])
        top_m = set(np.array(idx)[np.argsort(-xs, kind="mergesort")[:ktop]]) if (xs > 0).any() else set()
        overlap = len(top_m & top_truth) / ktop if top_truth else None
        print(f"  {m:11s} n={len(pairs)} spearman={sp} pearson={pe} MAE=${mae:,.0f} zero/nonzero agreement={flag:.3f} both-zero={both_zero:.3f} "
              f"top10% overlap={overlap}")
    # failure modes: largest misses for c and a_plan
    for m in ("c_usd", "a_plan_usd"):
        worst = sorted(S, key=lambda r: -abs((r.get(m) or 0) - r["d1_usd"]))[:6]
        print(f"  largest |{m} - d1|:")
        for r in worst:
            print(f"    {r['pid'][:26]} line {r['line']} team {r['team']} kind {r['kind']} TF {r['TF']/1440:.1f}d work {r['work']} {m}={r.get(m)} "
                  f"d1={r['d1_usd']:,.0f} per-aircraft={r.get('d1_per')} shifts={r.get('d1_shift')} moved={r.get('d1_moved')}")

print("\n-- totals per team-shift and per aircraft (USD)")
for m in ["a_plan_usd", "c_usd", "d1_usd"]:
    if not any(r.get(m) is not None for r in rows):
        continue
    team = defaultdict(float); line = defaultdict(float)
    for r in rows:
        v = r.get(m)
        if v is None:
            continue
        team[r["team"]] += v
        line[r["line"]] += v
    tv = sorted(team.values(), reverse=True)
    print(f"  {m}: team-shifts {len(tv)}, zero-total {sum(1 for x in tv if x == 0)}, top team {tv[0]:,.0f}, top-3 share "
          f"{(sum(tv[:3]) / sum(x for x in tv if x > 0)) if any(x > 0 for x in tv) else None}")
    print(f"     by aircraft (own units): {dict((k, round(v)) for k, v in sorted(line.items()))}")

print("\n-- d1 impact attributed to aircraft (sum of d1_per over units)")
att = defaultdict(float); cnt = Counter()
for r in S:
    for line, v in (r.get("d1_per") or {}).items():
        att[line] += v; cnt[line] += 1
print("  ", {k: (round(v), cnt[k]) for k, v in sorted(att.items())})
cross = sum(1 for r in S if any(str(l) != str(r["line"]) for l in (r.get("d1_per") or {})))
print(f"  units whose d1 moved another aircraft's cost: {cross} of {len(S)}")

print("\n-- variant pricing of the same completion shifts (own aircraft re-priced; others as planned)")
def price(rate, delta_min, variant, late_min):
    if delta_min is None:
        return None
    if variant == "late":
        return rate * max(delta_min, -late_min) / 1440 if late_min > 0 else rate * max(0, delta_min - 0) / 1440
    if variant == "early_2d":
        return rate * max(0, delta_min - 2880) / 1440
    if variant == "at_cap":
        return rate * min(0, delta_min) / 1440
    if variant == "unknown":
        return None
for src in ("d1_shift",):
    tot = Counter(); nz = Counter(); unk = Counter()
    for r in S:
        sh = r.get(src) or {}
        for variant in ("late", "early_2d", "at_cap", "unknown"):
            s = 0.0; nonzero = False; unknown = False
            for line, delta in sh.items():
                a = A[str(line)]
                if a["rate_usd"] is None or a["delay_days"] is None:
                    continue
                v = price(a["rate_usd"], delta, variant if str(line) == str(r["line"]) else "late", a["delay_days"] * 1440)
                if v is None:
                    unknown = True
                    continue
                s += v; nonzero = nonzero or v != 0
            tot[variant] += s; nz[variant] += nonzero; unk[variant] += unknown
    print(f"  {src}: total {dict((k, round(v)) for k, v in tot.items())} nonzero units {dict(nz)} units with unknown part {dict(unk)}")

if joint:
    print("\n-- non-additivity: joint counterfactual vs the sum of individual d1")
    for j in joint:
        print(f"  {j['kind']:22s} k={len(j['pids'])} sum={j['sum_individual_usd']:,.0f} max={j['max_individual_usd']:,.0f} joint={j['joint_usd']:,.0f} "
              f"sum_c={j['sum_c_usd']:,.0f} sum_b={j['sum_b_usd']:,.0f} unknown_changed={j['unknown_changed']}")
if meta.get("d2_baseline"):
    print("\n-- repair-mode counterfactual d2 baseline", meta["d2_baseline"])
    D2 = [r for r in rows if r.get("d2_usd") is not None]
    if D2:
        x = np.array([r["d2_usd"] for r in D2]); y = np.array([r.get("d1_usd", 0) for r in D2])
        print(f"  d2 n={len(D2)} nonzero {int((x != 0).sum())} mean {x.mean():,.0f} max {x.max():,.0f} min {x.min():,.0f}; vs d1 spearman {corr(rank(x), rank(y))} "
              f"MAE ${np.abs(x - y).mean():,.0f}; d2 seconds median {np.median([r['d2_s'] for r in D2]):.1f}; rehomed median {np.median([r['d2_rehomed'] for r in D2])}")
