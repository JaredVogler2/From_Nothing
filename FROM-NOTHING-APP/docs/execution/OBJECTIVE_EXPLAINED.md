# What the scheduler minimizes

**Start here for anything about the objective function, late-delivery cost or "penalty cost".** This page gives the
objective in plain terms, a worked example, every rule with the code that implements it and the test that proves it,
and how to tell whether the cost objective is working on live data. `ECONOMICS_POLICY.md` has the full detail;
`docs/data/08-economics.md` describes the seven input tables.

`tests/unit/test_objective_explained.py` fails if this page stops matching the code: the order, the example, and
every file, function and test named below.

## 1. In plain terms

Each run builds a complete schedule for every aircraft in scope, through delivery. It then tries changes to that
schedule and keeps a change only when the new schedule is **strictly better**. There is no AI in it. It is a fixed
procedure with a fixed seed: a run that stops at its move limit gives the same plan every time, and the run's receipt
says how the search ended (`stop_reason`).

"Better" is decided in a fixed order. A later line only breaks a tie in every line above it. It is not a weighted
sum, so no amount of saved overtime can buy a late delivery.

| Order | A schedule is better when it has | Policy name |
| --- | --- | --- |
| 0 | no rule broken: every constraint holds, checked by an independent validator | `FEASIBILITY` |
| 1 | more promises kept: jobs already committed to a slot keep it, then keep their crew | `COMMITMENT_DEFENSE` |
| 2 | fewer aircraft whose finish date cannot be worked out | `CONTROLLABLE_COST` |
| 3 | lower late-delivery cost, in exact dollars | `CONTROLLABLE_COST` |
| 4 | less crew travel and waiting | `NVA_MINUTES` |
| 5 | less overtime, then fewer crew changes on a job | `OT_AND_CHURN` |
| 6 | fewer jobs left unplaced for lack of capacity | `SCARCITY_AFFINITY_GATE` |
| 7 | (nothing else differs) a fixed tie-break, so runs repeat | |

**Late-delivery cost of one aircraft** = daily rate × days late.

- **Daily rate.** One rate per aircraft, from its economics term. A term names a customer and minor model, a
  contract, or one aircraft; the most specific one wins. The shipped rates are a proxy for what a late day costs the
  airline, not a contract figure, and are labeled ASSUMED.
- **Due.** The aircraft's planned unload date at the last control station (CS744) plus 2 days, to the end of that
  local day (owner decision OD-06).
- **Finish.** The time the last open job on the aircraft is released in the schedule. Every open job counts,
  including ones the schedule could not place.
- **Days late.** Finish minus due, to the minute, never below zero. Early is worth nothing extra.
- **No cap** unless a term carries an explicit one. An amount accounting has already booked is a floor, never added
  on top.
- **Only the part a schedule can change is compared.** Cost already run up by the data cut is the same in every
  candidate, so it is reported but does not decide between them.

**Unknown is never zero.** When an aircraft has no rate, no due date, or a job that cannot be planned, its cost is
unknown. It is counted in order 2 and left out of order 3. A total is reported only over the aircraft that are
known, and is labeled as such.

**The rate also steers the search.** Work is tried in order of its cost of delay: the aircraft's cost of one more
day, fading as the job has more slack before the due date. That ordering only guides; the exact comparison above
decides what is kept.

**Two plans come out of each run.**

- The **executable** plan is what crews are dispatched from. It uses only confirmed facts (parts received, real
  people), so many aircraft have no finish date in it.
- The **forecast** plan runs through delivery using expected part dates and nominal crews. Its finish dates are
  labeled conditional. Delivery dates, costs and commitments come from it.

## 2. Worked example

Two aircraft. A costs $380,000 a day late and is due at the end of 12 March. B costs $187,000 a day and is due at
the end of 14 March.

| Schedule | A finishes | B finishes | Cost |
| --- | --- | --- | --- |
| 1 | 14 March 12:00, 1.5 days late | 14 March 18:00, on time | **$570,000** |
| 2 | 13 March 12:00, 0.5 days late | 15 March 06:00, 0.25 days late | **$236,750** |
| 3 | 12 March 20:00, on time | one job cannot be planned: unknown | unknown |

