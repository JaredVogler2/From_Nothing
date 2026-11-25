"""The rebuild agent's system prompt (docs/integration/OBJECTIVE_PARITY_PROMPT.md) says what the code does.

The prompt tells another coding agent how the objective is defined and gives it fixed cases to run against its own
build. Every table in it is read here and checked against the reference code, so a case the rebuild fails is a
difference in the rebuild, and a change to the objective fails here until the prompt says so too.
"""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus.cli import build_parser
from focus.contracts.planning import AircraftCompletion, AircraftEconomics
from focus.domain import Disposition, to_minute
from focus.economics.evaluator import JobRelease, aircraft_completions, line_costs, summarize
from focus.economics.registry import EconomicsRegistry
from focus.engine.max.optimizer.orchestrator import DEFENDED, OBJECTIVE_FIELDS, Evaluation, MaxOptimizer, key_levels
from focus.engine.max.optimizer.urgency import COD_DECAY, COD_STEP_MINUTES

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "docs" / "integration" / "OBJECTIVE_PARITY_PROMPT.md").read_text(encoding="utf-8")
TOKEN = re.compile(r"`([^`]+)`")
PATH = re.compile(r"^(config|docs|focus|tests|tools|fixtures)/[\w./-]+$")


def _table(header: str) -> list[list[str]]:
    start = TEXT.index(header)
    rows = []
    for line in TEXT[start:].splitlines()[2:]:
        if not line.startswith("|"):
            break
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    assert rows, header
    return rows


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# ---------------------------------------------------------------------- the order and the settings

def test_the_order_is_the_comparison_key(policy) -> None:
    rows = _table("| Position | Key field | Plain meaning | Better is | Policy level |")
    assert [r[0] for r in rows] == [str(i) for i in range(1, 10)]
    assert tuple(TOKEN.findall(r[1])[0] for r in rows) == key_levels(policy)
    level_of = {field: level for level, fields in OBJECTIVE_FIELDS.items() for field in fields}
    for _, field, _, better, level in rows[:8]:
        name = TOKEN.findall(field)[0]
        assert TOKEN.findall(level) == [level_of[name]], name
        assert (better == "more") == (level_of[name] == "COMMITMENT_DEFENSE"), name          # the two counts are stored negated
        assert better in ("more", "fewer", "lower"), name
    assert rows[8][3] == "the smaller text" and rows[8][4] == "(tie-break)"
    assert OBJECTIVE_FIELDS["FEASIBILITY"] == () and list(policy.seq("objective.order"))[0] == "FEASIBILITY"   # level 0 adds no field
    assert DEFENDED == {"KEPT", "CREW_CHANGED"} and "`KEPT`" in TEXT and "`CREW_CHANGED`" in TEXT


def test_every_setting_it_lists_has_that_value() -> None:
    values = json.loads((ROOT / "config" / "policies" / "rebuild-v1.json").read_text(encoding="utf-8"))["values"]
    rows = _table("| Setting | Value | What it fixes |")
    assert len(rows) >= 15
    for setting, value, _ in rows:
        key = TOKEN.findall(setting)[0]
        assert values[key]["value"] == json.loads(TOKEN.findall(value)[0]), key
    assert values["objective.order"]["authority"] == "manual_specified" and "`manual_specified`" in TEXT
    zone = json.loads((ROOT / "config" / "policies" / "shift_fixture.json").read_text(encoding="utf-8"))["values"]["shift.timezone"]
    assert zone["value"] and "`shift.timezone`" in TEXT
    assert values["optimizer.budget_seconds"]["value"] and values["optimizer.max_neighborhood_moves"]["value"]


# ---------------------------------------------------------------------- table A: the cost of one aircraft

def _amount(text: str) -> Fraction | None:
    return None if text == "none" else Fraction(text) * 100


