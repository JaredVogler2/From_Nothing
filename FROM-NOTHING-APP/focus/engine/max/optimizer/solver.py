"""Optional CP-SAT ordering stage and the complete-scope benchmark (S12 TIME/PROOF, P22).

**What the solver may and may not do.** S12 allows a CP-SAT plan only if the model represents every named rule:

* distinct named roles and whole-interval qualifications;
* pure pools and activation;
* calendars, CS, parts and the graph;
* breaks, OT, travel and setup;
* commitments.

This build has no such complete named model, so CP-SAT never produces an executable plan. It solves a **pooled
relaxation** and only proposes a job *order*:

* the relaxation keeps every phase the validated baseline placed, its work duration, the hard graph with lags, job,
  CS and release floors, and team capacity;
* it drops named people, qualifications, travel, budgets, breaks and shift windows;
* the solution yields job orders (by relaxation start, and by relaxation aircraft completion). Each order becomes
  dispatch ranks for the exact named decoder: one **probe**. The decoder builds every crew, duty and transfer, the
  independent validator checks the result, and a probe replaces the baseline only if the exact complete-fleet
  comparison improves and no kept commitment is lost.

The relaxation optimum is a diagnostic about the pooled problem. It is never an executable crew plan and never a
proof: ``global_optimality_proven`` stays false.

**Budgets** (policy ``solver.*``, fixture defaults): the relaxation solve has a wall-clock limit
(``solver.budget_seconds``), a memory limit (``solver.max_memory_mb``) and a worker count (``solver.num_workers``; one
worker with the policy seed is reproducible whenever the solve ends before its time limit; more workers trade that for
speed). At most ``solver.max_probes`` complete named decodes are tried. A cancel request stops the solve through a watchdog
(``CpSolver.StopSearch``) and is checked again before every probe.

**Truthful fallback.** Each of these outcomes keeps the same-input validated baseline and records a typed status:

| Outcome | Status |
| --- | --- |
| solver disabled (the default) | ``DISABLED`` |
| ortools missing | ``UNAVAILABLE`` |
| no relaxation solution before the deadline | ``TIMEOUT`` |
| ``UNKNOWN`` from CP-SAT | ``UNKNOWN`` |
| relaxation found infeasible | ``INFEASIBLE`` |
| model rejected by CP-SAT | ``MODEL_INVALID`` |
| no decoded probe passes the validator, or the baseline itself is invalid | ``INVALID_CANDIDATE`` |
| a valid probe, but none strictly better at a substantive key level (a signature-only tie is not) | ``NOT_BETTER`` |
| cancelled | ``CANCELLED`` |
| unexpected adapter error | ``ERROR`` |

The candidate replaces the baseline only with ``IMPROVED``.
"""
from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from ....domain import EdgeType, PhaseKind

SOLVER_NAME = "CP-SAT"
PROOF_SCOPE = "POOLED_RELAXATION_ORDER_ONLY: no named roles, qualifications, travel, budgets or shift windows; never an executable plan"
STATUSES = ("DISABLED", "UNAVAILABLE", "TIMEOUT", "UNKNOWN", "INFEASIBLE", "MODEL_INVALID", "INVALID_CANDIDATE", "NOT_BETTER",
            "CANCELLED", "ERROR", "IMPROVED")
WEIGHT_SCALE = 1000                     # proxy objective only; acceptance is the exact evaluator, never this weight


@dataclass(frozen=True)
class SolverStatus:
    name: str
    enabled_by: str                     # "policy" | "setting" | "request" | "" (disabled)
    available: bool | None              # None: not checked (a disabled stage never imports ortools)
    version: str | None
    status: str
    proof_scope: str
    budgets: dict[str, float] = field(default_factory=dict)
    model: dict[str, int] = field(default_factory=dict)
    relaxation_status: str | None = None          # CP-SAT's status for the RELAXATION only (OPTIMAL is not a plan proof)
    relaxation_objective: int | None = None
    relaxation_bound: int | None = None
    probes: list[dict[str, Any]] = field(default_factory=list)
    solve_seconds: float = 0.0
    wall_seconds: float = 0.0
    reason: str = ""
    global_optimality_proven: bool = False

    def receipt(self) -> dict[str, Any]:
        return asdict(self)


