"""One canonical effort score over frozen earning units (S14 CANONICAL SCORE, P18).

Components come from verified unit facts only, all in standard *person* minutes (never task
counts, elapsed or actual burn, never dollars, and never multiplied by crew again):

* B0 = original frozen effort.
* B = B0 + approved feasible emergency additions.
* E = approved relief on still-missed units.
* D = B - E.
* Cp = verified planned/amended completion, counted once.
* U = unapproved offplan verified completion.
* O = ``score.oos_rate`` x affected planned effort of evidenced discretionary OOS, counted once
  per unit.

When D > 0:

* A = min(1, Cp/D);
* H = Cp/(Cp+U) if output > 0, else 0;
* base = 100 * (wA*A + wH*H);
* final = clamp(base - 100*O/D, 0, 100), rounded only at the very end.

Offplan OOS is already in U and receives no second numeric charge. Cp stays unchanged by O.

**Null semantics.**

* D = 0 (no plan, or all excused) is NEUTRAL with no score.
* Missing coverage, unknown dispositions, unknown U/O, an open window or an invalid policy is
  UNKNOWN with no score.
* Components are never redistributed to invent a score.

The status (DEMONSTRATION or OFFICIAL) comes from the applicable policy receipt, never from a
hardcoded approval. Personal grades stay disabled unless their own policy exists.

**Aggregation.** Team, aircraft, factory and history totals pool the exact components before
dividing. D400/Cp200 plus D100/Cp100 is 72, not the 82.5 average of percentages. Every surface
reads the same persisted evaluations (``history``), so all views agree by construction.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any, Iterable

from ..codec import content_hash, parse_utc
from ..contracts.evaluation import Evaluation, EvaluationEvidence, OffplanFact, OOSFact, UnitFact
from ..domain import EvaluationStatus, aware_utc, digest_id


WINDOW_OPEN = "WINDOW_OPEN: the shift's credit window has not closed; output, offplan work and OOS can still change"


# ---------------------------------------------------------------------- pure arithmetic (the one formula authority)

def score_policy_errors(policy: Any) -> list[str]:
    out = []
    try:
        wa, wh = Fraction(policy.str("score.attainment_weight")), Fraction(policy.str("score.adherence_weight"))
        rate = Fraction(policy.str("score.oos_rate"))
    except (ValueError, TypeError) as exc:
        return [f"SCORE_POLICY_INVALID: {exc}"]
    if wa < 0 or wh < 0 or wa + wh != 1:
        out.append("SCORE_POLICY_INVALID: attainment and adherence weights must be non-negative and sum to 1")
    if not 0 <= rate <= 1:
        out.append("SCORE_POLICY_INVALID: score.oos_rate must lie in [0, 1]")
    bands = [int(b[1]) for b in policy.seq("score.bands")]
    if bands != sorted(bands, reverse=True):
        out.append("SCORE_POLICY_INVALID: score.bands must be strictly descending thresholds")
    status = policy.str("score.policy_status")
    if status not in ("DEMONSTRATION", "OFFICIAL"):
        out.append(f"SCORE_POLICY_INVALID: unknown score.policy_status {status!r}")
    elif status == "OFFICIAL" and not policy.optional("score.policy_approval_ref"):
        out.append("SCORE_POLICY_UNAPPROVED: an OFFICIAL score policy needs its approval receipt reference")
    return out


def round_half_up(value: Fraction, decimals: int) -> Decimal:
    """Exact half-away-from-zero rounding of a rational, applied once to the final unrounded value."""
    scaled = value * 10 ** decimals
    half = Fraction(1, 2)
    q = math.floor(scaled + half) if scaled >= 0 else -math.floor(-scaled + half)
    return Decimal(q).scaleb(-decimals)


def grade_for(score: Decimal, policy: Any) -> str:
    for label, threshold in policy.seq("score.bands"):
        if score >= Decimal(int(threshold)):
            return str(label)
    return "F"


def formula(B: Fraction, E: Fraction, Cp: Fraction, U: Fraction, O: Fraction, policy: Any) -> dict[str, Any]:
    """S14 arithmetic on exact components. D == 0 -> neutral (all None)."""
    D = B - E
    if D <= 0:
        return {"D": D, "attainment": None, "adherence": None, "base": None, "final": None, "score": None, "grade": None}
    wa, wh = Fraction(policy.str("score.attainment_weight")), Fraction(policy.str("score.adherence_weight"))
    rate_charge = Fraction(100) * O / D
    A = min(Fraction(1), Cp / D)
    output = Cp + U
    H = Cp / output if output > 0 else Fraction(0)
    base = Fraction(100) * (wa * A + wh * H)
    final = min(Fraction(100), max(Fraction(0), base - rate_charge))
    score = round_half_up(final, policy.int("score.round_decimals"))
    return {"D": D, "attainment": A, "adherence": H, "base": base, "final": final, "score": score, "grade": grade_for(score, policy)}


# ---------------------------------------------------------------------- components from unit facts

def components(facts: Iterable[UnitFact], offplan: Iterable[OffplanFact], oos: Iterable[OOSFact], policy: Any) -> dict[str, Any]:
    facts, offplan, oos = list(facts), list(offplan), list(oos)
    reasons: list[str] = []
    B0 = Fraction(sum(f.effort for f in facts if f.origin == "ORIGINAL"))
    additions = Fraction(sum(f.effort for f in facts if f.origin == "AMENDMENT"))
    Cp = Fraction(sum(f.effort for f in facts if f.status == "EARNED"))
    E = Fraction(sum(f.effort for f in facts if f.status == "MISSED" and f.relief))
    unknown_units = [f for f in facts if f.status in ("UNKNOWN", "PENDING")]
    if any(f.status == "PENDING" for f in unknown_units):
        reasons.append(WINDOW_OPEN)
    if any(f.status == "UNKNOWN" for f in unknown_units):
        reasons.append(f"UNKNOWN_DISPOSITION: {sum(1 for f in unknown_units if f.status == 'UNKNOWN')} unit(s) have unknown completion/disposition")
    U: Fraction | None = Fraction(0)
    for o in offplan:
        if o.effort is None:
            U = None
            reasons.append(f"OFFPLAN_EFFORT_UNKNOWN: {o.phase} has no sourced standard")
            break
        U += o.effort  # type: ignore[operator]
    rate = Fraction(policy.str("score.oos_rate"))
    O: Fraction | None = rate * sum((Fraction(x.affected_effort) for x in oos if x.charged), Fraction(0))
    if any(x.discretionary is None and x.planned for x in oos):
        O = None
        reasons.append("OOS_CLASSIFICATION_UNKNOWN: an evidenced planned violation has no discretionary classification")
    return {"B0": B0, "B": B0 + additions, "E": E, "Cp": Cp, "U": U, "O": O, "reasons": reasons}


def pooled(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Sum exact components across goals before any division (team/aircraft/factory/history)."""
    out: dict[str, Any] = {"B0": Fraction(0), "B": Fraction(0), "E": Fraction(0), "Cp": Fraction(0), "U": Fraction(0), "O": Fraction(0), "reasons": []}
    for p in parts:
        for k in ("B0", "B", "E", "Cp"):
            out[k] += p[k]
        for k in ("U", "O"):
            out[k] = None if (out[k] is None or p[k] is None) else out[k] + p[k]
        out["reasons"] += list(p["reasons"])
    return out


