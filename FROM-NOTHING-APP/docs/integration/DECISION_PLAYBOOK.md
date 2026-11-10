# Decision playbook for the coding agent

**What this is.** Every decision that is still open for FOCUS, written so an agent with little context can work
through them one at a time. Each entry gives the context, the options, a recommendation to proceed with, the rule for
deciding once real data is wired, the files to change, the tests to update and where to record the result.

**The owner's instruction.** Work through each decision. Proceed with the recommendation now where no data is
needed. Make the data-dependent decisions once real data is wired, using the rules below. The few decisions marked
**OWNER** need the owner's sign-off even so: prepare the evidence, build the change behind a switch, and ask.

**State on 2026-11-02.**

- FOCUS runs on synthetic fixtures only.
- No SQL is installed, no pipeline is active, and no company data has been read.
- Full non-UI test suite: 906 passed, 3 skipped (10.5 minutes).

**Changed on 2026-11-10.** The owner decided that extra work earns credit (D-27, DEC-22). First live wiring by a
coding agent showed every aircraft with an unknown late-delivery cost, which added DEC-28 to DEC-30.

**Read first, in this order:**

1. `docs/integration/HANDOFF.md`: architecture, staging contract, GitLab, work items.
2. `docs/execution/OBJECTIVE_EXPLAINED.md`: what the scheduler minimizes, and how to tell whether the cost
   objective is working on live data.
3. `docs/execution/SCORING_EXPLAINED.md`: how a team's shift is scored.
4. This playbook.
5. `docs/execution/DECISIONS.md`, for history; each decision below names the entries it touches.

## 0. Guardrails (apply to every decision)

- **Reading needs no approval.**
  - Read the whole repository, run the tests and run read-only commands without asking, and without waiting for a
    reading plan to be approved.
  - Approval is for changes only: the writes listed below and the decisions marked OWNER.
- **Confidentiality.**
  - This public repository holds synthetic data only. Real system names, object names, query text, mappings,
    credentials, data rows, team scores and calibration outputs stay in company storage and the company's internal
    fork.
  - Record decisions there. In the public repository, record at most the rule that was applied, never company
    values.
- **No unauthorized writes.**
  - Never install migrations, apply grants, publish plans or enable dispatch without the authorized installer and
    owner approvals described in `INSTALL_AND_PIPELINE.md`.
  - Read-only discovery is always allowed.
- **Unknown stays unknown.**
  - Never fill a missing rate, duration, crew size, date or calendar day with an average, a median, a neighbor's
    value or a default. A known number built on a guess is wrong and looks right.
  - Record how many rows are affected, by reason, and work the cause (`OBJECTIVE_EXPLAINED.md` §4). A fallback is an
    owner decision, recorded with its label.
- **Never self-approve the OFFICIAL score policy.**
  - `score.policy_status` stays `DEMONSTRATION` until the owner's approval receipt exists
    (`score.policy_approval_ref`, OD-16).
  - Personal grades stay disabled.
- **Policy changes change the policy hash.**
  - Every edit to `config/policies/*.json` makes period rollups that span the change UNKNOWN (`MIXED_POLICY`,
    `focus/performance/rollup.py`).
  - Batch policy decisions and apply them at a period boundary: Monday 00:00 site time, or a month start.
  - Note the change in `DECISIONS.md`.
- **Do not relabel an `open_decision` key to `owner_decision` without the owner.**
  - That label is the switch that lets an unconfirmed semantic drive live executable plans
    (`snapshot_builder.py`, `graph.py`).
  - You may set the value from data and write the evidence in the key's `ref`, then ask the owner to confirm the
    relabel. Batch these confirmations.
  - Allowed labels (`focus/policy.py`): `user_requirement`, `manual_specified`, `fixture_default`, `demo_default`,
    `open_decision`, `owner_decision`.
- **One canonical formula.** Scores come only from `focus/performance/score.py` (`formula`, `status_and_score`). A
  scoring change updates in one change:
  - the code;
  - the simulation (`focus/sim/execute.py`);
  - the reference views (`tests/support/reference_views.py`);
  - the docs (`PERFORMANCE_POLICY.md`, `docs/data/09-observation-feeds.md`);
  - the tests.
- **Tests that pin decisions.** Update them in the same change as the decision they pin:
  - `tests/unit/test_policy_config.py` pins which OD ids are open and that `cs.date_mode` is `open_decision`;
  - `tests/integration/test_migration_pipeline.py::test_ci_draft_orders_roles_and_protects_signing` pins
    `when: manual` on both signing jobs, that acquisition needs every verify job in its pipeline, and that the long
    suites run on merge while a live run requires a protected ref (D-28);
  - `tests/unit/test_data_reference_docs.py` pins the dataset columns against `docs/data/`.
- **Validate before you push.**
  - Fast checks: `python tools/check_imports.py`, then `python -m focus.cli migrate --dry-run`, then the test files
    you touched.
  - Full suite (10.5 minutes): `python -m pytest -q -p no:cacheprovider tests --ignore=tests/ui`.
  - UI suite: `python -m pytest -q -p no:cacheprovider tests/ui`.
- **Report honestly.** Never claim an install, a signature, an acceptance, a deploy or a review that did not happen.

## 1. How to work one decision

1. Read the entry. Check its **Prerequisites**; if they are not met, use the **Default now** and move on.
2. Run the **Measure** commands, in the company environment, against company data.
3. Apply the **Decision rule**. When the data is ambiguous, take the safer option: the one that withholds work from
   EXECUTABLE plans or scores UNKNOWN rather than guessing.
4. Change the listed files, following the guardrails.
5. Run the listed tests, then the fast checks.
6. Record the decision. Use the template in §8: a new `D-27`, `D-28`, … entry in `docs/execution/DECISIONS.md`, and
   for an OD item, an entry in the policy file's `decided_decisions` once the owner confirms.
7. For **OWNER** items, write the memo (template in §8), build the change behind the named switch with the switch
   off, and stop there until the owner answers.

**Authority levels used below:**

| Level | Meaning |
| --- | --- |
| **ENGINEERING** | Decide and implement now; no company data needed. |
| **DATA** | Decide from the rule once real data is wired. Until then, use the default. The owner can override. |
| **OWNER** | Prepare evidence and a recommendation; implement behind a switch; the owner signs. |

## 2. Decisions already made — do not reopen

