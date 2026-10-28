"""Strict JSON-Schema subset validator shared by REST commands, query params and MCP tools.

Supported keywords: type (string/integer/number/boolean/object/array/null),
enum, const, pattern, minLength, maxLength, minimum, maximum, required,
properties, additionalProperties (false only), items, minItems, maxItems,
uniqueItems, format (date, date-time). Unsupported keywords raise at
definition time so schemas cannot silently drift from what is enforced.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

SUPPORTED = frozenset({
    "type", "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum",
    "required", "properties", "additionalProperties", "items", "minItems", "maxItems",
    "uniqueItems", "format", "description", "title", "default", "examples", "readOnly",
    "nullable", "$comment",
})


class SchemaViolation(ValueError):
    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def check_schema(schema: dict[str, Any], path: str = "#") -> None:
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise ValueError(f"{path}: unsupported schema keywords {sorted(unknown)}")
    if schema.get("type") == "object" and schema.get("additionalProperties", False) is not False:
        raise ValueError(f"{path}: objects must declare additionalProperties false")
    for name, sub in schema.get("properties", {}).items():
        check_schema(sub, f"{path}/properties/{name}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}/items")


def _type_ok(expected: str, value: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    raise ValueError(f"unknown schema type {expected!r}")


def validate(schema: dict[str, Any], value: Any, path: str = "$") -> None:
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if value is None and schema.get("nullable"):
            return
        if not any(_type_ok(t, value) for t in types):
            raise SchemaViolation(path, f"expected {'/'.join(types)}")
    if "const" in schema and value != schema["const"]:
        raise SchemaViolation(path, f"must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaViolation(path, f"must be one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaViolation(path, f"shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaViolation(path, f"longer than {schema['maxLength']}")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise SchemaViolation(path, "does not match the required pattern")
        fmt = schema.get("format")
        if fmt == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                raise SchemaViolation(path, "not an ISO date") from None
        elif fmt == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise SchemaViolation(path, "not an RFC3339 date-time") from None
            if parsed.tzinfo is None:
                raise SchemaViolation(path, "date-time needs an explicit offset")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaViolation(path, f"below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaViolation(path, f"above maximum {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise SchemaViolation(f"{path}.{name}", "is required")
        if schema.get("additionalProperties", False) is False and schema.get("type") == "object":
            extra = sorted(set(value) - set(props))
            if extra:
                raise SchemaViolation(path, f"unknown field(s) {extra}")
        for name, sub in props.items():
            if name in value:
                validate(sub, value[name], f"{path}.{name}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaViolation(path, f"fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaViolation(path, f"more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = []
            for item in value:
                if item in seen:
                    raise SchemaViolation(path, "items must be unique")
                seen.append(item)
        if "items" in schema:
            for i, item in enumerate(value):
                validate(schema["items"], item, f"{path}[{i}]")


def coerce_query(schema: dict[str, Any], args: dict[str, str]) -> dict[str, Any]:
    """Convert string query args per schema types, then validate strictly."""
    props = schema.get("properties", {})
    out: dict[str, Any] = {}
    for name, raw in args.items():
        sub = props.get(name)
        if sub is None:
            raise SchemaViolation(f"$.{name}", "unknown query parameter")
        kind = sub.get("type")
        if kind == "integer":
            if not re.fullmatch(r"-?\d{1,12}", raw):
                raise SchemaViolation(f"$.{name}", "expected integer")
            out[name] = int(raw)
        elif kind == "boolean":
            if raw not in ("true", "false"):
                raise SchemaViolation(f"$.{name}", "expected true/false")
            out[name] = raw == "true"
        else:
            out[name] = raw
    validate(schema, out)
    return out
