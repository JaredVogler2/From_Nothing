"""A seeded model of the shop floor executing one published shift (synthetic outcomes, repeatable from the seed).

For every assignment that starts in the shift, in start order:

* **parts:** a task whose part shortage is still open when it should start cannot start (``part``);
* **absence:** each crew member misses the shift after publication with ``late_absence`` probability (``absence``);
* **predecessors:** every hard predecessor must be finished, before the shift or earlier in it; a late predecessor
  pushes the start, and a start pushed past the shift end is a slip (``predecessor``);
* **duration:** the actual length is the planned length times a lognormal factor (``duration_sigma``); work that would
  run more than the authorized overtime past the shift end is not finished (``overrun``).

Then the source system moves on: finished tasks are completed with their actual times, rework found on the floor and
QA rejections add rework jobs, parts whose expected date has passed arrive (or slip with ``part_slip``), undated
shortages get a date, and a few newly found shortages arrive undated. Each team's shift is scored from the outcomes:
score = 100 x (0.6 x planned tasks finished + 0.4 x planned tasks finished on time), graded A (90+) to F (below 60);
utilization (actual labor hours over present people's regular hours) is reported beside it, not in the grade, since it
reflects how much work the plan could give the team.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..domain import PhaseKind, from_minute, to_minute
from .state import SourceState

OT_ALLOWANCE = 60
SCORE_WEIGHTS = {"finished": 0.6, "on_time": 0.4}           # shares of a team's planned tasks: finished, and finished on time
GRADE_BANDS = (("A", 90), ("B", 80), ("C", 70), ("D", 60))     # lowest score of each grade; below the last band is F


def grade_of(score: float | None) -> str | None:
    if score is None:
        return None
    return next((grade for grade, low in GRADE_BANDS if score >= low), "F")


def scoring_text() -> str:
    """How a team's shift is scored, for the dashboard (the browser never holds the weights or the bands)."""
    bands = ", ".join(f"{grade} {low}+" for grade, low in GRADE_BANDS)
    return (f"Each team is scored on its published shift: score = 100 × ({SCORE_WEIGHTS['finished']} × planned tasks finished + "
            f"{SCORE_WEIGHTS['on_time']} × finished on time), graded {bands}, F below. Utilization is reported beside the score, not in it.")


@dataclass(frozen=True)
class FloorSpec:
    duration_sigma: float = 0.15
    late_absence: float = 0.01
    part_slip: float = 0.15
    qa_reject: float = 0.04
    new_undated_per_aircraft_shift: float = 0.02
    undated_dated_per_shift: float = 0.4
    planned_absence: float = 0.03


@dataclass
class Outcome:
    rows: list[tuple]
    events: list[tuple]
    perf: list[tuple]
    completed_jobs: int


def _local(dt_utc: datetime, tz) -> str:
    return dt_utc.astimezone(tz).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def attendance_rows(state: SourceState, day: date, rng: random.Random, absence: float, load_ts: str) -> list[dict]:
    """Every rostered person's attendance for the day's three shifts (absent with ``absence`` probability)."""
    rows = []
    for r in sorted(state.all("shift_roster"), key=lambda r: (r["SHIFT_NUMBER"], r["PERSON_KEY"])):
        present = rng.random() >= absence
        rows.append({"PRODUCTION_DAY": day.isoformat(), "SHIFT_NUMBER": int(r["SHIFT_NUMBER"]), "BEMS_ID": r["PERSON_KEY"], "PRESENT": present,
                     "HOURS": ("8.50" if int(r["SHIFT_NUMBER"]) != 3 else "7.00") if present else "0.00", "SOURCE": "SIM_BADGE",
                     "LOAD_TIMESTAMP": load_ts})
    return rows


