"""A13: simulation recognition from stored rows, on a hand-built run database with exact expected values (synthetic data)."""
from __future__ import annotations

import sqlite3
from fractions import Fraction
from pathlib import Path

from focus.sim.db import SimDB
from focus.sim.recognition import derive

# shift_no -> (production day, shift number); 10-31 S1..S3 then 11-01 S1, so October closes inside the run
SHIFTS = {0: ("2026-10-31", 1), 1: ("2026-10-31", 2), 2: ("2026-10-31", 3), 3: ("2026-11-01", 1)}


def _team_shift(shift_no: int, team: str, B: int, Cp: int, score: float | None, status: str) -> tuple:
    day, number = SHIFTS[shift_no]
    return (shift_no, day, number, 1, team, 1, 1, 1, 0, 0, 0, 0, 0, 1.0, 1.0, 1, 1.0, B, 0, B, 0, Cp, 0, 0, 0, 0, 0, 0,
            None, None, None, score, None, status, score, 0.0, score, "rebuild-v1", "h", None if B else "NO_PLAN: no frozen obligation (B = 0)")


def _unit(shift_no: int, team: str, assignment: str, job: str, effort: int, status: str, origin: str = "ORIGINAL", relief: int = 0,
          scarce: int = 0) -> tuple:
    return (shift_no, team, assignment, job, f"{job}.W.0", 1201, effort, status, None, None, None, None, relief, 0, origin, scarce)


def build(path: Path) -> None:
    db = SimDB(path)
    db.insert("perf_goal_day", [(0, "2026-10-31", "PLAN-A", team, n, 1, 100) for team in ("T1", "T2") for n in (1, 2, 3)] +
              [(3, "2026-11-01", "PLAN-B", team, 1, 1, 100) for team in ("T1", "T2")])
    # the freezing snapshot: J1 three days behind its station gate (10-28), J2 not behind, J3 behind, J4 without a gate
    db.insert("calc_job", [(0, job, 1201, None, "741", "T1", 0, "OPEN", "admitted", None, 60, 1, None, None, gate, None, 0)
                           for job, gate in (("J1", "2026-10-28"), ("J2", "2026-11-03"), ("J3", "2026-10-20"), ("J4", None))])
    db.insert("plan_assignment", [(0, "EXECUTABLE", "A1", "J1", 1201, "J1", "WORK", "T1", "", "", "", 1, "", 0, 1, "KEPT"),
                                  (0, "EXECUTABLE", "A2", "J2", 1201, "J2", "WORK", "T1", "", "", "", 1, "", 0, 1, "KEPT")])
    db.insert("perf_team_shift", [_team_shift(0, "T1", 400, 300, 95.0, "DEMONSTRATION"), _team_shift(0, "T2", 100, 100, 96.0, "DEMONSTRATION"),
                                  _team_shift(1, "T1", 0, 0, None, "NEUTRAL"), _team_shift(1, "T2", 100, 100, 92.0, "DEMONSTRATION"),
                                  _team_shift(2, "T2", 100, 100, 91.0, "DEMONSTRATION"), _team_shift(3, "T2", 100, 50, 40.0, "DEMONSTRATION")])
    db.insert("perf_unit", [_unit(0, "T1", "A1", "J1", 100, "EARNED"),                  # behind: base 20, bonus 20 x 0.35 x 3/5
                            _unit(0, "T1", "A2", "J2", 100, "EARNED"),                  # not behind: base 20
                            _unit(0, "T1", "A3", "J3", 100, "EARNED", origin="AMENDMENT"),   # behind, but an addition: no bonus
                            _unit(0, "T1", "A4", "J4", 100, "MISSED", relief=1, scarce=1),   # relieved miss: commitment kept; no steward
                            _unit(1, "T1", "A5", "J1", 50, "EARNED"),                   # NEUTRAL goal: earns nothing
                            _unit(0, "T2", "B1", "J2", 100, "EARNED", scarce=1),      # earned scarce-role work: SkillSteward
                            _unit(1, "T2", "B2", "J2", 100, "EARNED"),
                            _unit(2, "T2", "B3", "J2", 100, "EARNED"), _unit(3, "T2", "B4", "J2", 50, "EARNED")])
    # released dependencies: A1 freed one job, A2 three (Firebreak at the threshold), the NEUTRAL goal's A5 three (earns nothing)
    db.insert("perf_unlock", [(0, "T1", "A1", "J1.W.0", 1201, "J9")] + [(0, "T1", "A2", "J2.W.0", 1201, j) for j in ("J5", "J6", "J7")] +
              [(1, "T1", "A5", "J1.W.0", 1201, j) for j in ("J5", "J6", "J7")])
    db.insert("perf_claim", [(0, "T1", "C1", "LATE_PART", None, "T1", "SUBMITTED", "AUTO", 1, 100, "E", "APPROVED", "rev", "ok, verified", "sim"),
                             (0, "T1", "C2", "SAME_TEAM_PREDECESSOR", None, "T1", "PROPOSED", "AUTO", 1, 100, "E", None, None, "proposed", "sim")])
    db.commit()
    db.close()


