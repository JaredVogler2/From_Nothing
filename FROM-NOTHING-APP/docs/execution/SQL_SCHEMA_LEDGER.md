# SQL schema ledger (P03)

The source contracts are embedded verbatim from manual S04 in
`focus/data/discovery.py` (`S04_CONTRACTS_TEXT`). They are parsed into 28
typed table contracts and exported to `fixtures/source_contracts/s04_contracts.json`
(contracts SHA-256 `6ff301d3dbb5440eafe523c03d8b21610f9bbe8650d91793ac2d9b6cd0086ab5`).

| Item | Status |
| --- | --- |
| Authoring verification | SQL Server MCP read-only on 2026-10-27, `SQL-D-22D10090` / `SHARED`, driver pymssql. 28 core contracts, 8 economics FKs (S04). |
| This build environment | **Not probed live.** The cloud build container has no network path or credentials to `SQL-D-22D10090`. `python -m focus.cli source-discover --read-only --mode live` reports `not_probed` until credentials exist. |
| Drift method | The S05 schema reference query (`queries/discovery/schema_reference.sql`) is compared column by column with the S04 contracts (type, declared length or NVARCHAR characters, precision/scale, datetime2 scale, nullability, computed). Every mismatch is a `SCHEMA_DRIFT` / `COLUMN_MISSING` / `COLUMN_UNEXPECTED` / `OBJECT_MISSING` finding naming the object and column (`tests/integration/test_source_scope.py`). |
| Proposed prefix collision check | `queries/discovery/fn_collisions.sql` lists `FOCUS_FN_%` / `FOCUS_REBUILD%` objects. The manual observed none on 2026-10-27; this must be re-run before any migration. |

## Tables and reader projections

Each reader uses an explicit S05 projection (`focus/data/datasets.py`,
generated SQL in `queries/source_inputs/*.sql`, exported to
`fixtures/source_contracts/dataset_projections.json`). There is no
`SELECT *`.

| Dataset | Object | Projection notes |
| --- | --- | --- |
| master_task_list | FOCUS_MASTER_TASK_LIST | Broad WIP/anchor query: all statuses, including COMPLETE anchors, on lines with any non-COMPLETE work; no line cap |
| pnet_dag | FOCUS_PNET_DAG | Model-template edges; raw type/hard flag/signed offset preserved |
| neo_graph_rework | NEO_GRAPH_REWORK | Same-line parent/child rework links |
| flow_budget | FOCUS_FLOW_BUDGET | Keyed by JOB_NUMBER+LINE_NUMBER; TOTAL/PRIMARY/ASSIST/labor fields kept |
| buyoff_plan, inspection_counts, inspection_standards | FOCUS_BUYOFF_PLAN / FOCUS_INSPECTION_COUNTS / FOCUS_INSPECTION_STANDARDS | Counts never locate IP holds |
| cday_op_plan | FOCUS_CDAY_OP_PLAN | `WHERE IS_ACTIVE=1`; generation and dense bounds admitted per line |
| cs_windows | FOCUS_CS_WINDOWS | `WHERE IS_ACTIVE=1`; mandatory same-run dataset |
| etracs_active, etracs_history | FOCUS_ETRACS_ACTIVE / _HISTORY | Null ECD and blank USED_ON retained as gaps |
| actual_labor, labor_shift | FOCUS_ACTUAL_LABOR / FOCUS_LABOR_SHIFT | Aggregate person effort (labeled), not elapsed |
| soi_cert_requirements, cert_course_map | NEO_SOI_CERT_REQUIREMENTS / NEO_CERT_COURSE_MAP | Positive-only rows; no names from the eligibility matrix |
| mechanic_certs | FOCUS_MECHANIC_CERTS | varchar cert dates parsed strictly |
| mech_team_inferred, dept_team_skill_ref | FOCUS_MECH_TEAM_INFERRED / FOCUS_DEPT_TEAM_SKILL_REF | Legacy evidence only |
| person_team_map, attendance_shift | FOCUS_PERSON_TEAM_MAP / FOCUS_ATTENDANCE_SHIFT | Empty live on 2026-10-27, so executable crews are unready |
| econ_* | FOCUS_SOL_TESTING2_* (7 tables) | Exact decimal(19,4) to Fraction cents |

`FOCUS_CERT_ELIGIBILITY` (dataset `cert_eligibility_matrix`, P06) is read only as a
display projection. The projection excludes `MECHANIC` names, descriptions and missing-cert
text. Its ELIGIBLE/PCT/SUBSTITUTION labels grant nothing: the snapshot reports each label next
to the authoritative decision as a `CERT_MATRIX_PROJECTION_ONLY` note (S05, S07).

