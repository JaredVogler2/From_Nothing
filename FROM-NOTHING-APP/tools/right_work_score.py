"""The proposed right-work score beside today's score (report 12; DECISION_PLAYBOOK.md, DEC-33). Read-only.

    points of a planned job = its standard person-minutes x the multiplier of its tier
    expected = the points of the planned jobs that were not excused
    done     = the points of the planned jobs verified complete
    left     = expected - done                    (planned work left without an excuse)
    score    = 100 x (done + credit x min(off-plan minutes, left)) / expected - the out-of-sequence charge

Three things make it differ from a count of jobs:

* **effort, not count:** a job counts by its standard person-minutes, so a long job with three people is not worth
  the same as a short one with one;
* **priority:** a job that other planned work waits on (``MUST``, ``focus.valuation.priority``) can count for more
  than a job nothing depends on (``FILL``);
* **the right work first:** work done in place of planned work that was left without an excuse earns the credit rate
  of that work, never all of it. It is still worth more than doing nothing.

With every multiplier 1 and the credit equal to the canonical attainment weight it is the canonical score, exactly:
the canonical rule is the special case with no priorities and a credit of 80%.

**It is a proposal, not a second formula.** FOCUS has one score formula, ``focus/performance/score.py``, and no grade,
record or view reads this tool. If the owner adopts the proposal, that formula changes and this tool goes.

Usage:

    python tools/right_work_score.py example [--credit 0.3] [--tiers 2,1,0.5]
        the owner's ten-job example and the count example, at today's rule and at the given settings

    python tools/right_work_score.py sim RUN.db [--credit 0.3] [--tiers 2,1,0.5] [--json]
        every team of a simulation run (focus.sim): today's stored score and the proposed score (each the mean
        over the team's shifts) and the must-do work done (its minutes over the run, summed before dividing). It
        also counts the team-shifts where the proposal at today's settings equals the stored score: all of them,
        or the run and this tool disagree.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from focus.performance.score import round_half_up  # noqa: E402
from focus.policy import load_policy  # noqa: E402
from focus.valuation.priority import FILL, MUST, PLAN, TIERS  # noqa: E402

EARNED, MISSED, EXCUSED = "EARNED", "MISSED", "EXCUSED"
FLAT = {MUST: Fraction(1), PLAN: Fraction(1), FILL: Fraction(1)}
PROPOSED_CREDIT, PROPOSED_TIERS = "0.3", "2,1,0.5"


@dataclass(frozen=True, slots=True)
class PlannedJob:
    effort: Fraction            # standard person-minutes
    tier: str                   # MUST | PLAN | FILL
    status: str                 # EARNED | MISSED | EXCUSED (approved relief, or already done before the shift)


def right_work(jobs: Iterable[PlannedJob], offplan_minutes: Fraction, oos_charge_minutes: Fraction = Fraction(0), *, rho: Fraction,
               multipliers: Mapping[str, Fraction] = FLAT, decimals: int = 1) -> dict[str, Any]:
    """One team-shift (or any pooled period: pass all its jobs and the summed off-plan minutes).

    ``rho`` is the credit rate. ``oos_charge_minutes`` is the canonical out-of-sequence charge O (the rate times the
    charged effort); it is taken off against the expected minutes, as the canonical score does. ``score`` is None when
    nothing was expected."""
    jobs = list(jobs)
    bad = sorted({j.tier for j in jobs} - set(TIERS)) or sorted({j.status for j in jobs} - {EARNED, MISSED, EXCUSED})
    if bad:
        raise ValueError(f"unknown tier or status: {bad}")
    live = [j for j in jobs if j.status != EXCUSED]
    points = lambda rows: sum((Fraction(j.effort) * Fraction(multipliers[j.tier]) for j in rows), Fraction(0))      # noqa: E731
    minutes = lambda rows: sum((Fraction(j.effort) for j in rows), Fraction(0))                                     # noqa: E731
    expected, done = points(live), points([j for j in live if j.status == EARNED])
    by_tier = {t: {"planned": sum(1 for j in jobs if j.tier == t), "excused": sum(1 for j in jobs if j.tier == t and j.status == EXCUSED),
                   "done": sum(1 for j in jobs if j.tier == t and j.status == EARNED),
                   "left": sum(1 for j in jobs if j.tier == t and j.status == MISSED)} for t in TIERS}
    out = {"expected_points": expected, "done_points": done, "left_points": expected - done, "expected_minutes": minutes(live),
           "offplan_minutes": Fraction(offplan_minutes), "by_tier": by_tier, "credit_points": None, "final": None, "score": None}
    if expected <= 0:
        return out
    credit = Fraction(rho) * min(Fraction(offplan_minutes), expected - done)
    charge = Fraction(100) * Fraction(oos_charge_minutes) / out["expected_minutes"]
    final = min(Fraction(100), max(Fraction(0), Fraction(100) * (done + credit) / expected - charge))
    out.update(credit_points=credit, final=final, score=round_half_up(final, decimals))
    return out


def tiers_of(text: str) -> dict[str, Fraction]:
    """``"2,1,0.5"`` -> the multipliers of MUST, PLAN and FILL."""
    parts = [Fraction(p.strip()) for p in text.split(",")]
    if len(parts) != 3 or min(parts) <= 0:
        raise ValueError("--tiers takes three positive numbers: MUST,PLAN,FILL")
    return dict(zip((MUST, PLAN, FILL), parts))


def canonical_rate(app_root: Path = ROOT) -> Fraction:
    """Today's credit: the canonical attainment weight of the policy in force."""
    return Fraction(load_policy(Path(app_root) / "config" / "policies").str("score.attainment_weight"))


