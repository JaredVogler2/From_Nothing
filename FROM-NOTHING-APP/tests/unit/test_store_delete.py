"""The store's three retention operations: ``exists``, ``stored_ids`` and ``delete_immutable`` (D-24).

* only derived collections (display parts) can be deleted; every history collection is refused before anything happens;
* ``stored_ids`` lists exactly what ``list_ids`` lists, from file names, opening only records whose id is not a safe name;
* a lock holder's own time-to-live decides when it is stale; the unavailable store refuses deletion.
"""
from __future__ import annotations

import json
import time

import pytest

from focus.clock import FIXTURE_CUTOFF
from focus.codec import seal
from focus.data.store import (
    DERIVED_COLLECTIONS,
    FixtureStore,
    LockTimeout,
    StoreError,
    StoreUnavailable,
    UnavailableStore,
)


def envelope(record_id: str) -> bytes:
    return seal("Thing", record_id, {"value": record_id}, created_at=FIXTURE_CUTOFF).to_bytes()


def test_only_derived_records_can_be_deleted(tmp_path) -> None:
    store = FixtureStore(tmp_path)
    assert DERIVED_COLLECTIONS == frozenset({"display_parts"})
    for collection in ("snapshots", "published_plans", "display_manifests", "display_receipts", "candidate_digests", "frozen_goals"):
        store.put_immutable(collection, "R1", envelope("R1"))
        with pytest.raises(StoreError) as refused:
            store.delete_immutable(collection, "R1")
        assert refused.value.code == "NOT_DELETABLE" and store.exists(collection, "R1")
    data = envelope("a" * 64)
    store.put_immutable("display_parts", "a" * 64, data)
    assert store.exists("display_parts", "a" * 64)
    assert store.delete_immutable("display_parts", "a" * 64) == len(data)
    assert not store.exists("display_parts", "a" * 64) and store.get("display_parts", "a" * 64) is None
    assert store.delete_immutable("display_parts", "a" * 64) is None                      # already gone
    store.put_immutable("display_parts", "a" * 64, data)                                  # and it can be written again
    assert store.get("display_parts", "a" * 64) == data


def test_stored_ids_lists_what_list_ids_lists_without_opening_safe_records(tmp_path, monkeypatch) -> None:
    store = FixtureStore(tmp_path)
    ids = ["b" * 64, "SNAP123", "h-literal", "has space", "x" * 200, "ünïcode"]           # safe, safe "h-", unsafe (hashed tokens)
    for record_id in ids:
        store.put_immutable("display_parts", record_id, envelope(record_id))
    (tmp_path / "objects" / "display_parts" / ".tmp-unfinished").write_bytes(b"{")         # an unfinished write is not a record
    assert store.stored_ids("display_parts") == store.list_ids("display_parts") == sorted(ids)
    opened: list[str] = []
    from pathlib import Path

    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: opened.append(self.name) or original(self))
    store.stored_ids("display_parts")
    assert sorted(opened) == sorted(n for n in opened if n.startswith("h-")) and len(opened) == 4   # only the "h-" files
    assert store.stored_ids("never_written") == []


def test_a_lock_is_stale_by_its_holders_own_ttl(tmp_path) -> None:
    store = FixtureStore(tmp_path)
    with store.lock("display-parts", ttl_seconds=5, wait_seconds=1):
        owner = json.loads((tmp_path / "locks" / "display-parts.lock" / "owner").read_text())
        assert owner["ttl"] == 5
        with pytest.raises(LockTimeout), store.lock("display-parts", ttl_seconds=7200, wait_seconds=0.1):   # a long-ttl waiter still waits
            pass
    lock_dir = tmp_path / "locks" / "display-parts.lock"
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(json.dumps({"owner": "crashed", "at": time.time() - 10, "pid": 1, "ttl": 5}))
    with store.lock("display-parts", ttl_seconds=7200, wait_seconds=1):                   # stale by the holder's 5 s, not 7200 s
        pass


def test_the_unavailable_store_refuses_deletion() -> None:
    store = UnavailableStore()
    assert store.stored_ids("display_parts") == [] and store.exists("display_parts", "x") is False
    with pytest.raises(StoreUnavailable):
        store.delete_immutable("display_parts", "x")
