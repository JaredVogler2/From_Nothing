"""SQL Server access for live mode (S05): pymssql with injected credentials.

* Credentials come only from :class:`focus.config.Settings` (FOCUS_* or the
  Tanzu VCAP adapter); nothing is hardcoded and nothing is logged.
* Login and query timeouts are bounded.
* Values are always parameters (``%s``); object names must be allowlisted.
* The reader role only executes SELECT/WITH statements. Installer, stager and
  signer actions use separate principals and separate code paths.
* ``pymssql`` is imported lazily so the app imports without the driver.
* Nothing connects at import or on a GET request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence

from ..codec import sha256_hex
from ..config import Settings

SOURCE_OBJECTS = frozenset({
    "WEB_APP.FOCUS_MASTER_TASK_LIST", "WEB_APP.FOCUS_PNET_DAG", "WEB_APP.NEO_GRAPH_REWORK",
    "WEB_APP.FOCUS_FLOW_BUDGET", "WEB_APP.FOCUS_BUYOFF_PLAN", "WEB_APP.FOCUS_INSPECTION_COUNTS",
    "WEB_APP.FOCUS_INSPECTION_STANDARDS", "WEB_APP.FOCUS_CDAY_OP_PLAN", "WEB_APP.FOCUS_CS_WINDOWS",
    "WEB_APP.FOCUS_ETRACS_ACTIVE", "WEB_APP.FOCUS_ETRACS_HISTORY", "WEB_APP.FOCUS_ACTUAL_LABOR",
    "WEB_APP.NEO_SOI_CERT_REQUIREMENTS", "WEB_APP.NEO_CERT_COURSE_MAP", "WEB_APP.FOCUS_MECHANIC_CERTS",
    "WEB_APP.FOCUS_CERT_ELIGIBILITY", "WEB_APP.FOCUS_MECH_TEAM_INFERRED", "WEB_APP.FOCUS_DEPT_TEAM_SKILL_REF",
    "WEB_APP.FOCUS_PERSON_TEAM_MAP", "WEB_APP.FOCUS_ATTENDANCE_SHIFT", "WEB_APP.FOCUS_LABOR_SHIFT",
    "WEB_APP.FOCUS_SOL_TESTING2_MappingRevision", "WEB_APP.FOCUS_SOL_TESTING2_AircraftMapping",
    "WEB_APP.FOCUS_SOL_TESTING2_EconomicsScenario", "WEB_APP.FOCUS_SOL_TESTING2_ContractAssignment",
    "WEB_APP.FOCUS_SOL_TESTING2_EconomicsTerm", "WEB_APP.FOCUS_SOL_TESTING2_OriginalSaleBasis",
    "WEB_APP.FOCUS_SOL_TESTING2_LiabilityCheckpoint",
})

FN_OBJECTS = frozenset({
    "WEB_APP.FOCUS_FN_SchemaVersion", "WEB_APP.FOCUS_FN_Policy", "WEB_APP.FOCUS_FN_SourceRun",
    "WEB_APP.FOCUS_FN_SourceDataset", "WEB_APP.FOCUS_FN_SourceHead", "WEB_APP.FOCUS_FN_SourceRow",
    "WEB_APP.FOCUS_FN_ClockObservation", "WEB_APP.FOCUS_FN_TeamInference", "WEB_APP.FOCUS_FN_DurationObservation",
    "WEB_APP.FOCUS_FN_DurationStatistics", "WEB_APP.FOCUS_FN_PlanStage", "WEB_APP.FOCUS_FN_Validation",
    "WEB_APP.FOCUS_FN_PublishedPlan", "WEB_APP.FOCUS_FN_PlanHead", "WEB_APP.FOCUS_FN_FrozenGoal",
    "WEB_APP.FOCUS_FN_Commitment", "WEB_APP.FOCUS_FN_Event", "WEB_APP.FOCUS_FN_ExcusalDecision",
    "WEB_APP.FOCUS_FN_Evaluation", "WEB_APP.FOCUS_FN_WorkerRun", "WEB_APP.FOCUS_FN_Artifact",
    "WEB_APP.FOCUS_FN_State",
    # P17 observation landing tables and receipts (sql/migrations/008_lifecycle.sql, proposed, not installed)
    "WEB_APP.FOCUS_FN_ObservationProducerReceipt", "WEB_APP.FOCUS_FN_LifecycleSource", "WEB_APP.FOCUS_FN_ActualIntervalSource",
    "WEB_APP.FOCUS_FN_DefectEvidenceSource", "WEB_APP.FOCUS_FN_GoalAmendmentSource", "WEB_APP.FOCUS_FN_DisruptionClaimSource",
    "WEB_APP.FOCUS_FN_ExcusalDecisionSource",
})

ALLOWED_OBJECTS = SOURCE_OBJECTS | FN_OBJECTS

_SELECT_ONLY = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE)\b", re.IGNORECASE)


class SqlUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def query_hash(sql: str) -> str:
    normalized = "\n".join(line.rstrip() for line in sql.strip().splitlines())
    return sha256_hex(normalized)


def connect(settings: Settings, *, autocommit: bool = False) -> Any:
    """Open a pymssql connection with bounded timeouts (never at import)."""
    if not settings.sql_configured:
        raise SqlUnavailable("SQL_NOT_CONFIGURED", "FOCUS_SQL_HOST/DATABASE/USER/PASSWORD are not all configured")
    try:
        import pymssql  # noqa: PLC0415 - lazy optional driver
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise SqlUnavailable("DRIVER_MISSING", "pymssql is not installed") from exc
    assert settings.sql_password is not None
    try:
        conn = pymssql.connect(
            server=settings.sql_host,
            user=settings.sql_user,
            password=settings.sql_password.reveal(),
            database=settings.sql_database,
            login_timeout=settings.sql_login_timeout_seconds,
            timeout=settings.sql_read_timeout_seconds,
            appname="FOCUS-FROM-NOTHING",
            autocommit=autocommit,
        )
    except Exception as exc:  # pymssql raises driver-specific errors
        raise SqlUnavailable("SQL_CONNECT_FAILED", f"could not connect to {settings.sql_host}/{settings.sql_database}: {type(exc).__name__}") from None
    return conn


@dataclass(frozen=True, slots=True)
class QueryResult:
    name: str
    query_sha: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


class SqlReader:
    """Read-only, allowlisted, parameterized reader."""

    def __init__(self, settings: Settings, connect_fn: Callable[[Settings], Any] = connect, batch_size: int = 5000):
        self.settings = settings
        self._connect = connect_fn
        self.batch_size = batch_size

    @staticmethod
    def check_read_only(sql: str) -> None:
        if not _SELECT_ONLY.match(sql) or _FORBIDDEN.search(sql):
            raise SqlUnavailable("NOT_READ_ONLY", "the reader principal only executes SELECT/WITH statements")
        for name in re.findall(r"\bWEB_APP\.[A-Za-z0-9_]+", sql):
            if name not in ALLOWED_OBJECTS:
                raise SqlUnavailable("OBJECT_NOT_ALLOWLISTED", f"{name} is not an allowlisted object")

    def fetch(self, name: str, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        self.check_read_only(sql)
        conn = self._connect(self.settings)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params) if params else None)
            columns = tuple(col[0] for col in (cursor.description or ()))
            rows: list[tuple[Any, ...]] = []
            while True:
                batch = cursor.fetchmany(self.batch_size)
                if not batch:
                    break
                rows.extend(tuple(r) for r in batch)
            return QueryResult(name, query_hash(sql), columns, tuple(rows))
        finally:
            conn.close()

    def iterate(self, name: str, sql: str, params: Sequence[Any] = ()) -> Iterator[tuple[Any, ...]]:
        self.check_read_only(sql)
        conn = self._connect(self.settings)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params) if params else None)
            while True:
                batch = cursor.fetchmany(self.batch_size)
                if not batch:
                    break
                for row in batch:
                    yield tuple(row)
        finally:
            conn.close()
