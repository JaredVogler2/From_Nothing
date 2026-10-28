"""MCP stdio transport (read-only), reusing the HTTP operation pipeline and grants."""
from __future__ import annotations

import getpass
import json
import sys
from typing import Any

from flask import g

from ..codec import CodecError, strict_loads
from ..web.security import DEMO_PERSONAS, NO_SCOPE, RequestContext, Scope, _grants_for, _scope_for_live
from .server import JSONRPC, PARSE_ERROR, handle_message


def _identity(services: Any, persona: str) -> RequestContext:
    settings = services.settings
    if settings.identity_provider == "fixture":
        spec = DEMO_PERSONAS.get(persona)
        if spec is None:
            raise SystemExit(f"unknown demo persona {persona!r}")
        scope = Scope(True, frozenset(), spec["person"], spec["display"]) if spec["teams"] == "*" else Scope(False, frozenset(spec["teams"]), spec["person"], spec["display"])
        return RequestContext("stdio", f"demo:{persona}", spec["display"], frozenset(spec["grants"]), scope, "fixture", True, persona)
    subject = f"local:{getpass.getuser()}"
    grants = _grants_for(settings, subject, ())
    return RequestContext("stdio", subject, subject, grants, _scope_for_live(settings, subject, grants) if grants else NO_SCOPE, "local", False)


def serve_stdio(services: Any, persona: str = "manager") -> None:
    from ..web.app import create_app
    from ..web.routes import invoke_operation

    app = create_app(services=services)
    ctx = _identity(services, persona)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = strict_loads(line)
        except CodecError as exc:
            sys.stdout.write(json.dumps({"jsonrpc": JSONRPC, "id": None, "error": {"code": PARSE_ERROR, "message": exc.message}}) + "\n")
            sys.stdout.flush()
            continue
        with app.test_request_context("/mcp", method="POST"):
            g.focus_ctx = ctx
            response = handle_message(message, lambda op, p, q, b: invoke_operation(op, p, q, b, transport="mcp-stdio"))
        if response is not None:
            sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
            sys.stdout.flush()
