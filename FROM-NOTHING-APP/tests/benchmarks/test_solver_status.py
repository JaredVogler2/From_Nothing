"""P22: the optional CP-SAT ordering stage reports a truthful status and can never bypass named feasibility (A45, A70).

Every non-IMPROVED outcome must leave the same-input validated baseline untouched. IMPROVED must be strictly better at
a substantive level of the exact comparison, confirmed here from the plan's own duties and costs. A pooled relaxation
optimum is never a proof.
"""
from __future__ import annotations

import dataclasses
import threading
import time
import types

import pytest

from focus.domain import Disposition, DutyKind, Purpose
from focus.engine.commitments import defended_tasks
from focus.engine.max.optimizer import solver as S
from focus.engine.max.optimizer.orchestrator import KEY_LEVELS, MaxOptimizer, schedule_snapshot
from focus.engine.model import EngineContext
from focus.validation.duty import reconstruct

pytest.importorskip("ortools.sat.python.cp_model")


def _schedule(demo, purpose: Purpose = Purpose.EXECUTABLE, **kw):
    services, _, snapshot = demo
    return schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=30, max_moves=20, **kw)


def _optimizer_and_run(demo, purpose: Purpose = Purpose.EXECUTABLE):
    services, _, snapshot = demo
    ctx = EngineContext(snapshot, services.policy, purpose)
    defense = defended_tasks(services, ctx)
    optimizer = MaxOptimizer(ctx, defended=defense.defended, budget_seconds=30, max_moves=20, incumbent_id=defense.incumbent_id)
    return optimizer, optimizer.run()


def _stages(plan) -> list[str]:
    return [s.stage for s in plan.algorithm_status.stages]


