"""Reading ahead in the web process (D-33: the application opens at once at shift start, with the task lists).

The first request for a view after a new plan pays for reading and decoding its display parts. ``web.warm`` reads what
the team lead views need before anyone asks, into the same caches through the same verified loaders.
"""
from __future__ import annotations

import pytest

from focus.display.store import PART_CACHE
from focus.web import warm as W
from focus.web.app import create_app
from tests.conftest import login
from tests.integration.test_display_package import _clear_caches, heads, template, world   # noqa: F401 - fixtures


def _client(w, persona: str = "manager"):
    app = create_app(services=w.services)
    app.config["TESTING"] = True
    client = app.test_client()
    login(client, persona)
    return client


def test_reading_ahead_loads_what_the_team_lead_views_read(world) -> None:
    w = world
    result = W.warm(w.services)
    run, plans = heads(w)
    assert result["status"] == "ok" and result["source_run_id"] == run.id and result["plans"] == plans
    assert result["teams"] >= 2 and result["teams_read"] == result["teams"] and result["parts_read"] > 0 and result["days"]
    assert 0 < result["cache_mb"] <= result["cache_budget_mb"]
    client = _client(w)
    context = client.get("/api/v1/context").get_json()["data"]["choices"]
    before = PART_CACHE.loads
    for team in context["teams"]:
        response = client.get(f"/api/v1/team-lead?team={team}")
        assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert PART_CACHE.loads == before                              # every part those views read was already there
    again = W.warm(w.services)
    assert again["parts_read"] == 0 and again["teams_read"] == again["teams"]       # nothing is read twice


def test_reading_ahead_gives_the_same_answers_as_a_cold_process(world) -> None:
    w = world
    cold = _client(w).get("/api/v1/team-lead?team=PROD-A").get_json()
    _clear_caches()
    W.warm(w.services)
    warm = _client(w).get("/api/v1/team-lead?team=PROD-A").get_json()
    assert warm["data"] == cold["data"] and warm["reasons"] == cold["reasons"]


def test_reading_ahead_stops_before_the_cache_is_full(world, monkeypatch) -> None:
    w = world
    monkeypatch.setattr(PART_CACHE, "budget", 1)                   # nothing fits: no team is read ahead, and nothing breaks
    result = W.warm(w.services)
    assert result["status"] == "ok" and result["teams"] >= 2 and result["teams_read"] <= 1
    assert _client(w).get("/api/v1/team-lead?team=PROD-A").status_code == 200


def test_nothing_to_read_ahead_without_an_accepted_plan(services_factory) -> None:
    _clear_caches()
    result = W.warm(services_factory())
    assert result["status"] == "nothing to read ahead" and result["teams_read"] == 0 and result["reasons"]


def test_the_background_reader_reads_once_and_again_when_a_head_moves(world, monkeypatch) -> None:
    w = world
    warmer = W.Warmer(w.services)
    first = warmer.check()
    assert first is not None and first["status"] == "ok" and warmer.seen == W.heads(w.services)
    assert warmer.check() is None and warmer.check() is None       # nothing moved: nothing is read
    monkeypatch.setattr(W, "heads", lambda services: ("another-run", "another-plan", None))
    assert warmer.check() is not None and warmer.seen == ("another-run", "another-plan", None)


def test_the_background_reader_is_off_unless_asked_for(world, monkeypatch) -> None:
    assert W.interval({}) == 0.0 and W.interval({"FOCUS_WARM_SECONDS": "0"}) == 0.0 and W.interval({"FOCUS_WARM_SECONDS": "x"}) == 0.0
    assert W.interval({"FOCUS_WARM_SECONDS": "-5"}) == 0.0 and W.interval({"FOCUS_WARM_SECONDS": "30"}) == 30.0
    monkeypatch.delenv("FOCUS_WARM_SECONDS", raising=False)
    assert W.start(world.services) is None
    calls: list[int] = []
    monkeypatch.setattr(W.Warmer, "check", lambda self: calls.append(1) or (_ for _ in ()).throw(RuntimeError("boom")))
    thread = W.start(world.services, seconds=0.01)
    assert thread is not None and thread.daemon and thread.name == "focus-warm"
    import time

    deadline = time.time() + 5
    while len(calls) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert len(calls) >= 3 and thread.is_alive()                   # a failed check never ends the reader
