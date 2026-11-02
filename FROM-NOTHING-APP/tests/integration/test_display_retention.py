"""Display retention (D-24): the parts nothing reads are deleted; what the web reads, what can still become current and
every accepted plan's history parts are kept.

* after a second generation, the first one's snapshot display is released whole and its plans keep exactly their
  summary and goals parts; every read endpoint still answers and the goals of record are unchanged;
* a released display is never read again, can be rebuilt to its signed manifest, and is released again;
* a display that can still become current (a crashed publication, an unmoved head, an unpromoted run) is kept whole;
* writers and retention take turns on one lock, a crash at any point loses nothing the web reads, another scope's
  current displays are kept, and storage stays flat over generations.
"""
from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.acquisition import acquire, get_run, make_reader, source_head_name
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore, LockTimeout
from focus.display import retention as R
from focus.display.records import DisplayManifest
from focus.display.store import (
    DIGESTS,
    LOCK,
    MANIFESTS,
    PART_CACHE,
    PARTS,
    RECEIPTS,
    cli_display,
    load_part,
    verify_plan_display,
)
from focus.domain import Purpose
from focus.runtime import build_services
from tests.conftest import OBSERVATION_SHIFT, login

GEN2 = FIXTURE_CUTOFF + timedelta(hours=4)        # every generation stays inside the demo sources' freshness window (B7)
LATER = FIXTURE_CUTOFF + timedelta(hours=8)       # the world's clock: the second generation's heads moved four hours ago


class Crash(RuntimeError):
    pass


def services_on(root: Path, app_root: Path, at, fault=None, **env: str):
    settings = Settings.from_env(env, app_root=app_root, inprocess_worker=False)
    clock = FixedClock(at)
    repos = Repositories(FixtureStore(root, fault=fault), mode="fixture", scope_key=settings.scope_key, clock=clock)
    return build_services(settings, repositories=repos, clock=clock)


def generation(services, at, key: str, purposes=("EXECUTABLE", "FORECAST"), fault=None) -> dict[str, Any]:
    """One shift's pipeline at ``at``: acquire at that cutoff, then schedule and publish each purpose."""
    from focus.engine.max.optimizer.orchestrator import schedule
    from focus.publication.service import publish

    services.clock.set(at)
    out = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=at, idempotency_key=key)
    assert out.status == "COMPLETE", out.rejection
    plans = {}
    for purpose in purposes:
        candidate, _ = schedule(services, source_run=out.run_id, purpose=Purpose(purpose), budget_seconds=5, max_moves=5)
        result = publish(services, candidate.id, fault=fault)
        assert result.status == "ACCEPTED", result.reasons
        plans[purpose] = result.plan_id
    return {"run_id": out.run_id, "snapshot_id": out.snapshot_id, "plans": plans}


@pytest.fixture(scope="module")
def template(tmp_path_factory, app_root):
    from focus.pipeline import demo_run

    root = tmp_path_factory.mktemp("display_retention") / "runtime"
    services = services_on(root, app_root, FIXTURE_CUTOFF)
    first = demo_run(services, budget_seconds=5, max_moves=5)
    assert first["status"] == "ok", first
    gen1 = {"snapshot_id": first["snapshot_id"], "plans": {p: first["plans"][p]["plan_id"] for p in ("EXECUTABLE", "FORECAST")}}
    gen2 = generation(services, GEN2, "retention-2")
    return SimpleNamespace(root=root, gen1=gen1, gen2=gen2)


@pytest.fixture()
def world(template, tmp_path, app_root):
    root = tmp_path / "runtime"
    shutil.copytree(template.root, root)
    clear_caches()
    armed: dict[str, Any] = {"point": None}

    def fault(point: str) -> None:
        if point == armed["point"]:
            armed["point"] = None
            raise Crash(point)

    services = services_on(root, app_root, LATER, fault)
    return SimpleNamespace(root=root, services=services, repos=services.repositories, store=services.repositories.store, armed=armed,
                           gen1=template.gen1, gen2=template.gen2, scope=services.settings.scope_key, app_root=app_root)


