"""S12 LEXICOGRAPHIC COMPARISON: the acceptance key follows the policy's objective order, money outranks raw job counts,
and known cost is compared only over the same aircraft."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

import pytest

from focus.engine.max.optimizer.orchestrator import Evaluation, MaxOptimizer, key_levels


def _ev(levels, *, defended=0, unknown=(), cents=0, nva=0, deferred=0, sig="S") -> Evaluation:
    fields = {"commitments_defended": -defended, "commitments_exact": 0, "unknown_completion_aircraft": len(unknown),
              "controllable_known_cents": Fraction(cents), "travel_plus_wait_minutes": nva, "ot_minutes": 0, "continuity_breaks": 0,
              "deferred_phases": deferred, "signature": sig}
    ev = Evaluation(SimpleNamespace(commitment_results=()), {}, {}, {}, None, defended, 0, nva, 0, 0, 0, sig)
    ev.key, ev.levels, ev.unknown = tuple(fields[f] for f in levels), levels, frozenset(unknown)
    return ev


def test_the_key_follows_the_policy_order(policy) -> None:
    levels = key_levels(policy)
    assert levels[:4] == ("commitments_defended", "commitments_exact", "unknown_completion_aircraft", "controllable_known_cents")
    assert levels.index("deferred_phases") > levels.index("controllable_known_cents") and levels[-1] == "signature"
    swapped = policy.with_overrides({"objective.order": ["FEASIBILITY", "COMMITMENT_DEFENSE", "NVA_MINUTES", "CONTROLLABLE_COST",
                                                         "OT_AND_CHURN", "SCARCITY_AFFINITY_GATE"]}, new_id="nva-first", reason="test")
    assert key_levels(swapped).index("travel_plus_wait_minutes") < key_levels(swapped).index("controllable_known_cents")
    with pytest.raises(ValueError):
        key_levels(policy.with_overrides({"objective.order": ["FEASIBILITY", "CONTROLLABLE_COST"]}, new_id="partial", reason="test"))


def test_money_outranks_the_deferred_count(policy) -> None:
    levels = key_levels(policy)
    cheaper_more_deferred = _ev(levels, cents=1000, deferred=9)
    dearer_fewer_deferred = _ev(levels, cents=2000, deferred=1)
    assert MaxOptimizer.better(cheaper_more_deferred, dearer_fewer_deferred)
    assert not MaxOptimizer.better(dearer_fewer_deferred, cheaper_more_deferred)


def test_known_cost_compares_only_the_same_aircraft(policy) -> None:
    levels = key_levels(policy)
    best = _ev(levels, unknown=(1201,), cents=5000)
    other_unknown = _ev(levels, unknown=(1202,), cents=1000)               # cheaper, but over a different known fleet
    assert not MaxOptimizer.better(other_unknown, best)
    resolved = _ev(levels, unknown=(), cents=9000)                          # makes 1201 known and loses none: better
    assert MaxOptimizer.better(resolved, best) and MaxOptimizer.improved_level(resolved, best) == "unknown_completion_aircraft"
    same_set = _ev(levels, unknown=(1201,), cents=4000)
    assert MaxOptimizer.better(same_set, best)
    defends_more = _ev(levels, defended=1, unknown=(1202,), cents=9000)     # commitment defense precedes cost: comparable
    assert MaxOptimizer.better(defends_more, best)
