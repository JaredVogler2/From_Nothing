# Prompt 04 — Formal Mathematics, Counterfactuals, Nonadditivity and Attribution

## Formalize top three candidates
For each of the strongest models from Prompt 03, provide symbols, domains, units, input availability, equations and step-by-step examples at (line, SOI, phase/segment, shift) grain. Distinguish critical path, economic model, objective priorities and standard labor.

## Counterfactual protocol
Consider:
V_i = J(S_defer_i) - J(S_base).

DO NOT assume J is scalar dollars: FOCUS currently uses an ordered (lexicographic) objective and strict feasibility/commitment constraints. Determine if a vector of lexicographic deltas, dominance class, Pareto analysis, or disclosed scalar projection is more truthful than a single number. Do not silently trade a defended commitment for modeled savings.

Specify:
- Reference accepted/published snapshot and engine version.
- What is held fixed: requirements, releases, calendar, labor availability, economics assumptions, in-progress locks, incumbent promises.
- Meaning of "defer": start > shift end? remove task until next shift? impose blocked constraint? Defer a phase or entire job?
- Treatment of freed crews, substitutes, preemptions, dependencies and work already started.
- Reoptimization protocol and comparator with matching solve budgets; incumbent-aware warm starts; validation.
- Difference between optimized candidate change, causal sensitivity estimate and a guaranteed optimum delta.
- Repeatability under randomized/bounded heuristic search; uncertainty bounds.
- Attribution to multiple aircraft/resources and indirect effects.

## Mandatory shared-impact example
Two tasks A and B are on one critical chain. Holding everything else constant, deferring A alone or B alone creates a one-day delay; joint deferral need not create two days.
Explain why sum of separate marginal effects can double-count fleet consequences.

Also examine AND gates/complementarity, substitutes, constraints and deadlines that make marginal attribution context-dependent.

Investigate:
- Incremental marginal contribution in the actual scheduled order.
- Permutation/sample-based Shapley values and error bars.
- Shared outcome allocation to critical sets, with possible non-uniqueness.
- Bounded proxy weights normalized to a per-shift budget (and why this may divorce point totals from marginal impact).
- Two currencies that avoid pretending independently computed impacts sum to a real factory benefit.

Establish whether sum_i task_points_i = shift_scheduled_points must hold, what it means, and whether a consistent team shift grading denominator exists. Explain accounting against double claims across phase, segment, SOI, shift and aircraft.

## Deliverable
Create 04_ATTRIBUTION_AND_MATH.md with formal top-three specifications, at least three full worked examples and proof/counterexample sketches for nonadditivity. State whether an additive impact point system is mathematically defensible; if only under assumptions, list them. Continue with 05_SHIFT_METRICS_AND_FAIRNESS.md.