def clear_caches() -> None:
    from focus.web import serializers as S

    PART_CACHE.clear()
    S.CACHE.clear()
    S.PROJECTIONS.clear()


def manifest(w, mid: str) -> DisplayManifest:
    return R._manifest(w.repos, mid)


def shas(w, mid: str, keep=lambda name: True) -> set[str]:
    return {ref.sha256 for ref in manifest(w, mid).parts if keep(ref.name)}


def stored(w, collection: str = PARTS) -> set[str]:
    return set(w.store.stored_ids(collection))


def current_ids(w) -> list[str]:
    return [w.gen2["snapshot_id"], *w.gen2["plans"].values()]


def files(w) -> dict[str, bytes]:
    return {str(p.relative_to(w.root)): p.read_bytes() for p in w.root.rglob("*") if p.is_file() and "/locks/" not in str(p)}


def read_paths(w) -> dict[str, str]:
    from focus.data.acquisition import load_snapshot

    snap = load_snapshot(w.repos, w.gen2["snapshot_id"])
    line = min(c.line for c in snap.census)
    job = next(j.id for j in snap.jobs if j.team == "PROD-A")
    plan = w.gen2["plans"]["EXECUTABLE"]
    return {
        "fleet": "/api/v1/fleet", "aircraft": f"/api/v1/aircraft/{line}", "jobs": "/api/v1/jobs?limit=5", "job": f"/api/v1/jobs/{job}",
        "plan": f"/api/v1/plans/{plan}", "shiftbook": "/api/v1/shiftbook?team=PROD-A", "myday": "/api/v1/myday?person=DEMO-A1-05",
        "workforce": "/api/v1/workforce", "capacity": "/api/v1/capacity", "capacity_forecast": "/api/v1/capacity?purpose=FORECAST",
        "commitments": "/api/v1/commitments", "economics": "/api/v1/economics", "shortages": "/api/v1/shortages", "quality": "/api/v1/quality",
        "performance": f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}", "performance_history": "/api/v1/performance/history",
        "recognition": "/api/v1/recognition", "progression": "/api/v1/progression", "team_lead": "/api/v1/team-lead?team=PROD-A",
        "standards": "/api/v1/standards", "budget": "/api/v1/budget", "gantt": "/api/v1/gantt?team=PROD-A", "development": "/api/v1/development",
        "turnover": "/api/v1/turnover?team=PROD-A", "history": "/api/v1/history", "scenarios": "/api/v1/scenarios",
    }


def client(w, persona: str = "manager"):
    from focus.web.app import create_app

    services = services_on(w.root, w.app_root, GEN2 + timedelta(minutes=1))   # a reader during the second generation's shift
    app = create_app(services=services)
    app.config["TESTING"] = True
    c = app.test_client()
    login(c, persona)
    return c


def assert_web_reads(w) -> None:
    clear_caches()
    c = client(w)
    for name, path in read_paths(w).items():
        r = c.get(path)
        assert r.status_code == 200, (name, r.get_data(as_text=True)[:300])
        body = r.get_json()
        assert body["status"] in ("ready", "empty"), (name, body["status"], body["reasons"])
    old = c.get(f"/api/v1/plans/{w.gen1['plans']['EXECUTABLE']}").get_json()             # a superseded plan still verifies
    assert old["status"] == "stale" and old["data"]["verified"] is True, old


def registry_facts(services) -> tuple:
    from focus.display.view import accepted_display
    from focus.performance.lifecycle import GoalRegistry

    plans, bad = accepted_display(services, "EXECUTABLE")
    reg = GoalRegistry.from_display(services, plans, bad)
    return ([p.published.id for p in plans], bad, sorted((k, v.goal_id, v.plan_id) for k, v in reg.records.items()), dict(reg.day_plan),
            dict(reg.unit_start), dict(reg.unit_end), dict(reg.unit_crew), sorted(reg.committed_units))


# ---------------------------------------------------------------------- the rule

