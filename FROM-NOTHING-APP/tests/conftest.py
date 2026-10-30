"""Shared pytest fixtures. Tests never need SQL credentials or network access."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:  # pragma: no cover - editable install normally provides this
    sys.path.insert(0, str(APP_ROOT))

from focus.clock import FIXTURE_CUTOFF, FixedClock  # noqa: E402
from focus.config import Settings  # noqa: E402
from focus.data.repositories import Repositories  # noqa: E402
from focus.data.store import FixtureStore  # noqa: E402
from focus.policy import load_policy  # noqa: E402
from focus.runtime import build_services  # noqa: E402


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--benchmark-sizes", default="", help="complete-scope benchmark aircraft counts, e.g. 100,150,300 (P22; slow)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """``benchmark`` tests (sizes beyond the bounded 51-aircraft cases) run only when sizes are requested."""
    if config.getoption("--benchmark-sizes"):
        return
    skip = pytest.mark.skip(reason="complete-scope benchmark sizes run only with --benchmark-sizes=100,150,300 (slow)")
    for item in items:
        if item.get_closest_marker("benchmark") is not None:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def app_root() -> Path:
    return APP_ROOT


@pytest.fixture(scope="session")
def policy(app_root: Path):
    return load_policy(app_root / "config" / "policies", "rebuild-v1")


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(FIXTURE_CUTOFF)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("FOCUS_") or key in ("PORT", "VCAP_APPLICATION", "VCAP_SERVICES", "RSTUDIO_PRODUCT", "CONNECT_CONTENT_GUID"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def fixture_settings(tmp_path: Path, app_root: Path, clean_env: None) -> Settings:
    return Settings.from_env({}, app_root=app_root, scenario_id="test_scenario")


@pytest.fixture()
def tmp_store(tmp_path: Path) -> FixtureStore:
    return FixtureStore(tmp_path / "runtime")


@pytest.fixture()
def services_factory(tmp_path: Path, app_root: Path, clean_env: None) -> Callable[..., Any]:
    def make(**overrides: Any):
        store = overrides.pop("store", None)
        settings = Settings.from_env({}, app_root=app_root, **overrides)
        clock = FixedClock(FIXTURE_CUTOFF)
        repos = Repositories(store or FixtureStore(tmp_path / "runtime"), mode=settings.mode, scope_key=settings.scope_key, clock=clock)
        return build_services(settings, repositories=repos, clock=clock)

    return make


@pytest.fixture()
def make_client(services_factory: Callable[..., Any]) -> Callable[..., Any]:
    from focus.web.app import create_app

    def make(**overrides: Any):
        services = services_factory(**overrides)
        app = create_app(services=services)
        app.config["TESTING"] = True
        return app, app.test_client(), services

    return make


def login(client: Any, persona: str) -> str:
    """Sign in a demo persona and return a CSRF token for commands."""
    response = client.post("/auth/demo-login", json={"persona": persona})
    assert response.status_code == 200, response.get_data(as_text=True)
    context = client.get("/api/v1/context").get_json()
    return context["data"]["csrf_token"]


def acquire_demo(services, key: str = "test-demo", cutoff=FIXTURE_CUTOFF, reader=None, fault=None):
    from focus.data.acquisition import acquire, make_reader

    return acquire(services, reader or make_reader(services, "manufacturing_demo"), cutoff=cutoff, idempotency_key=key, fault=fault)


@pytest.fixture()
def demo(services_factory):
    """Services with the manufacturing_demo source run acquired into a temp fixture store."""
    services = services_factory()
    outcome = acquire_demo(services)
    assert outcome.status == "COMPLETE", outcome.rejection
    from focus.data.acquisition import load_snapshot

    snapshot = load_snapshot(services.repositories, outcome.snapshot_id)
    return services, outcome, snapshot


@pytest.fixture(scope="session")
def demo_snapshot(tmp_path_factory, app_root):
    """Session-cached demo snapshot for read-only domain assertions."""
    from focus.data.acquisition import load_snapshot

    root = tmp_path_factory.mktemp("demo_session")
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root / "runtime"), mode="fixture", scope_key=settings.scope_key, clock=clock)
    services = build_services(settings, repositories=repos, clock=clock)
    outcome = acquire_demo(services, key="session-demo")
    assert outcome.status == "COMPLETE", outcome.rejection
    return load_snapshot(repos, outcome.snapshot_id)


def job_id(snapshot, line: int, soi: str) -> str:
    for job in snapshot.jobs:
        if job.line == line and job.key.native_id == soi:
            return job.id
    raise KeyError((line, soi))


OBSERVATION_SHIFT = "DEMO:2026-10-26:S1"


def observed_world(root: Path, app_root: Path, *, points: tuple[str, ...] = (), teams: tuple[str, ...] = ("PROD-A", "PROD-B", "QA"), policy=None):
    """Publish the 2026-10-26 obligation plan, then collect the S18 observation feeds and evaluate at each point (P17/P18)."""
    from types import SimpleNamespace

    from focus.codec import parse_utc
    from focus.data.acquisition import acquire, make_reader, snapshot_for_run
    from focus.domain import Purpose
    from focus.engine.commitments import import_fixture_incumbent
    from focus.engine.max.optimizer.orchestrator import schedule
    from focus.performance.actuals import collect_observations
    from focus.performance.score import evaluate_shift
    from focus.publication.service import publish

    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock)
    services = build_services(settings, repositories=repos, clock=clock, policy=policy)
    run = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="observed-world")
    import_fixture_incumbent(services)
    candidate, _ = schedule(services, source_run=run.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    result = publish(services, candidate.id)
    assert result.status == "ACCEPTED", result.reasons
    runs, evals = {}, {}
    for text in points:
        at = parse_utc(text)
        runs[at] = collect_observations(services, as_of=at)
        evals[at] = {team: evaluate_shift(services, team, OBSERVATION_SHIFT, as_of=at) for team in teams}
    return SimpleNamespace(services=services, root=root, runs=runs, evals=evals, plan_id=result.plan_id, snap=snapshot_for_run(services, run.run_id))
