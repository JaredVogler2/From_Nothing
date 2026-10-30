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
| scope_receipt, shift_roster, availability_exceptions, ot_authorizations, requirement_census, requirement_alternatives, holding_events, rework_classification, inspection_holds, work_progress, part_events, carry_in, people_directory | none yet | `NOT_PROBED`: precise readiness gaps (OD-01..OD-14) |

Proposed `FOCUS_FN_*` output tables (S06) are **not installed**. See
`sql/migrations/` (drafts, never executed by this build).

## Drafted SQL authorities (written, not installed)

P23 consolidated these into the versioned set:

| File | Content |
| --- | --- |
| `001_focus_fn_core.sql` | S06 core, verbatim |
| `002_worker_pipeline.sql` | New in P23 |
| `003_source_promotion.sql` | Source-run admission and head promotion |
| `004_workforce_inference.sql` | Workforce inference |
| `005_duration_statistics.sql` | Duration statistics |
| `006_commitments.sql` | Delivery commitments |
| `007_publication.sql` | Publication |
| `008_lifecycle.sql` | Lifecycle |
| `009_evaluation.sql` | Evaluation |

- Every version has a generated rollback in `sql/migrations/rollback/`.
- Roles and grants are in `sql/grants/focus_fn_roles.sql`.
- The checksum ledger, lint, plan, apply and rollback protocol is `focus/migrations.py`.
- Install steps are in `docs/execution/INSTALL_AND_PIPELINE.md`.

The statements of 003-009 are unchanged from the reviewed drafts below.

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
| `queries/actuals/*.sql` | P17 | Read-only feed readers (lifecycle events, actual intervals, defect evidence, amendments, claims, decisions, producer receipts) with the exact `FEEDS` projections and one as-of parameter |
| `sql/migrations/008_lifecycle.sql` | P17 | Producer landing tables and receipts; FOCUS_FN_Event clock checks; typed append-only ActualInterval, DefectEvidence, GoalAmendment, DisruptionClaim (cause/status checks, notes rule), SequenceViolation, ObservationRun/FeedReceipt/Quarantine, IEReview; ExcusalDecision track/exception columns; immutability triggers; RecordAmendment (zero-minute goal row for B0 = 0) |
| `sql/migrations/009_evaluation.sql` | P18 | Evaluation series columns and null-score checks; EvaluationEvidence; EvaluationHead (optimistic version); RecognitionEvent and BadgeAward chains (dedupe, positive values except corrections); immutability triggers; AppendEvaluation (applock, unchanged evidence is a no-op); CurrentEvaluation and TeamXp views |

All of them are consolidated into the versioned migration set (P23). Installing them requires an authorized
installer session (`migrate --apply --catalog live --installer ... --confirm-manifest ...`). None was executed against
a server by this build.