| Decision | Rule | Where |
| --- | --- | --- |
| No AI in the scheduler | The plans come from the deterministic optimizer; explanations are validated text only | `focus/engine/` |
| Cadence | The scheduler runs before every shift, 3× a day, as a global optimization through delivery; GitLab runs it; Posit Connect only displays | D-22, D-26 |
| Per-shift goals (D-26) | A shift is scored against the EXECUTABLE plan accepted at or before its start; if none was, the first plan accepted for its day stands in | `lifecycle.GoalRegistry.plan_of_record` |
| Adherence = same job | Start and finish times are never compared with the plan | D-26 |
| No early credit | A completion before a goal's shift earns nothing there | `lifecycle.select_goal` |
| Done before the shift | A unit whose phase was verified complete before its shift started is `DONE_BEFORE_SHIFT` and counts in E | `lifecycle.unit_facts`, `score.components` |
| Amendments during the shift only | The command and the feed refuse `SHIFT_NOT_STARTED` / `SHIFT_ENDED` | `amendments.py`, `actuals.amendment_from_row` |
| Late events after the fact | Scores come from timestamped events once overtime closes, and are revised when data arrives late | D-26 |
| 20 minutes before each shift (D-28) | Data gathering, scheduling and the dashboard are ready within 20 minutes, from the data cut to the accepted executable plan. The test suites run on merge, not in the run | `orchestration.window`, `.gitlab-ci.yml`, `pipeline.pre_shift_budget_minutes` |
| Extra work earns credit (D-27) | Every unplanned job a team gets done counts toward its score at 80% of the same planned work (rate confirmed by the owner) and never lowers it. A missed planned job costs points on its own unless excused. Adherence is the planned jobs done over the expected work | `score.formula`, `score.components`, `docs/execution/SCORING_EXPLAINED.md` |
| Criticality comes from the objective (D-29) | A job's criticality is its aircraft's modeled cost of one more day late in the accepted forecast, as a share of the fleet's highest. Never assigned by hand or by job type. Reported only; whether it weights a score is DEC-31 | `focus/valuation/criticality.py` |
| OD-01 crew | `TOTAL_MECHANICS` is the crew needed on the job at once (owner, 2026-10-31) | `duration.total_mechanics_semantics` |
| OD-06 due date | CS744 planned unload + 2-day grace, as a whole local day (owner, 2026-11-02) | `due.*` |
| Time | The database holds UTC; the app shows site local time (America/New_York); live `FOCUS_SOURCE_TIMEZONE_MAP_JSON={"*": "UTC"}` | owner, 2026-10-31 |
| Display retention, per-job details | Display parts deleted when unread (D-24); per-job score evidence kept for 90 days (D-25) | D-24, D-25 |
| Planning mode | `auto`: full solve before shift 1, repair otherwise; full solve when the incumbent is over 12 h old | `planning.*`, `PLAN_REPAIR.md` |
| Penalty proxy | Modeled from public airline and pricing data, labeled as a proxy (owner: keep) | A15 |
| Duration statistic | P75 of verified comparable rework episodes | `duration.default_statistic` (`user_requirement`) |

## 3. At a glance

| Id | Decision | Authority | Default now | Decide when |
| --- | --- | --- | --- | --- |
| DEC-00 | First full read, with the scoring check | ENGINEERING | read-only, no approval | first |
| DEC-01 | Live store adapter and migration 011 | ENGINEERING | build it | now |
| DEC-02 | Code ids vs `uniqueidentifier` columns | ENGINEERING | deterministic UUID mapping | now |
| DEC-03 | Signer path vs separation of duties | ENGINEERING | signer only accepts | now |
| DEC-04 | Where delivery commitments are recorded | ENGINEERING | inside acceptance | now |
| DEC-05 | Read grants for worker roles | ENGINEERING | least privilege per step | now |
| DEC-06 | Live observe and evaluate | ENGINEERING | as `evaluate_closed_shifts` | now |
| DEC-07 | Certified-empty and `loaded_at` for live captures | ENGINEERING | producer receipt table | now |
| DEC-08 | Staging loader design | ENGINEERING | per `HANDOFF.md` §4 | now |
| DEC-09 | Order of supplemental producers | ENGINEERING | `work_progress` first | now |
| DEC-10 | Test suite inside the live window | **settled 2026-11-10 by the 20-minute requirement (D-28), built** | suites run on merge; live runs need a protected ref | now |
| DEC-11 | Signing before each shift | OWNER | recommend: automatic with a kill switch | before go-live |
| DEC-12 | Schedule times inside the 20-minute budget | DATA | start 20 min before each shift; crons in `HANDOFF.md` §5.2 | after 1 week of runs |
| DEC-13 | Freshness limits (load cadences) | DATA | fixture limits 18–240 h | after 2 weeks of loads |
| OD-02…OD-17 | Data semantics (§5) | DATA, then the owner confirms the relabel | fixture meanings; live withheld | as each source is wired |
| DEC-19 | Validate the score on real data | DATA, then OWNER | no grade counts until done | after 4 weeks of pilot |
| DEC-20 | Swap watch: unplanned work done in place of planned work | DATA | report it; it changes no score | after 4 weeks of pilot |
| DEC-21 | The rate for extra work | **decided 2026-11-10: 80%** | keep; revisit only on DEC-20 evidence | now |
| DEC-22 | Extra-work credit | **decided 2026-11-10 (D-27), built** | port it; do not reopen | now |
| DEC-23 | Grade bands | OWNER (with data) | demo 90/80/70/60, DEMONSTRATION | after 4 weeks of pilot |
| DEC-24 | Late-plan protection | OWNER | evidence-only `PLAN_CHURN` claims | after 1 week of runs |
| DEC-25 | Overtime credit | OWNER (with data) | today's rule | after 4 weeks of pilot |
| DEC-26 | Approved additions and extra-work credit | OWNER | obligations (today) | before the pilot |
| DEC-27 | Policy `live_effect` text corrections | ENGINEERING | with the first policy batch | first policy batch |
| DEC-28 | Calendar days after the last control station | DATA, then OWNER | the producer sends the site's working days through the horizon | with the first live plans |
| DEC-29 | Make the cost objective steer on live data | ENGINEERING and DATA | count the unknowns every run; work them before speed | with the first live plans |
| DEC-30 | Completed jobs in the planning snapshot | ENGINEERING | keep them; fix the writes first | with the first live plans |
| DEC-31 | Should criticality weight a team's score | OWNER (with data) | no: reported only (D-29) | after 4 weeks of pilot |
| OD-16 | OFFICIAL score policy approval | OWNER only | DEMONSTRATION | last |

