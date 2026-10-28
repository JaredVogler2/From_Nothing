"""Canonical read projections for /api/v1 (S15 READ ENDPOINTS, P19/P20).

Every projection reads only immutable facts:

* the admitted source snapshot of the current source head, loaded read-only (a GET never
  rebuilds or stores a snapshot);
* the end-to-end verified accepted plans (``publication.service.verify_publication``);
* persisted evaluations, observations, claims, decisions and recognition chains.

It applies the caller's scope to that canonical object and returns a typed ``Projection``.
Nothing here acquires, schedules, validates, grades, signs or writes, so a GET can never change
state.

**Caching.** Two process-local LRU caches, both safe across users:

* ``CACHE`` holds content-addressed immutable objects: a snapshot by id, a verified accepted
  plan by head record and version, and a goal registry by the accepted-plan set and policy hash.
  Scope never enters these keys because scope is applied afterwards, on every request.
* ``PROJECTIONS`` holds scoped snapshot projections keyed by the authorized scope (teams,
  person, economics visibility), the filters and the canonical revision (source run, snapshot,
  policy hash, plan heads and staleness), so a new source or plan head is a new key (S15).

Absence and verification failures are never cached. Evaluation-bearing projections
(performance, history, recognition, claims) are computed per request from persisted records.

**Null semantics.** ``None`` means unknown or not applicable, never zero. Statuses are ``ready``,
``empty``, ``unknown``, ``blocked`` or ``stale``, each with typed reasons.
"""
from __future__ import annotations

import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any, Callable, Iterable

from ..calendar import parse_shift_id
from ..codec import content_hash
from ..domain import PhaseKind
from .operations import reason
from .security import ApiError, RequestContext

ECONOMICS_GRANTS = ("manager", "scenario_planner")


# ---------------------------------------------------------------------- caches

class ImmutableCache:
    """LRU over values derived only from immutable, content-addressed records."""

    def __init__(self, capacity: int = 12):
        self.capacity = capacity
        self._items: OrderedDict[tuple, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_load(self, key: tuple, loader: Callable[[], Any], keep: Callable[[Any], bool] = lambda v: v is not None) -> Any:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = loader()
        if not keep(value):
            return value                    # absence or a failed verification is never cached
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


CACHE = ImmutableCache(capacity=16)
PROJECTIONS = ImmutableCache(capacity=128)


@dataclass
class Projection:
    status: str
    data: Any
    reasons: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, int] | None = None
    source_run_id: str | None = None
    plan_id: str | None = None
    evaluation_id: str | None = None
    purpose: str | None = None
    next_cursor: str | None = None


def can_see_economics(ctx: RequestContext) -> bool:
    return bool(ctx.grants & set(ECONOMICS_GRANTS))


def scope_key(ctx: RequestContext) -> tuple:
    s = ctx.scope
    return (s.all_teams, tuple(sorted(s.teams)), s.person_native, can_see_economics(ctx))


# ---------------------------------------------------------------------- canonical heads

@dataclass
class Canon:
    services: Any
    source_run_id: str | None
    snapshot: Any | None
    executable: Any | None                  # publication.service.CurrentPlan
    executable_reason: str
    forecast: Any | None
    forecast_reason: str
    stale: bool
    reasons: list[dict[str, Any]]
    revision: tuple = ()

    @classmethod
    def load(cls, services: Any) -> "Canon":
        from ..data.acquisition import get_run, load_snapshot, source_head_name
        from ..policy import PolicyError
        from ..publication.repository import repository_for
        from ..publication.service import verify_publication

        repos = services.repositories
        if not repos.available:
            return cls(services, None, None, None, "REPOSITORY_UNAVAILABLE", None, "REPOSITORY_UNAVAILABLE", False,
                       [reason("REPOSITORY_UNAVAILABLE", "No repository is configured; the application is in a typed no-data state.",
                               "Configure FOCUS_SQL_* (live) or use FOCUS_MODE=fixture.")], ("unavailable",))
        try:
            policy = services.policy
        except PolicyError as exc:
            return cls(services, None, None, None, "POLICY_UNAVAILABLE", None, "POLICY_UNAVAILABLE", False,
                       [reason("POLICY_UNAVAILABLE", exc.message, "Fix the configured policy bundle.")], ("no-policy",))
        scope = services.settings.scope_key
        reasons: list[dict[str, Any]] = []
        head = repos.head(source_head_name(scope))
        snapshot = None
        if head is None:
            reasons.append(reason("NO_SOURCE_RUN", "No admitted source run exists for this scope.",
                                  "Run the demo pipeline (fixture) or a source acquisition worker (live)."))
        else:
            found = get_run(repos, head.record_id)
            run = None if found is None else found[0]
            if run is None or run.status != "COMPLETE" or not run.snapshot_id:
                reasons.append(reason("SOURCE_RUN_INCOMPLETE", f"The source head names run {head.record_id}, which has no complete admitted snapshot.",
                                      "Investigate the source run receipt; nothing partial is shown as complete."))
            else:
                snapshot = CACHE.get_or_load(("snapshot", run.snapshot_id), lambda: load_snapshot(repos, run.snapshot_id))
                if snapshot is None:
                    reasons.append(reason("SNAPSHOT_MISSING", f"Snapshot {run.snapshot_id} of the current source run is missing.",
                                          "Re-admit the source run."))
                elif snapshot.policy_hash != policy.hash:
                    reasons.append(reason("POLICY_CHANGED_SINCE_ADMISSION",
                                          "The admitted snapshot was built under another policy revision; facts are shown as admitted.",
                                          "Re-admit or reschedule under the active policy."))
        plans: dict[str, tuple[Any, str, tuple]] = {}
        repo = repository_for(services)
        for purpose in ("EXECUTABLE", "FORECAST"):
            plan_head = repo.head(scope, purpose)
            if plan_head is None:
                plans[purpose] = (None, "NO_ACCEPTED_PLAN", (purpose, None))
                continue
            current, why = CACHE.get_or_load(
                ("plan", plan_head.record_id, plan_head.version),
                lambda h=plan_head, p=purpose: verify_publication(services, h.record_id, scope=scope, purpose=p, head_version=h.version),
                keep=lambda v: v[0] is not None)
            plans[purpose] = (current, why, (purpose, plan_head.record_id, plan_head.version))
        stale = False
        if snapshot is not None:
            stale = services.clock.now() - snapshot.cutoff > timedelta(hours=policy.int("dispatch.max_source_age_hours"))
            if stale:
                reasons.append(reason("SOURCE_STALE", f"Source cut off at {snapshot.cutoff.isoformat()} is older than the freshness policy.",
                                      "Shown for history only; acquire a current source run."))
        revision = (None if head is None else head.record_id, None if snapshot is None else snapshot.id, policy.hash,
                    plans["EXECUTABLE"][2], plans["FORECAST"][2], stale)
        return cls(services, None if head is None else head.record_id, snapshot, plans["EXECUTABLE"][0], plans["EXECUTABLE"][1],
                   plans["FORECAST"][0], plans["FORECAST"][1], stale, reasons, revision)

    # -- indexes (cheap; built from the cached immutable snapshot)
    def jobs(self) -> dict[str, Any]:
        return {j.key.id: j for j in self.snapshot.jobs} if self.snapshot else {}

    def phases(self) -> dict[str, Any]:
        return {p.key.id: p for p in self.snapshot.phases} if self.snapshot else {}

    def people(self) -> dict[str, Any]:
        return {p.key.id: p for p in self.snapshot.people} if self.snapshot else {}

    def dispositions(self) -> dict[str, Any]:
        return {d.job: d for d in self.snapshot.dispositions} if self.snapshot else {}

    def current(self, purpose: str) -> Any:
        return self.executable if purpose == "EXECUTABLE" else self.forecast

    def plan(self, purpose: str) -> Any:
        cur = self.current(purpose)
        return None if cur is None else cur.artifact.candidate

    def plan_id(self, purpose: str) -> str | None:
        cur = self.current(purpose)
        return None if cur is None else cur.published.id

    def base_status(self) -> str:
        if self.snapshot is None:
            return "unknown" if self.services.repositories.available and self.executable_reason != "POLICY_UNAVAILABLE" else "blocked"
        return "stale" if self.stale else "ready"

    def plan_reason(self, purpose: str) -> dict[str, Any]:
        why = self.executable_reason if purpose == "EXECUTABLE" else self.forecast_reason
        if why in ("NO_ACCEPTED_PLAN", "REPOSITORY_UNAVAILABLE", "POLICY_UNAVAILABLE"):
            return reason("NO_ACCEPTED_PLAN", f"No accepted {purpose} plan exists for this scope.",
                          "Schedule, validate and publish a plan through the trusted publication worker.")
        return reason("PLAN_NOT_VERIFIED", f"The accepted {purpose} head did not verify: {why}", "Investigate the publication receipt; nothing is current.")


