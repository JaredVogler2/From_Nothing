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
          "method_stage", "method_check", "plan_assignment", "plan_aircraft", "plan_duty", "exec_assignment", "perf_team_shift", "perf_unit",
          "sim_person")


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
    # every incumbent task is found again: none is lost to written-back progress renumbering a job's remaining work, or to
    # an S3 start after local midnight being read on the shift's production day
    assert _all(first, "SELECT COUNT(*) FROM method_commitment WHERE outcome = 'KEPT'")[0][0] > 0
    assert not _all(first, "SELECT * FROM method_commitment WHERE outcome = 'UNSAFE' AND code IN ('NO_START_RULE', 'INCUMBENT_PHASE_MISSING')")


def test_the_same_arguments_repeat_exactly(sims) -> None:
    first, second = sims
    for sql in ("SELECT shift_no, snapshot_hash, decision_digest FROM sim_shift ORDER BY shift_no",
                "SELECT shift_no, assignment_id, outcome, cause, actual_start, actual_end FROM exec_assignment ORDER BY shift_no, assignment_id",
                "SELECT shift_no, team, units, earned, B0, E, Cp, U, oos_effort, score, grade FROM perf_team_shift ORDER BY shift_no, team",
                "SELECT shift_no, team, cause, origin_team, status, units, effort, decision FROM perf_claim ORDER BY shift_no, claim_id"):
        assert _all(first, sql) == _all(second, sql), sql


def test_dashboard_operations_answer_from_the_tables(sims, tmp_path, app_root, monkeypatch) -> None:
    first, _ = sims
    monkeypatch.setenv("FOCUS_SIM_DB", str(first))
    client, _, _ = client_on(tmp_path / "runtime", app_root, "manager")
    line = _all(first, "SELECT MIN(line) FROM calc_aircraft")[0][0]
    team = _all(first, "SELECT team FROM plan_assignment GROUP BY team ORDER BY COUNT(*) DESC LIMIT 1")[0][0]
    for path in ("overview", "performance", "supply", "capacity", "methods", "profile", f"aircraft?line={line}", f"dispatch?team={team}",
                 f"gantt?team={team}", "overview?shift=0", "performance?shift=1", "excusals", f"excusals?team={team}&shift=1", "recognition",
                 "recognition?shift=1"):
        response = client.get(f"/api/v1/sim/{path}")
        assert response.status_code == 200, (path, response.get_data(as_text=True)[:300])
        assert response.get_json()["status"] == "ready", path
    overview = client.get("/api/v1/sim/overview").get_json()["data"]
    assert len(overview["shifts"]) == 3 and len(overview["fleet"]) == 2
    performance = client.get("/api/v1/sim/performance").get_json()["data"]
    assert set(performance["compare"]) == {"shift", "day", "week", "overall"}
    # an aircraft's score pools its goal units across teams; a team's history is its own rows; no deviation is a cause
    aircraft = client.get(f"/api/v1/sim/aircraft?line={line}").get_json()["data"]
    B, Cp = _all(first, f"SELECT SUM(effort), SUM(CASE WHEN status = 'EARNED' THEN effort ELSE 0 END) FROM perf_unit WHERE line = {line}")[0]
    assert sum(x["B"] for x in aircraft["scores"]) == B and sum(x["Cp"] for x in aircraft["scores"]) == Cp
    assert aircraft["score_overall"]["B"] == B
    history = client.get(f"/api/v1/sim/performance?team={team}").get_json()["data"]
    assert history["history_team"] == team
    assert [h["shift_no"] for h in history["history"]] == [r[0] for r in _all(first, f"SELECT shift_no FROM perf_team_shift WHERE team = '{team}' ORDER BY shift_no")]
    assert not {c["cause"] for c in history["missed_by_cause"]} & {"DEVIATION", "REPLANNED"}
    assert client.get("/api/v1/sim/overview?bogus=1").status_code == 400          # unknown parameters are refused
    assert client.get("/simulation").status_code == 200


def test_without_a_database_the_operations_say_how_to_build_one(tmp_path, app_root, monkeypatch) -> None:
    monkeypatch.setenv("FOCUS_SIM_DB", str(tmp_path / "missing.db"))
    client, _, _ = client_on(tmp_path / "runtime", app_root, "manager")
    body = client.get("/api/v1/sim/overview").get_json()
    assert body["status"] == "empty"
    assert body["reasons"][0]["code"] == "SIMULATION_NOT_RUN" and "focus.sim.runner" in body["reasons"][0]["next_action"]


