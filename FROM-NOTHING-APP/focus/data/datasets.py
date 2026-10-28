"""Dataset registry for source runs (S04, S05, S06, P04).

Two kinds of datasets make up one source run:

* ``SOURCE`` datasets project an S04-verified table with the explicit S05
  column list (no ``SELECT *``).
* ``SUPPLEMENTAL`` datasets are the typed inputs the manual requires because
  S04 metadata cannot establish them. Examples: affirmative no-cert census,
  approved OR alternatives, revocation events, rework classification, sourced
  QA hold points, raw clock intervals, completed duration episodes, scheduled
  rosters, planned leave, OT authorizations, part receipt/reopen events,
  carry-in duties, the scope receipt, and SQL-owned team inference output
  (``FOCUS_FN_TeamInference``).

``required_for_run`` datasets must capture successfully (rows or certified
empty) or the run is REJECTED. Capability datasets that are missing or empty
without a producer certificate become readiness gaps. They never shrink the
fleet or default facts.
"""
from __future__ import annotations

from dataclasses import dataclass

from .discovery import CONTRACTS


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    name: str
    kind: str                       # SOURCE | SUPPLEMENTAL
    source_object: str | None
    columns: tuple[str, ...]
    required_for_run: bool
    capabilities: tuple[str, ...]   # inventory | forecast | executable | economics | performance | duration
    cutoff_column: str | None = None
    description: str = ""

    @property
    def query_file(self) -> str:
        return f"queries/source_inputs/{self.name}.sql"


def _src(name: str, table: str, columns: str, required: bool, caps: tuple[str, ...], desc: str = "", cutoff: str | None = None) -> DatasetSpec:
    cols = tuple(c.strip() for c in columns.split(",") if c.strip())
    contract = CONTRACTS[f"WEB_APP.{table}"]
    unknown = set(cols) - set(contract.column_names)
    if unknown:
        raise ValueError(f"{name}: columns {sorted(unknown)} are not in the S04 contract for {table}")
    return DatasetSpec(name, "SOURCE", f"WEB_APP.{table}", cols, required, caps, cutoff, desc)


def _sup(name: str, columns: str, required: bool, caps: tuple[str, ...], cutoff: str | None = None, desc: str = "") -> DatasetSpec:
    cols = tuple(c.strip() for c in columns.split(",") if c.strip())
    return DatasetSpec(name, "SUPPLEMENTAL", None, cols, required, caps, cutoff, desc)


