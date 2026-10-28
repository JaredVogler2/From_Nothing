"""P03: exact S04 contracts, schema drift, complete WIP census and lossless identity (A03, A04, A15, A46)."""
from __future__ import annotations

import copy
import importlib.util
from collections import Counter

import pytest

from focus.data.datasets import DATASETS
from focus.data.discovery import CONTRACTS, catalog_rows_from_contracts, compare_catalog
from focus.data.fixture_builder import build_scale_fixture
from focus.data.fixture_reader import FixtureSourceReader
from focus.domain import Disposition
from tests.conftest import acquire_demo, job_id


def test_s04_contracts_embedded_exactly() -> None:
    assert len(CONTRACTS) == 28
    master = CONTRACTS["WEB_APP.FOCUS_MASTER_TASK_LIST"]
    assert master.primary_key == ("SHOP_ORDER_IDENTIFIER", "LINE_NUMBER")
    assert master.column("LOAD_TIMESTAMP").render() == "LOAD_TIMESTAMP datetime2(3)!"
    assert master.column("FIRST_AVAILABLE").render() == "FIRST_AVAILABLE datetime2(0)?"
    assert CONTRACTS["WEB_APP.FOCUS_PNET_DAG"].primary_key == ()
    assert CONTRACTS["WEB_APP.FOCUS_CDAY_OP_PLAN"].column("DAY_STATUS").render() == "DAY_STATUS nvarchar(15)?"
    term = CONTRACTS["WEB_APP.FOCUS_SOL_TESTING2_EconomicsTerm"]
    assert term.column("ContractIsNull").computed and term.column("DailyRate").render() == "DailyRate decimal(19,4)!"


def test_schema_drift_names_exact_object_and_column() -> None:
    rows = catalog_rows_from_contracts()
    assert compare_catalog(rows) == []
    drifted = copy.deepcopy(rows)
    for r in drifted:
        if r["table_name"] == "FOCUS_FLOW_BUDGET" and r["column_name"] == "TOTAL_MECHANICS":
            r["type_name"] = "smallint"
        if r["table_name"] == "FOCUS_CS_WINDOWS" and r["column_name"] == "LOAD_OFFSET":
            r["is_nullable"] = False
    drifted = [r for r in drifted if not (r["table_name"] == "FOCUS_ETRACS_ACTIVE" and r["column_name"] == "USED_ON")]
    findings = {(f.code, f.object, f.column) for f in compare_catalog(drifted)}
    assert ("SCHEMA_DRIFT", "WEB_APP.FOCUS_FLOW_BUDGET", "TOTAL_MECHANICS") in findings
    assert ("SCHEMA_DRIFT", "WEB_APP.FOCUS_CS_WINDOWS", "LOAD_OFFSET") in findings
    assert ("COLUMN_MISSING", "WEB_APP.FOCUS_ETRACS_ACTIVE", "USED_ON") in findings


