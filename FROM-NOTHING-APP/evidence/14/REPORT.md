# Stage P14: Incumbent defense and persistent delivery commitments

Status: **fixture_verified**

## Changed paths

- `focus/engine/commitments.py`.
  - **Accepted incumbent.** `record_accepted_incumbent` writes an immutable ACCEPTED artifact,
    then moves the head last with compare-and-set. `import_fixture_incumbent` uses the same
    path, fixture only.
  - **Lookup.** `lookup_incumbent` gives a verdict: `LOADED`, `NO_INCUMBENT`,
    `ARTIFACT_MISSING`, `INCOMPATIBLE_SCOPE`, `INCOMPATIBLE_POLICY` or `INCOMPATIBLE_SOURCE`
    (incumbent cutoff after the snapshot, or a foreign person namespace).
  - **Defended tasks.** `defended_tasks` turns offsets 0/1/2 into `DefendedTask`s and records
    COMPLETED, OUT_OF_HORIZON and phase-missing results.
  - **Hysteresis.** `observe` is pure: hold below 2 days, bad news at 7 or more, otherwise 2
    distinct confirmations within 1 day. Improvements wait too. Replays are a no-op, unknown
    never invents a date, and a safety alert is raised on onset.
  - **Persistence.** `DeliveryCommitments` keeps versioned state with an outbox that a later
    state write acknowledges. Event ids include the version they came from, CAS conflicts
    retry, cohort exit and reentry are handled, and each accepted plan gets one immutable
    receipt.
- `focus/engine/projections.py`.
  - completion minutes (MAX step 5);
  - local-date delivery projections (unknown stays None);
  - safety alerts from UNSAFE commitments;
  - `record_accepted`: only accepted plans of `forecast.delivery_commitment_purpose` feed the
    ledger, and replays return the stored receipt.
- `focus/engine/max/optimizer/greedy.py`.
  - **Dispatch classes.** Ready commitments go first, then their hard-edge ancestors, then
    other work.
  - **Two attempts.** Exact slot and crew, then the same slot with an alternate full crew.
    A commitment that fails both, or that a safety fact invalidated, drops to the ordinary
    class before its minimal audited move or deferral.
  - **Unreached commitments.** A defended phase that never becomes placeable is `UNSAFE` with
    its cause.
  - Floor reasons are printed in ISO time.
- `focus/engine/max/optimizer/orchestrator.py`.
  - The key is (defended, exact, ...).
  - `MaxOptimizer.better` rejects any candidate that loses or downgrades a kept commitment.
  - The incumbent receipt is in `AlgorithmStatus`.
- `focus/pipeline.py`: `demo-bootstrap`.
  - It verifies the 45 deterministic inputs against the builder; drift is reported, never
    overwritten.
  - It records the accepted incumbent for EXECUTABLE and FORECAST.
  - `demo-run` stays unavailable until P24.
- `config/policies/rebuild-v1.json`: `forecast.delivery_commitment_purpose` = FORECAST,
  recorded as open decision OD-14 and noted in `docs/execution/DECISIONS.md`.
- `sql/migrations/commitments_draft.sql`.
  - Commitment checks.
  - Tables: `CommitmentObservation` (replay key per accepted plan), `DeliveryReceipt`, and
    the `CommitmentResult` projection.
  - Immutability triggers and the `IncumbentAtHead` view.
  - `RecordDeliveryObservation`: one transaction with an optimistic version check.
  - A state readback view.
