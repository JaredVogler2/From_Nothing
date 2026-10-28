"""Approved feasible emergency additions to a goal of record (S13 GOAL FREEZE, S15 POST /amendments).

An amendment adds one whole open phase to one frozen team-shift goal, so B = B0 + additions. The
original B0 and units are never changed. The command is a worker run, so the browser never mutates a
plan. ``amend_goal`` decides it under a per-goal lock:

1. **Plan and version.** ``plan_id`` must be the goal's obligation plan of record, verified end to
   end. ``expected_version`` must equal the goal's amendment count (optimistic concurrency): a
   concurrent amendment makes the later one ``STALE_VERSION``.
2. **Authority.** The requester's server-owned scope (injected at enqueue, never client-supplied)
   must cover the goal's team.
3. **Work.** The phase exists in the current admitted facts and is open, not already complete, not a
   release, owned by the goal's team, not an original unit and not already amended. Its job is
   executable now. Every hard predecessor is verified complete (AND releases recursively), every
   part release is confirmed, and no blocking shortage applies.
4. **Feasibility.** The shift has not ended, the line calendar admits it, and either the current
   accepted EXECUTABLE plan already assigns the phase in that shift, or enough admitted, non-synthetic
   pool members hold the full certificate bundle over the shift and each has regular-capacity
   headroom for the whole work after their planned duties.

The effort is the phase's sourced standard person minutes, never a client number. The feasibility
facts are hashed into ``feasibility_ref`` and returned in the run receipt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..calendar import parse_shift_id
from ..contracts.events import GoalAmendment
from ..contracts.planning import EarningUnit
from ..domain import DomainError, PhaseKind, Purpose, ReleaseKind, ReleaseStatus, aware_utc, digest_id, minute_ceil, to_minute

CHANGE_KINDS = ("ADD_EMERGENCY_WORK",)


def _ready(inp: Any, node: str, now: int, trail: frozenset[str] = frozenset()) -> tuple[bool, str]:
    """Whether a graph node is satisfied now: verified phase, confirmed release, or an AND release whose inputs all are."""
    rel = inp.releases.get(node)
    if rel is not None:
        ok = rel.status is ReleaseStatus.CONFIRMED and rel.ready_at is not None and minute_ceil(rel.ready_at) <= now
        return ok, "" if ok else f"PART_NOT_RECEIVED: {rel.label} is {rel.status.value}"
    phase = inp.phases.get(node)
    if phase is None:
        return False, f"PREDECESSOR_UNKNOWN: {node}"
    if inp.satisfied(phase) is not None:
        return True, ""
    if phase.kind is PhaseKind.RELEASE and node not in trail:
        for e in inp.hard_in.get(node, ()):
            ok, why = _ready(inp, e.pred_phase, now, trail | {node})
            if not ok:
                return False, why
        return bool(inp.hard_in.get(node)), "" if inp.hard_in.get(node) else f"RELEASE_WITHOUT_INPUTS: {node}"
    return False, f"PREDECESSOR_INCOMPLETE: {phase.label} is not verified complete"


def amend_goal(services: Any, *, request_id: str, requested_by: str, plan_id: str, expected_version: int, change: dict[str, Any],
               reason: str, allowed_teams: Any, at: datetime) -> tuple[GoalAmendment, dict[str, Any]]:
    from ..data.acquisition import snapshot_for_run, source_head_name
    from ..publication.service import current_plan
    from ..validation.input import ValidationInput, overlap_minutes
    from .actuals import AMENDMENTS, ObservationStore
    from .lifecycle import GoalRegistry

    at = aware_utc(at, "at")
    scope = services.settings.scope_key
    policy = services.policy
    kind = change.get("kind")
    if kind not in CHANGE_KINDS:
        raise DomainError("UNSUPPORTED_CHANGE", f"amendments support {list(CHANGE_KINDS)}; relief is an excusal decision, not an amendment")
    team, shift_id, phase_id = change.get("team"), change.get("shift_id"), change.get("phase_id")
    if not all(isinstance(x, str) and x for x in (team, shift_id, phase_id)):
        raise DomainError("BAD_CHANGE", "an emergency addition names team, shift_id and phase_id")
    try:
        _, day, number = parse_shift_id(shift_id)
    except ValueError:
        raise DomainError("BAD_CHANGE", "shift_id must look like SITE:YYYY-MM-DD:S1") from None
    if len(reason.strip()) < policy.int("disruption.notes_min_chars"):
        raise DomainError("REASON_REQUIRED", "an emergency addition records a substantive reason")
    if allowed_teams != "*" and team not in set(allowed_teams or ()):
        raise DomainError("OUT_OF_SCOPE", f"team {team} is outside the requester's scope")

    registry = GoalRegistry.load(services)
    record = registry.records.get((team, shift_id)) or registry.record(team, shift_id)
    if record is None:
        raise DomainError("GOAL_NOT_FOUND", f"no accepted obligation plan covers {team} {shift_id}")
    if record.plan_id != plan_id:
        raise DomainError("PLAN_NOT_OF_RECORD", f"the goal of record for {team} {shift_id} was frozen by {record.plan_id}")
    window = record.window
    if at >= window.end:
        raise DomainError("SHIFT_ENDED", "work completed after the shift is offplan output, not an emergency addition")
    if any(u.phase == phase_id for u in record.original_units):
        raise DomainError("ALREADY_IN_GOAL", "the phase is already an original frozen unit of this goal")

    head = services.repositories.head(source_head_name(scope))
    if head is None:
        raise DomainError("SOURCE_UNAVAILABLE", "no admitted current source run")
    facts = snapshot_for_run(services, head.record_id)
    inp = ValidationInput.build(facts, policy, Purpose.EXECUTABLE)
    now = to_minute(at)
    phase = inp.phases.get(phase_id)
    if phase is None:
        raise DomainError("PHASE_NOT_FOUND", "the phase is not in the current admitted facts")
    if phase.kind is PhaseKind.RELEASE or phase.status != "OPEN" or phase.work_minutes <= 0:
        raise DomainError("NOT_WORK", f"{phase.label} is not open work")
    if inp.satisfied(phase) is not None:
        raise DomainError("ALREADY_COMPLETE", "current facts show this work verified complete")
    demand = phase.role_demands[0] if phase.role_demands else None
    owner = demand.team if demand else phase.team
    if owner != team:
        raise DomainError("TEAM_MISMATCH", f"{phase.label} is {owner} work, not {team}'s")
    effort = phase.standard_person_minutes
    if effort <= 0 or demand is None or demand.bundle.count <= 0:
        raise DomainError("EFFORT_UNKNOWN", "the phase has no sourced standard effort or crew demand")
    ok, _, why = inp.placeable(phase.job)
    if not ok:
        raise DomainError("NOT_EXECUTABLE", why[:300])
    for e in inp.hard_in.get(phase_id, ()):
        ready, why = _ready(inp, e.pred_phase, now)
        if not ready:
            code, _, detail = why.partition(": ")
            raise DomainError(code, detail)
    for node in sorted(inp.ancestors(phase_id, {})):
        rel = inp.releases.get(node)
        if rel is not None and rel.kind is ReleaseKind.PART and rel.status is not ReleaseStatus.CONFIRMED:
            raise DomainError("PART_NOT_RECEIVED", f"ancestor {rel.label} is {rel.status.value}")
    for s in facts.shortages:
        if s.status in ("UNKNOWN_ECD", "REOPENED") and phase.job in s.affected_jobs:
            raise DomainError("SHORTAGE_BLOCKED", f"shortage {s.record_id} is {s.status}")
    shift = inp.shift(shift_id)
    if shift is None:
        raise DomainError("SHIFT_UNKNOWN", f"{shift_id} is not a shift of this site")
    workday, code, detail = inp.workday(phase.line, day, number)
    if not workday:
        raise DomainError("CALENDAR_CLOSED", f"{code}: {detail}")

    current, _ = current_plan(services, scope, "EXECUTABLE")
    plan = None if current is None else current.artifact.candidate
    planned = next((a for a in (plan.assignments if plan else ()) if a.phase == phase_id and a.shift_id == shift_id), None)
    if planned is not None:
        evidence = {"basis": "ASSIGNED_IN_ACCEPTED_PLAN", "plan_id": current.published.id, "assignment_id": planned.id,
                    "crew": [r.person for r in planned.crew]}
    else:
        pool = inp.pool_by_key.get((team, shift.start_date, shift.number))
        if pool is None:
            raise DomainError("NO_POOL", f"no admitted {team} pool for {shift_id}")
        start, end = max(now, shift.start), shift.end
        remaining = overlap_minutes(start, end, shift.regular_windows)
        booked: dict[str, int] = {}
        for d in (plan.duties if plan else ()):
            if d.shift_id == shift_id:
                booked[d.person] = booked.get(d.person, 0) + d.regular_minutes
        fit = []
        for person in sorted(inp.admitted_members(pool)):
            avail = inp.availability.get((person, pool.id))
            held, _ = inp.bundle_held(person, demand.bundle, start, end)
            headroom = (getattr(avail, "regular_cap", 0) if avail is not None else 0) - booked.get(person, 0)
            if held and headroom >= phase.work_minutes and remaining >= phase.work_minutes:
                fit.append(person)
        if len(fit) < demand.bundle.count:
            raise DomainError("INFEASIBLE", f"{len(fit)} qualified pool members have headroom for {phase.work_minutes} min; "
                                            f"{demand.bundle.count} are required")
        evidence = {"basis": "QUALIFIED_POOL_HEADROOM", "pool_id": pool.id, "qualified_with_headroom": fit, "required": demand.bundle.count,
                    "work_minutes": phase.work_minutes, "remaining_regular_minutes": remaining}
    feasibility_ref = digest_id("FEAS", facts.source_run_id, phase_id, shift_id, evidence)

    store = ObservationStore(services.repositories, scope)
    unit = EarningUnit(digest_id("EU", record.goal_id, "AMD", request_id, phase_id, 0), f"AMD:{request_id}", phase.job, phase_id, 0, team, shift_id,
                       effort, team, "EMERGENCY_ADDITION")
    amendment = GoalAmendment(digest_id("AMD", scope, "cmd", request_id), scope, record.goal_id, (unit,), effort, requested_by, at, reason[:2000],
                              feasibility_ref, "SNAPSHOT_STANDARD_PERSON_MINUTES", at, f"command:{request_id}")
    with services.repositories.store.lock(f"amendment-{record.goal_id}"):
        prior = [a for a in store.all(AMENDMENTS) if a.goal_id == record.goal_id and a.id != amendment.id]
        if store.get(AMENDMENTS, amendment.id) is None and len(prior) != expected_version:
            raise DomainError("STALE_VERSION", f"goal {record.goal_id} has {len(prior)} amendments, not {expected_version}")
        if any(u.phase == phase_id for a in prior for u in a.units):
            raise DomainError("ALREADY_AMENDED", "an earlier amendment already added this phase")
        if store.put(AMENDMENTS, amendment) == "CONFLICT":
            raise DomainError("REVISION_CONTENT_CONFLICT", "this request id was already recorded with different content")
    return amendment, {"source_run_id": facts.source_run_id, **evidence}


def worker_amendment(services: Any, run: Any) -> dict[str, Any]:
    from .actuals import knowledge_time

    req = run.request
    server = req.get("_server") or {}
    amendment, evidence = amend_goal(services, request_id=run.id, requested_by=run.requested_by, plan_id=str(req.get("plan_id", "")),
                                     expected_version=int(req.get("expected_version", -1)), change=dict(req.get("change") or {}),
                                     reason=str(req.get("reason", "")), allowed_teams=server.get("teams", ()),
                                     at=knowledge_time(services, run.created_at))
    return {"status": "recorded", "amendment_id": amendment.id, "goal_id": amendment.goal_id, "added_minutes": amendment.standard_person_minutes,
            "units": [u.id for u in amendment.units], "feasibility_ref": amendment.feasibility_ref, "feasibility": evidence,
            "b0_unchanged": True, "note": "B = B0 + approved feasible additions; original frozen units are never changed"}
