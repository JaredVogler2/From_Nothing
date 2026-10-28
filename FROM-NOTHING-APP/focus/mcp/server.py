"""Minimal, dependency-free MCP server over JSON-RPC 2.0 (read-only).

Transports:

* Streamable HTTP: ``POST /mcp`` (one JSON-RPC message per request). Enabled
  only with ``FOCUS_MCP_ENABLED=true``. It uses the same identity provider,
  grants and scope as the REST API and validates ``Origin``.
* stdio: ``python -m focus.cli mcp-stdio``. Local, fixture/operator use, with
  an explicitly chosen demo persona or the trusted operator identity.

Only read-only registry operations are callable. Results carry the canonical
API envelope as ``structuredContent`` and as JSON text.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from ..codec import CodecError, strict_loads
from ..runtime import Services
from ..web.operations import REGISTRY, Operation
from ..web.security import ApiError, check_same_origin
from .catalog import PROTOCOL_VERSION, SERVER_INFO, match_resource, resource_templates, static_resources, tool_definitions

JSONRPC = "2.0"
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
PARSE_ERROR = -32700

Invoker = Callable[[Operation, dict[str, Any], dict[str, str], bytes | None], tuple[dict[str, Any], int]]


def _split_arguments(op: Operation, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    path_args: dict[str, Any] = {}
    query: dict[str, str] = {}
    query_props = op.query.get("properties", {})
    for name, value in arguments.items():
        if name in op.path_params:
            path_args[name] = value
        elif name in query_props:
            if isinstance(value, bool):
                query[name] = "true" if value else "false"
            elif isinstance(value, (int, str)):
                query[name] = str(value)
            else:
                raise ApiError(400, "INVALID_PARAMETER", f"argument {name} must be a scalar")
        else:
            raise ApiError(400, "INVALID_PARAMETER", f"unknown argument {name}")
    return path_args, query


def handle_message(message: Any, invoke: Invoker) -> dict[str, Any] | None:
    """Process one JSON-RPC message; returns the response or None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC or not isinstance(message.get("method"), str):
        return {"jsonrpc": JSONRPC, "id": None, "error": {"code": INVALID_REQUEST, "message": "invalid JSON-RPC request"}}
    method = message["method"]
    msg_id = message.get("id")
    params = message.get("params") or {}
    if "id" not in message:
        return None  # notification (e.g. notifications/initialized)

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}

    def fail(code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC, "id": msg_id, "error": {"code": code, "message": text}}

    if not isinstance(params, dict):
        return fail(INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False, "subscribe": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Read-only access to canonical FOCUS projections. Null means unknown, never zero. "
                            "Scheduling, signing, promotion and grading are not available through MCP.",
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_definitions(REGISTRY)})
    if method == "resources/list":
        return ok({"resources": static_resources(REGISTRY)})
    if method == "resources/templates/list":
        return ok({"resourceTemplates": resource_templates(REGISTRY)})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return fail(INVALID_PARAMS, "tools/call needs name and object arguments")
        tools = {t["name"] for t in tool_definitions(REGISTRY)}
        if name not in tools:
            return fail(INVALID_PARAMS, f"unknown or non-read-only tool {name!r}")
        op = REGISTRY.get(name)
        try:
            path_args, query = _split_arguments(op, arguments)
            envelope, _status = invoke(op, path_args, query, None)
        except ApiError as exc:
            error = {"error": {"code": exc.code, "message": exc.message}}
            return ok({"content": [{"type": "text", "text": json.dumps(error)}], "isError": True})
        return ok({"content": [{"type": "text", "text": json.dumps(envelope, sort_keys=True)}], "structuredContent": envelope, "isError": False})
    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str):
            return fail(INVALID_PARAMS, "resources/read needs a uri")
        found = match_resource(REGISTRY, uri)
        if found is None:
            return fail(INVALID_PARAMS, f"unknown resource {uri!r}")
        op, path_args = found
        try:
            envelope, _status = invoke(op, path_args, {}, None)
        except ApiError as exc:
            return fail(INVALID_PARAMS, f"{exc.code}: {exc.message}")
        return ok({"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(envelope, sort_keys=True)}]})
    return fail(METHOD_NOT_FOUND, f"method {method!r} not supported")


def register_mcp(bp: Blueprint, services: Services) -> None:
    from ..web.routes import invoke_operation

    def invoker(op: Operation, path_args: dict[str, Any], query: dict[str, str], body: bytes | None) -> tuple[dict[str, Any], int]:
        return invoke_operation(op, path_args, query, body, transport="mcp")

    @bp.post("/mcp")
    def mcp_endpoint():  # type: ignore[no-untyped-def]
        check_same_origin()
        if request.mimetype != "application/json":
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "MCP requests use application/json")
        try:
            message = strict_loads(request.get_data(cache=False))
        except CodecError as exc:
            return jsonify({"jsonrpc": JSONRPC, "id": None, "error": {"code": PARSE_ERROR, "message": exc.message}}), 400
        if isinstance(message, list):
            return jsonify({"jsonrpc": JSONRPC, "id": None, "error": {"code": INVALID_REQUEST, "message": "batches are not supported"}}), 400
        response = handle_message(message, invoker)
        if response is None:
            return "", 202
        return jsonify(response)

    @bp.get("/mcp")
    def mcp_get():  # type: ignore[no-untyped-def]
        raise ApiError(405, "METHOD_NOT_ALLOWED", "this MCP server does not offer a server-initiated SSE stream")
