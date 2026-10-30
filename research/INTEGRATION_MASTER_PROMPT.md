# MASTER AGENT PROMPT — 15 Research Methods for FROM_NOTHING (FOCUS / NEO)

## Mission
Review the ACTUAL current implementation in `FROM-NOTHING-APP/` and authoritative `FOCUS_FROM_NOTHING_MASTER.txt`. Read `research/papers.json`, every locally available PDF in `research/` and the repository ROOT, and any lawfully accessible primary-source full text. Prioritize the highest-value research contributions for FOCUS, NEO, or both. **Do not assume NEO is a separately implemented application in this repository**: discover the existing graph and MCP capabilities, and design a clean NEO adapter if needed. Preserve current capabilities.

## Nonnegotiable gates
- Never confuse the research paper's benchmark with an aerospace-fleet benchmark. Validate title, year, method, equations, datasets, outcomes and page numbers. Flag inaccessible/full-text-missing papers and avoid fabricated replication claims.
- Preserve all hard constraints: typed precedence, per-line calendars, QA and certification applicability, named mechanic duty/qualification, material readiness, rework, spatial/tool constraints where implemented, immutable snapshots, fleet economic evaluation, nine-check independent validator, dispatch and publication gates. Never weaken acceptance A01–A72 or C01–C39.
- Do not use confidential real data in this repository, mutate production SQL, dispatch live schedules or modify production permissions.
- New techniques are optional adapters with feature flags, audit logs, deterministic baseline and rollback. Learned policies propose decisions, not certify feasibility. Unknown != zero; conditional ETA != receipt.
- Test on paired seeded synthetic datasets with fixed CPU/memory/time budgets, same objective, full validator, held-out disruptions, statistical uncertainty and failure injection. Never compare raw makespan alone when fleet economic value is the actual objective.

## Phase 0 — forensic implementation audit
Inventory every scheduling engine, resource allocator, graph type, optimization interface, validator, source schema, scenario/benchmark harness, MCP tool, API, UI, worker and simulation module. Trace a task from source snapshot to eligible resource, optimization, validation, immutable publication and audit. Identify implemented vs placeholder, and cite exact files/functions. Run current test suite and record baseline; do not claim tests passed unless executed. Produce `FROM-NOTHING-APP/docs/research-evaluation/IMPLEMENTATION_AUDIT.md`.

## Phase 1 — primary-source paper audit
For all 15 papers: identify exact source PDF/local root match, version, date, equations, dataset size, baselines, performance metrics, assumptions, source page citations, reproducibility/code availability, transfer mismatch and licensing. Mark FULL_TEXT_VERIFIED / ABSTRACT_ONLY / NOT_AVAILABLE. Check any existing research prompt series under `FROM-NOTHING-APP/docs/research-prompts/` to avoid duplicating work. Produce `PAPER_EVIDENCE_MATRIX.md` and crosswalk to actual modules.

## Phase 2 — prioritized work packages
**P0 — Deterministic enhancements first**
A. Paper 03 mixed-model CP: benchmark calendar preemption, variable capacity, blocking and topological decomposition; ensure no invalid partition of shared fleet resources.
B. Paper 05 aircraft workforce explanation: minimal infeasible subsets, correction suggestions only on authorized adjustable constraints, independently prove explanations on small cases.
C. Papers 07 and 15 selective repair: deterministic affected neighborhood, incumbent preservation, global repair fallback; measure time-to-feasible, churn, fleet value, P95 and resource coupling across aircraft.
D. Paper 08 CoRS: separate immutable/static graph features from dynamic eligibility; versioned cache invalidation and equality with uncached oracle on out-of-order event streams.
E. Paper 11 Benders: derive mathematically valid cuts or explicitly label hierarchical heuristic; benchmark against monolithic solver on tiny provably optimal cases and scaled scenarios.

**P1 — Graph and domain extensions**
F. Papers 01 and 04: compare skill-aware duration and approved multimode staffing with existing named-crew semantics; distinguish empirical skill assumptions from authorized time standards.
G. Paper 02: station-local/global coordination with fleet objective, material delays and shared workforce; no independent station optimization that double-books resources.
H. Paper 06: graph-grounded natural-language analysis using only deterministic read-only tools and existing MCP/API grants; require provenance, numeric correctness, safe identity resolution and no unsupported schedule modification.
I. Paper 09 GSEM: versioned disruption memory, graph similarity vs flat-feature retrieval, solver warm starts, leakage-free train/test, never replay unvalidated schedules.
J. Paper 13 graph-guided ALNS: start with deterministic destroy/repair operators; optionally learn neighborhood selection only if clear lift.

**P2 — Optional learned/agent methods**
K. Paper 10 SAT: exact local challenger, full constraint encoding or narrow verified subproblem, UNSAT vs UNKNOWN, proof or independent optimum checks.
L. Paper 12 LLM/MCP constraint compiler: propose code only, validate via exhaustive tiny-instance tests and reviewer approval, no runtime auto-edit.
M. Paper 14 CP-guided multi-agent RL: offline teacher trajectories, prevent reward/objective mismatch and leakage, only retain if beats simpler heuristics.

## Phase 3 — scientific evaluation
Generate configurable 100/1000/5000/20000 operation networks and optional 50-aircraft stress test with realistic resource contention and shared qualifications. Disruptions: part shortages, QA holds, workforce loss, tooling failure, calendar changes, rework, state latency, workspace conflicts. Benchmark baseline, individual method and selected combinations with equal compute. Metrics: zero independent hard-constraint violations, time-to-first-feasible, economic objective, gap to best-known incumbent and proven bound where available, churn, QA utilization, delay exposure, memory, P95/P99, robustness, implementation cost. Keep paired confidence intervals and negative results. Add metamorphic, property, regression and fault-injection tests. Explain when a technique helps and when it hurts.

## Phase 4 — integration decision and deployment
Rank all 15 as ADOPT / SHADOW / EXPERIMENT / REJECT / DEFER, with measured evidence and explicit uncertainties. Prefer minimum-complexity Pareto improvements. Provide proposed module interfaces, migration-safe schemas, diagrams, UI examples, operational kill switches, rollback tests, human approval boundaries and CI gates. Implement only approved, testable, reversible changes. No new ML dependencies in default production install without proof of value.

## Required deliverables
1. `docs/research-evaluation/IMPLEMENTATION_AUDIT.md`
2. `docs/research-evaluation/PAPER_EVIDENCE_MATRIX.md`
3. `docs/research-evaluation/RESEARCH_ROADMAP.md`
4. `docs/research-evaluation/BENCHMARK_PROTOCOL.md`
5. `docs/research-evaluation/DECISION_SCORECARD.md`
6. Reproducible experiment code/tests/evidence only for methods actually attempted.
7. Git commits after each completed gated phase, with no claim of deployment without deployment evidence.

## Execution instruction
Start with Phase 0 and Phase 1, then prioritize P0 work packages based on evidence and actual code gaps. Proceed methodically; stop and document blockers rather than fabricate evidence. Produce a concise progress register after each phase.