def test_the_first_generation_is_released_and_only_history_parts_stay(world) -> None:
    w = world
    clear_caches()
    goals_before = registry_facts(w.services)
    before = files(w)
    report = R.run_retention(w.services)                                                  # a report changes nothing
    assert files(w) == before and report["status"] == "ok" and report["applied"] is False
    verdicts = {e["id"]: e["why"] for e in report["displays_released"]}
    assert verdicts == {w.gen1["snapshot_id"]: "superseded snapshot",
                        **{p: "superseded accepted plan; its summary and goals stay" for p in w.gen1["plans"].values()}}
    assert {e["id"] for e in report["displays_kept_whole"]} == set(current_ids(w))
    keep = set().union(*(shas(w, m) for m in current_ids(w)))
    history = set().union(*(shas(w, p, R.history_part) for p in w.gen1["plans"].values()))
    everything = stored(w)
    assert report["parts_to_delete"] == len(everything - keep - history) > 0
    manifests, receipts, digests = stored(w, MANIFESTS), stored(w, RECEIPTS), stored(w, DIGESTS)

    applied = R.run_retention(w.services, apply=True)
    assert applied["deleted_parts"] == report["parts_to_delete"] and applied["deleted_bytes"] > 0
    assert stored(w) == keep | history                                                    # exactly the kept parts remain
    assert (stored(w, MANIFESTS), stored(w, RECEIPTS), stored(w, DIGESTS)) == (manifests, receipts, digests)   # never deleted
    for mid in current_ids(w):                                                            # the current packages verify part by part
        for ref in manifest(w, mid).parts:
            load_part(w.repos, ref)
    for purpose, plan_id in w.gen1["plans"].items():                                      # the history still verifies end to end
        _, why = verify_plan_display(w.services, plan_id, scope=w.scope, purpose=purpose)
        assert why == "OK", why
    clear_caches()
    assert registry_facts(w.services) == goals_before                                     # the goals of record are unchanged
    assert_web_reads(w)
    receipt = w.repos.get_envelope(R.RUNS, applied["receipt_id"]).payload
    assert receipt["deleted_parts"] == applied["deleted_parts"] and len(receipt["displays_released"]) == 3


def test_a_released_display_is_never_read_again(world, monkeypatch) -> None:
    w = world
    R.run_retention(w.services, apply=True)
    read: list[str] = []
    original = R._manifest
    monkeypatch.setattr(R, "_manifest", lambda repos, mid: read.append(mid) or original(repos, mid))
    again = R.run_retention(w.services, apply=True)
    assert again["deleted_parts"] == 0 and again["displays_released"] == [] and again["displays_released_before"] == 3
    assert set(read) == set(current_ids(w))                                               # the released displays are not opened


def test_a_rebuilt_display_matches_its_signed_manifest_and_is_released_again(world) -> None:
    w = world
    R.run_retention(w.services, apply=True)
    old_plan, old_snapshot = w.gen1["plans"]["EXECUTABLE"], w.gen1["snapshot_id"]
    signed = w.repos.get_raw(RECEIPTS, old_plan)
    out = cli_display(w.services, SimpleNamespace(snapshot=old_snapshot, plan=[old_plan], current_source=False, current_plans=False))
    assert out["status"] == "ok", out
    assert w.repos.get_raw(RECEIPTS, old_plan) == signed                                  # the same signature, the same manifest
    _, why = verify_plan_display(w.services, old_plan, scope=w.scope, purpose="EXECUTABLE")
    assert why == "OK"
    for mid in (old_plan, old_snapshot):                                                  # every rebuilt part hashes to its entry
        for ref in manifest(w, mid).parts:
            load_part(w.repos, ref)
    keep = set().union(*(shas(w, m) for m in current_ids(w)), *(shas(w, p, R.history_part) for p in w.gen1["plans"].values()))
    again = R.run_retention(w.services, apply=True)
    assert again["deleted_parts"] == len((shas(w, old_plan) | shas(w, old_snapshot)) - keep) > 0
    assert stored(w) == keep


# ---------------------------------------------------------------------- missing parts are put back

