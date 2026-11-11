# 12 — A score for a floor that chooses its own work

**Context (owner, 2026-11-11).** Mechanics should work jobs 1 to 10. They miss some because of poor planning, for
valid reasons, or because they do not know what to prioritize and work easy, low-priority jobs instead of the right
ones. The culture counts jobs. A job count is a bad metric because jobs are not equal: some take several people, some
are long, some have no slack, some have nothing after them. The owner also asked whether adherence plus throughput
corrects itself, since working non-critical jobs helps throughput and hurts adherence.

**Source revision:** `352555e` plus the code named in §10. All data is synthetic. No real team has been scored.
Nothing here changes a score: the canonical score stays the grade of record until the owner decides (§9).

## 1. Answer

1. **Stop counting jobs. Count the planned effort that got done.** On simulated floors every crew left about a
   quarter of its planned effort each shift and did easier ready work instead. Jobs completed went **up** 1% to 3%.
   Standard minutes stayed within 1.5%. Three teams in four completed more tasks than the same team did when it
   worked its plan. A job count and a throughput figure both say those floors are as good as the floor that worked
   its plan, or better.
2. **Adherence plus throughput is the right shape, and it is the score FOCUS already has**: 80% throughput against
   the plan and 20% adherence. It corrects in the right direction and far too weakly. Those floors scored **93**. All
   of the correction comes from the adherence part (§3).
3. **Which planned job is left matters, and only a priority can see it.** Two floors left the same amount of work.
   One left jobs that other planned work was waiting on. The other left jobs that nothing planned was waiting on. The
   first blocked 13,300 minutes of other production teams' work, the second 955 (12,000 and 860 on the second seed).
   Every measure without priorities gave the two floors the same result.
4. **Team by team, only the measures with priorities follow the harm** (§4.4). Lowering the credit for off-plan work
   makes leaving the plan cost more. It does not make the score better at finding the teams that did the damage.
