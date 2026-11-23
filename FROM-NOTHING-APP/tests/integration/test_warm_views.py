"""The team lead's first paint and reading ahead (D-33: the application opens at once at shift start, with the task lists).

The team lead's page first asks for its task list without the addition proposals (``proposals=0``). That form reads
only the aircraft the team is planned on in the shift, and gives the same actions, goal and counts as the full form.
``web.warm`` reads what those first requests need before anyone asks, into the same caches through the same verified
loaders, the shift about to start first.
"""
from __future__ import annotations

import pytest

import focus.display.view as V
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
    assert result["shifts"] and result["shifts_read"] == result["shifts"]            # every shift the plan's day has work in
    client = _client(w)
    context = client.get("/api/v1/context").get_json()["data"]["choices"]
    before = PART_CACHE.loads
    for team in context["teams"]:
        for shift in (1, 2, 3):
            response = client.get(f"/api/v1/team-lead?team={team}&shift={shift}&proposals=0")
            assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert PART_CACHE.loads == before                              # every part those first paints read was already there
    again = W.warm(w.services)
    assert again["parts_read"] == 0 and again["teams_read"] == again["teams"]       # nothing is read twice


def _loads(monkeypatch) -> list[str]:
    """The names of the parts read from the store from now on (a part already in the cache is not read)."""
    names: list[str] = []
    real = V.load_part

    def spy(repos, ref):
        if PART_CACHE.get(ref.sha256) is None:
            names.append(ref.name)
        return real(repos, ref)
    monkeypatch.setattr(V, "load_part", spy)
    return names


def _both_forms(client, team: str, shift: int) -> tuple[dict, dict]:
    full = client.get(f"/api/v1/team-lead?team={team}&shift={shift}").get_json()
    first = client.get(f"/api/v1/team-lead?team={team}&shift={shift}&proposals=0").get_json()
    return full, first


def test_the_first_paint_gives_the_same_task_list_goal_and_counts_as_the_full_form(world, monkeypatch) -> None:
    from datetime import UTC, datetime

    from focus.performance import actuals

    client = _client(world)
    teams = client.get("/api/v1/context").get_json()["data"]["choices"]["teams"]
    seen = {"actions": 0, "deferred": 0, "proposals": 0}
    for inside_the_shift in (False, True):
        if inside_the_shift:                                      # shift 1 of the fixture day runs 10:00 to 18:30 UTC
            monkeypatch.setattr(actuals, "knowledge_time", lambda services, at=None: datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
        for team in teams:
            for shift in (1, 2, 3):
                full, first = _both_forms(client, team, shift)
                assert {k: v for k, v in first.items() if k != "data"} == {k: v for k, v in full.items() if k != "data"}
                d, f = full["data"], dict(first["data"])
                deferred = f.pop("proposals_deferred")
                assert f.pop("proposals") == []
                assert f == {k: v for k, v in d.items() if k != "proposals"}       # actions, counts, goal, reasons, outcomes
                assert "proposals_deferred" not in d
                # additions are open exactly when the full form assesses them: inside the shift, with a goal of record
                assert deferred == (d["goal"] is not None and not d["proposal_reasons"])
                assert deferred or d["proposals"] == []
                seen["actions"] += len(d["actions"])
                seen["deferred"] += deferred
                seen["proposals"] += len(d["proposals"])
    assert seen["actions"] >= 20 and seen["deferred"] >= 2 and seen["proposals"] >= 2, seen


def test_the_first_paint_reads_only_the_aircraft_the_team_is_planned_on_in_the_shift(world, monkeypatch) -> None:
    client = _client(world)
    names = _loads(monkeypatch)
    first = client.get("/api/v1/team-lead?team=PROD-A&shift=1&proposals=0").get_json()["data"]
    planned = {a["line"] for a in first["actions"]}
    read = [n for n in names if n.startswith(("work:", "graph:"))]
    assert planned and sorted(read) == sorted([f"work:PROD-A:{line}" for line in planned] + [f"graph:{line}" for line in planned])
    names.clear()
    client.get("/api/v1/team-lead?team=PROD-A&shift=1")
    more = {int(n.split(":")[2]) for n in names if n.startswith("work:PROD-A:")}
    assert more and not more & planned                       # the full form goes on to the team's other aircraft
    names.clear()
    assert client.get("/api/v1/team-lead?team=PROD-A&shift=2&proposals=0").get_json()["data"]["actions"] == []
    assert not [n for n in names if n.startswith(("work:", "graph:"))]            # no work planned in that shift: nothing to read


def test_the_proposals_switch_is_a_checked_query_parameter(world) -> None:
    client = _client(world)
    assert client.get("/api/v1/team-lead?team=PROD-A&proposals=1").get_json()["data"].get("proposals_deferred") is None
    for bad in ("2", "-1", "yes"):
        response = client.get(f"/api/v1/team-lead?team=PROD-A&proposals={bad}")
        assert response.status_code == 400, (bad, response.status_code)


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
