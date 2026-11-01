"""P20: operational view projections (A59, A60, A63, A68) and the scenario prerequisite (isolation, cancel).

The world is the complete fixture pipeline (``demo_run``): both plans accepted, observations collected at the
S18 points, every closed goal evaluated with its stored factory aggregate, and recognition projected.
"""
from __future__ import annotations

import shutil
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.runtime import build_services
from focus.web.app import create_app
from tests.conftest import OBSERVATION_SHIFT, login


def services_on(root: Path, app_root: Path, **overrides: Any):
    settings = Settings.from_env({}, app_root=app_root, **overrides)
    clock = FixedClock(FIXTURE_CUTOFF)
    return build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock), clock=clock)


@pytest.fixture(scope="module")
def demo(tmp_path_factory, app_root):
    from focus.pipeline import demo_run

    root = tmp_path_factory.mktemp("views_api") / "runtime"
    services = services_on(root, app_root, inprocess_worker=False)
    result = demo_run(services, budget_seconds=5, max_moves=5)
    assert result["status"] == "ok", result
    return root, services, result


def client(root: Path, app_root: Path, persona: str | None = None, **overrides: Any):
    overrides.setdefault("inprocess_worker", False)
    services = services_on(root, app_root, **overrides)
    app = create_app(services=services)
    app.config["TESTING"] = True
    c = app.test_client()
    token = login(c, persona) if persona else None
    return c, services, token


