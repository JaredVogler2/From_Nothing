"""P18: canonical effort score over unit facts and frozen gate recognition (A57, A58, A59, A60, A61; S14 numeric examples).

Expected values are the S14 numbers as the owner's extra-work decision (D-27) changed them, written down here, never
recomputed by the scorer under test. D-27: adherence is the planned jobs done over the expected work, and unplanned work
earns credit as extra work, below the same planned work, after covering missed planned work that has no reason on record.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from focus.analytics.points import behind_station, gate_context, unit_values
from focus.contracts.evaluation import OffplanFact, OOSFact, UnitFact
from focus.domain import DomainError, EvaluationStatus
from focus.performance.score import (CREDIT_UNKNOWN, NO_REASON, OFFPLAN_OOS_BY_CHOICE, OFFPLAN_OOS_UNCLASSIFIED, WINDOW_OPEN, build_evaluation,
                                     components, formula, grade_for, personal_grade_refusal, pooled, round_half_up, score_policy_errors,
                                     status_and_score)

T = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def unit(i: int, effort: int, status: str, *, relief: bool = False, origin: str = "ORIGINAL", goal: str = "G",
         reasons: tuple[str, ...] = ()) -> UnitFact:
    return UnitFact(f"U{goal}{i}", goal, "J", f"J.WORK.{i}", 0, "PROD-A", effort, origin, status, T if status == "EARNED" else None,
                    "EVT" if status == "EARNED" else None, relief, False, reasons)


def facts(B: int, Cp: int, E: int = 0, *, unit_size: int = 10, goal: str = "G", no_reason: int = 0) -> list[UnitFact]:
    """B minutes of frozen units: Cp earned, E missed-with-relief, the rest missed. ``no_reason`` of the missed minutes
    have no reason on record (no claim); the others have a claim that was not approved."""
    assert B % unit_size == Cp % unit_size == E % unit_size == no_reason % unit_size == 0 and no_reason <= B - Cp - E
    out, i = [], 0
    for status, relief, minutes, why in (("EARNED", False, Cp, ()), ("MISSED", True, E, ()), ("MISSED", False, no_reason, (NO_REASON,)),
                                         ("MISSED", False, B - Cp - E - no_reason, ())):
        for _ in range(minutes // unit_size):
            out.append(unit(i, unit_size, status, relief=relief, goal=goal, reasons=why))
            i += 1
    return out


def offplan(effort: int | None, *, reasons: tuple[str, ...] = ()) -> OffplanFact:
    return OffplanFact("OFP1", "PROD-A", "DEMO:2026-10-26:S1", "J2", "J2.WORK.0", effort, T, "EVT2", reasons)


def oos(affected: int, *, discretionary: bool | None = True, planned: bool = True) -> OOSFact:
    return OOSFact("OOS1", "J.WORK.0", ("U1",), affected, discretionary, planned, discretionary is True and planned, "x")


def score(policy, B, Cp, E=0, U=0, O_affected=0, no_reason=0, **kw):
    parts = components(facts(B, Cp, E, no_reason=no_reason), [offplan(U)] if U else [], [oos(O_affected)] if O_affected else [], policy)
    status, result, reasons = status_and_score(parts, policy, [])
    return status, result, parts, reasons


# ---------------------------------------------------------------------- the S14 examples (A57, A58, A60)

@pytest.mark.parametrize("B,E,Cp,U,O_aff,no_reason,expected", [
    (200, 0, 200, 0, 0, 0, "100.0"),
    (200, 0, 100, 0, 0, 0, "50.0"),        # A57 (65 before D-27): half the expected work done is 50, with no free adherence points
    (200, 0, 100, 100, 0, 100, "50.0"),    # A57: unplanned work done instead of planned work with no reason on record earns nothing
    (200, 0, 100, 100, 0, 0, "90.0"),      # D-27: the same unplanned work as extra work (the misses have a reason): 0.8 x 1 + 0.2 x 0.5
    (200, 0, 100, 50, 0, 0, "70.0"),       # D-27: 50 extra minutes are worth 0.8 of 50 planned minutes (which would give 75)
    (200, 0, 200, 100, 0, 0, "100.0"),     # D-27: extra work beyond a finished plan never lowers the score (96.7 before)
    (200, 0, 0, 0, 0, 0, "0.0"),           # A57: nothing done is 0
    (200, 0, 0, 400, 0, 0, "80.0"),        # D-27: extra work alone never earns the adherence share
    (200, 100, 100, 0, 0, 0, "100.0"),     # A58: reviewed relief, B0 preserved
    (200, 0, 200, 0, 200, 0, "90.0"),      # A60: base 100, separate 10-point charge
])
def test_s14_numeric_examples(policy, B, E, Cp, U, O_aff, no_reason, expected) -> None:
    status, result, parts, _ = score(policy, B, Cp, E, U, O_aff, no_reason)
    assert status is EvaluationStatus.DEMONSTRATION and str(result["score"]) == expected
    assert parts["B0"] == B and parts["Cp"] == Cp and parts["U"] == U                       # Cp never reduced by O; B0 never shrinks


def test_zero_output_is_zero_and_h_is_zero(policy) -> None:
    _, result, _, _ = score(policy, 200, 0)
    assert result["adherence"] == 0 and result["attainment"] == 0 and result["base"] == 0


def test_extra_work_covers_unexplained_misses_first_and_is_credited_below_planned_work(policy) -> None:
    """D-27. Planned 200, done 120; 80 missed, 30 of them with no reason on record; 60 unplanned minutes done."""
    parts = components(facts(200, 120, no_reason=30), [offplan(60)], [], policy)
    assert (parts["U"], parts["Uc"]) == (60, 30)                                            # 30 cover the unexplained miss at no credit
    _, r, _ = status_and_score(parts, policy, [])
    assert r["attainment"] == Fraction(150, 200) and r["adherence"] == Fraction(120, 200) and str(r["score"]) == "72.0"
    # the same 30 credited minutes as planned work would be worth more: extra work earns 0.8 of it
    as_planned = status_and_score(components(facts(200, 150, no_reason=30), [offplan(30)], [], policy), policy, [])[1]
    assert as_planned["final"] - r["final"] == Fraction(100) * Fraction(2, 10) * Fraction(30, 200)
    # a swap never pays: doing the unexplained 30 planned minutes instead of 30 of the unplanned ones scores higher
    instead = status_and_score(components(facts(200, 150), [offplan(30)], [], policy), policy, [])[1]
    assert instead["final"] > r["final"]
    # nor does it cost more than leaving the job undone and doing nothing else: one penalty, not two
    idle = status_and_score(components(facts(200, 120, no_reason=30), [], [], policy), policy, [])[1]
    swap = status_and_score(components(facts(200, 120, no_reason=30), [offplan(30)], [], policy), policy, [])[1]
    assert swap["final"] == idle["final"] == 60


def test_extra_work_started_out_of_sequence_by_choice_earns_nothing_and_unclassified_is_unknown(policy) -> None:
    by_choice = components(facts(200, 100), [offplan(100, reasons=(OFFPLAN_OOS_BY_CHOICE,))], [], policy)
    assert (by_choice["U"], by_choice["Uc"]) == (100, 0) and str(status_and_score(by_choice, policy, [])[1]["score"]) == "50.0"
    unclassified = components(facts(200, 100), [offplan(100, reasons=(OFFPLAN_OOS_UNCLASSIFIED,))], [], policy)
    status, result, reasons = status_and_score(unclassified, policy, [])
    assert unclassified["Uc"] is None and status is EvaluationStatus.UNKNOWN and result["score"] is None
    assert any(r.startswith("OFFPLAN_OOS_CLASSIFICATION_UNKNOWN") for r in reasons)


def test_a_member_scored_before_the_extra_work_credit_makes_the_pool_unknown(policy) -> None:
    old = {**components(facts(200, 100), [], [], policy), "Uc": None}                       # a record written before D-27
    status, result, reasons = status_and_score(pooled([old, components(facts(100, 100, goal="B"), [], [], policy)]), policy, [])
    assert status is EvaluationStatus.UNKNOWN and result["score"] is None and reasons == [CREDIT_UNKNOWN]


def test_oos_charge_is_separate_and_once(policy) -> None:
    status, result, parts, _ = score(policy, 200, 200, O_affected=200)
    assert parts["O"] == 20 and result["base"] == 100 and result["final"] == 90 and parts["Cp"] == 200
    # unapproved offplan OOS is in U and earns no extra-work credit, but there is no second numeric O
    parts = components(facts(200, 200), [offplan(100, reasons=(OFFPLAN_OOS_BY_CHOICE,))], [oos(200), oos(100, planned=False)], policy)
    _, result, _ = status_and_score(parts, policy, [])
    assert parts["O"] == 20 and parts["U"] == 100 and parts["Uc"] == 0
    assert result["base"] == 100 and result["final"] == 90
    # XP is the frozen recognition value: never reduced by O
    assert sum(unit_values(None, f.effort, policy)[0] for f in facts(200, 200) if f.status == "EARNED") == 40


@pytest.mark.parametrize("B,E,Cp", [(200, 200, 0), (0, 0, 0)])
def test_all_excused_or_no_plan_is_neutral_null(policy, B, E, Cp) -> None:
    status, result, parts, reasons = score(policy, B, Cp, E)
    assert status is EvaluationStatus.NEUTRAL and result["score"] is None and result["grade"] is None and result["base"] is None
    assert reasons[0].startswith("NO_PLAN" if B == 0 else "ALL_EXCUSED")
    assert parts["B0"] == B                                                                # not 100, not F, and B0 retained


def test_missing_evidence_or_policy_is_unknown_null(policy) -> None:
    unknown = facts(200, 100) + [unit(99, 10, "UNKNOWN")]
    parts = components(unknown, [], [], policy)
    status, result, reasons = status_and_score(parts, policy, [])
    assert status is EvaluationStatus.UNKNOWN and result["score"] is None and any(r.startswith("UNKNOWN_DISPOSITION") for r in reasons)
    status, result, reasons = status_and_score(components(facts(200, 100), [], [], policy), policy, ["COVERAGE_EXCUSAL_DECISIONS_FAILED: x"])
    assert status is EvaluationStatus.UNKNOWN and result["score"] is None
    parts = components(facts(200, 200), [offplan(None)], [], policy)                          # U without a sourced standard
    assert parts["U"] is None and status_and_score(parts, policy, [])[0] is EvaluationStatus.UNKNOWN
    parts = components(facts(200, 200), [], [oos(50, discretionary=None)], policy)            # unclassified planned OOS
    assert parts["O"] is None and status_and_score(parts, policy, [])[0] is EvaluationStatus.UNKNOWN
    pending = facts(200, 100) + [unit(98, 10, "PENDING")]
    assert WINDOW_OPEN in status_and_score(components(pending, [], [], policy), policy, [])[2]


# ---------------------------------------------------------------------- A59: pooled aggregation

def test_a59_pooled_aggregate_is_60_not_the_average_75(policy) -> None:
    """A59: sums before division. The S14 numbers were 72 and 82.5; without the free adherence points (D-27) they are 60 and 75."""
    a = components(facts(400, 200, goal="A"), [], [], policy)
    b = components(facts(100, 100, goal="B"), [], [], policy)
    each = [formula(p["B"], p["E"], p["Cp"], p["Uc"], p["O"], policy)["final"] for p in (a, b)]
    assert each == [50, 100] and (each[0] + each[1]) / 2 == 75                               # the wrong answer, 75
    whole = pooled([a, b])
    status, result, _ = status_and_score(whole, policy, [])
    assert (whole["B"], whole["Cp"]) == (500, 300) and str(result["score"]) == "60.0"
    unit_level = components(facts(400, 200, goal="A") + facts(100, 100, goal="B"), [], [], policy)
    assert status_and_score(unit_level, policy, [])[1]["final"] == result["final"]             # units pooled == components pooled


def test_extra_work_beyond_one_shifts_expected_amount_counts_in_the_pooled_period(policy) -> None:
    """D-27: a team that finished its plan and worked ahead scores 100 that shift, and the extra minutes count toward the
    period: they fill the output share of another shift's shortfall, never its adherence share."""
    ahead = components(facts(200, 200, goal="A"), [offplan(50)], [], policy)                 # 100 on its own, with 50 credited
    short = components(facts(200, 150, goal="B"), [], [], policy)                            # 75 on its own
    assert [str(status_and_score(p, policy, [])[1]["score"]) for p in (ahead, short)] == ["100.0", "75.0"]
    both = status_and_score(pooled([ahead, short]), policy, [])[1]
    assert both["attainment"] == 1 and both["adherence"] == Fraction(350, 400) and str(both["score"]) == "97.5"   # 87.5 without it


