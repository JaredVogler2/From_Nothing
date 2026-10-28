"""P19: scoped canonical API (A01, A57, A59, A68).

Every read goes through the registry operations with a real Flask client over a published and
observed fixture world: scope is checked before any fetch, people and economics outside the scope
are withheld, cursors are bound to scope/filters/revision, GET never writes, and commands need
CSRF, idempotency and optimistic versions and return honest worker status.
"""
from __future__ import annotations

import re
import shutil
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.codec import parse_utc
from focus.config import Settings, SettingsError
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.runtime import build_services
from focus.web.app import create_app
from tests.conftest import OBSERVATION_SHIFT, acquire_demo, login, observed_world

POINTS = ("2026-10-26T19:30:00Z", "2026-10-27T16:00:00Z")
LAST = parse_utc(POINTS[-1])


# ---------------------------------------------------------------------- worlds and clients

@pytest.fixture(scope="module")
def world(tmp_path_factory, app_root):
    """Published 2026-10-26 obligation plan, observations at two points, three goal evaluations and the stored factory aggregate."""
    from focus.performance.score import aggregate

    root = tmp_path_factory.mktemp("api_scope") / "runtime"
    w = observed_world(root, app_root, points=POINTS)
    w.factory = aggregate(w.services, list(w.evals[LAST].values()), label=f"FACTORY {OBSERVATION_SHIFT}", as_of=LAST)
    return w


@pytest.fixture(scope="module")
def published(tmp_path_factory, app_root):
    """The same plan published, with no observations collected yet (commands happen at the fixture clock)."""
    root = tmp_path_factory.mktemp("api_published") / "runtime"
    return observed_world(root, app_root, points=())


def services_on(root: Path, app_root: Path, clock=None, **overrides: Any):
    settings = Settings.from_env({}, app_root=app_root, **overrides)
    clock = clock or FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock)
    return build_services(settings, repositories=repos, clock=clock)


def client_on(root: Path, app_root: Path, persona: str | None = None, **overrides: Any):
    overrides.setdefault("inprocess_worker", False)
    services = services_on(root, app_root, **overrides)
    app = create_app(services=services)
    app.config["TESTING"] = True
    client = app.test_client()
    token = login(client, persona) if persona else None
    return client, services, token


def copy_world(src: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "runtime"
    shutil.copytree(src, dst)
    return dst


def codes(env: dict[str, Any]) -> set[str]:
    return {r["code"] for r in env["reasons"]}


def error(response) -> str:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]["code"]


def read_paths(world) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """One concrete request per registered read operation (A68 coverage is checked against the registry)."""
    line = min(c.line for c in world.snap.census)
    job = next(j.id for j in world.snap.jobs if j.team == "PROD-A")
    return {
        "get_fleet": "/api/v1/fleet",
        "get_aircraft": f"/api/v1/aircraft/{line}",
        "list_jobs": "/api/v1/jobs",
        "get_job": f"/api/v1/jobs/{job}",
        "get_plan": f"/api/v1/plans/{world.plan_id}",
        "get_shiftbook": "/api/v1/shiftbook?team=PROD-A",
        "get_myday": "/api/v1/myday?person=DEMO-A1-05",
        "get_workforce": "/api/v1/workforce",
        "get_capacity": "/api/v1/capacity",
        "get_commitments": "/api/v1/commitments",
        "get_economics": "/api/v1/economics",
        "get_shortages": "/api/v1/shortages",
        "get_quality": "/api/v1/quality",
        "get_performance": f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}",
        "get_performance_history": "/api/v1/performance/history",
        "get_recognition": "/api/v1/recognition",
        "get_progression": "/api/v1/progression",
        "list_disruption_claims": "/api/v1/disruption-claims",
        "list_ie_reviews": "/api/v1/ie-reviews",
        "get_run": "/api/v1/runs/RUN-abcdef12",
        "get_team_lead": "/api/v1/team-lead?team=PROD-A",
        "get_standards": "/api/v1/standards",
        "get_budget": "/api/v1/budget",
        "get_gantt": "/api/v1/gantt?team=PROD-A",
        "get_development": "/api/v1/development",
        "get_arena": "/api/v1/arena",
        "get_turnover": "/api/v1/turnover?team=PROD-A",
        "get_history": "/api/v1/history",
        "list_scenarios": "/api/v1/scenarios",
        "get_scenario": "/api/v1/scenarios/SCN-abcdef12",
        "get_twin": "/api/v1/twin?team=PROD-A",
        "get_explanation": "/api/v1/explanations/EXP-abcdef12",
    }


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------- A68: identity, grants and scope before fetch

