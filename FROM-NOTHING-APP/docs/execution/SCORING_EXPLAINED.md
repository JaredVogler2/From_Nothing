# How FOCUS scores a team's shift

**Start here for anything about performance scoring or grading.** This page gives the score in plain terms, a worked
example, every rule with the code that implements it and the test that proves it, and how to check the code against
the rules. `PERFORMANCE_POLICY.md` has the full detail; `DECISIONS.md` (D-25, D-26, D-27) has the reasons.

`tests/unit/test_scoring_explained.py` fails if this page stops matching the code: the example, the weights and
bands, and every file, function and test named below.

## 1. In plain terms

A score is for **one team on one shift**. It is never for a person. It is measured in standard minutes of work.

| Term | Meaning | Symbol in the code |
| --- | --- | --- |
| Planned | The jobs in the plan accepted before the shift started, plus approved emergency additions | `B0`, `B` |
| Excused | Missed jobs a reviewer approved as not the team's fault, plus jobs already finished before the shift began | `E` |
| Expected | Planned minus excused | `D` |
| Done | Planned jobs verified complete in the shift or its authorized overtime | `Cp` |
| Extra | Verified work that was not in the plan | `U` |
| Extra that counts | Every extra job, except one the crew chose to start out of sequence | `Uc` |
| Out-of-sequence charge | 10% of the planned minutes of a job the crew chose to start before its predecessor was done | `O` |

**Score** = 80% × ((done + extra that counts) ÷ expected, at most 100%) + 20% × (done ÷ expected), on a 0–100 scale,
minus the out-of-sequence charge as a share of expected. The result is clamped to 0–100 and rounded to one decimal.

**Grades:** A is 90 and up, B 80, C 70, D 60, F below 60.

**What that means for a team:**

- A planned job done is worth its full minutes.
- Any other job the team gets done is worth 80% of the same minutes, whatever else happened in the shift.
- A planned job missed costs its points, unless a reviewer accepts the reason. Then it is excused and nothing is
  lost.
- Extra work never lowers a score. A team is never better off idle than working ahead.
- The last 20% of the score comes only from doing the planned jobs. Extra work cannot replace it.
- Without extra work the score is simply done ÷ expected: 80% of the expected work done scores 80.

**The owner's example** (2026-11-10). Ten jobs are scheduled. The team does 8 of them and 4 others that were not on
the plan. Take every job as one hour.

| | Score |
| --- | --- |
| Full credit for the 8, with nothing else done | 80 |
| The 4 extra jobs fill the two-hour gap at 80% | +16 |
| The 2 missed jobs, with no valid excuse | **96.0** |
| The 2 missed jobs, with a valid excuse (they leave what was expected) | **100.0** |

Two of the four extra hours go beyond what the shift was expected to do. They cannot lift this shift above 100, and
they count when the shifts of a day or a week are added up.

**Two limits on extra work** (owner decision D-27):

1. Extra work the crew chose to start out of sequence earns nothing.
2. One shift's score stops at 100. Extra minutes beyond that are kept, and they count when shifts are added up for
   a day, a week or longer.

**Also:**

- Only verified completion counts. When in the shift a job was done does not matter.
- No plan, or everything excused, gives no score (neutral). Missing data gives no score (unknown). Neither is a zero.
- For a day, a week or a group of teams, add up the minutes first, then apply the same formula once.
- A missed job that nobody has given a reason for is marked "no reason on record". That is information for the lead
  and for reports. It does not change the score.
- The weights and bands are a demonstration policy. An official grade needs the owner's approval, and personal
  grades are switched off.

## 2. Worked example

| | Minutes |
| --- | --- |
| Planned | 1,000 |
| Excused (a late part, approved) | 100 |
| Expected | 900 |
| Done | 720 |
| Missed, not excused | 180 |
| Extra | 80 |

- (Done + extra that counts) ÷ expected = 800 ÷ 900 = 88.9%.
- Done ÷ expected = 720 ÷ 900 = 80%.
- Score = 0.8 × 88.9 + 0.2 × 80 = **87.1**, a **B**.

