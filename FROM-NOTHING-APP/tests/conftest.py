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