def test_a68_every_read_needs_a_trusted_identity_and_its_grants(world, app_root) -> None:
    import focus.web.api_views  # noqa: F401  (registers the projection operations)
    from focus.web.operations import REGISTRY

    paths = read_paths(world)
    guarded = {op.name for op in REGISTRY.all() if op.read_only and op.grants}
    assert guarded == set(paths), "every scoped read operation is exercised"
    client, _, _ = client_on(world.root, app_root)
    for name, path in paths.items():
        r = client.get(path)
        assert r.status_code == 401 and error(r) == "UNAUTHENTICATED", name
    login(client, "mechanic-a05")
    for path in ("/api/v1/economics", "/api/v1/ie-reviews"):
        r = client.get(path)
        assert r.status_code == 403 and error(r) == "FORBIDDEN", path


def test_a68_scope_is_checked_before_any_fetch(world, app_root, monkeypatch) -> None:
    from focus.web import serializers as S

    lead, _, token = client_on(world.root, app_root, "lead-prod-a")
    mechanic, _, _ = client_on(world.root, app_root, "mechanic-a05")

    def fetched(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("repository read before the scope check")

    monkeypatch.setattr(S.Canon, "load", classmethod(fetched))
    monkeypatch.setattr(S, "registry", fetched)
    for path in ("/api/v1/shiftbook?team=PROD-B", "/api/v1/jobs?team=PROD-B", "/api/v1/performance?team=PROD-B",
                 "/api/v1/performance/history?team=PROD-B", "/api/v1/recognition?team=PROD-B", "/api/v1/progression?team=PROD-B",
                 "/api/v1/disruption-claims?team=PROD-B"):
        r = lead.get(path)
        assert r.status_code == 403 and error(r) == "OUT_OF_SCOPE", path
    body = {"plan_id": world.plan_id, "expected_version": 0, "reason": "emergency rework on line 1303",
            "change": {"kind": "ADD_EMERGENCY_WORK", "team": "PROD-B", "shift_id": OBSERVATION_SHIFT, "phase_id": "J0.WORK.0"},
            "idempotency_key": "amend-scope-0001"}
    r = lead.post("/api/v1/amendments", json=body, headers={"X-CSRF-Token": token})
    assert r.status_code == 403 and error(r) == "OUT_OF_SCOPE"
    r = mechanic.get("/api/v1/myday?person=DEMO-A1-01")
    assert r.status_code == 403 and error(r) == "OUT_OF_SCOPE"           # a mechanic opens only their own day


def test_a68_scoped_content_hides_other_teams_people_and_economics(world, app_root) -> None:
    manager, _, _ = client_on(world.root, app_root, "manager")
    lead, _, _ = client_on(world.root, app_root, "lead-prod-a")
    every = manager.get("/api/v1/jobs?limit=500").get_json()["data"]["jobs"]
    mine = lead.get("/api/v1/jobs?limit=500").get_json()
    assert mine["data"]["jobs"] and {j["team"] for j in mine["data"]["jobs"]} == {"PROD-A"}
    assert "JOBS_OUTSIDE_SCOPE" in codes(mine)
    assert mine["data"]["total"] == sum(1 for j in every if j["team"] == "PROD-A") < len(every)
    other = next(j for j in every if j["team"] == "PROD-B")
    r = lead.get(f"/api/v1/jobs/{other['job_id']}")
    assert r.status_code == 404 and error(r) == "JOB_NOT_FOUND"        # no existence leak across scope

    # a PROD-A job with a QA phase: the PROD-A lead sees own crew names, QA inspectors are redacted
    found = None
    for job in (j for j in every if j["team"] == "PROD-A"):
        detail = manager.get(f"/api/v1/jobs/{job['job_id']}").get_json()["data"]
        if any(a["team"] == "QA" and a["crew"] for a in detail["assignments"]) and any(a["team"] == "PROD-A" and a["crew"] for a in detail["assignments"]):
            found = job["job_id"]
            break
    assert found is not None
    seen = lead.get(f"/api/v1/jobs/{found}").get_json()["data"]["assignments"]
    qa = [c for a in seen if a["team"] == "QA" for c in a["crew"]]
    own = [c for a in seen if a["team"] == "PROD-A" for c in a["crew"]]
    assert qa and all(c.get("redacted") and c["person_id"] is None and c["name"] == "outside your scope" for c in qa)
    assert own and all(c["name"] and c["person_id"] and not c.get("redacted") for c in own)

    # cost and contract terms are withheld (null with a reason), never shown as zero
    lf, mf = lead.get("/api/v1/fleet").get_json(), manager.get("/api/v1/fleet").get_json()
    assert all(row["cost"] is None for row in lf["data"]["aircraft"]) and "ECONOMICS_RESTRICTED" in codes(lf)
    assert any(row["cost"] is not None for row in mf["data"]["aircraft"]) and "ECONOMICS_RESTRICTED" not in codes(mf)
    assert lf["data"]["totals"]["jobs"] == mf["data"]["totals"]["jobs"]              # fleet aggregates stay visible
    assert {p["team"] for p in lead.get("/api/v1/workforce").get_json()["data"]["pools"]} == {"PROD-A"}


def test_a68_cursor_is_bound_to_scope_filters_and_revision(world, app_root, tmp_path) -> None:
    root = copy_world(world.root, tmp_path)
    client, services, _ = client_on(root, app_root, "manager")
    first = client.get("/api/v1/jobs?limit=2").get_json()
    total, cursor = first["data"]["total"], first["next_cursor"]
    assert total > 4 and cursor and len(first["data"]["jobs"]) == 2
    seen = [j["job_id"] for j in first["data"]["jobs"]]
    token = cursor
    while token:
        page = client.get(f"/api/v1/jobs?limit=2&cursor={token}").get_json()
        seen += [j["job_id"] for j in page["data"]["jobs"]]
        token = page["next_cursor"]
    assert len(seen) == total == len(set(seen))                       # every job exactly once
    r = client.get(f"/api/v1/jobs?limit=2&team=PROD-A&cursor={cursor}")
    assert r.status_code == 400 and error(r) == "CURSOR_SCOPE_MISMATCH"
    r = client.get(f"/api/v1/jobs?limit=2&cursor={cursor[:-4]}AAAA")
    assert r.status_code == 400 and error(r) == "BAD_CURSOR"
    for bad in ("limit=501", "limit=0", "limit=x"):
        assert client.get(f"/api/v1/jobs?{bad}").status_code == 400
    other = client.application.test_client()                          # same app and secret, another subject (all teams too)
    login(other, "operator")
    r = other.get(f"/api/v1/jobs?limit=2&cursor={cursor}")
    assert r.status_code == 400 and error(r) == "CURSOR_SCOPE_MISMATCH"
    outcome = acquire_demo(services, key="api-scope-next-run", cutoff=FIXTURE_CUTOFF + timedelta(hours=1))
    assert outcome.status == "COMPLETE"
    r = client.get(f"/api/v1/jobs?limit=2&cursor={cursor}")
    assert r.status_code == 409 and error(r) == "CURSOR_STALE"         # a new source revision restarts pagination


def test_a68_get_never_writes_solves_or_grades(world, app_root) -> None:
    before = _tree(world.root)
    paths = read_paths(world)
    extra = ["/api/v1/context", f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}&team=PROD-B", "/api/v1/capacity?purpose=FORECAST",
             f"/api/v1/disruption-claims?shift_id={OBSERVATION_SHIFT}", "/api/v1/jobs?disposition=scheduled", "/dashboard", "/dashboard/classic"]
    for persona in ("manager", "lead-prod-b", "mechanic-a05", "excusal", "ie", "quality", "supply"):
        client, _, _ = client_on(world.root, app_root, persona)
        for path in list(paths.values()) + extra:
            assert client.get(path).status_code in (200, 400, 403, 404), (persona, path)
    assert _tree(world.root) == before


