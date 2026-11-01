"""Incumbent task defense and persistent delivery commitments (S12, P14).

Three kinds of obligation stay separate:

* **Physical carry-in** (an ongoing duty that started before the cutoff) is a locked ledger
  duty (``EngineContext.new_ledger``). It is hard and is not a defeasible commitment.
* **Frozen goals** issued at publication (P16) are immutable obligations, scored separately.
* **Task commitments** (defeasible) are loaded here.

Task defense
------------
The incumbent is loaded only from an ACCEPTED artifact in the persistent repository, through
the incumbent head. It is never taken from process memory, from a mere candidate or from the
newest JSON file. Its scope, purpose, policy and source must be compatible (``lookup_incumbent``),
otherwise no task is defended and the reason is reported. Phases starting at day offsets
0..horizon-1 become ``DefendedTask``s. With ``commitment.horizon_calendar_days`` = 3 that is
offsets 0/1/2. For each one the decoder tries:

1. the exact slot with the exact full crew;
2. the same slot with an alternate full crew;
3. only then a minimal audited move, or a deferral.

Day 3 and later are freely repacked.

An incumbent task is found in the new snapshot by its position: the production minutes still to go in its job from
that phase (``IncumbentPlan.positions``). Written-back progress re-splits a job's remaining work and renumbers it from
ordinal 0, so an ordinal would point at the wrong piece. A position beyond the job's remaining work was PERFORMED since
acceptance. When the re-split folds two committed pieces into one phase, the earlier slot defends it and the later one
is MERGED. Incumbents recorded without positions (the fixture incumbent) are matched by ordinal.

Delivery hysteresis
-------------------
Per aircraft the repository keeps the original and current committed date, the raw projection,
the pending target, the confirmation count and the accepted plan ids already counted.
``delta = projected - current`` is measured in calendar days:

* ``|delta| < forecast.hysteresis_days`` (2): hold the committed date and reset pending; the
  raw risk stays visible;
* ``delta >= forecast.bad_news_days`` (7): change immediately;
* otherwise: change only after ``forecast.confirmation_runs`` (2) distinct accepted replans
  agree on the target within ``forecast.tolerance_days`` (1). Large improvements wait too.

Replayed or rejected runs never count. Safety alerts are raised immediately and never wait.
Exiting the cohort keeps the history.

Changes go to an outbox first. Each event id includes the state version it was computed from.
A later state write acknowledges the event, so an event left behind by a crash or a lost race
stays invisible, and replaying the same accepted plan re-derives the identical event. Each
accepted plan leaves one immutable receipt, so recovery returns the same receipt instead of
counting the plan again.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..calendar import shift_local_minute
from ..codec import parse_utc, to_jsonable
from ..contracts.planning import CommitmentResult, IncumbentAssignment, IncumbentPlan, locate_position
from ..data.store import VersionConflict
from ..domain import Disposition, PersonKey, PhaseKind, digest_id
from ..policy import Policy
from .max.optimizer.greedy import DefendedTask
from .model import EngineContext, shift_id_of

UTC = timezone.utc
ACCEPTED = "accepted_plans"
DELIVERY = "delivery_commitments"
DELIVERY_EVENTS = "delivery_events"
DELIVERY_RECEIPTS = "delivery_receipts"
COUNTED_KEEP = 64                     # per-line counted plan ids kept for crash replay; receipts cover older replays


def incumbent_head(scope: str, purpose: str) -> str:
    return f"incumbent:{scope}:{purpose}"


@dataclass(frozen=True)
class IncumbentLookup:
    plan: IncumbentPlan | None
    status: str                           # LOADED | NO_INCUMBENT | ARTIFACT_MISSING | INCOMPATIBLE_SCOPE | INCOMPATIBLE_POLICY | INCOMPATIBLE_SOURCE
    detail: str


def record_accepted_incumbent(services: Any, plan: IncumbentPlan) -> IncumbentPlan:
    """Store the immutable ACCEPTED artifact, then move the incumbent head last (compare-and-set); idempotent.

    The head only moves forward in acceptance time: recording an older acceptance (for example re-running the
    fixture bootstrap after a newer plan was published) stores the artifact but never regresses the head.
    """
    repos = services.repositories
    if repos.get_raw(ACCEPTED, plan.id) is None:
        repos.put_record(ACCEPTED, "IncumbentPlan", plan.id, plan, purpose=plan.purpose)
    name = incumbent_head(plan.scope, plan.purpose)
    head = repos.head(name)
    if head is not None and head.record_id != plan.id:
        current = repos.get_typed(ACCEPTED, head.record_id, "IncumbentPlan", IncumbentPlan)
        if current is not None and current.accepted_at > plan.accepted_at:
            return plan
    if head is None or head.record_id != plan.id:
        repos.move_head(name, plan.id, head.version if head else 0)
    return plan


def import_fixture_incumbent(services: Any, scenario: str | None = None, purpose: str = "EXECUTABLE") -> IncumbentPlan:
    """Fixture mode only: record the S18 previous accepted plan as an ACCEPTED artifact and move the incumbent head."""
    settings = services.settings
    if settings.mode != "fixture":
        raise ValueError("fixture incumbents can only be imported in fixture mode")
    path = Path(settings.app_root) / "fixtures" / "inputs" / (scenario or settings.scenario_id) / "incumbent_plan.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assignments = tuple(IncumbentAssignment(int(a["line"]), str(a["soi"]), str(a["phase"]), int(a["ordinal"]), date.fromisoformat(a["date"]),
                                            int(a["shift"]), str(a["start_local"]), tuple(str(c) for c in a["crew"])) for a in raw["assignments"])
    plan = IncumbentPlan(
        id=digest_id("INC", settings.scope_key, purpose, raw["accepted_at_utc"], to_jsonable(list(assignments))),
        scope=settings.scope_key, purpose=purpose, policy_id=services.policy.id, origin="FIXTURE_ACCEPTED",
        accepted_at=parse_utc(raw["accepted_at_utc"]), previous_cutoff=parse_utc(raw["previous_cutoff_utc"]), namespace="fixture-demo",
        assignments=assignments)
    return record_accepted_incumbent(services, plan)


def lookup_incumbent(services: Any, scope: str, purpose: str, *, policy_id: str | None = None, cutoff: datetime | None = None,
                     namespaces: frozenset[str] | None = None) -> IncumbentLookup:
    """The accepted incumbent for this scope/purpose from the persistent repository, with the compatibility verdict."""
    repos = services.repositories
    head = repos.head(incumbent_head(scope, purpose))
    if head is None:
        return IncumbentLookup(None, "NO_INCUMBENT", f"no accepted incumbent for {scope}/{purpose}")
    plan = repos.get_typed(ACCEPTED, head.record_id, "IncumbentPlan", IncumbentPlan)
    if plan is None:
        return IncumbentLookup(None, "ARTIFACT_MISSING", f"incumbent head names {head.record_id} but no accepted artifact exists")
    if plan.scope != scope or plan.purpose != purpose:
        return IncumbentLookup(None, "INCOMPATIBLE_SCOPE", f"accepted for {plan.scope}/{plan.purpose}, requested {scope}/{purpose}")
    active = policy_id or services.policy.id
    if plan.policy_id != active:
        return IncumbentLookup(None, "INCOMPATIBLE_POLICY", f"accepted under policy {plan.policy_id}, active policy is {active}")
    if cutoff is not None and plan.previous_cutoff > cutoff:
        return IncumbentLookup(None, "INCOMPATIBLE_SOURCE", f"incumbent cutoff {plan.previous_cutoff.isoformat()} is after the snapshot "
                                                            f"cutoff {cutoff.isoformat()}")
    if namespaces is not None and plan.namespace not in namespaces:
        return IncumbentLookup(None, "INCOMPATIBLE_SOURCE", f"person namespace {plan.namespace} is not in the snapshot {sorted(namespaces)}")
    return IncumbentLookup(plan, "LOADED", f"accepted {plan.origin} incumbent {plan.id}")


def load_incumbent(services: Any, scope: str, purpose: str) -> IncumbentPlan | None:
    return lookup_incumbent(services, scope, purpose).plan


@dataclass
class Defense:
    defended: dict[str, DefendedTask] = field(default_factory=dict)
    incumbent_id: str | None = None
    other_results: tuple[CommitmentResult, ...] = ()
    status: str = "NO_INCUMBENT"
    detail: str = ""
    plan: IncumbentPlan | None = None           # the loaded incumbent, which a repair keeps beyond the defended horizon

    def receipt(self) -> dict[str, str]:
        return {"id": self.incumbent_id or "", "status": self.status, "detail": self.detail, "defended": str(len(self.defended))}


def defended_tasks(services: Any, ctx: EngineContext) -> Defense:
    if not ctx.policy.bool("commitment.enabled"):
        return Defense(status="DISABLED", detail="commitment.enabled is false")
    namespaces = frozenset(p.namespace for p in ctx.snapshot.people if not p.synthetic)
    found = lookup_incumbent(services, ctx.snapshot.scope_key, ctx.purpose.value, policy_id=ctx.policy.id, cutoff=ctx.snapshot.cutoff,
                             namespaces=namespaces)
    if found.plan is None:
        return Defense(status=found.status, detail=found.detail)
    return defense_for(ctx, found.plan, status=found.status, detail=found.detail)


def _located(ctx: EngineContext, plan: IncumbentPlan):
    """Each incumbent assignment found in this context: (n, assignment, incumbent id, day offset, job, phase, performed)."""
    today = ctx.snapshot.cutoff.astimezone(ctx.shift_policy.tz).date()
    by_key = {(j.line, j.key.native_id): j for j in ctx.jobs.values()}
    positions = plan.positions if len(plan.positions) == len(plan.assignments) else ()
    for n, a in enumerate(plan.assignments):
        job = by_key.get((a.line, a.soi))
        phase, performed = None, False
        if job is not None:
            # by position in the job's remaining work: written-back progress re-splits and renumbers what is left
            kind = PhaseKind(a.phase)
            located = locate_position(ctx.job_chain[job.id], kind, positions[n]) if positions and positions[n] is not None else None
            if located is not None:
                phase, performed = located
            else:
                phase = next((p for p in ctx.job_phases[job.id] if p.kind is kind and p.ordinal == a.ordinal), None)
        yield n, a, digest_id("INCA", plan.id, n), (a.day - today).days, job, phase, performed


def _slot(ctx: EngineContext, plan: IncumbentPlan, a: IncumbentAssignment, aid: str, phase: Any, offset: int) -> DefendedTask:
    start = shift_local_minute(a.day, a.shift, a.start_local, ctx.shift_policy, ctx.site)     # S3 starts after midnight: next day
    crew = tuple(PersonKey(native, plan.namespace).id for native in a.crew)
    return DefendedTask(phase.key.id, aid, shift_id_of(ctx.site, a.day, a.shift), start, crew, offset)


def repair_hints(ctx: EngineContext, plan: IncumbentPlan, defended: Mapping[str, DefendedTask]) -> dict[str, DefendedTask]:
    """Every open incumbent phase outside the commitment defense, at its incumbent slot and crew: what a repair keeps when
    it is still feasible (the decoder re-places it only when it is not). Completed and performed work, and phases the
    snapshot no longer has, keep nothing."""
    hints: dict[str, DefendedTask] = {}
    for _, a, aid, offset, job, phase, performed in _located(ctx, plan):
        if job is None or phase is None or performed or ctx.dispositions[job.id].disposition is Disposition.VERIFIED_COMPLETE:
            continue
        if phase.key.id not in defended and phase.key.id not in hints:
            hints[phase.key.id] = _slot(ctx, plan, a, aid, phase, offset)
    return hints


def defense_for(ctx: EngineContext, plan: IncumbentPlan, *, status: str, detail: str) -> Defense:
    """The defended tasks and other commitment outcomes of ``plan`` (an accepted incumbent) against this context."""
    horizon = ctx.policy.int("commitment.horizon_calendar_days")
    positions = plan.positions if len(plan.positions) == len(plan.assignments) else ()
    out = Defense(incumbent_id=plan.id, status=status, detail=detail, plan=plan)
    others: list[CommitmentResult] = []
    for n, a, aid, offset, job, phase, performed in _located(ctx, plan):
        if job is not None and ctx.dispositions[job.id].disposition is Disposition.VERIFIED_COMPLETE:
            others.append(CommitmentResult(phase.key.id if phase else job.id, job.id, aid, offset, "COMPLETED", "not needed", "not needed",
                                           "verified complete since acceptance", None))
            continue
        if performed:
            others.append(CommitmentResult(job.id, job.id, aid, offset, "PERFORMED", "not needed", "not needed",
                                           f"performed since acceptance: {positions[n]} production minutes to go lies beyond the job's "
                                           f"remaining work", None))
            continue
        if job is None or phase is None:
            others.append(CommitmentResult(f"{a.line}/{a.soi}", job.id if job else f"{a.line}/{a.soi}", aid, offset, "UNSAFE",
                                           "not attempted", "not attempted", "INCUMBENT_PHASE_MISSING: no matching phase in the snapshot", None))
            continue
        if offset < 0 or offset >= horizon:
            others.append(CommitmentResult(phase.key.id, job.id, aid, offset, "OUT_OF_HORIZON", "not attempted", "not attempted",
                                           f"day offset {offset} is outside 0..{horizon - 1}: freely repacked", None))
            continue
        if phase.key.id in out.defended:
            others.append(CommitmentResult(phase.key.id, job.id, aid, offset, "MERGED", "not attempted", "not attempted",
                                           f"REMAINING_WORK_RESPLIT: this slot's work is now part of {phase.label}, defended from an earlier "
                                           f"slot", None))
            continue
        out.defended[phase.key.id] = _slot(ctx, plan, a, aid, phase, offset)
    out.other_results = tuple(others)
    return out


# ---------------------------------------------------------------------- delivery hysteresis

@dataclass(frozen=True)
class DeliveryState:
    line: int
    original_date: date | None
    current_date: date | None
    raw_projection: date | None
    pending_target: date | None
    confirmations: int
    last_plan_id: str | None
    counted: tuple[tuple[str, str], ...]       # (accepted plan id, decision), oldest first, bounded
    in_cohort: bool
    risk: str | None                           # raw risk shown immediately (held jitter, pending change, unknown projection)
    alert: str | None                          # active safety alert
    acknowledged: tuple[str, ...]              # change-event ids committed with this state, in order

    @classmethod
    def empty(cls, line: int) -> "DeliveryState":
        return cls(line, None, None, None, None, 0, None, (), True, None, None, ())

    def decision_for(self, plan_id: str) -> str | None:
        return next((d for p, d in self.counted if p == plan_id), None)


@dataclass(frozen=True)
class DeliveryEvent:
    id: str
    line: int
    plan_id: str
    kind: str                            # ESTABLISHED | BAD_NEWS | CONFIRMED | SAFETY_ALERT | COHORT_EXIT | COHORT_REENTRY
    from_date: date | None
    to_date: date | None
    from_version: int
    detail: str


@dataclass(frozen=True)
class Observation:
    state: DeliveryState
    decision: str                        # ESTABLISHED | HOLD | PENDING | CONFIRMED | BAD_NEWS | UNKNOWN | REPLAY
    detail: str
    events: tuple[DeliveryEvent, ...]


@dataclass(frozen=True)
class DeliveryReceipt:
    plan_id: str
    scope: str
    status: str                          # RECORDED | REPLAY | REJECTED | NOT_APPLICABLE
    decisions: tuple[tuple[int, str], ...]
    events: tuple[DeliveryEvent, ...]


@dataclass(frozen=True)
class HysteresisPolicy:
    hold_days: int
    bad_news_days: int
    confirmations: int
    tolerance_days: int

    @classmethod
    def from_policy(cls, policy: Policy) -> "HysteresisPolicy":
        return cls(policy.int("forecast.hysteresis_days"), policy.int("forecast.bad_news_days"), policy.int("forecast.confirmation_runs"),
                   policy.int("forecast.tolerance_days"))


def _event(state: DeliveryState, plan_id: str, version: int, kind: str, a: date | None, b: date | None, detail: str) -> DeliveryEvent:
    return DeliveryEvent(digest_id("DEV", plan_id, state.line, kind, version), state.line, plan_id, kind, a, b, version, detail)


def observe(state: DeliveryState, plan_id: str, projected: date | None, hp: HysteresisPolicy, safety_alert: str | None = None,
            version: int = 0) -> Observation:
    """Apply one accepted replan's projection to one aircraft (pure). A plan already counted changes nothing."""
    if state.decision_for(plan_id) is not None:
        return Observation(state, "REPLAY", "accepted plan already counted", ())
    events: list[DeliveryEvent] = []
    alert = safety_alert or None
    if alert and alert != state.alert:
        events.append(_event(state, plan_id, version, "SAFETY_ALERT", state.current_date, state.current_date, alert))   # never waits

    def done(decision: str, detail: str, **changes: Any) -> Observation:
        counted = (state.counted + ((plan_id, decision),))[-COUNTED_KEEP:]
        new = replace(state, counted=counted, last_plan_id=plan_id, alert=alert, **changes)
        return Observation(new, decision, detail, tuple(events))

    if projected is None:
        return done("UNKNOWN", "projection unknown: committed date held, nothing confirmed", raw_projection=None,
                    risk="PROJECTION_UNKNOWN: completion is not projected by this accepted plan")
    if state.current_date is None:
        events.append(_event(state, plan_id, version, "ESTABLISHED", None, projected, "first accepted projection"))
        return done("ESTABLISHED", "first accepted projection", original_date=state.original_date or projected, current_date=projected,
                    raw_projection=projected, pending_target=None, confirmations=0, in_cohort=True, risk=None)
    delta = (projected - state.current_date).days
    if abs(delta) < hp.hold_days:
        return done("HOLD", f"|delta| {abs(delta)} < {hp.hold_days}: committed date held", raw_projection=projected, pending_target=None,
                    confirmations=0, risk=(f"raw projection {delta:+d} days (held)" if delta else None))
    if delta >= hp.bad_news_days:
        events.append(_event(state, plan_id, version, "BAD_NEWS", state.current_date, projected,
                             f"worsening {delta:+d} >= {hp.bad_news_days} days: changed immediately"))
        return done("BAD_NEWS", f"worsening {delta:+d} days changed immediately", current_date=projected, raw_projection=projected,
                    pending_target=None, confirmations=0, risk=None)
    agrees = state.pending_target is not None and abs((projected - state.pending_target).days) <= hp.tolerance_days
    confirmations = state.confirmations + 1 if agrees else 1
    if confirmations >= hp.confirmations:
        events.append(_event(state, plan_id, version, "CONFIRMED", state.current_date, projected,
                             f"{confirmations} distinct accepted replans agree within {hp.tolerance_days} day"))
        return done("CONFIRMED", f"{confirmations} distinct confirmations", current_date=projected, raw_projection=projected,
                    pending_target=None, confirmations=0, risk=None)
    return done("PENDING", f"delta {delta:+d}: {confirmations}/{hp.confirmations} confirmations", raw_projection=projected,
                pending_target=projected, confirmations=confirmations,
                risk=f"pending {delta:+d} days, {confirmations}/{hp.confirmations} confirmations")