## 4. Engineering decisions (take now, no company data needed)

### DEC-00 First full read, with the scoring check

- **Authority:** ENGINEERING. It is read-only, so it needs no approval and no approved plan.
- **Do:**
  1. Read `HANDOFF.md`, this playbook and `docs/execution/SCORING_EXPLAINED.md`.
  2. Read the code, following `HANDOFF.md` §9 ("Where things are").
  3. Check the code against the scoring rules, as `SCORING_EXPLAINED.md` §4 describes.
  4. Run the fast checks and the scoring tests named there.
- **Report:**
  - a short summary of how the system works;
  - every difference between a documented rule and the code, with file and line;
  - your open questions, each checked against this playbook first.
- **Do not** change code, docs or policy during the read. Where a rule and the code disagree, the owner decides
  which one is wrong.

### DEC-01 Live store adapter and migration 011

- **Authority:** ENGINEERING. The DBA installs.
- **Why.** In live mode `SqlStore` (`focus/data/sql_store.py`) refuses every write, so nothing persists. Several
  runtime collections have no FOCUS_FN table at all. `docs/execution/SQL_SCHEMA_LEDGER.md` lists them: candidates,
  snapshots, org charts, the display package, retention ledgers, attendance and holding evidence, scenarios,
  explanations and the observation head.
- **Recommendation.**
  1. Write `sql/migrations/011_*.sql` with tables for those collections, and fix the known issues in it:
     - the quarantine key gains the revision;
     - `ObservationProducerReceipt` allows eight feeds;
     - a locked `FOCUS_FN_PurgeEvaluationEvidence` sets `focus_retention`;
     - the `TR_FN_EvaluationEvidence_Immutable` trigger honors that bypass;
     - the retention role gets SELECT on `JobOutcome`;
     - display-package tables with a locked purge procedure.
  2. Implement the repository protocol over the tables in `sql_store.py`, mapping the per-day documents
     (`team_shift_performance`, `job_outcomes`) to row tables.
  3. Update `FN_OBJECTS` in `focus/data/sql.py`: remove `Artifact` and `State`, and add the 29 created objects the
     ledger lists.
- **Rules.** Migrations are append-only and versioned: never edit 001–010, whose checksums are recorded at install.
  Regenerate the rollbacks and grants with `python -m focus.cli migrate --write-generated`.
- **Verify:**
  - `python -m focus.cli migrate --dry-run`, which runs the lint and freshness checks;
  - `tests/integration/test_migration_pipeline.py`;
  - the repository fault-injection suites against a non-production database.
- **Record:** a D-27 entry, `SQL_SCHEMA_LEDGER.md` (inventory and runtime collections), and `INSTALL_AND_PIPELINE.md`
  §1 (the version table).

### DEC-02 Code ids versus `uniqueidentifier` columns

- **Authority:** ENGINEERING.
- **Why.** Code ids are prefixed hex digests (`GOAL…`, `EVL…`), but `GoalId` and `EvaluationId` are
  `uniqueidentifier` columns (001, 010).
- **Options:**
  - (a) store `uuid5(NAMESPACE, code_id)` in the GUID column and add a nullable `CodeId varchar(64)` column in 011;
  - (b) change the column types in 011.
- **Recommendation: (a).** It needs no type change on keys with foreign keys, it is deterministic and reversible, and
  the lint allows nullable added columns. Put the mapping in one function used by every adapter path.
- **Verify:** a round-trip unit test (code id → GUID → code id via `CodeId`), and that idempotent replays produce the
  same GUID.
- **Record:** D-27 (with DEC-01).

### DEC-03 The signer path versus separation of duties

- **Authority:** ENGINEERING. It keeps an existing rule.
- **Why.** The `sign_accept` step runs the full `publish` (`focus/orchestration.py` → `focus/publication/service.py`).
  That stages, reads back and records the readback validation before it accepts. The grants reserve those
  procedures to the stager, and the grant lint enforces "only the stager stages and reads back".
- **Options:**
  - (a) give the signer step its own path: re-read the stager's staged bytes, re-validate in memory, sign, then call
    `AcceptPlan`, with no staging calls;
  - (b) grant the signer the stager's procedures.
- **Recommendation: (a).** Option (b) breaks separation of duties.
- **Verify:**
  - `tests/integration/test_publication_recovery.py`, especially A50 and A51;
  - `tests/integration/test_migration_pipeline.py`;
  - a new test that the signer role never calls stage, read stage or record validation.
- **Record:** a D-28 entry; `INSTALL_AND_PIPELINE.md` §2 (remove the open item); `ROLLBACK.md`.

### DEC-04 Where delivery commitments are recorded

- **Authority:** ENGINEERING.
- **Why.** Accepting a FORECAST plan records delivery commitments (`service.py`, `projections.record_accepted`). In
  SQL that is `RecordDeliveryObservation`, which is granted to the evaluator only. Nothing writes `DeliveryReceipt` or
  `CommitmentResult`.
- **Recommendation.** Record the commitment observation inside `AcceptPlan`'s transaction, either by calling it from
  the procedure or by merging the logic. A recovery after a crash (A50) must leave the commitment state
  byte-identical, which needs the same transaction. Then define a writer for `DeliveryReceipt` and
  `CommitmentResult` (the evaluator, from delivery observations), or drop them in 011 if no reader needs them.
- **Verify:** `tests/integration/test_publication_recovery.py`, and `tests/unit/test_commitments.py`.
- **Record:** D-28.

### DEC-05 Read grants for the worker roles

- **Authority:** ENGINEERING. The DBA applies the grants.
- **Why.** Only `FOCUS_FN_AppReader` has any SELECT grant, but every worker reads:
  - acquisition reads `TeamInference`, `ClockObservation` and `SourceHead`;
  - the app role reads the snapshot and plans;
  - the evaluator reads plans and goals.
- **Recommendation.** Least privilege per step. Add SELECT grants to `ROLE_GRANTS` in `focus/migrations.py` for
  exactly what each step reads, add an app pipeline role for `focus-app`, then regenerate
  `sql/grants/focus_fn_roles.sql`.
- **Verify:** the grant lint (`migrate --dry-run`) and `test_migration_pipeline.py`.
- **Record:** D-28; `INSTALL_AND_PIPELINE.md` §2.

### DEC-06 Live observe and evaluate