def _need_snapshot(canon: Canon) -> Projection | None:
    if canon.snapshot is None:
        return Projection(canon.base_status(), None, list(canon.reasons))
    return None


def cached(op: str, ctx: RequestContext, canon: Canon, filters: dict[str, Any], build: Callable[[], Any]) -> Any:
    """A scoped snapshot projection, cached by authorized scope + filters + canonical revision."""
    if canon.snapshot is None:
        return build()
    key = (op, scope_key(ctx), tuple(sorted(filters.items())), canon.revision)
    return PROJECTIONS.get_or_load(key, build)


def registry(services: Any) -> Any:
    """Goals of record (verified accepted EXECUTABLE plans), cached by the accepted set and policy."""
    from ..performance.lifecycle import GoalRegistry
    from ..publication.repository import repository_for

    accepted = tuple(repository_for(services).accepted_ids())
    return CACHE.get_or_load(("registry", accepted, services.policy.hash), lambda: GoalRegistry.load(services))


# ---------------------------------------------------------------------- scope helpers

def team_allowed(ctx: RequestContext, team: str | None) -> bool:
    return ctx.scope.allows_team(team)


def require_team(ctx: RequestContext, team: str) -> None:
    """Scope before fetch: a team outside the caller's scope is refused before any data is assembled."""
    if not ctx.scope.allows_team(team):
        raise ApiError(403, "OUT_OF_SCOPE", f"team {team} is outside your data scope")


def default_team(ctx: RequestContext, team: str | None) -> str:
    if team is not None:
        require_team(ctx, team)
        return team
    if not ctx.scope.all_teams and len(ctx.scope.teams) == 1:
        return next(iter(ctx.scope.teams))
    raise ApiError(400, "TEAM_REQUIRED", "name a team (your scope covers several or none)")


def _origin(p: Any) -> str:
    return p.origin.value if hasattr(p.origin, "value") else str(p.origin)


def person_view(canon: Canon, ctx: RequestContext, person_id: str, team: str | None, people: dict[str, Any] | None = None,
                role: str | None = None) -> dict[str, Any]:
    people = people if people is not None else canon.people()
    p = people.get(person_id)
    if p is None:
        return {"person_id": person_id, "name": None, "native": None, "origin": "unknown", "synthetic": None, "role": role}
    own = ctx.scope.person_native is not None and p.key.native_id == ctx.scope.person_native
    if not (own or team_allowed(ctx, team)):
        return {"person_id": None, "name": "outside your scope", "native": None, "origin": _origin(p), "synthetic": p.synthetic,
                "role": role, "redacted": True}
    return {"person_id": p.key.id, "name": p.display_name, "native": p.key.native_id, "origin": _origin(p), "synthetic": p.synthetic, "role": role}


def phase_team(phase: Any) -> str | None:
    if phase is None:
        return None
    return phase.role_demands[0].team if phase.role_demands else phase.team


def frac(x: Fraction | None) -> dict[str, int] | None:
    return None if x is None else {"num": x.numerator, "den": x.denominator}


def ratio_text(x: Fraction | None, digits: int = 1) -> str | None:
    if x is None:
        return None
    from ..performance.score import round_half_up

    return str(round_half_up(x, digits))


def parse_day(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "INVALID_PARAMETER", "date must be YYYY-MM-DD") from None


def check_shift_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parse_shift_id(value)
    except ValueError:
        raise ApiError(400, "INVALID_PARAMETER", "shift_id must look like SITE:YYYY-MM-DD:S1") from None
    return value


# ---------------------------------------------------------------------- fleet and aircraft

def _completion(c: Any) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"status": c.status, "completion_at": c.completion_at, "required_jobs": c.required_jobs, "scheduled_jobs": c.scheduled_jobs,
            "unresolved_jobs": len(c.unresolved_jobs), "reasons": list(c.reasons)}


def _cost(lc: Any) -> dict[str, Any] | None:
    if lc is None:
        return None
    return {"status": lc.status, "authority": lc.authority, "completion_at": lc.completion_at, "due_at": lc.due_at, "delay_minutes": lc.delay_minutes,
            "total_cents": frac(lc.total_cents), "unavoidable_cents": frac(lc.unavoidable_cents), "controllable_cents": frac(lc.controllable_cents),
            "marginal_day_cents": frac(lc.marginal_day_cents), "reasons": list(lc.reasons)}


def _cs(f: Any) -> dict[str, Any] | None:
    if f is None:
        return None
    return {"station": f.station, "status": f.status, "gate_date": f.gate_date, "due_proxy": f.due_proxy, "earliest_at": f.earliest_at,
            "hard_latest_at": f.hard_latest_at, "load_id": f.raw_load_id, "source_run_id": f.source_run_id, "reasons": list(f.reasons)}


def coverage_of(census: Iterable[Any]) -> dict[str, int]:
    out = {"admitted": 0, "blocked": 0, "quarantined": 0, "excluded": 0}
    for c in census:
        for k in out:
            out[k] += getattr(c, k)
    return out


def census_row(c: Any) -> dict[str, Any]:
    return {"line": c.line, "model": c.model, "customer": c.customer, "status_counts": dict(c.status_counts), "jobs_total": c.jobs_total,
            "admitted": c.admitted, "blocked": c.blocked, "quarantined": c.quarantined, "excluded": c.excluded,
            "verified_complete": c.verified_complete, "scope_basis": c.scope_basis, "reasons": list(c.reasons)}


RESTRICTED = reason("ECONOMICS_RESTRICTED", "Cost and contract terms need an economics grant; they are withheld, not zero.")


def fleet(canon: Canon, ctx: RequestContext) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    snap = canon.snapshot
    show_cost = can_see_economics(ctx)
    cs = {f.line: f for f in snap.cs_facts}
    fp, ep = canon.plan("FORECAST"), canon.plan("EXECUTABLE")
    f_comp = {c.line: c for c in fp.aircraft_completion} if fp else {}
    e_comp = {c.line: c for c in ep.aircraft_completion} if ep else {}
    costs = {lc.line: lc for lc in (fp or ep).costs.lines} if (fp or ep) else {}
    rows = []
    for c in sorted(snap.census, key=lambda x: x.line):
        rows.append(census_row(c) | {"cs": _cs(cs.get(c.line)), "forecast_completion": _completion(f_comp.get(c.line)),
                                     "executable_completion": _completion(e_comp.get(c.line)),
                                     "cost": _cost(costs.get(c.line)) if show_cost else None})
    reasons = list(canon.reasons)
    if fp is None:
        reasons.append(canon.plan_reason("FORECAST"))
    if not show_cost:
        reasons.append(RESTRICTED)
    status = canon.base_status() if rows else "empty"
    data = {"aircraft": rows,
            "totals": {"aircraft": len(rows), "jobs": sum(r["jobs_total"] for r in rows), "verified_complete": sum(r["verified_complete"] for r in rows),
                       "completion_known": sum(1 for r in rows if (r["forecast_completion"] or {}).get("status") == "known"),
                       "completion_conditional": sum(1 for r in rows if (r["forecast_completion"] or {}).get("status") == "conditional"),
                       "completion_unknown": sum(1 for r in rows if (r["forecast_completion"] or {}).get("status") in (None, "unknown"))},
            "cutoff": snap.cutoff, "forecast_plan_id": canon.plan_id("FORECAST"), "executable_plan_id": canon.plan_id("EXECUTABLE"),
            "economics_visible": show_cost,
            "gaps": [{"code": g.code, "scope": g.scope, "detail": g.detail, "effect": g.capability_effect} for g in snap.gaps]}
    return Projection(status, data, reasons, coverage_of(snap.census), canon.source_run_id, canon.plan_id("FORECAST") or canon.plan_id("EXECUTABLE"),
                      purpose="FORECAST" if fp else ("EXECUTABLE" if ep else None))


