# Install, start, pipeline and recovery (P23)

**Status: nothing here has been executed against a live system.** No SQL was installed, no grant applied, no CI pipeline
activated and no host deployed by this build. This page lists the exact, reviewable steps for operators who hold the
authority. Every command was rehearsed locally against fixture runtimes and a fixture catalog (`evidence/23/`).

Hosting is **Posit Connect** first, then **VMware Tanzu**, with **Python 3.12** on both (`runtime.txt`,
`requires-python`). See `DEPLOYMENT.md` for the platform settings.

## 1. Migrations (installer role)

The versioned set is `sql/migrations/001`-`010`. Each version has a generated rollback in `sql/migrations/rollback/`,
and roles and grants are in `sql/grants/focus_fn_roles.sql`.

| Version | Content |
| --- | --- |
| 001 | S06 core tables, verbatim |
| 002 | Worker-run invariants, durable pipeline steps, append-only outbox |
| 003 | Source-run admission and head promotion procedures |
| 004 | Workforce inference integrity, head view, retention procedure |
| 005 | Duration evidence integrity, head view, retention procedure |
| 006 | Delivery commitments, observations, receipts |
| 007 | Immutable publication: stage, readback, validation, accept (head last) |
| 008 | Lifecycle landing tables, actuals, claims, reviewed excusals, observation runs |
| 009 | Evaluation revisions, evidence, recognition, badges |
| 010 | Light per-team-shift performance rows (`TeamShiftPerformance`) and compact job records (`JobOutcome`, `JobName`) (D-25) |

Version 010 is new (D-25), not a consolidated draft. Versions 003-009 consolidate the reviewed P04-P18 drafts. Their statements are unchanged (verified by a
comment-stripped comparison).

The runner (`focus/migrations.py`) never runs at import or start. Its rules:

- **Checksum ledger.** It records each script's SHA-256 in `WEB_APP.FOCUS_FN_SchemaVersion`. If a recorded checksum
  differs, every later step is blocked.
- **Static lint.** Every version passes the lint before anything runs:
  - FOCUS_FN naming;
  - a primary key on every table;
  - references only to earlier tables;
  - views and triggers bound only to objects that already exist;
  - `datetime2(6)` instants and `varbinary(max)` payloads;
  - bigint identities;
  - no `TRUNCATE` or `DROP`;
  - `DELETE` only in the two locked retention procedures;
  - added columns nullable or with a default;
  - every version ships a rollback, and the rollback lint allows only its own objects, guarded drops and no `DELETE`;
  - the generated rollbacks and grants script are fresh (`ROLLBACK_STALE`, `GRANTS_STALE`): regenerate both with
    `python -m focus.cli migrate --write-generated`;
  - the grant lint (separation of duties, below).
- **Plan.** The plan against the catalog names `APPLIED`, `PENDING`, `COLLISION`, `CHECKSUM_MISMATCH`, `DRIFT`,
  `UNKNOWN_RECORDED_VERSION` and `BLOCKED`.
- **One transaction per version.** Each version takes `sp_getapplock('FOCUS_FN_MIGRATIONS')` and verifies every
  declared object before writing the ledger row. A failure records nothing and leaves nothing behind; the attempt
  receipt is kept.
- **Rollback.** Only the latest version can be rolled back, and only while it holds no data. A table is dropped only
  when it is empty, and an added column only when no row holds a value. Recovery is forward by default.

### Steps

```bash
python -m focus.cli migrate --dry-run
```

This is the static lint plus the fixture-catalog plan. Exit 0 means it is clean.

```bash
python -m focus.cli migrate --emit-dir install-bundle
```

This writes the reviewable bundle: scripts, rollbacks, grants and `MANIFEST.json` with every SHA-256. The result
prints `manifest_sha256`.

```bash
python -m focus.cli preflight --role installer --probe-sql
```

Run this in the installer's session. It compares the target's installed ledger with the shipped checksums,
read-only.

```bash
python -m focus.cli migrate --dry-run --catalog live
```

