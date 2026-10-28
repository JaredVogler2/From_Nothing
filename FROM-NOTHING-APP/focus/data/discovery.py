"""SQL discovery, exact S04 source contracts and schema drift (S04, S05, P03).

The 28 core contracts below are the manual's S04 text, verified read-only
through the SQL Server MCP on 2026-10-27 (server SQL-D-22D10090, database
SHARED). Notation: ``!`` NOT NULL, ``?`` nullable, ``^`` computed. They are
parsed into typed :class:`ColumnContract` records. Live discovery compares
the results of the S05 schema reference query against them. Any mismatch is a
``SCHEMA_DRIFT`` diagnostic naming the exact object and column.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..codec import content_hash

S04_CONTRACTS_TEXT = """
TABLE WEB_APP.FOCUS_MASTER_TASK_LIST
PRIMARY KEY: SHOP_ORDER_IDENTIFIER,LINE_NUMBER
  SHOP_ORDER_IDENTIFIER varchar(30)!
  SHOP_ORDER_DESCRIPTION varchar(500)?
  LINE_NUMBER int!
  TEAM varchar(30)?
  CURRENT_CONTROL_STATION varchar(10)?
  MINOR_MODEL_IDENT varchar(10)?
  CUSTOMER_CODE varchar(10)?
  PARENT_SOI varchar(30)?
  SUPERINTENDENT varchar(50)?
  SHOP_ORDER_STATUS varchar(20)!
  FIRST_AVAILABLE datetime2(0)?
  ACT_START datetime2(0)?
  ACT_COMP datetime2(0)?
  LOAD_TIMESTAMP datetime2(3)!
  PLANNING_SYSTEM_NAME varchar(50)?
  IS_REWORK bit?
  RECORD_KEY int?

TABLE WEB_APP.FOCUS_PNET_DAG
PRIMARY KEY: NONE
  PREDECESSOR_SOI varchar(30)!
  SUCCESSOR_SOI varchar(30)!
  RELATIONSHIP_TYPE varchar(30)?
  IS_HARD char(1)?
  PRED_MINOR_MODEL varchar(10)?
  SUC_MINOR_MODEL varchar(10)?
  ORIGINAL_PRED_JOBNUMBER varchar(50)?
  ORIGINAL_SUC_JOBNUMBER varchar(50)?
  NOTE varchar(200)?
  OFFSET_MINUTES int?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.NEO_GRAPH_REWORK
PRIMARY KEY: LINE_NUMBER,CHILD_SOI
  LINE_NUMBER int!
  PARENT_SOI varchar(30)!
  CHILD_SOI varchar(30)!
  CHILD_STATUS varchar(30)?
  CHILD_DESCRIPTION varchar(200)?
  SNAPSHOT_TS datetime?

TABLE WEB_APP.FOCUS_FLOW_BUDGET
PRIMARY KEY: JOB_NUMBER,LINE_NUMBER
  JOB_NUMBER varchar(30)!
  LINE_NUMBER int!
  MINOR_MODEL_IDENT varchar(10)?
  CUSTOMER_CODE varchar(10)?
  SUPERINTENDENT varchar(50)?
  TEAM varchar(30)?
  ASSIST_COUNT int?
  ASSIST_LABOR_MINUTES int?
  PRIMARY_MECHANIC_COUNT int?
  PRIMARY_MECHANIC_DURATION int?
  TOTAL_MECHANICS int?
  TOTAL_LABOR_MINUTES int?
  CRITICAL_FLOW_MINUTES int?
  DURATION_SOURCE varchar(30)?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_BUYOFF_PLAN
PRIMARY KEY: SHOP_ORDER_IDENTIFIER,LINE_NUMBER
  SHOP_ORDER_IDENTIFIER varchar(30)!
  LINE_NUMBER int!
  SHOP_ORDER_STATUS varchar(20)?
  MANUFACTURING_INSP int?
  QUALITY_INSP int?
  CC_INSP int?
  ODAUNITMEMBER_INSP int?
  PARTNER_QT_INSP int?
  FINAL_QUALITY_INSP int?
  IN_PROCESS_QUALITY_INSP int?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_INSPECTION_COUNTS
