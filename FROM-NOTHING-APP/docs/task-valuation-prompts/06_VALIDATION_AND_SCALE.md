# Prompt 06 — Falsifiable Validation, Scaling and Efficient Approximation

## Experimental protocol
Design fair, reproducible benchmarks against the CURRENT system:
- Audit fixtures and actual benchmark commands, sizes, constraints and stop reasons. Do not invent timing.
- Paired scenarios with identical snapshots, engine policy, seeds, solve budgets and independent validator.
- Distinguish baseline scheduler quality from quality of impact estimates. Do not let valuation algorithm silently change dispatch and then cite changed results as evidence of score validity.
- At least critical versus slack work; parts/QA holds; scarce crews; resource-constrained float; aircraft cost assumptions; unplanned rework; multiple critical chains; task completion changes; shift crossing; disruption approval; no-feasible-plan; unknown/capped economics; incremental replans.
- Stress interactions: delay A, B, both, and neither. Compare summed local impact to joint system impact.
- Hold out scenarios and perturb durations, attendance, due dates and assumed rates.
- If historical empirical data is absent, do not claim predictive validation from synthetic fixtures.
- Provide falsification and stopping thresholds, including computational overhead and unstable rankings.

Measures to report (as available):
- Task rank concordance against trusted counterfactual schedule impact.
- Regret when choosing top-ranked tasks under resource limits.
- Forecast-delay/throughput sensitivity.
- Top-K rank stability and confidence/variation across solver seeds.
- Counterfactual full decode count, p50/p95 wall time, memory, cache hit rate.
- Ratio of double-claimed modeled impact to true joint impact.
- Data coverage and unknown-share; fairness gaps; explainability audit.
- Constraint and commitment invariants: zero verified violations.

## Scale strategy to challenge
Stage 1: inexpensive per-task proxy from existing tails, graph, economics and scarcity signals.
Stage 2: selected counterfactuals for high-consequence or high-uncertainty eligible tasks.
Stage 3: confidence labels or robust ranges; cache/incremental invalidation on relevant changes.

Investigate graph-based local approximation, change-impact cones, pooled solve concurrency (with safe limits), memoization keyed by immutable planning snapshot, scenario engine reuse, resource-affinity shadow prices and constraint-aware criticality. Determine if exact-ish attribution is too expensive at fleet scale.

Note that scenario evaluations may not be isolated if shared mechanics cross aircraft; a local-only replan can miss global effects. Also note that greedy optimization is not globally proven optimal.

## Deliverable
Create 06_VALIDATION_AND_SCALE.md, including:
- actual measured baseline facts with commands/versions or explicit NOT_MEASURED;
- test matrix with acceptance/rejection criteria;
- sample expected-value tables from transparent illustrative data;
- computational complexity/risk analysis and minimum viable shadow-mode study;
- decision rules for promoting or rejecting expensive methods.

Do not write product code or new tests. Safe existing read-only replay only if available and needed; document actual output. Continue with 07_INTEGRATION_BLUEPRINT.md.
