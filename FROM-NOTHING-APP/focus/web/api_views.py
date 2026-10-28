"""Read-projection operations (S15 READ ENDPOINTS): fleet, jobs, plans, shift book, my day, workforce, ...

Each operation reads immutable snapshots, verified accepted plans and persisted evaluations through
:mod:`focus.web.serializers` and never computes a second scheduling, qualification or scoring
authority. Explicit team or person parameters are checked against the caller's server-owned scope
*before* any repository read (scope before fetch). Every handler is GET-only and read-only: it never
acquires, solves, validates, signs, grades or writes.
"""
from __future__ import annotations

from typing import Any, Callable

from ..codec import content_hash
from ..data.store import StoreUnavailable
from ..policy import PolicyError
from . import serializers as S
from .api import ALL_READERS, ID_PATTERN, svc
from .operations import PAGE_QUERY, REGISTRY, ApiResult, decode_cursor, encode_cursor
from .security import ApiError, RequestContext

ECONOMICS_READERS = S.ECONOMICS_GRANTS
PERFORMANCE_READERS = ALL_READERS
TEAM = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$", "description": "Team id, e.g. PROD-A."}
DAY = {"type": "string", "format": "date", "description": "Shift start date (local), YYYY-MM-DD."}
SHIFT_NUMBER = {"type": "integer", "minimum": 1, "maximum": 3, "description": "Shift number 1, 2 or 3."}
SHIFT_ID = {"type": "string", "pattern": r"^[A-Za-z0-9._-]{1,32}:[0-9]{4}-[0-9]{2}-[0-9]{2}:S[1-3]$", "description": "Shift id SITE:YYYY-MM-DD:S1."}
PERSON = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", "description": "Native person key (badge/BEMS or fixture id)."}
DISPOSITIONS = ["admitted", "scheduled", "blocked", "quarantined", "excluded", "verified_complete", "conditional", "deferred"]


def _query(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "additionalProperties": False}


def _result(services: Any, p: S.Projection) -> ApiResult:
    try:
        policy_id = services.policy.id
    except PolicyError:
        policy_id = None
    return ApiResult(status=p.status, data=p.data, source_run_id=p.source_run_id, plan_id=p.plan_id, policy_id=policy_id,
                     evaluation_id=p.evaluation_id, purpose=p.purpose, coverage=p.coverage, reasons=list(p.reasons), next_cursor=p.next_cursor)


def _read(build: Callable[[Any], S.Projection]) -> ApiResult:
    services = svc()
    try:
        return _result(services, build(services))
    except StoreUnavailable:
        raise ApiError(503, "REPOSITORY_UNAVAILABLE", "the repository is unavailable; retry later") from None


def _canon(services: Any) -> S.Canon:
    return S.Canon.load(services)


def _snapshot_read(op: str, ctx: RequestContext, filters: dict[str, Any], build: Callable[[S.Canon], S.Projection]) -> ApiResult:
    """A snapshot projection cached by authorized scope, filters and canonical revision."""
    def run(services: Any) -> S.Projection:
        canon = _canon(services)
        return S.cached(op, ctx, canon, filters, lambda: build(canon))

    return _read(run)


# ---------------------------------------------------------------------- fleet, aircraft, jobs

