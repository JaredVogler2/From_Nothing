"""Isolated what-if scenarios (S16 SCENARIOS; the Scenario view of S15, P20 prerequisite of P21).

A scenario copies the immutable admitted snapshot of a source run into its own namespace, applies typed,
allowlisted changes, and runs the same rich MAX, duty ledger and independent validator twice under one bounded
budget: once on the unchanged copy (the same-settings baseline) and once on the changed copy. The comparison
isolates the change.

Allowlisted changes:

* ``virtual_staffing``: add 1-10 synthetic virtual people to one team-shift pool's forecast membership. They hold
  the team's union of required certificates, are FORECAST_ONLY and never executable.
* ``eta_forecast``: move an unconfirmed part release's forecast. A confirmed receipt is a fact, never edited.
* ``duration_statistic``: select another sourced duration statistic (P50, P75 or P90) through the policy toggle.
* ``daily_cost``: assume a daily delay cost for one aircraft (labeled SCENARIO_ASSUMED, never contractual).
* ``calendar_day``: open or close one line calendar day.
* ``travel_minutes``: assume another changed-aircraft travel time through the policy toggle. Tool capacity is a
  sourced fact (``tools.reservation_policy`` NONE_UNLESS_SOURCED), so a scenario cannot invent tools.
* ``task_priority``: rank up to 20 jobs first among ready work. The rank leads the dispatch key, so search moves
  cannot undo it, but graph, parts, QA, crews and defended commitments still decide what may start.

Anything else is refused as ``UNSUPPORTED_CHANGE``. Scenario candidates have purpose SCENARIO, which publication
refuses, so a scenario never moves a source or plan head and never touches frozen goals, commitments, actuals,
evaluations or recognition. A spoofed approval or mock flag in a change is rejected as an unknown field.

**Namespace.** A snapshot rebuilt under a toggled policy is never written to the live snapshot collection; the
changed copy is stored only in ``scenario_snapshots`` and results only in ``scenario_results``.

**Bounds.** At most 20 changes; the budget is the worker run's, split between the baseline and the changed copy.
A cancel request stops the run cooperatively (``RunCancelled``: the run ends cancelled and stores no result); a
baseline that overruns the budget ends TIMED_OUT without scheduling the changed copy. Results are immutable records
keyed by the worker run, so a retry of the same run returns the same result.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from fractions import Fraction
from typing import Any, Callable

from ..codec import content_hash, parse_utc, to_jsonable
from ..contracts.planning import Availability, Person, PlanningSnapshot, Pool, seal_snapshot
from ..domain import (AvailabilityBasis, DomainError, EvidenceRef, Holding, Interval, PersonKey, PersonOrigin, Purpose, ReleaseStatus,
                      RequirementStatus, aware_utc, digest_id, from_minute)

SCENARIO_SNAPSHOTS = "scenario_snapshots"
SCENARIO_RESULTS = "scenario_results"
MAX_CHANGES = 20
FIELDS = {
    "virtual_staffing": {"type", "team", "date", "shift", "add"},
    "eta_forecast": {"type", "release_id", "forecast_at"},
    "duration_statistic": {"type", "statistic"},
    "daily_cost": {"type", "line", "daily_rate_cents"},
    "calendar_day": {"type", "line", "date", "status"},
    "travel_minutes": {"type", "changed_aircraft_minutes"},
    "task_priority": {"type", "job_id", "rank"},
}
SINGLETONS = ("duration_statistic", "travel_minutes")
MAX_PRIORITY_RANK = 20
MAX_TRAVEL_MINUTES = 240
STATISTICS = ("P50", "P75", "P90")
CALENDAR_STATUSES = ("WORKING", "NONWORKING")


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    id: str
    scope: str
    run_id: str
    source_run_id: str
    base_snapshot_id: str
    scenario_snapshot_id: str | None
    changes: tuple[dict[str, Any], ...]
    requested_by: str
    created_at: datetime
    budget_seconds: float
    status: str                      # COMPLETED | TIMED_OUT
    capability: str                  # always SCENARIO_ONLY
    baseline_candidate_id: str | None
    scenario_candidate_id: str | None
    baseline_valid: bool | None
    scenario_valid: bool | None
    comparison: dict[str, Any]
    notes: tuple[str, ...]


# ---------------------------------------------------------------------- change validation

def normalize_changes(raw: Any) -> tuple[dict[str, Any], ...]:
    """Strictly typed, allowlisted changes in canonical order (stable reasons on anything else)."""
    if not isinstance(raw, list) or not raw:
        raise DomainError("BAD_CHANGE", "changes is a non-empty list of typed changes")
    if len(raw) > MAX_CHANGES:
        raise DomainError("TOO_MANY_CHANGES", f"at most {MAX_CHANGES} changes per scenario")
    out = []
    for i, ch in enumerate(raw):
        if not isinstance(ch, dict) or ch.get("type") not in FIELDS:
            raise DomainError("UNSUPPORTED_CHANGE", f"change {i}: type must be one of {sorted(FIELDS)}")
        kind = ch["type"]
        extra = set(ch) - FIELDS[kind]
        missing = FIELDS[kind] - set(ch)
        if extra or missing:
            raise DomainError("BAD_CHANGE", f"change {i} ({kind}): unknown {sorted(extra)} / missing {sorted(missing)}")
        try:
            if kind == "virtual_staffing":
                add = ch["add"]
                if not isinstance(add, int) or isinstance(add, bool) or not 1 <= add <= 10 or ch["shift"] not in (1, 2, 3):
                    raise ValueError("add is 1..10 and shift is 1, 2 or 3")
                out.append({"type": kind, "team": str(ch["team"]), "date": date.fromisoformat(ch["date"]).isoformat(), "shift": ch["shift"], "add": add})
            elif kind == "eta_forecast":
                out.append({"type": kind, "release_id": str(ch["release_id"]), "forecast_at": aware_utc(parse_utc(ch["forecast_at"]), "forecast_at").isoformat()})
            elif kind == "duration_statistic":
                if ch["statistic"] not in STATISTICS:
                    raise ValueError(f"statistic is one of {STATISTICS}")
                out.append({"type": kind, "statistic": ch["statistic"]})
            elif kind == "travel_minutes":
                minutes = ch["changed_aircraft_minutes"]
                if not isinstance(minutes, int) or isinstance(minutes, bool) or not 0 <= minutes <= MAX_TRAVEL_MINUTES:
                    raise ValueError(f"changed_aircraft_minutes is an integer 0..{MAX_TRAVEL_MINUTES}")
                out.append({"type": kind, "changed_aircraft_minutes": minutes})
            elif kind == "task_priority":
                rank = ch["rank"]
                if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= MAX_PRIORITY_RANK:
                    raise ValueError(f"rank is an integer 1..{MAX_PRIORITY_RANK} (1 runs first)")
                if not isinstance(ch["job_id"], str) or not 1 <= len(ch["job_id"]) <= 160:
                    raise ValueError("job_id is a job id of the admitted snapshot")
                out.append({"type": kind, "job_id": ch["job_id"], "rank": rank})
            elif kind == "daily_cost":
                rate = ch["daily_rate_cents"]
                if not isinstance(rate, int) or isinstance(rate, bool) or not 0 <= rate <= 10**12 or not isinstance(ch["line"], int):
                    raise ValueError("line and daily_rate_cents are non-negative integers")
                out.append({"type": kind, "line": ch["line"], "daily_rate_cents": rate})
            else:
                if ch["status"] not in CALENDAR_STATUSES or not isinstance(ch["line"], int):
                    raise ValueError(f"status is one of {CALENDAR_STATUSES}")
                out.append({"type": kind, "line": ch["line"], "date": date.fromisoformat(ch["date"]).isoformat(), "status": ch["status"]})
        except (ValueError, TypeError) as exc:
            raise DomainError("BAD_CHANGE", f"change {i} ({kind}): {exc}") from None
    for kind in SINGLETONS:
        if sum(1 for c in out if c["type"] == kind) > 1:
            raise DomainError("BAD_CHANGE", f"one {kind} change per scenario")
    jobs = [c["job_id"] for c in out if c["type"] == "task_priority"]
    if len(set(jobs)) != len(jobs):
        raise DomainError("BAD_CHANGE", "one priority per job")
    return tuple(sorted(out, key=lambda c: content_hash(c)))


def scenario_policy(policy: Any, changes: tuple[dict[str, Any], ...]) -> Any:
    """The policy of the changed copy: the active policy with the scenario's toggles (a separately hashed policy)."""
    overrides: dict[str, Any] = {}
    stat = next((c["statistic"] for c in changes if c["type"] == "duration_statistic"), None)
    if stat is not None and stat != policy.str("duration.default_statistic"):
        overrides["duration.default_statistic"] = stat
    travel = next((c["changed_aircraft_minutes"] for c in changes if c["type"] == "travel_minutes"), None)
    if travel is not None and travel != policy.int("travel.changed_aircraft_minutes"):
        overrides["travel.changed_aircraft_minutes"] = travel
    if not overrides:
        return policy
    return policy.with_overrides(overrides, new_id=f"{policy.id}-scenario-{content_hash(overrides)[:10]}", authority="user_requirement",
                                 reason="scenario " + ", ".join(f"{k}={v}" for k, v in sorted(overrides.items())))


