"""The native canonical encoder writes exactly the Python codec's bytes (``focus/codec.py``, ``focus_native.Canon``).

Canonical JSON is what every record hash, snapshot seal, plan id and digest id is computed from, so the native path must
be byte-identical: on every value type the codec encodes, on random nested values, on a real snapshot and plan, and on
every error, which must come out as the same exception with the same message.
"""
from __future__ import annotations

import collections
import dataclasses
import enum
import random
import types
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from focus import codec
from focus.domain import digest_id

native = pytest.importorskip("focus_native")
if not hasattr(native, "Canon"):  # pragma: no cover - an older build of the extension
    pytest.skip("focus_native has no Canon", allow_module_level=True)


class Color(str, enum.Enum):
    RED = "red"
    BLUE = "blue"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 20


class Shape(enum.Enum):
    BOX = ("box", 3)
    WHEN = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)


class Precise(float):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class Leaf:
    b: int
    a: str
    Z: tuple = ()
    _x: Color | None = None


@dataclasses.dataclass(frozen=True)
class Node:
    name: str
    at: datetime
    day: date
    leaves: tuple[Leaf, ...]
    extra: dict
    tags: frozenset
    cost: Decimal | None = None
    share: Fraction | None = None


Point = collections.namedtuple("Point", "x y")
IST = timezone(timedelta(hours=5, minutes=30))
SCALARS = [None, True, False, 0, -1, 7, 2 ** 63 - 1, -(2 ** 63), 2 ** 70, -(2 ** 90), 1.0, -0.0, 0.1, 1e16, 1.5e-7, 123456789.125,
           1e300, 5e-324, Precise(2.5), "", "plain", "quote \" back \\ slash", "\n\r\t\b\f", "\x00\x01\x1f\x7f", "é 中 😀    ",
           Color.RED, Level.HIGH, Shape.BOX, Shape.WHEN, datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc),
           datetime(2026, 10, 26, 5, 0, 0, 1500, tzinfo=timezone.utc), datetime(2026, 10, 26, 10, 30, tzinfo=IST),
           datetime(999, 1, 2, 3, 4, 5, tzinfo=timezone.utc), date(2026, 10, 26), date(5, 1, 1), Decimal("1.50"), Decimal("-0"),
           Decimal("1E+3"), Fraction(3, 4), Fraction(-7, 2)]


def python_dumps(value):  # type: ignore[no-untyped-def]
    saved, codec._CANON = codec._CANON, None
    try:
        return codec.canonical_dumps(value)
    finally:
        codec._CANON = saved


def python_digest(prefix, *parts, length=20):  # type: ignore[no-untyped-def]
    saved, codec._CANON = codec._CANON, None
    try:
        return digest_id(prefix, *parts, length=length)
    finally:
        codec._CANON = saved


@pytest.fixture()
def canon():  # type: ignore[no-untyped-def]
    assert codec.enable_native_codec(True)
    yield codec.native_codec()
    codec.enable_native_codec(False)


def _random_value(rng: random.Random, depth: int = 0):  # type: ignore[no-untyped-def]
    pick = rng.random()
    if depth > 4 or pick < 0.35:
        return rng.choice(SCALARS)
    if pick < 0.5:
        return [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 5))]
    if pick < 0.6:
        return tuple(_random_value(rng, depth + 1) for _ in range(rng.randint(0, 4)))
    if pick < 0.75:
        keys = rng.sample(["a", "B", "é", "10", "9", "_", "zz", "a b", "\x01", "😀"], rng.randint(0, 6))
        if rng.random() < 0.3:
            keys = [k for k in keys if not k.isdigit()] + [rng.randint(-5, 50)]
        return {k: _random_value(rng, depth + 1) for k in keys}
    if pick < 0.8:
        return frozenset(rng.choice(["x", "y", 3, 4, ("t", 1), Color.BLUE, None]) for _ in range(rng.randint(0, 5)))
    if pick < 0.85:
        return types.MappingProxyType({"k": _random_value(rng, depth + 1), "a": 1})
    if pick < 0.9:
        return Point(_random_value(rng, depth + 1), _random_value(rng, depth + 1))
    leaves = tuple(Leaf(rng.randint(0, 9), rng.choice(["p", "q\n"]), (rng.random(),), rng.choice([None, Color.RED])) for _ in range(2))
    return Node("n", datetime(2026, 10, 26, 5, rng.randint(0, 59), tzinfo=timezone.utc), date(2026, 11, rng.randint(1, 30)), leaves,
                {"x": _random_value(rng, depth + 1), 3: [1, 2]}, frozenset({"b", "a"}), Decimal("2.25"), Fraction(1, 3))