## Supplemental typed inputs (proposed FOCUS_FN producers or fixture-only)

| Dataset | Live producer | Live status |
| --- | --- | --- |
| team_inference | FOCUS_FN_TeamInference (S06/S07 SQL) | Not installed; `queries/workforce/read_team_inference.sql` ready |
| clock_observations | FOCUS_FN_ClockObservation | Not installed (no raw interval feed in S04) |
| duration_episodes | FOCUS_FN_DurationObservation | Not installed |
| scope_receipt, shift_roster, availability_exceptions, ot_authorizations, requirement_census, requirement_alternatives, holding_events, rework_classification, inspection_holds, work_progress, part_events, carry_in, people_directory | none yet: each needs a landing table and a registered live query (`LIVE_SUPPLEMENTAL_QUERIES`) | `NOT_PROBED`: gap `DATASET_NOT_PROBED`, plus the open-decision gap where one applies (`docs/data/`) |

The `FOCUS_FN_*` tables are the versioned migrations 001-010 in `sql/migrations/`. **None is installed**; this build
never executed them against a server.

## Drafted SQL authorities (written, not installed)

P23 consolidated these into the versioned set:

| File | Content |
| --- | --- |
| `001_focus_fn_core.sql` | S06 core, verbatim |
| `002_worker_pipeline.sql` | New in P23: WorkerRun role, attempts and lease checks, queue index; PipelineRun; PipelineStep; append-only Outbox (`TR_FN_Outbox_AppendOnly`) |
| `003_source_promotion.sql` | P04 draft: `BeginSourceRun`, `CompleteSourceRun`, `RejectSourceRun`, `PromoteSourceHead` |
| `004_workforce_inference.sql` | Workforce inference |
| `005_duration_statistics.sql` | Duration statistics |
| `006_commitments.sql` | Delivery commitments |
| `007_publication.sql` | Publication |
| `008_lifecycle.sql` | Lifecycle |
| `009_evaluation.sql` | Evaluation |
| `010_performance_rollup.sql` | Light per-team-shift performance rows and the compact job record (D-25) |

- Every version has a generated rollback in `sql/migrations/rollback/`.
- Roles and grants are in `sql/grants/focus_fn_roles.sql`.
- The checksum ledger, lint, plan, apply and rollback protocol is `focus/migrations.py`.
- Install steps are in `docs/execution/INSTALL_AND_PIPELINE.md`.

001 is S06 verbatim; 002 and 010 are new; the statements of 003-009 are unchanged from the reviewed P04-P18 drafts below.