def priority_ranks(changes: tuple[dict[str, Any], ...]) -> dict[str, int]:
    """Planned task priorities as leading dispatch keys: rank 1 -> -20 (first) ... rank 20 -> -1; other jobs 0."""
    return {c["job_id"]: c["rank"] - MAX_PRIORITY_RANK - 1 for c in changes if c["type"] == "task_priority"}


# ---------------------------------------------------------------------- the scenario copy

def apply_changes(base: PlanningSnapshot, changes: tuple[dict[str, Any], ...], policy: Any) -> tuple[PlanningSnapshot, list[str]]:
    """The base snapshot with the typed changes applied, re-identified and sealed in the scenario namespace."""
    from ..calendar import ShiftPolicy, build_shift
    from ..data.workforce import activation_cap
    from ..simulation.pools import VIRTUAL_NAMESPACE

    people, holdings, availability = list(base.people), list(base.holdings), list(base.availability)
    pools = {p.id: p for p in base.pools}
    releases = {r.id: r for r in base.releases}
    economics = {e.line: e for e in base.economics}
    calendars = {c.line: c for c in base.calendars}
    notes: list[str] = []
    shift_policy = ShiftPolicy.from_policy(policy)
    num, den = policy.int("activation.fraction_numerator"), policy.int("activation.fraction_denominator")
    team_certs: dict[str, set[str]] = {}
    for r in base.requirements:
        job = next((j for j in base.jobs if j.id == r.job), None)
        if job is not None and r.status is RequirementStatus.REQUIRED and r.bundle is not None and job.team:
            team_certs.setdefault(job.team, set()).update(r.bundle.all_codes)
    for ch in changes:
        kind = ch["type"]
        if kind == "virtual_staffing":
            day = date.fromisoformat(ch["date"])
            pool = next((p for p in pools.values() if p.key.team == ch["team"] and p.key.start_date == day and p.key.shift_number == ch["shift"]), None)
            if pool is None:
                raise DomainError("NO_POOL", f"no {ch['team']} pool for {ch['date']} S{ch['shift']} in the admitted snapshot")
            shift = build_shift(day, ch["shift"], shift_policy, base.site)
            certs = sorted(team_certs.get(ch["team"], set()))
            added = []
            for i in range(1, ch["add"] + 1):
                key = PersonKey(f"SCENARIO-{ch['team']}-{day.isoformat()}-S{ch['shift']}-{i:02d}", VIRTUAL_NAMESPACE)
                person = Person(key=key, display_name=f"Scenario virtual {ch['team']} S{ch['shift']} #{i:02d}", origin=PersonOrigin.SYNTHETIC_VIRTUAL,
                                synthetic=True, namespace=VIRTUAL_NAMESPACE, purpose_scope="FORECAST_ONLY",
                                evidence=(EvidenceRef("scenario:virtual_staffing", f"{ch['team']}/{day.isoformat()}/S{ch['shift']}/{i}"),))
                people.append(person)
                added.append(person.id)
                for cert in certs:
                    holdings.append(Holding(person=person.id, cert=cert, status="ACTIVE", validity="BOUNDED", valid_from=from_minute(shift.start),
                                            valid_to=from_minute(shift.end), revoked_at=None, applicability="SYNTHETIC_TEAM_UNION",
                                            raw_status="SYNTHETIC", raw_last_cert_date=None, raw_expiry=None,
                                            evidence=(EvidenceRef("scenario-union", f"{ch['team']}:{cert}"),)))
                availability.append(Availability(id=digest_id("AV", pool.id, person.id), person=person.id, pool=pool.id,
                                                 intervals=tuple(Interval(from_minute(a), from_minute(b)) for a, b in shift.regular_windows),
                                                 other_duty=(), ot_intervals=(), regular_cap=shift.regular_cap, ot_cap=0, admitted_presence=False,
                                                 basis=AvailabilityBasis.SYNTHETIC,
                                                 evidence=(EvidenceRef("scenario:virtual_staffing", pool.id),)))
            fore = tuple(sorted(set(pool.forecast_members) | set(added)))
            pools[pool.id] = Pool(key=pool.key, members=tuple(sorted(set(pool.members) | set(added))), executable_members=pool.executable_members,
                                  forecast_members=fore, synthetic=True, n_executable=pool.n_executable, cap_executable=pool.cap_executable,
                                  n_forecast=len(fore), cap_forecast=activation_cap(len(fore), num, den),
                                  basis=pool.basis + "+SCENARIO_VIRTUAL_STAFFING", excluded=pool.excluded)
            notes.append(f"+{ch['add']} virtual {ch['team']} {ch['date']} S{ch['shift']} (forecast only; certs {certs})")
        elif kind == "eta_forecast":
            rel = releases.get(ch["release_id"])
            if rel is None:
                raise DomainError("RELEASE_NOT_FOUND", f"no release {ch['release_id']} in the admitted snapshot")
            if rel.status is ReleaseStatus.CONFIRMED:
                raise DomainError("RELEASE_CONFIRMED", "a confirmed receipt is a fact; only unconfirmed ETAs are scenario inputs")
            releases[rel.id] = replace(rel, forecast_at=parse_utc(ch["forecast_at"]), status=ReleaseStatus.CONDITIONAL, precision="TIMESTAMP")
            notes.append(f"{rel.label}: ETA forecast {ch['forecast_at']} (conditional)")
        elif kind == "daily_cost":
            econ = economics.get(ch["line"])
            if econ is None:
                raise DomainError("LINE_NOT_FOUND", f"line {ch['line']} has no economics row")
            economics[ch["line"]] = replace(econ, status="KNOWN", authority="ASSUMED", daily_rate_cents=Fraction(ch["daily_rate_cents"]),
                                            reasons=tuple(econ.reasons) + ("SCENARIO_ASSUMED_DAILY_COST",))
            notes.append(f"line {ch['line']}: assumed daily cost {ch['daily_rate_cents']} cents (scenario assumption, not contractual)")
        elif kind == "calendar_day":
            cal = calendars.get(ch["line"])
            day = date.fromisoformat(ch["date"])
            if cal is None or not any(d.day == day for d in cal.days):
                raise DomainError("CALENDAR_DAY_NOT_FOUND", f"line {ch['line']} has no admitted calendar row for {ch['date']}")
            calendars[ch["line"]] = replace(cal, days=tuple(replace(d, status=ch["status"]) if d.day == day else d for d in cal.days),
                                            reasons=tuple(cal.reasons) + (f"SCENARIO_CALENDAR {ch['date']} {ch['status']}",))
            notes.append(f"line {ch['line']} {ch['date']}: {ch['status']} (scenario calendar)")
        elif kind == "duration_statistic":
            notes.append(f"duration statistic {ch['statistic']} (policy toggle)")
        elif kind == "travel_minutes":
            notes.append(f"changed-aircraft travel {ch['changed_aircraft_minutes']} min (policy toggle)")
        elif kind == "task_priority":
            job = next((j for j in base.jobs if j.id == ch["job_id"]), None)
            if job is None:
                raise DomainError("JOB_NOT_FOUND", f"no job {ch['job_id']} in the admitted snapshot")
            notes.append(f"{job.key.line}/{job.key.native_id}: planned priority {ch['rank']} (first among ready work; readiness unchanged)")
    copy = replace(base, id=digest_id("SCN", base.id, list(changes), length=24), people=tuple(people), holdings=tuple(holdings),
                   availability=tuple(availability),
                   pools=tuple(sorted(pools.values(), key=lambda p: (p.key.start_date, p.key.shift_number, p.key.team))),
                   releases=tuple(sorted(releases.values(), key=lambda r: r.id)), economics=tuple(sorted(economics.values(), key=lambda e: e.line)),
                   calendars=tuple(sorted(calendars.values(), key=lambda c: c.line)))
    return seal_snapshot(copy), notes


