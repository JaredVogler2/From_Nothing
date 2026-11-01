# Fleet simulation (synthetic data)

`focus.sim` replays the FOCUS shift cycle over a synthetic 787 fleet: it plans, publishes, executes and scores each
shift, then feeds the source changes into the next shift. Every number is kept in SQLite tables, and the
`/simulation` dashboard reads them. All data is invented. No real person, aircraft, customer contract or source system is
involved.

## Run it

```bash
python -m focus.sim.runner --aircraft 50 --days 14 --db runtime/sim/fleet.db
```

```bash
FOCUS_SIM_DB=runtime/sim/fleet.db python app.py
```

Then sign in with a demo persona that sees all teams and open `/simulation` (classic) or `/simulation/ios` (the iOS
layout: one scrolling module selector and, on a phone, the Team Lead / My Day / Fleet / Performance / More bottom
navigation over the same modules). More options:

- `--shifts N` stops after N shifts, for a trial run.
- `--purposes` chooses the plans made each shift (default `EXECUTABLE,FORECAST`). The floor works the EXECUTABLE plan.
- `--planning` chooses how each plan is made (default `full`). `repair` keeps the previous shift's accepted plan where
  it still holds and re-places only what changed. `auto` runs the full solve for the day's first shift and repairs the
  next two, as the pipeline does (`docs/execution/PLAN_REPAIR.md`). Each shift's log line shows what ran and why.
- `--max-moves`, `--max-repairs`, `--baseline`, `--edges`, `--rework`, `--teams` and `--team-size` change the defaults
  below.

The same arguments always give the same tables, down to the decision digests, the shop-floor outcomes and the scores
(`tests/integration/test_fleet_simulation.py`).

## The shift loop

Each production day has three shifts, so 14 days is 42 shifts. Each shift goes through these steps:

1. **Attendance** (first shift of the day). Every rostered person is present or absent for the day's three shifts.
   Absence is `planned_absence` = 3 %.
2. **Pull.** The simulated source system keeps every dataset's rows. Rows change only through keyed upserts and
   removals. The changes since the last shift are written to the pulled tables as deltas.
3. **Admit.** The production snapshot builder (`build_snapshot`) runs on the datasets captured at the cutoff, 15 minutes
   before the shift. It does census, admission, durations, eligibility, pools, calendars, station windows, parts, graph
   and economics. Only the file-store round trip of `acquire` is skipped: its input and output are the same.
4. **Plan, twice.** The rich MAX optimizer runs with the native core for both purposes (`--purposes`, default
   `EXECUTABLE,FORECAST`):
   - the neutral seed;
   - the cost-of-delay redispatch, through each aircraft's Registry weight;
   - delay repair, with `--max-repairs` boosts;
   - `--max-moves` search moves, half of them aimed at the longest waits.

   With `--planning repair` or `auto`, a repaired shift replaces these four passes with a decode that keeps the
   incumbent where it holds, followed by the cost-of-delay redispatch of that repair (`docs/execution/PLAN_REPAIR.md`).

   Each purpose's previous valid plan is its incumbent, so its three-day commitments are defended. The incumbent is
   accepted by the simulation without the stage, readback and sign protocol, and its defense says so (`SIMULATED`).
   The forecast is planned, written and released first, and the executable plan last, so only one engine context (several
   GB at 50 aircraft) is in memory at a time.
5. **Validate.** The independent validator checks each candidate from the snapshot, with every check required. Only a
   valid plan becomes the next incumbent. The floor works only a valid EXECUTABLE plan; when it is invalid, the floor
   gets no dispatch that shift.
6. **Freeze the day's goals.** The first valid EXECUTABLE plan of a production day freezes that day's team-shift goals,
   as `GoalRegistry` does in production. Later plans of the day are proposals: the floor works the latest one, while
   the goals stay frozen.
7. **Publish.** Both plans' assignments, duties, aircraft completions, costs and exceptions are written to tables, with
   every commitment's defense outcome.
8. **Execute.** A seeded shop-floor model works the EXECUTABLE assignments that start in the shift (see below). The
   FORECAST plan only projects completions and penalties.