def test_projections_are_explicit_and_queries_match_generator(app_root) -> None:
    spec = importlib.util.spec_from_file_location("wsq", app_root / "tools" / "write_source_queries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative, text in module.expected_files().items():
        assert (app_root / relative).read_text(encoding="utf-8") == text, relative
        assert "SELECT *" not in text
    master_sql = (app_root / "queries/source_inputs/master_task_list.sql").read_text()
    assert "SHOP_ORDER_STATUS <> 'COMPLETE'" in master_sql and "TOP 50" not in master_sql.upper()
    assert "WHERE IS_ACTIVE=1" in (app_root / "queries/source_inputs/cs_windows.sql").read_text()


def test_a03_fifty_one_aircraft_all_reconciled_no_cap(services_factory, app_root) -> None:
    scale = {name: ds.to_json() for name, ds in build_scale_fixture(51).items()}
    services = services_factory()
    reader = FixtureSourceReader(app_root / "fixtures" / "inputs" / "manufacturing_demo", overrides=scale)
    outcome = acquire_demo(services, key="scale51", reader=reader)
    assert outcome.status == "COMPLETE", outcome.rejection
    from focus.data.acquisition import load_snapshot

    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    lines = sorted(c.line for c in snap.census)
    assert len(lines) == 51 and lines[0] == 2001 and lines[-1] == 2051
    observed = Counter(int(r["LINE_NUMBER"]) for r in scale["master_task_list"]["rows"])
    for c in snap.census:
        assert c.jobs_total == observed[c.line]
        assert c.admitted + c.blocked + c.quarantined + c.excluded + c.verified_complete == c.jobs_total
    blocked_line = next(c for c in snap.census if c.line == 2001)
    open_jobs = blocked_line.jobs_total - blocked_line.verified_complete
    assert blocked_line.blocked == open_jobs and blocked_line.excluded == 0       # entirely blocked, still in census
    missing_std = [d for d in snap.dispositions if next(j for j in snap.jobs if j.id == d.job).line == 2002 and d.disposition is Disposition.BLOCKED]
    assert missing_std and all(any("STANDARD_MISSING" in r for r in d.reasons) for d in missing_std)
    assert all(d.disposition is not Disposition.EXCLUDED for d in snap.dispositions)


def test_a04_same_soi_two_lines_lossless_through_graph_and_durations(demo_snapshot) -> None:
    snap = demo_snapshot
    fgi_1301, fgi_1302 = job_id(snap, 1301, "FGI"), job_id(snap, 1302, "FGI")
    assert fgi_1301 != fgi_1302
    durations = {d.job: d for d in snap.durations}
    assert durations[fgi_1301].elapsed_minutes == 30 and durations[fgi_1302].elapsed_minutes == 40   # flow differs by line
    phases_1301 = {p.id for p in snap.phases if p.job == fgi_1301}
    phases_1302 = {p.id for p in snap.phases if p.job == fgi_1302}
    assert phases_1301 and phases_1302 and not (phases_1301 & phases_1302)
    a2, b2 = job_id(snap, 1301, "A2"), job_id(snap, 1302, "B2")
    rel = {p.job: p.id for p in snap.phases if p.kind.value == "RELEASE"}
    starts = {p.job: p.id for p in snap.phases if p.kind.value == "WORK" and p.ordinal == 0}
    edges = {(e.pred_phase, e.succ_phase) for e in snap.edges if e.basis == "SOURCE_PNET"}
    assert (rel[a2], starts[fgi_1301]) in edges and (rel[b2], starts[fgi_1302]) in edges
    assert (rel[a2], starts[fgi_1302]) not in edges            # template edge only where both endpoints exist
    for job in snap.jobs:
        assert job.key.revision is None                       # source omission retained, not fabricated


def test_a15_flow_fields_preserved_and_total_drives_demand(demo_snapshot) -> None:
    snap = demo_snapshot
    root5 = next(j for j in snap.jobs if j.line == 1305 and j.key.native_id == "ROOT5")
    assert root5.raw_budget.total_mechanics == 3 and root5.raw_budget.primary_mechanic_count == 2 and root5.raw_budget.assist_count == 1
    est = next(d for d in snap.durations if d.job == root5.id)
    assert est.crew_size == 3                                   # TOTAL, not PRIMARY
    tst6b = next(d for d in snap.dispositions if d.job == job_id(snap, 1306, "TST6B"))
    assert tst6b.disposition is Disposition.BLOCKED and any("FLOW_CREW_INVALID" in r for r in tst6b.reasons)


def test_a46_assumed_terms_and_unknown_economics_are_labeled(demo_snapshot) -> None:
    econ = {e.line: e for e in demo_snapshot.economics}
    assert econ[1301].authority == "ASSUMED" and econ[1301].daily_rate_cents == 10000
    assert any("ASSUMED_PLANNING_TERM" in r for r in econ[1301].reasons)
    assert any("CAP_FIELD_NOT_APPLIED" in r for r in econ[1301].reasons) and econ[1301].cap_cents is None
    assert econ[1304].selector_type == "AIRCRAFT_OVERRIDE" and econ[1304].daily_rate_cents == 2500
    assert econ[1305].selector_type == "CUSTOMER_MODEL"
    assert econ[1306].status == "UNKNOWN" and econ[1306].daily_rate_cents is None     # unknown, never zero
    assert econ[1301].original_sale_cents is None and econ[1301].accrued_floor_cents is None


def test_census_status_preserved_and_unknown_status_quarantined(services_factory, app_root) -> None:
    import json

    inputs = app_root / "fixtures" / "inputs" / "manufacturing_demo"
    master = json.loads((inputs / "master_task_list.json").read_text())
    for row in master["rows"]:
        if row["SHOP_ORDER_IDENTIFIER"] == "PRE4":
            row["SHOP_ORDER_STATUS"] = "MYSTERY"
    services = services_factory()
    reader = FixtureSourceReader(inputs, overrides={"master_task_list": master})
    outcome = acquire_demo(services, key="mystery", reader=reader)
    from focus.data.acquisition import load_snapshot

    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    pre4 = next(d for d in snap.dispositions if d.job == job_id(snap, 1304, "PRE4"))
    assert pre4.disposition is Disposition.QUARANTINED and "UNKNOWN_STATUS" in pre4.reasons[0]
    root4 = next(d for d in snap.dispositions if d.job == job_id(snap, 1304, "ROOT4"))
    assert root4.disposition is Disposition.BLOCKED and any("PREDECESSOR_NOT_SCHEDULABLE" in r for r in root4.reasons)
    c1304 = next(c for c in snap.census if c.line == 1304)
    assert c1304.status_counts == {"IN WORK": 1, "MYSTERY": 1}
