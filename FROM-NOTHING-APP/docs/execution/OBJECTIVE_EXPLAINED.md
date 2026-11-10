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

On live data it is easy to have a run that finishes, publishes a plan and still never compared a dollar. Check
every run:

1. Read `costs` in the schedule step's receipt (`python -m focus.cli schedule ...`, or the pipeline's schedule
   receipt): `unknown_lines`, `complete`, `known_cents`, `conditional_cents`, `controllable_known_cents`.
2. Read it this way:

   | You see | It means |
   | --- | --- |
   | `unknown_lines` lists every aircraft | The cost objective is **not steering**. Every candidate ties at orders 2 and 3, and plans are chosen on travel, waiting and overtime. Only the search order still follows the rates. |
   | `unknown_lines` lists some aircraft | Cost is compared over the others only. The total is not a fleet total. |
   | `unknown_lines` is empty and `complete` is true | Every aircraft is priced. This is the target for the forecast plan. |

3. For each unknown aircraft, find why. The candidate's `aircraft_completion` entry lists the unreleased jobs and
   their reasons; the candidate's `costs.lines` entry gives the reason when the rate or the due date is missing.
4. Count the unreleased jobs by reason over the fleet, and work the largest group first.

**What makes a cost unknown, and what to do about it:**

| Cause | What it looks like | What to do |
| --- | --- | --- |
| No rate for the aircraft | `ECONOMICS_MISSING`, `NO_EFFECTIVE_TERM`, `AIRCRAFT_NOT_MAPPED` | Load a term for that customer and minor model, or ask the owner for one. Never substitute an average, a median or another customer's rate. |
| No due date | `DUE_UNKNOWN` | The line has no CS744 planned unload date in the same run. Fix the control-station data. |
| A job has no duration | the job is blocked with a duration reason | A duration producer, or the owner's decision on a fallback (OD-02). Never guess one. |
| A job has no control-station window | the job is blocked with a control-station reason | OD-05 and the `cs_windows` producer. |
| Jobs depend on each other in a loop | the jobs are quarantined | Report the loops to the data owner. FOCUS will not pick an edge to drop. |
| The crew size is invalid | the job is blocked with a crew reason | OD-01 fixed the meaning; fix the rows that break it. |
| The line calendar ends before the work does | jobs cannot be placed after the calendar's last date | The calendar producer must cover the whole horizon, including days after roll-out for aircraft that are already late (`DECISION_PLAYBOOK.md`, DEC-28). FOCUS never adds days. |
| A part has no date (executable plan only) | the job waits for a confirmed receipt | Expected. The forecast plan uses the expected date and labels the result conditional. |

**Rules for anyone wiring live data:**

- Do not make a cost known by inventing an input. An unknown cost that is reported is correct; a known cost built
  on a guess is wrong and looks right.
- Do not move the cost objective down the order, and do not add a weighted sum.
- Report the number of unknown aircraft and the unreleased jobs by reason with every run, until both are zero for
  the forecast plan.
- Speed work does not come before this. A fast pipeline whose objective compares nothing is not doing its job.

## 5. Open, not built

- **Live economics terms, caps and booked liability** (OD-12). Until approved terms are loaded, live costs are
  unknown or assumed.
- **A carrying cost for early aircraft.** Today an aircraft costs nothing until it passes its due date.
- **A proven optimum.** The optional solver only proposes job orders to the same comparison
  (`SOLVER_ENABLEMENT.md`).
- **Dollars per job, and points by how critical a job is.** Investigated and not adopted
  (`docs/task-valuation-investigation/`, reports `01` to `08`): the cost of delay is a search aid, not a value of a
  job, and team scores stay in standard minutes (`SCORING_EXPLAINED.md`).