def solver_available() -> tuple[bool, str | None, str]:
    """(available, version, reason) without importing ortools at module import time."""
    try:
        import ortools
        from ortools.sat.python import cp_model  # noqa: F401
    except ImportError as exc:
        return False, None, f"ortools is not installed ({exc.name}); install the optional extra: pip install 'focus[solver]'"
    return True, getattr(ortools, "__version__", "unknown"), "available"


def enabled_by(policy: Any, settings: Any, requested: bool | None = None) -> str:
    """Which switch turns the optional stage on: an explicit request, the policy toggle or the deployment setting."""
    if requested is not None:
        return "request" if requested else ""
    if policy.bool("solver.enabled"):
        return "policy"
    if getattr(settings, "solver_enabled", False):
        return "setting"
    return ""


def budgets(policy: Any, budget_seconds: float | None = None) -> dict[str, float]:
    """The stage's bounds and seed; read only when the stage is enabled."""
    return {"wall_seconds": float(budget_seconds if budget_seconds is not None else policy.int("solver.budget_seconds")),
            "max_probes": policy.int("solver.max_probes"), "max_memory_mb": policy.int("solver.max_memory_mb"),
            "num_workers": policy.int("solver.num_workers"), "random_seed": policy.int("optimizer.seed") % 2**31}


# ---------------------------------------------------------------------- the pooled relaxation

def build_relaxation(ctx: Any, best: Any, weights: dict[int, Fraction] | None) -> tuple[Any, dict[str, Any], dict[str, int]]:
    """CP-SAT model of the pooled relaxation, warm-started from the validated baseline's placements."""
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    decode = best.decode
    placed = {pid: p for pid, p in decode.placements.items() if ctx.phases[pid].kind is not PhaseKind.RELEASE}
    nodes = set(decode.placements) | set(decode.milestones)
    end_hint = max([p.end for p in decode.placements.values()] + [ctx.cutoff_minute])
    horizon = ctx.cutoff_minute + 2 * max(end_hint - ctx.cutoff_minute, 7 * 24 * 60)
    starts: dict[str, Any] = {}
    ends: dict[str, Any] = {}
    intervals_by_team: dict[str, list[tuple[Any, int]]] = {}
    for pid in sorted(nodes):
        phase = ctx.phases.get(pid)
        if phase is None:                                    # a release milestone: a fixed floor
            floor = decode.milestones.get(pid, ctx.cutoff_minute)
            s = model.NewIntVar(floor, horizon, f"m_{len(starts)}")
            starts[pid] = ends[pid] = s
            continue
        floor, _ = ctx.job_floor(phase.job)
        duration = 0 if phase.kind is PhaseKind.RELEASE else max(0, phase.work_minutes)
        s = model.NewIntVar(floor, horizon, f"s_{len(starts)}")
        e = model.NewIntVar(floor, horizon, f"e_{len(starts)}")
        model.Add(e == s + duration)
        starts[pid], ends[pid] = s, e
        if pid in placed and phase.role_demands:
            demand = phase.role_demands[0]
            interval = model.NewIntervalVar(s, duration, e, f"i_{len(starts)}")
            intervals_by_team.setdefault(demand.team, []).append((interval, max(1, demand.bundle.count)))
        hint = decode.placements.get(pid)
        if hint is not None:
            model.AddHint(s, hint.start)
    edges = 0
    for succ, preds in ctx.preds.items():
        if succ not in starts:
            continue
        for e in preds:
            if e.pred_phase not in starts:
                continue
            lag = e.lag_minutes
            if e.type is EdgeType.FS:
                model.Add(starts[succ] >= ends[e.pred_phase] + lag)
            elif e.type is EdgeType.SS:
                model.Add(starts[succ] >= starts[e.pred_phase] + lag)
            elif e.type is EdgeType.FF:
                model.Add(ends[succ] >= ends[e.pred_phase] + lag)
            elif e.type is EdgeType.SF:
                model.Add(ends[succ] >= starts[e.pred_phase] + lag)
            edges += 1
    capacity: dict[str, int] = {}
    for pool in ctx.snapshot.pools:
        cap = ctx.pool_caps.get(pool.id, 0)
        capacity[pool.key.team] = max(capacity.get(pool.key.team, 0), cap)
    for team, items in intervals_by_team.items():
        cap = capacity.get(team, 0)
        if cap <= 0:
            continue
        model.AddCumulative([i for i, _ in items], [min(d, cap) for _, d in items], cap)
    line_end: dict[int, Any] = {}
    for pid, p in placed.items():
        line_end.setdefault(p.line, []).append(ends[pid])
    objective = []
    for line, line_ends in sorted(line_end.items()):
        c = model.NewIntVar(ctx.cutoff_minute, horizon, f"c_{line}")
        model.AddMaxEquality(c, line_ends)
        w = (weights or {}).get(line, Fraction(0))
        objective.append(c * max(1, int(round(float(w) * WEIGHT_SCALE))))
    model.Minimize(sum(objective) if objective else 0)
    sizes = {"phases": len(placed), "milestones": len(nodes) - len(placed), "precedence_edges": edges, "teams": len(intervals_by_team),
             "lines": len(line_end), "horizon_minutes": horizon - ctx.cutoff_minute}
    return model, {"starts": starts, "ends": ends, "placed": placed}, sizes


