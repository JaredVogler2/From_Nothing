# Rollback and recovery (P24)

**Status: these procedures are rehearsed on fixtures only** (the crash, replay and recovery tests named in
`evidence/24/ACCEPTANCE.md`). Nothing was deployed, so no live rollback has been performed.

Every rollback preserves:

- accepted plans, their payloads and receipts;
- original goals (B0);
- source runs;
- lifecycle events, claims and decisions;
- delivery commitment state;
- evaluations, recognition and badges.

Nothing in this application deletes that history, and retention only archives unreferenced fixture candidates
(`retain-history`). The one thing deleted is display parts nothing reads any more (`display-retention`, D-24), a cache
rebuilt from the kept records with `python -m focus.cli display` and checked against its kept manifest.

## 1. Application rollback (code)

- **Posit Connect:** activate the previous bundle in the content's settings, or redeploy the previous reviewed commit
  with `rsconnect deploy api`.
- **Tanzu:** `cf rollback <app> --version <n>` where revisions are enabled, or `cf push` the previous reviewed commit.
  Roll back the web, worker and role apps together, so that every process runs one implementation hash.
- **Afterwards:**
  - Run `python -m focus.cli review --verify NNN` for the review of the restored commit, and
    `python -m focus.cli preflight --role <role>` on each process.
  - A code rollback never touches stored records.
  - A code rollback across a migration version is possible only if the older code reads the newer schema. Versions
    are additive by lint (no `DROP`, nullable added columns), so they usually can.

## 2. Plan rollback (data)

**There is no pointer rewind.** Plan heads move only forward, and acceptance moves the head last. A historical plan
never becomes executable by itself: `publish` refuses stale replays, and dispatch refuses any plan that is not
current (`STALE_PLAN`).

To return to earlier plan content:

1. Re-run the pipeline under a **new key** on the current source.
2. Validate the result again; dispatch re-checks every action against current facts.
3. Accept it through the signer as a new plan.

The earlier plan stays in history unchanged. Its frozen goals remain the goals of record for the shifts they
covered, and amendments add to them without mutating B0.

To stop work while a corrected plan is prepared, stop the signer step (it is manual) and let dispatch checks block
actions whose facts changed. Every block carries a typed reason and a repair request.

## 3. Interrupted publication (signed but not accepted, or client timeout)

Reconcile through durable receipts, never by file presence:

- Re-run `pipeline --role signer` for the same key, or `publish` for the same candidate.
- `publish` reconciles the stage, the readback, the signature and the acceptance receipt. It returns `RECOVERED` or
  `ALREADY_ACCEPTED` when the acceptance was durable. Otherwise it re-stages the identical bytes and accepts once.
- A signature alone is never success: the previous head stays until the atomic acceptance (A51).
- Delivery commitments are fed once per accepted run. A takeover after acceptance adds no duplicate change (A50).

## 4. Source acquisition

- A failed or partial run never becomes the head; the old head stays (A05).
- Replaying the same idempotency key reconciles to one run and one head (A07).
- A run that crashed before admission is resumed under its key, or abandoned under a new key. It is never promoted
  by hand.

## 5. Evaluations and recognition

- Corrections append a new evaluation revision with a reversal (A56, A62). Prior XP, badges and recognition are never
  rewritten.
- A code or policy rollback does not re-grade history. A new evaluation runs only from new evidence, as of a stated
  instant.

## 6. Schema rollback

- Recovery is **forward by default**: fix the migration and apply the next version.
- `python -m focus.cli migrate --rollback <latest>` (fixture), or the same with `--catalog live` and confirmation,
  rolls back only the latest version, and only while it holds no data. A table is dropped only when empty, and an
  added column only when no row holds a value.
- No rollback ever drops a table holding history.

## 7. Signing key rotation

Verification uses the one configured key id. Rotating the key therefore makes signatures under the old key
unverifiable by the running processes:

- the current plan fails verification and cannot be dispatched until a plan is accepted under the new key;
- older plans stay stored, unchanged and readable as history.

This fails closed. Verifying old signatures after a rotation needs the old key in a verification-only context, which
is not built. Rotate only together with a new acceptance.

## 8. Pipeline steps

- A step whose lease expired is taken over and re-runs idempotently. After three attempts it is `EXHAUSTED`: start a
  new key, and the earlier pipeline stays as history.
- Each completed step has exactly one outbox event, re-emitted after a crash. Consumers deduplicate by event id.
