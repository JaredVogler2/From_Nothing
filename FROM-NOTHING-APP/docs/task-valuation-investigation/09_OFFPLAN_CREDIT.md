# 09 — Off-plan work: what an unscheduled minute is worth

**Owner question (2026-11-02).** "I don't want work done that wasn't on plan to be worth zero. They should not count toward adherence however (open to a rebuttal about this, however). But I'm not sure how to quantitatively determine how much more a scheduled 5-hour task is worth vs a non-scheduled 5-hour task toward scoring."

**Scope.** Investigation only, on synthetic data (`FleetSpec`), the production engine and the production simulation floor (`focus/sim`). No application code, policy, test or schema was changed. Weights and bands are the demonstration policy (`config/policies/grading-demo-v1.json`: attainment 0.70, adherence 0.30, OOS rate 0.10, bands A 90 / B 80 / C 70 / D 60). That policy is not owner-approved (OD-16).

**Units.** Standard person minutes (spm). A "5-hour task" is 300 spm. The worked scenarios use a 35-hour (2,100 spm) team-shift plan.

## 1. Answer

1. **Today, unscheduled work is worth less than zero.** Adherence *H* = Cp/(Cp+U) falls with every off-plan minute, whether the team was idle, blocked, or following the latest dispatched plan (§2).
2. **No single exchange rate is honest.** What an unscheduled minute is worth depends on what it displaced, not on whether it was scheduled. In paired floor simulations (10 aircraft, 12 shifts, two seeds, §4.2):
   - **Done in idle time, it displaces nothing.** Each minute pulled ahead raised total verified output by **1.9–2.2 minutes**: finishing work early made its successors ready sooner, so more planned work got done too (+28,700 to +36,900 spm), and 500 or more extra jobs were completed.
   - **Done instead of ready scheduled work, it loses value.** Each displaced planned minute cost **0.33–0.35 minutes** of net output and blocked **0.20 minutes** of other teams' work, even though the swapped-in work was the team's own next-shift work.
3. **The value evidence does not support a fixed premium for scheduled work:**
   - one-shift delivery dollars are $0 for almost every unit, scheduled or pulled ahead (§4.1);
   - pulled-ahead work has the same priority-class mix as planned work (§4.3);
   - additive unscheduled minutes were worth more than their own content, and displacing minutes cost more than their own content (§4.2).

   A premium for scheduled work is an **incentive device**: it keeps teams on the plan and makes an undetected swap unprofitable. It is not a measured difference in value, and it should be set knowing that.
4. **The system already has the right lever.** The goal-amendment command accepts any open, ready task the team owns (`performance/amendments.py:57-126`). The work must either be assigned in that shift by the current accepted plan, or fit in qualified spare capacity after planned duties. An approved task counts 1:1 as scheduled work. The exchange-rate question only arises for work done **without** an approved addition.
5. **Recommendation (for evaluation; nothing implemented):**
   - **Each shift is measured against the plan published for it** (owner direction, §7). Goals of record then freeze per shift instead of per day, so work the latest plan dispatched is planned work, and work it moved out is not a miss.
   - **Additive off-plan work** (no ready planned work left undone) counts 1:1 in an *output* measure. It also counts toward the grade through an output-attainment term, and never toward plan adherence.
   - **Substitutive off-plan work** earns nothing. The displaced planned unit is missed: one penalty, not two.
   - **Grade = 100 × (w × output attainment + (1 − w) × plan attainment).** The exchange rate *is* w. With the demo split (w = 0.70), a scheduled 5-hour task earns 14.3 grade points on a 35-hour plan, and an additive unscheduled one earns 10.0, up to the plan size. At the suggested w = 0.80 it earns 11.4 (§8). Points and the output index count both 1:1.
6. **Adherence.** The owner is right that off-plan work should not count toward adherence. Four rebuttal points need answers before that is final (§7):
   - removing it leaves today's adherence term constant, so the bands must be re-derived;
   - shared resources the plan does not model;
   - rework risk;
   - off-plan volume as a planning signal.

## 2. What the current score does to off-plan work (measured)

`performance/score.py:95-108`: *A* = min(1, Cp/D), *H* = Cp/(Cp+U), base = 100(0.7*A* + 0.3*H*). U is every verified completion outside a goal of record (`performance/lifecycle.py:37-38`, `796-818`). In production U has two reasons:
- `NOT_IN_ANY_GOAL_OF_RECORD`: pulled ahead, swapped in, or dispatched by a later plan;
- `LATE_FOR_GOAL`: planned work finished after its goal's credit deadline. Unless relieved, that work was already counted as missed in its own shift, so it is penalized twice.