# ---------------------------------------------------------------------- A01: no data

def test_a01_every_read_is_typed_without_data_and_never_writes(world, app_root, tmp_path) -> None:
    root = tmp_path / "empty"
    client, _, _ = client_on(root, app_root, "manager")
    for name, path in read_paths(world).items():
        if name in ("get_aircraft", "get_job", "get_plan", "get_run", "get_myday", "get_scenario", "get_explanation"):
            assert client.get(path).status_code in (200, 404), name
            continue
        r = client.get(path)
        assert r.status_code == 200, (name, r.get_data(as_text=True))
        env = r.get_json()
        assert env["status"] in ("unknown", "blocked", "empty"), (name, env["status"])
        assert env["reasons"] or env["status"] == "empty", name
    assert not root.exists() or not any(root.rglob("*"))              # no acquisition, solve, grade or write


# ---------------------------------------------------------------------- A57/A59: one canonical score on every surface

def test_a57_a59_every_surface_reads_the_same_stored_evaluation(world, app_root) -> None:
    from focus.performance import history
    from focus.performance.score import formula

    services = world.services
    manager, _, _ = client_on(world.root, app_root, "manager")
    perf = manager.get(f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}").get_json()
    assert perf["status"] == "ready"
    goals = {g["team"]: g for g in perf["data"]["goals"]}
    assert set(goals) == {"PROD-A", "PROD-B", "QA"}
    for team, g in goals.items():
        stored = history.current(services, services.settings.scope_key, history.series_for_goal(services, g["goal_id"]))
        assert g["evaluation"]["evaluation_id"] == stored.id == world.evals[LAST][team].id
        assert g["evaluation"]["score"] == (None if stored.score is None else str(stored.score))
        assert len(g["units"]) == g["evidence"]["units"] > 0
    # the factory view is the stored canonical aggregate: exact pooled components, not the average of scores
    pooled = perf["data"]["pooled"]
    assert pooled["persisted"] is True and perf["evaluation_id"] == pooled["evaluation_id"] == world.factory.id
    members = list(world.evals[LAST].values())
    sums = {k: sum((getattr(m, k) for m in members), Fraction(0)) for k in ("B", "E", "Cp", "U", "O")}
    expected = formula(sums["B"], sums["E"], sums["Cp"], sums["U"], sums["O"], services.policy)
    assert pooled["score"] == str(expected["score"])
    average = sum(Fraction(str(m.score)) for m in members) / len(members)
    assert Fraction(pooled["score"]) != average.limit_denominator(1000)
    # the team lead's pool is their own goal; shift book, history and performance name the same record
    lead, _, _ = client_on(world.root, app_root, "lead-prod-b")
    mine = lead.get(f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}").get_json()
    assert [g["team"] for g in mine["data"]["goals"]] == ["PROD-B"]
    assert mine["data"]["pooled"]["evaluation_id"] == goals["PROD-B"]["evaluation"]["evaluation_id"] == mine["evaluation_id"]
    book = lead.get("/api/v1/shiftbook?team=PROD-B&date=2026-10-26&shift=1").get_json()
    assert book["evaluation_id"] == book["data"]["evaluation"]["evaluation_id"] == goals["PROD-B"]["evaluation"]["evaluation_id"]
    assert book["data"]["goal"]["goal_id"] == goals["PROD-B"]["goal_id"] and book["data"]["goal"]["B0"] == goals["PROD-B"]["B0"]
    hist = lead.get("/api/v1/performance/history?team=PROD-B").get_json()["data"]["series"]
    assert hist[0]["current_evaluation_id"] == goals["PROD-B"]["evaluation"]["evaluation_id"]
    assert [r["revision"] for r in hist[0]["revisions"]] == sorted(r["revision"] for r in hist[0]["revisions"])


