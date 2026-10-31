"""A tiny fleet simulation end to end (synthetic data: two aircraft, one day of three shifts).

The shift loop must fill every table layer (pulled, calculated, method, results and outcomes), publish only plans the
independent validator passed, repeat exactly from the same arguments (decision digests and outcomes), and the
dashboard's read-only operations must answer from the database it wrote.
"""
from __future__ import annotations

import sqlite3

import pytest

from focus.data.fullscale_fixture import FleetSpec
from focus.sim.runner import SimSpec, run
from tests.integration.test_api_scope import client_on

SPEC = SimSpec(fleet=FleetSpec(n_aircraft=2, baseline=240, edges=1000, rework_total=90, teams=4, team_size=(4, 6)), days=1,
               max_moves=1, max_repairs=1)
LAYERS = ("src_master_task_list", "src_etracs_active", "src_attendance_shift", "calc_count", "calc_job", "calc_aircraft", "calc_pool",
          "method_stage", "method_check", "plan_assignment", "plan_aircraft", "plan_duty", "exec_assignment", "perf_team_shift", "sim_person")


@pytest.fixture(scope="module")
def sims(tmp_path_factory):
    root = tmp_path_factory.mktemp("sim")
    first, second = root / "first.db", root / "second.db"
    run(SPEC, first, log=lambda message: None)
    run(SPEC, second, log=lambda message: None)
    return first, second


def _all(path, sql):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_every_layer_is_filled_and_every_published_plan_is_valid(sims) -> None:
    first, _ = sims
    shifts = _all(first, "SELECT shift_no, shift_number, valid, stop_reason, decision_digest FROM sim_shift ORDER BY shift_no")
    assert [(s[0], s[1]) for s in shifts] == [(0, 1), (1, 2), (2, 3)]
    assert all(s[2] == 1 and s[3] == "move_bound" and s[4] for s in shifts)
    for table in LAYERS:
        assert _all(first, f'SELECT COUNT(*) FROM "{table}"')[0][0] > 0, table
    assert _all(first, "SELECT status FROM sim_run") == [("COMPLETE",)]
    assert not _all(first, "SELECT * FROM method_check WHERE violations > 0")
    # every executed assignment was published in its shift's plan, and its outcome is a known one
    orphans = _all(first, "SELECT e.assignment_id FROM exec_assignment e LEFT JOIN plan_assignment p ON p.shift_no = e.shift_no "
                          "AND p.assignment_id = e.assignment_id WHERE p.assignment_id IS NULL")
    assert not orphans
    assert {r[0] for r in _all(first, "SELECT DISTINCT outcome FROM exec_assignment")} <= {"completed", "slipped"}


def test_the_same_arguments_repeat_exactly(sims) -> None:
    first, second = sims
    for sql in ("SELECT shift_no, snapshot_hash, decision_digest FROM sim_shift ORDER BY shift_no",
                "SELECT shift_no, assignment_id, outcome, cause, actual_start, actual_end FROM exec_assignment ORDER BY shift_no, assignment_id",
                "SELECT shift_no, team, planned, completed, on_time, score FROM perf_team_shift ORDER BY shift_no, team"):
        assert _all(first, sql) == _all(second, sql), sql


def test_dashboard_operations_answer_from_the_tables(sims, tmp_path, app_root, monkeypatch) -> None:
    first, _ = sims
    monkeypatch.setenv("FOCUS_SIM_DB", str(first))
    client, _, _ = client_on(tmp_path / "runtime", app_root, "manager")
    line = _all(first, "SELECT MIN(line) FROM calc_aircraft")[0][0]
    team = _all(first, "SELECT team FROM plan_assignment GROUP BY team ORDER BY COUNT(*) DESC LIMIT 1")[0][0]
    for path in ("overview", "performance", "supply", "capacity", "methods", "profile", f"aircraft?line={line}", f"dispatch?team={team}",
                 f"gantt?team={team}", "overview?shift=0", "performance?shift=1"):
        response = client.get(f"/api/v1/sim/{path}")
        assert response.status_code == 200, (path, response.get_data(as_text=True)[:300])
        assert response.get_json()["status"] == "ready", path
    overview = client.get("/api/v1/sim/overview").get_json()["data"]
    assert len(overview["shifts"]) == 3 and len(overview["fleet"]) == 2
    performance = client.get("/api/v1/sim/performance").get_json()["data"]
    assert set(performance["compare"]) == {"shift", "day", "week", "overall"}
    assert client.get("/api/v1/sim/overview?bogus=1").status_code == 400          # unknown parameters are refused
    assert client.get("/simulation").status_code == 200


def test_without_a_database_the_operations_say_how_to_build_one(tmp_path, app_root, monkeypatch) -> None:
    monkeypatch.setenv("FOCUS_SIM_DB", str(tmp_path / "missing.db"))
    client, _, _ = client_on(tmp_path / "runtime", app_root, "manager")
    body = client.get("/api/v1/sim/overview").get_json()
    assert body["status"] == "empty"
    assert body["reasons"][0]["code"] == "SIMULATION_NOT_RUN" and "focus.sim.runner" in body["reasons"][0]["next_action"]