### 2.1 Replay of the 50-aircraft, 14-day simulation (read-only, `offplan_replay.py`, output `OR50.json`)

| Measure | Value |
|---|---|
| Team-shifts | 1,302 (42 shifts) |
| Team-shifts with any off-plan work | 249 (19%): pull-ahead 169, deviation 23, dispatched-but-not-frozen 90 |
| Off-plan volume | 29,305 spm = 0.47% of verified output (pull-ahead 20,810; dispatched-not-frozen 7,070; deviation 1,425) |
| Current *H* | **= 1 on all 1,053 team-shifts without off-plan work; < 1 on exactly the 249 with it** (minimum 0.875) |
| Points lost to off-plan work under the current formula | mean 0.84, median 0.72, maximum 3.75 |
| Teams that only pulled work ahead | 160 team-shifts, mean 0.97 points lost |
| Teams penalized for following the latest plan | 90 team-shifts (7,070 spm in U); plus 765 spm of frozen work the latest plan moved out, charged as unexcused misses (7 team-shifts) |
| Replay equals stored score | max difference 0.05 (rounding) |

- **The adherence term measures only off-plan work.** If off-plan work no longer counts toward adherence, today's *H* becomes the constant 1, and its 30% weight becomes 30 free points (already flagged in `05` §2).
- **The synthetic floor is too forgiving to show grade swings.** All 1,302 team-shifts grade A, mean utilization is 56% and Cp/D is 99.2%. The direction of every effect is clear; the magnitudes would be larger on a real floor.

### 2.2 Worked scenarios (`offplan_scenarios.py`, output `OS.json`, exact fractions)

Plan 2,100 spm; a 5-hour task is 300 spm. Rules:
- **R0** current;
- **R1** off-plan ignored (worth zero, no penalty);
- **R3** flat credit δ inside attainment, no classification;
- **R5** recommended: output attainment *A′* = min(1, (Cp + U_add)/D) at 0.7, plan attainment *H′* = min(1, Cp/D) at 0.3, dispatched work treated as planned.

| Scenario | R0 | R1 | R3 δ=½ | R3 δ=1 | R5 | R5 output index | R5 points |
|---|---|---|---|---|---|---|---|
| S0 completes plan, idles 5 h | 100.0 A | 100.0 A | 100.0 A | 100.0 A | 100.0 A | 1.000 | 2,100 |
| S1 completes plan, pulls a 5 h task ahead | **96.3 A** | 100.0 A | 100.0 A | 100.0 A | 100.0 A | **1.143** | **2,400** |
| S2 late part blocks 5 h (relieved), idles | 100.0 A | 100.0 A | 100.0 A | 100.0 A | 100.0 A | 1.000 | 1,800 |
| S3 late part blocks 5 h (relieved), fills 5 h | **95.7 A** | 100.0 A | 100.0 A | 100.0 A | 100.0 A | 1.167 | 2,100 |
| S4 own predecessor blocks 5 h (not relieved), idles | 90.0 A | 90.0 A | 90.0 A | 90.0 A | 85.7 B | 0.857 | 1,800 |
| S5 own predecessor blocks 5 h, fills 5 h | **85.7 B** | 90.0 A | 95.0 A | 100.0 A | **95.7 A** | 1.000 | 2,100 |
| S6 swaps a ready 5 h planned task for 5 h unplanned | 85.7 B | 90.0 A | 95.0 A | **100.0 A** | 85.7 B | 0.857 | 1,800 |
| S7 swaps a ready 2 h planned task for 5 h unplanned | 92.0 A | 96.0 A | **100.0 A** | **100.0 A** | 94.3 A | 0.943 | 1,980 |
| S8 skips a ready 5 h planned task, idles | 90.0 A | 90.0 A | 90.0 A | 90.0 A | 85.7 B | 0.857 | 1,800 |
| S9 follows the latest plan: 5 h moved out, 5 h in | **85.7 B** | 90.0 A | 95.0 A | 100.0 A | **100.0 A** | 1.000 | 2,100 |

