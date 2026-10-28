"""Operation registry: the single definition of every /api/v1 operation (D-13).

Each :class:`Operation` declares its path, method, required grants, whether it
is read-only, JSON Schemas for path/query parameters, request body and
response data, and a handler. Three things are generated from it:

* Flask routes (``focus/web/routes.py``);
* the OpenAPI 3.1 document (``focus/web/openapi.py``);
* the MCP tool and resource catalog (``focus/mcp/catalog.py``).

A REST endpoint, its documentation and its future MCP tool therefore cannot
drift apart. Handlers receive an already-authorized, scope-bound context and
validated arguments, and return an :class:`ApiResult`. They never acquire
sources, run solvers or write on GET.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..codec import format_utc, to_jsonable
from .schema import check_schema
from .security import ApiError, RequestContext

STATUSES = ("ready", "unknown", "blocked", "stale", "empty")
PURPOSES = ("FORECAST", "EXECUTABLE", "SCENARIO")


@dataclass
class ApiResult:
    status: str
    data: Any
    source_run_id: str | None = None
    plan_id: str | None = None
    policy_id: str | None = None
    evaluation_id: str | None = None
    purpose: str | None = None
    coverage: dict[str, int] | None = None
    reasons: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None
    http_status: int = 200

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"envelope status must be one of {STATUSES}")
        if self.purpose is not None and self.purpose not in PURPOSES:
            raise ValueError(f"envelope purpose must be one of {PURPOSES}")


def reason(code: str, message: str, next_action: str | None = None, **facts: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message}
    if next_action:
        out["next_action"] = next_action
    if facts:
        out["facts"] = to_jsonable(facts)
    return out


def build_envelope(result: ApiResult, as_of: datetime) -> dict[str, Any]:
    coverage = result.coverage or {"admitted": 0, "blocked": 0, "quarantined": 0, "excluded": 0}
    return {
        "schema_version": 1,
        "status": result.status,
        "source_run_id": result.source_run_id,
        "plan_id": result.plan_id,
        "policy_id": result.policy_id,
        "evaluation_id": result.evaluation_id,
        "as_of_utc": format_utc(as_of),
        "purpose": result.purpose,
        "coverage": coverage,
        "data": to_jsonable(result.data),
        "reasons": to_jsonable(result.reasons),
        "next_cursor": result.next_cursor,
    }


ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "status", "source_run_id", "plan_id", "policy_id", "evaluation_id",
                 "as_of_utc", "purpose", "coverage", "data", "reasons", "next_cursor"],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "status": {"type": "string", "enum": list(STATUSES)},
        "source_run_id": {"type": ["string", "null"]},
        "plan_id": {"type": ["string", "null"]},
        "policy_id": {"type": ["string", "null"]},
        "evaluation_id": {"type": ["string", "null"]},
        "as_of_utc": {"type": "string", "format": "date-time"},
        "purpose": {"type": ["string", "null"], "enum": list(PURPOSES) + [None]},
        "coverage": {
            "type": "object",
            "required": ["admitted", "blocked", "quarantined", "excluded"],
            "properties": {k: {"type": "integer", "minimum": 0} for k in ("admitted", "blocked", "quarantined", "excluded")},
            "additionalProperties": False,
        },
        "data": {"description": "Operation-specific payload. Null means unknown/not applicable, never zero."},
        "reasons": {"type": "array", "items": {"type": "object", "required": ["code", "message"],
                                                "properties": {"code": {"type": "string"}, "message": {"type": "string"},
                                                               "next_action": {"type": "string"}, "facts": {}},
                                                "additionalProperties": False}},
        "next_cursor": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message", "request_id"],
            "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "request_id": {"type": "string"}},
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}

Handler = Callable[[RequestContext, dict[str, Any]], ApiResult]


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    summary: str
    description: str
    grants: tuple[str, ...]
    read_only: bool
    handler: Handler
    path_params: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False})
    body: dict[str, Any] | None = None
    data_schema: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    mcp_tool: bool = True
    mcp_resource: str | None = None
    success_status: int = 200

    def __post_init__(self) -> None:
        if self.method not in ("GET", "POST"):
            raise ValueError(f"{self.name}: only GET and POST operations are registered")
        if self.method == "GET" and not self.read_only:
            raise ValueError(f"{self.name}: GET operations must be read-only")
        if self.method == "POST" and self.read_only:
            raise ValueError(f"{self.name}: POST operations are commands")
        if self.method == "POST" and self.body is None:
            raise ValueError(f"{self.name}: commands declare a request body schema")
        check_schema(self.query)
        if self.body is not None:
            check_schema(self.body)
        for name, schema in self.path_params.items():
            check_schema(schema, f"#/path/{name}")
            if "{" + name + "}" not in self.path:
                raise ValueError(f"{self.name}: path parameter {name} missing from {self.path}")

    @property
    def flask_rule(self) -> str:
        rule = self.path
        for name in self.path_params:
            rule = rule.replace("{" + name + "}", f"<{name}>")
        return rule

    def input_schema(self) -> dict[str, Any]:
        """Combined argument schema (path + query + body) used by MCP tools."""
        props: dict[str, Any] = {}
        required: list[str] = []
        for name, schema in self.path_params.items():
            props[name] = schema
            required.append(name)
        props.update(self.query.get("properties", {}))
        required.extend(self.query.get("required", []))
        if self.body is not None:
            props.update(self.body.get("properties", {}))
            required.extend(self.body.get("required", []))
        return {"type": "object", "properties": props, "required": sorted(set(required)), "additionalProperties": False}


class OperationRegistry:
    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}

    def add(self, op: Operation) -> Operation:
        if op.name in self._ops:
            raise ValueError(f"duplicate operation {op.name}")
        for other in self._ops.values():
            if other.path == op.path and other.method == op.method:
                raise ValueError(f"duplicate route {op.method} {op.path}")
        self._ops[op.name] = op
        return op

    def operation(self, name: str, method: str, path: str, **kwargs: Any) -> Callable[[Handler], Handler]:
        def decorator(fn: Handler) -> Handler:
            self.add(Operation(name=name, method=method, path=path, handler=fn, **kwargs))
            return fn

        return decorator

    def get(self, name: str) -> Operation:
        try:
            return self._ops[name]
        except KeyError:
            raise ApiError(404, "UNKNOWN_OPERATION", f"no operation named {name!r}") from None

    def all(self) -> list[Operation]:
        return sorted(self._ops.values(), key=lambda o: (o.path, o.method))


REGISTRY = OperationRegistry()


# ---------------------------------------------------------------------------
# opaque, scope-bound pagination cursors
# ---------------------------------------------------------------------------

def encode_cursor(secret: str, ctx: RequestContext, op_name: str, filters: dict[str, Any], version: str, offset: int) -> str:
    body = json.dumps({
        "op": op_name,
        "s": ctx.subject,
        "sc": ctx.scope.describe(),
        "f": filters,
        "v": version,
        "o": offset,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    mac = hmac.new(hashlib.sha256(b"FOCUS_CURSOR_V1\0" + secret.encode()).digest(), body, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(body + mac).decode("ascii").rstrip("=")


def decode_cursor(secret: str, ctx: RequestContext, op_name: str, filters: dict[str, Any], version: str, token: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        raise ApiError(400, "BAD_CURSOR", "cursor is malformed") from None
    body, mac = raw[:-16], raw[-16:]
    expected = hmac.new(hashlib.sha256(b"FOCUS_CURSOR_V1\0" + secret.encode()).digest(), body, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expected):
        raise ApiError(400, "BAD_CURSOR", "cursor signature is invalid")
    data = json.loads(body)
    if data.get("op") != op_name or data.get("s") != ctx.subject or data.get("sc") != ctx.scope.describe() or data.get("f") != filters:
        raise ApiError(400, "CURSOR_SCOPE_MISMATCH", "cursor belongs to another scope, filter or operation")
    if data.get("v") != version:
        raise ApiError(409, "CURSOR_STALE", "the underlying data revision changed; restart pagination")
    offset = data.get("o")
    if not isinstance(offset, int) or offset < 0:
        raise ApiError(400, "BAD_CURSOR", "cursor offset invalid")
    return offset


PAGE_QUERY = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Page size (default 100, max 500)."},
    "cursor": {"type": "string", "maxLength": 2048, "description": "Opaque cursor from a previous page."},
}
