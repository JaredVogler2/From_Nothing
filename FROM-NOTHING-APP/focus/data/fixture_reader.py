"""Source readers producing raw dataset captures (S05).

* :class:`FixtureSourceReader` reads static ``fixtures/inputs/<scenario>/*.json``.
  It emulates capture at a cutoff: rows whose declared cutoff column (a load or
  observation time) is later than the cutoff are not visible yet.
* :class:`LiveSourceReader` runs the explicit S05 projections from
  ``queries/source_inputs/*.sql`` through the SELECT-only reader. Supplemental
  datasets without an installed live producer are reported ``NOT_PROBED``.
  That is a precise readiness gap, never an empty success.

Both return :class:`Capture` objects with raw, JSON-native values. Naive
``datetime2`` text stays naive; its timezone comes from the explicit source
timezone policy at admission time. The exception is a column named ``..._UTC``
or ``...Utc``: the database holds UTC there, so the live reader writes the
value with its offset.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from ..codec import CodecError, content_hash, parse_utc, sha256_hex, strict_loads
from .datasets import DATASETS, DatasetSpec
from .discovery import CONTRACTS

UTC = timezone.utc

CAPTURE_STATUSES = ("ROWS", "CERTIFIED_EMPTY", "EMPTY_UNCERTIFIED", "FAILED", "NOT_PROBED")


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Capture:
    name: str
    status: str
    rows: tuple[dict[str, Any], ...]
    columns: tuple[str, ...]
    query_sha: str
    schema_sha: str
    receipt: dict[str, Any]
    captured_at: datetime
    error: str | None = None

    @property
    def content_sha(self) -> str:
        return content_hash({"columns": list(self.columns), "rows": list(self.rows)})


def schema_sha_for(spec: DatasetSpec) -> str:
    if spec.source_object:
        contract = CONTRACTS[spec.source_object]
        cols = [contract.column(c).render() for c in spec.columns]
        return content_hash({"object": spec.source_object, "columns": cols})
    return content_hash({"supplemental": spec.name, "columns": list(spec.columns)})


def _cutoff_value(spec: DatasetSpec, row: Mapping[str, Any], tz_name: str) -> datetime | None:
    raw = row.get(spec.cutoff_column or "")
    if raw in (None, ""):
        return None
    text = str(raw)
    if spec.cutoff_column and spec.cutoff_column.endswith("_UTC"):
        return parse_utc(text)
    naive = datetime.fromisoformat(text)
    return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


class FixtureSourceReader:
    kind = "fixture"

    def __init__(self, inputs_dir: Path, fault: Callable[[str], None] | None = None,
                 overrides: Mapping[str, Mapping[str, Any]] | None = None):
        self.inputs_dir = Path(inputs_dir)
        self._fault = fault or (lambda name: None)
        self._overrides = dict(overrides or {})

    def _load(self, name: str) -> dict[str, Any]:
        if name in self._overrides:
            return dict(self._overrides[name])
        path = self.inputs_dir / f"{name}.json"
        try:
            return strict_loads(path.read_bytes())
        except FileNotFoundError:
            raise CaptureError("DATASET_MISSING", f"fixture input {path.name} not found") from None
        except CodecError as exc:
            raise CaptureError("DATASET_INVALID", f"{path.name}: {exc.message}") from None

    def capture(self, spec: DatasetSpec, cutoff: datetime, tz_map: Mapping[str, str], captured_at: datetime) -> Capture:
        self._fault(spec.name)
        data = self._load(spec.name)
        if data.get("dataset") != spec.name or data.get("schema_version") != 1:
            raise CaptureError("DATASET_INVALID", f"{spec.name}: header mismatch")
        columns = tuple(data.get("columns", ()))
        if columns != spec.columns:
            raise CaptureError("SCHEMA_DRIFT", f"{spec.name}: columns {list(columns)} differ from the explicit projection {list(spec.columns)}")
        tz_name = tz_map.get(spec.name, tz_map.get("*", "America/New_York"))
        rows = []
        for ordinal, row in enumerate(data.get("rows", [])):
            if set(row) != set(spec.columns):
                raise CaptureError("SCHEMA_DRIFT", f"{spec.name}: row {ordinal} keys differ from the projection")
            if spec.cutoff_column:
                at = _cutoff_value(spec, row, tz_name)
                if at is not None and at > cutoff:
                    continue
            rows.append(row)
        receipt = dict(data.get("receipt", {}))
        if not receipt.get("complete"):
            raise CaptureError("RECEIPT_INCOMPLETE", f"{spec.name}: producer receipt is not complete")
        if rows:
            status = "ROWS"
        elif receipt.get("certified_empty") or (spec.cutoff_column and data.get("rows")):
            # rows exist but none visible at this cutoff: the producer is complete, nothing loaded yet
            status = "CERTIFIED_EMPTY"
        else:
            status = "EMPTY_UNCERTIFIED"
        query_sha = sha256_hex(f"fixture:{self.inputs_dir.name}:{spec.name}:{','.join(spec.columns)}")
        return Capture(spec.name, status, tuple(rows), columns, query_sha, schema_sha_for(spec), receipt, captured_at)

    def contract_coverage(self) -> dict[str, Any]:
        present, missing, drift = [], [], []
        for name, spec in DATASETS.items():
            path = self.inputs_dir / f"{name}.json"
            if not path.exists():
                missing.append(name)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if tuple(data.get("columns", ())) != spec.columns:
                drift.append(name)
            else:
                present.append(name)
        return {"present": len(present), "missing": missing, "projection_drift": drift, "inputs_dir": str(self.inputs_dir.name)}


# ---------------------------------------------------------------------------
# live reader
# ---------------------------------------------------------------------------

# Supplemental datasets with an installed FOCUS_FN producer (S06). Others have no live producer yet. Each read is bound
# to this scope's source head (its one parameter is the scope key), never to the newest head of any scope.
LIVE_SUPPLEMENTAL_QUERIES: dict[str, str] = {
    "team_inference": "queries/workforce/read_team_inference.sql",
    "clock_observations": "queries/durations/read_clock_observations.sql",
}


def is_utc_column(column: str) -> bool:
    """A column named ``..._UTC`` or ``...Utc``: the database holds UTC there (owner decision, 2026-10-31)."""
    return column.upper().endswith("_UTC") or column.endswith("Utc")


def raw_value(value: Any, utc: bool = False) -> Any:
    """Convert driver values to raw JSON-native source text without changing meaning. A naive ``datetime2`` from a UTC
    column is written with its offset, so it reads back as the instant the database means; other naive values stay naive
    for the dataset's source timezone policy."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None and utc:
            value = value.replace(tzinfo=UTC)
        if value.tzinfo is None:
            return value.isoformat(timespec="microseconds" if value.microsecond else "seconds")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    raise CaptureError("UNSUPPORTED_SOURCE_TYPE", f"unsupported driver value {type(value).__name__}")