def test_rounding_only_after_sums_and_bands(policy) -> None:
    assert round_half_up(Fraction(1799, 20), 1) == Decimal("90.0")                            # 89.95 -> 90.0 (half up, once)
    assert round_half_up(Fraction(8994, 100), 1) == Decimal("89.9")
    assert [grade_for(Decimal(x), policy) for x in ("90.0", "89.9", "80.0", "70.0", "60.0", "59.9")] == ["A", "B", "B", "C", "D", "F"]
    thirds = [components(facts(30, 10, unit_size=10, goal=g), [], [], policy) for g in "XYZ"]
    r = status_and_score(pooled(thirds), policy, [])[1]
    assert r["final"] == Fraction(100, 3) and str(r["score"]) == "33.3"


# ---------------------------------------------------------------------- official status and personal grades

def test_official_status_needs_an_applicable_policy_receipt(policy) -> None:
    assert score_policy_errors(policy) == [] and policy.str("score.policy_status") == "DEMONSTRATION"
    official = policy.with_overrides({"score.policy_status": "OFFICIAL"}, new_id="official-unapproved")
    assert any(e.startswith("SCORE_POLICY_UNAPPROVED") for e in score_policy_errors(official))
    approved = official.with_overrides({"score.policy_approval_ref": "APPROVAL-RECEIPT-1"}, new_id="official-approved", authority="user_requirement")
    assert score_policy_errors(approved) == []
    parts = components(facts(200, 100), [], [], approved)
    assert status_and_score(parts, approved, [])[0] is EvaluationStatus.OFFICIAL
    bad = policy.with_overrides({"score.attainment_weight": "0.70"}, new_id="bad-weights")
    assert any(e.startswith("SCORE_POLICY_INVALID") for e in score_policy_errors(bad))
    e = build_evaluation(scope="S", kind="GOAL", label="x", goal_ids=["G"], cutoff=T, policy=bad, parts=parts, extra_reasons=score_policy_errors(bad),
                         evidence_hash="h")
    assert e.status is EvaluationStatus.UNKNOWN and e.score is None