Variations on the same shift:

| What changes | Score |
| --- | --- |
| No extra work | **80.0** |
| The 80 minutes were planned jobs instead (done 800) | **88.9** |
| One 100-minute planned job was started out of sequence by choice (charge 10 ÷ 900 = 1.1 points) | **86.0** |
| All 900 expected minutes done, plus 100 extra | **100.0** |

Before D-27 the last case scored 97.0: extra work lowered the score.

## 3. The rules, the code and the tests

| # | Rule | Code | Tests |
| --- | --- | --- | --- |
| 1 | A score is for one team on one shift, in standard person-minutes. Personal grades are disabled. | `focus/performance/score.py` (`evaluate_shift`, `personal_grade_refusal`) | `tests/unit/test_score_facts.py::test_personal_grades_stay_disabled_without_their_own_policy` |
| 2 | A shift is judged against the EXECUTABLE plan accepted at or before the shift starts. If none was, the first plan accepted for its day stands in. Plans accepted later are proposals for that shift. | `focus/performance/lifecycle.py` (`plan_of_record`) | `tests/unit/test_goal_of_record_per_shift.py::test_the_plan_accepted_before_each_shift_is_its_plan_of_record`, `::test_a_plan_accepted_after_the_start_is_a_proposal_until_the_next_shift`; `tests/integration/test_lifecycle_excusals.py::test_each_shift_is_scored_against_the_plan_accepted_before_it_starts` |
| 3 | Only verified completion earns. A reported completion, a terminal status, a cancellation and a disappearance never earn. | `focus/performance/lifecycle.py` (`phase_lifecycle`) | `tests/integration/test_lifecycle_excusals.py::test_a56_reported_completion_is_a_claim_never_credit`, `::test_a56_terminal_status_and_disappearance_are_unknown_not_credit`, `::test_a56_cancel_before_completion_earns_nothing_and_stays_in_b0` |
| 4 | A completion credits the goal whose shift window contains it, then the goal whose authorized overtime window contains it. Never early. Start and finish times are not compared with the plan. | `focus/performance/lifecycle.py` (`select_goal`) | `tests/unit/test_goal_of_record_per_shift.py::test_credit_is_the_shift_window_then_its_overtime_never_early` |
| 5 | Work verified complete before the shift started is `DONE_BEFORE_SHIFT`: it is excused and never earned twice. | `focus/performance/lifecycle.py` (`unit_facts`); `focus/performance/score.py` (`components`) | `tests/unit/test_score_facts.py::test_done_before_shift_is_out_of_d_and_never_earned`; `tests/integration/test_fleet_simulation.py::test_work_finished_after_the_next_cutoff_counts_once_in_its_own_shift` |
| 6 | A missed job is excused only by a reviewed decision. A claim is evidence, not relief. Nobody decides their own claim. There are exactly eight causes, five of them excusable. Overlapping approvals never multiply relief. | `focus/performance/disruption.py` (`approved_units`, `effective_decisions`) | `tests/integration/test_lifecycle_excusals.py::test_a54_all_eight_causes_duplicates_and_union_relief`, `::test_a54_review_authority_self_review_and_appeals`, `::test_a54_taxonomy_is_exact_and_policy_cannot_redefine_it` |
| 7 | Automatic attribution only proposes claims from evidence (a call-off, a certificate revocation, a defect origin, a blocking predecessor). It never grants relief, and an overrun is never excused automatically. | `focus/performance/disruption.py` (`auto_attribute`) | `tests/integration/test_crew_shortage_attribution.py::test_a_call_off_after_the_cutoff_is_capacity_evidence_for_that_persons_missed_units`; `tests/integration/test_lifecycle_excusals.py::test_a55_no_automatic_overrun_excuse_below_or_above_3x` |
| 8 | An emergency addition joins the goal only when it is approved, feasible and made during the shift. | `focus/performance/amendments.py` (`amend_goal`); `focus/performance/actuals.py` (`amendment_from_row`) | `tests/integration/test_lifecycle_excusals.py::test_goal_of_record_freezes_b0_and_amendment_adds_without_mutating`, `::test_an_amendment_waits_for_its_shift_to_start`, `::test_a_feed_amendment_follows_the_same_shift_window_rule` |
| 9 | Verified work outside the plan is extra output. It earns credit as extra work unless the crew chose to start it out of sequence. | `focus/performance/lifecycle.py` (`_offplan`); `focus/performance/score.py` (`components`) | `tests/integration/test_lifecycle_excusals.py::test_canonical_outcomes_at_first_collection`, `::test_planned_oos_charged_once_offplan_oos_not_double_charged`; `tests/unit/test_score_facts.py::test_extra_work_started_out_of_sequence_by_choice_earns_nothing_and_unclassified_is_unknown` |
| 10 | Extra work is credited whatever the team missed. A missed planned job costs points on its own unless it is excused. A missed job that no claim names is marked `NO_REASON_ON_RECORD`, which is information only. | `focus/performance/score.py` (`components`); `focus/performance/disruption.py` (`explained_units`); `focus/performance/lifecycle.py` (`unit_facts`) | `tests/unit/test_score_facts.py::test_extra_work_is_credited_below_planned_work_and_a_miss_costs_points_on_its_own`; `tests/integration/test_lifecycle_excusals.py::test_a_missed_job_with_no_cause_on_record_is_marked_and_extra_work_is_credited_either_way` |
| 11 | Only a discretionary out-of-sequence start or finish is charged: 10% of the affected planned minutes, once per unit. | `focus/performance/lifecycle.py` (`derive_sequence_violations`, `oos_for`); `focus/performance/score.py` (`components`) | `tests/unit/test_score_facts.py::test_oos_charge_is_separate_and_once`; `tests/integration/test_lifecycle_excusals.py::test_planned_oos_charged_once_offplan_oos_not_double_charged`, `::test_a26_oos_record_survives_predecessor_completion_and_never_legalizes` |
| 12 | The formula of §1, on exact fractions, rounded once after every sum. The weights and bands come from the grading policy. | `focus/performance/score.py` (`formula`, `grade_for`, `round_half_up`); `config/policies/grading-demo-v1.json` | `tests/unit/test_score_facts.py::test_s14_numeric_examples`, `::test_zero_output_is_zero_and_h_is_zero`, `::test_rounding_only_after_sums_and_bands` |
| 13 | No plan, or everything excused, is neutral with no score. Missing data, an open window or an invalid policy is unknown with no score. Never a zero. | `focus/performance/score.py` (`status_and_score`); `focus/performance/actuals.py` (`coverage_reasons`) | `tests/unit/test_score_facts.py::test_all_excused_or_no_plan_is_neutral_null`, `::test_missing_evidence_or_policy_is_unknown_null`; `tests/integration/test_lifecycle_excusals.py::test_missing_or_uncertified_feeds_withhold_the_result`, `::test_open_window_and_live_reader_are_honest` |
| 14 | Every score is a demonstration until the owner's approval reference exists. | `focus/performance/score.py` (`score_policy_errors`, `status_and_score`) | `tests/unit/test_score_facts.py::test_official_status_needs_an_applicable_policy_receipt` |
| 15 | A score is never edited. A correction appends a new revision, and only when the evidence changed. | `focus/performance/history.py` (`append`) | `tests/integration/test_lifecycle_excusals.py::test_a56_reopen_revokes_and_late_void_appends_corrected_evaluation`, `::test_a56_revisions_voids_and_corrections_append` |
| 16 | For any group over any period, add the minutes first, then apply the formula once. Extra minutes beyond one shift's expected work count in the sum. | `focus/performance/score.py` (`pooled`, `aggregate`); `focus/performance/rollup.py` (`summarize`, `rollup`) | `tests/unit/test_score_facts.py::test_a59_pooled_aggregate_is_60_not_the_average_75`, `::test_extra_work_beyond_one_shifts_expected_amount_counts_in_the_pooled_period`; `tests/integration/test_performance_rollup.py::test_every_level_and_period_equals_the_pooled_canonical_evaluation`, `::test_every_evaluation_writes_a_row_and_a_job_record_that_add_up_to_its_score` |
| 17 | The simulation scores with the same formula. | `focus/sim/execute.py` (`execute_shift`) | `tests/integration/test_fleet_simulation.py::test_team_scores_are_the_canonical_formula_over_their_goal_units` |

