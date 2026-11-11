"""The calibration tool's arithmetic (tools/calibrate_scores.py): the rules, the swap watch and the review loop.

* R5 (the canonical rule since D-27) credits every counted off-plan minute at the rate w and never more than 100;
* the swap watch flags off-plan minutes done beside planned work left undone with no claim on record. It changes no
  score: a missed planned job costs points on its own;
* without off-plan work R5 is 100 (1 - unexcused shortfall) minus the out-of-sequence charge, for every w;
* a swap never pays for w below 1; the rule before D-27 lowered a score for extra work;
* pooled scores add components first;
* the review sample and the labels say how well the watch finds real swaps.
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("calibrate_scores", Path(__file__).resolve().parents[2] / "tools" / "calibrate_scores.py")
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


def ts(key="T1|1", *, D=100.0, Cp=100.0, U=0.0, O=0.0, missed=0.0, team="T1", day="2027-01-04", number=1, today=None):
    return C.team_shift(key=key, team=team, day=day, number=number, superintendent=None, B0=D, B=D, E=0.0, Cp=Cp, U=U, O=O,
                        status="demonstration", no_plan=False, today=today, missed_unclaimed=missed, excused={}, done_before=0.0,
                        unplanned=["J1"] if U else [], window=None, completions=None)


def test_additive_work_counts_at_w_and_never_above_100():
    r = ts(D=100, Cp=80, U=10)                       # nothing missed without a claim: additive
    assert C.classify(r) == (10, 0)
    assert C.r5(r, 0.8) == pytest.approx(100 * (0.8 * 0.9 + 0.2 * 0.8))
    assert C.r5(ts(D=100, Cp=100, U=50), 1.0) == 100.0


def test_the_swap_watch_flags_minutes_beside_unclaimed_misses_and_the_score_credits_them_anyway():
    swap = ts(D=100, Cp=80, U=20, missed=20)
    assert C.classify(swap) == (0, 20)               # all 20 off-plan minutes sit beside 20 unclaimed missed minutes
    assert C.r5(swap, 0.8) == pytest.approx(96.0)    # credited: 0.8 x 100% + 0.2 x 80%
    assert C.classify(ts(D=100, Cp=80, U=0, missed=20)) == (0, 0)
    both = ts(D=100, Cp=70, U=25, missed=10)         # 10 minutes beside the unclaimed miss, 15 clear extra
    assert C.classify(both) == (15, 10)
    assert C.r5(both, 0.8) == pytest.approx(100 * (0.8 * 0.95 + 0.2 * 0.70))
    counted = ts(D=100, Cp=70, U=25)                 # the evaluation left 5 minutes out (started out of sequence by choice)
    counted["Uc"] = 20.0
    assert C.credited(counted) == 20.0 and C.r5(counted, 0.8) == pytest.approx(100 * (0.8 * 0.90 + 0.2 * 0.70))


def test_the_rule_before_d27_lowered_a_score_for_extra_work():
    done, ahead = ts(D=100, Cp=100), ts(D=100, Cp=100, U=50)
    assert C.before_d27(done) == 100.0 and C.before_d27(ahead) == pytest.approx(100 * (0.7 + 0.3 * 100 / 150))   # 90: worse for working ahead
    assert C.r5(ahead, 0.8) == C.r5(done, 0.8) == 100.0
    half = ts(D=100, Cp=50)
    assert C.before_d27(half) == pytest.approx(65.0) and C.r5(half, 0.8) == pytest.approx(50.0)                  # the free 30 points are gone


@pytest.mark.parametrize("w", C.WS)
def test_without_offplan_work_the_grade_is_one_minus_the_shortfall(w):
    r = ts(D=200, Cp=170, O=4)
    assert C.r5(r, w) == pytest.approx(100 * (1 - 30 / 200) - 100 * 4 / 200)


@pytest.mark.parametrize("w", C.WS)
def test_a_swap_never_pays_and_costs_the_adherence_share(w):
    records = [ts(f"T{i}|1", D=100, Cp=100 - m, U=m, missed=m, team=f"T{i}") for i, m in enumerate((5, 20, 60))]
    watch = C.analyze(records, lead=20, bands=C.BAND_SETS)["swap_watch"]
    assert watch["team_shifts"] == 3 and watch["share_of_scored"] == 1.0
    cost = watch[f"points_given_up_w{w}"]
    # doing the m missed planned minutes instead of the m off-plan ones scores 100: the swap gave up (1 - w) x m points
    assert cost["share_where_the_swap_paid"] == 0
    assert cost["mean"] == pytest.approx((1 - w) * (5 + 20 + 60) / 3, abs=1e-3) and cost["max"] == pytest.approx((1 - w) * 60, abs=1e-3)


def test_the_owners_example_ten_jobs_eight_done_four_others():
    """Owner, 2026-11-10: 10 jobs scheduled, 8 done, 4 others not on plan: full credit for the 8, some credit for the 4,
    and points lost for the 2 missed unless their excuse is valid."""
    r = ts(D=600, Cp=480, U=240, missed=120)             # ten 60-minute jobs; the two misses are not excused
    assert C.r5(r, 0.8) == pytest.approx(96.0)           # 80 for the 8, 16 for the extra work, 4 lost for the 2 missed
    assert C.r5(ts(D=600, Cp=480, U=0, missed=120), 0.8) == pytest.approx(80.0)
    assert C.r5(ts(D=480, Cp=480, U=240), 0.8) == 100.0  # the two misses validly excused: nothing lost
    assert C.before_d27(r) == pytest.approx(100 * (0.7 * 0.8 + 0.3 * 480 / 720))   # 76: the old rule took points for the extra work


def test_pooled_scores_add_components_first():
    a, b = ts("T1|1", D=400, Cp=200), ts("T1|2", D=100, Cp=100, number=2)
    p = C.pooled([a, b])
    assert C.r5(p, 0.8) == pytest.approx(100 * 300 / 500)
    ahead = C.pooled([ts("T1|1", D=200, Cp=200, U=50), ts("T1|2", D=200, Cp=150, number=2)])
    assert C.r5(ahead, 0.8) == pytest.approx(97.5)       # extra work beyond one shift's expected amount counts in the pool


def test_the_analysis_reports_shortfall_grades_flips_and_coverage():
    records = [ts("T1|1", D=100, Cp=100, today=100.0), ts("T1|2", D=100, Cp=85, number=2, today=85.0),
               ts("T2|1", D=0, Cp=0, team="T2")]
    res = C.analyze(records, lead=20, bands={"demo": (90, 80, 70, 60)})
    assert res["coverage"]["scored"] == 2 and res["coverage"]["all_excused_or_no_obligation"] == 1
    assert res["shortfall"]["share_zero"] == 0.5
    assert res["grades"]["demo"]["R5_w0.8"] == {"A": 1, "B": 1}
    assert res["grades"]["demo"]["R0"] == {"A": 1, "B": 1} and res["scores"]["R0_before_D27"]["p1"] == 89.5        # 0.7 x 85 + 30
    assert res["scores"]["canonical_today"]["p1"] == 85.0
    assert res["grades"]["demo"]["flip_rate_consecutive_shifts_R5_w0.8"] == 1.0
    assert C.analyze([], lead=20, bands={})["coverage"]["scored"] == 0


def test_the_review_sample_and_labels_give_the_miss_and_false_alarm_rates(tmp_path):
    records = [ts(f"T{i}|1", D=100, Cp=90, U=10, missed=10 if i < 4 else 0, team=f"T{i}") for i in range(8)]
    sample = C.review_sample(records, 8, seed=1)
    assert {s["watch"] for s in sample} == {"EXTRA_ONLY", "POSSIBLE_SWAP"} and len(sample) == 8
    path = tmp_path / "labels.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(sample[0]))
        writer.writeheader()
        for s in sample:
            # supervisors: every flag is right; one team-shift the watch did not flag was really a swap
            s["label"] = "SWAP" if s["watch"] == "POSSIBLE_SWAP" or s["team_shift"] == "T4|1" else "EXTRA"
            writer.writerow(s)
    rates = C.label_rates(path)
    assert rates["swaps_labeled"] == 5 and rates["swaps_not_flagged"] == 1
    assert rates["miss_rate"] == 0.2 and rates["false_alarm_rate"] == 0.0


def test_spearman_and_percentiles():
    assert C.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert C.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert C.percentiles([5.0])["p50"] == 5.0


def test_scores_by_shift_number_show_a_shift_that_is_lower_and_what_was_excused():
    rows = [ts(f"T1|{d}|1", D=100, Cp=100, day=f"2027-01-0{d}", number=1) for d in (4, 5)]
    rows += [ts(f"T1|{d}|3", D=100, Cp=70, day=f"2027-01-0{d}", number=3) for d in (4, 5)]
    out = C.by_shift_number(rows)
    assert out["shifts"]["1"] == {"team_shifts": 2, "mean_score": 100.0, "median_score": 100.0, "mean_shortfall": 0.0, "excused_share_of_B": 0.0}
    assert out["shifts"]["3"]["mean_score"] == 70.0 and out["shifts"]["3"]["mean_shortfall"] == pytest.approx(0.3)
    assert out["spread_of_means"] == 30.0                    # third shift sits 30 points under first shift
    assert C.by_shift_number([]) == {"shifts": {}, "spread_of_means": None}


def test_weekly_scores_say_whether_weeks_tell_teams_apart():
    def week(team, monday, done):                            # five days of one team's shifts, pooled by ISO week
        return [ts(f"{team}|{monday}|{i}", team=team, D=100, Cp=done, day=f"2027-01-{monday + i:02d}") for i in range(5)]

    steady = week("T1", 4, 100) + week("T1", 11, 100) + week("T2", 4, 60) + week("T2", 11, 60)
    s = C.weekly(steady)                                     # teams differ, each is the same every week
    assert (s["team_weeks"], s["weeks"], s["teams"]) == (4, 2, 2)
    assert s["change_between_consecutive_weeks"] == {"mean": 0.0, "p90": 0.0, "max": 0.0} and s["between_team_share_of_variance"] == 1.0
    noisy = week("T1", 4, 100) + week("T1", 11, 60) + week("T2", 4, 60) + week("T2", 11, 100)
    n = C.weekly(noisy)                                      # the same two scores, swapping between the teams each week
    assert n["change_between_consecutive_weeks"]["mean"] == 40.0 and n["between_team_share_of_variance"] == 0.0
    assert C.weekly(week("T1", 4, 100))["note"] == "needs at least two weeks of scores"
    pooled_week = C.weekly(week("T1", 4, 100)[:4] + [ts("T1|x", team="T1", D=100, Cp=0, day="2027-01-08")] + week("T1", 11, 100))
    assert pooled_week["weekly_score"]["p1"] == 80.0         # 400 of 500 minutes in the week: pooled first, not the mean of 100s and a 0


def test_the_blind_grade_sample_hides_the_score_and_the_labels_show_which_bands_agree(tmp_path):
    done = {"A": 95, "B": 85, "C": 75, "D": 65, "F": 40}
    records = [ts(f"{g}{i}", D=100, Cp=cp, day="2027-01-04") for g, cp in done.items() for i in range(6)]
    demo = C.BAND_SETS["demo 90/80/70/60"]
    sample = C.grade_sample(records, 10, 7, demo)
    assert len(sample) == 10 and all(row["supervisor_grade"] == "" for row in sample)
    assert not any(k in row for row in sample for k in ("score", "grade", "today"))                 # blind to the result
    first = next(row for row in sample if row["team_shift"].startswith("D"))                        # the facts a supervisor judges from
    assert (first["expected_minutes"], first["planned_minutes_done"], first["planned_minutes_missed_not_excused"]) == (100.0, 65.0, 35.0)
    assert sorted(key[0] for key in (row["team_shift"] for row in sample)) == sorted("AABBCCDDFF")  # two from each grade
    assert C.grade_sample(records, 10, 7, demo) == sample and C.grade_sample(records, 10, 8, demo) != sample
    assert len(C.grade_sample(records[:3], 10, 7, demo)) == 3                                        # never more than there are
    path = tmp_path / "grades.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(sample[0]))
        writer.writeheader()
        for row in sample:                                    # the supervisor grades one step kinder than the demo bands for C and D work
            given = {"A": "A", "B": "B", "C": "B", "D": "C", "F": "F"}[row["team_shift"][0]]
            writer.writerow({**row, "supervisor_grade": given})
        writer.writerow({**sample[0], "team_shift": "not a shift", "supervisor_grade": "A"})
        writer.writerow({**sample[0], "team_shift": "A5" if sample[0]["team_shift"] != "A5" else "A4", "supervisor_grade": ""})
    kinder = (90, 75, 65, 60)                                 # the bands the supervisor is using: 75 is a B and 65 a C
    out = C.grade_agreement(path, records, {"demo 90/80/70/60": demo, "kinder": kinder})
    assert (out["labeled"], out["unlabeled_or_unmatched"]) == (10, 2)
    assert out["band_sets"]["demo 90/80/70/60"] == {"same_grade": 0.6, "within_one_grade": 1.0, "formula_stricter": 4, "formula_more_lenient": 0}
    assert out["band_sets"]["kinder"]["same_grade"] == 1.0 and out["best_agreement"] == "kinder"


def test_the_analysis_includes_the_shift_number_and_weekly_sections():
    rows = [ts(f"T{t}|{d}|{n}", team=f"T{t}", D=100, Cp=90 + t, day=f"2027-01-{d:02d}", number=n) for t in (1, 2) for d in (4, 5, 11, 12) for n in (1, 2)]
    res = C.analyze(rows, lead=20, bands=dict(C.BAND_SETS))
    assert set(res["by_shift_number"]["shifts"]) == {"1", "2"} and res["by_shift_number"]["spread_of_means"] == 0.0
    assert res["weekly"]["weeks"] == 2 and res["weekly"]["teams"] == 2 and res["weekly"]["between_team_share_of_variance"] == 1.0
