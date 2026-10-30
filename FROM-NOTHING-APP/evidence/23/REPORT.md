# Stage P23: SQL migrations, worker orchestration and CI

Status: **fixture_verified**. Everything here ran on fixture runtimes and a fixture catalog. **No SQL was installed, no
grant was applied, no CI pipeline was pushed or activated, and no host was deployed.** The live paths exist as exact,
reviewable artifacts with typed refusals until an authorized installer acts (`docs/execution/INSTALL_AND_PIPELINE.md`).

## Changed paths

### Migrations

- `sql/migrations/`, the versioned set:
  - `001_focus_fn_core.sql`: S06 verbatim, unchanged.
  - `002_worker_pipeline.sql` (new): `WorkerRun` status and lease invariants plus role, attempts and timestamps;
    `PipelineRun` and `PipelineStep` (role, input/output hashes, lease, attempts, version); the append-only `Outbox`
    with its trigger.
  - `003_source_promotion.sql` ... `009_evaluation.sql`: the reviewed P04-P18 drafts, consolidated. Their statements
    are identical to the drafts (comment-stripped comparison); only the headers changed. The drafts were removed.
  - `rollback/001`-`009` (new): generated from each version's declared objects. Code objects drop outright; tables
    drop only when empty; added columns drop only when no row holds a value.
- `sql/grants/focus_fn_roles.sql` (new): generated from `ROLE_GRANTS`. Nine roles; the installer is a DBA principal
  and no script grants DDL.
- `focus/migrations.py` (new):
  - discovery (versioned files only) and checksums;
  - static lint: naming, primary keys, reference and binding order, `datetime2(6)`, `varbinary(max)`, bigint
    identity, no `TRUNCATE` or `DROP`, `DELETE` only in locked retention procedures, nullable added columns;
  - rollback and grant lint, with freshness of both generated files;
  - the plan (`APPLIED`, `PENDING`, `COLLISION`, `CHECKSUM_MISMATCH`, `DRIFT`, `UNKNOWN_RECORDED_VERSION`, `BLOCKED`);
  - `FixtureCatalog` (atomic per version, injected failures, attempt receipts) and `SqlCatalog` (the live adapter,
    never connected);
  - `apply_pending`, `rollback_latest` and the reviewable bundle;
  - the confirmed live installer path (`--installer` plus `--confirm-manifest <sha256>` plus the CREATE TABLE
    privilege).
- `tools/migrate.py` (new).

### Preflight and live storage

- `focus/preflight.py` and `tools/runtime_preflight.py` (new): host preflight per role (web, worker, signer,
  installer). It checks CPython 3.12, settings and platform (Posit Connect, Tanzu, local), trusted identity, a
  non-ephemeral session secret, SQL by presence, the policy, storage, `create_app`, migrations and the optional solver.
  `--probe-sql` compares the installed ledger read-only. No secret value is ever printed.
- `focus/data/sql_store.py` (new): the live store module that `runtime.default_store` imported but that **did not
  exist**. A live host with SQL credentials used to crash at startup. It now starts, with one typed
  `REPOSITORY_UNAVAILABLE` reason, until 001-009 and the live repository adapter exist.

### Orchestration, workers and retention

- `focus/orchestration.py` (new): the durable, role-separated pipeline
  (`acquire`, `admit`, `schedule`, `validate`, `stage_readback`, `sign_accept`, `observe_evaluate`).
  - Needs-gated steps with input hashes.
  - Optimistic-version leases with expiry takeover; at most three attempts.
  - Exactly one outbox event per completed step, re-emitted after a crash.
  - A role run that only waited reports `blocked` (non-zero exit).
- `focus/publication/service.py`: `publish(until="readback")`. The stager stops after the independent readback
  validation, so the signer signs only re-read, re-validated bytes.
- `focus/pipeline.py`: `observe_and_evaluate`, one authority for `demo-run` and the evaluator step.
- `focus/workers.py`: `KIND_ROLES` and `worker --role`.
- `focus/retention.py` and `tools/retain_history.py` (new): non-destructive retention with the operator's own period.
  Accepted history, references, evidence and legal holds are preserved. Fixture archives carry SHA-256 receipts and
  restore byte for byte.

### CLI, CI, hosting files and docs

- `focus/cli.py`:
  - new commands `preflight`, `pipeline` and `retain-history`;
  - `migrate --catalog --installer --confirm-manifest --rollback --write-generated`;
  - `worker --role`.
- `.gitlab-ci.yml` (new, draft): verify; then source, app, stager, signer (manual, protected) and evaluator jobs.
  Each runs one role with `needs`, `resource_group` per scope and receipts-only artifacts.
- `manifest.yml` and `Procfile`: the Tanzu worker runs app-role kinds only (`--role app`).
- References to the removed drafts now name the versioned files in `focus/publication/repository.py`,
  `focus/data/sql.py`, `queries/actuals/*.sql`, the policy's OD-15 text (not hashed: policy hash unchanged,
  `98a57f5e...`) and the docs.