PRIMARY KEY: NONE
  SHOP_ORDER_IDENTIFIER varchar(50)!
  LINE_NUMBER int!
  MANUFACTURING_INSPECTIONS int!
  QUALITY_INSPECTIONS int!
  FINAL_QUALITY_INSPECTIONS int!
  IN_PROCESS_QUALITY_INSPECTIONS int!
  CC_INSPECTIONS int!
  ODAUNITMEMBER_INSPECTIONS int!
  PARTNER_QT_INSPECTIONS int!
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_INSPECTION_STANDARDS
PRIMARY KEY: INSPECTION_TYPE
  INSPECTION_TYPE varchar(30)!
  DURATION_MINUTES int!
  SCHEDULING_RELATIONSHIP varchar(20)!
  DESCRIPTION varchar(200)?

TABLE WEB_APP.FOCUS_CDAY_OP_PLAN
PRIMARY KEY: NONE
  LINE_NUMBER int!
  LINE nvarchar(10)?
  CALENDAR_DATE date!
  POSITION nvarchar(10)?
  DAY_STATUS nvarchar(15)?
  LAST_MODIFIED_DT date!
  LOAD_ID int!
  LOAD_TS datetime2(0)!
  IS_ACTIVE bit!

TABLE WEB_APP.FOCUS_CS_WINDOWS
PRIMARY KEY: NONE
  MBU nvarchar(15)?
  CONTROL_STATION int!
  LINE_NUMBER int!
  LOAD_REF_POS nvarchar(5)?
  LOAD_OFFSET int?
  CS_OP_LOAD date?
  UNLOAD_REF_POS nvarchar(5)?
  UNLOAD_OFFSET int?
  CS_OP_UNLOAD date?
  LAST_MODIFIED_DT date!
  LOAD_ID int!
  LOAD_TS datetime2(0)!
  IS_ACTIVE bit!

TABLE WEB_APP.FOCUS_ETRACS_ACTIVE
PRIMARY KEY: RECORD_ID
  RECORD_ID int!
  LINE_NUMBER int!
  MINOR_MODEL_IDENT varchar(10)?
  ETRAC_CREATED_DT date?
  ITEM_NUMBER varchar(50)?
  USED_ON varchar(50)?
  ITEM_DESCRIPTION varchar(500)?
  BUS_PARTNER_NAME varchar(200)?
  BUS_PARTNER_CODE varchar(30)?
  REASON_CODE varchar(10)?
  TEAM varchar(50)?
  ETRAC_ECD date?
  LEAD_TIME_FLOW_DAYS int?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_ETRACS_HISTORY
PRIMARY KEY: RECORD_ID
  RECORD_ID int!
  LINE_NUMBER int!
  MINOR_MODEL_IDENT varchar(10)?
  ETRAC_CREATED_DT date?
  ITEM_NUMBER varchar(50)?
  ITEM_DESCRIPTION varchar(500)?
  BUS_PARTNER_NAME varchar(200)?
  REASON_CODE varchar(10)?
  TEAM varchar(50)?
  CONTROL_STATION varchar(10)?
  USED_ON varchar(50)?
  PARENT_SOI varchar(50)?
  ETRAC_ECD date?
  ACTUAL_CLOSURE_DATE date?
  PRIMARY_JOB varchar(50)?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_ACTUAL_LABOR
PRIMARY KEY: SHOP_ORDER_IDENTIFIER,LINE_NUMBER
  SHOP_ORDER_IDENTIFIER varchar(30)!
  LINE_NUMBER int!
  SHOP_ORDER_STATUS varchar(20)?
  LABOR_RECORDS int?
  ACTUAL_MINUTES_BURNED decimal(18,2)?
  ACTUAL_HOURS_BURNED decimal(18,2)?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.NEO_SOI_CERT_REQUIREMENTS
PRIMARY KEY: NONE
  SOI_ID varchar(30)!
  LINE_NUMBER int!
  CRITICAL_PROCESS_NAME varchar(200)?
  CERT_CODE varchar(100)!
  CERT_TYPE varchar(20)?
  REQ_CERT char(1)?
  SNAPSHOT_TS datetime?

