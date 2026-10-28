"""P21: digital twin and isolated scenarios (A64, A65; S16 TWIN and SCENARIOS).

Two deterministic fixture worlds come from the complete pipeline (``demo_run``):

* **observed**: both plans accepted, observations collected and goals evaluated;
* **published**: plans accepted only, so the fixture clock is inside the obligation shift.

Expected values come from independent sources, never from the twin itself:

* the signed artifact's frozen goals;
* the stored evaluations;
* the plan's duty ledger;
* the admitted census.
"""
from __future__ import annotations

import itertools
import shutil
from collections import Counter, defaultdict
from dataclasses import replace
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

# Paths a scenario or command may add: its own namespace, candidate and validation receipts, worker run state, idempotency
# keys and locks. Everything else (heads, goals, plans, commitments, actuals, evaluations, recognition) must stay byte-identical.
SCENARIO_WRITES = ("objects/scenario_snapshots/", "objects/scenario_results/", "objects/candidates/", "objects/validations/",
                   "objects/run_cancel_requests/", "state/worker_runs/", "idem/", "locks/")


def services_on(root: Path, app_root: Path, **overrides: Any):
    overrides.setdefault("inprocess_worker", False)
    settings = Settings.from_env({}, app_root=app_root, **overrides)
    clock = FixedClock(FIXTURE_CUTOFF)
    return build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                          clock=clock)


def client(root: Path, app_root: Path, persona: str, **overrides: Any):
    services = services_on(root, app_root, **overrides)
    app = create_app(services=services)
    app.config["TESTING"] = True
    c = app.test_client()
    return c, services, login(c, persona)


def world(tmp_path_factory: Any, app_root: Path, name: str, observe: bool) -> Path:
    from focus.pipeline import demo_run

    root = tmp_path_factory.mktemp(name) / "runtime"
    result = demo_run(services_on(root, app_root), budget_seconds=5, max_moves=5, observe=observe)
    assert result["status"] == "ok", result
    return root


@pytest.fixture(scope="module")
def observed(tmp_path_factory, app_root) -> Path:
    return world(tmp_path_factory, app_root, "twin_observed", True)


@pytest.fixture(scope="module")
def published(tmp_path_factory, app_root) -> Path:
    return world(tmp_path_factory, app_root, "twin_published", False)


def copy_world(src: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "runtime"
    shutil.copytree(src, dst)
    return dst


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}


def twin(c: Any, query: str = "") -> dict[str, Any]:
    r = c.get(f"/api/v1/twin{query}")
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r.get_json()


def canon(services: Any) -> Any:
    from focus.web.serializers import Canon

    return Canon.load(services)


