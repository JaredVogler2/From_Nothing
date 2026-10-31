"""SQLite tables of a simulation run, in four layers (synthetic data; one run per database file).

* **Pulled** (``src_<dataset>``): every source row version, the shift it was loaded in (``load_shift``) and whether it
  was written or removed (``op``). The rows visible at shift k are the latest version of each key up to k.
* **Calculated** (``calc_*``): what each shift's admitted snapshot holds: per open job its team, station window,
  duration, crew, earliest start, open part shortage and disposition; per aircraft its customer, minor model, due date
  and daily penalty; per team and shift the pool size and activation cap; inventory counts.
* **Method** (``method_*``, ``sim_shift``): stage timings and accepted moves of the optimizer, validator check counts and
  the first violations of each failed check (code, entity and detail),
  the defense outcome of every incumbent commitment (with the code of each UNSAFE one), stop reason, decision digest and
  each step's wall time.
* **Results and outcomes** (``plan_*``, ``exec_*``, ``perf_*``): the published assignments, duties, aircraft completions
  and costs, exceptions with reasons; what the simulated shop floor did with each assignment, its offplan work and
  out-of-sequence starts; each team-shift's goal units (frozen and amended), approved emergency additions, excusal claims
  with their decisions, and canonical score.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..data.datasets import DATASETS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_run (sim_id TEXT PRIMARY KEY, created_utc TEXT, spec TEXT, facts TEXT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS sim_shift (
  shift_no INTEGER PRIMARY KEY, shift_id TEXT, production_day TEXT, shift_number INTEGER, week INTEGER, cutoff_utc TEXT,
  start_utc TEXT, end_utc TEXT, source_run_id TEXT, snapshot_id TEXT, snapshot_hash TEXT, plan_id TEXT, plan_hash TEXT,
  decision_digest TEXT, valid INTEGER, engine TEXT, stop_reason TEXT, moves INTEGER, load_s REAL, plan_s REAL,
  validate_s REAL, execute_s REAL, write_s REAL, rss_mb REAL);
CREATE TABLE IF NOT EXISTS sim_person (person_id TEXT PRIMARY KEY, native TEXT, display TEXT, team TEXT, shift_number INTEGER, role TEXT);
CREATE TABLE IF NOT EXISTS calc_count (shift_no INTEGER, item TEXT, value INTEGER);
CREATE TABLE IF NOT EXISTS calc_job (
  shift_no INTEGER, job_id TEXT, line INTEGER, soi TEXT, station TEXT, team TEXT, is_rework INTEGER, status TEXT,
  disposition TEXT, reason TEXT, work_minutes INTEGER, crew INTEGER, earliest_utc TEXT, cs_start TEXT, cs_end TEXT,
  shortage_ecd TEXT, conditional INTEGER);
CREATE TABLE IF NOT EXISTS calc_aircraft (
  shift_no INTEGER, line INTEGER, customer TEXT, minor_model TEXT, station TEXT, due_utc TEXT, daily_penalty_usd REAL,
  jobs_open INTEGER, jobs_done INTEGER, shortages_open INTEGER, rework_open INTEGER);
CREATE TABLE IF NOT EXISTS calc_pool (shift_no INTEGER, day TEXT, shift_number INTEGER, team TEXT, members INTEGER, cap INTEGER);
CREATE TABLE IF NOT EXISTS method_stage (
  shift_no INTEGER, purpose TEXT, seq INTEGER, stage TEXT, runtime_ms INTEGER, candidates INTEGER, accepted INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS method_move (shift_no INTEGER, purpose TEXT, seq INTEGER, move TEXT);
CREATE TABLE IF NOT EXISTS method_check (shift_no INTEGER, purpose TEXT, check_name TEXT, count INTEGER, violations INTEGER);
CREATE TABLE IF NOT EXISTS method_violation (shift_no INTEGER, purpose TEXT, check_name TEXT, code TEXT, entity TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS method_commitment (shift_no INTEGER, purpose TEXT, outcome TEXT, code TEXT, n INTEGER);
CREATE TABLE IF NOT EXISTS plan_assignment (
  shift_no INTEGER, purpose TEXT, assignment_id TEXT, job_id TEXT, line INTEGER, soi TEXT, kind TEXT, team TEXT,
  start_utc TEXT, end_utc TEXT, shift_id TEXT, crew_size INTEGER, crew TEXT, conditional INTEGER, committed INTEGER,
  commitment_outcome TEXT);
CREATE TABLE IF NOT EXISTS plan_aircraft (
  shift_no INTEGER, purpose TEXT, line INTEGER, status TEXT, completion_utc TEXT, due_utc TEXT, late_days REAL, cost_usd REAL);
CREATE TABLE IF NOT EXISTS plan_exception (shift_no INTEGER, purpose TEXT, job_id TEXT, line INTEGER, disposition TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS plan_duty (
  shift_no INTEGER, purpose TEXT, person TEXT, team TEXT, start_utc TEXT, end_utc TEXT, kind TEXT, aircraft_from INTEGER,
  aircraft_to INTEGER, assignment_id TEXT, shift_id TEXT);
CREATE TABLE IF NOT EXISTS exec_assignment (
  shift_no INTEGER, assignment_id TEXT, job_id TEXT, line INTEGER, soi TEXT, team TEXT, crew_size INTEGER,
  planned_start TEXT, planned_end TEXT, actual_start TEXT, actual_end TEXT, outcome TEXT, cause TEXT);
CREATE TABLE IF NOT EXISTS exec_event (shift_no INTEGER, kind TEXT, line INTEGER, job TEXT, person TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS perf_team_shift (
  shift_no INTEGER, production_day TEXT, shift_number INTEGER, week INTEGER, team TEXT, units INTEGER, earned INTEGER, on_time INTEGER,
  missed_part INTEGER, missed_absence INTEGER, missed_predecessor INTEGER, missed_overrun INTEGER, missed_deviation INTEGER,
  planned_hours REAL, actual_hours REAL, present INTEGER, available_hours REAL, B0 INTEGER, additions INTEGER, B INTEGER, E INTEGER,
  Cp INTEGER, U INTEGER, oos_effort INTEGER, relieved_units INTEGER, added_units INTEGER, offplan_units INTEGER, oos_units INTEGER,
  attainment REAL, adherence REAL, utilization REAL, score REAL, grade TEXT, status TEXT, base REAL, O REAL, final REAL,
  policy_id TEXT, policy_hash TEXT, status_reason TEXT);
CREATE TABLE IF NOT EXISTS perf_unit (
  shift_no INTEGER, team TEXT, assignment_id TEXT, job_id TEXT, phase TEXT, line INTEGER, effort INTEGER, status TEXT, cause TEXT,
  claim_cause TEXT, origin_team TEXT, claim_status TEXT, relief INTEGER, oos_charged INTEGER, origin TEXT);
CREATE TABLE IF NOT EXISTS perf_claim (
  shift_no INTEGER, team TEXT, claim_id TEXT, cause TEXT, origin_team TEXT, executing_team TEXT, status TEXT, attribution TEXT,
  units INTEGER, effort INTEGER, evidence TEXT, decision TEXT, decided_by TEXT, reason TEXT, reported_by TEXT);
CREATE TABLE IF NOT EXISTS perf_amendment (
  shift_no INTEGER, team TEXT, amendment_id TEXT, assignment_id TEXT, job_id TEXT, phase TEXT, line INTEGER, effort INTEGER, basis TEXT,
  authority TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS perf_goal_day (
  frozen_shift_no INTEGER, production_day TEXT, plan_id TEXT, team TEXT, shift_number INTEGER, units INTEGER, B0 INTEGER);
CREATE TABLE IF NOT EXISTS exec_offplan (
  shift_no INTEGER, team TEXT, job_id TEXT, phase TEXT, line INTEGER, soi TEXT, effort INTEGER, start_utc TEXT, end_utc TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS exec_oos (
  shift_no INTEGER, team TEXT, assignment_id TEXT, phase TEXT, blocked_by TEXT, discretionary INTEGER, charged INTEGER, affected_effort INTEGER,
  effective_utc TEXT, evidence TEXT);
CREATE INDEX IF NOT EXISTS ix_calc_job ON calc_job (shift_no, line);
CREATE INDEX IF NOT EXISTS ix_plan_asg ON plan_assignment (shift_no, purpose, line);
CREATE INDEX IF NOT EXISTS ix_plan_asg_team ON plan_assignment (shift_no, purpose, team, start_utc);
CREATE INDEX IF NOT EXISTS ix_plan_duty ON plan_duty (shift_no, purpose, person);
CREATE INDEX IF NOT EXISTS ix_exec ON exec_assignment (shift_no, team);
CREATE INDEX IF NOT EXISTS ix_perf ON perf_team_shift (shift_no, team);
CREATE INDEX IF NOT EXISTS ix_perf_unit ON perf_unit (shift_no, team);
CREATE INDEX IF NOT EXISTS ix_perf_claim ON perf_claim (shift_no, team);
"""


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SimDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(SCHEMA)
        for name, spec in DATASETS.items():
            cols = ", ".join(f"{_quote(c)}" for c in spec.columns)
            self.con.execute(f"CREATE TABLE IF NOT EXISTS {_quote('src_' + name)} ({cols}, load_shift INTEGER, op TEXT)")
        self.con.commit()

    # ------------------------------------------------------------------ writes
    def insert(self, table: str, rows: Iterable[Sequence[Any]]) -> int:
        rows = list(rows)
        if rows:
            marks = ",".join("?" * len(rows[0]))
            self.con.executemany(f"INSERT INTO {_quote(table)} VALUES ({marks})", rows)
        return len(rows)

    def source_rows(self, name: str, rows: Iterable[dict], load_shift: int, op: str = "upsert") -> int:
        cols = DATASETS[name].columns
        return self.insert("src_" + name, ([_cell(r.get(c)) for c in cols] + [load_shift, op] for r in rows))

    def run_meta(self, sim_id: str, created_utc: str, spec: dict, facts: dict, status: str, note: str = "") -> None:
        self.con.execute("INSERT OR REPLACE INTO sim_run VALUES (?,?,?,?,?,?)",
                         (sim_id, created_utc, json.dumps(spec, default=str), json.dumps(facts, default=str), status, note))

    def commit(self) -> None:
        self.con.commit()

    def close(self) -> None:
        self.con.commit()
        self.con.close()


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str)):
        return int(value) if isinstance(value, bool) else value
    return json.dumps(value, default=str)
