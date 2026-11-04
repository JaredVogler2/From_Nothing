"""Exact arithmetic for the hand-verifiable counterexamples of 02_HYPOTHESIS_STRESS_TEST.md.

Time unit: hours. Illustrative money: aircraft X $300,000/day, Y $150,000/day (labeled ILLUSTRATIVE; inside the synthetic
fleet's $138k-$380k/day range). The FOCUS urgency is computed with the repository's own decay table and rule
(focus/engine/max/optimizer/urgency.py: weight x COD_DECAY[slack // 360 minutes], slack = latest start - earliest start,
latest start = due - CPM tail, clamped at 0). Penalty = rate x max(0, completion - due) / 24 (LINEAR_DAILY, no cap).
"""
from __future__ import annotations

import sys
from fractions import Fraction as F

sys.path.insert(0, "/home/user/From_Nothing/FROM-NOTHING-APP")
from focus.engine.max.optimizer.urgency import COD_DECAY, COD_STEP_MINUTES  # noqa: E402

X, Y = F(300_000), F(150_000)


def penalty(rate, completion, due):
    return rate * max(F(0), F(completion) - F(due)) / 24


def marginal_day(rate, completion, due):
    """registry.marginal with weight_horizon_days = 1: penalty(completion + 24h) - penalty(completion)."""
    return penalty(rate, F(completion) + 24, due) - penalty(rate, completion, due)


def urgency(weight, due, tail, earliest):
    latest = F(due) - F(tail)
    slack_min = max(F(0), latest - F(earliest)) * 60
    step = int(slack_min // COD_STEP_MINUTES)
    decay = F(COD_DECAY[step], 1_000_000) if step < len(COD_DECAY) else F(0)
    return weight * decay, step, decay


def show(name, **kv):
    print(name, {k: (float(v) if isinstance(v, F) else v) for k, v in kv.items()})


# CE1 short zero-float release vs long slack-rich job (aircraft X, due 30h)
# A (2h, 1 person) -> C (30h); L (20h, 2 persons) parallel. Completion = max(32, 20) = 32h.
base = penalty(X, 32, 30)
w = marginal_day(X, 32, 30)
show("CE1", completion=32, penalty=base, weight=w,
     mi_A=penalty(X, 40, 30) - base, mi_L=penalty(X, max(32, 28), 30) - base,
     effort_A=2 * 60, effort_L=20 * 2 * 60,
     u_A=urgency(w, 30, 32, 0)[0], u_L=urgency(w, 30, 20, 0)[0], u_L_decay=urgency(w, 30, 20, 0)[2])

# CE2 two zero-float jobs, different exposure. X due 30h, chain P(4)->Q(26) completes 30h; Y due 50h, M(4)->N(26) completes 30h.
wX, wY = marginal_day(X, 30, 30), marginal_day(Y, 30, 50)
show("CE2", wX=wX, wY=wY, mi_P=penalty(X, 38, 30) - penalty(X, 30, 30), mi_M=penalty(Y, 38, 50) - penalty(Y, 30, 50),
     u_P=urgency(wX, 30, 30, 0)[0], u_M=urgency(wY, 50, 30, 0)[0], u_M_step=urgency(wY, 50, 30, 0)[1])

# CE3 scarce inspector. X: W1(8)->I1(2)->W2(8), due 20h. Y: V1(8)->I2(2)->V2(12), due 22h. One inspector.
# Precedence-only: X completes 18 (CPM float of I1 to due = 2h); Y completes 22 (float 0).
# Resource-feasible plan: I2 8-10 (Y critical), I1 10-12 -> X completes 20 = due (resource-constrained float of I1 = 0).
show("CE3", cpm_float_I1=20 - 18, rc_float_I1=20 - 20,
     mi_I1_defer8=penalty(X, 28, 20) - penalty(X, 20, 20),
     mi_I2_defer8=(penalty(Y, 30, 22) - penalty(Y, 22, 22)) + (penalty(X, 18, 20) - penalty(X, 20, 20)),
     u_I1=urgency(marginal_day(X, 20, 20), 20, 10, 8)[0], u_I1_step=urgency(marginal_day(X, 20, 20), 20, 10, 8)[1])

# CE4 high cost of delay scheduled late: shift 0-8h, part for H arrives 5h. Plan: F (4h, float 72h) 0-4, H (3h, critical, X late) 5-8.
show("CE4", start_F=0, start_H=5, mi_H=penalty(X, 40 + 8, 30) - penalty(X, 40, 30), mi_F=0)

# CE7 shared critical chain: A(4) 0-4 -> B(4) 4-8 -> C(16) 8-24, X due 20h (4h late).
b7 = penalty(X, 24, 20)
show("CE7", base=b7, mi_A=penalty(X, 32, 20) - b7, mi_B=penalty(X, 32, 20) - b7, joint=penalty(X, 32, 20) - b7,
     sum_singles=2 * (penalty(X, 32, 20) - b7))

# CE8 AND gate: Z needs A (team 1) and B (team 2), both planned 0-4h; Z critical. Either missing slips Z one shift.
v = penalty(X, 32, 20) - penalty(X, 24, 20)
show("CE8", mi_A=v, mi_B=v, joint=v, shapley_A=v / 2, shapley_B=v / 2, last_finisher_rule="all to whichever finishes last")

# CE11 unknown economics: weight absent -> no cost-of-delay key; capped (if a cap were applied): marginal 0 past the cap.
cap = F(1_000_000)
show("CE11", capped_marginal=min(cap, penalty(X, 24 * 5 + 24, 0)) - min(cap, penalty(X, 24 * 5, 0)))

# CE13 already-late aircraft: due -240h (10 days late at cutoff 0), completion 48h -> 12 days late; 2 controllable days.
unavoidable = penalty(X, 0, -240)
total = penalty(X, 48, -240)
show("CE13", total=total, unavoidable=unavoidable, controllable=total - unavoidable, marginal_day=marginal_day(X, 48, -240),
     marginal_day_on_time_aircraft=marginal_day(X, 48, 48))