TABLE WEB_APP.NEO_CERT_COURSE_MAP
PRIMARY KEY: CRITICAL_PROCESS_NAME,CERT_CODE
  CRITICAL_PROCESS_NAME varchar(200)!
  CERT_CODE varchar(100)!
  CERT_TYPE varchar(20)?
  BAC_SPEC varchar(50)?
  BAC_VERB varchar(150)?
  SNAPSHOT_TS datetime?

TABLE WEB_APP.FOCUS_MECHANIC_CERTS
PRIMARY KEY: NONE
  BEMS_ID varchar(10)!
  CERT_CODE varchar(20)?
  STATUS varchar(10)?
  LAST_CERT_DATE varchar(10)?
  CERT_EXP_DATE varchar(10)?
  SNAPSHOT_TS datetime?

TABLE WEB_APP.FOCUS_CERT_ELIGIBILITY
PRIMARY KEY: NONE
  TEAM varchar(30)?
  JOB_SOI varchar(30)?
  DESCRIPTION varchar(500)?
  MINOR_MODEL varchar(10)?
  LINE_NUMBER int?
  CONTROL_STATION varchar(10)?
  BEMS_ID bigint?
  MECHANIC varchar(80)?
  SHIFT varchar(5)?
  ELIGIBLE varchar(15)?
  PCT_CERTS_HELD decimal(5,1)?
  CERTS_REQUIRED int?
  CERTS_HELD int?
  MISSING_CERTS varchar(2000)?
  MISSING_TRAININGS varchar(4000)?
  SUBSTITUTION varchar(8)?
  LINE_RANGE varchar(20)?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_MECH_TEAM_INFERRED
PRIMARY KEY: BEMS_ID,INFERRED_TEAM
  BEMS_ID bigint!
  INFERRED_TEAM varchar(30)!
  SOURCE varchar(15)!
  LABOR_RECORDS int!
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_DEPT_TEAM_SKILL_REF
PRIMARY KEY: ACCTG_DEPT_CD
  ACCTG_DEPT_CD varchar(10)!
  PRIMARY_TEAM varchar(30)?
  DOMINANT_SKILL varchar(5)?
  LABOR_RECORDS int?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_PERSON_TEAM_MAP
PRIMARY KEY: BEMS_ID,EFFECTIVE_FROM
  BEMS_ID varchar(10)!
  ROLE varchar(10)!
  TEAM varchar(40)?
  SUPERINTENDENT varchar(50)?
  QA_AREA varchar(20)?
  IN_SCOPE bit!
  BASIS varchar(30)!
  TOP_SHARE decimal(5,4)?
  SECOND_TEAM varchar(40)?
  EVIDENCE_COUNT int!
  LOOKBACK_DAYS smallint!
  LAST_EVIDENCE_DATE date?
  FLAGS varchar(40)?
  EFFECTIVE_FROM datetime2(0)!
  EFFECTIVE_TO datetime2(0)?
  RUN_ID varchar(40)?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_ATTENDANCE_SHIFT
PRIMARY KEY: PRODUCTION_DAY,SHIFT_NUMBER,BEMS_ID
  PRODUCTION_DAY date!
  SHIFT_NUMBER tinyint!
  BEMS_ID varchar(10)!
  PRESENT bit!
  SOURCE varchar(20)!
  HOURS decimal(5,2)?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_LABOR_SHIFT
PRIMARY KEY: PRODUCTION_DAY,SHIFT_NUMBER,LINE_NUMBER,SOI,BEMS_ID
  PRODUCTION_DAY date!
  SHIFT_NUMBER tinyint!
  LINE_NUMBER int!
  SOI varchar(40)!
  BEMS_ID varchar(10)!
  MINUTES decimal(9,2)!
  FIRST_ON_ET datetime2(0)?
  LAST_OFF_ET datetime2(0)?
  LOAD_TIMESTAMP datetime2(3)!

TABLE WEB_APP.FOCUS_SOL_TESTING2_MappingRevision
PRIMARY KEY: MappingRevisionId
  MappingRevisionId uniqueidentifier!
  SourceName varchar(128)!
  SourcePredicate nvarchar(500)!
  QuerySha256 char(64)!
  ContentSha256 char(64)!
  RowCount int!
  ImportedByKey varbinary(256)!
  ImportedByKeyLength int?^
  ImportedAtUtc datetime2(3)!
  CoverageJson nvarchar(4000)!
  IsCurrent bit!