def _wait_minutes(plan) -> int:
    return sum(int((d.end - d.start).total_seconds() // 60) for d in plan.duties if d.kind is DutyKind.WAIT)


# ---------------------------------------------------------------------- switches and the default


def test_disabled_by_default_records_the_status_and_changes_nothing(demo) -> None:
    plan, run = _schedule(demo)
    receipt = run.solver
    assert receipt["status"] == "DISABLED" and receipt["enabled_by"] == "" and receipt["available"] is None
    assert receipt["global_optimality_proven"] is False and "never an executable plan" in receipt["proof_scope"]
    assert plan.algorithm_status.baseline_comparison["solver"]["status"] == "DISABLED"     # stored with the candidate
    assert "solver" not in _stages(plan) and "cpsat_relaxation_order" not in plan.algorithm_status.paths_used
    off, _ = _schedule(demo, solver=False)
    assert off.id == plan.id                                                             # an explicit off is the same substance


def test_switch_precedence_request_then_policy_then_setting() -> None:
    on, off = types.SimpleNamespace(bool=lambda key: True), types.SimpleNamespace(bool=lambda key: False)
    setting_on, setting_off = types.SimpleNamespace(solver_enabled=True), types.SimpleNamespace(solver_enabled=False)
    assert S.enabled_by(off, setting_off, True) == "request"
    assert S.enabled_by(on, setting_on, False) == ""                                     # an explicit request off wins
    assert S.enabled_by(on, setting_off, None) == "policy"
    assert S.enabled_by(off, setting_on, None) == "setting"
    assert S.enabled_by(off, setting_off, None) == ""


def test_deployment_setting_turns_the_stage_on(services_factory) -> None:
    from tests.conftest import acquire_demo
    from focus.data.acquisition import load_snapshot

    services = services_factory(solver_enabled=True)
    outcome = acquire_demo(services)
    plan, run = schedule_snapshot(services, load_snapshot(services.repositories, outcome.snapshot_id), purpose=Purpose.EXECUTABLE,
                                  budget_seconds=30, max_moves=20)
    assert run.solver["enabled_by"] == "setting" and run.solver["status"] in S.STATUSES and run.solver["status"] != "DISABLED"
    assert "solver" in _stages(plan)


def test_budgets_come_from_the_policy_and_the_wall_limit_can_be_overridden(policy) -> None:
    bounds = S.budgets(policy)
    assert bounds["wall_seconds"] == policy.int("solver.budget_seconds") and bounds["max_probes"] == policy.int("solver.max_probes")
    assert bounds["max_memory_mb"] == policy.int("solver.max_memory_mb") and bounds["num_workers"] == policy.int("solver.num_workers")
    assert S.budgets(policy, 2.5)["wall_seconds"] == 2.5


# ---------------------------------------------------------------------- truthful fallback: the same-input baseline


def test_unavailable_dependency_returns_the_same_input_baseline(demo, monkeypatch) -> None:
    baseline, _ = _schedule(demo, solver=False)
    monkeypatch.setattr(S, "solver_available", lambda: (False, None, "ortools is not installed (ortools)"))
    plan, run = _schedule(demo, solver=True)
    assert run.solver["status"] == "UNAVAILABLE" and run.solver["available"] is False and "not installed" in run.solver["reason"]
    assert plan.id == baseline.id and run.validation.valid             # the persisted same-substance candidate is the receipt
    stage = next(s for s in run.stages if s.stage == "solver")         # this run's stage lives in the run receipt
    assert stage.accepted == 0 and stage.note.startswith("UNAVAILABLE")


class _FakeSolver:
    """A CP-SAT stand-in that ends without a solution (status classification only)."""

    def __init__(self, name: str, limit: float):
        self._name = name
        self.parameters = types.SimpleNamespace(max_time_in_seconds=limit)

    def StatusName(self, code: int) -> str:  # noqa: N802 - CP-SAT's API
        return self._name


@pytest.mark.parametrize(("name", "limit", "status"), [("UNKNOWN", 1e-9, "TIMEOUT"), ("UNKNOWN", 3600.0, "UNKNOWN")])
def test_no_relaxation_solution_keeps_the_baseline(demo, monkeypatch, name: str, limit: float, status: str) -> None:
    baseline, _ = _schedule(demo, solver=False)
    monkeypatch.setattr(S, "_solve", lambda model, bounds, cancel: (_FakeSolver(name, limit), 0, False))
    plan, run = _schedule(demo, solver=True)
    assert run.solver["status"] == status and run.solver["relaxation_objective"] is None and run.solver["probes"] == []
    assert plan.id == baseline.id and run.validation.valid and plan.global_optimality_proven is False


def _infeasible(ctx, best, weights):
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    x = model.NewIntVar(0, 10, "x")
    model.Add(x >= 5)
    model.Add(x <= 3)
    return model, {"starts": {}, "ends": {}, "placed": {}}, {"phases": 0}


def _invalid(ctx, best, weights):
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    start, end = model.NewIntVar(0, 10, "s"), model.NewIntVar(0, 10, "e")
    model.NewIntervalVar(start, -5, end, "negative")          # a performed interval with negative size is rejected
    return model, {"starts": {}, "ends": {}, "placed": {}}, {"phases": 1}


@pytest.mark.parametrize(("builder", "status"), [(_infeasible, "INFEASIBLE"), (_invalid, "MODEL_INVALID")])
def test_infeasible_or_invalid_relaxation_keeps_the_baseline(demo, monkeypatch, builder, status: str) -> None:
    monkeypatch.setattr(S, "build_relaxation", builder)
    optimizer, run = _optimizer_and_run(demo)
    before = run.best.signature
    outcome = S.improve(optimizer, run, enabled="request", bounds=S.budgets(demo[0].policy, 5))
    assert outcome.status == status and outcome.probes == [] and run.best.signature == before
    assert outcome.global_optimality_proven is False and outcome.reason


def test_adapter_error_keeps_the_baseline(demo, monkeypatch) -> None:
    baseline, _ = _schedule(demo, solver=False)

    def boom(ctx, best, weights):
        raise RuntimeError("relaxation exploded")
    monkeypatch.setattr(S, "build_relaxation", boom)
    plan, run = _schedule(demo, solver=True)
    assert run.solver["status"] == "ERROR" and "RuntimeError: relaxation exploded" in run.solver["reason"]
    assert plan.id == baseline.id and run.validation.valid


def test_invalid_baseline_publishes_nothing_from_the_solver(demo) -> None:
    optimizer, run = _optimizer_and_run(demo)
    run.best_valid = False
    before = run.best.signature
    outcome = S.improve(optimizer, run, enabled="request", bounds=S.budgets(demo[0].policy, 5))
    assert outcome.status == "INVALID_CANDIDATE" and "nothing is published" in outcome.reason
    assert run.best.signature == before and run.best_valid is False


# ---------------------------------------------------------------------- the validator, not the solver, decides


def test_a70_solver_probe_cannot_bypass_named_feasibility(demo) -> None:
    """Every probe crew is corrupted to an unqualified outsider: the independent validator rolls each one back."""
    optimizer, run = _optimizer_and_run(demo)
    ctx = optimizer.ctx
    before, rejected_before = run.best.signature, len(optimizer._gate_log)
    real_decode = optimizer.decode

    def corrupt(state, weights):
        result = real_decode(state, weights)
        pid, placement = next((pid, p) for pid, p in sorted(result.placements.items()) if p.crew)
        demand = ctx.phases[pid].role_demands[0]
        outsider = next(p.id for p in ctx.snapshot.people if p.id not in placement.crew
                        and not ctx.qualifies(p.id, demand.bundle, placement.start, placement.end))
        result.placements[pid] = dataclasses.replace(placement, crew=(outsider,) + placement.crew[1:])
        return result
    optimizer.decode = corrupt
    outcome = S.improve(optimizer, run, enabled="request", bounds=S.budgets(ctx.policy, 5))
    assert outcome.status == "INVALID_CANDIDATE" and outcome.probes and not any(p["valid"] for p in outcome.probes)
    assert run.best.signature == before                                                  # the validated baseline stands
    rolled_back = optimizer._gate_log[rejected_before:]
    assert len(rolled_back) == len(outcome.probes) and all(r.startswith("solver:") for r in rolled_back)


def test_a45_improvement_is_exact_and_never_a_proof(demo) -> None:
    """On the demo the relaxation order removes waiting. Confirm the claimed level from the plans' own duties."""
    services, _, snapshot = demo
    off, off_run = _schedule(demo, solver=False)
    on, run = _schedule(demo, solver=True)
    receipt = run.solver
    assert receipt["status"] == "IMPROVED" and receipt["enabled_by"] == "request" and receipt["relaxation_status"] in ("OPTIMAL", "FEASIBLE")
    chosen = [p for p in receipt["probes"] if p["better"]]
    assert chosen and chosen[-1]["improved_level"] in KEY_LEVELS[:-1]                  # never the signature tie-break
    assert run.validation.valid and not run.validation.required_skips
    assert on.global_optimality_proven is False and on.algorithm_status.proof_status == "HEURISTIC_NO_PROOF"
    assert receipt["global_optimality_proven"] is False                                 # a relaxation optimum is not a plan proof
    assert "cpsat_relaxation_order" in on.algorithm_status.paths_used and on.id != off.id
    # every level above the claimed one ties; the claimed one is strictly lower (independent duty reconstruction)
    level = chosen[-1]["improved_level"]
    assert sorted(r.outcome for r in on.commitment_results) == sorted(r.outcome for r in off.commitment_results)
    assert on.costs.unknown_lines == off.costs.unknown_lines
    deferred = lambda p: sum(1 for o in p.job_dispositions if o.disposition is Disposition.DEFERRED)  # noqa: E731
    assert deferred(on) == deferred(off)
    assert on.costs.controllable_known_cents == off.costs.controllable_known_cents
    assert level == "travel_plus_wait_minutes"
    rec_on, rec_off = reconstruct(snapshot, services.policy, on), reconstruct(snapshot, services.policy, off)
    assert rec_on.violations == [] and rec_off.violations == []
    assert rec_on.travel_minutes + _wait_minutes(on) < rec_off.travel_minutes + _wait_minutes(off)


def test_signature_only_difference_is_not_an_improvement() -> None:
    base = (0, 0, 1, 5, 100, 60, 0, 0, "SIG-b")
    ev = lambda key: types.SimpleNamespace(key=key)  # noqa: E731
    assert MaxOptimizer.improved_level(ev(base[:-1] + ("SIG-a",)), ev(base)) == "signature"
    assert MaxOptimizer.improved_level(ev((0, 0, 1, 4, 999, 99, 9, 9, "SIG-z")), ev(base)) == "deferred_phases"
    assert MaxOptimizer.improved_level(ev((0, 0, 2, 0, 0, 0, 0, 0, "SIG-a")), ev(base)) is None        # worse at a higher level
    assert MaxOptimizer.improved_level(ev(base), ev(base)) is None


def test_probe_budget_is_honoured(demo) -> None:
    optimizer, run = _optimizer_and_run(demo)
    bounds = {**S.budgets(optimizer.ctx.policy, 5), "max_probes": 1}
    outcome = S.improve(optimizer, run, enabled="request", bounds=bounds)
    assert len(outcome.probes) == 1 and outcome.budgets["max_probes"] == 1


def test_solver_stage_is_reproducible(demo) -> None:
    first, run1 = _schedule(demo, solver=True)
    second, run2 = _schedule(demo, solver=True)
    timeless = lambda run: [{k: v for k, v in p.items() if k != "seconds"} for p in run.solver["probes"]]  # noqa: E731
    assert first.id == second.id and timeless(run1) == timeless(run2) and run1.solver["relaxation_objective"] == run2.solver["relaxation_objective"]


# ---------------------------------------------------------------------- cancellation


def test_cancel_before_solving_keeps_the_baseline(demo) -> None:
    optimizer, run = _optimizer_and_run(demo)
    before = run.best.signature
    outcome = S.improve(optimizer, run, enabled="request", bounds=S.budgets(optimizer.ctx.policy, 5), cancel=lambda: True)
    assert outcome.status == "CANCELLED" and outcome.probes == [] and run.best.signature == before


def _hard_model():
    """A job shop proven optimal only after far longer than this test waits."""
    import random

    from ortools.sat.python import cp_model

    rng = random.Random(7)
    model = cp_model.CpModel()
    jobs, machines = 14, 14
    horizon = jobs * machines * 99
    ends, by_machine = [], {m: [] for m in range(machines)}
    for j in range(jobs):
        prev = None
        for m in rng.sample(range(machines), machines):
            d = rng.randint(1, 99)
            s = model.NewIntVar(0, horizon, f"s{j}_{m}")
            e = model.NewIntVar(0, horizon, f"e{j}_{m}")
            by_machine[m].append(model.NewIntervalVar(s, d, e, f"i{j}_{m}"))
            if prev is not None:
                model.Add(s >= prev)
            prev = e
        ends.append(prev)
    for intervals in by_machine.values():
        model.AddNoOverlap(intervals)
    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, ends)
    model.Minimize(makespan)
    return model


def test_cancel_during_the_solve_stops_cp_sat_promptly() -> None:
    flag = threading.Event()
    threading.Timer(0.5, flag.set).start()
    began = time.monotonic()
    solver, code, stopped = S._solve(_hard_model(), {"wall_seconds": 60, "num_workers": 1, "max_memory_mb": 512, "random_seed": 1}, flag.is_set)
    assert stopped and time.monotonic() - began < 10                                      # far below the 60 s wall budget
    assert solver.StatusName(code) in ("FEASIBLE", "UNKNOWN")                            # never OPTIMAL from a stopped search