def test_writers_put_back_missing_parts_of_the_current_packages(world) -> None:
    from focus.publication.repository import repository_for
    from focus.publication.service import publish

    w = world
    snap_part = manifest(w, w.gen2["snapshot_id"]).part("summary").sha256
    plan_part = next(r.sha256 for r in manifest(w, w.gen2["plans"]["EXECUTABLE"]).parts if r.name.startswith("team-day:"))
    for sha in (snap_part, plan_part):
        w.store.delete_immutable(PARTS, sha)
    clear_caches()
    c = client(w)
    body = c.get("/api/v1/fleet").get_json()                                              # a typed state that names the rebuild
    assert body["data"] is None and "DISPLAY_PARTS_MISSING" in {x["code"] for x in body["reasons"]}
    # the acquisition replay checks every part, not just the manifest
    services = services_on(w.root, w.app_root, GEN2)
    replay = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=GEN2, idempotency_key="retention-2")
    assert replay.replayed and w.store.exists(PARTS, snap_part)
    # so does the publication's recovery path
    published = repository_for(services).published(w.gen2["plans"]["EXECUTABLE"])
    again = publish(services, published.candidate_id)
    assert again.status == "ALREADY_ACCEPTED" and "display" in again.steps and w.store.exists(PARTS, plan_part)
    assert_web_reads(w)


# ---------------------------------------------------------------------- what can still become current

