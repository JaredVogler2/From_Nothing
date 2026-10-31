"""Read-only operations over a fleet simulation database (``focus.sim``), for the simulation dashboard and MCP.

Every handler opens the database read-only, answers with plain JSON built by SQL over the simulation tables and never
writes. ``FOCUS_SIM_DB`` names the database (default ``runtime/sim/fleet.db`` under the app root); without one the
operations report ``empty`` with the command that builds it.

**Synthetic demonstration.** All data is synthetic, and every payload says so (``synthetic``, ``status_label``).
**Fleet-wide reads only.** The simulation is a fleet view, so a session scoped to some teams is refused before anything
is read (S15 scope before fetch).
**One score authority.** Team, day, week and fleet scores are recomputed here by ``focus.performance.score.formula``
from each goal's stored components under the active policy, pooled before dividing, never read back as stored grades.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..sim.execute import scoring_text
from .api import ALL_READERS, svc
from .operations import REGISTRY, ApiResult, reason
from .security import ApiError, RequestContext

SHIFT_Q = {"type": "object", "properties": {"shift": {"type": "integer", "minimum": 0, "maximum": 10000,
                                                      "description": "Simulation shift number (0 = first); default the latest."}},
           "additionalProperties": False}
TEAM_Q = {"type": "object", "properties": {**SHIFT_Q["properties"], "team": {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,16}$"}},
          "additionalProperties": False}
LINE_Q = {"type": "object", "properties": {**SHIFT_Q["properties"], "line": {"type": "integer", "minimum": 1, "maximum": 99999}},
          "additionalProperties": False}
BUILD = "python -m focus.sim.runner --aircraft 50 --days 14 --db runtime/sim/fleet.db"
LOAD_BANDS = (("over", 1.0, "over the pool"), ("high", 0.9, "above 90 %"), ("busy", 0.6, "60-90 %"), ("light", 0.3, "30-60 %"),
              ("idle", None, "below 30 %"))      # planned hours over pool hours; the browser colors the band it is given


def db_path() -> Path:
    explicit = os.environ.get("FOCUS_SIM_DB")
    return Path(explicit) if explicit else svc().settings.app_root / "runtime" / "sim" / "fleet.db"


def _connect() -> sqlite3.Connection | None:
    path = db_path()
    if not path.is_file():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


SCHEMA_TABLES = ("perf_unit", "perf_claim", "perf_amendment", "exec_offplan", "exec_oos", "method_commitment")   # this version's tables


def _fleet_scope(ctx: RequestContext) -> None:
    """Scope before fetch: the simulation is fleet-wide, so only an all-teams session may read it."""
    if not ctx.scope.all_teams:
        raise ApiError(403, "SCOPE_DENIED", "the fleet simulation is a fleet-wide view; this session is scoped to some teams")


def _outdated(con: sqlite3.Connection) -> ApiResult | None:
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = [t for t in SCHEMA_TABLES if t not in have]
    if missing:
        return ApiResult("blocked", None, reasons=[reason("SIMULATION_SCHEMA_OUTDATED", f"the simulation database lacks {', '.join(missing)}",
                                                          f"rebuild it with: {BUILD}")])
    return None


def _label(data: dict[str, Any]) -> dict[str, Any]:
    """Every simulation payload says what it is: synthetic data, scored under a demonstration policy, never measured actuals."""
    policy = svc().policy
    return {**data, "synthetic": True, "status_label": "DEMONSTRATION", "score_policy": {"id": policy.id, "hash": policy.hash,
                                                                                     "status": policy.str("score.policy_status")}}


def _unavailable() -> ApiResult:
    return ApiResult("empty", None, reasons=[reason("SIMULATION_NOT_RUN", f"no simulation database at {db_path().name}",
                                                          f"build one with: {BUILD}")])


def _rows(con: sqlite3.Connection, sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def _one(con: sqlite3.Connection, sql: str, *args: Any) -> Any:
    row = con.execute(sql, args).fetchone()
    return None if row is None else row[0]


def _shift(con: sqlite3.Connection, args: dict[str, Any]) -> int | None:
    latest = _one(con, "SELECT MAX(shift_no) FROM sim_shift")
    if latest is None:
        return None
    want = args.get("shift")
    return latest if want is None else min(int(want), latest)


def _purpose(con: sqlite3.Connection, k: int) -> str:
    """The projection purpose: FORECAST when the shift has one (completions and costs), else the purpose it has."""
    found = [r["purpose"] for r in _rows(con, "SELECT DISTINCT purpose FROM plan_aircraft WHERE shift_no = ?", k)]
    return "FORECAST" if "FORECAST" in found else (found[0] if found else "FORECAST")


def _dispatch(con: sqlite3.Connection, k: int) -> str:
    """The dispatched purpose: EXECUTABLE when the shift has one (what the floor worked and the goals of record)."""
    found = [r["purpose"] for r in _rows(con, "SELECT DISTINCT purpose FROM plan_assignment WHERE shift_no = ?", k)]
    return "EXECUTABLE" if "EXECUTABLE" in found else (found[0] if found else "FORECAST")


def _rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pooled canonical score of team-shift goals (S14 aggregation): the exact components are summed before any division,
    so a fleet, day or week score is never an average of team scores. Counts and hours are summed, utilization recomputed."""
    from fractions import Fraction

    from ..performance.score import formula

    keys = ("units", "earned", "on_time", "missed_part", "missed_absence", "missed_predecessor", "missed_overrun", "missed_deviation",
            "planned_hours", "actual_hours", "available_hours", "present", "B0", "additions", "B", "E", "Cp", "U", "oos_effort", "relieved_units",
            "added_units", "offplan_units", "oos_units")
    out = {k: round(sum((r.get(k) or 0) for r in rows), 2) for k in keys}
    policy = svc().policy
    result = formula(Fraction(int(out["B"])), Fraction(int(out["E"])), Fraction(int(out["Cp"])), Fraction(int(out["U"])),
                     Fraction(policy.str("score.oos_rate")) * int(out["oos_effort"]), policy)
    out["D"] = int(out["B"]) - int(out["E"])
    out["attainment"] = None if result["attainment"] is None else round(float(result["attainment"]), 4)
    out["adherence"] = None if result["adherence"] is None else round(float(result["adherence"]), 4)
    out["oos_charge"] = round(float(Fraction(policy.str("score.oos_rate")) * int(out["oos_effort"]) * 100 / out["D"]), 2) if out["D"] > 0 else None
    out["score"] = None if result["score"] is None else float(result["score"])
    out["grade"] = result["grade"]
    out["status"] = "SCORED" if result["score"] is not None else "NEUTRAL"
    out["utilization"] = round(out["actual_hours"] / out["available_hours"], 4) if out["available_hours"] else None
    return out