def status_and_score(parts: dict[str, Any], policy: Any, extra_reasons: list[str]) -> tuple[EvaluationStatus, dict[str, Any], list[str]]:
    reasons = list(extra_reasons) + list(parts["reasons"])
    blank = {"D": parts["B"] - parts["E"], "attainment": None, "adherence": None, "base": None, "final": None, "score": None, "grade": None}
    if reasons:
        return EvaluationStatus.UNKNOWN, blank, reasons
    if parts["B"] - parts["E"] <= 0:
        why = "NO_PLAN: no frozen obligation (B = 0)" if parts["B"] == 0 else "ALL_EXCUSED: approved relief covers every missed obligation (D = 0)"
        return EvaluationStatus.NEUTRAL, blank, [why]
    result = formula(parts["B"], parts["E"], parts["Cp"], parts["U"], parts["O"], policy)
    status = EvaluationStatus.OFFICIAL if policy.str("score.policy_status") == "OFFICIAL" else EvaluationStatus.DEMONSTRATION
    return status, result, []


def build_evaluation(*, scope: str, kind: str, label: str, goal_ids: Iterable[str], cutoff: datetime, policy: Any, parts: dict[str, Any],
                     extra_reasons: list[str], evidence_hash: str) -> Evaluation:
    from .history import series_key

    goal_ids = tuple(sorted(set(goal_ids)))
    status, r, reasons = status_and_score(parts, policy, extra_reasons)
    goal_set_hash = content_hash(list(goal_ids))
    key = series_key(scope, kind, goal_ids, policy.id)
    return Evaluation(
        id=digest_id("EVL", scope, cutoff, policy.hash, goal_set_hash, evidence_hash), scope=scope, kind=kind, label=label, series_key=key,
        goal_ids=goal_ids, goal_set_hash=goal_set_hash, cutoff=cutoff, policy_id=policy.id, policy_hash=policy.hash,
        policy_status=policy.str("score.policy_status"), B0=parts["B0"], B=parts["B"], E=parts["E"], D=r["D"], Cp=parts["Cp"], U=parts["U"],
        O=parts["O"], attainment=r["attainment"], adherence=r["adherence"], base=r["base"], final=r["final"], score=r["score"], grade=r["grade"],
        status=status, reasons=tuple(dict.fromkeys(reasons)), evidence_hash=evidence_hash, revision=0, supersedes=None, created_at=cutoff)


