# Stage P16: Immutable publication, pre-sign readback and dispatch

Status: **fixture_verified**. This is a fixture-readback rehearsal: no live SQL stage, signature,
acceptance or head move was performed.

## Changed paths

- `focus/publication/serializer.py`: `build_artifact` binds the following into a `PlanArtifact`:
  - scope, purpose and capability (`EXECUTABLE_DISPATCH | FORECAST_ONLY`);
  - source run, snapshot and manifest hashes, policy and timezone;
  - the full hard-edge graph and the same-run CS lineage;
  - the candidate: crews, duties, economics and the complete inventory;
  - today's frozen goals: segment earning units with exact largest-remainder effort, admitted
    membership, and the S14 gate context (base = effort/5; bonus = base × 35% × min(age/5, 1)).

  `artifact_bytes` is strict canonical JSON, and `decode_artifact` accepts only identical
  re-encodings.
- `focus/publication/signing.py`: domain-separated HMAC-SHA256 over `FOCUS_PLAN_V1\0` plus the
  canonical binding. The key id is tracked separately, and the key comes from `env:` or `vcap:`
  references. The fixture TEST key is public and refused for live. Key material never reaches a
  repr, receipt or client.
- `focus/publication/repository.py`:
  - a write-once private unsigned stage with byte-exact readback;
  - immutable goals, published plan, payload and receipt, then the head LAST (lock plus
    compare-and-set);
  - the SQL adapter refuses with `SQL_STAGE_NOT_INSTALLED`.
- `focus/publication/service.py`: `publish` runs the S13 order:
  final → serialize → detached → stage → readback → readback validation → sign → verify →
  accept/head-last → post-acceptance (incumbent recorded; FORECAST feeds delivery commitments).
  - **Recovery:** deterministic ids and an idempotent reconcile.
  - **Refusals:** `STALE_REPLAY`, `NOT_CURRENT`, `UNAUTHORIZED_SIGNER`, `TEST_KEY_NOT_LIVE`, live
    `UNAVAILABLE`.
  - **Read path:** `current_plan` verifies the accepted head end to end.
- `focus/publication/dispatch.py`: `dispatch_check` rechecks the action against the current
  source facts: plan, capability, freshness, predecessors, parts and shortages, crew membership
  and certificates, carry-in, calendar, CS and window. A blocked action writes an idempotent
  repair request.
- `config/policies/rebuild-v1.json`: `goals.obligation_days` = 1 (manual_specified) and
  `dispatch.max_source_age_hours` = 24 (fixture_default).
- `sql/migrations/publication_draft.sql`: append-only triggers, StagePlan (sha-checked,
  idempotent), ReadPlanStage, RecordValidation, AcceptPlan (applock, exact stage bytes, clean
  READBACK receipt, goals, head LAST with expected version) and the CurrentPlan view.
- `tools/publish_plan.py`, `docs/execution/DEPLOYMENT.md` (plan signing),
  `docs/execution/SQL_SCHEMA_LEDGER.md`, `tests/integration/test_publication_recovery.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/integration/test_publication_recovery.py -q   -> 23 passed
.venv/bin/python -m pytest tests -q                                            -> 277 passed
python -m focus.cli publish --candidate <id> --mode fixture --rehearse-readback  -> ACCEPTED, fixture_readback, head plan/DEMO-787/EXECUTABLE v1 (~1.5 s)
python -m focus.cli publish ... (again)                                         -> ALREADY_ACCEPTED
python -m focus.cli dispatch-check --plan <plan> --action <assignment>
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A48 | A stage whose readback differs from the serialized bytes is rejected by the independent READBACK validation and by the byte comparison. The corruptions tested are crew, root edge, timezone, station/load, offset and source hash. Nothing is signed or accepted, the head is unchanged, and the invalid READBACK receipt is durable. Same-run CS lineage is included. |
| A51 | **Crash points.** A crash is injected after stage, after readback, after signing, after the published plan is written, before the head moves, after the head moves, and after post-acceptance (client timeout). **Before the pointer moves:** the previous accepted head stays current and verifies. **Recovery:** it reconciles exactly once (`ACCEPTED`, `RECOVERED` or `ALREADY_ACCEPTED` as appropriate). The head ends at version 2 with a single receipt that matches the published content. A further retry is `ALREADY_ACCEPTED`. A signed but uncommitted publication is never reported as success. |
| A52 | **Tampering.** A raw-corrupted payload, a re-sealed payload with altered crew, and a re-sealed receipt with a forged signature each give `SECURITY_FAILURE`; nothing becomes current and dispatch is blocked. **Replay and scope.** Replaying the older plan is `NOT_CURRENT` and dispatching it is `STALE_PLAN`. A forged head in another scope gives `SECURITY_FAILURE: wrong scope`. **Signers.** A rogue signer is `UNAUTHORIZED_SIGNER`; a TEST key with a non-fixture repository is `TEST_KEY_NOT_LIVE`. **Other refusals.** A skipped required check is rejected with nothing staged. A synthetic person in an executable candidate fails V5. Live SQL is `UNAVAILABLE` after the local rehearsal. An accepted FORECAST plan is `FORECAST_ONLY` and never dispatchable as executable work. |
| A53 | After an accepted plan, a new current source run with a revoked certificate (`CERT_NOT_HELD`), a reopened shortage (`PART_NOT_RECEIVED`), an incomplete predecessor (`PREDECESSOR_INCOMPLETE`) or withdrawn attendance (`ATTENDANCE_WITHDRAWN`) blocks the affected action. A repair request is written, the accepted payload bytes are unchanged, and the plan stays accepted. |

## Fixture versus live

The fixture runs the complete protocol on the persistent fixture store with the TEST key and
labels receipts `fixture_readback`. The live SQL stage, readback and acceptance objects are
drafted, not installed. A live publication runs the local rehearsal and then stops UNAVAILABLE:
no signature, acceptance or head move.

## Remaining gaps

- Install `publication_draft.sql` through authorized migration tooling (P23), then add a live
  repository adapter.
- Live signer custody (key, rotation, authorization) must be supplied by the operator.
- Excusal and amendment records on frozen goals arrive with P17/P18.