def job_row(job: Any, disposition: Any) -> dict[str, Any]:
    return {"job_id": job.key.id, "line": job.key.line, "soi": job.key.native_id, "revision": job.key.revision, "description": job.description,
            "team": job.team, "status": job.status, "station": job.station, "is_rework": job.is_rework, "rework_class": job.rework_class,
            "parent": job.parent_native, "disposition": disposition.disposition.value if disposition else None,
            "reasons": list(disposition.reasons) if disposition else [], "executable_reasons": list(disposition.executable_reasons) if disposition else []}


def economics_term(e: Any) -> dict[str, Any]:
    return {"line": e.line, "status": e.status, "authority": e.authority, "term_id": e.term_id, "selector": e.selector_type, "currency": e.currency,
            "daily_rate_cents": frac(e.daily_rate_cents), "cap_cents": frac(e.cap_cents), "original_sale_cents": frac(e.original_sale_cents),
            "accrued_floor_cents": frac(e.accrued_floor_cents), "due_at": e.due_at, "due_basis": e.due_basis, "curve": e.curve, "reasons": list(e.reasons)}


def aircraft(canon: Canon, ctx: RequestContext, line: int) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    snap = canon.snapshot
    census = next((c for c in snap.census if c.line == line), None)
    if census is None:
        raise ApiError(404, "AIRCRAFT_NOT_FOUND", f"line {line} is not in the admitted census")
    show_cost = can_see_economics(ctx)
    disp = canon.dispositions()
    jobs = sorted((j for j in snap.jobs if j.key.line == line), key=lambda j: j.key.native_id)
    visible = [j for j in jobs if team_allowed(ctx, j.team)]
    fp, ep = canon.plan("FORECAST"), canon.plan("EXECUTABLE")
    reasons = list(canon.reasons)
    if len(visible) < len(jobs):
        reasons.append(reason("JOBS_OUTSIDE_SCOPE", f"{len(jobs) - len(visible)} jobs of this aircraft belong to teams outside your scope; counts include them."))
    if not show_cost:
        reasons.append(RESTRICTED)
    data = {"line": line, "census": census_row(census), "jobs": [job_row(j, disp.get(j.key.id)) for j in visible],
            "cs": _cs(next((f for f in snap.cs_facts if f.line == line), None)),
            "calendar": next(({"status": c.status, "min_date": c.min_date, "max_date": c.max_date, "holes": list(c.holes), "reasons": list(c.reasons)}
                              for c in snap.calendars if c.line == line), None),
            "forecast_completion": _completion(next((c for c in fp.aircraft_completion if c.line == line), None)) if fp else None,
            "executable_completion": _completion(next((c for c in ep.aircraft_completion if c.line == line), None)) if ep else None,
            "cost": _cost(next((lc for lc in (fp or ep).costs.lines if lc.line == line), None)) if (fp or ep) and show_cost else None,
            "economics": next((economics_term(e) for e in snap.economics if e.line == line), None) if show_cost else None,
            "shortages": [shortage_row(canon, s) for s in snap.shortages if s.line == line]}
    return Projection(canon.base_status(), data, reasons, coverage_of([census]), canon.source_run_id, canon.plan_id("FORECAST"),
                      purpose="FORECAST" if fp else None)


# ---------------------------------------------------------------------- jobs

def jobs_list(canon: Canon, ctx: RequestContext, *, line: int | None, team: str | None, disposition: str | None) -> tuple[Projection, list[Any]]:
    if team is not None:
        require_team(ctx, team)
    missing = _need_snapshot(canon)
    if missing:
        return missing, []
    disp = canon.dispositions()
    rows = []
    hidden = 0
    for j in sorted(canon.snapshot.jobs, key=lambda j: (j.key.line, j.key.native_id, j.key.id)):
        d = disp.get(j.key.id)
        if line is not None and j.key.line != line:
            continue
        if team is not None and j.team != team:
            continue
        if disposition is not None and (d is None or d.disposition.value != disposition):
            continue
        if not team_allowed(ctx, j.team):
            hidden += 1
            continue
        rows.append(job_row(j, d))
    reasons = list(canon.reasons)
    if hidden:
        reasons.append(reason("JOBS_OUTSIDE_SCOPE", f"{hidden} matching jobs belong to teams outside your scope and are not listed."))
    return Projection(canon.base_status() if rows else "empty", None, reasons, coverage_of(canon.snapshot.census), canon.source_run_id), rows


def _assignment_view(canon: Canon, ctx: RequestContext, a: Any, phases: dict[str, Any], people: dict[str, Any], purpose: str) -> dict[str, Any]:
    team = phase_team(phases.get(a.phase))
    return {"assignment_id": a.id, "phase": a.phase, "kind": a.kind.value, "purpose": purpose, "shift_id": a.shift_id, "start": a.start, "end": a.end,
            "segments": [{"start": s.start, "end": s.end} for s in a.segments], "work_minutes": a.work_minutes,
            "crew": [person_view(canon, ctx, r.person, team, people, r.role) for r in a.crew], "conditional_reasons": list(a.conditional_reasons),
            "predecessor_release_ids": list(a.predecessor_release_ids), "committed": a.committed, "commitment_outcome": a.commitment_outcome,
            "team": team}


def _edge(e: Any, label: Callable[[str], str]) -> dict[str, Any]:
    return {"pred": e.pred_phase, "pred_label": label(e.pred_phase), "succ": e.succ_phase, "succ_label": label(e.succ_phase), "type": e.type.value,
            "lag_minutes": e.lag_minutes, "basis": e.basis}


def job_detail(canon: Canon, ctx: RequestContext, job_id: str) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    snap = canon.snapshot
    jobs = canon.jobs()
    job = jobs.get(job_id)
    own = sorted((p for p in snap.phases if p.job == job_id), key=lambda p: (p.kind.value != "WORK", p.kind.value, p.ordinal))
    if job is None or not (team_allowed(ctx, job.team) or any(team_allowed(ctx, phase_team(p)) for p in own)):
        raise ApiError(404, "JOB_NOT_FOUND", "no job with that id in your scope")
    phases = canon.phases()
    people = canon.people()
    own_ids = {p.key.id for p in own}
    releases = {r.id: r for r in snap.releases}

    def label(node: str) -> str:
        if node in releases:
            return releases[node].label
        p = phases.get(node)
        if p is None:
            return node
        j = jobs.get(p.job)
        return f"{j.key.line}/{j.key.native_id} {p.kind.value} {p.ordinal}" if j else node

    edges_in = [_edge(e, label) for e in snap.edges if e.hard and e.succ_phase in own_ids and e.pred_phase not in own_ids]
    edges_out = [_edge(e, label) for e in snap.edges if e.hard and e.pred_phase in own_ids and e.succ_phase not in own_ids]
    internal = [_edge(e, label) for e in snap.edges if e.hard and e.pred_phase in own_ids and e.succ_phase in own_ids]
    rel = [{"release_id": r.id, "kind": r.kind.value, "status": r.status.value, "ready_at": r.ready_at, "forecast_at": r.forecast_at,
            "precision": r.precision, "label": r.label} for r in releases.values() if job_id in r.scope_keys]
    duration = next((d for d in snap.durations if d.job == job_id), None)
    requirement = next((r for r in snap.requirements if r.job == job_id), None)
    assignments = []
    for purpose in ("EXECUTABLE", "FORECAST"):
        plan = canon.plan(purpose)
        if plan is not None:
            assignments += [_assignment_view(canon, ctx, a, phases, people, purpose) for a in plan.assignments if a.job == job_id]
    outcome = {}
    for purpose in ("EXECUTABLE", "FORECAST"):
        plan = canon.plan(purpose)
        o = next((o for o in plan.job_dispositions if o.job == job_id), None) if plan else None
        outcome[purpose] = None if o is None else {"disposition": o.disposition.value, "reasons": list(o.reasons), "start": o.start, "end": o.end,
                                                   "conditional": o.conditional}
    trigger = job.inspection_trigger
    data = {"job": job_row(job, canon.dispositions().get(job_id)),
            "phases": [{"phase_id": p.key.id, "kind": p.kind.value, "ordinal": p.ordinal, "work_minutes": p.work_minutes,
                        "standard_person_minutes": p.standard_person_minutes, "team": phase_team(p), "status": p.status, "label": p.label,
                        "requirement": None if not p.role_demands else {"groups": [list(g) for g in p.role_demands[0].bundle.groups],
                                                                         "role": p.role_demands[0].bundle.role, "count": p.role_demands[0].bundle.count,
                                                                         "status": p.role_demands[0].requirement_status.value,
                                                                         "basis": p.role_demands[0].basis},
                        "conditional_reasons": list(p.conditional_reasons), "blocked_reasons": list(p.blocked_reasons)} for p in own],
            "predecessors": edges_in, "successors": edges_out, "internal_edges": internal, "releases": rel,
            "inspection": None if trigger is None else {"final_required": trigger.final_required, "ip_required": trigger.ip_required,
                                                         "ip_hold_status": trigger.ip_hold_status, "other_buyoffs": list(trigger.other_buyoffs),
                                                         "holds": [{"after_work_minutes": hd.after_work_minutes, "inspector_minutes": hd.inspector_minutes}
                                                                   for hd in trigger.ip_holds], "basis": trigger.basis},
            "duration": None if duration is None else {"elapsed_minutes": duration.elapsed_minutes, "standard_person_minutes": duration.standard_person_minutes,
                                                       "basis": duration.measurement_basis, "statistic": duration.statistic, "method": duration.method,
                                                       "crew_size": duration.crew_size, "uncertainty": duration.uncertainty},
            "requirement": None if requirement is None else {"status": requirement.status.value if hasattr(requirement.status, "value") else str(requirement.status),
                                                             "groups": [list(g) for g in requirement.bundle.groups] if requirement.bundle else [],
                                                             "basis": requirement.basis},
            "actual": {"start": job.actual_start, "complete": job.actual_complete, "performed_elapsed": job.performed_elapsed,
                       "remaining_elapsed": job.remaining_elapsed},
            "shortages": [shortage_row(canon, s) for s in snap.shortages if job_id in s.affected_jobs or s.root_job == job_id],
            "plan_outcome": outcome,
            "assignments": sorted(assignments, key=lambda a: (a["purpose"], a["start"]))}
    return Projection(canon.base_status(), data, list(canon.reasons), None, canon.source_run_id, canon.plan_id("EXECUTABLE"),
                      purpose="EXECUTABLE" if canon.executable else None)


