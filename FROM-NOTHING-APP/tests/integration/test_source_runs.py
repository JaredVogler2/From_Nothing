"""P04: atomic source runs, same-run CS lineage, idempotent replay and persistence (A05, A06, A07, A48 source part)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from focus.clock import FIXTURE_CUTOFF
from focus.codec import open_envelope, seal
from focus.data.acquisition import get_run, load_snapshot, source_head_name
from focus.data.fixture_reader import CaptureError, FixtureSourceReader
from focus.data.store import FixtureStore
from tests.conftest import acquire_demo

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


class Boom(Exception):
    pass


def test_a05_failed_mandatory_dataset_never_moves_head(services_factory) -> None:
    services = services_factory()
    first = acquire_demo(services, key="good-1", cutoff=FIXTURE_CUTOFF - timedelta(hours=1))
    assert first.status == "COMPLETE" and first.head_moved
    head_before = services.repositories.head(source_head_name(services.settings.scope_key))

    def fail_midway(name: str) -> None:
        if name == "cs_windows":
            raise CaptureError("SOURCE_TIMEOUT", "simulated read timeout midway through acquisition")

    reader = FixtureSourceReader(INPUTS, fault=fail_midway)
    failed = acquire_demo(services, key="bad-2", reader=reader)
    assert failed.status == "REJECTED"
    assert failed.rejection["code"] == "MANDATORY_DATASET_FAILED" and failed.rejection["dataset"] == "cs_windows"
    head_after = services.repositories.head(source_head_name(services.settings.scope_key))
    assert head_after == head_before                       # old head unchanged
    assert failed.snapshot_id is None


def test_a05_certified_empty_differs_from_unavailable(services_factory) -> None:
    services = services_factory()
    empty_uncertified = {"schema_version": 1, "dataset": "carry_in", "columns": ["PERSON_KEY", "KIND", "START_UTC", "END_UTC", "LINE_NUMBER", "SOI", "EVIDENCE_REF"],
                         "rows": [], "receipt": {"complete": True, "certified_empty": False}}

    def fail_optional(name: str) -> None:
        if name == "ot_authorizations":
            raise CaptureError("SOURCE_TIMEOUT", "optional feed unavailable")

    reader = FixtureSourceReader(INPUTS, fault=fail_optional, overrides={"carry_in": empty_uncertified})
    outcome = acquire_demo(services, key="empties", reader=reader)
    assert outcome.status == "COMPLETE"
    assert outcome.datasets["econ_liability_checkpoint"] == "CERTIFIED_EMPTY"    # affirmative producer receipt
    assert outcome.datasets["carry_in"] == "EMPTY_UNCERTIFIED"                  # empty without receipt
    assert outcome.datasets["ot_authorizations"] == "FAILED"                    # unavailable, not empty
    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    gap_codes = {(g.code, g.scope) for g in snap.gaps}
    assert ("DATASET_EMPTY_UNCERTIFIED", "carry_in") in gap_codes and ("DATASET_FAILED", "ot_authorizations") in gap_codes
    assert ("DATASET_CERTIFIED_EMPTY", "econ_liability_checkpoint") not in gap_codes


def test_a06_cs_dataset_from_another_run_fails_admission(services_factory) -> None:
    services = services_factory()
    other = acquire_demo(services, key="other-run", cutoff=FIXTURE_CUTOFF - timedelta(hours=2))
    foreign = services.repositories.get_raw("datasets", f"{other.run_id}.cs_windows")
    store: FixtureStore = services.repositories.store  # type: ignore[assignment]

    def swap(point: str) -> None:
        if point == "captured:cs_windows":
            run_id = max((r for r in store.list_state_ids("source_runs") if r != other.run_id))
            target = store.root / "objects" / "datasets" / f"{run_id}.cs_windows.json"
            target.write_bytes(foreign)                  # attacker attaches yesterday's CS dataset

    outcome = acquire_demo(services, key="swapped", fault=swap)
    assert outcome.status == "REJECTED" and outcome.rejection["code"] == "DATASET_FOREIGN_RUN"
    assert services.repositories.head(source_head_name(services.settings.scope_key)).record_id == other.run_id


def test_a06_forged_hash_cannot_cure_mismatch(services_factory) -> None:
    services = services_factory()
    store: FixtureStore = services.repositories.store  # type: ignore[assignment]

    def tamper(point: str) -> None:
        if point == "captured:cs_windows":
            run_id = store.list_state_ids("source_runs")[-1]
            path = store.root / "objects" / "datasets" / f"{run_id}.cs_windows.json"
            env = open_envelope(path.read_bytes())
            payload = dict(env.payload)
            rows = [dict(r) for r in payload["rows"]]
            rows[0]["CS_OP_UNLOAD"] = "2026-11-30"          # change a gate date
            payload["rows"] = rows
            # re-seal consistently so the envelope hash is valid; the declared content hash is now a lie
            forged = seal("SourceDataset", env.id, payload, created_at=env.created_at_utc, source_run_id=env.source_run_id, policy_hash=env.policy_hash)
            path.write_bytes(forged.to_bytes())

    outcome = acquire_demo(services, key="tampered", fault=tamper)
    assert outcome.status == "REJECTED" and outcome.rejection["code"] == "DATASET_HASH_MISMATCH"


def test_a06_cs_generation_mismatch_rejected(services_factory) -> None:
    services = services_factory()
    cs = json.loads((INPUTS / "cs_windows.json").read_text())
    cs["receipt"]["active_load_ids"] = {"1301": 7002}
    outcome = acquire_demo(services, key="gen", reader=FixtureSourceReader(INPUTS, overrides={"cs_windows": cs}))
    assert outcome.status == "REJECTED" and outcome.rejection["code"] == "CS_GENERATION_MISMATCH"


def test_a07_crash_before_head_then_replay_reconciles_once(services_factory) -> None:
    services = services_factory()

    def crash(point: str) -> None:
        if point == "before_head":
            raise Boom("process killed before head update")

    with pytest.raises(Boom):
        acquire_demo(services, key="crashy", fault=crash)
    store = services.repositories.store
    runs = store.list_state_ids("source_runs")
    assert len(runs) == 1
    run, _ = get_run(services.repositories, runs[0])
    assert run.status == "COMPLETE"
    assert services.repositories.head(source_head_name(services.settings.scope_key)) is None   # nothing visible yet
    replay = acquire_demo(services, key="crashy")
    assert replay.replayed and replay.run_id == run.id and replay.head_moved and replay.head_version == 1
    assert store.list_state_ids("source_runs") == runs                 # no duplicate run
    dataset_ids = store.list_ids("datasets")
    assert len(dataset_ids) == len(set(dataset_ids)) == len(run.datasets)   # no duplicate source facts
    again = acquire_demo(services, key="crashy")
    assert again.replayed and not again.head_moved and again.head_version == 1


def test_a07_crash_mid_capture_resumes_same_run(services_factory) -> None:
    services = services_factory()
    calls = {"n": 0}

    def crash_once(point: str) -> None:
        if point == "captured:flow_budget" and calls["n"] == 0:
            calls["n"] += 1
            raise Boom("killed mid-capture")

    with pytest.raises(Boom):
        acquire_demo(services, key="midcap", fault=crash_once)
    resumed = acquire_demo(services, key="midcap", fault=crash_once)
    assert resumed.status == "COMPLETE" and resumed.replayed
    assert len(services.repositories.store.list_state_ids("source_runs")) == 1


def test_head_only_moves_forward_in_cutoff(services_factory) -> None:
    services = services_factory()
    newer = acquire_demo(services, key="newer", cutoff=FIXTURE_CUTOFF)
    older = acquire_demo(services, key="older", cutoff=FIXTURE_CUTOFF - timedelta(hours=3))
    assert newer.head_moved and not older.head_moved
    head = services.repositories.head(source_head_name(services.settings.scope_key))
    assert head.record_id == newer.run_id


def test_a48_source_part_snapshot_binds_same_run_cs_lineage(demo) -> None:
    services, outcome, snap = demo
    assert all(f.source_run_id == snap.source_run_id for f in snap.cs_facts)
    assert all(ev.source_run_id == snap.source_run_id for f in snap.cs_facts for ev in f.evidence)
    run, _ = get_run(services.repositories, outcome.run_id)
    cs_receipt = next(d for d in run.datasets if d.name == "cs_windows")
    assert snap.dataset_lineage["cs_windows"] == cs_receipt.content_sha
    facts = {(f.line, f.station): f for f in snap.cs_facts}
    f1301 = facts[(1301, "744")]
    assert f1301.status == "ADMITTED" and f1301.raw_load_id == 7001 and f1301.raw_offset_fields["load_offset"] == 0
    assert f1301.gate_date.isoformat() == "2026-10-28" and f1301.due_proxy.isoformat() == "2026-10-30"
    snap.verify()                                                   # canonical hash round trip


def test_fixture_runs_survive_independent_processes(app_root, tmp_path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOCUS_")}
    env.update({"FOCUS_RUNTIME_DIR": str(tmp_path / "rt"), "FOCUS_APP_ROOT": str(app_root)})
    cmd = [sys.executable, "-m", "focus.cli", "acquire", "--mode", "fixture", "--scenario", "manufacturing_demo", "--idempotency-key", "proc-1"]
    first = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(app_root))
    assert first.returncode == 0, first.stdout + first.stderr
    a = json.loads(first.stdout)
    second = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(app_root))
    b = json.loads(second.stdout)
    assert a["source_run_id"] == b["source_run_id"] and b["replayed"] is True and a["head_moved"] is True
    status = subprocess.run([sys.executable, "-m", "focus.cli", "status"], capture_output=True, text=True, env=env, cwd=str(app_root))
    heads = json.loads(status.stdout)["heads"]
    assert heads["source/DEMO-787"]["record_id"] == a["source_run_id"]