Readings:
- **R0 punishes all three helpful cases and the plan-following case.** It also charges a swap (S6, −14.3) more than skipping and idling (S8, −10.0), so a swap is penalized twice.
- **R1 (worth zero)** removes the penalty but gives no reason to fill idle time.
- **Flat credit without classification is exploitable.** At δ = 1 an equal swap costs nothing (S6 = 100). At δ = ½, swapping a 2-hour planned task for a 5-hour unplanned one is free (S7 = 100).
- **R5** pays for filling blocked time (S5 − S4 = +10.0) and is neutral for following the plan (S9 = S0). A swap costs exactly what skipping costs (S6 = S8): one penalty. Beyond the plan, idle-time work shows in the output index and points, not the grade.
- **Calibration.** R5 also changes the scale. Without off-plan work, R5 equals plan attainment × 100, while R0 is 70 × attainment + 30. So R0 grades S4 and S8 at A (90.0), and R5 at B (85.7). That comes from removing the 30 free points, not from off-plan credit (§7, rebuttal 1).

## 3. Three kinds of off-plan work need three answers

| Kind | What happened | Identifiable in production? | Simulation ground truth | Recommended treatment |
|---|---|---|---|---|
| **Dispatched, not frozen** | The latest accepted plan assigned it in this shift; the day's frozen goal did not include it | **Exactly.** The plan of record names it. `assess_addition` already accepts it with basis `ASSIGNED_IN_ACCEPTED_PLAN` (`amendments.py:102-105`) | `outside_goals` (`execute.py:572-580`) | Planned work: add it automatically, credit 1:1, never U. Relieve the frozen units the same plan moved out (cause "plan change"). *Superseded by the owner's per-shift plan of record (§7): it is then simply in the shift's goal.* |
| **Additive** | Done in idle time, or in time freed by blocked planned work; no ready planned unit left undone | **By evidence** (§6), or ex ante by an approved addition with basis `QUALIFIED_POOL_HEADROOM` (`amendments.py:106-126`: headroom after planned duties) | `pull_ahead` (`execute.py:437-452`) | 1:1 output credit; in the grade at weight w through output attainment; not in plan adherence |
| **Substitutive** | Done instead of a ready, dispatched planned unit | By evidence (§6): an unexplained miss of a ready, dispatched unit in the same shift | `deviation` (`execute.py:403-407`) | No credit. The displaced unit is a miss, charged once |

**The goal of record and the dispatched plan disagree.** Goals freeze from the day's first accepted plan; the floor works the latest one (`execute.py:5-12`). A team that follows dispatch is charged on both sides:
- work moved in lands in U and lowers *H*;
- frozen work moved out is an unexcused miss and lowers *A*.

S9 shows the cost: −14.3 points, A to B. This holds regardless of how off-plan credit is decided.

**Late planned work** (`LATE_FOR_GOAL`) is a fourth production reason that the simulation does not generate. Classify it the same way: additive when done in spare time, substitutive when it displaced today's ready plan.

## 4. What an unscheduled minute is worth: four measurements

### 4.1 Delivery dollars (OV5, `offplan_value.py`)
Five aircraft, one FORECAST plan; all 5 aircraft were projected late.
- **Pull-ahead.** Marking a ready job (placed 1–9 shifts ahead) complete now: **59 of 60 sampled jobs move no delivery** ($0). One job is worth **$159,809** ($850 per spm), because it sat on a critical chain.
- **Scheduled work.** Slipping a planned first-shift unit by one shift: **$0 for all 60** sampled units.

Delivery dollars cannot set the exchange rate: the ratio is 0/0 almost everywhere, with rare large exceptions. Those exceptions are the case for priority classes (`03`, model G), not for a scheduled premium. On these data the average pulled-ahead job was worth more than the average scheduled unit.

### 4.2 Production (paired floor scenarios, `offplan_paired.py`, output `PAIRED.json`)

**Setup.** 10 aircraft (234 mechanics per shift), 12 shifts. The floor works the EXECUTABLE plan; no forecast is planned. Every run starts from the same synthetic fleet and plans with the same engine. Only the floor's behavior and its random seed differ:
- **N0:** no off-plan work (deviation 0, pull-ahead 0);
- **PA:** every team with at least 2 idle hours pulls up to 2 ready tasks from its next 10 hours forward (pull-ahead 1.0);
- **DV:** every team swaps one ready dispatched task for a ready task from its next 10 hours (deviation 1.0).