5. **The proposal** (§5; it exists as a tested tool, and today's score is its special case):
   - a planned job counts by its **standard person-minutes**, times **2** when other planned work waits on it and
     **½** when nothing depends on it;
   - the score is the points done out of the points expected, with excused work taken out;
   - work done **in place of** planned work that was left without an excuse earns **30%** of it, not 80%;
   - beside the score: the must-do jobs done (11 of 12), work beyond the plan, and three guardrails.
6. **Three of my earlier conclusions change on the floor the owner describes.** One of them was a recommendation
   the owner acted on (§2).
7. **The simulation could not show a delivery cost** (§4.5). The harm it shows is other teams' work being blocked,
   about 1% of the planned minutes. The case that the wrong jobs also cost deliveries rests on the owner's knowledge
   of the real floor, not on this evidence.

## 2. What changes in my earlier advice

| Earlier conclusion | On a floor that chooses its own work | Why |
| --- | --- | --- |
| "Extra work should earn 80%" (my recommendation of 2026-11-10, confirmed by the owner, D-27) | **Too generous. Lower it.** | The 80% only ever applies to work done in place of planned work left without excuse (§3). Where that is rare it costs little. Where it is the habit, it prices a left job at 20% of its value. Report `10` named this case: lower the rate if swapping proves material |
| "Weighting jobs by a priority class changes nothing" (report `06` T9, report `08`, `AW50`) | **Wrong when crews choose.** | It was measured on floors that missed work for reasons unrelated to the job. When the choice of what to leave is deliberate, two floors that the unweighted score cannot tell apart are 13 points apart with weights (§4.3) |
| "Job importance must not enter the grade" (report `08`) | **Holds for dollar values and for totals. Not for a bounded tier in a ratio.** | The objections were that impact dollars do not add up, that weighted totals measure what a team was assigned, and that waiting makes a job worth more. The proposal uses neither dollars nor totals: a bounded tier, frozen at acceptance, inside a ratio of done to expected points. On the floor where nobody chooses it moved no team by more than 0.6 points (§4.4) |
| "Priority classes carry no signal on this fleet" (report `08`, T8) | Holds for the delivery class. The tier here is a different fact | Report `08`'s class asked whether a job has float to delivery. Every near-term job had. The tier asks whether planned work waits on the job. That holds for 24% of planned minutes and it follows blocked work |
| "Do not compare on throughput or a sum of job points" (report `11`) | Holds, more strongly | Both rose on the floors that left their plan |
| "The score is the right core" (report `11`) | Holds for the formula, not for its two settings | Today's score is the proposal with a credit of 80% and no priorities |
| The owner's earlier idea: jobs worth points by criticality, off-plan jobs worth less | **Right, as a ratio** | I answered the "sum of points" form and did not separate the two clearly enough |

## 3. Is adherence plus throughput self-correcting?

Today's score, written the way the owner put it (D = expected work, Cp = planned work done, U = other work done):

    score = 80% × throughput against the plan + 20% × adherence
          = 0.8 × min(1, (Cp + U) ÷ D) + 0.2 × (Cp ÷ D)
          = (Cp + 0.8 × min(U, D − Cp)) ÷ D

The last line is the same formula rearranged: **the planned work done, plus 80% credit for other work that fills the
gap left by planned work that was not done and not excused.** Four things follow.

- **The correction is exactly the adherence weight.** Swapping one planned hour for one other hour costs 20% of an
  hour today. That is the whole penalty for working the wrong job.
- **It reverses if throughput is counted in jobs.** Leave one long job, do three short ones: adherence falls by one
  job and throughput rises by two. Only effort units make throughput neutral.
- **It can reverse where standards are uneven.** A crew that will not finish its plan gains by spending an hour on
  an easy job when that job's standard is more generous than the planned job's by more than 1 ÷ credit:

  | Credit for other work | The easy job pays when its standard is more generous by |
  | --- | --- |
  | 80% (today) | 1.25 times |
  | 50% | 2 times |
  | 30% | 3.3 times |
  | 30%, and the planned job is a must-do counted double | 6.7 times |

- **It cannot tell which planned job was left.** A two-hour job that three other jobs wait on and a two-hour job with
  nothing after it cost the same.

**A count against effort, on one shift.** Ten planned jobs: two long ones (4 hours, 2 people) and eight short ones
(1 hour, 1 person). The crew does the eight short ones and six other short jobs.

| Measure | Result |
| --- | --- |
| Job count | 14 jobs against 10 planned: "140%" |
| Planned effort done | 8 of 24 person-hours: **33%** |
| Today's score | 53.3 |
| Credit at 30% | 40.8 |
| Credit at 30%, and the two long jobs were must-do jobs | 24.5 |

So adherence plus throughput corrects itself only when three things hold: both are in effort units, the credit for
other work is well below the planned work, and planned jobs carry a priority. With those three it is the proposal of
§5.

## 4. The experiment: floors that choose their own work

### 4.1 Setup

Ten aircraft, 30 production teams, 18 shifts (six days), the delivery forecast planned every shift, two seeds. Every
floor starts from the same fleet. On three of the four, every production team leaves about a quarter of its
dispatched effort undone each shift, by choice and with no excuse. It spends that time on the shortest ready tasks
the plan has for the ten hours after the shift, about two for each task it leaves. The floors differ only in which
planned tasks are left:

| Floor | What each crew leaves | Must-do share of the effort left (s6, s7) |
| --- | --- | --- |
| Plan | Nothing: it works its plan | — |
| Hardest | The tasks with the most person-minutes (easy work first: the count culture) | 26%, 28% (near the plan's own mix) |
| Gating | First the tasks that other planned work of this shift or the next waits on | 88%, 86% |
| Non-gating | First the tasks that nothing planned waits on | 4%, 4% |

"Waits on" comes from the dependency graph and the accepted plan (`focus/valuation/priority.py`). In the simulated
plans 24% of planned minutes were must-do work (other planned work waited on them), 75% had dependents planned later,
and under 1% had no dependents at all.

### 4.2 What happened (seed s6)

| Floor | Planned effort left by choice | Jobs completed | Standard minutes | Other production teams' work blocked | Inspections blocked | The team's own later work blocked |
| --- | --- | --- | --- | --- | --- | --- |
| Plan | 0 | 9,015 | 1,007,344 | 635 min | 225 min | 2,020 min |
| Hardest | 27% | 9,182 (+1.9%) | 997,120 (−1.0%) | 6,065 min | 2,835 min | 12,920 min |
| Gating | 27% | 9,092 (+0.9%) | 1,010,839 (+0.3%) | 13,300 min | 1,710 min | 19,075 min |
| Non-gating | 27% | 9,213 (+2.2%) | 1,022,136 (+1.5%) | 955 min | 2,655 min | 11,640 min |

- **The count rose and the standard minutes held** on every floor that left a quarter of its plan.
- **The gating floor blocked 14 times as much of other production teams' work as the non-gating floor**, which
  blocked barely more than the floor that worked its plan.
- **The amounts are small**: 13,300 minutes is 1.3% of the planned minutes. A blocked crew in the simulation found
  other ready work.
- **A task left by choice waited a median of four to five shifts** before it was done. One in ten waited nine shifts
  or more. Of the tasks first left in the first half of the run, 90% to 94% were done by its end.

### 4.3 What each measure said (seed s6, the mean over team-shifts)

| Measure | Plan | Hardest | Gating | Non-gating |
| --- | --- | --- | --- | --- |
| Jobs completed (the count culture) | 9,015 | 9,182 | 9,092 | 9,213 |
| Planned jobs done, by count | 99.6% | 73.3% | 67.6% | 68.5% |
| Planned effort done | 99.6% | 67.6% | 68.4% | 68.3% |
| **Today's score** (credit 80%) | 99.7 | **92.7** | **93.1** | **93.2** |
| Credit 50% | 99.7 | 83.3 | 83.9 | 83.9 |
| Credit 30% | 99.6 | 77.0 | 77.7 | 77.7 |
| Credit 0% | 99.6 | 67.5 | 68.4 | 68.3 |
| Credit 80% with tiers 2 / 1 / ½ | 99.7 | 91.1 | 84.2 | 93.9 |
| **Credit 30% with tiers 2 / 1 / ½** | 99.7 | **76.8** | **67.6** | **81.0** |
| Credit 30% with tiers 3 / 1 / ½ | 99.7 | 76.4 | 59.5 | 83.1 |
| Must-do work done | 99.9% | 73.8% | 19.6% | 97.4% |

- **Today's score barely moved**: 93 for crews that ignore a quarter of the plan every shift.
- **The credit sets the size of the penalty**: at 30% the same floors score 77.
- **Without tiers the gating and non-gating floors are identical** (93.1 and 93.2; 77.7 and 77.7).
- **With tiers the score follows the harm**: non-gating 81.0, hardest 76.8, gating 67.6, in the order of the other
  teams' work blocked (955, 6,065 and 13,300 minutes).
- **"Must-do work done" needs no formula**: 97% against 20%.

The row "today's score" is the stored canonical score: the analysis formula with flat multipliers and a credit of 80%
reproduces it exactly for every team-shift.

### 4.4 Team by team (seed s6)

Every production team on every floor is one row. The table ranks the teams by each measure and by the minutes of
other production teams' work the team blocked. A measure that follows the harm is strongly negative.

| Measure | All four floors (120 team-periods) | The three floors that choose (90) |
| --- | --- | --- |
| Tasks completed | +0.15 | +0.14 |
| Standard minutes | +0.11 | +0.12 |
| Today's score (credit 80%) | −0.39 | −0.11 |
| Credit 30% | −0.33 | −0.02 |
| Credit 0% | −0.32 | 0.00 |
| Credit 80% with tiers 2 / 1 / ½ | −0.72 | −0.70 |
| Credit 30% with tiers 2 / 1 / ½ | −0.71 | −0.68 |
| Must-do work done | −0.73 | −0.70 |

- **Counts and standard minutes point slightly the wrong way.** Against itself on the plan floor, a team completed
  more tasks on the choosing floor in 63 of 90 comparisons.
- **The credit does not improve the ordering.** It changes how much leaving the plan costs every team.
- **The tiers do**, at either credit. So does the plain share of must-do work done.
- **Where nobody chooses, the tiers change almost nothing.** On the plan floor the tiered and the flat score differ
  by 0.04 points for the average team and 0.6 at most, and rank the teams alike (0.97). A weighted ratio moves only
  when the work left is not a fair sample of the plan.

### 4.5 Delivery did not move, and why that does not settle it

Compared aircraft by aircraft with the plan floor, the forecast finish dates of the three floors differed by −0.2 to
+0.3 days in both seeds, with no consistent direction. No effect.

- **The same work got done.** Every job is needed before delivery, and the easy work the crews did instead was work
  the plan had for the next ten hours. The floors reordered work within a day or two.
- **Near-term work has days of float in this fleet.** Reports `06` and `08` measured it: no first-shift job was
  delivery-critical at 5 or 50 aircraft, because the critical chains run through future part arrivals. Report `08`'s
  test T8 found the same: missed at-risk work did not move delivery measurably.

On a real floor the easy jobs may be far from needed, a blocked crew may stand idle, and stations and takt may leave
no float. The simulation models none of that, so it neither proves nor disproves a delivery cost. What it shows is
the measurement problem: three floors that ignore the plan, with rising counts, an "A" score and blocked neighbors.

### 4.6 Second seed (s7)

The same four floors from a different seed.

| | Plan | Hardest | Gating | Non-gating |
| --- | --- | --- | --- | --- |
| Jobs completed | 9,006 | 9,208 (+2.2%) | 9,162 (+1.7%) | 9,274 (+3.0%) |
| Standard minutes | 1,011,639 | 1,003,465 (−0.8%) | 1,017,350 (+0.6%) | 1,026,175 (+1.4%) |
| Other production teams' work blocked | 105 min | 5,855 min | 11,980 min | 860 min |
| Today's score (credit 80%) | 99.5 | 93.0 | 93.0 | 93.3 |
| Credit 30% | 99.4 | 77.0 | 77.2 | 77.9 |
| Credit 30% with tiers 2 / 1 / ½ | 99.4 | 76.7 | 67.2 | 81.1 |
| Must-do work done | 99.7% | 71.3% | 18.7% | 96.9% |

| Team by team, against the work blocked for others | All four floors | The three floors that choose |
| --- | --- | --- |
| Tasks completed | +0.11 | +0.14 |
| Standard minutes | +0.08 | +0.14 |
| Today's score (credit 80%) | −0.49 | −0.20 |
| Credit 30% | −0.47 | −0.17 |
| Credit 30% with tiers 2 / 1 / ½ | −0.75 | −0.67 |
| Must-do work done | −0.78 | −0.71 |

- A team completed more tasks on the choosing floor than on the plan floor in 70 of 90 comparisons.
- On the plan floor the tiers moved no team by more than 0.4 points, and ranked the teams as the flat score did
  (0.98).
- A task left by choice waited a median of four to six shifts.
- Delivery: −0.1 to +0.3 days against the plan floor. On the last shift the hardest and the gating floor were 0.2
  days later than the plan floor (9 aircraft later, 2 earlier). Over the last six shifts they were 0.1 days earlier
  and level. Seed s6 shows the opposite on its last shift, so I read this as noise. It is the only sign of a delivery
  effect in either seed.

Both seeds agree on every point of §4.2 to §4.5.

## 5. The system

### 5.1 The unit: standard person-minutes

A job counts by its standard duration times its crew. This alone answers "some take several people, some are long,
some are short". FOCUS already freezes each planned job's standard person-minutes with the plan. Job counts appear
nowhere.

### 5.2 The plan is the list of right jobs, and each job says what waits on it

The scheduler's plan for the shift is jobs 1 to 10. Each one carries, from the plan and the dependency graph:

| Tier | Meaning | Multiplier (proposed) |
| --- | --- | --- |
| **MUST** | Other work planned for this shift or the next cannot start until this job is done | 2 |
| **PLAN** | Something depends on it, but nothing planned soon | 1 |
| **FILL** | Nothing depends on it | ½ |

- **The dispatch list shows the tier and what waits** ("3 jobs, 2 of them another team's"). That is the answer to
  "they do not know what to prioritize".
- **The tier is frozen when the plan is accepted**, so nobody can raise a job's worth by delaying it.
- **The aircraft's cost of delay (D-29) is shown beside it**, to choose between two must-do jobs.
- **"No slack" is the second half and is not built.** A job with less than a shift of float to its aircraft's
  delivery should also be MUST (report `08`'s class). The synthetic fleet has no such job, so it could not be tested.

### 5.3 The score

    points of a planned job = standard person-minutes × its tier's multiplier
    expected = points of the planned jobs that were not excused
    done     = points of the planned jobs verified complete
    score    = 100 × (done + 30% × min(other work done, expected − done)) ÷ expected − the out-of-sequence charge

In words: **the planned points you completed out of the points you were expected to complete. Excused jobs come off
the expected list. Other work done in place of planned work you left counts at 30 cents on the dollar.**

The owner's example: ten one-hour jobs, eight done, two left, four other jobs done.

| Case | Today | Proposed |
| --- | --- | --- |
| The two left were excused | 100.0 | 100.0 |
| The two left were ordinary planned jobs | 96.0 | 86.0 |
| The two left were must-do jobs | 96.0 | 76.7 |
| The two left were fill jobs | 96.0 | 92.2 |
| The two left were must-do jobs, and no other work was done | 80.0 | 66.7 |

- Doing the right jobs is always best.
- Doing other work is always better than doing nothing (76.7 against 66.7).
- Leaving a must-do job costs three times what leaving a fill job costs.
- Other work never lifts a score above what the plan allows.

### 5.4 What replaces the job count on the board

| Number | Reads as | Role |
| --- | --- | --- |
| **Right-work score** | 86 | The grade |
| **Must-do jobs** | 11 of 12 done, 1 excused | What a lead looks at first |
| **Work beyond the plan** | +6% | Recognition for working ahead; never part of the grade |
| Excused, by cause | 2 h late part, 1.5 h another team | Not the team's result; it goes to whoever owns the cause |

Three guardrails, per week: labor efficiency (standard hours earned ÷ hours worked), rework traced to the team, and
other teams' work blocked by this team's unexcused misses. A crew cannot win the score by rushing, by pushing
problems downstream or on generous standards without one of these moving.

### 5.5 Excuses, poor planning and a plan that is wrong

- **A valid reason** takes the job off the expected list. The team loses nothing.
- **Poor planning** shows as the share of planned effort that could not be worked (parts, another team, capacity).
  That share is the plan's result, not the team's, and it goes to planning, supply and staffing (report `11`, §3.6).
- **A wrong plan** needs a lead who can change it. A change the lead approves before the work starts should count as
  planned work (DEC-26, option b). Without that, a strict score punishes good judgment.

### 5.6 Levels and periods

| Level | Grade | Beside it |
| --- | --- | --- |
| Team, one shift | The right-work score, as information | Must-do jobs done; excused by cause |
| Team, five days or more | The score over the period: sum the points first, then divide | Must-do share, work beyond the plan, the three guardrails |
| Superintendent, business unit | The same, with knock-on effects inside the group charged to the group | The loss by cause and owner; the plan's load; the share of the plan that could not be worked |
| Site | The same | Output per available hour, split into its factors (report `11`, §3.7) |
| One period against another | Factor by factor, against a no-change band | — |

The no-change bands of report `11` were measured for today's score. They widen as the credit falls and must be
measured again for the settings the owner chooses.

## 6. Can it be gamed?

| Route | Under a job count | Under the proposal |
| --- | --- | --- |
| Pick easy jobs over hard ones | Pays | Costs: 30% credit, and a must-do job counts double |
| Do many small jobs | Pays | Nothing: effort units |
| Leave must-do work for the next shift | Free | Costs double, and shows as "must-do jobs 9 of 12" |
| Claim an excuse | — | Needs verified evidence; the excused share per team is on the board |
| Have the lead approve the swap afterwards | — | Approval counts only before the work starts; approvals per lead are reported |
| Rush the work | Pays | Rework traced to the team |
| Work to generous standards | Pays | Labor efficiency shows it; a swap needs a standard 3.3 times too generous |
| Wait until a job becomes urgent | — | Nothing: the tier is frozen at acceptance and a score stops at 100 |
| Lower the target | — | The scheduler writes the plan, not the team |

Two routes stay open and need a person: a lead who approves everything, and standards that are far off for a class
of jobs. Both show in the reports named above.

## 7. What switching it on takes

| Step | What | Size |
| --- | --- | --- |
| 1 | **The credit.** Policy `score.attainment_weight` 0.80 → 0.30 and `score.adherence_weight` 0.20 → 0.70 | Two policy values and their pinned tests. No code |
| 2 | **Approved changes count as planned** (DEC-26, option b) | A scoring rule change |
| 3 | **Tiers.** Freeze each goal's tier at acceptance; carry it through the evaluation, the job record and the rollups; multipliers in policy (`1/1/1` keeps today's result); the simulation; the dispatch view | The larger build |
| 4 | **The board.** Must-do jobs done, work beyond the plan, blocked work caused | Display and one rollup field each |
| 5 | **Pilot beside the job count** for four weeks before any grade counts. Show the flat and the tiered score together. Set the credit and the multipliers from what the pilot shows: how often crews swap, the tier mix of real plans, and whether blocked work follows the tier | Calibration (`tools/calibrate_scores.py` has the swap watch; the tier checks are new) |

## 8. Limits

- **No delivery effect was shown** (§4.5). The tier is tested against blocked work, which is a nearer and smaller
  harm than a late delivery. It is also close to the tier's own definition: the test shows that a tier computed
  before the shift predicts the blocking during it, and no more.
- **The harm shown is small**: about 1% of planned minutes blocked for other teams.
- **The tier is one fact.** It does not include float to the aircraft's due date (§5.2).
- **Fill work was nearly absent** in the simulated graph (under 1% of planned minutes), so the ½ multiplier is
  untested.
- **The multipliers and the 30% are judgments.** The simulation shows their direction, not their right size.
- **Tiers are only as good as the dependency data.** Missing or looped dependencies give wrong tiers. If most real
  jobs turn out to be must-do, the tier separates nothing and its definition must be narrowed.
- **Effort is only as good as the standards.** The efficiency guardrail is there to show where they are off.
- **A strict score makes the plan's quality matter more.** If plans are often wrong and leads cannot change them,
  the score will punish the crews for the planners' errors.
- **Simulated crews** chose by a fixed rule every shift, on every team. Real crews mix reasons.
- **The plans were the scheduler's first pass only.** The improvement search was off to save time, as in the other
  runs made with this folder's scripts. Later checks (`SB`, `EVIDENCE_INDEX.md`) put what the search takes off the
  forecast's late cost at 1% to 2% on the fleet and rates of these runs, and at 24% to 25% on another 10-aircraft
  fleet at the owner's rates (`DECISIONS.md` D-31). Every floor was planned the same way, so the comparisons stand.
  Whether a tighter plan would leave less room to reorder work without a delivery cost was not tested.
- **I built today's score and recommended the 80%.** The tests here were chosen to try to break it.

## 9. Decisions for the owner

1. **The credit for work done in place of planned work:** 30% (recommended, with decision 3), 50% (recommended
   without it), or keep 80%.
2. **Tiers:** multipliers 2 / 1 / ½ in the score (recommended as the pilot setting), must-do jobs shown beside a flat
   score, or none.
3. **Approved changes count as planned** (DEC-26, option b): needed before a strict score is fair.
4. **Working ahead:** shown and recognized (recommended), or a bonus on the score.
5. **Pilot beside the job count** for four weeks before the new score counts.

`DECISION_PLAYBOOK.md`, DEC-33, records these.

## 10. Evidence

| Piece | Where |
| --- | --- |
| Priority facts of a planned job | `focus/valuation/priority.py`; tests in `tests/unit/test_right_work_score.py` |
| The proposed score (a tool outside `focus/`; no grade reads it) | `tools/right_work_score.py`: `example` prints the tables of §3 and §5.3, `sim RUN.db` scores a simulation run both ways. Tests: `tests/unit/test_right_work_score.py` (the canonical score is its special case; the owner's example; the count example) and `tests/integration/test_fleet_simulation.py` (it equals the stored score of every team-shift at today's settings) |
| Floors that choose their own work | `focus.sim.execute.TeamTraits.skip`; table `exec_priority`; tests in `tests/integration/test_fleet_simulation.py` |
| Runs | `python cherry_pick.py 10 5,10 18 CP_<pick>_s6 --pick <plan\|hardest\|gating\|non_gating> --seed 20261026` (and `_s7 --seed 20261027`), about half an hour each |
| Analysis | `python perf_cherry_pick.py RUN_DIR PM_CP_s6.json CP_plan_s6 CP_hardest_s6 CP_gating_s6 CP_non_gating_s6` → [PM_CP_s6.json](evidence/outputs/PM_CP_s6.json); the same for `s7` → [PM_CP_s7.json](evidence/outputs/PM_CP_s7.json) |