# ---------------------------------------------------------------------- plans

def plan_view(services: Any, ctx: RequestContext, plan_id: str) -> Projection:
    from ..contracts.planning import CandidatePlan
    from ..engine.max.optimizer.orchestrator import CANDIDATES
    from ..publication.repository import repository_for
    from ..publication.service import verify_publication

    repo = repository_for(services)
    published = repo.published(plan_id)
    if published is not None:
        head = repo.head(published.scope, published.purpose.value)
        current, why = CACHE.get_or_load(("published", plan_id),
                                         lambda: verify_publication(services, plan_id, scope=published.scope, purpose=published.purpose.value),
                                         keep=lambda v: v[0] is not None)
        if current is None:
            return Projection("blocked", {"plan_id": plan_id, "accepted": True, "verified": False},
                              [reason("PLAN_NOT_VERIFIED", why, "Investigate the publication receipt; this plan is not trusted.")], plan_id=plan_id)
        art = current.artifact
        is_current = head is not None and head.record_id == plan_id
        receipt = published.acceptance_receipt
        return Projection("ready" if is_current else "stale", {
            "plan_id": plan_id, "accepted": True, "verified": True, "current": is_current, "purpose": published.purpose.value,
            "capability": art.capability, "accepted_at": published.accepted_at, "key_id": published.key_id,
            "fixture_readback": receipt.fixture_readback, "head_version": receipt.head_version, "source_run_id": published.source_run_id,
            "snapshot_id": published.snapshot_id, "policy_id": art.policy_id, "policy_hash": published.policy_hash,
            "frozen_goal_ids": list(published.frozen_goal_ids), "obligation_dates": list(art.obligation_dates),
            "validations": {"final": art.final_validation_id, "detached": receipt.detached_validation_id, "readback": receipt.readback_validation_id},
            "candidate": candidate_summary(art.candidate)},
            [] if is_current else [reason("NOT_CURRENT", "This accepted plan is history; it is not currently executable.")],
            source_run_id=published.source_run_id, plan_id=plan_id, purpose=published.purpose.value)
    candidate = services.repositories.get_typed(CANDIDATES, plan_id, "CandidatePlan", CandidatePlan)
    if candidate is None:
        raise ApiError(404, "PLAN_NOT_FOUND", "no accepted plan or candidate with that id")
    return Projection("ready", {"plan_id": plan_id, "accepted": False, "verified": False, "current": False, "purpose": candidate.purpose.value,
                                "capability": "NONE", "candidate": candidate_summary(candidate)},
                      [reason("CANDIDATE_ONLY", "A candidate is not accepted and never executable until published through the acceptance protocol.")],
                      source_run_id=candidate.source_run_id, plan_id=None, purpose=candidate.purpose.value)


def candidate_summary(plan: Any) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for o in plan.job_dispositions:
        counts[o.disposition.value] += 1
    outcomes: dict[str, int] = defaultdict(int)
    for r in plan.commitment_results:
        outcomes[r.outcome] += 1
    st = plan.algorithm_status
    return {"candidate_id": plan.id, "purpose": plan.purpose.value, "created_at": plan.created_at, "snapshot_id": plan.snapshot_id,
            "assignments": len(plan.assignments), "duties": len(plan.duties), "dispositions": dict(sorted(counts.items())),
            "commitment_outcomes": dict(sorted(outcomes.items())),
            "algorithm": {"name": st.name, "version": st.version, "seed": st.seed, "budget_seconds": st.budget_seconds,
                          "deadline_reached": st.deadline_reached, "proof_status": st.proof_status, "paths_used": list(st.paths_used),
                          "stages": [{"stage": s.stage, "runtime_ms": s.runtime_ms, "candidates": s.candidates, "accepted": s.accepted, "note": s.note}
                                     for s in st.stages], "incumbent": dict(st.incumbent)},
            "global_optimality_proven": plan.global_optimality_proven,
            "costs": {"known_total_cents": frac(plan.costs.known_total_cents), "conditional_total_cents": frac(plan.costs.conditional_total_cents),
                      "controllable_known_cents": frac(plan.costs.controllable_known_cents), "unknown_lines": list(plan.costs.unknown_lines),
                      "complete": plan.costs.complete, "currency": plan.costs.currency}}


# ---------------------------------------------------------------------- shift book and my day

def default_day(canon: Canon) -> date | None:
    cur = canon.executable
    if cur is not None and cur.artifact.obligation_dates:
        return cur.artifact.obligation_dates[0]
    return None


def goal_versions(services: Any, goal_id: str) -> dict[str, int]:
    """Optimistic versions clients send back with amendment and excusal commands (facts known at the knowledge time)."""
    from ..performance.actuals import AMENDMENTS, DECISIONS, ObservationStore, knowledge_time

    store = ObservationStore(services.repositories, services.settings.scope_key)
    at = knowledge_time(services)
    return {"amendments": sum(1 for a in store.known(AMENDMENTS, at) if a.goal_id == goal_id),
            "decisions": sum(1 for d in store.known(DECISIONS, at) if d.goal_id == goal_id)}