Two floor seeds (s6, s7) per scenario.

| Effect against N0 (same seed) | PA s6 | PA s7 | DV s6 | DV s7 |
|---|---|---|---|---|
| Off-plan minutes added | 30,440 | 31,296 | 16,035 | 15,181 |
| Ready planned minutes displaced | — | — | 35,635 (326 swaps) | 36,445 (325 swaps) |
| Planned work completed (Cp) | **+36,919** | **+28,677** | −27,795 | −27,826 |
| Total verified output (Cp + U) | **+67,359 (+9.9%)** | **+59,973 (+8.8%)** | −11,760 (−1.7%) | −12,645 (−1.8%) |
| Worked hours (mean utilization) | +1,124 (61% → 67%) | +1,005 (62% → 68%) | −113 | −187 |
| Open work at the last cutoff | −72,136 | −56,947 | +6,465 | +7,228 |
| Jobs completed | **+544** | **+501** | −42 | −70 |
| Other teams' work blocked (approved cross-team relief) | −540 | +610 | **+6,965** | **+7,280** |
| **Output per off-plan minute** | **+2.21** | **+1.92** | | |
| **Output per displaced minute** | | | **−0.33** | **−0.35** |
| **Other teams blocked per displaced minute** | | | **0.195** | **0.200** |

**Readings.**
- **Pull-ahead turned idle capacity into progress at better than 1:1.** The extra hours came from idle time. Finished predecessors made successor work ready sooner, so later plans could load more planned work: predecessor misses fell from 16,800 to 12,405 spm in s6.
- **Substitution cost more than the minutes it moved.** The displaced work's successors waited: predecessor misses rose to 24,995 and 21,110 spm, and other teams were relieved for 0.2 minutes of blocked work per displaced minute.
- **The scores, rule by rule:**

| Run | R0 mean | R5 mean (w = 0.7) | R0 grades | R5 grades |
|---|---|---|---|---|
| N0 s6 / s7 | 98.63 / 98.91 | 98.26 / 98.56 | A 357, B 13, D 1 / A 359, B 11, C 2 | A 351, B 14, C 5, F 1 / A 353, B 13, C 5, D 1 |
| PA s6 / s7 | **97.88 / 97.89** | **99.39 / 99.32** | A 360, B 9, C 3 / A 359, B 12, C 1 | A 364, B 8 / A 368, B 3, C 1 |
| DV s6 / s7 | 94.05 / 94.35 | 92.90 / 93.16 | A 316, B 48, C 8 / A 335, B 33, C 4 | A 305, B 53, C 12, D 2 / A 311, B 51, C 8, D 2 |

  **Under today's formula the most productive floor (PA) scores below the floor that did nothing extra (N0), in both seeds.** Under R5 the order matches production: PA > N0 > DV.
- **Caveat.** The simulated pull-ahead is always the team's own next planned work, the scheduler's next choices, so it is well aimed by construction. The floor is capacity-rich (61% utilization), so readiness, not labor, is the binding constraint. Where labor binds there is little idle time and little additive work, so the question matters less.

### 4.3 Priority class (shadow run SH10, `shadow_offplan_classes.py`, output `SH10.offplan_classes.json`)

Each off-plan completion at shift *k*, looked up in the classes of publication *k* (model G, `06` §2.7), effort-weighted:

| | FLOAT | AT_RISK | Outside the 24-hour window |
|---|---|---|---|
| Planned goal units | 64.9% | 10.2% | 24.9% |
| Pull-ahead | 57.9% | **19.1%** | 23.0% |
| Deviation (work swapped in) | 26.8% | 26.8% | 46.3% |

- Pulled-ahead work is at least as "important" by class as planned work. No unit of either kind was ever CRITICAL in 15 publications.
- By class, nothing supports valuing a pulled-ahead minute below a planned one. T8 also showed that classes did not predict delivery movement on this fleet (`06` §2.7), so this is weak evidence either way.

### 4.4 Knock-on cost of substitution
In the 50-aircraft run, the 69 swaps displaced 4,455 spm of ready planned work:
- other teams' missed units citing a displaced unit as the blocker: **160 spm across 4 units**, all approved as relief;
- same-team follow-on misses: 55 spm across 2 units.

That is **4.8% of the displaced effort**. Of the 69 swaps, 46 pulled in a frozen unit of a later shift the same day. Those count as early completions of that later goal (`execute.py:7-10`), so only 1,425 spm of swapped-in work appears in U.

