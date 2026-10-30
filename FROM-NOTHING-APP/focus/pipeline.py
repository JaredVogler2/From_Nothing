"""Fixture demo pipeline (S17 LOCAL CLI: ``demo-bootstrap``; ``demo-run`` arrives with P24).

``demo-bootstrap`` does three things, and never touches a live source or SQL:

1. It verifies the deterministic S18 inputs (source datasets, the previous accepted plan and the
   S14 observation feeds) against the builder, or writes them when absent. Drift is reported,
   never silently overwritten.
2. With ``--reset`` it first removes only ``<runtime>/<scenario>``.
3. It records the S18 previous accepted plan as the ACCEPTED incumbent for both purposes in
   the persistent fixture runtime. A later process therefore reloads it from the repository,
   not from memory.
"""
from __future__ import annotations

import json
import shutil
from typing import Any

from .data.fixture_builder import DEMO_INCUMBENT, build_manufacturing_demo, build_observations
from .engine.commitments import import_fixture_incumbent

DEMO_SCENARIO = "manufacturing_demo"


def _demo_files() -> dict[str, str]:
    files = {f"{name}.json": json.dumps(ds.to_json(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
             for name, ds in build_manufacturing_demo(DEMO_SCENARIO).items()}
    files["incumbent_plan.json"] = json.dumps(DEMO_INCUMBENT, indent=1, sort_keys=True) + "\n"
    for name, payload in build_observations(DEMO_SCENARIO).items():
        files[f"observations/{name}.json"] = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    return files


def cli_demo_bootstrap(services: Any, args: Any) -> dict[str, Any]:
    settings = services.settings
    scenario = getattr(args, "scenario", None) or settings.scenario_id
    if settings.mode != "fixture":
        return {"status": "invalid", "error": {"code": "FIXTURE_ONLY", "message": "demo-bootstrap runs in fixture mode only"}}
    if scenario != DEMO_SCENARIO:
        return {"status": "invalid", "error": {"code": "SCENARIO_UNKNOWN", "message": f"no deterministic builder for {scenario!r}"}}
    runtime = settings.runtime_dir(scenario)
    if getattr(args, "reset", False) and runtime.is_dir():
        shutil.rmtree(runtime)
    target = settings.fixture_inputs_dir / scenario
    expected = _demo_files()
    written, drift = [], []
    for name, text in sorted(expected.items()):
        path = target / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(name)
        elif path.read_text(encoding="utf-8") != text:
            drift.append(name)
    if drift:
        return {"status": "invalid", "error": {"code": "FIXTURE_DRIFT", "message": f"inputs differ from the deterministic builder: {drift}"}}
    incumbents = {purpose: import_fixture_incumbent(services, scenario, purpose).id for purpose in ("EXECUTABLE", "FORECAST")}
    return {"status": "ok", "scenario": scenario, "inputs": {"verified": len(expected) - len(written), "written": written},
            "runtime_dir": str(runtime), "incumbents": incumbents, "policy": services.policy.id}


def demo_run(services: Any, *, scenario: str = DEMO_SCENARIO, budget_seconds: float | None = None, max_moves: int | None = None,
             observe: bool = True) -> dict[str, Any]:
    """The complete fixture pipeline end to end, carrying each returned id into the next stage (S17 ``demo-run``).

    bootstrap -> acquire -> schedule + publish EXECUTABLE and FORECAST -> collect observations at every manifest point ->
    evaluate every closed goal (plus the stored factory aggregate of each shift) -> recognition projection.

    It is idempotent: the acquisition key is fixed, a purpose whose accepted head already comes from this source run is
    not rescheduled, collections replay write-once, evaluations append only changed evidence and the projection
    appends nothing twice.
    """
    from types import SimpleNamespace

    from .clock import FIXTURE_CUTOFF
    from .data.acquisition import acquire, make_reader
    from .domain import Purpose
    from .engine.max.optimizer.orchestrator import schedule
    from .publication.service import current_plan, publish

    boot = cli_demo_bootstrap(services, SimpleNamespace(scenario=scenario, reset=False))
    if boot["status"] != "ok":
        return boot
    scope = services.settings.scope_key
    outcome = acquire(services, make_reader(services, scenario), cutoff=FIXTURE_CUTOFF, idempotency_key=f"demo-run:{scenario}")
    if outcome.status != "COMPLETE":
        return {"status": "error", "error": {"code": "ACQUISITION_REJECTED", "message": str(outcome.rejection)}, "source_run_id": outcome.run_id}
    budget = 10.0 if budget_seconds is None else float(budget_seconds)
    plans: dict[str, dict[str, Any]] = {}
    for purpose in ("EXECUTABLE", "FORECAST"):
        current, _ = current_plan(services, scope, purpose)
        if current is not None and current.published.source_run_id == outcome.run_id:
            plans[purpose] = {"status": "ALREADY_ACCEPTED", "plan_id": current.published.id, "candidate_id": current.published.candidate_id}
            continue
        candidate, run = schedule(services, source_run=outcome.run_id, purpose=Purpose(purpose), budget_seconds=budget, max_moves=max_moves)
        if not run.validation.valid:
            plans[purpose] = {"status": "INVALID", "candidate_id": candidate.id, "violations": len(run.validation.violations)}
            continue
        result = publish(services, candidate.id)
        plans[purpose] = {"status": result.status, "plan_id": result.plan_id, "candidate_id": candidate.id, "reasons": list(result.reasons)}
    if plans["EXECUTABLE"].get("plan_id") is None:
        return {"status": "error", "error": {"code": "NO_EXECUTABLE_PLAN", "message": "the executable plan was not accepted"},
                "source_run_id": outcome.run_id, "plans": plans}
    collections, recognition = observe_and_evaluate(services, scenario) if observe else ([], None)
    return {"status": "ok", "scenario": scenario, "source_run_id": outcome.run_id, "snapshot_id": outcome.snapshot_id, "plans": plans,
            "collections": collections, "recognition": recognition,
            "note": "fixture demonstration: invented data, TEST signing key, demonstration score policy"}


def observe_and_evaluate(services: Any, scenario: str = DEMO_SCENARIO) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    """At every fixture observation point not yet collected: collect, then evaluate every goal closed by that point (plus
    each shift's stored factory aggregate); then project recognition. One authority for ``demo-run`` and the pipeline's
    evaluator step. Replays skip collected points (older points would regress history) and append nothing twice."""
    from .analytics.progression import project
    from .performance.actuals import RUNS as OBSERVATION_RUNS
    from .performance.actuals import ObservationStore, collect_observations, manifest_points, observation_head, run_summary
    from .performance.lifecycle import GoalRegistry
    from .performance.score import aggregate, evaluate_all, summary

    scope = services.settings.scope_key
    collections: list[dict[str, Any]] = []
    points = manifest_points(services, scenario)
    head = services.repositories.head(observation_head(scope))
    latest = None if head is None else ObservationStore(services.repositories, scope).get(OBSERVATION_RUNS, head.record_id)
    for at in points:
        if latest is not None and at <= latest.as_of:
            collections.append({"as_of": at, "skipped": "ALREADY_COLLECTED"})
            continue
        observed = collect_observations(services, as_of=at)
        evaluations = evaluate_all(services, as_of=at)
        shift_of = {r.goal_id: r.shift_id for r in GoalRegistry.load(services).records.values()}
        by_shift: dict[str, list[Any]] = {}
        for e in evaluations:
            for goal_id in e.goal_ids:
                if goal_id in shift_of:
                    by_shift.setdefault(shift_of[goal_id], []).append(e)
        factories = [summary(aggregate(services, members, label=f"FACTORY {shift}", as_of=at))
                     for shift, members in sorted(by_shift.items()) if len(members) > 1]
        collections.append({"as_of": at, "observation": run_summary(observed), "evaluations": [summary(e) for e in evaluations],
                            "factory": factories})
    projection = project(services, as_of=points[-1]) if points else None
    return collections, None if projection is None else {"appended": len(projection.appended), "badges": len(projection.badges)}


def cli_demo_run(services: Any, args: Any) -> dict[str, Any]:
    if services.settings.mode != "fixture":
        return {"status": "invalid", "error": {"code": "FIXTURE_ONLY", "message": "demo-run runs in fixture mode only"}}
    return demo_run(services, scenario=getattr(args, "scenario", None) or DEMO_SCENARIO, budget_seconds=getattr(args, "budget_seconds", None),
                    max_moves=getattr(args, "max_moves", None))
