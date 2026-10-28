"""P12/P13: registry weights steer a shared pure crew and the exact fleet cost improves over the neutral seed (A44)."""
from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

from focus.codec import from_jsonable
from focus.data.fixture_reader import FixtureSourceReader
from focus.domain import Purpose
from focus.engine.max.optimizer.orchestrator import cli_schedule, schedule
from tests.conftest import acquire_demo

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
KEEP = {"DEMO-A1-05", "DEMO-A1-06", "DEMO-A1-07"}                # one shared crew at a time (N=3, cap 2) on S1


def contention(data: dict) -> None:
    for name, key in (("person_team_map", "BEMS_ID"), ("shift_roster", "PERSON_KEY"), ("attendance_shift", "BEMS_ID")):
        data[name]["rows"] = [r for r in data[name]["rows"] if not (str(r[key]).startswith("DEMO-A1-") and r[key] not in KEEP)]
    for r in data["cs_windows"]["rows"]:
        if r["LINE_NUMBER"] in (1301, 1302):
            r["CS_OP_UNLOAD"] = "2026-10-23"                    # proxy due 10-25 (+2 grace): both aircraft already late


def _services(services_factory, key: str):
    names = ("person_team_map", "shift_roster", "attendance_shift", "cs_windows")
    data = {n: json.loads((INPUTS / f"{n}.json").read_text()) for n in names}
    contention(data)
    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection
    return services, outcome


def test_a44_economic_redispatch_improves_exact_fleet_cost(services_factory) -> None:
    services, outcome = _services(services_factory, "a44")
    plan, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=20, max_moves=0)
    weights = run.weights
    assert weights[1301].normalized == 1 and weights[1302].normalized == Fraction(1, 10)      # $100/day vs $10/day, exact
    base, best = run.baseline, run.best
    assert best.summary.controllable_known_cents < base.summary.controllable_known_cents        # steering improves exact dollars
    assert base.summary.unknown_lines == best.summary.unknown_lines                             # same unknown scope on both sides
    stage = next(s for s in run.stages if s.stage == "economic_redispatch")
    assert stage.accepted == 1
    first = lambda ev: min((p for p in ev.decode.placements.values()                            # noqa: E731
                            if p.crew and p.kind.value == "WORK" and p.line in (1301, 1302)), key=lambda p: (p.start, p.line)).line
    assert first(base) == 1302 and first(best) == 1301                                          # the shared crew serves $100/day first
    comp = lambda ev, line: ev.completions[line].completion_at                                  # noqa: E731
    assert comp(best, 1301) < comp(base, 1301)
    assert best.costs[1301].total_cents < base.costs[1301].total_cents
    assert best.ot_minutes <= base.ot_minutes and best.travel_minutes <= base.travel_minutes    # no hidden NVA cost bought the gain
    assert plan.global_optimality_proven is False and plan.algorithm_status.proof_status == "HEURISTIC_NO_PROOF"


def test_a44_cli_schedule_reports_weights_complete_dispositions_and_costs(services_factory) -> None:
    services, outcome = _services(services_factory, "a44-cli")
    args = argparse.Namespace(source_run=outcome.run_id, policy=services.policy.id, budget_seconds=10.0, purpose="EXECUTABLE")
    result = cli_schedule(services, args)
    assert result["status"] == "ok" and result["published"] is False
    assert result["jobs_total"] == 19 and sum(result["disposition_census"].values()) == 19                # complete inventory
    assert result["algorithm"]["weights"]["1301"] == "1" and result["algorithm"]["weights"]["1302"] == "1/10"
    comparison = result["algorithm"]["baseline_comparison"]
    final = from_jsonable(Fraction, comparison["final_known_cents"])
    baseline = from_jsonable(Fraction, comparison["baseline_known_cents"])
    assert final < baseline and comparison["complete_fleet_cost"] is False                                # unknown lines kept visible
    assert result["costs"]["unknown_lines"] and result["global_optimality_proven"] is False
    assert {s["stage"] for s in result["algorithm"]["stages"]} == {"neutral_seed", "economic_redispatch", "delay_repair", "neighborhoods"}
    bad = cli_schedule(services, argparse.Namespace(source_run=outcome.run_id, policy="other-policy", budget_seconds=1.0, purpose="EXECUTABLE"))
    assert bad["status"] == "invalid" and bad["error"]["code"] == "POLICY_MISMATCH"
