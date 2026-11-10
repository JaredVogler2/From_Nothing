"""The objective explainer (docs/execution/OBJECTIVE_EXPLAINED.md) matches the code.

* the order it gives is the policy's ``objective.order`` and the optimizer's comparison key;
* its worked example is what the economics registry and the comparison return;
* every file, function and test named in its rules table exists;
* the decision playbook covers each cause of an unknown cost it sends the reader to.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from focus.contracts.planning import AircraftEconomics
from focus.domain import to_minute
from focus.economics.registry import EconomicsRegistry
from focus.engine.max.optimizer.orchestrator import OBJECTIVE_FIELDS, Evaluation, MaxOptimizer, key_levels

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "docs" / "execution" / "OBJECTIVE_EXPLAINED.md").read_text(encoding="utf-8")
TOKEN = re.compile(r"`([^`]+)`")


def _table(header: str) -> list[list[str]]:
    start = TEXT.index(header)
    rows = []
    for line in TEXT[start:].splitlines()[2:]:
        if not line.startswith("|"):
            break
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def test_the_order_is_the_policys_and_the_comparison_keys(policy) -> None:
    rows = _table("| Order | A schedule is better when it has | Policy name |")
    assert [r[0] for r in rows] == [str(i) for i in range(8)]
    named = [TOKEN.findall(r[2])[0] for r in rows[:7]]
    order = list(policy.seq("objective.order"))
    assert list(dict.fromkeys(named)) == order                                    # the same levels, in the same order
    # the cost level holds two comparisons: unknown aircraft first, then dollars
    assert OBJECTIVE_FIELDS["CONTROLLABLE_COST"] == ("unknown_completion_aircraft", "controllable_known_cents")
    assert named.count("CONTROLLABLE_COST") == 2 and "finish date cannot be worked out" in rows[2][1] and "dollars" in rows[3][1]
    levels = key_levels(policy)
    assert levels == ("commitments_defended", "commitments_exact", "unknown_completion_aircraft", "controllable_known_cents",
                      "travel_plus_wait_minutes", "ot_minutes", "continuity_breaks", "deferred_phases", "signature")
    assert policy.int("economics.monetary_tolerance_cents") == 0 and policy.str("economics.cap_policy") == "EXPLICIT_ONLY"
    assert policy.int("gate.proxy_grace_days") == 2 and "plus 2 days" in TEXT


def _econ(line: int, usd_per_day: int, due: datetime) -> AircraftEconomics:
    return AircraftEconomics(line, "KNOWN", "ASSUMED", f"T{line}", "CUSTOMER_MODEL", "USD", Fraction(usd_per_day * 100), None, None, None, due,
                             "test due", "LINEAR_DAILY", ())


def _ev(levels, *, unknown=(), cents=0, defended=0) -> Evaluation:
    fields = {"commitments_defended": -defended, "commitments_exact": 0, "unknown_completion_aircraft": len(unknown),
              "controllable_known_cents": Fraction(cents), "travel_plus_wait_minutes": 0, "ot_minutes": 0, "continuity_breaks": 0,
              "deferred_phases": 0, "signature": "S"}
    ev = Evaluation(SimpleNamespace(commitment_results=()), {}, {}, {}, None, defended, 0, 0, 0, 0, 0, "S")
    ev.key, ev.levels, ev.unknown = tuple(fields[f] for f in levels), levels, frozenset(unknown)
    return ev


def test_the_worked_example_is_what_the_registry_and_the_comparison_return(policy) -> None:
    a_due, b_due = datetime(2027, 3, 13, tzinfo=UTC), datetime(2027, 3, 15, tzinfo=UTC)      # the end of 12 and of 14 March
    reg = EconomicsRegistry({1: _econ(1, 380_000, a_due), 2: _econ(2, 187_000, b_due)}, policy)

    def usd(line: int, finish: datetime) -> Fraction:
        return reg.penalty(line, to_minute(finish)) / 100

    one = usd(1, datetime(2027, 3, 14, 12, tzinfo=UTC)) + usd(2, datetime(2027, 3, 14, 18, tzinfo=UTC))
    two = usd(1, datetime(2027, 3, 13, 12, tzinfo=UTC)) + usd(2, datetime(2027, 3, 15, 6, tzinfo=UTC))
    assert (one, two) == (570_000, 236_750) and "**$570,000**" in TEXT and "**$236,750**" in TEXT
    assert usd(1, datetime(2027, 3, 12, 20, tzinfo=UTC)) == 0                                # early or on time costs nothing
    levels = key_levels(policy)
    s1, s2, s3 = _ev(levels, cents=one * 100), _ev(levels, cents=two * 100), _ev(levels, unknown=(2,), cents=0)
    assert MaxOptimizer.better(s2, s1) and MaxOptimizer.improved_level(s2, s1) == "controllable_known_cents"
    assert not MaxOptimizer.better(s3, s1) and not MaxOptimizer.better(s3, s2)               # an unknown aircraft loses before any dollar is read
    assert MaxOptimizer.better(_ev(levels, cents=one * 100, defended=1), s2)                 # a kept commitment outranks the dollars


def test_every_rule_names_code_and_tests_that_exist() -> None:
    rules = _table("| # | Rule | Code | Tests |")
    assert [r[0] for r in rules] == [str(i) for i in range(1, len(rules) + 1)] and len(rules) >= 10
    for number, _, code, tests in rules:
        source: str | None = None
        for token in TOKEN.findall(code):
            if "/" in token:
                path = ROOT / token
                assert path.is_file(), (number, token)
                source = path.read_text(encoding="utf-8") if path.suffix == ".py" else None
            else:
                assert source is not None and re.search(rf"^\s*def {re.escape(token)}\(", source, re.MULTILINE), (number, token)
        found, test_source = 0, ""
        for token in TOKEN.findall(tests):
            path_part, _, name = token.partition("::")
            if path_part:
                path = ROOT / path_part
                assert path.is_file(), (number, token)
                test_source = path.read_text(encoding="utf-8")
            assert name and re.search(rf"^def {re.escape(name)}\(", test_source, re.MULTILINE), (number, token)
            found += 1
        assert found, number


def test_the_reason_codes_and_decisions_it_names_exist() -> None:
    repository = (ROOT / "focus" / "economics" / "repository.py").read_text(encoding="utf-8")
    registry = (ROOT / "focus" / "economics" / "registry.py").read_text(encoding="utf-8")
    for code in ("NO_EFFECTIVE_TERM", "AIRCRAFT_NOT_MAPPED", "DUE_UNKNOWN"):
        assert code in TEXT and code in repository
    assert "ECONOMICS_MISSING" in TEXT and "ECONOMICS_MISSING" in registry
    receipt = (ROOT / "focus" / "engine" / "max" / "optimizer" / "orchestrator.py").read_text(encoding="utf-8")
    for field in ("unknown_lines", "complete", "known_cents", "conditional_cents", "controllable_known_cents"):
        assert f"`{field}`" in TEXT and f'"{field}"' in receipt                              # the receipt fields the page tells readers to check
    playbook = (ROOT / "docs" / "integration" / "DECISION_PLAYBOOK.md").read_text(encoding="utf-8")
    for decision in ("OD-02", "OD-05", "OD-12", "DEC-28"):
        assert decision in TEXT and decision in playbook
