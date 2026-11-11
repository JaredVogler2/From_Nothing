# 11 — Which measure of performance? Shift, team, superintendent, any period

**Question (owner, 2026-11-11).** What is the best way to determine shift, team and superintendent performance, daily
and over any period, and to compare periods: throughput, adherence, an excuse-adjusted measure, criticality, capacity,
the sum of completed jobs' point values weighted by schedule criticality, or something else?

**Source revision:** `aec4687` plus the simulator option described in §2.

**Evidence labels:** EXPERIMENT (a synthetic run, reproducible from §10), VERIFIED CODE FACT, THEORETICAL PROPERTY,
JUDGMENT. All data is synthetic. No real team has been scored.

## 1. Answer

**No single number does every job in the question. The plan-based score FOCUS already has is the right core, and it
needs two companions and a rule for each level. Throughput, capacity and summed job points should not be used to
compare teams.**

1. **Team and shift: keep the score.** It asks whether the team did its part of the accepted plan, with approved
   excuses taken out and extra work credited at 80%. It was the only kind of measure that passed all three team tests:
   - it invented no differences between teams that were equal by construction;
   - it put a team that was really 2% worse below a normal team 82% of the time after one shift, 90% after a day and
     99.7% after five days;
   - it found every team that failed its plan because it was slow or swapped planned work for other work.
2. **Add two numbers beside it, because the score is blind to them.**
   - **Efficiency**: standard hours earned ÷ hours worked. It was the only measure that found teams that were faster
     than standard (every time), and it found slow teams as well as the score did.
   - **Work beyond plan**: (planned work done + extra work) ÷ expected work, not capped at 100%. It was the only
     measure that told a team that always works ahead (112%) from one that never does (100%). The score gave both
     about 99.8, because it stops at 100.
3. **Do not compare teams on throughput, on a sum of job points, or on output per available hour.**
   - Throughput and summed points ranked identical teams the same way in both halves of the period (0.99): by head
     count. They could not find a team that was 10% worse in 14 days (61%, where 50% is a coin toss).
   - Output per available hour ranked teams by how fully the plan loaded them (correlation 0.97), which a team does
     not control.
   - Weighting points by criticality changed neither result.
4. **Above the team, read the same minutes differently.**
   - 84% of the planned minutes that were not done were excused, and 42% of those excused minutes were caused by
     another team on the same floor. Pooled as they are, the site scored 99.2; with its own teams' knock-on effects
     charged it scored 97.4; the plan delivered was 95.0.
   - So a superintendent or a site needs the excused loss shown by cause and owner, with causes inside the group
     counted against the group.
5. **Compare periods factor by factor, against the same team's own past, with a noise band.**
   - Output per available hour fell 6.1% from week 1 to week 2 on the simulated site. The plan had asked for 6.7%
     less of the teams' time; team execution was 0.7% better. One number would have called that a worse week.
   - With nothing changed, a team's score still moved up to ±1.9 points from one day to the next and ±1.0 from one
     five-day period to the next. Its throughput moved ±11% and ±8%.
6. **Delivery cost and late days are the outcome to watch, not a team measure.** The change in the fleet's delivery
   forecast from one shift to the next barely followed that shift's missed work (correlation 0.26 over 23
   comparisons; not distinguishable from zero).

§5 gives the verdict on each candidate, and §6 the method.

## 2. What was tested

**A measure of a team's performance has to pass five tests.**

| Test | Question | How it was tested |
| --- | --- | --- |
| Fair | Does it stay level when teams are equally good? | T1: a floor where every team behaves the same by construction |
| Sensitive | Does it find a team that is really worse, and how soon? | T4: a known shortfall injected into chosen teams |
| Stable over time | Does it stay still when nothing changed, and move when something did? | T4 and T8: two periods compared |
| Complete | Does it see slow, fast, undisciplined, idle and eager teams? | T6: floors where teams were made to differ |
| Aligned | Does it move the way production moves? | T3: 18 floors with different behaviors; T2: the delivery forecast |

**The candidates,** each computed per team and shift from the same records:

| Measure | Definition |
| --- | --- |
| Score | The canonical score: 100 × (0.8 × min(1, (done + extra) ÷ expected) + 0.2 × done ÷ expected) − the out-of-sequence charge. Expected = planned − excused |
| Attainment, excuse-adjusted | Planned minutes done ÷ (planned − excused) |
| Attainment, no excusals | Planned minutes done ÷ planned (work already done before the shift is not counted as a miss) |
| Jobs done, excuse-adjusted | The same by count of jobs instead of minutes |
| Throughput | Standard minutes completed, planned or not |
| Output per available hour ("capacity") | Throughput ÷ the labor hours present |
| Efficiency | Standard minutes completed ÷ minutes actually worked |
| Sum of job points | Each completed job's standard minutes × (1 + criticality of its aircraft) |
| Job points per available hour | The same ÷ the labor hours present |
| Criticality-weighted attainment | Points earned ÷ points expected |
| Work beyond plan | (Planned done + extra) ÷ expected, not capped |

**The floors.**

- **The 50-aircraft floor**: 42 shifts, 30 production teams of 13 to 29 people, 6.5 million planned minutes
  (`runtime/sim/fleet.db`, run `SIM9427255df24c45204d12`). Every team follows the same rules, so no team is better
  than another.
- **The 18 paired floors** of report `10`: 10 aircraft and 12 shifts each, the same fleet under nine floor behaviors
  and two seeds.
- **Two known-truth floors** (new): 10 aircraft and 18 shifts each. Thirty teams were put by a seeded shuffle into six
  groups of five: 40% slower, 25% faster, swapping a planned task for an unplanned one in 60% of shifts, always
  working ahead when idle, never working ahead, and unchanged. For this the simulator gained an option to make teams
  differ (`focus.sim.execute.TeamTraits`). With it, a person starts a task only after finishing the previous one, so
  a slow team runs out of shift. Without it a run is unchanged, draw for draw.

## 3. Results

### 3.1 Equal teams: which measures invent differences (T1, EXPERIMENT)

Thirty teams that are equal by construction, over 42 shifts. "Repeat" is the correlation between a team's average in
the odd shifts and in the even shifts: near zero is right, because there is nothing to find.

| Measure | Repeat | Follows team size | Follows plan load |
| --- | --- | --- | --- |
| Score | −0.08 | −0.06 | −0.08 |
| Attainment, excuse-adjusted | −0.16 | −0.10 | −0.17 |
| Attainment, no excusals | −0.20 | −0.03 | −0.30 |
| Jobs done, excuse-adjusted | −0.26 | −0.04 | −0.09 |
| Efficiency | 0.17 | −0.10 | 0.13 |
| Criticality-weighted attainment | −0.18 | −0.10 | −0.13 |
| Output per available hour | **0.84** | 0.61 | **0.97** |
| Job points per available hour | **0.84** | 0.63 | **0.98** |
| Throughput | **0.99** | **1.00** | 0.68 |
| Sum of job points | **0.99** | **1.00** | 0.68 |

- Throughput and summed points give the same ranking of teams every time, and the ranking is the head count.
- Dividing by available hours removes size and leaves the plan's load: how much of a team's time the plan asked for
  (51% to 61% here). The plan decides that, from sequence, skills and parts.
- The plan-based measures show nothing, which is correct.

### 3.2 A team that is really worse: how soon is it found (T4, EXPERIMENT)

On the same floor, 6 of the 30 teams were made worse: a share of their completed planned jobs was turned into
unexcused misses. The table gives the chance that a worse team ranks below a normal one. 50% is a coin toss.

| True shortfall | Period | Score | Attainment, no excusals | Output per available hour | Throughput | Sum of job points |
| --- | --- | --- | --- | --- | --- | --- |
| 2% | 1 shift | 82% | 68% | 59% | 52% | 52% |
| 2% | 1 day | 90% | 70% | 65% | 53% | 53% |
| 2% | 5 days | 99.7% | 84% | 72% | 52% | 52% |
| 2% | 14 days | 100% | 96% | 73% | 51% | 51% |
| 5% | 1 shift | 93% | 79% | 71% | 56% | 55% |
| 5% | 1 day | 99% | 87% | 81% | 56% | 56% |
| 10% | 1 day | 100% | 98% | 94% | 63% | 63% |
| 10% | 14 days | 100% | 100% | 98% | 61% | 61% |