This plans against the live catalog with the session's credentials. Look for collisions and unaccounted objects.

```bash
python -m focus.cli migrate --apply --catalog live --installer "<subject>" --confirm-manifest "<manifest_sha256>"
```

This applies the pending versions. It refuses without a reviewed manifest checksum, and refuses unless the principal
can `CREATE TABLE`.

Before any of this, re-run `queries/discovery/fn_collisions.sql`. It lists tables only; views, procedures, `TR_FN_`
triggers and constraint names are checked by `migrate --dry-run --catalog live`. On 2026-10-27 the manual saw no `FOCUS_FN_%`
object, and that is not proof for today.

**Rehearsal versus live.** All of the above was rehearsed against the fixture catalog: apply, replay, an injected
mid-version failure, collision, checksum mismatch, rollback with and without data. The live adapter (`SqlCatalog`)
uses the same protocol but has never connected to a server.

## 2. Roles and grants (DBA)

Apply `sql/grants/focus_fn_roles.sql` after 001-010, then add members:

| Database role | Login (one per process) | Permissions |
| --- | --- | --- |
| `FOCUS_FN_AppReader`, `FOCUS_FN_AppCommand` | web app (Connect content or Tanzu `web`) | SELECT on views and outputs; INSERT/UPDATE `WorkerRun` (enqueue, cancel); no EXECUTE |
| `FOCUS_FN_SourceWorker` + `FOCUS_FN_PipelineWorker` | source worker | source-run procedures; source, inference and duration-evidence inserts |
| `FOCUS_FN_PipelineWorker` + read access | app pipeline role (`focus-app`: schedule, validate, forecast) | pipeline state; needs SELECT on the source run, snapshot and plan tables it reads (no grant drafted yet) |
| `FOCUS_FN_Stager` + `FOCUS_FN_PipelineWorker` | stager | `StagePlan`, `ReadPlanStage`, `RecordValidation` |
| `FOCUS_FN_Signer` + `FOCUS_FN_PipelineWorker` | signer | `AcceptPlan` only; signing key referenced on this process only (see the open item below) |
| `FOCUS_FN_Evaluator` + `FOCUS_FN_PipelineWorker` | evaluator | `AppendEvaluation`, `RecordAmendment`, `RecordDeliveryObservation`; INSERT lifecycle history, `RecognitionEvent`, `BadgeAward`, `JobOutcome`; INSERT/UPDATE `TeamShiftPerformance`, `JobName` |
| `FOCUS_FN_ObservationProducer` | approved producer (OD-15) | landing tables and producer receipts |
| `FOCUS_FN_Retention` | retention operator | the two retention procedures, with the operator's own period |

The installer is a DBA principal with DDL rights, and no script grants DDL.

**Open items in the drafted grants** (to resolve with the live adapter; `docs/integration/HANDOFF.md` §6):

- Worker roles hold no SELECT; only `FOCUS_FN_AppReader` does. Each worker login needs read access to what its steps
  read: acquisition reads `TeamInference`, `ClockObservation` and `SourceHead`; the app role reads the admitted
  snapshot and plans.
- The signer step runs the full `publish`, which stages, reads back and records the readback validation before it
  accepts, and it records FORECAST delivery commitments. Those calls map to the stager's procedures and to
  `RecordDeliveryObservation` (evaluator). Either the signer path or `ROLE_GRANTS` must change.
- `DeliveryReceipt` and `CommitmentResult` have no writing role or procedure; no role can UPDATE `Outbox`
  (`DeliveredAtUtc`) or advance an `ObservationProducerReceipt` watermark (INSERT only).

The grant lint enforces separation of duties:

- only the signer can accept;
- only the stager stages and reads back;
- only the source worker promotes the source head;
- only the retention role purges;
- the app reader executes nothing.

## 3. Start (per host and role)

```bash
python -m focus.cli preflight --role web
```

Run this before serving. Exit 1 lists the failing checks:

