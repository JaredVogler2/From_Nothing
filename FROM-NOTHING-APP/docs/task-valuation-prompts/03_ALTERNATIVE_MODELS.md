# Prompt 03 — Alternative Task-Valuation Models, Compared Without Favoritism

## Task
Evaluate these distinct alternatives and at least one you derive independently:

A. Engineered standard person-minute points (current production/recognition concept).
B. CPM or resource-constrained criticality, latest start, float or criticality-index points.
C. Aircraft cost-of-delay / urgency-weight points.
D. Counterfactual task-deferral marginal impact (compare accepted plan with feasible reoptimized plan when work is deferred).
E. Dependency-unlocking, bottleneck relief and scarce-resource shadow-price proxies.
F. Hybrid effort × bounded impact multiplier.
G. A distinct model you propose: e.g. expected throughput benefit, robust opportunity cost, lexicographic impact classes, event contribution ledger, Shapley or proportional shared impact.
Optional: statistical prediction from histories; only if data coverage actually permits.

For EACH:
- Define unit, formula, normalization, required data and workload granularity.
- Describe what "one point" means and whether point totals across tasks/teams/shifts are additive and comparable.
- Explain semantics for tasks with zero effort, missing standards, unknown economic terms, conditional costs and blocked work.
- Consider scale invariance, arbitrary weights, cap effects, time horizon, model uncertainty, estimated versus realized impact.
- Is it a *priority indicator*, a *performance earning currency*, both or neither?
- Identify potential Goodhart effects and how operators could game the score.
- Describe maintainability and interpretability, including frontline user explanations.
- State technical requirements for using existing scheduler outputs and likely computational complexity.

Especially examine the case in which a 15-minute task protects delivery, but a 20-hour job fills all spare crew time; neither task should become "worthless" under the wrong metric.

Compare quantitative validity separately from desirability as a motivational game mechanic.

## Decision matrix
Use at least:
1. Fidelity to independently measured fleet impact.
2. Mathematical coherency and additivity.
3. Feasibility/constraint correctness.
4. Stability under perturbations.
5. Fairness and controllability.
6. Computation at production scale.
7. Interpretability.
8. Provenance, audit and maintenance complexity.
9. Compatibility with existing FOCUS scoring and goals.
10. Incentive risks.

Separate measured facts from expert judgments; avoid ungrounded numerical rankings presented as objective. Present at least three serious contenders and one least-complex option.

## Deliverable
Create 03_MODEL_COMPARISON.md. Include a comparison table, worked synthetic numbers, top three models to formalize in 04, and argument AGAINST your provisional favorite. Continue with 04_ATTRIBUTION_AND_MATH.md.