9. **Score.** Each team-shift goal is scored with the canonical formula, after its excusal claims are decided.
10. **Feed back.** The floor's results change the source system:
    - finished work is written back per job;
    - new rework from the floor and QA rejects;
    - part arrivals and slips;
    - new undated shortages, and dates for undated ones.

## The synthetic fleet (`FleetSpec`, `focus/data/fullscale_fixture.py`)

| Field | Default | Meaning |
| --- | --- | --- |
| `n_aircraft` | 50 | Lines 1201-1250 over control stations 740-744, each held for `stay_days` = 7. 744 is roll-out and the due-date proxy. |
| `baseline` | 4,500 | Baseline tasks per minor-model network (787-8, -9, -10). |
| `edges` | 22,000 | Model-level dependencies. Predecessors are always earlier; 98 % are finish-to-start. |
| `rework_total` | 3,000 | Rework found per aircraft over the build, in proportion to progress. 30 % of what has been found is still open. |
| `teams`, `team_size` | 30, 10-30 | Mechanics per team per shift on three shifts, plus a QA team sized to the inspection load. |
| `utilization` | 0.90 | Task lengths are scaled so the fleet's work fills 90 % of regular hours over the build. |
| `late_part_share` | 0.20 | 20 % of each aircraft's baseline tasks have a part shortage. |
| `late_delays` | 70 % 1-3 days, 25 % 4-7, 5 % 8-14 | How late each part is, measured from the task's need date. |
| `undated_share` | 0 | Undated shortages at the start. The simulation introduces them as they are found. |

Station windows are start floors. A task may be planned any distance ahead, but never before its own station's window
opens on its own aircraft. The planner applies the floor and validator check V4 re-checks it.

Customers and minor models follow the open-order mix of `fixtures/economics/penalty_proxy.csv`. Lateness is priced per
customer and minor model with that table's daily ticket-revenue proxy (`fixtures/economics/README.md`).

An aircraft's lateness is set by its worst late parts, the maximum over hundreds of draws. The 8-14 day tail of
`late_delays` therefore decides how late the fleet runs. It is the first knob to review.

## The shop floor (`FloorSpec`, `focus/sim/execute.py`)

At the start of the shift a few present people call off after publication (`late_absence` = 1 % per person and
shift). The call-offs are written to attendance (`SOURCE` = `SIM_LATE_CALLOFF`): they are the evidence of any capacity
claim, and the next shift's plan sees them. Then each dispatched assignment that starts in the shift is worked in start
order:

- **Predecessors.** Every hard edge is honored by type and lag: finish-to-start waits for the predecessor's end,
  start-to-start for its start, and finish-to-finish holds the end. Links between jobs run through each job's
  RELEASE node, which finishes when all its inputs do, so work finished earlier in the shift releases its successors.
  A crew whose predecessor is unfinished waits (a slip), or with `oos_work` = 15 % works out of sequence anyway.
- **Out of sequence.** Every out-of-sequence start is recorded at its actual start against each unfinished
  predecessor. By choice (`oos_discretionary` = 60 %) it is charged once at `score.oos_rate`, finished or not. A
  directed one cites a directive and is not charged.
- **Parts.** A task cannot start while its part is still open. Its kit can also be found short at the point of use
  (`kit_shortage` = 0.3 % of tasks), which enters a new dated shortage. Half of the part slips (`unannounced_slip`) are
  entered only once work finds the part missing.
- **Absence.** A task with a called-off crew member cannot start.
- **Deviation.** With `deviation` = 5 % a team works an unplanned ready task in place of one dispatched task.
- **Duration.** The actual length is the planned length times a lognormal factor (`duration_sigma` = 0.15). Work that
  would end more than 60 minutes past the shift end is not finished (overrun): the crew stops when the allowance runs
  out, and the work it performed is kept (see the write-back).

Afterwards, a team with at least two idle hours pulls ready work forward with `pull_ahead` = 35 %.

**Write-back.** Finished work, and work an overrun cut short, is written back per job before the next cutoff. So each
shift plans only the work the earlier shifts left open, and nothing is credited twice:

- A job with production work left becomes IN WORK, with its performed and remaining minutes in `work_progress`. The
  next admission plans only what is left; a phase an overrun cut short keeps the rest of its actual length. Its remaining work is re-split and renumbered from ordinal 0, so the next
  plan's defense finds each committed piece by its minutes still to go, not by its ordinal.
- A job whose production work is all done takes its buyoff at the end of the shift and is completed. A completed rework
  job's NEO link is marked `COMPLETE`, the master vocabulary the census reconciles it with.

Then the source system moves on:

- **Parts:** parts whose expected date has passed arrive, or slip one to three days (`part_slip` = 15 %).
- **Undated shortages:** they get a date with probability `undated_dated_per_shift` = 40 % a shift. New ones are found
  at `new_undated_per_aircraft_shift` = 2 % per aircraft per shift.
- **Rework:** found on the floor at the fleet's rework rate, and from QA rejections (`qa_reject` = 4 %). The defect
  origin is an explicit fact in the rework classification. A QA reject is the team's own defect. Rework found on the
  floor is traced to another team's finished work at the station with `upstream_defect` = 35 %, otherwise to the
  owning team.

## Scores and excusals

Each team-shift goal is scored by the one canonical formula (`focus.performance.score`) under the active policy, the
same function production uses (S14):

- **Goal.** B0 is the frozen standard person minutes of the goal's units. B = B0 + approved emergency additions.
- **Additions.** At the start of each shift the team's lead adds the rework found after the goals froze that the accepted
  plan assigns in the shift (basis `ASSIGNED_IN_ACCEPTED_PLAN`). The original units are never changed.
- **Relief.** E is the effort of missed units relieved by approved claims, and D = B − E.
- **Earned.** Cp is the planned or added effort finished, once. A frozen unit finished in an earlier shift of the day is
  credited to its own goal.
- **Offplan.** U is finished work that is no unit of the goal: pulled ahead, a deviation, or work a later plan dispatched
  outside the day's goals.
- **Formula.** attainment A = min(1, Cp/D); adherence H = Cp/(Cp + U), so offplan work is discounted; score =
  100 × (0.70 A + 0.30 H) − 100 × O/D, where O is the once-only out-of-sequence charge. Grades are A 90+, B 80+, C 70+,
  D 60+ and F below.
- **Labels.** Results carry the policy's status (DEMONSTRATION). A goal with D = 0 is NEUTRAL, never 100 or F.

Fleet, day and week figures pool the components of every team-shift before dividing, so they are never an average of
team scores. Utilization is reported beside the score, not in it.

**Excusals.** Every missed unit's reason becomes a claim in the eight-cause S14 taxonomy, attributed as
`disruption.auto_attribute` does in production:

- **Blame passes upstream.** A blocking task that was itself blocked passes the blame to its own unfinished
  prerequisite, and a RELEASE node passes it to its unfinished inputs. The culpable task's team is the origin of a
  CROSS_TEAM_PREDECESSOR claim; an open part makes the claim LATE_PART.
- **Capacity.** A CAPACITY_SHORTAGE claim cites the attendance call-off.
- **Rework.** A REWORK_INJECTION claim cites rework the team took on, with the defect origin from the classification.
  Without an origin it is only proposed.
- **Proposed only.** SAME_TEAM_PREDECESSOR and DURATION_OVERRUN claims need a person's notes, so they are proposed and
  never reviewed automatically.
- **No claim.** A deviation has no claim, and neither has work a replan moved with no disruption on record.

A simulated reviewer in the `excusal_decider` role, never the claimant, decides each submitted claim. It approves only
when the cited evidence holds:

- the part record was open at the planned start;
- the cited task belongs to the named other team and was unfinished at the planned start;
- attendance shows the call-off after publication;
- the rework's defect came from another team's work.

Otherwise it rejects with a reason. Only approved claims of the five nominally excusable causes give relief.

The dashboard compares:

- this shift with the prior shift;
- this day with the prior day;
- this week with the prior week;
- the whole simulation.

## Tables (`focus/sim/db.py`)

