"""Display-backed projections equal the projections that loaded whole snapshots and plan artifacts.

The world is the complete fixture pipeline (``demo_run``): both plans accepted with their display packages, observations
collected, goals evaluated. Every projection runs twice on the same store, once through ``focus.web.serializers`` (display
parts only) and once through ``tests.support.reference_views`` (the projections as they were, loading the whole snapshot
and decoding every accepted artifact), for every demo persona and every filter the demo can exercise. Their output must be
identical, apart from the new paging fields of the lists that page now.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Callable

import pytest

import tests.support.reference_views as R
from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.runtime import build_services
from focus.web import serializers as S
from focus.web.security import DEMO_PERSONAS, ApiError, RequestContext, _scope_from

PAGING = {"total", "offset", "limit", "lines"}


def services_on(root, app_root, **overrides: Any):
    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False, **overrides)
    clock = FixedClock(FIXTURE_CUTOFF)
    return build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                          clock=clock)


@pytest.fixture(scope="module")
def world(tmp_path_factory, app_root):
    from focus.pipeline import demo_run

    root = tmp_path_factory.mktemp("display_views") / "runtime"
    services = services_on(root, app_root)
    result = demo_run(services, budget_seconds=5, max_moves=5)
    assert result["status"] == "ok", result
    return root, services


def ctx_of(persona: str) -> RequestContext:
    p = DEMO_PERSONAS[persona]
    return RequestContext(f"rq-{persona}", f"demo:{persona}", p["display"], frozenset(p["grants"]), _scope_from(p["teams"], p["person"], p["display"]),
                          "fixture", True, persona)


def outcome(call: Callable[[], Any]) -> Any:
    """A projection's comparable outcome: its fields, or the API error it raised."""
    try:
        result = call()
    except ApiError as exc:
        return ("ApiError", exc.status, exc.code)
    if isinstance(result, tuple):                     # (projection, rows) / (projection, next page) forms
        result = result[0] if isinstance(result[0], (R.Projection, S.Projection)) else result
    if isinstance(result, (R.Projection, S.Projection)):
        out = asdict(result)
        if isinstance(out.get("data"), dict):
            out["data"] = {k: v for k, v in out["data"].items() if k not in PAGING}
        return out
    return result


def first_difference(a: Any, b: Any, path: str = "$") -> str | None:
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return f"{path}: {type(a).__name__} {a!r:.200} != {type(b).__name__} {b!r:.200}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'reference' if k in a else 'display'}"
            found = first_difference(a[k], b[k], f"{path}.{k}")
            if found:
                return found
        return None
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r:.200} != {b!r:.200}"


def same(old: Callable[[], Any], new: Callable[[], Any], label: str) -> None:
    a, b = outcome(old), outcome(new)
    diff = first_difference(a, b)
    assert diff is None, f"{label}: {diff}"


def canons(services):
    R.CACHE.clear()
    R.PROJECTIONS.clear()
    S.CACHE.clear()
    S.PROJECTIONS.clear()
    return R.Canon.load(services), S.Canon.load(services)


def demo_days(old) -> list[date | None]:
    days = list(old.executable.artifact.obligation_dates) if old.executable else []
    return [None] + days + ([days[-1] + timedelta(days=1)] if days else [])


PERSONAS = sorted(DEMO_PERSONAS)


@pytest.mark.parametrize("persona", PERSONAS)
def test_fleet_aircraft_jobs_and_plans_match(world, persona) -> None:
    root, services = world
    old, new = canons(services)
    ctx = ctx_of(persona)
    same(lambda: R.fleet(old, ctx), lambda: S.fleet(new, ctx), "fleet")
    for line in sorted(c.line for c in old.snapshot.census) + [999]:
        same(lambda: R.aircraft(old, ctx, line), lambda: S.aircraft(new, ctx, line), f"aircraft {line}")
    teams = sorted({j.team for j in old.snapshot.jobs if j.team}) + [None]
    lines = sorted({j.key.line for j in old.snapshot.jobs}) + [None]
    for line in lines:
        for team in teams:
            for disposition in (None, "admitted", "blocked", "quarantined", "excluded", "verified_complete"):
                label = f"jobs line={line} team={team} disposition={disposition}"
                try:
                    _, rows = R.jobs_list(old, ctx, line=line, team=team, disposition=disposition)
                    expected: Any = rows
                except ApiError as exc:
                    expected = ("ApiError", exc.status, exc.code)
                try:
                    pages, offset = [], 0
                    while True:
                        _, page, total = S.jobs_page(new, ctx, line=line, team=team, disposition=disposition, offset=offset, limit=3)
                        pages += page
                        offset += 3
                        if offset >= total:
                            break
                    got: Any = pages
                except ApiError as exc:
                    got = ("ApiError", exc.status, exc.code)
                assert first_difference(expected, got) is None, f"{label}: {first_difference(expected, got)}"
                same(lambda: R.jobs_list(old, ctx, line=line, team=team, disposition=disposition)[0],
                     lambda: S.jobs_page(new, ctx, line=line, team=team, disposition=disposition, offset=0, limit=10_000)[0], label)
    for job in [j.key.id for j in old.snapshot.jobs] + ["J0000000000000000000"]:
        same(lambda: R.job_detail(old, ctx, job), lambda: S.job_detail(new, ctx, job), f"job {job}")
    from focus.publication.repository import repository_for

    for plan_id in repository_for(services).accepted_ids():
        same(lambda: R.plan_view(services, ctx, plan_id), lambda: S.plan_view(services, ctx, plan_id), f"plan {plan_id}")
    for purpose in ("EXECUTABLE", "FORECAST"):
        cand = old.plan(purpose).id
        same(lambda: R.plan_view(services, ctx, cand), lambda: S.plan_view(services, ctx, cand), f"candidate {cand}")