- The score finds a 2% difference in a week and a 5% difference in a day.
- Without excusals the same measure needs 14 days for what the score finds in one. The excused misses are noise on top
  of the team's own result, and they are most of the misses (§3.6).
- Throughput and summed points never find the worse team: team size swamps a 10% difference.
- Output per available hour stops improving at about 73% for a 2% shortfall: the plan's load differs more between
  teams than the shortfall does.

### 3.3 Two periods compared (T4 and T8, EXPERIMENT)

**A real change.** One team was made worse in the second of two equal periods. The table gives the chance that its
change is below an unchanged team's change.

| True change | Periods | Score | Attainment, no excusals | Output per available hour | Throughput |
| --- | --- | --- | --- | --- | --- |
| 2% | day against day | 84% | 63% | 59% | 57% |
| 2% | 5 days against 5 days | 98% | 80% | 70% | 63% |
| 5% | day against day | 98% | 76% | 77% | 68% |
| 5% | 5 days against 5 days | 100% | 97% | 90% | 80% |
| 10% | day against day | 100% | 95% | 93% | 86% |

Within one team, size no longer matters, so throughput does better than across teams. It is still the weakest: the
plan asks for a different amount in each period.

**No change at all.** Nine in ten changes between two equal periods were inside these bands when nothing had changed.
Score, plan delivered, output per available hour, efficiency and load are all on a scale of 0 to 100.

| Level | Periods | Score | Plan delivered (no excusals) | Output per available hour | Efficiency | Load |
| --- | --- | --- | --- | --- | --- | --- |
| Team | shift | ±3.5 | ±14.4 | ±8.7 | ±6.9 | ±9.5 |
| Team | day | ±1.9 | ±8.0 | ±4.9 | ±3.4 | ±5.3 |
| Team | 5 days | ±1.0 | ±3.1 | ±4.1 | ±1.3 | ±4.7 |
| Superintendent (5 teams) | day | ±1.0 | ±5.0 | ±2.7 | ±1.5 | ±2.9 |
| Superintendent (5 teams) | 5 days | ±0.7 | ±1.2 | ±3.4 | ±0.6 | ±4.1 |
| Site (30 teams) | day | ±0.6 | ±2.9 | ±2.1 | ±0.8 | ±1.9 |
| Site (30 teams) | 5 days | ±0.3 | ±0.4 | ±2.0 | ±0.1 | ±1.8 |

- A change smaller than the band is not a change. One shift against another says little at team level (±3.5).
- The score and efficiency settle as the period grows. Output per available hour and load do not: the plan's load
  really does drift from week to week. That is a change in what was asked, and it is not the teams'.
- A team's throughput moved ±11% from day to day and ±8% between five-day periods with nothing changed.
- These bands are from a simulated floor. Real ones will be wider and must be measured on the pilot
  (`tools/calibrate_scores.py`, section `weekly`).

### 3.4 Teams made to differ (T6, EXPERIMENT)

Two floors, ten teams in each group, 18 shifts. Group averages:

| Group | Score | Efficiency | Work beyond plan | Throughput per team | Output per available hour |
| --- | --- | --- | --- | --- | --- |
| Slow (40% slower) | 93.8 | 0.69 | 94% | 25,041 | 0.58 |
| Fast (25% faster) | 100.0 | 1.32 | 104% | 32,200 | 0.60 |
| Swaps planned work | 99.2 | 0.99 | 104% | 35,607 | 0.62 |
| Always works ahead | 99.9 | 0.98 | 112% | 38,299 | 0.65 |
| Never works ahead | 99.7 | 0.99 | 100% | 32,921 | 0.62 |
| Unchanged | 99.9 | 0.99 | 104% | 35,888 | 0.62 |

The chance that a team of the group ranks **above** an unchanged team (50% = the measure cannot tell):

| Group | Should rank | Score | Efficiency | Work beyond plan | Throughput | Output per available hour |
| --- | --- | --- | --- | --- | --- | --- |
| Slow | below | **0%** | **0%** | **0%** | 14% | 23% |
| Swaps planned work | below | **0%** | 39% | 44% | 47% | 41% |
| Fast | above | 69% | **100%** | 43% | 42% | 32% |
| Always works ahead | above | 49% | 22% | **99%** | 57% | 72% |
| Never works ahead | below | 41% | 37% | **0%** | 41% | 42% |