- CPython 3.12;
- settings and platform;
- trusted identity;
- a non-ephemeral session secret;
- SQL by presence;
- the policy;
- storage;
- `create_app`;
- migrations;
- the optional solver.

No secret value is printed. The other roles use `--role worker` and `--role signer`.

Until 001-010 are installed **and** the live repository adapter exists, a live host starts with typed
`REPOSITORY_UNAVAILABLE` states: `/health/live` is 200 and `/health/ready` is 503 with reasons. It never crashes or
pretends.

- **Posit Connect.**
  - Deploy the API (`app:app`).
  - App-role runs (schedule, scenario, explanation) can use the in-process worker (`FOCUS_INPROCESS_WORKER=true`).
  - Run the pipeline roles from a scheduler that holds each role's own credentials: the GitLab draft below, or a Tanzu
    task. Where scheduled Python content is permitted on the Connect server, a scheduled document can run
    `python -m focus.cli pipeline --role <role>` with that role's content variables.
  - Never give the web content signer or installer credentials.
- **Tanzu.**
  - `manifest.yml` runs `web` and an app-role `worker` (`python -m focus.cli worker --loop --role app`).
  - Run pipeline roles as tasks of separate apps bound to separate user-provided services, so each process holds only
    its role's credentials. For example:

    ```bash
    cf run-task focus-source --command "python -m focus.cli pipeline --key <key> --role source"
    ```

## 4. The pipeline (`focus/orchestration.py`)

```bash
python -m focus.cli display-retention --apply                      # retention role: display parts nothing reads (D-24)
python -m focus.cli performance-details --keep-days 90 --apply     # retention role: per-job evidence past 90 days (D-25)
python -m focus.cli pipeline --key 2026-10-26 --role source      # --planning auto|full|repair (default: policy)
python -m focus.cli pipeline --key 2026-10-26 --role app        # the executable plan
python -m focus.cli pipeline --key 2026-10-26 --role stager
python -m focus.cli pipeline --key 2026-10-26 --role signer     # the executable plan is out
python -m focus.cli pipeline --key 2026-10-26 --role app        # the forecast, on the same snapshot
python -m focus.cli pipeline --key 2026-10-26 --role stager
python -m focus.cli pipeline --key 2026-10-26 --role signer
python -m focus.cli pipeline --key 2026-10-26 --role evaluator
python -m focus.cli pipeline --key 2026-10-26 --status
```

Steps: `acquire -> admit -> schedule -> validate -> stage_readback -> sign_accept`, then
`forecast -> forecast_stage_readback -> forecast_sign_accept`, and `observe_evaluate` after `sign_accept`.

Every evaluation revision that `observe_evaluate` writes also writes its team-shift's light performance row and its
compact job record (D-25). That is what the period rollups and the job patterns read
(`GET /api/v1/performance/rollup` and `/performance/jobs`), so no further pipeline step is needed. A store from before
D-25 is filled once with `python -m focus.cli performance-ledger` (evaluator role).

- **One pipeline before every shift (D-26).** The scheduler runs three times a day, before each shift. Each shift is
  scored against the executable plan accepted before it starts, so `sign_accept` must finish before the shift begins.
  In the GitLab draft that step is a manual operator promotion. A plan promoted after its shift starts is only a
  proposal for that shift: the previous plan stays its goal of record, and the late plan becomes the next shift's.
  `observe_evaluate` scores a shift once its overtime window has closed. The run before shift 2 starts while shift 1
  is still finishing, so shift 1's final score comes from the next run, or from the evaluator-only pipeline scheduled
  right after shift 1's overtime window (`evaluate_closed_shifts`, below). In live mode the in-pipeline
  `observe_evaluate` step stops with `LIVE_OBSERVATIONS_UNAVAILABLE` until the live observation path is built
  (`docs/integration/HANDOFF.md` §6).

