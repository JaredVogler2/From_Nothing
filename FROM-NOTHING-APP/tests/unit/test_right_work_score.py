"""The proposed right-work score (tools/right_work_score.py; report 12, DEC-33): the canonical score is its special
case, and the owner's ten-job example shows what the credit rate and the tiers change. A tool outside ``focus/``: the
application has one score formula and no grade reads this one. Then the priority facts it uses (valuation.priority)."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from decimal import Decimal
from fractions import Fraction
from itertools import product
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus.domain import PhaseKind
from focus.performance.score import formula
from focus.valuation.priority import FILL, MUST, PLAN, dependents, plan_priorities

SPEC = importlib.util.spec_from_file_location("right_work_score", Path(__file__).resolve().parents[2] / "tools" / "right_work_score.py")
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R                      # dataclasses look their module up by name
SPEC.loader.exec_module(R)
EARNED, EXCUSED, FLAT, MISSED, PlannedJob, right_work = R.EARNED, R.EXCUSED, R.FLAT, R.MISSED, R.PlannedJob, R.right_work

TIERED = {MUST: Fraction(2), PLAN: Fraction(1), FILL: Fraction(1, 2)}


def _jobs(done: int, missed: int, excused: int = 0, tier: str = PLAN, minutes: int = 60) -> list[PlannedJob]:
    return ([PlannedJob(Fraction(minutes), tier, EARNED)] * done + [PlannedJob(Fraction(minutes), tier, MISSED)] * missed
            + [PlannedJob(Fraction(minutes), tier, EXCUSED)] * excused)


def test_with_no_priorities_and_the_canonical_rate_it_is_the_canonical_score(policy) -> None:
    rho = Fraction(policy.str("score.attainment_weight"))
    assert rho + Fraction(policy.str("score.adherence_weight")) == 1
    for done, missed, excused, extra, oos in product((0, 3, 8, 10), (0, 2, 7), (0, 1), (0, 60, 150, 900), (0, 12)):
        jobs = _jobs(done, missed, excused)
        B, E, Cp = Fraction(60 * (done + missed + excused)), Fraction(60 * excused), Fraction(60 * done)
        canonical = formula(B, E, Cp, Fraction(extra), Fraction(oos), policy)["score"]
        assert right_work(jobs, Fraction(extra), Fraction(oos), rho=rho)["score"] == canonical, (done, missed, excused, extra, oos)


def test_the_owners_ten_jobs_eight_done_four_others_at_each_setting() -> None:
    """Ten one-hour jobs, eight done, two left without excuse, four other jobs done instead."""
    score = lambda jobs, rho, mult=None, extra=240: right_work(jobs, Fraction(extra), rho=Fraction(rho), **({"multipliers": mult} if mult else {}))["score"]  # noqa: E731
    plain = _jobs(8, 2)
    assert score(plain, "0.8") == Decimal("96.0")                       # today: two right jobs left costs four points
    assert score(plain, "0.5") == Decimal("90.0") and score(plain, "0.3") == Decimal("86.0")
    assert score(plain, "0.3", extra=0) == Decimal("80.0")              # doing nothing instead is still worse
    assert score(_jobs(8, 0, excused=2), "0.3") == Decimal("100.0")     # the two were excused: nothing is lost
    must_left = _jobs(8, 0) + _jobs(0, 2, tier=MUST)                    # the two left were jobs other work waited on
    fill_left = _jobs(8, 0) + _jobs(0, 2, tier=FILL)                    # the two left were jobs nothing depends on
    assert score(must_left, "0.3", TIERED) == Decimal("76.7") and score(fill_left, "0.3", TIERED) == Decimal("92.2")
    assert score(must_left, "0.3", TIERED, extra=0) == Decimal("66.7")  # ... and idle is worse again
    assert score(must_left, "0.8") == score(fill_left, "0.8") == Decimal("96.0")       # today both look the same
    done_right = right_work(_jobs(10, 0), Fraction(240), rho=Fraction("0.3"), multipliers=TIERED)
    assert done_right["score"] == Decimal("100.0") and done_right["credit_points"] == 0   # extra work never lifts a full plan past 100


def test_effort_not_count_and_the_breakdown_by_tier() -> None:
    long_job, short_jobs = [PlannedJob(Fraction(480), PLAN, MISSED)], [PlannedJob(Fraction(30), PLAN, EARNED)] * 9
    r = right_work(long_job + short_jobs, Fraction(0), rho=Fraction("0.3"))
    assert r["score"] == Decimal("36.0")                                # nine of ten jobs by count, 36% of the work
    # report 12's count example: two long jobs (4 hours, 2 people) left, eight short ones done, six other short jobs done.
    # By count that is 14 jobs against 10 planned; by effort a third of the plan
    long_left = lambda tier: [PlannedJob(Fraction(480), tier, MISSED)] * 2 + [PlannedJob(Fraction(60), PLAN, EARNED)] * 8   # noqa: E731
    count = lambda rho, tier=PLAN, mult=FLAT: right_work(long_left(tier), Fraction(360), rho=Fraction(rho), multipliers=mult)   # noqa: E731
    assert count("0.8")["done_points"] / count("0.8")["expected_points"] == Fraction(1, 3)
    assert (count("0.8")["score"], count("0.3")["score"], count("0.3", MUST, TIERED)["score"]) == (Decimal("53.3"), Decimal("40.8"), Decimal("24.5"))
    mixed = right_work([PlannedJob(Fraction(60), MUST, EARNED), PlannedJob(Fraction(60), MUST, MISSED), PlannedJob(Fraction(60), MUST, EXCUSED),
                        PlannedJob(Fraction(60), FILL, EARNED)], Fraction(0), rho=Fraction("0.3"), multipliers=TIERED)
    assert mixed["by_tier"][MUST] == {"planned": 3, "excused": 1, "done": 1, "left": 1} and mixed["by_tier"][FILL]["done"] == 1
    assert (mixed["expected_points"], mixed["done_points"], mixed["left_points"]) == (270, 150, 120)
    nothing = right_work(_jobs(0, 0, excused=3), Fraction(120), rho=Fraction("0.3"))
    assert nothing["score"] is None and nothing["expected_points"] == 0     # everything excused: no score, as the canonical neutral
    with pytest.raises(ValueError):
        right_work([PlannedJob(Fraction(60), "URGENT", EARNED)], Fraction(0), rho=Fraction("0.3"))


def test_the_out_of_sequence_charge_is_taken_as_the_canonical_score_takes_it(policy) -> None:
    jobs = _jobs(9, 1)
    plain = right_work(jobs, Fraction(0), rho=Fraction("0.3"))["final"]
    charged = right_work(jobs, Fraction(0), Fraction(6), rho=Fraction("0.3"))["final"]
    assert plain - charged == Fraction(100) * 6 / 600 == 1


def test_the_tool_prints_the_reports_examples_and_refuses_what_it_cannot_score(policy, tmp_path, capsys) -> None:
    assert R.canonical_rate() == Fraction(policy.str("score.attainment_weight"))          # "today" is the policy in force
    assert R.main(["example"]) == 0
    text = capsys.readouterr().out
    rows = {line[:78].strip(): line[78:].split() for line in text.splitlines()[1:]}
    assert rows["ten jobs: the two left were ordinary planned jobs"] == ["96.0", "86.0"]
    assert rows["ten jobs: the two left were must-do jobs"] == ["96.0", "76.7"]
    assert rows["ten jobs: the two left were excused"] == ["100.0", "100.0"]
    assert rows["count example: the long jobs were must-do jobs"] == ["53.3", "24.5"]
    assert R.main(["example", "--credit", "0.8", "--tiers", "1,1,1"]) == 0               # today's settings: both columns agree
    assert all(line[78:].split()[0] == line[78:].split()[1] for line in capsys.readouterr().out.splitlines()[1:])
    assert R.tiers_of("3, 1, 0.5") == {MUST: Fraction(3), PLAN: Fraction(1), FILL: Fraction(1, 2)}
    for bad in (["example", "--tiers", "2,1"], ["example", "--tiers", "2,1,0"], ["example", "--credit", "1.5"]):
        assert R.main(bad) == 2
    assert "right_work_score:" in capsys.readouterr().err
    empty = tmp_path / "old.db"                                                          # a run without priority facts
    sqlite3.connect(empty).close()
    assert R.main(["sim", str(empty)]) == 2 and "exec_priority" in capsys.readouterr().err


# ---------------------------------------------------------------------- what waits on a planned job (valuation.priority)

def _ctx():
    """Job A (work, inspection, release) gates job B and job C; job D has nothing after it; B gates E."""
    def phase(pid, job, kind, team="T1", minutes=60):
        demands = () if kind is PhaseKind.RELEASE else (SimpleNamespace(team=team),)
        return SimpleNamespace(job=job, kind=kind, role_demands=demands, standard_person_minutes=minutes)

    phases = {"A.w": phase("A.w", "A", PhaseKind.WORK), "A.q": phase("A.q", "A", PhaseKind.FINAL_QA, team="QA", minutes=20),
              "A.r": phase("A.r", "A", PhaseKind.RELEASE), "B.w": phase("B.w", "B", PhaseKind.WORK, team="T2", minutes=120),
              "B.r": phase("B.r", "B", PhaseKind.RELEASE), "C.w": phase("C.w", "C", PhaseKind.WORK, minutes=90),
              "D.w": phase("D.w", "D", PhaseKind.WORK), "E.w": phase("E.w", "E", PhaseKind.WORK, team="T3")}
    edge = lambda b: SimpleNamespace(succ_phase=b)                                                # noqa: E731
    succs = {"A.w": [edge("A.q")], "A.q": [edge("A.r")], "A.r": [edge("B.w"), edge("C.w")], "B.w": [edge("B.r")], "B.r": [edge("E.w")]}
    return SimpleNamespace(phases=phases, succs=succs, tails={"A.w": 500, "D.w": 0})


def test_what_waits_on_a_job_is_found_through_its_own_inspection_and_release_and_stops_at_the_next_job() -> None:
    ctx = _ctx()
    assert dependents(ctx, "A.w") == ("B.w", "C.w")               # through A's inspection and release; not on to E
    assert dependents(ctx, "A.q") == ("B.w", "C.w") and dependents(ctx, "B.w") == ("E.w",)
    assert dependents(ctx, "D.w") == () and dependents(ctx, "missing") == ()


def test_the_tier_of_a_planned_job_follows_from_what_is_planned_behind_it() -> None:
    ctx = _ctx()
    starts = {"A.w": 0, "B.w": 400, "C.w": 2000, "D.w": 0, "E.w": 9000}                          # E is planned days away
    p = plan_priorities(ctx, starts, ["A.w", "B.w", "D.w"], horizon_end=960)
    a, b, d = p["A.w"], p["B.w"], p["D.w"]
    assert (a.tier, a.dependents, a.gated_jobs, a.gated_minutes, a.gated_other_team_minutes, a.tail_minutes) == (MUST, 2, 1, 120, 120, 500)
    assert (b.tier, b.dependents, b.gated_minutes) == (PLAN, 1, 0)          # something depends on it, but nothing planned soon
    assert (d.tier, d.dependents, d.gated_minutes) == (FILL, 0, 0)          # nothing depends on it at all
    later = plan_priorities(ctx, starts, ["A.w"], horizon_end=2500)["A.w"]  # a longer horizon also counts job C, the team's own
    assert (later.gated_jobs, later.gated_minutes, later.gated_other_team_minutes) == (2, 210, 120)
