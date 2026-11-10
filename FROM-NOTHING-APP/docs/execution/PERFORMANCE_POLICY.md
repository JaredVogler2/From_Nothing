# Performance, excusal and recognition policy (P17, P18)

**New here? Read [SCORING_EXPLAINED.md](SCORING_EXPLAINED.md) first:** the score in plain terms, a worked example, and
each rule with its code and tests.

This document records how FOCUS turns observed work into one canonical shift evaluation, and
how supplementary recognition is projected from it. The rules are implemented in
`focus/performance/` (`actuals`, `lifecycle`, `disruption`, `amendments`, `score`, `history`, `rollup`, `details`) and
`focus/analytics/` (`points`, `progression`). Values are policy keys in
`config/policies/grading-demo-v1.json`, each with its authority label.

## Observations

- **Feeds** (`actuals.FEEDS`): lifecycle events, actual intervals, defect evidence, attendance
  evidence, holding evidence, goal amendments, disruption claims and excusal decisions.
- **Visibility.** A row is visible only once observed (`OBSERVED_AT_UTC <= as-of`). A row whose
  `OBSERVED_AT_UTC` is missing or unreadable is not hidden by this rule: `lifecycle_events`,
  `actual_intervals`, `defect_evidence`, `attendance_evidence` and `holding_evidence` then
  quarantine it when they parse the column, but `goal_amendments`, `disruption_claims` and
  `excusal_decisions` admit it at every as-of (`docs/data/09-observation-feeds.md`).
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
  `UNIT_ALREADY_EARNED`, `GOAL_NOT_FOUND`, `SHIFT_NOT_STARTED`, `SHIFT_ENDED`, `SELF_REVIEW` and
  `PLAN_CHURN_SEPARATE_REVIEW`. Commands (not feeds) also refuse a stale expected version with
  `STALE_VERSION`.

## Goals of record

- Each shift is scored against the plan published for it (D-26, owner direction, November 2, 2026). The
  scheduler runs before every shift. A team-shift's goal of record is the last accepted EXECUTABLE plan
  (verified end to end) covering the shift's day that was accepted at or before the shift starts. When none
  was, the first accepted plan covering the day stands in. A team-shift with no frozen units has B0 = 0.
- The other accepted plans' goals for the same team-shift are proposals. Their new work counts only through
  an approved, feasible `GoalAmendment`, which gives B = B0 + additions; original unit ids are retained. An
  amendment is accepted only during the shift: the command (`POST /api/v1/amendments`) refuses
  `SHIFT_NOT_STARTED`, `SHIFT_ENDED` and `PLAN_NOT_OF_RECORD`, and the `goal_amendments` feed quarantines a row
  whose `DECISION_AT_UTC` falls before the shift start (`SHIFT_NOT_STARTED`) or at or after its end
  (`SHIFT_ENDED`). The Team Lead view lists no addition before the shift starts.
- A unit whose phase has a verified `COMPLETED` effective before its shift starts is `DONE_BEFORE_SHIFT`
  (reason `VERIFIED_COMPLETE_BEFORE_SHIFT_START`): usually the previous shift finished it after this shift's
  plan was cut. It stays in B0, counts in E (so it leaves D), and is never earned twice. Only a whole-phase
  completion qualifies: a segment verified early by `PROGRESS` earns nothing in this goal and ends `MISSED`.

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
  2. the goal whose authorized OT window contains it.

  Never early (D-26): a completion before a goal's shift starts earns nothing in that goal. It credits
  the goal whose window contains it when that goal planned the work, and is otherwise offplan output U
  of the team-shift whose window contains it; the later goal holds its unit as `DONE_BEFORE_SHIFT`.
  Because shift windows come before overtime windows, a job finished in one shift's overtime that the
  next shift's plan also holds is credited to the next shift. A completion outside every credit window is offplan output U of the
  team-shift whose window contains it. Earning, relief and offplan sets are disjoint.
