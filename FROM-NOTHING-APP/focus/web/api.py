"""API operation handlers registered in the operation registry.

Handlers are pure read projections over canonical repositories (GET) or
command enqueuers (POST -> durable worker run, HTTP 202). They never acquire
sources, optimize, sign, promote or grade inline.
"""
from __future__ import annotations

from typing import Any

from flask import current_app

from ..data.repositories import RepositoryError
from ..data.store import StoreError, StoreUnavailable
from ..runtime import Services
from .operations import REGISTRY, ApiResult, reason
from .security import DEMO_PERSONAS, ApiError, RequestContext, csrf_token

ALL_READERS = ("reader", "team_lead", "mechanic", "IE", "supply_chain", "QA", "excusal_decider", "manager", "scenario_planner", "source_worker", "signer")


def svc() -> Services:
    return current_app.extensions["focus"]


def source_head_name(scope: str) -> str:
    return f"source/{scope}"


def plan_head_name(scope: str, purpose: str) -> str:
    return f"plan/{scope}/{purpose}"


ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$"
IDEMPOTENCY = {"type": "string", "pattern": r"^[A-Za-z0-9._:-]{8,128}$", "description": "Client-chosen idempotency key."}


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

@REGISTRY.operation(
    "get_context", "GET", "/api/v1/context",
    summary="Trusted identity, scope, current source/plan heads and capabilities",
    description="Returns the caller's server-owned grants and data scope, the current source and plan heads, policy status, "
                "capabilities and a CSRF token for subsequent commands. Safe to call without data: it reports unknown states.",
    grants=(),
    read_only=True,
    tags=("context",),
    mcp_resource="focus://context",
)
def get_context(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    services = svc()
    settings = services.settings
    repos = services.repositories
    reasons: list[dict[str, Any]] = []
    source = None
    plans: dict[str, Any] = {}
    if repos.available:
        head = repos.head(source_head_name(settings.scope_key))
        if head:
            source = {"run_id": head.record_id, "head_version": head.version, "updated_at": head.updated_at}
        else:
            reasons.append(reason("NO_SOURCE_RUN", "No admitted source run exists for this scope.",
                                  "Run `python -m focus.cli demo-run` (fixture) or a source acquisition worker (live)."))
        for purpose in ("EXECUTABLE", "FORECAST"):
            phead = repos.head(plan_head_name(settings.scope_key, purpose))
            plans[purpose] = None if phead is None else {"plan_id": phead.record_id, "head_version": phead.version, "updated_at": phead.updated_at}
    else:
        reasons.append(reason("REPOSITORY_UNAVAILABLE", "No repository is configured; the application is in a typed no-data state.",
                              "Configure FOCUS_SQL_* (live) or use FOCUS_MODE=fixture."))
    policy = services.policy_status()
    if policy["status"] != "ready":
        reasons.append(reason("POLICY_UNAVAILABLE", policy.get("reason", "policy unavailable")))
    data = {
        "identity": {
            "authenticated": ctx.authenticated,
            "subject": ctx.subject,
            "display_name": ctx.display_name,
            "provider": ctx.provider,
            "demo": ctx.demo,
            "persona": ctx.persona,
            "grants": sorted(ctx.grants),
        },
        "scope": ctx.scope.describe(),
        "csrf_token": csrf_token(settings, ctx),
        "mode": settings.mode,
        "platform": settings.platform,
        "scope_key": settings.scope_key,
        "source": source,
        "plans": plans,
        "policy": policy,
        "capabilities": {
            "forecast": source is not None,
            "executable_plan": bool(plans.get("EXECUTABLE")),
            "dispatch": bool(plans.get("EXECUTABLE")),
            "explanations": {"deterministic": True, "provider": settings.llm_provider},
            "solver": settings.solver_enabled,
            "mcp": settings.mcp_enabled,
        },
        "demo_personas": [
            {"id": key, "display": value["display"], "grants": list(value["grants"])}
            for key, value in DEMO_PERSONAS.items()
        ] if settings.identity_provider == "fixture" else [],
    }
    status = "ready" if source is not None and policy["status"] == "ready" else ("unknown" if repos.available else "blocked")
    return ApiResult(status=status, data=data, source_run_id=source["run_id"] if source else None,
                     plan_id=(plans.get("EXECUTABLE") or {}).get("plan_id") if plans else None,
                     policy_id=policy.get("policy_id"), reasons=reasons)


# ---------------------------------------------------------------------------
# worker runs
# ---------------------------------------------------------------------------

RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string"},
        "kind": {"type": "string"},
        "status": {"type": "string", "enum": ["queued", "running", "failed", "completed", "cancelled"]},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "attempts": {"type": "integer"},
        "result_receipt": {"type": ["object", "null"]},
        "error": {"type": ["object", "null"]},
    },
}


