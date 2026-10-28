"""P16: canonical acceptance, pre-sign readback, crash recovery, security and current dispatch (A48, A51, A52, A53)."""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.codec import seal
from focus.config import Settings
from focus.contracts.planning import seal_candidate
from focus.data.acquisition import acquire, make_reader
from focus.data.fixture_reader import FixtureSourceReader
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import PersonKey, Purpose
from focus.engine.commitments import import_fixture_incumbent, lookup_incumbent
from focus.engine.max.optimizer.orchestrator import CANDIDATES, schedule
from focus.publication.dispatch import dispatch_check
from focus.publication.repository import (PAYLOADS, PUBLISHED, RECEIPTS, REPAIRS, FixturePublicationRepository, SqlPublicationRepository,
                                          plan_head)
from focus.publication.service import current_plan, publish
from focus.publication.signing import Signer
from focus.runtime import build_services
from focus.validation.plan import VALIDATIONS
from tests.conftest import job_id

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
HEAD = plan_head("DEMO-787", "EXECUTABLE")


class Crash(RuntimeError):
    pass


def _services(root: Path, app_root: Path, fault=None):
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root, fault=fault), mode="fixture", scope_key=settings.scope_key, clock=clock)
    return build_services(settings, repositories=repos, clock=clock)


@pytest.fixture(scope="module")
def template(tmp_path_factory, app_root):
    """Plan P0 accepted on run 1; candidate P1 scheduled on run 2 (same cutoff) and not yet published."""
    root = tmp_path_factory.mktemp("publication") / "rt"
    services = _services(root, app_root)
    run1 = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="pub-1")
    import_fixture_incumbent(services)
    p0c, _ = schedule(services, source_run=run1.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    p0 = publish(services, p0c.id)
    assert p0.status == "ACCEPTED", p0.reasons
    run2 = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="pub-2")
    p1c, _ = schedule(services, source_run=run2.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    assert p1c.id != p0c.id
    return SimpleNamespace(root=root, p0c=p0c, p0=p0, p1c=p1c, run1=run1, run2=run2)


@pytest.fixture()
def world(template, tmp_path, app_root):
    """A private copy of the template store, with an armable store fault."""
    root = tmp_path / "rt"
    shutil.copytree(template.root, root)
    armed = {"point": None}

    def fault(point: str) -> None:
        if point == armed["point"]:
            armed["point"] = None
            raise Crash(point)

    services = _services(root, app_root, fault)
    return SimpleNamespace(services=services, root=root, armed=armed, t=template, repo=FixturePublicationRepository(services.repositories))


def head_id(w) -> str | None:
    head = w.services.repositories.head(HEAD)
    return None if head is None else head.record_id


def test_accepted_plan_is_signed_frozen_and_reloaded(world) -> None:
    w = world
    receipt = w.t.p0.receipt
    assert head_id(w) == w.t.p0.plan_id and receipt.fixture_readback and receipt.key_id == "fixture-test-key-v1"
    assert w.t.p0.steps[:9] == ["final_validation", "serialize", "detached_validation", "stage", "readback", "readback_validation", "sign",
                                "verify", "accept"]                                        # the one canonical order
    current, why = current_plan(w.services, "DEMO-787", "EXECUTABLE")
    assert why == "OK" and current.published.id == w.t.p0.plan_id and current.artifact.candidate.id == w.t.p0c.id
    goals = w.repo.goals(current.published.frozen_goal_ids)
    assert goals and all(g.hash == g.computed_hash() and g.earning_units for g in goals)  # B0 frozen with its units
    assert {g.shift_start_date for g in goals} == set(current.artifact.obligation_dates)   # today's obligation set only
    inc = lookup_incumbent(w.services, "DEMO-787", "EXECUTABLE")
    assert inc.status == "LOADED" and inc.plan.origin == "PUBLISHED"                     # the next replan defends the accepted plan
    for kind in ("FINAL", "DETACHED", "READBACK"):
        assert w.services.repositories.get_raw(VALIDATIONS, w.t.p0.validations[kind]) is not None


# ---------------------------------------------------------------------- A48: corrupted stage readback

class CorruptingRepo(FixturePublicationRepository):
    def __init__(self, repositories, edit):
        super().__init__(repositories)
        self.edit = edit

    def read_stage(self, stage_id):
        obj = json.loads(super().read_stage(stage_id))
        self.edit(obj)
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _root_edge(o):
    e = next(x for x in o["graph"] if x["basis"] != "PART_RELEASE")
    e["pred_phase"], e["succ_phase"] = e["succ_phase"], e["pred_phase"]


READBACK_CORRUPTIONS = {
    "crew": (lambda o: o["candidate"]["assignments"][0]["crew"][0].update(person=PersonKey("DEMO-B1-02", "fixture-demo").id), "E_LINEAGE:PLAN_HASH_MISMATCH"),
    "root-edge": (_root_edge, "E_ARTIFACT:GRAPH_LINEAGE_MISMATCH"),
    "timezone": (lambda o: o.update(timezone="UTC"), "E_ARTIFACT:TIMEZONE_MISMATCH"),
    "station-load": (lambda o: o["cs_lineage"][0].update(station="745", raw_load_id=7002), "E_ARTIFACT:CS_LINEAGE_MISMATCH"),
    "offset": (lambda o: o["cs_lineage"][0].update(unload_offset=4), "E_ARTIFACT:CS_LINEAGE_MISMATCH"),
    "source-hash": (lambda o: o.update(snapshot_hash="1" * 64), "E_ARTIFACT:SOURCE_HASH_MISMATCH"),
}


@pytest.mark.parametrize("name", sorted(READBACK_CORRUPTIONS))
def test_a48_corrupted_readback_is_rejected_before_signing(world, name) -> None:
    w = world
    edit, expected = READBACK_CORRUPTIONS[name]
    result = publish(w.services, w.t.p1c.id, repository=CorruptingRepo(w.services.repositories, edit))
    assert result.status == "REJECTED" and expected in result.reasons, result.reasons
    assert any(r.startswith("STAGE_READBACK_MISMATCH") for r in result.reasons)
    assert "sign" not in result.steps and "accept" not in result.steps                     # no signing of corrupted bytes
    assert head_id(w) == w.t.p0.plan_id                                                     # no head promotion
    assert w.services.repositories.get_raw(PUBLISHED, result.plan_id) is None
    readback = w.services.repositories.get_envelope(VALIDATIONS, result.validations["READBACK"])
    assert readback is not None and readback.payload["valid"] is False                       # the independent readback receipt is durable


# ---------------------------------------------------------------------- A51: crash at every step

CRASHES = [
    ("after_stage", "service", "ACCEPTED"),
    ("after_readback", "service", "ACCEPTED"),
    ("after_sign", "service", "ACCEPTED"),
    ("after_put:published_plans", "store", "ACCEPTED"),
    (f"before_head:{HEAD}", "store", "RECOVERED"),
    (f"after_head:{HEAD}", "store", "ALREADY_ACCEPTED"),
    ("after_post", "service", "ALREADY_ACCEPTED"),
]


@pytest.mark.parametrize(("point", "where", "recovered"), CRASHES, ids=[c[0] for c in CRASHES])
def test_a51_crash_recovery_reconciles_once(world, point, where, recovered) -> None:
    w = world
    service_fault = None
    if where == "store":
        w.armed["point"] = point
    else:
        def service_fault(step):
            if step == point:
                raise Crash(step)
    with pytest.raises(Crash):
        publish(w.services, w.t.p1c.id, fault=service_fault)                               # no success is reported
    moved = point in (f"after_head:{HEAD}", "after_post")
    if not moved:
        assert head_id(w) == w.t.p0.plan_id                                                # the previous pointer is retained
        current, why = current_plan(w.services, "DEMO-787", "EXECUTABLE")
        assert why == "OK" and current.published.id == w.t.p0.plan_id
    result = publish(w.services, w.t.p1c.id)                                               # recovery: same content, reconciled once
    assert result.status == recovered, (result.status, result.reasons)
    assert head_id(w) == result.plan_id and w.services.repositories.head(HEAD).version == 2
    published = w.repo.published(result.plan_id)
    assert published.acceptance_receipt == w.repo.receipt(result.plan_id) and published.payload_hash == result.receipt.payload_sha256
    again = publish(w.services, w.t.p1c.id)
    assert again.status == "ALREADY_ACCEPTED" and w.services.repositories.head(HEAD).version == 2   # idempotent, no second acceptance
    current, why = current_plan(w.services, "DEMO-787", "EXECUTABLE")
    assert why == "OK" and current.published.id == result.plan_id


# ---------------------------------------------------------------------- A52: security failures

def test_a52_tampered_storage_never_becomes_current(world) -> None:
    w = world
    pid = w.t.p0.plan_id
    path = w.root / "objects" / PAYLOADS / f"{pid}.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"payload_text":"{', b'"payload_text":"{ ', 1))                # raw corruption
    current, why = current_plan(w.services, "DEMO-787", "EXECUTABLE")
    assert current is None and why.startswith("SECURITY_FAILURE")
    env = json.loads(original)
    obj = json.loads(env["payload"]["payload_text"])
    obj["candidate"]["assignments"][0]["crew"] = obj["candidate"]["assignments"][0]["crew"][:1]
    forged_text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    forged = seal("PublishedPayload", pid, {**env["payload"], "payload_text": forged_text}, created_at=FIXTURE_CUTOFF)
    path.write_bytes(forged.to_bytes())                                                     # re-sealed envelope, unsignable content
    current, why = current_plan(w.services, "DEMO-787", "EXECUTABLE")
    assert current is None and "payload hash" in why
    path.write_bytes(original)
    rpath = w.root / "objects" / RECEIPTS / f"{pid}.json"
    renv = json.loads(rpath.read_bytes())
    rpath.write_bytes(seal("AcceptanceReceipt", pid, {**renv["payload"], "signature_hex": "0" * 64}, created_at=FIXTURE_CUTOFF).to_bytes())
    current, why = current_plan(w.services, "DEMO-787", "EXECUTABLE")
    assert current is None and "signature" in why
    a1 = next(a for a in w.t.p0c.assignments if a.commitment_outcome == "KEPT")
    decision = dispatch_check(w.services, pid, a1.id)
    assert not decision.dispatchable and decision.reasons[0][0] == "PLAN_NOT_VERIFIED"


def test_a52_replayed_wrong_scope_and_unauthorized_never_accept(world) -> None:
    w = world
    rogue = Signer("rogue-key", False, b"r" * 32)
    refused = publish(w.services, w.t.p1c.id, signer=rogue)
    assert refused.status == "REJECTED" and refused.reasons[0].startswith("UNAUTHORIZED_SIGNER") and head_id(w) == w.t.p0.plan_id
    p1 = publish(w.services, w.t.p1c.id)
    assert p1.status == "ACCEPTED" and head_id(w) == p1.plan_id
    replay = publish(w.services, w.t.p0c.id)                                                # the historical artifact cannot impersonate current
    assert replay.status == "NOT_CURRENT" and head_id(w) == p1.plan_id
    old_action = w.t.p0c.assignments[0]
    assert dispatch_check(w.services, w.t.p0.plan_id, old_action.id).reasons[0][0] == "STALE_PLAN"
    w.services.repositories.move_head(plan_head("OTHER-SCOPE", "EXECUTABLE"), p1.plan_id, 0)        # a forged pointer in another scope
    current, why = current_plan(w.services, "OTHER-SCOPE", "EXECUTABLE")
    assert current is None and "wrong scope" in why


def test_a52_skipped_check_synthetic_capability_and_live_refusal(world) -> None:
    w = world
    repos = w.services.repositories
    forged = seal_candidate(replace(w.t.p1c, id="CAND-FORGED-SKIP", incumbent_id="INC-NOT-ACCEPTED"))
    repos.put_record(CANDIDATES, "CandidatePlan", forged.id, forged)
    skipped = publish(w.services, forged.id)
    assert skipped.status == "REJECTED" and any(r.startswith("REQUIRED_CHECKS_SKIPPED") for r in skipped.reasons)
    assert skipped.steps == ["final_validation"]                                            # nothing staged or signed
    virtual = next(p.id for p in _people(w) if p.key.native_id == "VIRTUAL-MHE-S1-01")
    a = w.t.p1c.assignments[0]
    synthetic = seal_candidate(replace(w.t.p1c, id="CAND-FORGED-SYNTHETIC",
                                       assignments=(replace(a, crew=(replace(a.crew[0], person=virtual),) + a.crew[1:]),) + w.t.p1c.assignments[1:]))
    repos.put_record(CANDIDATES, "CandidatePlan", synthetic.id, synthetic)
    refused = publish(w.services, synthetic.id)
    assert refused.status == "REJECTED" and any("SYNTHETIC_IN_EXECUTABLE" in r for r in refused.reasons)
    live = publish(w.services, w.t.p1c.id, repository=SqlPublicationRepository(repos))
    assert live.status == "UNAVAILABLE" and live.reasons[0].startswith("SQL_STAGE_NOT_INSTALLED") and "sign" not in live.steps
    assert {"final_validation", "detached_validation"} <= set(live.steps)                    # the local rehearsal still ran

    class LiveLikeRepo:                                                                       # stages like SQL would, but is not the fixture
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

    test_key = publish(w.services, w.t.p1c.id, repository=LiveLikeRepo(FixturePublicationRepository(repos)))
    assert test_key.status == "REJECTED" and test_key.reasons[0].startswith("TEST_KEY_NOT_LIVE") and head_id(w) == w.t.p0.plan_id


def _people(w):
    from focus.data.acquisition import snapshot_for_run

    return snapshot_for_run(w.services, w.t.run2.run_id).people


def test_a52_forecast_publication_is_never_executable_dispatch(world) -> None:
    w = world
    import_fixture_incumbent(w.services, purpose="FORECAST")
    fplan, _ = schedule(w.services, source_run=w.t.run2.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    accepted = publish(w.services, fplan.id)
    assert accepted.status == "ACCEPTED" and "delivery_recorded" in accepted.steps            # forecast acceptance feeds delivery commitments
    current, _ = current_plan(w.services, "DEMO-787", "FORECAST")
    assert current.artifact.capability == "FORECAST_ONLY"
    virtual_action = next(a for a in fplan.assignments if a.conditional_reasons)
    decision = dispatch_check(w.services, accepted.plan_id, virtual_action.id)
    assert not decision.dispatchable and "FORECAST_ONLY" in {c for c, _ in decision.reasons}


# ---------------------------------------------------------------------- A53: current-action recheck

def _acquire_changed(w, key, edit, names):
    data = {n: json.loads((INPUTS / f"{n}.json").read_text()) for n in names}
    edit(data)
    outcome = acquire(w.services, FixtureSourceReader(INPUTS, overrides=data), cutoff=FIXTURE_CUTOFF, idempotency_key=key)
    assert outcome.status == "COMPLETE", outcome.rejection
    return outcome


def revoke(data):
    for r in data["mechanic_certs"]["rows"]:
        if r["BEMS_ID"] == "DEMO-A1-01" and r["CERT_CODE"] == "A":
            r["STATUS"] = "REVOKED"


def reopen(data):
    data["etracs_active"]["rows"].append({"ETRAC_CREATED_DT": "2026-10-25", "ETRAC_ECD": "2026-10-30", "ITEM_NUMBER": "DEMO-P-0013", "LINE_NUMBER": 1301,
                                          "LOAD_TIMESTAMP": "2026-10-25T17:30:00.000", "MINOR_MODEL_IDENT": "9", "REASON_CODE": "LP", "RECORD_ID": 50513,
                                          "TEAM": "PROD-A", "USED_ON": "A1"})


def unfinish(data):
    for r in data["master_task_list"]["rows"]:
        if r["LINE_NUMBER"] == 1301 and r["SHOP_ORDER_STATUS"] == "COMPLETE":
            r["SHOP_ORDER_STATUS"], r["ACT_COMP"] = "IN WORK", None


def withdraw(data):
    data["availability_exceptions"]["rows"].append({"END_LOCAL": None, "EVIDENCE_REF": "LEAVE-A101", "KIND": "LEAVE", "OBSERVED_AT_UTC": "2026-10-26T09:30:00Z",
                                                    "PERSON_KEY": "DEMO-A1-01", "PRODUCTION_DAY": "2026-10-26", "SHIFT_NUMBER": 1, "START_LOCAL": None})


@pytest.mark.parametrize(("name", "edit", "names", "code"), [
    ("cert-revoked", revoke, ("mechanic_certs",), "CERT_NOT_HELD"),
    ("shortage-reopened", reopen, ("etracs_active",), "PART_NOT_RECEIVED"),
    ("predecessor-incomplete", unfinish, ("master_task_list",), "PREDECESSOR_INCOMPLETE"),
    ("attendance-withdrawn", withdraw, ("availability_exceptions",), "ATTENDANCE_WITHDRAWN"),
])
def test_a53_adverse_current_evidence_blocks_dispatch_and_requests_repair(world, name, edit, names, code) -> None:
    w = world
    pid = w.t.p0.plan_id
    a1 = next(a for a in w.t.p0c.assignments if a.job == job_id(_snapshot(w, w.t.run1.run_id), 1301, "A1"))
    before = dispatch_check(w.services, pid, a1.id)
    assert before.dispatchable, before.reasons                                              # dispatchable on the accepted facts
    stored = (w.root / "objects" / PAYLOADS / f"{pid}.json").read_bytes()
    _acquire_changed(w, f"a53-{name}", edit, names)
    after = dispatch_check(w.services, pid, a1.id)
    assert not after.dispatchable and code in {c for c, _ in after.reasons}, after.reasons
    assert after.repair_request_id and w.services.repositories.get_raw(REPAIRS, after.repair_request_id) is not None
    assert (w.root / "objects" / PAYLOADS / f"{pid}.json").read_bytes() == stored            # accepted history is immutable
    current, why = current_plan(w.services, "DEMO-787", "EXECUTABLE")
    assert why == "OK" and current.published.id == pid                                      # the plan stays accepted; only dispatch blocks


def _snapshot(w, run_id):
    from focus.data.acquisition import snapshot_for_run

    return snapshot_for_run(w.services, run_id)


def test_cli_publish_and_dispatch_in_fresh_processes(tmp_path, app_root) -> None:
    import subprocess
    import sys

    env = {"FOCUS_RUNTIME_DIR": str(tmp_path / "rt"), "PATH": "/usr/bin:/bin"}

    def cli(*args: str) -> tuple[int, dict]:
        out = subprocess.run([sys.executable, "-m", "focus.cli", *args], cwd=app_root, env=env, capture_output=True, text=True, timeout=300)
        return out.returncode, json.loads(out.stdout)

    run_id = cli("acquire", "--mode", "fixture", "--scenario", "manufacturing_demo")[1]["source_run_id"]
    cli("demo-bootstrap", "--scenario", "manufacturing_demo")
    cand = cli("schedule", "--source-run", run_id, "--policy", "rebuild-v1", "--purpose", "EXECUTABLE", "--budget-seconds", "3")[1]["candidate_id"]
    code, flagless = cli("publish", "--candidate", cand, "--mode", "fixture")
    assert code == 2 and flagless["error"]["code"] == "REHEARSAL_FLAG_REQUIRED"
    code, pub = cli("publish", "--candidate", cand, "--mode", "fixture", "--rehearse-readback")
    assert code == 0 and pub["publication_status"] == "ACCEPTED" and pub["receipt"]["fixture_readback"] is True
    code, status = cli("status")
    assert status["heads"][HEAD]["record_id"] == pub["plan_id"]                              # readers select the accepted head
    code, again = cli("publish", "--candidate", cand, "--mode", "fixture", "--rehearse-readback")
    assert code == 0 and again["publication_status"] == "ALREADY_ACCEPTED"
    plan = json.loads((tmp_path / "rt" / "manufacturing_demo" / "objects" / CANDIDATES / f"{cand}.json").read_text())["payload"]
    first = plan["assignments"][0]["id"]
    code, decision = cli("dispatch-check", "--plan", pub["plan_id"], "--action", first)
    assert code == 0 and decision["decision"] in ("DISPATCHABLE", "BLOCKED") and "reasons" in decision
    code, missing = cli("dispatch-check", "--plan", pub["plan_id"], "--action", "ASG-NOT-THERE")
    assert code == 2 and missing["reasons"][-1]["code"] == "ACTION_NOT_FOUND"