- **Unit statuses**, in order of precedence (`lifecycle.unit_facts`):

  | Status | When | Counts in |
  | --- | --- | --- |
  | `EARNED` | A verified completion or segment credited to this goal | Cp |
  | `DONE_BEFORE_SHIFT` | The phase was verified complete before the shift started | E |
  | `UNKNOWN` | The disposition is unknown (terminal or vanished without verified completion) | evaluation UNKNOWN |
  | `PENDING` | The credit window is still open at the as-of | evaluation UNKNOWN (`WINDOW_OPEN`) |
  | `MISSED` | None of the above after the credit deadline | E only when an approved decision relieves it |

  Unit reasons: `VERIFIED_COMPLETE_BEFORE_SHIFT_START`; `COMPLETION_CREDITED_ELSEWHERE_OR_LATE` (a missed
  unit whose phase was completed in another window); `WITHDRAWN_WORK_STAYS_IN_B0` (cancelled or detabbed
  before completion); `NO_REASON_ON_RECORD` (a missed unit with no approved relief that no claim names:
  `disruption.explained_units`; D-27); plus the phase lifecycle's own reasons.
- **Offplan reasons** (`lifecycle._offplan`): `LATE_FOR_GOAL:<goal>` (completed after the credit deadline
  of a goal that planned it); `NOT_IN_ANY_GOAL_OF_RECORD` (no goal whose credit window has closed planned
  it, which includes work pulled forward from a later shift's goal); `STANDARD_EFFORT_UNKNOWN` (no sourced
  standard, so U is unknown); `TEAM_UNKNOWN`; `OUT_OF_SEQUENCE_BY_CHOICE` (a discretionary sequence
  violation on the work: it earns no credit as extra work); `OOS_CLASSIFICATION_UNKNOWN` (an unclassified
  violation on the work: the credit is unknown).

## Sequence violations (OOS)

- **Detection.** An actual start (start event, actual interval, or implied by completion) before a
  hard FS/SS predecessor is satisfied in the admitted graph is a `SequenceViolation`. So is a
  finish before an FF predecessor. The record keeps the effective time and the incomplete
  prerequisites, with AND releases expanded to their incomplete leaves.
- **Classification** uses what was known at the start:
  - `DIRECTED` when an explicit directive exists;
  - `STARTED_WITH_KNOWN_INCOMPLETE_PREDECESSOR` or, for a finish before an FF predecessor,
    `FINISHED_WITH_KNOWN_INCOMPLETE_PREDECESSOR` (both discretionary);
  - `RELIED_ON_RECORDED_COMPLETION` when a later void or reopen exposed the gap.
- **Which goal is charged.** A violation is charged to the goal that `select_goal` picks for its
  effective time: the goal whose shift or authorized OT window contains it. An out-of-sequence start
  made before the shift that planned the work charges no goal; that work is offplan where it happened,
  and violations on offplan work are already in U (no second numeric charge). OOS fact reasons
  include `ALREADY_CHARGED_ONCE`, `CLASSIFICATION_UNKNOWN` and `TRIGGER_EVIDENCE_VOIDED`.
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
- **Crew capacity and skill** come only from source records observed after the plan's source
  cutoff (`attendance_evidence`, `holding_evidence`).
  - A planned crew member's call-off for the shift is CAPACITY_SHORTAGE evidence for that
    person's missed units. A later record for the same person and shift corrects it.
  - A revocation of a certificate in the work's bundle is SKILL_SHORTAGE evidence when it takes
    effect before the unit's planned end and is not reinstated by the planned start.
  - What the plan already knew is not a disruption. Both feeds are optional, like
    `defect_evidence`; without them these causes are claimed by hand.
- **Decisions.**
  - The deciding role is `disruption.reviewer_roles` (`excusal_decider`), and nobody reviews
    their own claim.
  - PLAN_CHURN is reviewed on its own track.
  - Approving a cause outside the five is a recorded exception with a substantive reason.
  - Appeals append superseding decisions.
- **Relief and E.** Decisions are the only source of relief: the union of approved units that are
  still missed at the cutoff. Earned units are never relief, and multiple claims or causes never
  multiply it. E = relief + `DONE_BEFORE_SHIFT` effort (D-26). A `DONE_BEFORE_SHIFT` unit can still be
  claimed and approved, but that adds nothing: it already counts in E.

## Canonical score (demonstration policy)

The score is computed on exact rationals in standard person minutes. `SCORING_EXPLAINED.md` gives it in
plain terms with a worked example. When D > 0:

- D = B - E
- Uc = max(0, eligible offplan minutes - missed minutes with `NO_REASON_ON_RECORD`): the extra work that
  earns credit (D-27). Offplan work started out of sequence by choice is not eligible.