def test_table_a_is_what_the_registry_and_the_evaluator_return(policy) -> None:
    rows = _table("| Case | Daily rate, USD | Due | Data cut | Finish | Cap, USD | Booked, USD | Cost, cents | Changeable, cents |")
    assert [r[0] for r in rows] == [f"A{i}" for i in range(1, len(rows) + 1)] and len(rows) >= 13
    for case, rate, due, cut, finish, cap, booked, cost, changeable in rows:
        known_term = rate != "none" and due != "none"
        econ = AircraftEconomics(1, "KNOWN" if known_term else "UNKNOWN", "ASSUMED", "T1", "CUSTOMER_MODEL", "USD", _amount(rate), _amount(cap),
                                 None, _amount(booked), None if due == "none" else _instant(due), "case", "LINEAR_DAILY", ())
        registry = EconomicsRegistry({1: econ}, policy)
        done = None if finish == "unknown" else _instant(finish)
        completion = AircraftCompletion(1, "unknown" if done is None else "known", done, 1, 0 if done is None else 1,
                                        ("J1",) if done is None else (), ())
        line = line_costs(registry, {1: completion}, to_minute(_instant(cut)))[1]
        total = summarize({1: line})
        if cost == "unknown":
            assert changeable == "unknown" and line.total_cents is None and line.controllable_cents is None, case
            assert total.unknown_lines == (1,) and total.controllable_known_cents == 0 and not total.complete, case   # listed, never zero
            continue
        assert (line.total_cents, line.controllable_cents) == (Fraction(cost), Fraction(changeable)), case
        assert total.unknown_lines == () and total.controllable_known_cents == Fraction(changeable), case
        # the formula of the page, worked out here without the registry
        late = max(0, to_minute(done) - to_minute(_instant(due)))                                # type: ignore[arg-type]
        modeled = _amount(rate) * late / 1440                                                    # type: ignore[operator]
        modeled = min(modeled, _amount(cap)) if cap != "none" else modeled                       # type: ignore[type-var]
        modeled = max(modeled, _amount(booked)) if booked != "none" else modeled                 # type: ignore[type-var]
        assert modeled == Fraction(cost) == registry.penalty(1, to_minute(done)), case            # type: ignore[arg-type]
    by_case = {r[0]: r for r in rows}
    assert Fraction(by_case["A5"][7]).denominator != 1                                           # a cost that is not a whole cent
    assert by_case["A10"][1] == "0" and by_case["A10"][7] == "0" and by_case["A11"][7] == "unknown"   # known zero is not unknown


# ---------------------------------------------------------------------- table B: which of two schedules is better

def _schedule(text: str, levels: tuple[str, ...]) -> Evaluation:
    fields: dict[str, str] = {}
    spec = TOKEN.findall(text)[0]
    if spec != "all zero":
        fields = dict(item.split("=", 1) for item in spec.split())
    assert set(fields) <= {"kept", "exact", "unknown", "cents", "nva", "ot", "breaks", "unplaced", "sig", "promises"}, spec
    promises = [SimpleNamespace(incumbent_assignment_id=name, outcome=outcome)
                for name, outcome in (item.split(":") for item in fields.get("promises", "").split("+") if item)]
    kept = int(fields["kept"]) if "kept" in fields else sum(1 for p in promises if p.outcome in DEFENDED)
    exact = int(fields["exact"]) if "exact" in fields else sum(1 for p in promises if p.outcome == "KEPT")
    unknown = frozenset(int(n) for n in fields.get("unknown", "").split("+") if n)
    nva, signature = int(fields.get("nva", 0)), fields.get("sig", "S")
    key = {"commitments_defended": -kept, "commitments_exact": -exact, "unknown_completion_aircraft": len(unknown),
           "controllable_known_cents": Fraction(fields.get("cents", "0")), "travel_plus_wait_minutes": nva, "ot_minutes": int(fields.get("ot", 0)),
           "continuity_breaks": int(fields.get("breaks", 0)), "deferred_phases": int(fields.get("unplaced", 0)), "signature": signature}
    ev = Evaluation(SimpleNamespace(commitment_results=tuple(promises)), {}, {}, {}, None, kept, exact, nva, 0, key["ot_minutes"],
                    key["continuity_breaks"], signature)
    ev.key, ev.levels, ev.unknown = tuple(key[f] for f in levels), levels, unknown
    return ev


