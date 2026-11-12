# FOCUS handoff: moving to the official data sources

**Audience.** The company-side coding agent that connects FOCUS to the official data sources, stages them in SQL
Server through GitLab, and runs FOCUS before every shift.

**Status (2026-11-02).** Nothing here has run against a company system. No SQL is installed, no grant is applied, no
pipeline is active, and no host is deployed. Everything below was rehearsed on synthetic fixtures. Statements about
behaviour were checked against the code on this date.

**Read with:**

- [`AGENTS.md`](../../../AGENTS.md), at the repository root: the one-page brief for a coding agent. Read it first.
- [DECISION_PLAYBOOK.md](DECISION_PLAYBOOK.md): every open decision, with a recommendation, the rule for deciding
  once real data is wired, the files and tests to change and where to record it. Work through it after this guide.
- [DATA_CADENCE.md](DATA_CADENCE.md): one page on how often every dataset is gathered, staged and processed, and
  the schedules that do it (the owner's decision D-34).
- [STAGING_PLAN.md](STAGING_PLAN.md): what to copy from Teradata into SQL Server, how and how often, and the
  staging pipelines. Read it before writing the loader.
- [WORKPLACE_PORTING_GUIDE.md](WORKPLACE_PORTING_GUIDE.md): confidentiality and the rollout gates A–F.
- [SOURCE_REPLACEMENT_CROSSWALK.md](SOURCE_REPLACEMENT_CROSSWALK.md): the mapping checklist for each dataset.
- [`docs/data/`](../data/README.md): every input, column by column, and what FOCUS does when it is missing.
- [`SQL_SCHEMA_LEDGER.md`](../execution/SQL_SCHEMA_LEDGER.md): the tables FOCUS writes and the gaps in them.
- [`INSTALL_AND_PIPELINE.md`](../execution/INSTALL_AND_PIPELINE.md): install, roles, the pipeline and recovery.
- [`DECISIONS.md`](../execution/DECISIONS.md): the rules (D-26 above all) and the open decisions.
- [`OBJECTIVE_EXPLAINED.md`](../execution/OBJECTIVE_EXPLAINED.md): what the scheduler minimizes, how late-delivery
  cost is worked out, and how to tell whether the cost objective is working on live data.
- [`SCORING_EXPLAINED.md`](../execution/SCORING_EXPLAINED.md): how a team's shift is scored, in plain terms, with
  each rule's code and tests.
- [`10_CALIBRATION.md`](../task-valuation-investigation/10_CALIBRATION.md): re-running the score calibration on real
  data.

**Confidentiality, in one line.** Real server names, object names, query text, mappings, credentials and data rows
stay in company storage. This public repository keeps the synthetic reference contract only.

## 0. Standing answers (read before asking)

- **Reading needs no approval.**
  - Read the whole repository, run the test suite and run any read-only command without asking, and without
    waiting for a reading plan to be approved.
  - Approval is for changes only: installing SQL, applying grants, publishing or signing plans, enabling dispatch,
    changing the policy files, and the decisions marked OWNER in the playbook.
  - If your tooling needs a human click before a read-only step, ask once and say the step is read-only.
- **The first task is a full read with a scoring check.** Read this guide, then the playbook, then the code. As part
  of that read, check the code against the scoring rules (`SCORING_EXPLAINED.md` §4). Change nothing. Report three
  things:
  - a short summary of how the system works;
  - every difference between a documented rule and the code, with file and line;
  - your open questions.
- **The pre-shift run has 20 minutes** (owner, D-28): from the data cut to the accepted plan and its dashboard data.
  Staging counts toward it (§3). The pipeline status reports the minutes used on every run.
- **No person accepts a plan** (owner, 2026-11-12, D-34): "once a schedule is produced that is what the mechanics and
  shop floor should use to determine what jobs they work. They do not need to "accept" anything - the scheduler is
  the answer." A scheduled pipeline run accepts its plan once the checks pass (§5.3). This is about the pipeline:
  you, the coding agent, still sign or publish nothing by hand without approval.
- **Each dataset is gathered, staged and processed at its own cadence** (owner, D-34), not everything every shift:
  weekly, nightly, before each shift, at the cut, after each shift. [DATA_CADENCE.md](DATA_CADENCE.md) has every
  dataset and every schedule on one page.
- **Scoring** is explained in `SCORING_EXPLAINED.md`. One file computes it: `focus/performance/score.py`. Scoring
  is part of the application, not an extra: verified completions earn credit for the team-shift whose plan held
  the job, and the record behind every score is kept. Do not design a second completions store.
- **The objective** is explained in `OBJECTIVE_EXPLAINED.md`. Two schedules are compared in a fixed order, and
  late-delivery dollars are one level of it.
  - An aircraft's cost is unknown while any open job on it cannot be planned, or its rate or due date is missing.
  - With every aircraft unknown, the run still publishes a plan but has compared no dollars.
  - Every run reports this itself: `objective` in the pipeline status counts the aircraft with a rate, a due date
    and a finish date, and the unplanned jobs by reason (`OBJECTIVE_EXPLAINED.md` §4). Report it with every live
    run, and work it before speed (`DECISION_PLAYBOOK.md`, DEC-29).
- **Unknown stays unknown.** Never fill a missing rate, duration, date or calendar day with an average, a median or
  a default.
  - Rates from a file: `python tools/build_economics_rows.py` builds the economics rows and lists the aircraft it
    cannot price. It never fills one in.
  - Which rates (owner, 2026-11-11, D-31): the airline economic proxy per customer airline and minor model, column
    `daily_airline_economic_proxy_usd` of the owner's rate table (`fixtures/economics/airline_economic_proxy.csv`).
    Lateness begins at 744 unload plus 2 days; there is no other grace period and no cap. The table names airlines,
    so the tool needs a list joining each customer code to the airline's name exactly as the table writes it.
  - Which aircraft (owner, 2026-11-11, D-32): whichever have open tasks at the data cut. Never fix a list of
    aircraft or of customers, and never tune to one fleet.
  - Calendars: a line whose calendar ends at or before its due date can never be planned late, so its lateness is
    never costed (`readiness_gaps` in the pipeline status: `CALENDAR_ENDS_BEFORE_DUE`, `CALENDAR_NO_DAYS_LEFT`). The
    calendar producer fixes that; FOCUS never adds a day.
- **Decisions.** Before asking the owner, look the question up in `DECISION_PLAYBOOK.md`: it says what to do now,
  what to decide from data, and what only the owner can decide.

## 1. What FOCUS does and what is decided

- **The scheduler.** It contains no AI. It runs before every shift, three times a day, and makes one schedule
  through delivery (owner, 2026-11-11, `docs/execution/DECISIONS.md` D-33).
  - It first makes the **shift plan** (`EXECUTABLE`): only work that can really start. Crews are dispatched from it
    and shifts are scored against it.
  - After that plan is accepted, it makes the **delivery plan** (`FORECAST`) from the same snapshot: delivery dates,
    days late and penalties. It keeps every slot of the shift plan and plans the rest of the flow around them, so
    the two never disagree about a shift (`docs/execution/PLAN_REPAIR.md`, "One schedule").
  - Every pre-shift run repairs the accepted plans: it keeps what still holds and re-plans what changed. That keeps
    the task lists similar from run to run and fits the time. The full solve runs once a night (§5.2).
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
  D ≥ 60). An OFFICIAL grade needs the owner's approval receipt (OD-16).
