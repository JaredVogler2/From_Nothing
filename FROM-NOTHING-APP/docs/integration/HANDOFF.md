# FOCUS handoff: moving to the official data sources

**Audience.** The company-side coding agent that connects FOCUS to the official data sources, stages them in SQL
Server through GitLab, and runs FOCUS before every shift.

**Status (2026-11-02).** Nothing here has run against a company system. No SQL is installed, no grant is applied, no
pipeline is active, and no host is deployed. Everything below was rehearsed on synthetic fixtures. Statements about
behaviour were checked against the code on this date.

**Read with:**

- [WORKPLACE_PORTING_GUIDE.md](WORKPLACE_PORTING_GUIDE.md): confidentiality and the rollout gates A–F.
- [SOURCE_REPLACEMENT_CROSSWALK.md](SOURCE_REPLACEMENT_CROSSWALK.md): the mapping checklist for each dataset.
- [`docs/data/`](../data/README.md): every input, column by column, and what FOCUS does when it is missing.
- [`SQL_SCHEMA_LEDGER.md`](../execution/SQL_SCHEMA_LEDGER.md): the tables FOCUS writes and the gaps in them.
- [`INSTALL_AND_PIPELINE.md`](../execution/INSTALL_AND_PIPELINE.md): install, roles, the pipeline and recovery.
- [`DECISIONS.md`](../execution/DECISIONS.md): the rules (D-26 above all) and the open decisions.
- [`10_CALIBRATION.md`](../task-valuation-investigation/10_CALIBRATION.md): re-running the score calibration on real
  data.

**Confidentiality, in one line.** Real server names, object names, query text, mappings, credentials and data rows
stay in company storage. This public repository keeps the synthetic reference contract only.

## 1. What FOCUS does and what is decided

- **The scheduler.** It contains no AI. It runs before every shift, three times a day, as one global optimization
  through delivery.
  - It first makes the **EXECUTABLE** plan, the one crews are dispatched on.
  - After that plan is accepted, it makes the **FORECAST** plan from the same snapshot: delivery dates, days late
    and penalties.
- **Scoring each shift (D-26).**
  - A team-shift is scored against the plan accepted before the shift starts.
  - Adherence means doing the same planned job; start and finish times are not compared.
  - There is no early credit.
  - Work finished after the next plan's data cut counts once, in its own shift. The next shift holds it as
    `DONE_BEFORE_SHIFT` relief.
  - Amendments are accepted only during the shift.
  - Late completions and constraints are captured after the fact. A shift is scored once its overtime window
    closes.
- **The score.** The canonical score formula still uses the demonstration policy (A ≥ 90, B ≥ 80, C ≥ 70,
  D ≥ 60). An OFFICIAL grade needs the owner's approval receipt (OD-16). The proposed off-plan credit rule (R5,
  w = 0.8) is not implemented.
- **Where things run.** The GitLab pipeline runs the scheduler and every other step. Posit Connect only displays:
  the web app reads a small display package the pipeline publishes, never a snapshot or a plan.
- **Time.** The database holds UTC; the app shows the site's local time (America/New_York). Live runs set
  `FOCUS_SOURCE_TIMEZONE_MAP_JSON={"*": "UTC"}`.

## 2. Data flow

```text
Official systems (Teradata and the other systems of record)
   │  GitLab stage_sources  (company loader; read-only on the sources, write on staging tables only)
   ▼
SQL Server
   ├─ 28 SOURCE tables in the S04 contract shape (WEB_APP.FOCUS_* reference names)
   ├─ supplemental producer tables (FOCUS_FN_TeamInference, FOCUS_FN_ClockObservation today; 14 more needed)
   └─ observation landing tables + producer receipts (migration 008; 6 of 8 feeds drafted)
   │  verify_staged_sources  (schema drift against S04, read-only)
   ▼
FOCUS acquire → admit            source role     one immutable source run and snapshot, readiness gaps
   → schedule → validate         app role        EXECUTABLE plan, independently re-validated
   → stage + readback            stager role     unsigned stage, exact readback, readback validation
   → sign + accept               signer role     signature, acceptance, goals frozen, head moved last
   → forecast (same steps)       app/stager/signer
   → display package             written at acquire and at each acceptance; Posit reads only this
   → observe → evaluate          evaluator role  observation feeds → unit facts → scores → rollup rows
```

## 3. One shift, end to end

Shift times come from the site policy (`config/policies/shift_fixture.json`, America/New_York):

