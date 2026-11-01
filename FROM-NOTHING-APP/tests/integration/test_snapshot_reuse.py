"""Speed item 2: reuse admitted data instead of re-reading it.

A pipeline process that needs the admitted snapshot in several steps (schedule, then validate, stage and sign) parses
and verifies it once, while the stored bytes are unchanged; any change to them is parsed and verified again. Publishing
reads the previous plan's source cutoff from its source run instead of loading that plan's whole snapshot.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

import focus.data.acquisition as acquisition
from focus.clock import FIXTURE_CUTOFF
from focus.codec import CodecError, seal
from focus.data.acquisition import SNAPSHOTS, load_snapshot
from focus.data.store import _file_token
from focus.domain import DomainError, Purpose
from focus.engine.max.optimizer.orchestrator import schedule
from focus.publication.service import publish
from tests.conftest import acquire_demo


def _stored(services, snapshot_id):
    return services.repositories.store.root / "objects" / SNAPSHOTS / f"{_file_token(snapshot_id)}.json"


def test_a_verified_snapshot_is_reused_while_its_stored_bytes_are_unchanged(demo, monkeypatch) -> None:
    services, outcome, _ = demo
    first = load_snapshot(services.repositories, outcome.snapshot_id)
    parsed: list[str] = []
    real = acquisition.from_jsonable
    monkeypatch.setattr(acquisition, "from_jsonable", lambda cls, *a, **k: parsed.append(cls.__name__) or real(cls, *a, **k))
    assert load_snapshot(services.repositories, outcome.snapshot_id) is first and parsed == []     # no second parse or hash
    path = _stored(services, outcome.snapshot_id)
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"site":"', b'"site":"X', 1))                             # raw corruption
    with pytest.raises(CodecError) as err:
        load_snapshot(services.repositories, outcome.snapshot_id)
    assert err.value.code == "HASH_MISMATCH"
    env = json.loads(original)
    forged = seal("PlanningSnapshot", env["id"], {**env["payload"], "site": "FORGED"}, created_at=FIXTURE_CUTOFF)
    path.write_bytes(forged.to_bytes())                                                         # re-sealed, content changed
    with pytest.raises(DomainError) as err:
        load_snapshot(services.repositories, outcome.snapshot_id)
    assert err.value.code == "SNAPSHOT_HASH_MISMATCH" and parsed == ["PlanningSnapshot"]         # parsed and verified again
    path.write_bytes(original)
    assert load_snapshot(services.repositories, outcome.snapshot_id) is first                   # the verified bytes are back


def test_the_admit_step_reuses_the_snapshot_its_acquisition_just_stored(services_factory, monkeypatch) -> None:
    """Acquire and admit run in the same source process: admit reads the stored bytes back and hashes them, and reuses
    the snapshot acquisition sealed instead of parsing it again. A change to the stored bytes is still parsed."""
    services = services_factory()
    parsed: list[str] = []
    real = acquisition.from_jsonable
    monkeypatch.setattr(acquisition, "from_jsonable", lambda cls, *a, **k: parsed.append(cls.__name__) or real(cls, *a, **k))
    outcome = acquire_demo(services, key="admit-reuse")
    snapshot = load_snapshot(services.repositories, outcome.snapshot_id)
    assert "PlanningSnapshot" not in parsed and snapshot.id == outcome.snapshot_id               # never parsed back
    snapshot.verify()
    path = _stored(services, outcome.snapshot_id)
    path.write_bytes(path.read_bytes().replace(b'"site":"', b'"site":"X', 1))
    with pytest.raises(CodecError):
        load_snapshot(services.repositories, outcome.snapshot_id)


def test_publishing_reads_the_previous_cutoff_from_its_source_run(services_factory, monkeypatch) -> None:
    services = services_factory()
    late = acquire_demo(services, key="late")
    early = acquire_demo(services, key="early", cutoff=FIXTURE_CUTOFF - timedelta(hours=1))
    assert late.status == early.status == "COMPLETE"
    current, _ = schedule(services, source_run=late.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    assert publish(services, current.id).status == "ACCEPTED"
    replay, _ = schedule(services, source_run=early.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    loaded: list[str] = []
    real = acquisition.load_snapshot
    monkeypatch.setattr(acquisition, "load_snapshot", lambda repos, sid: loaded.append(sid) or real(repos, sid))
    result = publish(services, replay.id)
    assert result.status == "REJECTED" and result.reasons[0].startswith("STALE_REPLAY")      # still refused: an older cutoff
    assert current.snapshot_id not in loaded                                                   # without loading the current plan's snapshot
