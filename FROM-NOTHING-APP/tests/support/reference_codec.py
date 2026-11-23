"""The typed decoder's full rules, as they stood before the short paths were added to ``focus.codec``.

``tests/unit/test_codec_fast_path.py`` decodes the same data with this module and with ``focus.codec`` and requires the
same value, or the same error code and message. This copy is the oracle: do not make it faster, and change a rule here
only together with the same rule in ``focus.codec``.
"""
from __future__ import annotations

import dataclasses
import enum
import math
import types
import typing
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any, Literal, Union, get_args, get_origin

from focus.codec import CodecError, _hints, _p, decimal_from_text, fraction_from_json, parse_iso_date, parse_utc


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