| Shift | Start | Wall end | Credit deadline (end + 60 min overtime) |
| --- | --- | --- | --- |
| 1 | 06:00 | 14:30 | 15:30 |
| 2 | 14:30 | 23:00 | 00:00 |
| 3 | 23:00 | 06:00 | 07:00 |

Before each shift, inside a 15–20 minute window (owner, 2026-11-02):

| Step | Budget | Measured |
| --- | --- | --- |
| Data cut and `stage_sources` (changed rows into SQL Server) | ≤ 2 min | not measured (company loader) |
| `verify_staged_sources` | < 1 min | not measured live |
| `acquire_admit` | ≤ 1–2 min (target) | snapshot load 69–90 s at 50 aircraft (simulation); writing the snapshot's display parts adds 162 s (`DISPLAY_PACKAGE.md`) |
| `schedule_validate` | ≤ 8 min | plan 118–192 s, validate 17–24 s at 50 aircraft |
| `stage_readback`, `sign_accept` | ≤ 2 min | display parts for the executable plan 10 s; stage and sign not measured live |
| **The plan must be accepted before the shift starts** | | |

- **Total so far.** The plan path measured 3.6–5.2 minutes per shift at 50 aircraft, peaking at 6.6 GB
  (`10_CALIBRATION.md` §7). With the snapshot's display parts (about 2.7 minutes) and the executable plan's parts
  (10 s), that is roughly 6.5–8 minutes before staging, the drift check, stage and sign. The live data pull is the
  unmeasured part. The window is achievable but not roomy: measure every step on the first live runs.
- **A plan accepted after its shift starts** is only a proposal for that shift. The previous plan stays the goal of
  record, and the late plan becomes the next shift's. The calibration measured what late plans cost the teams: the
  95th-percentile shortfall doubled.
- **After each shift's credit deadline,** the evaluator-only pipeline scores that shift (§5).

## 4. The staging job (official sources → SQL Server)

### 4.1 Interface

`stage_sources` runs the company loader named by the protected variable `FOCUS_STAGE_COMMAND`:

```bash
$FOCUS_STAGE_COMMAND --part all --as-of "$CI_PIPELINE_CREATED_AT" --receipt ci-receipts/stage.json
```

- **`--part`.** `sources` loads the planning datasets, `observations` loads the observation feeds, and `all` loads
  both.
- **`--as-of`.** The single extraction instant for every dataset of this load.
- **`--receipt`.** A JSON file with one entry per dataset:
  - the target table and the row count;
  - the minimum and maximum row clock;
  - the extraction instant and the status;
  - the duration and the mapping version.

  It contains no data rows and no secrets, and the CI artifact keeps it for 90 days.
- **Exit codes:**
  - 0: every dataset was published;
  - 1: a required dataset failed and nothing was published;
  - 2: optional datasets failed and the rest were published;
  - 3: the loader is not configured.
- **Environment.** `stage_sources` runs in `focus-staging`. It holds read-only credentials for the official sources
  and write access to the staging tables only. It never holds FOCUS_FN pipeline, signing or installer credentials.
- **Observation landing tables.** These belong to `FOCUS_FN_ObservationProducer` (OD-15). Give the loader that role
  only if the company approves it as the producer.

### 4.2 What to load

- **The 28 SOURCE tables.**
  - Load them in the exact S04 contract shape: names, column types, precision, nullability and keys. The contract
    is in `fixtures/source_contracts/s04_contracts.json`; `scripts/export_integration_contract.py` writes a blank
    manifest of it.
  - The reference queries (`queries/source_inputs/*.sql`) read these tables unchanged.
  - If company object names differ, change the queries and the allowlist (`focus/data/sql.py`, `SOURCE_OBJECTS`)
    in the company fork. Never change a projection's column names or order: FOCUS refuses drift (`SCHEMA_DRIFT`).
- **The 11 required datasets.** These are `master_task_list`, `pnet_dag`, `neo_graph_rework`, `flow_budget`,
  `buyoff_plan`, `inspection_standards`, `cday_op_plan`, `cs_windows`, `etracs_active`, `soi_cert_requirements` and
  `mechanic_certs`. If one fails or comes back empty, the whole run is rejected.
- **Supplemental datasets.**
  - Today only `team_inference` and `clock_observations` have live query paths, through FOCUS_FN tables.
  - The other 14 report `NOT_PROBED` until each one has a landing table and a registered live query
    (`LIVE_SUPPLEMENTAL_QUERIES` in `focus/data/fixture_reader.py`).
  - Priority order for executable plans: `work_progress` first (without it every in-work job is blocked,
    `REMAINING_UNKNOWN`), then `shift_roster`, `ot_authorizations`, `availability_exceptions`, `carry_in`,
    `requirement_census`, `holding_events`, `rework_classification`, `inspection_holds`, `part_events` and
    `duration_episodes`.
