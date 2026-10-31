"""Service container: wires settings, clock, policy and repositories (dependency injection).

Nothing here runs at import. ``build_services`` reads configuration files only
when called. It never connects to SQL or creates directories (fixture
directories are created lazily on the first write).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .clock import Clock, FixedClock, SystemClock, FIXTURE_CUTOFF
from .config import Settings
from .data.repositories import Repositories
from .data.store import FixtureStore, Store, UnavailableStore
from .policy import Policy, PolicyError, load_policy


@dataclass
class Services:
    settings: Settings
    clock: Clock
    repositories: Repositories
    _policy: Policy | None = None
    _policy_error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def policy(self) -> Policy:
        if self._policy is None and self._policy_error is None:
            try:
                self._policy = load_policy(self.settings.policy_dir, self.settings.policy_id)
            except PolicyError as exc:
                self._policy_error = f"{exc.code}: {exc.message}"
        if self._policy is None:
            raise PolicyError("POLICY_UNAVAILABLE", self._policy_error or "policy unavailable")
        return self._policy

    def policy_status(self) -> dict[str, Any]:
        try:
            policy = self.policy
        except PolicyError as exc:
            return {"status": "blocked", "reason": exc.message}
        return {"status": "ready", "policy_id": policy.id, "hash": policy.hash}


def default_store(settings: Settings) -> Store:
    if settings.mode == "fixture":
        return FixtureStore(settings.runtime_dir())
    if settings.sql_configured:
        from .data.sql_store import SqlStore  # lazy: live adapter only

        return SqlStore(settings)
    return UnavailableStore("Live mode without SQL credentials: no repository is available.")


def default_clock(settings: Settings) -> Clock:
    # Fixture mode is deterministic: the S18 cutoff is the fixture "now".
    return FixedClock(FIXTURE_CUTOFF) if settings.mode == "fixture" else SystemClock()


def build_services(settings: Settings | None = None, *, repositories: Repositories | None = None,
                   clock: Clock | None = None, store: Store | None = None, policy: Policy | None = None) -> Services:
    settings = settings or Settings.from_env()
    clock = clock or default_clock(settings)
    if settings.native_core:
        from .codec import enable_native_codec

        enable_native_codec(True)             # byte-identical canonical JSON and hashes from the native core, when installed
    if repositories is None:
        repositories = Repositories(store or default_store(settings), mode=settings.mode, scope_key=settings.scope_key, clock=clock)
    return Services(settings=settings, clock=clock, repositories=repositories, _policy=policy)