def test_recognition_values_badges_and_levels(tmp_path: Path, policy) -> None:
    build(tmp_path / "run.db")
    con = sqlite3.connect(tmp_path / "run.db")
    try:
        data = derive(con, policy)
    finally:
        con.close()
    shifts = {(s["shift_no"], s["team"]): s for s in data["shifts"]}
    t1 = shifts[(0, "T1")]
    bonus = Fraction(20) * Fraction(35, 100) * Fraction(3, 5)                          # 4.2
    assert (t1["earned_units"], t1["base"], t1["gate_bonus"], t1["behind_units"]) == (3, 60.0, float(bonus), 1)
    assert t1["commitment_kept"] and shifts[(1, "T1")]["xp"] == 0 and shifts[(1, "T1")]["earned_units"] == 0
    assert data["streams"] == {"badge_earned": 6, "commitment_kept": 1, "completion_credited": 7, "dependency_unlocked": 2, "excusal_decided": 1,
                               "gate_recovery": 1}
    badges = sorted((b["badge"], b["team"], b["shift_no"]) for b in data["badges"] if b["badge"] != "MonthlyChampion")
    assert badges == [("CommitmentKeeper", "T1", 0), ("Firebreak", "T1", 0), ("Recovery", "T1", 0), ("SkillSteward", "T2", 0), ("SteadyShift", "T2", 2)]
    assert [b["released"] for b in data["badges"] if b["badge"] == "Firebreak"] == [3]
    champion = [b for b in data["badges"] if b["badge"] == "MonthlyChampion"]
    assert [(b["team"], b["period"]) for b in champion] == [("T2", "2026-10")]         # pooled 300/300 beats T1; November is still open
    teams = {t["team"]: t for t in data["teams"]}
    assert teams["T1"]["xp"] == round(60 + float(bonus), 2) and teams["T1"]["level"] == 1
    assert teams["T2"]["xp"] == 70.0 and teams["T2"]["badges"] == {"MonthlyChampion": 1, "SkillSteward": 1, "SteadyShift": 1}
    assert data["skipped_goals"] == {"NEUTRAL": 1} and data["not_derived"] == {}
    week = {(m["team"], m["iso_week"]): m["awards"] for m in data["inflation"]}
    assert week[("T1", 44)] == 3 + 1 + 2 + 3                                             # credits, the recovery, two releases and three badges


def test_a_run_without_the_stored_facts_lists_those_rules_as_not_derived(tmp_path: Path, policy) -> None:
    build(tmp_path / "old.db")
    con = sqlite3.connect(tmp_path / "old.db")
    try:
        con.execute("DROP TABLE perf_unlock")
        con.execute("ALTER TABLE perf_unit DROP COLUMN scarce")                      # the schema of a run made before they were stored
        data = derive(con, policy)
    finally:
        con.close()
    assert set(data["not_derived"]) == {"dependency_unlocked", "Firebreak", "SkillSteward"}
    assert "dependency_unlocked" not in data["streams"] and not {"Firebreak", "SkillSteward"} & {b["badge"] for b in data["badges"]}
    assert data["streams"]["completion_credited"] == 7                                  # everything else is derived as before