## 4. Checking the code against these rules

A full read of the codebase should include this check. It is read-only and needs no approval.

1. For each rule in §3, read the named function and confirm it does what the rule says.
2. Run the named tests, or all of them:

   ```bash
   python -m pytest -q -p no:cacheprovider tests/unit/test_scoring_explained.py tests/unit/test_score_facts.py tests/unit/test_goal_of_record_per_shift.py tests/integration/test_lifecycle_excusals.py tests/integration/test_performance_rollup.py tests/integration/test_crew_shortage_attribution.py
   ```

3. Search for any other place that computes a score or a grade. Inside `focus/` there must be none outside
   `focus/performance/score.py`: the rollups, the simulation and the simulation's API all call its `formula` or
   `status_and_score`. `tools/calibrate_scores.py` also computes the rule at other rates and the rule before D-27,
   for analysis only.
4. Report every difference between a rule and the code, with the file and line. Change nothing during the read.
   - The code and its tests are what runs today.
   - `DECISIONS.md` (D-26) is the owner's intent.
   - Where they disagree, the owner decides which one is wrong.

The last such check was made on 2026-11-02: about 60 documentation errors and four code gaps were found and fixed.
A new difference is therefore worth reporting.

## 5. Open, not built

- **Swaps.** A team could do easier unplanned work in place of planned work and still earn 80% for it. The 20% it
  gives up is the only deterrent. `tools/calibrate_scores.py` reports how often unplanned work sits beside planned
  work that was missed with no reason on record, so the owner can see whether this happens.