def shiftbook(canon: Canon, ctx: RequestContext, *, team: str, day: date | None, shift: int) -> Projection:
    from ..performance import history
    from ..performance.score import summary

    require_team(ctx, team)                                                     # scope before fetch
    missing = _need_snapshot(canon)
    if missing:
        return missing
    plan = canon.plan("EXECUTABLE")
    if plan is None:
        return Projection("unknown", None, list(canon.reasons) + [canon.plan_reason("EXECUTABLE")], source_run_id=canon.source_run_id)
    day = day or default_day(canon)
    site = canon.services.settings.site
    shift_id = f"{site}:{day.isoformat()}:S{shift}" if day else None
    phases, jobs, people = canon.phases(), canon.jobs(), canon.people()
    rows = []
    for a in sorted(plan.assignments, key=lambda x: (x.start, x.id)):
        p = phases.get(a.phase)
        if a.shift_id != shift_id or phase_team(p) != team:
            continue
        j = jobs.get(a.job)
        rows.append(_assignment_view(canon, ctx, a, phases, people, "EXECUTABLE") | {
            "line": a.line, "soi": j.key.native_id if j else None, "description": j.description if j else None,
            "phase_label": p.label if p else a.phase, "blocked_reasons": list(p.blocked_reasons) if p else []})
    goal = None
    evaluation = None
    if shift_id is not None:
        reg = registry(canon.services)
        record = reg.record(team, shift_id)
        if record is not None:
            g = record.goal
            goal = {"goal_id": record.goal_id, "plan_id": record.plan_id, "of_record": True, "B0": record.B0,
                    "units": len(g.earning_units) if g else 0, "members": len(g.members) if g else 0, "hash": g.hash if g else None,
                    "window": {"start": record.window.start, "end": record.window.end, "credit_deadline": record.window.credit_deadline},
                    "versions": goal_versions(canon.services, record.goal_id)}
            e = history.current(canon.services, canon.services.settings.scope_key, history.series_for_goal(canon.services, record.goal_id))
            evaluation = None if e is None else summary(e)
    reasons = list(canon.reasons)
    if goal is not None and goal["plan_id"] != canon.plan_id("EXECUTABLE"):
        reasons.append(reason("GOAL_FROM_EARLIER_PLAN", "The goal of record was frozen by the day's first accepted plan; the current plan's goals are proposals."))
    data = {"team": team, "date": day, "shift": shift, "shift_id": shift_id, "assignments": rows, "goal": goal, "evaluation": evaluation,
            "crew_count": len({c["person_id"] for r in rows for c in r["crew"] if c["person_id"]})}
    status = canon.base_status() if rows else "empty"
    return Projection(status, data, reasons, None, canon.source_run_id, canon.plan_id("EXECUTABLE"),
                      evaluation_id=evaluation["evaluation_id"] if evaluation else None, purpose="EXECUTABLE")


def myday_person(ctx: RequestContext, person_native: str | None) -> str:
    """Scope before fetch for My Day: a mechanic opens only their own day; an aggregates-only scope opens none."""
    native = person_native or ctx.scope.person_native
    if native is None:
        raise ApiError(400, "PERSON_REQUIRED", "name a person (your own scope has no person)")
    if ctx.scope.person_native is not None and native != ctx.scope.person_native and not ctx.scope.all_teams:
        raise ApiError(403, "OUT_OF_SCOPE", "you can only open your own day")
    if ctx.scope.person_native is None and not ctx.scope.all_teams and not ctx.scope.teams:
        raise ApiError(403, "OUT_OF_SCOPE", "your scope has no team or person detail")
    return native