def probe_orders(ctx: Any, refs: dict[str, Any], value: Callable[[Any], int]) -> list[tuple[str, dict[str, float]]]:
    """Deterministic job orders read from one relaxation solution; an earlier job gets a more negative dispatch rank."""
    first: dict[str, int] = {}
    line_done: dict[int, int] = {}
    for pid, placement in refs["placed"].items():
        job = ctx.phases[pid].job
        start, end = value(refs["starts"][pid]), value(refs["ends"][pid])
        first[job] = min(first.get(job, start), start)
        line_done[placement.line] = max(line_done.get(placement.line, end), end)
    by_start = sorted(first, key=lambda j: (first[j], j))
    by_line = sorted(first, key=lambda j: (line_done.get(ctx.jobs[j].line, 0), first[j], j))

    def ranks(order: list[str]) -> dict[str, float]:
        return {job: float(i - len(order)) for i, job in enumerate(order)}
    return [("relaxation_start", ranks(by_start)), ("relaxation_aircraft_completion", ranks(by_line))]


def _solve(model: Any, bounds: dict[str, float], cancel: Callable[[], bool]) -> tuple[Any, int, bool]:
    """Solve under the wall-clock, memory and worker bounds; a watchdog turns a cancel request into StopSearch."""
    from ortools.sat.python import cp_model

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, float(bounds["wall_seconds"]))
    solver.parameters.num_workers = max(1, int(bounds["num_workers"]))
    solver.parameters.max_memory_in_mb = max(64, int(bounds["max_memory_mb"]))
    solver.parameters.random_seed = int(bounds.get("random_seed", 0))
    done, cancelled = threading.Event(), threading.Event()

    def watch() -> None:
        while not done.wait(0.25):
            if cancel():
                cancelled.set()
                solver.StopSearch()
                return
    watchdog = threading.Thread(target=watch, name="focus-cpsat-cancel", daemon=True)
    watchdog.start()
    try:
        code = solver.Solve(model)
    finally:
        done.set()
        watchdog.join()
    return solver, code, cancelled.is_set()