- **Executable first.** `schedule`, `validate`, `stage_readback` and `sign_accept` handle only the executable plan, the one
  crews are dispatched on. `forecast` needs the accepted executable plan, so each role's first run stops before the
  forecast and the shift's dispatch never waits for it. The forecast (delivery dates, days late and penalties for every
  aircraft) still runs on every pipeline, on the same admitted snapshot, and is staged, read back and signed the same
  way. The GitLab draft runs it as `forecast_schedule_validate`, `forecast_stage_readback` and `forecast_sign_accept`.

- **Planning mode.** A pipeline's request fixes how both plans are made (`--planning`, default policy
  `planning.default_mode` = `auto`). The schedule and forecast step outputs, and each candidate's receipt
  (`algorithm_status.baseline_comparison.planning`), record what was asked, what ran and why.
  - `full`: the complete solve (seed, cost-of-delay redispatch, delay repair, neighbourhood search).
  - `repair`: a decode that keeps the accepted plan of the same purpose wherever its slot and crew still hold and places
    new and changed work around it, then the cost-of-delay redispatch of that repair (the better one is kept). It re-plans the whole horizon, so a shift's shortfall still moves delivery
    dates and penalties. The FINAL validation gates it like any plan. With no accepted plan, or when the repaired plan
    fails validation, the full solve runs instead and the receipt says why (`REPAIR_NO_INCUMBENT`, `REPAIR_INVALID`).
  - `auto`: repair between daily full solves. A run plans the shift that contains its cutoff plus
    `planning.cutoff_lead_minutes` (60). It gets the full solve when that shift is in `planning.full_solve_shifts` ([1]),
    when there is no accepted plan, or when the accepted plan's cutoff is more than
    `planning.repair_max_incumbent_age_hours` (12) old. Each such full solve also decodes the repair it replaces and
    records the drift (`planning.drift_check`): cost, deferred work and aircraft completion dates, repair minus full.
  - `docs/execution/PLAN_REPAIR.md` explains the method, the measurements and the limits.
- **Admitted data is read once per process.** A process parses and verifies the admitted snapshot the first time it
  loads it.
  - Later steps in the same process reuse it while the stored bytes are unchanged. Every load still reads and hashes
    those bytes, so a changed record is parsed and verified again.
  - Acquisition registers the snapshot it just stored, so the `admit` step of the same run does not parse it at all.
  - A role that runs `schedule` and `validate`, or `all` roles in one process, loads the snapshot once.
  - Publishing reads the current plan's source cutoff (the stale-replay check) from its source run, not by loading that
    plan's snapshot.
  - The first load in a process holds as little at once as the checks allow: at 50 aircraft it takes about 2 minutes
    and peaks at 7.0 GB (`PRODUCTION_SCALE.md`).
  - `python tools/time_pre_shift.py run WORKDIR --aircraft 50` times every action of the flow on the synthetic
    fleet, each role in its own process. As shipped the four jobs to the accepted plan took 23.2 minutes
    (`PRODUCTION_SCALE.md`; `DECISION_PLAYBOOK.md`, DEC-36).
- **Needs and input hashes.** A step runs only when every step it needs is completed. Its input hash is the hash of
  those outputs. A step started early is `BLOCKED`, so signing never precedes a clean readback and evaluation never
  runs without the accepted plan's frozen goals.
- **Same source.** `validate` re-validates every candidate independently against the admitted snapshot and refuses a
  candidate bound to another source run.
- **The stage/sign split.** `stage_readback` runs `publish(until="readback")`: the unsigned stage plus the independent
  readback validation, with no signing material. `sign_accept` re-reads and re-validates those exact bytes, signs,
  accepts and moves the head last.