def _run_view(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "kind": run.kind,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "attempts": run.attempts,
        "budget_seconds": run.budget_seconds,
        "result_receipt": run.result_receipt,
        "error": run.error,
    }


@REGISTRY.operation(
    "get_run", "GET", "/api/v1/runs/{run_id}",
    summary="Worker run status and durable result receipt",
    description="queued/running/failed/completed/cancelled plus the durable result receipt. A queued run is not a successful schedule.",
    grants=ALL_READERS,
    read_only=True,
    path_params={"run_id": {"type": "string", "pattern": ID_PATTERN}},
    data_schema=RUN_SCHEMA,
    tags=("runs",),
    mcp_resource="focus://runs/{run_id}",
)
def get_run(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    services = svc()
    run = services.repositories.get_worker_run(args["run_id"])
    if run is None or run.scope != services.settings.scope_key:
        raise ApiError(404, "RUN_NOT_FOUND", "no worker run with that id in your scope")
    if run.requested_by != ctx.subject and not ctx.has_any(("manager", "source_worker", "signer")):
        raise ApiError(404, "RUN_NOT_FOUND", "no worker run with that id in your scope")
    return ApiResult(status="ready", data=_run_view(run))


# ---------------------------------------------------------------------------
# commands (POST -> 202 worker run)
# ---------------------------------------------------------------------------

def enqueue(ctx: RequestContext, kind: str, request_body: dict[str, Any], budget_seconds: float | None = None) -> ApiResult:
    services = svc()
    if not services.repositories.available:
        raise ApiError(503, "REPOSITORY_UNAVAILABLE", "commands need a configured repository")
    idem = request_body["idempotency_key"]
    budget = float(budget_seconds if budget_seconds is not None else services.settings.worker_budget_seconds)
    budget = max(0.1, min(budget, services.settings.worker_budget_seconds * 4))
    try:
        run, created = services.repositories.create_worker_run(
            kind=kind,
            scope=services.settings.scope_key,
            idempotency_key=idem,
            requested_by=ctx.subject or "anonymous",
            request=request_body,
            budget_seconds=budget,
        )
    except RepositoryError as exc:
        if exc.code == "IDEMPOTENCY_CONFLICT":
            raise ApiError(409, exc.code, exc.message) from None
        raise ApiError(500, exc.code, "worker run could not be recorded") from None
    except StoreUnavailable:
        raise ApiError(503, "REPOSITORY_UNAVAILABLE", "commands need a configured repository") from None
    except StoreError as exc:
        raise ApiError(503, exc.code, "worker run storage is busy; retry with the same idempotency key") from None
    if created:
        from ..workers import maybe_start_inprocess_worker

        worker = maybe_start_inprocess_worker(services)  # lazy: never at import
        if worker is not None:
            worker.notify()
    data = _run_view(run) | {"replayed": not created}
    return ApiResult(status="ready", data=data, http_status=202,
                     reasons=[reason("QUEUED", "Accepted for background processing; poll /api/v1/runs/{run_id}.")])


@REGISTRY.operation(
    "create_schedule_run", "POST", "/api/v1/schedule-runs",
    summary="Queue a named rich-MAX schedule run for an admitted source run",
    description="Queues the MAX optimizer for the given admitted source run and policy. Returns 202 with a run id. "
                "Signing and promotion stay in the separate trusted publication worker.",
    grants=("manager", "source_worker", "scenario_planner"),
    read_only=False,
    body={
        "type": "object",
        "required": ["source_run_id", "policy_id", "budget_seconds", "idempotency_key"],
        "properties": {
            "source_run_id": {"type": "string", "pattern": ID_PATTERN},
            "policy_id": {"type": "string", "pattern": ID_PATTERN},
            "budget_seconds": {"type": "number", "minimum": 0.1, "maximum": 600},
            "purpose": {"type": "string", "enum": ["FORECAST", "EXECUTABLE"]},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_schedule_run(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return enqueue(ctx, "schedule", args, args["budget_seconds"])