def test_table_b_is_what_the_comparison_returns(policy) -> None:
    levels = key_levels(policy)
    rows = _table("| Case | Schedule X | Schedule Y | X better than Y? | Decided at |")
    assert [r[0] for r in rows] == [f"B{i}" for i in range(1, len(rows) + 1)] and len(rows) >= 20
    decided: set[str] = set()
    for case, x_text, y_text, verdict, at in rows:
        x, y = _schedule(x_text, levels), _schedule(y_text, levels)
        assert verdict in ("yes", "no"), case
        assert MaxOptimizer.better(x, y) is (verdict == "yes"), case
        named = TOKEN.findall(at)
        if verdict == "yes":
            assert named == [MaxOptimizer.improved_level(x, y)], case                 # the position the page names is the deciding one
            assert not MaxOptimizer.better(y, x), case
            decided.add(named[0])
        else:
            assert not named, case                                                     # a refusal is explained in words
    assert decided == set(levels)                                                      # every position decides at least one case
    by_case = {r[0]: r for r in rows}
    for case in ("B7", "B17", "B18"):          # the key alone would have said yes: the rule of the page is what refuses
        x, y = _schedule(by_case[case][1], levels), _schedule(by_case[case][2], levels)
        assert MaxOptimizer.improved_level(x, y) is not None and not MaxOptimizer.better(x, y), case
    for case in ("B2", "B15"):                 # and here the key itself is not better
        x, y = _schedule(by_case[case][1], levels), _schedule(by_case[case][2], levels)
        assert MaxOptimizer.improved_level(x, y) is None, case
    assert policy.int("economics.monetary_tolerance_cents") == 0 and Fraction("8999999/9") < 1000000   # B14: a ninth of a cent decides


# ---------------------------------------------------------------------- table C: the cost of delay