- Schedule 2 beats schedule 1 at order 3: both have every finish date, and $236,750 is less than $570,000.
- Schedule 3 loses to both at order 2. It has one aircraft with no finish date, and they have none. Its dollars are
  never looked at.
- If schedule 2 moved a job that was already committed to a slot and schedule 1 did not, schedule 1 would win at
  order 1, whatever the dollars.

## 3. The rules, the code and the tests

| # | Rule | Code | Tests |
| --- | --- | --- | --- |
| 1 | Candidates are compared level by level in the policy's order, with no monetary tolerance. Money outranks any job count. | `focus/engine/max/optimizer/orchestrator.py` (`key_levels`, `better`, `improved_level`); `config/policies/rebuild-v1.json` | `tests/unit/test_acceptance_key.py::test_the_key_follows_the_policy_order`, `::test_money_outranks_the_deferred_count` |
| 2 | Only a schedule that passes the independent validation is kept. No constraint is traded for cost. | `focus/validation/plan.py` (`validate`); `focus/engine/max/optimizer/orchestrator.py` (`evaluate`) | `tests/integration/test_max_pipeline.py::test_final_candidate_passes_independent_duty_reconstruction`, `::test_complete_inventory_and_truthful_receipt` |
| 3 | An aircraft is finished only when every open job on it is released. One unreleased job makes its finish, and so its cost, unknown. A finish that rests on forecast-only evidence is conditional. | `focus/economics/evaluator.py` (`aircraft_completions`, `line_costs`) | `tests/unit/test_economics.py::test_a45_exact_evaluator_beats_weighted_tardiness_and_keeps_unknowns` |
| 4 | Cost = daily rate × minutes late ÷ 1,440, exact to the cent fraction. A cap applies only when it is explicit. Booked liability is a floor. | `focus/economics/registry.py` (`penalty`, `marginal`) | `tests/unit/test_economics.py::test_exact_linear_penalty_with_subcent_rates_and_caps`, `::test_accrued_accounting_floor_bounds_exposure_increment_and_weight` |
| 5 | One term per aircraft, by exact keys, most specific first. A missing or ambiguous term leaves the aircraft unknown. No default rate exists, and none is ever invented. | `focus/economics/repository.py` (`build_economics`) | `tests/unit/test_economics.py::test_a46_authority_labels_no_fabricated_rates_and_missing_due`, `::test_a46_ambiguous_assignment_or_sale_basis_is_never_resolved_by_row_order` |
| 6 | The due date is the CS744 planned unload date plus the 2-day grace, at the end of that local day. Without it the cost is unknown. | `focus/economics/repository.py` (`build_economics`) | `tests/unit/test_economics.py::test_a23_economic_due_is_the_explicit_station_proxy_not_the_gate_or_earliest` |
| 7 | A known zero rate is known, with no pressure. Unknown is never treated as zero, and known cost is compared only over the same set of aircraft. | `focus/economics/evaluator.py` (`summarize`); `focus/engine/max/optimizer/orchestrator.py` (`better`) | `tests/unit/test_economics.py::test_known_zero_differs_from_unknown_and_unsupported`; `tests/unit/test_acceptance_key.py::test_known_cost_compares_only_the_same_aircraft` |
| 8 | Priority weights are the cost of one more day per aircraft. They order the search and never decide what is kept. An aircraft with a rate and a due date but no finish date still gets its weight. | `focus/economics/registry.py` (`derive_priority_weights`); `focus/engine/max/optimizer/urgency.py` (`cost_of_delay`) | `tests/unit/test_economics.py::test_weights_are_exact_marginals_normalized_without_rounding`; `tests/integration/test_economic_steering.py::test_a44_economic_redispatch_improves_exact_fleet_cost` |
| 9 | The executable plan never uses forecast-only people or parts; the forecast plan labels them. | `focus/engine/max/optimizer/orchestrator.py` (`schedule`) | `tests/integration/test_max_pipeline.py::test_forecast_labels_virtual_support_and_executable_excludes_it` |
| 10 | The run reports what it did truthfully: it is a heuristic, so it never claims a proven optimum, and it lists every aircraft with an unknown cost. | `focus/engine/max/optimizer/orchestrator.py` (`receipt`) | `tests/integration/test_economic_steering.py::test_a44_cli_schedule_reports_weights_complete_dispositions_and_costs` |

