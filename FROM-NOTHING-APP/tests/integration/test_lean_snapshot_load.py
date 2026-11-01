"""Loading a stored snapshot holds as little at once as its checks allow (a 50-aircraft snapshot is 1.8 GB stored).

Canonical bytes are hashed in place, decoded and released, parsed and released, proven canonical by a streamed hash,
and built into objects while the parsed JSON is released item by item. The result and every check are those of the
strict path (``open_envelope`` then ``from_jsonable``); anything that is not canonical takes the strict path itself.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

import focus.codec as codec
import focus.data.acquisition as acquisition
from focus.codec import CodecError, from_jsonable, from_jsonable_releasing, open_envelope
from focus.contracts.planning import PlanningSnapshot
from focus.data.acquisition import SNAPSHOTS, load_snapshot
from focus.data.store import _file_token


def _path(services, snapshot_id):
    return services.repositories.store.root / "objects" / SNAPSHOTS / f"{_file_token(snapshot_id)}.json"


def _strict(raw: bytes) -> PlanningSnapshot:
    return from_jsonable(PlanningSnapshot, open_envelope(raw, "PlanningSnapshot").payload)


@pytest.fixture(params=["native", "python"])
def encoder(request):
    """Both canonical proofs: the native encoder's streamed hash, and the Python streaming encoder."""
    saved = codec.native_codec()
    if request.param == "native":
        assert codec.enable_native_codec(True)
    else:
        codec.enable_native_codec(False)
    yield request.param
    codec._CANON = saved


def test_canonical_bytes_take_the_lean_path_and_build_the_same_snapshot(demo, encoder, monkeypatch) -> None:
    services, outcome, _ = demo
    raw = _path(services, outcome.snapshot_id).read_bytes()
    expected = _strict(raw)
    acquisition._VERIFIED.clear()
    monkeypatch.setattr(acquisition, "open_envelope", lambda *a, **k: pytest.fail("canonical bytes took the strict path"))
    snapshot = load_snapshot(services.repositories, outcome.snapshot_id)
    assert snapshot == expected and snapshot.hash == expected.hash
    snapshot.verify()


def test_bytes_that_are_not_canonical_take_the_strict_path(demo, encoder, monkeypatch) -> None:
    services, outcome, _ = demo
    path = _path(services, outcome.snapshot_id)
    raw = path.read_bytes()
    expected = _strict(raw)
    path.write_bytes(json.dumps(json.loads(raw), indent=1).encode("utf-8"))        # same content, not canonical bytes
    acquisition._VERIFIED.clear()
    strict: list[str] = []
    real = acquisition.open_envelope
    monkeypatch.setattr(acquisition, "open_envelope", lambda data, kind: strict.append(kind) or real(data, kind))
    assert load_snapshot(services.repositories, outcome.snapshot_id) == expected and strict == ["PlanningSnapshot"]


def test_a_duplicate_key_is_still_rejected(demo, encoder) -> None:
    services, outcome, _ = demo
    path = _path(services, outcome.snapshot_id)
    raw = path.read_bytes()
    assert raw.count(b'"payload":{') == 1
    path.write_bytes(raw.replace(b'"payload":{', b'"payload":{"site":"ELSEWHERE",', 1))  # a second "site" in the payload
    acquisition._VERIFIED.clear()
    with pytest.raises(CodecError) as err:
        load_snapshot(services.repositories, outcome.snapshot_id)
    assert err.value.code == "DUPLICATE_KEY"


def test_changed_bytes_fail_the_envelope_hash(demo, encoder) -> None:
    services, outcome, _ = demo
    path = _path(services, outcome.snapshot_id)
    path.write_bytes(path.read_bytes().replace(b'"site":"', b'"site":"X', 1))         # still canonical, not the sealed body
    acquisition._VERIFIED.clear()
    with pytest.raises(CodecError) as err:
        load_snapshot(services.repositories, outcome.snapshot_id)
    assert err.value.code == "HASH_MISMATCH"


@dataclasses.dataclass(frozen=True)
class _Item:
    name: str
    size: int


@dataclasses.dataclass(frozen=True)
class _Box:
    label: str
    items: tuple[_Item, ...]
    note: str | None = None


def test_releasing_decoder_matches_from_jsonable_and_consumes_its_input() -> None:
    def data():
        return {"label": "b", "items": [{"name": f"i{n}", "size": n} for n in range(3)]}

    source = data()
    items = source["items"]
    assert from_jsonable_releasing(_Box, source) == from_jsonable(_Box, data()) == _Box("b", tuple(_Item(f"i{n}", n) for n in range(3)))
    assert source == {} and items == [None, None, None]                              # released as it was decoded
    with pytest.raises(CodecError) as err:
        from_jsonable_releasing(_Box, {**data(), "extra": 1})
    assert err.value.code == "UNKNOWN_FIELD"
    with pytest.raises(CodecError) as err:
        from_jsonable_releasing(_Box, {"items": []})
    assert err.value.code == "MISSING_FIELD"
    with pytest.raises(CodecError) as err:
        from_jsonable_releasing(_Box, {"label": "b", "items": [{"name": "i", "size": "big"}]})
    assert err.value.code == from_jsonable_error(_Box, {"label": "b", "items": [{"name": "i", "size": "big"}]})


def from_jsonable_error(tp, data) -> str:
    with pytest.raises(CodecError) as err:
        from_jsonable(tp, data)
    return err.value.code