- **Observation feeds.**
  - Migration 008 drafts landing tables for `lifecycle_events`, `actual_intervals`, `defect_evidence`,
    `goal_amendments`, `disruption_claims` and `excusal_decisions`, plus `ObservationProducerReceipt`.
  - `attendance_evidence` and `holding_evidence` have no table and no query yet.

### 4.3 Load rules (each one comes from how FOCUS reads)

1. **Publish atomically.** Most SOURCE queries take the table as it stands, with no as-of parameter, and FOCUS's
   `CAPTURE_SKEW` check measures only its own capture times. Load into shadow tables, then swap them in one
   transaction (`ALTER SCHEMA … TRANSFER` or `sp_rename`), or switch a view. Never truncate and insert in place.
2. **Load everything from one instant.** All SOURCE tables come from `--as-of`. `cs_windows` must come from the
   same load generation as the task list, or admission rejects it (`CS_GENERATION_MISMATCH`).
3. **Stamp the row clock on every row.** Set the table's row clock (`LOAD_TIMESTAMP`, `SNAPSHOT_TS` or `LOAD_TS`)
   to the load instant, in UTC.
   - The live reader passes no `loaded_at`, so live freshness comes from these columns.
   - A stale clock (limits 18–240 h) or an unreadable one fails executable plans (`SOURCE_STALE`,
     `SOURCE_CLOCK_UNKNOWN`).
   - A delta load must still restamp the row clock: refresh it, or keep a load-instant column the query reads.
4. **Store UTC everywhere.** Columns ending in `_UTC` are instants. Local wall times exist only where a column is
   named `_LOCAL`.
5. **Load the complete census.**
   - Every job on every line with open work, including COMPLETE anchors. Never apply a top-N cap.
   - Keep null ECDs, blank `USED_ON` values and every status: FOCUS turns them into precise gaps, never into
     silence.
6. **Empty is not "nothing to do".**
   - A live empty read is `EMPTY_UNCERTIFIED`, so an empty required dataset rejects the run.
   - A legitimately empty source (for example, no open shortages) needs a certification path that FOCUS does not
     have yet (§6, item 4).
7. **Keep observation history append-only.**
   - Each row carries its source key and revision, and `OBSERVED_AT_UTC` is when the producer first knew it.
   - Corrections are new revisions, `VOIDED` or `CORRECTION` rows; never rewrite a row.
   - A row observed after the as-of is invisible to that as-of, so a late correction never rewrites what a past
     plan knew.
8. **Load deltas, read full tables.** The shift-end budget assumes a delta pull: changed rows only, applied to the
   shadow copy. FOCUS always reads the full tables. Reconcile in full nightly (row counts and checksums against the
   source).
9. **Late events.** Completions and constraints in the last 20 minutes of a shift arrive in the next load. They need
   no special handling: FOCUS scores each shift from timestamped events once its overtime closes, and revises when
   data arrives late.

### 4.4 What FOCUS checks after staging

- **`verify_staged_sources`.** It runs `python -m focus.cli source-discover --read-only --mode live`, which compares
  every column with the S04 contract, and fails on any drift.
- **Acquisition.** It assigns each dataset a capture status. A failed or empty required dataset rejects the run.
  The run-rejection codes are listed in `docs/data/README.md`.
- **Admission.** It checks freshness and capture skew and records every readiness gap. The validator refuses an
  EXECUTABLE plan on a stale, unreadable or skewed snapshot.

## 5. GitLab

The draft (`.gitlab-ci.yml`) has never been pushed to a GitLab remote.

### 5.1 Jobs