def minutes(d: Any) -> int:
    return int((d.end - d.start).total_seconds() // 60)


# ---------------------------------------------------------------------- twin keys and layers

def test_twin_cells_are_keyed_by_the_canonical_revision_and_layers_never_mix(observed, app_root) -> None:
    from focus.data.acquisition import source_head_name
    from focus.performance import history
    from focus.performance.actuals import observation_head
    from focus.publication.repository import repository_for
    from focus.web.serializers import registry

    c, services, _ = client(observed, app_root, "manager")
    env = twin(c)
    assert env["status"] == "ready" and env["data"]["reconciliation"]["ok"], env["data"]["reconciliation"]
    d = env["data"]
    repo, scope = repository_for(services), services.settings.scope_key
    rev = d["revision"]
    assert rev["source_run_id"] == services.repositories.head(source_head_name(scope)).record_id
    assert rev["plan_id"] == repo.head(scope, "EXECUTABLE").record_id and rev["forecast_plan_id"] == repo.head(scope, "FORECAST").record_id
    assert rev["policy_id"] == services.policy.id and rev["policy_hash"] == services.policy.hash
    assert rev["observation_run_id"] == services.repositories.head(observation_head(scope)).record_id
    reg = registry(services)
    for cell in d["cells"]:
        k = cell["key"]
        assert (k["source_run_id"], k["plan_id"], k["forecast_plan_id"], k["policy_id"]) == \
            (rev["source_run_id"], rev["plan_id"], rev["forecast_plan_id"], rev["policy_id"])
        shift_id = f"DEMO:{k['shift_start_date']}:S{k['shift_number']}"
        goal_id = reg.goal_id_for(k["team"], shift_id) if k["shift_start_date"] in {str(x) for x in reg.day_plan} else None
        stored = None if goal_id is None else history.current(services, scope, history.series_for_goal(services, goal_id))
        assert k["evaluation_id"] == (None if stored is None else stored.id), k
        assert k["evaluation_revision"] == (None if stored is None else stored.revision)
        if cell["observed"] is not None:
            assert cell["observed"]["basis"] == "CANONICAL_EVALUATION_EVIDENCE" and stored is not None
        if cell["forecast"] is not None:
            assert cell["forecast"]["basis"] == "CONDITIONAL_FORECAST"
    # measured never includes anything synthetic; the executable plan never carries virtual duty; the forecast labels it
    cn = canon(services)
    synthetic = {p.key.id for p in cn.snapshot.people if p.synthetic}
    assert not any(duty.person in synthetic for duty in cn.plan("EXECUTABLE").duties)
    assert d["totals"]["planned"]["grand"]["virtual_duty_minutes"] == 0
    virtual = sum(minutes(duty) for duty in cn.plan("FORECAST").duties if duty.person in synthetic)
    assert virtual > 0 and d["totals"]["forecast"]["grand"]["virtual_duty_minutes"] == virtual
    assert d["forecast_conditions"]["SUPPORT_RESOURCE_UNAVAILABLE"] >= 1
    observed_jobs = {u.job for e in reg.records.values()
                     for u in history.evidence(services, history.current(services, scope, history.series_for_goal(services, e.goal_id)).evidence_hash).unit_facts}
    forecast_only = {o.job for o in cn.plan("FORECAST").job_dispositions if o.disposition.value == "conditional"}
    assert not observed_jobs & forecast_only                          # no forecast-only work is ever counted as measured


def test_twin_reconciles_exactly_with_frozen_goals_evaluations_ledger_and_census(observed, app_root) -> None:
    from focus.performance import history
    from focus.web.serializers import registry

    c, services, _ = client(observed, app_root, "manager")
    d = twin(c)["data"]
    cn = canon(services)
    scope = services.settings.scope_key
    plan = cn.plan("EXECUTABLE")
    # planned standard on the obligation day == the frozen B0 of the signed artifact
    frozen = {(g.team, g.shift_id): g.standard_person_minutes for g in cn.executable.artifact.frozen_goals}
    assert frozen
    by_team_shift: dict[tuple[str, str], int] = defaultdict(int)
    for cell in d["cells"]:
        k = cell["key"]
        if cell["planned"] is not None:
            by_team_shift[(k["team"], f"DEMO:{k['shift_start_date']}:S{k['shift_number']}")] += cell["planned"]["standard_minutes"]
    for key, b0 in frozen.items():
        assert by_team_shift[key] == b0, key
    # planned duty == the plan's duty ledger, by kind
    ledger = Counter()
    for duty in plan.duties:
        ledger[duty.kind.value] += minutes(duty)
    assert {k: v for k, v in d["totals"]["planned"]["grand"]["duty_minutes"].items() if v} == dict(ledger)
    # observed effort == the stored evaluation components of each team-shift
    reg = registry(services)
    for record in reg.records.values():
        e = history.current(services, scope, history.series_for_goal(services, record.goal_id))
        team_cells = [cell["observed"] for cell in d["cells"] if cell["key"]["team"] == record.team and cell["key"]["evaluation_id"] == e.id
                      and cell["observed"] is not None]
        assert sum(x["earned_minutes"] for x in team_cells) == e.Cp
        assert sum(x["relieved_minutes"] for x in team_cells) == e.E
        assert sum(x["goal_original_minutes"] for x in team_cells) == e.B0
        assert sum(x["goal_minutes"] for x in team_cells) == e.B
        assert sum(x["offplan_minutes"] for x in team_cells) == e.U
    # every census job appears exactly once per layer, per line and in total
    lines = Counter(j.key.line for j in cn.snapshot.jobs)
    for row in d["inventory"]["by_line"]:
        for layer in ("observed", "planned", "forecast"):
            assert sum(row[layer].values()) == row["jobs"] == lines[row["line"]], (row, layer)
    assert d["inventory"]["total"]["jobs"] == len(cn.snapshot.jobs)
    assert d["inventory"]["total"]["planned"] == dict(Counter(o.disposition.value for o in plan.job_dispositions))
    assert d["inventory"]["total"]["observed"]["complete"] >= 1 and d["unknown"]["jobs"], "measured completions and an honest unknown list"


def test_twin_drilldowns_give_the_same_canonical_totals_and_revisions(observed, app_root) -> None:
    from focus.analytics.twin import add, blank_observed, blank_plan

    manager, _, _ = client(observed, app_root, "manager")
    whole = twin(manager)["data"]
    blanks = {"observed": blank_observed, "planned": blank_plan, "forecast": blank_plan}
    teams = sorted(set().union(*(whole["totals"][layer]["by_team"].keys() for layer in blanks)))
    lines = sorted({c["key"]["line"] for c in whole["cells"] if c["key"]["line"] is not None})
    for dimension, values in (("team", teams), ("line", lines)):
        sums = {layer: blanks[layer]() for layer in blanks}
        for value in values:
            part = twin(manager, f"?{dimension}={value}")["data"]
            assert part["revision"] == whole["revision"] and part["reconciliation"]["ok"], (dimension, value)
            for layer in sums:
                sums[layer] = add(sums[layer], part["totals"][layer]["grand"])
        for layer in sums:
            expected = whole["totals"][layer]["grand"]
            if dimension == "line" and "none" in whole["totals"][layer]["by_line"]:
                none = whole["totals"][layer]["by_line"]["none"]
                expected = {k: (v - none[k] if not isinstance(v, dict) else {kk: vv - none[k][kk] for kk, vv in v.items()}) for k, v in expected.items()}
            assert sums[layer] == expected, (dimension, layer)
    shift = twin(manager, "?date=2026-10-26&shift=1")["data"]
    assert shift["totals"]["planned"]["grand"] == whole["totals"]["planned"]["by_shift"]["2026-10-26:S1"]
    # a team lead's twin is exactly the manager's row for that team; another team is refused before any fetch
    lead, _, _ = client(observed, app_root, "lead-prod-a")
    mine = twin(lead)["data"]
    assert {c["key"]["team"] for c in mine["cells"]} == {"PROD-A"}
    assert mine["totals"]["planned"]["grand"] == whole["totals"]["planned"]["by_team"]["PROD-A"]
    assert mine["inventory"]["total"]["jobs"] == next(r["jobs"] for r in whole["inventory"]["by_team"] if r["team"] == "PROD-A")
    r = lead.get("/api/v1/twin?team=PROD-B")
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "OUT_OF_SCOPE"


def test_twin_correction_appends_an_evaluation_revision_and_still_reconciles(observed, app_root, tmp_path) -> None:
    from focus.performance import history
    from focus.performance.actuals import knowledge_time
    from focus.performance.score import evaluate_shift
    from focus.workers import WorkerService

    root = copy_world(observed, tmp_path)
    manager, services, token = client(root, app_root, "manager")
    before = twin(manager, "?team=QA")["data"]
    old_id = next(c["key"]["evaluation_id"] for c in before["cells"] if c["key"]["evaluation_id"])
    perf = manager.get(f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}&team=QA").get_json()["data"]["goals"][0]
    missed = [u for u in perf["units"] if u["status"] == "MISSED" and not u["relief"]]
    claim = manager.post("/api/v1/disruption-claims", json={"goal_id": perf["goal_id"], "unit_ids": [missed[0]["unit_id"]], "cause": "CAPACITY_SHORTAGE",
                                                           "evidence_ids": ["ATT:TWIN-1"], "notes": "inspector pulled to line 1306",
                                                           "idempotency_key": "twin-claim-0001"}, headers={"X-CSRF-Token": token})
    WorkerService(services).run_until_idle()
    claim_id = manager.get(f"/api/v1/runs/{claim.get_json()['data']['run_id']}").get_json()["data"]["result_receipt"]["claim_id"]
    decider, _, dtoken = client(root, app_root, "excusal")
    version = decider.get(f"/api/v1/disruption-claims?shift_id={OBSERVATION_SHIFT}&team=QA").get_json()["data"]["goals"][0]["decision_version"]
    decider.post("/api/v1/excusal-decisions", json={"claim_ids": [claim_id], "decision": "APPROVED", "unit_ids": [missed[0]["unit_id"]],
                                                    "notes": "roster confirms the pull", "expected_version": version,
                                                    "idempotency_key": "twin-decide-0001"}, headers={"X-CSRF-Token": dtoken})
    WorkerService(services).run_until_idle()
    corrected = evaluate_shift(services, "QA", OBSERVATION_SHIFT, as_of=knowledge_time(services))
    after = twin(manager, "?team=QA")["data"]
    new_ids = {c["key"]["evaluation_id"] for c in after["cells"] if c["key"]["evaluation_id"]}
    assert new_ids == {corrected.id} and corrected.id != old_id
    assert corrected.supersedes == old_id and corrected.revision == history.get(services, old_id).revision + 1
    assert history.get(services, old_id) is not None                   # the earlier revision stays readable, never rewritten
    relieved = after["totals"]["observed"]["grand"]["relieved_minutes"] - before["totals"]["observed"]["grand"]["relieved_minutes"]
    assert relieved == missed[0]["effort"] and after["reconciliation"]["ok"]


def test_twin_without_accepted_plans_is_honest_and_still_reconciles(demo, app_root) -> None:
    services, _, snapshot = demo
    app = create_app(services=services)
    app.config["TESTING"] = True
    c = app.test_client()
    login(c, "manager")
    env = twin(c)
    d = env["data"]
    assert env["status"] == "ready" and d["reconciliation"]["ok"], d["reconciliation"]
    assert {r["code"] for r in env["reasons"]} >= {"NO_ACCEPTED_PLAN", "NO_OBSERVATIONS"}
    assert d["revision"]["plan_id"] is None and d["cutoffs"]["planned"] is None and d["cutoffs"]["observed"] is None
    assert d["inventory"]["total"]["planned"] == {"no_accepted_plan": len(snapshot.jobs)}       # absent plans are not missing jobs
    assert d["totals"]["planned"]["grand"]["standard_minutes"] == 0 and not d["cells"]
    assert len(d["unknown"]["jobs"]) == sum(1 for x in snapshot.dispositions if x.disposition.value != "verified_complete")


# ---------------------------------------------------------------------- A65: scenarios never move live pointers

def test_a65_scenario_cancel_timeout_and_retry_never_change_the_twin_or_live_state(observed, app_root, tmp_path) -> None:
    from focus.analytics.scenarios import run_scenario
    from focus.workers import WorkerService

    root = copy_world(observed, tmp_path)
    planner, services, token = client(root, app_root, "manager")
    snapshot_before = tree(root)
    twin_before = twin(planner)
    source = twin_before["data"]["revision"]["source_run_id"]
    headers = {"X-CSRF-Token": token}

    def post(changes: list[dict[str, Any]], key: str) -> str:
        r = planner.post("/api/v1/scenarios", json={"source_run_id": source, "changes": changes, "budget_seconds": 4, "idempotency_key": key},
                         headers=headers)
        assert r.status_code == 202, r.get_data(as_text=True)
        return r.get_json()["data"]["run_id"]

    def status(run_id: str) -> dict[str, Any]:
        return planner.get(f"/api/v1/runs/{run_id}").get_json()["data"]

    plus_one = post([{"type": "virtual_staffing", "team": "PROD-B", "date": "2026-10-26", "shift": 1, "add": 1}], "a65-plus-one-0001")
    queued = post([{"type": "travel_minutes", "changed_aircraft_minutes": 45}], "a65-queued-0001")
    r = planner.post(f"/api/v1/runs/{queued}/cancel", json={"reason": "not needed"}, headers=headers)
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "cancelled"
    worker = WorkerService(services)
    done = worker.run_until_idle()
    assert [x.id for x in done] == [plus_one] and status(plus_one)["status"] == "completed"
    assert status(plus_one)["result_receipt"]["capability"] == "SCENARIO_ONLY" and status(queued)["status"] == "cancelled"
    # a running scenario: the cancel request is durable and the handler stops cooperatively without a result
    running = post([{"type": "duration_statistic", "statistic": "P90"}], "a65-running-0001")
    claimed = services.repositories.claim_worker_run("test-worker", 60)
    assert claimed.id == running and status(running)["status"] == "running"
    r = planner.post(f"/api/v1/runs/{running}/cancel", json={}, headers=headers)
    assert r.status_code == 200 and r.get_json()["data"]["cancel_requested"] is True
    again = planner.post(f"/api/v1/runs/{running}/cancel", json={}, headers=headers)
    assert again.get_json()["data"]["replayed"] is True
    assert worker.execute(claimed).status == "cancelled" and status(running)["error"]["code"] == "CANCELLED"
    r = planner.post(f"/api/v1/runs/{plus_one}/cancel", json={}, headers=headers)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "RUN_NOT_CANCELLABLE"
    # a timeout: the baseline overruns the budget, so the changed copy is never scheduled; a retry of the run is the same result
    ticks = itertools.chain([0.0], itertools.repeat(1000.0))
    timed = run_scenario(services, run_id="RUN-a65-timeout", requested_by="demo:manager", source_run_id=source,
                         changes=[{"type": "travel_minutes", "changed_aircraft_minutes": 30}], budget_seconds=2, at=services.clock.now(),
                         timer=lambda: next(ticks))
    assert timed.status == "TIMED_OUT" and timed.scenario_candidate_id is None
    retried = run_scenario(services, run_id="RUN-a65-timeout", requested_by="demo:manager", source_run_id=source,
                           changes=[{"type": "travel_minutes", "changed_aircraft_minutes": 30}], budget_seconds=2, at=services.clock.now())
    assert retried == timed
    # nothing live moved: heads, goals, plans, commitments, actuals, evaluations and recognition are byte-identical
    moved = changed(snapshot_before, tree(root))
    assert moved and all(p.startswith(SCENARIO_WRITES) for p in moved), sorted(p for p in moved if not p.startswith(SCENARIO_WRITES))
    assert not any(p.startswith("objects/snapshots/") for p in moved)            # a toggled-policy rebuild stays in the scenario namespace
    assert twin(planner) == twin_before


# ---------------------------------------------------------------------- scenarios: typed changes, travel and priority

def test_scenario_changes_are_typed_and_bounded() -> None:
    from focus.analytics.scenarios import normalize_changes
    from focus.domain import DomainError

    ok = normalize_changes([{"type": "travel_minutes", "changed_aircraft_minutes": 0}, {"type": "task_priority", "job_id": "J1", "rank": 20}])
    assert {c["type"] for c in ok} == {"travel_minutes", "task_priority"}
    bad = [
        ([{"type": "travel_minutes", "changed_aircraft_minutes": 241}], "BAD_CHANGE"),
        ([{"type": "travel_minutes", "changed_aircraft_minutes": True}], "BAD_CHANGE"),
        ([{"type": "travel_minutes", "changed_aircraft_minutes": 5}, {"type": "travel_minutes", "changed_aircraft_minutes": 6}], "BAD_CHANGE"),
        ([{"type": "task_priority", "job_id": "J1", "rank": 0}], "BAD_CHANGE"),
        ([{"type": "task_priority", "job_id": "J1", "rank": 21}], "BAD_CHANGE"),
        ([{"type": "task_priority", "job_id": "J1", "rank": 1}, {"type": "task_priority", "job_id": "J1", "rank": 2}], "BAD_CHANGE"),
        ([{"type": "task_priority", "job_id": "J1", "rank": 1, "approved": True}], "BAD_CHANGE"),
        ([{"type": "tool_capacity", "tool": "T1", "add": 1}], "UNSUPPORTED_CHANGE"),
        ([{"type": "task_priority", "job_id": f"J{i}", "rank": 1} for i in range(21)], "TOO_MANY_CHANGES"),
    ]
    for changes, code in bad:
        with pytest.raises(DomainError) as err:
            normalize_changes(changes)
        assert err.value.code == code, changes


def test_scenario_travel_toggle_and_priority_keep_every_rule(published, app_root, tmp_path) -> None:
    from focus.analytics.scenarios import SCENARIO_SNAPSHOTS, run_scenario
    from focus.domain import DomainError, Purpose
    from focus.engine.model import EngineContext
    from focus.engine.max.optimizer.neighborhoods import PriorityState
    from focus.engine.max.optimizer.orchestrator import CANDIDATES, make_priority, schedule_snapshot
    from focus.contracts.planning import CandidatePlan

    root = copy_world(published, tmp_path)
    services = services_on(root, app_root)
    cn = canon(services)
    source = cn.source_run_id
    travel = run_scenario(services, run_id="RUN-travel-60", requested_by="t", source_run_id=source,
                          changes=[{"type": "travel_minutes", "changed_aircraft_minutes": 60}], budget_seconds=4, at=services.clock.now())
    assert travel.status == "COMPLETED" and travel.scenario_valid is True and travel.capability == "SCENARIO_ONLY"
    copy = services.repositories.get_typed(SCENARIO_SNAPSHOTS, travel.scenario_snapshot_id, "PlanningSnapshot", type(cn.snapshot))
    assert copy.policy_hash != cn.snapshot.policy_hash                         # the changed copy carries its own toggled policy
    plan = services.repositories.get_typed(CANDIDATES, travel.scenario_candidate_id, "CandidatePlan", CandidatePlan)
    changed_aircraft = [d for d in plan.duties if d.kind.value == "TRAVEL" and None not in (d.aircraft_from, d.aircraft_to)
                        and d.aircraft_from != d.aircraft_to]
    assert changed_aircraft and all(minutes(d) == 60 for d in changed_aircraft)
    # planned task priority never waives a rule: rank 1 on a job waiting for a part keeps it after the part's ETA floor,
    # still labeled conditional (a forecast-like scenario never claims receipt) ...
    jobs = {(j.key.line, j.key.native_id): j.id for j in cn.snapshot.jobs}
    child = jobs[(1305, "CHILD5")]
    part = next(r for r in cn.snapshot.releases if child in r.scope_keys and r.kind.value == "PART")
    prio = run_scenario(services, run_id="RUN-prio-child", requested_by="t", source_run_id=source,
                        changes=[{"type": "task_priority", "job_id": child, "rank": 1}], budget_seconds=4, at=services.clock.now())
    scen = services.repositories.get_typed(CANDIDATES, prio.scenario_candidate_id, "CandidatePlan", CandidatePlan)
    assert prio.scenario_valid is True and part.status.value != "confirmed"
    outcome = next(o for o in scen.job_dispositions if o.job == child)
    assert outcome.disposition.value != "scheduled" and outcome.conditional
    assert outcome.start is None or outcome.start >= part.forecast_at
    assert all(a.start >= part.forecast_at for a in scen.assignments if a.job == child)
    # ... and the rank leads the dispatch key, so search moves cannot reorder it
    ctx = EngineContext(cn.snapshot, services.policy, Purpose.SCENARIO)
    a = sorted(ctx.phases)[0]
    b = next(pid for pid in sorted(ctx.phases) if ctx.phases[pid].job != ctx.phases[a].job)
    job_a, job_b = ctx.phases[a].job, ctx.phases[b].job
    boosted_b = PriorityState({job_b: -1000.0})
    key = make_priority(ctx, boosted_b, None, {job_a: -20})
    assert key(a) < key(b)
    with pytest.raises(DomainError) as err:
        schedule_snapshot(services, cn.snapshot, purpose=Purpose.EXECUTABLE, fixed_ranks={job_a: -1})
    assert err.value.code == "PRIORITY_SCENARIO_ONLY"
    with pytest.raises(DomainError) as err:
        run_scenario(services, run_id="RUN-prio-bad", requested_by="t", source_run_id=source,
                     changes=[{"type": "task_priority", "job_id": "J-not-a-job", "rank": 1}], budget_seconds=2, at=services.clock.now())
    assert err.value.code == "JOB_NOT_FOUND"


# ---------------------------------------------------------------------- A64: virtual support is forecast only, by origin

def test_a64_ten_virtual_people_per_team_shift_union_certs_and_origin_rejection(published, app_root, tmp_path) -> None:
    from focus.domain import PersonOrigin, Purpose, RequirementStatus
    from focus.publication.service import publish
    from focus.simulation.pools import VIRTUAL_NAMESPACE, admit_executable_crew
    from focus.validation.plan import validate

    root = copy_world(published, tmp_path)
    manager, services, token = client(root, app_root, "manager")
    cn = canon(services)
    snap, policy = cn.snapshot, services.policy
    people = {p.key.id: p for p in snap.people}
    teams = tuple(policy.seq("support.virtual_teams"))
    assert set(teams) == {"MHE", "PSC", "TST"}
    union: dict[str, set[str]] = defaultdict(set)
    job_team = {j.id: j.team for j in snap.jobs}
    for req in snap.requirements:
        if req.status is RequirementStatus.REQUIRED and req.bundle is not None and job_team.get(req.job) in teams:
            union[job_team[req.job]] |= set(req.bundle.all_codes)
    held: dict[str, set[str]] = defaultdict(set)
    for h in snap.holdings:
        held[h.person].add(h.cert)
    for pool in snap.pools:
        if pool.key.team not in teams:
            continue
        virtual = [m for m in pool.forecast_members if people[m].key.authority_namespace == VIRTUAL_NAMESPACE]
        assert len(virtual) == len(set(virtual)) == 10, pool.id
        assert not set(virtual) & set(pool.executable_members)
        for m in virtual:
            p = people[m]
            assert p.origin is PersonOrigin.SYNTHETIC_VIRTUAL and p.synthetic and p.purpose_scope == "FORECAST_ONLY"
            assert held[m] == union[pool.key.team], (pool.key.team, m)
    # the forecast is labeled; the same crew relabeled EXECUTABLE (a spoofed production purpose) fails on origin evidence
    forecast = cn.plan("FORECAST")
    with_virtual = [a for a in forecast.assignments if any(people[r.person].synthetic for r in a.crew)]
    assert with_virtual and all(any(x.startswith("SUPPORT_RESOURCE_UNAVAILABLE") for x in a.conditional_reasons) for a in with_virtual)
    spoofed = replace(forecast, purpose=Purpose.EXECUTABLE)
    receipt = validate(snap, policy, spoofed, kind="FINAL")
    assert not receipt.valid and ("V5", "SYNTHETIC_IN_EXECUTABLE") in {(v.check, v.code) for v in receipt.violations}, \
        sorted({(v.check, v.code) for v in receipt.violations})
    pool = next(p for p in snap.pools if p.id == with_virtual[0].pool_key)
    decision = admit_executable_crew([r.person for r in with_virtual[0].crew], pool, people,
                                     claims={"mock_data": False, "purpose": "EXECUTABLE", "synthetic": False})
    assert not decision.allowed and decision.code in ("SYNTHETIC_NOT_EXECUTABLE", "POOL_SYNTHETIC_ONLY")
    assert "payload claims ignored" in decision.reason
    # a scenario cannot be spoofed into an executable plan either
    body = {"source_run_id": cn.source_run_id, "budget_seconds": 4, "idempotency_key": "a64-spoof-0001",
            "changes": [{"type": "virtual_staffing", "team": "MHE", "date": "2026-10-26", "shift": 1, "add": 1, "mock_data": False}]}
    r = manager.post("/api/v1/scenarios", json=body, headers={"X-CSRF-Token": token})
    assert r.status_code == 422
    from focus.analytics.scenarios import run_scenario

    result = run_scenario(services, run_id="RUN-a64-virtual", requested_by="demo:manager", source_run_id=cn.source_run_id,
                          changes=[{"type": "virtual_staffing", "team": "PROD-B", "date": "2026-10-26", "shift": 1, "add": 2}],
                          budget_seconds=4, at=services.clock.now())
    assert result.capability == "SCENARIO_ONLY"
    refused = publish(services, result.scenario_candidate_id)
    assert refused.status != "ACCEPTED" and refused.reasons[0].startswith("PURPOSE_NOT_PUBLISHABLE"), refused.reasons