- **The score finds a team that fails its plan**, whether from slowness or from choosing other work. Every slow or
  swapping team ranked below every unchanged team.
- **The score cannot see above the plan.** A fast team and an unchanged team both finish the plan and both score
  about 100. A team that works ahead and one that never does also score the same.
- **Efficiency sees pace**, in both directions, and nothing else.
- **Work beyond plan sees initiative** and nothing else. It also depends on how much idle time and ready work a team
  has: on the equal floor it was larger for small teams (correlation −0.47 with team size). It can recognize a
  team; it should not rank teams.
- **Throughput and output per available hour found none of the five groups reliably**, and put the fast teams on
  the wrong side: a fast team is given the same plan, so it produces no more.
- A slow team's score fell only 6 points for a 40% slowdown, because the plan used about 60% of each team's time and
  the slack absorbed the rest. On a floor planned closer to full, slowness would show in the score sooner.

### 3.5 Following production (T3, EXPERIMENT)

Eighteen floors, the same fleet, different behaviors (report `10`). For each behavior, the change against the floor
that only works the plan. "Follows" is the rank correlation with the change in jobs completed; "same direction" counts
the runs where the measure and the jobs completed moved the same way.

| Floor-level measure | Follows jobs completed | Follows less open work | Same direction |
| --- | --- | --- | --- |
| Throughput (and per available hour) | 0.88 | 0.91 | 16 of 16 |
| Work beyond plan | 0.90 | 0.84 | 14 of 16 |
| Score, pooled over the floor | 0.80 | 0.73 | 10 of 16 |
| Attainment, excuse-adjusted | 0.90 | 0.56 | 3 of 16 |
| Attainment, no excusals | 0.87 | 0.51 | 4 of 16 |
| Efficiency | −0.05 | 0.06 | 9 of 16 |

- **For the whole floor, throughput is production.** It belongs at the top of the picture, as the outcome.
- **Attainment alone moves the wrong way** when teams work ahead: it can only fall from 100, so it went down in 13
  of 16 runs while production went up. The score's extra-work credit corrects most of that.
- The six runs where the score and production disagree are cherry-picking (production up, other teams blocked 15
  times as often) and late plans. The score is lower there on purpose.

### 3.6 What each level controls (T5, EXPERIMENT)

Where the 6.48 million planned minutes of the 50-aircraft floor went:

| | Share of planned minutes |
| --- | --- |
| Done as planned | 95.0% |
| Missed, excused | 4.2% |
| Missed, not excused | 0.8% |

Of the minutes not done, **84% were excused**:

| Cause | Share of the loss | Who can change it |
| --- | --- | --- |
| A person called off after the plan was made | 39% | Staffing and attendance |
| Another team's unfinished work | 21% | The other team; their common superintendent; the site |
| Rework from another team's defect | 14% | The team that made the defect |
| A late part | 10% | Supply |
| The team's own unfinished earlier work (not excused) | 10% | The team |
| Overrun, swapped work, other (not excused) | 6% | The team |

- 42% of the excused minutes were caused by another production team on the same floor.
- The site under three rules: **99.2** with every team's excusals pooled as they are; **97.4** with the excuses its
  own teams caused each other counted against it; **95.0** with no excusals.
- With six hypothetical superintendents of five teams each, 7% of the excused minutes were caused inside the same
  group, worth 0.2 to 0.5 points each.

The team score is fair to the team. It describes one sixth of the loss. The other five sixths belong to somebody, and
the higher the level, the more of them belong to that level.

### 3.7 One tree (T7, EXPERIMENT and THEORETICAL PROPERTY)

Output per available labor hour splits exactly into factors, and each factor has an owner:

    output per available hour = load × share not excused × team execution × extra work
                              = time on work × efficiency

| | Output per available hour | Load | Share not excused | Team execution | Extra work | Efficiency |
| --- | --- | --- | --- | --- | --- | --- |
| Week 1 | 0.586 | 0.616 | 0.957 | 0.989 | 1.006 | 0.985 |
| Week 2 | 0.551 | 0.575 | 0.959 | 0.996 | 1.004 | 0.985 |
| Change | **−6.1%** | **−6.7%** | +0.2% | **+0.7%** | −0.2% | 0.0% |

