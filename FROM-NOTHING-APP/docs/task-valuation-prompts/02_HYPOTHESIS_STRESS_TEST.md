# Prompt 02 — Try to Falsify the "Highest Points Scheduled First" Hypothesis

## Central claim to test
"The scheduler should give the most points to the highest-impact line/SOI, which will be scheduled earlier because it has no slack."

Decompose and test the premises separately:
- Does earliest scheduled start identify the most important task?
- Does zero CPM float imply high economic or operational value?
- Is resource-constrained float different from CPM float in this implementation?
- Does modeled task urgency correspond to the task's marginal effect on fleet outcomes?
- If the optimizer is already dispatching tasks, does adding points provide new decision support?
- Should a team be graded more highly for being assigned a more important task rather than for executing its published obligation?

## Mandatory counterexamples
Build small, hand-verifiable synthetic examples for at least:
1. Short zero-slack release that moves delivery; long slack-rich job.
2. Two zero-slack jobs on different aircraft with different due-date exposures.
3. Scarce specialized mechanic or inspector: CPM slack > 0, resource-constrained slack ~ 0.
4. High cost-of-delay task scheduled late because it is not ready, cannot be staffed or lacks required parts.
5. A task scheduled early only to exploit idle capacity or crew continuity.
6. Predecessor that unlocks many jobs whose successor path is independently blocked.
7. Two critical-chain tasks that each claim the same day's delay impact.
8. Parallel tasks with AND prerequisites and complementary release value.
9. Team assigned a low-impact but essential, high-volume obligation.
10. Work crossing shift boundaries, unverified partial progress and corrections.
11. Unknown or capped aircraft economics: economic weight absent/zero but real operational constraints persist.
12. A counterfactual shift deferral where another feasible action fully compensates without delaying delivery.
13. A schedule with already-late aircraft: does extra task importance match marginal penalty rather than historical lateness?
14. A committed work item that leads dispatch class despite a lower urgency estimate.

For each: supply tiny graph, durations, constraints, baseline feasible schedule, plausible alternative, expected ranking under different measures, and the inference that is and is not justified. Use exact arithmetic when possible, label illustrative monetary inputs.

## Deliverable
Create 02_HYPOTHESIS_STRESS_TEST.md with:
- formal definitions of feasibility, urgency, slack, marginal impact, effort, sequence, operational importance, quality and accountability;
- a claim-by-claim verdict (supported / conditional / false / unknown);
- at least 10 counterexamples;
- conditions under which the original claim would hold.

Do not treat intuition as experimental validation. Continue with 03_ALTERNATIVE_MODELS.md.