def test_the_redispatch_keeps_the_seeds_commitment_outcomes(tmp_path, monkeypatch) -> None:
    """With the previous shift's plan defended, cost of delay must not reorder committed work: the redispatch keeps
    every commitment outcome of the seed, so it is judged on the uncommitted work it reorders (otherwise one changed
    crew among thousands of commitments would reject the whole redispatch)."""
    from dataclasses import replace

    from focus.engine.max.optimizer import orchestrator

    seen: list[tuple[dict, dict, bool]] = []
    original = orchestrator.MaxOptimizer._admit

    def admit(self, cand, best, best_valid, path):  # type: ignore[no-untyped-def]
        verdict = original(self, cand, best, best_valid, path)
        if path == "economic_redispatch":
            outcomes = lambda ev: {r.incumbent_assignment_id: r.outcome for r in ev.decode.commitment_results}  # noqa: E731
            seen.append((outcomes(best), outcomes(cand), verdict))
        return verdict

    monkeypatch.setattr(orchestrator.MaxOptimizer, "_admit", admit)
    run(replace(SPEC, shifts=2), tmp_path / "commit.db", log=lambda message: None)
    assert len(seen) == 4                                                     # two shifts, executable and forecast plans
    defended = [(seed, econ) for seed, econ, _ in seen if seed]               # the shift with an incumbent
    assert defended and all(seed == econ for seed, econ in defended)


def test_team_scores_are_the_canonical_formula_over_their_goal_units(sims, app_root) -> None:
    """Every team-shift score is focus.performance.score.formula over its own unit facts: B0 the planned effort, E the
    effort of missed units relieved by approved claims, Cp the earned planned effort, U the offplan output and O the
    once-only charge on discretionary out-of-sequence work."""
    from fractions import Fraction

    from focus.performance.score import formula
    from focus.policy import load_policy

    first, _ = sims
    policy = load_policy(app_root / "config" / "policies", "rebuild-v1")
    rate = Fraction(policy.str("score.oos_rate"))
    teams = _all(first, "SELECT shift_no, team, B0, additions, B, E, Cp, U, oos_effort, score, grade, status, base, O, final, policy_id, "
                        "policy_hash, status_reason FROM perf_team_shift")
    assert teams
    for shift_no, team, B0, additions, B, E, Cp, U, oos_effort, score, grade, status, base, O, final, policy_id, policy_hash, why in teams:
        units = _all(first, f"SELECT effort, status, relief, oos_charged, origin FROM perf_unit WHERE shift_no = {shift_no} AND team = '{team}'")
        assert B0 == sum(u[0] for u in units if u[4] == "ORIGINAL") and additions == sum(u[0] for u in units if u[4] == "AMENDMENT")
        assert B == B0 + additions and Cp == sum(u[0] for u in units if u[1] == "EARNED")
        assert additions == _all(first, f"SELECT COALESCE(SUM(effort), 0) FROM perf_amendment WHERE shift_no = {shift_no} AND team = '{team}'")[0][0]
        assert E == sum(u[0] for u in units if u[1] == "MISSED" and u[2]) and not any(u[2] for u in units if u[1] == "EARNED")
        assert oos_effort == sum(u[0] for u in units if u[3])
        assert U == (_all(first, f"SELECT COALESCE(SUM(effort), 0) FROM exec_offplan WHERE shift_no = {shift_no} AND team = '{team}'")[0][0])
        assert status in ("DEMONSTRATION", "OFFICIAL", "NEUTRAL")
        result = formula(Fraction(B), Fraction(E), Fraction(Cp), Fraction(U), rate * oos_effort, policy)
        assert (None if result["score"] is None else float(result["score"])) == score and result["grade"] == grade
        assert (status == "NEUTRAL") == (score is None)
        # status_and_score's parts are stored: base before the OOS charge, the charge O, the clamped final, the policy and why neutral
        assert O == float(rate * oos_effort) and (policy_id, policy_hash) == (policy.id, policy.hash)
        assert base == (None if result["base"] is None else float(result["base"])) and final == (None if result["final"] is None else float(result["final"]))
        assert (why is not None) == (status == "NEUTRAL") and (why is None or why.startswith(("NO_PLAN", "ALL_EXCUSED")))


