# Stage P17: Lifecycle actuals and disruption/excusal evidence

Status: **fixture_verified**. The observation producers are fixture feeds. No live SQL producer,
DDL or write was installed or executed.

## Changed paths

- `focus/contracts/events.py`: append-only contracts.
  - `LifecycleEvent` keeps four clocks (effective ≤ recorded ≤ observed ≤ ingested), the source
    sequence and revision, the exact job, phase and person, verification, directive and
    supersession. Reported observations can never be verified.
  - `ActualInterval`, `SequenceViolation`, `DefectEvidence`, `DisruptionClaim` (PROPOSED,
    SUBMITTED or WITHDRAWN; AUTO or MANUAL; supersession), `ExcusalDecision` (review track,
    exception, appeal), `GoalAmendment` (feasible addition, effort = units), `ObservationRun`,
    `FeedReceipt`, `QuarantinedRow` and `IEReview`.
  - Exactly 8 causes and 5 nominally excusable ones.
- `focus/performance/actuals.py`: the collector.
  - Six feed contracts, a fixture reader (rows visible once observed; producer receipt and
    covered-through watermark) and a live reader (NOT_PROBED until producers exist).
  - Natural keys resolve to the admission's own job, phase and person ids.
  - Stable quarantine codes; write-once dedupe (duplicate versus `REVISION_CONTENT_CONFLICT`).
  - Derived violations, disappearances and automatic claims.
  - An `ObservationRun` receipt, and an `observation/<scope>` head that only moves forward.
  - `worker_reported_observation`, which records a claim and never verified credit.
- `focus/performance/lifecycle.py`:
  - **Goals of record.** The first accepted EXECUTABLE plan per obligation day, verified end to
    end in acceptance order. Later replans are proposals; a B0 = 0 record covers empty
    team-shifts.
  - **Revisions.** Latest revision, void (and void of a void) and correction applied at its own
    clocks.
  - **Ordered lifecycle** with the cancel-after-completion policy, reopen until re-verified, and
    terminal or disappeared states treated as unknown.
  - **Once-only credit:** shift window → authorized OT window → early. Otherwise offplan U for
    the team-shift where the work happened.
  - **Unit facts:** EARNED, MISSED, UNKNOWN or PENDING.
  - **OOS:** derivation with as-known classification (directed, known-incomplete, or relied on a
    recorded completion), charged once per planned unit.
  - Disappearance derivation.
- `focus/performance/disruption.py`:
  - **Intake rules:** taxonomy guard, unit in goal, never earned, notes, evidence, cross-team or
    same-team consistency, evidenced rework origin, supersession.
  - **Decisions:** reviewer role, no self-review, PLAN_CHURN on its own track, substantive reason
    for an exception, appeals without double supersession, optimistic version.
  - **Automatic attribution** from explicit evidence:
    - the culpable-blocker walk, so a blocked neighbour is never blamed;
    - LATE_PART from part releases;
    - REWORK_INJECTION origin only from defect evidence;
    - DURATION_OVERRUN and SAME_TEAM proposals only;
    - PLAN_CHURN from cancellations;
    - revisions and withdrawals of earlier automatic claims.
  - **Union relief.** Also the worker handlers for claims and decisions.
- `focus/performance/history.py`: append-only evaluation revisions (unchanged evidence is a
  no-op), as-known replay, evidence records, and `worker_ie_review` (never rewrites issued goals).
- `focus/publication/service.py`: `verify_publication` and `accepted_plans` (the end-to-end
  verification reused by the goal registry; acceptance order by head version).
  `focus/publication/repository.py`: `accepted_ids`.
- `focus/data/fixture_builder.py`: `build_observations()`, the deterministic 2026-10-26 S1
  scenario with three collection points. `focus/pipeline.py` verifies and writes
  `fixtures/inputs/manufacturing_demo/observations/*.json`.
- `focus/data/workforce.py`: `person_namespace` (one identity authority).
  `focus/data/sql.py`: allowlist for the proposed landing tables. `focus/cli.py`: `--as-of`.
- `config/policies/grading-demo-v1.json`: `lifecycle.reopen_after_completion`,
  `observations.planned_credit_window`, `observations.max_feed_age_hours`,
  `disruption.notes_min_chars`, `disruption.reviewer_roles`, `disruption.self_review_allowed`
  and `disruption.overrun_auto_excuse` (`NEVER`, user_requirement).
  `config/policies/rebuild-v1.json`: OD-15..OD-17.
- `queries/actuals/*.sql` (7 read-only drafts), `sql/migrations/lifecycle_draft.sql`,
  `tools/collect_observations.py`.
- `docs/execution/PERFORMANCE_POLICY.md`, `DECISIONS.md` (D-16, OD-15..17),
  `SQL_SCHEMA_LEDGER.md`.
- `tests/integration/test_lifecycle_excusals.py` (21 tests), `tests/conftest.py`
  (`observed_world`).

## Commands and results

```
.venv/bin/python -m pytest tests -q                                                -> 323 passed (6:19)
.venv/bin/python -m pytest tests/integration/test_lifecycle_excusals.py -q        -> 21 passed
.venv/bin/python -m pytest tests/unit/test_query_files.py -q                       -> 3 passed (queries/actuals read-only, allowlisted, no hidden placeholders)
python -m focus.cli demo-bootstrap                                                  -> observations/*.json written/verified (7 files)
python -m focus.cli collect-observations --mode fixture --as-of 2026-10-26T19:30:00Z -> 6 feeds usable; 21 events admitted, 1 duplicate, 4 + 2 quarantined
python -m focus.cli evaluate --shift DEMO:2026-10-26:S1 --policy rebuild-v1 --as-of ... -> PROD-A 80.9 B, PROD-B 75.5 C -> 97.7 A (rev 2), QA 45.8 F -> 61.5 D (rev 2)
python tools/collect_observations.py --mode fixture --all-points                    -> replays the manifest points in order
```

