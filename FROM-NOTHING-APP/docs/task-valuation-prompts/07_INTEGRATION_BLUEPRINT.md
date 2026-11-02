# Prompt 07 — Design Integration Options Without Implementing

## Goal
Translate the leading alternatives into a least-disruptive, observable, versioned architecture. Do not assume the most sophisticated model wins.

Offer at least:
Option A — do nothing to earning points; expose explainable scheduler priority.
Option B — effort-based earned points plus a separate frozen impact index / critical completion coverage.
Option C — additive impact points, only if attribution and fairness arguments justify them.
You may add a fourth stronger alternative.

## Trace end-to-end
Identify exact existing modules and safe extension seams for:
1. Canonical composite task key (site/program/aircraft line/SOI/revision/job/phase/segment) and team-shift assignment; ensure task splits don't double count.
2. Read-only TaskValuation artifact containing: snapshot/plan identifiers, policy version, analysis horizon, source status, unit, base standardized effort, urgency basis, slack method, marginal impact range/uncertainty, blockers, evidence, explainable reason codes, timestamp and provenance.
3. An optional richer TaskValueAttribution artifact if shared benefits must be reconciled.
4. Separation from scheduler's required feasibility/optimization and from existing recognition XP.
5. Readiness/priority API and Team Lead UI: what to do next, why, why not ready, standard effort, impact explanation, no "points first" override of safety or feasible schedule.
6. Frozen per-shift score definitions and independently approved amendments; event-based earnings, corrections, reversals, verified progress and excusals.
7. Team, superintendent, aircraft and fleet rollups; event-time versions versus current forecast.
8. Optional digital twin graphs of schedule execution to downstream impact; protect against claiming causal credit from coincident plan changes.
9. Historical comparison, retention, migration, feature flags, shadow runs, regression tests, rollback and user communications.
10. API/MCP contracts and governance for interpretation, fairness and audit.

A task's computed importance may change with the fleet state. Explain when displayed priorities can update, when score obligations must remain frozen, and how to show both without misleading users.

## Minimum viable recommendation
Propose a phased approach:
- baseline code audit and confidence;
- read-only task prioritization / impact explanation;
- shadow valuations with no score impact;
- empirical comparison and fairness review;
- conditional adoption if improvement is demonstrated.

Specify development order, dependencies, failure modes, exact module touchpoints, acceptance criteria, performance budgets grounded in real tests, and rollback. Never promise zero latency without measurements.

## Deliverable
Create 07_INTEGRATION_OPTIONS.md: architecture diagram, concise typed schemas/pseudocode, dependencies, migration and de-risking plan, decision gates and separate alternatives. This report does not authorize code modifications. Continue with 08_FINAL_SYNTHESIS.md.
