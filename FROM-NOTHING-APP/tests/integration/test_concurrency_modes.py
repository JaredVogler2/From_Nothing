"""B2: hard CONCURRENT / NON-CONCURRENT edges are scheduled by their admitted policy mode, not dropped (S10).

Fixture policy admits CONCURRENT = REQUIRED_OVERLAP and NON-CONCURRENT = NO_OVERLAP; a policy override admits SAME_START.
Two worlds get one hard template edge each:

* line 1303's independent rework children NC3 and PU3 (both PROD-B, two-person crews, executable);
* line 1306's support tasks MHE6 and PSC6 (two different support teams, forecast only).

Every plan must pass the validator's mode check. The pair holds its mode wherever both phases are planned; a phase that
cannot meet its partner is deferred with a CONCURRENCY reason, never placed in violation. The overlap rule is conservative:
whichever phase is placed second takes the constraint (S10: a heuristic, not a proof of the best concurrent schedule).
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.acquisition import load_snapshot, snapshot_for_run
from focus.data.fixture_reader import FixtureSourceReader
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import EdgeType, Purpose, to_minute
from focus.engine.max.optimizer.orchestrator import schedule_snapshot
from focus.runtime import build_services
from focus.validation.plan import validate
from tests.conftest import acquire_demo

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _edge(pred: str, succ: str, kind: str) -> dict:
    return {"IS_HARD": "Y", "LOAD_TIMESTAMP": "2026-10-25T17:30:00.000", "OFFSET_MINUTES": 0, "ORIGINAL_PRED_JOBNUMBER": pred,
            "ORIGINAL_SUC_JOBNUMBER": succ, "PREDECESSOR_SOI": pred, "PRED_MINOR_MODEL": "9", "RELATIONSHIP_TYPE": kind,
            "SUCCESSOR_SOI": succ, "SUC_MINOR_MODEL": "9"}


def _services(root: Path, app_root: Path, policy=None):
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock)
    return build_services(settings, repositories=repos, clock=clock, policy=policy)


def _world(root: Path, app_root: Path, kind: str, pred: str = "NC3", succ: str = "PU3"):
    data = {"pnet_dag": json.loads((INPUTS / "pnet_dag.json").read_text())}
    data["pnet_dag"]["rows"].append(_edge(pred, succ, kind))
    services = _services(root, app_root)
    outcome = acquire_demo(services, key=f"concurrency-{kind}", reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection
    return services, outcome, load_snapshot(services.repositories, outcome.snapshot_id)


@pytest.fixture(scope="module")
def concurrent(tmp_path_factory, app_root):
    return _world(tmp_path_factory.mktemp("concurrent") / "runtime", app_root, "CONCURRENT")


@pytest.fixture(scope="module")
def nonconcurrent(tmp_path_factory, app_root):
    return _world(tmp_path_factory.mktemp("nonconcurrent") / "runtime", app_root, "NON-CONCURRENT")


@pytest.fixture(scope="module")
def support(tmp_path_factory, app_root):
    return _world(tmp_path_factory.mktemp("support") / "runtime", app_root, "CONCURRENT", "MHE6", "PSC6")


def _pair(snapshot):
    (edge,) = [e for e in snapshot.edges if e.type in (EdgeType.CONCURRENT, EdgeType.NONCONCURRENT) and e.hard]
    return edge


def _holds(plan, run, snapshot) -> bool:
    """The plan is valid and the pair holds its mode when both phases are planned. True when both are."""
    assert run.validation.valid, [(v.check, v.code, v.detail) for v in run.validation.violations][:5]
    edge = _pair(snapshot)
    at = {a.phase: (to_minute(a.start), to_minute(a.end)) for a in plan.assignments}
    a, b = at.get(edge.pred_phase), at.get(edge.succ_phase)
    if a is None or b is None:
        unplaced = edge.succ_phase if b is None else edge.pred_phase
        reasons = " ".join(r for o in plan.job_dispositions if unplaced.startswith(o.job) for r in o.reasons)
        assert "CONCURRENCY" in reasons or a is None and b is None, reasons
        return False
    overlaps = a[0] < b[1] and b[0] < a[1]
    assert {"REQUIRED_OVERLAP": overlaps, "NO_OVERLAP": not overlaps, "SAME_START": a[0] == b[0]}[edge.concurrency_mode], (edge.concurrency_mode, a, b)
    return True


@pytest.mark.parametrize("purpose", [Purpose.EXECUTABLE, Purpose.FORECAST])
def test_no_overlap_puts_the_pair_one_after_the_other(nonconcurrent, purpose) -> None:
    services, _, snapshot = nonconcurrent
    assert _pair(snapshot).concurrency_mode == "NO_OVERLAP"
    plan, run = schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=5, max_moves=2)
    assert _holds(plan, run, snapshot)                                         # both children planned, apart


@pytest.mark.parametrize("purpose", [Purpose.EXECUTABLE, Purpose.FORECAST])
def test_an_overlap_one_crew_cannot_staff_is_deferred_not_violated(concurrent, purpose) -> None:
    services, _, snapshot = concurrent
    assert _pair(snapshot).concurrency_mode == "REQUIRED_OVERLAP"
    plan, run = schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=5, max_moves=2)
    assert not _holds(plan, run, snapshot)                                     # PROD-B cannot run both two-person jobs at once
    assert any("CONCURRENCY_UNMET" in r for o in plan.job_dispositions for r in o.reasons)


@pytest.mark.parametrize("mode", ["REQUIRED_OVERLAP", "SAME_START"])
def test_two_support_teams_work_together(support, tmp_path, app_root, mode) -> None:
    services, outcome, snapshot = support
    if mode == "SAME_START":
        policy = services.policy.with_overrides({"edges.concurrent_mode": "SAME_START"}, new_id="rebuild-v1-same-start",
                                                authority="fixture_default", reason="B2 same-start mode")
        snapshot = snapshot_for_run(services, outcome.run_id, policy=policy, store=False)
        services = _services(tmp_path / "runtime", app_root, policy=policy)
    assert _pair(snapshot).concurrency_mode == mode
    plan, run = schedule_snapshot(services, snapshot, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=2)
    assert _holds(plan, run, snapshot)                                         # both support tasks planned, together


def test_an_overlap_forged_into_a_no_overlap_pair_is_caught(nonconcurrent) -> None:
    services, _, snapshot = nonconcurrent
    plan, run = schedule_snapshot(services, snapshot, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=0)
    edge = _pair(snapshot)
    by_phase = {a.phase: a for a in plan.assignments}
    first, second = by_phase[edge.pred_phase], by_phase[edge.succ_phase]
    shift = first.start - second.start
    moved = replace(second, start=second.start + shift, end=second.end + shift,
                    segments=tuple(replace(s, start=s.start + shift, end=s.end + shift) for s in second.segments))
    forged = replace(plan, assignments=tuple(moved if a.id == second.id else a for a in plan.assignments))
    receipt = validate(snapshot, services.policy, forged, purpose=Purpose.EXECUTABLE)
    assert any(v.code == "CONCURRENCY_VIOLATION" and v.entity == edge.id for v in receipt.violations)
    assert shift != timedelta(0)                                               # the decoder had put them apart


@pytest.fixture(scope="module")
def support_apart(tmp_path_factory, app_root):
    return _world(tmp_path_factory.mktemp("support_apart") / "runtime", app_root, "NON-CONCURRENT", "MHE6", "PSC6")


def test_two_free_support_teams_are_kept_apart_when_the_edge_says_so(support_apart) -> None:
    """Without the edge the two support tasks start together (both teams are free at 10:15); NON-CONCURRENT moves one after."""
    services, _, snapshot = support_apart
    assert _pair(snapshot).concurrency_mode == "NO_OVERLAP"
    plan, run = schedule_snapshot(services, snapshot, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=2)
    assert _holds(plan, run, snapshot)