def test_personal_grades_stay_disabled_without_their_own_policy(policy) -> None:
    assert personal_grade_refusal(policy).startswith("PERSONAL_GRADES_DISABLED")


# ---------------------------------------------------------------------- A61: gate recognition

def test_a61_actual_gate_ages(policy) -> None:
    gate = date(2026, 10, 20)
    raws = [gate_context(policy, gate, gate + timedelta(days=age), 200, basis="ACTUAL_CS_GATE").behind_raw for age in (0, 2, 3, 5, 7)]
    assert raws == [0, Fraction(2, 5), Fraction(3, 5), 1, 1]
    ctx = gate_context(policy, gate, gate + timedelta(days=3), 200, basis="ACTUAL_CS_GATE")
    assert ctx.base_value == 40 and ctx.bonus_value == 40 * Fraction(35, 100) * Fraction(3, 5) and ctx.effective_gate_date == gate


def test_a61_proxy_grace_once_future_absent_and_completion(policy) -> None:
    gate = date(2026, 10, 20)
    proxy = gate_context(policy, gate, gate + timedelta(days=3), 200, basis="BASELINE_PROXY")
    assert proxy.effective_gate_date == gate + timedelta(days=2) and proxy.behind_raw == Fraction(1, 5)       # +2 calendar days
    adjusted = gate_context(policy, proxy.effective_gate_date, gate + timedelta(days=3), 200, basis="ALREADY_ADJUSTED_PROXY")
    assert adjusted.effective_gate_date == proxy.effective_gate_date and adjusted.behind_raw == Fraction(1, 5)  # never twice
    future = gate_context(policy, gate, gate - timedelta(days=4), 200, basis="ACTUAL_CS_GATE")
    assert future.behind_raw == 0 and future.behind_days == 0 and future.bonus_value == 0                       # no negative offset
    day0 = gate_context(policy, gate, gate, 200, basis="ACTUAL_CS_GATE")
    assert day0.basis == "ACTUAL_CS_GATE" and day0.behind_raw == 0                                             # day 0 is a real gate
    absent = gate_context(policy, None, gate, 200)
    assert absent.basis == "UNAVAILABLE" and absent.behind_raw is None and absent.bonus_value is None and absent.base_value == 40
    done = gate_context(policy, gate, gate + timedelta(days=9), 200, basis="ACTUAL_CS_GATE", completed_on=gate + timedelta(days=2))
    assert done.valuation_date == gate + timedelta(days=2) and done.behind_raw == Fraction(2, 5)               # completion stops aging


