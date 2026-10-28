"""Current-action dispatch check (S13 DISPATCH, P16).

``dispatch_check(services, plan_id, action_id)`` decides whether one assignment of an accepted
plan may be released now. It uses the **current** source facts, the latest admitted source run,
never the facts frozen in the plan. Checks:

* **The plan.** The accepted immutable head for its scope and purpose, verified end to end, with
  an executable capability. A stale or forecast plan can be viewed but is never executable.
* **Freshness.** The current source is fresh (``dispatch.max_source_age_hours``) and not older
  than the plan's source.
* **The work.** The phase still exists and is open. The job is still executable: a reopened or
  new shortage, a hold or quarantine blocks it. Every hard predecessor is verified complete
  now, and every own or ancestor part release is confirmed now.
* **The crew.** Each person is still an admitted, non-synthetic member of the role's
  team-shift pool, so withdrawn attendance or leave blocks. Each still holds the whole
  certificate bundle over the action's interval under current holdings, so a revoked or expired
  certificate blocks. No current carry-in duty conflicts.
* **The calendar and CS facts.** The line calendar still admits the shift, the station's CS fact
  is still admitted from the current run, and the action's window has not passed.

A blocked action writes an idempotent repair request with its reasons. Accepted history (the
published artifact, goals and receipt) is never modified.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..domain import Purpose, ReleaseKind, ReleaseStatus, digest_id, minute_ceil, to_minute
from ..validation.input import ValidationInput
from .repository import REPAIRS, repository_for
from .serializer import decode_artifact, payload_sha256
from .service import current_plan
from .signing import Signer


@dataclass(frozen=True)
class RepairRequest:
    id: str
    plan_id: str
    action_id: str
    source_run_id: str
    reasons: tuple[tuple[str, str], ...]
    requested_at: datetime


@dataclass(frozen=True)
class DispatchDecision:
    plan_id: str
    action_id: str
    dispatchable: bool
    reasons: tuple[tuple[str, str], ...]
    source_run_id: str | None
    repair_request_id: str | None
    checked_at: datetime

    def summary(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "action_id": self.action_id, "dispatchable": self.dispatchable,
                "decision": "DISPATCHABLE" if self.dispatchable else "BLOCKED", "reasons": [{"code": c, "detail": d} for c, d in self.reasons],
                "current_source_run_id": self.source_run_id, "repair_request_id": self.repair_request_id, "checked_at": self.checked_at.isoformat()}


def _current_facts(services: Any):
    from ..data.acquisition import snapshot_for_run, source_head_name

    head = services.repositories.head(source_head_name(services.settings.scope_key))
    return None if head is None else snapshot_for_run(services, head.record_id)


def dispatch_check(services: Any, plan_id: str, action_id: str, *, now: datetime | None = None, signer: Signer | None = None) -> DispatchDecision:
    now = now or services.clock.now()
    repo = repository_for(services)
    reasons: list[tuple[str, str]] = []
    try:
        published, payload = repo.published(plan_id), repo.payload(plan_id)
    except Exception as exc:  # noqa: BLE001 - tampered storage never dispatches
        return DispatchDecision(plan_id, action_id, False, (("SECURITY_FAILURE", f"stored publication unreadable: {type(exc).__name__}"),), None, None, now)
    if published is None or payload is None:
        return DispatchDecision(plan_id, action_id, False, (("PLAN_NOT_FOUND", "no accepted publication with this id"),), None, None, now)
    current, why = current_plan(services, published.scope, published.purpose.value, signer=signer)
    if current is None:
        reasons.append(("PLAN_NOT_VERIFIED", why))
    elif current.published.id != plan_id:
        reasons.append(("STALE_PLAN", f"{plan_id} is accepted history; the current plan is {current.published.id}"))
    data = payload.payload_text.encode("utf-8")
    if payload_sha256(data) != published.payload_hash:
        return DispatchDecision(plan_id, action_id, False, (("SECURITY_FAILURE", "stored payload does not match its signed hash"),), None, None, now)
    artifact = decode_artifact(data)
    if artifact.capability != "EXECUTABLE_DISPATCH":
        reasons.append(("FORECAST_ONLY", f"{artifact.capability} plans are never dispatched as executable work"))
    action = next((a for a in artifact.candidate.assignments if a.id == action_id), None)
    if action is None:
        return DispatchDecision(plan_id, action_id, False, tuple(reasons) + (("ACTION_NOT_FOUND", "not an assignment of this plan"),), None, None, now)
    facts = _current_facts(services)
    if facts is None:
        reasons.append(("SOURCE_UNAVAILABLE", "no admitted current source run"))
        return _decide(services, plan_id, action_id, reasons, None, now)
    if facts.cutoff < _plan_cutoff(services, artifact):
        reasons.append(("SOURCE_OLDER_THAN_PLAN", "current facts precede the plan's source"))
    reasons += assess_action(services, action, facts, ValidationInput.build(facts, services.policy, Purpose.EXECUTABLE), now)
    return _decide(services, plan_id, action_id, reasons, facts.source_run_id, now)


def assess_action(services: Any, action: Any, facts: Any, inp: ValidationInput, now: datetime) -> list[tuple[str, str]]:
    """Why one accepted assignment cannot be released now, from current facts only (empty: dispatchable).

    Pure: it writes nothing, so read projections (the Team Lead ranking) share this exact rule with
    ``dispatch_check``, which adds the plan-head checks and records repair requests.
    """
    reasons: list[tuple[str, str]] = []
    if now - facts.cutoff > timedelta(hours=services.policy.int("dispatch.max_source_age_hours")):
        reasons.append(("SOURCE_STALE", f"current facts cut off at {facts.cutoff.isoformat()}"))
    start, end = to_minute(action.start), to_minute(action.end)
    if to_minute(now) >= end:
        reasons.append(("ACTION_WINDOW_PASSED", f"the action ended at {action.end.isoformat()}"))
    phase = inp.phases.get(action.phase)
    if phase is None:
        reasons.append(("PHASE_GONE", "the phase is no longer in the current source"))
        return reasons
    if inp.satisfied(phase) is not None:
        reasons.append(("ALREADY_COMPLETE", "current facts show this work verified complete"))
    ok, _, why = inp.placeable(action.job)
    if not ok:
        reasons.append(("NOT_EXECUTABLE", why[:300]))
    for e in inp.hard_in.get(action.phase, ()):
        rel = inp.releases.get(e.pred_phase)
        if rel is not None:
            if not (rel.status is ReleaseStatus.CONFIRMED and rel.ready_at is not None and minute_ceil(rel.ready_at) <= to_minute(now)):
                reasons.append(("PART_NOT_RECEIVED", f"{rel.label} is {rel.status.value}"))
            continue
        pred = inp.phases.get(e.pred_phase)
        if pred is None or inp.satisfied(pred) is None:
            reasons.append(("PREDECESSOR_INCOMPLETE", f"{e.type.value} predecessor {e.pred_phase} is not verified complete now"))
    for node in sorted(inp.ancestors(action.phase, {})):
        rel = inp.releases.get(node)
        if rel is not None and rel.kind is ReleaseKind.PART and rel.status is not ReleaseStatus.CONFIRMED:
            reasons.append(("PART_NOT_RECEIVED", f"ancestor {rel.label} is {rel.status.value}"))
    for s in facts.shortages:
        if s.status in ("UNKNOWN_ECD", "REOPENED") and action.job in s.affected_jobs:
            reasons.append(("SHORTAGE_BLOCKED", f"shortage {s.record_id} is {s.status}"))
    shift = inp.shift(action.shift_id)
    if shift is None:
        reasons.append(("SHIFT_UNKNOWN", f"{action.shift_id} is not a current shift"))
    else:
        ok_day, code, detail = inp.workday(action.line, shift.start_date, shift.number)
        if not ok_day:
            reasons.append(("CALENDAR_CHANGED", f"{code}: {detail}"))
        demand = phase.role_demands[0] if phase.role_demands else None
        pool = inp.pool_by_key.get((demand.team, shift.start_date, shift.number)) if demand else None
        members = inp.admitted_members(pool) if pool is not None else frozenset()
        for r in action.crew:
            if inp.synthetic(r.person):
                reasons.append(("SYNTHETIC_RESOURCE", f"{r.person} is virtual support: never executable"))
                continue
            if r.person not in members:
                reasons.append(("ATTENDANCE_WITHDRAWN", f"{r.person} is not an admitted member of the current {demand.team if demand else '?'} pool"))
            if demand is not None and demand.bundle.groups:
                held, missing = inp.bundle_held(r.person, demand.bundle, start, end)
                if not held:
                    reasons.append(("CERT_NOT_HELD", f"{r.person}: {missing}"))
            for ci in facts.carry_in:
                if ci.person == r.person and to_minute(ci.start) < end and start < to_minute(ci.end):
                    reasons.append(("DUTY_CONFLICT", f"{r.person} has current carry-in duty {ci.id}"))
    job = inp.jobs.get(action.job)
    cs = inp.cs.get((job.line, job.station)) if job is not None else None
    if cs is None or cs.status != "ADMITTED" or cs.source_run_id != facts.source_run_id:
        reasons.append(("CS_NOT_ADMITTED", "the station fact is not admitted in the current source run"))
    return reasons


def ranked_actions(services: Any, plan: Any, facts: Any, inp: ValidationInput, *, team: str, shift_id: str,
                   now: datetime) -> list[tuple[Any, list[tuple[str, str]]]]:
    """One team-shift's accepted actions in the server's ranking, each with its current dispatch reasons.

    This is the one ranking authority for the Team Lead view and for explanations: dispatchable first, then
    committed, then planned start, then the stable assignment id. Pure: it writes nothing.
    """
    out = []
    for a in plan.assignments:
        phase = inp.phases.get(a.phase)
        team_of = None if phase is None else (phase.role_demands[0].team if phase.role_demands else phase.team)
        if a.shift_id == shift_id and team_of == team:
            out.append((a, assess_action(services, a, facts, inp, now)))
    out.sort(key=lambda item: (bool(item[1]), not item[0].committed, item[0].start, item[0].id))
    return out


def _plan_cutoff(services: Any, artifact: Any) -> datetime:
    from ..data.acquisition import load_snapshot

    snap = load_snapshot(services.repositories, artifact.snapshot_id)
    return snap.cutoff if snap is not None else artifact.candidate.created_at


def _decide(services: Any, plan_id: str, action_id: str, reasons: list[tuple[str, str]], run_id: str | None, now: datetime) -> DispatchDecision:
    unique = tuple(dict.fromkeys(reasons))
    if not unique:
        return DispatchDecision(plan_id, action_id, True, (), run_id, None, now)
    rid = digest_id("RPR", plan_id, action_id, run_id or "", sorted(c for c, _ in unique))
    repos = services.repositories
    if repos.get_raw(REPAIRS, rid) is None:
        repos.put_record(REPAIRS, "RepairRequest", rid, RepairRequest(rid, plan_id, action_id, run_id or "", unique, now), created_at=now)
    return DispatchDecision(plan_id, action_id, False, unique, run_id, rid, now)


def cli_dispatch_check(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    decision = dispatch_check(services, args.plan, args.action)
    if decision.reasons and decision.reasons[-1][0] in ("PLAN_NOT_FOUND", "ACTION_NOT_FOUND"):
        return {"status": "invalid", **decision.summary()}
    return {"status": "ok", **decision.summary()}