@REGISTRY.operation(
    "get_fleet", "GET", "/api/v1/fleet",
    summary="Whole-WIP fleet census with forecast completion, CS gate, cost and uncertainty",
    description="One row per aircraft line in the admitted census: status counts, admitted/blocked/quarantined/excluded jobs, CS gate facts, "
                "forecast and executable completion (known/conditional/unknown) and modeled cost (economics grants only).",
    grants=ALL_READERS, read_only=True, tags=("fleet",), mcp_resource="focus://fleet",
)
def get_fleet(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _snapshot_read("fleet", ctx, {}, lambda canon: S.fleet(canon, ctx))


@REGISTRY.operation(
    "get_aircraft", "GET", "/api/v1/aircraft/{line}",
    summary="One aircraft: census, jobs in your scope, CS gate, calendar, completion, cost and shortages",
    description="Aircraft detail for one line. Jobs of teams outside your scope are counted but not listed.",
    grants=ALL_READERS, read_only=True, path_params={"line": {"type": "string", "pattern": r"^[1-9][0-9]{0,5}$"}},
    tags=("fleet",), mcp_resource="focus://aircraft/{line}",
)
def get_aircraft(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    line = int(args["line"])
    return _snapshot_read("aircraft", ctx, {"line": line}, lambda canon: S.aircraft(canon, ctx, line))


@REGISTRY.operation(
    "list_jobs", "GET", "/api/v1/jobs",
    summary="Jobs in your scope with disposition and reasons (paginated)",
    description="Admitted jobs filtered by line, team and disposition. Pages use opaque signed cursors bound to your scope, the filters and "
                "the source/plan revision; a new revision invalidates them (409 CURSOR_STALE).",
    grants=ALL_READERS, read_only=True,
    query=_query(line={"type": "integer", "minimum": 1, "maximum": 999999}, team=TEAM, disposition={"type": "string", "enum": DISPOSITIONS}, **PAGE_QUERY),
    tags=("jobs",),
)
def list_jobs(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    services = svc()
    filters = {k: args[k] for k in ("line", "team", "disposition") if k in args}
    if "team" in filters:
        S.require_team(ctx, filters["team"])                                    # scope before fetch
    policy = services.policy
    limit = min(int(args.get("limit", policy.int("api.page_default"))), policy.int("api.page_max"))
    try:
        canon = _canon(services)
    except StoreUnavailable:
        raise ApiError(503, "REPOSITORY_UNAVAILABLE", "the repository is unavailable; retry later") from None
    proj, rows = S.cached("jobs", ctx, canon, filters, lambda: S.jobs_list(canon, ctx, line=filters.get("line"), team=filters.get("team"),
                                                                          disposition=filters.get("disposition")))
    version = content_hash(list(map(repr, canon.revision)))
    secret = services.settings.session_secret.reveal()
    offset = decode_cursor(secret, ctx, "list_jobs", filters, version, args["cursor"]) if "cursor" in args else 0
    page = rows[offset:offset + limit]
    more = offset + limit < len(rows)
    out = S.Projection(proj.status, {"jobs": page, "total": len(rows), "offset": offset, "limit": limit} if canon.snapshot is not None else None,
                       list(proj.reasons), proj.coverage, proj.source_run_id,
                       next_cursor=encode_cursor(secret, ctx, "list_jobs", filters, version, offset + limit) if more else None)
    return _result(services, out)


@REGISTRY.operation(
    "get_job", "GET", "/api/v1/jobs/{job_id}",
    summary="Exact phases, crews, certificates, duration, graph, parts, QA and readiness reasons of one job",
    description="Job detail: phases with role demands and certificate groups, typed hard edges, releases, inspection trigger, duration basis, "
                "shortages and the assignments of the accepted plans. People outside your scope are redacted; a job outside it is not found.",
    grants=ALL_READERS, read_only=True, path_params={"job_id": {"type": "string", "pattern": ID_PATTERN}},
    tags=("jobs",), mcp_resource="focus://jobs/{job_id}",
)
def get_job(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    job_id = args["job_id"]
    return _snapshot_read("job", ctx, {"job": job_id}, lambda canon: S.job_detail(canon, ctx, job_id))


@REGISTRY.operation(
    "get_plan", "GET", "/api/v1/plans/{plan_id}",
    summary="An immutable accepted, historical or candidate plan with its purpose and capability",
    description="Accepted plans are verified end to end (payload hash, signature, bindings) on read; history is labeled stale and a candidate "
                "is never executable.",
    grants=ALL_READERS, read_only=True, path_params={"plan_id": {"type": "string", "pattern": ID_PATTERN}},
    tags=("plans",), mcp_resource="focus://plans/{plan_id}",
)
def get_plan(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.plan_view(services, ctx, args["plan_id"]))


# ---------------------------------------------------------------------- shift book and my day

@REGISTRY.operation(
    "get_shiftbook", "GET", "/api/v1/shiftbook",
    summary="One team's shift: jobs and full crews in chronological order, with its goal of record",
    description="The accepted EXECUTABLE plan's assignments for a team, start date and shift, with full named crews, the frozen goal of "
                "record (B0, units, optimistic versions) and its canonical evaluation. The team must be in your scope.",
    grants=ALL_READERS, read_only=True, query=_query(team=TEAM, date=DAY, shift=SHIFT_NUMBER), tags=("operate",),
)
def get_shiftbook(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    team = S.default_team(ctx, args.get("team"))                                # scope before fetch
    day = S.parse_day(args.get("date"))
    return _read(lambda services: S.shiftbook(_canon(services), ctx, team=team, day=day, shift=int(args.get("shift", 1))))


@REGISTRY.operation(
    "get_myday", "GET", "/api/v1/myday",
    summary="One person's ordered WORK/TRAVEL/WAIT/TOOL/OT duties, crewmates and readiness",
    description="A mechanic opens only their own day; leads and managers open people in their scope. Duties are planned assignments, never "
                "check-offs.",
    grants=ALL_READERS, read_only=True, query=_query(person=PERSON, date=DAY), tags=("operate",),
)
def get_myday(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    person = S.myday_person(ctx, args.get("person"))                           # scope before fetch
    day = S.parse_day(args.get("date"))
    return _read(lambda services: S.myday(_canon(services), ctx, person_native=person, day=day))


# ---------------------------------------------------------------------- workforce and capacity

@REGISTRY.operation(
    "get_workforce", "GET", "/api/v1/workforce",
    summary="Pure pools by team and shift: denominators, activation caps, members and availability",
    description="Pools in your scope for a date (default: the obligation day) and optional shift, with executable versus forecast members.",
    grants=ALL_READERS, read_only=True, query=_query(date=DAY, shift=SHIFT_NUMBER), tags=("staffing",),
)
def get_workforce(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    day = S.parse_day(args.get("date"))
    shift = args.get("shift")
    return _snapshot_read("workforce", ctx, {"date": day, "shift": shift}, lambda canon: S.workforce(canon, ctx, day=day, shift=shift))


@REGISTRY.operation(
    "get_capacity", "GET", "/api/v1/capacity",
    summary="Activation against the cap, utilization against capacity, and unscheduled bottleneck reasons",
    description="Per pool: distinct activated people against the activation cap, planned WORK/TRAVEL/other minutes against regular capacity "
                "(utilization is not activation), OT minutes and planned person-minutes; plus unscheduled reason codes.",
    grants=ALL_READERS, read_only=True, query=_query(date=DAY, purpose={"type": "string", "enum": ["EXECUTABLE", "FORECAST"]}), tags=("staffing",),
)
def get_capacity(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    day = S.parse_day(args.get("date"))
    purpose = args.get("purpose", "EXECUTABLE")
    return _snapshot_read("capacity", ctx, {"date": day, "purpose": purpose}, lambda canon: S.capacity(canon, ctx, day=day, purpose=purpose))


# ---------------------------------------------------------------------- commitments, economics, shortages, quality

@REGISTRY.operation(
    "get_commitments", "GET", "/api/v1/commitments",
    summary="Delivery dates (original/current/raw/pending), the change ledger and task-commitment outcomes",
    description="Per-aircraft delivery commitment state with acknowledged change events, plus the three-day task commitment defense outcomes "
                "of the accepted EXECUTABLE plan for work in your scope.",
    grants=ALL_READERS, read_only=True, tags=("plan",),
)
def get_commitments(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.commitments(_canon(services), ctx))


@REGISTRY.operation(
    "get_economics", "GET", "/api/v1/economics",
    summary="Known, conditional and unknown cost totals, per-line cost and contract terms",
    description="Restricted to economics grants. Unknown liability stays unknown, never $0; terms show assumed versus actual authority.",
    grants=ECONOMICS_READERS, read_only=True, tags=("plan",),
)
def get_economics(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _snapshot_read("economics", ctx, {}, lambda canon: S.economics(canon, ctx))


@REGISTRY.operation(
    "get_shortages", "GET", "/api/v1/shortages",
    summary="Material shortages: used-on, root and descendant impact, ECD versus confirmed receipt",
    description="Every admitted shortage with its root job, affected jobs and part release status.",
    grants=ALL_READERS, read_only=True, tags=("supply",),
)
def get_shortages(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _snapshot_read("shortages", ctx, {}, lambda canon: S.shortages(canon, ctx))


@REGISTRY.operation(
    "get_quality", "GET", "/api/v1/quality",
    summary="All sourced inspection holds (in-process, final, other buyoffs) and inspector capacity",
    description="QA phases with their assignment and inspectors, blocked reasons and QA pool capacity by shift.",
    grants=ALL_READERS, read_only=True, tags=("quality",),
)
def get_quality(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _snapshot_read("quality", ctx, {}, lambda canon: S.quality(canon, ctx))


# ---------------------------------------------------------------------- performance, history, recognition, progression

@REGISTRY.operation(
    "get_performance", "GET", "/api/v1/performance",
    summary="The canonical frozen-unit evaluation per goal and the pooled view over your scope",
    description="Each goal of record with its persisted current evaluation (B0/B/E/Cp/U/O/base/final/grade) and evidence counts. The pooled "
                "view sums exact components before division and names a stored aggregate when one matches.",
    grants=PERFORMANCE_READERS, read_only=True, query=_query(shift_id=SHIFT_ID, team=TEAM), tags=("performance",),
    mcp_resource="focus://performance",
)
def get_performance(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    if "team" in args:
        S.require_team(ctx, args["team"])                                       # scope before fetch
    return _read(lambda services: S.performance(services, ctx, shift_id=args.get("shift_id"), team=args.get("team")))


@REGISTRY.operation(
    "get_performance_history", "GET", "/api/v1/performance/history",
    summary="Every evaluation revision per goal in your scope (append-only)",
    description="Revision chains oldest first; a correction appends a revision and never rewrites an earlier one.",
    grants=PERFORMANCE_READERS, read_only=True, query=_query(team=TEAM), tags=("performance",),
)
def get_performance_history(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    if "team" in args:
        S.require_team(ctx, args["team"])
    return _read(lambda services: S.performance_history(services, ctx, team=args.get("team")))


@REGISTRY.operation(
    "get_recognition", "GET", "/api/v1/recognition",
    summary="Recognition events with frozen value, gate context and earning lineage",
    description="Supplementary progression events (completion, recovery, unlock, commitment, badge, correction) for teams in your scope.",
    grants=PERFORMANCE_READERS, read_only=True, query=_query(team=TEAM), tags=("performance",),
)
def get_recognition(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    if "team" in args:
        S.require_team(ctx, args["team"])
    return _read(lambda services: S.recognition(services, ctx, team=args.get("team")))


@REGISTRY.operation(
    "get_progression", "GET", "/api/v1/progression",
    summary="Team XP, level and badges with the inflation monitor (no qualification effect)",
    description="Verified XP net of corrections, level = 1 + floor(xp/level size), badges with statements and evidence.",
    grants=PERFORMANCE_READERS, read_only=True, query=_query(team=TEAM), tags=("performance",),
)
def get_progression(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    if "team" in args:
        S.require_team(ctx, args["team"])
    return _read(lambda services: S.progression_view(services, ctx, team=args.get("team")))


@REGISTRY.operation(
    "list_disruption_claims", "GET", "/api/v1/disruption-claims",
    summary="The excusal review queue: effective claims, decisions and decision versions per goal",
    description="Claims are evidence; relief comes only from a reviewed decision. The decision version is the optimistic version an excusal "
                "decision sends back.",
    grants=PERFORMANCE_READERS, read_only=True, query=_query(shift_id=SHIFT_ID, team=TEAM), tags=("performance",),
)
def list_disruption_claims(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    if "team" in args:
        S.require_team(ctx, args["team"])
    return _read(lambda services: S.claims_view(services, ctx, shift_id=args.get("shift_id"), team=args.get("team")))


@REGISTRY.operation(
    "list_ie_reviews", "GET", "/api/v1/ie-reviews",
    summary="IE standard/role/data review proposals with provenance",
    description="Recorded proposals only; an IE review never changes issued goals, frozen units or evaluations.",
    grants=("IE", "manager", "team_lead"), read_only=True, tags=("improve",),
)
def list_ie_reviews(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.ie_reviews_view(services, ctx))


# ---------------------------------------------------------------------- P20 operational views

@REGISTRY.operation(
    "get_team_lead", "GET", "/api/v1/team-lead",
    summary="A team-shift's server-ranked actions with current dispatch reasons, exact crews and amendment proposals",
    description="Each accepted assignment of the team-shift is assessed against current facts with the dispatch rule (predecessors, parts, "
                "QA, certificates, attendance, calendar, CS); dispatchable work ranks first. Open team work is assessed as feasible "
                "emergency-addition proposals with reasons. Read-only: nothing is assigned, completed or recorded.",
    grants=ALL_READERS, read_only=True, query=_query(team=TEAM, date=DAY, shift=SHIFT_NUMBER), tags=("operate",),
)
def get_team_lead(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    team = S.default_team(ctx, args.get("team"))                                # scope before fetch
    day = S.parse_day(args.get("date"))
    return _read(lambda services: S.team_lead(_canon(services), ctx, team=team, day=day, shift=int(args.get("shift", 1))))


@REGISTRY.operation(
    "get_standards", "GET", "/api/v1/standards",
    summary="Duration standards, certificate requirements and team authority with provenance and a review queue",
    description="Sourced duration statistics and methods, requirement status and bundles, team-inference evidence and data gaps; weak or "
                "unknown facts form the IE review queue. Proposals never rewrite issued standards.",
    grants=ALL_READERS, read_only=True, tags=("improve",),
)
def get_standards(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _snapshot_read("standards", ctx, {}, lambda canon: S.standards(canon, ctx))


@REGISTRY.operation(
    "get_budget", "GET", "/api/v1/budget",
    summary="Schedule budget: standard person effort, planned duty, NVA and OT by team-shift and job flow budgets",
    description="Per team-shift: prorated standard person-minutes of planned work, duty minutes by kind, non-value-added minutes, OT and "
                "regular capacity. Per job: flow budget versus planned span.",
    grants=ALL_READERS, read_only=True, query=_query(date=DAY, purpose={"type": "string", "enum": ["EXECUTABLE", "FORECAST"]}), tags=("plan",),
)
def get_budget(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    day = S.parse_day(args.get("date"))
    purpose = args.get("purpose", "EXECUTABLE")
    return _snapshot_read("budget", ctx, {"date": day, "purpose": purpose}, lambda canon: S.budget(canon, ctx, day=day, purpose=purpose))


@REGISTRY.operation(
    "get_gantt", "GET", "/api/v1/gantt",
    summary="Worker Gantt: every person's work, travel, wait, tool, setup and OT duty with shift boundaries",
    description="People of the pools in your scope for a day (optionally one team) with every planned duty and carry-in, plus the policy "
                "windows of shifts 1-3.",
    grants=ALL_READERS, read_only=True,
    query=_query(date=DAY, team=TEAM, purpose={"type": "string", "enum": ["EXECUTABLE", "FORECAST"]}), tags=("operate",),
)
def get_gantt(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    if "team" in args:
        S.require_team(ctx, args["team"])                                       # scope before fetch
    day = S.parse_day(args.get("date"))
    purpose = args.get("purpose", "EXECUTABLE")
    team = args.get("team")
    return _snapshot_read("gantt", ctx, {"date": day, "team": team, "purpose": purpose},
                          lambda canon: S.gantt(canon, ctx, day=day, team=team, purpose=purpose))


@REGISTRY.operation(
    "get_development", "GET", "/api/v1/development",
    summary="Development: IE proposals, skill gaps with training candidates and team progression (never certification)",
    description="Auditable improvement proposals, certificate bundles short of qualified admitted people with partially qualified "
                "candidates, and XP/levels/badges with no qualification effect.",
    grants=ALL_READERS, read_only=True, tags=("improve",),
)
def get_development(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.development(_canon(services), ctx))


@REGISTRY.operation(
    "get_arena", "GET", "/api/v1/arena",
    summary="Arena: team standings from stored evaluations (pooled exactly), XP, badges and recent recognition",
    description="Each team's goal evaluations pooled by summing exact components (never averaging scores), with XP, level and badges.",
    grants=PERFORMANCE_READERS, read_only=True, tags=("performance",),
)
def get_arena(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.arena(services, ctx))


@REGISTRY.operation(
    "get_turnover", "GET", "/api/v1/turnover",
    summary="Shift turnover: open frozen units, pending claims and holds, and what the next shift inherits",
    description="Open units from the canonical evaluation evidence, unreviewed claims, open quality holds, the next shift's actions with "
                "their dispatch reasons and carry-in duties.",
    grants=ALL_READERS, read_only=True, query=_query(team=TEAM, date=DAY, shift=SHIFT_NUMBER), tags=("operate",),
)
def get_turnover(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    team = S.default_team(ctx, args.get("team"))                                # scope before fetch
    day = S.parse_day(args.get("date"))
    return _read(lambda services: S.turnover(_canon(services), ctx, team=team, day=day, shift=int(args.get("shift", 1))))


@REGISTRY.operation(
    "get_history", "GET", "/api/v1/history",
    summary="History: accepted plans over time, evaluation revisions, corrections and delivery changes",
    description="Verified accepted plans of both purposes with their head versions, every evaluation revision in your scope, recognition "
                "corrections and acknowledged delivery change events. Accepted history is immutable.",
    grants=ALL_READERS, read_only=True, tags=("plan",),
)
def get_history(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.history_view(services, ctx))


@REGISTRY.operation(
    "list_scenarios", "GET", "/api/v1/scenarios",
    summary="Stored what-if scenarios (SCENARIO_ONLY) and the inputs a new scenario may change",
    description="Newest first: changes, status, baseline and scenario candidates and the line-by-line comparison.",
    grants=ALL_READERS, read_only=True, tags=("plan",),
)
def list_scenarios(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.scenarios_view(services, ctx))


@REGISTRY.operation(
    "get_scenario", "GET", "/api/v1/scenarios/{scenario_id}",
    summary="One stored scenario result with its baseline comparison",
    description="Immutable scenario result; it is never publishable or executable.",
    grants=ALL_READERS, read_only=True, path_params={"scenario_id": {"type": "string", "pattern": ID_PATTERN}}, tags=("plan",),
    mcp_resource="focus://scenarios/{scenario_id}",
)
def get_scenario(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.scenarios_view(services, ctx, args["scenario_id"]))


# ---------------------------------------------------------------------- digital twin (S16, P21)

LINE = {"type": "integer", "minimum": 1, "maximum": 999999, "description": "Aircraft line number."}


@REGISTRY.operation(
    "get_twin", "GET", "/api/v1/twin",
    summary="Digital twin: observed, planned, forecast and unknown layers with exact team/shift/line reconciliation",
    description="Cells keyed by source run, accepted plans, policy and evaluation revision plus team, shift start date, shift number and "
                "line. Observed effort is the stored canonical evaluation evidence; planned is the accepted EXECUTABLE plan; forecast is the "
                "accepted FORECAST plan (conditional); unknown is what no layer can state. Every layer reconciles exactly by team, shift, "
                "line and in total, and the reconciliation checks are returned. Drill down with team, date, shift and line.",
    grants=ALL_READERS, read_only=True, query=_query(team=TEAM, date=DAY, shift=SHIFT_NUMBER, line=LINE), tags=("plan",),
    mcp_resource="focus://twin",
)
def get_twin(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    team = args.get("team")
    if team is not None:
        S.require_team(ctx, team)                                               # scope before fetch
    day = S.parse_day(args.get("date"))
    shift = int(args["shift"]) if "shift" in args else None
    line = int(args["line"]) if "line" in args else None
    return _read(lambda services: S.twin_view(_canon(services), ctx, team=team, day=day, shift=shift, line=line))


@REGISTRY.operation(
    "get_explanation", "GET", "/api/v1/explanations/{explanation_id}",
    summary="One stored explanation: validated provider text or the deterministic fallback, with grounded claims and audit hashes",
    description="Items follow the deterministic rank. The outcome is DETERMINISTIC (no provider), VALIDATED (every claim and the "
                "narrative grounded) or FALLBACK (with the codes that rejected the provider output). Audit hashes cover the request, "
                "prompt, raw response, recorded input, output and facts; no raw prompt, response or secret is stored.",
    grants=ALL_READERS, read_only=True, path_params={"explanation_id": {"type": "string", "pattern": ID_PATTERN}}, tags=("operate",),
    mcp_resource="focus://explanations/{explanation_id}",
)
def get_explanation(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    return _read(lambda services: S.explanation_view(services, ctx, args["explanation_id"]))