# ---------------------------------------------------------------------- the worked examples

def _jobs(done: int, left: int, excused: int = 0, *, tier: str = PLAN, minutes: int = 60) -> list[PlannedJob]:
    return ([PlannedJob(Fraction(minutes), tier, EARNED)] * done + [PlannedJob(Fraction(minutes), tier, MISSED)] * left
            + [PlannedJob(Fraction(minutes), tier, EXCUSED)] * excused)


def examples(rho: Fraction, multipliers: Mapping[str, Fraction], today: Fraction) -> list[tuple[str, Decimal | None, Decimal | None]]:
    """(case, today's score, the proposed score). Ten one-hour jobs with eight done and four other jobs done, then the
    count example: two long jobs (4 hours, 2 people) left, eight short ones done, six other short jobs done."""
    eight, long_left = _jobs(8, 0), lambda tier: _jobs(0, 2, tier=tier, minutes=480) + _jobs(8, 0)         # noqa: E731
    cases = [("ten jobs: the two left were excused", _jobs(8, 0, 2), 240),
             ("ten jobs: the two left were ordinary planned jobs", _jobs(8, 2), 240),
             ("ten jobs: the two left were must-do jobs", eight + _jobs(0, 2, tier=MUST), 240),
             ("ten jobs: the two left were fill jobs", eight + _jobs(0, 2, tier=FILL), 240),
             ("ten jobs: the two left were must-do jobs, no other work done", eight + _jobs(0, 2, tier=MUST), 0),
             ("count example (14 jobs done against 10 planned): the long jobs were ordinary", long_left(PLAN), 360),
             ("count example: the long jobs were must-do jobs", long_left(MUST), 360)]
    return [(name, right_work(jobs, Fraction(extra), rho=today)["score"],
             right_work(jobs, Fraction(extra), rho=rho, multipliers=multipliers)["score"]) for name, jobs, extra in cases]


# ---------------------------------------------------------------------- a simulation run

