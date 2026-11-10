"""The scoring explainer (docs/execution/SCORING_EXPLAINED.md) matches the code.

* its worked example is what the canonical formula returns under the grading policy;
* the weights, the out-of-sequence rate and the bands it states are the policy's;
* every file, function and test named in its rules table exists;
* only ``focus/performance/score.py`` turns the components into a score.
"""
from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

from focus.performance.score import formula

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "docs" / "execution" / "SCORING_EXPLAINED.md").read_text(encoding="utf-8")
TOKEN = re.compile(r"`([^`]+)`")


def _rules() -> list[tuple[str, str, str, str]]:
    start = TEXT.index("| # | Rule | Code | Tests |")
    rows = []
    for line in TEXT[start:].splitlines()[2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 4, line[:80]
        rows.append(tuple(cells))
    return rows


def test_the_worked_example_is_what_the_formula_returns(policy) -> None:
    def score(B: int, E: int, Cp: int, U: int, O: int = 0) -> tuple[str, str]:
        r = formula(Fraction(B), Fraction(E), Fraction(Cp), Fraction(U), Fraction(O), policy)
        return str(r["score"]), r["grade"]

    assert score(1000, 100, 720, 80) == ("83.0", "B")
    assert score(1000, 100, 720, 80, O=10) == ("81.9", "B")              # 10% of one 100-minute job
    assert score(1000, 100, 900, 100) == ("97.0", "A")                   # extra work lowers today's score
    assert all(shown in TEXT for shown in ("**83.0**", "**81.9**", "**97.0**"))


def test_the_stated_weights_rate_and_bands_are_the_policys(policy) -> None:
    attainment = int(Fraction(policy.str("score.attainment_weight")) * 100)
    adherence = int(Fraction(policy.str("score.adherence_weight")) * 100)
    rate = int(Fraction(policy.str("score.oos_rate")) * 100)
    assert f"{attainment}% × (done ÷ expected) + {adherence}% × (done ÷ (done + extra))" in TEXT
    assert f"{rate}% of the planned minutes" in TEXT
    bands = {str(label): int(low) for label, low in policy.seq("score.bands")}
    assert f"A is {bands['A']} and up, B {bands['B']}, C {bands['C']}, D {bands['D']}, F below {bands['D']}." in TEXT
    assert policy.str("score.policy_status") == "DEMONSTRATION" and "demonstration policy" in TEXT


def test_every_rule_names_code_and_tests_that_exist() -> None:
    rules = _rules()
    assert [r[0] for r in rules] == [str(i) for i in range(1, len(rules) + 1)] and len(rules) >= 16
    for number, _, code, tests in rules:
        source: str | None = None
        for token in TOKEN.findall(code):
            if "/" in token:
                path = ROOT / token
                assert path.is_file(), (number, token)
                source = path.read_text(encoding="utf-8") if path.suffix == ".py" else None
            else:
                assert source is not None and re.search(rf"^\s*def {re.escape(token)}\(", source, re.MULTILINE), (number, token)
        found = 0
        test_source = ""
        for token in TOKEN.findall(tests):
            path_part, _, name = token.partition("::")
            if path_part:
                path = ROOT / path_part
                assert path.is_file(), (number, token)
                test_source = path.read_text(encoding="utf-8")
            assert name and re.search(rf"^def {re.escape(name)}\(", test_source, re.MULTILINE), (number, token)
            found += 1
        assert found, number


def test_only_score_py_turns_components_into_a_score() -> None:
    """Everything else calls ``score.formula`` or ``score.status_and_score``; a second reader of the weights would be a
    second place that computes scores."""
    readers = set()
    for path in (ROOT / "focus").rglob("*.py"):
        if "score.attainment_weight" in path.read_text(encoding="utf-8"):
            readers.add(path.relative_to(ROOT).as_posix())
    # score.py computes; policy.py lists the key; sim/execute.py only prints the weights in a label (scoring_text)
    assert readers == {"focus/performance/score.py", "focus/policy.py", "focus/sim/execute.py"}
    assert len(re.findall(r"^def formula\(", "".join(p.read_text(encoding="utf-8") for p in (ROOT / "focus").rglob("*.py")), re.MULTILINE)) == 1