- **Authority:** ENGINEERING.
- **Why.** In live mode, `step_observe_evaluate` (`focus/orchestration.py`) raises `LIVE_OBSERVATIONS_UNAVAILABLE`, so
  the in-pipeline evaluation never scores live.
- **Recommendation.** Make the live branch do what `tools/evaluate_closed_shifts.py` does:
  `collect_observations(services, as_of=now)` with the live reader, then `score.evaluate_all(services, as_of=now)`.
  Keep the check that the accepted plan is current.
- **Verify:** the fixture path stays identical (`tests/integration/test_migration_pipeline.py`), plus a live-mode unit
  test with a stub reader.
- **Record:** D-28; `HANDOFF.md` §6.

### DEC-07 Certified-empty and `loaded_at` for live captures

- **Authority:** ENGINEERING.
- **Why.** `LiveSourceReader` (`focus/data/fixture_reader.py`) marks every zero-row read `EMPTY_UNCERTIFIED`. An empty
  required dataset therefore rejects the run even when empty is correct, for example no open shortages. It also
  passes no `loaded_at`, so freshness depends on row clocks.
- **Recommendation.** Add a producer receipt table (in 011), written by the staging job: dataset, load instant, row
  count, certified-empty flag and load id. The live reader reads the receipt for each dataset:
  - a zero-row capture with a certified-empty receipt for the same load becomes `CERTIFIED_EMPTY`;
  - the receipt's load instant becomes `loaded_at`.
- **Verify:**
  - `tests/integration/test_source_run_failures.py` and `tests/integration/test_source_freshness.py`;
  - new tests for a certified empty capture and a mismatched load id.
- **Record:** D-28; `docs/data/README.md` (capture statuses and "Known gaps").

### DEC-08 The staging loader design

- **Authority:** ENGINEERING. It is company code in the internal fork.
- **Default.** Follow `HANDOFF.md` §4 exactly:
  - the `--part`, `--as-of` and `--receipt` interface;
  - atomic publication (shadow tables and a swap);
  - one extraction instant;
  - row clocks stamped in UTC;
  - the complete census;
  - append-only observation history;
  - delta loads with a nightly full reconciliation.
- **Staging counts toward the pre-shift window.** The window runs from the data cut to plan acceptance
  (`HANDOFF.md` §3). Only a preload that runs before the cut is outside it.
- **Measure** (once wired): the duration of each dataset load; the 95th percentile must fit the 2-minute budget.
- **Decision rule.** If the 95th percentile is over 2 minutes, move the slowest datasets to an earlier preload, at
  T−40 minutes, for slowly changing tables (`cday_op_plan`, `cs_windows`, `pnet_dag`, `cert_course_map`,
  `dept_team_skill_ref`), and keep the fast-moving ones at T−20. Never preload completions, work in progress,
  shortages, rosters or attendance: the plan would be built on data older than the cut.
- **Verify:** `verify_staged_sources` passes, and a week of `stage.json` receipts shows complete loads.

### DEC-09 Order of the supplemental producers

- **Authority:** ENGINEERING.
- **Why.** 14 supplemental datasets report `NOT_PROBED` live, and each one withholds part of the EXECUTABLE plan
  (`docs/data/`).
- **Recommendation**, in order:
  1. `work_progress` (without it every in-work job is blocked);
  2. `shift_roster`;
  3. `ot_authorizations`;
  4. `availability_exceptions`;
  5. `carry_in`;
  6. `requirement_census`;
  7. `holding_events`;
  8. `rework_classification`;
  9. `inspection_holds`;
  10. `part_events`;
  11. `duration_episodes`;
  12. `scope_receipt`, `requirement_alternatives` and `people_directory`.

  Each producer needs a landing table (in 011 or later), a query registered in `LIVE_SUPPLEMENTAL_QUERIES`, and the
  as-of rule for datasets with a cutoff column.
- **Verify:** `tests/unit/test_live_reader_binding.py` and `tests/unit/test_data_reference_docs.py` (the docs must
  match), and the readiness gap counts fall as each producer lands.

## 5. Data semantics: the open decisions OD-02 to OD-17

The general rule for every OD:

1. Profile the source with the listed read-only query, inside the company environment.
2. Write down what the data and the system owner say.
3. Set the policy value with the evidence in `ref`.
4. Ask the owner to confirm the relabel from `open_decision` to `owner_decision`.
5. On confirmation, move the item from `open_decisions` to `decided_decisions` in `config/policies/rebuild-v1.json`
   (fields: `id`, `topic`, `decision`, `decided_by`, `decided_on`, `policy_key`), update `test_policy_config.py`, and
   update the dataset pages in `docs/data/`.

Until confirmation, live behavior stays as the "Live effect" says.

