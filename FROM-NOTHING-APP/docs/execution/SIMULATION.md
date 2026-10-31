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

Then sign in with a demo persona and open `/simulation`. Two more options:

- `--shifts N` stops after N shifts, for a trial run.
- `--max-moves`, `--max-repairs`, `--baseline`, `--edges`, `--rework`, `--teams` and `--team-size` change the defaults
  below.

The same arguments always give the same tables, down to the decision digests, the shop-floor outcomes and the scores
(`tests/integration/test_fleet_simulation.py`).

## The shift loop

Each production day has three shifts, so 14 days is 42 shifts. Each shift goes through eight steps:

1. **Attendance** (first shift of the day). Every rostered person is present or absent for the day's three shifts.
   Absence is `planned_absence` = 3 %.
2. **Pull.** The simulated source system keeps every dataset's rows. Rows change only through keyed upserts and
   removals. The changes since the last shift are written to the pulled tables as deltas.
3. **Admit.** The production snapshot builder (`build_snapshot`) runs on the datasets captured at the cutoff, 15 minutes
   before the shift. It does census, admission, durations, eligibility, pools, calendars, station windows, parts, graph
   and economics. Only the file-store round trip of `acquire` is skipped: its input and output are the same.
4. **Plan.** The rich MAX optimizer runs with the native core:
   - the neutral seed;
   - the cost-of-delay redispatch;
   - delay repair, with `--max-repairs` boosts;
   - `--max-moves` search moves, half of them aimed at the longest waits.

   The previous shift's published plan is the incumbent, so its three-day commitments are defended.
5. **Validate.** The independent validator checks the candidate from the snapshot, with every check required. Only a
   valid plan is used.
6. **Publish.** The plan's assignments, duties, aircraft completions, costs and exceptions are written to tables. It
   becomes the next shift's incumbent.
7. **Execute.** A seeded shop-floor model works the assignments that start in the shift (see below).
8. **Feed back.** Finished tasks are completed with their actual times. The floor's outcomes change the source system:
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

Each assignment that starts in the shift is worked in start order:

- **Part:** a task whose part is still open at its start cannot start.
- **Absence:** each crew member misses the shift after publication with probability `late_absence` = 1 %.
- **Predecessor:** every hard predecessor must be finished, before the shift or earlier in it. A late predecessor
  pushes the start; a start pushed past the shift end is a slip.
- **Duration:** the actual length is the planned length times a lognormal factor (`duration_sigma` = 0.15). Work that
  would end more than 60 minutes past the shift end is not finished (overrun).

Then the source system moves on:

- **Parts:** parts whose expected date has passed arrive, or slip one to three days (`part_slip` = 15 %).
- **Undated shortages:** they get a date with probability `undated_dated_per_shift` = 40 % a shift. New ones are found
  at `new_undated_per_aircraft_shift` = 2 % per aircraft per shift.
- **Rework:** found on the floor at the fleet's rework rate, and from QA rejections (`qa_reject` = 4 %).

## Scores

Each team's shift is scored from its published tasks:

- score = 100 × (0.6 × share of planned tasks finished + 0.4 × share finished on time);
- graded A (90+), B, C, D, or F (below 60).

Utilization is reported beside the score, not in it: actual labor hours over present people's regular hours. It
reflects how much work the plan could give the team, which the team does not control. Fleet figures are rolled up from
team-shifts: counts are summed, rates recomputed, and the score is weighted by planned tasks. The dashboard compares:

- this shift with the prior shift;
- this day with the prior day;
- this week with the prior week;
- the whole simulation.

## Tables (`focus/sim/db.py`)

| Layer | Tables | What they hold |
| --- | --- | --- |
| Pulled | `src_<dataset>` | Every source row version, the shift it was loaded in (`load_shift`) and `op` (`upsert`/`delete`). |
| Calculated | `calc_count`, `calc_job`, `calc_aircraft`, `calc_pool` | Each shift's admitted snapshot. Per open job: team, window, duration, crew, earliest start, open shortage and disposition. Per aircraft: customer, model, due date and daily penalty. Per team and shift: pool size and activation cap. |
| Method | `sim_shift`, `method_stage`, `method_move`, `method_check` | Stage timings and notes, accepted moves, validator check counts and violations, stop reason, decision digest, each step's wall time and peak memory. |
| Results and outcomes | `plan_assignment`, `plan_duty`, `plan_aircraft`, `plan_exception`, `exec_assignment`, `exec_event`, `perf_team_shift` | Published plans, the floor's outcome for each assignment with its cause, source events, and team scores. |

## Dashboard and API

The `/simulation` page has ten modules, each over a read-only operation that is also exposed through MCP. All
operations take `shift` (default: the latest).

| Module | Operation | Shows |
| --- | --- | --- |
| Fleet overview | `GET /api/v1/sim/overview` | KPIs against the prior shift, days late per aircraft, penalty and lateness trends, every aircraft. |
| Economics | `GET /api/v1/sim/overview` | Projected penalty and daily rate by customer and minor model; costliest aircraft. |
| Aircraft | `GET /api/v1/sim/aircraft?line=` | Projection plan by plan, open work by station and team, done and slipped per shift, open shortages. |
| Shift performance | `GET /api/v1/sim/performance` | Shift, day, week and whole-run comparisons; score and slip causes per shift; by day; week against week; teams. |
| Dispatch | `GET /api/v1/sim/dispatch?team=` | Labor hours per team, and one team's task list with crew, commitment outcome and floor outcome. |
| Worker Gantt | `GET /api/v1/sim/gantt?team=` | Each person's work, transfers and waits in the published shift. |
| Capacity | `GET /api/v1/sim/capacity` | Planned load per team and shift for the next three days; actual utilization. |
| Supply chain | `GET /api/v1/sim/supply` | Open shortages by aircraft, when they are expected, arrivals and slips, and tasks blocked by a missing part. |
| How the plan was made | `GET /api/v1/sim/methods` | Data pulled, the snapshot inventory, optimizer stages, moves, validator checks and timings. |
| Data profile | `GET /api/v1/sim/profile` | Workforce, late parts, tasks by station and team, customer mix, pulled table sizes. |

Without a database the operations answer `empty`, with the command that builds one.
