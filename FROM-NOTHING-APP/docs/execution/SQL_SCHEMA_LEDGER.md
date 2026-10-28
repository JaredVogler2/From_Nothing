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

`FOCUS_CERT_ELIGIBILITY` is deliberately not read. It is a convenience matrix
with employee names, and its SUBST/percent labels grant nothing (S05, S07).

## Supplemental typed inputs (proposed FOCUS_FN producers or fixture-only)

| Dataset | Live producer | Live status |
| --- | --- | --- |
| team_inference | FOCUS_FN_TeamInference (S06/S07 SQL) | Not installed; `queries/workforce/read_team_inference.sql` ready |
| clock_observations | FOCUS_FN_ClockObservation | Not installed (no raw interval feed in S04) |
| duration_episodes | FOCUS_FN_DurationObservation | Not installed |
| scope_receipt, shift_roster, availability_exceptions, ot_authorizations, requirement_census, requirement_alternatives, holding_events, rework_classification, inspection_holds, work_progress, part_events, carry_in, people_directory | none yet | `NOT_PROBED`: precise readiness gaps (OD-01..OD-13) |

Proposed `FOCUS_FN_*` output tables (S06) are **not installed**. See
`sql/migrations/` (drafts, never executed by this build).