def test_table_c_is_the_decay_table() -> None:
    rows = _table("| Slack, minutes | Share of the cost of one more day, in millionths |")
    assert len(rows) >= 8
    for slack, share in rows:
        step = int(slack) // COD_STEP_MINUTES
        assert int(share) == (COD_DECAY[step] if step < len(COD_DECAY) else 0), slack
    assert COD_STEP_MINUTES == 360 and "`COD_STEP_MINUTES` (360)" in TEXT
    assert {int(r[0]) for r in rows} >= {0, COD_STEP_MINUTES - 1, COD_STEP_MINUTES, 1440, len(COD_DECAY) * COD_STEP_MINUTES - 1,
                                         len(COD_DECAY) * COD_STEP_MINUTES}                 # both sides of the first and the last step
    assert 8_000_000 * COD_DECAY[1440 // COD_STEP_MINUTES] == 2_943_032_000_000 and "2,943,032,000,000" in TEXT


# ---------------------------------------------------------------------- table D: an aircraft's finish

def test_table_d_is_what_the_completion_rule_returns() -> None:
    rows = _table("| Case | The aircraft's jobs | Finish |")
    assert [r[0] for r in rows] == [f"D{i}" for i in range(1, len(rows) + 1)] and len(rows) >= 6
    for case, jobs_text, finish in rows:
        jobs, dispositions, releases = [], {}, {}
        for item in TOKEN.findall(jobs_text)[0].split("; "):
            name, _, state = item.partition(" ")
            jobs.append(SimpleNamespace(id=name, line=1))
            if state == "verified complete":
                dispositions[name] = SimpleNamespace(disposition=Disposition.VERIFIED_COMPLETE)
            elif state == "not released":
                releases[name] = JobRelease(name, None, False, "PART_NOT_RECEIVED")
            else:
                kind, minute = state.split(" ")
                assert kind in ("released", "conditional"), case
                releases[name] = JobRelease(name, int(minute), kind == "conditional", "released")
        got = aircraft_completions(jobs, dispositions, releases, [1])[1]
        if finish == "unknown":
            assert got.status == "unknown" and got.completion_at is None and got.unresolved_jobs, case
        elif finish == "known, no open work":
            assert (got.status, got.completion_at, got.required_jobs) == ("known", None, 0), case
        else:
            status, _, minute = finish.partition(" at ")
            assert (got.status, to_minute(got.completion_at)) == (status, int(minute)), case   # type: ignore[arg-type]
    d3 = next(r for r in rows if r[0] == "D3")
    assert "released 400" in d3[1] and d3[2] == "unknown"            # never the latest of the jobs that were placed


# ---------------------------------------------------------------------- what it points at exists

def test_every_file_function_and_test_it_names_exists() -> None:
    for token in set(TOKEN.findall(TEXT)):
        if PATH.match(token):
            assert (ROOT / token).exists(), token
    assert "`AGENTS.md` at the repository root" in TEXT and (ROOT.parent / "AGENTS.md").is_file()
    values = json.loads((ROOT / "config" / "policies" / "rebuild-v1.json").read_text(encoding="utf-8"))["values"]
    rows = _table("| Part | File | Function or name |")
    assert len(rows) >= 15
    for part, file, names in rows:
        path = ROOT / TOKEN.findall(file)[0]
        source = path.read_text(encoding="utf-8")
        for name in TOKEN.findall(names):
            if path.suffix == ".json":
                assert name in values, (part, name)
            else:
                assert re.search(rf"^\s*(def|class) {re.escape(name)}\b|^{re.escape(name)}\b.*=", source, re.MULTILINE), (part, name)
    tests = _table("| What it proves | Tests |")
    assert len(tests) >= 12 and all((ROOT / TOKEN.findall(r[1])[0]).is_file() for r in tests)
    assert "tests/unit/test_objective_parity_prompt.py" in {TOKEN.findall(r[1])[0] for r in tests}
    assert re.search(r"^def test_a70_deterministic_and_bounded\(", (ROOT / "tests" / "integration" / "test_max_pipeline.py").read_text(encoding="utf-8"),
                     re.MULTILINE) and "`test_a70_deterministic_and_bounded`" in TEXT
    # the functions the text itself leans on
    orchestrator = (ROOT / "focus" / "engine" / "max" / "optimizer" / "orchestrator.py").read_text(encoding="utf-8")
    for name in ("_run_repair", "_admit", "planning_mode", "better", "evaluate", "key_levels"):
        assert f"def {name}(" in orchestrator, name
        assert f"`{name}`" in TEXT or f"`MaxOptimizer.{name}`" in TEXT or f"`MaxOptimizer.{name}(candidate, best)`" in TEXT, name
    assert '"E_ECONOMICS"' in (ROOT / "focus" / "validation" / "plan.py").read_text(encoding="utf-8") and "`E_ECONOMICS`" in TEXT
    # the receipt fields it tells the agent to compare are in the receipt
    fields = [TOKEN.findall(r[0])[0] for r in _table("| Field | What equal means |")]
    assert fields == ["candidate_id", "decision_digest", "costs", "objective_status", "algorithm.stages", "algorithm.weights",
                      "algorithm.baseline_comparison.stop_reason"]
    assert "`candidate_hash` is not in the list" in TEXT and '"candidate_hash":' in orchestrator      # it covers timings: never compared
    for field in fields:
        assert f'"{field.split(".")[-1]}":' in orchestrator, field
    for reason in ("move_bound", "no_moves", "repair", "deadline"):
        assert f"`{reason}`" in TEXT and f'"{reason}"' in orchestrator, reason
    # the decisions it cites are on record
    decisions = (ROOT / "docs" / "execution" / "DECISIONS.md").read_text(encoding="utf-8")
    playbook = (ROOT / "docs" / "integration" / "DECISION_PLAYBOOK.md").read_text(encoding="utf-8")
    for number in ("D-33", "D-31", "OD-06"):
        assert number in TEXT and number in decisions, number
    for number in ("DEC-34", "DEC-35"):
        assert number in TEXT and number in playbook, number
    assert "daily_airline_economic_proxy_usd" in (ROOT / "fixtures" / "economics" / "airline_economic_proxy.csv").read_text(encoding="utf-8").splitlines()[0]


def test_every_command_it_gives_is_one_the_reference_accepts() -> None:
    blocks = re.findall(r"```bash\n(.*?)```", TEXT, re.DOTALL)
    commands = [line.strip() for block in blocks for line in block.splitlines() if line.strip()]
    cli = [c for c in commands if c.startswith("python -m focus.cli ")]
    assert len(cli) >= 6
    parser = build_parser()
    seen = set()
    for command in cli:
        parser.parse_args(shlex.split(command)[3:])                              # an unknown command or flag exits here
        seen.add(shlex.split(command)[3])
    assert seen == {"demo-bootstrap", "acquire", "schedule", "validate", "pipeline"}
    suites = [c for c in commands if c.startswith("python -m pytest ")]
    assert len(suites) == 2
    for command in suites:
        paths = [t for t in shlex.split(command) if t.startswith("tests/")]
        assert paths and all((ROOT / t).is_file() for t in paths), command
    # one acquisition, then one copy of the store for each build
    order = [c for c in commands if c.startswith(("export ", "cp ")) or " acquire " in c]
    assert order == ["export FOCUS_MODE=fixture", "export FOCUS_RUNTIME_DIR=/path/to/scratch/reference",
                     "python -m focus.cli acquire --mode fixture --scenario manufacturing_demo --idempotency-key parity-1",
                     "cp -r /path/to/scratch/reference /path/to/scratch/rebuild", "export FOCUS_RUNTIME_DIR=/path/to/scratch/rebuild"]
    assert (ROOT / "tests" / "integration" / "test_objective_parity_procedure.py").is_file()
    timing = (ROOT / "tools" / "time_pre_shift.py").read_text(encoding="utf-8")
    assert "`python tools/time_pre_shift.py run WORK_DIRECTORY --aircraft 10`" in TEXT and '"--aircraft"' in timing
    assert "`python tools/build_economics_rows.py`" in TEXT and (ROOT / "tools" / "build_economics_rows.py").is_file()


def test_the_brief_the_handoff_guide_and_the_explainer_point_at_it() -> None:
    """A prompt nobody finds checks nothing: the three pages a rebuild agent reads first each name it."""
    name = "OBJECTIVE_PARITY_PROMPT.md"
    brief = (ROOT.parent / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`FROM-NOTHING-APP/docs/integration/{name}`" in brief.split("## What a port to real data must keep", 1)[1]
    assert f"[{name}]({name})" in (ROOT / "docs" / "integration" / "HANDOFF.md").read_text(encoding="utf-8")
    assert f"`docs/integration/{name}`" in (ROOT / "docs" / "execution" / "OBJECTIVE_EXPLAINED.md").read_text(encoding="utf-8")


def test_it_is_a_prompt_with_an_assignment_a_method_and_a_report() -> None:
    headings = re.findall(r"^## (\d+)\. (.+)$", TEXT, re.MULTILINE)
    assert [int(n) for n, _ in headings] == list(range(1, 13))
    titles = [t for _, t in headings]
    assert titles[0] == "Your assignment" and titles[4] == 'What "matching" means' and titles[10] == "What to hand back"
    assert len(re.findall(r"^### Step \d+\. ", TEXT, re.MULTILINE)) == 8
    claims = TEXT[TEXT.index('## 5. What "matching" means'):TEXT.index("## 6. How to prove it")]
    assert len(re.findall(r"^\d\. \*\*", claims, re.MULTILINE)) == 5
