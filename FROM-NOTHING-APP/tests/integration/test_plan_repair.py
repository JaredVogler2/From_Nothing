"""Plan repair (speed item 3): keep the accepted plan where it holds, re-place only what changed, fall back to the full solve.

A repair re-plans the whole horizon in one decode, so delivery dates and penalties still follow every change; the
FINAL validation gates it like any plan. ``auto`` (the pipeline default) repairs between daily full solves, and each
scheduled full solve records how far the repair would have landed (the drift check).
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.contracts.planning import CandidatePlan, Violation
from focus.data.acquisition import snapshot_for_run
from focus.data.fixture_reader import FixtureSourceReader
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import PersonKey, PhaseKind, Purpose
from focus.engine.commitments import Defense, import_fixture_incumbent
from focus.engine.max.optimizer.orchestrator import CANDIDATES, decision_digest, planning_mode, schedule, schedule_snapshot
from focus.engine.model import EngineContext
from focus.runtime import build_services
from focus.validation.duty import reconstruct
from tests.conftest import acquire_demo, job_id

UTC = timezone.utc
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _local(services, day: date, hhmm: str) -> datetime:
    tz = ZoneInfo(services.policy.str("shift.timezone"))
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz).astimezone(UTC)


def _work(plan: CandidatePlan, job: str):
    return [a for a in plan.assignments if a.job == job and a.kind is PhaseKind.WORK]


def _crew(assignment) -> set[str]:
    return {r.person for r in assignment.crew}


def _completion(plan: CandidatePlan, line: int):
    return next(c.completion_at for c in plan.aircraft_completion if c.line == line)


@pytest.fixture()
def services_with(tmp_path, app_root, clean_env):
    """Fixture services under a given policy, so its snapshots are sealed with that policy."""
    def make(policy):
        settings = Settings.from_env({}, app_root=app_root)
        clock = FixedClock(FIXTURE_CUTOFF)
        repos = Repositories(FixtureStore(tmp_path / "runtime"), mode=settings.mode, scope_key=settings.scope_key, clock=clock)
        return build_services(settings, repositories=repos, clock=clock, policy=policy)
    return make


def late_part_on_b2(data) -> None:
    data["etracs_active"]["rows"].append({"ETRAC_CREATED_DT": "2026-10-24", "ETRAC_ECD": "2026-10-29", "ITEM_NUMBER": "DEMO-P-0010",
                                          "LINE_NUMBER": 1302, "LOAD_TIMESTAMP": "2026-10-25T17:30:00.000", "MINOR_MODEL_IDENT": "9",
                                          "REASON_CODE": "LP", "RECORD_ID": 50510, "TEAM": "PROD-A", "USED_ON": "B2"})


def test_repair_keeps_the_incumbent_where_it_holds_and_runs_no_search(demo) -> None:
    services, outcome, snapshot = demo
    import_fixture_incumbent(services)          # B2 is committed on day 3: outside the defended horizon (days 0-2)
    b2 = job_id(snapshot, 1302, "B2")
    kept_slot = _local(services, date(2026, 10, 29), "06:15")
    full, frun = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    assert min(a.start for a in _work(full, b2)) < kept_slot and not frun.planning      # the full solve repacks day 3 freely
    plan, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, mode="repair")
    assert run.planning == {"requested": "repair", "ran": "repair", "reason": "repair requested", "hints": 1, "kept": 1, "rehomed": 0,
                            "deferred": 0}
    (work,) = _work(plan, b2)
    assert work.start == kept_slot and _crew(work) == {PersonKey(n, "fixture-demo").id for n in ("DEMO-A1-05", "DEMO-A1-07")}
    assert not work.committed                                                       # kept as planned, not a defended commitment
    assert sorted((c.job, c.outcome) for c in plan.commitment_results) == sorted((c.job, c.outcome) for c in full.commitment_results)
    status = plan.algorithm_status
    assert run.stop_reason == "repair" and [s.stage for s in status.stages] == ["repair"] and run.validated == 0
    assert status.baseline_comparison["planning"]["ran"] == "repair" and status.baseline_comparison["repeatable"] is True
    assert "repair_hints" in status.paths_used and not {"neighborhoods", "delay_repair"} & set(status.paths_used)
    assert run.solver["status"] == "DISABLED" and "repair" in run.solver["reason"]
    assert run.validation.valid and run.validation.kind == "FINAL" and not reconstruct(snapshot, services.policy, plan).violations
    again, _ = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, mode="repair")
    assert again.id == plan.id                                                      # repeatable: the same inputs give the same plan


def test_repair_moves_what_no_longer_holds_and_the_delivery_date_follows(services_factory) -> None:
    """A late part removes B2's kept slot: the forecast repair re-places it after the part, and the aircraft's delivery moves."""
    data = {"etracs_active": json.loads((INPUTS / "etracs_active.json").read_text())}
    late_part_on_b2(data)
    services = services_factory()
    import_fixture_incumbent(services, purpose="FORECAST")
    plain = acquire_demo(services, key="repair-plain")
    late = acquire_demo(services, key="repair-late-part", reader=FixtureSourceReader(INPUTS, overrides=data))
    assert plain.status == late.status == "COMPLETE"
    snapshot = snapshot_for_run(services, late.run_id)
    b2 = job_id(snapshot, 1302, "B2")
    before, brun = schedule(services, source_run=plain.run_id, purpose=Purpose.FORECAST, mode="repair")
    assert (brun.planning["kept"], brun.planning["rehomed"]) == (1, 0)
    plan, run = schedule(services, source_run=late.run_id, purpose=Purpose.FORECAST, mode="repair")
    assert (run.planning["ran"], run.planning["hints"], run.planning["kept"], run.planning["rehomed"]) == ("repair", 1, 0, 1)
    (work,) = _work(plan, b2)
    assert work.start >= _local(services, date(2026, 10, 30), "00:00") and work.conditional_reasons   # after the part's floor
    assert _completion(plan, 1302) > _completion(before, 1302)                        # the whole horizon is re-planned: delivery moves
    assert sorted((c.job, c.outcome) for c in plan.commitment_results) == sorted((c.job, c.outcome) for c in before.commitment_results)
    assert run.validation.valid and not reconstruct(snapshot, services.policy, plan).violations
    executable, erun = schedule(services, source_run=late.run_id, purpose=Purpose.EXECUTABLE, mode="repair")
    assert erun.planning["ran"] == "full" and erun.planning["reason"].startswith("REPAIR_NO_INCUMBENT")   # no executable incumbent here


def test_repair_without_an_incumbent_runs_the_full_solve(demo) -> None:
    services, outcome, _ = demo
    plan, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5, mode="repair")
    assert run.planning["requested"] == "repair" and run.planning["ran"] == "full"
    assert run.planning["reason"].startswith("REPAIR_NO_INCUMBENT: NO_INCUMBENT")
    assert plan.algorithm_status.baseline_comparison["planning"] == run.planning and run.stop_reason != "repair"
    full, frun = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    assert decision_digest(plan) == decision_digest(full) and frun.planning == {}       # the same plan as a plain full solve
    assert run.validation.valid


def test_a_repair_that_fails_validation_is_replaced_by_the_full_solve(demo, monkeypatch) -> None:
    import focus.validation.plan as vplan

    services, outcome, _ = demo
    import_fixture_incumbent(services)
    real = vplan.validate
    rejected: list[str] = []

    def strict(snapshot, policy, plan, **kw):                    # the independent validator refuses any repaired plan
        receipt = real(snapshot, policy, plan, **kw)
        if kw.get("kind") == "FINAL" and plan.algorithm_status.baseline_comparison.get("stop_reason") == "repair":
            rejected.append(plan.id)
            return dataclasses.replace(receipt, valid=False, violations=(Violation("V1", "TEST_REJECTION", plan.id, "forced"),))
        return receipt

    monkeypatch.setattr(vplan, "validate", strict)
    plan, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5, mode="repair")
    assert len(rejected) == 1 and plan.id != rejected[0]
    assert run.planning["ran"] == "full" and run.planning["reason"].startswith(f"REPAIR_INVALID: repaired candidate {rejected[0]} failed")
    assert "V1:TEST_REJECTION" in run.planning["reason"] and run.stop_reason != "repair" and run.validation.valid
    stored = services.repositories.get_typed(CANDIDATES, rejected[0], "CandidatePlan", CandidatePlan)
    assert stored is not None and stored.algorithm_status.baseline_comparison["planning"]["ran"] == "repair"   # kept as evidence


def _same_id(policy, **values):
    return policy.with_overrides(values, new_id=policy.id, reason="test planning cadence")


@pytest.mark.parametrize(("values", "ran", "why"), [
    ({}, "full", "the run plans 2026-10-26 S1: the daily full solve runs for shifts [1]"),
    ({"planning.full_solve_shifts": [2], "planning.repair_max_incumbent_age_hours": 24}, "repair", "is 12.0 h old"),
    ({"planning.full_solve_shifts": [2], "planning.repair_max_incumbent_age_hours": 6}, "full",
     "over planning.repair_max_incumbent_age_hours (6)"),
])
def test_auto_repairs_between_daily_full_solves(services_with, policy, values, ran, why) -> None:
    services = services_with(_same_id(policy, **values) if values else policy)
    outcome = acquire_demo(services)
    snapshot = snapshot_for_run(services, outcome.run_id)
    incumbent = import_fixture_incumbent(services)               # accepted 12 h before the fixture cutoff (06:00 local, shift 1)
    ctx = EngineContext(snapshot, services.policy, Purpose.EXECUTABLE)
    mode, reason = planning_mode(ctx, Defense(incumbent_id=incumbent.id, status="LOADED", plan=incumbent), "auto")
    assert mode == ran and why in reason
    assert planning_mode(ctx, Defense(status="NO_INCUMBENT"), "auto")[0] == "full"
    assert planning_mode(ctx, Defense(), "repair") == ("repair", "repair requested")
    plan, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5, mode="auto")
    assert run.planning["requested"] == "auto" and run.planning["ran"] == ran and why in run.planning["reason"]
    assert run.validation.valid
    drift = run.planning.get("drift")
    if ran == "full":                                                    # an auto full solve measures the repair it replaced
        assert drift is not None and drift["repair_seconds"] >= 0 and drift["costs_comparable"] is True
        assert drift["controllable_delta_cents"] == drift["repair_controllable_known_cents"] - drift["full_controllable_known_cents"]
        assert drift["aircraft_completion_moved"] >= 1                   # the repair keeps B2 on day 3; the full solve pulls it in
    else:
        assert drift is None


def test_a_run_plans_the_shift_its_cutoff_leads_into(services_with, policy) -> None:
    """A pipeline cuts off shortly before its shift: 05:45 local lies in the night shift but plans the morning one."""
    early = FIXTURE_CUTOFF - timedelta(minutes=15)
    for lead, expected in ((60, ("full", "the run plans 2026-10-26 S1: the daily full solve")),
                           (0, ("repair", "the run plans 2026-10-25 S3 and the incumbent's cutoff is 11.8 h old"))):
        services = services_with(_same_id(policy, **{"planning.cutoff_lead_minutes": lead}))
        outcome = acquire_demo(services, key=f"lead-{lead}", cutoff=early)
        incumbent = import_fixture_incumbent(services)
        ctx = EngineContext(snapshot_for_run(services, outcome.run_id), services.policy, Purpose.EXECUTABLE)
        mode, reason = planning_mode(ctx, Defense(incumbent_id=incumbent.id, status="LOADED", plan=incumbent), "auto")
        assert mode == expected[0] and reason.startswith(expected[1]), reason


def test_auto_without_an_incumbent_runs_full_and_skips_the_drift_check(services_with, policy) -> None:
    services = services_with(_same_id(policy, **{"planning.full_solve_shifts": [2]}))
    outcome = acquire_demo(services)
    plan, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5, mode="auto")
    assert run.planning == {"requested": "auto", "ran": "full", "reason": "no accepted incumbent to repair (NO_INCUMBENT)"}


def test_unknown_planning_mode_is_refused(demo) -> None:
    from focus.domain import DomainError

    services, outcome, snapshot = demo
    with pytest.raises(DomainError) as err:
        schedule_snapshot(services, snapshot, purpose=Purpose.EXECUTABLE, mode="partial")
    assert err.value.code == "SCHEDULE_MODE_UNKNOWN"