| OD | Topic | Live effect until decided | How to decide from data | Recommended answer |
| --- | --- | --- | --- | --- |
| OD-02 | Elapsed rework durations | No live P75; rework jobs without remaining work are blocked (`DURATION_UNAVAILABLE`). The P75 comes only from `duration_episodes`. | Build a `duration_episodes` producer from raw clock intervals (`queries/durations/build_observations.sql`, `percentiles.sql`). Check cohort sizes per class, team, model and crew. | P75 of verified comparable episodes (already `user_requirement`); a cohort below the policy's minimum stays unavailable, never defaulted. |
| OD-03 | QA hold locations and inspector roles | Jobs with in-process inspections are blocked in every plan (`IP_HOLD_UNKNOWN_LOCATION`). | `queries/quality/inspection_profile.sql`; find the QA system's hold points per job. | An `inspection_holds` producer from the QA system; no inference from counts. |
| OD-04 | Breaks and overtime authority | Live OT = 0. | Find the labor system's OT authorizations per team, shift and day. | An `ot_authorizations` producer; protected windows stay as in `shift.protected_windows`. |
| OD-05 | CS date basis | **Every live job is kept out of EXECUTABLE plans** (`CS_DATE_SEMANTICS_UNCONFIRMED`). Conflicting loads give `CS_LOAD_CONFLICT`. | `queries/calendars/cs_window_grain.sql` and `calendar_generations.sql`. Are the dates resolved per line, or offsets from a reference? Is there one active load per line? | `cs.date_mode = RESOLVED` if the dates are present and stable per load; otherwise `OFFSET_FROM_REFERENCE` with `cs.offset_day_basis`. **Highest priority: it gates all executable work.** |
| OD-07 | Concurrent and non-concurrent edges | Both jobs of a hard concurrent or non-concurrent edge are quarantined (`CONCURRENCY_SEMANTICS_UNRESOLVED`). | `queries/graph/template_edge_profile.sql`. For hard pairs, check history: did the actual intervals overlap (concurrent) or never overlap (non-concurrent)? | Confirm the fixture meanings (`REQUIRED_OVERLAP`, `NO_OVERLAP`) if 95% or more of the historical pairs agree; otherwise ask the engineering owner. |
| OD-08 | Rework class source | Open `IS_REWORK` jobs are blocked (`REWORK_CLASS_UNKNOWN`). | `queries/graph/rework_linkage.sql`; find the NC/PU/CI codes in the quality system. | A `rework_classification` producer from the quality system, with root and defect-origin team. |
| OD-09 | Affirmative no-certificate census | Jobs without requirement rows stay blocked (`REQUIREMENTS_UNKNOWN`). | `queries/qualifications/requirement_census.sql`. Which system affirms "no certificate needed"? | A `requirement_census` producer; absence is never proof. |
| OD-10 | Assignment and attendance feeds | No executable crews (both tables were empty on 2026-10-27). | Check that `person_team_map` (with `SUPERINTENDENT`) and `attendance_shift` are populated and current. | Load both from HR and timekeeping, plus `shift_roster`; team inference remains forecast-only. |
| OD-11 | Travel origin at shift start | Muster at the first assignment, 0 minutes. | Check whether crews actually start at a muster point that costs time. | Keep the fixture rule unless supervisors report material travel at shift start. |
| OD-12 | Economics terms, caps and checkpoints | Cost UNKNOWN where terms are missing; no default rate. | `queries/economics/effective_terms.sql` and `liability_floor.sql`. | Load approved terms only; leave unknown unknown. The planner still orders work by the other objectives. |
| OD-13 | Master status meanings | PREVIEW and FORECASTED stay forecast-only; unknown statuses are quarantined. | Count the statuses in the master list, and confirm each meaning with the planning system owner. | Confirm `scope.status_classes`, adding any new status explicitly; unknown is never open. |
| OD-14 | Purpose behind delivery commitments | FORECAST plans drive commitments (fixture). | Ask the delivery owner which date is promised. | FORECAST, as today. |
| OD-15 | Live observation producers | Official evaluation stays UNKNOWN. | Find the sources for verified completions, clock intervals, claims, decisions, amendments and defect evidence. | Producers into the migration 008 landing tables, plus attendance and holding evidence tables (DEC-01); `OBSERVED_AT_UTC` is when the producer first knew. |
| OD-16 | OFFICIAL score policy and personal grades | DEMONSTRATION; personal grades disabled. | n/a | **OWNER only** (§7). |
| OD-17 | Excusal reviewer identity | No decision is admitted; relief stays 0. | List who reviews excusals today. | Map the reviewers' SSO groups to `excusal_decider` in `FOCUS_GRANTS_JSON` (`docs/execution/DEPLOYMENT.md`). A claimant never decides their own claim. |

**Also correct with the first policy batch (DEC-27).** The policy file's `live_effect` texts for OD-02, OD-03, OD-05
and OD-08 are out of date. `DECISIONS.md` ("Semantic gaps") gives the correct effects. Fix them together with the
first owner-confirmed OD, because any edit changes the policy hash.

## 6. Operations decisions

### DEC-10 The test suite inside the live window: settled (D-28)

- **Settled on 2026-11-10** by the owner's requirement: data gathering, scheduling and the dashboard ready within
  20 minutes. The suites alone take about half of that.
- **Built in `.gitlab-ci.yml`.**
  - `unit_and_integration`, `ui` and `fixture_pipeline_rehearsal` run on merge requests and on the default branch.
  - A live run does not repeat them. `acquire_admit` refuses a ref that is not protected.
  - `import_and_lint`, the preflights, `native_wheel` and the staged-schema check stay in every run.
  - `FOCUS_LIVE_VERIFY=full` puts the suites back in every live run.
- **Your part.** Protect the default branch, require a passed pipeline to merge, and run the live schedules on that
  branch. Cache the native build, or fetch the wheel built on merge, so it does not cost minutes in every run.
- **Measure** on every run: `pipeline --status`, section `window` (minutes used, per step, late or not).

### DEC-11 Signing before each shift

- **Authority:** OWNER. It is a governance decision.
- **Why.** `sign_accept` is `when: manual`. A plan accepted after its shift starts is only a proposal for that shift,
  and teams are scored against the stale plan (DEC-24).
- **Options:**
  - (a) manual promotion, staffed three times a day, with an alert at T−5 minutes;
  - (b) automatic promotion in the protected `focus-signer` environment, only when the FINAL and READBACK validations
    are clean and the pipeline runs from a scheduled, protected ref;
  - (c) automatic for EXECUTABLE, manual for FORECAST.
- **Recommendation: (b), or (c) if the owner wants a human on the delivery forecast.** Keep a kill switch
  (`FOCUS_AUTO_SIGN=false` falls back to manual).
- **Build now.** Add the rules with the switch off. The test above asserts `when: manual`; extend it to assert the
  manual fallback.
- **Measure.** From `tools/calibrate_scores.py store`: `stale_plans.share`, and the minutes from acceptance to shift
  start (pipeline receipts).
- **Decision evidence.** If manual signing gives any stale plan in a week of shadow, recommend automatic.

### DEC-12 Cutoff lead and schedule times

- **Authority:** DATA.
- **The owner's budget is 20 minutes** (D-28): from the data cut to the accepted plan and its dashboard data.
- **Default now.** Plan crons at 05:40, 14:10 and 22:40; score crons at 15:35, 00:05 and 07:05, site time
  (`HANDOFF.md` §5.2). Set each GitLab schedule's time zone to America/New_York.
- **Measure**, after one week of runs:
  - `window.minutes` and `window.step_seconds` from `pipeline --status`, for every run, staging included;
  - from `calibrate_scores.py store`: `boundary.share_in_last_lead_minutes` and `boundary.share_in_overtime`.
- **Decision rule.** Start each run as late as the 95th-percentile duration plus 3 minutes allows, and never more
  than 20 minutes before the shift. A run that cannot finish in 20 minutes is a defect to fix (DEC-01, DEC-08,
  DEC-30), not a reason to start earlier: tell the owner before changing the start.
- **Implement in:** the GitLab schedule crons. `planning.cutoff_lead_minutes` (60) is a different parameter, the
  auto-mode look-ahead; leave it unless the lead goes past 45 minutes.
- **Record:** a D-entry with the measured 95th percentile.