def execute_shift(state: SourceState, shift_plan: Any, shift: Any, *, shift_no: int, week: int, rng: random.Random, spec: FloorSpec,
                  rework_rate: float, rework_minutes: list[int], cert_of_team: dict[str, str], tz) -> Outcome:
    ctx = shift_plan.context
    plan = shift_plan.plan
    s0, s1 = shift.start, shift.end
    phases = ctx.phases
    team_of_phase = {pid: (p.role_demands[0].team if p.role_demands else "?") for pid, p in phases.items()}
    done_phase: dict[str, int] = {}                                   # phase -> actual end minute (this shift)
    satisfied = {p.key.id for job, plist in ctx.job_phases.items() for p in plist
                 if ctx.dispositions.get(job) is not None and ctx.dispositions[job].disposition.value == "verified_complete"}
    open_short: dict[tuple[int, str], dict] = {}
    for r in state.all("etracs_active"):
        open_short[(int(r["LINE_NUMBER"]), str(r["USED_ON"]))] = r
    shift_day = from_minute(s0).astimezone(tz).date()

    def part_open(line: int, soi: str, at_minute: int) -> bool:
        r = open_short.get((line, soi))
        if r is None:
            return False
        if not r.get("ETRAC_ECD"):
            return True
        ready_day = date.fromisoformat(str(r["ETRAC_ECD"])) + timedelta(days=1)         # end-of-local-date floor
        return from_minute(at_minute).astimezone(tz).date() < ready_day

    todo = sorted((a for a in plan.assignments if s0 <= to_minute(a.start) < s1), key=lambda a: (to_minute(a.start), a.id))
    rows, events = [], []
    by_team: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    job_phase_total: dict[str, int] = defaultdict(int)
    for plist_job, plist in ctx.job_phases.items():
        job_phase_total[plist_job] = sum(1 for p in plist if p.kind is not PhaseKind.RELEASE)
    job_phase_done: dict[str, int] = defaultdict(int)
    job_times: dict[str, list[int]] = {}
    for a in todo:
        phase = phases.get(a.phase)
        job = ctx.jobs.get(a.job)
        if phase is None or job is None:
            continue
        team = team_of_phase.get(a.phase, "?")
        start, end = to_minute(a.start), to_minute(a.end)
        planned = a.work_minutes or (end - start)
        crew_size = len(a.crew)
        stats = by_team[team]
        stats["planned"] += 1
        stats["planned_hours"] += planned * crew_size / 60
        cause, actual_start, actual_end = None, None, None
        latest_pred = start
        for e in ctx.preds.get(a.phase, ()):
            pred = e.pred_phase
            if pred in satisfied or pred in ctx.releases:
                continue
            if pred in done_phase:
                latest_pred = max(latest_pred, done_phase[pred])
            else:
                cause = "predecessor"
                break
        if cause is None and phase.kind is PhaseKind.WORK and part_open(job.line, job.key.native_id, start):
            cause = "part"
        if cause is None and any(rng.random() < spec.late_absence for _ in a.crew):
            cause = "absence"
        if cause is None:
            actual_start = latest_pred
            factor = math.exp(rng.gauss(0.0, spec.duration_sigma))
            actual_end = actual_start + max(5, round(planned * factor))
            if actual_start >= s1 or actual_end > s1 + OT_ALLOWANCE:
                cause = "overrun" if actual_start < s1 else "predecessor"
                if cause == "predecessor":
                    actual_start = None
        if cause is None:
            done_phase[a.phase] = actual_end
            job_phase_done[a.job] += 1
            t = job_times.setdefault(a.job, [actual_start, actual_end])
            t[0], t[1] = min(t[0], actual_start), max(t[1], actual_end)
            stats["completed"] += 1
            stats["actual_hours"] += (actual_end - actual_start) * crew_size / 60
            if actual_end <= end + 15:
                stats["on_time"] += 1
        else:
            stats[f"slip_{cause}"] += 1
        rows.append((shift_no, a.id, a.job, job.line, job.key.native_id, team, crew_size, a.start.isoformat(), a.end.isoformat(),
                     from_minute(actual_start).isoformat() if actual_start is not None else None,
                     from_minute(actual_end).isoformat() if actual_end is not None and cause is None else None,
                     "completed" if cause is None else "slipped", cause))

    # ---- the source system moves on
    load_ts = _local(from_minute(s1 - 20), tz)
    completed = 0
    for job_id, n_done in sorted(job_phase_done.items()):
        if n_done < job_phase_total.get(job_id, 1):
            continue
        job = ctx.jobs[job_id]
        row = state.get("master_task_list", job.line, job.key.native_id)
        if row is None:
            continue
        t0, t1 = job_times[job_id]
        state.upsert("master_task_list", {**row, "SHOP_ORDER_STATUS": "COMPLETE", "ACT_START": _local(from_minute(t0), tz),
                                          "ACT_COMP": _local(from_minute(t1), tz), "LOAD_TIMESTAMP": load_ts})
        completed += 1
        if row.get("IS_REWORK"):
            link = state.get("neo_graph_rework", job.line, job.key.native_id)
            if link is not None:
                state.upsert("neo_graph_rework", {**link, "CHILD_STATUS": "CLOSED"})
        if any(p.kind is PhaseKind.FINAL_QA for p in ctx.job_phases.get(job_id, ())) and rng.random() < spec.qa_reject:
            _new_rework(state, job.line, row, rng, rework_minutes, cert_of_team, load_ts, shift_no, "QA reject", events)
    # rework found on the floor, against open or just-finished tasks of each aircraft's current station
    by_line_station: dict[int, str] = {}
    for f in ctx.snapshot.cs_facts:
        if f.earliest_at is not None and f.earliest_at <= from_minute(s1) and (f.line not in by_line_station or f.station > by_line_station[f.line]):
            by_line_station[f.line] = f.station
    station_tasks: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for r in state.all("master_task_list"):
        if not r["IS_REWORK"]:
            station_tasks[(r["LINE_NUMBER"], str(r["CURRENT_CONTROL_STATION"]))].append(r)
    for line in sorted(by_line_station):
        candidates = station_tasks.get((line, str(by_line_station[line])), [])
        count = _poisson(rng, rework_rate)
        for _ in range(min(count, len(candidates))):
            parent = candidates[rng.randrange(len(candidates))]
            _new_rework(state, line, parent, rng, rework_minutes, cert_of_team, load_ts, shift_no, "found on the floor", events)
        if rng.random() < spec.new_undated_per_aircraft_shift and candidates:
            target = candidates[rng.randrange(len(candidates))]
            if target["SHOP_ORDER_STATUS"] != "COMPLETE" and (line, target["SHOP_ORDER_IDENTIFIER"]) not in open_short:
                rec = 900000 + shift_no * 1000 + line % 1000
                state.upsert("etracs_active", {"RECORD_ID": rec, "LINE_NUMBER": line, "MINOR_MODEL_IDENT": target["MINOR_MODEL_IDENT"],
                                               "USED_ON": target["SHOP_ORDER_IDENTIFIER"], "ETRAC_ECD": None, "TEAM": target["TEAM"],
                                               "REASON_CODE": "LP", "ETRAC_CREATED_DT": shift_day.isoformat(), "ITEM_NUMBER": f"P-{rec}",
                                               "LOAD_TIMESTAMP": load_ts})
                events.append((shift_no, "shortage_found_undated", line, target["SHOP_ORDER_IDENTIFIER"], None, "no expected date yet"))
    # parts: arrive once their date has passed (or slip), undated ones get a date
    end_day = from_minute(s1).astimezone(tz).date()
    for r in sorted(list(state.all("etracs_active")), key=lambda r: r["RECORD_ID"]):
        ecd = r.get("ETRAC_ECD")
        if not ecd:
            if rng.random() < spec.undated_dated_per_shift:
                new = (end_day + timedelta(days=rng.randint(1, 7))).isoformat()
                state.upsert("etracs_active", {**r, "ETRAC_ECD": new, "LOAD_TIMESTAMP": load_ts})
                events.append((shift_no, "shortage_dated", r["LINE_NUMBER"], r["USED_ON"], None, f"expected {new}"))
            continue
        if date.fromisoformat(str(ecd)) < end_day:
            if rng.random() < spec.part_slip:
                new = (date.fromisoformat(str(ecd)) + timedelta(days=rng.randint(1, 3))).isoformat()
                state.upsert("etracs_active", {**r, "ETRAC_ECD": new, "LOAD_TIMESTAMP": load_ts})
                events.append((shift_no, "part_slipped", r["LINE_NUMBER"], r["USED_ON"], None, f"{ecd} -> {new}"))
            else:
                state.remove("etracs_active", r)
                events.append((shift_no, "part_arrived", r["LINE_NUMBER"], r["USED_ON"], None, f"expected {ecd}"))

    # ---- each team's shift score
    present: dict[str, int] = defaultdict(int)
    roster_team = {r["PERSON_KEY"]: r["TEAM"] for r in state.all("shift_roster") if int(r["SHIFT_NUMBER"]) == shift.number}
    for r in state.all("attendance_shift"):
        if r["PRODUCTION_DAY"] == shift.start_date.isoformat() and int(r["SHIFT_NUMBER"]) == shift.number and r["PRESENT"]:
            team = roster_team.get(r["BEMS_ID"])
            if team:
                present[team] += 1
    regular = shift.regular_cap
    perf = []
    for team in sorted(set(by_team) | set(present)):
        st = by_team.get(team, {})
        planned = int(st.get("planned", 0))
        done = int(st.get("completed", 0))
        on_time = int(st.get("on_time", 0))
        available = present.get(team, 0) * regular / 60
        adherence = done / planned if planned else 1.0
        on_time_rate = on_time / planned if planned else 1.0
        utilization = st.get("actual_hours", 0.0) / available if available else 0.0
        score = round(100 * (SCORE_WEIGHTS["finished"] * adherence + SCORE_WEIGHTS["on_time"] * on_time_rate), 1) if planned else None
        grade = grade_of(score)
        perf.append((shift_no, shift.start_date.isoformat(), shift.number, week, team, planned, done, on_time, int(st.get("slip_part", 0)),
                     int(st.get("slip_absence", 0)), int(st.get("slip_predecessor", 0)), int(st.get("slip_overrun", 0)),
                     round(st.get("planned_hours", 0.0), 2), round(st.get("actual_hours", 0.0), 2), present.get(team, 0), round(available, 2),
                     round(adherence, 4), round(on_time_rate, 4), round(utilization, 4), score, grade))
    return Outcome(rows, events, perf, completed)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _new_rework(state: SourceState, line: int, parent: dict, rng: random.Random, rework_minutes: list[int], cert_of_team: dict[str, str],
                load_ts: str, shift_no: int, why: str, events: list) -> None:
    existing = state.next_rework.get(line, 0)
    child = f"R{existing:05d}"
    while state.get("master_task_list", line, child) is not None:
        existing += 1
        child = f"R{existing:05d}"
    state.next_rework[line] = existing + 1
    team = parent["TEAM"]
    cls = rng.choice(("NC", "PU", "CI"))
    state.upsert("master_task_list", {
        "SHOP_ORDER_IDENTIFIER": child, "SHOP_ORDER_DESCRIPTION": f"Rework on {parent['SHOP_ORDER_IDENTIFIER']} ({why})", "LINE_NUMBER": line,
        "SHOP_ORDER_STATUS": "AVAILABLE", "TEAM": team, "CURRENT_CONTROL_STATION": parent["CURRENT_CONTROL_STATION"],
        "MINOR_MODEL_IDENT": parent["MINOR_MODEL_IDENT"], "CUSTOMER_CODE": parent["CUSTOMER_CODE"], "PARENT_SOI": parent["SHOP_ORDER_IDENTIFIER"],
        "SUPERINTENDENT": parent["SUPERINTENDENT"], "FIRST_AVAILABLE": load_ts, "ACT_START": None, "ACT_COMP": None, "LOAD_TIMESTAMP": load_ts,
        "PLANNING_SYSTEM_NAME": "SYNTH-PS", "IS_REWORK": True, "RECORD_KEY": 10_000_000 + shift_no * 10_000 + existing})
    state.upsert("flow_budget", {"JOB_NUMBER": child, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": None, "TOTAL_MECHANICS": 2,
                                 "PRIMARY_MECHANIC_COUNT": 2, "PRIMARY_MECHANIC_DURATION": None, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                                 "TOTAL_LABOR_MINUTES": None, "DURATION_SOURCE": "REWORK_STATISTIC", "LOAD_TIMESTAMP": load_ts})
    state.upsert("soi_cert_requirements", {"SOI_ID": child, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": "PROC REWORK",
                                           "CERT_CODE": cert_of_team.get(team, "C01"), "CERT_TYPE": "CERT", "REQ_CERT": "Y",
                                           "SNAPSHOT_TS": "2026-10-25T05:00:00"})
    state.upsert("neo_graph_rework", {"LINE_NUMBER": line, "PARENT_SOI": parent["SHOP_ORDER_IDENTIFIER"], "CHILD_SOI": child,
                                      "CHILD_STATUS": "OPEN", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
    state.upsert("rework_classification", {"LINE_NUMBER": line, "CHILD_SOI": child, "REWORK_CLASS": cls, "ROOT_SOI": parent["SHOP_ORDER_IDENTIFIER"],
                                           "DEFECT_ORIGIN_TEAM": team, "EVIDENCE_REF": f"SIM-NCR-{line}-{shift_no}-{existing}"})
    events.append((shift_no, "rework_found", line, child, None, why))