class DeliveryCommitments:
    """Repository-backed per-aircraft commitment state with optimistic versions, an acknowledged outbox and receipts."""

    MAX_ATTEMPTS = 5

    def __init__(self, repositories: Any, scope: str, policy: Policy):
        self.repos = repositories
        self.scope = scope
        self.hp = HysteresisPolicy.from_policy(policy)

    def _key(self, line: int) -> str:
        return f"{self.scope}:{line}"

    def _receipt_id(self, plan_id: str) -> str:
        return digest_id("DRC", self.scope, plan_id)

    def get(self, line: int) -> tuple[DeliveryState, int]:
        found = self.repos.get_state(DELIVERY, self._key(line))
        if found is None:
            return DeliveryState.empty(line), 0
        value, version = found
        return DeliveryState(
            line=int(value["line"]), original_date=_d(value["original_date"]), current_date=_d(value["current_date"]),
            raw_projection=_d(value["raw_projection"]), pending_target=_d(value["pending_target"]), confirmations=int(value["confirmations"]),
            last_plan_id=value["last_plan_id"], counted=tuple((str(p), str(d)) for p, d in value["counted"]), in_cohort=bool(value["in_cohort"]),
            risk=value["risk"], alert=value["alert"], acknowledged=tuple(value["acknowledged"])), version

    def lines(self) -> list[int]:
        prefix = f"{self.scope}:"
        return sorted(int(k[len(prefix):]) for k in self.repos.store.list_state_ids(DELIVERY) if k.startswith(prefix))

    def record_accepted_plan(self, plan_id: str, projections: Mapping[int, date | None], *, rejected: bool = False,
                             safety_alerts: Mapping[int, str] | None = None) -> DeliveryReceipt:
        """Count one accepted replan once. Rejected plans never count; a replay returns the stored receipt."""
        if rejected:
            return DeliveryReceipt(plan_id, self.scope, "REJECTED", (), ())
        stored = self.receipt(plan_id)
        if stored is not None:
            return replace(stored, status="REPLAY")
        alerts = dict(safety_alerts or {})
        for line in sorted(set(projections) | set(self.lines())):
            for attempt in range(self.MAX_ATTEMPTS):
                state, version = self.get(line)
                change = self._transition(state, version, plan_id, line, projections, alerts)
                if change is None:
                    break
                try:
                    self._write(line, change[0], version, change[1])
                    break
                except VersionConflict:          # a concurrent writer moved the state: re-read and re-derive
                    if attempt == self.MAX_ATTEMPTS - 1:
                        raise
        receipt = self._collect(plan_id)
        rid = self._receipt_id(plan_id)
        if self.repos.get_raw(DELIVERY_RECEIPTS, rid) is None:
            self.repos.put_record(DELIVERY_RECEIPTS, "DeliveryReceipt", rid, receipt)
        return receipt

    def receipt(self, plan_id: str) -> DeliveryReceipt | None:
        return self.repos.get_typed(DELIVERY_RECEIPTS, self._receipt_id(plan_id), "DeliveryReceipt", DeliveryReceipt)

    def events(self, line: int | None = None) -> list[DeliveryEvent]:
        """Acknowledged change events in commit order; unacknowledged outbox entries are never shown."""
        out: list[DeliveryEvent] = []
        for ln in ([line] if line is not None else self.lines()):
            state, _ = self.get(ln)
            for eid in state.acknowledged:
                ev = self.repos.get_typed(DELIVERY_EVENTS, eid, "DeliveryEvent", DeliveryEvent)
                if ev is not None:
                    out.append(ev)
        return out

    def outbox_ids(self) -> list[str]:
        return self.repos.list_ids(DELIVERY_EVENTS)

    # ------------------------------------------------------------------ internals
    def _transition(self, state: DeliveryState, version: int, plan_id: str, line: int, projections: Mapping[int, date | None],
                    alerts: Mapping[int, str]) -> tuple[DeliveryState, list[DeliveryEvent]] | None:
        if state.decision_for(plan_id) is not None:
            return None                                    # this line already committed the plan (crash replay)
        if line not in projections:
            if not (state.in_cohort and state.current_date is not None):
                return None
            ev = _event(state, plan_id, version, "COHORT_EXIT", state.current_date, state.current_date,
                        "aircraft left the cohort: commitment history kept")
            counted = (state.counted + ((plan_id, "COHORT_EXIT"),))[-COUNTED_KEEP:]
            return replace(state, in_cohort=False, counted=counted, last_plan_id=plan_id), [ev]
        obs = observe(state, plan_id, projections[line], self.hp, alerts.get(line), version)
        events = list(obs.events)
        new = obs.state
        if not state.in_cohort and state.current_date is not None:
            events.insert(0, _event(state, plan_id, version, "COHORT_REENTRY", state.current_date, state.current_date,
                                    "aircraft re-entered the cohort: history restored"))
            new = replace(new, in_cohort=True)
        return new, events

    def _write(self, line: int, state: DeliveryState, version: int, events: list[DeliveryEvent]) -> None:
        for ev in events:                       # outbox first; the state write below acknowledges them
            if self.repos.get_raw(DELIVERY_EVENTS, ev.id) is None:
                self.repos.put_record(DELIVERY_EVENTS, "DeliveryEvent", ev.id, ev)
        state = replace(state, acknowledged=state.acknowledged + tuple(ev.id for ev in events))
        payload = to_jsonable({f: getattr(state, f) for f in state.__dataclass_fields__})
        self.repos.put_state(DELIVERY, self._key(line), payload, version)

    def _collect(self, plan_id: str) -> DeliveryReceipt:
        decisions: list[tuple[int, str]] = []
        events: list[DeliveryEvent] = []
        for line in self.lines():
            state, _ = self.get(line)
            decision = state.decision_for(plan_id)
            if decision is None:
                continue
            decisions.append((line, decision))
            events.extend(ev for ev in self.events(line) if ev.plan_id == plan_id)
        return DeliveryReceipt(plan_id, self.scope, "RECORDED", tuple(decisions), tuple(events))


def _d(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) else value
