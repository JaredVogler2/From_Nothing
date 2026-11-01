# Plan repair and the daily full solve

On November 1, 2026 the owner asked for three speed changes to the shift cycle:

1. publish the shift plan first;
2. reuse admitted data between shifts;
3. repair the previous plan instead of rebuilding it.

This page covers item 3. Items 1 and 2 are in `INSTALL_AND_PIPELINE.md` section 4.

## Why

At 50 aircraft, a full solve of one shift takes about 4 minutes for the executable plan and 6 for the forecast. Most of
that time is the search:

- the neutral seed;
- the cost-of-delay redispatch;
- the neighbourhood moves;
- the independent validation of every candidate the search keeps.

A shift usually changes a small part of the plan: some work finished, some slipped, a part arrived late, a person is
absent. Rebuilding everything to absorb that is slow, and it reshuffles work that nothing touched.

## What a repair does

A repair starts from the accepted plan of the same purpose (its incumbent) and runs two decodes
(`MaxOptimizer(repair=True)`, `greedy.Decoder`). The first one works like this:

1. **Commitments first.** Defended commitments (days 0 to 2) go first, as in every plan: the exact slot and crew, then
   an alternate crew, then a minimal move.
2. **Kept slots.** Every other open placement of the incumbent becomes a hint with the same start and crew
   (`commitments.repair_hints`). The decoder keeps it when that slot still holds (`Decoder._keep`):
   - the shift exists;
   - every predecessor is done by the start;
   - parts, QA and station rules allow the start;
   - the same crew is free and qualified;
   - the pool has capacity.

   Otherwise the decoder re-places the work with the normal rules, at the earliest feasible slot.
3. **New and changed work.** Work the incumbent did not have is placed last, in neutral critical-path order.

The second decode is the full solve's cost-of-delay redispatch applied to that repair:
- kept slots still lead;
- new, changed and re-placed work is dispatched by the cost of delaying it (`urgency.cost_of_delay`, computed from the
  first decode).

The exact comparison keeps the second decode only when it is strictly better. At full scale it closes almost all of the
gap to the full solve (see below).

Some steps do not run in a repair:

- delay repair and the neighbourhood search;
- in-search validation;
- the optional CP-SAT stage.

The independent FINAL validation still checks the whole plan, with every check required, as for any plan. When it
fails, the full solve runs instead (`REPAIR_INVALID`). With no accepted plan to repair, the full solve runs as well
(`REPAIR_NO_INCUMBENT`).

## Measuring performance without a full solve every shift

A repair re-plans the whole horizon for both plans, so it still measures each shift's impact on delivery:

- Work a shift did not finish comes back with its remaining minutes (progress is written back before the next cutoff).
- That work, its successors and anything sharing its crews move as far as they must.
- Each aircraft's completion, days late and penalty are recomputed by the same exact evaluator as in a full solve.

So the forecast published after every shift still shows the new delivery dates and penalties. Schedule-to-schedule
comparisons work as before. For example, in `tests/integration/test_plan_repair.py` a late part removes a kept slot.
The repair re-places that work after the part, and the aircraft's completion moves later.

A kept placement never moves earlier, even when capacity frees up, for example after a shift that ran ahead. That gain
appears at the next full solve. The drift check below measures the gap every day.

The performance side is unchanged:

- frozen goals, the canonical score and excusals are computed the same way;
- each production day's goals are still frozen from its first accepted executable plan;
- plan repair changes only how the next plan is made.

## When each mode runs

A pipeline's request carries the mode (`pipeline --planning auto|full|repair`). The default is policy
`planning.default_mode`.

| Policy key | Value | Meaning |
| --- | --- | --- |
| `planning.default_mode` | `auto` | the mode of a pipeline that does not name one |
| `planning.cutoff_lead_minutes` | 60 | a run plans the shift that contains its cutoff plus this lead (pipelines run shortly before each shift) |
| `planning.full_solve_shifts` | `[1]` | `auto` runs the full solve for a run that plans one of these shifts, once per production day |
| `planning.repair_max_incumbent_age_hours` | 12 | `auto` runs the full solve when the accepted plan's cutoff is older than this |
| `planning.drift_check` | `true` | each `auto` full solve with an accepted plan also measures the repair it replaces |

`auto` runs the full solve when any of these holds; otherwise it repairs:

- the run plans one of `planning.full_solve_shifts`;
- there is no accepted plan;
- the accepted plan's cutoff is too old (`planning.repair_max_incumbent_age_hours`).

`full` always runs the full solve, and `repair` always tries a repair first. The simulation has the same choice
(`python -m focus.sim.runner --planning auto`).

## The drift check

