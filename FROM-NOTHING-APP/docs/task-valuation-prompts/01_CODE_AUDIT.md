# Prompt 01 — Forensic Code and Data Contract Audit

## Research question
What does the CURRENT FOCUS implementation already calculate, and which outputs could reasonably be reused for task valuation without inventing another scheduling or scoring authority?

## Required inspection
Read the actual engine, economics, progress/points, performance, publication, web, policy and test code referenced in 00_START_HERE.md. Follow call sites to establish the end-to-end flow:

source data -> planning snapshot -> phase/line/SOI identity -> precedence/resource constraints -> optimizer priority and lexicographic objective -> accepted/published plan -> earning unit -> frozen goal -> verified progress/completion -> scoring -> rollups -> Team Lead UI.

Explicitly determine:
1. True identity: (LINE_NUMBER, SHOP_ORDER_IDENTIFIER), effectivity/revision, job/phase/segment, team/shift; collision and rework cases. Never assume a unique SOI string identifies a single earning event.
2. Current phase priority tuple, hard dispatch classes, seed/replan behavior, objective comparison, heuristic limitations, reason for early placement.
3. Derivation and meaning of CPM tails, earliest/latest timestamps, slack, cost-of-delay, aircraft marginal penalty and uncertainty/unknown economics; whether a resource-constrained float measure exists.
4. Existing standard-effort person-minute conversion and the difference between engineered standard, elapsed time, calendar duration, rework P75 and observed clocking.
5. Current recognition XP/gate bonuses versus execution-grade points; verify whether any S14 scoring is demo-only.
6. Publication/versioning, defended commitments, the shift goal denominator, approved relief, amendments, off-plan work and absence/unknown states.
7. Conditions for VERIFIED_COMPLETED/PROGRESS earning, partial phase credit, reversals, correction audit, double-count prevention.
8. Team/shift hierarchy, historical retention, rollout source-readiness and available fixture/benchmark commands.
9. Existing API/UI locations for explaining what is ready, assigned, blocked, priority order and performance evidence.

## Deliverable
Create 01_CODE_AUDIT.md with a concise but comprehensive evidence table:
component | real path/symbol | current semantics | confidence | reusable for valuation? | limitations.

Also add a data-lineage diagram (Mermaid or text), the correct work/point grain, a list of semantic traps, and any missing data needed for accurate economic or constrained-slack valuation.

Cite paths/lines and commit. Distinguish NOT_IMPLEMENTED vs DEMO vs VERIFIED_AVAILABLE.

No code changes. Continue with 02_HYPOTHESIS_STRESS_TEST.md.