def test_only_verified_excusable_causes_relieve_a_team(sims, app_root) -> None:
    """The excuse engine: a missed task is relieved only by an approved claim of a nominally excusable cause; same-team
    predecessor and overrun claims stay proposed (they need a person's notes), deviations have no claim, and a cross-team
    claim always names another team as the origin of the unfinished work."""
    from focus.policy import load_policy

    first, _ = sims
    excusable = set(load_policy(app_root / "config" / "policies", "rebuild-v1").seq("disruption.nominally_excusable"))
    for cause, claim, origin, status, relief, team in _all(first, "SELECT cause, claim_cause, origin_team, claim_status, relief, team FROM perf_unit "
                                                                  "WHERE status = 'MISSED'"):
        if cause in ("deviation", "replanned"):                          # chosen other work, or no disruption on record
            assert claim is None and not relief
            continue
        assert claim is not None
        if relief:
            assert claim in excusable and status == "SUBMITTED"
        if claim in ("SAME_TEAM_PREDECESSOR", "DURATION_OVERRUN"):
            assert status == "PROPOSED" and not relief
        if claim == "CROSS_TEAM_PREDECESSOR":
            assert origin and origin != team
    for cause, status, decision, decided_by, reported_by, reason, origin, team in _all(
            first, "SELECT cause, status, decision, decided_by, reported_by, reason, origin_team, team FROM perf_claim"):
        if status == "PROPOSED":
            assert decision is None and decided_by is None                  # needs a person's notes before any review
        else:
            assert decision in ("APPROVED", "REJECTED") and decided_by != reported_by and len(reason) >= 10
        if decision == "APPROVED":
            assert cause in excusable and status == "SUBMITTED"
        if cause == "REWORK_INJECTION":                                   # origin only from the defect classification
            assert (status == "SUBMITTED") == (origin is not None)
            if status == "SUBMITTED":
                assert (decision == "APPROVED") == (origin != team)          # the team's own defect is no excuse
    # a capacity claim always cites a call-off that attendance records
    for evidence, shift_no in _all(first, "SELECT evidence, shift_no FROM perf_claim WHERE cause = 'CAPACITY_SHORTAGE' AND decision = 'APPROVED'"):
        person, day, number = evidence.split(":")[1:4]
        assert _all(first, f"SELECT COUNT(*) FROM src_attendance_shift WHERE BEMS_ID = '{person}' AND PRODUCTION_DAY = '{day}' "
                           f"AND SHIFT_NUMBER = {number[1:]} AND SOURCE = 'SIM_LATE_CALLOFF'")[0][0] >= 1
    # no unit is earned twice: within a production day each phase is EARNED in one shift's goal at most
    assert not _all(first, "SELECT u.phase FROM perf_unit u JOIN sim_shift s ON s.shift_no = u.shift_no WHERE u.status = 'EARNED' "
                           "GROUP BY s.production_day, u.phase HAVING COUNT(DISTINCT u.shift_no) > 1")


def test_recognition_is_the_frozen_value_of_earned_goal_units(sims, app_root) -> None:
    """A13: XP is effort / base divisor for each earned goal unit, plus the gate bonus frozen for a behind-gate original unit
    (valued on its goal's date against its station gate in the snapshot that froze the goals). Offplan output, NEUTRAL goals
    and emergency additions' gate bonus earn nothing; badges follow their conditions; levels follow the level size."""
    from datetime import date
    from fractions import Fraction

    from focus.analytics.points import gate_context
    from focus.policy import load_policy
    from focus.sim.recognition import derive

    first, _ = sims
    policy = load_policy(app_root / "config" / "policies", "rebuild-v1")
    con = sqlite3.connect(first)
    try:
        data = derive(con, policy)
    finally:
        con.close()
    divisor = policy.int("recognition.base_divisor")
    frozen = {(d, t, n): f for f, d, t, n in _all(first, "SELECT frozen_shift_no, production_day, team, shift_number FROM perf_goal_day")}
    expected: dict[tuple[int, str], tuple[Fraction, Fraction, int]] = {}
    for shift_no, team, day, number, status in _all(first, "SELECT shift_no, team, production_day, shift_number, status FROM perf_team_shift"):
        base = bonus = Fraction(0)
        behind = 0
        if status != "NEUTRAL":
            f = frozen[(day, team, number)]
            gates = dict(_all(first, f"SELECT job_id, cs_end FROM calc_job WHERE shift_no = {f} AND cs_end IS NOT NULL"))
            for job, effort, origin in _all(first, f"SELECT job_id, effort, origin FROM perf_unit WHERE shift_no = {shift_no} AND team = '{team}' "
                                                   "AND status = 'EARNED'"):
                base += Fraction(effort, divisor)
                if origin == "ORIGINAL":
                    gate = gates.get(job)
                    ctx = gate_context(policy, date.fromisoformat(gate) if gate else None, date.fromisoformat(day), effort)
                    if ctx.bonus_value:
                        bonus += ctx.bonus_value
                        behind += 1
        expected[(shift_no, team)] = (base, bonus, behind)
    got = {(s["shift_no"], s["team"]): s for s in data["shifts"]}
    assert set(got) == set(expected) and data["streams"]["completion_credited"] > 0
    for key, (base, bonus, behind) in expected.items():
        assert (got[key]["base"], got[key]["gate_bonus"], got[key]["behind_units"]) == (round(float(base), 2), round(float(bonus), 2), behind), key
    size = policy.int("xp.level_size")
    for t in data["teams"]:
        xp = sum((b + g for (_, team), (b, g, _) in expected.items() if team == t["team"]), Fraction(0))
        assert t["xp"] == round(float(xp), 2) and t["level"] == 1 + int(xp) // size
    recovery = {(b["shift_no"], b["team"]) for b in data["badges"] if b["badge"] == "Recovery"}
    assert recovery == {key for key, (_, _, behind) in expected.items() if behind}
    assert data["streams"].get("gate_recovery", 0) == sum(behind for _, _, behind in expected.values())
    decided = _all(first, "SELECT COUNT(*) FROM perf_claim WHERE decision IN ('APPROVED', 'REJECTED')")[0][0]
    assert data["streams"].get("excusal_decided", 0) == decided
    assert set(data["not_derived"]) == {"dependency_unlocked", "Firebreak", "SkillSteward"}
