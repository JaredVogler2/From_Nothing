"""Calibrate the extra-work credit rate and the grade bands on scored team-shifts (the owner's re-run on real data).

The extra-work credit is the canonical rule since D-27 (``focus.performance.score``). This tool shows what it does on a
period of scored team-shifts, what other rates would do, and what the rule before D-27 gave ("R0").

    python tools/calibrate_scores.py store --from 2027-01-04 --to 2027-03-28 [--app-root PATH] [--out calibration.json]
    python tools/calibrate_scores.py sim runtime/sim/fleet.db [--out calibration.json]
    ... [--review-sample 60 --review-csv review.csv] [--labels labels.csv] [--lead 20] [--bands A,B,C,D ...]

**What it reads.** Read-only; nothing is written to the store.

* ``store``: what FOCUS keeps for every scored team-shift (D-25): the light performance row (B0, B, E, Cp, U, Uc, O, status)
  and the compact job record (each planned job's minutes and outcome with its claim causes; each unplanned completion).
  While the per-unit evidence is still kept (the owner's 90 days), it also reads each verified completion's time. The
  plans of record come from the accepted plans (D-26).
* ``sim``: the same quantities from a simulation database (``focus.sim``), so real and simulated distributions go
  through the same arithmetic.

**What it measures** (docs/task-valuation-investigation/10_CALIBRATION.md explains each one and what to decide from it):

* today's canonical score, the rule before D-27 (R0: attainment 0.70, plus 0.30 for the share of output that was
  planned) and R5(w) for w = 0.5 ... 1.0, the canonical rule at other rates. The score credits every off-plan minute
  the evaluation counted (Uc);
* the unexcused shortfall s = 1 - Cp/D. Without off-plan work, R5 = 100 (1 - s) minus the out-of-sequence charge
  for every w, so bands are tolerances on s;
* grades under candidate band sets, grade flips between a team's consecutive shifts, and pooled daily grades;
* a swap watch, which changes no score: off-plan minutes done beside planned work that was left undone with no claim
  on record ("possible swap" minutes), how often that happens, and the points those team-shifts gave up against
  doing the missed planned work instead;
* relief by cause, DONE_BEFORE_SHIFT work, completions in the last ``--lead`` minutes of the shift and in overtime, and
  shifts whose plan of record is the previous shift's plan (the run before the shift was not accepted in time);
* fairness: planned minutes against the grade gain from off-plan credit and against the output index;
* scores by shift number (is one shift systematically lower?), and week to week: how much a team's pooled weekly score
  moves from one week to the next, and how much of the difference between weekly scores is between teams. Weekly
  scores that move as much within a team as they differ between teams cannot rank teams.

**Checking the watch.** ``--review-sample N --review-csv FILE`` writes N team-shifts with off-plan work (half flagged
as a possible swap, half not) for supervisors to label ``SWAP`` or ``EXTRA`` in the ``label`` column. ``--labels FILE``
reads that file back and reports how many real swaps the watch missed and how many flags were real extra work.

**Checking the grades.** ``--grade-sample N --grade-csv FILE`` writes N scored team-shifts, spread over the grades,
with the facts of each shift and without its score or grade. A supervisor who knows the shift writes the grade they
would give (A, B, C, D or F) in the ``supervisor_grade`` column. ``--grade-labels FILE`` reads it back and reports,
for every band set, how often the formula's grade matches, how often it is within one grade, and whether the formula
is the stricter or the more lenient of the two. The band set that agrees best is the evidence for DEC-23.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from statistics import mean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
BAND_SETS = {"demo 90/80/70/60": (90, 80, 70, 60), "strict 95/90/85/80": (95, 90, 85, 80),
             "today's effective 85.7/71.4/57.1/42.9": (85.7, 71.4, 57.1, 42.9), "wide 92/85/75/65": (92, 85, 75, 65)}
PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)


# ---------------------------------------------------------------------- one record per team-shift

def team_shift(*, key: str, team: str, day: str, number: int, superintendent: str | None, B0: float, B: float, E: float, Cp: float,
               U: float | None, O: float | None, status: str, no_plan: bool, today: float | None, missed_unclaimed: float,
               excused: dict[str, float], done_before: float, unplanned: list[str], window: tuple[datetime, datetime, datetime] | None,
               completions: list[datetime] | None, Uc: float | None = None) -> dict[str, Any]:
    """``today`` is the canonical score as stored; ``Uc`` the credited extra minutes the canonical evaluation kept."""
    return {"key": key, "team": team, "day": day, "number": number, "superintendent": superintendent, "B0": B0, "B": B, "E": E,
            "D": B - E, "Cp": Cp, "U": U, "Uc": Uc, "O": O, "status": status, "no_plan": no_plan, "today": today,
            "missed_unclaimed": missed_unclaimed,
            "excused": excused, "done_before": done_before, "unplanned": unplanned, "window": window, "completions": completions}


def from_store(services: Any, start: date, end: date, *, with_evidence: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Team-shift records from the store's light rows and job records (and evidence while it is kept)."""
    from focus.performance import history
    from focus.performance import rollup as R
    from focus.performance.lifecycle import shift_window
    from focus.performance.score import formula

    policy = services.policy
    rows = R.rows_between(services, start, end)
    jobs, _ = R.job_records_between(services, start, end)
    by_key = {f"{j['team']}|{j['shift_id']}": j for j in jobs}
    out, notes = [], Counter()
    for row in rows:
        key = f"{row['team']}|{row['shift_id']}"
        job = by_key.get(key)
        missed_unclaimed, excused, done_before, unplanned = 0.0, Counter(), 0.0, []
        if job is None:
            notes["no_job_record"] += 1
        else:
            columns = R.JOB_COLUMNS
            for values in job["jobs"]:
                j = dict(zip(columns, values))
                causes = [c for c in (j["causes"] or "").split("|") if c]
                if j["outcome"] == "missed" and not causes:
                    missed_unclaimed += j["minutes"]
                elif j["outcome"] == "excused":
                    for c in causes or ["UNSPECIFIED"]:
                        excused[c] += j["minutes"] / max(1, len(causes))
                    if "DONE_BEFORE_SHIFT" in causes:
                        done_before += j["minutes"]
            unplanned = [str(dict(zip(R.UNPLANNED_COLUMNS, u))["soi"] or dict(zip(R.UNPLANNED_COLUMNS, u))["job"]) for u in job["unplanned"]]
        today = None
        if row.get("Uc") is not None and row["O"] is not None and row["status"] != "unknown":
            parts = {k: Fraction(row[k]) for k in ("B", "E", "Cp", "Uc", "O")}
            result = formula(parts["B"], parts["E"], parts["Cp"], parts["Uc"], parts["O"], policy)   # today's rule under today's policy
            today = None if result["score"] is None else float(result["score"])
        elif row["status"] != "unknown" and "Uc" not in row:
            notes["evaluated_before_the_extra_work_credit"] += 1
        if row["policy_hash"] != policy.hash:
            notes["evaluated_under_another_policy"] += 1
        win = shift_window(policy, row["shift_id"])
        completions = None
        if with_evidence:
            evaluation = history.get(services, row["evaluation_id"])
            proof = None if evaluation is None else history.evidence(services, evaluation.evidence_hash)
            if proof is None:
                notes["evidence_expired"] += 1
            else:
                completions = [f.completed_at for f in proof.unit_facts if f.status == "EARNED" and f.completed_at is not None]
        out.append(team_shift(key=key, team=row["team"], day=row["day"], number=row["shift_number"], superintendent=row["superintendent"],
                              B0=float(Fraction(row["B0"])), B=float(Fraction(row["B"])), E=float(Fraction(row["E"])),
                              Cp=float(Fraction(row["Cp"])), U=None if row["U"] is None else float(Fraction(row["U"])),
                              O=None if row["O"] is None else float(Fraction(row["O"])), status=row["status"],
                              no_plan=R.NO_PLAN in row["reasons"], today=today, missed_unclaimed=missed_unclaimed, excused=dict(excused),
                              done_before=done_before, unplanned=unplanned, window=(win.start, win.end, win.credit_deadline), completions=completions,
                              Uc=None if row.get("Uc") is None else float(Fraction(row["Uc"]))))
        rec = out[-1]
        if today is not None and rec["D"] > 0 and abs(r5(rec, float(Fraction(policy.str("score.attainment_weight")))) - today) > 0.051:
            notes["tool_differs_from_canonical"] += 1   # e.g. unplanned work started out of sequence by choice, which earns no credit
    return out, {"source": "store", "from": start.isoformat(), "to": end.isoformat(), "notes": dict(notes),
                 "stale_plans": stale_plans(services, sorted({r["shift_id"] for r in rows}))}


