"""The calibration tool's arithmetic (tools/calibrate_scores.py): the rules, the swap check and the review loop.

* R5 adds additive off-plan minutes at the rate w and never more than 100; substitutive minutes earn nothing;
* P2 calls a team-shift's off-plan work substitutive only when planned work was left undone with no claim on record;
* without off-plan work R5 is 100 (1 - unexcused shortfall) minus the out-of-sequence charge, for every w;
* a swap correctly classified never pays at any w; misread swaps are what can pay;
* pooled scores add components first;
* the review sample and the labels give P2's miss and false-alarm rates.
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("calibrate_scores", Path(__file__).resolve().parents[2] / "tools" / "calibrate_scores.py")
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


def ts(key="T1|1", *, D=100.0, Cp=100.0, U=0.0, O=0.0, missed=0.0, team="T1", day="2027-01-04", number=1, r0=None):
    return C.team_shift(key=key, team=team, day=day, number=number, superintendent=None, B0=D, B=D, E=0.0, Cp=Cp, U=U, O=O,
                        status="demonstration", no_plan=False, r0=r0, missed_unclaimed=missed, excused={}, done_before=0.0,
                        unplanned=["J1"] if U else [], window=None, completions=None)


def test_additive_work_counts_at_w_and_never_above_100():
    r = ts(D=100, Cp=80, U=10)                       # nothing missed without a claim: additive
    assert C.classify(r) == (10, 0)
    assert C.r5(r, 0.8) == pytest.approx(100 * (0.8 * 0.9 + 0.2 * 0.8))
    assert C.r5(ts(D=100, Cp=100, U=50), 1.0) == 100.0


def test_p2_calls_work_substitutive_only_beside_unclaimed_missed_work():
    swap = ts(D=100, Cp=80, U=20, missed=20)
    assert C.classify(swap) == (0, 20)
    assert C.r5(swap, 0.8) == pytest.approx(80.0)    # substitutive minutes earn nothing
    assert C.classify(ts(D=100, Cp=80, U=0, missed=20)) == (0, 0)


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


def test_a_misread_swap_pays_only_when_much_more_work_came_in_than_went_out():
    r = ts(D=100, Cp=50, U=60, missed=10)                # 10 planned minutes dropped for 60 off-plan minutes
    gain = C.analyze([r], lead=20, bands=C.BAND_SETS)["swap_gain"]
    # instead of the swap: Cp 60 and nothing off plan scores 60 at every w
    assert gain["w1.0_q0.0"]["max_gain"] == pytest.approx(50 - 60)
    assert gain["w1.0_q0.3"]["max_gain"] == pytest.approx(68 - 60)            # 30% of the 60 minutes misread as additive
    assert gain["w0.8_q0.3"]["max_gain"] == pytest.approx(0.8 * 68 + 0.2 * 50 - 60)
    assert gain["w0.8_q0.0"]["share_that_pays"] == 0


def test_pooled_scores_add_components_first():
    a, b = ts("T1|1", D=400, Cp=200), ts("T1|2", D=100, Cp=100, number=2)
    p = C.pooled([a, b])
    assert C.r5(p, 0.8, add=p["add"], sub=p["sub"]) == pytest.approx(100 * 300 / 500)


def test_the_analysis_reports_shortfall_grades_flips_and_coverage():
    records = [ts("T1|1", D=100, Cp=100, r0=100.0), ts("T1|2", D=100, Cp=85, number=2, r0=89.5),
               ts("T2|1", D=0, Cp=0, team="T2")]
    res = C.analyze(records, lead=20, bands={"demo": (90, 80, 70, 60)})
    assert res["coverage"]["scored"] == 2 and res["coverage"]["all_excused_or_no_obligation"] == 1
    assert res["shortfall"]["share_zero"] == 0.5
    assert res["grades"]["demo"]["R5_w0.8"] == {"A": 1, "B": 1}
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