def improve(optimizer: Any, run: Any, *, enabled: str, bounds: dict[str, float] | None,
            cancel: Callable[[], bool] = lambda: False) -> SolverStatus:
    """The optional ordering stage after the heuristic; ``run.best`` changes only when the result is IMPROVED."""
    if not enabled:
        return SolverStatus(name=SOLVER_NAME, enabled_by="", available=None, version=None, proof_scope=PROOF_SCOPE, status="DISABLED",
                            reason="optional solver is off (policy solver.enabled=false, FOCUS_SOLVER_ENABLED unset)")
    available, version, why = solver_available()
    base = dict(name=SOLVER_NAME, enabled_by=enabled, available=available, version=version, proof_scope=PROOF_SCOPE, budgets=dict(bounds or {}))
    if not available:
        return SolverStatus(**base, status="UNAVAILABLE", reason=why)
    if not run.best_valid:
        return SolverStatus(**base, status="INVALID_CANDIDATE", reason="the same-input baseline is not valid; nothing is published from it")
    began = time.monotonic()
    sizes: dict[str, int] = {}
    try:
        weights = {line: w.normalized for line, w in run.weights.items()} if run.weights else None
        model, refs, sizes = build_relaxation(optimizer.ctx, run.best, weights)
        problem = model.Validate()
        if problem:
            return SolverStatus(**base, status="MODEL_INVALID", model=sizes, wall_seconds=round(time.monotonic() - began, 3), reason=problem[:300])
        if cancel():
            return SolverStatus(**base, status="CANCELLED", model=sizes, reason="cancelled before solving")
        solve_began = time.monotonic()
        solver, code, stopped = _solve(model, base["budgets"], cancel)
        name = solver.StatusName(code)
        solve_elapsed = time.monotonic() - solve_began
        solved = round(time.monotonic() - began, 3)
        found = name in ("OPTIMAL", "FEASIBLE")
        facts = dict(model=sizes, relaxation_status=name, solve_seconds=round(solve_elapsed, 3),
                     relaxation_objective=int(solver.ObjectiveValue()) if found else None,
                     relaxation_bound=int(solver.BestObjectiveBound()) if found else None)
        if stopped:
            return SolverStatus(**base, **facts, status="CANCELLED", wall_seconds=solved, reason="cancel requested during the relaxation solve")
        if name == "INFEASIBLE":
            return SolverStatus(**base, **facts, status="INFEASIBLE", wall_seconds=solved,
                                reason="the pooled relaxation is infeasible; the validated baseline stands")
        if name == "MODEL_INVALID":
            return SolverStatus(**base, **facts, status="MODEL_INVALID", wall_seconds=solved, reason="CP-SAT rejected the model")
        if not found:
            status = "TIMEOUT" if solve_elapsed >= solver.parameters.max_time_in_seconds * 0.95 else "UNKNOWN"
            return SolverStatus(**base, **facts, status=status, wall_seconds=solved, reason=f"CP-SAT returned {name}; the validated baseline stands")
        from .neighborhoods import PriorityState

        best, chosen, probes = run.best, None, []
        baseline = {pid: (pl.start, pl.crew) for pid, pl in run.best.decode.placements.items()}
        for label, ranks in probe_orders(optimizer.ctx, refs, solver.Value)[: max(0, int(base["budgets"]["max_probes"]))]:
            if cancel():
                return SolverStatus(**base, **facts, probes=probes, status="CANCELLED", wall_seconds=round(time.monotonic() - began, 3),
                                    reason="cancel requested between probes; the validated baseline stands")
            t = time.monotonic()
            candidate = optimizer.evaluate(optimizer.decode(PriorityState(ranks), run.urgency))
            valid = optimizer._valid(candidate, f"solver:{label}")
            level = optimizer.improved_level(candidate, best) if valid and optimizer.better(candidate, best) else None
            better = level not in (None, "signature")       # a tie on every substantive level is not an improvement
            churn = sum(1 for pid, pl in candidate.decode.placements.items() if baseline.get(pid) != (pl.start, pl.crew))
            churn += sum(1 for pid in baseline if pid not in candidate.decode.placements)
            probes.append({"order": label, "valid": valid, "better": better, "improved_level": level,
                           "changed_placements": churn, "deferred_phases": len(candidate.decode.deferred),
                           "unknown_lines": len(candidate.summary.unknown_lines),
                           "controllable_known_cents": str(candidate.summary.controllable_known_cents),
                           "seconds": round(time.monotonic() - t, 3)})
            if better:
                best, chosen = candidate, label
        wall = round(time.monotonic() - began, 3)
        if chosen is not None:
            run.best, run.best_valid = best, True
            run.accepted_moves.append(f"solver order {chosen}")
            level = next(p["improved_level"] for p in probes if p["order"] == chosen)
            return SolverStatus(**base, **facts, probes=probes, status="IMPROVED", wall_seconds=wall,
                                reason=f"the {chosen} order decoded into a valid named plan, strictly better at {level} (no optimality claimed)")
        if any(p["valid"] for p in probes):
            return SolverStatus(**base, **facts, probes=probes, status="NOT_BETTER", wall_seconds=wall,
                                reason="valid, but not better under the exact complete-fleet comparison; the baseline stands")
        return SolverStatus(**base, **facts, probes=probes, status="INVALID_CANDIDATE", wall_seconds=wall,
                            reason="no probe passed independent validation; the baseline stands" if probes else "no probe was allowed (solver.max_probes=0)")
    except Exception as exc:  # noqa: BLE001 - any adapter failure keeps the validated baseline
        return SolverStatus(**base, model=sizes, status="ERROR", wall_seconds=round(time.monotonic() - began, 3),
                            reason=f"{type(exc).__name__}: {exc}"[:300])