- **Load** = planned hours ÷ available hours: planning and staffing own it.
- **Share not excused** = (planned − excused) ÷ planned: the owners of the causes own it (§3.6).
- **Team execution** = done ÷ (planned − excused): the teams own it. It is the core of the score.
- **Extra work** = (done + extra) ÷ done: the teams' initiative, limited by the ready work there is.
- **Efficiency** = standard hours earned ÷ hours worked: the teams' pace against the standard, and the standard's
  accuracy.

The site produced 6.1% less per available hour in week 2. The teams executed slightly better; the plan asked for
less of their time. A single number, throughput or productivity, would have blamed the teams.

### 3.8 Delivery outcome as a measure (T2, EXPERIMENT, inconclusive)

On the 50-aircraft floor, the change in the fleet's forecast late days from one shift's forecast to the next was
compared with that shift's missed minutes, over the 23 pairs of shifts with the same priced aircraft.

- Correlation with all missed minutes 0.26, with excused misses 0.26, with unexcused misses 0.22; rank correlations
  between −0.28 and 0.14. With 23 pairs none of these is distinguishable from zero.
- The forecast moved between −4.2 and +5.4 late days per shift, for reasons other than that shift's execution: new
  rework, parts, and the next plan's own choices.

This fits reports `06` and `08`: a single shift's work cannot be traced to deliveries. Delivery cost and late days
are the outcome the business watches over weeks. They cannot grade a team or a shift.

## 4. What the results mean

- **The plan is how the cost objective reaches a team** (THEORETICAL PROPERTY). The scheduler turns one objective over
  all aircraft into each team's work for the shift. Doing that work is the team's contribution. A team cannot see
  the objective, cannot be credited a share of it (reports `04` and `08`), and can only hurt it by substituting its
  own choice of work. That is why a plan-based measure is the fair and sensitive one, and why D-27 gives planned work
  full credit and other work 80%.
- **Volume measures answer a different question.** Throughput says how much the floor produced. It depends on how
  many people there were and how much the plan could give them. It is the right outcome for the site and the wrong
  yardstick for a team.
- **Excusing is right for the team and wrong to stop at.** It makes the team's number fair and three times more
  sensitive. It also removes five sixths of the loss from view. The loss has to reappear, by cause and owner, one
  level up.
- **A ratio capped at 100 cannot reward more than the plan.** Pace and initiative need their own numbers.
- **Nothing here measures quality.** A team could hit every measure above by rushing. Rework traced to the team that
  made the defect is in the data (`origin_team`) and needs its own line.

## 5. Verdict on each candidate

| Candidate | Use it for | Do not use it for | Why |
| --- | --- | --- | --- |
| **Throughput** | The site's outcome; the top of the tree | Comparing teams or superintendents; judging a team's period | Ranks head count (0.99). Cannot find a 10% worse team in 14 days (61%). Moves ±8% to ±11% with nothing changed |
| **Adherence / plan attainment** | The core of the team score | Using it alone | Fair and sensitive. Alone it falls when teams work ahead (wrong direction in 13 of 16 runs) |
| **Excuse-adjusted** | The team score | Superintendent and site results as they are | Finds in one day what the unadjusted measure finds in 14. Hides 84% of the loss, 42% of it caused on the same floor |
| **Criticality** | Dispatch priority; a lens on which losses matter | Weighting points or scores | Weighted attainment equals plain attainment (0.05 points apart, D-29). As a sum it is throughput again |
| **Capacity (output per available hour)** | The site's productivity, split into factors | Comparing teams | Ranks the plan's load (0.97). Put fast teams on the wrong side |
| **Sum of job points, criticality-weighted** | Recognition inside the plan, if wanted | Performance, at any level | Behaves exactly as throughput in every test. Also re-prices: the same job is worth more when its aircraft is later, so a worse fleet pays more points |
| **Something else** | The method of §6 | | One set of minutes, read as a tree, with three team numbers and a rule per level |

## 6. The method

**One record per team and shift, in standard minutes** (VERIFIED CODE FACT: the evaluation and the performance row
hold planned, excused, done, extra and the out-of-sequence charge; the job record holds each job's outcome and
cause). Everything below is read from it. Hours worked and hours available are the two additions.

