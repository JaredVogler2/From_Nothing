"""The production-shaped benchmark fixture has the program's stated shape and schedules validly (scale work).

Shape per aircraft: about 4,500 baseline tasks over 5 positions, about 22,000 typed model-level dependencies crossing
teams, 30 % of 3,000 rework tasks open, a station window per position. A small instance of the same generator is
admitted and scheduled with zero violations and a complete inventory.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from focus.data.production_fixture import build_production_fixture
from focus.engine.max.optimizer.solver import benchmark_failures, benchmark_size


def test_production_fixture_has_the_stated_shape() -> None:
    data = build_production_fixture(2)
    tasks = data["master_task_list"].rows
    per_line = Counter(r["LINE_NUMBER"] for r in tasks)
    assert sorted(per_line.values()) == [4500 + 900, 4500 + 900]                    # baseline + open rework (30 % of 3,000)
    assert {r["CURRENT_CONTROL_STATION"] for r in tasks} == {"740", "741", "742", "743", "744"}
    edges = data["pnet_dag"].rows
    assert 0.85 * 22000 <= len(edges) <= 22000                                     # model-level, deduplicated
    kinds = Counter(r["RELATIONSHIP_TYPE"] for r in edges)
    assert kinds["FINISH <= START"] / len(edges) > 0.95 and kinds["START <= START"] and kinds["FINISH <= FINISH"]
    team_of = {r["SHOP_ORDER_IDENTIFIER"]: r["TEAM"] for r in tasks if r["LINE_NUMBER"] == 3001}
    crossing = sum(1 for r in edges if team_of[r["PREDECESSOR_SOI"]] != team_of[r["SUCCESSOR_SOI"]])
    assert crossing / len(edges) > 0.8                                             # interwoven across teams
    assert all(int(r["PREDECESSOR_SOI"][1:]) < int(r["SUCCESSOR_SOI"][1:]) for r in edges)   # acyclic by construction
    assert Counter(r["LINE_NUMBER"] for r in data["cs_windows"].rows) == {3001: 5, 3002: 5}
    assert len(data["neo_graph_rework"].rows) == 2 * 900


def test_production_shape_is_admitted_and_scheduled_validly(app_root: Path) -> None:
    run = benchmark_size(app_root, 2, budget_seconds=30, max_moves=0, solver=False, purposes=("EXECUTABLE", "FORECAST"), shape="production",
                         shape_options={"baseline": 400, "edges": 1800, "rework_total": 300})
    assert run["status"] == "COMPLETE" and benchmark_failures([run]) == []
    assert run["label"].startswith("PRODUCTION_SHAPED_SYNTHETIC_FIXTURE")
    for purpose in ("EXECUTABLE", "FORECAST"):
        plan = run["plans"][purpose]
        assert plan["validation"]["valid"] and plan["inventory_complete"] and plan["assignments"] > 0
    assert run["plans"]["FORECAST"]["assignments"] >= run["plans"]["EXECUTABLE"]["assignments"]
