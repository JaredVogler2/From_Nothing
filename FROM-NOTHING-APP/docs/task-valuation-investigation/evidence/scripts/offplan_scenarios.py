"""Worked scenarios: what a scheduled and an unscheduled 5-hour task earn under each rule, and the incentive checks.

Usage: offplan_scenarios.py OUT

One team, 8-hour shift, standard person minutes (spm). Frozen plan B0 = 2,100 spm; a "5-hour task" is 300 spm. The
demonstration weights (0.70 attainment, 0.30 adherence), bands (A 90, B 80, C 70, D 60) and no OOS. Exact fractions.

Rules:
* R0  current canonical: A = min(1, Cp/D), H = Cp/(Cp+U), base = 100(0.7A + 0.3H). Every off-plan minute is in U.
* R1  off-plan ignored: as R0 with U = 0 (worth zero, no penalty).
* R3  flat credit delta, no classification: A = min(1, (Cp + delta*U)/D), adherence ignores off-plan (H = 1).
* R5  recommended for evaluation: classify off-plan work; dispatched-by-latest-plan work counts as planned;
      A' = min(1, (Cp + U_add)/D)  (output attainment: planned + additive off-plan)
      H' = min(1, Cp/D)            (plan adherence: off-plan never counts, either way)
      base = 100(0.7A' + 0.3H').   Substitutive off-plan earns nothing; the displaced planned unit is simply missed.
      Output index X = (Cp + U_add)/D, uncapped, and points = Cp + U_add (spm), outside the grade.
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction as F

WA, WH = F(7, 10), F(3, 10)
BANDS = (("A", 90), ("B", 80), ("C", 70), ("D", 60))
B0, T = 2100, 300


def grade(s):
    for g, lo in BANDS:
        if s >= lo:
            return g
    return "F"


# each scenario: B, E (relief), Cp (planned earned), and off-plan minutes by true kind
#   add: additive (team idle or its planned work blocked); sub: substitutive (a ready planned unit displaced);
#   replan: dispatched by the latest accepted plan but not a unit of the frozen goal; replan_moved: frozen effort the latest
#   plan moved out of the shift (no disruption on record)
S = {
    "S0 completes plan, idles 5 h":                          dict(E=0, Cp=B0, add=0, sub=0),
    "S1 completes plan, pulls a 5 h task ahead (additive)":  dict(E=0, Cp=B0, add=T, sub=0),
    "S2 late part blocks a 5 h task (relieved), idles":      dict(E=T, Cp=B0 - T, add=0, sub=0),
    "S3 late part blocks a 5 h task (relieved), fills 5 h":  dict(E=T, Cp=B0 - T, add=T, sub=0),
    "S4 own predecessor blocks 5 h (not relieved), idles":   dict(E=0, Cp=B0 - T, add=0, sub=0),
    "S5 own predecessor blocks 5 h (not relieved), fills":   dict(E=0, Cp=B0 - T, add=T, sub=0),
    "S6 swaps a ready 5 h planned task for 5 h unplanned":   dict(E=0, Cp=B0 - T, add=0, sub=T),
    "S7 swaps a ready 2 h planned task for 5 h unplanned":   dict(E=0, Cp=B0 - 120, add=0, sub=T),
    "S8 skips a ready 5 h planned task, idles":              dict(E=0, Cp=B0 - T, add=0, sub=0),
    "S9 follows the latest plan: 5 h moved out, 5 h in":     dict(E=0, Cp=B0 - T, add=0, sub=0, replan=T, replan_moved=T),
}


def score(rule, s, misclassify=False):
    D = F(B0 - s["E"])
    Cp, add, sub = F(s["Cp"]), F(s["add"]), F(s["sub"])
    replan, moved = F(s.get("replan", 0)), F(s.get("replan_moved", 0))
    if misclassify:                       # the classifier calls a swap additive
        add, sub = add + sub, F(0)
    if rule == "R0":
        U = add + sub + replan
        A = min(F(1), Cp / D)
        H = Cp / (Cp + U) if Cp + U else F(0)
        return 100 * (WA * A + WH * H)
    if rule == "R1":
        return 100 * (WA * min(F(1), Cp / D) + WH * (1 if Cp else 0))
    if rule.startswith("R3"):
        delta = F(rule.split("_")[1])
        U = add + sub + replan
        return 100 * (WA * min(F(1), (Cp + delta * U) / D) + WH * 1)
    if rule == "R5":
        # dispatched-by-latest-plan work is planned work: moved-out frozen effort is relieved, the dispatched work amends the goal
        D5 = D - moved + replan
        Cp5 = Cp + replan
        A5 = min(F(1), (Cp5 + add) / D5)
        H5 = min(F(1), Cp5 / D5)
        return 100 * (WA * A5 + WH * H5)
    raise ValueError(rule)


def output(s, misclassify=False):
    D = F(B0 - s["E"]) - s.get("replan_moved", 0) + s.get("replan", 0)
    add = s["add"] + (s["sub"] if misclassify else 0)
    pts = s["Cp"] + s.get("replan", 0) + add
    return float(pts / D), pts


rules = ["R0", "R1", "R3_1/2", "R3_1", "R5"]
table = {}
for name, s in S.items():
    row = {r: float(round(score(r, s), 2)) for r in rules}
    row.update({f"{r}_grade": grade(score(r, s)) for r in rules})
    x, pts = output(s)
    row["R5_output_index"] = round(x, 4)
    row["R5_points"] = pts
    table[name] = row

# per-minute marginal values (score points per spm, from S0 / the blocked baselines)
k = {n[:2]: n for n in S}


def d(a, b, r, mis=False):
    return float(score(r, S[k[a]], mis) - score(r, S[k[b]], mis))


checks = {}
for r in rules:
    checks[r] = {
        "pull_ahead_when_idle_pays (S1-S0 > 0)": d("S1", "S0", r),
        "fill_after_relieved_block_pays (S3-S2 > 0)": d("S3", "S2", r),
        "fill_after_unrelieved_block_pays (S5-S4 > 0)": d("S5", "S4", r),
        "equal_swap_never_pays (S6-S0 < 0)": d("S6", "S0", r),
        "cherry_pick_never_pays (S7-S0 < 0)": d("S7", "S0", r),
        "swap_beats_idling_after_skip? (S6-S8)": d("S6", "S8", r),
        "following_latest_plan_is_neutral (S9-S0 = 0)": d("S9", "S0", r),
        "equal_swap_if_misclassified (S6m-S0)": float(score(r, S[k["S6"]], True) - score(r, S[k["S0"]])),
        "cherry_pick_if_misclassified (S7m-S0)": float(score(r, S[k["S7"]], True) - score(r, S[k["S0"]])),
    }
# the exchange rate inside R5's grade: score points per scheduled minute vs per additive unscheduled minute (below the cap)
D = F(B0)
rates = {"R5_scheduled_points_per_spm": float(100 * (WA + WH) / D), "R5_additive_unscheduled_points_per_spm": float(100 * WA / D),
         "R5_ratio_scheduled_to_additive": float((WA + WH) / WA), "R5_substitutive_points_per_spm": 0.0,
         "R0_unscheduled_points_per_spm_at_full_plan": float(score("R0", S[k["S1"]]) - score("R0", S[k["S0"]])) / T,
         "swap_proof_condition": "an undetected swap pays only if unplanned spm > planned spm x (wA+wH)/wA = 1.43x"}
out = {"B0": B0, "task_spm": T, "weights": [str(WA), str(WH)], "scenarios": table, "checks": checks, "rates": rates}
open(sys.argv[1], "w").write(json.dumps(out, indent=1))
w = max(len(n) for n in S)
print(f"{'scenario':<{w}} " + " ".join(f"{r:>8}" for r in rules) + "   X_R5  pts_R5")
for name, row in table.items():
    print(f"{name:<{w}} " + " ".join(f"{row[r]:>6.2f}{row[r + '_grade']:>2}" for r in rules) + f"  {row['R5_output_index']:.3f} {row['R5_points']:>6}")
print()
for r, c in checks.items():
    print(r, json.dumps({kk: round(v, 2) for kk, v in c.items()}))
print(json.dumps(rates, indent=1))