### 6.1 A team's shift

| Number | Question it answers | Compare between teams? |
| --- | --- | --- |
| **Score** (built) | Did the team do its part of the plan, fairly? | Yes, over five days or more |
| **Efficiency** (not built) | How much time did the work take against the standard? | With care: it also measures the standards |
| **Work beyond plan** (computed, not shown) | Did the team do more than was asked? | No: recognize it, do not rank on it |
| **Knock-on caused** (in the data, not shown) | How much of other teams' work did this team block or cause to be redone? | Yes |
| **Excused, by cause** (built) | What stopped the team, and who owns that? | It is not the team's result |

### 6.2 A day, a week, any period

- Sum the minutes over the period first, then apply the same formulas. A period's score is never the average of its
  shifts' scores (VERIFIED CODE FACT: `performance.rollup`, D-25).
- Extra work in one shift then offsets a shortfall in another shift of the same period, up to 100.
- One shift is feedback for the lead. A grade that counts should cover five days or more.

### 6.3 A superintendent

- The same sums over the superintendent's teams.
- An excused minute whose cause is one of the superintendent's own teams counts as not excused at this level
  (not built).
- Beside it: the loss by cause and owner, the teams' scores, efficiency.
- Compare a superintendent with their own earlier periods first. Compare two superintendents only when their teams
  do similar work.

### 6.4 The site or business unit

- Output per available hour, with its factors (§3.7), each with its owner.
- The loss by cause and owner, with every cause inside the site counted against the site.
- The delivery forecast: late days and modeled cost, as a trend over weeks.
- Criticality here: of the minutes lost, how many were on aircraft where a lost day costs money.

### 6.5 Comparing

- **Period against period:** factor by factor (§3.7), never the total alone.
- **Against a band:** show the change with the band for that level and period length (§3.3), and call a change
  inside the band "no change".
- **Team against team:** on the score, over the same period of five days or more. Never on throughput, points,
  output per available hour or work beyond plan.
- **Like with like:** the same shift number, and the same rules in both periods (a policy change makes periods
  `MIXED_POLICY`).

## 7. What exists and what would be built

| Piece | State |
| --- | --- |
| Team-shift score, excusal process, extra-work credit | Built (D-26, D-27) |
| Any period, any group (team, superintendent, business unit), minutes summed first | Built (D-25, `performance.rollup`) |
| Each job's outcome and cause; excused minutes by cause | Built (job records) |
| Scores by shift number, week-to-week stability, supervisor grade check | Built (`tools/calibrate_scores.py`, D-30) |
| Criticality by aircraft, reported | Built (D-29) |
| Work beyond plan, shown beside the score | Not built: the components are stored; it is a display and a rollup field |
| Efficiency | Not built: needs hours worked per team and shift from `actual_intervals` (or `labor_shift`), stored with the row |
| Hours available per team and shift; load; the productivity tree | Not built: needs present hours from `attendance_shift` stored with the row |
| Loss by cause and owner at each level; causes inside the group charged to the group | Not built: the job records hold the cause; the causing team has to be kept with it |
| Knock-on caused, by team; rework originated, by team | Not built: the same field |
| Bands in the comparison views | Not built: measured per site by the calibration tool |

None of this changes a score. It adds numbers beside the score and reads the same minutes at higher levels.

## 8. Limits

- **Synthetic floors.** Teams differ only where they were made to. Real floors add learning, morale, claims that are
  argued, and standards that drift. The directions found here are structural and should hold: size in a sum, plan
  load in a rate, a cap that hides pace and initiative. The sizes will not hold: the bands, the days needed, the 84%.
  Measure them on the pilot.
- **Plan slack.** The simulated plans used about 60% of each team's time. On a floor planned near 100%, slowness
  would reach the score sooner and working ahead would be rarer.