def _delta(cur: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    if not prev:
        return {}
    out = {}
    for key, value in cur.items():
        other = prev.get(key)
        if isinstance(value, (int, float)) and isinstance(other, (int, float)):
            out[key] = round(value - other, 4)
    return out


# ---------------------------------------------------------------------------------------------- overview


@REGISTRY.operation(
    "sim_overview", "GET", "/api/v1/sim/overview",
    summary="Fleet simulation overview for one shift: KPIs, every aircraft's projection and cost, trends",
    description="The selected shift's published plan for the fleet (synthetic): projected completion, days late and penalty per aircraft "
                "with customer and minor model, fleet KPIs against the prior shift, and per-shift trends of cost, late aircraft, work done "
                "and utilization. Read-only over the simulation database.",
    grants=ALL_READERS, read_only=True, query=SHIFT_Q, tags=("simulation",),
)
def sim_overview(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        run = _rows(con, "SELECT sim_id, created_utc, status, spec, facts FROM sim_run")
        meta = run[0] if run else {}
        for key in ("spec", "facts"):
            if key in meta and meta[key]:
                meta[key] = json.loads(meta[key])
        shifts = _rows(con, "SELECT shift_no, shift_id, production_day, shift_number, week, valid, engine, stop_reason, moves, load_s, plan_s, "
                            "execute_s, write_s, rss_mb, decision_digest, cutoff_utc, plan_id, source_run_id FROM sim_shift ORDER BY shift_no")
        purpose = _purpose(con, k)
        fleet = _rows(con, """
            SELECT a.line, c.customer, c.minor_model, c.station, a.status, a.completion_utc, a.due_utc, a.late_days, a.cost_usd,
                   c.daily_penalty_usd, c.jobs_open, c.jobs_done, c.shortages_open, c.rework_open
            FROM plan_aircraft a LEFT JOIN calc_aircraft c ON c.shift_no = a.shift_no AND c.line = a.line
            WHERE a.shift_no = ? AND a.purpose = ? ORDER BY a.line""", k, purpose)

        def kpis(n: int) -> dict[str, Any] | None:
            if n < 0:
                return None
            p = _purpose(con, n)
            row = con.execute("SELECT COUNT(*), SUM(CASE WHEN late_days > 0 THEN 1 ELSE 0 END), SUM(cost_usd), AVG(late_days), "
                              "SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) FROM plan_aircraft WHERE shift_no = ? AND purpose = ?",
                              (n, p)).fetchone()
            perf = _rollup(_rows(con, "SELECT * FROM perf_team_shift WHERE shift_no = ?", n))
            open_jobs = _one(con, "SELECT SUM(jobs_open) FROM calc_aircraft WHERE shift_no = ?", n)
            return {"aircraft": row[0], "late_aircraft": row[1], "projected_penalty_usd": None if row[2] is None else round(row[2], 2),
                    "penalty_scope": "known aircraft only" if row[4] else "every aircraft",
                    "mean_late_days": round(row[3], 2) if row[3] is not None else None, "unknown_completion": row[4], "open_jobs": open_jobs,
                    "planned_tasks": perf["units"], "completed_tasks": perf["earned"], "attainment": perf["attainment"],
                    "adherence": perf["adherence"], "utilization": perf["utilization"], "score": perf["score"], "grade": perf["grade"]}

        cur, prev = kpis(k), kpis(k - 1)
        trend = []
        for s in shifts:
            n = s["shift_no"]
            p = _purpose(con, n)
            row = con.execute("SELECT SUM(cost_usd), SUM(CASE WHEN late_days > 0 THEN 1 ELSE 0 END), AVG(late_days) FROM plan_aircraft "
                              "WHERE shift_no = ? AND purpose = ?", (n, p)).fetchone()
            perf = _rollup(_rows(con, "SELECT * FROM perf_team_shift WHERE shift_no = ?", n))
            trend.append({"shift_no": n, "label": f"{s['production_day'][5:]} S{s['shift_number']}", "penalty_usd": None if row[0] is None else round(row[0], 2),
                          "late_aircraft": row[1], "mean_late_days": round(row[2], 2) if row[2] is not None else None,
                          "completed": perf["earned"], "attainment": perf["attainment"], "adherence": perf["adherence"],
                          "utilization": perf["utilization"], "score": perf["score"]})
        by_customer: dict[str, dict[str, float]] = defaultdict(lambda: {"aircraft": 0, "penalty_usd": 0.0, "late_days": 0.0, "unknown": 0})
        for f in fleet:
            key = f"{f['customer']} 787-{f['minor_model']}"
            by_customer[key]["aircraft"] += 1
            if f["cost_usd"] is None:
                by_customer[key]["unknown"] += 1               # unknown completion: never counted as zero penalty
            else:
                by_customer[key]["penalty_usd"] += f["cost_usd"]
                by_customer[key]["late_days"] += f["late_days"] or 0
            by_customer[key]["daily_penalty_usd"] = f["daily_penalty_usd"]
    data = {"run": meta, "shift": k, "purpose": purpose, "shifts": shifts, "kpis": cur, "prior": prev, "delta": _delta(cur or {}, prev),
            "fleet": fleet, "trend": trend,
            "by_customer": [{"customer_model": key, **{m: round(v, 2) if isinstance(v, float) else v for m, v in val.items()}}
                            for key, val in sorted(by_customer.items(), key=lambda kv: -kv[1]["penalty_usd"])]}
    return ApiResult("ready", _label(data), purpose=purpose)


# ---------------------------------------------------------------------------------------------- performance


@REGISTRY.operation(
    "sim_performance", "GET", "/api/v1/sim/performance",
    summary="Shift, day and week performance: this shift against the prior shift, day against day, week against week",
    description="Canonical team-shift scores of the simulated execution (S14 formula under the active policy) pooled by shift, production "
                "day and week: planned effort, approved relief, earned planned effort, offplan output, out-of-sequence charge, attainment, "
                "adherence, score and grade, missed tasks by cause, labor hours and utilization, with the selected period against the one "
                "before and per-team detail.",
    grants=ALL_READERS, read_only=True, query=SHIFT_Q, tags=("simulation",),
)
def sim_performance(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        rows = _rows(con, "SELECT * FROM perf_team_shift ORDER BY shift_no, team")
        meta = {r["shift_no"]: r for r in _rows(con, "SELECT shift_no, production_day, shift_number, week FROM sim_shift")}
        causes = _rows(con, "SELECT shift_no, COALESCE(claim_cause, CASE cause WHEN 'deviation' THEN 'DEVIATION' ELSE UPPER(cause) END) AS cause, "
                            "SUM(CASE WHEN relief = 1 THEN effort ELSE 0 END) AS excused, SUM(CASE WHEN relief = 0 THEN effort ELSE 0 END) "
                            "AS unexcused FROM perf_unit WHERE status = 'MISSED' GROUP BY shift_no, 2 ORDER BY shift_no, 2")
    by_shift: dict[int, list] = defaultdict(list)
    by_day: dict[str, list] = defaultdict(list)
    by_week: dict[int, list] = defaultdict(list)
    for r in rows:
        by_shift[r["shift_no"]].append(r)
        by_day[r["production_day"]].append(r)
        by_week[r["week"]].append(r)
    shifts = [{"shift_no": n, "label": f"{meta[n]['production_day'][5:]} S{meta[n]['shift_number']}", **_rollup(v)}
              for n, v in sorted(by_shift.items())]
    days = [{"day": d, **_rollup(v)} for d, v in sorted(by_day.items())]
    weeks = [{"week": w, **_rollup(v)} for w, v in sorted(by_week.items())]
    sel = meta.get(k, {})
    day, week = sel.get("production_day"), sel.get("week")
    day_list = [d["day"] for d in days]
    prior_day = day_list[day_list.index(day) - 1] if day in day_list and day_list.index(day) > 0 else None

    def find(items: list, key: str, value: Any) -> dict | None:
        return next((x for x in items if x.get(key) == value), None)

    compare = {
        "shift": {"current": find(shifts, "shift_no", k), "prior": find(shifts, "shift_no", k - 1)},
        "day": {"current": find(days, "day", day), "prior": find(days, "day", prior_day)},
        "week": {"current": find(weeks, "week", week), "prior": find(weeks, "week", (week or 0) - 1)},
        "overall": {"current": _rollup(rows), "prior": None},
    }
    for c in compare.values():
        c["delta"] = _delta(c["current"] or {}, c["prior"])
    teams = []
    prior_teams = {r["team"]: r for r in by_shift.get(k - 1, [])}
    for r in by_shift.get(k, []):
        cur, p = _rollup([r]), (_rollup([prior_teams[r["team"]]]) if r["team"] in prior_teams else None)
        teams.append({**r, **{key: cur[key] for key in ("score", "grade", "attainment", "adherence", "D", "status")},
                      "score_delta": round(cur["score"] - p["score"], 1) if p and cur["score"] is not None and p["score"] is not None else None})
    data = {"shift": k, "day": day, "week": week, "compare": compare, "shifts": shifts, "days": days, "weeks": weeks, "teams": teams,
            "missed_by_cause": causes, "scoring": scoring_text(svc().policy)}
    return ApiResult("ready", _label(data))


# ---------------------------------------------------------------------------------------------- excusals


@REGISTRY.operation(
    "sim_excusals", "GET", "/api/v1/sim/excusals",
    summary="Why planned work was missed: excusal claims by cause, decisions and relief, offplan work and out-of-sequence starts",
    description="For one shift, optionally one team: every missed planned task's claim in the S14 disruption taxonomy with origin team, "
                "evidence, status (submitted or proposed) and the simulated excusal decision; relief and unexcused misses by cause; whose "
                "unfinished work blocked whom (the shift and the whole run); offplan output and out-of-sequence starts. Read-only.",
    grants=ALL_READERS, read_only=True, query=TEAM_Q, tags=("simulation",),
)
def sim_excusals(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        team = args.get("team")
        scope, params = ("shift_no = ? AND team = ?", (k, team)) if team else ("shift_no = ?", (k,))
        claims = _rows(con, f"SELECT team, claim_id, cause, origin_team, status, attribution, units, effort, evidence, decision, decided_by, reason "
                            f"FROM perf_claim WHERE {scope} ORDER BY team, cause, claim_id", *params)
        by_cause = _rows(con, "SELECT cause, COUNT(*) AS claims, SUM(units) AS units, SUM(effort) AS effort, "
                              "SUM(CASE WHEN decision = 'APPROVED' THEN effort ELSE 0 END) AS relieved, "
                              "SUM(CASE WHEN status = 'PROPOSED' THEN effort ELSE 0 END) AS proposed FROM perf_claim WHERE shift_no = ? "
                              "GROUP BY cause ORDER BY cause", k)
        unexcused = _rows(con, "SELECT COALESCE(claim_cause, UPPER(cause)) AS cause, COUNT(*) AS units, SUM(effort) AS effort FROM perf_unit "
                               "WHERE shift_no = ? AND status = 'MISSED' AND relief = 0 GROUP BY 1 ORDER BY 1", k)
        blame_shift = _rows(con, "SELECT origin_team, team, SUM(units) AS units, SUM(effort) AS effort FROM perf_claim WHERE shift_no = ? AND "
                                 "cause = 'CROSS_TEAM_PREDECESSOR' GROUP BY origin_team, team ORDER BY effort DESC", k)
        blame_run = _rows(con, "SELECT origin_team, team, SUM(units) AS units, SUM(effort) AS effort FROM perf_claim WHERE "
                               "cause = 'CROSS_TEAM_PREDECESSOR' GROUP BY origin_team, team ORDER BY effort DESC LIMIT 40")
        offplan = _rows(con, f"SELECT team, line, soi, effort, start_utc, end_utc, kind FROM exec_offplan WHERE {scope} ORDER BY team, start_utc",
                        *params)
        oos = _rows(con, f"SELECT o.team, o.assignment_id, o.discretionary, o.charged, o.affected_effort, a.line, a.soi FROM exec_oos o "
                         f"LEFT JOIN exec_assignment a ON a.shift_no = o.shift_no AND a.assignment_id = o.assignment_id "
                         f"WHERE o.{scope.replace(' AND team', ' AND o.team')} ORDER BY o.team, o.assignment_id", *params)
        amendments = _rows(con, f"SELECT a.team, a.job_id, a.line, a.effort, a.authority, a.reason, u.status FROM perf_amendment a "
                                f"LEFT JOIN perf_unit u ON u.shift_no = a.shift_no AND u.assignment_id = a.assignment_id "
                                f"WHERE a.{scope.replace(' AND team', ' AND a.team')} ORDER BY a.team, a.line", *params)
        teams = [r["team"] for r in _rows(con, "SELECT DISTINCT team FROM perf_team_shift WHERE shift_no = ? ORDER BY team", k)]
    data = {"shift": k, "team": team, "teams": teams, "claims": claims, "by_cause": by_cause, "unexcused": unexcused, "blame_shift": blame_shift,
            "blame_run": blame_run, "offplan": offplan, "oos": oos, "amendments": amendments,
            "decider": "simulated excusal decider (role excusal_decider), never the claimant",
            "rules": "Excusable when approved: LATE_PART, CROSS_TEAM_PREDECESSOR, CAPACITY_SHORTAGE, SKILL_SHORTAGE, REWORK_INJECTION. "
                     "A submitted claim is approved only when its evidence holds: the cited part record was open at the planned start; the "
                     "cited task belongs to the named other team and was unfinished at the planned start (a task that was itself blocked passes "
                     "the blame upstream); attendance shows the call-off after publication; the rework's defect came from another team's work "
                     "(the team's own defect is rejected). SAME_TEAM_PREDECESSOR and DURATION_OVERRUN are only proposed (a person must add "
                     "notes) and never excused automatically. A deviation, or work a replan moved with no disruption on record, has no claim. "
                     "Rework found after the goals froze that the plan assigns is added to the goal by the shift lead (B = B0 + additions)."}
    return ApiResult("ready", _label(data))


# ---------------------------------------------------------------------------------------------- dispatch and gantt


@REGISTRY.operation(
    "sim_dispatch", "GET", "/api/v1/sim/dispatch",
    summary="The published dispatch for one shift: per team, or one team's task list with aircraft and crew",
    description="The static snapshot published for the shift: without team, each team's task count, aircraft and labor hours; with team, "
                "every task starting in the shift with aircraft, job, times and the named crew. Read-only.",
    grants=ALL_READERS, read_only=True, query=TEAM_Q, tags=("simulation",),
)
def sim_dispatch(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        purpose = _dispatch(con, k)
        s = _rows(con, "SELECT shift_id, start_utc, end_utc, production_day, shift_number FROM sim_shift WHERE shift_no = ?", k)[0]
        base = ("FROM plan_assignment WHERE shift_no = ? AND purpose = ? AND start_utc >= ? AND start_utc < ?")
        teams = _rows(con, f"SELECT team, COUNT(*) AS tasks, COUNT(DISTINCT line) AS aircraft, "
                           f"ROUND(SUM((julianday(end_utc) - julianday(start_utc)) * 24 * crew_size), 1) AS labor_hours, "
                           f"SUM(conditional) AS conditional {base} GROUP BY team ORDER BY team", k, purpose, s["start_utc"], s["end_utc"])
        tasks = []
        team = args.get("team") or (teams[0]["team"] if teams else None)
        if team:
            names = {r["person_id"]: r["display"] for r in _rows(con, "SELECT person_id, display FROM sim_person")}
            outcome = {r["assignment_id"]: r for r in _rows(con, "SELECT assignment_id, outcome, cause, actual_start, actual_end FROM exec_assignment "
                                                                "WHERE shift_no = ?", k)}
            for r in _rows(con, f"SELECT assignment_id, line, soi, kind, start_utc, end_utc, crew_size, crew, conditional, committed, "
                                f"commitment_outcome {base} AND team = ? ORDER BY start_utc, line", k, purpose, s["start_utc"], s["end_utc"], team):
                ex = outcome.get(r["assignment_id"], {})
                tasks.append({**{key: r[key] for key in r if key != "crew"}, "crew": [names.get(p, p) for p in (r["crew"] or "").split(",") if p],
                              "outcome": ex.get("outcome"), "cause": ex.get("cause")})
    return ApiResult("ready", _label({"shift": k, "window": s, "purpose": purpose, "teams": teams, "team": team, "tasks": tasks}), purpose=purpose)


@REGISTRY.operation(
    "sim_gantt", "GET", "/api/v1/sim/gantt",
    summary="One team's people over the published shift: work, transfers and waits per person",
    description="Every duty of the team's people in the published shift (work, transfer between aircraft, wait), for a worker Gantt. Read-only.",
    grants=ALL_READERS, read_only=True, query=TEAM_Q, tags=("simulation",),
)
def sim_gantt(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        purpose = _dispatch(con, k)
        s = _rows(con, "SELECT shift_id, start_utc, end_utc, shift_number FROM sim_shift WHERE shift_no = ?", k)[0]
        teams = [r["team"] for r in _rows(con, "SELECT DISTINCT team FROM sim_person WHERE shift_number = ? AND team IS NOT NULL ORDER BY team",
                                          s["shift_number"])]
        team = args.get("team") or (teams[0] if teams else None)
        people = _rows(con, "SELECT person_id, display FROM sim_person WHERE team = ? AND shift_number = ? ORDER BY display", team, s["shift_number"])
        ids = {p["person_id"] for p in people}
        duties = [d for d in _rows(con, "SELECT person, start_utc, end_utc, kind, aircraft_to, assignment_id FROM plan_duty WHERE shift_no = ? "
                                        "AND purpose = ? AND start_utc >= ? AND start_utc < ? ORDER BY person, start_utc",
                                   k, purpose, s["start_utc"], s["end_utc"]) if d["person"] in ids]
    return ApiResult("ready", _label({"shift": k, "team": team, "teams": teams, "window": s, "people": people, "duties": duties}), purpose=purpose)


# ---------------------------------------------------------------------------------------------- aircraft, supply, capacity


@REGISTRY.operation(
    "sim_aircraft", "GET", "/api/v1/sim/aircraft",
    summary="One aircraft: projection over shifts, open work by station and team, parts, execution",
    description="The aircraft's customer, minor model, station, due date and daily penalty; its projected completion and days late in every "
                "shift's plan; open jobs by station and team; open part shortages; and its tasks done and slipped per shift. Read-only.",
    grants=ALL_READERS, read_only=True, query=LINE_Q, tags=("simulation",),
)
def sim_aircraft(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        line = args.get("line") or _one(con, "SELECT MIN(line) FROM calc_aircraft WHERE shift_no = ?", k)
        facts = _rows(con, "SELECT * FROM calc_aircraft WHERE shift_no = ? AND line = ?", k, line)
        history = _rows(con, "SELECT shift_no, purpose, status, completion_utc, due_utc, late_days, cost_usd FROM plan_aircraft WHERE line = ? "
                             "ORDER BY shift_no", line)
        stations = _rows(con, "SELECT station, team, COUNT(*) AS open_jobs, SUM(work_minutes) AS work_minutes, "
                              "SUM(CASE WHEN shortage_ecd IS NOT NULL THEN 1 ELSE 0 END) AS short FROM calc_job WHERE shift_no = ? AND line = ? "
                              "GROUP BY station, team ORDER BY station, team", k, line)
        shortages = _rows(con, "SELECT soi, station, team, shortage_ecd, earliest_utc, cs_start FROM calc_job WHERE shift_no = ? AND line = ? "
                               "AND shortage_ecd IS NOT NULL ORDER BY shortage_ecd LIMIT 200", k, line)
        execution = _rows(con, "SELECT shift_no, SUM(CASE WHEN outcome = 'completed' THEN 1 ELSE 0 END) AS completed, "
                               "SUM(CASE WHEN outcome = 'slipped' THEN 1 ELSE 0 END) AS slipped FROM exec_assignment WHERE line = ? "
                               "GROUP BY shift_no ORDER BY shift_no", line)
        lines = [r["line"] for r in _rows(con, "SELECT line FROM calc_aircraft WHERE shift_no = ? ORDER BY line", k)]
    return ApiResult("ready", _label({"shift": k, "line": line, "lines": lines, "facts": facts[0] if facts else None, "history": history,
                            "stations": stations, "shortages": shortages, "execution": execution}))


@REGISTRY.operation(
    "sim_supply", "GET", "/api/v1/sim/supply",
    summary="Late parts: open shortages by aircraft, how late they are, arrivals and slips per shift",
    description="Open part shortages per aircraft in the shift's snapshot, the spread of expected dates, part arrivals, slips, undated "
                "findings and datings per shift, and tasks the floor could not start for a missing part. Read-only.",
    grants=ALL_READERS, read_only=True, query=SHIFT_Q, tags=("simulation",),
)
def sim_supply(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        by_aircraft = _rows(con, "SELECT j.line, a.customer, a.minor_model, COUNT(*) AS open_shortages, "
                                 "SUM(CASE WHEN j.shortage_ecd = 'UNDATED' THEN 1 ELSE 0 END) AS undated FROM calc_job j "
                                 "LEFT JOIN calc_aircraft a ON a.shift_no = j.shift_no AND a.line = j.line "
                                 "WHERE j.shift_no = ? AND j.shortage_ecd IS NOT NULL GROUP BY j.line ORDER BY open_shortages DESC", k)
        day = _one(con, "SELECT production_day FROM sim_shift WHERE shift_no = ?", k)
        spread = _rows(con, "SELECT CAST(julianday(shortage_ecd) - julianday(?) AS INTEGER) AS days_out, COUNT(*) AS n FROM calc_job "
                            "WHERE shift_no = ? AND shortage_ecd IS NOT NULL AND shortage_ecd != 'UNDATED' GROUP BY days_out ORDER BY days_out",
                       day, k)
        events = _rows(con, "SELECT shift_no, kind, COUNT(*) AS n FROM exec_event WHERE kind IN ('part_arrived', 'part_slipped', "
                            "'shortage_found_undated', 'shortage_dated') GROUP BY shift_no, kind ORDER BY shift_no")
        blocked = _rows(con, "SELECT shift_no, COUNT(*) AS n FROM exec_assignment WHERE cause = 'part' GROUP BY shift_no ORDER BY shift_no")
    return ApiResult("ready", _label({"shift": k, "by_aircraft": by_aircraft, "spread": spread, "events": events, "blocked": blocked}))


def _load_band(load: float | None) -> str:
    if load is None:
        return "none"
    return next((band for band, above, _ in LOAD_BANDS if above is not None and load > above), "idle")


@REGISTRY.operation(
    "sim_capacity", "GET", "/api/v1/sim/capacity",
    summary="Planned labor against available hours per team and shift for the next three days",
    description="From the shift's published plan: each team's planned labor hours per shift for the next three days against its pool "
                "(members x regular hours), and actual utilization per team in executed shifts. Read-only.",
    grants=ALL_READERS, read_only=True, query=SHIFT_Q, tags=("simulation",),
)
def sim_capacity(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    from ..calendar import ShiftPolicy

    regular = dict(ShiftPolicy.from_policy(svc().policy).regular_minutes)
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        purpose = _dispatch(con, k)
        day, number = con.execute("SELECT production_day, shift_number FROM sim_shift WHERE shift_no = ?", (k,)).fetchone()
        planned = _rows(con, "SELECT team, shift_id, ROUND(SUM((julianday(end_utc) - julianday(start_utc)) * 24 * crew_size), 1) AS hours "
                             "FROM plan_assignment WHERE shift_no = ? AND purpose = ? GROUP BY team, shift_id", k, purpose)
        # the published shift and the rest of the next three production days
        pools = _rows(con, "SELECT day, shift_number, team, members, cap FROM calc_pool WHERE shift_no = ? AND (day > ? OR (day = ? AND shift_number >= ?)) "
                           "AND julianday(day) < julianday(?) + 3", k, day, day, number, day)
        rostered = {r["team"] for r in _rows(con, "SELECT DISTINCT team FROM sim_person WHERE team IS NOT NULL")}
        actual = _rows(con, "SELECT team, ROUND(SUM(actual_hours), 1) AS actual, ROUND(SUM(available_hours), 1) AS available, "
                            "CASE WHEN SUM(available_hours) > 0 THEN ROUND(SUM(actual_hours) / SUM(available_hours), 4) END AS utilization "
                            "FROM perf_team_shift GROUP BY team ORDER BY team")
    hours = {(r["team"], r["shift_id"]): r["hours"] for r in planned}
    cells = []
    for p in pools:
        sid = f"DEMO:{p['day']}:S{p['shift_number']}"
        avail = p["members"] * regular.get(p["shift_number"], 430) / 60
        h = hours.get((p["team"], sid), 0.0) or 0.0
        load = round(h / avail, 3) if avail else None
        cells.append({"team": p["team"], "shift_id": sid, "day": p["day"], "shift_number": p["shift_number"], "planned_hours": h,
                      "available_hours": round(avail, 1), "load": load, "band": _load_band(load), "members": p["members"], "cap": p["cap"]})
    loaded = {c["team"] for c in cells if c["planned_hours"]}
    shown = rostered | loaded
    idle_support = sorted({c["team"] for c in cells} - shown)        # virtual support pools with no planned work in the window
    return ApiResult("ready", _label({"shift": k, "purpose": purpose, "idle_support_pools": idle_support, "bands": [{"band": b, "label": t} for b, _, t in LOAD_BANDS],
                            "cells": sorted((c for c in cells if c["team"] in shown), key=lambda c: (c["team"], c["day"], c["shift_number"])),
                            "actual": actual}), purpose=purpose)


# ---------------------------------------------------------------------------------------------- methods and profile


@REGISTRY.operation(
    "sim_methods", "GET", "/api/v1/sim/methods",
    summary="How the shift's plan was made: data pulled, snapshot admitted, optimizer stages, moves, validation, timings",
    description="Source rows loaded for the shift by table, the admitted snapshot's inventory and dispositions, every optimizer stage with "
                "its time and accepted candidates, accepted search moves, independent validator check counts and violations, the stop "
                "reason, decision digest and each step's wall time. Read-only.",
    grants=ALL_READERS, read_only=True, query=SHIFT_Q, tags=("simulation",),
)
def sim_methods(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        k = _shift(con, args)
        if k is None:
            return _unavailable()
        outdated = _outdated(con)
        if outdated is not None:
            return outdated
        tables = [r["name"] for r in _rows(con, "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'src_%' ORDER BY name")]
        pulled = []
        for t in tables:
            row = con.execute(f'SELECT SUM(CASE WHEN op = \'upsert\' THEN 1 ELSE 0 END), SUM(CASE WHEN op = \'delete\' THEN 1 ELSE 0 END) '
                              f'FROM "{t}" WHERE load_shift = ?', (k,)).fetchone()
            if row[0] or row[1]:
                pulled.append({"table": t[4:], "written": row[0] or 0, "removed": row[1] or 0})
        counts = _rows(con, "SELECT item, value FROM calc_count WHERE shift_no = ? ORDER BY item", k)
        stages = _rows(con, "SELECT purpose, seq, stage, runtime_ms, candidates, accepted, note FROM method_stage WHERE shift_no = ? ORDER BY purpose, seq", k)
        moves = _rows(con, "SELECT purpose, seq, move FROM method_move WHERE shift_no = ? ORDER BY purpose, seq", k)
        checks = _rows(con, "SELECT purpose, check_name, count, violations FROM method_check WHERE shift_no = ? ORDER BY purpose, check_name", k)
        shift = _rows(con, "SELECT * FROM sim_shift WHERE shift_no = ?", k)
    return ApiResult("ready", _label({"shift": k, "pulled": pulled, "counts": counts, "stages": stages, "moves": moves, "checks": checks,
                            "run": shift[0] if shift else None}))


@REGISTRY.operation(
    "sim_profile", "GET", "/api/v1/sim/profile",
    summary="Data profile of the synthetic fleet: table sizes, tasks by station and team, mix, late parts, rework growth",
    description="What the simulation was built from: rows per pulled table, tasks per control station and status, tasks per team, the "
                "customer and minor-model mix, late-part bands, team sizes and open rework per shift. Read-only.",
    grants=ALL_READERS, read_only=True, query=SHIFT_Q, tags=("simulation",),
)
def sim_profile(ctx: RequestContext, args: dict[str, Any]) -> ApiResult:
    _fleet_scope(ctx)
    con = _connect()
    if con is None:
        return _unavailable()
    with con:
        tables = [r["name"] for r in _rows(con, "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'src_%' ORDER BY name")]
        sizes = []
        for t in tables:
            n = _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE load_shift = 0')
            later = _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE load_shift > 0')
            if n or later:
                sizes.append({"table": t[4:], "initial_rows": n, "later_changes": later})
        stations = _rows(con, "SELECT CURRENT_CONTROL_STATION AS station, SHOP_ORDER_STATUS AS status, IS_REWORK AS rework, COUNT(*) AS n "
                              "FROM src_master_task_list WHERE load_shift = 0 GROUP BY station, status, rework ORDER BY station")
        teams = _rows(con, "SELECT TEAM AS team, COUNT(*) AS tasks FROM src_master_task_list WHERE load_shift = 0 GROUP BY team ORDER BY team")
        run = _rows(con, "SELECT facts FROM sim_run")
        facts = json.loads(run[0]["facts"]) if run and run[0]["facts"] else {}
        rework = _rows(con, "SELECT shift_no, SUM(rework_open) AS rework_open, SUM(jobs_open) AS jobs_open, SUM(shortages_open) AS shortages_open "
                            "FROM calc_aircraft GROUP BY shift_no ORDER BY shift_no")
        mix = _rows(con, "SELECT customer, minor_model, COUNT(*) AS aircraft, MAX(daily_penalty_usd) AS daily_penalty_usd FROM calc_aircraft "
                         "WHERE shift_no = 0 GROUP BY customer, minor_model ORDER BY aircraft DESC")
    return ApiResult("ready", _label({"sizes": sizes, "stations": stations, "teams": teams, "facts": facts, "rework": rework, "mix": mix}))