- `tests/unit/test_commitments.py`, `tests/integration/test_incumbent_defense.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_commitments.py -q              -> 15 passed
.venv/bin/python -m pytest tests/integration/test_incumbent_defense.py -q -> 8 passed
.venv/bin/python -m pytest tests -q                                       -> 203 passed
python -m focus.cli demo-bootstrap --scenario manufacturing_demo          -> ok, 45 inputs verified, 2 incumbents
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A42 | **Contest.** Without an incumbent, uncommitted B1 takes A1-02 at A1's committed minute. With the accepted incumbent, the ready commitment is dispatched first. **Attempt 1.** A1, NC3 (day 0) and B1 (day 1) are KEPT: exact minute, exact full crew, `attempt1 = OK`. **Attempt 2.** A2 (day 2, A1-06 on leave) is CREW_CHANGED: `attempt1 = NOT_POOL_MEMBER ... A1-06`, attempt 2 at the same 06:15 on the same shift, a full alternate crew, each member individually qualified. **Day 3.** B2 is OUT_OF_HORIZON and repacked earlier, not pinned. **Both attempts fail.** With one shared crew (N=3, cap 2), one commitment is KEPT and the other is MOVED exactly to "crew free + one 15-minute transfer", with both failed attempts recorded. With no slot left the outcome is DEFERRED, with no forced placement. Outcomes persist in the candidate and the duty reconstruction is clean. |
| A43 | **Fresh process.** In separate processes: acquire, then schedule (`NO_INCUMBENT`: neither the inputs' JSON nor a candidate counts), then `demo-bootstrap`, then schedule again. The last run is `LOADED` with the bootstrap's id: 3 KEPT, CREW_CHANGED, OUT_OF_HORIZON. **Incompatible.** Foreign policy gives `INCOMPATIBLE_POLICY`; a future cutoff or a foreign namespace gives `INCOMPATIBLE_SOURCE`; a dangling head gives `ARTIFACT_MISSING`. Another purpose's incumbent is never borrowed. **Late part.** In EXECUTABLE, B1 is `UNSAFE: SAFETY_OVERRIDE: PART_NOT_RECEIVED ... DEMO-P-0009` and not placed. In FORECAST it is `UNSAFE: FLOOR_AFTER_COMMITTED_START: earliest 2026-10-30T04:00Z`, moved to the earliest safe slot and labelled conditional. **Expired cert.** A1-01's expired cert makes A1 CREW_CHANGED with `NOT_QUALIFIED`, and A1-01 is placed nowhere. **Calendar.** A NONWORKING 10-27 makes B1 `UNSAFE: CALENDAR`, moved off 10-27, while A1 stays KEPT. |
| A49 | **Hysteresis.** +1 and −1 HOLD, with the raw risk shown. +2, +6 and −3 go PENDING, then CONFIRMED on the second distinct run. +7 is BAD_NEWS immediately. −10 still waits. **Tolerance.** Targets 1 day apart agree; 2 days apart restart the count. **Replays and alerts.** Replays never count, and unknown never fabricates or confirms a date. A safety alert is immediate while the date is held, and is not re-raised while it persists. |
| A50 | **Replay and reject.** A replay returns the stored receipt (`REPLAY`); a rejected plan changes nothing. **Cohort.** Exit and reentry keep the original and current dates: one exit, one reentry. **Crash before acknowledgment.** The orphan event stays invisible; recovery in a fresh process re-derives the same event id once. **Crash before the receipt.** Recovery writes the same receipt, then `REPLAY`. **Concurrent writer.** CAS retry; the lost race's event stays an unacknowledged orphan, and there is exactly one BAD_NEWS. |
| Delivery feed | An accepted FORECAST plan establishes every aircraft. Unknown completions stay UNKNOWN, and B1's invalidated commitment raises SAFETY_ALERT on 1302. A replay returns `REPLAY`; an EXECUTABLE plan is `NOT_APPLICABLE`. |

## Fixture versus live

The incumbent is the S18 fixture accepted plan, recorded through the accepted-artifact path.
Live incumbents come from published plans (P16, `FOCUS_FN_PlanHead`). Delivery state runs on
the fixture store's versioned state and acknowledged outbox. The SQL equivalent (one
transaction per observation) is drafted and not installed. No production signing or
promotion happens in this stage.

## Remaining gaps

- Publication (P16) must call `projections.record_accepted` at acceptance and record live
  incumbents through `record_accepted_incumbent` / `FOCUS_FN_PlanHead`.
- The live SqlStore adapter for these collections (P23).
- OD-14: the delivery owner must confirm which purpose drives delivery commitments.
