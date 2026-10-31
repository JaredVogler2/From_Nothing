"""P05: SQL-owned supplemental reads are bound to this scope's source head, never the newest head of any scope."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from focus.data.datasets import DATASETS
from focus.data.fixture_reader import LIVE_SUPPLEMENTAL_QUERIES, CaptureError, LiveSourceReader
from focus.data.sql import QueryResult

AT = datetime(2026, 10, 26, 9, 45, tzinfo=timezone.utc)


class FakeSql:
    """Records every query and its bound parameters; returns no rows with the dataset's own columns."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple]] = []

    def fetch(self, name: str, sql: str, params=()) -> QueryResult:  # type: ignore[no-untyped-def]
        self.calls.append((name, sql, tuple(params)))
        return QueryResult(name, "sha", DATASETS[name].columns, ())


@pytest.mark.parametrize("name", sorted(LIVE_SUPPLEMENTAL_QUERIES))
def test_supplemental_reads_bind_the_scope_head(app_root: Path, name: str) -> None:
    sql = FakeSql()
    LiveSourceReader(app_root, sql, "DEMO|787|ALL_WIP").capture(DATASETS[name], AT, {}, AT)
    (_, text, params), = sql.calls
    assert params == ("DEMO|787|ALL_WIP",)
    assert "h.ScopeKey = %s" in text and "ORDER BY h.UpdatedAtUtc" not in text and text.count("%s") == 1


def test_supplemental_read_without_a_scope_is_refused(app_root: Path) -> None:
    with pytest.raises(CaptureError) as exc:
        LiveSourceReader(app_root, FakeSql()).capture(DATASETS["team_inference"], AT, {}, AT)
    assert exc.value.code == "SCOPE_KEY_MISSING"


def test_source_reads_take_no_parameters(app_root: Path) -> None:
    sql = FakeSql()
    LiveSourceReader(app_root, sql, "DEMO|787|ALL_WIP").capture(DATASETS["etracs_active"], AT, {}, AT)
    assert sql.calls[0][2] == ()


def test_naive_database_values_in_utc_columns_are_read_as_utc(app_root: Path) -> None:
    """Database columns named ..._UTC or ...Utc hold UTC (owner decision): the driver's naive datetime2 keeps its instant."""
    naive = datetime(2026, 10, 26, 8, 30)

    class OneRow(FakeSql):
        def fetch(self, name, sql, params=()):  # type: ignore[no-untyped-def]
            columns = DATASETS[name].columns
            return QueryResult(name, "sha", columns, (tuple(naive if c.upper().endswith(("_UTC", "TIMESTAMP")) or c.endswith("Utc") else 1
                                                            for c in columns),))

    inference = LiveSourceReader(app_root, OneRow(), "DEMO|787|ALL_WIP").capture(DATASETS["team_inference"], AT, {}, AT)
    assert inference.rows[0]["WINDOW_START_UTC"] == "2026-10-26T08:30:00+00:00"            # UTC by its name
    term = LiveSourceReader(app_root, OneRow()).capture(DATASETS["econ_term"], AT, {}, AT)
    assert term.rows[0]["EffectiveFromUtc"] == "2026-10-26T08:30:00+00:00"
    actual = LiveSourceReader(app_root, OneRow()).capture(DATASETS["actual_labor"], AT, {}, AT)
    assert actual.rows[0]["LOAD_TIMESTAMP"] == "2026-10-26T08:30:00"                         # other naive values keep the source zone policy
