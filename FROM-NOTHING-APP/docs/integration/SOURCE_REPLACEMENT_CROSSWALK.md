# FOCUS source replacement crosswalk — every dataset, table and SQL query family

**Audience:** company-authorized coding agent running inside the company network. **No real company source mapping belongs in this repository.** Every name below is a **checked-in reference contract**, not a claim that a corresponding object is currently accessible or approved in your workplace.

## Source of truth and how to inspect 100% of fields

The exact authoritative list is in:
- focus/data/datasets.py — complete ordered DatasetSpec list, canonical column names, kind, required-for-run, relevant capability, cutoff.
- focus/data/discovery.py and fixtures/source_contracts/s04_contracts.json — reference SQL column type, precision, nullability and PK for 28 S04 reference objects.
- queries/source_inputs/NAME.sql — exact checked-in projection and filters for each SQL-backed canonical dataset; the file name equals the canonical dataset name.
- focus/data/fixture_reader.py — LiveSourceReader: reference SOURCE queries and only two registered supplemental producer query paths (team_inference and clock_observations).
- focus/data/sql.py — source-object allowlist, parameterized SQL execution and separate FOCUS_FN storage object list.
- scripts/export_integration_contract.py — **automatic exhaustive JSON manifest** with columns, type strings, query SHA-256, all query files and **blank** private mapping slots.

**Do not manually copy a few example columns from this handbook and call it complete.** Always generate the current manifest and preserve the exact DatasetSpec columns.

### Schema mapping field checklist for EVERY dataset (record ONLY internally)

| Private mapping field | Mandatory meaning |
|---|---|
| canonical_dataset | Existing DatasetSpec.name, unchanged |
| internal source owner/approval | Named data authority and proof of permitted use |
| internal database/schema/object | Real location, **internal repository only** |
| query file and SHA | Approved SELECT projection and exact revision, internal only |
| column alias map | Each ordered canonical column, type/cast/nullability |
| grain and unique key | One row per what? Are repeats/versions allowed? |
| coverage | Full WIP, site/program/line/shift, including COMPLETE anchors and blocked jobs |
| status definitions | Terminal/in progress/forecast-only/blocked/voided mapping |
| temporal semantics | effective time vs observed/load time, UTC/local timezone, precision, DST |
| snapshot boundary | as-of cutoff, captured-through watermark, latest version selection |
| empty/missing semantics | ROWS / CERTIFIED_EMPTY / EMPTY_UNCERTIFIED / NOT_PROBED / FAILED |
| trust/evidence | fixture, raw sourced, inferred, synthetic, conditional or verified; source receipt |
| authority class | inventory / FORECAST / EXECUTABLE / economics / actuals / scores |
| tests | schema drift, cardinality, negative case, duplicates, cutoff and cross-source reconciliation |

## A. All **28 SQL-backed SOURCE datasets** (repo reference objects)

**Table object prefixes are existing repo reference names.** Work agent replaces each with a company-approved adapter, not by embedding production schema details in this file. Each row's exact SQL is at queries/source_inputs/DATASET.sql and explicit columns in datasets.py.

