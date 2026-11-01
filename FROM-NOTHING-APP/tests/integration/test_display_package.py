"""The display package: built by the pipeline, verified by the web, and the only thing the web reads.

* the source head's snapshot and both accepted plans carry a package whose every part hashes to its manifest entry, and a
  plan's manifest is signed under the display domain;
* every read endpoint answers with whole-snapshot loading and artifact decoding switched off;
* a changed part, manifest or receipt is never shown, and a plan without a package says so;
* builds are idempotent, an older store is backfilled by the CLI, and an acquisition replay rebuilds a missing package;
* the paged lists cover every row exactly once.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.codec import from_jsonable, open_envelope, seal
from focus.config import Settings
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore, _file_token
from focus.display.records import DisplayManifest, DisplayReceipt
from focus.display.store import MANIFESTS, PART_CACHE, PARTS, RECEIPTS, load_part, snapshot_manifest, verify_plan_display
from focus.runtime import build_services
from focus.web.app import create_app
from tests.conftest import OBSERVATION_SHIFT, login


def services_on(root: Path, app_root: Path, **overrides: Any):
    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False, **overrides)
    clock = FixedClock(FIXTURE_CUTOFF)
    return build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                          clock=clock)


@pytest.fixture(scope="module")
def template(tmp_path_factory, app_root):
    from focus.pipeline import demo_run

    root = tmp_path_factory.mktemp("display_package") / "runtime"
    result = demo_run(services_on(root, app_root), budget_seconds=5, max_moves=5)
    assert result["status"] == "ok", result
    return root


@pytest.fixture()
def world(template, tmp_path, app_root):
    root = tmp_path / "runtime"
    shutil.copytree(template, root)
    _clear_caches()
    services = services_on(root, app_root)
    return SimpleNamespace(root=root, services=services, repos=services.repositories, scope=services.settings.scope_key)


def _clear_caches() -> None:
    from focus.web import serializers as S

    PART_CACHE.clear()
    S.CACHE.clear()
    S.PROJECTIONS.clear()


def heads(w) -> tuple[Any, dict[str, str]]:
    from focus.data.acquisition import get_run, source_head_name
    from focus.publication.repository import repository_for

    run = get_run(w.repos, w.repos.head(source_head_name(w.scope)).record_id)[0]
    repo = repository_for(w.services)
    return run, {p: repo.head(w.scope, p).record_id for p in ("EXECUTABLE", "FORECAST")}


def path_of(w, collection: str, record_id: str) -> Path:
    return w.root / "objects" / collection / f"{_file_token(record_id)}.json"


def reseal(w, collection: str, record_id: str, change) -> None:
    """Rewrite a stored record with changed content under a valid envelope (what a store writer could do)."""
    path = path_of(w, collection, record_id)
    env = json.loads(path.read_bytes())
    payload = change(env["payload"])
    path.write_bytes(seal(env["type"], env["id"], payload, created_at=FIXTURE_CUTOFF, source_run_id=env["source_run_id"],
                          policy_hash=env["policy_hash"], purpose=env["purpose"]).to_bytes())


def client(w, app_root, persona: str, **overrides: Any):
    services = services_on(w.root, app_root, **overrides)
    app = create_app(services=services)
    app.config["TESTING"] = True
    c = app.test_client()
    login(c, persona)
    return c


# ---------------------------------------------------------------------- the package

def test_the_pipeline_writes_a_verified_package_for_the_source_head_and_both_plans(world) -> None:
    from focus.contracts.planning import CandidatePlan
    from focus.validation.plan import CANDIDATES

    w = world
    run, plans = heads(w)
    manifest, why = snapshot_manifest(w.repos, run.snapshot_id, run.snapshot_hash)
    assert why == "OK" and manifest.snapshot_hash == run.snapshot_hash
    names = {p.name for p in manifest.parts}
    summary = load_part(w.repos, manifest.part("summary"))
    lines = {c.line for c in summary.census}
    assert {f"line:{n}" for n in lines} | {f"standards:{n}" for n in lines} | {"summary", "holds", "releases"} <= names
    assert any(n.startswith("jobs:") for n in names) and any(n.startswith("work:") for n in names) and any(n.startswith("pools:") for n in names)
    for ref in manifest.parts:                                                     # every part hashes to its manifest entry
        load_part(w.repos, ref)
    for purpose, plan_id in plans.items():
        vp, why = verify_plan_display(w.services, plan_id, scope=w.scope, purpose=purpose)
        assert why == "OK" and vp.display.key_id == "fixture-test-key-v1" and vp.display.fixture
        cand = w.repos.get_typed(CANDIDATES, vp.summary.candidate_id, "CandidatePlan", CandidatePlan)
        assert (vp.summary.assignment_count, vp.summary.duty_count) == (len(cand.assignments), len(cand.duties))
        for ref in vp.manifest.parts:
            load_part(w.repos, ref)
        team_days = [load_part(w.repos, vp.manifest.part(f"team-day:{k}")) for k in vp.summary.team_days]
        assert {a.id for part in team_days for a in part.assignments} == {a.id for a in cand.assignments}
        assert {d.id for part in team_days for d in part.duties} == {d.id for d in cand.duties}   # every duty in some team-day


def test_every_read_endpoint_answers_without_loading_a_snapshot_or_an_artifact(world, app_root, monkeypatch) -> None:
    import focus.data.acquisition as acquisition
    import focus.publication.dispatch as dispatch
    import focus.publication.serializer as serializer
    import focus.publication.service as service
    from focus.performance.lifecycle import GoalRegistry
    from focus.web.operations import REGISTRY

    w = world
    run, plans = heads(w)
    snap = acquisition.load_snapshot(w.repos, run.snapshot_id)
    line = min(c.line for c in snap.census)
    job = next(j.id for j in snap.jobs if j.team == "PROD-A")
    paths = {
        "get_fleet": "/api/v1/fleet", "get_aircraft": f"/api/v1/aircraft/{line}", "list_jobs": "/api/v1/jobs?limit=5",
        "get_job": f"/api/v1/jobs/{job}", "get_plan": f"/api/v1/plans/{plans['EXECUTABLE']}", "get_shiftbook": "/api/v1/shiftbook?team=PROD-A",
        "get_myday": "/api/v1/myday?person=DEMO-A1-05", "get_workforce": "/api/v1/workforce", "get_capacity": "/api/v1/capacity",
        "get_commitments": "/api/v1/commitments", "get_economics": "/api/v1/economics", "get_shortages": "/api/v1/shortages",
        "get_quality": "/api/v1/quality", "get_performance": f"/api/v1/performance?shift_id={OBSERVATION_SHIFT}",
        "get_performance_history": "/api/v1/performance/history", "get_recognition": "/api/v1/recognition",
        "get_progression": "/api/v1/progression", "list_disruption_claims": "/api/v1/disruption-claims", "list_ie_reviews": "/api/v1/ie-reviews",
        "get_team_lead": "/api/v1/team-lead?team=PROD-A", "get_standards": "/api/v1/standards", "get_budget": "/api/v1/budget",
        "get_gantt": "/api/v1/gantt?team=PROD-A", "get_development": "/api/v1/development", "get_arena": "/api/v1/arena",
        "get_turnover": "/api/v1/turnover?team=PROD-A", "get_history": "/api/v1/history", "list_scenarios": "/api/v1/scenarios",
        "get_twin": "/api/v1/twin?team=PROD-A",
    }
    del snap

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the web loaded a snapshot or decoded a plan artifact")

    monkeypatch.setattr(acquisition, "load_snapshot", forbidden)
    for module in (serializer, service, dispatch):
        monkeypatch.setattr(module, "decode_artifact", forbidden)
    for name in ("verify_publication", "current_plan", "accepted_plans"):
        monkeypatch.setattr(service, name, forbidden)
    monkeypatch.setattr(GoalRegistry, "load", classmethod(forbidden))
    _clear_caches()
    snapshot_reads = {op.name for op in REGISTRY.all() if op.read_only and op.path.startswith("/api/v1/") and not op.path.startswith("/api/v1/sim")
                      and op.name not in ("get_run", "get_scenario", "get_explanation", "get_context")}
    assert snapshot_reads <= set(paths), sorted(snapshot_reads - set(paths))          # every read projection is exercised
    c = client(w, app_root, "manager")
    context = c.get("/api/v1/context").get_json()["data"]["choices"]                  # the session choices, from the summaries
    assert context["teams"] and context["lines"] and context["obligation_dates"]
    for name, path in paths.items():
        r = c.get(path)
        assert r.status_code == 200, (name, r.get_data(as_text=True)[:300])
        body = r.get_json()
        if name == "get_twin":
            assert body["status"] == "unknown" and "TWIN_NEEDS_FULL_LOAD" in {x["code"] for x in body["reasons"]}
        else:
            assert body["status"] in ("ready", "empty"), (name, body["status"], body["reasons"])
    lead = client(w, app_root, "lead-prod-a")
    for name in ("get_shiftbook", "get_team_lead", "get_turnover", "get_gantt", "list_jobs", "get_job"):
        assert lead.get(paths[name]).status_code == 200, name


# ---------------------------------------------------------------------- tampering

def test_a_changed_part_is_never_shown(world, app_root) -> None:
    w = world
    run, _ = heads(w)
    manifest, _ = snapshot_manifest(w.repos, run.snapshot_id, run.snapshot_hash)
    line = min(int(p.name.split(":")[1]) for p in manifest.parts if p.name.startswith("line:"))
    ref = manifest.part(f"line:{line}")
    reseal(w, PARTS, ref.sha256, lambda p: {**p, "jobs": p["jobs"][1:]})              # a job removed, envelope re-sealed
    c = client(w, app_root, "manager")
    body = c.get(f"/api/v1/aircraft/{line}").get_json()
    assert body["status"] == "blocked" and "DISPLAY_NOT_VERIFIED" in {x["code"] for x in body["reasons"]}
    assert "PART_HASH_MISMATCH" in body["reasons"][-1]["message"]
    reseal(w, PARTS, manifest.part("summary").sha256, lambda p: {**p, "census": p["census"][1:]})
    _clear_caches()
    body = client(w, app_root, "manager").get("/api/v1/fleet").get_json()
    assert body["status"] == "unknown" and body["data"] is None and "DISPLAY_NOT_VERIFIED" in {x["code"] for x in body["reasons"]}


def test_a_snapshot_manifest_must_name_the_source_runs_snapshot(world) -> None:
    w = world
    run, _ = heads(w)
    reseal(w, MANIFESTS, run.snapshot_id, lambda p: {**p, "snapshot_hash": "0" * 64})
    manifest, why = snapshot_manifest(w.repos, run.snapshot_id, run.snapshot_hash)
    assert manifest is None and why.startswith("DISPLAY_MISMATCH")


@pytest.mark.parametrize("attack", ["signature", "manifest", "swapped", "other_key", "missing"])
def test_a_plan_display_needs_the_signed_receipt_of_this_plan(world, app_root, attack) -> None:
    from focus.publication.signing import Signer

    w = world
    _, plans = heads(w)
    plan_id, other = plans["EXECUTABLE"], plans["FORECAST"]
    if attack == "signature":
        reseal(w, RECEIPTS, plan_id, lambda p: {**p, "signature_hex": "0" * 64})
    elif attack == "manifest":
        reseal(w, MANIFESTS, plan_id, lambda p: {**p, "parts": p["parts"][:-1]})
    elif attack == "swapped":
        path_of(w, RECEIPTS, plan_id).write_bytes(path_of(w, RECEIPTS, other).read_bytes())
    elif attack == "other_key":
        stranger = Signer("fixture-test-key-v1", True, b"\x01" * 32)                    # the same key id, another key
        env = json.loads(path_of(w, RECEIPTS, plan_id).read_bytes())
        receipt = from_jsonable(DisplayReceipt, env["payload"])
        forged = stranger.sign_display(receipt.scope, receipt.purpose.value, plan_id, receipt.payload_sha256, receipt.snapshot_hash,
                                       receipt.policy_hash, receipt.manifest_sha256)
        reseal(w, RECEIPTS, plan_id, lambda p: {**p, "signature_hex": forged})
    else:
        path_of(w, RECEIPTS, plan_id).unlink()
    vp, why = verify_plan_display(w.services, plan_id, scope=w.scope, purpose="EXECUTABLE")
    assert vp is None
    expected = "DISPLAY_NOT_BUILT" if attack == "missing" else "SECURITY_FAILURE"
    assert why.startswith(expected), why
    body = client(w, app_root, "manager").get("/api/v1/shiftbook?team=PROD-A").get_json()
    assert body["status"] == "unknown" and body["data"] is None
    assert {x["code"] for x in body["reasons"]} & {"DISPLAY_NOT_BUILT" if attack == "missing" else "PLAN_NOT_VERIFIED"}


# ---------------------------------------------------------------------- idempotence, backfill and recovery

def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and "/locks/" not in str(p)}


def test_builds_are_idempotent_and_the_cli_backfills_an_older_store(world, app_root) -> None:
    from focus.data.acquisition import load_snapshot
    from focus.display.store import cli_display, write_snapshot_display

    w = world
    run, plans = heads(w)
    before = tree(w.root)
    write_snapshot_display(w.repos, load_snapshot(w.repos, run.snapshot_id))
    assert tree(w.root) == before                                                   # nothing rewritten or added
    for plan_id in plans.values():                                                  # an acceptance from before the display package
        path_of(w, RECEIPTS, plan_id).unlink()
        path_of(w, MANIFESTS, plan_id).unlink()
    path_of(w, MANIFESTS, run.snapshot_id).unlink()
    out = cli_display(w.services, SimpleNamespace(snapshot=None, plan=None, current_source=True, current_plans=True))
    assert out["status"] == "ok" and len(out["built"]) == 3, out
    assert tree(w.root) == before                                                   # the same bytes as the pipeline wrote
    for purpose, plan_id in plans.items():
        assert verify_plan_display(w.services, plan_id, scope=w.scope, purpose=purpose)[1] == "OK"


def test_an_acquisition_replay_rebuilds_a_missing_snapshot_package(world) -> None:
    from focus.data.acquisition import acquire, make_reader

    w = world
    run, _ = heads(w)
    path_of(w, MANIFESTS, run.snapshot_id).unlink()
    outcome = acquire(w.services, make_reader(w.services), cutoff=run.cutoff, idempotency_key=run.idempotency_key)
    assert outcome.status == "COMPLETE" and outcome.replayed
    assert snapshot_manifest(w.repos, run.snapshot_id, run.snapshot_hash)[1] == "OK"


def test_a_publication_retry_signs_a_display_the_crash_left_unsigned(world, app_root) -> None:
    from focus.publication.service import publish

    w = world
    _, plans = heads(w)
    plan_id = plans["EXECUTABLE"]
    vp, _ = verify_plan_display(w.services, plan_id, scope=w.scope, purpose="EXECUTABLE")
    path_of(w, RECEIPTS, plan_id).unlink()                                          # signed acceptance, display receipt lost
    staged = publish(w.services, vp.summary.candidate_id, until="readback")         # the stager never signs it
    assert staged.status == "ALREADY_ACCEPTED" and "display_pending" in staged.steps
    assert verify_plan_display(w.services, plan_id, scope=w.scope, purpose="EXECUTABLE")[0] is None
    signed = publish(w.services, vp.summary.candidate_id)
    assert signed.status == "ALREADY_ACCEPTED" and "display" in signed.steps
    assert verify_plan_display(w.services, plan_id, scope=w.scope, purpose="EXECUTABLE")[1] == "OK"


# ---------------------------------------------------------------------- paging

def walk(c, path: str, key: str) -> list[Any]:
    rows, cursor = [], None
    while True:
        sep = "&" if "?" in path else "?"
        body = c.get(path + (f"{sep}cursor={cursor}" if cursor else "")).get_json()
        assert body["status"] in ("ready", "empty"), body
        rows += body["data"][key]
        cursor = body.get("next_cursor")
        if not cursor:
            return rows


def test_paged_lists_cover_every_row_exactly_once(world, app_root) -> None:
    import tests.support.reference_views as R
    from tests.integration.test_display_views import ctx_of

    w = world
    c = client(w, app_root, "manager")
    old = R.Canon.load(w.services)
    ctx = ctx_of("manager")
    assert walk(c, "/api/v1/jobs?limit=3", "jobs") == json.loads(json.dumps(R.jobs_list(old, ctx, line=None, team=None, disposition=None)[1],
                                                                          default=str))
    assert len(walk(c, "/api/v1/shortages?limit=1", "shortages")) == len(old.snapshot.shortages)
    ref = R.standards(old, ctx).data
    assert [d["job_id"] for d in walk(c, "/api/v1/standards?limit=1", "durations")] == [d["job_id"] for d in ref["durations"]]
    assert [r["job_id"] for r in walk(c, "/api/v1/standards?limit=1", "requirements")] == [r["job_id"] for r in ref["requirements"]]
    ref_jobs = R.budget(old, ctx, day=None, purpose="FORECAST").data["jobs"]
    assert [j["job_id"] for j in walk(c, "/api/v1/budget?purpose=FORECAST&limit=1", "jobs")] == [j["job_id"] for j in ref_jobs]