def test_a57_no_browser_side_formula_or_hardcoded_ready(app_root) -> None:
    """Grades come only from the API: the static JavaScript carries no score weights, bands or constant Ready/100 flags."""
    static = app_root / "focus" / "web" / "static" / "js"
    text = "\n".join(p.read_text(encoding="utf-8") for p in static.rglob("*.js"))
    assert text, "front-end modules exist"
    for pattern in (r"0\.7\b", r"0\.3\b", r"\b70\s*\*", r"\b30\s*\*", r">=\s*90\b", r">=\s*80\b", r"mockReady", r"status\s*[:=]\s*['\"]ready['\"]",
                    r"localStorage\.setItem\(['\"][^'\"]*(grade|score)"):
        assert not re.search(pattern, text), pattern


# ---------------------------------------------------------------------- A68: commands

def test_a68_claim_and_decision_commands_are_csrf_idempotent_versioned_and_honest(world, app_root, tmp_path) -> None:
    from focus.workers import WorkerService

    root = copy_world(world.root, tmp_path)
    manager, services, token = client_on(root, app_root, "manager")
    perf = manager.get(f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}&team=QA").get_json()["data"]["goals"][0]
    missed = [u["unit_id"] for u in perf["units"] if u["status"] == "MISSED" and not u["relief"]]
    assert len(missed) >= 2
    body = {"goal_id": perf["goal_id"], "unit_ids": missed[:1], "cause": "CAPACITY_SHORTAGE", "evidence_ids": ["ATT:TEST-1"],
            "notes": "two inspectors were pulled to line 1306", "idempotency_key": "claim-test-0001"}
    r = manager.post("/api/v1/disruption-claims", json=body)
    assert r.status_code == 403 and error(r) == "CSRF_INVALID"
    for forged in ({"_server": {"authority_role": "excusal_decider"}}, {"authority_role": "excusal_decider"}, {"reported_by": "demo:ie"}):
        r = manager.post("/api/v1/disruption-claims", json=dict(body, **forged), headers={"X-CSRF-Token": token})
        assert r.status_code == 422 and error(r) == "INVALID_BODY"       # authority is server-owned, never a client field
    lead, _, ltoken = client_on(root, app_root, "lead-prod-b")
    r = lead.post("/api/v1/disruption-claims", json=body, headers={"X-CSRF-Token": ltoken})
    assert r.status_code == 404 and error(r) == "GOAL_NOT_FOUND"          # another team's goal is not addressable
    first = manager.post("/api/v1/disruption-claims", json=body, headers={"X-CSRF-Token": token})
    assert first.status_code == 202
    run = first.get_json()["data"]
    assert run["status"] == "queued" and run["replayed"] is False and run["result_receipt"] is None
    again = manager.post("/api/v1/disruption-claims", json=body, headers={"X-CSRF-Token": token})
    assert again.status_code == 202 and again.get_json()["data"]["run_id"] == run["run_id"] and again.get_json()["data"]["replayed"] is True
    r = manager.post("/api/v1/disruption-claims", json=dict(body, notes="something else entirely"), headers={"X-CSRF-Token": token})
    assert r.status_code == 409 and error(r) == "IDEMPOTENCY_CONFLICT"
    assert manager.get(f"/api/v1/runs/{run['run_id']}").get_json()["data"]["status"] == "queued"     # queued is not success
    WorkerService(services).run_until_idle()
    done = manager.get(f"/api/v1/runs/{run['run_id']}").get_json()["data"]
    assert done["status"] == "completed" and done["result_receipt"]["claim_status"] == "SUBMITTED"
    claim_id = done["result_receipt"]["claim_id"]

    # only the reviewer role decides; the version is the goal's decision count; the decider never reviews their own claim
    decision = {"claim_ids": [claim_id], "decision": "APPROVED", "unit_ids": missed[:1], "notes": "attendance pull confirmed by roster",
                "expected_version": 0, "idempotency_key": "decide-test-0001"}
    r = lead.post("/api/v1/excusal-decisions", json=decision, headers={"X-CSRF-Token": ltoken})
    assert r.status_code == 403 and error(r) == "FORBIDDEN"
    decider, _, dtoken = client_on(root, app_root, "excusal")
    queue = decider.get(f"/api/v1/disruption-claims?shift_id={OBSERVATION_SHIFT}&team=QA").get_json()["data"]["goals"][0]
    version = queue["decision_version"]
    assert claim_id in {c["claim_id"] for c in queue["claims"]}
    r = decider.post("/api/v1/excusal-decisions", json=dict(decision, expected_version=version + 3), headers={"X-CSRF-Token": dtoken})
    assert r.status_code == 409 and error(r) == "STALE_VERSION"
    ok = decider.post("/api/v1/excusal-decisions", json=dict(decision, expected_version=version), headers={"X-CSRF-Token": dtoken})
    assert ok.status_code == 202
    WorkerService(services).run_until_idle()
    receipt = decider.get(f"/api/v1/runs/{ok.get_json()['data']['run_id']}").get_json()["data"]
    assert receipt["status"] == "completed" and receipt["result_receipt"]["decision"] == "APPROVED", receipt
    after = decider.get(f"/api/v1/disruption-claims?shift_id={OBSERVATION_SHIFT}&team=QA").get_json()["data"]["goals"][0]
    assert after["decision_version"] == version + 1
    mine = next(d for d in after["decisions"] if d["decision_id"] == receipt["result_receipt"]["decision_id"])
    assert mine["authority"] == "demo:excusal" and mine["authority_role"] == "excusal_decider" and mine["effective"]
    # the decider files a claim of their own, then cannot decide it
    own = decider.post("/api/v1/disruption-claims", json=dict(body, unit_ids=missed[-1:], idempotency_key="claim-test-0002"),
                       headers={"X-CSRF-Token": dtoken})
    WorkerService(services).run_until_idle()
    own_claim = decider.get(f"/api/v1/runs/{own.get_json()['data']['run_id']}").get_json()["data"]["result_receipt"]["claim_id"]
    r = decider.post("/api/v1/excusal-decisions", json=dict(decision, claim_ids=[own_claim], unit_ids=missed[-1:], expected_version=version + 1,
                                                           idempotency_key="decide-test-0002"), headers={"X-CSRF-Token": dtoken})
    assert r.status_code == 403 and error(r) == "SELF_REVIEW"