| Canonical dataset | Current reference object (WEB_APP schema) | Role / critical replacement semantics |
|---|---|---|
| master_task_list | FOCUS_MASTER_TASK_LIST | All-WIP master census, status, zone, team, first available, completion; query includes COMPLETE anchors on every line with any non-COMPLETE work. Never top-N cap. |
| pnet_dag | FOCUS_PNET_DAG | Template PNet edge endpoints, relationship FS/SS/FF/SF or concurrency, hard flag, signed offset; map across correct line/model/effectivity. |
| neo_graph_rework | NEO_GRAPH_REWORK | Line-specific rework parent→child topology; no unrelated cross-line edges. |
| flow_budget | FOCUS_FLOW_BUDGET | Exact (LINE_NUMBER, JOB_NUMBER) minutes + TOTAL/PRIMARY/ASSIST crew and labor; require approved simultaneous-vs-sequential meaning. |
| buyoff_plan | FOCUS_BUYOFF_PLAN | Inspection kinds/counts (IP, FINAL, CC, ODA, partner etc.); does NOT locate IP hold progress. |
| inspection_counts | FOCUS_INSPECTION_COUNTS | Auxiliary inspection count crosscheck; counts alone do not authorize placement. |
| inspection_standards | FOCUS_INSPECTION_STANDARDS | Typed inspection minutes and relation types; reconcile whether work standard already embeds QA effort. |
| cday_op_plan | FOCUS_CDAY_OP_PLAN | Active per-line calendar load/generation, dates and WORKING/WEEKEND/NONWORKING statuses, bounded horizon. Reference query IS_ACTIVE=1. |
| cs_windows | FOCUS_CS_WINDOWS | Active station load/unload references, signed offsets and load lineage. Must be captured SAME source run as task list. Reference query IS_ACTIVE=1. |
| etracs_active | FOCUS_ETRACS_ACTIVE | Open shortages, used-on linkage, ECD, team, reason. Unknown ECD or used-on must be surfaced as blockers/conditional. |
| etracs_history | FOCUS_ETRACS_HISTORY | Historical closure/release context, parent SOI, actual closure, no conflating predicted ECD and actual receipt. |
| actual_labor | FOCUS_ACTUAL_LABOR | Aggregate person-effort evidence, **not** raw elapsed intervals for P75 rework duration. |
| soi_cert_requirements | NEO_SOI_CERT_REQUIREMENTS | Positive critical-process/cert requirements; absence of rows does NOT prove a job needs no cert. |
| cert_course_map | NEO_CERT_COURSE_MAP | Map cert/course/spec metadata; approved substitutions separately controlled. |
| mechanic_certs | FOCUS_MECHANIC_CERTS | Individual cert holdings, status/effective/expiry and corrected revocations; exact shift-interval validity. |
| cert_eligibility_matrix | FOCUS_CERT_ELIGIBILITY | Display/diagnostic only: ELIGIBLE, PCT or SUBSTITUTION labels can NEVER grant scheduling authority. |
| mech_team_inferred | FOCUS_MECH_TEAM_INFERRED | Legacy historical inferred home team, FORECAST evidence only. Not a current roster. |
| dept_team_skill_ref | FOCUS_DEPT_TEAM_SKILL_REF | Department-to-team skill history evidence; cannot by itself prove availability or certification. |
| person_team_map | FOCUS_PERSON_TEAM_MAP | Effective dated authoritative person/team assignment; no cross-team fabricated membership. |
| attendance_shift | FOCUS_ATTENDANCE_SHIFT | Actual attendance/availability per person/day/shift; no historical team inference substitution for EXECUTABLE. |
| labor_shift | FOCUS_LABOR_SHIFT | Shift-level labor/clock observation, only if owner defines temporal and person-effort meaning. |
| econ_mapping_revision | FOCUS_SOL_TESTING2_MappingRevision | Approved line/customer/model mapping revision and source provenance; current revision selection. |
| econ_aircraft_mapping | FOCUS_SOL_TESTING2_AircraftMapping | Effective line→customer/model/mapping facts, version conflict and stale mapping checks. |
| econ_scenario | FOCUS_SOL_TESTING2_EconomicsScenario | Active authorized economic scenario, currency/cap/penalty model and approval status. |
| econ_contract_assignment | FOCUS_SOL_TESTING2_ContractAssignment | Effective line-to-contract assignment; active/non-conflicting, owner-approved. |
| econ_term | FOCUS_SOL_TESTING2_EconomicsTerm | Scoped price, daily penalty, terms and selectors; no fallback to fictional global $/day. |
| econ_original_sale_basis | FOCUS_SOL_TESTING2_OriginalSaleBasis | Actual approved original sale basis and currency used when computing any cap. |
| econ_liability_checkpoint | FOCUS_SOL_TESTING2_LiabilityCheckpoint | Previously accounted liability floor and checkpoint provenance, no double-charge. |

**Every one of these 28 datasets has a checked-in source_inputs SQL projection**; none of those reference object names is guaranteed to match your work database without internal verification. Economics and labor historical feeds may exist but are NOT authoritative without data-owner signoff.

## B. All **16 SUPPLEMENTAL** canonical datasets (needed beyond S04 views)

The existing LiveSourceReader executes an SQL path for **only** team_inference and clock_observations. The remaining 14 have **no installed live reader** in this code and therefore return NOT_PROBED, regardless of what the sample JSON contains.