### DEC-13 Freshness limits (live load cadences)

- **Authority:** DATA.
- **Default now.** `admission.max_source_age_hours` keeps its fixture limits (18–240 h, `fixture_default`), and
  `admission.max_capture_skew_minutes` stays at 30.
- **Measure.** The load interval of each dataset over two weeks (`stage.json` receipts).
- **Decision rule.** Limit = 2 × the 95th-percentile load interval, rounded up to whole hours, and never below 6 h for
  a dataset loaded before every shift. Keep 30 minutes of capture skew unless acquisition itself takes longer.
- **Implement in:** `config/policies/rebuild-v1.json`, as one policy batch, with the evidence in `ref`.
- **Verify:** `tests/integration/test_source_freshness.py`.

### DEC-28 Calendar days after the last control station

- **Authority:** DATA for what the sources hold; OWNER for which calendar applies after roll-out.
- **Why.** FOCUS takes each line's working days only from `cday_op_plan` and never adds a day
  (`calendar.admit_line_calendars`). If a line's calendar stops at its last control station, work that is still
  open after that date cannot be placed. The aircraft then has no finish date and no cost
  (`OBJECTIVE_EXPLAINED.md` §4), and these are the aircraft that are already late.
- **Measure.** For each line: the calendar's last date, the CS744 planned unload date, and the open work minutes
  after the calendar's last date.
- **Recommendation.** The calendar producer sends working days for every line through the planning horizon, using
  the site's working-day calendar after the line's last control-station date. It marks those rows so they can be
  told apart, and keeps one active load per line.