def test_a68_amendment_is_versioned_feasible_and_never_changes_b0(published, app_root, tmp_path) -> None:
    from focus.workers import WorkerService

    root = copy_world(published.root, tmp_path)
    lead, services, token = client_on(root, app_root, "lead-prod-a")
    book = lead.get("/api/v1/shiftbook?team=PROD-A&date=2026-10-26&shift=1").get_json()["data"]
    goal = book["goal"]
    assert goal["versions"]["amendments"] == 0
    phase = next(p for p in published.snap.phases if p.label == "1302/B1 work")      # open PROD-A work, predecessors complete, crew available
    body = {"plan_id": goal["plan_id"], "expected_version": 0, "reason": "customer escalation pulled B1 into today",
            "change": {"kind": "ADD_EMERGENCY_WORK", "team": "PROD-A", "shift_id": OBSERVATION_SHIFT, "phase_id": phase.key.id},
            "idempotency_key": "amend-test-0001"}
    r = lead.post("/api/v1/amendments", json=dict(body, expected_version=4), headers={"X-CSRF-Token": token})
    assert r.status_code == 409 and error(r) == "STALE_VERSION"
    r = lead.post("/api/v1/amendments", json=dict(body, change=dict(body["change"], kind="RELIEF")), headers={"X-CSRF-Token": token})
    assert r.status_code == 422                                             # relief is an excusal decision, not an amendment
    queued = lead.post("/api/v1/amendments", json=body, headers={"X-CSRF-Token": token})
    assert queued.status_code == 202 and queued.get_json()["data"]["status"] == "queued"
    WorkerService(services).run_until_idle()
    done = lead.get(f"/api/v1/runs/{queued.get_json()['data']['run_id']}").get_json()["data"]
    assert done["status"] == "completed", done
    receipt = done["result_receipt"]
    assert receipt["added_minutes"] == phase.standard_person_minutes and receipt["b0_unchanged"] is True
    assert receipt["feasibility"]["basis"] in ("QUALIFIED_POOL_HEADROOM", "ASSIGNED_IN_ACCEPTED_PLAN")
    after = lead.get("/api/v1/shiftbook?team=PROD-A&date=2026-10-26&shift=1").get_json()["data"]["goal"]
    assert after["versions"]["amendments"] == 1 and after["B0"] == goal["B0"]
    # the same expected version again is stale; a part-blocked phase is refused by the worker with its reason
    r = lead.post("/api/v1/amendments", json=dict(body, idempotency_key="amend-test-0002"), headers={"X-CSRF-Token": token})
    assert r.status_code == 409 and error(r) == "STALE_VERSION"
    blocked = next(p for p in published.snap.phases if p.label == "1305/CHILD5 work")
    second = lead.post("/api/v1/amendments", json=dict(body, expected_version=1, idempotency_key="amend-test-0003",
                                                        change=dict(body["change"], phase_id=blocked.key.id)), headers={"X-CSRF-Token": token})
    assert second.status_code == 202
    WorkerService(services).run_until_idle()
    failed = lead.get(f"/api/v1/runs/{second.get_json()['data']['run_id']}").get_json()["data"]
    assert failed["status"] == "failed" and failed["error"]["code"] == "NOT_EXECUTABLE" and "PART_NOT_RECEIVED" in failed["error"]["message"]