| Artifact | Stage | Purpose |
| --- | --- | --- |
| `queries/workforce/infer_mechanic_teams.sql` | P05 | S07 plurality inference into FOCUS_FN_TeamInference (run-isolated, CAPTURING only) |
| `sql/migrations/004_workforce_inference.sql` | P05 | TeamInference constraints, clock indexes, sealed-run trigger, head view, retention |
| `queries/qualifications/requirement_census.sql`, `holding_validity_profile.sql` | P06 | Read-only readiness: unknown-requirement census, holding date-encoding/status profile |
| `queries/durations/build_observations.sql` | P07 | Episode metrics (job/owner unions, cycle) into FOCUS_FN_DurationObservation |
| `queries/durations/percentiles.sql` | P07 | PERCENTILE_CONT P50/P55/P75/P90 + mean into FOCUS_FN_DurationStatistics per policy |
| `sql/migrations/005_duration_statistics.sql` | P07 | Duration constraints, UX(RunId, GroupKeySha, PolicyId), indexes, sealed triggers, head view, retention |
| `queries/calendars/*.sql`, `queries/graph/*.sql`, `queries/parts/shortage_profile.sql`, `queries/quality/inspection_profile.sql` | P08-P10 | Read-only readiness: calendar generations and CS grain, template edges and rework linkage, shortages, inspection counts |
| `queries/economics/effective_terms.sql`, `liability_floor.sql` | P12 | Read-only mirror of the term resolution (one current mapping, one active scenario, one assignment, selector precedence, version conflicts, authority) and the latest checkpoint floor |
| `sql/migrations/006_commitments.sql` | P14 | FOCUS_FN_Commitment checks; CommitmentObservation (replay key per accepted plan), DeliveryReceipt, CommitmentResult projection; immutability triggers; IncumbentAtHead view; one-transaction RecordDeliveryObservation with an optimistic version check; state readback view |
| `sql/migrations/007_publication.sql` | P16 | PlanStage/Validation/PublishedPlan/PlanHead checks; append-only triggers (stages, validations, published plans, frozen goals); StagePlan (idempotent, sha-checked), ReadPlanStage, RecordValidation; AcceptPlan (applock, exact staged bytes, clean READBACK receipt, goals, head LAST with expected version); CurrentPlan view |
| `queries/actuals/*.sql` | P17 | Read-only feed readers (lifecycle events, actual intervals, defect evidence, amendments, claims, decisions, producer receipts) with the exact `FEEDS` projections and one as-of parameter. `attendance_evidence` and `holding_evidence` (B9) have no query, landing table, receipt value or history table yet: fixture only (`QUERY_MISSING` live) |
| `sql/migrations/008_lifecycle.sql` | P17 | Producer landing tables and receipts; FOCUS_FN_Event clock checks; typed append-only ActualInterval, DefectEvidence, GoalAmendment, DisruptionClaim (cause/status checks, notes rule), SequenceViolation, ObservationRun/FeedReceipt/Quarantine, IEReview; ExcusalDecision track/exception columns (and Event Feed/Origin/IngestedAtUtc, ExcusalDecision ScopeKey/AuthorityRole/IngestedAtUtc); immutability triggers on Event, ExcusalDecision, ActualInterval, DisruptionClaim, SequenceViolation and GoalAmendment, while the other history tables are append-only by grant (INSERT only); RecordAmendment (zero-minute goal row for B0 = 0) |
| `sql/migrations/009_evaluation.sql` | P18 | Evaluation series columns and null-score checks; EvaluationEvidence; EvaluationHead (optimistic version); RecognitionEvent and BadgeAward chains (dedupe, positive values except corrections); immutability triggers; AppendEvaluation (applock, unchanged evidence is a no-op); CurrentEvaluation and TeamXp views |
| `sql/migrations/010_performance_rollup.sql` | D-25 | TeamShiftPerformance (one row per scope, team and shift: exact components payload, display decimals, superintendent fixed at first write, current evaluation; day and superintendent indexes); JobOutcome (append-only by grant, keyed by evaluation and sequence: shop order id or job key, aircraft, phase, minutes, outcome check including `no_plan`, out of sequence, causes, origin; day and shop-order indexes); JobName (one description per shop order id). Nothing computes a score in SQL |

All of them are consolidated into the versioned migration set (P23). Installing them requires an authorized
installer session (`migrate --apply --catalog live --installer ... --confirm-manifest ...`). None was executed against
a server by this build.

## Objects by migration (inventory, 2026-11-02)

The migration lint finds versions 1–10 and no findings. They create 49 tables, 7 views, 13 procedures and 20 triggers.
Role abbreviations: AR AppReader, AC AppCommand, SW SourceWorker, ST Stager, SG Signer, EV Evaluator, OP
ObservationProducer, PW PipelineWorker, RT Retention.

| Object | Kind | Migration | Written by |
| --- | --- | --- | --- |
| SchemaVersion | table | 001 | installer (migration runner) |
| Policy | table | 001 | no role granted |
| SourceRun; SourceDataset, SourceRow; SourceHead | table | 001 | SW via Begin/Complete/RejectSourceRun; SW; SW via PromoteSourceHead |
| ClockObservation; TeamInference, DurationObservation, DurationStatistics | table | 001 | SW (RT purges the last three) |
| PlanStage, Validation; PublishedPlan, PlanHead | table | 001 | ST via StagePlan / RecordValidation; SG via AcceptPlan |
| FrozenGoal | table | 001 | SG via AcceptPlan; EV via RecordAmendment |
| Commitment | table | 001 | EV via RecordDeliveryObservation (the code records commitments on the signer path; see below) |
| Event, ExcusalDecision; Evaluation | table | 001 | EV; EV via AppendEvaluation |
| WorkerRun | table | 001 | AC, PW |
| PipelineRun, PipelineStep, Outbox | table | 002 | PW |
| CommitmentObservation; DeliveryReceipt, CommitmentResult | table | 006 | EV via RecordDeliveryObservation; **no writer** |
| ObservationProducerReceipt and the six landing tables (LifecycleSource, ActualIntervalSource, DefectEvidenceSource, GoalAmendmentSource, DisruptionClaimSource, ExcusalDecisionSource) | table | 008 | OP |
| ActualInterval, DefectEvidence, DisruptionClaim, SequenceViolation, IEReview, ObservationRun, ObservationFeedReceipt, ObservationQuarantine; GoalAmendment | table | 008 | EV; EV via RecordAmendment |
| EvaluationEvidence, EvaluationHead; RecognitionEvent, BadgeAward | table | 009 | EV via AppendEvaluation; EV |
| TeamShiftPerformance, JobName; JobOutcome | table | 010 | EV (INSERT/UPDATE); EV (INSERT) |
| BeginSourceRun, CompleteSourceRun, RejectSourceRun, PromoteSourceHead | procedure | 003 | executed by SW |
| PurgeTeamInference; PurgeDurationEvidence | procedure | 004; 005 | executed by RT |
| RecordDeliveryObservation; RecordAmendment; AppendEvaluation | procedure | 006; 008; 009 | executed by EV |
| StagePlan, ReadPlanStage, RecordValidation; AcceptPlan | procedure | 007 | executed by ST; by SG |
| TeamInferenceAtHead; DurationStatisticsAtHead; DeliveryCommitmentState; IncumbentAtHead; CurrentPlan; CurrentEvaluation, TeamXp | view | 004; 005; 006; 006; 007; 009 | read by AR |
| 20 `TR_FN_*` triggers (1 in 002, 1 in 004, 2 in 005, 2 in 006, 4 in 007, 6 in 008, 4 in 009) | trigger | 002–009 | – |

