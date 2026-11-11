"""A truncated line calendar is reported when the data is admitted, and the plan shows why it matters (DEC-28)."""
from __future__ import annotations

import json
from pathlib import Path

from focus.data.acquisition import load_snapshot
from focus.data.fixture_reader import FixtureSourceReader
from focus.domain import Purpose
from focus.economics.evaluator import objective_status
from focus.engine.max.optimizer.orchestrator import schedule
from tests.conftest import acquire_demo

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _acquire(services_factory, key: str, last_day: dict[int, str]):
    data = {"cday_op_plan": json.loads((INPUTS / "cday_op_plan.json").read_text())}
    data["cday_op_plan"]["rows"] = [r for r in data["cday_op_plan"]["rows"]
                                    if r["LINE_NUMBER"] not in last_day or r["CALENDAR_DATE"] <= last_day[r["LINE_NUMBER"]]]
    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection
    return services, outcome, load_snapshot(services.repositories, outcome.snapshot_id)


def test_the_demo_calendars_reach_past_their_due_dates(demo) -> None:
    _, _, snapshot = demo
    assert not [g for g in snapshot.gaps if g.code in ("CALENDAR_NO_DAYS_LEFT", "CALENDAR_ENDS_BEFORE_DUE")]


def test_a_truncated_calendar_is_a_gap_at_admission_and_the_aircraft_can_never_be_planned_late(services_factory) -> None:
    # line 1304's calendar now stops on 29 October, before its due date; line 1305's stopped before the data cut
    services, outcome, snapshot = _acquire(services_factory, "calendar-coverage", {1304: "2026-10-29", 1305: "2026-10-25"})
    found = {(g.code, g.scope): g for g in snapshot.gaps if g.code in ("CALENDAR_NO_DAYS_LEFT", "CALENDAR_ENDS_BEFORE_DUE")}
    assert set(found) == {("CALENDAR_ENDS_BEFORE_DUE", "line 1304"), ("CALENDAR_NO_DAYS_LEFT", "line 1305")}
    assert found[("CALENDAR_NO_DAYS_LEFT", "line 1305")].capability_effect == "BLOCKS_ALL"
    assert found[("CALENDAR_ENDS_BEFORE_DUE", "line 1304")].capability_effect == "INFO"
    plan, _ = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=10, max_moves=0)
    status = objective_status(plan.costs, plan.aircraft_completion, plan.job_dispositions, plan.created_at)
    assert 1305 in status["aircraft_without_finish"]                        # nothing on it could be planned
    cost = {c.line: c for c in plan.costs.lines}[1304]
    assert cost.status == "unknown" or not cost.delay_minutes               # on time or unknown: this plan cannot show it late