In the heavy-swap scenario (§4.2), the knock-on cost was larger and consistent across both seeds:
- other teams were relieved for **0.20 minutes of blocked work per displaced minute**;
- total output fell **0.33–0.35 minutes per displaced minute**.

In the simulation, each swap replaces the team's first workable task of the shift (`execute.py:403-407`), so its successors wait the whole shift.

The simulation does not model shared work zones, tooling, inspector bookings, part kitting or the planning value of a reliable plan. The measured knock-on cost is therefore a **lower bound**.

## 5. Incentive tests: a swap must never pay

Score change against the baseline scenario (positive = the behavior pays):

| Check | R0 | R1 | R3 δ=½ | R3 δ=1 | R5 |
|---|---|---|---|---|---|
| Pull ahead when idle pays (S1 − S0) | **−3.75** | 0 | 0 | 0 | 0 grade, +300 points |
| Fill after a relieved block pays (S3 − S2) | **−4.29** | 0 | 0 | 0 | 0 grade, +300 points |
| Fill after an unrelieved block pays (S5 − S4) | **−4.29** | 0 | +5.0 | +10.0 | **+10.0** |
| Equal swap never pays (S6 − S0) | −14.29 | −10.0 | −5.0 | **0** | −14.29 |
| Swap costs no more than skipping (S6 − S8) | −4.29 (twice) | 0 | +5.0 (pays) | +10.0 (pays) | 0 |
| 2 h → 5 h swap never pays (S7 − S0) | −7.95 | −4.0 | **0** | **0** | −5.71 |
| Following the latest plan is neutral (S9 − S0) | **−14.29** | −10.0 | −5.0 | 0 | 0 |
| Equal swap mistaken for additive (S6′ − S0) | −14.29 | −10.0 | −5.0 | **0** | −4.29 |
| 2 h → 5 h swap mistaken for additive (S7′ − S0) | −7.95 | −4.0 | **0** | **0** | −1.71 |

- **Why w < 1.** In R5 a scheduled minute earns 100/D in the grade and an additive unscheduled minute 100·w/D (below the cap), so the ratio is 1/w. For a team already below plan, a swap the classifier misses pays only if the unplanned task is more than 1/w times the planned one: 1.43× at the demo w = 0.70, 1.11× at w = 0.90. At w = 1, every undetected equal swap is free. **The premium is the insurance against undetected substitution.**
- **Why w > 0.** At w = 0, filling blocked time earns nothing in the grade (R1). The owner's objection applies.
- **What value alone would suggest.** No premium: additive minutes were worth 1.9–2.2× their content in output (§4.2), and delivery dollars say nothing (§4.1). On value alone w would be 1 or more. The premium exists only to make undetected swaps unprofitable.
- **What the premium can and cannot protect.**
  - **A team otherwise at full plan:** an undetected swap still costs (1 − w)·e/D, because output attainment is capped at 1 and plan attainment drops.
  - **A team already below plan:** an undetected swap pays when the swapped-in task is more than 1/w times the displaced one. In the 50-aircraft run, **91% of planned units had a same-team task more than 1.43× larger in the same team-shift**, and 82% one more than 2× larger.
  - So against **deliberate** gaming by teams that are behind, the premium is weak. **Detecting substitution (§6) is the real protection**, and w only needs to cover the residual risk.
- **Choosing w is an owner decision.** Base it on the production classifier's measured miss rate (§6).

## 6. Classifying off-plan work in production

Two production-feasible rules, tested against the simulation's ground truth on the 50-aircraft run (spm):

| Rule | Pull-ahead (truly additive) called additive | called substitutive | Deviation (truly substitutive) called substitutive |
|---|---|---|---|
| **P1** additive iff the team has no unexcused shortfall (Cp ≥ D) | 12,900 (62%) | **7,910 (38%)** | 1,425 (100%) |
| **P2** additive iff the team has no **unexplained** miss of a ready, dispatched unit | 19,690 (95%) | 1,120 (5%) | 1,425 (100%) |