def score_run(db_path: Path, rho: Fraction, multipliers: Mapping[str, Fraction], today: Fraction) -> dict[str, Any]:
    """Every team of a ``focus.sim`` run under the proposal, beside its stored canonical score."""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        tables = {name for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"exec_priority", "perf_unit", "perf_team_shift"} <= tables:
            raise ValueError("not a simulation run with priority facts (exec_priority): run the simulation again with this version")
        tier = {(s, a): t for s, a, t in con.execute("SELECT shift_no, assignment_id, tier FROM exec_priority")}
        jobs: dict[tuple, list[PlannedJob]] = defaultdict(list)
        for s, team, a, effort, status, relief in con.execute("SELECT shift_no, team, assignment_id, effort, status, relief FROM perf_unit"):
            state = EXCUSED if relief or status == "DONE_BEFORE_SHIFT" else EARNED if status == "EARNED" else MISSED
            jobs[(s, team)].append(PlannedJob(Fraction(effort), tier.get((s, a), PLAN), state))
        shifts = con.execute("SELECT shift_no, team, Uc, O, score FROM perf_team_shift ORDER BY team, shift_no").fetchall()
    finally:
        con.close()
    teams: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(float))
    scored = same = 0
    for s, team, extra, charge, stored in shifts:
        mine = jobs.get((s, team), [])
        args = (mine, Fraction(extra or 0), Fraction(str(charge or 0)))
        proposed = right_work(*args, rho=rho, multipliers=multipliers)
        if stored is None or proposed["score"] is None:
            continue
        scored += 1
        same += right_work(*args, rho=today)["score"] == Decimal(str(stored))
        t = teams[team]
        t["team_shifts"] += 1
        t["today"] += stored
        t["proposed"] += float(proposed["score"])
        must = [j for j in mine if j.tier == MUST and j.status != EXCUSED]
        t["must_do_minutes"] += float(sum(j.effort for j in must))
        t["must_do_done_minutes"] += float(sum(j.effort for j in must if j.status == EARNED))
    share = lambda t: round(100 * t["must_do_done_minutes"] / t["must_do_minutes"], 1) if t["must_do_minutes"] else None    # noqa: E731
    out_teams = {team: {"team_shifts": int(t["team_shifts"]), "todays_score": round(t["today"] / t["team_shifts"], 1),
                        "proposed_score": round(t["proposed"] / t["team_shifts"], 1), "must_do_work_done_percent": share(t)}
                 for team, t in sorted(teams.items())}
    total = {k: sum(t[k] for t in teams.values()) for k in ("team_shifts", "today", "proposed", "must_do_minutes", "must_do_done_minutes")}
    return {"credit": str(rho), "multipliers": {k: str(v) for k, v in multipliers.items()}, "todays_credit": str(today),
            "team_shifts_scored": scored, "same_as_the_stored_score_at_todays_settings": same,
            "all_teams": {"todays_score": round(total["today"] / scored, 1) if scored else None,
                          "proposed_score": round(total["proposed"] / scored, 1) if scored else None,
                          "must_do_work_done_percent": share(total)},
            "teams": out_teams}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="source", required=True)
    e = sub.add_parser("example", help="the worked examples of report 12")
    m = sub.add_parser("sim", help="a simulation database (focus.sim)")
    m.add_argument("db", type=Path)
    m.add_argument("--json", action="store_true", help="print the result as JSON")
    for p in (e, m):
        p.add_argument("--credit", default=PROPOSED_CREDIT, help="credit for work done in place of planned work left without excuse")
        p.add_argument("--tiers", default=PROPOSED_TIERS, help="multipliers of MUST,PLAN,FILL")
        p.add_argument("--app-root", default=str(ROOT), help="where config/policies is (today's credit is read from it)")
    args = ap.parse_args(argv)
    try:
        rho, multipliers, today = Fraction(args.credit), tiers_of(args.tiers), canonical_rate(Path(args.app_root))
        if not 0 <= rho <= 1:
            raise ValueError("--credit is a share between 0 and 1")
        if args.source == "example":
            print(f"{'case':78s} {'today':>7s} {'proposed':>9s}   (today: credit {float(today):.2f}, no tiers; proposed: credit "
                  f"{float(rho):.2f}, tiers {args.tiers})")
            for name, before, after in examples(rho, multipliers, today):
                print(f"{name:78s} {before!s:>7s} {after!s:>9s}")
            return 0
        result = score_run(args.db, rho, multipliers, today)
    except (ValueError, sqlite3.Error) as error:
        print(f"right_work_score: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"{'team':8s} {'shifts':>6s} {'today':>7s} {'proposed':>9s} {'must-do done':>13s}")
        rows = list(result["teams"].items()) + [("all", {"team_shifts": result["team_shifts_scored"], **result["all_teams"]})]
        for team, t in rows:
            must = "n/a" if t["must_do_work_done_percent"] is None else f"{t['must_do_work_done_percent']:.1f}%"
            print(f"{team:8s} {t['team_shifts']:6d} {t['todays_score']:7.1f} {t['proposed_score']:9.1f} {must:>13s}")
        print(f"\nproposed: credit {float(rho):.2f}, tiers {args.tiers}. At today's settings (credit {float(today):.2f}, no tiers) the "
              f"proposal equals the stored score in {result['same_as_the_stored_score_at_todays_settings']} of "
              f"{result['team_shifts_scored']} team-shifts.")
    return 0 if result["team_shifts_scored"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
