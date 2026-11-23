"""The typed decoder's short paths give exactly what its full rules give (DEC-36, option (i)).

``focus.codec.from_jsonable`` takes a short path through well-formed data: an object with exactly its class's fields, a
string for a string field, a known enum value, a timestamp text seen before. ``tests/support/reference_codec.py`` is the
decoder as it stood before those paths existed. Both decode the same data here, valid and damaged, and must return the
same value or raise the same error, with the same code and the same message.
"""
from __future__ import annotations

import copy
import dataclasses
import enum
import random
from dataclasses import InitVar, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Any, Literal

import pytest

from focus import codec
from focus.codec import CodecError, from_jsonable, to_jsonable
from tests.support import reference_codec


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Level(enum.Enum):
    LOW = 1
    HIGH = 2


@dataclass(frozen=True, slots=True)
class Leaf:
    name: str
    count: int
    flag: bool
    note: str | None = None
    size: int | None = None
    on: bool | None = None


@dataclass(frozen=True)
class Stamp:
    at: datetime
    day: date
    color: Color
    level: Level
    ratio: float
    amount: Decimal
    share: Fraction
    until: datetime | None = None
    mode: Literal["a", "b"] = "a"


@dataclass(frozen=True, slots=True)
class Checked:
    low: int
    high: int

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError("low is above high")


@dataclass(frozen=True, slots=True)
class Node:
    key: Leaf
    leaves: tuple[Leaf, ...]
    tags: tuple[str, ...]
    counts: tuple[int, ...]
    flags: tuple[bool, ...]
    stamp: Stamp | None
    either: int | str
    extra: dict[str, int]
    by_number: dict[int, str]
    pair: tuple[int, str]
    span: Checked
    colors: tuple[Color, ...] = ()
    items: list[Leaf] = field(default_factory=list)
    anything: Any = None


@dataclass(kw_only=True)
class KeywordOnly:
    a: int
    b: str


@dataclass
class One:
    only: str


@dataclass
class WithInitVar:
    a: int
    scale: InitVar[int] = 1
    b: int = 0

    def __post_init__(self, scale: int) -> None:
        self.a *= scale


@dataclass(init=False)
class OwnInit:
    a: int
    b: int

    def __init__(self, *, a: int, b: int) -> None:
        self.a, self.b = a, b * 2


class Words(str):
    pass


class Table(dict):
    pass


class Rows(list):
    pass


T0 = datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc)


def _leaf(rng: random.Random) -> Leaf:
    return Leaf(rng.choice(["a", "b", "", "long name"]), rng.randint(-3, 9), rng.random() < 0.5,
                rng.choice([None, "n"]), rng.choice([None, 0, 7]), rng.choice([None, True, False]))


def _stamp(rng: random.Random) -> Stamp:
    at = T0 + timedelta(minutes=rng.randint(0, 5) * 30, microseconds=rng.choice([0, 0, 250]))
    return Stamp(at, at.date(), rng.choice(list(Color)), rng.choice(list(Level)), rng.choice([0.0, 1.5, -2.25]),
                 Decimal(rng.choice(["0", "1.50", "-3"])), Fraction(rng.randint(1, 5), rng.choice([1, 7])),
                 rng.choice([None, at]), rng.choice(["a", "b"]))


def _node(rng: random.Random) -> Node:
    low = rng.randint(0, 5)
    return Node(_leaf(rng), tuple(_leaf(rng) for _ in range(rng.randint(0, 3))), tuple(rng.choice("xyz") for _ in range(rng.randint(0, 4))),
                tuple(rng.randint(0, 9) for _ in range(rng.randint(0, 4))), tuple(rng.random() < 0.5 for _ in range(rng.randint(0, 3))),
                rng.choice([None, _stamp(rng)]), rng.choice([3, "three"]), {k: rng.randint(0, 3) for k in rng.sample("abcd", rng.randint(0, 3))},
                {n: str(n) for n in rng.sample(range(20), rng.randint(0, 3))}, (rng.randint(0, 9), rng.choice("pq")),
                Checked(low, low + rng.randint(0, 3)), tuple(rng.choice(list(Color)) for _ in range(rng.randint(0, 2))),
                [_leaf(rng) for _ in range(rng.randint(0, 2))], rng.choice([None, 1, "s", [1, {"k": None}]]))


