# Performance, excusal and recognition policy (P17, P18)

This document records how FOCUS turns observed work into one canonical shift evaluation, and
how supplementary recognition is projected from it. The rules are implemented in
`focus/performance/` (`actuals`, `lifecycle`, `disruption`, `score`, `history`) and
`focus/analytics/` (`points`, `progression`). Values are policy keys in
`config/policies/grading-demo-v1.json`, each with its authority label.

## Observations

- **Feeds** (`actuals.FEEDS`): lifecycle events, actual intervals, defect evidence, goal
  amendments, disruption claims and excusal decisions.
- **Visibility.** A row is visible only once observed (`OBSERVED_AT_UTC <= as-of`).
- **Ingestion clock.** Ingestion time is the collection's as-of instant, so the event store can
  replay any as-known time.
- **Coverage.** Each feed gets a receipt: `ROWS`, `CERTIFIED_EMPTY`, `EMPTY_UNCERTIFIED`, `FAILED`
  or `NOT_PROBED`. An empty feed is covered only with an affirmative producer receipt.
  Evaluations need every required feed covered through the shift's credit deadline. Otherwise
  they are UNKNOWN, never "zero excuses".
- **Write-once.** Records never change. A replayed row is a counted duplicate. The same key and
  revision with different content is quarantined (`REVISION_CONTENT_CONFLICT`). Sources correct
  themselves with a new revision, `VOIDED` or `CORRECTION`, and a correction becomes known only
  at its own clocks.
- **Quarantine codes are stable**, for example `BAD_EVENT_KIND`, `REPORTED_NOT_VERIFIED`,
  `DERIVED_ONLY`, `NOTES_REQUIRED`, `ORIGIN_TEAM_UNEVIDENCED`, `UNIT_NOT_IN_GOAL`,
  `UNIT_ALREADY_EARNED`, `GOAL_NOT_FOUND`, `SELF_REVIEW`, `PLAN_CHURN_SEPARATE_REVIEW` and
  `STALE_VERSION`.

## Goals of record

- For each obligation day, the first accepted EXECUTABLE plan (verified end to end, in acceptance
  order) freezes B0 for every team-shift of that day. A team-shift with no frozen units has B0 = 0.
- Later replans' goals for the same team-shift are proposals. Their new work counts only through
  an approved, feasible `GoalAmendment`, which gives B = B0 + additions; original unit ids are
  retained.

## Lifecycle and credit

- **What earns credit.** Only verified completion (`COMPLETED`) or a verified segment earning unit
  (`PROGRESS`) earns.
- **What never earns.** `REPORTED_COMPLETION`, terminal status, cancellation, detab and
  disappearance never earn.
- **Cancellation.**
  - Cancel or detab before completion earns nothing, and the unit stays in B0.
  - A verified completion followed by an administrative cancel follows
    `lifecycle.cancel_after_verified_completion`: `PRIOR_VERIFIED_COMPLETION_EARNS` (fixture) or
    `CANCELLATION_REVOKES_CREDIT`.
- **Reopen** revokes credit until it is verified again (`lifecycle.reopen_after_completion`).
- **Unknown disposition.** Terminal status, or disappearance from the admitted census, without
  verified completion makes the disposition UNKNOWN. The evaluation is then UNKNOWN; the
  denominator is never deleted.
- **Credit window.** A verified completion credits exactly one goal-of-record unit set. The goal is
  chosen in this order:
  1. the goal whose shift window contains the completion;
  2. the goal whose authorized OT window contains it;
  3. the next goal that planned it (early completion).

  A completion outside every credit window is offplan output U of the team-shift whose window
  contains it. Earning, relief and offplan sets are disjoint.

## Sequence violations (OOS)

- **Detection.** An actual start (start event, actual interval, or implied by completion) before a
  hard FS/SS predecessor is satisfied in the admitted graph is a `SequenceViolation`. So is a
  finish before an FF predecessor. The record keeps the effective time and the incomplete
  prerequisites, with AND releases expanded to their incomplete leaves.