# ---------------------------------------------------------------------- the complete-scope benchmark

def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _plan_summary(plan: Any, run: Any, synthetic: frozenset[str]) -> dict[str, Any]:
    duty: dict[str, int] = {}
    ot = 0
    virtual: set[str] = set()
    for d in plan.duties:
        duty[d.kind.value] = duty.get(d.kind.value, 0) + int((d.end - d.start).total_seconds() // 60)
        ot += d.ot_minutes
        if d.person in synthetic:
            virtual.add(d.person)
    receipt = run.validation
    comparison = plan.algorithm_status.baseline_comparison
    return {
        "candidate_id": plan.id, "assignments": len(plan.assignments), "jobs": len(plan.job_dispositions),
        "dispositions": _count(o.disposition.value for o in plan.job_dispositions),
        "conditional_assignments": sum(1 for a in plan.assignments if a.conditional_reasons),
        "duty_minutes": dict(sorted(duty.items())), "ot_minutes": ot, "virtual_people_with_duty": len(virtual),
        "commitment_outcomes": _count(c.outcome for c in plan.commitment_results),
        "known_cost_cents": str(plan.costs.known_total_cents), "conditional_cost_cents": str(plan.costs.conditional_total_cents),
        "controllable_known_cents": str(plan.costs.controllable_known_cents), "unknown_cost_lines": len(plan.costs.unknown_lines),
        "complete_cost": plan.costs.complete,
        "baseline": {"known_cents": str(comparison.get("baseline_known_cents")), "conditional_cents": str(comparison.get("baseline_conditional_cents")),
                     "unknown_lines": len(comparison.get("baseline_unknown_lines") or ()), "deferred_phases": comparison.get("baseline_deferred_phases")},
        "final_deferred_phases": comparison.get("final_deferred_phases"), "travel_minutes": comparison.get("final_travel_minutes"),
        "wait_minutes": comparison.get("final_wait_minutes"), "accepted_moves": len(run.accepted_moves), "moves": run.moves,
        "deadline_reached": run.deadline_reached, "stages_ms": {st.stage: st.runtime_ms for st in run.stages},
        "search_validation": {"validated": run.validated, "rolled_back": len(run.validator_rejections), "seed_valid": run.seed_valid},
        "validation": {"valid": receipt.valid, "violations": len(receipt.violations), "required_skips": list(receipt.required_skips),
                       "check_counts": receipt.check_counts},
        "global_optimality_proven": plan.global_optimality_proven, "proof_status": plan.algorithm_status.proof_status,
        "solver": run.solver,
    }


def schedule_digest(plan: Any) -> str:
    """Hash of what a plan decides (assignments, duties, dispositions with reasons, completions, costs, commitments)."""
    from ....codec import content_hash, to_jsonable

    return content_hash(to_jsonable({"assignments": plan.assignments, "duties": plan.duties, "dispositions": plan.job_dispositions,
                                     "completions": plan.aircraft_completion, "costs": plan.costs, "commitments": plan.commitment_results}))


def benchmark_size(app_root: Path, n_aircraft: int, *, budget_seconds: float, max_moves: int | None, solver: bool,
                   solver_budget_seconds: float | None = None, purposes: tuple[str, ...] = ("EXECUTABLE", "FORECAST"),
                   shape: str = "scale", engines: tuple[str, ...] = ("python",), shape_options: dict[str, Any] | None = None) -> dict[str, Any]:
    """One complete-scope run in a fresh fixture runtime: acquire the synthetic census, schedule, validate.

    ``shape`` is ``scale`` (the P22 14-operation template) or ``production`` (the program's stated per-aircraft shape,
    ``focus.data.production_fixture``). ``engines`` lists the ledger/crew cores to run (``python``, ``native``); every
    engine schedules the SAME admitted snapshot in this process, so their plans can be compared byte for byte.
    """
    from ....clock import FIXTURE_CUTOFF, FixedClock
    from ....config import Settings
    from ....data.acquisition import acquire, load_snapshot
    from ....data.fixture_builder import build_scale_fixture
    from ....data.fixture_reader import FixtureSourceReader
    from ....data.production_fixture import build_production_fixture
    from ....data.repositories import Repositories
    from ....data.store import FixtureStore
    from ....domain import Purpose
    from ....runtime import build_services
    from .orchestrator import schedule_snapshot

    if shape not in ("scale", "production") or not engines or set(engines) - {"python", "native"}:
        raise ValueError(f"shape must be scale or production and engines python/native, got {shape!r} {engines!r}")
    with tempfile.TemporaryDirectory(prefix=f"focus-bench-{n_aircraft}-") as tmp:
        clock = FixedClock(FIXTURE_CUTOFF)

        def services_for(engine: str) -> Any:
            settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False, native_core=engine == "native")
            return build_services(settings, repositories=Repositories(FixtureStore(Path(tmp)), mode="fixture", scope_key=settings.scope_key,
                                                                      clock=clock), clock=clock)

        services = services_for(engines[0])
        started = time.monotonic()
        datasets = build_scale_fixture(n_aircraft) if shape == "scale" else build_production_fixture(n_aircraft, **(shape_options or {}))
        reader = FixtureSourceReader(app_root / "fixtures" / "inputs" / "manufacturing_demo",
                                     overrides={name: ds.to_json() for name, ds in datasets.items()})
        outcome = acquire(services, reader, cutoff=FIXTURE_CUTOFF, idempotency_key=f"benchmark-{n_aircraft}")
        if outcome.status != "COMPLETE":
            return {"aircraft": n_aircraft, "status": "SOURCE_REJECTED", "reason": str(outcome.rejection)}
        snapshot = load_snapshot(services.repositories, outcome.snapshot_id)
        synthetic = frozenset(p.id for p in snapshot.people if p.synthetic)
        label = "SYNTHETIC_SCALE_FIXTURE" if shape == "scale" else "PRODUCTION_SHAPED_SYNTHETIC_FIXTURE"
        result: dict[str, Any] = {
            "aircraft": n_aircraft, "status": "COMPLETE", "shape": shape, "label": f"{label} (real readiness not established)",
            "shape_options": shape_options or {},
            "inventory": {"source_task_rows": len(datasets["master_task_list"].rows), "census_lines": len(snapshot.census), "jobs": len(snapshot.jobs),
                          "phases": len(snapshot.phases), "phase_kinds": _count(ph.kind.value for ph in snapshot.phases),
                          "people": len(snapshot.people), "virtual_people": len(synthetic), "pools": len(snapshot.pools), "edges": len(snapshot.edges),
                          "releases": len(snapshot.releases), "rework_links": len(snapshot.rework_links), "shortages": len(snapshot.shortages),
                          "dispositions": _count(d.disposition.value for d in snapshot.dispositions)},
            "acquire_seconds": round(time.monotonic() - started, 2), "budget_seconds": budget_seconds, "max_moves": max_moves,
            "solver_requested": solver, "solver_budget_seconds": solver_budget_seconds, "plans": {}, "engines": {},
        }
        for purpose in purposes:
            runs: dict[str, Any] = {}
            for engine in engines:
                svc = services if engine == engines[0] else services_for(engine)
                t = time.monotonic()
                plan, run = schedule_snapshot(svc, snapshot, purpose=Purpose(purpose), budget_seconds=budget_seconds, max_moves=max_moves,
                                              solver=solver, solver_budget_seconds=solver_budget_seconds)
                wall = round(time.monotonic() - t, 2)
                runs[engine] = {"wall_seconds": wall, "engine": run.native, "candidate_id": plan.id, "plan_hash": plan.hash,
                                "schedule_digest": schedule_digest(plan), "stages_ms": {st.stage: st.runtime_ms for st in run.stages},
                                "valid": run.validation.valid, "rss_mb_after": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}
                if engine == engines[0]:
                    summary = _plan_summary(plan, run, synthetic)
                    summary["wall_seconds"] = wall
                    summary["inventory_complete"] = summary["jobs"] == len(snapshot.jobs)
                    result["plans"][purpose] = summary
            first = runs[engines[0]]
            result["engines"][purpose] = {"runs": runs, "identical": all(r["plan_hash"] == first["plan_hash"] and r["candidate_id"] == first["candidate_id"]
                                                                          for r in runs.values())}
        result["max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        return result


def benchmark_failures(runs: list[dict[str, Any]]) -> list[str]:
    """Why a benchmark is not a pass: an unread source, an invalid plan, a skipped required check or a lost job."""
    out = []
    for r in runs:
        if r.get("status") != "COMPLETE":
            out.append(f"{r.get('aircraft')}: {r.get('status')}")
            continue
        for purpose, e in (r.get("engines") or {}).items():
            if not e["identical"]:
                out.append(f"{r['aircraft']} {purpose}: engines produced different plans")
        for purpose, p in r["plans"].items():
            if not p["validation"]["valid"]:
                out.append(f"{r['aircraft']} {purpose}: {p['validation']['violations']} violations")
            if p["validation"]["required_skips"]:
                out.append(f"{r['aircraft']} {purpose}: required checks skipped {p['validation']['required_skips']}")
            if not p["inventory_complete"]:
                out.append(f"{r['aircraft']} {purpose}: {p['jobs']} of {r['inventory']['jobs']} jobs dispositioned")
            if p["global_optimality_proven"]:
                out.append(f"{r['aircraft']} {purpose}: claims global optimality")
    return out


def cli_benchmark(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    """``benchmark --complete-scope``: each size runs in its own process (honest per-size peak memory)."""
    try:
        sizes = [int(x) for x in str(args.sizes).split(",") if x.strip()]
    except ValueError:
        sizes = []
    if not sizes or min(sizes) < 2:
        return {"status": "invalid", "error": {"code": "BAD_SIZES", "message": "sizes are comma-separated aircraft counts >= 2"}}
    runs = []
    for n in sizes:
        cmd = [sys.executable, "-m", "focus.engine.max.optimizer.solver", "--size", str(n), "--budget-seconds", str(args.budget_seconds),
               "--solver", args.solver, "--app-root", str(services.settings.app_root)]
        if getattr(args, "max_moves", None) is not None:
            cmd += ["--max-moves", str(args.max_moves)]
        if getattr(args, "solver_budget_seconds", None) is not None:
            cmd += ["--solver-budget-seconds", str(args.solver_budget_seconds)]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=max(600, int(args.budget_seconds) * 20 + 3600))
        if done.returncode != 0:
            runs.append({"aircraft": n, "status": "ERROR", "stderr": done.stderr[-2000:]})
            continue
        runs.append(json.loads(done.stdout))
    failures = benchmark_failures(runs)
    return {"status": "ok" if not failures else "error", "solver": args.solver, "sizes": sizes, "runs": runs, "failures": failures,
            "note": "synthetic scale fixtures; runtimes are this machine's; the exact evaluator and the independent validator decide"}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="one complete-scope benchmark size (used by `focus.cli benchmark`)")
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--budget-seconds", type=float, default=20.0)
    parser.add_argument("--max-moves", type=int, default=None)
    parser.add_argument("--solver", choices=["off", "on"], default="off")
    parser.add_argument("--solver-budget-seconds", type=float, default=None)
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--shape", choices=["scale", "production"], default="scale")
    parser.add_argument("--engines", default="python", help="comma-separated: python, native (all schedule one snapshot)")
    parser.add_argument("--purposes", default="EXECUTABLE,FORECAST")
    parser.add_argument("--shape-options", default="{}", help="JSON keyword arguments for the production shape")
    args = parser.parse_args(argv)
    result = benchmark_size(Path(args.app_root), args.size, budget_seconds=args.budget_seconds, max_moves=args.max_moves, solver=args.solver == "on",
                            solver_budget_seconds=args.solver_budget_seconds, purposes=tuple(x for x in args.purposes.split(",") if x),
                            shape=args.shape, engines=tuple(x for x in args.engines.split(",") if x), shape_options=json.loads(args.shape_options))
    from ....codec import to_jsonable

    sys.stdout.write(json.dumps(to_jsonable(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
