"""Flask route registration: UI shells, health, demo auth, registry-driven API, OpenAPI and MCP."""
from __future__ import annotations

from typing import Any, Callable

from flask import Blueprint, Flask, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from ..codec import CodecError, strict_loads
from ..runtime import Services
from .operations import REGISTRY, ApiResult, Operation, build_envelope, reason
from .schema import SchemaViolation, coerce_query, validate
from .security import DEMO_PERSONAS, ApiError, check_same_origin, csrf_token, current_context, verify_csrf


def _services() -> Services:
    return current_app.extensions["focus"]


def _is_api_path(path: str) -> bool:
    return path.startswith("/api/") or path.startswith("/mcp") or path.startswith("/health") or path.startswith("/auth/")


def error_response(status: int, code: str, message: str):  # type: ignore[no-untyped-def]
    ctx = getattr(g, "focus_ctx", None)
    rid = ctx.request_id if ctx is not None else "unknown"
    return jsonify({"error": {"code": code, "message": message, "request_id": rid}}), status


def invoke_operation(op: Operation, path_args: dict[str, Any], query_args: dict[str, str],
                     body_raw: bytes | None, *, transport: str = "rest") -> tuple[dict[str, Any], int]:
    """Authorize, validate and execute an operation; shared by REST and MCP transports."""
    ctx = current_context()
    services = _services()
    if op.grants:
        if not ctx.authenticated:
            raise ApiError(401, "UNAUTHENTICATED", "sign in through the configured identity provider")
        if not ctx.has_any(op.grants):
            raise ApiError(403, "FORBIDDEN", f"operation {op.name} requires one of {list(op.grants)}")
    args: dict[str, Any] = {}
    try:
        for name, schema in op.path_params.items():
            validate(schema, path_args.get(name), f"$.{name}")
            args[name] = path_args[name]
        args.update(coerce_query(op.query, query_args))
    except SchemaViolation as exc:
        raise ApiError(400, "INVALID_PARAMETER", str(exc)) from None
    if op.method == "POST":
        if body_raw is None:
            raise ApiError(400, "MISSING_BODY", "commands need a JSON body")
        try:
            body = strict_loads(body_raw)
        except CodecError as exc:
            raise ApiError(400, "BAD_JSON", exc.message) from None
        if not isinstance(body, dict):
            raise ApiError(422, "INVALID_BODY", "command body must be a JSON object")
        try:
            validate(op.body or {}, body)
        except SchemaViolation as exc:
            raise ApiError(422, "INVALID_BODY", str(exc)) from None
        clash = set(body) & set(args)
        if clash:
            raise ApiError(422, "INVALID_BODY", f"body repeats path/query parameters {sorted(clash)}")
        args.update(body)
    result: ApiResult = op.handler(ctx, args)
    status = result.http_status if result.http_status else op.success_status
    return build_envelope(result, services.clock.now()), status


def _make_view(op: Operation) -> Callable[..., Any]:
    def view(**path_args: Any):  # type: ignore[no-untyped-def]
        repeated = [key for key, values in request.args.lists() if len(values) > 1]
        if repeated:
            raise ApiError(400, "REPEATED_PARAMETER", f"query parameters may appear once: {repeated}")
        body_raw = None
        if op.method == "POST":
            check_same_origin()
            if request.mimetype != "application/json":
                raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "commands require Content-Type: application/json")
            if not verify_csrf(_services().settings, current_context(), request.headers.get("X-CSRF-Token")):
                raise ApiError(403, "CSRF_INVALID", "missing or invalid X-CSRF-Token; read it from GET /api/v1/context")
            body_raw = request.get_data(cache=False)
        envelope, status = invoke_operation(op, path_args, request.args.to_dict(flat=True), body_raw)
        return jsonify(envelope), status

    view.__name__ = f"api_{op.name}"
    return view