def test_behind_station_value_names_its_weight_cap_grace_and_date_basis(policy) -> None:
    gate = date(2026, 10, 20)
    actual = behind_station(policy, gate, gate + timedelta(days=3), basis="ACTUAL_CS_GATE")
    assert (actual["basis"], actual["effective_gate_date"], actual["grace_days"], actual["behind_days"]) == ("ACTUAL_CS_GATE", gate, 0, 3)
    assert actual["behind_raw"] == Fraction(3, 5) and actual["bonus_factor"] == Fraction(21, 100)
    assert (actual["weight"], actual["cap_days"], actual["valuation_date"]) == (35, 5, gate + timedelta(days=3))
    proxy = behind_station(policy, gate, gate + timedelta(days=3), basis="BASELINE_PROXY")
    assert proxy["effective_gate_date"] == gate + timedelta(days=2) and proxy["grace_days"] == 2 and proxy["bonus_factor"] == Fraction(7, 100)
    capped = behind_station(policy, gate, gate + timedelta(days=10))                                           # policy basis, past the cap
    assert capped["basis"] == policy.str("gate.date_basis") and capped["behind_raw"] == 1 and capped["bonus_factor"] == Fraction(35, 100)
    missing = behind_station(policy, None, gate)
    assert missing["basis"] == "UNAVAILABLE" and missing["behind_days"] is None and missing["bonus_factor"] is None   # never zero-filled
    with pytest.raises(DomainError):
        gate_context(policy, gate, gate, 200, basis="WHATEVER")