def myday(canon: Canon, ctx: RequestContext, *, person_native: str | None, day: date | None) -> Projection:
    from ..data.workforce import person_namespace
    from ..domain import PersonKey

    native = myday_person(ctx, person_native)
    missing = _need_snapshot(canon)
    if missing:
        return missing
    person_id = PersonKey(native, person_namespace(canon.services.settings.mode)).id
    people = canon.people()
    if person_id not in people:
        raise ApiError(404, "PERSON_NOT_FOUND", "no admitted person with that key in your scope")
    teams = sorted({p.key.team for p in canon.snapshot.pools if person_id in p.members})
    own = ctx.scope.person_native == native
    if not own and not any(team_allowed(ctx, t) for t in teams):
        raise ApiError(404, "PERSON_NOT_FOUND", "no admitted person with that key in your scope")
    plan = canon.plan("EXECUTABLE")
    if plan is None:
        return Projection("unknown", None, list(canon.reasons) + [canon.plan_reason("EXECUTABLE")], source_run_id=canon.source_run_id)
    day = day or default_day(canon)
    phases, jobs = canon.phases(), canon.jobs()
    assignments = {a.id: a for a in plan.assignments}
    duties = []
    totals = {"WORK": 0, "TRAVEL": 0, "WAIT": 0, "TOOL": 0, "OTHER": 0, "regular": 0, "ot": 0}
    for d in sorted(plan.duties, key=lambda x: (x.start, x.id)):
        if d.person != person_id:
            continue
        if day is not None and d.shift_id and parse_shift_id(d.shift_id)[1] != day:
            continue
        a = assignments.get(d.assignment_id) if d.assignment_id else None
        p = phases.get(a.phase) if a else None
        j = jobs.get(a.job) if a else None
        team = phase_team(p)
        minutes = int((d.end - d.start).total_seconds() // 60)
        totals[d.kind.value] = totals.get(d.kind.value, 0) + minutes
        totals["regular"] += d.regular_minutes
        totals["ot"] += d.ot_minutes
        duties.append({"duty_id": d.id, "kind": d.kind.value, "start": d.start, "end": d.end, "minutes": minutes, "shift_id": d.shift_id,
                       "aircraft_from": d.aircraft_from, "aircraft_to": d.aircraft_to, "regular_minutes": d.regular_minutes, "ot_minutes": d.ot_minutes,
                       "job": None if j is None else {"job_id": j.key.id, "line": j.key.line, "soi": j.key.native_id, "description": j.description},
                       "phase": None if p is None else {"phase_id": p.key.id, "kind": p.kind.value, "ordinal": p.ordinal, "label": p.label},
                       "readiness": None if a is None else {"conditional_reasons": list(a.conditional_reasons),
                                                            "blocked_reasons": list(p.blocked_reasons) if p else [],
                                                            "predecessor_release_ids": list(a.predecessor_release_ids),
                                                            "committed": a.committed},
                       "crewmates": [person_view(canon, ctx, r.person, team, people, r.role) for r in (a.crew if a else ()) if r.person != person_id]})
    carry = [{"id": c.id, "kind": c.kind.value if hasattr(c.kind, "value") else str(c.kind), "start": c.start, "end": c.end, "line": c.line,
              "job": c.job} for c in canon.snapshot.carry_in if c.person == person_id]
    data = {"person": person_view(canon, ctx, person_id, teams[0] if teams else None, people), "teams": teams, "date": day, "duties": duties,
            "carry_in": carry, "totals_minutes": totals,
            "first_arrival": duties[0]["start"] if duties else None, "last_release": duties[-1]["end"] if duties else None,
            "note": "a duty is a planned assignment; only source-verified completion is a check-off"}
    return Projection(canon.base_status() if duties or carry else "empty", data, list(canon.reasons), None, canon.source_run_id,
                      canon.plan_id("EXECUTABLE"), purpose="EXECUTABLE")


# ---------------------------------------------------------------------- workforce and capacity

def _pool_rows(canon: Canon, ctx: RequestContext, day: date | None, shift: int | None) -> list[Any]:
    out = []
    for p in sorted(canon.snapshot.pools, key=lambda x: (x.key.start_date, x.key.shift_number, x.key.team)):
        if day is not None and p.key.start_date != day:
            continue
        if shift is not None and p.key.shift_number != shift:
            continue
        if not team_allowed(ctx, p.key.team):
            continue
        out.append(p)
    return out


def workforce(canon: Canon, ctx: RequestContext, *, day: date | None, shift: int | None) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    day = day or default_day(canon) or canon.snapshot.cutoff.date()
    people = canon.people()
    avail = {(a.person, a.pool): a for a in canon.snapshot.availability}
    rows = []
    for p in _pool_rows(canon, ctx, day, shift):
        members = []
        for m in p.members:
            a = avail.get((m, p.id))
            members.append(person_view(canon, ctx, m, p.key.team, people) | {
                "executable": m in p.executable_members, "forecast": m in p.forecast_members,
                "admitted_presence": None if a is None else a.admitted_presence, "regular_cap": None if a is None else a.regular_cap,
                "ot_cap": None if a is None else a.ot_cap, "basis": None if a is None else (a.basis.value if hasattr(a.basis, "value") else str(a.basis))})
        rows.append({"pool_id": p.id, "team": p.key.team, "date": p.key.start_date, "shift": p.key.shift_number, "synthetic": p.synthetic,
                     "basis": p.basis, "n_executable": p.n_executable, "cap_executable": p.cap_executable, "n_forecast": p.n_forecast,
                     "cap_forecast": p.cap_forecast, "members": members, "excluded": list(p.excluded)})
    reasons = list(canon.reasons)
    if not ctx.scope.all_teams and not ctx.scope.teams:
        reasons.append(reason("AGGREGATES_ONLY", "Your scope has no team detail; pool membership is not shown."))
    return Projection(canon.base_status() if rows else "empty", {"date": day, "shift": shift, "pools": rows}, reasons, None,
                      canon.source_run_id, canon.plan_id("EXECUTABLE"))


def capacity(canon: Canon, ctx: RequestContext, *, day: date | None, purpose: str) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    plan = canon.plan(purpose)
    if plan is None:
        return Projection("unknown", None, list(canon.reasons) + [canon.plan_reason(purpose)], source_run_id=canon.source_run_id)
    day = day or default_day(canon) or canon.snapshot.cutoff.date()
    avail = {(a.person, a.pool): a for a in canon.snapshot.availability}
    load: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for d in plan.duties:
        if d.shift_id:
            load[(d.person, d.shift_id)][d.kind.value] += int((d.end - d.start).total_seconds() // 60)
            load[(d.person, d.shift_id)]["OT"] += d.ot_minutes
    phases = canon.phases()
    demand: dict[tuple[str, str], int] = defaultdict(int)
    for a in plan.assignments:
        if a.shift_id:
            demand[(phase_team(phases.get(a.phase)) or "?", a.shift_id)] += a.work_minutes * max(1, len(a.crew))
    rows = []
    site = canon.services.settings.site
    for p in _pool_rows(canon, ctx, day, None):
        shift_id = f"{site}:{p.key.start_date.isoformat()}:S{p.key.shift_number}"
        members = p.executable_members if purpose == "EXECUTABLE" else p.forecast_members
        cap_minutes = sum((avail[(m, p.id)].regular_cap if (m, p.id) in avail else 0) for m in members)
        loads = [load.get((m, shift_id), {}) for m in members]
        work = sum(x.get("WORK", 0) for x in loads)
        travel = sum(x.get("TRAVEL", 0) for x in loads)
        other = sum(x.get("WAIT", 0) + x.get("TOOL", 0) + x.get("OTHER", 0) for x in loads)
        ot = sum(x.get("OT", 0) for x in loads)
        active = sum(1 for x in loads if x.get("WORK", 0) > 0)
        cap = p.cap_executable if purpose == "EXECUTABLE" else p.cap_forecast
        rows.append({"pool_id": p.id, "team": p.key.team, "date": p.key.start_date, "shift": p.key.shift_number, "shift_id": shift_id,
                     "synthetic": p.synthetic, "denominator": len(members), "activation_cap": cap, "activated": active,
                     "activation_pct": None if cap == 0 else ratio_text(Fraction(active * 100, cap)),
                     "regular_capacity_minutes": cap_minutes, "work_minutes": work, "travel_minutes": travel, "other_duty_minutes": other,
                     "ot_minutes": ot, "planned_person_minutes": demand.get((p.key.team, shift_id), 0),
                     "utilization_pct": None if cap_minutes == 0 else ratio_text(Fraction((work + travel + other) * 100, cap_minutes))})
    bottlenecks: dict[str, int] = defaultdict(int)
    for o in plan.job_dispositions:
        if o.disposition.value in ("deferred", "blocked"):
            for r in o.reasons:
                bottlenecks[r.split(":")[0]] += 1
    data = {"date": day, "purpose": purpose, "pools": rows,
            "unscheduled_reason_codes": dict(sorted(bottlenecks.items(), key=lambda kv: (-kv[1], kv[0]))),
            "note": "utilization (minutes against regular capacity) is distinct from activation (distinct people against the 85% cap)"}
    return Projection(canon.base_status() if rows else "empty", data, list(canon.reasons), None, canon.source_run_id, canon.plan_id(purpose),
                      purpose=purpose)


# ---------------------------------------------------------------------- commitments and economics

def commitments(canon: Canon, ctx: RequestContext) -> Projection:
    from ..engine.commitments import DeliveryCommitments

    services = canon.services
    if not services.repositories.available:
        return Projection("blocked", None, list(canon.reasons))
    store = DeliveryCommitments(services.repositories, services.settings.scope_key, services.policy)
    lines = []
    for line in store.lines():
        state, version = store.get(line)
        lines.append({"line": line, "original_date": state.original_date, "current_date": state.current_date, "raw_projection": state.raw_projection,
                      "pending_target": state.pending_target, "confirmations": state.confirmations, "last_plan_id": state.last_plan_id,
                      "in_cohort": state.in_cohort, "risk": state.risk, "alert": state.alert, "version": version,
                      "changes": [{"event_id": ev.id, "kind": ev.kind, "plan_id": ev.plan_id, "from_date": ev.from_date, "to_date": ev.to_date,
                                   "from_version": ev.from_version, "detail": ev.detail} for ev in store.events(line)]})
    tasks = []
    plan = canon.plan("EXECUTABLE")
    jobs, phases = canon.jobs(), canon.phases()
    if plan is not None:
        for r in plan.commitment_results:
            j, p = jobs.get(r.job), phases.get(r.phase)
            if not team_allowed(ctx, phase_team(p)):
                continue
            tasks.append({"job_id": r.job, "line": j.key.line if j else None, "soi": j.key.native_id if j else None, "phase": r.phase,
                          "phase_label": p.label if p else r.phase, "day_offset": r.day_offset, "outcome": r.outcome, "attempt1": r.attempt1,
                          "attempt2": r.attempt2, "reason": r.reason, "incumbent_assignment_id": r.incumbent_assignment_id,
                          "new_assignment_id": r.new_assignment_id})
    reasons = list(canon.reasons)
    if not lines:
        reasons.append(reason("NO_DELIVERY_COMMITMENTS", "No accepted plan has established delivery commitments yet."))
    outcomes: dict[str, int] = defaultdict(int)
    for t in tasks:
        outcomes[t["outcome"]] += 1
    status = canon.base_status() if (lines or tasks) else "empty"
    return Projection(status, {"delivery": lines, "tasks": tasks, "task_outcomes": dict(sorted(outcomes.items()))}, reasons, None,
                      canon.source_run_id, canon.plan_id("EXECUTABLE"), purpose="EXECUTABLE" if plan is not None else None)


def economics(canon: Canon, ctx: RequestContext) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    plans = {}
    for purpose in ("EXECUTABLE", "FORECAST"):
        plan = canon.plan(purpose)
        if plan is not None:
            c = plan.costs
            plans[purpose] = {"plan_id": canon.plan_id(purpose), "known_total_cents": frac(c.known_total_cents),
                              "conditional_total_cents": frac(c.conditional_total_cents), "controllable_known_cents": frac(c.controllable_known_cents),
                              "unknown_lines": list(c.unknown_lines), "complete": c.complete, "currency": c.currency,
                              "lines": [_cost(lc) | {"line": lc.line} for lc in c.lines]}
    terms = [economics_term(e) for e in sorted(canon.snapshot.economics, key=lambda e: e.line)]
    reasons = list(canon.reasons)
    if not plans:
        reasons.append(canon.plan_reason("FORECAST"))
    return Projection(canon.base_status() if (plans or terms) else "empty", {"plans": plans, "terms": terms,
                                                                            "note": "unknown liability stays unknown, never $0"},
                      reasons, None, canon.source_run_id, canon.plan_id("FORECAST") or canon.plan_id("EXECUTABLE"),
                      purpose="FORECAST" if "FORECAST" in plans else ("EXECUTABLE" if plans else None))


# ---------------------------------------------------------------------- shortages and quality

def shortage_row(canon: Canon, s: Any) -> dict[str, Any]:
    jobs = canon.jobs()
    rel = next((r for r in canon.snapshot.releases if r.id == s.release_id), None) if s.release_id else None

    def lab(jid: str | None) -> dict[str, Any] | None:
        j = jobs.get(jid) if jid else None
        return None if j is None else {"job_id": j.key.id, "label": f"{j.key.line}/{j.key.native_id}", "team": j.team}

    return {"record_id": s.record_id, "line": s.line, "used_on": s.used_on, "ecd": s.ecd, "team": s.team, "reason_code": s.reason_code,
            "status": s.status, "created": s.created, "root_job": lab(s.root_job), "resolved_job": lab(s.resolved_job),
            "affected_jobs": [lab(j) for j in s.affected_jobs],
            "release": None if rel is None else {"release_id": rel.id, "status": rel.status.value, "ready_at": rel.ready_at, "forecast_at": rel.forecast_at,
                                                  "precision": rel.precision, "label": rel.label},
            "reasons": list(s.reasons)}


def shortages(canon: Canon, ctx: RequestContext) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    rows = [shortage_row(canon, s) for s in sorted(canon.snapshot.shortages, key=lambda s: (s.line, s.record_id))]
    by_status: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r["status"]] += 1
    return Projection(canon.base_status() if rows else "empty",
                      {"shortages": rows, "by_status": dict(sorted(by_status.items())),
                       "note": "an ETA is a conditional forecast; only confirmed receipt makes work executable"},
                      list(canon.reasons), None, canon.source_run_id)


def quality(canon: Canon, ctx: RequestContext) -> Projection:
    missing = _need_snapshot(canon)
    if missing:
        return missing
    jobs, people = canon.jobs(), canon.people()
    plan = canon.plan("EXECUTABLE")
    by_phase = {a.phase: a for a in plan.assignments} if plan else {}
    holds = []
    for p in sorted(canon.snapshot.phases, key=lambda x: (x.line, x.job, x.kind.value, x.ordinal)):
        if p.kind not in (PhaseKind.IP_QA, PhaseKind.FINAL_QA, PhaseKind.OTHER_BUYOFF):
            continue
        j = jobs.get(p.job)
        a = by_phase.get(p.key.id)
        holds.append({"phase_id": p.key.id, "job_id": p.job, "kind": p.kind.value, "ordinal": p.ordinal, "line": p.line, "soi": j.key.native_id if j else None,
                      "label": p.label, "work_minutes": p.work_minutes, "status": p.status, "team": phase_team(p),
                      "inspection_basis": j.inspection_trigger.basis if j and j.inspection_trigger else None,
                      "assignment": None if a is None else {"start": a.start, "end": a.end, "shift_id": a.shift_id,
                                                            "inspectors": [person_view(canon, ctx, r.person, phase_team(p), people) for r in a.crew]},
                      "blocked_reasons": list(p.blocked_reasons)})
    demand: dict[str, int] = defaultdict(int)
    for hd in holds:
        if hd["assignment"]:
            demand[hd["assignment"]["shift_id"]] += hd["work_minutes"]
    qa_pools = [{"pool_id": p.id, "date": p.key.start_date, "shift": p.key.shift_number, "inspectors": p.n_executable, "cap": p.cap_executable}
                for p in sorted(canon.snapshot.pools, key=lambda x: (x.key.start_date, x.key.shift_number)) if p.key.team == "QA"]
    data = {"holds": holds, "demand_minutes_by_shift": dict(sorted(demand.items())), "qa_pools": qa_pools,
            "counts": {"total": len(holds), "scheduled": sum(1 for hd in holds if hd["assignment"]),
                       "blocked": sum(1 for hd in holds if hd["blocked_reasons"])}}
    return Projection(canon.base_status() if holds else "empty", data, list(canon.reasons), None, canon.source_run_id, canon.plan_id("EXECUTABLE"),
                      purpose="EXECUTABLE" if plan else None)


# ---------------------------------------------------------------------- performance, history, recognition, progression

def _visible_records(services: Any, ctx: RequestContext, shift_id: str | None, team: str | None) -> list[Any]:
    reg = registry(services)
    records = [r for r in reg.records.values() if team_allowed(ctx, r.team) and (shift_id is None or r.shift_id == shift_id)
               and (team is None or r.team == team)]
    return sorted(records, key=lambda r: (r.window.start, r.team))


def _evidence_counts(proof: Any) -> dict[str, Any] | None:
    if proof is None:
        return None
    return {"units": len(proof.unit_facts), "earned": sum(1 for f in proof.unit_facts if f.status == "EARNED"),
            "missed": sum(1 for f in proof.unit_facts if f.status == "MISSED"), "unknown": sum(1 for f in proof.unit_facts if f.status == "UNKNOWN"),
            "pending": sum(1 for f in proof.unit_facts if f.status == "PENDING"), "relieved": sum(1 for f in proof.unit_facts if f.relief),
            "offplan": len(proof.offplan), "oos_charged": sum(1 for o in proof.oos if o.charged), "coverage": list(proof.coverage),
            "observation_run_id": proof.observation_run_id}


def phase_labels(services: Any) -> dict[str, str]:
    """Readable phase labels from the current admitted snapshot (empty when none is cached or admitted)."""
    canon = Canon.load(services)
    return {p.key.id: p.label for p in canon.snapshot.phases} if canon.snapshot is not None else {}


def unit_view(f: Any, labels: dict[str, str]) -> dict[str, Any]:
    return {"unit_id": f.unit_id, "phase": f.phase, "label": labels.get(f.phase, f.phase), "segment": f.segment, "team": f.team, "effort": f.effort,
            "origin": f.origin, "status": f.status, "completed_at": f.completed_at, "relief": f.relief, "oos_charged": f.oos_charged,
            "reasons": list(f.reasons)}


def performance(services: Any, ctx: RequestContext, *, shift_id: str | None, team: str | None) -> Projection:
    """One canonical evaluation per visible goal (the persisted revision) and the pooled view over them."""
    from ..performance import history
    from ..performance.score import aggregate, summary

    if team is not None:
        require_team(ctx, team)                                                 # scope before fetch
    check_shift_id(shift_id)
    if not services.repositories.available:
        return Projection("blocked", None, [reason("REPOSITORY_UNAVAILABLE", "No repository is configured.")])
    if not ctx.scope.all_teams and not ctx.scope.teams:
        return Projection("empty", {"shift_id": shift_id, "goals": [], "pooled": None}, [reason("AGGREGATES_ONLY", "Your scope has no team detail.")])
    scope = services.settings.scope_key
    records = _visible_records(services, ctx, shift_id, team)
    labels = phase_labels(services)
    goals, members = [], []
    for r in records:
        e = history.current(services, scope, history.series_for_goal(services, r.goal_id))
        proof = history.evidence(services, e.evidence_hash) if e is not None else None
        goals.append({"team": r.team, "shift_id": r.shift_id, "goal_id": r.goal_id, "plan_id": r.plan_id, "B0": r.B0,
                      "window": {"start": r.window.start, "end": r.window.end, "credit_deadline": r.window.credit_deadline},
                      "evaluation": None if e is None else summary(e), "evidence": _evidence_counts(proof),
                      "units": [] if proof is None else [unit_view(f, labels) for f in proof.unit_facts],
                      "offplan": [] if proof is None else [{"job": o.job, "phase": o.phase, "label": labels.get(o.phase, o.phase), "effort": o.effort,
                                                            "completed_at": o.completed_at, "reasons": list(o.reasons)} for o in proof.offplan],
                      "oos": [] if proof is None else [{"violation_id": o.violation_id, "phase": o.phase, "label": labels.get(o.phase, o.phase),
                                                        "affected_effort": o.affected_effort, "discretionary": o.discretionary, "planned": o.planned,
                                                        "charged": o.charged, "reason": o.reason} for o in proof.oos],
                      "relief": None if proof is None else proof.relief})
        if e is not None:
            members.append(e)
    reasons: list[dict[str, Any]] = []
    pooled = None
    evaluation_id = None
    if len(members) == 1:
        pooled = summary(members[0]) | {"members": [members[0].id], "persisted": True}
        evaluation_id = members[0].id
    elif members:
        goal_ids = tuple(sorted({g for m in members for g in m.goal_ids}))
        stored = history.current(services, scope, history.series_key(scope, "AGGREGATE", goal_ids, services.policy.id))
        expected = content_hash({"members": sorted((m.id, m.evidence_hash) for m in members)})
        if stored is not None and stored.evidence_hash == expected:
            pooled = summary(stored) | {"members": sorted(m.id for m in members), "persisted": True}
            evaluation_id = stored.id
        else:
            agg = aggregate(services, members, label="POOLED (your scope)", as_of=max(m.cutoff for m in members), persist=False)
            pooled = summary(agg) | {"members": sorted(m.id for m in members), "persisted": False}
            reasons.append(reason("AGGREGATE_NOT_STORED", "The pooled view sums the persisted member revisions exactly with the canonical formula; "
                                  "no stored aggregate matches this member set."))
    if not records:
        reasons.append(reason("NO_GOALS_IN_SCOPE", "No frozen goal of record in your scope for this selection."))
    elif not members:
        reasons.append(reason("NOT_EVALUATED", "Goals exist but no canonical evaluation has been recorded yet.", "Collect observations and evaluate the shift."))
    status = "ready" if members else ("empty" if not records else "unknown")
    return Projection(status, {"shift_id": shift_id, "team": team, "goals": goals, "pooled": pooled, "policy_id": services.policy.id,
                               "note": "one canonical evaluation per goal; pooled totals sum exact components before division"},
                      reasons, evaluation_id=evaluation_id)


def performance_history(services: Any, ctx: RequestContext, *, team: str | None) -> Projection:
    from ..performance import history
    from ..performance.score import summary

    if team is not None:
        require_team(ctx, team)
    if not services.repositories.available:
        return Projection("blocked", None, [reason("REPOSITORY_UNAVAILABLE", "No repository is configured.")])
    scope = services.settings.scope_key
    series = []
    for r in _visible_records(services, ctx, None, team):
        revs = history.revisions(services, scope, history.series_for_goal(services, r.goal_id))
        if revs:
            series.append({"team": r.team, "shift_id": r.shift_id, "goal_id": r.goal_id, "current_evaluation_id": revs[-1].id,
                           "revisions": [summary(e) for e in revs]})
    return Projection("ready" if series else "empty", {"series": series, "note": "revisions append; a correction never rewrites an earlier revision"},
                      [] if series else [reason("NO_HISTORY", "No evaluation history in your scope.")])


def recognition(services: Any, ctx: RequestContext, *, team: str | None) -> Projection:
    from ..analytics.progression import _chains

    if team is not None:
        require_team(ctx, team)
    if not services.repositories.available:
        return Projection("blocked", None, [reason("REPOSITORY_UNAVAILABLE", "No repository is configured.")])
    rows = []
    for chain in _chains(services).values():
        for ev in chain:
            if not team_allowed(ctx, ev.team) or (team is not None and ev.team != team):
                continue
            g = ev.frozen_gate_context
            rows.append({"event_id": ev.id, "dedupe_key": ev.dedupe_key, "type": ev.event_type, "team": ev.team, "goal_id": ev.goal_id,
                         "units": list(ev.earning_unit_ids), "value": frac(ev.value), "revision": ev.revision, "supersedes": ev.supersedes,
                         "evidence": list(ev.evidence), "policy_id": ev.policy_id,
                         "gate": None if g is None else {"basis": g.basis, "gate_date": g.gate_date, "effective_gate_date": g.effective_gate_date,
                                                         "valuation_date": g.valuation_date, "behind_days": g.behind_days, "behind_raw": frac(g.behind_raw),
                                                         "base_value": frac(g.base_value), "bonus_value": frac(g.bonus_value)},
                         "created_at": ev.created_at})
    rows.sort(key=lambda r: (r["created_at"], r["dedupe_key"]))
    return Projection("ready" if rows else "empty", {"events": rows, "note": "frozen value earned once; corrections append, never rewrite"},
                      [] if rows else [reason("NO_RECOGNITION", "No recognition events in your scope yet.")])


def progression_view(services: Any, ctx: RequestContext, *, team: str | None) -> Projection:
    from ..analytics.progression import BADGES, inflation_monitor, team_progress
    from ..contracts.evaluation import BadgeAward

    if team is not None:
        require_team(ctx, team)
    if not services.repositories.available:
        return Projection("blocked", None, [reason("REPOSITORY_UNAVAILABLE", "No repository is configured.")])
    repos = services.repositories
    badges = [b for b in (repos.get_typed(BADGES, i, "BadgeAward", BadgeAward) for i in repos.list_ids(BADGES)) if b is not None]
    monitor = inflation_monitor(services)
    teams = sorted({b.team for b in badges} | {r["team"] for r in monitor})
    teams = [t for t in teams if team_allowed(ctx, t) and (team is None or t == team)]
    rows = []
    for t in teams:
        prog = team_progress(services, t)
        rows.append({"team": t, "xp": frac(prog["xp"]), "level": prog["level"], "progress_in_level": frac(prog["progress_in_level"]),
                     "next_threshold": prog["next_threshold"], "events": prog["events"], "qualification_effect": "none",
                     "badges": [{"badge": b.badge, "period": b.period, "revision": b.revision, "revoked": b.revoked, "statement": b.statement,
                                 "evidence": list(b.evidence_ids), "awarded_at": b.awarded_at}
                                for b in sorted(badges, key=lambda b: (b.awarded_at, b.id)) if b.team == t]})
    return Projection("ready" if rows else "empty", {"teams": rows, "inflation": [r for r in monitor if r["team"] in teams],
                                                    "note": "XP never certifies, qualifies or assigns anyone"},
                      [] if rows else [reason("NO_PROGRESSION", "No progression in your scope yet.")])


def claims_view(services: Any, ctx: RequestContext, *, shift_id: str | None, team: str | None) -> Projection:
    """The excusal review queue: effective claims, decisions and the decision version per visible goal."""
    from ..performance.actuals import CLAIMS, DECISIONS, ObservationStore, knowledge_time
    from ..performance.disruption import effective_claims

    if team is not None:
        require_team(ctx, team)
    check_shift_id(shift_id)
    if not services.repositories.available:
        return Projection("blocked", None, [reason("REPOSITORY_UNAVAILABLE", "No repository is configured.")])
    store = ObservationStore(services.repositories, services.settings.scope_key)
    at = knowledge_time(services)
    all_claims, all_decisions = store.known(CLAIMS, at), store.known(DECISIONS, at)
    rows = []
    for r in _visible_records(services, ctx, shift_id, team):
        claims = [c for c in all_claims if c.goal_id == r.goal_id]
        decisions = [d for d in all_decisions if d.goal_id == r.goal_id]
        if not claims and not decisions:
            continue
        units = {u.id: u for u in r.units}
        effective = {c.id: c for c in effective_claims(None, r.goal_id, store_claims=claims).values()}
        superseded = {d.supersedes for d in decisions if d.supersedes}
        rows.append({"team": r.team, "shift_id": r.shift_id, "goal_id": r.goal_id, "decision_version": len(decisions),
                     "claims": [{"claim_id": c.id, "cause": c.cause, "status": c.status, "attribution": c.attribution,
                                 "units": [{"unit_id": u, "phase": units[u].phase if u in units else None,
                                            "minutes": units[u].standard_person_minutes if u in units else None} for u in c.units],
                                 "origin_team": c.origin_team, "executing_team": c.executing_team, "evidence": list(c.evidence), "notes": c.notes,
                                 "reported_by": c.reported_by, "reported_at": c.reported_at, "supersedes": c.supersedes}
                                for c in sorted(effective.values(), key=lambda c: (c.reported_at, c.id))],
                     "decisions": [{"decision_id": d.id, "decision": d.decision, "claim_ids": list(d.claim_ids), "approved_units": list(d.approved_units),
                                    "authority": d.authority, "authority_role": d.authority_role, "decision_at": d.decision_at, "reason": d.reason,
                                    "review_track": d.review_track, "exception": d.exception, "supersedes": d.supersedes,
                                    "effective": d.id not in superseded} for d in sorted(decisions, key=lambda d: (d.decision_at, d.id))]})
    return Projection("ready" if rows else "empty", {"goals": rows, "known_at": at,
                                                    "note": "claims are evidence; relief comes only from a reviewed decision"},
                      [] if rows else [reason("NO_CLAIMS", "No disruption claims in your scope for this selection.")])


def ie_reviews_view(services: Any, ctx: RequestContext) -> Projection:
    from ..contracts.events import IEReview
    from ..performance.history import IE_REVIEWS

    if not services.repositories.available:
        return Projection("blocked", None, [reason("REPOSITORY_UNAVAILABLE", "No repository is configured.")])
    repos = services.repositories
    rows = []
    for rid in sorted(repos.list_ids(IE_REVIEWS)):
        rv = repos.get_typed(IE_REVIEWS, rid, "IEReview", IEReview)
        if rv is None or rv.scope != services.settings.scope_key:
            continue
        rows.append({"review_id": rv.id, "job_id": rv.job, "standard_revision": rv.standard_revision, "proposed_correction": rv.proposed_correction,
                     "reason": rv.reason, "requested_by": rv.requested_by, "requested_at": rv.requested_at, "status": rv.status,
                     "affects_issued_goals": rv.affects_issued_goals})
    rows.sort(key=lambda r: (r["requested_at"], r["review_id"]))
    return Projection("ready" if rows else "empty", {"reviews": rows, "note": "proposals only; issued goals and evaluations never change"},
                      [] if rows else [reason("NO_IE_REVIEWS", "No IE review proposals recorded.")])


def now_utc(services: Any) -> datetime:
    return services.clock.now()