def get(c, path: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = c.get(path)
    assert r.status_code == 200, (path, r.get_data(as_text=True)[:400])
    return r.get_json()


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------- the pipeline itself

def test_demo_run_is_idempotent_byte_for_byte(demo, app_root) -> None:
    from focus.pipeline import demo_run

    root, _, first = demo
    assert {p["status"] for p in first["plans"].values()} == {"ACCEPTED"} and len(first["collections"]) == 3
    before = _tree(root)
    again = demo_run(services_on(root, app_root, inprocess_worker=False), budget_seconds=5, max_moves=5)
    assert {p["status"] for p in again["plans"].values()} == {"ALREADY_ACCEPTED"}
    assert [c.get("skipped") for c in again["collections"]] == ["ALREADY_COLLECTED"] * 3
    assert again["recognition"] == {"appended": 0, "badges": 0}               # the projection replays and appends nothing
    assert _tree(root) == before                                              # nothing appended or rewritten


# ---------------------------------------------------------------------- A59 / A60 / A63: one canonical score everywhere

def test_a59_arena_history_and_factory_pool_the_same_stored_evaluations(demo, app_root) -> None:
    from focus.performance.score import formula

    root, services, _ = demo
    c, _, _ = client(root, app_root, "manager")
    perf = get(c, f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}")["data"]
    goals = {g["team"]: g for g in perf["goals"]}
    assert perf["pooled"]["persisted"] is True                                  # the factory aggregate stored by demo_run
    arena = get(c, "/api/v1/arena")["data"]["standings"]
    by_team = {r["team"]: r for r in arena if r["pooled"]}
    for team, g in goals.items():                                             # one goal per team: the pool is that goal
        assert by_team[team]["pooled"]["evaluation_id"] == g["evaluation"]["evaluation_id"]
        assert by_team[team]["pooled"]["score"] == g["evaluation"]["score"]
    ranked = [r["team"] for r in arena if r["position"] is not None]
    assert ranked == sorted(ranked, key=lambda t: -Fraction(goals[t]["evaluation"]["final"]))
    sums = {k: sum(Fraction(g["evaluation"][k]) for g in goals.values()) for k in ("B", "E", "Cp", "U", "O")}
    assert perf["pooled"]["score"] == str(formula(sums["B"], sums["E"], sums["Cp"], sums["U"], sums["O"], services.policy)["score"])
    history = get(c, "/api/v1/history")["data"]
    series = {s["team"]: s for s in history["evaluation_series"]}
    for team, g in goals.items():
        assert series[team]["current_evaluation_id"] == g["evaluation"]["evaluation_id"]
    assert {p["purpose"] for p in history["plans"]} == {"EXECUTABLE", "FORECAST"} and all(p["current"] for p in history["plans"])


def test_a60_oos_is_charged_once_in_o_and_never_reduces_xp(demo, app_root) -> None:
    root, services, _ = demo
    c, _, _ = client(root, app_root, "manager")
    prod_b = next(g for g in get(c, f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}&team=PROD-B")["data"]["goals"])
    charged = [o for o in prod_b["oos"] if o["charged"]]
    assert charged and Fraction(prod_b["evaluation"]["O"]) > 0                  # the planned OOS sits once in O
    assert {u["unit_id"] for u in prod_b["units"] if u["oos_charged"]} == {u for o in charged for u in o["unit_ids"]}   # once per unit
    rate = Fraction(services.policy.str("score.oos_rate"))
    assert Fraction(prod_b["evaluation"]["O"]) == rate * sum(o["affected_effort"] for o in charged)
    assert Fraction(prod_b["evaluation"]["Cp"]) == sum(u["effort"] for u in prod_b["units"] if u["status"] == "EARNED")   # Cp intact
    xp = get(c, "/api/v1/progression?team=PROD-B")["data"]["teams"][0]["xp"]
    events = get(c, "/api/v1/recognition?team=PROD-B")["data"]["events"]
    assert Fraction(xp["num"], xp["den"]) == sum(Fraction(e["value"]["num"], e["value"]["den"]) for e in events)      # no OOS term in XP


def test_a63_projection_replay_appends_nothing_and_arena_shows_lineage(demo, app_root) -> None:
    from focus.analytics.progression import project

    root, services, result = demo
    c, _, _ = client(root, app_root, "manager")
    before = get(c, "/api/v1/recognition")["data"]["events"]
    replay = project(services, as_of=result["collections"][-1]["as_of"])
    assert replay.appended == [] and replay.badges == []
    assert get(c, "/api/v1/recognition")["data"]["events"] == before
    recent = get(c, "/api/v1/arena")["data"]["recent_events"]
    assert recent and all(e["evidence"] for e in recent)                     # every award names its earning lineage
    development = get(c, "/api/v1/development")["data"]
    assert all(t["qualification_effect"] == "none" for t in development["progression"])


# ---------------------------------------------------------------------- operational projections

def test_team_lead_ranks_by_the_dispatch_rule_with_reasons(demo, app_root) -> None:
    from focus.publication.dispatch import assess_action
    from focus.web import serializers as S

    root, services, _ = demo
    c, svc, _ = client(root, app_root, "lead-prod-b")
    d = get(c, "/api/v1/team-lead?date=2026-10-26&shift=1")["data"]
    assert d["team"] == "PROD-B" and d["actions"] and [a["rank"] for a in d["actions"]] == list(range(1, len(d["actions"]) + 1))
    flags = [a["dispatchable"] for a in d["actions"]]
    assert flags == sorted(flags, reverse=True)                               # every dispatchable action ranks first
    from focus.domain import Purpose
    from focus.validation.input import ValidationInput
    from focus.web.full_canon import FullCanon

    full = FullCanon.load(svc, S.CACHE)                                         # the whole snapshot and artifact, as dispatch_check reads them
    plan = {a.id: a for a in full.plan("EXECUTABLE").assignments}
    inp = ValidationInput.build(full.snapshot, svc.policy, Purpose.EXECUTABLE)
    for a in d["actions"]:                                                     # the same rule dispatch_check applies, from display parts
        expected = {code for code, _ in assess_action(svc, plan[a["assignment_id"]], full.snapshot, inp, svc.clock.now())}
        assert {r["code"] for r in a["reasons"]} == expected
        assert all(m["name"] for m in a["crew"])
    assert any(not a["dispatchable"] and "PREDECESSOR_INCOMPLETE" in {r["code"] for r in a["reasons"]} for a in d["actions"])
    assert [r["code"] for r in d["proposal_reasons"]] == ["SHIFT_ENDED"]      # after the observed points the shift is over
    r = c.get("/api/v1/team-lead?team=PROD-A")
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "OUT_OF_SCOPE"


def test_turnover_open_units_come_from_the_canonical_evidence(demo, app_root) -> None:
    from focus.performance import history

    root, services, _ = demo
    c, _, _ = client(root, app_root, "manager")
    d = get(c, "/api/v1/turnover?team=QA&date=2026-10-26&shift=1")["data"]
    e = history.current(services, services.settings.scope_key, history.series_for_goal(services, d["goal_id"]))
    proof = history.evidence(services, e.evidence_hash)
    assert {u["unit_id"] for u in d["open_units"]} == {f.unit_id for f in proof.unit_facts if f.status != "EARNED"}
    assert d["evaluation"]["evaluation_id"] == e.id and d["next_shift_id"] == "DEMO:2026-10-26:S2"


def test_budget_gantt_and_capacity_reconcile_with_the_plan(demo, app_root) -> None:
    from focus.web import serializers as S

    from focus.web.full_canon import FullCanon

    root, services, _ = demo
    c, svc, _ = client(root, app_root, "manager")
    plan = FullCanon.load(svc, S.CACHE).plan("EXECUTABLE")
    budget = get(c, "/api/v1/budget?date=2026-10-26")["data"]
    gantt = get(c, "/api/v1/gantt?date=2026-10-26")["data"]
    capacity = get(c, "/api/v1/capacity?date=2026-10-26")["data"]
    for row in budget["team_shifts"]:
        assert row["nva_minutes"] == sum(row["duty_minutes"].get(k, 0) for k in ("TRAVEL", "WAIT", "TOOL", "OTHER"))
        cap = next(p for p in capacity["pools"] if p["shift_id"] == row["shift_id"] and p["team"] == row["team"])
        assert row["work_minutes"] == cap["work_minutes"]                     # one duty ledger behind both views
    bars = sum(len(p["bars"]) for p in gantt["people"])
    on_day = sum(1 for d in plan.duties if d.shift_id and d.shift_id.split(":")[1] == "2026-10-26")
    assert bars == on_day and [s["shift"] for s in gantt["shifts"]] == [1, 2, 3]
    for pool in capacity["pools"]:
        for skill in pool["skills"]:
            assert skill["shortfall"] == max(0, skill["crew_size"] - skill["qualified_admitted"])
            assert skill["seat_assignments"] >= skill["crew_size"]


def test_supply_quality_standards_and_project_projections(demo, app_root) -> None:
    root, _, _ = demo
    c, _, _ = client(root, app_root, "manager")
    shortages = get(c, "/api/v1/shortages")["data"]["shortages"]
    assert {s["readiness"] for s in shortages} <= {"RECEIVED", "RECEIVED_LATE", "ETA_CONDITIONAL", "UNKNOWN_ETA", "UNLINKED"}
    assert any(s["readiness"] == "ETA_CONDITIONAL" for s in shortages)         # an ETA is a conditional forecast
    quality = get(c, "/api/v1/quality")["data"]
    assert quality["counts"]["total"] == len(quality["holds"]) and quality["competition"]
    standards = get(c, "/api/v1/standards")["data"]
    assert standards["durations"] and any(q["kind"] == "DATA_GAP" for q in standards["review_queue"])
    project = get(c, "/api/v1/aircraft/1305")["data"]
    assert project["timeline"] and len(project["completion_history"]) == 2     # one completion per accepted plan
    fleet = get(c, "/api/v1/fleet")["data"]
    assert fleet["burndown"]["open_jobs"] >= fleet["burndown"]["never_finishing"] >= 0


def test_new_reads_never_write(demo, app_root) -> None:
    root, _, _ = demo
    before = _tree(root)
    for persona in ("manager", "lead-prod-b", "ie", "supply"):
        c, _, _ = client(root, app_root, persona)
        for path in ("/api/v1/team-lead?date=2026-10-26", "/api/v1/turnover?date=2026-10-26", "/api/v1/gantt", "/api/v1/budget", "/api/v1/standards",
                     "/api/v1/development", "/api/v1/arena", "/api/v1/history", "/api/v1/scenarios", "/api/v1/context"):
            assert c.get(path).status_code in (200, 400, 403), (persona, path)
    assert _tree(root) == before


# ---------------------------------------------------------------------- scenarios: isolated, bounded, never publishable

def test_scenario_is_isolated_and_never_publishable(demo, app_root, tmp_path) -> None:
    from focus.analytics.scenarios import SCENARIO_RESULTS
    from focus.publication.service import publish
    from focus.workers import WorkerService

    src, _, _ = demo
    root = tmp_path / "runtime"
    shutil.copytree(src, root)
    c, services, token = client(root, app_root, "manager")
    heads = {p.name: p.read_bytes() for p in (root / "manufacturing_demo" / "heads").glob("*.json")} if (root / "manufacturing_demo").exists() \
        else {p.name: p.read_bytes() for p in (root / "heads").glob("*.json")}
    guarded = ("frozen_goals", "published_plans", "evaluations", "recognition_events", "badge_awards", "lifecycle_events", "excusal_decisions",
               "goal_amendments", "delivery_events", "accepted_plans")
    store_root = root / "manufacturing_demo" if (root / "manufacturing_demo").exists() else root
    counts = {g: len(list((store_root / "objects" / g).glob("*"))) if (store_root / "objects" / g).exists() else 0 for g in guarded}
    source = get(c, "/api/v1/context")["data"]["source"]["run_id"]
    body = {"source_run_id": source, "budget_seconds": 6, "idempotency_key": "scn-test-0001",
            "changes": [{"type": "virtual_staffing", "team": "PROD-B", "date": "2026-10-26", "shift": 1, "add": 1}, {"type": "duration_statistic", "statistic": "P90"}]}
    for bad in ([{"type": "approve_plan"}], [{"type": "virtual_staffing", "team": "PROD-B", "date": "2026-10-26", "shift": 1, "add": 1, "mock_data": False}],
                [{"type": "virtual_staffing", "team": "PROD-B", "date": "2026-10-26", "shift": 1, "add": 11}]):
        r = c.post("/api/v1/scenarios", json=dict(body, changes=bad), headers={"X-CSRF-Token": token})
        assert r.status_code == 422, r.get_json()                             # spoofed or unsupported changes never run
    lead, _, ltoken = client(root, app_root, "lead-prod-b")
    assert lead.post("/api/v1/scenarios", json=body, headers={"X-CSRF-Token": ltoken}).status_code == 403
    queued = c.post("/api/v1/scenarios", json=body, headers={"X-CSRF-Token": token})
    assert queued.status_code == 202
    WorkerService(services).run_until_idle()
    run = get(c, f"/api/v1/runs/{queued.get_json()['data']['run_id']}")["data"]
    assert run["status"] == "completed", run
    scenario = get(c, f"/api/v1/scenarios/{run['result_receipt']['scenario_id']}")
    d = scenario["data"]
    assert scenario["purpose"] == "SCENARIO" and d["capability"] == "SCENARIO_ONLY" and d["baseline_valid"] and d["scenario_valid"]
    assert any(line["delta_minutes"] not in (None, 0) for line in d["comparison"]["lines"])     # P90 durations move a completion
    assert d["comparison"]["scenario"]["virtual_assignments"] >= d["comparison"]["baseline"]["virtual_assignments"]
    rejected = publish(services, d["scenario_candidate_id"])
    assert rejected.status != "ACCEPTED" and any("PURPOSE_NOT_PUBLISHABLE" in x for x in rejected.reasons)
    after_heads = {p.name: p.read_bytes() for p in (store_root / "heads").glob("*.json")}
    assert {k: v for k, v in after_heads.items() if k in heads} == heads        # no live head moved
    assert {g: len(list((store_root / "objects" / g).glob("*"))) if (store_root / "objects" / g).exists() else 0 for g in guarded} == counts
    assert services.repositories.list_ids(SCENARIO_RESULTS) == [d["scenario_id"]]


def test_queued_runs_can_be_cancelled_and_never_execute(demo, app_root, tmp_path) -> None:
    from focus.workers import WorkerService

    src, _, _ = demo
    root = tmp_path / "runtime"
    shutil.copytree(src, root)
    c, services, token = client(root, app_root, "manager")
    source = get(c, "/api/v1/context")["data"]["source"]["run_id"]
    body = {"source_run_id": source, "budget_seconds": 6, "idempotency_key": "scn-cancel-0001",
            "changes": [{"type": "daily_cost", "line": 1301, "daily_rate_cents": 500000}]}
    run_id = c.post("/api/v1/scenarios", json=body, headers={"X-CSRF-Token": token}).get_json()["data"]["run_id"]
    other, _, otoken = client(root, app_root, "ie")
    assert other.post(f"/api/v1/runs/{run_id}/cancel", json={}, headers={"X-CSRF-Token": otoken}).status_code == 404   # not their run
    cancelled = c.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "wrong rate"}, headers={"X-CSRF-Token": token})
    assert cancelled.status_code == 200 and cancelled.get_json()["data"]["status"] == "cancelled"
    again = c.post(f"/api/v1/runs/{run_id}/cancel", json={}, headers={"X-CSRF-Token": token})
    assert again.get_json()["data"]["replayed"] is True
    assert WorkerService(services).run_until_idle() == []                     # a cancelled run is never claimed
    assert get(c, f"/api/v1/runs/{run_id}")["data"]["result_receipt"] is None
