"""MCP tool/resource catalog derived from the operation registry (D-13).

Read-only registry operations become MCP tools (``readOnlyHint``) and, when
they declare ``mcp_resource``, MCP resources or resource templates. Commands
are deliberately not exposed as tools: their idempotency/version/CSRF
workflow stays on the REST surface until a separately authorized MCP command
policy exists.
"""
from __future__ import annotations

import re
from typing import Any

from ..web.operations import ENVELOPE_SCHEMA, Operation, OperationRegistry

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "focus-scheduling", "title": "FOCUS Manufacturing Scheduling", "version": "1.0.0"}


def tool_definitions(registry: OperationRegistry) -> list[dict[str, Any]]:
    tools = []
    for op in registry.all():
        if not op.mcp_tool or not op.read_only:
            continue
        tools.append({
            "name": op.name,
            "title": op.summary,
            "description": op.description,
            "inputSchema": op.input_schema(),
            "outputSchema": ENVELOPE_SCHEMA,
            "annotations": {
                "title": op.summary,
                "readOnlyHint": True,
                "idempotentHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        })
    return tools


def static_resources(registry: OperationRegistry) -> list[dict[str, Any]]:
    out = []
    for op in registry.all():
        uri = op.mcp_resource
        if uri and "{" not in uri and op.read_only:
            out.append({"uri": uri, "name": op.name, "title": op.summary, "description": op.description, "mimeType": "application/json"})
    return out


def resource_templates(registry: OperationRegistry) -> list[dict[str, Any]]:
    out = []
    for op in registry.all():
        uri = op.mcp_resource
        if uri and "{" in uri and op.read_only:
            out.append({"uriTemplate": uri, "name": op.name, "title": op.summary, "description": op.description, "mimeType": "application/json"})
    return out


def match_resource(registry: OperationRegistry, uri: str) -> tuple[Operation, dict[str, str]] | None:
    for op in registry.all():
        template = op.mcp_resource
        if not template or not op.read_only:
            continue
        pattern = "^" + re.sub(r"\\\{([a-z_]+)\\\}", r"(?P<\1>[A-Za-z0-9._-]+)", re.escape(template)) + "$"
        found = re.match(pattern, uri)
        if found:
            return op, found.groupdict()
    return None


def describe(registry: OperationRegistry) -> dict[str, Any]:
    """Catalog document (also printed by ``python -m focus.cli mcp-catalog``)."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": SERVER_INFO,
        "tools": tool_definitions(registry),
        "resources": static_resources(registry),
        "resourceTemplates": resource_templates(registry),
        "transport": {"streamable_http": "/mcp (enabled with FOCUS_MCP_ENABLED=true)", "stdio": "python -m focus.cli mcp-stdio"},
    }