- **Ask the owner:** which calendar applies to an aircraft after roll-out (the line's, the site's or the delivery
  center's), and how far the horizon runs.
- **Do not:** extend the calendar inside FOCUS, or let late work finish "on the last day".

### DEC-29 Make the cost objective steer on live data

- **Authority:** ENGINEERING and DATA.
- **Why.** The scheduler compares late-delivery cost only over aircraft whose finish date it can work out. With
  every aircraft unknown, every candidate ties on cost and the plan is chosen on travel, waiting and overtime. The
  run still finishes and publishes, so nothing fails loudly.
- **Do, on every run:**
  1. record `objective` from the pipeline status, for the executable plan and the forecast: aircraft with a rate, a
     due date and a finish date, and the unplanned jobs by reason (`OBJECTIVE_EXPLAINED.md` §4);
  2. work the largest reason first: rates (OD-12), due dates, durations (OD-02), control-station windows (OD-05),
     dependency loops, crew sizes (OD-01) and calendar coverage (DEC-28);
  3. report it beside the run time (`window` in the same status).
- **Decision rule.** The forecast plan is ready for the owner when no aircraft in scope has an unknown cost, or each
  remaining one is listed with an accepted reason. Speed work does not come before this.
- **Do not** substitute a rate or a duration to make a cost known (guardrail "Unknown stays unknown").

### DEC-30 Completed jobs in the planning snapshot

- **Authority:** ENGINEERING.
- **Why.** A live run can carry far more completed jobs than open ones, and trimming them looks like the quickest
  way to a faster pipeline.
- **What reads them.** The plan uses completed jobs to know which predecessors are already satisfied. The
  performance side uses the same graph to find out-of-sequence starts and to trace a missed job to the job that
  blocked it. Scores themselves read the observation feeds, not the snapshot.
- **Recommendation: keep them, and fix the writes first.** On the synthetic 50-aircraft fleet, which carries its
  completed jobs, one simulated shift (load, plan and store, on a local database) takes 3.6 to 5.2 minutes. Time
  beyond that in a live run is usually row-by-row database writes and work that does not belong in the window:
  - write display parts, snapshots and statistics in batches (DEC-01);
  - run the test suite and the native build on merge, not before each shift (DEC-10);
  - start staging at the data cut, beside the verify jobs (DEC-08).
- **Decision rule.** Reconsider trimming only if the window is still missed after those three, and only with a test
  that a reopened or voided completion still yields the same out-of-sequence findings.

## 7. Scoring decisions (after at least 4 weeks of pilot scores)

**Prerequisites:**

- The teams being measured are dispatched from FOCUS's accepted plan (a pilot). Scores from pure shadow mode, where
  crews follow another schedule, say how closely FOCUS's plan matched the floor; they do not measure the teams.
- DEC-01, DEC-06 and OD-15 are done, so scores exist live.
- OD-17 is done, so claims can be decided.
- At least about 1,000 scored team-shifts.

The tool for all of these is `tools/calibrate_scores.py`, and `docs/task-valuation-investigation/10_CALIBRATION.md`
§8 is the full procedure. Keep its outputs in company storage only.

```bash
python tools/calibrate_scores.py store --from <start> --to <end> --out calibration.json --review-sample 60 --review-csv review.csv
```

### DEC-19 Validate the score on real data before any grade counts

- **Authority:** DATA for the measurements; OWNER for what follows from them.
- **Why.** Real data is a poor way to pick the weights: they state what the owner wants to reward, a fit on tens of
  teams is mostly noise, and the split moves scores very little (`10_CALIBRATION.md` §8). Real data can show
  whether the score behaves sensibly. The simulation cannot, because its floor is synthetic.
- **Measure** (`10_CALIBRATION.md` §8, "What real data can and cannot decide"):
  - spread: the shortfall percentiles and the grades under each band set;
  - noise: does a team's score one week predict its score the next;
  - fairness: do shift number, team size or work mix move the score for reasons the team does not control;
  - agreement: do supervisors, shown a blind sample of the highest- and lowest-scoring shifts, see the same thing;
  - outcomes: do higher-scoring team-weeks have better flow (fewer blocked successors, less rework);
  - sensitivity: do rankings change between 60/40, 70/30 and 80/20.
- **Decision rules** (rules of thumb, for the owner to confirm):
  - rankings nearly identical across the weights (rank correlation 0.95 or more): keep the defaults;
  - week-to-week correlation below about 0.3: single shifts are mostly noise, so grade weeks, not shifts;
  - supervisors disagree on more than about a quarter of the sample: find the cause first. It is usually the
    plan, the standard minutes or missing excusals, not the weights.
- **Tooling.** `tools/calibrate_scores.py` reports spread, flips and the w grid today. The noise, by-shift fairness,
  blind sample, outcome and 60/40–80/20 checks are not built yet.
- **Record:** a D-entry with the measurements (summary only).

### DEC-20 Swap watch: unplanned work done in place of planned work

- **Authority:** DATA.
- **Why.** The owner's rule credits every unplanned job at 80% (D-27). A team could therefore do easier unplanned
  work in place of planned work. It gives up only the 20% adherence share, so the swap never pays when standard
  minutes are fair, and can pay when the unplanned job's standard is generous. The owner accepted that risk and needs
  to see whether it happens.
- **Measure.** `tools/calibrate_scores.py` reports, in `offplan` and `swap_watch`: the unplanned minutes done beside
  planned work that was missed with no reason on record ("possible swap" minutes), the team-shifts involved, and the
  points those team-shifts gave up against doing the missed planned work instead. To check the watch itself:
  1. supervisors label `review.csv` (each row `SWAP` or `EXTRA`);
  2. run `python tools/calibrate_scores.py store --from <start> --to <end> --labels review.csv`;
  3. read the miss rate and the false-alarm rate.
- **Decision rule:**
  - possible-swap minutes under about 5% of unplanned minutes: nothing to do;
  - above that, and supervisors confirm teams are choosing other work over ready planned work: tell the owner. The
    options are a lower rate (DEC-21), or crediting no extra work beside a planned job missed with no reason on
    record. The second was built first and removed on the owner's example, so it needs the owner again.
- **Record:** a D-entry with the shares and the sample size (summary only).

### DEC-21 The rate for extra work: decided

- **Decided by the owner on 2026-11-10: 80%.** `score.attainment_weight` = 0.80 and `score.adherence_weight` = 0.20
  in `config/policies/grading-demo-v1.json`, labeled `owner_decision`.
- **What the rate does.** It is what an extra minute earns against a planned minute. The rest (20%) is earned only
  by planned jobs, and is what a team gives up when it does other work in place of a planned job.
- **Revisit only** on evidence from DEC-20, and only with the owner. A change is one policy batch at a period
  boundary, because the policy hash changes.

### DEC-22 Extra-work credit: decided and built (D-27)

- **Decided by the owner on 2026-11-10.** Unplanned work must earn credit, below the same planned work, and must
  never make a team prefer idling to working ahead. Do not reopen it.
- **The rule:** `docs/execution/SCORING_EXPLAINED.md` §1 in plain terms; `docs/execution/DECISIONS.md` D-27 in full.
  - score = 100 × (0.8 × min(1, (Cp + Uc)/D) + 0.2 × min(1, Cp/D)) − 100 × O/D;
  - Uc, the credited extra minutes, is every unplanned job the team got done; unplanned work started out of
    sequence by choice earns none;
  - a missed planned job costs points on its own unless it is excused.
- **What to do in a port.**
  - Take `focus/performance/score.py`, `lifecycle.py` (`unit_facts`, `_offplan`), `disruption.py`
    (`explained_units`) and `rollup.py` as they are. Keep `Uc` in every stored evaluation and team-shift row.
  - Run `tests/unit/test_score_facts.py` and `tests/unit/test_scoring_explained.py` against the port.
  - Never compute a score anywhere else, and never in SQL or the browser.
- **Still open:** the bands (DEC-23) and approved additions (DEC-26). DEC-20 watches for swaps.

### DEC-23 Grade bands

- **Authority:** OWNER (with data).
- **Why.** Without extra work, the score is 100 × (1 − unexcused shortfall) − the out-of-sequence charge, so a band
  is a tolerance on planned work left undone without an excuse. The synthetic floor is too forgiving to set them.
  Since D-27 no team gets points for adherence alone, so the demo bands are stricter than they were: 80% of the
  expected work done scores 80, where the old formula gave 86.
- **Default:** the demo bands 90/80/70/60 under DEMONSTRATION. Starting proposal: A ≤ 2% undone, B ≤ 5%, C ≤ 10%,
  D ≤ 20%, which gives bands 98/95/90/80.
- **Measure:** `shortfall.percentiles`, `grades` (with `--bands 98,95,90,80` and other candidate sets), the flip
  rates, and `daily_pooled_R5_w0.8`. Exclude stale-plan shifts first (DEC-24).
- **Decision rule:**
  - pick the tolerance set whose flip rate between a team's consecutive shifts is at most 5%;
  - check that a supervisor review of a sample of C, D and F team-shifts finds real problems in most of them;
  - tie recognition (badges, rankings) to pooled days or weeks, not single shifts.
- **Implement in:** `score.bands` in `config/policies/grading-demo-v1.json` (one policy batch, at a period
  boundary).

### DEC-24 Late-plan protection

- **Authority:** OWNER.
- **Why.** A plan accepted after its shift starts is only a proposal; the shift is scored against the previous plan
  while crews work the new one. On the synthetic floor this doubled the 95th-percentile shortfall and raised
  cross-team relief 25–28 times (`10_CALIBRATION.md` §7).
- **Options:**
  - (a) status quo;
  - (b) automatically file `PLAN_CHURN` claims for the frozen units the late plan moved out of the shift. These are
    evidence on the separate review track (`disruption.separate_review`); they become relief only by reviewed
    decision;
  - (c) score stale-plan shifts NEUTRAL;
  - (d) score against the late plan if it was accepted within N minutes of the start.
- **Recommendation: prevent first (DEC-11), then (b).** Option (b) changes no score by itself and keeps a human
  reviewer.
- **Build (b) now**, behind `FOCUS_AUTO_PLAN_CHURN=false`.
- **Measure:** `stale_plans.share` and examples.
- **Decision rule.** If the share is about 0 after DEC-11, (b) is a safety net; if above 1%, ask the owner about (c)
  or (d).

### DEC-25 Overtime credit

- **Authority:** OWNER (with data).
- **Why.** `select_goal` (`focus/performance/lifecycle.py`) checks shift windows before overtime windows. A job
  finished in shift k's authorized overtime, which shift k+1's plan also holds, is credited to shift k+1. Shift k's
  unit is missed (`COMPLETION_CREDITED_ELSEWHERE_OR_LATE`), though shift k's crew did the work.
- **Options:**
  - (a) keep it;
  - (b) credit the shift whose crew completed it, from the completing person's team-shift on the completion record.
    The later goal's unit then gets a relief status such as `DONE_IN_PREVIOUS_OVERTIME`, counted in E like
    `DONE_BEFORE_SHIFT`.
- **Measure.** How many such units there are and their share of goal minutes. Count EARNED units whose completion
  falls in the previous shift's overtime window and whose phase was also in the previous goal; extend
  `calibrate_scores.py` if needed.
- **Decision rule.** Under 1% of goal minutes: recommend (a) and document. Otherwise recommend (b).
- **Implement (b) in:** `select_goal` and `unit_facts`, plus the simulation and reference views, with tests mirroring
  the `DONE_BEFORE_SHIFT` ones.

### DEC-26 Approved additions and extra-work credit

- **Authority:** OWNER.
- **Why.** An approved emergency addition raises what is expected (B). Since D-27, a team that is behind can score
  lower for an approved extra job than for the same job done unapproved. Example: expected 2,100, done 1,800, one
  300-minute extra job. Approved: 2,100 ÷ 2,400 = 87.5. Unapproved and credited: 0.8 × 100% + 0.2 × 85.7% = 97.1.
  Approval must never score worse than not asking (report `09` §8).
- **Options:**
  - (a) keep additions as obligations (today): right for emergency work the team must absorb, wrong as a route to
    credit for extra work;
  - (b) an approved addition that gets done counts as planned work done without raising what is expected, and one
    that is not done counts as a miss;
  - (c) two kinds of addition, told apart by the amendment's basis: emergency obligations as (a), approved
    pull-ahead as (b).
- **Recommendation:** (b). It keeps one kind of addition, rewards asking first, and never scores below unapproved
  work.
- **Until decided:** additions stay obligations. No live amendment feed exists yet (OD-15), so nothing is affected.

### DEC-31 Should criticality weight a team's score

- **Authority:** OWNER, with data.
- **What exists** (D-29). Criticality is defined from the cost objective: the modeled cost of one more day late for
  the job's aircraft in the accepted forecast, as a share of the fleet's highest. Every pipeline run reports it
  (`criticality` in the status). No score reads it.
- **The question.** Should a planned job on a more critical aircraft be worth more points than the same minutes on
  a less critical one, so that a team is graded on points earned over points expected?
- **Options:**
  - (a) reported only: leads and managers see each aircraft's criticality; scores stay in standard minutes;
  - (b) weighted: a job's points are its standard minutes times (1 + k x criticality), with k set by the owner
    (k = 1 counts a job on the most critical aircraft double). Extra work keeps the 80% rate on its points. The
    weights are those of the forecast accepted for the shift, frozen with it; a job on an aircraft whose criticality
    is unknown counts its plain minutes;
  - (c) reported now, weighted later if the pilot shows it changes what teams protect.
- **What is measured** (D-29; `evidence/outputs/AW50.json`). On the stored 50-aircraft simulation, k = 1 moves a
  team's attainment by 0.05 points on average and by a point or more in 0.5% of team-shifts; k = 3 by 0.08 and
  0.9%. The order of teams does not change. So weighting would not make scores unfair, and it would not tell teams
  apart either. Its use is as a signal: when a shift goes wrong, protect the work on the aircraft that costs most.
- **Decide from data.** Run `aircraft_weighted.py`'s comparison on four weeks of real job records and accepted
  forecasts. Weight the score only if both hold:
  1. the weights differ inside a typical team-shift (the simulation's spread was 0.15 on a 0 to 1 scale), and
  2. teams that missed work on critical aircraft are the ones whose scores fall, when a supervisor reviews a blind
     sample.
- **Recommendation:** (c). Show it first. A weighted score is harder to explain to a team, and the measurements so
  far say it changes almost nothing.
- **Do not:** value single jobs in dollars and add them up (the values overlap, D-29), or assign criticality by
  hand or by job type.

### OD-16 The OFFICIAL score policy (last)

- **Authority:** OWNER only. Never self-approve.
- **When.** After DEC-20 to DEC-25 are settled and at least two weeks of pilot scores look right to supervisors.
- **What the owner provides.** An approval reference, which goes in `score.policy_approval_ref`, with
  `score.policy_status = OFFICIAL`. The decision on personal grades is separate and needs its own policy;
  `score.personal_grades_enabled` stays false until then.

## 8. Templates

**Decision record (append to `docs/execution/DECISIONS.md`):**

```markdown
- **D-NN <title> (<date>, <authority: engineering | data | owner>).**
  - **Decision.** <one sentence>.
  - **Evidence.** <what was measured, when, sample size; company values stay in company storage>.
  - **Rule applied.** <the playbook rule, DEC-xx>.
  - **Changes.** <files, policy keys (old -> new), migration version>.
  - **Tests.** <updated or added tests; suite result>.
  - **Revisit when.** <condition>.
```

**Policy decision (an OD item, after the owner confirms), in `config/policies/rebuild-v1.json`:**

```json
{"id": "OD-05", "topic": "CS offsets, reference anchors and date basis",
 "decision": "<the confirmed meaning>", "decided_by": "owner", "decided_on": "<YYYY-MM-DD>", "policy_key": "cs.date_mode"}
```

Set the key's `authority` to `owner_decision` and its `ref` to the evidence. Remove the item from `open_decisions`.
Update `tests/unit/test_policy_config.py`. Apply at a period boundary.

**Owner memo (for OWNER items; keep it to one page):**

```markdown
**Decision needed:** <DEC-xx title>
**Recommendation:** <option>, because <one or two sentences>.
**Evidence:** <the key numbers, with the period and sample size>.
**Other options:** <one line each, with their cost>.
**Built and switched off:** <switch name>; turning it on changes <what>.
**If no answer by <date>:** <the safe default that stays in place>.
```

## 9. Suggested order

1. **Now (no data):** DEC-00 first, then DEC-01 to DEC-09; build DEC-11 and DEC-24 behind switches that are off.
   DEC-10 and DEC-22 are settled and built: port them.
2. **Data wired, read-only:**
   - staging (DEC-08) and the OD profiling (§5), with OD-05 first, then OD-10, OD-13, OD-03, OD-07, OD-08, OD-09 and
     OD-04;
   - a FORECAST plan for the complete scope, with the unknown-cost counts of DEC-29 and the calendar check of
     DEC-28;
   - review the readiness gaps with their owners.
3. **First week of pipeline runs:** DEC-12; the evidence for DEC-11; DEC-24's measurement.
4. **Four weeks of pilot scores** (teams dispatched from the accepted plan): DEC-13, DEC-19, DEC-20, DEC-25 and
   DEC-23. Ask the owner about DEC-26 before the pilot starts. DEC-31 is decided on the same pilot data.
5. **Go-live gate:** the `HANDOFF.md` §8 checklist; the owner confirms the OD relabels, DEC-11, DEC-23 to DEC-26
   and, last, OD-16.