# ---------------------------------------------------------------------- the evaluation service

def evidence_for(view: Any, record: Any) -> tuple[EvaluationEvidence, dict[str, Any], list[str]]:
    from .actuals import coverage_reasons
    from .disruption import relief_summary, taxonomy_errors

    facts = view.unit_facts(record)
    offplan = view.offplan_for(record.team, record.shift_id)
    oos = view.oos_for(record)
    coverage = coverage_reasons(view.run, record.window.credit_deadline)
    relief = relief_summary(view, record, facts)
    payload = {"unit_facts": facts, "offplan": offplan, "oos": oos, "offplan_oos": view.offplan_oos(record.team, record.shift_id), "relief": relief,
               "coverage": coverage}
    evidence = EvaluationEvidence(content_hash(payload), tuple(facts), tuple(offplan), tuple(oos), tuple(payload["offplan_oos"]), relief,
                                  tuple(coverage), None if view.run is None else view.run.id)
    parts = components(facts, offplan, oos, view.policy)
    errors = score_policy_errors(view.policy) + taxonomy_errors(view.policy) + list(view.errors) + list(coverage)
    if view.as_of < record.window.credit_deadline:
        errors.insert(0, WINDOW_OPEN)
    if relief["pending_claims"]:
        parts["notes"] = [f"CLAIMS_PENDING_REVIEW: {len(relief['pending_claims'])}"]
    return evidence, parts, errors


def evaluate_shift(services: Any, team: str, shift_id: str, *, as_of: datetime | None = None, view: Any = None, persist: bool = True) -> Evaluation:
    """Evaluate one team-shift goal of record at ``as_of`` and append it to its revision history."""
    from . import history
    from .lifecycle import ObservationView

    as_of = aware_utc(as_of or services.clock.now(), "as_of")
    view = view or ObservationView.build(services, as_of)
    policy = services.policy
    record = view.record(team, shift_id)
    scope = services.settings.scope_key
    if record is None:
        parts = {"B0": Fraction(0), "B": Fraction(0), "E": Fraction(0), "Cp": Fraction(0), "U": Fraction(0), "O": Fraction(0), "reasons": []}
        evidence = EvaluationEvidence(content_hash({"no_obligation_plan": [team, shift_id]}), (), (), (), (), {}, (), None)
        draft = build_evaluation(scope=scope, kind="GOAL", label=f"{team} {shift_id}", goal_ids=(digest_id("GOAL", "NO-PLAN", team, shift_id),),
                                 cutoff=as_of, policy=policy, parts=parts, extra_reasons=score_policy_errors(policy), evidence_hash=evidence.id)
        draft = replace(draft, reasons=draft.reasons + ("NO_OBLIGATION_PLAN: no accepted executable plan covers this shift day",))
        return history.append(services, draft, evidence)[0] if persist else draft
    evidence, parts, errors = evidence_for(view, record)
    draft = build_evaluation(scope=scope, kind="GOAL", label=f"{team} {shift_id}", goal_ids=(record.goal_id,), cutoff=as_of, policy=policy,
                             parts=parts, extra_reasons=errors, evidence_hash=evidence.id)
    if parts.get("notes"):
        draft = replace(draft, reasons=draft.reasons + tuple(parts["notes"]))
    return history.append(services, draft, evidence)[0] if persist else draft


def evaluate_all(services: Any, *, as_of: datetime, view: Any = None) -> list[Evaluation]:
    """Every goal of record whose credit window has closed by ``as_of``, plus team-shifts that only produced offplan output."""
    from .lifecycle import ObservationView

    view = view or ObservationView.build(services, as_of)
    seen: set[tuple[str, str]] = set()
    out = []
    for record in view.records():
        if record.window.credit_deadline <= as_of:
            seen.add((record.team, record.shift_id))
            out.append(evaluate_shift(services, record.team, record.shift_id, as_of=as_of, view=view))
    for o in view.offplan:
        key = (o.team, o.shift_id)
        if key not in seen and view.registry.goal_id_for(*key) is not None:
            window_ok = view.record(*key)
            if window_ok is not None and window_ok.window.credit_deadline <= as_of:
                seen.add(key)
                out.append(evaluate_shift(services, o.team, o.shift_id, as_of=as_of, view=view))
    return out