def test_a68_schedule_and_other_commands_report_honest_worker_status(published, app_root, tmp_path) -> None:
    from focus.workers import WorkerService

    root = copy_world(published.root, tmp_path)
    operator, services, token = client_on(root, app_root, "operator")
    source = services.repositories.head(f"source/{services.settings.scope_key}").record_id
    body = {"source_run_id": source, "policy_id": services.policy.id, "budget_seconds": 2, "purpose": "FORECAST", "idempotency_key": "sched-test-0001"}
    assert error(operator.post("/api/v1/schedule-runs", json=dict(body, policy_id="other-v9"), headers={"X-CSRF-Token": token})) == "POLICY_MISMATCH"
    r = operator.post("/api/v1/schedule-runs", json=dict(body, source_run_id="SR-doesnotexist"), headers={"X-CSRF-Token": token})
    assert r.status_code == 404 and error(r) == "SOURCE_RUN_NOT_FOUND"
    queued = operator.post("/api/v1/schedule-runs", json=body, headers={"X-CSRF-Token": token})
    assert queued.status_code == 202 and queued.get_json()["data"]["status"] == "queued"
    plan_head = services.repositories.head(f"plan/{services.settings.scope_key}/EXECUTABLE")
    WorkerService(services).run_until_idle()
    done = operator.get(f"/api/v1/runs/{queued.get_json()['data']['run_id']}").get_json()["data"]
    assert done["status"] == "completed", done
    assert done["result_receipt"]["published"] is False and done["result_receipt"]["candidate_id"]
    assert services.repositories.head(f"plan/{services.settings.scope_key}/EXECUTABLE").version == plan_head.version   # never promotes
    candidate = operator.get(f"/api/v1/plans/{done['result_receipt']['candidate_id']}").get_json()
    assert candidate["data"]["accepted"] is False and "CANDIDATE_ONLY" in codes(candidate)

    ie, _, itoken = client_on(root, app_root, "ie")
    job = next(j.id for j in published.snap.jobs if j.team == "PROD-B")
    review = ie.post("/api/v1/ie-reviews", json={"job_id": job, "standard_revision": "fixture-v1", "proposed_correction": "raise the P75 duration",
                                                 "reason": "five observed overruns", "idempotency_key": "ie-test-0001"}, headers={"X-CSRF-Token": itoken})
    mechanic, _, mtoken = client_on(root, app_root, "mechanic-a05")
    report = mechanic.post("/api/v1/reported-observations", json={"job_id": job, "kind": "COMPLETED", "effective_at": "2026-10-26T09:00:00Z",
                                                                 "notes": "done before lunch", "idempotency_key": "rep-test-0001"},
                           headers={"X-CSRF-Token": mtoken})
    assert review.status_code == report.status_code == 202
    WorkerService(services).run_until_idle()
    rv = ie.get(f"/api/v1/runs/{review.get_json()['data']['run_id']}").get_json()["data"]
    rp = mechanic.get(f"/api/v1/runs/{report.get_json()['data']['run_id']}").get_json()["data"]
    assert rv["status"] == "completed" and rv["result_receipt"]["affects_issued_goals"] is False
    assert rp["status"] == "completed" and rp["result_receipt"]["verified"] is False          # a report is a claim, never credit
    assert [r["job_id"] for r in ie.get("/api/v1/ie-reviews").get_json()["data"]["reviews"]] == [job]
    # another person cannot read someone else's run
    r = mechanic.get(f"/api/v1/runs/{rv['run_id']}")
    assert r.status_code == 404 and error(r) == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------- live scope configuration and MCP parity

