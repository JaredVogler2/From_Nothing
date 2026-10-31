"""Gate recognition, XP, progression streams and badges of a simulation run, derived from its stored rows (S14).

The production projection (``focus.analytics.progression``) reads canonical evaluations and their unit-fact evidence. A
simulation run stores the same facts as rows: frozen goal units with their outcome, decided claims, the freezing plan's
defended assignments and the snapshot's station gates. The same rules are applied to those rows here. Nothing is stored or
re-scored; the canonical score stays in ``perf_team_shift``.

* **Frozen gate context.** An original goal unit is valued on its goal's shift date against its job's station gate in the
  snapshot that froze the day's goals (``calc_job.cs_end`` at ``perf_goal_day.frozen_shift_no``), by
  ``analytics.points.gate_context`` under ``gate.date_basis``. An emergency addition carries no gate context, as in production.
* **XP.** Each earned unit credits effort / ``recognition.base_divisor`` (``completion_credited``). A frozen behind-gate unit
  adds its gate bonus (``gate_recovery``). Offplan output earns nothing, and an out-of-sequence charge stays in the score
  only. Only goals with a policy-status score earn: a NEUTRAL goal (D = 0) earns nothing, as only scored evaluations do.
* **commitment_kept**: every incumbent-defended unit of the goal (committed in the freezing plan) is earned and every miss
  carries approved relief. **excusal_decided**: one event per decided claim, approved or rejected.
* **Badges.** Recovery (a goal with a ``gate_recovery``), CommitmentKeeper (a ``commitment_kept``), SteadyShift (each window of
  ``badges.steady_shift_run`` consecutive positive-goal shifts scored at least ``badges.steady_shift_min_score``) and
  MonthlyChampion (the best pooled score of a month that closed within the run; ties go to the team id).
* **Not derived.** ``dependency_unlocked`` and Firebreak need each shift's admitted graph and lifecycle facts, and SkillSteward
  the frozen membership's certificate holders. The run stores neither, so they are listed as not derived, never as zero.
* **Level** = 1 + floor(XP / ``xp.level_size``). The inflation monitor counts awards per team and ISO week against
  ``xp.inflation_alert_awards_per_team_week``.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from typing import Any

from ..analytics.points import gate_context
from ..performance.score import formula

SCORED = ("DEMONSTRATION", "OFFICIAL")
NOT_DERIVED = {
    "dependency_unlocked": "needs each shift's admitted graph and lifecycle facts, which the run does not store",
    "Firebreak": "awarded from dependency_unlocked, which is not derived",
    "SkillSteward": "needs the frozen membership's certificate holders, which the run does not store",
}


def _q(x: Fraction) -> float:
    return round(float(x), 2)


def derive(con: sqlite3.Connection, policy: Any) -> dict[str, Any]:
    """Team-shift recognition, team XP and levels, stream counts, badges and the inflation monitor of one run."""
    divisor = policy.int("recognition.base_divisor")
    size = policy.int("xp.level_size")
    con.row_factory = sqlite3.Row
    goals = {(r["shift_no"], r["team"]): r for r in con.execute(
        "SELECT shift_no, production_day, shift_number, week, team, B, E, Cp, U, oos_effort, score, status FROM perf_team_shift")}
    frozen = {(r["production_day"], r["team"], r["shift_number"]): r["frozen_shift_no"] for r in con.execute(
        "SELECT frozen_shift_no, production_day, team, shift_number FROM perf_goal_day")}
    gates: dict[int, dict[str, str]] = {}
    for f in sorted(set(frozen.values())):
        gates[f] = {job: end for job, end in con.execute("SELECT job_id, cs_end FROM calc_job WHERE shift_no = ? AND cs_end IS NOT NULL", (f,))}
    committed: dict[int, set[str]] = {f: {a for (a,) in con.execute(
        "SELECT assignment_id FROM plan_assignment WHERE shift_no = ? AND purpose = 'EXECUTABLE' AND committed = 1", (f,))} for f in gates}

    zero = Fraction(0)
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    units_of: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    for u in con.execute("SELECT shift_no, team, assignment_id, job_id, effort, status, relief, origin FROM perf_unit ORDER BY shift_no, team, assignment_id"):
        units_of[(u["shift_no"], u["team"])].append(u)
    streams: Counter[str] = Counter()
    awards: Counter[tuple[str, int, int]] = Counter()              # team, ISO year, ISO week -> awards
    badges: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for key in sorted(goals):
        shift_no, team = key
        g = goals[key]
        day = date.fromisoformat(g["production_day"])
        iso = day.isocalendar()
        f = frozen.get((g["production_day"], team, g["shift_number"]))
        row = rows[key] = {"shift_no": shift_no, "production_day": g["production_day"], "shift_number": g["shift_number"], "week": g["week"],
                           "team": team, "status": g["status"], "earned_units": 0, "base": zero, "bonus": zero, "behind_units": 0,
                           "unavailable_units": 0, "commitment_kept": False}
        if g["status"] not in SCORED:
            skipped[g["status"] or "UNSCORED"] += 1
            continue
        mine = units_of.get(key, [])
        recovered = []
        for u in mine:
            if u["status"] != "EARNED":
                continue
            ctx = None
            if u["origin"] == "ORIGINAL":
                gate = (gates.get(f) or {}).get(u["job_id"]) if f is not None else None
                ctx = gate_context(policy, date.fromisoformat(gate) if gate else None, day, u["effort"])
            base = Fraction(u["effort"], divisor)
            bonus = (ctx.bonus_value or zero) if ctx is not None else zero
            row["earned_units"] += 1
            row["base"] += base
            streams["completion_credited"] += 1
            awards[(team, iso[0], iso[1])] += 1
            if ctx is not None and ctx.basis == "UNAVAILABLE":
                row["unavailable_units"] += 1
            if bonus > 0:
                row["bonus"] += bonus
                row["behind_units"] += 1
                streams["gate_recovery"] += 1
                awards[(team, iso[0], iso[1])] += 1
                recovered.append(u["assignment_id"])
        defended = [u for u in mine if f is not None and u["assignment_id"] in committed.get(f, ())]
        if defended and all(u["status"] == "EARNED" for u in defended) and all(u["relief"] for u in mine if u["status"] == "MISSED"):
            row["commitment_kept"] = True
            streams["commitment_kept"] += 1
            badges.append({"badge": "CommitmentKeeper", "team": team, "shift_no": shift_no, "units": len(defended)})
        if recovered:
            badges.append({"badge": "Recovery", "team": team, "shift_no": shift_no, "units": len(recovered)})
    streams["excusal_decided"] = con.execute("SELECT COUNT(*) FROM perf_claim WHERE decision IN ('APPROVED', 'REJECTED')").fetchone()[0]

    # SteadyShift: each window of consecutive qualifying shifts in the team's own sequence
    need, floor = policy.int("badges.steady_shift_run"), Fraction(policy.str("badges.steady_shift_min_score"))
    by_team: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    for (shift_no, team), g in sorted(goals.items()):
        by_team[team].append((shift_no, g))
    for team, items in sorted(by_team.items()):
        run: list[int] = []
        for shift_no, g in items:
            ok = g["status"] in SCORED and (g["B"] or 0) > 0 and g["score"] is not None and Fraction(str(g["score"])) >= floor
            run = run + [shift_no] if ok else []
            if len(run) >= need:
                badges.append({"badge": "SteadyShift", "team": team, "shift_no": shift_no, "units": None, "window": run[-need:]})

    # MonthlyChampion: a month that closed inside the run (its next first day was reached)
    last_day = max((date.fromisoformat(g["production_day"]) for g in goals.values()), default=None)
    months: dict[tuple[int, int], dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for (shift_no, team), g in goals.items():
        if g["status"] in SCORED and (g["B"] or 0) > 0:
            d = date.fromisoformat(g["production_day"])
            months[(d.year, d.month)][team].append(g)
    rate = Fraction(policy.str("score.oos_rate"))
    for (year, month), teams in sorted(months.items()):
        first_next = date(year + (month == 12), month % 12 + 1, 1)
        if last_day is None or last_day < first_next:
            continue
        ranked = []
        for team, members in sorted(teams.items()):
            total = {k: sum(int(m[k] or 0) for m in members) for k in ("B", "E", "Cp", "U", "oos_effort")}
            result = formula(Fraction(total["B"]), Fraction(total["E"]), Fraction(total["Cp"]), Fraction(total["U"]), rate * total["oos_effort"], policy)
            if result["score"] is not None:
                ranked.append((-Fraction(str(result["score"])), team, len(members)))
        if ranked:
            ranked.sort()
            score, team, n = ranked[0]
            badges.append({"badge": "MonthlyChampion", "team": team, "shift_no": None, "units": None, "period": f"{year}-{month:02d}",
                           "score": float(-score), "goals": n})
    for b in badges:
        streams["badge_earned"] += 1
        d = None if b["shift_no"] is None else date.fromisoformat(goals[(b["shift_no"], b["team"])]["production_day"])
        if d is not None:
            iso = d.isocalendar()
            awards[(b["team"], iso[0], iso[1])] += 1

    teams: dict[str, dict[str, Any]] = {}
    for (shift_no, team), r in sorted(rows.items()):
        t = teams.setdefault(team, {"team": team, "xp": zero, "base": zero, "bonus": zero, "earned_units": 0, "behind_units": 0, "badges": Counter()})
        t["xp"] += r["base"] + r["bonus"]
        t["base"] += r["base"]
        t["bonus"] += r["bonus"]
        t["earned_units"] += r["earned_units"]
        t["behind_units"] += r["behind_units"]
    for b in badges:
        teams.setdefault(b["team"], {"team": b["team"], "xp": zero, "base": zero, "bonus": zero, "earned_units": 0, "behind_units": 0,
                                     "badges": Counter()})["badges"][b["badge"]] += 1
    out_teams = []
    for team, t in sorted(teams.items()):
        whole = t["xp"].numerator // t["xp"].denominator
        level = 1 + whole // size
        out_teams.append({"team": team, "xp": _q(t["xp"]), "base": _q(t["base"]), "gate_bonus": _q(t["bonus"]), "earned_units": t["earned_units"],
                          "behind_units": t["behind_units"], "level": level, "progress_in_level": _q(t["xp"] - (level - 1) * size),
                          "next_threshold": level * size, "badges": dict(sorted(t["badges"].items()))})
    limit = policy.int("xp.inflation_alert_awards_per_team_week")
    monitor = [{"team": team, "iso_year": y, "iso_week": w, "awards": n, "alert": n > limit, "limit": limit}
               for (team, y, w), n in sorted(awards.items())]
    shifts = [{**r, "base": _q(r["base"]), "gate_bonus": _q(r["bonus"]), "xp": _q(r["base"] + r["bonus"])}
              for r in (dict(v) for _, v in sorted(rows.items()))]
    for s in shifts:
        s.pop("bonus")
    return {"policy": {"base_divisor": divisor, "gate_weight": policy.int("gate.weight"), "gate_cap_days": policy.int("gate.cap_days"),
                       "gate_grace_days": policy.int("gate.proxy_grace_days"), "date_basis": policy.str("gate.date_basis"), "level_size": size,
                       "steady_shift_run": need, "steady_shift_min_score": str(floor), "inflation_limit": limit},
            "teams": out_teams, "shifts": shifts, "streams": dict(sorted(streams.items())), "badges": badges, "inflation": monitor,
            "not_derived": NOT_DERIVED, "skipped_goals": dict(skipped)}