## 4. Is the cost objective working on your data?

On live data it is easy to have a run that finishes, publishes a plan and still never compared a dollar. Every
run now says so itself. Read `objective_status` in the schedule receipt (`python -m focus.cli schedule ...`), or
`objective` in the pipeline's status (`python -m focus.cli pipeline --key K --status`, one entry for the executable
plan and one for the forecast). The code is `objective_status` in `focus/economics/evaluator.py`; the tests are
`tests/unit/test_objective_status.py` and
`test_the_pipeline_reports_whether_the_cost_objective_steered_each_plan`.

| Field | Meaning |
| --- | --- |
| `cost_steering` | `full`: every aircraft has a cost. `partial`: some do, and only those are compared. `none`: no aircraft has a cost, so every candidate ties at orders 2 and 3 and the plan is chosen on travel, waiting and overtime. Only the search order still follows the rates. |
| `message` | The same in one sentence. |
| `aircraft`, `cost_known` | Aircraft in the plan, and how many have a known or conditional cost. |
| `rate_known`, `aircraft_without_rate` | Aircraft with a usable rate, and the line numbers with none. No rate is ever filled in. |
| `due_date_known`, `aircraft_without_due_date` | Aircraft with a due date (CS744 planned unload plus the grace days), and the line numbers with none. Every aircraft in work should have one. |
| `past_due_when_planned` | Aircraft whose due date had already passed when the plan was made. |
| `finish_known`, `finish_conditional`, `finish_unknown`, `aircraft_without_finish` | Aircraft by projected finish. Unknown means at least one required job could not be planned. |
| `cost_unknown_by_reason` | For each aircraft with no cost, its first cause, counted. `COMPLETION_UNKNOWN` means the rate and the due date are fine and the finish is not. |
| `unplanned_open_jobs`, `unplanned_open_jobs_by_reason` | The required jobs that could not be planned, counted by first reason, largest first. |

The same status lists `readiness_gaps`: the admitted data's gaps by code, largest first. The two calendar codes in
the table below are there before any plan is made.

How to use it:

1. `cost_steering` should be `full` for the forecast plan. Until it is, the totals are not fleet totals.
2. `aircraft_without_rate` and `aircraft_without_due_date` should be empty. If they are not, fix the data; do not
   fill the gap.
3. Work `unplanned_open_jobs_by_reason` from the top. Each reason is one of the causes below.
4. The executable plan uses confirmed facts only, so it can have more aircraft with an unknown finish than the
   forecast (a job waiting for a part with no confirmed receipt). Judge delivery dates and costs on the forecast.

**What makes a cost unknown, and what to do about it:**

| Cause | What it looks like | What to do |
| --- | --- | --- |
| No rate for the aircraft | `ECONOMICS_MISSING`, `NO_EFFECTIVE_TERM`, `AIRCRAFT_NOT_MAPPED` | Load a term for that customer and minor model, or ask the owner for one. `tools/build_economics_rows.py` builds the rows from a rate file and lists the aircraft it cannot price. Never substitute an average, a median or another customer's rate. |
| No due date | `DUE_UNKNOWN` | The line has no CS744 planned unload date in the same run. Fix the control-station data. |
| A job has no duration | the job is blocked with a duration reason | A duration producer, or the owner's decision on a fallback (OD-02). Never guess one. |
| A job has no control-station window | the job is blocked with a control-station reason | OD-05 and the `cs_windows` producer. |
| Jobs depend on each other in a loop | the jobs are quarantined | Report the loops to the data owner. FOCUS will not pick an edge to drop. |
| The crew size is invalid | the job is blocked with a crew reason | OD-01 fixed the meaning; fix the rows that break it. |
| The line calendar ends before the work does | readiness gap `CALENDAR_NO_DAYS_LEFT` (nothing on the line can be planned) or `CALENDAR_ENDS_BEFORE_DUE` (a late finish cannot be planned, so lateness shows as unknown and no cost is counted) | The calendar producer must cover the whole horizon, past each line's due date, including days after roll-out for aircraft that are already late (`DECISION_PLAYBOOK.md`, DEC-28). FOCUS never adds days. |
| A part has no date (executable plan only) | the job waits for a confirmed receipt | Expected. The forecast plan uses the expected date and labels the result conditional. |