WRONG = (None, True, False, 0, 1, -1, 2.5, "", "x", "red", "2026-11-01", "2026-11-01T05:00:00Z", "not a time", [], [1], ["x"], {}, {"k": 1},
         {"num": 1, "den": 2}, Words("w"), Table(), Rows(), 10 ** 30, float("inf"))


def _places(value: Any, path: tuple = ()) -> list[tuple]:
    """Every position in a JSON value, as the keys and indexes that lead to it."""
    out = [path]
    if isinstance(value, dict):
        for key, item in value.items():
            out.extend(_places(item, path + (key,)))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.extend(_places(item, path + (i,)))
    return out


def _damage(rng: random.Random, payload: Any) -> Any:
    """One change at one random position: a value of another kind, a field removed, a field nobody declared, or the
    same content in a subclass of ``dict``, ``list`` or ``str``."""
    damaged = copy.deepcopy(payload)
    place = rng.choice(_places(damaged))
    if not place:
        return rng.choice(WRONG)
    parent = damaged
    for step in place[:-1]:
        parent = parent[step]
    last = place[-1]
    kind = rng.choice(("wrong", "wrong", "wrong", "remove", "unknown", "subclass"))
    if kind == "remove" and isinstance(parent, dict):
        del parent[last]
    elif kind == "unknown" and isinstance(parent[last], dict):
        parent[last][rng.choice(["zzz", "aaa", "name "])] = rng.choice(WRONG)
    elif kind == "subclass":
        value = parent[last]
        parent[last] = Table(value) if type(value) is dict else Rows(value) if type(value) is list else Words(value) if type(value) is str else value
    else:
        parent[last] = rng.choice(WRONG)
    return damaged


def _outcome(decode: Any, tp: Any, payload: Any) -> tuple:
    try:
        value = decode(tp, copy.deepcopy(payload))
    except CodecError as exc:
        return "error", exc.code, str(exc)
    except Exception as exc:  # noqa: BLE001 - whatever else the rules let through must be let through alike
        return "raised", type(exc).__name__, str(exc)
    return "value", _typed(value)