- **The display package.** The web reads small, partitioned parts the pipeline publishes, never a snapshot or a plan
  artifact (`DISPLAY_PACKAGE.md`). There is no new step:
  - `acquire` writes the snapshot's parts before it moves the source head. At 50 aircraft that takes 162 s and writes
    4.8 GB;
  - each `sign_accept` writes the plan's parts and signs their manifest before it moves the plan head. At 50 aircraft
    that is 10 s for the executable plan and 23 s for the forecast.

  Neither raises the job's peak memory.

  A store from before the package is backfilled once with these two commands:
  - `python -m focus.cli display --current-source` (source role);
  - `python -m focus.cli display --current-plans` (signer role, because it signs).

  Before acquisition, `display-retention --apply` deletes the parts nothing reads any more (section 7), so the store
  holds at most the current package and the one being built. At 50 aircraft it releases a whole generation, 5.9 GB, in
  6.4 s.
- **Leases.** The default is 900 s (`--lease-seconds`). A live lease returns `LEASED` to a competitor. An expired lease
  is taken over, and the new owner re-runs the idempotent handler. After three attempts the step is `EXHAUSTED`; start
  a new key.
- **Outbox.** Each completed step has exactly one event, keyed by pipeline and step and built from the recorded state.
  Every run re-emits any event missing after a crash.
- **Fixture versus live.** In fixture mode the state lives in the runtime store. Live, it maps to the migration-002
  tables through the pending live adapter, so live steps stop with typed `UNAVAILABLE` results until then.

### CI (`.gitlab-ci.yml`, draft)

- **Verify** runs the import check, migration lint and bundle, preflight, the full non-UI suite, the UI suite and a
  fixture rehearsal of the whole pipeline. None of it needs secrets.
- **Live jobs** each run one role in a protected environment, chained with `needs` and serialized per scope with
  `resource_group`. Acquisition waits for the verify jobs in its pipeline and for the staged sources. In a live run
  the long suites (non-UI, UI, rehearsal) are not in the pipeline: they ran on the merge that put the code on the
  protected ref, and acquisition refuses any other ref (D-28; `FOCUS_LIVE_VERIFY=full` runs them every time).
  `sign_accept` waits for an operator and holds the only signing reference. `FOCUS_AUTO_SIGN=true` lets a scheduled
  run on a protected ref accept the executable plan itself; it is off, and turning it on is the owner's decision
  (`DECISION_PLAYBOOK.md`, DEC-11).
- **The window.** `python -m focus.cli pipeline --key K --status` reports, under `window`, the minutes from the data
  cut to the accepted executable plan against the owner's 20-minute budget, the seconds per step, and whether the plan
  was accepted after its shift started.
- **The objective.** The same status reports, under `objective`, whether the cost objective compared any dollars for
  the executable plan and for the forecast: the aircraft with a rate, a due date and a finish date, and the open jobs
  that could not be planned, by reason (`OBJECTIVE_EXPLAINED.md` §4). A plan is published either way; this is how a
  run that compared nothing is seen.
- **Staging** keeps a complete copy of the official sources in the SQL Server tables the reference queries read,
  through the company loader named by `FOCUS_STAGE_COMMAND` (`docs/integration/STAGING_PLAN.md`; the loader's
  contract is `docs/integration/HANDOFF.md` §4).
  - In the plan pipeline, `stage_sources` pulls only what changed since the pass before it (`--part cut`). It needs
    no other job, so it starts with the pipeline, and `verify_staged_sources` then checks the staged schema against
    the S04 contract before acquisition.
  - **Staging-only pipelines** keep the copy current ahead of time: `FOCUS_PIPELINE_PART=preshift` (60 minutes
    before each shift: `prestage_sources`, `verify_prestaged_sources`) and `FOCUS_PIPELINE_PART=nightly`
    (`nightly_sources`, `verify_nightly_sources`). They share the plan pipeline's `resource_group`, so a pass never
    publishes while acquisition reads.
  - A value of `FOCUS_PIPELINE_PART` the draft does not know runs nothing, and no live job is interruptible.