| Supplemental dataset | Source needed INSIDE work | Replaces / unblocks | Current producer |
|---|---|---|---|
| scope_receipt | Signed/approved inclusion/exclusion census by line/job | ALL-WIP scope and documented exceptions | NOT_PROBED |
| team_inference | Versioned SQL-owned inference derived from historical evidence | FORECAST nominal team; not EXECUTABLE attendance | queries/workforce/read_team_inference.sql (proposed FOCUS_FN table) |
| shift_roster | Approved per-person team/role/shift planned roster with effective dates | Prospective named crew membership | NOT_PROBED |
| availability_exceptions | PTO, sick/leave, off-shift obligations, already-booked time | Accurate available regular/OT intervals | NOT_PROBED |
| ot_authorizations | Team/shift/day/person overtime authorization per owner policy | OT > 0 | NOT_PROBED |
| requirement_census | **Affirmative** complete per-SOI cert requirement/no-cert receipt | Stops false cert-free eligibility | NOT_PROBED |
| requirement_alternatives | Owner-approved OR alternative certificate groups | Legal OR substitution; default AND otherwise | NOT_PROBED |
| holding_events | Effective-dated revocation/reinstatement events | Current cert evidence over entire job duty | NOT_PROBED |
| rework_classification | Authoritative NC/PU/CI + root and defect-origin linkage | Rework P75 cohort and rework graph | NOT_PROBED |
| inspection_holds | Actual progress-point and required inspector minutes for each IP hold | IP QA placement / hold segmentation | NOT_PROBED |
| clock_observations | Raw person owner start/stop/correction/void intervals and lineage | Elapsed rework calibration | queries/durations/read_clock_observations.sql (proposed FOCUS_FN table) |
| duration_episodes | Verified completed comparable NC/PU/CI episode measurements | Rework percentiles/censoring/staleness | NOT_PROBED |
| work_progress | Sourced completed and remaining elapsed work for IN_PROGRESS jobs | Avoid restarting already performed work | NOT_PROBED |
| part_events | Sourced physical receipt/reopen/cancellation with event time | EXECUTABLE material availability | NOT_PROBED |
| carry_in | Already occupied person duty across horizon (work/travel/wait/other) | No double booking / concurrent crew collision | NOT_PROBED |
| people_directory | Authorized minimal display identity (avoid private HR fields) | UI names and human-readable roles | NOT_PROBED |

Any supplemental source without approved producer + complete receipt is **not** a silent empty success. Use a trusted SQL view, batch producer, approved adapter or governed landing table and return correct capture status. Missing/empty inputs may block specific capabilities even if the base source run is valid.

## C. All checked-in SQL query families and why they exist

All query texts are in the repository under queries/. The exporter provides an exhaustive **one-row-per-SQL-file** catalog with SHA-256, path and top comment. Below is the functional grouping (query text is not duplicated here to avoid divergent copies).