- Docs:
  - `docs/execution/INSTALL_AND_PIPELINE.md` (new);
  - `SQL_SCHEMA_LEDGER.md`, `DEPLOYMENT.md`, `DECISIONS.md` (D-21).

### Tests

- `tests/integration/test_migration_pipeline.py` (new, 22 tests).

## Commands and results

| Command | Result |
| --- | --- |
| `python -m pytest tests -q` | **471 passed, 3 skipped** (the opt-in `benchmark` sizes), 0 failed (16:43) |
| `python -m pytest tests/integration/test_migration_pipeline.py -q` | 22 passed |
| `python tools/check_imports.py` | ok: 103 modules, optional packages lazy, no runtime writes |
| `python -m focus.cli migrate --dry-run` | ok: 9 versions, 0 findings (`cli/migrate_dry_run.json`) |
| `python -m focus.cli migrate --emit-dir <dir>` | bundle with `MANIFEST.json` (`MANIFEST.json` here) |
| `python -m focus.cli migrate --apply --installer fixture-dba` (fixture catalog), then again | 9 applied, then 0 (`cli/migrate_apply_fixture.json`, `cli/migrate_replay_fixture.json`) |
| `python -m focus.cli migrate --dry-run --catalog live` / `--apply --catalog live` | `not_probed` / `refused INSTALLER_SESSION_REQUIRED` (no credentials in this session) |
| `python -m focus.cli preflight` | ok; only warning: the ephemeral fixture session secret (`cli/preflight_fixture.json`) |
| `python -m focus.cli pipeline --key evidence-23 --role signer` before the readback | `blocked`, exit 1 (`cli/pipeline_signer_first_blocked.json`) |
| `pipeline --role source/app/stager/signer/evaluator`, then `--status`, then `--role all` | every step completed in order; the replay is all `ALREADY_COMPLETED` (`cli/pipeline_*.json`) |
| `python -m focus.cli retain-history --keep-days 30` | ok: nothing eligible in a fresh runtime; preserved collections listed (`cli/retain_history_plan.json`) |

## Acceptance cases

| Case | Result |
| --- | --- |
| A05 | **Pass.** A mandatory dataset fails midway inside the pipeline: `acquire` FAILED (`ACQUISITION_REJECTED`) and the old source head is unchanged. Everything downstream is BLOCKED, and the replayed key stays rejected until `EXHAUSTED`. The certified-empty versus unavailable distinction stays covered by the P04 tests in the same suite. |
| A07 | **Pass.** The process is killed after the acquisition and before the step is recorded. After lease expiry the takeover replays the same idempotency key: one source run, the head on it, no duplicate facts. |
| A50 | **Pass.** The signer is killed after acceptance and before the step is recorded. The takeover's `publish` reconciles (`RECOVERED`/`ALREADY_ACCEPTED`). Delivery-commitment state (version and bytes) and the published plans are unchanged: no extra confirmation. |
| A51 | **Pass.** The signer is killed after signing and before acceptance: the step FAILED, no plan was published and the old head stayed. The retry accepts once (one plan per purpose). The stager's `until="readback"` stage never signs. |
| A71 | **Pass.** A source failure blocks promotion. A competing worker gets `LEASED`, and an expired lease is taken over while the stale owner's late write is refused (`LEASE_LOST`). A migration replay applies nothing. Checksum mismatch, collision and mid-version failure each record nothing. Rollback is latest-only and data-free. A signer started before the readback is `BLOCKED` and the CLI exits non-zero. The CI draft chains the roles with `needs` and a manual, protected signer. |

## Fixture versus live

| Area | Fixture | Live |
| --- | --- | --- |
| Migrations | Rehearsed against the fixture catalog | **Not installed.** `SqlCatalog` and the confirmed installer path are untested live. |
| Grants | Generated and linted | **Not applied** |
| Pipeline | Runs in the fixture store | The live adapter for the 002 tables is pending, so steps report typed unavailability |
| Hosts | — | **Not deployed.** Preflight was exercised with simulated Tanzu and Posit Connect environments only. |
| GitLab CI | — | **Never pushed** to a GitLab remote |

## Remaining exact gaps

- **The live repository adapter**, mapping the repository protocol onto the FOCUS_FN tables, is not built. Live hosts
  stay `REPOSITORY_UNAVAILABLE` until it exists and 001-009 are installed by an authorized installer.
- **Live observation producers (OD-15) are not installed**, so the evaluator step refuses live
  (`LIVE_OBSERVATIONS_UNAVAILABLE`).
- **Collision probe.** Run `queries/discovery/fn_collisions.sql` against the target before any install. The
  2026-10-27 observation is not current proof.
- **Posit Connect scheduling.** How pipeline roles are scheduled on Connect depends on that server's configuration.
  The documented options are an external scheduler (the CI draft or Tanzu tasks) or scheduled content where the
  server permits it.