| Layer | Tables | What they hold |
| --- | --- | --- |
| Pulled | `src_<dataset>` | Every source row version, the shift it was loaded in (`load_shift`) and `op` (`upsert`/`delete`). |
| Calculated | `calc_count`, `calc_job`, `calc_aircraft`, `calc_pool` | Each shift's admitted snapshot. Per open job: team, window, duration, crew, earliest start, open shortage and disposition. Per aircraft: customer, model, due date and daily penalty. Per team and shift: pool size and activation cap. |
| Method | `sim_shift`, `method_stage`, `method_move`, `method_check`, `method_violation`, `method_commitment` | Stage timings and notes, accepted moves, validator check counts and violations, the first 500 violations of each plan (check, code, entity, detail), each incumbent commitment's defense outcome (with the code of each UNSAFE one), stop reason, decision digest, each step's wall time and peak memory. |
| Results | `plan_assignment`, `plan_duty`, `plan_aircraft`, `plan_exception` | Both purposes' published plans. |
| Outcomes | `exec_assignment`, `exec_event`, `exec_offplan`, `exec_oos` | The floor's outcome for each dispatched assignment with its cause, source events, offplan work with its kind, and out-of-sequence starts with their evidence. |
| Performance | `perf_goal_day`, `perf_unit`, `perf_unlock`, `perf_amendment`, `perf_claim`, `perf_team_shift` | Each day's frozen goals, every goal unit (frozen or added) with its status, cause and claim and whether its role needs a certificate group at most two of the frozen membership hold (`scarce`), the other jobs each earned unit's completion released as their last prerequisite (`perf_unlock`), the emergency additions, the claims with their decisions and reasons, and each team-shift's B0, additions, B, E, Cp, U, attainment, adherence, score and grade, scored by `status_and_score` with its parts kept: base before the out-of-sequence charge, the charge O, the clamped final, the policy id and hash, and why a goal is NEUTRAL (NO_PLAN or ALL_EXCUSED). Runs made before this change lack those last columns; the dashboard shows them as —. |

## Dashboard and API

The `/simulation` page has twelve modules, each over a read-only operation that is also exposed through MCP. All
operations take `shift` (default: the latest). The simulation is a fleet-wide view, so only a session scoped to all
teams may read it. Every payload is labelled synthetic and DEMONSTRATION.

| Module | Operation | Shows |
| --- | --- | --- |
| Fleet overview | `GET /api/v1/sim/overview` | KPIs against the prior shift, days late per aircraft, penalty and lateness trends, every aircraft. |
| Economics | `GET /api/v1/sim/overview` | Projected penalty and daily rate by customer and minor model; costliest aircraft. |
| Aircraft | `GET /api/v1/sim/aircraft?line=` | Projection plan by plan, the aircraft score (the canonical formula over its goal units pooled across teams) per shift and over the run, open work by station and team, done and slipped per shift, open shortages. |
| Shift performance | `GET /api/v1/sim/performance?team=` | Shift, day, week and whole-run comparisons of the pooled canonical score, attainment and adherence; unexcused and excused missed effort by cause, with missed work that had no disruption on record (a deviation, or work a replan moved) shown apart because it is not a cause; by day; week against week; each team's B0, additions, E, Cp, U and OOS; one team's history over the run (default: the first team). |
| Excusals | `GET /api/v1/sim/excusals?team=` | Every claim with its evidence, decision and reason; relief and unexcused misses by cause; whose unfinished work blocked whom; offplan work; out-of-sequence starts; emergency additions. |
| Recognition | `GET /api/v1/sim/recognition` | Gate points, XP, levels and badges by the production progression rules over the run's stored goal units (`focus.sim.recognition`): completion credit of each earned unit, the frozen gate bonus of behind-gate original units, commitments kept, decided excusals, released dependencies (`dependency_unlocked`), the Recovery, CommitmentKeeper, Firebreak, SkillSteward, SteadyShift and MonthlyChampion badges, and the inflation monitor. A run made before `perf_unlock` and `perf_unit.scarce` were stored lists dependency_unlocked, Firebreak and SkillSteward as not derived. |
| Dispatch | `GET /api/v1/sim/dispatch?team=` | Labor hours per team, and one team's task list (default: the first team) with crew, commitment outcome and floor outcome. |
| Worker Gantt | `GET /api/v1/sim/gantt?team=` | Each person's work, transfers and waits in the published shift. |
| Capacity | `GET /api/v1/sim/capacity` | Planned load per team and shift for the next three days; actual utilization. |
| Supply chain | `GET /api/v1/sim/supply` | Open shortages by aircraft, when they are expected, arrivals and slips, and tasks blocked by a missing part. |
| How the plan was made | `GET /api/v1/sim/methods` | Data pulled, the snapshot inventory, optimizer stages, moves, validator checks and timings. |
| Data profile | `GET /api/v1/sim/profile` | Workforce, late parts, tasks by station and team, customer mix, pulled table sizes. |