The end-to-end CLI sequence ran across separate processes on a persistent runtime: bootstrap →
acquire → schedule → publish → collect ×3 → evaluate ×3. Collection takes ≈0.1–0.7 s after the
one-time snapshot load, which is cached per immutable run and policy.

## Acceptance cases

| Case | Observed |
| --- | --- |
| A54 | **Taxonomy.** Exactly 8 causes and 5 nominal ones. Policy overrides are refused (`USER_CONSTANT`) and a tampered bundle is refused at runtime. **Claims.** All eight causes plus a duplicate are submitted against PROD-B's missed ROOT3 units. SAME_TEAM_PREDECESSOR/DURATION_OVERRUN without notes give `NOTES_REQUIRED`, a claim on an earned unit gives `UNIT_ALREADY_EARNED`, and claims alone give E = 0. **Decisions.** Nominal approvals are not exceptions. Mixing PLAN_CHURN gives `PLAN_CHURN_SEPARATE_REVIEW` and a thin exception gives `REASON_REQUIRED`. The exception approvals and PLAN_CHURN (own track) are recorded exceptions. **Relief.** 9 claims × 168 = 1512 claimed minutes, but E = 168 = the frozen missed effort (union), and B0 stays 408. **Feed.** In the integrated feed, the duplicate REWORK_INJECTION claims and the overlapping SAME_TEAM approval give E = 168, not the 456 sum. **Review rules.** `SELF_REVIEW`, `AUTHORITY_NOT_REVIEWER`, `DECISION_ALREADY_SUPERSEDED` and `STALE_VERSION` are enforced. The appeal DEC-B-04 supersedes DEC-B-03 and both stay stored. |
| A55 | **Rework origin.** The injected NC3B rework is executed by PROD-B and its defect origin is PROD-A (NCR-303 defect evidence), so the automatic REWORK_INJECTION claim has origin PROD-A and executor PROD-B. A manual claim naming the executing team (PROD-B) gives `ORIGIN_TEAM_UNEVIDENCED`. Without the defect row, no rework claim is produced at all; the origin is never guessed. **Reversed DAG.** Flipping or removing edges leaves the origin unchanged. **Culpability.** QA's misses blame the culpable unblocked PU3 WORK (PROD-B), not the immediate neighbour. QA is never blamed automatically, and the manual claim blaming QA is rejected and the rejection upheld on appeal. **Burn.** NC3 at 1.06x (below 3x) and PU3 at 5.00x (above 3x) are DURATION_OVERRUN *proposals* only: no decision and no relief. |
| A56 | **Cancellations.** The ODA buyoff cancelled before completion is MISSED, earns nothing and stays in B0 = 200. NC3's verified completions survive the later administrative job cancel under `PRIOR_VERIFIED_COMPLETION_EARNS`; the same facts under `CANCELLATION_REVOKES_CREDIT` revoke it, and an unknown policy value is refused. **Reopen and void.** PU3 final QA was reopened (Cp 45, 45.8 F). Voiding the reopen late appends revision 2 (Cp 90, 61.5 D) that supersedes revision 1. The as-known replay at 10-27 08:00 returns revision 1, which is unchanged. **Other.** A reported completion is a claim, never credit. Terminal status or disappearance without verified completion is UNKNOWN (evaluation unknown, B kept); a later verified completion resolves it. Revision, void, void-of-void and correction semantics are covered. |
| A26 | ROOT4's actual start on 2026-10-23 12:00Z with PRE4 incomplete is a persisted discretionary violation: the actual effective time, not backdated or re-anchored to the cutoff, incomplete `PRE4/WORK/0`, known incomplete. After PRE4 is verified complete on 10-28, the record is byte-identical and never erased. The accepted plan still orders PRE4 before ROOT4, and the ROOT4 continuation's dispatch check stays BLOCKED (`PREDECESSOR_INCOMPLETE`). The OOS record legalizes nothing. |

Further checks:

- A planned OOS is charged once: O = 10% of the 80 planned ROOT3 WORK0 minutes. The earned segment
  keeps Cp.
- PROD-A's offplan B2 OOS stays compliance evidence with no numeric O, because its effort is
  already in U.
- An uncertified empty feed or missing feeds give UNKNOWN (never "zero excuses").
- An open window gives UNKNOWN with `WINDOW_OPEN`. The live reader gives `NOT_PROBED`.
- A later replan's goals are proposals, and B0 stays with the first acceptance.
- Commands run as durable worker runs with receipts. Self-approval fails `SELF_REVIEW`. An IE
  review never affects issued goals.

## Fixture versus live

All facts come from the deterministic S18 observation fixture. Live observation producers are
proposed: the landing tables and readers are drafted, live collection is `NOT_PROBED`, and
official evaluation stays UNKNOWN (OD-15). Reviewer identity mapping is the operator's (OD-17).

## Remaining gaps

- Install `lifecycle_draft.sql` and real producers through authorized migration tooling (P23).
- The SQL observation store adapter arrives with the consolidated SqlStore (P23).
- The `POST /amendments` command handler (`worker_amendment`) with feasibility validation arrives
  with the P19–P21 command API. Amendments are fixture-feed only today.
- CAPACITY_SHORTAGE and SKILL_SHORTAGE are evidence-backed manual claims. Automatic attribution
  needs live attendance withdrawal and skill evidence feeds.
