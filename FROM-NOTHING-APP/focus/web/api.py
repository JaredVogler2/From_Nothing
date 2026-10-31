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
from .serializers import can_see_economics

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
    now = services.clock.now()
    shift: dict[str, Any] = {"now": now, "current_shift_id": None}
    evidence: dict[str, Any] | None = None
    if policy["status"] == "ready":
        from ..performance.actuals import RUNS, ObservationStore, freshness, observation_head
        from ..performance.lifecycle import containing_shift_id

        shift["current_shift_id"] = containing_shift_id(services.policy, now, settings.site)
        ohead = repos.head(observation_head(settings.scope_key)) if repos.available else None
        run = None if ohead is None else ObservationStore(repos, settings.scope_key).get(RUNS, ohead.record_id)
        if run is not None:
            evidence = {"observation_run_id": run.id, "as_of": run.as_of, "mode": run.mode,
                        "freshness": freshness(run, now, services.policy.int("observations.max_feed_age_hours")),
                        "feeds": {f.feed: {"status": f.status, "usable": f.usable, "covered_through": f.covered_through} for f in run.feeds}}
    choices: dict[str, Any] = {"teams": [], "lines": [], "obligation_dates": [], "default_date": None}
    if source is not None and policy["status"] == "ready" and ctx.authenticated:
        from .serializers import Canon, default_day

        canon = Canon.load(services)                         # read-only and cached per immutable snapshot
        if canon.snapshot is not None:
            real = {p.key.team for p in canon.snapshot.pools if p.executable_members}
            teams = {p.key.team for p in canon.snapshot.pools if ctx.scope.allows_team(p.key.team)}
            choices["teams"] = sorted(teams, key=lambda t: (t not in real, t))          # real teams first, virtual support last
            choices["lines"] = sorted(c.line for c in canon.snapshot.census)
            choices["default_date"] = default_day(canon) or canon.snapshot.cutoff.date()
            if canon.executable is not None:
                choices["obligation_dates"] = list(canon.executable.artifact.obligation_dates)
    data = {
        "choices": choices,
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
        "shift": shift,
        "evidence": evidence,
        "capabilities": {
            "forecast": source is not None,
            "executable_plan": bool(plans.get("EXECUTABLE")),
            "dispatch": bool(plans.get("EXECUTABLE")),
            "explanations": {"deterministic": True, "provider": settings.llm_provider},
            "solver": settings.solver_enabled,
            "mcp": settings.mcp_enabled,
            "observations": evidence is not None and evidence["freshness"] == "FRESH",
            "economics": can_see_economics(ctx),
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

def enqueue(ctx: RequestContext, kind: str, request_body: dict[str, Any], budget_seconds: float | None = None,
            server: dict[str, Any] | None = None) -> ApiResult:
    """Record a durable worker run (idempotent) and return an honest 202 queued status.

    ``server`` holds facts the server derives from trusted grants and scope (for example the
    excusal authority role). They are stored under ``_server``, which no client body may carry
    (every command schema forbids unknown fields), so a worker never trusts a client claim.
    """
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
            request=request_body if server is None else dict(request_body, _server=server),
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
    from ..data.acquisition import get_run

    services = svc()
    if not services.repositories.available:
        raise ApiError(503, "REPOSITORY_UNAVAILABLE", "commands need a configured repository")
    if args["policy_id"] != services.policy.id:
        raise ApiError(409, "POLICY_MISMATCH", f"the active policy is {services.policy.id}")
    found = get_run(services.repositories, args["source_run_id"])
    if found is None or found[0].scope != services.settings.scope_key:
        raise ApiError(404, "SOURCE_RUN_NOT_FOUND", "no admitted source run with that id in your scope")
    if found[0].status != "COMPLETE":
        raise ApiError(409, "SOURCE_RUN_NOT_COMPLETE", f"source run is {found[0].status}; only complete admitted runs are scheduled")
    return enqueue(ctx, "schedule", args, args["budget_seconds"])


def _scope_teams(ctx: RequestContext) -> Any:
    return "*" if ctx.scope.all_teams else sorted(ctx.scope.teams)


def _goal_record(ctx: RequestContext, goal_id: str) -> Any:
    """The goal of record behind a command, refused with the same 404 whether it is missing or outside the caller's scope."""
    from .serializers import registry

    record = registry(svc()).by_goal_id().get(goal_id)
    if record is None or not ctx.scope.allows_team(record.team):
        raise ApiError(404, "GOAL_NOT_FOUND", "no goal of record with that id in your scope")
    return record


NOTES = {"type": "string", "maxLength": 2000}
UNIT_IDS = {"type": "array", "minItems": 1, "maxItems": 200, "uniqueItems": True, "items": {"type": "string", "pattern": ID_PATTERN}}
EVIDENCE_IDS = {"type": "array", "maxItems": 50, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 160}}


@REGISTRY.operation(
    "create_amendment", "POST", "/api/v1/amendments",
    summary="Queue an approved feasible emergency addition to a frozen goal (B = B0 + additions)",
    description="Adds one whole open phase to a team-shift goal of record. expected_version is the goal's amendment count (from GET "
                "/shiftbook goal.versions.amendments). The worker re-checks the version, scope, readiness and crew feasibility; the original "
                "B0 and units never change.",
    grants=("team_lead", "manager"),
    read_only=False,
    body={
        "type": "object",
        "required": ["plan_id", "expected_version", "change", "reason", "idempotency_key"],
        "properties": {
            "plan_id": {"type": "string", "pattern": ID_PATTERN},
            "expected_version": {"type": "integer", "minimum": 0, "maximum": 100000},
            "change": {
                "type": "object",
                "required": ["kind", "team", "shift_id", "phase_id"],
                "properties": {
                    "kind": {"type": "string", "enum": ["ADD_EMERGENCY_WORK"]},
                    "team": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$"},
                    "shift_id": {"type": "string", "pattern": r"^[A-Za-z0-9._-]{1,32}:[0-9]{4}-[0-9]{2}-[0-9]{2}:S[1-3]$"},
                    "phase_id": {"type": "string", "pattern": ID_PATTERN},
                },
                "additionalProperties": False,
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_amendment(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    from .serializers import goal_versions, registry

    change = args["change"]
    if not ctx.scope.allows_team(change["team"]):
        raise ApiError(403, "OUT_OF_SCOPE", f"team {change['team']} is outside your data scope")      # scope before fetch
    goal_id = registry(svc()).goal_id_for(change["team"], change["shift_id"])
    if goal_id is None:
        raise ApiError(404, "GOAL_NOT_FOUND", "no accepted obligation plan covers that team-shift")
    current = goal_versions(svc(), goal_id)["amendments"]
    if args["expected_version"] != current:
        raise ApiError(409, "STALE_VERSION", f"the goal has {current} amendments, not {args['expected_version']}; reload and retry")
    return enqueue(ctx, "amendment", args, server={"teams": _scope_teams(ctx)})


@REGISTRY.operation(
    "create_disruption_claim", "POST", "/api/v1/disruption-claims",
    summary="Queue a disruption claim against missed frozen units of a goal (evidence, never relief)",
    description="A claim names one of the eight causes and the missed units it concerns. Relief exists only after a reviewed excusal decision; "
                "SAME_TEAM_PREDECESSOR and DURATION_OVERRUN need notes.",
    grants=("team_lead", "manager"),
    read_only=False,
    body={
        "type": "object",
        "required": ["goal_id", "unit_ids", "cause", "evidence_ids", "notes", "idempotency_key"],
        "properties": {
            "goal_id": {"type": "string", "pattern": ID_PATTERN},
            "unit_ids": UNIT_IDS,
            "cause": {"type": "string", "enum": ["LATE_PART", "CROSS_TEAM_PREDECESSOR", "CAPACITY_SHORTAGE", "SKILL_SHORTAGE", "REWORK_INJECTION",
                                                 "SAME_TEAM_PREDECESSOR", "DURATION_OVERRUN", "PLAN_CHURN"]},
            "evidence_ids": EVIDENCE_IDS,
            "notes": NOTES,
            "origin_team": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$"},
            "executing_team": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$"},
            "supersedes": {"type": "string", "pattern": ID_PATTERN},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_disruption_claim(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _goal_record(ctx, args["goal_id"])
    return enqueue(ctx, "disruption_claim", args, server={"teams": _scope_teams(ctx)})


@REGISTRY.operation(
    "create_excusal_decision", "POST", "/api/v1/excusal-decisions",
    summary="Queue a reviewed excusal decision on submitted claims of one goal",
    description="Only the policy's reviewer role decides, never on one's own claim. expected_version is the goal's decision count (from GET "
                "/disruption-claims). PLAN_CHURN is reviewed on its own track; approving a non-nominal cause is a recorded exception.",
    grants=("excusal_decider",),
    read_only=False,
    body={
        "type": "object",
        "required": ["claim_ids", "decision", "unit_ids", "notes", "expected_version", "idempotency_key"],
        "properties": {
            "claim_ids": {"type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True, "items": {"type": "string", "pattern": ID_PATTERN}},
            "decision": {"type": "string", "enum": ["APPROVED", "REJECTED", "PENDING"]},
            "unit_ids": {"type": "array", "maxItems": 200, "uniqueItems": True, "items": {"type": "string", "pattern": ID_PATTERN}},
            "notes": NOTES,
            "expected_version": {"type": "integer", "minimum": 0, "maximum": 100000},
            "review_track": {"type": "string", "enum": ["STANDARD", "PLAN_CHURN"]},
            "supersedes": {"type": "string", "pattern": ID_PATTERN},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_excusal_decision(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    from ..performance.actuals import CLAIMS, DECISIONS, ObservationStore, knowledge_time

    services = svc()
    store = ObservationStore(services.repositories, services.settings.scope_key)
    claims = [store.get(CLAIMS, cid) for cid in args["claim_ids"]]
    goals = {c.goal_id for c in claims if c is not None}
    if None in claims or len(goals) != 1:
        raise ApiError(404, "CLAIM_NOT_FOUND", "every reviewed claim must exist and belong to one goal in your scope")
    goal_id = next(iter(goals))
    _goal_record(ctx, goal_id)
    if any(c.reported_by == ctx.subject for c in claims) and not services.policy.bool("disruption.self_review_allowed"):
        raise ApiError(403, "SELF_REVIEW", "a claimant cannot decide their own claim")
    current = sum(1 for d in store.known(DECISIONS, knowledge_time(services)) if d.goal_id == goal_id)
    if args["expected_version"] != current:
        raise ApiError(409, "STALE_VERSION", f"the goal's decision state is at version {current}, not {args['expected_version']}; reload and retry")
    roles = [g for g in services.policy.seq("disruption.reviewer_roles") if g in ctx.grants]
    return enqueue(ctx, "excusal_decision", args, server={"authority_role": roles[0] if roles else "", "teams": _scope_teams(ctx)})


@REGISTRY.operation(
    "create_ie_review", "POST", "/api/v1/ie-reviews",
    summary="Queue an IE standard/role/data review proposal",
    description="Records a proposal with provenance. Issued goals, frozen units and evaluations never change; a correction reaches only "
                "future plans through a new policy or snapshot.",
    grants=("IE",),
    read_only=False,
    body={
        "type": "object",
        "required": ["job_id", "standard_revision", "proposed_correction", "reason", "idempotency_key"],
        "properties": {
            "job_id": {"type": "string", "pattern": ID_PATTERN},
            "standard_revision": {"type": "string", "minLength": 1, "maxLength": 160},
            "proposed_correction": {"type": "string", "minLength": 1, "maxLength": 2000},
            "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_ie_review(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return enqueue(ctx, "ie_review", args)


@REGISTRY.operation(
    "create_reported_observation", "POST", "/api/v1/reported-observations",
    summary="Queue a reported observation: a claim, never source-verified completion",
    description="Records what a person reports (for example a completion) as REPORTED evidence. It never earns credit; only a source-verified "
                "completion does.",
    grants=("mechanic", "team_lead"),
    read_only=False,
    body={
        "type": "object",
        "required": ["job_id", "kind", "effective_at", "notes", "idempotency_key"],
        "properties": {
            "job_id": {"type": "string", "pattern": ID_PATTERN},
            "phase_id": {"type": "string", "pattern": ID_PATTERN},
            "kind": {"type": "string", "enum": ["COMPLETED"]},
            "effective_at": {"type": "string", "format": "date-time"},
            "notes": {"type": "string", "maxLength": 400},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_reported_observation(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    from .serializers import Canon, phase_team

    canon = Canon.load(svc())
    job = canon.jobs().get(args["job_id"])
    phase = canon.phases().get(args.get("phase_id") or "")
    team = phase_team(phase) if phase is not None and phase.job == args["job_id"] else (job.team if job is not None else None)
    if job is None or not ctx.scope.allows_team(team):
        # the same 404 whether the job is missing or outside the caller's scope: no existence leak across teams
        raise ApiError(404, "JOB_NOT_FOUND", "no job with that id in your scope")
    return enqueue(ctx, "reported_observation", args)


SCENARIO_CHANGE = {
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {"type": "string", "enum": ["virtual_staffing", "eta_forecast", "duration_statistic", "daily_cost", "calendar_day",
                                            "travel_minutes", "task_priority"]},
        "team": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$"},
        "date": {"type": "string", "format": "date"},
        "shift": {"type": "integer", "minimum": 1, "maximum": 3},
        "add": {"type": "integer", "minimum": 1, "maximum": 10},
        "release_id": {"type": "string", "pattern": ID_PATTERN},
        "forecast_at": {"type": "string", "format": "date-time"},
        "statistic": {"type": "string", "enum": ["P50", "P75", "P90"]},
        "line": {"type": "integer", "minimum": 1, "maximum": 999999},
        "daily_rate_cents": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
        "status": {"type": "string", "enum": ["WORKING", "NONWORKING"]},
        "changed_aircraft_minutes": {"type": "integer", "minimum": 0, "maximum": 240},
        "job_id": {"type": "string", "pattern": ID_PATTERN},
        "rank": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "additionalProperties": False,
}


@REGISTRY.operation(
    "create_scenario", "POST", "/api/v1/scenarios",
    summary="Queue an isolated what-if scenario on a copy of an admitted snapshot",
    description="Typed, allowlisted changes (virtual staffing, ETA forecast, duration statistic, assumed daily cost, calendar day, "
                "changed-aircraft travel minutes, planned task priority) run with the same optimizer and validator against a same-settings "
                "baseline under one bounded budget. Results are SCENARIO_ONLY: never publishable, and no source/plan head, frozen goal, "
                "commitment, actual or evaluation changes. A running scenario can be cancelled cooperatively.",
    grants=("scenario_planner", "manager"),
    read_only=False,
    body={
        "type": "object",
        "required": ["source_run_id", "changes", "budget_seconds", "idempotency_key"],
        "properties": {
            "source_run_id": {"type": "string", "pattern": ID_PATTERN},
            "changes": {"type": "array", "minItems": 1, "maxItems": 20, "items": SCENARIO_CHANGE},
            "budget_seconds": {"type": "number", "minimum": 1, "maximum": 600},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_scenario(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    from ..analytics.scenarios import normalize_changes
    from ..data.acquisition import get_run
    from ..domain import DomainError

    services = svc()
    try:
        normalize_changes(args["changes"])
    except DomainError as exc:
        raise ApiError(422, exc.code, exc.message) from None
    found = get_run(services.repositories, args["source_run_id"])
    if found is None or found[0].scope != services.settings.scope_key or found[0].status != "COMPLETE":
        raise ApiError(404, "SOURCE_RUN_NOT_FOUND", "no complete admitted source run with that id in your scope")
    return enqueue(ctx, "scenario", args, args["budget_seconds"])


@REGISTRY.operation(
    "create_explanation", "POST", "/api/v1/explanations",
    summary="Queue a grounded explanation of ranked actions of one team-shift",
    description="Candidates are actions of the current accepted EXECUTABLE plan in your scope, all of one team-shift. The worker ranks "
                "them with the dispatch rule, builds privacy-minimized fact rows, asks the configured provider (default none) and validates "
                "its complete schema, every claim and the narrative; anything invalid returns the complete deterministic explanation in "
                "the same rank. Explanations never change ranks, crews, dates, qualifications or plans.",
    grants=ALL_READERS,
    read_only=False,
    body={
        "type": "object",
        "required": ["plan_id", "candidate_ids", "idempotency_key"],
        "properties": {
            "plan_id": {"type": "string", "pattern": ID_PATTERN},
            "candidate_ids": {"type": "array", "minItems": 1, "maxItems": 20, "uniqueItems": True, "items": {"type": "string", "pattern": ID_PATTERN}},
            "locale": {"type": "string", "enum": ["en-US"]},
            "idempotency_key": IDEMPOTENCY,
        },
        "additionalProperties": False,
    },
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
    success_status=202,
)
def create_explanation(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    from ..domain import DomainError
    from ..explanations.pipeline import current_inputs
    from .serializers import can_see_economics

    services = svc()
    if len(args["candidate_ids"]) > services.policy.int("explanations.max_candidates"):
        raise ApiError(422, "TOO_MANY_CANDIDATES", f"at most {services.policy.int('explanations.max_candidates')} candidates per request")
    try:
        snapshot, current = current_inputs(services, args["plan_id"])
    except DomainError as exc:
        raise ApiError(409, exc.code, exc.message) from None
    phases = {p.key.id: p for p in snapshot.phases}
    actions = {a.id: a for a in current.artifact.candidate.assignments}
    team_shifts = set()
    for cid in args["candidate_ids"]:
        a = actions.get(cid)
        p = phases.get(a.phase) if a is not None else None
        team = None if p is None else (p.role_demands[0].team if p.role_demands else p.team)
        if a is None or team is None or not ctx.scope.allows_team(team):
            raise ApiError(404, "CANDIDATE_NOT_FOUND", "no action with that id in your scope")      # outside the scope is "not found"
        team_shifts.add((team, a.shift_id))
    if len(team_shifts) != 1:
        raise ApiError(422, "MIXED_TEAM_SHIFT", "explain the actions of one team-shift per request (they share one ranking)")
    (team, shift_id), = team_shifts
    economics = can_see_economics(ctx)
    privacy = ("all_teams" if ctx.scope.all_teams else "team") + ("+economics" if economics else "")
    return enqueue(ctx, "explanation", args, None, server={"team": team, "shift_id": shift_id, "economics": economics, "privacy_scope": privacy})


@REGISTRY.operation(
    "cancel_run", "POST", "/api/v1/runs/{run_id}/cancel",
    summary="Cancel a queued or running worker run (requester or manager)",
    description="A queued run becomes cancelled and is never executed. A running run gets a durable cancel request: handlers that stop "
                "cooperatively (scenarios) end it as cancelled without a result; others finish within their budget. A finished run is not "
                "changed (409). Cancelling is idempotent and never touches accepted plans, goals, commitments, actuals or evaluations.",
    grants=ALL_READERS,
    read_only=False,
    path_params={"run_id": {"type": "string", "pattern": ID_PATTERN}},
    body={"type": "object", "properties": {"reason": {"type": "string", "maxLength": 200}}, "additionalProperties": False},
    data_schema=RUN_SCHEMA,
    tags=("commands",),
    mcp_tool=False,
)
def cancel_run(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    from ..data.store import VersionConflict

    services = svc()
    repos = services.repositories
    run = repos.get_worker_run(args["run_id"])
    if run is None or run.scope != services.settings.scope_key or (run.requested_by != ctx.subject and not ctx.has_any(("manager",))):
        raise ApiError(404, "RUN_NOT_FOUND", "no worker run with that id in your scope")
    if run.status == "cancelled":
        return ApiResult(status="ready", data=_run_view(run) | {"replayed": True})
    if run.status == "running":
        from ..workers import request_cancel

        fresh = request_cancel(services, run, by=ctx.subject or "unknown", reason=args.get("reason") or "cancelled by request",
                               at=services.clock.now())
        return ApiResult(status="ready", data=_run_view(run) | {"cancel_requested": True, "replayed": not fresh})
    if run.status != "queued":
        raise ApiError(409, "RUN_NOT_CANCELLABLE", f"the run is {run.status}; only queued or running runs are cancelled")
    try:
        done = repos.update_worker_run(run, repos.worker_run_version(run.id), status="cancelled",
                                       error={"code": "CANCELLED", "message": (args.get("reason") or "cancelled by request")[:200]})
    except VersionConflict:
        raise ApiError(409, "RUN_NOT_CANCELLABLE", "the run changed while cancelling; reload it") from None
    return ApiResult(status="ready", data=_run_view(done) | {"replayed": False})
