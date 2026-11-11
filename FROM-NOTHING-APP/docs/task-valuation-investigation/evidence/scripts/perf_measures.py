"""Candidate performance measures per team-shift, from a simulation database (read-only). Investigation only.

One record per team-shift with the components and each candidate measure. Shared by the perf_*.py tests of report 11
(11_PERFORMANCE_MEASURES.md). Every script takes the database paths and the output path as arguments.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

OOS_RATE = 0.10
W = 0.80
MEASURES = ("score", "att_excused", "att_raw", "ppc_count", "throughput", "per_available_hour", "efficiency", "points_sum",
            "points_per_available_hour", "crit_attainment")
LABEL = {"score": "canonical score (excuse-adjusted, extra at 80%)", "att_excused": "plan attainment, excuse-adjusted (minutes)",
         "att_raw": "plan attainment, no excusals", "ppc_count": "planned jobs done, excuse-adjusted (count)",
         "throughput": "throughput: standard minutes completed", "per_available_hour": "throughput per available labor hour (capacity)",
         "efficiency": "earned standard minutes per minute worked", "points_sum": "sum of job points (minutes x criticality)",
         "points_per_available_hour": "job points per available labor hour", "crit_attainment": "criticality-weighted attainment"}


def weights(con) -> dict[tuple[int, int], float]:
    """Criticality (0..1) per shift and line from the stored FORECAST rows: cost of one more day late over the fleet's peak.
    Without FORECAST rows (executable-only runs) every line weighs the same (0)."""
    rate = {(s, line): r for s, line, r in con.execute("select shift_no, line, daily_penalty_usd from calc_aircraft")}
    plan = {(s, line): (st, late) for s, line, st, late in
            con.execute("select shift_no, line, status, late_days from plan_aircraft where purpose='FORECAST'")}
    out: dict[tuple[int, int], float] = {}
    for s in sorted({s for s, _ in rate}):
        cost = {}
        for (ss, line), r in rate.items():
            if ss != s:
                continue
            st, late = plan.get((s, line), ("unknown", None))
            if st == "unknown" or late is None or r is None:
                cost[line] = None
            elif late > 0:
                cost[line] = r
            else:
                cost[line] = r * max(0.0, min(1.0, late + 1.0))
        peak = max((c for c in cost.values() if c), default=0.0)
        for line, c in cost.items():
            out[(s, line)] = 0.0 if c is None or not peak else c / peak
    return out


def load(path: str, with_weights: bool = True) -> list[dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cols = {r[1] for r in con.execute("pragma table_info(perf_team_shift)")}
    w = weights(con) if with_weights else {}
    pts = defaultdict(lambda: [0.0, 0.0, 0.0])                  # earned points, expected points, relieved points (multiplier 1 + w)
    before = defaultdict(lambda: [0, 0.0])                      # units and minutes already done before the shift (in E, not a miss)
    for s, team, line, effort, status, relief in con.execute("select shift_no, team, line, effort, status, relief from perf_unit"):
        m = 1.0 + w.get((s, line), 0.0)
        p = pts[(s, team)]
        p[1] += effort * m
        if status == "EARNED":
            p[0] += effort * m
        elif relief or status == "DONE_BEFORE_SHIFT":
            p[2] += effort * m
        if status == "DONE_BEFORE_SHIFT":
            before[(s, team)][0] += 1
            before[(s, team)][1] += effort
    off = defaultdict(float)
    for s, team, line, effort in con.execute("select shift_no, team, line, effort from exec_offplan"):
        off[(s, team)] += effort * (1.0 + w.get((s, line), 0.0))
    out = []
    q = ("select shift_no, production_day, shift_number, week, team, units, earned, relieved_units, planned_hours, actual_hours, present, "
         "available_hours, B, E, Cp, U, oos_effort, missed_part, missed_absence, missed_predecessor, missed_overrun, missed_deviation "
         "from perf_team_shift order by shift_no, team")
    for (s, day, number, week, team, units, earned, relieved, planned_h, actual_h, present, avail_h, B, E, Cp, U, oos, m_part, m_abs, m_pred,
         m_over, m_dev) in con.execute(q):
        D = B - E
        r = {"shift": s, "day": day, "number": number, "week": week, "team": team, "B": B, "E": E, "D": D, "Cp": Cp, "U": U or 0, "oos": oos or 0,
             "units": units, "earned": earned, "relieved": relieved, "present": present, "available_h": avail_h, "planned_h": planned_h,
             "actual_h": actual_h, "load": (planned_h / avail_h) if avail_h else None}
        earned_pts, expected_pts, relieved_pts = pts[(s, team)]
        r["score"] = None if D <= 0 else max(0.0, min(100.0, 100 * (W * min(1, (Cp + r["U"]) / D) + (1 - W) * min(1, Cp / D)) - 100 * OOS_RATE * r["oos"] / D))
        r["att_excused"] = None if D <= 0 else 100 * min(1.0, Cp / D)
        r["db_units"], r["db_minutes"] = before[(s, team)]
        r["att_raw"] = None if B - r["db_minutes"] <= 0 else 100 * min(1.0, Cp / (B - r["db_minutes"]))    # no excusals; work done earlier is not a miss
        r["ppc_count"] = None if units - relieved - r["db_units"] <= 0 else 100 * earned / (units - relieved - r["db_units"])
        r["throughput"] = float(Cp + r["U"])
        r["per_available_hour"] = None if not avail_h else (Cp + r["U"]) / 60 / avail_h
        r["efficiency"] = None if not actual_h else (Cp + r["U"]) / 60 / actual_h
        r["points_sum"] = earned_pts + off[(s, team)]
        r["points_per_available_hour"] = None if not avail_h else r["points_sum"] / 60 / avail_h
        r["crit_attainment"] = None if expected_pts - relieved_pts <= 0 else 100 * min(1.0, earned_pts / (expected_pts - relieved_pts))
        out.append(r)
    return out


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def pearson(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx, my = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    sxx, syy = sum((a - mx) ** 2 for a, _ in pairs), sum((b - my) ** 2 for _, b in pairs)
    return None if sxx <= 0 or syy <= 0 else sum((a - mx) * (b - my) for a, b in pairs) / (sxx * syy) ** 0.5


def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r, i = [0.0] * len(v), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 3:
        return None
    return pearson(ranks([a for a, _ in pairs]), ranks([b for _, b in pairs]))