TABLE WEB_APP.FOCUS_SOL_TESTING2_AircraftMapping
PRIMARY KEY: MappingRevisionId,LineNumber
  MappingRevisionId uniqueidentifier!
  LineNumber int!
  CustomerCode varchar(7)!
  CustomerBytes varbinary(7)?^
  CustomerLength int?^
  MinorModel tinyint!
  SourceStart datetimeoffset(6)?
  SourceEnd datetimeoffset(6)?
  RevisionDate date?
  ShopCompleteDate date?
  AssemblyLocation char(1)?
  SourceContentSha256 char(64)!
  ImportedAtUtc datetime2(3)!

TABLE WEB_APP.FOCUS_SOL_TESTING2_EconomicsScenario
PRIMARY KEY: ScenarioRevisionId
  ScenarioRevisionId uniqueidentifier!
  ScenarioKey varchar(64)!
  ScenarioKeyBytes varbinary(64)?^
  ScenarioKeyLength int?^
  ScenarioRevision int!
  Provenance varchar(24)!
  Methodology nvarchar(1000)!
  OwnerSubject nvarchar(128)!
  EffectiveFromUtc datetime2(3)!
  EffectiveToUtc datetime2(3)?
  Currency char(3)!
  CapFraction decimal(5,4)!
  PriorityWeight decimal(6,5)!
  CreatedByKey varbinary(256)!
  CreatedByKeyLength int?^
  CreatedAtUtc datetime2(3)!
  IsActive bit!

TABLE WEB_APP.FOCUS_SOL_TESTING2_ContractAssignment
PRIMARY KEY: AssignmentId
  AssignmentId uniqueidentifier!
  LineNumber int!
  MappingRevisionId uniqueidentifier!
  ScenarioRevisionId uniqueidentifier!
  ContractKey varchar(128)!
  ContractKeyBytes varbinary(128)?^
  ContractKeyLength int?^
  Provenance varchar(24)!
  EffectiveFromUtc datetime2(3)!
  EffectiveToUtc datetime2(3)?
  OwnerSubject nvarchar(128)!
  ApprovedBy nvarchar(128)?
  SourceReference nvarchar(512)!
  CreatedByKey varbinary(256)!
  CreatedByKeyLength int?^
  CreatedAtUtc datetime2(3)!
  IsActive bit!

TABLE WEB_APP.FOCUS_SOL_TESTING2_EconomicsTerm
PRIMARY KEY: TermId
  TermId uniqueidentifier!
  ScenarioRevisionId uniqueidentifier!
  SelectorType varchar(24)!
  SelectorKey varbinary(256)!
  SelectorLength smallint!
  CustomerCode varchar(7)!
  CustomerBytes varbinary(7)?^
  CustomerLength int?^
  MinorModel tinyint!
  LineNumber int?
  ContractKey varchar(128)?
  ContractIsNull bit?^
  ContractBytes varbinary(128)?^
  ContractLength int?^
  OriginalSalePrice decimal(19,4)!
  DailyRate decimal(19,4)!
  Currency char(3)!
  EffectiveFromUtc datetime2(3)!
  EffectiveToUtc datetime2(3)?
  Provenance varchar(24)!
  Methodology nvarchar(1000)!
  SourceReference nvarchar(512)!
  OwnerSubject nvarchar(128)!
  ApprovedBy nvarchar(128)?
  TermVersion bigint!
  CreatedByKey varbinary(256)!
  CreatedByKeyLength int?^
  CreatedAtUtc datetime2(3)!

TABLE WEB_APP.FOCUS_SOL_TESTING2_OriginalSaleBasis
PRIMARY KEY: OriginalBasisId
  OriginalBasisId uniqueidentifier!
  LineNumber int!
  ContractKey varchar(128)!
  ContractKeyBytes varbinary(128)?^
  ContractKeyLength int?^
  ScenarioRevisionId uniqueidentifier!
  AssignmentId uniqueidentifier!
  OriginalSalePrice decimal(19,4)!
  Currency char(3)!
  Provenance varchar(24)!
  TermId uniqueidentifier!
  SourceReference nvarchar(512)!
  CreatedByKey varbinary(256)!
  CreatedByKeyLength int?^
  CreatedAtUtc datetime2(3)!