def test_every_value_type_encodes_to_the_same_bytes(canon) -> None:  # type: ignore[no-untyped-def]
    for value in SCALARS + [list(SCALARS), tuple(SCALARS), {"b": 1, "a": [2, {"d": None, "c": True}]}, {10: "x", 9: "y"},
                            {"10": 1, "9": 2, "": 3}, collections.OrderedDict([("z", 1), ("a", 2)]), set(), {3, 1, 2},
                            {"x", ("t", 1), 2}, Point(1, "a"), Leaf(1, "a"), Leaf(2, "b", (Leaf(3, "c"),), Color.BLUE)]:
        assert canon.dumps(value) == python_dumps(value), value
        assert codec.canonical_dumps(value) == python_dumps(value)
        assert codec.content_hash(value) == codec.sha256_hex(python_dumps(value))


def test_random_nested_values_encode_to_the_same_bytes(canon) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(20261030)
    for _ in range(3000):
        value = _random_value(rng)
        assert codec.canonical_dumps(value) == python_dumps(value), value
    big = [_random_value(rng) for _ in range(20000)]                     # streamed hash across many buffer flushes
    assert codec.streamed_content_hash({"items": big, "n": 1}) == codec.sha256_hex(python_dumps({"items": big, "n": 1}))


def test_digest_ids_are_the_same(canon) -> None:  # type: ignore[no-untyped-def]
    for parts in [("P", 1), (), ("a", None, 3, Color.RED), (datetime(2026, 10, 26, tzinfo=timezone.utc), Leaf(1, "x")), (["nested", {"k": 1}],)]:
        for length in (0, 8, 20, 64, 100):
            assert digest_id("ID", *parts, length=length) == python_digest("ID", *parts, length=length)


def test_errors_are_the_same_exceptions(canon) -> None:  # type: ignore[no-untyped-def]
    bad = [datetime(2026, 10, 26, 5, 0), float("nan"), float("inf"), [1, float("-inf")], {True: 1}, {1: "a", "1": "b"}, {(1, 2): 3},
           object(), "\ud800", {"k": datetime(2026, 1, 1)}, Leaf(1, datetime(2026, 1, 1)), Decimal("NaN"), {None: 1}]
    for value in bad:
        with pytest.raises(Exception) as native_error:
            codec.canonical_dumps(value)
        saved, codec._CANON = codec._CANON, None
        try:
            with pytest.raises(Exception) as python_error:
                codec.canonical_dumps(value)
        finally:
            codec._CANON = saved
        assert type(native_error.value) is type(python_error.value) and str(native_error.value) == str(python_error.value), value


def test_a_real_snapshot_and_plan_hash_the_same(demo, canon) -> None:  # type: ignore[no-untyped-def]
    from focus.contracts.planning import record_hash
    from focus.domain import Purpose
    from focus.engine.max.optimizer.orchestrator import schedule_snapshot

    services, _, snapshot = demo
    plan, _ = schedule_snapshot(services, snapshot, purpose=Purpose.FORECAST, budget_seconds=10, max_moves=2)
    for record in (snapshot, plan):
        content = {f.name: getattr(record, f.name) for f in dataclasses.fields(record) if f.name not in ("hash",)}
        assert codec.streamed_content_hash(content) == codec.sha256_hex(python_dumps(content))
    native_hashes = (record_hash(snapshot), record_hash(plan))
    codec.enable_native_codec(False)
    assert native_hashes == (record_hash(snapshot), record_hash(plan)) == (snapshot.hash, plan.hash)


@dataclasses.dataclass(frozen=True)
class Ref:
    name: str
    source_run_id: str
    evidence: tuple = ()


def test_decision_digests_drop_the_same_keys(canon, demo) -> None:  # type: ignore[no-untyped-def]
    from focus.domain import Purpose
    from focus.engine.max.optimizer.orchestrator import RUN_REFERENCES, decision_digest, schedule_snapshot

    def python_strip(value):  # type: ignore[no-untyped-def]
        if isinstance(value, dict):
            return {k: python_strip(v) for k, v in value.items() if k not in RUN_REFERENCES}
        if isinstance(value, list):
            return [python_strip(v) for v in value]
        return value

    rng = random.Random(7)
    values = [{"a": Ref("x", "SR-1"), "evidence": [1], "items": [Ref("y", "SR-2", ("e",)), {"snapshot_id": "S", "k": 1}]},
              frozenset({Ref("b", "SR-9"), Ref("a", "SR-1"), Ref("a", "SR-0")}),                # set order uses the full encoding
              [_random_value(rng) for _ in range(200)]]
    for value in values:
        python = python_digest("DEC", python_strip(codec.to_jsonable(value)), length=64)
        assert canon.digest_excluding("DEC", value, RUN_REFERENCES, 64) == python
    services, _, snapshot = demo
    plan, _ = schedule_snapshot(services, snapshot, purpose=Purpose.FORECAST, budget_seconds=10, max_moves=2)
    native_digest = decision_digest(plan)
    codec.enable_native_codec(False)
    assert decision_digest(plan) == native_digest
