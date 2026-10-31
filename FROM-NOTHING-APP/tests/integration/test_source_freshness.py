"""B7: admission checks source freshness and capture skew; stale or skewed sources block executable plans only (S05 step 5)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from focus.data.acquisition import load_snapshot
from focus.data.fixture_reader import FixtureSourceReader
from focus.data.freshness import freshness_gaps
from focus.data.sourcetime import TimezonePolicy
from focus.domain import Purpose
from tests.conftest import acquire_demo

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
CUTOFF = datetime(2026, 10, 26, 9, 45, tzinfo=timezone.utc)
NY = TimezonePolicy({"*": "America/New_York"}, "TEST")


def test_freshness_rules(policy) -> None:
    fresh = ({"LOAD_TIMESTAMP": "2026-10-25T17:30:00.000"},)                     # 12.25 h before the cutoff in New York time
    stale = ({"LOAD_TIMESTAMP": "2026-10-23T17:30:00.000"}, {"LOAD_TIMESTAMP": None})
    gaps = freshness_gaps(datasets={"master_task_list": fresh, "etracs_active": stale, "pnet_dag": ({"LOAD_TIMESTAMP": "garbage"},)},
                          receipts=None, captured=None, tzp=NY, policy=policy, cutoff=CUTOFF)
    assert [(g.code, g.scope, g.capability_effect) for g in gaps] == [("SOURCE_STALE", "etracs_active", "BLOCKS_EXECUTABLE"),
                                                                       ("SOURCE_CLOCK_UNKNOWN", "pnet_dag", "BLOCKS_EXECUTABLE")]
    assert "60.2 h before the cutoff (limit 36 h)" in gaps[0].detail
    # a producer's loaded_at outranks row clocks; a naive clock without a declared zone is unknown, never fresh
    loaded = freshness_gaps(datasets={"etracs_active": stale}, receipts={"etracs_active": {"loaded_at": "2026-10-26T09:00:00Z"}}, captured=None,
                            tzp=NY, policy=policy, cutoff=CUTOFF)
    assert loaded == []
    unzoned = freshness_gaps(datasets={"master_task_list": fresh}, receipts=None, captured=None, tzp=TimezonePolicy({}, "TEST"), policy=policy,
                             cutoff=CUTOFF)
    assert [g.code for g in unzoned] == ["SOURCE_CLOCK_UNKNOWN"]
    # capture skew across one run's datasets
    skewed = freshness_gaps(datasets={}, receipts=None, captured={"a": CUTOFF, "b": CUTOFF + timedelta(minutes=31)}, tzp=NY, policy=policy,
                            cutoff=CUTOFF)
    assert [g.code for g in skewed] == ["CAPTURE_SKEW"]
    assert freshness_gaps(datasets={}, receipts=None, captured={"a": CUTOFF, "b": CUTOFF + timedelta(minutes=30)}, tzp=NY, policy=policy,
                          cutoff=CUTOFF) == []


def test_a_stale_task_list_blocks_the_executable_plan_and_not_the_forecast(services_factory) -> None:
    from focus.engine.max.optimizer.orchestrator import schedule_snapshot

    data = {"master_task_list": json.loads((INPUTS / "master_task_list.json").read_text())}
    for r in data["master_task_list"]["rows"]:
        r["LOAD_TIMESTAMP"] = "2026-10-23T17:30:00.000"                        # the nightly load stopped three days ago
    services = services_factory()
    outcome = acquire_demo(services, key="stale-mtl", reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection                     # admitted: the forecast may still use it
    snapshot = load_snapshot(services.repositories, outcome.snapshot_id)
    assert [(g.code, g.scope) for g in snapshot.gaps if g.code == "SOURCE_STALE"] == [("SOURCE_STALE", "master_task_list")]
    executable, run = schedule_snapshot(services, snapshot, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=0)
    found = [(v.check, v.code, v.entity) for v in run.validation.violations if v.code == "SOURCE_STALE"]
    assert found == [("E_LINEAGE", "SOURCE_STALE", "master_task_list")] and not run.validation.valid
    forecast, frun = schedule_snapshot(services, snapshot, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=0)
    assert not [v for v in frun.validation.violations if v.code == "SOURCE_STALE"]