TABLE WEB_APP.FOCUS_SOL_TESTING2_LiabilityCheckpoint
PRIMARY KEY: CheckpointId
  CheckpointId uniqueidentifier!
  OriginalBasisId uniqueidentifier!
  SequenceNumber bigint!
  AsOfUtc datetime2(3)!
  ElapsedLateDays decimal(18,6)!
  AssessedAmount decimal(19,4)!
  CumulativeAccounted decimal(19,4)!
  AssessmentCoverage varchar(32)!
  AssessmentReference nvarchar(512)?
  CheckpointKind varchar(16)!
  ActorKey varbinary(256)!
  ActorKeyLength int?^
  CreatedAtUtc datetime2(3)!
"""

ECONOMICS_FOREIGN_KEYS = (
    ("AircraftMapping.MappingRevisionId", "MappingRevision.MappingRevisionId"),
    ("ContractAssignment.MappingRevisionId", "MappingRevision.MappingRevisionId"),
    ("ContractAssignment.ScenarioRevisionId", "EconomicsScenario.ScenarioRevisionId"),
    ("EconomicsTerm.ScenarioRevisionId", "EconomicsScenario.ScenarioRevisionId"),
    ("OriginalSaleBasis.AssignmentId", "ContractAssignment.AssignmentId"),
    ("OriginalSaleBasis.ScenarioRevisionId", "EconomicsScenario.ScenarioRevisionId"),
    ("OriginalSaleBasis.TermId", "EconomicsTerm.TermId"),
    ("LiabilityCheckpoint.OriginalBasisId", "OriginalSaleBasis.OriginalBasisId"),
)

# S04 CURRENT OBSERVATIONS (catalog estimates and exact bounded aggregates), kept as
# authoring evidence for readiness reports; never used as live facts.
S04_OBSERVATIONS: dict[str, Any] = {
    "catalog_row_estimates": {
        "FOCUS_MASTER_TASK_LIST": 192525, "FOCUS_FLOW_BUDGET": 8721, "FOCUS_PNET_DAG": 294941,
        "FOCUS_CDAY_OP_PLAN": 87417, "FOCUS_CS_WINDOWS": 40840, "FOCUS_ETRACS_ACTIVE": 4841,
        "FOCUS_ACTUAL_LABOR": 143862, "FOCUS_MECHANIC_CERTS": 88093, "NEO_SOI_CERT_REQUIREMENTS": 300115,
        "FOCUS_MECH_TEAM_INFERRED": 987, "FOCUS_ATTENDANCE_SHIFT": 0, "FOCUS_PERSON_TEAM_MAP": 0, "FOCUS_LABOR_SHIFT": 0,
    },
    "invalid_total_mechanics": 984,
    "active_shortage_null_ecd": 450,
    "blank_used_on": 1484,
    "calendar_statuses": {"WORKING": 5079, "WEEKEND": 2409, "NONWORKING": 1305},
    "inference_source": {"LABOR": 930, "DEPT_FALLBACK": 57},
    "master_statuses": {"COMPLETE": 139263, "AVAILABLE": 36831, "PREVIEW": 10643, "IDLE": 3016, "PLANNED": 1103,
                        "FORECASTED": 617, "ON HOLD": 478, "IN WORK": 423, "FAILPREVIEW": 114, "REVISIONHOLD": 24, "UNRELEASED": 13},
    "noncomplete_distinct_lines": 150,
    "dag_relationships": {"FINISH <= START": 290118, "FINISH <= FINISH": 1929, "START <= START": 1747,
                          "START <= FINISH": 5, "CONCURRENT": 581, "NON-CONCURRENT": 561},
    "dag_is_hard": {"Y": 96690, "N": 198251},
    "source_clock": "2026-10-27T21:38:02.5354146Z",
}

_COLUMN = re.compile(r"^\s+([A-Za-z0-9_]+) ([a-z0-9]+)(?:\(([^)]*)\))?([!?])(\^)?\s*$")


@dataclass(frozen=True, slots=True)
class ColumnContract:
    name: str
    sql_type: str
    length: int | None        # declared characters for char types; -1 for max
    precision: int | None
    scale: int | None
    nullable: bool
    computed: bool

    def render(self) -> str:
        if self.sql_type in ("decimal", "numeric"):
            spec = f"({self.precision},{self.scale})"
        elif self.sql_type in ("datetime2", "datetimeoffset", "time"):
            spec = f"({self.scale})"
        elif self.length is not None:
            spec = "(max)" if self.length == -1 else f"({self.length})"
        else:
            spec = ""
        return f"{self.name} {self.sql_type}{spec}{'?' if self.nullable else '!'}{'^' if self.computed else ''}"


@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    primary_key: tuple[str, ...]
    columns: tuple[ColumnContract, ...]

    def column(self, name: str) -> ColumnContract:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(f"{self.name} has no column {name}")

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


def parse_contracts(text: str = S04_CONTRACTS_TEXT) -> dict[str, TableContract]:
    tables: dict[str, TableContract] = {}
    current: str | None = None
    pk: tuple[str, ...] = ()
    cols: list[ColumnContract] = []

    def flush() -> None:
        if current is not None:
            tables[current] = TableContract(current, pk, tuple(cols))

    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("TABLE "):
            flush()
            current = raw.split(" ", 1)[1].strip()
            pk, cols = (), []
            continue
        if raw.startswith("PRIMARY KEY:"):
            spec = raw.split(":", 1)[1].strip()
            pk = () if spec == "NONE" else tuple(s.strip() for s in spec.split(","))
            continue
        found = _COLUMN.match(raw)
        if not found:
            raise ValueError(f"unparseable contract line: {raw!r}")
        name, sql_type, spec, null_flag, computed = found.groups()
        length = precision = scale = None
        if spec:
            parts = [p.strip() for p in spec.split(",")]
            if sql_type in ("decimal", "numeric"):
                precision, scale = int(parts[0]), int(parts[1])
            elif sql_type in ("datetime2", "datetimeoffset", "time"):
                scale = int(parts[0])
            else:
                length = -1 if parts[0] == "max" else int(parts[0])
        cols.append(ColumnContract(name, sql_type, length, precision, scale, null_flag == "?", computed == "^"))
    flush()
    return tables


CONTRACTS: dict[str, TableContract] = parse_contracts()
CONTRACTS_SHA = content_hash({name: [c.render() for c in t.columns] + ["PK:" + ",".join(t.primary_key)] for name, t in sorted(CONTRACTS.items())})

SCHEMA_REFERENCE_QUERY = """SELECT s.name AS schema_name,t.name AS table_name,c.column_id,c.name AS column_name,
 ty.name AS type_name,c.max_length,c.precision,c.scale,c.is_nullable,
 c.is_identity,c.is_computed
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.columns c ON c.object_id=t.object_id
JOIN sys.types ty ON ty.user_type_id=c.user_type_id
WHERE s.name='WEB_APP' AND
 (t.name LIKE 'FOCUS%' OR t.name IN ('NEO_GRAPH_REWORK',
 'NEO_SOI_CERT_REQUIREMENTS','NEO_CERT_COURSE_MAP'))
