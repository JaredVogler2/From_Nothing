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
import functools
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
    return _format_utc(value)


@functools.lru_cache(maxsize=1 << 17)
def _format_utc(value: datetime) -> str:
    """RFC3339 UTC text of an aware datetime. Equal instants give equal text, so caching by value is exact."""
    if value.tzinfo is not UTC:
        value = value.astimezone(UTC)
    if value.year < 1000:                                   # strftime's own padding rules for unusual years
        text = value.strftime("%Y-%m-%dT%H:%M:%S.%f" if value.microsecond else "%Y-%m-%dT%H:%M:%S")
        return text + "Z"
    if value.microsecond:
        return (f"{value.year:04d}-{value.month:02d}-{value.day:02d}T{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
                f".{value.microsecond:06d}Z")
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}T{value.hour:02d}:{value.minute:02d}:{value.second:02d}Z"


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
    """Convert typed domain values into JSON-native structures (deterministically).

    Each type is resolved once to its converter (``_encoder_for``, which follows the same order of checks as the rules
    above, subclasses included) and cached; a dataclass's field names are read once per class.
    """
    tp = type(value)
    enc = _ENCODERS.get(tp)
    if enc is None:
        enc = _encoder_for(tp)
    return enc(value)


_NATIVE = frozenset({str, int, bool, type(None)})          # returned unchanged by to_jsonable


def _identity(value: Any) -> Any:
    return value


def _enc_float(value: float) -> float:
    if not math.isfinite(value):
        raise CodecError("NONFINITE", f"non-finite float {value!r}")
    return value


def _enc_enum(value: enum.Enum) -> Any:
    return to_jsonable(value.value)


def _enc_date(value: date) -> str:
    return value.isoformat()


def _enc_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise CodecError("NONFINITE", f"non-finite decimal {value!r}")
    return str(value)