def stale_plans(services: Any, shift_ids: list[str]) -> dict[str, Any]:
    """Shifts whose plan of record is the previous shift's plan: the run before the shift was not accepted in time (D-26)."""
    from focus.performance.lifecycle import (
        GoalRegistry,
        containing_shift_id,
        shift_window,
    )

    try:
        registry = GoalRegistry.load(services)
    except Exception as exc:  # noqa: BLE001 - a report, never a failure of the calibration
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    policy, site = services.policy, services.settings.site
    stale, checked, examples = 0, 0, []
    for sid in shift_ids:
        mine = registry.plan_of_record(sid)
        prev = containing_shift_id(policy, shift_window(policy, sid).start - timedelta(minutes=1), site)
        if mine is None or prev is None:
            continue
        theirs = registry.plan_of_record(prev)
        if theirs is None:
            continue
        checked += 1
        if mine == theirs:
            stale += 1
            if len(examples) < 10:
                examples.append(sid)
    return {"status": "ok", "shifts_checked": checked, "stale": stale, "share": round(stale / checked, 4) if checked else None,
            "examples": examples}


def from_sim(path: Path, *, lead: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Team-shift records from a simulation database, with the same rules as the store (``focus.sim``)."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    q = lambda sql, *a: db.execute(sql, a).fetchall()
    shifts = {k: (day, n, datetime.fromisoformat(s), datetime.fromisoformat(e)) for k, day, n, s, e in
              q("SELECT shift_no, production_day, shift_number, start_utc, end_utc FROM sim_shift")}
    units: dict[tuple[int, str], list[tuple]] = defaultdict(list)
    for row in q("SELECT shift_no, team, effort, status, relief, claim_cause, claim_status FROM perf_unit"):
        units[(row[0], row[1])].append(row[2:])
    offplan: dict[tuple[int, str], list[str]] = defaultdict(list)
    for s, t, job in q("SELECT shift_no, team, COALESCE(soi, job_id) FROM exec_offplan"):
        offplan[(s, t)].append(job)
    done: dict[tuple[int, str], list[datetime]] = defaultdict(list)
    for s, t, end in q("SELECT shift_no, team, actual_end FROM exec_assignment WHERE outcome = 'completed'"):
        done[(s, t)].append(datetime.fromisoformat(end))
    out = []
    columns = {c[1] for c in q("PRAGMA table_info(perf_team_shift)")}
    missing = sorted({"B0", "B", "E", "Cp", "U", "O", "status", "score"} - columns)
    if missing:
        raise SystemExit(f"{path}: this simulation database was made by an older FOCUS (perf_team_shift has no {', '.join(missing)}); "
                         "run the simulation again")
    has_uc = "Uc" in columns                                                       # absent in a database made before D-27
    for s, team, B0, B, E, Cp, U, O, status, score, Uc in q(
            f"SELECT shift_no, team, B0, B, E, Cp, U, O, status, score, {'Uc' if has_uc else 'NULL'} FROM perf_team_shift ORDER BY shift_no, team"):
        missed_unclaimed, excused, done_before = 0.0, Counter(), 0.0
        for effort, st, relief, cause, claim_status in units[(s, team)]:
            if st == "MISSED" and not relief and not cause:
                missed_unclaimed += effort
            elif st == "MISSED" and relief:
                excused[cause or "UNSPECIFIED"] += effort
            elif st == "DONE_BEFORE_SHIFT":
                excused["DONE_BEFORE_SHIFT"] += effort
                done_before += effort
        day, number, start, end = shifts[s]
        out.append(team_shift(key=f"{team}|{s}", team=team, day=day, number=number, superintendent=None, B0=B0, B=B, E=E, Cp=Cp, U=U,
                              O=O or 0.0, status=status or "", no_plan=False, today=score, missed_unclaimed=missed_unclaimed,
                              excused=dict(excused), done_before=done_before, unplanned=offplan[(s, team)],
                              window=(start, end, end), completions=done[(s, team)], Uc=Uc))
    late_plans = q("SELECT COUNT(*) FROM exec_event WHERE kind = 'plan_accepted_late'")[0][0] if q(
        "SELECT name FROM sqlite_master WHERE name = 'exec_event'") else None
    return out, {"source": "sim", "path": str(path), "lead": lead, "notes": {}, "stale_plans": {"status": "sim", "late_plans": late_plans}}


# ---------------------------------------------------------------------- the rules

def clamp(x: float) -> float:
    return min(100.0, max(0.0, x))


def classify(r: dict[str, Any]) -> tuple[float, float]:
    """(clear extra, possible swap) off-plan minutes, for the swap watch only. Possible-swap minutes are the off-plan
    minutes done beside planned work left undone with no claim on record, minute for minute. The score credits both
    (D-27): a missed planned job costs points on its own."""
    u = r["U"] or 0.0
    swap = min(u, max(0.0, r["missed_unclaimed"]))
    return u - swap, swap


def credited(r: dict[str, Any]) -> float:
    """The off-plan minutes the score credits: the evaluation's Uc where it was kept, else all of U."""
    return r["Uc"] if r.get("Uc") is not None else (r["U"] or 0.0)


def before_d27(r: dict[str, Any]) -> float | None:
    """R0, the rule before D-27: 100 (0.7 min(1, Cp/D) + 0.3 Cp/(Cp + U)) - 100 O/D. Off-plan work lowered it."""
    if r["D"] <= 0:
        return None
    out = r["Cp"] + (r["U"] or 0.0)
    return clamp(100 * (0.7 * min(1, r["Cp"] / r["D"]) + 0.3 * (r["Cp"] / out if out > 0 else 0.0)) - 100 * (r["O"] or 0.0) / r["D"])


def r5(r: dict[str, Any], w: float) -> float | None:
    """The canonical rule at the rate w: 100 (w min(1, (Cp + credited)/D) + (1 - w) min(1, Cp/D)) - 100 O/D."""
    if r["D"] <= 0:
        return None
    return clamp(100 * (w * min(1, (r["Cp"] + credited(r)) / r["D"]) + (1 - w) * min(1, r["Cp"] / r["D"])) - 100 * (r["O"] or 0.0) / r["D"])


def grade(score: float, bands: tuple[float, ...]) -> str:
    for g, lo in zip("ABCD", bands):
        if score >= lo - 1e-9:
            return g
    return "F"


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    v = sorted(values)
    return {f"p{p}": round(v[min(len(v) - 1, max(0, round(p / 100 * (len(v) - 1))))], 4) for p in PERCENTILES}


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None

    def ranks(x: list[float]) -> list[float]:
        order = sorted(range(len(x)), key=lambda i: x[i])
        out = [0.0] * len(x)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return None if den == 0 else round(num / den, 4)


def pooled(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Components summed first, then the rule once (the rollup rule, D-25)."""
    tot = Counter()
    for r in rows:
        for k in ("D", "Cp", "O", "U"):
            tot[k] += r[k] or 0.0
        tot["Uc"] += credited(r)
    return {"D": tot["D"], "Cp": tot["Cp"], "O": tot["O"], "U": tot["U"], "Uc": tot["Uc"], "missed_unclaimed": 0.0}


# ---------------------------------------------------------------------- the analysis

def analyze(records: list[dict[str, Any]], *, lead: int, bands: dict[str, tuple[float, ...]]) -> dict[str, Any]:
    scored = [r for r in records if r["D"] > 0 and r["U"] is not None and r["O"] is not None and r["status"] not in ("unknown", "UNKNOWN")]
    res: dict[str, Any] = {"coverage": {
        "team_shifts": len(records), "scored": len(scored), "no_plan": sum(r["no_plan"] for r in records),
        "all_excused_or_no_obligation": sum(r["D"] <= 0 for r in records),
        "unknown": sum(r["status"] in ("unknown", "UNKNOWN") or r["U"] is None or r["O"] is None for r in records),
        "teams": len({r["team"] for r in records}), "days": len({r["day"] for r in records})}}
    if not scored:
        res["note"] = "no scored team-shift with D > 0 in the period"
        return res

    # scores and the shortfall
    shortfall = [1 - r["Cp"] / r["D"] for r in scored]
    res["shortfall"] = {"definition": "s = 1 - Cp/D, unexcused planned work not done", "percentiles": percentiles(shortfall),
                        "mean": round(mean(shortfall), 4), "share_zero": round(sum(s <= 1e-9 for s in shortfall) / len(shortfall), 4)}
    for r in scored:
        r["r0"] = before_d27(r)
    res["scores"] = {"canonical_today": percentiles([r["today"] for r in scored if r["today"] is not None]),
                     "R0_before_D27": percentiles([r["r0"] for r in scored])}
    for w in WS:
        res["scores"][f"R5_w{w}"] = percentiles([r5(r, w) for r in scored])
    res["output_index"] = percentiles([(r["Cp"] + credited(r)) / r["D"] for r in scored])

    # grades, flips, daily pooled
    grades: dict[str, Any] = {}
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        by_team[r["team"]].append(r)
    for seq in by_team.values():
        seq.sort(key=lambda r: (r["day"], r["number"]))
    for label, b in bands.items():
        g0 = Counter(grade(r["r0"], b) for r in scored)
        g5 = Counter(grade(r5(r, 0.8), b) for r in scored)
        flips = n = 0
        for seq in by_team.values():
            for x, y in pairwise(seq):
                flips += grade(r5(x, 0.8), b) != grade(r5(y, 0.8), b)
                n += 1
        changed = Counter()
        for r in scored:
            a, c = grade(r["r0"], b), grade(r5(r, 0.8), b)
            changed["up" if c < a else "down" if c > a else "same"] += 1
        grades[label] = {"R0": dict(sorted(g0.items())), "R5_w0.8": dict(sorted(g5.items())),
                         "flip_rate_consecutive_shifts_R5_w0.8": round(flips / n, 4) if n else None,
                         "R0_to_R5_w0.8_grade_change": dict(changed)}
    res["grades"] = grades
    daily: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        daily[(r["team"], r["day"])].append(r)
    dsc = []
    for rows in daily.values():
        p = pooled(rows)
        if p["D"] > 0:
            dsc.append(r5(p, 0.8))
    res["daily_pooled_R5_w0.8"] = {"team_days": len(dsc), "percentiles": percentiles(dsc)}

    # off-plan work, and the swap watch (information only: the score credits every counted off-plan minute)
    with_u = [r for r in scored if (r["U"] or 0) > 0]
    total_u = sum(r["U"] or 0.0 for r in scored)
    swap_min = sum(classify(r)[1] for r in scored)
    swaps = [r for r in scored if classify(r)[1] > 0]
    res["offplan"] = {"team_shifts_with_offplan": len(with_u), "share": round(len(with_u) / len(scored), 4),
                      "U_over_D_when_present": percentiles([r["U"] / r["D"] for r in with_u]),
                      "offplan_minutes": total_u, "credited_minutes": sum(credited(r) for r in scored),
                      "possible_swap_minutes": swap_min,
                      "possible_swap_share_of_offplan": round(swap_min / total_u, 4) if total_u else None,
                      "team_shifts_with_possible_swap": len(swaps)}
    watch: dict[str, Any] = {"definition": "off-plan minutes done beside planned work left undone with no claim on record",
                             "team_shifts": len(swaps), "share_of_scored": round(len(swaps) / len(scored), 4)}
    for w in WS:
        # what each of those team-shifts gave up against doing the missed planned minutes instead of the same off-plan minutes
        cost = [r5({**r, "Cp": r["Cp"] + classify(r)[1], "U": (r["U"] or 0.0) - classify(r)[1], "Uc": max(0.0, credited(r) - classify(r)[1])}, w)
                - r5(r, w) for r in swaps]
        if cost:
            watch[f"points_given_up_w{w}"] = {"mean": round(mean(cost), 3), "max": round(max(cost), 3),
                                              "share_where_the_swap_paid": round(sum(x < -1e-9 for x in cost) / len(cost), 4)}
    res["swap_watch"] = watch

    # relief and the shift boundary
    B = sum(r["B"] for r in records)
    by_cause = Counter()
    for r in records:
        for c, m in r["excused"].items():
            by_cause[c] += m
    res["relief"] = {"B_minutes": B, "excused_minutes_by_cause": {c: round(m, 1) for c, m in by_cause.most_common()},
                     "excused_share_of_B": round(sum(by_cause.values()) / B, 4) if B else None,
                     "done_before_shift_minutes": sum(r["done_before"] for r in records),
                     "done_before_shift_share_of_B": round(sum(r["done_before"] for r in records) / B, 4) if B else None}
    late = ot = wall = 0
    have = [r for r in records if r["completions"] is not None and r["window"] is not None]
    for r in have:
        end = r["window"][1]
        for t in r["completions"]:
            if t >= end:
                ot += 1
            elif t >= end - timedelta(minutes=lead):
                late += 1
            else:
                wall += 1
    total = late + ot + wall
    res["boundary"] = {"lead_minutes": lead, "team_shifts_with_times": len(have), "completions": total,
                       "share_in_last_lead_minutes": round(late / total, 4) if total else None,
                       "share_in_overtime": round(ot / total, 4) if total else None,
                       "note": "completions after the next plan's data cut reach that plan as DONE_BEFORE_SHIFT relief (D-26)"}

    # fairness: does off-plan credit follow how much was planned?
    planned = [r["D"] for r in scored]
    gain08 = [r5(r, 0.8) - clamp(100 * min(1, r["Cp"] / r["D"]) - 100 * (r["O"] or 0) / r["D"]) for r in scored]
    res["fairness"] = {"spearman_planned_minutes_vs_R5_gain": spearman(planned, gain08),
                       "spearman_planned_minutes_vs_output_index": spearman(planned, [(r["Cp"] + credited(r)) / r["D"] for r in scored]),
                       "note": "planned minutes stand in for load; join available hours from attendance for the load itself"}
    res["by_shift_number"] = by_shift_number(scored)
    res["weekly"] = weekly(scored)
    return res


def by_shift_number(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Scores by shift number. A shift that is lower every week points at its conditions (support, parts, staffing), not at
    its teams; compare the excused share before reading the scores."""
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        groups[r["number"]].append(r)
    out: dict[str, Any] = {}
    for number, rows in sorted(groups.items()):
        scores = [r5(r, 0.8) for r in rows]
        expected = sum(r["B"] for r in rows)
        out[str(number)] = {"team_shifts": len(rows), "mean_score": round(mean(scores), 2), "median_score": percentiles(scores)["p50"],
                            "mean_shortfall": round(mean(1 - r["Cp"] / r["D"] for r in rows), 4),
                            "excused_share_of_B": round(sum(r["E"] for r in rows) / expected, 4) if expected else None}
    means = [v["mean_score"] for v in out.values()]
    return {"shifts": out, "spread_of_means": round(max(means) - min(means), 2) if means else None}


def weekly(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Each team's pooled score per ISO week: how far it moves between consecutive weeks, and how much of the spread of
    weekly scores lies between teams (1 = teams differ and each is steady; near 0 = the weeks differ, the teams do not)."""
    groups: dict[tuple[str, tuple[int, int]], list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        iso = date.fromisoformat(r["day"]).isocalendar()
        groups[(r["team"], (iso[0], iso[1]))].append(r)
    by_team: dict[str, list[tuple[tuple[int, int], float]]] = defaultdict(list)
    for (team, week), rows in groups.items():
        p = pooled(rows)
        if p["D"] > 0:
            by_team[team].append((week, r5(p, 0.8)))
    values = [score for seq in by_team.values() for _, score in seq]
    weeks = {week for seq in by_team.values() for week, _ in seq}
    if len(weeks) < 2 or not values:
        return {"team_weeks": len(values), "weeks": len(weeks), "note": "needs at least two weeks of scores"}
    moves = []
    for seq in by_team.values():
        seq.sort()
        moves.extend(abs(b[1] - a[1]) for a, b in pairwise(seq))
    overall = mean(values)
    total = sum((v - overall) ** 2 for v in values)
    between = sum(len(seq) * (mean(v for _, v in seq) - overall) ** 2 for seq in by_team.values())
    ordered = sorted(moves)
    return {"team_weeks": len(values), "weeks": len(weeks), "teams": len(by_team), "weekly_score": percentiles(values),
            "change_between_consecutive_weeks": {"mean": round(mean(moves), 3), "p90": round(ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))], 3),
                                                 "max": round(ordered[-1], 3)} if moves else None,
            "between_team_share_of_variance": round(between / total, 4) if total > 0 else None}


# ---------------------------------------------------------------------- review sample and labels

def review_sample(records: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    with_u = [r for r in records if r["D"] > 0 and (r["U"] or 0) > 0]
    sub = [r for r in with_u if classify(r)[1] > 0]
    add = [r for r in with_u if classify(r)[1] == 0]
    pick = rng.sample(sub, min(len(sub), n // 2)) + rng.sample(add, min(len(add), n - min(len(sub), n // 2)))
    return [{"team_shift": r["key"], "team": r["team"], "day": r["day"], "shift_number": r["number"], "planned_minutes": r["D"],
             "done_planned_minutes": r["Cp"], "missed_unclaimed_minutes": r["missed_unclaimed"], "offplan_minutes": r["U"],
             "offplan_jobs": " ".join(r["unplanned"][:20]), "watch": "POSSIBLE_SWAP" if classify(r)[1] > 0 else "EXTRA_ONLY", "label": ""}
            for r in sorted(pick, key=lambda r: r["key"])]


def label_rates(path: Path) -> dict[str, Any]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    labeled = [r for r in rows if r.get("label", "").strip().upper() in ("SWAP", "EXTRA")]
    called_add = [r for r in labeled if r["watch"] == "EXTRA_ONLY"]
    called_sub = [r for r in labeled if r["watch"] == "POSSIBLE_SWAP"]
    misses = sum(r["label"].strip().upper() == "SWAP" for r in called_add)
    alarms = sum(r["label"].strip().upper() == "EXTRA" for r in called_sub)
    swaps = sum(r["label"].strip().upper() == "SWAP" for r in labeled)
    return {"labeled": len(labeled), "unlabeled": len(rows) - len(labeled),
            "miss_rate": round(misses / swaps, 4) if swaps else None,
            "swaps_not_flagged": misses, "swaps_labeled": swaps,
            "false_alarm_rate": round(alarms / len(called_sub), 4) if called_sub else None,
            "note": "the miss rate is the share of real swaps the watch did not flag; with a stratified sample, weight by each "
                    "class's population share before quoting a site-wide rate"}


# ---------------------------------------------------------------------- blind grade sample

GRADES = "ABCDF"


def _scored(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["D"] > 0 and r["U"] is not None and r["O"] is not None and r["status"] not in ("unknown", "UNKNOWN")]


def grade_sample(records: list[dict[str, Any]], n: int, seed: int, bands: tuple[float, ...]) -> list[dict[str, Any]]:
    """N scored team-shifts, as evenly over the five grades as the period allows, in random order and without the score
    or the grade. The facts are what a supervisor needs to judge the shift."""
    rng = random.Random(seed)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sorted(_scored(records), key=lambda r: r["key"]):
        groups[grade(r5(r, 0.8), bands)].append(r)
    pick: list[dict[str, Any]] = []
    for g in GRADES:
        pick += rng.sample(groups[g], min(len(groups[g]), n // len(GRADES)))
    chosen = {r["key"] for r in pick}
    rest = [r for g in GRADES for r in groups[g] if r["key"] not in chosen]
    pick += rng.sample(rest, min(len(rest), max(0, n - len(pick))))
    rng.shuffle(pick)
    return [{"team_shift": r["key"], "team": r["team"], "day": r["day"], "shift_number": r["number"],
             "expected_minutes": round(r["D"], 1), "planned_minutes_done": round(r["Cp"], 1),
             "planned_minutes_missed_not_excused": round(max(0.0, r["D"] - r["Cp"]), 1),
             "of_which_no_reason_on_record": round(r["missed_unclaimed"], 1), "excused_minutes": round(r["E"], 1),
             "excused_causes": "; ".join(f"{c}={round(m, 1)}" for c, m in sorted(r["excused"].items())),
             "extra_minutes": round(r["U"] or 0.0, 1), "out_of_sequence_minutes": round(r["O"] or 0.0, 1), "supervisor_grade": ""} for r in pick]


def grade_agreement(path: Path, records: list[dict[str, Any]], bands: dict[str, tuple[float, ...]]) -> dict[str, Any]:
    """Supervisors' grades against the formula's, for every band set. Stricter means the formula gave the lower grade."""
    by_key = {r["key"]: r for r in _scored(records)}
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    labeled = [(by_key[row["team_shift"]], row["supervisor_grade"].strip().upper()) for row in rows
               if row.get("team_shift") in by_key and row.get("supervisor_grade", "").strip().upper() in tuple(GRADES)]
    out: dict[str, Any] = {"labeled": len(labeled), "unlabeled_or_unmatched": len(rows) - len(labeled), "band_sets": {}}
    for label, b in bands.items():
        diff = [GRADES.index(grade(r5(r, 0.8), b)) - GRADES.index(given) for r, given in labeled]
        if diff:
            out["band_sets"][label] = {"same_grade": round(sum(d == 0 for d in diff) / len(diff), 4),
                                       "within_one_grade": round(sum(abs(d) <= 1 for d in diff) / len(diff), 4),
                                       "formula_stricter": sum(d > 0 for d in diff), "formula_more_lenient": sum(d < 0 for d in diff)}
    if out["band_sets"]:
        out["best_agreement"] = max(out["band_sets"], key=lambda k: (out["band_sets"][k]["same_grade"], out["band_sets"][k]["within_one_grade"]))
    out["note"] = "a few dozen labels show a direction, not a rate; label at least 20 shifts per grade before changing the bands"
    return out


# ---------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="source", required=True)
    s = sub.add_parser("store", help="the FOCUS store of this environment (fixture or live)")
    s.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    s.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    s.add_argument("--app-root", default=None)
    s.add_argument("--no-evidence", action="store_true", help="skip completion times (faster; the boundary section stays empty)")
    m = sub.add_parser("sim", help="a simulation database (focus.sim)")
    m.add_argument("db", type=Path)
    for p in (s, m):
        p.add_argument("--out", type=Path, default=None)
        p.add_argument("--lead", type=int, default=20, help="minutes before the shift end that the next plan's data is cut")
        p.add_argument("--bands", action="append", default=[], help="an extra band set as A,B,C,D lower bounds, e.g. 98,95,90,80")
        p.add_argument("--review-sample", type=int, default=0)
        p.add_argument("--review-csv", type=Path, default=None)
        p.add_argument("--labels", type=Path, default=None)
        p.add_argument("--grade-sample", type=int, default=0, help="team-shifts to hand a supervisor, without scores or grades")
        p.add_argument("--grade-csv", type=Path, default=None)
        p.add_argument("--grade-bands", default="demo 90/80/70/60", help="the band set the grade sample is spread over")
        p.add_argument("--grade-labels", type=Path, default=None, help="the grade sample, filled in, to compare with the formula")
        p.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args(argv)
    if args.source == "store":
        from focus.config import Settings
        from focus.runtime import build_services

        services = build_services(Settings.from_env(**({"app_root": Path(args.app_root)} if args.app_root else {})))
        records, meta = from_store(services, args.start, args.end, with_evidence=not args.no_evidence)
    else:
        records, meta = from_sim(args.db, lead=args.lead)
    bands = dict(BAND_SETS)
    for text in args.bands:
        bounds = tuple(float(x) for x in text.split(","))
        if len(bounds) != 4 or list(bounds) != sorted(bounds, reverse=True):
            ap.error(f"--bands {text}: four descending lower bounds for A, B, C and D")
        bands[f"custom {text}"] = bounds
    result = {"schema": 1, "meta": meta, **analyze(records, lead=args.lead, bands=bands)}
    if args.review_sample and args.review_csv:
        sample = review_sample(records, args.review_sample, args.seed)
        with args.review_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(sample[0]) if sample else ["team_shift", "label"])
            writer.writeheader()
            writer.writerows(sample)
        result["review_sample"] = {"rows": len(sample), "csv": str(args.review_csv)}
    if args.labels:
        result["labels"] = label_rates(args.labels)
    if args.grade_sample and args.grade_csv:
        if args.grade_bands not in bands:
            ap.error(f"--grade-bands {args.grade_bands!r}: one of {sorted(bands)}")
        graded = grade_sample(records, args.grade_sample, args.seed, bands[args.grade_bands])
        with args.grade_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(graded[0]) if graded else ["team_shift", "supervisor_grade"])
            writer.writeheader()
            writer.writerows(graded)
        result["grade_sample"] = {"rows": len(graded), "csv": str(args.grade_csv), "spread_over": args.grade_bands}
    if args.grade_labels:
        result["grade_agreement"] = grade_agreement(args.grade_labels, records, bands)
    text = json.dumps(result, indent=1, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["coverage"]["scored"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