# ---------------------------------------------------------------------- comparison

def _summary(plan: Any) -> dict[str, Any]:
    census: dict[str, int] = {}
    for o in plan.job_dispositions:
        census[o.disposition.value] = census.get(o.disposition.value, 0) + 1
    return {"candidate_id": plan.id, "assignments": len(plan.assignments), "dispositions": dict(sorted(census.items())),
            "known_cost_cents": str(plan.costs.known_total_cents), "complete_cost": plan.costs.complete}


def compare(baseline: Any, scenario: Any, snapshot: Any) -> dict[str, Any]:
    synthetic = {p.id for p in snapshot.people if p.synthetic}
    lines = []
    base_c = {c.line: c for c in baseline.aircraft_completion}
    for c in sorted(scenario.aircraft_completion, key=lambda x: x.line):
        b = base_c.get(c.line)
        delta = None
        if b is not None and b.completion_at is not None and c.completion_at is not None:
            delta = int((c.completion_at - b.completion_at).total_seconds() // 60)
        lines.append({"line": c.line, "baseline": None if b is None else {"status": b.status, "completion_at": b.completion_at},
                      "scenario": {"status": c.status, "completion_at": c.completion_at}, "delta_minutes": delta})
    base, scen = _summary(baseline), _summary(scenario)
    scen["virtual_assignments"] = sum(1 for a in scenario.assignments if any(r.person in synthetic for r in a.crew))
    base["virtual_assignments"] = sum(1 for a in baseline.assignments if any(r.person in synthetic for r in a.crew))
    return {"lines": lines, "baseline": base, "scenario": scen,
            "cost_delta_cents": str(scenario.costs.known_total_cents - baseline.costs.known_total_cents),
            "note": "same optimizer, ledger, validator and budget on the unchanged copy and the changed copy; differences come from the changes"}


# ---------------------------------------------------------------------- the run

def run_scenario(services: Any, *, run_id: str, requested_by: str, source_run_id: str, changes: Any, budget_seconds: float,
                 at: datetime, cancelled: Callable[[], bool] = lambda: False, timer: Callable[[], float] = time.monotonic) -> ScenarioResult:
    from ..data.acquisition import get_run, snapshot_for_run
    from ..engine.max.optimizer.orchestrator import schedule_snapshot
    from ..workers import RunCancelled

    def stop_if_cancelled(stage: str) -> None:
        if cancelled():
            raise RunCancelled(f"scenario cancelled {stage}; nothing live was changed and no result is stored")

    repos = services.repositories
    scope = services.settings.scope_key
    rid = digest_id("SCR", scope, run_id)
    existing = repos.get_typed(SCENARIO_RESULTS, rid, "ScenarioResult", ScenarioResult)
    if existing is not None:
        return existing                                       # a retried run returns its immutable result
    normalized = normalize_changes(changes)
    found = get_run(repos, source_run_id)
    if found is None or found[0].scope != scope or found[0].status != "COMPLETE":
        raise DomainError("SOURCE_RUN_NOT_FOUND", "scenarios start from a complete admitted source run of this scope")
    started = timer()
    budget = max(1.0, float(budget_seconds))
    policy = scenario_policy(services.policy, normalized)
    stop_if_cancelled("before start")
    base = snapshot_for_run(services, source_run_id, services.policy, store=False)     # the unchanged admitted facts
    toggled = base if policy is services.policy else snapshot_for_run(services, source_run_id, policy, store=False)
    copy, notes = apply_changes(toggled, normalized, policy)
    if repos.get_raw(SCENARIO_SNAPSHOTS, copy.id) is None:
        repos.put_record(SCENARIO_SNAPSHOTS, "PlanningSnapshot", copy.id, copy, source_run_id=source_run_id, policy_hash=policy.hash)
    half = budget / 2
    baseline, brun = schedule_snapshot(services, base, purpose=Purpose.SCENARIO, policy=services.policy, budget_seconds=half, cancel=cancelled)
    stop_if_cancelled("after the baseline")
    status, scenario, srun, comparison = "COMPLETED", None, None, {}
    if timer() - started > budget * 1.5:
        status = "TIMED_OUT"
        notes.append("the baseline used the whole budget; the changed copy was not scheduled")
    else:
        scenario, srun = schedule_snapshot(services, copy, purpose=Purpose.SCENARIO, policy=policy, budget_seconds=half,
                                           fixed_ranks=priority_ranks(normalized) or None, cancel=cancelled)
        stop_if_cancelled("after the changed copy")
        comparison = to_jsonable(compare(baseline, scenario, copy))
    result = ScenarioResult(rid, scope, run_id, source_run_id, base.id, copy.id, normalized, requested_by, aware_utc(at, "at"), budget, status,
                            "SCENARIO_ONLY", baseline.id, None if scenario is None else scenario.id, brun.validation.valid,
                            None if srun is None else srun.validation.valid, comparison, tuple(notes))
    if repos.get_raw(SCENARIO_RESULTS, rid) is None:
        repos.put_record(SCENARIO_RESULTS, "ScenarioResult", rid, result, created_at=result.created_at)
    return result


def summary(r: ScenarioResult) -> dict[str, Any]:
    return {"scenario_id": r.id, "run_id": r.run_id, "status": r.status, "capability": r.capability, "source_run_id": r.source_run_id,
            "base_snapshot_id": r.base_snapshot_id, "scenario_snapshot_id": r.scenario_snapshot_id, "changes": list(r.changes),
            "requested_by": r.requested_by, "created_at": r.created_at, "budget_seconds": r.budget_seconds,
            "baseline_candidate_id": r.baseline_candidate_id, "scenario_candidate_id": r.scenario_candidate_id,
            "baseline_valid": r.baseline_valid, "scenario_valid": r.scenario_valid, "comparison": r.comparison, "notes": list(r.notes)}


def worker_scenario(services: Any, run: Any) -> dict[str, Any]:
    from ..workers import cancel_requested

    req = run.request
    result = run_scenario(services, run_id=run.id, requested_by=run.requested_by, source_run_id=str(req.get("source_run_id", "")),
                          changes=req.get("changes"), budget_seconds=min(float(req.get("budget_seconds", run.budget_seconds)), float(run.budget_seconds)),
                          at=run.created_at, cancelled=lambda: cancel_requested(services, run.id))
    return {"status": result.status.lower(), "scenario_id": result.id, "capability": result.capability,
            "scenario_candidate_id": result.scenario_candidate_id, "baseline_candidate_id": result.baseline_candidate_id,
            "note": "scenario results never move a source or plan head and are never publishable"}


def cli_scenario(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    import json

    try:
        changes = json.loads(args.changes)
    except ValueError as exc:
        return {"status": "invalid", "error": {"code": "BAD_CHANGE", "message": f"changes must be JSON: {exc}"}}
    try:
        result = run_scenario(services, run_id=f"cli-{content_hash([args.source_run, changes, args.budget_seconds])[:16]}", requested_by="cli",
                              source_run_id=args.source_run, changes=changes, budget_seconds=args.budget_seconds, at=services.clock.now())
    except DomainError as exc:
        return {"status": "invalid", "error": {"code": exc.code, "message": exc.message}}
    view = summary(result)
    return {**view, "status": "ok", "scenario_status": view["status"]}       # a bounded TIMED_OUT result is a valid outcome, not an error