`focus/data/sql.py::FN_OBJECTS`, the reader's allowlist, is out of date: it names `FOCUS_FN_Artifact` and
`FOCUS_FN_State`, which no migration creates, and lacks 29 created objects (PipelineRun, PipelineStep, Outbox,
CommitmentObservation, DeliveryReceipt, CommitmentResult, the nine 008 history tables, EvaluationEvidence,
EvaluationHead, RecognitionEvent, BadgeAward, the three 010 tables and all seven views). The live adapter must update
it, or the reader refuses the views AR is granted (`OBJECT_NOT_ALLOWLISTED`).

## Runtime collections with no FOCUS_FN table

The repository protocol stores these collections today (fixture store); no migration gives them a table, so the live
adapter needs a new migration for them:

- candidates, snapshots, org_charts;
- display_parts, display_manifests, display_receipts, candidate_digests, display_retention (and its runs);
- detail_retention (and its runs);
- attendance_evidence, holding_evidence;
- repair_requests, explanations, scenario_snapshots, scenario_results;
- the `observation/<scope>` head.

Partial fits:

- `validations`: the app role's validate step writes FINAL receipts before any stage exists, but `Validation.StageId`
  is a NOT NULL foreign key and only ST may call `RecordValidation`;
- `team_shift_performance` and `job_outcomes`: the store keeps one versioned document per production day; SQL has one
  row per team-shift or job;
- `accepted_plans`: maps only to the `IncumbentAtHead` view;
- `run_cancel_requests`: would become a WorkerRun update.

## Known schema issues for the live adapter

- **Identifiers.** Code ids are prefixed hex digests (`GOAL…`, `EVL…`), but `GoalId` and `EvaluationId` are
  `uniqueidentifier` columns (001, 010). The adapter needs a mapping or the columns a different type.
- **Quarantine key.** `ObservationQuarantine`'s primary key is (run, feed, source key, code); the code's quarantine
  key leaves out the revision, so two revisions of one `SOURCE_EVENT_KEY` failing with the same code collide.
- **Receipt feeds.** The `ObservationProducerReceipt` feed CHECK lists six feeds, not eight.
- **Grants.** Worker roles hold no SELECT; the signer path needs the stager's procedures and
  `RecordDeliveryObservation`; no role can UPDATE `Outbox.DeliveredAtUtc` or advance a producer receipt watermark;
  `DeliveryReceipt` and `CommitmentResult` have no writer (`INSTALL_AND_PIPELINE.md` §2).
- **Deletion.** `TR_FN_EvaluationEvidence_Immutable` blocks every DELETE (no `focus_retention` bypass), no purge
  procedure exists for evaluation evidence, and display parts have no table, so the D-24 and D-25 retention commands
  need a new migration, not only the adapter.
- **Error numbers.** `TR_FN_Outbox_AppendOnly` (002) and `TR_FN_Event_Immutable` (008) both throw error 51700.
- **Comments.** The comments in 009 ("EARNED/MISSED/UNKNOWN/PENDING units") and 010 (job causes) predate D-26
  (`DONE_BEFORE_SHIFT`); no constraint rejects the new status, which lives in the evidence payload. Editing a
  comment changes the file's checksum, so fix them with the next migration change, before any install.