def test_live_identity_scope_comes_from_server_configuration(make_client) -> None:
    grants = {"subjects": {"posit:lead": ["reader", "team_lead"], "posit:roamer": ["reader", "team_lead"], "posit:mech": ["reader", "mechanic"]},
              "groups": {}, "default": [],
              "scopes": {"posit:lead": {"teams": ["PROD-A"]}, "mech": {"teams": ["PROD-A"], "person": "BEMS-1"}}}
    from focus.config import _normalize_grants

    app, client, services = make_client(identity_provider="posit_connect", platform="posit_connect", grants=_normalize_grants(grants))

    def ctx(user: str) -> dict[str, Any]:
        return client.get("/api/v1/context", headers={"RStudio-Connect-Credentials": f'{{"user": "{user}", "groups": []}}'}).get_json()["data"]

    assert ctx("lead")["scope"] == {"all_teams": False, "teams": ["PROD-A"], "person": None, "label": "PROD-A"}
    assert ctx("mech")["scope"]["person"] == "BEMS-1" and ctx("mech")["scope"]["teams"] == ["PROD-A"]
    roamer = ctx("roamer")["scope"]
    assert roamer["teams"] == [] and roamer["all_teams"] is False and roamer["label"].startswith("aggregates only")
    r = client.get("/api/v1/shiftbook", headers={"RStudio-Connect-Credentials": '{"user": "roamer", "groups": []}'})
    assert r.status_code == 400 and error(r) == "TEAM_REQUIRED"
    r = client.get("/api/v1/shiftbook?team=PROD-A", headers={"RStudio-Connect-Credentials": '{"user": "roamer", "groups": []}'})
    assert r.status_code == 403 and error(r) == "OUT_OF_SCOPE"
    for bad in ({"scopes": {"x": {"teams": "PROD-A"}}}, {"scopes": {"x": {"teams": ["PROD-A"], "grants": ["manager"]}}},
                {"scopes": {"x": {"person": "bad id!"}}}, {"scopes": []}):
        with pytest.raises(SettingsError):
            _normalize_grants(bad)


def test_mcp_read_tools_share_the_rest_envelope(world, app_root) -> None:
    client, _, _ = client_on(world.root, app_root, "lead-prod-b", mcp_enabled=True)
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).get_json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"get_fleet", "get_shiftbook", "get_performance", "list_jobs", "list_disruption_claims"} <= names
    assert not names & {"create_amendment", "create_disruption_claim", "create_excusal_decision", "create_ie_review", "create_reported_observation"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    call = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                     "params": {"name": "get_performance", "arguments": {"shift_id": OBSERVATION_SHIFT}}}).get_json()["result"]
    rest = client.get(f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}").get_json()
    via_mcp = call["structuredContent"]
    assert call["isError"] is False and via_mcp["data"] == rest["data"] and via_mcp["evaluation_id"] == rest["evaluation_id"]
    denied = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                       "params": {"name": "get_shiftbook", "arguments": {"team": "PROD-A"}}}).get_json()["result"]
    assert denied["isError"] is True and "OUT_OF_SCOPE" in denied["content"][0]["text"]