- **Evaluator-only pipelines** (`FOCUS_PIPELINE_PART=evaluate`, one schedule just after each shift's overtime window)
  run only `stage_observations` and `evaluate_closed_shifts` (`tools/evaluate_closed_shifts.py`), which scores every
  shift whose credit window has closed. The schedules are listed in `HANDOFF.md` §5.2.
- **Display retention** (`display_retention`) runs before acquisition with retention credentials only. It is
  `allow_failure`, so it never holds back the shift's plan. The fixture rehearsal runs it too.
- **Detail retention** (`detail_retention`) deletes per-job evaluation evidence past the owner's 90 days, with
  retention credentials only. It is `allow_failure` too, and the fixture rehearsal runs it.
- **The scheduling job** (`schedule_validate`) does the heavy work: it associates jobs with people and start times.
  - Tag it to a runner with at least 4 vCPU and 16 GB. At 50 aircraft, loading the snapshot and running a full
    forecast solve peaked at 7.5 GB (`PRODUCTION_SCALE.md`). The stager and signer jobs load the snapshot too and fit
    the same size.
  - It installs the native wheel that `native_wheel` built from the same commit (an artifact, never an index copy) and
    sets `FOCUS_NATIVE_CORE=true`. A wheel built for another protocol is refused and the Python core runs, with the
    reason in the run receipt.
  - Posit Connect only displays published results, so it needs no scheduling capacity
    (`docs/execution/PRODUCTION_SCALE.md`).
- **Status.** The draft was never pushed to a GitLab remote or activated.

## 5. Recovery

| Crash or fault | What happens |
| --- | --- |
| Acquisition fails midway (A05) | `acquire` FAILED; the old head is unchanged; everything downstream is BLOCKED; replays keep the rejection until `EXHAUSTED` |
| Crash after acquisition, before the step is recorded (A07) | The lease expires and the takeover replays the same idempotency key: one source run, one head |
| Crash after signing, before acceptance (A51) | The step FAILED; no plan is published and the old head stays; the retry re-stages the identical bytes, signs and accepts once |
| Crash after acceptance, before the step is recorded (A50) | The takeover's `publish` returns RECOVERED; delivery commitment state and the published plans are byte-identical (no extra confirmation) |
| Migration fails mid-version | Nothing is recorded or left behind; the attempt receipt says why; fix it and re-run, and applied versions are skipped |
| Bad install, no data yet | `migrate --rollback <latest>` (fixture), or the same with `--catalog live` and confirmation |

A recovered historical plan never becomes executable by itself: publication refuses stale replays, and acceptance moves
heads only forward.

## 6. Monitoring

| Signal | Where |
| --- | --- |
| Missing or stale datasets; quarantined or blocked inventory | `python -m focus.cli status`, `/health/ready`, source run receipts, `GET /api/v1/source-runs/...` |
| Worker durations, leases and failures | `pipeline --status` (attempts, owners, lease expiry, typed errors) and worker runs (`/api/v1/runs/<id>`) |
| Economic and commitment churn; check coverage | candidate receipts (`baseline_comparison`, `check_counts`) and validation receipts |
| Source and plan heads; readback and signature failures | `stage_readback` and `sign_accept` step errors, acceptance receipts |
| Evaluations pending correction | evaluation revisions and `/api/v1/performance` |
| A team's, a superintendent's or the business unit's performance over any period, and the jobs behind it | `/api/v1/performance/rollup` and `/api/v1/performance/jobs` (Performance Over Time) |

## 7. Retention

### History (never deleted by this command)

```bash
python -m focus.cli retain-history --keep-days <N> [--legal-hold holds.json] [--apply]
```

There is no default period: the operator supplies it.

- **Preserved:** accepted plans with their stages, payloads, receipts and goals; every candidate and validation they
  or a pipeline step name; source runs and evidence; observations, evaluations with their light performance rows and
  job records, recognition, lifecycle and commitment history; legal holds. (The per-job evidence behind each
  evaluation goes after the owner's 90 days: see Per-job details below.)
- **Archived** (`--apply`, fixture only): only superseded, unreferenced candidates, their receipts and old scenario
  artifacts. They move to `<runtime>/archive/<receipt>/` with a SHA-256 receipt and can be restored byte for byte.
- **Live:** the installed retention procedures, run by the retention role with the operator's period. They do delete:
  `PurgeTeamInference` and `PurgeDurationEvidence` remove inference and duration rows of runs that no head or stage
  references. Two commands delete records too: `display-retention` (D-24) and `performance-details` (D-25), below.

### Display parts (D-24)

```bash
python -m focus.cli display-retention [--grace-minutes 60] [--apply]
```

Display parts are a cache rebuilt from the kept snapshot or accepted payload, and checked against the kept (for plans,
signed) manifest. It is one of the two commands that delete records (with `performance-details`, below), and the store
refuses it outside `display_parts`.

- **Kept whole:** the current packages; any package that can still become current (an unpromoted run, an acceptance
  whose head has not moved, a plan that can still be accepted); and every package of a head that moved less than the
  grace period ago.
- **Kept forever:** each accepted plan's `summary` and `goals:<date>` parts (3.3 MB per shift at 50 aircraft).
- **Never deleted:** manifests, display receipts, candidate digests, and everything section 7's history list preserves.
- **Deleted:** every other part.

Without `--apply` it only reports. With it, it holds the display parts lock that every package writer holds, writes a
ledger of released packages (so later runs open only new ones) and a receipt, then deletes. `DISPLAY_PARTS_MISSING` in
a view means a current part is gone for another reason; `python -m focus.cli display --current-source` or
`--current-plans` rebuilds it. `DISPLAY_PACKAGE.md` has the full rule and the measurements.

**Live:** display parts have no FOCUS_FN table yet. This needs a new migration (display-package tables and a locked
purge procedure that the retention role executes) and the live adapter's `exists`, `stored_ids` and `delete_immutable`
for `display_parts`. Until then the command reports `REPOSITORY_UNAVAILABLE`, like every live store operation.

### Per-job details (D-25)

```bash
python -m focus.cli performance-details [--keep-days 90] [--apply]
python -m focus.cli performance-ledger                              # rebuild the light rows and job records
```

Each team-shift keeps its score forever: the evaluation with its seven components, its light row and its compact job
record (each job's shop order id, aircraft, phase, minutes and outcome, about 50 bytes a job). The bulky per-unit
evidence behind the score (`evaluation_evidence`, about 58 KB per team-shift at 50 aircraft) is kept for the owner's
period of 90 days, then deleted.

- **Deleted:** the evidence of every revision of a team-shift whose production day is more than `--keep-days` (at
  least 7) before today, site time, and only once its job record carries the current evaluation.
- **Kept:** every evaluation, its row and its job record; the small member lists of pooled evaluations; the evidence
  of a team-shift with no job record yet. The report counts those (`kept_without_job_record`), and the watermark stops
  before their day, so a later run deletes them once `performance-ledger` has written the record.
- **Readers hold.** Recognition and badges keep what they awarded, and the shift performance view says
  `DETAILS_EXPIRED` instead of guessing.

Without `--apply` it only reports. With it, it deletes, moves a watermark past every finished day (so each day is read
once) and writes a receipt (`detail_retention_runs`). The store allows the deletion for this collection only
(`RETAINED_DETAIL_COLLECTIONS`).

`performance-ledger` writes every current evaluation into its row and job record and leaves finished ones alone. It
reads the claims as of each evaluation's own cutoff, names a job once the current snapshot knows it, and cannot rebuild
a job record whose evidence is already gone (it says how many). A row written anew takes the superintendent from the
org chart as of the shift start.

**Live:** the light rows and job records are the proposed tables of migration 010 (`FOCUS_FN_TeamShiftPerformance`,
`FOCUS_FN_JobOutcome`, `FOCUS_FN_JobName`). The deletion needs a new migration as well as the adapter:
`TR_FN_EvaluationEvidence_Immutable` (009) blocks every DELETE, no purge procedure exists for evaluation evidence, and
the retention role has no SELECT on `JobOutcome`, which the deletion must check first. Until then both commands report
`REPOSITORY_UNAVAILABLE`, like every live store operation.
