# FOCUS Task-Valuation Investigation — Start Here

## Purpose and non-negotiable posture

Conduct an INDEPENDENT, ADVERSARIAL investigation of whether the FOCUS optimizer should determine point values for (LINE_NUMBER, SHOP_ORDER_IDENTIFIER) tasks, use the resulting values in a prioritized dispatch list, and grade teams on points earned versus points scheduled for a shift.

**The user's idea is a hypothesis, NOT a requirement.** No presumption that earlier placement, zero float, large modeled cost of delay, or more effort equates to greater earned value. A well-supported conclusion to keep current effort points, separate priority from earned points, or abandon task valuation is a valid success.

This is a design/decision investigation, not authorization to implement. **Do not change application code, scheduling logic, policies, tests, schemas, production SQL, UI, source feeds, or dispatch; do not push implementation.** May inspect code and run existing isolated read-only tests/replays if safe; label measured evidence versus conjecture. Do not access real employee data or operational production data. Never commit confidential or operationally sensitive data.

## The decision to investigate

Potential functionality:
1. Quantified impact/importance for each aircraft line number + SOI (respecting phase/segment identity).
2. Explainable optimized task priority in team/shift dispatch views.
3. Frozen scheduled obligation in points at shift publication.
4. Verified points earned through completed or validated partial work.
5. Team, superintendent, aircraft and factory views.
6. Attribution to dependency releases, bottleneck relief, projected fleet delivery, and plan performance.

**Challenge whether these should use one currency at all.**

Crucial distinctions: feasibility versus priority; priority versus economic impact; task marginal contribution versus additive earned points; schedule quality versus execution accountability; engineered labor standards versus schedule-duration estimates; urgency versus zero resource-constrained slack; expected versus realized impact.

## Repository-first evidence standard

Work from THIS checkout. Read the root FOCUS_FROM_NOTHING_MASTER.txt if present, FROM-NOTHING-APP/README.md, relevant policies and current implementation; repo paths can evolve. Inspect tests, existing benchmarks, comments and registered limitations. Cite exact path + symbol and line or commit hash where possible. Never infer an implementation exists merely from documentation.

Important starting areas:
- focus/engine/max/optimizer/{orchestrator,urgency,greedy,repair}.py
- focus/engine/model.py and focus/economics/{registry,evaluator}.py
- focus/analytics/{points,progression}.py
- focus/performance/{score,rollup,lifecycle,disruption}.py
- focus/publication/{serializer,dispatch}.py
- focus/contracts/planning.py; focus/web/ serializers, routes, views
- config/policies; docs/execution/DURATION_METHOD.md; docs/execution/SOURCE_READINESS.md
- docs/integration/SCHEDULING_CONSTRAINTS.md; fixtures and tests

Verify actual files and class names; discover further modules as needed. Current implementation may treat task urgency as aircraft marginal penalty decayed with approximate slack, while keeping defended commitments and lexicographic objective levels dominant. Do not interpret such a dispatch heuristic as a proven marginal task value.

## Workflow and continuity

Execute 01 through 08 **in order**. Use each stage's output as input to the next; if a stage is blocked by missing data or computational capacity, document the blocker, produce the strongest permissible analytical result, and continue without inventing experimental outcomes.

Write reports under:
FROM-NOTHING-APP/docs/task-valuation-investigation/

Requested artifacts (create directory and Markdown files as needed):
- 01_CODE_AUDIT.md
- 02_HYPOTHESIS_STRESS_TEST.md
- 03_MODEL_COMPARISON.md
- 04_ATTRIBUTION_AND_MATH.md
- 05_SHIFT_METRICS_AND_FAIRNESS.md
- 06_VALIDATION_AND_SCALE.md
- 07_INTEGRATION_OPTIONS.md
- 08_FINAL_DECISION_REPORT.md
- EVIDENCE_INDEX.md (links to source and real reproducible runs, if any)

If relevant reports already exist, review and extend without overwriting valid evidence. Preserve failed and negative results.

For each stage: distinguish VERIFIED CODE FACT / EXPERIMENT / THEORETICAL PROPERTY / ASSUMPTION / UNVERIFIED. Record source revision and synthetic-input limitations. Do not present modeled monetary penalties as contracts or actual production losses.

**Do not stop for a user decision mid-investigation**, unless access or safety makes continuing impossible. Stop after completing the decision report; ask for explicit authorization before any implementation.

## Minimum final synthesis

- Answer separately: good for task priority? good for additive earned points? good for shift grading? one metric or several?
- Compare at least six models, including rejecting some.
- Expose double counting, wrong incentives, insufficient capacity, unknown costs and ranking instability.
- Give an actionable top recommendation plus a credible simpler fallback, evidence level, confidence, open decisions, minimum viable read-only integration, success criteria and rollback.
- Choose: (1) effort only; (2) impact for priority only; (3) impact for priority and performance; (4) separate effort and impact; or (5) defensibly superior alternative.
- No invented quantitative claims, citations, experiments, or standards.

**Next:** Read 01_CODE_AUDIT.md and complete its deliverables.
