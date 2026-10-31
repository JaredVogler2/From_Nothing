"""Supplementary recognition and progression projections (S14 PROGRESSION, P18).

Everything here is projected from the persisted canonical evaluations (``performance.history``), their
exact unit-fact evidence and the frozen goals. It is never a second scoring, completion or
qualification authority.

**Streams.** ``completion_credited``, ``gate_recovery``, ``dependency_unlocked``, ``commitment_kept``,
``excusal_decided``, ``badge_earned`` and ``correction_applied``. Each event carries:

* a dedupe key ``<base>#r<revision>``;
* the exact earning-unit ids;
* the evaluation and verified credit events it rests on;
* the policy.

Replaying the projection is a no-op.

**XP** is the verified frozen recognition value:

* completion of a planned (or approved-amendment) unit earns effort/divisor;
* a frozen behind-gate unit adds its frozen gate bonus.

Offplan output earns nothing, so there is no unsafe offplan farming and no automatic milestone
minimum. OOS is charged once, in the canonical score; XP never subtracts it again. A correction
(reopen, void, late fact) appends a reversing ``correction_applied`` event and keeps the original.
If the unit is re-earned, a fresh revision is appended.

Level = 1 + floor(total verified XP / ``xp.level_size``), with progress inside the level and the
next threshold. The inflation monitor counts awards per team per ISO week against
``xp.inflation_alert_awards_per_team_week``.

**Badges** have explicit, deterministic conditions:

* **Recovery:** a verified planned completion of a frozen behind-gate unit.
* **Firebreak:** a verified mandatory predecessor release unlocks at least
  ``badges.firebreak_min_unlocked`` distinct, previously blocked downstream jobs in the same admitted
  graph revision.
* **CommitmentKeeper:** every incumbent-defended original unit of the shift is complete, every miss
  is reviewed relief, and the evidence is complete.
* **SteadyShift:** ``badges.steady_shift_run`` consecutive fully admitted positive-goal shifts at or
  above ``badges.steady_shift_min_score``.
* **SkillSteward:** verified completion of planned work whose role needs a scarce certificate group
  (at most ``badges.skill_steward_max_holders`` admitted holders in the frozen membership). It never
  implies certification.
* **MonthlyChampion:** once the month has closed, the best pooled canonical score over the team's
  eligible goals. Ties go to the stable team id, and the award names the member evaluations.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from fractions import Fraction
from typing import Any

from ..contracts.evaluation import BadgeAward, Evaluation, RecognitionEvent
from ..contracts.planning import GateContext
from ..domain import EvaluationStatus, aware_utc, digest_id, to_minute
from .points import unit_values

RECOGNITION = "recognition_events"
BADGES = "badge_awards"
STREAMS = ("completion_credited", "gate_recovery", "dependency_unlocked", "commitment_kept", "excusal_decided", "badge_earned", "correction_applied")
SCORED = (EvaluationStatus.DEMONSTRATION, EvaluationStatus.OFFICIAL)


@dataclass(frozen=True)
class Entitlement:
    base_key: str
    event_type: str
    team: str
    goal_id: str | None
    units: tuple[str, ...]
    value: Fraction
    context: GateContext | None
    evidence: tuple[str, ...]


@dataclass
class ProjectionResult:
    appended: list[RecognitionEvent] = field(default_factory=list)
    corrections: list[RecognitionEvent] = field(default_factory=list)
    badges: list[BadgeAward] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------- what the canonical evaluations entitle now

def current_goal_evaluations(services: Any, view: Any, as_of: datetime) -> dict[str, Evaluation]:
    from ..performance import history

    out: dict[str, Evaluation] = {}
    for record in view.records():
        e = history.as_known(services, services.settings.scope_key, history.series_for_goal(services, record.goal_id), as_of)
        if e is not None:
            out[record.goal_id] = e
    return out


def entitlements(services: Any, view: Any, evaluations: dict[str, Evaluation]) -> tuple[list[Entitlement], list[str]]:
    from ..performance import history

    policy = services.policy
    out: list[Entitlement] = []
    skipped: list[str] = []
    for record in view.records():
        e = evaluations.get(record.goal_id)
        if e is None or e.status not in SCORED:
            skipped.append(f"{record.team} {record.shift_id}: {'not evaluated' if e is None else e.status.value} (no recognition change)")
            continue
        proof = history.evidence(services, e.evidence_hash)
        if proof is None:
            skipped.append(f"{record.team} {record.shift_id}: evidence missing")
            continue
        contexts = record.goal.gate_contexts if record.goal is not None else {}
        facts = list(proof.unit_facts)
        earned = [f for f in facts if f.status == "EARNED"]
        for f in earned:
            context = contexts.get(f.job) if f.origin == "ORIGINAL" else None
            base, bonus = unit_values(context, f.effort, policy)
            evidence = (e.id, f.credit_event or "")
            out.append(Entitlement(f"completion_credited:{record.goal_id}:{f.unit_id}", "completion_credited", record.team, record.goal_id,
                                   (f.unit_id,), base, context, evidence))
            if bonus > 0:
                out.append(Entitlement(f"gate_recovery:{record.goal_id}:{f.unit_id}", "gate_recovery", record.team, record.goal_id, (f.unit_id,),
                                       bonus, context, evidence))
        out.extend(_unlocks(view, record, earned, e, policy))
        committed = [f for f in facts if f.unit_id in view.registry.committed_units]
        if committed and all(f.status == "EARNED" for f in committed) and all(f.relief for f in facts if f.status == "MISSED"):
            out.append(Entitlement(f"commitment_kept:{record.goal_id}", "commitment_kept", record.team, record.goal_id,
                                   tuple(f.unit_id for f in committed), Fraction(0), None, (e.id,)))
        from ..performance.disruption import effective_decisions

        for d in effective_decisions(view, record.goal_id):
            out.append(Entitlement(f"excusal_decided:{d.id}", "excusal_decided", record.team, record.goal_id, tuple(d.approved_units), Fraction(0),
                                   None, (e.id, d.id) + tuple(d.claim_ids)))
    return out, skipped


def _unlocks(view: Any, record: Any, earned: list[Any], evaluation: Evaluation, policy: Any) -> list[Entitlement]:
    """dependency_unlocked: a verified completion that released previously blocked downstream jobs (same admitted graph)."""
    from ..performance.lifecycle import Satisfaction

    graph = view.graph
    if graph is None:
        return []
    sat = Satisfaction(graph, view.lifecycles)
    succ: dict[str, list[str]] = defaultdict(list)
    for node, edges in graph.hard_in.items():
        for e in edges:
            succ[e.pred_phase].append(node)
    out = []
    for phase in sorted({f.phase for f in earned}):
        done = sat.at(phase)
        if done is None:
            continue
        frontier, seen, unlocked = list(succ.get(phase, [])), set(), set()
        while frontier:
            node = frontier.pop()
            if node in seen:
                continue
            seen.add(node)
            target = graph.phases.get(node)
            if target is None:
                continue
            if target.kind.value == "RELEASE":
                frontier.extend(succ.get(node, []))
                continue
            preds = [sat.at(e.pred_phase) for e in graph.hard_in.get(node, [])]
            if target.job != phase.rsplit(".", 2)[0] and preds and all(t is not None for t in preds) and max(t for t in preds if t) == done:
                unlocked.add(target.job)                         # this completion was the last prerequisite
        if unlocked:
            units = tuple(f.unit_id for f in earned if f.phase == phase)
            out.append(Entitlement(f"dependency_unlocked:{record.goal_id}:{phase}", "dependency_unlocked", record.team, record.goal_id, units,
                                   Fraction(0), None, (evaluation.id, graph.snapshot_id) + tuple(sorted(unlocked))))
    return out


# ---------------------------------------------------------------------- append-only chains

def _chains(services: Any) -> dict[str, list[RecognitionEvent]]:
    repos = services.repositories
    chains: dict[str, list[RecognitionEvent]] = defaultdict(list)
    for rid in repos.list_ids(RECOGNITION):
        ev = repos.get_typed(RECOGNITION, rid, "RecognitionEvent", RecognitionEvent)
        if ev is not None and ev.scope == services.settings.scope_key:
            chains[ev.base_key].append(ev)
    for chain in chains.values():
        chain.sort(key=lambda x: x.revision)
    return chains


def _event(services: Any, ent: Entitlement, revision: int, value: Fraction, kind: str, supersedes: str | None, at: datetime,
           evidence: tuple[str, ...]) -> RecognitionEvent:
    key = f"{ent.base_key}#r{revision}"
    return RecognitionEvent(digest_id("REC", services.settings.scope_key, key), key, ent.base_key, kind, services.settings.scope_key, ent.team,
                            ent.goal_id, ent.units, value, ent.context, evidence, services.policy.id, revision, supersedes, at)


def project(services: Any, *, as_of: datetime, view: Any = None) -> ProjectionResult:
    """Append whatever the current canonical evaluations newly entitle or no longer support (idempotent)."""
    from ..performance.lifecycle import ObservationView

    as_of = aware_utc(as_of, "as_of")
    view = view or ObservationView.build(services, as_of)
    evaluations = current_goal_evaluations(services, view, as_of)
    wanted, skipped = entitlements(services, view, evaluations)
    repos = services.repositories
    result = ProjectionResult(skipped=list(skipped))
    with repos.store.lock(f"recognition-{services.settings.scope_key}"):
        chains = _chains(services)
        wanted_keys = {w.base_key for w in wanted}
        for ent in wanted:
            chain = chains.get(ent.base_key, [])
            net = sum((c.value for c in chain), Fraction(0))
            live = bool(chain) and chain[-1].event_type != "correction_applied"
            if live and net == ent.value:
                continue
            revision = (chain[-1].revision + 1) if chain else 1
            supersedes = chain[-1].id if chain else None
            if live and net != ent.value:                  # a frozen value never changes; a different value is a correction then a re-award
                fix = _event(services, ent, revision, -net, "correction_applied", supersedes, as_of, ent.evidence)
                repos.put_record(RECOGNITION, "RecognitionEvent", fix.id, fix, created_at=as_of)
                result.corrections.append(fix)
                revision, supersedes = revision + 1, fix.id
            ev = _event(services, ent, revision, ent.value, ent.event_type, supersedes, as_of, ent.evidence)
            repos.put_record(RECOGNITION, "RecognitionEvent", ev.id, ev, created_at=as_of)
            result.appended.append(ev)
        evaluated_goals = {g for g, e in evaluations.items() if e.status in SCORED}
        for key, chain in sorted(chains.items()):
            last = chain[-1]
            if key in wanted_keys or last.event_type == "correction_applied" or last.event_type == "badge_earned":
                continue
            if last.goal_id not in evaluated_goals:
                continue                                   # unknown now: hold, never reverse on missing evidence
            net = sum((c.value for c in chain), Fraction(0))
            stale = Entitlement(key, last.event_type, last.team, last.goal_id, last.earning_unit_ids, Fraction(0), last.frozen_gate_context,
                                last.evidence)
            current = evaluations.get(last.goal_id)
            fix = _event(services, stale, last.revision + 1, -net, "correction_applied", last.id, as_of,
                         ((current.id,) if current else ()) + (f"REVERSES:{last.id}",))
            repos.put_record(RECOGNITION, "RecognitionEvent", fix.id, fix, created_at=as_of)
            result.corrections.append(fix)
        result.badges = _badges(services, view, evaluations, wanted, as_of)
    return result


# ---------------------------------------------------------------------- badges

STATEMENTS = {
    "Recovery": "verified planned completion of a frozen behind-gate unit",
    "Firebreak": "a verified predecessor release unlocked previously blocked downstream jobs in one admitted graph revision",
    "CommitmentKeeper": "every defended original unit complete; every miss reviewed relief; complete evidence",
    "SteadyShift": "consecutive fully admitted positive-goal shifts at or above the threshold",
    "SkillSteward": "planned scarce-role work completed and verified; this badge is not a certification and grants no qualification",
    "MonthlyChampion": "best pooled canonical score over the closed month (ties: stable team id)",
}


@dataclass(frozen=True)
class DesiredBadge:
    badge: str
    team: str
    period: str
    evidence: tuple[str, ...]
    goal_id: str | None                                    # goal-scoped badges are held (never revoked) while that goal is unknown
    units: tuple[str, ...] = ()                            # the exact earning units behind it

    @property
    def key(self) -> str:
        return f"{self.badge}:{self.team}:{self.period}"


def _badge_chains(services: Any) -> dict[str, list[BadgeAward]]:
    repos = services.repositories
    chains: dict[str, list[BadgeAward]] = defaultdict(list)
    for bid in repos.list_ids(BADGES):
        b = repos.get_typed(BADGES, bid, "BadgeAward", BadgeAward)
        if b is not None and b.scope == services.settings.scope_key:
            chains[b.dedupe_key.rsplit("#r", 1)[0]].append(b)
    for chain in chains.values():
        chain.sort(key=lambda b: b.revision)
    return chains


def _badge_revision(services: Any, d: DesiredBadge, revision: int, revoked: bool, supersedes: str | None, at: datetime,
                    evidence: tuple[str, ...]) -> BadgeAward:
    key = f"{d.key}#r{revision}"
    award = BadgeAward(digest_id("BDG", services.settings.scope_key, key), key, d.badge, services.settings.scope_key, d.team, d.period, evidence,
                       STATEMENTS[d.badge], at, revision, revoked, supersedes, d.units, services.policy.id)
    repos = services.repositories
    repos.put_record(BADGES, "BadgeAward", award.id, award, created_at=at)
    kind = "correction_applied" if revoked else "badge_earned"
    ent = Entitlement(f"badge_earned:{d.key}", kind, d.team, d.goal_id, d.units, Fraction(0), None, (award.id,) + evidence)
    chain = [c for c in _chains(services).get(ent.base_key, [])]
    ev = _event(services, ent, (chain[-1].revision + 1) if chain else 1, Fraction(0), kind, chain[-1].id if chain else None, at, ent.evidence)
    if repos.get_raw(RECOGNITION, ev.id) is None:
        repos.put_record(RECOGNITION, "RecognitionEvent", ev.id, ev, created_at=at)
    return award


def _reconcile_badges(services: Any, desired: list[DesiredBadge], evaluable_goals: set[str], closed_periods: set[str],
                      at: datetime) -> list[BadgeAward]:
    """Award newly met conditions; append a revocation when a correction removes one (the original award is kept)."""
    out: list[BadgeAward] = []
    chains = _badge_chains(services)
    wanted = {d.key: d for d in desired}
    for key, d in sorted(wanted.items()):
        chain = chains.get(key, [])
        if chain and not chain[-1].revoked:
            continue
        out.append(_badge_revision(services, d, (chain[-1].revision + 1) if chain else 1, False, chain[-1].id if chain else None, at, d.evidence))
    for key, chain in sorted(chains.items()):
        last = chain[-1]
        if key in wanted or last.revoked:
            continue
        goal_scoped = last.badge in ("Recovery", "Firebreak", "CommitmentKeeper", "SkillSteward")
        goal_id = last.period.split(":", 1)[0] if goal_scoped else None
        if (goal_scoped and goal_id not in evaluable_goals) or (not goal_scoped and last.period not in closed_periods and last.badge == "MonthlyChampion"):
            continue                                       # unknown now, or not re-decidable: hold
        d = DesiredBadge(last.badge, last.team, last.period, (), goal_id)
        out.append(_badge_revision(services, d, last.revision + 1, True, last.id, at, (f"REVOKES:{last.id}",)))
    return out


def _badges(services: Any, view: Any, evaluations: dict[str, Evaluation], wanted: list[Entitlement], as_of: datetime) -> list[BadgeAward]:
    policy = services.policy
    desired: list[DesiredBadge] = []
    by_goal: dict[str, list[Entitlement]] = defaultdict(list)
    for w in wanted:
        if w.goal_id:
            by_goal[w.goal_id].append(w)
    records = {r.goal_id: r for r in view.records()}
    for goal_id, items in sorted(by_goal.items()):
        team = records[goal_id].team if goal_id in records else items[0].team
        e = evaluations.get(goal_id)
        if e is None:
            continue
        recov = [w for w in items if w.event_type == "gate_recovery"]
        if recov:
            units = tuple(u for w in recov for u in w.units)
            desired.append(DesiredBadge("Recovery", team, goal_id, (e.id,) + units, goal_id, units))
        for w in items:
            if w.event_type == "dependency_unlocked" and len(w.evidence) - 2 >= policy.int("badges.firebreak_min_unlocked"):
                desired.append(DesiredBadge("Firebreak", team, f"{goal_id}:{w.base_key.rsplit(':', 1)[-1]}", w.evidence, goal_id, w.units))
            if w.event_type == "commitment_kept":
                desired.append(DesiredBadge("CommitmentKeeper", team, goal_id, (e.id,) + w.units, goal_id, w.units))
        steward = _skill_steward(view, records.get(goal_id), e)
        if steward:
            desired.append(DesiredBadge("SkillSteward", team, goal_id, (e.id,) + steward, goal_id, steward))
    desired += _steady_shift(services, view, evaluations)
    champions, closed = _monthly_champion(services, view, evaluations, as_of)
    desired += champions
    evaluable = {g for g, e in evaluations.items() if e.status in SCORED}
    return _reconcile_badges(services, desired, evaluable, closed, as_of)


def _skill_steward(view: Any, record: Any, evaluation: Evaluation) -> tuple[str, ...]:
    from ..performance import history

    if record is None or record.goal is None or evaluation.status not in SCORED:
        return ()
    snapshot = _plan_snapshot(view, record.plan_id)
    if snapshot is None:
        return ()
    from ..eligibility import HoldingIndex

    policy = view.policy
    index = HoldingIndex(snapshot.holdings)                                  # the one certificate-validity authority
    start, end = to_minute(record.window.start), to_minute(record.window.end)
    members = sorted(record.goal.members)
    phases = {p.key.id: p for p in snapshot.phases}
    proof = history.evidence(view.services, evaluation.evidence_hash)
    out = []
    for f in (proof.unit_facts if proof else ()):
        phase = phases.get(f.phase)
        if f.status != "EARNED" or phase is None or not phase.role_demands:
            continue
        for group in phase.role_demands[0].bundle.groups:
            holders = {m for m in members if any(index.holds(m, cert, start, end)[0] for cert in group)}
            if group and 0 < len(holders) <= policy.int("badges.skill_steward_max_holders"):
                out.append(f.unit_id)
                break
    return tuple(out)


def _plan_snapshot(view: Any, plan_id: str) -> Any:
    from ..data.acquisition import load_snapshot

    cache = view.services.extras.setdefault("plan_snapshots", {})
    sid = view.registry.plan_snapshot.get(plan_id)
    if sid is None:
        return None
    if sid not in cache:
        cache[sid] = load_snapshot(view.services.repositories, sid)
    return cache[sid]


def _earned_units(view: Any, evaluations: list[Evaluation]) -> tuple[str, ...]:
    """The verified-credited earning units of the evaluated goals, in goal and unit order (a run or month badge's units)."""
    records = {r.goal_id: r for r in view.records()}
    out: list[str] = []
    for e in evaluations:
        for goal_id in e.goal_ids:
            record = records.get(goal_id)
            out += [u.id for u in (record.units if record is not None else ()) if u.id in view.credits]
    return tuple(out)


def _team_series(view: Any, evaluations: dict[str, Evaluation]) -> dict[str, list[tuple[Any, Evaluation]]]:
    series: dict[str, list[tuple[Any, Evaluation]]] = defaultdict(list)
    for record in view.records():
        e = evaluations.get(record.goal_id)
        if e is not None:
            series[record.team].append((record, e))
    for items in series.values():
        items.sort(key=lambda x: (x[0].window.start, x[0].goal_id))
    return series


def _steady_shift(services: Any, view: Any, evaluations: dict[str, Evaluation]) -> list[DesiredBadge]:
    policy = services.policy
    need, floor = policy.int("badges.steady_shift_run"), Fraction(policy.str("badges.steady_shift_min_score"))
    out = []
    for team, items in sorted(_team_series(view, evaluations).items()):
        run: list[Evaluation] = []
        for record, e in items:
            ok = e.status in SCORED and e.B > 0 and e.score is not None and Fraction(str(e.score)) >= floor
            run = run + [e] if ok else []
            if len(run) >= need:
                window = run[-need:]
                out.append(DesiredBadge("SteadyShift", team, f"RUN:{window[0].goal_ids[0]}:{window[-1].goal_ids[0]}", tuple(x.id for x in window), None,
                                        _earned_units(view, window)))
    return out


def _monthly_champion(services: Any, view: Any, evaluations: dict[str, Evaluation], as_of: datetime) -> tuple[list[DesiredBadge], set[str]]:
    from ..performance.score import aggregate

    months: dict[tuple[int, int], dict[str, list[Evaluation]]] = defaultdict(lambda: defaultdict(list))
    for team, items in _team_series(view, evaluations).items():
        for record, e in items:
            if e.status in SCORED and e.B > 0:
                d: date = record.window.start_date
                months[(d.year, d.month)][team].append(e)
    today = as_of.date()
    out: list[DesiredBadge] = []
    closed: set[str] = set()
    for (year, month), teams in sorted(months.items()):
        first_next = date(year + (month == 12), month % 12 + 1, 1)
        if today < first_next:
            continue                                       # the month has not closed: no champion yet
        period = f"{year}-{month:02d}"
        closed.add(period)
        ranked = []
        for team, members in sorted(teams.items()):
            team_eval = aggregate(services, members, label=f"TEAM {team} {period}", as_of=as_of)
            if team_eval.final is not None:
                ranked.append((-team_eval.final, team, team_eval, members))
        if ranked:
            ranked.sort(key=lambda r: (r[0], r[1]))        # best pooled score; ties: stable team id ascending
            _, team, team_eval, members = ranked[0]
            out.append(DesiredBadge("MonthlyChampion", team, period, (team_eval.id,) + tuple(m.id for m in members), None,
                                    _earned_units(view, members)))
    return out, closed


# ---------------------------------------------------------------------- read projections

def team_progress(services: Any, team: str) -> dict[str, Any]:
    """Verified XP (net of corrections), level, in-level progress and next threshold; badges by name. Never a qualification."""
    size = services.policy.int("xp.level_size")
    events = [c for chain in _chains(services).values() for c in chain if c.team == team]
    xp = sum((c.value for c in events), Fraction(0))
    whole = xp.numerator // xp.denominator if xp >= 0 else 0
    level = 1 + whole // size
    repos = services.repositories
    badges = [b for b in (repos.get_typed(BADGES, i, "BadgeAward", BadgeAward) for i in repos.list_ids(BADGES)) if b is not None and b.team == team]
    return {"team": team, "xp": xp, "level": level, "progress_in_level": xp - (level - 1) * size, "next_threshold": level * size,
            "events": len(events), "badges": sorted(b.badge for b in badges), "qualification_effect": "none"}


def inflation_monitor(services: Any) -> list[dict[str, Any]]:
    """Awards per team per ISO week (corrections excluded) against the policy alert threshold."""
    limit = services.policy.int("xp.inflation_alert_awards_per_team_week")
    counts: dict[tuple[str, int, int], int] = defaultdict(int)
    for chain in _chains(services).values():
        for c in chain:
            if c.event_type in ("completion_credited", "gate_recovery", "badge_earned"):
                iso = c.created_at.isocalendar()
                counts[(c.team, iso[0], iso[1])] += 1
    return [{"team": t, "iso_year": y, "iso_week": w, "awards": n, "alert": n > limit, "limit": limit} for (t, y, w), n in sorted(counts.items())]
