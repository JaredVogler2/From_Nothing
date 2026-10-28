"""Canonical JSON codec, typed record conversion and content hashing (S03 SERIALIZATION).

Canonical JSON is UTF-8, sorted keys, compact separators and ``allow_nan=False``.
Typed values are encoded reversibly:

* ``datetime`` -> RFC3339 UTC string with ``Z`` (microseconds preserved when non-zero);
  naive datetimes are rejected.
* ``date`` -> ISO ``YYYY-MM-DD``.
* ``Decimal`` -> string (finite only).
* ``Fraction`` -> ``{"num": n, "den": d}``.
* ``Enum`` -> its value.
* dataclass -> object with every declared field (unknown and empty are distinct:
  ``None`` is unknown, ``[]`` is empty).

Decoding is strict: duplicate keys, non-finite numbers, unknown fields and
missing required fields are rejected with stable error codes.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
import types
import typing
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

UTC = timezone.utc

SCHEMA_VERSION = 1


class CodecError(ValueError):
    """Raised for malformed, ambiguous or unsupported serialized content."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# primitive conversions
# ---------------------------------------------------------------------------

def format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CodecError("NAIVE_DATETIME", f"datetime {value!r} has no timezone")
    value = value.astimezone(UTC)
    if value.microsecond:
        text = value.strftime("%Y-%m-%dT%H:%M:%S.%f")
    else:
        text = value.strftime("%Y-%m-%dT%H:%M:%S")
    return text + "Z"