- **Where the tests could favor the score.** The author of these tests built the score. Two things lean its way and
  should be checked on real data:
  - The injected shortfall (§3.2) is defined as planned work not done, which is what the score counts. It lowers
    throughput by the same minutes, so the comparison is fair on that count, but a shortfall of another kind (poor
    quality, slow pace on a slack plan) would not show in the score at all. §3.4 tests pace and finds exactly that.
  - Output per available hour looked unfair because the simulated plan loaded teams unevenly (51% to 61%). If a real
    plan loads every team equally, that measure becomes close to attainment without excusals: fairer than here, and
    still noisier than the score. Run `perf_equal_teams.py` on the pilot's records and read "follows plan load".
- **The injected shortfall** in §3.2 and §3.3 removes completed jobs at random. A real shortfall may be concentrated
  in long or late jobs. It also leaves other teams untouched, where a real one blocks them; their excusals would
  absorb that in the score and not in the unadjusted measures.
- **Small groups.** §3.4 has ten teams per group over two floors.
- **Superintendent groups** in §3.3 and §3.6 are hypothetical.
- **The forecast test** (§3.8) has 23 pairs and decides nothing by itself.
- **Standards.** Efficiency is only as good as the standard minutes. Here they are elapsed time × crew, not
  engineered standards.
- **Quality** is not measured by any candidate.

## 9. Decisions for the owner

1. **Adopt the method of §6**: the score stays the team's grade; efficiency and work beyond plan are shown beside it;
   superintendent and site read the loss by owner and the tree. (`DECISION_PLAYBOOK.md`, DEC-32.)
2. **Working ahead.** The score does not separate a team that finishes and works ahead from one that finishes and
   stops. If working ahead should be rewarded, and not only left unpunished, choose how: shown and recognized
   (recommended), or a score that can pass 100, which favors teams the plan leaves idle.
3. **Efficiency** needs hours worked per job. Decide whether that feed is in the first live scope.
4. **Who is charged for knock-on effects** at superintendent level: the group whose team caused them (recommended),
   or nobody.
5. **Quality**: whether rework traced to the team that caused it is shown beside the score from the start.

## 10. Evidence

Scripts are in [evidence/scripts](evidence/scripts) and outputs in [evidence/outputs](evidence/outputs). They read
simulation databases and write nothing to them. `SIM_DB` is `runtime/sim/fleet.db`; `RUN_DIR` holds the paired runs
of report `10` and the two known-truth runs.

| Test | Command | Output |
| --- | --- | --- |
| T1 equal teams | `python perf_equal_teams.py SIM_DB PM_T1_equal_teams.json` | [PM_T1_equal_teams.json](evidence/outputs/PM_T1_equal_teams.json) |
| T2 delivery forecast | `python perf_forecast.py SIM_DB PM_T2_forecast.json` | [PM_T2_forecast.json](evidence/outputs/PM_T2_forecast.json) |
| T3 floor behaviors | `python perf_behaviors.py RUN_DIR CALIB.json PM_T3_behaviors.json` | [PM_T3_behaviors.json](evidence/outputs/PM_T3_behaviors.json) |
| T4 detection | `python perf_detection.py SIM_DB PM_T4_detection.json` (200 trials per cell, seed 20261111) | [PM_T4_detection.json](evidence/outputs/PM_T4_detection.json) |
| T5 levels | `python perf_levels.py SIM_DB PM_T5_levels.json` | [PM_T5_levels.json](evidence/outputs/PM_T5_levels.json) |
| Known-truth floors | `python known_truth.py 10 5,10 18 KT_s6 --seed 20261026` and `... KT_s7 --seed 20261027` (about 12 minutes each) | run databases, not stored |
| T6 known truth | `python perf_known_truth.py RUN_DIR PM_T6_known_truth.json KT_s6 KT_s7` | [PM_T6_known_truth.json](evidence/outputs/PM_T6_known_truth.json) |
| T7 tree | `python perf_tree.py SIM_DB PM_T7_tree.json` | [PM_T7_tree.json](evidence/outputs/PM_T7_tree.json) |
| T8 bands | `python perf_bands.py SIM_DB PM_T8_bands.json` | [PM_T8_bands.json](evidence/outputs/PM_T8_bands.json) |

The measures are defined once, in `evidence/scripts/perf_measures.py`. The simulator option is
`focus.sim.execute.TeamTraits`; its test is
`tests/integration/test_fleet_simulation.py::test_teams_can_be_made_to_differ_and_a_slow_team_shows_in_efficiency_and_unexcused_overruns`.
