"""P02: strict identity/UTC/finite-value parsing and canonical hash round trips (A04 identity part)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from focus.codec import CodecError, canonical_dumps, decode, from_jsonable, open_envelope, open_typed, seal, strict_loads, to_jsonable
from focus.domain import (
    DomainError,
    Interval,
    JobKey,
    PersonKey,
    PhaseKey,
    PhaseKind,
    PoolKey,
    RequirementBundle,
    ensure_unique_ids,
    from_minute,
    minute_ceil,
    minute_floor,
    to_minute,
)

UTC = timezone.utc


def test_canonical_json_is_sorted_compact_and_utf8() -> None:
    data = {"b": 1, "a": "é", "c": [Decimal("1.50"), Fraction(1, 3), date(2026, 10, 26)]}
    assert canonical_dumps(data) == '{"a":"é","b":1,"c":["1.50",{"den":3,"num":1},"2026-10-26"]}'.encode("utf-8")


def test_strict_loads_rejects_duplicate_keys_and_nonfinite() -> None:
    with pytest.raises(CodecError) as exc:
        strict_loads('{"a":1,"a":2}')
    assert exc.value.code == "DUPLICATE_KEY"
    for bad in ('{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}', '{"x": 1e999}'):
        with pytest.raises(CodecError) as exc:
            strict_loads(bad)
        assert exc.value.code == "NONFINITE"


def test_naive_datetime_rejected_everywhere() -> None:
    naive = datetime(2026, 10, 26, 10, 0)
    with pytest.raises(CodecError):
        to_jsonable(naive)
    with pytest.raises(DomainError) as exc:
        Interval(naive, naive + timedelta(minutes=5))
    assert exc.value.code == "NAIVE_DATETIME"
    with pytest.raises(CodecError) as exc2:
        from_jsonable(datetime, "2026-10-26T10:00:00")
    assert exc2.value.code == "NAIVE_DATETIME"


def test_utc_round_trip_preserves_precision() -> None:
    aware = datetime(2026, 10, 26, 6, 0, 0, 123456, tzinfo=timezone(timedelta(hours=-4)))
    text = to_jsonable(aware)
    assert text == "2026-10-26T10:00:00.123456Z"
    assert from_jsonable(datetime, text) == aware


def test_interval_half_open_and_positive() -> None:
    start = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    iv = Interval(start, start + timedelta(minutes=45))
    assert iv.minutes == 45
    with pytest.raises(DomainError) as exc:
        Interval(start, start)
    assert exc.value.code == "EMPTY_INTERVAL"


def test_minute_helpers_exact_and_ceil() -> None:
    t = datetime(2026, 10, 27, 18, 15, 30, tzinfo=UTC)
    assert minute_ceil(t) == minute_floor(t) + 1
    with pytest.raises(DomainError):
        to_minute(t)
    m = to_minute(datetime(2026, 10, 27, 18, 15, tzinfo=UTC))
    assert from_minute(m) == datetime(2026, 10, 27, 18, 15, tzinfo=UTC)


def test_same_soi_on_two_lines_and_revisions_never_collide() -> None:
    a = JobKey("DEMO", "787", 1301, "FGI")
    b = JobKey("DEMO", "787", 1302, "FGI")
    c = JobKey("DEMO", "787", 1301, "FGI", revision="B")
    d = JobKey("DEMO", "787", 1301, "FGI", revision="")   # empty revision is not "no revision"
    ids = {a.id, b.id, c.id, d.id}
    assert len(ids) == 4
    assert ensure_unique_ids([a, b, c, d], "job").keys() == ids
    # native text is preserved exactly: no strip/casefold
    e = JobKey("DEMO", "787", 1301, "FGI ")
    f = JobKey("DEMO", "787", 1301, "fgi")
    assert len({a.id, e.id, f.id}) == 3
    assert e.native_id == "FGI "


def test_key_validation_rejects_lossy_inputs() -> None:
    with pytest.raises(DomainError):
        JobKey("DEMO", "787", 0, "X")
    with pytest.raises(DomainError):
        JobKey("DEMO", "787", True, "X")  # type: ignore[arg-type]
    with pytest.raises(DomainError):
        JobKey("DEMO", "787", 1301, "")
    with pytest.raises(DomainError):
        PoolKey("DEMO", "PROD-A", date(2026, 10, 26), 4, "run")
    with pytest.raises(DomainError):
        RequirementBundle(groups=(("A", "A"),), role="mechanic", count=1, applicability="job")
    with pytest.raises(DomainError):
        RequirementBundle(groups=(("A",),), role="mechanic", count=0, applicability="job")


def test_phase_and_person_keys_round_trip() -> None:
    job = JobKey("DEMO", "787", 1303, "ROOT3")
    phase = PhaseKey(job, PhaseKind.IP_QA, 2)
    restored = decode(PhaseKey, canonical_dumps(phase))
    assert restored == phase and restored.id == phase.id
    person = PersonKey("DEMO-A1-05", "fixture-demo")
    assert decode(PersonKey, canonical_dumps(person)).id == person.id


@dataclass(frozen=True)
class _Rec:
    amount: Fraction
    rate: Decimal
    when: datetime
    tags: tuple[str, ...]
    note: str | None


def test_typed_decode_rejects_unknown_and_missing_fields() -> None:
    rec = _Rec(Fraction(5, 2), Decimal("10.25"), datetime(2026, 10, 26, tzinfo=UTC), ("x",), None)
    raw = canonical_dumps(rec)
    assert decode(_Rec, raw) == rec
    data = json.loads(raw)
    data["extra"] = 1
    with pytest.raises(CodecError) as exc:
        from_jsonable(_Rec, data)
    assert exc.value.code == "UNKNOWN_FIELD"
    data = json.loads(raw)
    del data["note"]
    with pytest.raises(CodecError) as exc:
        from_jsonable(_Rec, data)
    assert exc.value.code == "MISSING_FIELD"
    data = json.loads(raw)
    data["amount"] = {"num": 10, "den": 4}
    with pytest.raises(CodecError) as exc:
        from_jsonable(_Rec, data)
    assert exc.value.code == "BAD_FRACTION"


def test_unknown_and_empty_are_distinguishable() -> None:
    @dataclass(frozen=True)
    class Holder:
        values: tuple[int, ...] | None

    assert canonical_dumps(Holder(None)) != canonical_dumps(Holder(()))
    assert decode(Holder, canonical_dumps(Holder(()))).values == ()
    assert decode(Holder, canonical_dumps(Holder(None))).values is None


def test_envelope_hash_detects_tampering() -> None:
    env = seal("Example", "EX-1", {"crew": ["P1", "P2"]}, created_at=datetime(2026, 10, 26, 10, tzinfo=UTC), source_run_id="R1")
    raw = env.to_bytes()
    assert open_envelope(raw, "Example").payload == {"crew": ["P1", "P2"]}
    tampered = raw.replace(b"P2", b"P3")
    with pytest.raises(CodecError) as exc:
        open_envelope(tampered)
    assert exc.value.code == "HASH_MISMATCH"
    with pytest.raises(CodecError) as exc:
        open_envelope(raw, "Other")
    assert exc.value.code == "WRONG_TYPE"
    # metadata is covered too
    meta_tampered = raw.replace(b'"source_run_id":"R1"', b'"source_run_id":"R2"')
    with pytest.raises(CodecError):
        open_envelope(meta_tampered)


def test_open_typed_decodes_payload_strictly() -> None:
    env = seal("JobKey", "J", JobKey("DEMO", "787", 1301, "A1"), created_at=datetime(2026, 10, 26, tzinfo=UTC))
    _, key = open_typed(env.to_bytes(), "JobKey", JobKey)
    assert key == JobKey("DEMO", "787", 1301, "A1")