_ALL = (
    # ---- S04-backed sources (S05 projections) ----
    _src("master_task_list", "FOCUS_MASTER_TASK_LIST",
         "SHOP_ORDER_IDENTIFIER,SHOP_ORDER_DESCRIPTION,LINE_NUMBER,SHOP_ORDER_STATUS,TEAM,CURRENT_CONTROL_STATION,MINOR_MODEL_IDENT,"
         "CUSTOMER_CODE,PARENT_SOI,SUPERINTENDENT,FIRST_AVAILABLE,ACT_START,ACT_COMP,LOAD_TIMESTAMP,PLANNING_SYSTEM_NAME,IS_REWORK,RECORD_KEY",
         True, ("inventory",), "Broad WIP/anchor census: every job on every line with any non-COMPLETE status."),
    _src("pnet_dag", "FOCUS_PNET_DAG",
         "PREDECESSOR_SOI,SUCCESSOR_SOI,RELATIONSHIP_TYPE,IS_HARD,PRED_MINOR_MODEL,SUC_MINOR_MODEL,ORIGINAL_PRED_JOBNUMBER,"
         "ORIGINAL_SUC_JOBNUMBER,OFFSET_MINUTES,LOAD_TIMESTAMP", True, ("forecast", "executable")),
    _src("neo_graph_rework", "NEO_GRAPH_REWORK", "LINE_NUMBER,PARENT_SOI,CHILD_SOI,CHILD_STATUS,SNAPSHOT_TS", True, ("forecast", "executable")),
    _src("flow_budget", "FOCUS_FLOW_BUDGET",
         "JOB_NUMBER,LINE_NUMBER,CRITICAL_FLOW_MINUTES,TOTAL_MECHANICS,PRIMARY_MECHANIC_COUNT,PRIMARY_MECHANIC_DURATION,ASSIST_COUNT,"
         "ASSIST_LABOR_MINUTES,TOTAL_LABOR_MINUTES,DURATION_SOURCE,LOAD_TIMESTAMP", True, ("forecast", "executable")),
    _src("buyoff_plan", "FOCUS_BUYOFF_PLAN",
         "SHOP_ORDER_IDENTIFIER,LINE_NUMBER,SHOP_ORDER_STATUS,MANUFACTURING_INSP,QUALITY_INSP,CC_INSP,ODAUNITMEMBER_INSP,PARTNER_QT_INSP,"
         "FINAL_QUALITY_INSP,IN_PROCESS_QUALITY_INSP,LOAD_TIMESTAMP", True, ("forecast", "executable")),
    _src("inspection_counts", "FOCUS_INSPECTION_COUNTS",
         "SHOP_ORDER_IDENTIFIER,LINE_NUMBER,MANUFACTURING_INSPECTIONS,QUALITY_INSPECTIONS,FINAL_QUALITY_INSPECTIONS,"
         "IN_PROCESS_QUALITY_INSPECTIONS,CC_INSPECTIONS,ODAUNITMEMBER_INSPECTIONS,PARTNER_QT_INSPECTIONS,LOAD_TIMESTAMP", False, ("forecast",)),
    _src("inspection_standards", "FOCUS_INSPECTION_STANDARDS", "INSPECTION_TYPE,DURATION_MINUTES,SCHEDULING_RELATIONSHIP,DESCRIPTION", True, ("forecast", "executable")),
    _src("cday_op_plan", "FOCUS_CDAY_OP_PLAN", "LINE_NUMBER,LINE,CALENDAR_DATE,POSITION,DAY_STATUS,LAST_MODIFIED_DT,LOAD_ID,LOAD_TS,IS_ACTIVE",
         True, ("forecast", "executable")),
    _src("cs_windows", "FOCUS_CS_WINDOWS",
         "MBU,CONTROL_STATION,LINE_NUMBER,LOAD_REF_POS,LOAD_OFFSET,CS_OP_LOAD,UNLOAD_REF_POS,UNLOAD_OFFSET,CS_OP_UNLOAD,LAST_MODIFIED_DT,LOAD_ID,LOAD_TS,IS_ACTIVE",
         True, ("forecast", "executable"), "Mandatory same-run CS source; never re-queried during scheduling."),
    _src("etracs_active", "FOCUS_ETRACS_ACTIVE",
         "RECORD_ID,LINE_NUMBER,MINOR_MODEL_IDENT,USED_ON,ETRAC_ECD,TEAM,REASON_CODE,ETRAC_CREATED_DT,ITEM_NUMBER,LOAD_TIMESTAMP",
         True, ("forecast", "executable")),
    _src("etracs_history", "FOCUS_ETRACS_HISTORY",
         "RECORD_ID,LINE_NUMBER,USED_ON,PARENT_SOI,PRIMARY_JOB,ETRAC_ECD,ACTUAL_CLOSURE_DATE,LOAD_TIMESTAMP", False, ("forecast",)),
    _src("actual_labor", "FOCUS_ACTUAL_LABOR",
         "SHOP_ORDER_IDENTIFIER,LINE_NUMBER,SHOP_ORDER_STATUS,LABOR_RECORDS,ACTUAL_MINUTES_BURNED,ACTUAL_HOURS_BURNED,LOAD_TIMESTAMP", False, ("performance",)),
    _src("soi_cert_requirements", "NEO_SOI_CERT_REQUIREMENTS", "SOI_ID,LINE_NUMBER,CRITICAL_PROCESS_NAME,CERT_CODE,CERT_TYPE,REQ_CERT,SNAPSHOT_TS",
         True, ("forecast", "executable")),
    _src("cert_course_map", "NEO_CERT_COURSE_MAP", "CRITICAL_PROCESS_NAME,CERT_CODE,CERT_TYPE,BAC_SPEC,BAC_VERB,SNAPSHOT_TS", False, ("forecast",)),
    _src("mechanic_certs", "FOCUS_MECHANIC_CERTS", "BEMS_ID,CERT_CODE,STATUS,LAST_CERT_DATE,CERT_EXP_DATE,SNAPSHOT_TS", True, ("forecast", "executable")),
    _src("cert_eligibility_matrix", "FOCUS_CERT_ELIGIBILITY",
         "TEAM,JOB_SOI,LINE_NUMBER,BEMS_ID,SHIFT,ELIGIBLE,PCT_CERTS_HELD,CERTS_REQUIRED,CERTS_HELD,SUBSTITUTION,LOAD_TIMESTAMP", False, ("projection",),
         "Convenience matrix, display projection only: ELIGIBLE/PCT/SUBSTITUTION grant no authorization; MECHANIC names are never extracted."),
    _src("mech_team_inferred", "FOCUS_MECH_TEAM_INFERRED", "BEMS_ID,INFERRED_TEAM,SOURCE,LABOR_RECORDS,LOAD_TIMESTAMP", False, ("forecast",),
         "Legacy inference evidence only; FOCUS_FN_TeamInference is the SQL-owned authority."),
    _src("dept_team_skill_ref", "FOCUS_DEPT_TEAM_SKILL_REF", "ACCTG_DEPT_CD,PRIMARY_TEAM,DOMINANT_SKILL,LABOR_RECORDS,LOAD_TIMESTAMP", False, ("forecast",)),
    _src("person_team_map", "FOCUS_PERSON_TEAM_MAP",
         "BEMS_ID,ROLE,TEAM,IN_SCOPE,BASIS,EFFECTIVE_FROM,EFFECTIVE_TO,RUN_ID,EVIDENCE_COUNT,LOOKBACK_DAYS,LOAD_TIMESTAMP",
         False, ("executable",), "Current effective assignment (empty live on 2026-10-27)."),
    _src("attendance_shift", "FOCUS_ATTENDANCE_SHIFT", "PRODUCTION_DAY,SHIFT_NUMBER,BEMS_ID,PRESENT,HOURS,SOURCE,LOAD_TIMESTAMP",
         False, ("executable",), "Current attendance (empty live on 2026-10-27).", cutoff="LOAD_TIMESTAMP"),
    _src("labor_shift", "FOCUS_LABOR_SHIFT", "PRODUCTION_DAY,SHIFT_NUMBER,LINE_NUMBER,SOI,BEMS_ID,MINUTES,FIRST_ON_ET,LAST_OFF_ET,LOAD_TIMESTAMP",
         False, ("performance",)),
    _src("econ_mapping_revision", "FOCUS_SOL_TESTING2_MappingRevision",
         "MappingRevisionId,SourceName,SourcePredicate,QuerySha256,ContentSha256,RowCount,ImportedAtUtc,CoverageJson,IsCurrent", False, ("economics",)),
    _src("econ_aircraft_mapping", "FOCUS_SOL_TESTING2_AircraftMapping",
         "MappingRevisionId,LineNumber,CustomerCode,MinorModel,SourceStart,SourceEnd,RevisionDate,ShopCompleteDate,AssemblyLocation,SourceContentSha256,ImportedAtUtc",
         False, ("economics",)),
    _src("econ_scenario", "FOCUS_SOL_TESTING2_EconomicsScenario",
         "ScenarioRevisionId,ScenarioKey,ScenarioRevision,Provenance,Methodology,OwnerSubject,EffectiveFromUtc,EffectiveToUtc,Currency,CapFraction,PriorityWeight,CreatedAtUtc,IsActive",
         False, ("economics",)),
    _src("econ_contract_assignment", "FOCUS_SOL_TESTING2_ContractAssignment",
         "AssignmentId,LineNumber,MappingRevisionId,ScenarioRevisionId,ContractKey,Provenance,EffectiveFromUtc,EffectiveToUtc,OwnerSubject,ApprovedBy,SourceReference,CreatedAtUtc,IsActive",
         False, ("economics",)),
    _src("econ_term", "FOCUS_SOL_TESTING2_EconomicsTerm",
         "TermId,ScenarioRevisionId,SelectorType,SelectorLength,CustomerCode,MinorModel,LineNumber,ContractKey,OriginalSalePrice,DailyRate,Currency,"
         "EffectiveFromUtc,EffectiveToUtc,Provenance,Methodology,SourceReference,OwnerSubject,ApprovedBy,TermVersion,CreatedAtUtc",
         False, ("economics",)),
    _src("econ_original_sale_basis", "FOCUS_SOL_TESTING2_OriginalSaleBasis",
         "OriginalBasisId,LineNumber,ContractKey,ScenarioRevisionId,AssignmentId,OriginalSalePrice,Currency,Provenance,TermId,SourceReference,CreatedAtUtc",
         False, ("economics",)),
    _src("econ_liability_checkpoint", "FOCUS_SOL_TESTING2_LiabilityCheckpoint",
         "CheckpointId,OriginalBasisId,SequenceNumber,AsOfUtc,ElapsedLateDays,AssessedAmount,CumulativeAccounted,AssessmentCoverage,AssessmentReference,CheckpointKind,CreatedAtUtc",
         False, ("economics",)),
    # ---- supplemental typed inputs (S03/S05/S06/S07/S08/S10) ----
    _sup("scope_receipt", "SITE,PROGRAM,SCOPE_BASIS,LINE_NUMBER,ADMISSION,REASON,EVIDENCE_REF", False, ("inventory",),
         desc="Explicit source-backed operational scope; ALL_WIP admits every observed non-COMPLETE line."),
    _sup("team_inference", "RUN_ID,PERSON_KEY,INFERRED_TEAM,WINDOW_START_UTC,WINDOW_END_UTC,EVIDENCE_COUNT,EFFORT_MINUTES,TOP_SHARE,"
         "RUNNER_UP_TEAM,RUNNER_UP_COUNT,IS_TIE,BASIS,ALGORITHM_VERSION,FIRST_EVIDENCE_UTC,LAST_EVIDENCE_UTC", False, ("forecast",),
         desc="FOCUS_FN_TeamInference rows computed in SQL Server (S07)."),
    _sup("shift_roster", "PERSON_KEY,TEAM,ROLE,SITE,SHIFT_NUMBER,EFFECTIVE_FROM,EFFECTIVE_TO,BASIS,EVIDENCE_REF", False, ("executable",),
         desc="Scheduled team/shift roster (planned availability for future dates)."),
    _sup("availability_exceptions", "PERSON_KEY,PRODUCTION_DAY,SHIFT_NUMBER,KIND,START_LOCAL,END_LOCAL,OBSERVED_AT_UTC,EVIDENCE_REF", False, ("executable",),
         "OBSERVED_AT_UTC", desc="Planned leave and other duty that removes or reduces availability."),
    _sup("ot_authorizations", "TEAM,SHIFT_NUMBER,DATE_FROM,DATE_TO,MINUTES,AUTHORITY,EVIDENCE_REF", False, ("executable",),
         desc="Explicit overtime authority; absent authority means OT 0."),
    _sup("requirement_census", "SOI_ID,LINE_NUMBER,STATUS,APPLICABILITY,EVIDENCE_REF", False, ("executable",),
         desc="Affirmative requirement census (e.g. AFFIRMATIVELY_NONE receipts)."),
    _sup("requirement_alternatives", "SOI_ID,LINE_NUMBER,GROUP_ID,CERT_CODE,APPROVAL_REF", False, ("executable",),
         desc="Explicitly approved OR alternatives (one approved group per GROUP_ID)."),
    _sup("holding_events", "BEMS_ID,CERT_CODE,EVENT,EFFECTIVE_AT_UTC,OBSERVED_AT_UTC,EVIDENCE_REF", False, ("executable",), "OBSERVED_AT_UTC",
         desc="Revocation/reinstatement events with effective time."),
    _sup("rework_classification", "LINE_NUMBER,CHILD_SOI,REWORK_CLASS,ROOT_SOI,DEFECT_ORIGIN_TEAM,EVIDENCE_REF", False, ("forecast", "executable"),
         desc="Source NC/PU/CI class and authoritative root linkage."),
    _sup("inspection_holds", "LINE_NUMBER,SOI,SEQUENCE,AFTER_WORK_MINUTES,INSPECTOR_MINUTES,EVIDENCE_REF", False, ("executable",),
         desc="Sourced in-process QA hold points with allocated inspector minutes."),
    _sup("clock_observations", "SOURCE_EVENT_KEY,LINE_NUMBER,NATIVE_SOI,PERSON_KEY,EPISODE_KEY,TEAM,REWORK_CLASS,START_UTC,END_UTC,SOURCE_REVISION,IS_VOIDED",
         False, ("duration",), desc="FOCUS_FN_ClockObservation raw intervals."),
    _sup("duration_episodes", "EPISODE_KEY,REWORK_CLASS,TEAM,MODEL,ELAPSED_MINUTES,PERSON_EFFORT_MINUTES,CYCLE_MINUTES,STATUS,CREW,COMPLETED_AT_UTC,SOURCE",
         False, ("duration",), desc="Completed comparable episodes (verified completion)."),
    _sup("work_progress", "LINE_NUMBER,SOI,PERFORMED_ELAPSED_MINUTES,REMAINING_ELAPSED_MINUTES,AS_OF_UTC,EVIDENCE_REF", False, ("forecast", "executable"),
         "AS_OF_UTC", desc="Actual performed/remaining elapsed work for in-progress jobs."),
    _sup("part_events", "RECORD_ID,EVENT,EFFECTIVE_AT_UTC,OBSERVED_AT_UTC,EVIDENCE_REF", False, ("executable",), "OBSERVED_AT_UTC",
         desc="Physical receipt / reopen / cancellation evidence for shortages."),
    _sup("carry_in", "PERSON_KEY,KIND,START_UTC,END_UTC,LINE_NUMBER,SOI,EVIDENCE_REF", False, ("executable",),
         desc="Actual or other duty already occupying people inside the horizon."),
    _sup("people_directory", "PERSON_KEY,DISPLAY_NAME,ORIGIN", False, ("inventory",),
         desc="Display names. Fixture people are invented; live omits private HR fields."),
)

DATASETS: dict[str, DatasetSpec] = {spec.name: spec for spec in _ALL}
REQUIRED = tuple(name for name, spec in DATASETS.items() if spec.required_for_run)