def _enc_mapping(value: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(key, enum.Enum):
            key = key.value
        if isinstance(key, bool) or not isinstance(key, (str, int)):
            raise CodecError("BAD_KEY", f"object keys must be strings, got {key!r}")
        skey = str(key)
        if skey in out:
            raise CodecError("DUPLICATE_KEY", f"duplicate key after conversion: {skey!r}")
        out[skey] = item if type(item) in _NATIVE else to_jsonable(item)
    return out


def _enc_sequence(value: Any) -> list[Any]:
    native = _NATIVE
    return [item if type(item) in native else to_jsonable(item) for item in value]


def _enc_set(value: Any) -> list[Any]:
    items = [to_jsonable(item) for item in value]
    return sorted(items, key=lambda x: canonical_dumps(x))


def _unsupported(tp: type) -> Any:
    def enc(value: Any) -> Any:
        raise CodecError("UNSUPPORTED_TYPE", f"cannot serialize {tp.__name__}")
    return enc


def _dataclass_encoder(cls: type) -> Any:
    names = tuple(f.name for f in dataclasses.fields(cls))
    native = _NATIVE

    def enc(value: Any) -> dict[str, Any]:
        out = {}
        for name in names:
            item = getattr(value, name)
            out[name] = item if type(item) in native else to_jsonable(item)
        return out
    return enc


def _encoder_for(tp: type) -> Any:
    """The converter for every value of exact type ``tp`` (checks in the order of the encoding rules)."""
    if tp is type(None) or issubclass(tp, (str, bool, int)):
        enc = _identity
    elif issubclass(tp, float):
        enc = _enc_float
    elif issubclass(tp, enum.Enum):
        enc = _enc_enum
    elif issubclass(tp, datetime):
        enc = format_utc
    elif issubclass(tp, date):
        enc = _enc_date
    elif issubclass(tp, Decimal):
        enc = _enc_decimal
    elif issubclass(tp, Fraction):
        enc = fraction_to_json
    elif dataclasses.is_dataclass(tp) and not issubclass(tp, type):
        enc = _dataclass_encoder(tp)
    elif issubclass(tp, (dict, types.MappingProxyType)):
        enc = _enc_mapping
    elif issubclass(tp, (list, tuple)):
        enc = _enc_sequence
    elif issubclass(tp, (set, frozenset)):
        enc = _enc_set
    else:
        return _unsupported(tp)                             # not cached: an unsupported type is an error path
    _ENCODERS[tp] = enc
    return enc


_ENCODERS: dict[type, Any] = {}


def canonical_dumps(value: Any) -> bytes:
    """Canonical UTF-8 JSON bytes for hashing and storage."""
    if _CANON is not None:
        try:
            return _CANON.dumps(value)
        except Exception:  # noqa: BLE001 - the Python path decides: it returns the same bytes or raises the exact error
            pass
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# native canonical encoder (optional; byte-identical)
# ---------------------------------------------------------------------------

_CANON: Any = None          # focus_native.Canon while enabled


def _native_kind(tp: type) -> tuple[int, tuple[str, ...] | None]:
    """The encoding rule ``_encoder_for`` picks for exact type ``tp``, as the native encoder's code, with a dataclass's
    field names in the order ``sort_keys`` writes them. Same order of checks: a str, int or bool subclass (an enum
    included) is written as JSON writes the base type."""
    if tp is type(None):
        return 0, None
    if issubclass(tp, str):
        return 1, None
    if issubclass(tp, bool):
        return 2, None
    if issubclass(tp, int):
        return 3, None
    if issubclass(tp, float):
        return 4, None
    if issubclass(tp, enum.Enum):
        return 5, None
    if issubclass(tp, datetime):
        return 6, None
    if issubclass(tp, date):
        return 7, None
    if issubclass(tp, (Decimal, Fraction)):
        return 8, None
    if dataclasses.is_dataclass(tp) and not issubclass(tp, type):
        return 9, tuple(sorted(f.name for f in dataclasses.fields(tp)))
    if issubclass(tp, (dict, types.MappingProxyType)):
        return 10, None
    if issubclass(tp, (list, tuple)):
        return 11, None
    if issubclass(tp, (set, frozenset)):
        return 12, None
    return 13, None


def enable_native_codec(on: bool = True) -> bool:
    """Route canonical JSON, content hashes and digest ids through the native core when it is installed.

    The native encoder writes exactly the bytes of the Python path (``tests/unit/test_native_codec.py``). Values it does
    not handle natively, and every error, go through the Python path, so results and error codes never change. Returns
    whether the native encoder is now active.
    """
    global _CANON
    _CANON = None
    if on:
        try:
            import focus_native  # type: ignore[import-not-found]
        except ImportError:
            return False
        canon = getattr(focus_native, "Canon", None)
        if canon is not None:
            _CANON = canon(_native_kind, to_jsonable, format_utc)
    return _CANON is not None


def native_codec() -> Any:
    """The active native encoder, or None."""
    return _CANON


def _native_dumps(value: Any) -> bytes:
    """``canonical_dumps`` for a value that is already JSON-native (parsed JSON or ``to_jsonable`` output)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any) -> str:
    if _CANON is not None:
        try:
            return _CANON.sha256(value)
        except Exception:  # noqa: BLE001 - see canonical_dumps
            pass
    return sha256_hex(canonical_dumps(value))


def canonical_stream(value: Any, write: Any, chunk: int = 256) -> None:
    """Write exactly ``canonical_dumps(value)`` to ``write`` in pieces, for a dict with string keys at the top.

    Keys go in sorted order and every list or tuple value is written item by item, so a record with hundreds of
    thousands of items is never held as one JSON structure or string. JSON joins list items with "," and wraps them in
    "[]" with each item encoded alone, so the bytes are the same as one ``canonical_dumps`` call.
    """
    if not isinstance(value, dict) or not all(type(k) is str for k in value):
        write(canonical_dumps(value))
        return
    write(b"{")
    for n, key in enumerate(sorted(value)):
        if n:
            write(b",")
        write(_native_dumps(key))
        write(b":")
        item = value[key]
        if isinstance(item, (list, tuple)) and len(item) > chunk:
            write(b"[")
            for i in range(0, len(item), chunk):
                part = _native_dumps([to_jsonable(x) for x in item[i:i + chunk]])
                write(part[1:-1] if i == 0 else b"," + part[1:-1])
            write(b"]")
        else:
            write(canonical_dumps(item))
    write(b"}")


def streamed_content_hash(value: Any) -> str:
    """``content_hash`` computed with ``canonical_stream`` (same digest, bounded memory; the native encoder streams too)."""
    if _CANON is not None:
        try:
            return _CANON.sha256(value)
        except Exception:  # noqa: BLE001 - see canonical_dumps
            pass
    digest = hashlib.sha256()
    canonical_stream(value, digest.update)
    return digest.hexdigest()


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
    """Strict JSON decode: duplicate keys and non-finite numbers are rejected.

    Canonical input (what ``canonical_dumps`` writes, e.g. every stored envelope) takes a fast path: parsed with the
    built-in dict construction, it re-encodes to exactly the same bytes, which proves no key was duplicated (a duplicate
    would have been dropped and the bytes would differ). Anything else is parsed with the duplicate-rejecting hook.
    """
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CodecError("BAD_UTF8", "payload is not valid UTF-8") from exc
    else:
        text = data
        raw = None
    try:
        fast = json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)
    except CodecError:
        raise
    except (ValueError, RecursionError) as exc:
        raise CodecError("BAD_JSON", f"malformed JSON: {exc}") from exc
    try:
        canonical = _native_dumps(fast) == (raw if raw is not None else text.encode("utf-8"))
    except (ValueError, TypeError, RecursionError):
        canonical = False
    if canonical:
        return fast
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


def from_jsonable(tp: Any, data: Any, path: Any = "$") -> Any:
    """Decode JSON-native ``data`` into type ``tp`` strictly.

    Each type is compiled once into a decoder (``_compile``) that applies exactly the rules below and is cached. A path
    is built as (parent, segment) pairs and turned into text only when an error message needs it.
    """
    dec = _DECODERS.get(tp)
    if dec is None:
        dec = _compile(tp)
    return dec(data, path)


_DECODERS: dict[Any, Any] = {}


def _p(path: Any) -> str:
    """Text of a lazy path: "$", then ".name" for fields and keys and "[i]" for items."""
    segments = []
    while type(path) is tuple:
        path, segment = path
        segments.append(f"[{segment}]" if type(segment) is int else segment)
    return path + "".join(reversed(segments))


def _compile(tp: Any) -> Any:
    try:
        dec = _build_decoder(tp)
    except TypeError:                                         # unhashable or exotic type objects: decode uncached
        return lambda data, path: _build_decoder(tp)(data, path)
    try:
        _DECODERS[tp] = dec
    except TypeError:
        pass
    return dec


def _null_check(tp: Any) -> Any:
    def check(path: Any) -> CodecError:
        return CodecError("MISSING_VALUE", f"{_p(path)}: null not allowed for {tp}")
    return check


def _build_decoder(tp: Any) -> Any:
    if tp is Any or tp is object:
        return lambda data, path: data
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = get_args(tp)
        nullable = type(None) in args
        options = tuple(arg for arg in args if arg is not type(None))

        def dec_union(data: Any, path: Any) -> Any:
            if data is None:
                if nullable:
                    return None
                raise CodecError("MISSING_VALUE", f"{_p(path)}: null not allowed")
            errors = []
            for arg in options:
                try:
                    return from_jsonable(arg, data, path)
                except CodecError as exc:
                    errors.append(exc.code)
            raise CodecError("TYPE_MISMATCH", f"{_p(path)}: value does not match {tp} ({errors})")
        return dec_union
    if origin is Literal:
        allowed = get_args(tp)

        def dec_literal(data: Any, path: Any) -> Any:
            if not any(type(data) is type(option) and data == option for option in allowed):
                raise CodecError("BAD_LITERAL", f"{_p(path)}: {data!r} not in {allowed}")
            return data
        return dec_literal
    if tp is type(None):
        def dec_none(data: Any, path: Any) -> Any:
            if data is not None:
                raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected null")
            return None
        return dec_none
    null = _null_check(tp)
    if origin in (tuple,):
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            item_tp = args[0]

            def dec_tuple_var(data: Any, path: Any) -> Any:
                if data is None:
                    raise null(path)
                if not isinstance(data, list):
                    raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected array")
                item = _DECODERS.get(item_tp) or _compile(item_tp)
                return tuple([item(x, (path, i)) for i, x in enumerate(data)])
            return dec_tuple_var

        def dec_tuple_fixed(data: Any, path: Any) -> Any:
            if data is None:
                raise null(path)
            if not isinstance(data, list):
                raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected array")
            if len(args) != len(data):
                raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected {len(args)} items")
            return tuple(from_jsonable(a, x, (path, i)) for i, (a, x) in enumerate(zip(args, data)))
        return dec_tuple_fixed
    if origin in (list,):
        (item_tp,) = get_args(tp) or (Any,)

        def dec_list(data: Any, path: Any) -> Any:
            if data is None:
                raise null(path)
            if not isinstance(data, list):
                raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected array")
            item = _DECODERS.get(item_tp) or _compile(item_tp)
            return [item(x, (path, i)) for i, x in enumerate(data)]
        return dec_list
    if origin in (dict, typing.Mapping, types.MappingProxyType) or tp in (dict,):
        key_tp, val_tp = get_args(tp) or (str, Any)
        enum_key = isinstance(key_tp, type) and issubclass(key_tp, enum.Enum)

        def dec_dict(data: Any, path: Any) -> Any:
            if data is None:
                raise null(path)
            if not isinstance(data, dict):
                raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected object")
            value = _DECODERS.get(val_tp) or _compile(val_tp)
            out = {}
            for key, item in data.items():
                if key_tp is int:
                    try:
                        real_key: Any = int(key)
                    except ValueError as exc:
                        raise CodecError("BAD_KEY", f"{_p(path)}: key {key!r} is not an integer") from exc
                    if str(real_key) != key:
                        raise CodecError("BAD_KEY", f"{_p(path)}: non-canonical integer key {key!r}")
                elif enum_key:
                    real_key = key_tp(key)
                else:
                    real_key = key
                out[real_key] = value(item, (path, f".{key}"))
            return out
        return dec_dict
    if isinstance(tp, type):
        if issubclass(tp, enum.Enum):
            def dec_enum(data: Any, path: Any) -> Any:
                if data is None:
                    raise null(path)
                try:
                    return tp(data)
                except ValueError as exc:
                    raise CodecError("BAD_ENUM", f"{_p(path)}: {data!r} is not a valid {tp.__name__}") from exc
            return dec_enum
        if dataclasses.is_dataclass(tp):
            return _dataclass_decoder(tp, null)
        simple = _SIMPLE.get(tp)
        if simple is not None:
            def dec_simple(data: Any, path: Any) -> Any:
                if data is None:
                    raise null(path)
                return simple(data, path)
            return dec_simple

    def dec_unsupported(data: Any, path: Any) -> Any:
        if data is None:
            raise null(path)
        raise CodecError("UNSUPPORTED_TYPE", f"{_p(path)}: cannot decode type {tp!r}")
    return dec_unsupported


def _dec_bool(data: Any, path: Any) -> bool:
    if not isinstance(data, bool):
        raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected boolean")
    return data


def _dec_int(data: Any, path: Any) -> int:
    if isinstance(data, bool) or not isinstance(data, int):
        raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected integer")
    return data


def _dec_float(data: Any, path: Any) -> float:
    if isinstance(data, bool) or not isinstance(data, (int, float)):
        raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected number")
    if not math.isfinite(float(data)):
        raise CodecError("NONFINITE", f"{_p(path)}: non-finite number")
    return float(data)


def _dec_str(data: Any, path: Any) -> str:
    if not isinstance(data, str):
        raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected string")
    return data


_SIMPLE: dict[type, Any] = {
    bool: _dec_bool,
    int: _dec_int,
    float: _dec_float,
    str: _dec_str,
    datetime: lambda data, path: parse_utc(data),
    date: lambda data, path: parse_iso_date(data),
    Decimal: lambda data, path: decimal_from_text(data),
    Fraction: lambda data, path: fraction_from_json(data),
}


def _dataclass_decoder(cls: type, null: Any) -> Any:
    fields = None

    def setup() -> tuple:
        hints = _hints(cls)
        return tuple((f.name, f".{f.name}", hints[f.name],
                      f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING)  # type: ignore[misc]
                     for f in dataclasses.fields(cls) if f.init)

    def dec_dataclass(data: Any, path: Any) -> Any:
        nonlocal fields
        if data is None:
            raise null(path)
        if not isinstance(data, dict):
            raise CodecError("TYPE_MISMATCH", f"{_p(path)}: expected object for {cls.__name__}")
        if fields is None:
            fields = setup()
        known = {name for name, _, _, _ in fields}
        for key in data:
            if key not in known:
                unknown = sorted(set(data) - known)
                raise CodecError("UNKNOWN_FIELD", f"{_p(path)}: unknown field(s) {unknown} for {cls.__name__}")
        kwargs = {}
        for name, segment, hint, has_default in fields:
            if name not in data:
                if has_default:
                    continue
                raise CodecError("MISSING_FIELD", f"{_p(path)}: missing field {name!r} for {cls.__name__}")
            dec = _DECODERS.get(hint) or _compile(hint)
            kwargs[name] = dec(data[name], (path, segment))
        try:
            return cls(**kwargs)
        except CodecError:
            raise
        except (ValueError, TypeError) as exc:
            code = getattr(exc, "code", "INVALID_RECORD")
            raise CodecError(code, f"{_p(path)}: {exc}") from exc
    return dec_dataclass


def _decode_dataclass(cls: type, data: Any, path: Any) -> Any:
    return from_jsonable(cls, data, path)


def from_jsonable_releasing(tp: Any, data: Any, path: Any = "$") -> Any:
    """``from_jsonable(tp, data)`` for a dataclass ``tp``, consuming ``data`` as it goes.

    Each field is removed from ``data`` as it is decoded, and the items of a variable-length tuple field are released
    one by one. The parsed JSON and the objects built from it therefore never both hold everything: for a snapshot of
    a million edges that is the difference between holding the data twice and once. The rules and errors are those of
    ``from_jsonable``; anything that is not a dataclass from an object goes to it unchanged.
    """
    if not (dataclasses.is_dataclass(tp) and isinstance(tp, type) and isinstance(data, dict)):
        return from_jsonable(tp, data, path)
    hints = _hints(tp)
    fields = [(f.name, hints[f.name], f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING)  # type: ignore[misc]
              for f in dataclasses.fields(tp) if f.init]
    unknown = sorted(set(data) - {name for name, _, _ in fields})
    if unknown:
        raise CodecError("UNKNOWN_FIELD", f"{_p(path)}: unknown field(s) {unknown} for {tp.__name__}")
    kwargs: dict[str, Any] = {}
    for name, hint, has_default in fields:
        if name not in data:
            if has_default:
                continue
            raise CodecError("MISSING_FIELD", f"{_p(path)}: missing field {name!r} for {tp.__name__}")
        value = data.pop(name)
        where = (path, f".{name}")
        args = get_args(hint)
        if get_origin(hint) is tuple and len(args) == 2 and args[1] is Ellipsis and isinstance(value, list):
            item = _DECODERS.get(args[0]) or _compile(args[0])
            out = []
            for i in range(len(value)):
                out.append(item(value[i], (where, i)))
                value[i] = None                              # this item's parsed JSON is released now
            kwargs[name] = tuple(out)
        else:
            kwargs[name] = (_DECODERS.get(hint) or _compile(hint))(value, where)
        del value
    try:
        return tp(**kwargs)
    except CodecError:
        raise
    except (ValueError, TypeError) as exc:
        code = getattr(exc, "code", "INVALID_RECORD")
        raise CodecError(code, f"{_p(path)}: {exc}") from exc


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
    """The body is JSON-native here (sealed fields or a parsed envelope), so it is hashed without conversion."""
    body = {k: v for k, v in fields.items() if k != "content_sha256"}
    return sha256_hex(_native_dumps(body))


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


def seal_bytes(type_name: str, record_id: str, payload_bytes: bytes, *, created_at: datetime, source_run_id: str | None = None,
               policy_hash: str | None = None, purpose: str | None = None) -> bytes:
    """``seal(...).to_bytes()`` for a payload already encoded by ``canonical_dumps``.

    The envelope's own members are encoded once and the payload bytes are spliced in at their sorted place, so a large
    record is encoded once (natively when the encoder is installed) instead of converted to JSON-native data and encoded
    twice. Canonical JSON encodes each member independently in sorted key order, so the bytes are identical."""
    if not type_name or not record_id:
        raise CodecError("BAD_ENVELOPE", "envelope type and id are required")
    head = _native_dumps({"created_at_utc": format_utc(created_at), "id": record_id})
    tail = _native_dumps({"policy_hash": policy_hash, "purpose": purpose, "schema_version": SCHEMA_VERSION, "source_run_id": source_run_id,
                          "type": type_name})
    body = head[:-1] + b',"payload":' + payload_bytes + b"," + tail[1:]
    return _ENVELOPE_HEAD + hashlib.sha256(body).hexdigest().encode("ascii") + b'",' + body[1:]


def _check_envelope_fields(data: Any, expected_type: str | None) -> None:
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


# A canonical envelope starts with this member: ``content_sha256`` sorts before every other envelope key.
_ENVELOPE_HEAD = b'{"content_sha256":"'
_HEX = frozenset(b"0123456789abcdef")


def canonical_envelope_hashes(raw: Any) -> tuple[str, str] | None:
    """For bytes laid out as a canonical envelope: the content hash they claim, and the SHA-256 of the canonical body
    they hold. That body is the bytes without their first member, so it is hashed in place, with no second copy and no
    re-encoding. None for any other layout (the strict path then decides)."""
    head = len(_ENVELOPE_HEAD)
    if not isinstance(raw, (bytes, bytearray)) or not raw.startswith(_ENVELOPE_HEAD) or raw[head + 64:head + 66] != b'",':
        return None
    claimed = bytes(raw[head:head + 64])
    if not set(claimed) <= _HEX:
        return None
    body = hashlib.sha256()
    body.update(b"{")
    body.update(memoryview(raw)[head + 66:])
    return claimed.decode("ascii"), body.hexdigest()


def parse_json_text(text: str) -> Any:
    """``json.loads`` with the strict constant and number rules. It does not reject duplicate keys by itself: the
    caller proves the bytes canonical (``canonical_envelope_payload``) or uses ``strict_loads``."""
    try:
        return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)
    except CodecError:
        raise
    except (ValueError, RecursionError) as exc:
        raise CodecError("BAD_JSON", f"malformed JSON: {exc}") from exc


def canonical_envelope_payload(data: Any, expected_type: str | None, raw_sha256: str, hashes: tuple[str, str]) -> Any:
    """The payload of an envelope parsed from canonical bytes, after every check ``open_envelope`` makes; None when the
    bytes were not exactly the canonical encoding of what was parsed (the caller then takes the strict path, which also
    rejects duplicate keys).

    The canonical proof streams the parsed value's canonical encoding into SHA-256 (bounded memory) and compares it with
    the stored bytes' digest, and the envelope hash comes from ``canonical_envelope_hashes``. Unlike ``open_envelope``,
    no copy of the bytes is re-encoded in memory.
    """
    try:
        canonical = isinstance(data, dict) and streamed_content_hash(data) == raw_sha256
    except Exception:  # noqa: BLE001 - not provably canonical: the strict path decides
        canonical = False
    if not canonical:
        return None
    _check_envelope_fields(data, expected_type)
    claimed, body = hashes
    if data["content_sha256"] != claimed or claimed != body:
        raise CodecError("HASH_MISMATCH", f"envelope {data.get('id')!r} content hash mismatch")
    parse_utc(data["created_at_utc"])
    return data["payload"]


def open_envelope(raw: bytes | str, expected_type: str | None = None) -> Envelope:
    data = strict_loads(raw)
    _check_envelope_fields(data, expected_type)
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