def _typed(value: Any) -> Any:
    """A value with the exact type of every part, so ``1``, ``True`` and ``1.0`` are told apart."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return type(value).__name__, tuple((f.name, _typed(getattr(value, f.name))) for f in dataclasses.fields(value))
    if isinstance(value, (tuple, list)):
        return type(value).__name__, tuple(_typed(v) for v in value)
    if isinstance(value, dict):
        return type(value).__name__, tuple((_typed(k), _typed(v)) for k, v in value.items())
    return type(value).__name__, repr(value)


def test_valid_and_damaged_data_decode_exactly_as_the_full_rules_decode_them() -> None:
    rng = random.Random(20261112)
    counts = {"value": 0, "error": 0, "raised": 0}
    for _ in range(1500):
        obj = _node(rng)
        payload = to_jsonable(obj)
        assert from_jsonable(Node, payload) == obj                          # the round trip, by the short path
        assert _outcome(from_jsonable, Node, payload) == _outcome(reference_codec.from_jsonable, Node, payload)
        for _ in range(4):
            damaged = _damage(rng, payload)
            now, before = _outcome(from_jsonable, Node, damaged), _outcome(reference_codec.from_jsonable, Node, damaged)
            assert now == before, damaged
            counts[now[0]] += 1
    assert counts["error"] > 2500 and counts["value"] > 300, counts         # both kinds of outcome were really exercised


@pytest.mark.parametrize("tp, payload", [
    (KeywordOnly, {"a": 1, "b": "x"}), (KeywordOnly, {"b": "x", "a": True}), (One, {"only": "x"}), (One, {"only": 1}), (One, {}),
    (WithInitVar, {"a": 2, "b": 3}), (WithInitVar, {"a": 2, "scale": 5, "b": 3}), (WithInitVar, {"a": 2}),
    (OwnInit, {"a": 1, "b": 2}), (OwnInit, {"a": 1}), (Checked, {"low": 3, "high": 1}), (Checked, {"high": 1, "low": 0}),
    (Checked, {"low": 0, "high": 1, "more": 2}), (Checked, {"low": 0}), (Checked, None), (Checked, [0, 1]), (Checked, Table(low=0, high=1)),
    (Leaf, {"name": "n", "count": 1, "flag": True}), (Leaf, {"name": Words("n"), "count": 1, "flag": True, "note": None, "size": None, "on": None}),
    (Leaf, {"name": "n", "count": True, "flag": True, "note": None, "size": None, "on": None}),
    (Leaf, {"name": "n", "count": 1, "flag": 1, "note": None, "size": None, "on": None}),
    (Leaf, {"name": "n", "count": 1, "flag": True, "note": 5, "size": "big", "on": None}),
    (Leaf, {"name": "n", "count": 1, "flag": True, "note": None, "size": None, "wrong": None}),
    (tuple[str, ...], ["a", "b"]), (tuple[str, ...], ["a", 1]), (tuple[str, ...], Rows(["a"])), (tuple[str, ...], None), (tuple[str, ...], "ab"),
    (tuple[int, ...], [1, True]), (tuple[bool, ...], [True, 0]), (tuple[Leaf, ...], [{"name": "n"}]),
    (Color, "red"), (Color, "green"), (Color, None), (Color, ["red"]), (Color, {"k": 1}), (Level, 1), (Level, True), (Level, 1.0), (Level, "1"),
    (datetime, "2026-11-01T05:00:00Z"), (datetime, "2026-11-01T00:00:00-05:00"), (datetime, "2026-11-01T05:00:00"), (datetime, " 2026-11-01T05:00:00Z"),
    (datetime, 5), (datetime, None), (datetime, Words("2026-11-01T05:00:00Z")), (date, "2026-11-01"), (date, "2026-11-31"), (date, None), (date, 20261101),
    (str | None, None), (str | None, "x"), (str | None, 5), (Leaf | None, {"name": 1}), (datetime | None, "nope"), (int | str, True), (int | str, 2.5),
])
def test_each_rule_at_the_edge_of_a_short_path(tp: Any, payload: Any) -> None:
    assert _outcome(from_jsonable, tp, payload) == _outcome(reference_codec.from_jsonable, tp, payload)


def test_the_first_error_in_field_order_is_the_one_raised() -> None:
    """The short path checks the fields that decode to themselves before it decodes the others. A later plain field
    that is wrong must still not hide an earlier nested field's error: the full rules decide then."""
    good = to_jsonable(_node(random.Random(1)))
    early_nested = copy.deepcopy(good)
    early_nested["key"]["count"] = "many"                  # field 1, nested
    early_nested["either"] = None                          # a later field
    with pytest.raises(CodecError) as caught:
        from_jsonable(Node, early_nested)
    assert caught.value.code == "TYPE_MISMATCH" and "$.key.count" in str(caught.value)
    both = {"name": 5, "count": "x", "flag": True, "note": None, "size": None, "on": None}
    with pytest.raises(CodecError) as caught:
        from_jsonable(Leaf, both)
    assert "$.name" in str(caught.value)                   # not $.count


def test_a_timestamp_text_is_parsed_once_and_an_invalid_one_is_refused_every_time() -> None:
    codec._parse_utc_text.cache_clear()
    first = from_jsonable(datetime, "2026-11-02T13:30:00Z")
    again = from_jsonable(datetime, "2026-11-02T13:30:00Z")
    assert first == again == datetime(2026, 11, 2, 13, 30, tzinfo=timezone.utc) and first.tzinfo is timezone.utc
    info = codec._parse_utc_text.cache_info()
    assert (info.misses, info.hits) == (1, 1)
    for _ in range(2):
        with pytest.raises(CodecError) as caught:
            from_jsonable(datetime, "2026-13-02T13:30:00Z")
        assert caught.value.code == "BAD_DATETIME"
    assert from_jsonable(datetime, "2026-11-02T08:30:00-05:00") == first        # another text of the same instant


def test_the_short_path_is_taken_only_by_classes_with_a_plain_generated_init() -> None:
    names = lambda cls: tuple(f.name for f in dataclasses.fields(cls) if f.init)       # noqa: E731
    assert codec._positional_init(Leaf, names(Leaf)) and codec._positional_init(Node, names(Node))
    for cls in (KeywordOnly, WithInitVar, OwnInit):
        assert not codec._positional_init(cls, names(cls)), cls
    assert codec._scalar_of(str) == (str, False) and codec._scalar_of(int | None) == (int, True) and codec._scalar_of(bool | None) == (bool, True)
    for tp in (float, float | None, datetime, Color, tuple[str, ...], int | str, int | str | None, Any):
        assert codec._scalar_of(tp) == (None, False), tp