- **Extra work earns credit (owner, 2026-11-10, D-27).** Work a team does outside its plan counts toward its score
  at 80% of the same planned work and never lowers it. A planned job the team missed costs points on its own, unless
  it is excused. `docs/execution/SCORING_EXPLAINED.md` gives the rule, the owner's example and a worked example.
- **Where things run.** The GitLab pipeline runs the scheduler and every other step. Posit Connect only displays:
  the web app reads a small display package the pipeline publishes, never a snapshot or a plan.
  - **The application must open at once at shift start** (D-33). Set `FOCUS_WARM_SECONDS=30` on the Posit content:
    each web process then reads ahead, as soon as a plan is accepted, what every team lead's page asks for first.
    Leave `FOCUS_DISPLAY_CACHE_MB` at 384 or more and give the content at least 2 GB
    (`docs/execution/DEPLOYMENT.md`). At 50 aircraft a team lead's task list then opens in under a tenth of a
    second; without the read-ahead its first open takes about a second.
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

Before each shift, within 20 minutes (owner, 2026-11-10, D-28: "the data gathering and scheduling and dashboard should
be ready to go within 20 minutes"):

| Step | Budget | Measured |
| --- | --- | --- |
| Data cut and `stage_sources` (changed rows into SQL Server) | ≤ 2 min | not measured (company loader) |
| `verify_staged_sources` | < 1 min | not measured live |
| `acquire_admit` | ≤ 1–2 min (target) | 5.7 min at 50 aircraft: snapshot built 70 s, stored 64 s, its dashboard parts 135 s |
| `schedule_validate` | ≤ 8 min | 6.3 min: snapshot loaded again 109 s, first pass 145 s, two FINAL validations 61 s |
| `stage_readback`, `sign_accept` | ≤ 2 min | 5.4 and 5.8 min: each loads the snapshot again (about 109 s) and validates the plan three times (about 170 s) |
| **The plan must be accepted before the shift starts** | | |

- **What counts toward the window.** The clock starts at the data cut (the pipeline's start, which is the loader's
  `--as-of`). The owner's 20 minutes hold the whole schedule (D-33), so two instants are reported:
  - when `sign_accept` finishes: the shift plan is accepted and its dashboard data written, so the task lists are
    ready (`window.minutes`);
  - when `forecast_sign_accept` finishes: the delivery plan is accepted too, with its dates and late cost
    (`window.schedule_minutes`). `window.within_budget` is about this one.
  - `python -m focus.cli pipeline --key "$PIPELINE_KEY" --status` reports them under `window`, with the seconds per
    step and whether the shift plan was accepted after its shift started. Keep that report with every run.
  - Everything in between counts, staging included. Work finished after the cut is not in the plan, so every minute
    of staging, checking, planning and signing widens the gap that is captured after the fact.
  - `stage_sources` runs alongside the verify jobs. The wall-clock total is therefore the longer of the two paths,
    not their sum.
  - Staging that runs before the data cut does not count toward the window, and almost all of it can
    ([STAGING_PLAN.md](STAGING_PLAN.md)). SQL Server keeps a complete copy of every source table: the large slow
    tables are read in full weekly, floor state is reconciled nightly, and everything that changes in a day is
    brought up to date 60 minutes before each shift. The pass at the cut then pulls only the rows that changed: jobs new, started
    and completed, rework links, work in progress, shortages and receipts, attendance, leave and certificate
    revocations. An earlier load alone is never enough for those.
  - The evaluator-only run after overtime is outside the window.
- **First measured end to end: 23.2 minutes to the shift plan, over the budget.** The four jobs above, each in its
  own process, at 50 aircraft on the test machine on 2026-11-11 (`docs/execution/PRODUCTION_SCALE.md`, "The pre-shift flow, action by
  action"; `python tools/time_pre_shift.py`). The live data pull, each job's start and the acceptance come on top.
  - The scheduler is 3 of those minutes. Loading the snapshot again in three jobs is 5.4, validating the plan eight
    times is 6.7, and the snapshot's dashboard parts are 2.3.
  - The delivery plan's three jobs took another 27.1 minutes.
  - Since then (D-33): each validation is computed once per plan, every pre-shift run repairs, the `validate` step
    takes the solve's receipt and jobs end without teardown.
- **Measured again on 2026-11-12, same fleet and machine: 13.9 minutes to the accepted shift plan in a repairing
  run** (15.5 on a first run, which solves in full), **and 13.0 more to the accepted delivery plan: 26.9 minutes for
  the whole schedule.** The task lists fit the 20 minutes there; the dates and the late cost do not yet.
  - Six loads of the snapshot are 7.1 of the 26.9 minutes, validations 6.6, the source job 4.9 and the scheduler 1.7
    (`PRODUCTION_SCALE.md`, "The pre-shift flow after D-33"; `DECISION_PLAYBOOK.md`, DEC-36 has what is left and
    what each step would leave).
  - **In four jobs instead of seven the same run took 9.8 minutes to the accepted shift plan and 20.4 for the whole
    schedule** (`FOCUS_JOBS=planner`, §5.1). The owner leaves the split free (D-34), so the draft ships this layout.
    Each job that is not needed is also a job your runner does not have to start.
  - **The signer now starts from the staged bytes** and no longer reads and serializes the candidate a second time
    (74 seconds of that run). Measured on a second machine that runs identical jobs 14 to 22% slower: 11.1 minutes
    to the shift plan and 21.9 for the whole schedule. So the whole schedule is between about 19 and 22 minutes
    depending on the machine, and the task lists are inside the 20 minutes on both.
  - Two questions for whoever owns your runners and SQL logins (DEC-36): may one job hold the source, app and stager
    rights at once (if not, set `FOCUS_JOBS=roles` and expect about 27 minutes); and can two jobs of one pipeline
    run at the same time, with about 20 GB of memory between them. The owner does not know the second; find out,
    because it decides whether the delivery plan's job can run beside the shift plan's signer.
  - Your runner is not that machine. Run `python tools/time_pre_shift.py run WORKDIR --aircraft 50 --repair` on it
    once, and keep `window` from every live run.
- **A plan accepted after its shift starts** is only a proposal for that shift. The previous plan stays the goal of
  record, and the late plan becomes the next shift's. The calibration measured what late plans cost the teams: the
  95th-percentile shortfall doubled.
- **After each shift's credit deadline,** the evaluator-only pipeline scores that shift (§5).

## 4. The staging job (official sources → SQL Server)

### 4.1 Interface

`stage_sources` runs the company loader named by the protected variable `FOCUS_STAGE_COMMAND`:

```bash
$FOCUS_STAGE_COMMAND --part cut --as-of "$CI_PIPELINE_CREATED_AT" --receipt ci-receipts/stage.json
```

- **`--part`.** Which pass to run ([STAGING_PLAN.md](STAGING_PLAN.md) §7). Each dataset is gathered at the pace
  its content changes (D-34); [DATA_CADENCE.md](DATA_CADENCE.md) lists every dataset's pass on one page:
  - `cut`: the rows that changed since the pass before it, in the plan pipeline;
  - `preshift`: everything that changes during a day, 60 minutes before each shift;
  - `nightly`: floor state reconciled in full with the sources, the small daily tables whole, the rows that changed
    in the weekly tables, and history;
  - `weekly`: the large tables that change with releases and training, in full: the standing facts of every job,
    the certificates people hold, lookups, and history no plan reads;
  - `observations`: the observation feeds;
  - `sources` and `all`: a full load of the planning datasets, or of them and the feeds, for the first load and for
    recovery.
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
  - 3: the loader is not configured;
  - 5: another pass published while a job was reading (`--check-generation`, `STAGING_PLAN.md` §7).
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
2. **Load everything from one instant.** All SOURCE tables staged at the cut come from `--as-of`. Publish one active
   load of `cs_windows` per line: when a producer receipt declares the active load, admission rejects rows from
   another load (`CS_GENERATION_MISMATCH`), and two active loads for a line give `CS_LOAD_CONFLICT`.
3. **Stamp the row clock on every row.** Set the table's row clock (`LOAD_TIMESTAMP`, `SNAPSHOT_TS` or `LOAD_TS`)
   to the load instant, in UTC.
   - The live reader passes no `loaded_at`, so live freshness comes from these columns.
   - A stale clock or an unreadable one fails executable plans (`SOURCE_STALE`, `SOURCE_CLOCK_UNKNOWN`). The
     limits follow each dataset's cadence, from 18 hours for attendance to 336 for what the weekly pass reads
     whole, and only the twelve datasets a plan is built from are checked (`docs/data/README.md`, "Freshness and
     capture skew").
   - A pass must restamp the row clock of every table it read whole and found unchanged (`STAGING_PLAN.md` §6.3):
     refresh it, or keep a load-instant column the query reads.
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
8. **Load deltas, read full tables.** The pass at the cut is a delta pull: changed rows only, applied to a copy
   that is already complete. FOCUS always reads the full tables. Reconcile in full nightly (row counts and checksums
   against the source). `STAGING_PLAN.md` says which dataset is loaded in which pass, and how to find changed rows,
   new jobs and deleted rows.
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
| `stage_sources` | stage_sql | `focus-staging` | – | §4: the pass at the cut (`--part cut`) |
| `verify_staged_sources` | stage_sql | `focus-source` | `stage_sources` | Schema drift check |
| `prestage_sources`, `verify_prestaged_sources` | stage_sql | `focus-staging`, `focus-source` | – | Staging-only pipelines, 60 minutes before each shift (`--part preshift`), then the schema check |
| `nightly_sources`, `verify_nightly_sources` | stage_sql | `focus-staging`, `focus-source` | – | Staging-only pipelines, once a day (`--part nightly`), then the schema check |
| `weekly_sources`, `verify_weekly_sources` | stage_sql | `focus-staging`, `focus-source` | – | Staging-only pipelines, once a week (`--part weekly`), then the schema check |
| `plan_unsigned` | source | `focus-planner`, runner tag `focus-scheduler` (≥ 4 vCPU, 16 GB) | verify, `display_retention`, `verify_staged_sources` | As shipped (`FOCUS_JOBS=planner`, DEC-36 (g)): source run and snapshot, the shift plan (native core), unsigned stage and readback, in one job |
| `sign_accept` | sign | `focus-signer` | `plan_unsigned` (or `stage_readback`) | Signs, accepts and moves the head, by itself in a scheduled run (D-34) |
| `delivery_unsigned` | forecast | `focus-planner`, runner tag `focus-scheduler` | `sign_accept` | As shipped: the delivery plan scheduled, validated and staged in one job |
| `forecast_sign_accept` | forecast_sign | `focus-signer` | `delivery_unsigned` (or `forecast_stage_readback`) | Signs and accepts the delivery plan, by itself in a scheduled run |
| `acquire_admit`, `schedule_validate`, `stage_readback`, `forecast_schedule_validate`, `forecast_stage_readback` | source … forecast_stage | `focus-source`, `focus-app`, `focus-stager` | each needs the one before it | Only with `FOCUS_JOBS=roles`, in place of the two `*_unsigned` jobs: one job per role, each with its own login. Seven jobs a run instead of four, and about a third slower |
| `observe_evaluate` | observe | `focus-evaluator` | `sign_accept` | In-pipeline evaluation; in live mode it stops with `LIVE_OBSERVATIONS_UNAVAILABLE` today (§6) |
| `stage_observations` | stage_sql | `focus-staging` | – | Evaluator-only pipelines: load the observation feeds |
| `evaluate_closed_shifts` | observe | `focus-evaluator` | `stage_observations` | Evaluator-only pipelines: `tools/evaluate_closed_shifts.py` |

**Protected CI/CD variables.** Values live in GitLab or a vault, never in the repository.

- Every live job: `FOCUS_MODE=live` and `FOCUS_SCOPE_KEY`.
- `focus-staging`: `FOCUS_STAGE_COMMAND` and the source and staging credentials (company names).
- The SQL roles: `FOCUS_SQL_HOST`, `FOCUS_SQL_DATABASE`, `FOCUS_SQL_USER` and `FOCUS_SQL_PASSWORD`, one login per
  environment.
- `focus-signer` only: `FOCUS_SIGN_KEY_REF`.
- `focus-planner` (`FOCUS_JOBS=planner`, as shipped): a SQL login that is a member of the source-worker,
  pipeline-worker and stager roles, and never of the signer role. It has no signing material.
- Schedules: `FOCUS_PIPELINE_PART`.

### 5.2 Schedules

Set each schedule's time zone to America/New_York so daylight saving is handled.

| Schedule | Cron (site time) | Variables |
| --- | --- | --- |
| Weekly staging | `30 7 * * 0` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=weekly` |
| Nightly staging | `15 1 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=nightly` |
| Staging before shift 1 | `0 5 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=preshift` |
| Staging before shift 2 | `30 13 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=preshift` |
| Staging before shift 3 | `0 22 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=preshift` |
| Plan for shift 1 | `40 5 * * *` | `FOCUS_MODE=live` |
| Plan for shift 2 | `10 14 * * *` | `FOCUS_MODE=live` |
| Plan for shift 3 | `40 22 * * *` | `FOCUS_MODE=live` |
| Nightly full solve | `45 2 * * *` | `FOCUS_MODE=live`, `FOCUS_PLANNING=full`, `FOCUS_BUDGET_SECONDS`, `FOCUS_MAX_MOVES` |
| Score shift 1 | `35 15 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` |
| Score shift 2 | `5 0 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` |
| Score shift 3 | `5 7 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` |

- **Plan schedules** start 20 minutes before the shift (the data cut). Auto planning
  (`planning.cutoff_lead_minutes` = 60) then plans the next shift. Every one of them repairs the accepted plans
  (`PLAN_REPAIR.md`): `planning.full_solve_shifts` is empty.
- **The nightly full solve** is a fourth schedule of the same plan pipeline, with `FOCUS_PLANNING=full`. It rebuilds
  both plans, runs the improvement search for as long as `FOCUS_BUDGET_SECONDS` and `FOCUS_MAX_MOVES` allow (DEC-34),
  and records how far a repair would have landed. Its plans are accepted like any other. Accepted while shift 3 is
  running, they are that shift's proposal only (D-26), and they are what the run before shift 1 repairs.
  - Start it when the nightly staging pass has finished, and give its jobs timeouts that end it before the 05:00
    staging pass: the live jobs share one resource group, so a full solve that runs long would hold the shift 1 run.
  - If it is missed, the pre-shift runs keep repairing. Only a plan older than
    `planning.repair_max_incumbent_age_hours` (30) makes a pre-shift run fall back to the slow full solve.
- **Score schedules** run 5 minutes after each credit deadline.
- **Staging schedules** keep the copy in SQL Server complete and current before the cut (`STAGING_PLAN.md` §4),
  each dataset at its own cadence (D-34). The weekly pass runs for hours and shares the resource group: Sunday 07:30
  is after the morning's plan and scoring runs and six hours before the next pass. Move it to the day with the
  least work on the floor, and keep its 4-hour limit clear of the hour before a shift.
- A plan schedule sets no `FOCUS_PIPELINE_PART`. A value the draft does not know runs nothing.
- `resource_group: focus-$FOCUS_SCOPE_KEY` serializes the live jobs of one scope, so a staging pass never publishes
  while `acquire_admit` reads. No live job is interruptible.

### 5.3 Two timing risks, both settled

- **Verification inside the live window: settled by the 20-minute requirement (D-28).**
  - The full non-UI suite alone takes about 10 minutes. With the installs, the UI suite and the rehearsal,
    verification would use most of the window before acquisition could start.
  - The draft therefore runs the suites on merge requests and on the default branch. A live run uses only code from
    a protected ref, and `acquire_admit` refuses any other. `import_and_lint`, the preflights and the staged-schema
    check stay in every run.
  - `FOCUS_LIVE_VERIFY=full` puts the suites back into every live run.
  - Protect the default branch and require a passed pipeline to merge, or the rule protects nothing.
- **Signing: no person accepts a plan (owner, 2026-11-12, D-34; DEC-11 is decided).**
  - "once a schedule is produced that is what the mechanics and shop floor should use to determine what jobs they
    work. They do not need to "accept" anything - the scheduler is the answer."
  - `FOCUS_AUTO_SIGN: "true"` and `FOCUS_AUTO_SIGN_DELIVERY: "true"` in the pipeline draft. In a scheduled pipeline
    on a protected ref, `sign_accept` and `forecast_sign_accept` run by themselves once the jobs before them
    succeed. The signer still validates the staged bytes itself and refuses a plan without a clean FINAL validation
    and a clean readback.
  - A plan that fails a check is never accepted. The plan before it stays in force and the pipeline fails. Nobody is
    watching a promote button any more, so **the alert is required**: tell a person when a plan pipeline fails or
    has not accepted its plan five minutes before the shift.
  - A pipeline started by hand still waits for the person who started it. To recover a scheduled run, retry its
    failed job: the pipeline is still a scheduled one and accepts by itself.
  - To hold plans for an operator again, set `FOCUS_AUTO_SIGN=false` on a run or in the project
    (`FOCUS_AUTO_SIGN_DELIVERY=false` for the delivery plan alone). Protect the signer environment and the default
    branch, or the rule protects nothing.

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
6. **Write the staging loader** (company code), following §4 and `STAGING_PLAN.md`. Start with its section 11: the
   inventory and the timing of every Teradata query.
7. **Policy.**
   - Correct the out-of-date `live_effect` texts (DECISIONS.md lists them). Do this with the next policy revision,
     because it changes the policy hash.
   - The owner must answer the open decisions OD-02 to OD-05 and OD-07 to OD-17.
8. **Shadow, then pilot, then calibrate.**
   - Shadow (crews still follow the existing schedule) proves the pipeline, its timing and the plan's realism. Its
     scores say how closely FOCUS's plan matched what the floor did; they do not measure the teams.
   - Calibrate only on a pilot, where some teams are dispatched from the accepted plan and scores are computed but
     used for nothing, for at least four weeks. Then follow `10_CALIBRATION.md` §8 (bands, noise, fairness, w, the
     classifier's error rates).

## 7. Owner decisions still open

`DECISION_PLAYBOOK.md` gives each of these, and the engineering and data decisions, a recommendation and a decision
rule.

- **Decided, not open:** nobody accepts a plan (D-34), and the long suites run on merge, not before every plan
  (D-28). §5.3 has both.
- **Late plans:** how to protect teams scored against a stale plan. For example, review the units the late plan
  moved under the existing `PLAN_CHURN` track (`10_CALIBRATION.md` §7).
- **Overtime credit.** Today a job finished in a shift's authorized overtime is credited to the next shift's goal when
  the next plan also holds it. The next shift's window contains the completion, and shift windows are checked before
  overtime windows. The finishing shift's unit is then missed (`COMPLETION_CREDITED_ELSEWHERE_OR_LATE`). The
  alternative is to credit the crew that finished it; completion records carry the person.
- **The rate for extra work (0.8 today) and the grade bands:** after the real-data calibration. Whether an approved
  addition should raise what is expected is also open (`DECISION_PLAYBOOK.md`, DEC-26).
- **Live load cadences:** determined per dataset (D-34, `DATA_CADENCE.md`), as starting values. The freshness
  limits follow them and are to be re-set from measured load intervals (DEC-13, a data decision).
- **The open decisions OD-02 to OD-05 and OD-07 to OD-17**, and the score policy approval (OD-16).

## 8. First live runs: checklist

- [ ] One clean run of each staging pass: weekly, nightly, before a shift, at the cut. Receipts kept.
- [ ] `stage_sources` receipt: all 28 SOURCE tables published, row clocks stamped, durations within budget.
- [ ] `verify_staged_sources`: no drift.
- [ ] `acquire_admit`: run ACCEPTED, census reconciled, readiness gaps reviewed with their owners (blocked and
      quarantined jobs, `DATASET_*`, `OD-*`).
- [ ] A complete-scope FORECAST plan, with every aircraft's delivery date or a stated reason.
- [ ] EXECUTABLE: the jobs withheld and why (crews, certificates, parts, QA holds) match what supervisors know.
- [ ] Timing: data cut to acceptance inside the window, three times a day, for a week, with nobody promoting.
- [ ] The alert fires when a plan pipeline fails or has not accepted its plan five minutes before the shift.
- [ ] Display package published, and Posit shows the accepted plan in local time.
- [ ] At shift start a team lead's task list opens in under a second, on a Posit process that has been running since
      before the plan was accepted (`FOCUS_WARM_SECONDS=30`).
- [ ] `evaluate_closed_shifts`: typed UNKNOWN results until the feeds exist; then scores whose job records add up
      (B, Cp, E, U).
- [ ] `tools/calibrate_scores.py store`: `stale_plans` about 0, `DONE_BEFORE_SHIFT` share plausible.

## 9. Where things are

| Need | File |
| --- | --- |
| Datasets, columns, required flags | `focus/data/datasets.py`; `docs/data/` |
| Every dataset's cadence and every schedule, on one page | `docs/integration/DATA_CADENCE.md`; `focus/data/cadence.py` |
| What to stage, in which pass, and the staging pipelines | `docs/integration/STAGING_PLAN.md` |
| Live reader, SQL access, allowlists | `focus/data/fixture_reader.py` (`LiveSourceReader`), `focus/data/sql.py` |
| Acquisition, admission, freshness | `focus/data/acquisition.py`, `snapshot_builder.py`, `freshness.py` |
| Observation feeds | `focus/performance/actuals.py`; `docs/data/09-observation-feeds.md` |
| Scoring and D-26 | `focus/performance/lifecycle.py`, `score.py`, `amendments.py`, `rollup.py` |
| Pipeline steps and roles | `focus/orchestration.py`; `docs/execution/INSTALL_AND_PIPELINE.md` §4 |
| Migrations and grants | `sql/migrations/`, `sql/grants/focus_fn_roles.sql`, `focus/migrations.py` |
| Live store (pending) | `focus/data/sql_store.py` |
| Objective, cost and criticality reports | `focus/economics/evaluator.py` (`objective_status`), `focus/valuation/criticality.py`; `docs/execution/OBJECTIVE_EXPLAINED.md` §4 and §5 |
| Calendar coverage check | `focus/calendar.py` (`coverage_gaps`); `docs/data/02-calendars-and-stations.md` |
| Economics rows from a rate file | `tools/build_economics_rows.py`, `focus/economics/rate_file.py`; `docs/data/08-economics.md` |
| Score calibration on real data | `tools/calibrate_scores.py`; `docs/task-valuation-investigation/10_CALIBRATION.md` |
| Evaluator-only run | `tools/evaluate_closed_shifts.py` |
| Simulation (synthetic) | `focus/sim/`; `docs/execution/SIMULATION.md` |
