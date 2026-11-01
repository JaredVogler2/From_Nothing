"""The whole-snapshot canon, kept only for the digital twin's opt-in path (``FOCUS_TWIN_FULL_LOAD=true``).

Every other projection reads display parts (``focus.display``). The twin reconciles every job, assignment and duty of the
fleet on each request, which the display package does not precompute yet, so by default the twin view returns a typed
``TWIN_NEEDS_FULL_LOAD`` state instead of loading GBs. A deployment with the memory for it can opt in; this canon then
loads the current admitted snapshot and decodes both accepted plan artifacts, exactly as the web did before.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .operations import reason


@dataclass
class FullCanon:
    services: Any
    source_run_id: str | None
    snapshot: Any | None
    executable: Any | None                  # publication.service.CurrentPlan
    executable_reason: str
    forecast: Any | None
    forecast_reason: str
    stale: bool
    reasons: list[dict[str, Any]]

    @classmethod
    def load(cls, services: Any, cache: Any) -> "FullCanon":
        from ..data.acquisition import get_run, load_snapshot, source_head_name
        from ..publication.repository import repository_for
        from ..publication.service import verify_publication

        repos = services.repositories
        policy = services.policy
        scope = services.settings.scope_key
        reasons: list[dict[str, Any]] = []
        head = repos.head(source_head_name(scope))
        snapshot = None
        if head is not None:
            found = get_run(repos, head.record_id)
            run = None if found is None else found[0]
            if run is not None and run.status == "COMPLETE" and run.snapshot_id:
                snapshot = cache.get_or_load(("snapshot", run.snapshot_id), lambda: load_snapshot(repos, run.snapshot_id))
        plans: dict[str, tuple[Any, str]] = {}
        repo = repository_for(services)
        for purpose in ("EXECUTABLE", "FORECAST"):
            plan_head = repo.head(scope, purpose)
            if plan_head is None:
                plans[purpose] = (None, "NO_ACCEPTED_PLAN")
                continue
            plans[purpose] = cache.get_or_load(
                ("plan", plan_head.record_id, plan_head.version),
                lambda h=plan_head, p=purpose: verify_publication(services, h.record_id, scope=scope, purpose=p, head_version=h.version),
                keep=lambda v: v[0] is not None)
        stale = False
        if snapshot is not None:
            stale = services.clock.now() - snapshot.cutoff > timedelta(hours=policy.int("dispatch.max_source_age_hours"))
            if stale:
                reasons.append(reason("SOURCE_STALE", f"Source cut off at {snapshot.cutoff.isoformat()} is older than the freshness policy.",
                                      "Shown for history only; acquire a current source run."))
        return cls(services, None if head is None else head.record_id, snapshot, plans["EXECUTABLE"][0], plans["EXECUTABLE"][1],
                   plans["FORECAST"][0], plans["FORECAST"][1], stale, reasons)

    def current(self, purpose: str) -> Any:
        return self.executable if purpose == "EXECUTABLE" else self.forecast

    def plan(self, purpose: str) -> Any:
        cur = self.current(purpose)
        return None if cur is None else cur.artifact.candidate

    def plan_id(self, purpose: str) -> str | None:
        cur = self.current(purpose)
        return None if cur is None else cur.published.id