def test_a_plan_whose_publication_crashed_is_kept_until_it_can_no_longer_be_accepted(world) -> None:
    from focus.engine.max.optimizer.orchestrator import schedule
    from focus.publication.service import publish

    w = world
    w.services.clock.set(LATER)
    run3 = acquire(w.services, make_reader(w.services, "manufacturing_demo"), cutoff=LATER, idempotency_key="retention-3")
    candidate, _ = schedule(w.services, source_run=run3.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)

    def crash_after_display(step: str) -> None:
        if step == "after_display":
            raise Crash(step)

    with pytest.raises(Crash):
        publish(w.services, candidate.id, fault=crash_after_display)
    pending = next(m for m in stored(w, MANIFESTS) if m not in {w.gen1["snapshot_id"], w.gen2["snapshot_id"], run3.snapshot_id,
                                                                *w.gen1["plans"].values(), *w.gen2["plans"].values()})
    w.services.clock.set(LATER + timedelta(hours=2))
    report = R.run_retention(w.services, apply=True)
    assert {e["id"]: e["why"] for e in report["displays_kept_whole"]}[pending] == "not accepted yet; it can still be accepted"
    assert shas(w, pending) <= stored(w)
    retry = publish(w.services, candidate.id)                                             # its retry is accepted with a whole package
    assert retry.status == "ACCEPTED" and retry.plan_id == pending
    # a crashed publication on an older cutoff than the plan head can never be accepted: released whole
    w.services.clock.set(LATER + timedelta(hours=3))
    run4 = acquire(w.services, make_reader(w.services, "manufacturing_demo"), cutoff=LATER + timedelta(hours=3), idempotency_key="retention-4")
    stale, _ = schedule(w.services, source_run=run4.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    with pytest.raises(Crash):
        publish(w.services, stale.id, fault=crash_after_display)
    dead = next(m for m in stored(w, MANIFESTS) if manifest(w, m).subject == "PLAN" and manifest(w, m).source_run_id == run4.run_id)
    generation(w.services, LATER + timedelta(hours=4), "retention-5", purposes=("EXECUTABLE",))
    w.services.clock.set(LATER + timedelta(hours=6))
    report = R.run_retention(w.services, apply=True)
    assert {e["id"]: e["why"] for e in report["displays_released"]}[dead] == "can no longer be accepted"
    assert publish(w.services, stale.id).reasons[0].startswith("STALE_REPLAY")              # the rule matches the publisher


def test_an_acceptance_whose_head_did_not_move_is_kept_until_recovered(world) -> None:
    from focus.engine.max.optimizer.orchestrator import schedule
    from focus.publication.repository import plan_head
    from focus.publication.service import publish

    w = world
    run3 = acquire(w.services, make_reader(w.services, "manufacturing_demo"), cutoff=LATER, idempotency_key="retention-3")
    candidate, _ = schedule(w.services, source_run=run3.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    w.armed["point"] = f"before_head:{plan_head(w.scope, 'EXECUTABLE')}"
    with pytest.raises(Crash):
        publish(w.services, candidate.id)
    plan_id = next(m for m in stored(w, MANIFESTS) if manifest(w, m).subject == "PLAN" and manifest(w, m).source_run_id == run3.run_id)
    w.services.clock.set(LATER + timedelta(hours=2))
    report = R.run_retention(w.services, apply=True)
    assert {e["id"]: e["why"] for e in report["displays_kept_whole"]}[plan_id] == "accepted; recovery can still move the head to it"
    recovered = publish(w.services, candidate.id)
    assert recovered.status == "RECOVERED" and shas(w, plan_id) <= stored(w)


def test_a_complete_run_not_yet_promoted_is_kept(world) -> None:
    w = world

    def crash_before_head(step: str) -> None:
        if step == "before_head":
            raise Crash(step)

    with pytest.raises(Crash):
        acquire(w.services, make_reader(w.services, "manufacturing_demo"), cutoff=LATER, idempotency_key="retention-3", fault=crash_before_head)
    head = get_run(w.repos, w.repos.head(source_head_name(w.scope)).record_id)[0]
    assert head.snapshot_id == w.gen2["snapshot_id"]                                      # the head did not move
    w.services.clock.set(LATER + timedelta(hours=2))
    report = R.run_retention(w.services, apply=True)
    unpromoted = {e["id"]: e["why"] for e in report["displays_kept_whole"] if e["subject"] == "SNAPSHOT"}
    assert [why for mid, why in unpromoted.items() if mid != w.gen2["snapshot_id"]] == ["can still become the source head"]
    replay = acquire(w.services, make_reader(w.services, "manufacturing_demo"), cutoff=LATER, idempotency_key="retention-3")
    assert replay.head_moved and shas(w, replay.snapshot_id) <= stored(w)


def test_heads_moved_within_the_grace_period_keep_what_they_replaced(world) -> None:
    w = world
    w.services.clock.set(GEN2 + timedelta(minutes=30))
    report = R.run_retention(w.services, apply=True)
    assert report["deleted_parts"] == 0 and report["displays_released"] == []
    assert {e["why"] for e in report["displays_kept_whole"] if e["id"] not in current_ids(w)} == {
        "the source head moved within the grace period", "the plan head moved within the grace period"}
    assert R.run_retention(w.services, grace_minutes=0)["status"] == "invalid"


# ---------------------------------------------------------------------- safety

def test_writers_and_retention_take_turns_on_one_lock(world, monkeypatch) -> None:
    from focus.data.acquisition import load_snapshot
    from focus.display import store as display_store

    w = world
    monkeypatch.setattr(R, "RETAIN_WAIT", 0.2)
    before = files(w)
    with w.store.lock(LOCK, ttl_seconds=60, wait_seconds=1):                              # a writer is mid-package
        busy = R.run_retention(w.services, apply=True)
    assert busy["status"] == "busy" and files(w) == before                                # nothing was deleted
    monkeypatch.setattr(display_store, "WRITE_WAIT", 0.2)
    snapshot = load_snapshot(w.repos, w.gen2["snapshot_id"])
    with w.store.lock(LOCK, ttl_seconds=60, wait_seconds=1):                              # retention is deciding
        with pytest.raises(LockTimeout):
            display_store.write_snapshot_display(w.repos, snapshot)
        from focus.publication.repository import repository_for
        from focus.publication.serializer import decode_artifact

        plan_id = w.gen2["plans"]["EXECUTABLE"]
        published, payload = repository_for(w.services).published(plan_id), repository_for(w.services).payload(plan_id)
        with pytest.raises(LockTimeout):
            display_store.write_plan_display(w.repos, snapshot, decode_artifact(payload.payload_text.encode("utf-8")), plan_id,
                                             published.payload_hash, published.accepted_at)
    owner = w.root / "locks" / f"{LOCK}.lock" / "owner"                                   # a crashed holder frees it after its own ttl
    owner.parent.mkdir(parents=True)
    owner.write_text(json.dumps({"owner": "gone", "at": 0, "pid": 1, "ttl": 1}))
    assert R.run_retention(w.services, apply=True)["status"] == "ok"


def test_the_current_packages_are_kept_whatever_the_ledger_says(world) -> None:
    w = world
    wrong = {"schema": R.LEDGER_SCHEMA, "released": {mid: [] for mid in current_ids(w)}}       # a damaged ledger releases them
    w.repos.put_state(R.LEDGER, R.LEDGER_ID, wrong, 0)
    R.run_retention(w.services, apply=True)
    assert set().union(*(shas(w, m) for m in current_ids(w))) <= stored(w)
    assert_web_reads(w)


@pytest.mark.parametrize("point", ["before_state:display_retention", "before_delete:display_parts", "after_delete:display_parts",
                                   "before_put:display_retention_runs"])
def test_a_crash_at_any_point_loses_nothing_the_web_reads(world, point) -> None:
    w = world
    keep = set().union(*(shas(w, m) for m in current_ids(w)), *(shas(w, p, R.history_part) for p in w.gen1["plans"].values()))
    w.armed["point"] = point
    with pytest.raises(Crash):
        R.run_retention(w.services, apply=True)
    assert keep <= stored(w)
    finished = R.run_retention(w.services, apply=True)
    assert finished["status"] == "ok" and stored(w) == keep
    assert_web_reads(w)


def test_another_scopes_current_packages_are_kept(world, app_root) -> None:
    from focus.pipeline import demo_run

    w = world
    other = services_on(w.root, app_root, FIXTURE_CUTOFF, FOCUS_SCOPE_KEY="OTHER-787")
    assert demo_run(other, budget_seconds=5, max_moves=5, observe=False)["status"] == "ok"
    run = get_run(w.repos, w.repos.head(source_head_name("OTHER-787")).record_id)[0]
    from focus.publication.repository import repository_for

    others = [run.snapshot_id, *(repository_for(other).head("OTHER-787", p).record_id for p in ("EXECUTABLE", "FORECAST"))]
    w.services.clock.set(LATER)
    report = R.run_retention(w.services, apply=True)
    kept = {e["id"]: e["why"] for e in report["displays_kept_whole"]}
    assert {kept[m] for m in others} == {"current source head", "current plan head"}
    assert set().union(*(shas(w, m) for m in others)) <= stored(w)


def test_storage_stays_flat_over_generations(world) -> None:
    w = world
    sizes = []
    for k in range(3):
        at = LATER + timedelta(hours=3 * k)
        gen = generation(w.services, at, f"flat-{k}")
        w.services.clock.set(at + timedelta(hours=2))                                     # the next pipeline starts with retention
        report = R.run_retention(w.services, apply=True)
        current = [gen["snapshot_id"], *gen["plans"].values()]
        history = set()
        for mid in stored(w, MANIFESTS):
            m = manifest(w, mid)
            if m.subject == "PLAN" and w.repos.get_raw("published_plans", mid) is not None and mid not in current:
                history |= shas(w, mid, R.history_part)
        assert stored(w) == set().union(*(shas(w, m) for m in current)) | history, k
        sizes.append(sum((w.root / "objects" / PARTS / f"{s}.json").stat().st_size for s in stored(w)))
        assert {e["id"] for e in report["displays_kept_whole"]} == set(current)
    assert max(sizes) - min(sizes) < 0.2 * min(sizes)                                     # one package plus small history, not growing


def test_the_cli_reports_by_default_and_refuses_a_zero_grace(world) -> None:
    from focus.cli import build_parser

    w = world
    args = build_parser().parse_args(["display-retention"])
    assert (args.grace_minutes, args.apply) == (60, False)
    before = files(w)
    out = R.cli_display_retention(w.services, args)
    assert out["status"] == "ok" and out["parts_to_delete"] > 0 and files(w) == before
    applied = R.cli_display_retention(w.services, build_parser().parse_args(["display-retention", "--apply"]))
    assert applied["deleted_parts"] == out["parts_to_delete"]
    assert R.cli_display_retention(w.services, build_parser().parse_args(["display-retention", "--grace-minutes", "0"]))["status"] == "invalid"