def aggregate(services: Any, members: list[Evaluation], *, label: str, as_of: datetime, persist: bool = True) -> Evaluation:
    """Pooled evaluation over member goal evaluations (team/aircraft/factory/history): sums before division."""
    from . import history

    policy = services.policy
    scope = services.settings.scope_key
    parts = pooled({"B0": m.B0, "B": m.B, "E": m.E, "Cp": m.Cp, "U": m.U, "O": m.O, "reasons": []} for m in members)
    reasons = [f"MEMBER_UNKNOWN: {m.label}" for m in members if m.status is EvaluationStatus.UNKNOWN]
    if any(m.policy_hash != policy.hash for m in members):
        reasons.append("MIXED_POLICY: members were evaluated under another policy")
    evidence = EvaluationEvidence(content_hash({"members": sorted((m.id, m.evidence_hash) for m in members)}), (), (), (), (),
                                  {"members": sorted(m.id for m in members)}, (), None)
    draft = build_evaluation(scope=scope, kind="AGGREGATE", label=label, goal_ids=[g for m in members for g in m.goal_ids], cutoff=as_of,
                             policy=policy, parts=parts, extra_reasons=reasons + score_policy_errors(policy), evidence_hash=evidence.id)
    return history.append(services, draft, evidence)[0] if persist else draft


def personal_grade_refusal(policy: Any) -> str | None:
    """Personal grades stay disabled unless their own approved policy exists (S14); team evaluations are unaffected."""
    if not policy.bool("score.personal_grades_enabled"):
        return "PERSONAL_GRADES_DISABLED: no personal grading policy exists; team-shift evaluations only"
    if policy.optional("score.personal_policy_approval_ref") is None:
        return "PERSONAL_GRADES_UNAPPROVED: personal grading needs its own approved policy receipt"
    return None


def canonical(services: Any, team: str, shift_id: str, *, as_of: datetime | None = None) -> Evaluation | None:
    """What every surface (iOS, Classic, Arena, team/factory/history panels, API, MCP) reads: the persisted revision, never a recomputation."""
    from . import history
    from .lifecycle import GoalRegistry

    goal_id = GoalRegistry.load(services).goal_id_for(team, shift_id)
    if goal_id is None:
        return None
    key = history.series_for_goal(services, goal_id)
    scope = services.settings.scope_key
    return history.current(services, scope, key) if as_of is None else history.as_known(services, scope, key, as_of)


def summary(e: Evaluation) -> dict[str, Any]:
    def num(x: Fraction | None) -> str | None:
        return None if x is None else (str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}")

    return {"evaluation_id": e.id, "kind": e.kind, "label": e.label, "status": e.status.value, "policy_id": e.policy_id,
            "policy_status": e.policy_status, "revision": e.revision, "supersedes": e.supersedes, "cutoff": e.cutoff.isoformat(),
            "B0": num(e.B0), "B": num(e.B), "E": num(e.E), "D": num(e.D), "Cp": num(e.Cp), "U": num(e.U), "O": num(e.O),
            "attainment": num(e.attainment), "adherence": num(e.adherence), "base": num(e.base), "final": num(e.final),
            "score": None if e.score is None else str(e.score), "grade": e.grade, "reasons": list(e.reasons), "evidence_hash": e.evidence_hash,
            "goal_ids": list(e.goal_ids)}


def cli_evaluate(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    from ..calendar import parse_shift_id
    from .lifecycle import GoalRegistry

    if args.policy != services.policy.id:
        return {"status": "invalid", "error": {"code": "POLICY_MISMATCH", "message": f"active policy is {services.policy.id}"}}
    try:
        parse_shift_id(args.shift)
        as_of = parse_utc(args.as_of) if getattr(args, "as_of", None) else services.clock.now()
    except ValueError as exc:
        return {"status": "invalid", "error": {"code": "BAD_ARGUMENT", "message": str(exc)}}
    registry = GoalRegistry.load(services)
    teams = [args.team] if args.team else sorted({r.team for (team, shift), r in registry.records.items() if shift == args.shift})
    if args.plan:
        _, day, _ = parse_shift_id(args.shift)
        if registry.day_plan.get(day) != args.plan:
            return {"status": "invalid", "error": {"code": "PLAN_NOT_OF_RECORD", "message": "that plan is not the obligation plan of record for the day"}}
    if not teams:
        return {"status": "unknown", "shift": args.shift, "as_of": as_of.isoformat(), "evaluations": [],
                "reasons": ["NO_OBLIGATION_PLAN: no accepted executable plan froze goals for this shift; name --team to record a neutral evaluation"]}
    evaluations = [evaluate_shift(services, team, args.shift, as_of=as_of) for team in teams]
    status = "ok" if all(e.status is not EvaluationStatus.UNKNOWN for e in evaluations) else "unknown"
    return {"status": status, "shift": args.shift, "as_of": as_of.isoformat(), "evaluations": [summary(e) for e in evaluations]}