class LiveSourceReader:
    kind = "live"

    def __init__(self, app_root: Path, sql_reader: Any, scope_key: str = ""):
        self.app_root = Path(app_root)
        self.sql = sql_reader
        self.scope_key = scope_key

    def _query(self, relative: str) -> str:
        return (self.app_root / relative).read_text(encoding="utf-8")

    def capture(self, spec: DatasetSpec, cutoff: datetime, tz_map: Mapping[str, str], captured_at: datetime) -> Capture:
        params: tuple[Any, ...] = ()
        if spec.kind == "SUPPLEMENTAL":
            query_file = LIVE_SUPPLEMENTAL_QUERIES.get(spec.name)
            if query_file is None:
                return Capture(spec.name, "NOT_PROBED", (), spec.columns, "", schema_sha_for(spec),
                               {"complete": False, "reason": "no installed live producer for this supplemental input"}, captured_at)
            if not self.scope_key:
                raise CaptureError("SCOPE_KEY_MISSING", f"{spec.name}: a supplemental read is bound to one scope's source head")
            params = (self.scope_key,)
        else:
            query_file = spec.query_file
        sql = self._query(query_file)
        try:
            result = self.sql.fetch(spec.name, sql, params)
        except Exception as exc:   # the driver's own error types (connection, login, missing object, denied access): the capture failed
            raise CaptureError("SOURCE_QUERY_FAILED", f"{spec.name}: {type(exc).__name__}: {exc}") from exc
        columns = tuple(result.columns)
        if columns != spec.columns:
            raise CaptureError("SCHEMA_DRIFT", f"{spec.name}: live columns {list(columns)} differ from {list(spec.columns)}")
        utc = {c: is_utc_column(c) for c in columns}
        rows = tuple({c: raw_value(v, utc[c]) for c, v in zip(columns, r)} for r in result.rows)
        status = "ROWS" if rows else "EMPTY_UNCERTIFIED"
        receipt = {"complete": True, "certified_empty": False, "producer": f"sql:{spec.source_object or spec.name}",
                   "query": query_file, "row_count": len(rows)}
        return Capture(spec.name, status, rows, columns, result.query_sha, schema_sha_for(spec), receipt, captured_at)