def test_a61_unit_values_use_the_frozen_context(policy) -> None:
    gate = date(2026, 10, 20)
    frozen = gate_context(policy, gate, gate + timedelta(days=5), 100, basis="ACTUAL_CS_GATE")
    assert unit_values(frozen, 50, policy) == (10, 10 * Fraction(35, 100))
    assert unit_values(gate_context(policy, None, gate, 100), 50, policy) == (10, 0)           # unavailable gate: base only


def test_done_before_shift_is_out_of_d_and_never_earned(policy) -> None:
    """D-26: a unit the previous shift finished after this shift's plan was made stays in B0, leaves D with the relief."""
    rows = facts(200, 100, 0)
    rows[-5:] = [unit(90 + i, 10, "DONE_BEFORE_SHIFT") for i in range(5)]                   # 50 of the 100 missed minutes were done before
    parts = components(rows, [], [], policy)
    assert (parts["B0"], parts["E"], parts["Cp"]) == (200, 50, 100)
    status, result, reasons = status_and_score(parts, policy, [])
    assert not reasons and result["D"] == 150 and result["attainment"] == Fraction(100, 150)
    done = components([unit(i, 10, "DONE_BEFORE_SHIFT") for i in range(3)], [], [], policy)
    status, _, why = status_and_score(done, policy, [])
    assert status is EvaluationStatus.NEUTRAL and why[0].startswith("ALL_EXCUSED")         # nothing was left to do: no score