| Job | Stage | Environment (credentials) | Needs | Does |
| --- | --- | --- | --- | --- |
| `import_and_lint`, `unit_and_integration`, `ui`, `native_wheel`, `fixture_pipeline_rehearsal` | verify | none | – | Lint, migrations dry-run, preflight, tests, Rust wheel, fixture rehearsal |
| `display_retention`, `detail_retention` | retain | `focus-retention` | verify | Housekeeping (D-24, D-25); never holds the plan back |
| `stage_sources` | stage_sql | `focus-staging` | `import_and_lint` | §4 |
| `verify_staged_sources` | stage_sql | `focus-source` | `stage_sources` | Schema drift check |
| `acquire_admit` | source | `focus-source` | verify, `display_retention`, `verify_staged_sources` | Source run and snapshot |
| `schedule_validate` | schedule | `focus-app`, runner tag `focus-scheduler` (≥ 4 vCPU, 16 GB) | `acquire_admit` | Executable plan (native core) |
| `stage_readback` | stage | `focus-stager` | `schedule_validate` | Unsigned stage and readback |
| `sign_accept` | sign | `focus-signer` | `stage_readback` | Manual promotion; accept and move the head |
| `forecast_*` (3 jobs) | forecast… | app, stager, signer | `sign_accept` | Forecast plan |
| `observe_evaluate` | observe | `focus-evaluator` | `sign_accept` | In-pipeline evaluation; in live mode it stops with `LIVE_OBSERVATIONS_UNAVAILABLE` today (§6) |
| `stage_observations` | stage_sql | `focus-staging` | – | Evaluator-only pipelines: load the observation feeds |
| `evaluate_closed_shifts` | observe | `focus-evaluator` | `stage_observations` | Evaluator-only pipelines: `tools/evaluate_closed_shifts.py` |

**Protected CI/CD variables.** Values live in GitLab or a vault, never in the repository.

- Every live job: `FOCUS_MODE=live` and `FOCUS_SCOPE_KEY`.
- `focus-staging`: `FOCUS_STAGE_COMMAND` and the source and staging credentials (company names).
- The SQL roles: `FOCUS_SQL_HOST`, `FOCUS_SQL_DATABASE`, `FOCUS_SQL_USER` and `FOCUS_SQL_PASSWORD`, one login per
  environment.
- `focus-signer` only: `FOCUS_SIGN_KEY_REF`.
- Schedules: `FOCUS_PIPELINE_PART`.

### 5.2 Schedules

Set each schedule's time zone to America/New_York so daylight saving is handled.

| Schedule | Cron (site time) | Variables |
| --- | --- | --- |
| Plan for shift 1 | `40 5 * * *` | `FOCUS_MODE=live` |
| Plan for shift 2 | `10 14 * * *` | `FOCUS_MODE=live` |
| Plan for shift 3 | `40 22 * * *` | `FOCUS_MODE=live` |
| Score shift 1 | `35 15 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` |
| Score shift 2 | `5 0 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` |
| Score shift 3 | `5 7 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` |

- **Plan schedules** start 20 minutes before the shift (the data cut). Auto planning
  (`planning.cutoff_lead_minutes` = 60) then plans the next shift. The full solve runs before shift 1; the others
  repair the accepted plan (`PLAN_REPAIR.md`).
- **Score schedules** run 5 minutes after each credit deadline.
- `resource_group: focus-$FOCUS_SCOPE_KEY` serializes all pipelines of one scope.

### 5.3 Two timing risks to settle before go-live

- **Verification inside the live window.**
  - The draft makes acquisition wait for every verify job (P23), including the whole test suite. A full non-UI run
    takes well over 15 minutes in this environment, so it does not fit the 15–20 minute window.
  - Recommendation: run the live schedules from a protected branch or tag whose merge pipeline already passed. Keep
    `import_and_lint` and the preflights in the live path, and drop the suite from it. This changes a recorded
    decision (P23), so it needs the owner.
- **Manual signing.**
  - `sign_accept` is `when: manual`. With about five minutes between the end of planning and the shift start, a
    missed promotion makes a late plan.
  - Options: automatic promotion inside the protected signer environment (signing only a plan whose FINAL and
    readback validations are clean), or a staffed promotion with an alert at T−5 minutes. This is an owner
    decision.

## 6. Engineering work before going live (in order)

1. **Install the schema** (an authorized installer and DBA): migrations 001–010, then the grants. Resolve the grant
   gaps first:
   - worker roles have no SELECT;
   - the signer path calls the stager's procedures and `RecordDeliveryObservation`;
   - nothing can write `DeliveryReceipt`, `CommitmentResult` or `Outbox.DeliveredAtUtc`;
   - a producer receipt's watermark cannot advance.

   `INSTALL_AND_PIPELINE.md` §2 and `SQL_SCHEMA_LEDGER.md` list them.
2. **Build the live repository adapter** (`focus/data/sql_store.py`), after a new migration (011):
   - tables for the runtime collections that have none (candidates, snapshots, org charts, the display package,
     retention ledgers, attendance and holding evidence, scenarios, explanations, the observation head);
   - an id mapping for the `uniqueidentifier` columns;
   - the quarantine key fix;
   - eight feeds in the receipt check;
   - a purge procedure for evaluation evidence and display parts;
   - an updated `FN_OBJECTS` allowlist.
