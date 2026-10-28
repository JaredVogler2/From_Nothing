"""OpenAPI 3.1 document generated from the operation registry (D-13)."""
from __future__ import annotations

import copy
from typing import Any

from .operations import ENVELOPE_SCHEMA, ERROR_SCHEMA, OperationRegistry

ERROR_STATUSES = {
    "400": "Malformed parameter, cursor or JSON",
    "401": "Not authenticated by the configured identity provider",
    "403": "Missing grant, wrong scope, failed CSRF or cross-site request",
    "404": "Unknown resource in the caller's scope",
    "409": "Version or idempotency conflict, or stale cursor",
    "413": "Request body larger than FOCUS_MAX_CONTENT_LENGTH",
    "415": "Commands require application/json",
    "422": "Body failed strict schema validation",
    "500": "Internal error (redacted; correlate with request_id)",
    "503": "Repository or source unavailable",
}


def _envelope_for(data_schema: dict[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(ENVELOPE_SCHEMA)
    if data_schema:
        schema["properties"]["data"] = copy.deepcopy(data_schema)
    return schema


def build_openapi(registry: OperationRegistry, server_url: str = "/") -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for op in registry.all():
        params: list[dict[str, Any]] = []
        for name, schema in op.path_params.items():
            params.append({"name": name, "in": "path", "required": True, "schema": schema})
        required_query = set(op.query.get("required", []))
        for name, schema in op.query.get("properties", {}).items():
            params.append({"name": name, "in": "query", "required": name in required_query, "schema": schema,
                           "description": schema.get("description", "")})
        if op.method == "POST":
            params.append({"name": "X-CSRF-Token", "in": "header", "required": True, "schema": {"type": "string"},
                           "description": "Token from GET /api/v1/context bound to the caller's identity."})
        responses: dict[str, Any] = {
            str(op.success_status): {
                "description": "Queued worker run (not yet a successful schedule)" if op.success_status == 202 else "Canonical envelope",
                "content": {"application/json": {"schema": _envelope_for(op.data_schema)}},
            }
        }
        for code, text in ERROR_STATUSES.items():
            if op.method == "GET" and code in ("413", "415", "422"):
                continue
            responses[code] = {"description": text, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
        operation: dict[str, Any] = {
            "operationId": op.name,
            "summary": op.summary,
            "description": op.description,
            "tags": list(op.tags),
            "parameters": params,
            "responses": responses,
            "x-focus-grants": list(op.grants),
            "x-focus-read-only": op.read_only,
            "x-mcp-tool": op.mcp_tool and op.read_only,
            "x-mcp-resource": op.mcp_resource,
        }
        if op.body is not None:
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": op.body}}}
        paths.setdefault(op.path, {})[op.method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "FOCUS Manufacturing Scheduling API",
            "version": "1.0.0",
            "description": (
                "Versioned read projections and audited commands over canonical FOCUS repositories. "
                "GET never acquires sources, optimizes, writes or regrades. Commands return 202 with a durable worker run. "
                "Null means unknown or not applicable, never zero."
            ),
        },
        "servers": [{"url": server_url}],
        "paths": paths,
        "components": {
            "schemas": {"Envelope": ENVELOPE_SCHEMA, "Error": ERROR_SCHEMA},
            "securitySchemes": {
                "positConnectKey": {"type": "apiKey", "in": "header", "name": "Authorization",
                                    "description": "Posit Connect API key ('Key <api-key>'); Connect authenticates and forwards trusted identity."},
                "trustedSso": {"type": "apiKey", "in": "header", "name": "X-Forwarded-User",
                               "description": "Set only by the trusted Tanzu SSO route service / proxy, never by clients."},
                "csrf": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"},
            },
        },
    }