def parse_utc(text: str) -> datetime:
    if not isinstance(text, str):
        raise CodecError("BAD_DATETIME", f"expected RFC3339 string, got {type(text).__name__}")
    candidate = text.strip()
    if candidate != text or not candidate:
        raise CodecError("BAD_DATETIME", f"datetime {text!r} has surrounding whitespace or is empty")
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CodecError("BAD_DATETIME", f"invalid RFC3339 datetime {text!r}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise CodecError("NAIVE_DATETIME", f"datetime {text!r} has no offset")
    return value.astimezone(UTC)


def parse_iso_date(text: str) -> date:
    if not isinstance(text, str) or len(text) != 10:
        raise CodecError("BAD_DATE", f"expected ISO date string, got {text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CodecError("BAD_DATE", f"invalid ISO date {text!r}") from exc


def decimal_from_text(text: Any) -> Decimal:
    if isinstance(text, bool) or not isinstance(text, (str, int)):
        raise CodecError("BAD_DECIMAL", f"decimal must be encoded as a string, got {type(text).__name__}")
    try:
        value = Decimal(str(text))
    except InvalidOperation as exc:
        raise CodecError("BAD_DECIMAL", f"invalid decimal {text!r}") from exc
    if not value.is_finite():
        raise CodecError("NONFINITE", f"non-finite decimal {text!r}")
    return value


def fraction_to_json(value: Fraction) -> dict[str, int]:
    return {"num": value.numerator, "den": value.denominator}


def fraction_from_json(data: Any) -> Fraction:
    if not isinstance(data, dict) or set(data) != {"num", "den"}:
        raise CodecError("BAD_FRACTION", f"fraction must be {{num,den}}, got {data!r}")
    num, den = data["num"], data["den"]
    if isinstance(num, bool) or isinstance(den, bool) or not isinstance(num, int) or not isinstance(den, int):
        raise CodecError("BAD_FRACTION", "fraction parts must be integers")
    if den <= 0:
        raise CodecError("BAD_FRACTION", "fraction denominator must be positive")
    value = Fraction(num, den)
    if value.numerator != num or value.denominator != den:
        raise CodecError("BAD_FRACTION", "fraction must be in lowest terms")
    return value


# ---------------------------------------------------------------------------
# encode
# ---------------------------------------------------------------------------

def to_jsonable(value: Any) -> Any:
    """Convert typed domain values into JSON-native structures (deterministically)."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CodecError("NONFINITE", f"non-finite float {value!r}")
        return value
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value)
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CodecError("NONFINITE", f"non-finite decimal {value!r}")
        return str(value)
    if isinstance(value, Fraction):
        return fraction_to_json(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (dict, types.MappingProxyType)):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, enum.Enum):
                key = key.value
            if isinstance(key, bool) or not isinstance(key, (str, int)):
                raise CodecError("BAD_KEY", f"object keys must be strings, got {key!r}")
            skey = str(key)
            if skey in out:
                raise CodecError("DUPLICATE_KEY", f"duplicate key after conversion: {skey!r}")
            out[skey] = to_jsonable(item)
        return out
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [to_jsonable(item) for item in value]
        return sorted(items, key=lambda x: canonical_dumps(x))
    raise CodecError("UNSUPPORTED_TYPE", f"cannot serialize {type(value).__name__}")


def canonical_dumps(value: Any) -> bytes:
    """Canonical UTF-8 JSON bytes for hashing and storage."""
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any) -> str:
    return sha256_hex(canonical_dumps(value))


# ---------------------------------------------------------------------------
# strict decode
# ---------------------------------------------------------------------------

def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise CodecError("DUPLICATE_KEY", f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise CodecError("NONFINITE", f"non-finite JSON constant {name}")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise CodecError("NONFINITE", f"non-finite JSON number {text}")
    return value


def strict_loads(data: bytes | str) -> Any:
    if isinstance(data, (bytes, bytearray)):
        try:
            text = bytes(data).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CodecError("BAD_UTF8", "payload is not valid UTF-8") from exc
    else:
        text = data
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except CodecError:
        raise
    except (ValueError, RecursionError) as exc:
        raise CodecError("BAD_JSON", f"malformed JSON: {exc}") from exc


_HINT_CACHE: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    cached = _HINT_CACHE.get(cls)
    if cached is None:
        cached = get_type_hints(cls)
        _HINT_CACHE[cls] = cached
    return cached


def from_jsonable(tp: Any, data: Any, path: str = "$") -> Any:
    """Decode JSON-native ``data`` into type ``tp`` strictly."""
    if tp is Any or tp is object:
        return data
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = get_args(tp)
        if data is None:
            if type(None) in args:
                return None
            raise CodecError("MISSING_VALUE", f"{path}: null not allowed")
        errors = []
        for arg in args:
            if arg is type(None):
                continue
            try:
                return from_jsonable(arg, data, path)
            except CodecError as exc:
                errors.append(exc.code)
        raise CodecError("TYPE_MISMATCH", f"{path}: value does not match {tp} ({errors})")
    if origin is Literal:
        allowed = get_args(tp)
        if not any(type(data) is type(option) and data == option for option in allowed):
            raise CodecError("BAD_LITERAL", f"{path}: {data!r} not in {allowed}")
        return data
    if tp is type(None):
        if data is not None:
            raise CodecError("TYPE_MISMATCH", f"{path}: expected null")
        return None
    if data is None:
        raise CodecError("MISSING_VALUE", f"{path}: null not allowed for {tp}")
    if origin in (tuple,):
        args = get_args(tp)
        if not isinstance(data, list):
            raise CodecError("TYPE_MISMATCH", f"{path}: expected array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(from_jsonable(args[0], item, f"{path}[{i}]") for i, item in enumerate(data))
        if len(args) != len(data):
            raise CodecError("TYPE_MISMATCH", f"{path}: expected {len(args)} items")
        return tuple(from_jsonable(a, item, f"{path}[{i}]") for i, (a, item) in enumerate(zip(args, data)))
    if origin in (list,):
        (arg,) = get_args(tp) or (Any,)
        if not isinstance(data, list):
            raise CodecError("TYPE_MISMATCH", f"{path}: expected array")
        return [from_jsonable(arg, item, f"{path}[{i}]") for i, item in enumerate(data)]
    if origin in (dict, typing.Mapping, types.MappingProxyType) or tp in (dict,):
        args = get_args(tp) or (str, Any)
        key_tp, val_tp = args
        if not isinstance(data, dict):
            raise CodecError("TYPE_MISMATCH", f"{path}: expected object")
        out = {}
        for key, item in data.items():
            if key_tp is int:
                try:
                    real_key: Any = int(key)
                except ValueError as exc:
                    raise CodecError("BAD_KEY", f"{path}: key {key!r} is not an integer") from exc
                if str(real_key) != key:
                    raise CodecError("BAD_KEY", f"{path}: non-canonical integer key {key!r}")
            elif isinstance(key_tp, type) and issubclass(key_tp, enum.Enum):
                real_key = key_tp(key)
            else:
                real_key = key
            out[real_key] = from_jsonable(val_tp, item, f"{path}.{key}")
        return out
    if isinstance(tp, type):
        if issubclass(tp, enum.Enum):
            try:
                return tp(data)
            except ValueError as exc:
                raise CodecError("BAD_ENUM", f"{path}: {data!r} is not a valid {tp.__name__}") from exc
        if dataclasses.is_dataclass(tp):
            return _decode_dataclass(tp, data, path)
        if tp is bool:
            if not isinstance(data, bool):
                raise CodecError("TYPE_MISMATCH", f"{path}: expected boolean")
            return data
        if tp is int:
            if isinstance(data, bool) or not isinstance(data, int):
                raise CodecError("TYPE_MISMATCH", f"{path}: expected integer")
            return data
        if tp is float:
            if isinstance(data, bool) or not isinstance(data, (int, float)):
                raise CodecError("TYPE_MISMATCH", f"{path}: expected number")
            if not math.isfinite(float(data)):
                raise CodecError("NONFINITE", f"{path}: non-finite number")
            return float(data)
        if tp is str:
            if not isinstance(data, str):
                raise CodecError("TYPE_MISMATCH", f"{path}: expected string")
            return data
        if tp is datetime:
            return parse_utc(data)
        if tp is date:
            return parse_iso_date(data)
        if tp is Decimal:
            return decimal_from_text(data)
        if tp is Fraction:
            return fraction_from_json(data)
    raise CodecError("UNSUPPORTED_TYPE", f"{path}: cannot decode type {tp!r}")


def _decode_dataclass(cls: type, data: Any, path: str) -> Any:
    if not isinstance(data, dict):
        raise CodecError("TYPE_MISMATCH", f"{path}: expected object for {cls.__name__}")
    hints = _hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls) if f.init}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise CodecError("UNKNOWN_FIELD", f"{path}: unknown field(s) {unknown} for {cls.__name__}")
    kwargs = {}
    for name, field_def in fields.items():
        if name not in data:
            if field_def.default is not dataclasses.MISSING or field_def.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                continue
            raise CodecError("MISSING_FIELD", f"{path}: missing field {name!r} for {cls.__name__}")
        kwargs[name] = from_jsonable(hints[name], data[name], f"{path}.{name}")
    try:
        return cls(**kwargs)
    except CodecError:
        raise
    except (ValueError, TypeError) as exc:
        code = getattr(exc, "code", "INVALID_RECORD")
        raise CodecError(code, f"{path}: {exc}") from exc


def decode(cls: type, payload: bytes | str) -> Any:
    return from_jsonable(cls, strict_loads(payload))


# ---------------------------------------------------------------------------
# envelopes
# ---------------------------------------------------------------------------

ENVELOPE_FIELDS = (
    "schema_version",
    "type",
    "id",
    "source_run_id",
    "policy_hash",
    "purpose",
    "created_at_utc",
    "payload",
    "content_sha256",
)


@dataclasses.dataclass(frozen=True, slots=True)
class Envelope:
    schema_version: int
    type: str
    id: str
    source_run_id: str | None
    policy_hash: str | None
    purpose: str | None
    created_at_utc: datetime
    payload: Any
    content_sha256: str

    def to_bytes(self) -> bytes:
        return canonical_dumps(self)


def _envelope_hash(fields: dict[str, Any]) -> str:
    body = {k: v for k, v in fields.items() if k != "content_sha256"}
    return content_hash(body)


def seal(
    type_name: str,
    record_id: str,
    payload: Any,
    *,
    created_at: datetime,
    source_run_id: str | None = None,
    policy_hash: str | None = None,
    purpose: str | None = None,
) -> Envelope:
    if not type_name or not record_id:
        raise CodecError("BAD_ENVELOPE", "envelope type and id are required")
    fields = {
        "schema_version": SCHEMA_VERSION,
        "type": type_name,
        "id": record_id,
        "source_run_id": source_run_id,
        "policy_hash": policy_hash,
        "purpose": purpose,
        "created_at_utc": format_utc(created_at),
        "payload": to_jsonable(payload),
    }
    digest = _envelope_hash(fields)
    return Envelope(
        schema_version=SCHEMA_VERSION,
        type=type_name,
        id=record_id,
        source_run_id=source_run_id,
        policy_hash=policy_hash,
        purpose=purpose,
        created_at_utc=parse_utc(fields["created_at_utc"]),
        payload=fields["payload"],
        content_sha256=digest,
    )


def open_envelope(raw: bytes | str, expected_type: str | None = None) -> Envelope:
    data = strict_loads(raw)
    if not isinstance(data, dict):
        raise CodecError("BAD_ENVELOPE", "envelope must be a JSON object")
    keys = set(data)
    missing = sorted(set(ENVELOPE_FIELDS) - keys)
    extra = sorted(keys - set(ENVELOPE_FIELDS))
    if missing or extra:
        raise CodecError("BAD_ENVELOPE", f"envelope fields mismatch missing={missing} extra={extra}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise CodecError("SCHEMA_VERSION", f"unsupported schema_version {data['schema_version']!r}")
    if expected_type is not None and data["type"] != expected_type:
        raise CodecError("WRONG_TYPE", f"expected envelope type {expected_type!r}, got {data['type']!r}")
    expected = _envelope_hash(data)
    if expected != data["content_sha256"]:
        raise CodecError("HASH_MISMATCH", f"envelope {data.get('id')!r} content hash mismatch")
    return Envelope(
        schema_version=data["schema_version"],
        type=data["type"],
        id=data["id"],
        source_run_id=data["source_run_id"],
        policy_hash=data["policy_hash"],
        purpose=data["purpose"],
        created_at_utc=parse_utc(data["created_at_utc"]),
        payload=data["payload"],
        content_sha256=data["content_sha256"],
    )


def open_typed(raw: bytes | str, expected_type: str, cls: type) -> tuple[Envelope, Any]:
    env = open_envelope(raw, expected_type)
    return env, from_jsonable(cls, env.payload, f"$.payload<{expected_type}>")
