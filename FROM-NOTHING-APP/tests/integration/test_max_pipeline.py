"""P13: the named rich-MAX pipeline over the complete WIP: inventory, bounds, receipts, purposes (A26, A38, A70 engine parts)."""
from __future__ import annotations

import json
import subprocess
import sys

from focus.contracts.planning import CandidatePlan
from focus.data.acquisition import snapshot_for_run
from focus.domain import Disposition, PhaseKind, Purpose, to_minute
from focus.engine.max.optimizer.orchestrator import CANDIDATES, MaxOptimizer, build_candidate, schedule
from focus.engine.model import EngineContext
from focus.simulation.pools import is_synthetic_person
from focus.validation.duty import reconstruct
from tests.conftest import job_id


def _plan(demo, purpose=Purpose.EXECUTABLE, **kw):
    services, outcome, _ = demo
    return (*schedule(services, source_run=outcome.run_id, purpose=purpose, **kw), services, outcome)


def test_complete_inventory_and_truthful_receipt(demo) -> None:
    plan, run, services, _ = _plan(demo, budget_seconds=10, max_moves=20)
    snapshot = snapshot_for_run(services, plan.source_run_id)
    assert len(plan.job_dispositions) == len(snapshot.jobs) == 19                       # nothing dropped to look cheap
    states = {o.job: o.disposition for o in plan.job_dispositions}
    assert states[job_id(snapshot, 1306, "TST6B")] is Disposition.BLOCKED
    assert states[job_id(snapshot, 1301, "A0")] is Disposition.VERIFIED_COMPLETE
    assert all(o.reasons for o in plan.job_dispositions if o.disposition is Disposition.DEFERRED)   # every wait has a cause
    assert plan.global_optimality_proven is False and plan.algorithm_status.proof_status == "HEURISTIC_NO_PROOF"
    assert [s.stage for s in plan.algorithm_status.stages] == ["neutral_seed", "economic_redispatch", "delay_repair", "neighborhoods"]
    assert {"duty_ledger", "crew_selection", "graph", "calendar", "cs_facts", "part_releases", "economics_registry",
            "exact_evaluator"} <= set(plan.algorithm_status.paths_used)
    assert plan.costs.unknown_lines and not plan.costs.complete                         # unknown aircraft stay visible
    assert plan.hash == plan.computed_hash()
    stored = services.repositories.get_typed(CANDIDATES, plan.id, "CandidatePlan", CandidatePlan)
    assert stored is not None and stored.hash == plan.hash                              # persisted, reloadable, verifiable


def test_final_candidate_passes_independent_duty_reconstruction(demo) -> None:
    plan, _, services, _ = _plan(demo, budget_seconds=10, max_moves=40)
    snapshot = snapshot_for_run(services, plan.source_run_id)
    rec = reconstruct(snapshot, services.policy, plan)
    assert rec.violations == [] and rec.checks["V6"] and rec.checks["V7"] and rec.checks["V8"]
    reported = plan.algorithm_status.baseline_comparison["final_travel_minutes"]
    assert rec.travel_minutes == reported                                               # independent travel equals the receipt


def test_a26_engine_keeps_in_progress_remaining_after_its_predecessor(demo) -> None:
    plan, _, services, _ = _plan(demo, budget_seconds=5, max_moves=10)
    snapshot = snapshot_for_run(services, plan.source_run_id)
    root4, pre4 = job_id(snapshot, 1304, "ROOT4"), job_id(snapshot, 1304, "PRE4")
    by_job = {}
    for a in plan.assignments:
        by_job.setdefault(a.job, []).append(a)
    pre4_end = max(a.end for a in by_job[pre4])
    root4_work = [a for a in by_job[root4] if a.kind is PhaseKind.WORK]
    assert root4_work and min(a.start for a in root4_work) >= pre4_end                # incomplete FS predecessor respected
    assert sum(a.work_minutes for a in root4_work) == 60                               # only the remaining work; history untouched
    assert all(to_minute(a.start) >= to_minute(snapshot.cutoff) for a in plan.assignments)   # no backdating before the cutoff


def test_a70_deterministic_and_bounded(demo) -> None:
    services, outcome, _ = demo
    snapshot = snapshot_for_run(services, outcome.run_id)
    ids = set()
    for _ in range(2):
        ctx = EngineContext(snapshot, services.policy, Purpose.EXECUTABLE)
        run = MaxOptimizer(ctx, budget_seconds=30, max_moves=25).run()
        ids.add(build_candidate(ctx, run, created_at=snapshot.cutoff).id)
    assert len(ids) == 1                                                                # same seed, same input -> same candidate
    ctx = EngineContext(snapshot, services.policy, Purpose.EXECUTABLE)
    ticks = iter(range(10_000))
    bounded = MaxOptimizer(ctx, budget_seconds=3, max_moves=1000, clock=lambda: next(ticks)).run()
    assert bounded.deadline_reached and bounded.moves < 1000                           # wall-clock budget stops the search
    cancelled = MaxOptimizer(ctx, budget_seconds=60, max_moves=1000, cancel=lambda: True).run()
    assert cancelled.deadline_reached and cancelled.moves == 0
    assert cancelled.best.decode.placements                                             # the validated seed is still returned


def test_forecast_labels_virtual_support_and_executable_excludes_it(demo) -> None:
    fplan, _, services, _ = _plan(demo, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    snapshot = snapshot_for_run(services, fplan.source_run_id)
    people = {p.id: p for p in snapshot.people}
    virtual = [a for a in fplan.assignments if any(is_synthetic_person(people[r.person]) for r in a.crew)]
    assert virtual and all(a.conditional_reasons for a in virtual)                     # forecast-only, labelled
    assert any(o.disposition is Disposition.CONDITIONAL for o in fplan.job_dispositions)
    eplan, _, _, _ = _plan(demo, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    assert not any(is_synthetic_person(people[r.person]) for a in eplan.assignments for r in a.crew)
    assert not any(a.conditional_reasons for a in eplan.assignments)


def test_cli_schedule_in_a_fresh_process(tmp_path, app_root) -> None:
    env = {"FOCUS_RUNTIME_DIR": str(tmp_path / "rt"), "PATH": "/usr/bin:/bin"}
    acq = subprocess.run([sys.executable, "-m", "focus.cli", "acquire", "--mode", "fixture", "--scenario", "manufacturing_demo"],
                         cwd=app_root, env=env, capture_output=True, text=True, timeout=300)
    run_id = json.loads(acq.stdout)["source_run_id"]
    out = subprocess.run([sys.executable, "-m", "focus.cli", "schedule", "--source-run", run_id, "--policy", "rebuild-v1",
                          "--purpose", "EXECUTABLE", "--budget-seconds", "3"], cwd=app_root, env=env, capture_output=True, text=True, timeout=300)
    result = json.loads(out.stdout)
    assert out.returncode == 0 and result["status"] == "ok" and result["published"] is False
    assert result["jobs_total"] == 19 and result["global_optimality_proven"] is False