- **Approved additions.** An emergency addition raises what is expected, so an approved extra job can score below
  the same job done unapproved. This needs the owner's decision (`DECISION_PLAYBOOK.md`, DEC-26).
- **Grade bands from real data**, **protection when a plan is accepted late**, and **who gets credit for work finished
  in overtime**: `docs/integration/DECISION_PLAYBOOK.md` §7.
- **A floor that chooses its own work.** Work done in place of planned work that was left without an excuse earns
  80% of that work today, so a crew that leaves a quarter of its plan every shift and does easier jobs instead still
  scores about 93, and the score cannot tell whether the jobs it left were ones other work waits on. Report
  `docs/task-valuation-investigation/12_RIGHT_WORK_SCORE.md` measures this and proposes a lower credit and priority
  tiers; `tools/right_work_score.py` is the tested proposal, outside `focus/`. It is the owner's decision (DEC-33).
- **What the score cannot see.** It says whether a team did its part of the plan. It stops at 100, so it does not
  separate a fast team from a normal one, or a team that works ahead from one that finishes and stops. Labor
  efficiency and work beyond plan would, and neither is built. Throughput and summed job points are not a substitute:
  they rank teams by head count. The tests and the method by level are in
  `docs/task-valuation-investigation/11_PERFORMANCE_MEASURES.md`; the decision is DEC-32.
- **Points by how critical a job is.** The owner's rule is that criticality comes from the cost objective (D-29).
  It is defined and reported with every run: a job's criticality is its aircraft's cost of one more day late
  (`OBJECTIVE_EXPLAINED.md` §5). No score reads it. On the simulated fleet, weighting scores by it moved a team's
  score by 0.05 points on average. Whether it should weight the score is the owner's decision on pilot data
  (`DECISION_PLAYBOOK.md`, DEC-31).

Do not build any of these without the owner's decision.
