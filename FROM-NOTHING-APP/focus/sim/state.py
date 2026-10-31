"""The simulated source system: every dataset's rows as loaded so far, captured at a cutoff like the fixture reader.

Rows change only through ``upsert`` and ``remove`` (keyed by each dataset's natural key), so each shift's changes are
known exactly and written to the pulled tables as deltas. A capture applies the same rules as
``FixtureSourceReader.capture``: the explicit column projection, rows loaded after the cutoff not yet visible, and the
producer receipt deciding between ROWS, CERTIFIED_EMPTY and EMPTY_UNCERTIFIED. A dataset's lineage hash is computed
once per version, not on every capture.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from ..codec import sha256_hex, streamed_content_hash
from ..data.datasets import DATASETS, DatasetSpec
from ..data.fixture_reader import Capture, _cutoff_value, schema_sha_for

KEYS: dict[str, tuple[str, ...]] = {
    "master_task_list": ("LINE_NUMBER", "SHOP_ORDER_IDENTIFIER"),
    "flow_budget": ("LINE_NUMBER", "JOB_NUMBER"),
    "soi_cert_requirements": ("LINE_NUMBER", "SOI_ID", "CERT_CODE"),
    "neo_graph_rework": ("LINE_NUMBER", "CHILD_SOI"),
    "rework_classification": ("LINE_NUMBER", "CHILD_SOI"),
    "etracs_active": ("RECORD_ID",),
    "etracs_history": ("RECORD_ID",),
    "attendance_shift": ("PRODUCTION_DAY", "SHIFT_NUMBER", "BEMS_ID"),
}


class SourceState:
    def __init__(self, datasets: Mapping[str, Any]):
        self.rows: dict[str, dict[tuple, dict[str, Any]]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.version: dict[str, int] = {}
        self._lineage: dict[str, tuple[int, str, tuple]] = {}
        self.changes: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.next_rework: dict[int, int] = {}                     # line -> next free rework number
        for name in DATASETS:
            ds = datasets[name]
            self.receipts[name] = dict(ds.receipt)
            key = KEYS.get(name)
            table: dict[tuple, dict[str, Any]] = {}
            for n, row in enumerate(ds.rows):
                table[tuple(row[c] for c in key) if key else (n,)] = row
            self.rows[name] = table
            self.version[name] = 0
        for row in self.rows["master_task_list"].values():
            if row.get("IS_REWORK") and str(row["SHOP_ORDER_IDENTIFIER"]).startswith("R"):
                n = int(str(row["SHOP_ORDER_IDENTIFIER"])[1:]) + 1
                self.next_rework[row["LINE_NUMBER"]] = max(self.next_rework.get(row["LINE_NUMBER"], 0), n)

    def key_of(self, name: str, row: Mapping[str, Any]) -> tuple:
        key = KEYS.get(name)
        if key is None:
            raise KeyError(f"{name} has no natural key; it cannot change during a simulation")
        return tuple(row[c] for c in key)

    def upsert(self, name: str, row: dict[str, Any]) -> None:
        self.rows[name][self.key_of(name, row)] = row
        self.version[name] += 1
        self.changes.setdefault(name, []).append(("upsert", row))

    def remove(self, name: str, row: Mapping[str, Any]) -> None:
        found = self.rows[name].pop(self.key_of(name, row), None)
        if found is not None:
            self.version[name] += 1
            self.changes.setdefault(name, []).append(("delete", found))

    def take_changes(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        out, self.changes = self.changes, {}
        return out

    def get(self, name: str, *key: Any) -> dict[str, Any] | None:
        return self.rows[name].get(tuple(key))

    def all(self, name: str):
        return self.rows[name].values()

    # ------------------------------------------------------------------ capture (FixtureSourceReader rules)
    def capture(self, spec: DatasetSpec, cutoff: datetime, tz_map: Mapping[str, str], captured_at: datetime) -> Capture:
        tz_name = tz_map.get(spec.name, tz_map.get("*", "America/New_York"))
        rows = []
        all_rows = list(self.rows[spec.name].values())
        for row in all_rows:
            if spec.cutoff_column:
                at = _cutoff_value(spec, row, tz_name)
                if at is not None and at > cutoff:
                    continue
            rows.append(row)
        receipt = self.receipts[spec.name]
        if rows:
            status = "ROWS"
        elif receipt.get("certified_empty") or (spec.cutoff_column and all_rows):
            status = "CERTIFIED_EMPTY"
        else:
            status = "EMPTY_UNCERTIFIED"
        query_sha = sha256_hex(f"simulation:{spec.name}:{','.join(spec.columns)}")
        return Capture(spec.name, status, tuple(rows), spec.columns, query_sha, schema_sha_for(spec), dict(receipt), captured_at)

    def lineage(self, capture: Capture) -> str:
        """The capture's content hash, computed once per dataset version and visible row count."""
        name = capture.name
        cached = self._lineage.get(name)
        stamp = (self.version[name], len(capture.rows))
        if cached is not None and (cached[0], cached[2]) == (stamp[0], (stamp[1],)):
            return cached[1]
        digest = streamed_content_hash({"columns": list(capture.columns), "rows": list(capture.rows)})
        self._lineage[name] = (stamp[0], digest, (stamp[1],))
        return digest
