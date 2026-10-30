"""P22: complete-scope whole-WIP runs at scale (A03, A47, A70).

The bounded cases run in the default suite on the 51-aircraft synthetic scale fixture: one aircraft entirely blocked,
one missing standards, shared pools, scarce certificates, rework, parts, QA holds and forecast virtual support. Larger
sizes (``benchmark`` marker) run with ``--benchmark-sizes=100,150,300``, each in its own process so peak memory is
honest per size.
"""
from __future__ import annotations

import json
import resource
import subprocess
import sys
import time

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.acquisition import acquire, load_snapshot
from focus.data.fixture_builder import build_scale_fixture
from focus.data.fixture_reader import FixtureSourceReader
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import Disposition, Purpose
from focus.engine.max.optimizer import solver as S
from focus.engine.max.optimizer.orchestrator import KEY_LEVELS, schedule_snapshot
from focus.runtime import build_services
from focus.validation.plan import REQUIRED_CHECKS

PLAN_SECONDS_51 = 240          # generous bound for one 51-aircraft plan on a CI runner (measured ~15 s here)
RSS_MB_LIMIT = 2048


@pytest.fixture(scope="module")
def scale51(tmp_path_factory, app_root):
    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False)
    clock = FixedClock(FIXTURE_CUTOFF)
    services = build_services(settings, repositories=Repositories(FixtureStore(tmp_path_factory.mktemp("scale51")), mode="fixture",
                                                                  scope_key=settings.scope_key, clock=clock), clock=clock)
    datasets = build_scale_fixture(51)
    reader = FixtureSourceReader(app_root / "fixtures" / "inputs" / "manufacturing_demo",
                                 overrides={name: ds.to_json() for name, ds in datasets.items()})
    outcome = acquire(services, reader, cutoff=FIXTURE_CUTOFF, idempotency_key="scale51")
    assert outcome.status == "COMPLETE", outcome.rejection
    return services, load_snapshot(services.repositories, outcome.snapshot_id), datasets


@pytest.fixture(scope="module")
def plans51(scale51):
    services, snapshot, _ = scale51
    out = {}
    for purpose in (Purpose.EXECUTABLE, Purpose.FORECAST):
        began = time.monotonic()
        plan, run = schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=PLAN_SECONDS_51, max_moves=2)
        out[purpose] = (plan, run, time.monotonic() - began)
    return out


def test_a03_every_aircraft_and_job_survives_to_the_plan(scale51, plans51) -> None:
    _, snapshot, datasets = scale51
    lines = sorted(c.line for c in snapshot.census)
    assert len(lines) == 51 and len(datasets["master_task_list"].rows) == sum(c.jobs_total for c in snapshot.census)
    by_line = {}
    for job in snapshot.jobs:
        by_line.setdefault(job.line, []).append(job.id)
    for purpose, (plan, _, _) in plans51.items():
        states = {o.job: o for o in plan.job_dispositions}
        assert len(states) == len(snapshot.jobs), purpose                              # nothing dropped by eligibility or a cap
        assert all(o.disposition is not Disposition.EXCLUDED for o in states.values())
        blocked = [states[j] for j in by_line[2001] if states[j].disposition is not Disposition.VERIFIED_COMPLETE]
        assert blocked and all(o.disposition is Disposition.BLOCKED and o.reasons for o in blocked)     # the blocked aircraft stays
        missing = [states[j] for j in by_line[2002] if states[j].disposition is Disposition.BLOCKED]
        assert missing and all(any("STANDARD_MISSING" in r for r in o.reasons) for o in missing)
        assert all(o.reasons for o in states.values() if o.disposition is Disposition.DEFERRED)          # every wait has a cause


def test_a47_a70_zero_violations_zero_required_skips_full_coverage(plans51) -> None:
    for purpose, (plan, run, seconds) in plans51.items():
        receipt = run.validation
        assert receipt.valid and not receipt.violations, purpose
        assert not receipt.required_skips, (purpose, receipt.required_skips)
        assert all(receipt.check_counts[c] > 0 for c in REQUIRED_CHECKS if c.startswith("V")), (purpose, receipt.check_counts)
        assert run.seed_valid and run.best_valid
        assert plan.global_optimality_proven is False and plan.algorithm_status.proof_status == "HEURISTIC_NO_PROOF"
        assert run.solver["status"] == "DISABLED"
        assert seconds < PLAN_SECONDS_51, (purpose, seconds)                           # bounded runtime
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 < RSS_MB_LIMIT       # bounded memory (process high-water mark)


def test_a70_scale_inventory_exercises_the_hard_rules(scale51, plans51) -> None:
    _, snapshot, _ = scale51
    kinds = {p.kind.value for p in snapshot.phases}
    assert {"WORK", "IP_QA", "FINAL_QA", "RELEASE"} <= kinds                           # QA holds and root releases
    assert snapshot.rework_links and snapshot.shortages                                # rework and parts readiness
    synthetic = {p.id for p in snapshot.people if p.synthetic}
    executable, _, _ = plans51[Purpose.EXECUTABLE]
    forecast, _, _ = plans51[Purpose.FORECAST]
    assert synthetic and not any(c.person in synthetic for a in executable.assignments for c in a.crew)   # virtual support never executes
    assert any(c.person in synthetic for a in forecast.assignments for c in a.crew)                      # forecast-only, labeled
    pools = {}
    for a in executable.assignments:
        pools.setdefault(a.pool_key, set()).add(a.line)
    assert max(len(lines) for lines in pools.values()) > 1                              # aircraft share pool capacity


def test_a70_optional_solver_at_scale_is_truthful(scale51, plans51) -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    services, snapshot, _ = scale51
    off, _, _ = plans51[Purpose.EXECUTABLE]
    plan, run = schedule_snapshot(services, snapshot, purpose=Purpose.EXECUTABLE, budget_seconds=PLAN_SECONDS_51, max_moves=2,
                                  solver=True, solver_budget_seconds=10)
    receipt = run.solver
    assert receipt["status"] in S.STATUSES and receipt["status"] != "DISABLED" and receipt["enabled_by"] == "request"
    assert receipt["model"]["phases"] > 0 and receipt["budgets"]["wall_seconds"] == 10
    assert receipt["solve_seconds"] <= 10 * 1.5 + 5                                    # the wall-clock limit holds
    assert receipt["global_optimality_proven"] is False and plan.global_optimality_proven is False
    assert run.validation.valid and not run.validation.required_skips
    if receipt["status"] == "IMPROVED":
        level = next(p["improved_level"] for p in reversed(receipt["probes"]) if p["better"])
        assert level in KEY_LEVELS[:-1] and plan.id != off.id
    else:
        assert plan.id == off.id                                                        # the same-input baseline, unchanged


def pytest_generate_tests(metafunc) -> None:
    if "aircraft" in metafunc.fixturenames:
        raw = metafunc.config.getoption("--benchmark-sizes") or "100,150,300"
        metafunc.parametrize("aircraft", [int(x) for x in raw.split(",") if x.strip()])


@pytest.mark.benchmark
def test_complete_scope_benchmark_size(app_root, aircraft: int) -> None:
    cmd = [sys.executable, "-m", "focus.engine.max.optimizer.solver", "--size", str(aircraft), "--budget-seconds", "600",
           "--max-moves", "2", "--solver", "off", "--app-root", str(app_root)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, cwd=app_root)
    assert done.returncode == 0, done.stderr[-2000:]
    result = json.loads(done.stdout)
    assert S.benchmark_failures([result]) == []
    assert result["inventory"]["census_lines"] == aircraft
    assert result["max_rss_mb"] < 4096