@pytest.mark.parametrize("persona", PERSONAS)
def test_team_shift_views_match(world, persona) -> None:
    root, services = world
    old, new = canons(services)
    ctx = ctx_of(persona)
    teams = sorted({p.key.team for p in old.snapshot.pools}) + ["NOPE"]
    for day in demo_days(old):
        for team in teams:
            for shift in (1, 2, 3):
                same(lambda: R.shiftbook(old, ctx, team=team, day=day, shift=shift), lambda: S.shiftbook(new, ctx, team=team, day=day, shift=shift),
                     f"shiftbook {team} {day} S{shift}")
                same(lambda: R.team_lead(old, ctx, team=team, day=day, shift=shift), lambda: S.team_lead(new, ctx, team=team, day=day, shift=shift),
                     f"team_lead {team} {day} S{shift}")
                same(lambda: R.turnover(old, ctx, team=team, day=day, shift=shift), lambda: S.turnover(new, ctx, team=team, day=day, shift=shift),
                     f"turnover {team} {day} S{shift}")
        for shift in (None, 1, 2, 3):
            same(lambda: R.workforce(old, ctx, day=day, shift=shift), lambda: S.workforce(new, ctx, day=day, shift=shift), f"workforce {day} {shift}")
        for purpose in ("EXECUTABLE", "FORECAST"):
            same(lambda: R.capacity(old, ctx, day=day, purpose=purpose), lambda: S.capacity(new, ctx, day=day, purpose=purpose),
                 f"capacity {day} {purpose}")
            same(lambda: R.budget(old, ctx, day=day, purpose=purpose), lambda: S.budget(new, ctx, day=day, purpose=purpose),
                 f"budget {day} {purpose}")
            for team in teams[:-1] + [None]:
                same(lambda: R.gantt(old, ctx, day=day, team=team, purpose=purpose), lambda: S.gantt(new, ctx, day=day, team=team, purpose=purpose),
                     f"gantt {day} {team} {purpose}")
    people = sorted({p.key.native_id for p in old.snapshot.people if not p.synthetic})
    for native in people[:12] + [None, "NOBODY"]:
        for day in demo_days(old):
            same(lambda: R.myday(old, ctx, person_native=native, day=day), lambda: S.myday(new, ctx, person_native=native, day=day),
                 f"myday {native} {day}")


@pytest.mark.parametrize("persona", PERSONAS)
def test_fleet_wide_views_match(world, persona) -> None:
    root, services = world
    old, new = canons(services)
    ctx = ctx_of(persona)
    same(lambda: R.commitments(old, ctx), lambda: S.commitments(new, ctx), "commitments")
    same(lambda: R.economics(old, ctx), lambda: S.economics(new, ctx), "economics")
    same(lambda: R.shortages(old, ctx), lambda: S.shortages(new, ctx), "shortages")
    same(lambda: R.quality(old, ctx), lambda: S.quality(new, ctx), "quality")
    same(lambda: R.standards(old, ctx), lambda: S.standards(new, ctx), "standards")
    same(lambda: R.development(old, ctx), lambda: S.development(new, ctx), "development")
    teams = sorted({p.key.team for p in old.snapshot.pools})
    shift_ids = sorted({r.shift_id for r in R.registry(services).records.values()})
    for shift_id in [None] + shift_ids[:6]:
        for team in [None] + teams:
            for name in ("performance", "claims_view"):
                same(lambda: getattr(R, name)(services, ctx, shift_id=shift_id, team=team),
                     lambda: getattr(S, name)(services, ctx, shift_id=shift_id, team=team), f"{name} {shift_id} {team}")
    for team in [None] + teams:
        for name in ("performance_history", "recognition", "progression_view"):
            same(lambda: getattr(R, name)(services, ctx, team=team), lambda: getattr(S, name)(services, ctx, team=team), f"{name} {team}")
    for name in ("arena", "history_view", "ie_reviews_view", "scenarios_view"):
        same(lambda: getattr(R, name)(services, ctx), lambda: getattr(S, name)(services, ctx), name)


def test_registries_match(world) -> None:
    root, services = world
    canons(services)
    old, new = R.registry(services), S.registry(services)
    for field in ("day_plan", "plan_snapshot", "records", "goals", "unit_start", "unit_end", "unit_crew", "plan_knowledge", "committed_units",
                  "rejected"):
        assert getattr(old, field) == getattr(new, field), field