- **Classification** uses what was known at the start:
  - `DIRECTED` when an explicit directive exists;
  - `STARTED_WITH_KNOWN_INCOMPLETE_PREDECESSOR` (discretionary);
  - `RELIED_ON_RECORDED_COMPLETION` when a later void or reopen exposed the gap.
- **Permanence.** Records are immutable; later predecessor completion never erases them.
- **Separation.** Readiness and dispatch stay graph-governed. The OOS record never legalizes
  continuation.

## Disruptions and excusals

- **Taxonomy.** Exactly eight causes; exactly five nominally excusable (LATE_PART,
  CROSS_TEAM_PREDECESSOR, CAPACITY_SHORTAGE, SKILL_SHORTAGE, REWORK_INJECTION). These are user
  requirements and cannot be overridden by policy, and a tampered bundle is refused at runtime.
- **Claims.** Claims are evidence; relief comes only from a reviewed decision.
  - SAME_TEAM_PREDECESSOR and DURATION_OVERRUN need notes.
  - Automatic attribution only *proposes* them.
  - There is no automatic 3x overrun allowance.
- **Automatic attribution** uses actual frozen impacted work and explicit evidence, and walks to
  the culpable blocker: a predecessor that was itself blocked passes blame to its own late
  prerequisite. DAG direction alone never proves culpability.
- **Rework origin** comes only from defect-origin evidence: the admitted rework classification or
  a `defect_evidence` row.
- **Decisions.**
  - The deciding role is `disruption.reviewer_roles` (`excusal_decider`), and nobody reviews
    their own claim.
  - PLAN_CHURN is reviewed on its own track.
  - Approving a cause outside the five is a recorded exception with a substantive reason.
  - Appeals append superseding decisions.
- **Relief E** is the union of approved units that are still missed at the cutoff. Earned units
  are never relief, and multiple claims or causes never multiply it.

## Canonical score (demonstration policy)

The score is computed on exact rationals in standard person minutes. When D > 0:

- D = B - E
- A = min(1, Cp/D)
- H = Cp/(Cp+U) when output > 0, otherwise 0
- base = 100(0.70A + 0.30H)
- O = 0.10 × affected planned effort of evidenced discretionary OOS, once per unit
- final = clamp(base - 100·O/D, 0, 100)

Rounding to one decimal happens once, after every sum. Bands (A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60,
else F) apply to that displayed value.

- **Neutral.** No plan, or all excused (D = 0), is NEUTRAL with no score.
- **Unknown.** Missing coverage, unknown disposition, unknown U/O, an open window or an invalid
  policy is UNKNOWN with no score.
- **Status.** DEMONSTRATION unless the applicable policy carries an approval receipt
  (`score.policy_approval_ref`) for OFFICIAL. Personal grades are disabled.
- **History.** Evaluations append revisions only when evidence changes, and every surface reads
  the persisted revision. Aggregates pool exact components before division.

## Recognition and progression

- **Gate recognition** (`analytics/points.py`, the one authority, also used to freeze goals):
  - base = effort/5;
  - bonus = base × 35% × min(age/5, 1);
  - an actual CS gate is used unchanged; a baseline proxy gets +2 days once; an already-adjusted
    proxy is unchanged;
  - a missing gate is unavailable; a future gate has factor 0; completion stops aging.
- **XP** is the frozen value of verified planned or approved-amendment units, earned once. Later
  display dates never raise it. Offplan output earns nothing.
- **OOS and XP.** OOS lives once in O. Planned OOS work keeps its frozen XP: there is no second
  charge in bonus or XP (S14, A60).
- **Corrections** append reversing `correction_applied` events. Badges are revoked by appended
  revisions. The originals are retained.
- **Badges** have deterministic conditions: Recovery, Firebreak, CommitmentKeeper, SteadyShift,
  SkillSteward (explicitly not a certification) and MonthlyChampion (only once the month has
  closed; ties go to the stable team id).
- **Levels** are 1 + floor(xp/1000), with an inflation monitor per team per ISO week.
- **No qualification effect.** XP never feeds eligibility, crews, dispatch or certification.