**Rules for anyone wiring live data:**

- Do not make a cost known by inventing an input. An unknown cost that is reported is correct; a known cost built
  on a guess is wrong and looks right.
- Do not move the cost objective down the order, and do not add a weighted sum.
- Report `objective_status` with every run (the counts of aircraft with a rate, a due date and a finish date, and
  the unplanned jobs by reason), until the forecast plan shows `cost_steering` `full`.
- Speed work does not come before this. A fast pipeline whose objective compares nothing is not doing its job.

## 5. How critical is a job?

The owner's rule (D-29): criticality is a function of the cost objective, never a label assigned by hand or by job
type.

**A job's criticality is the modeled cost of one more day late for its aircraft, in the accepted forecast plan, as a
share of the highest such cost in the fleet** (0 to 1). It is the measure the scheduler's own search orders work by,
read from the accepted plan.
The code is `focus/valuation/criticality.py`; the tests are `tests/unit/test_criticality.py`.

| State of the aircraft in the plan | Cost of one more day late | Example at $380,000 a day |
| --- | --- | --- |
| `LATE`: finishes after its due date | the daily rate | $380,000 |
| `ON_TIME`: finishes before its due date | only what would fall past the due date | 12 hours of margin: $190,000; a week of margin: $0 |
| `PAST_DUE`: due date passed, finish unknown | the daily rate, whatever the finish turns out to be | $380,000 |
| `FINISH_UNKNOWN`: not yet due, finish unknown | unknown, never read as zero | — |
| `NO_RATE_OR_DUE_DATE` | unknown, never read as zero and never filled in | — |
| `NO_OPEN_WORK` | nothing | $0 |

Example: three late aircraft at $380,000, $190,000 and $95,000 a day have criticality 1, 0.5 and 0.25. An aircraft
with a week of margin has 0.

Read it under `criticality` in the pipeline status, beside `objective`. Use the forecast's entry.

- **It is the aircraft's, shared by its jobs.** On the test fleets a single job slipping one shift moved no delivery,
  because one job can be re-sequenced; what moves a delivery is how much of the aircraft's work gets done. Which job
  of an aircraft comes first is the plan's sequence.
- **It is reported only.** No plan, goal or score reads it. Whether it should weight a team's score is the owner's
  decision (`DECISION_PLAYBOOK.md`, DEC-31).
- **It needs the cost objective to work** (§4). With every finish date unknown, it is known only for the aircraft
  already past due.

## 6. Open, not built

- **Live economics terms, caps and booked liability** (OD-12). Until approved terms are loaded, live costs are
  unknown or assumed.
- **A carrying cost for early aircraft.** Today an aircraft costs nothing until it passes its due date, so its jobs
  have criticality 0 until then.
- **A proven optimum.** The optional solver only proposes job orders to the same comparison
  (`SOLVER_ENABLEMENT.md`).
- **Dollars per job.** Investigated and not adopted (`docs/task-valuation-investigation/`, reports `01` to `08`):
  single-job costs were zero for a one-shift slip and overlapped where they were not, so they cannot be added up.
  The scheduler's per-job cost of delay stays a search aid.
- **Points weighted by criticality.** Not built. The definition and the report exist (§5); the weighting is DEC-31,
  and team scores stay in standard minutes until the owner decides (`SCORING_EXPLAINED.md`).