| SQL folder / files | Role | Status caveat |
|---|---|---|
| queries/source_inputs/*.sql (28) | Exact read-only source projections matching SOURCE DatasetSpec | Checked-in reference queries, not confirmed work production mappings. |
| queries/discovery/schema_reference.sql, fn_collisions.sql | Compare schema columns, inventory reserved FOCUS_FN prefixes | Run only after internal DBA approves target and read scope. |
| queries/workforce/infer_mechanic_teams.sql, read_team_inference.sql | Historical team plurality inference + read current SQL-owned result | Team inference is forecast, not current presence; FOCUS_FN producer not installed. |
| queries/qualifications/requirement_census.sql, holding_validity_profile.sql | Readiness: unknown/no-cert census and holding validity | Read-only diagnostic, source authority requires owner confirmation. |
| queries/durations/build_observations.sql, percentiles.sql, read_clock_observations.sql | Canonical clock union, duration stats, raw source | Draft SQL/producer not installed; actual labor aggregates cannot substitute. |
| queries/calendars/calendar_generations.sql, cs_window_grain.sql | Calendar/CS active loads, conflicts and raw signed offsets | No schedule if generation/offset interpretation unknown. |
| queries/graph/template_edge_profile.sql, rework_linkage.sql | Hard DAG relation and rework parent-child coverage | Unknown endpoints/cycles/hard concurrency need explicit handling. |
| queries/parts/shortage_profile.sql | Shortage linkage and ECD unknowns | ECD is not physical receipt. |
| queries/quality/inspection_profile.sql | QA counts vs sourced holds and inspection classification | QA counts alone cannot locate hold. |
| queries/economics/effective_terms.sql, liability_floor.sql | Authoritative effective pricing and accounted-liability comparison | No approved terms means cost UNKNOWN. |
| queries/actuals/actual_intervals.sql, defect_evidence.sql, disruption_claims.sql, excusal_decisions.sql, goal_amendments.sql, lifecycle_events.sql, producer_receipts.sql | Exact observation feed SELECT with as-of parameter and source revision | Proposed FOCUS_FN landing tables; not installed or validated in live. |

**Important query-time invariants:** use explicit projected aliases in spec.columns order; values parameterized, identifiers from an allowlist; cutoff/watermark and source identity controlled server-side; never SELECT *, never loose JOIN that duplicates jobs or personnel, never row-limit WIP to 50, never allow a future load to be mistaken as an observation at the cutoff; preserve raw source date precision and all voided/corrected revisions until latest valid version selected.

### Query substitution template (reference only; fill internals privately)

~~~sql
-- TEMPLATE ONLY; never paste a real company source object here.
-- Per DatasetSpec: validate exact aliases/types and producer owner.
SELECT
    src.<approved_identifier_1> AS <CANONICAL_COLUMN_1>,
    src.<approved_identifier_2> AS <CANONICAL_COLUMN_2>
    -- ... every spec.columns member, no SELECT *
FROM <APPROVED_INTERNAL_VIEW> AS src
WHERE src.<APPROVED_OBSERVED_OR_LOAD_INSTANT> <= %s;
-- Implement completeness/revision/cutoff semantics separately.
-- No hidden TOP N, no loss of completed anchors or affected lines.
~~~

Some already checked-in source queries do not themselves accept an as-of parameter; the company adapter must **explicitly** implement a consistent isolation/cutoff policy around captured data and record its evidence. Do not assume an ISO timestamp comparison is sufficient for every source: effective vs observed vs recorded semantics differ.

## D. FOCUS-owned storage and draft SQL objects — not source replacements

These are **proposed application persistence tables**, not incoming work-source views. See sql/migrations/*.sql; in source code focus/data/sql.py::FN_OBJECTS is the checked-in allowlist. The migration scripts were drafted; authoring records say not installed. Do NOT automatically run them.

| Groups | FOCUS_FN objects and role |
|---|---|
| Run/metadata | SchemaVersion, Policy, SourceRun, SourceDataset, SourceHead, SourceRow |
| Derived inputs | ClockObservation, TeamInference, DurationObservation, DurationStatistics |
| Scheduling & publication | PlanStage, Validation, PublishedPlan, PlanHead, FrozenGoal, Commitment |
| Observation & scoring | Event, ExcusalDecision, Evaluation, ObservationProducerReceipt, LifecycleSource, ActualIntervalSource, DefectEvidenceSource, GoalAmendmentSource, DisruptionClaimSource, ExcusalDecisionSource |
| Runtime state | WorkerRun, Artifact, State |

All above have WEB_APP.FOCUS_FN_ prefix in the reference SQL adapter (not guaranteed internal schema). Versioned source and publication heads require durable optimistic CAS semantics, immutable/readback proof and separate signed acceptance authority.

## E. Work-side replacement acceptance for each row

A mapping is READY only if it has: owner approval, exact canonical projection and types, correct grain/keys, dates/timezone, consistent as-of, certified completeness, honest evidence origin, drift test, negative-case test, sample count reconciled inside work, and no source-to-output collisions. Otherwise record a blocker code/capability and continue at most in the safe FORECAST/read-only lane.

## F. Source-of-truth references

- Exact columns: focus/data/datasets.py
- SQL type specs/PK: focus/data/discovery.py, fixtures/source_contracts/s04_contracts.json
- Exact current query text: queries/source_inputs/ and queries/actuals/ etc.
- SQL execution allowlist: focus/data/sql.py
- Versioned snapshot: focus/data/acquisition.py
- Target readiness: docs/execution/SOURCE_READINESS.md
- Source schema audit: docs/execution/SQL_SCHEMA_LEDGER.md
- Policy source: config/policies/rebuild-v1.json and config/policies/shift_fixture.json

Do not modify this public mapping guide with internal source details. The private work agent should maintain an independent mapping artifact in company-approved storage.
