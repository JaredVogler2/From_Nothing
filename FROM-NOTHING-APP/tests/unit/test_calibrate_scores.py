"""The calibration tool's arithmetic (tools/calibrate_scores.py): the rules, the swap check and the review loop.

* R5 (the canonical rule since D-27) adds additive off-plan minutes at the rate w and never more than 100; substitutive
  minutes earn nothing;
* off-plan work is substitutive up to the planned work left undone with no claim on record, minute for minute, and
  additive beyond that, exactly as the canonical score splits it;
* without off-plan work R5 is 100 (1 - unexcused shortfall) minus the out-of-sequence charge, for every w;
* a swap never pays at any w, even when it is misread as extra work; the rule before D-27 lowered a score for extra work;
* pooled scores add components first;
* the review sample and the labels give the classifier's miss and false-alarm rates.
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


def test_offplan_work_is_substitutive_up_to_the_unclaimed_missed_work_and_additive_beyond_it():
    swap = ts(D=100, Cp=80, U=20, missed=20)
    assert C.classify(swap) == (0, 20)
    assert C.r5(swap, 0.8) == pytest.approx(80.0)    # substitutive minutes earn nothing
    assert C.classify(ts(D=100, Cp=80, U=0, missed=20)) == (0, 0)
    both = ts(D=100, Cp=70, U=25, missed=10)         # 10 minutes cover the unclaimed miss, 15 are extra
    assert C.classify(both) == (15, 10)
    assert C.r5(both, 0.8) == pytest.approx(100 * (0.8 * 0.85 + 0.2 * 0.70))


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
def test_a_correctly_classified_swap_never_pays(w):
    records = [ts(f"T{i}|1", D=100, Cp=100 - m, U=m, missed=m, team=f"T{i}") for i, m in enumerate((5, 20, 60))]
    gain = C.analyze(records, lead=20, bands=C.BAND_SETS)["swap_gain"]
    assert gain[f"w{w}_q0.0"]["share_that_pays"] == 0
    assert gain[f"w{w}_q0.0"]["max_gain"] < 0


def test_a_swap_never_pays_even_when_part_of_it_is_misread_as_extra_work():
    r = ts(D=100, Cp=50, U=30, missed=20)                # 20 planned minutes dropped for 20 of the 30 off-plan minutes; 10 are extra
    gain = C.analyze([r], lead=20, bands=C.BAND_SETS)["swap_gain"]
    # instead of the swap: Cp 70 with the same 10 extra minutes scores 0.8 x 80 + 0.2 x 70 = 78
    assert gain["w0.8_q0.0"]["max_gain"] == pytest.approx((0.8 * 60 + 0.2 * 50) - 78)        # -20: the planned work it displaced
    assert gain["w0.8_q0.3"]["max_gain"] == pytest.approx((0.8 * 66 + 0.2 * 50) - 78)        # 30% of the 20 misread as extra: still a loss
    assert gain["w1.0_q0.3"]["max_gain"] == pytest.approx(66 - 80)
    assert all(v["share_that_pays"] == 0 for v in gain.values())


def test_pooled_scores_add_components_first():
    a, b = ts("T1|1", D=400, Cp=200), ts("T1|2", D=100, Cp=100, number=2)
    p = C.pooled([a, b])
    assert C.r5(p, 0.8, add=p["add"], sub=p["sub"]) == pytest.approx(100 * 300 / 500)


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
    assert {s["P2"] for s in sample} == {"ADDITIVE", "SUBSTITUTIVE"} and len(sample) == 8
    path = tmp_path / "labels.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(sample[0]))
        writer.writeheader()
        for s in sample:
            # supervisors: every substitutive call is right; one additive call was really a swap
            s["label"] = "SWAP" if s["P2"] == "SUBSTITUTIVE" or s["team_shift"] == "T4|1" else "EXTRA"
            writer.writerow(s)
    rates = C.label_rates(path)
    assert rates["swaps_labeled"] == 5 and rates["swaps_called_additive"] == 1
    assert rates["miss_rate_q"] == 0.2 and rates["false_alarm_rate"] == 0.0


def test_spearman_and_percentiles():
    assert C.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert C.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert C.percentiles([5.0])["p50"] == 5.0