ORDER BY s.name,t.name,c.column_id"""

FN_COLLISION_QUERY = """SELECT s.name AS schema_name, t.name AS table_name
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
WHERE s.name='WEB_APP' AND (t.name LIKE 'FOCUS[_]FN[_]%' OR t.name LIKE 'FOCUS[_]REBUILD%')
ORDER BY t.name"""

_NCHAR_TYPES = ("nvarchar", "nchar")
_CHAR_TYPES = ("varchar", "char", "nvarchar", "nchar", "varbinary", "binary")


@dataclass(frozen=True, slots=True)
class DriftFinding:
    code: str            # SCHEMA_DRIFT | OBJECT_MISSING | COLUMN_MISSING | COLUMN_UNEXPECTED
    object: str
    column: str | None
    expected: str | None
    observed: str | None


def contract_from_catalog_row(row: dict[str, Any]) -> ColumnContract:
    sql_type = str(row["type_name"])
    max_length = int(row["max_length"])
    length = None
    if sql_type in _CHAR_TYPES:
        if max_length == -1:
            length = -1
        elif sql_type in _NCHAR_TYPES:
            length = max_length // 2
        else:
            length = max_length
    precision = int(row["precision"]) if sql_type in ("decimal", "numeric") else None
    scale = int(row["scale"]) if sql_type in ("decimal", "numeric", "datetime2", "datetimeoffset", "time") else None
    return ColumnContract(str(row["column_name"]), sql_type, length, precision, scale, bool(row["is_nullable"]), bool(row["is_computed"]))


def compare_catalog(rows: Iterable[dict[str, Any]], expected: dict[str, TableContract] = CONTRACTS) -> list[DriftFinding]:
    """Compare S05 schema-reference rows with the S04 contracts (selected objects only)."""
    observed: dict[str, dict[str, ColumnContract]] = {}
    for row in rows:
        table = f"{row['schema_name']}.{row['table_name']}"
        observed.setdefault(table, {})[str(row["column_name"])] = contract_from_catalog_row(row)
    findings: list[DriftFinding] = []
    for name, contract in sorted(expected.items()):
        cols = observed.get(name)
        if cols is None:
            findings.append(DriftFinding("OBJECT_MISSING", name, None, "present", None))
            continue
        for col in contract.columns:
            seen = cols.get(col.name)
            if seen is None:
                findings.append(DriftFinding("COLUMN_MISSING", name, col.name, col.render(), None))
            elif seen != col:
                findings.append(DriftFinding("SCHEMA_DRIFT", name, col.name, col.render(), seen.render()))
        for extra in sorted(set(cols) - set(contract.column_names)):
            findings.append(DriftFinding("COLUMN_UNEXPECTED", name, extra, None, cols[extra].render()))
    return findings


def catalog_rows_from_contracts(contracts: dict[str, TableContract] = CONTRACTS) -> list[dict[str, Any]]:
    """Synthesize schema-reference rows equivalent to the contracts (fixture discovery)."""
    rows = []
    for name, contract in sorted(contracts.items()):
        schema, table = name.split(".", 1)
        for idx, col in enumerate(contract.columns, start=1):
            if col.length == -1:
                max_length = -1
            elif col.sql_type in _NCHAR_TYPES and col.length is not None:
                max_length = col.length * 2
            elif col.length is not None:
                max_length = col.length
            else:
                max_length = 8
            rows.append({
                "schema_name": schema, "table_name": table, "column_id": idx, "column_name": col.name,
                "type_name": col.sql_type, "max_length": max_length,
                "precision": col.precision or 0, "scale": col.scale or 0,
                "is_nullable": col.nullable, "is_identity": False, "is_computed": col.computed,
            })
    return rows


def cli_source_discover(services: Any, args: Any) -> dict[str, Any]:
    """Read-only discovery. Live: runs the S05 schema query; fixture: verifies fixture contracts."""
    settings = services.settings
    mode = args.mode or settings.mode
    report: dict[str, Any] = {
        "mode": mode,
        "contracts": len(CONTRACTS),
        "contracts_sha256": CONTRACTS_SHA,
        "schema_query_sha256": content_hash(SCHEMA_REFERENCE_QUERY),
    }
    if mode == "fixture":
        from .fixture_reader import FixtureSourceReader

        reader = FixtureSourceReader(settings.fixture_inputs_dir / settings.scenario_id)
        coverage = reader.contract_coverage()
        report.update({"status": "ok", "probe": "fixture_contracts", "coverage": coverage,
                       "drift": [f.__dict__ for f in compare_catalog(catalog_rows_from_contracts())]})
        return report
    if not settings.sql_configured:
        report.update({"status": "unavailable", "probe": "not_probed",
                       "reason": "FOCUS_SQL_* credentials are not configured in this environment"})
        return report
    from .sql import SqlReader, SqlUnavailable

    reader = SqlReader(settings)
    try:
        result = reader.fetch("schema_reference", SCHEMA_REFERENCE_QUERY)
        collisions = reader.fetch("fn_collisions", FN_COLLISION_QUERY)
    except SqlUnavailable as exc:
        report.update({"status": "unavailable", "probe": "failed", "reason": f"{exc.code}: {exc.message}"})
        return report
    rows = [dict(zip(result.columns, r)) for r in result.rows]
    drift = compare_catalog(rows)
    report.update({
        "status": "ok",
        "probe": "live_schema_reference",
        "observed_columns": len(rows),
        "drift": [f.__dict__ for f in drift],
        "fn_prefix_collisions": [dict(zip(collisions.columns, r)) for r in collisions.rows],
    })
    return report