def register(app: Flask, services: Services) -> None:
    from . import api  # noqa: F401  (registers core operations)
    from . import api_views  # noqa: F401  (registers projection operations)
    from .openapi import build_openapi

    bp = Blueprint("focus", __name__)

    # ------------------------------------------------------------------ UI
    @bp.get("/")
    def landing():  # type: ignore[no-untyped-def]
        return render_template("landing.html", page="landing")

    @bp.get("/dashboard")
    def dashboard_ios():  # type: ignore[no-untyped-def]
        return render_template("workspace.html", layout="ios", page="workspace")

    @bp.get("/dashboard/classic")
    def dashboard_classic():  # type: ignore[no-untyped-def]
        return render_template("workspace.html", layout="classic", page="workspace")

    @bp.get("/api/docs")
    def api_docs():  # type: ignore[no-untyped-def]
        return render_template("api_docs.html", operations=REGISTRY.all(), page="api_docs")

    # -------------------------------------------------------------- health
    @bp.get("/health/live")
    def health_live():  # type: ignore[no-untyped-def]
        return jsonify({"status": "live", "service": "focus", "mode": services.settings.mode, "platform": services.settings.platform})

    @bp.get("/health/ready")
    def health_ready():  # type: ignore[no-untyped-def]
        from .api import plan_head_name, source_head_name

        checks: dict[str, Any] = {}
        reasons = []
        repos = services.repositories
        checks["repository"] = {"kind": repos.kind, "available": repos.available}
        if not repos.available:
            reasons.append(reason("REPOSITORY_UNAVAILABLE", "no repository configured"))
        checks["policy"] = services.policy_status()
        if checks["policy"]["status"] != "ready":
            reasons.append(reason("POLICY_UNAVAILABLE", checks["policy"].get("reason", "")))
        source = repos.head(source_head_name(services.settings.scope_key)) if repos.available else None
        checks["source_head"] = None if source is None else {"run_id": source.record_id, "version": source.version}
        if source is None:
            reasons.append(reason("NO_SOURCE_RUN", "no admitted source run for this scope"))
        plan = repos.head(plan_head_name(services.settings.scope_key, "EXECUTABLE")) if repos.available else None
        checks["executable_plan_head"] = None if plan is None else {"plan_id": plan.record_id, "version": plan.version}
        if plan is None:
            reasons.append(reason("NO_ACCEPTED_PLAN", "no accepted executable plan for this scope"))
        ready = not reasons
        body = {"status": "ready" if ready else "not_ready", "checks": checks, "reasons": reasons,
                "settings": services.settings.public_view()}
        return jsonify(body), (200 if ready else 503)

    # ----------------------------------------------------------- demo auth
    @bp.post("/auth/demo-login")
    def demo_login():  # type: ignore[no-untyped-def]
        if services.settings.identity_provider != "fixture":
            raise ApiError(404, "NOT_FOUND", "demo login is only available in fixture mode")
        check_same_origin()
        persona = request.form.get("persona") or (request.get_json(silent=True) or {}).get("persona")
        if persona not in DEMO_PERSONAS:
            raise ApiError(400, "UNKNOWN_PERSONA", "choose one of the listed demo personas")
        session.clear()
        session["persona"] = persona
        if request.mimetype == "application/json":
            return jsonify({"persona": persona, "demo": True})
        next_path = request.form.get("next") or ""
        if next_path.startswith("/") and not next_path.startswith("//"):
            target = (request.script_root or "") + next_path   # prefix-safe under Posit Connect
        else:
            target = url_for("focus.dashboard_ios")
        return redirect(target)

    @bp.post("/auth/logout")
    def logout():  # type: ignore[no-untyped-def]
        check_same_origin()
        session.clear()
        if request.mimetype == "application/json":
            return jsonify({"logged_out": True})
        return redirect(url_for("focus.landing"))

    # --------------------------------------------------------------- API
    @bp.get("/api/v1/openapi.json")
    def openapi():  # type: ignore[no-untyped-def]
        return jsonify(build_openapi(REGISTRY, server_url=request.script_root or "/"))

    for op in REGISTRY.all():
        bp.add_url_rule(op.flask_rule, endpoint=f"api_{op.name}", view_func=_make_view(op), methods=[op.method])

    if services.settings.mcp_enabled:
        from ..mcp.server import register_mcp

        register_mcp(bp, services)

    app.register_blueprint(bp)

    # ------------------------------------------------------------ errors
    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):  # type: ignore[no-untyped-def]
        if not _is_api_path(request.path) and exc.status in (401, 403, 404):
            return render_template("error.html", status=exc.status, code=exc.code, message=exc.message, page="error"), exc.status
        return error_response(exc.status, exc.code, exc.message)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):  # type: ignore[no-untyped-def]
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE", 400: "BAD_REQUEST"}.get(exc.code or 500, "HTTP_ERROR")
        if _is_api_path(request.path):
            return error_response(exc.code or 500, code, exc.description or code)
        return render_template("error.html", status=exc.code, code=code, message=exc.description, page="error"), exc.code or 500

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):  # type: ignore[no-untyped-def]
        app.logger.exception("unhandled error (request %s)", getattr(getattr(g, "focus_ctx", None), "request_id", "?"))
        if _is_api_path(request.path):
            return error_response(500, "INTERNAL_ERROR", "internal error; see server logs with the request id")
        return render_template("error.html", status=500, code="INTERNAL_ERROR", message="internal error", page="error"), 500

    from .views import MOBILE_TABS, SUPPLEMENTARY, VIEWS

    @app.context_processor
    def _inject() -> dict[str, Any]:
        ctx = getattr(g, "focus_ctx", None)
        return {
            "focus_ctx": ctx,
            "focus_settings": services.settings,
            "csrf_token": csrf_token(services.settings, ctx) if ctx is not None else None,
            "base_path": request.script_root or "",
            "demo_personas": DEMO_PERSONAS if services.settings.identity_provider == "fixture" else {},
            "focus_views": VIEWS + SUPPLEMENTARY,
            "focus_mobile_tabs": MOBILE_TABS,
        }