3. **Wire the live observation path.**
   - In live mode, `observe_evaluate` stops with `LIVE_OBSERVATIONS_UNAVAILABLE`. It should collect the live feeds
     as of now and evaluate every closed window, as `tools/evaluate_closed_shifts.py` does.
   - Install the producers (OD-15), and add tables and queries for `attendance_evidence` and `holding_evidence`.
4. **Fix the live reader** (`LiveSourceReader`): add a certified-empty path for legitimately empty sources, and
   read a producer receipt's `loaded_at`.
5. **Supplemental producers:** `work_progress` first (§4.2).
6. **Write the staging loader** (company code), following §4.
7. **Policy.**
   - Correct the out-of-date `live_effect` texts (DECISIONS.md lists them). Do this with the next policy revision,
     because it changes the policy hash.
   - The owner must answer the open decisions OD-02 to OD-05 and OD-07 to OD-17.
8. **Shadow, then calibrate.** Run in shadow mode for at least four weeks, then follow `10_CALIBRATION.md` §8 (w,
   bands, the classifier's error rates, stale plans).

## 7. Owner decisions still open

- **Signing before each shift:** manual promotion or automatic (§5.3).
- **Verification in the live path:** keep the full suite before every run, or rely on the merge pipeline (§5.3).
- **Late plans:** how to protect teams scored against a stale plan. For example, review the units the late plan
  moved under the existing `PLAN_CHURN` track (`10_CALIBRATION.md` §7).
- **Overtime credit.** Today a job finished in a shift's authorized overtime is credited to the next shift's goal when
  the next plan also holds it. The next shift's window contains the completion, and shift windows are checked before
  overtime windows. The finishing shift's unit is then missed (`COMPLETION_CREDITED_ELSEWHERE_OR_LATE`). The
  alternative is to credit the crew that finished it; completion records carry the person.
- **R5 off-plan credit, w and the bands:** after the real-data calibration.
- **Live load cadences:** the freshness limits are fixture defaults.
- **The open decisions OD-02 to OD-05 and OD-07 to OD-17**, and the score policy approval (OD-16).

## 8. First live runs: checklist

- [ ] `stage_sources` receipt: all 28 SOURCE tables published, row clocks stamped, durations within budget.
- [ ] `verify_staged_sources`: no drift.
- [ ] `acquire_admit`: run ACCEPTED, census reconciled, readiness gaps reviewed with their owners (blocked and
      quarantined jobs, `DATASET_*`, `OD-*`).
- [ ] A complete-scope FORECAST plan, with every aircraft's delivery date or a stated reason.
- [ ] EXECUTABLE: the jobs withheld and why (crews, certificates, parts, QA holds) match what supervisors know.
- [ ] Timing: data cut to acceptance inside the window, three times a day, for a week.
- [ ] Display package published, and Posit shows the accepted plan in local time.
- [ ] `evaluate_closed_shifts`: typed UNKNOWN results until the feeds exist; then scores whose job records add up
      (B, Cp, E, U).
- [ ] `tools/calibrate_scores.py store`: `stale_plans` about 0, `DONE_BEFORE_SHIFT` share plausible.

## 9. Where things are

| Need | File |
| --- | --- |
| Datasets, columns, required flags | `focus/data/datasets.py`; `docs/data/` |
| Live reader, SQL access, allowlists | `focus/data/fixture_reader.py` (`LiveSourceReader`), `focus/data/sql.py` |
| Acquisition, admission, freshness | `focus/data/acquisition.py`, `snapshot_builder.py`, `freshness.py` |
| Observation feeds | `focus/performance/actuals.py`; `docs/data/09-observation-feeds.md` |
| Scoring and D-26 | `focus/performance/lifecycle.py`, `score.py`, `amendments.py`, `rollup.py` |
| Pipeline steps and roles | `focus/orchestration.py`; `docs/execution/INSTALL_AND_PIPELINE.md` §4 |
| Migrations and grants | `sql/migrations/`, `sql/grants/focus_fn_roles.sql`, `focus/migrations.py` |
| Live store (pending) | `focus/data/sql_store.py` |
| Score calibration on real data | `tools/calibrate_scores.py`; `docs/task-valuation-investigation/10_CALIBRATION.md` |
| Evaluator-only run | `tools/evaluate_closed_shifts.py` |
| Simulation (synthetic) | `focus/sim/`; `docs/execution/SIMULATION.md` |