- A = min(1, (Cp + Uc)/D): output attainment
- H = min(1, Cp/D): plan adherence, the planned jobs done
- base = 100(0.80A + 0.20H)
- O = 0.10 × affected planned effort of evidenced discretionary OOS, once per unit, charged to the goal
  whose shift or OT window contains the violation
- final = clamp(base - 100·O/D, 0, 100)

Rounding to one decimal happens once, after every sum. Bands (A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60,
else F) apply to that displayed value.

- **Extra work (D-27).** Below the cap a planned minute earns 100/D points and a credited extra minute
  earns 80/D, so extra work is worth 80% of the same planned work and never lowers a score. The 20%
  adherence share is earned only by planned work. Without extra work the base is 100·Cp/D.
- **Before D-27** the adherence term was H = Cp/(Cp+U) with weights 0.70 and 0.30: every extra minute
  lowered the score, and a team with no extra work got 30 points whatever it finished.

- **Neutral.** B = 0 (`NO_PLAN`; with `NO_OBLIGATION_PLAN` when no accepted executable plan covers the
  day) or D = 0 (`ALL_EXCUSED`: approved relief, or work finished before the shift, covers every
  obligation) is NEUTRAL with no score.
- **Unknown.** Missing coverage, unknown disposition (`UNKNOWN_DISPOSITION`), unknown U, Uc or O
  (`OFFPLAN_EFFORT_UNKNOWN`, `OFFPLAN_OOS_CLASSIFICATION_UNKNOWN`, `OOS_CLASSIFICATION_UNKNOWN`), an open
  window (`WINDOW_OPEN`) or an invalid or unapproved policy (`SCORE_POLICY_INVALID`,
  `SCORE_POLICY_UNAPPROVED`) is UNKNOWN with no score. Pooled results add `MEMBER_UNKNOWN`, `MIXED_POLICY`
  and `OFFPLAN_CREDIT_UNKNOWN` (a member evaluated before D-27 has no Uc until it is evaluated again);
  period rollups add `SHIFTS_NOT_SCORED` and `COMPONENT_UNKNOWN` (`rollup.summarize`).
- **Status.** DEMONSTRATION unless the applicable policy carries an approval receipt
  (`score.policy_approval_ref`) for OFFICIAL. Personal grades are disabled.
- **History.** Evaluations append revisions only when evidence changes, and every surface reads
  the persisted revision. Aggregates pool exact components before division.
- **Any group over any period (D-25).** A team, a superintendent (as of each shift, from the org chart) or the business unit, over any
  period, sums the light rows' components and applies the formula once (`focus.performance.rollup`), which equals the pooled
  evaluation of the same members. Credited extra work is summed like every component, so extra minutes beyond one shift's expected
  work count toward the period. The official result is unknown while any member is unscored; a labeled partial covers the scored
  ones. Each score's jobs are kept for good in a compact record that adds up to it (planned minutes to B, done to Cp, excused to E,
  unplanned to U; a missed job with no cause listed is one the unplanned work covers first); the per-unit evidence goes after
  90 days (`focus.performance.details`).

## Recognition and progression

- **Gate recognition** (`analytics/points.py`, the one authority, also used to freeze goals):
  - base = effort/5;
  - bonus = base × 35% × min(age/5, 1);
  - an actual CS gate is used unchanged; a baseline proxy gets +2 days once; an already-adjusted
    proxy is unchanged;
  - a missing gate is unavailable; a future gate has factor 0; completion stops aging.
- **XP** is the frozen value of verified planned or approved-amendment units, earned once. Later
  display dates never raise it. Offplan output earns no XP (S14: no offplan farming); D-27 changed the
  score only.
- **OOS and XP.** OOS lives once in O. Planned OOS work keeps its frozen XP: there is no second
  charge in bonus or XP (S14, A60).
- **Corrections** append reversing `correction_applied` events. Badges are revoked by appended
  revisions. The originals are retained.
- **Badges** have deterministic conditions: Recovery, Firebreak, CommitmentKeeper, SteadyShift,
  SkillSteward (explicitly not a certification) and MonthlyChampion (only once the month has
  closed; ties go to the stable team id).
- **Levels** are 1 + floor(xp/1000), with an inflation monitor per team per ISO week.
- **No qualification effect.** XP never feeds eligibility, crews, dispatch or certification.