Without a database the operations answer `empty`, with the command that builds one.

## A 50-aircraft, 14-day run

Run on 2026-10-31 with `python -m focus.sim.runner --aircraft 50 --days 14 --db runtime/sim/fleet.db`, from commit
`29157d9`, on a 4-core, 15 GB container with the native core. All data is synthetic. The run predates later changes:
concurrency modes (B2), the crew reserve (B3), source freshness (B7), the evidence feeds (B9), the calendar-aware critical
path (B10), the stored score parts (A14) and recognition facts (A13), stored validator violations, the overrun carry-over
(its 36 overruns were planned again at full length) and the cost changes below.

**Validity.** All 42 shifts planned both purposes, and every published plan passed the independent validator.

**Time per shift**, in seconds unless noted:

| | Mean | Median | Min | Max |
| --- | --- | --- | --- | --- |
| Load (admission) | 102 | 100 | 75 | 143 |
| Plan (both purposes) | 811 | 797 | 588 | 1,172 |
| Validate | 71 | 68 | 53 | 104 |
| Write | 15 | 14 | 11 | 29 |
| Execute | 4 | 4 | 1 | 6 |
| Shift total (minutes) | 16.7 | 16.1 | 12.7 | 23.8 |

That is 11.7 hours in all, with a peak of 8.9 GB.

- **Contention.** The slowest shifts ran while test suites shared the four cores. Shifts with the machine to themselves took
  13 to 16 minutes.
- **Planning.** In those quiet shifts the planning passes got faster as open work drained: the forecast's first pass took
  150 s at shift 0 and 124 s at shift 30.
- **Loading** grows with the finished history, since every completed job stays in the source: 75 s at shift 0, about 90 to
  100 s late in the run.

**Throughput.** 96,984 dispatched tasks completed and 2,642 slipped (2.7%); 96,450 jobs completed. Open jobs fell from
138,345 to 102,618 and the executable plan from 38,626 to 16,828 assignments, while verified-complete jobs rose from
159,664 to 254,277. Open work fell by less than the completions because the floor found 58,886 rework jobs over the run
(all jobs: 298,009 to 356,895), and the counts reconcile: 138,345 + 58,886 − 94,613 = 102,618.

**Carry-over.** Each shift planned only what the earlier shifts left open. Across all 41 shift transitions, the 94,613 jobs
the floor finished were written back COMPLETE before the next cutoff, matching each shift's count. Every one had been open
work, and none was admitted or planned again (executable or forecast).

**Scores.** 1,302 team-shift goals, all scored (none NEUTRAL).

- **Fleet score.** The pooled score was 99.0 in week 1 and 99.6 in week 2 (attainment 0.989 and 0.996, adherence 0.994
  and 0.996).
- **Relief.** 4,036 of 4,647 missed units carried approved relief. Most came from CROSS_TEAM_PREDECESSOR (1,088 claims),
  LATE_PART (464), CAPACITY_SHORTAGE (228) and REWORK_INJECTION (220).
- **Not relieved.** 412 same-team-predecessor and 34 overrun claims stayed proposed, and 71 claims were rejected.

**Cost at a late shift.** The source was replayed to shift 40 (355,461 jobs, 466,492 phases) and both purposes were planned
without an incumbent, before and after the cost changes (commits `29b2427` and `984b8e3`):

| | Before | After |
| --- | --- | --- |
| Load | 87.6 s | 76.2 s |
| Forecast plan | 381.3 s | 371.5 s |
| Executable plan | 242.6 s | 222.9 s |
| Validate (both) | 56.4 s | 46.1 s |
| Decision digests | | identical |