On each `auto` full solve with an accepted plan, the planner first decodes the repair it is replacing. It then records
how far that repair lands from the full plan, repair minus full (`planning.drift`):

| Field | Meaning |
| --- | --- |
| `repair_controllable_known_cents`, `full_controllable_known_cents`, `controllable_delta_cents` | the exact controllable penalty of each, and the difference |
| `costs_comparable` | true when both leave the same aircraft with unknown completion (otherwise the costs cover different aircraft) |
| `repair_unknown_aircraft`, `full_unknown_aircraft` | aircraft whose completion is unknown in each |
| `repair_deferred_phases`, `full_deferred_phases` | work each leaves deferred |
| `aircraft_completion_moved`, `max_completion_days_moved` | how many aircraft complete at a different time, and the largest difference in days |
| `repair_seconds` | the repair's decode time |

A delta near zero means repairs lose little against the full solve. A large delta means the daily full solve is
recovering a lot, and more full solves a day (`planning.full_solve_shifts`) may be worth their time.

## Receipts

Each candidate's `algorithm_status` says how the plan was made:

- `baseline_comparison.planning` holds the mode asked for, the mode that ran and why. A repair adds its counts
  (`hints`, `kept`, `rehomed`, `deferred`); an `auto` full solve adds the drift record. A plain `full` request records
  nothing, so its candidate is unchanged.
- A repair's `stop_reason` is `repair`, which is repeatable: the same inputs give the same plan.
- Its stages are `repair` and `repair_redispatch`; the second says whether the redispatch was kept.
- Its `paths_used` include `repair_hints` and leave out `neighborhoods` and `delay_repair`.
- Its solver status is `DISABLED`, with the reason.

The pipeline's `schedule` and `forecast` step outputs carry the same `planning` record.

## Measured at full scale

**Setup.** Measured on the stored 50-aircraft run (about 355,000 jobs, 466,000 phases and 1.2 million edges per
shift):
- shift 39 was planned in full, without an accepted plan of its own;
- its plans became the accepted plans that shift 40 repaired.

The scripts replay the run's stored source changes, so every plan sees the same source state.

**Repair variants for the shift 40 forecast**, against the full solve of the same snapshot. Times are decode only:
the full solve here skipped its in-search validation, and no row includes the FINAL validation.

| Variant | Decode | Controllable penalty | Repair minus full | Aircraft completing at a different time |
| --- | --- | --- | --- | --- |
| Full solve | 174 s | $115.82M | | |
| Kept slots only (the first decode) | 39 s | $153.0M | +$37.2M | 32, by up to 12.6 days |
| **Kept slots, then the cost-of-delay redispatch (the repair)** | **51 s** | **$115.89M** | **+$0.07M (0.06%)** | **6, by up to 1.7 hours** |
| The accepted order only, each piece at its earliest slot | 32 s | $182.3M | +$66.5M | 30, by up to 14.7 days |
| The accepted order, then the redispatch | 66 s | $117.85M | +$2.0M | 13, by up to 1.3 days |

**What the variants show.** Holding accepted slots is not what costs money: dropping them (the accepted-order rows)
did worse. The order in which displaced and new work is dispatched is what matters, and the cost-of-delay redispatch
fixes it.

**Stability.** The redispatch keeps fewer accepted placements at their exact slot and crew (48,055 of 92,908, against
81,789 for the first decode alone). Most of those are beyond the three defended days, which the full solve repacks
freely anyway. Defended commitments are unchanged.

**The replay is a stress test.** Shift 40's source data reflects the floor working the stored run's own shift-39 plan,
not the plan being repaired. Far more of the accepted plan no longer holds than in a real shift-to-shift repair, where
the floor works the plan being repaired.

**End-to-end plan times on shift 40.** These include building the candidate and the FINAL validation. They were
measured with the first-decode-only repair, before the redispatch was added, which adds about 12 s to the forecast
decode. A test run overlapped the full solves, so their times are inflated: the shift 39 forecast took 351 s alone.

| Shift 40 plan | Full solve | Repair | Accepted placements kept / re-placed (first decode) | Both valid |
| --- | --- | --- | --- | --- |
| Forecast | 417 s | 108 s | 81,789 / 10,948 of 92,908 | yes |
| Executable | 214 s | 59 s | 3,500 / 122 of 3,833 | yes |

## Limits

- **Quality.** A repair keeps the accepted plan where it holds, so its quality follows that plan's. The daily full solve
  and the age rule bound how far it can drift, and the drift check measures it.
- **Real crews only.** Hints cover the accepted plan's assignments with real crews. The accepted-plan record leaves out
  synthetic support crews, so that work is placed fresh.
- **Stored candidates.** Candidates are stored by substance. When a later run produces the same plan, it returns the
  stored candidate, with that candidate's receipt.