- **P1 is too blunt.** Its errors come from shortfalls with a recorded, non-relieved cause: same-team predecessor 4,840 spm, rework 1,095, deviation 540, overrun 100. A blocked team that filled its time is wrongly treated as swapping.
- **P2 must look at dispatched work, not only frozen goal units.** In the heavy-swap scenario, P2 restricted to goal units missed **15% of swapped-in minutes** (2,375 of 15,555 and 2,095 of 14,235): those swaps displaced dispatched work that was not in the frozen goal. **P2 over all dispatched assignments** classified every minute correctly in all six paired runs (54,960 additive and 29,790 substitutive minutes).
- **P2 needs evidence FOCUS already records:** dispatch membership, predecessor state at the planned start, part records and attendance (the simulation's claim logic uses the same facts, `execute.py:587-639`). Its 5% "errors" are team-shifts that both swapped and pulled ahead, so P2 labels the whole shift substitutive. A per-task timing rule ("did this task start while a ready dispatched unit sat untouched?") would separate them.
- **The simulation flatters any classifier.** Its only substitution is a clean swap of one ready unit. A real floor can substitute more quietly: planned work done slowly to make room, or a recorded but avoidable cause. Production accuracy is unknown until it is measured, and it should decide w.

## 7. Adherence: the owner's position and the rebuttal

**Position:** off-plan work should not count toward adherence. **Verdict: agree.** In R5 it neither raises nor lowers plan adherence. A swap is still caught, because the displaced unit lowers plan attainment, so no adherence penalty is needed.

**Rebuttal points to settle before finalizing:**
1. **Today's adherence term measures only off-plan work** (§2.1). Take off-plan work out and it is a constant 30 points. The attainment each band needs then falls to A 85.7%, B 71.4%, C 57.1% and D 42.9% (`05` §2). Either drop the term, or give "adherence" a real meaning:
   - **plan attainment** (R5): a share of the plan;
   - or **on-time and in-sequence** completion of planned work. That is what "schedule adherence" means in workforce management; OOS is already charged separately.

   Either way the bands must be re-derived (OD-16 is open).
2. **Unmodeled shared resources.** Off-plan work can consume a work zone, tooling, an inspector or a kitted part that the plan allocated to another team. FOCUS's precedence and part checks do not see this. Credit only work that passes the same readiness checks an amendment does (`amendments.py:69-101`), and let affected teams claim relief citing off-plan work.
3. **Rework risk.** Earned-schedule practice keeps out-of-sequence work out of schedule adherence and treats it as a rework warning: Lipke's P-factor counts as on-schedule only the earned value that conforms to the baseline (see Sources). In practice, measure the rework rate of off-plan work against planned work. If it is higher, lower w for additive work by that difference.
4. **Off-plan volume is a planning signal.** Teams pulling work ahead means the plan under-loaded them; in the simulation, pull-ahead teams still had about 50 idle hours after pulling (§2.1). Penalizing off-plan work teaches teams to hide it. Report it to planners instead.

**Owner clarification (2026-11-02):** "Adherence should be counted if the same job is worked as what's in the plan. We do not need to judge against when it was started and/or completed vs plan."
- **Adherence therefore means job identity: the planned jobs done**, net of excused ones. That is R5's plan-attainment term *H′* = Cp/D. The on-time and in-sequence option in rebuttal 1 is dropped.
- **What FOCUS already does.** It matches by job, not by clock time within a shift. A planned unit earns when its verified completion falls anywhere in its shift window, in the authorized overtime window after it, or earlier in an already-frozen goal of the same production day (`lifecycle.py:652-663`; policy `observations.planned_credit_window` = `SHIFT_WALL_PLUS_AUTHORIZED_OT`; `goals.obligation_days` = 1). Actual start and finish times are never compared with the planned ones.
- **What it does not do yet:**
  - Today's *H* = Cp/(Cp+U) measures the share of output that was planned, so it moves with off-plan work, not with "did the team work the planned jobs".
  - **A planned job finished in a later shift than planned** counts as missed in its planned shift (unless excused), and as off-plan (`LATE_FOR_GOAL`, `lifecycle.py:796-818`) in the shift that finished it. Under the owner's rule, it should count as planned work where it is finished. The planned shift still shows it as not done that shift: shift scores close at the end of the overtime window and are not revised later.
- **Kept separately:** the out-of-sequence charge (O, 10% of the affected effort for a discretionary start before a predecessor is verified complete). It judges order against other jobs, not times against the plan.

**Owner direction (2026-11-02):** "A planned job in shift 1 which isn't done in shift 1 is not adhered to. At the end of shift 1, a new scheduler runs to set shift 2 plan. That job should appear or may not appear in shift 2 plan due to constraints."
- **Each shift is measured against the plan published for it.** That plan comes from the run at the end of the previous shift (the D-22 shift cycle).
- **Today goals freeze per production day, not per shift.** The first accepted EXECUTABLE plan covering a day freezes every team-shift goal of that day (`lifecycle.py:166-185`; `goals.obligation_days` = 1, `serializer.py:72-76`). Later plans that day are stored only as proposals (`later_goal_ids`). The floor works the latest plan, but shifts 2 and 3 are scored against the morning plan. The simulation mirrors this (`execute.py:5-12`, `freeze_day`).
- **To follow the owner's model, goals of record must freeze per shift:** from the last accepted EXECUTABLE plan accepted at or before that shift's start. If that run fails, fall back to the latest accepted plan covering the shift. **Implemented as D-26** (`docs/execution/DECISIONS.md`) in `GoalRegistry`, the score, amendments, the twin, rollups and the simulation, with tests.
- **Measured after implementation** (`PERSHIFT.json`; same floor behavior and seed, identical work):
  - With no pull-ahead, offplan U fell from 2,335 to 0 standard minutes and predecessor-blocked misses from 16,800 to 635.
  - With heavy pull-ahead, all pulled-ahead work is offplan (80,302 minutes, against 27,850 when later shifts' frozen units took early credit). Today's *H* penalty grows accordingly (mean 96.3 against 97.9), and R5 scores the same floor 99.9.
  - **Per-shift goals make the off-plan rule (§8) more important, not less.**
- **Consequences under per-shift freezing:**
  - A job missed in shift 1 is not adhered to in shift 1, unless excused.
  - If the new run places it in shift 2 and it is done there, it is adhered to in shift 2. If the new run leaves it out and the team does it anyway, it is off-plan in shift 2: output credit, not adherence.
  - Work the new run moves out of shift 2 is simply not in shift 2's goal. The "plan change" relief of §8 is no longer needed.
  - Work the new run moves in is in shift 2's goal, so the automatic-addition workaround of §3 and §8 is no longer needed either.
  - A job pulled into shift 1 from a later shift's plan is off-plan output in shift 1. It is no longer an early completion credited to the later shift.
- **Size on the 50-aircraft simulation.** The day-level freeze is what produced 7,070 spm of plan-following work counted as off-plan (90 team-shifts) and 765 spm of unexcused misses for work the plan itself moved (7 team-shifts) (§2.1). It also credited 46 of the 69 swaps as early completions of later shifts (§4.4).

**Where counting off-plan work in adherence would be right:** only if FOCUS could not detect substitution at all. Then the *H* penalty is a crude insurance against swaps, and it is the insurance that today also taxes helpful work. R5 provides the insurance through w instead.

## 8. Recommendation and owner decisions

**R5, for evaluation:**
1. Dispatched-not-frozen work is planned: add it automatically, credit it, and relieve frozen work the plan moved out.
2. Approved additions stay the primary route for extra work; they count 1:1.
   - *Design detail found.* Under R5, an approved addition (in B and in Cp) can score **below** the same work done unapproved and classified additive. Scenario S5 with approval scores 87.5, against 95.7 unapproved.
   - Fix: count approved pull-ahead additions as additive output, and keep true emergency additions as obligations. Approval must never score worse than not asking.
3. Unapproved off-plan work is classified by P2 over all dispatched assignments (per task where possible).
   - additive: 1:1 in output and points, and in the grade at w through output attainment;
   - substitutive: no credit.
4. Plan attainment is unaffected by off-plan work.

**Owner decisions:**
- **Decided (2026-11-02): adherence = the planned jobs worked, with no timing test** (§7, owner clarification).
- **Decided (2026-11-02): each shift is measured against the plan published for it.** A job not done in its shift is not adhered to there. Whether it counts in the next shift depends on whether the next run plans it. Implementing this means freezing goals of record per shift instead of per day (§7, owner direction). That replaces R5 item 1 and the "plan change" relief.
- **w, the exchange rate.** The demo 0.70 gives a scheduled premium of 1.43×. Value alone suggests no premium (w = 1); detecting substitution is the main protection. Start at 0.80, which means a scheduled 5-hour task earns 1.25× an additive unscheduled one in the grade. Move toward 0.90 once the production classifier's miss rate is measured and low. *This is a recommendation, not a fact.* **Calibrated in `10_CALIBRATION.md`:** R5 tracks production best at w = 0.6–0.8, and at w = 0.8 a swap pays only when the classifier misreads it (0.09% of swap team-shifts at a 15% misread share, by at most 0.09 points). The recommendation stays 0.80 (0.70 for zero tolerance).
- **Bands** for a score without free adherence points. The simulation cannot set them (`10` §6): they are tolerances on unexcused shortfall, to be set on real data with `tools/calibrate_scores.py` (`10` §8).
- **Grade cap.** Extra output shows in the output index and points, not above 100.
- **Approved additions:** obligation or pre-classified additive output.
- **Approval to implement R5** (the off-plan credit rule). The per-shift goals of record were implemented on the owner's direction (D-26); R5 has not been.

**Not recommended:**
- flat credit without classification: an equal swap is free at δ = 1;
- any credit above 1 in the grade, even though the simulation valued additive minutes at 1.9–2.2×. That value comes from timing and readiness, which a per-minute grade cannot measure fairly; it belongs in planning reports, not team grades;
- keeping the current *H*, which taxes every helpful off-plan minute;
- a fixed discount derived from delivery dollars, which are 0/0 on these data.

## 9. Limitations
- **Synthetic floor.** Off-plan work is always near-term planned work (the next 10 hours, `execute.py:312-315`), so its content value is high by construction. The swap is a clean one-unit swap. Rework from off-plan work is not modeled. Utilization is low (56%).
- **Small samples.** The paired scenarios ran two seeds each, over 12 shifts at 10 aircraft, with extreme settings (every idle team pulls ahead; every team swaps once a shift) to make the effects measurable. Both seeds agree closely. The 50-aircraft replay had 23 swaps recorded as off-plan rows (69 displaced units).
- **Not owner-approved.** The weights and bands are demonstration values.
- **No real data.** Real validation needs weeks of floor data with verified completions, claims and dispatch history (OD-15).

## 10. Evidence

| Run | Command (from the app root virtualenv; scripts in `evidence/scripts/`) | Output |
|---|---|---|
| OR50 | `python offplan_replay.py runtime/sim/fleet.db OR50.json` (read-only replay of the 50-aircraft, 14-day run of 2026-10-31) | `evidence/outputs/OR50.json` |
| OS | `python offplan_scenarios.py OS.json` | `evidence/outputs/OS.json` |
| OV5 | `python offplan_value.py 5 5,8 OV5 --sample 60 --late-share 0.02 --late-max 3 --procs 1` | `evidence/outputs/OV5.json` |
| PAIRED | `python offplan_paired.py 10 5,10 12 {N0,PA,DV}_s{6,7} --deviation {0,0,1} --pull-ahead {0,1,0} --seed {20261026,20261027} --late-share 0.02 --late-max 3`; `python offplan_paired_analyze.py PAIRED N0_s6 PA_s6 DV_s6 N0_s7 PA_s7 DV_s7` | `evidence/outputs/PAIRED.json` |
| SH10 classes | `python shadow_sim.py 10 5,10 5 SH10 --late-share 0.02 --late-max 3`, then `python shadow_offplan_classes.py SH10` | `evidence/outputs/SH10.offplan_classes.json` |
| Swap sizes | inline query on `runtime/sim/fleet.db` (`perf_unit`, original units): share of units with a same-team, same-shift unit more than r× larger | quoted in §5 (r = 1.11: 94%; 1.25: 92%; 1.43: 91%; 2.0: 82%) |

**Sources.**
- W. Lipke, "Schedule Adherence and Rework", PM World Today, July 2011 ([earnedschedule.com](https://www.earnedschedule.com/Docs/Schedule%20Adherence%20and%20Rework%20-%20PMWT%20(July%202011).pdf)).
- W. Lipke, "Schedule Adherence", CrossTalk, Nov–Dec 2012 ([earnedschedule.com](https://www.earnedschedule.com/Docs/Schedule%20Adherence%20-%20CrossTalk%20(Nov-Dec%202012).pdf)).
- The P-factor has seen little adoption by researchers or practitioners, as a review notes ([Vlerick repository](https://repository.vlerick.com/entities/publication/5860c507-0ec8-458b-8985-47d6711804f7)). It is cited here as a precedent for the structure (earned output kept apart from schedule adherence), not as an industry standard.
