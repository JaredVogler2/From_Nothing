"""The objective status a schedule run reports: did the cost objective compare any dollars, and what kept aircraft out.

Every plan is returned whether or not its dollars steered anything. An aircraft's cost is known only when its rate, its
due date and its finish date are all known; this report counts each, so an objective that compared nothing is visible
in the run's own output instead of being found later (docs/execution/OBJECTIVE_EXPLAINED.md, section 4).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fractions import Fraction

from focus.contracts.planning import AircraftCompletion, JobOutcome, LineCost
from focus.domain import Disposition
from focus.economics.evaluator import objective_status, reason_codes, summarize

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
DUE = NOW + timedelta(days=10)


def _known(line: int, *, status: str = "known", due: datetime = DUE, late_days: int = 0) -> LineCost:
    total = Fraction(late_days * 2_500_000)
    return LineCost(line, status, "ASSUMED", due + timedelta(days=late_days), due, late_days * 1440, total, Fraction(0), total,
                    Fraction(2_500_000), ("ASSUMED term",))


def _no_finish(line: int, jobs: int = 1, due: datetime = DUE) -> LineCost:
    return LineCost(line, "unknown", "ASSUMED", None, due, None, None, Fraction(0), None, Fraction(2_500_000),
                    (f"COMPLETION_UNKNOWN: {jobs} required job(s) unreleased",))


def _no_term(line: int, why: str, due: datetime | None = DUE, status: str = "unknown") -> LineCost:
    return LineCost(line, status, "UNKNOWN", None, due, None, None, None, None, None, (why,))


def _finish(line: int, status: str, unresolved: tuple[str, ...] = ()) -> AircraftCompletion:
    return AircraftCompletion(line, status, None if status == "unknown" else DUE, 10, 10 - len(unresolved), unresolved, ())


def _job(job: str, disposition: Disposition, *reasons: str) -> JobOutcome:
    return JobOutcome(job, disposition, tuple(reasons), None, None, False)


def _status(lines, completions, outcomes=()):
    return objective_status(summarize({c.line: c for c in lines}), completions, outcomes, NOW)


def test_when_no_aircraft_has_a_known_finish_the_report_says_the_cost_objective_did_not_steer() -> None:
    """The case a live run can be in without failing: every aircraft has a rate and a due date, none has a finish date."""
    lines = [_no_finish(n, jobs=2) for n in (1, 2, 3)]
    completions = [_finish(n, "unknown", (f"J{n}a", f"J{n}b")) for n in (1, 2, 3)]
    outcomes = [_job("J1a", Disposition.QUARANTINED, "DURATION_UNKNOWN: no standard"), _job("J1b", Disposition.QUARANTINED, "DURATION_UNKNOWN"),
                _job("J2a", Disposition.BLOCKED, "PART_NOT_RECEIVED"), _job("J2b", Disposition.DEFERRED, "NO_CALENDAR_WINDOW: line 2"),
                _job("J3a", Disposition.QUARANTINED, "DURATION_UNKNOWN"), _job("J3b", Disposition.EXCLUDED),
                _job("J9", Disposition.SCHEDULED)]
    s = _status(lines, completions, outcomes)
    assert s["cost_steering"] == "none" and "did not steer" in s["message"] and "all 3 aircraft" in s["message"]
    assert (s["aircraft"], s["cost_known"], s["rate_known"], s["due_date_known"]) == (3, 0, 3, 3)       # terms are fine: finish is not
    assert (s["finish_known"], s["finish_conditional"], s["finish_unknown"]) == (0, 0, 3)
    assert s["aircraft_without_finish"] == [1, 2, 3] and s["aircraft_without_rate"] == [] and s["aircraft_without_due_date"] == []
    assert s["cost_unknown_by_reason"] == {"COMPLETION_UNKNOWN": 3}
    assert s["unplanned_open_jobs"] == 6
    assert s["unplanned_open_jobs_by_reason"] == {"DURATION_UNKNOWN": 3, "EXCLUDED": 1, "NO_CALENDAR_WINDOW": 1, "PART_NOT_RECEIVED": 1}
    assert list(s["unplanned_open_jobs_by_reason"])[0] == "DURATION_UNKNOWN"                              # largest cause first


def test_a_missing_rate_and_a_missing_due_date_are_told_apart_and_nothing_is_estimated() -> None:
    lines = [
        _known(1, late_days=2),
        _known(2, status="conditional"),
        _no_finish(3),
        _no_term(4, "NO_EFFECTIVE_TERM"),
        _no_term(5, "ASSUMED_PLANNING_TERM: modeled cost, not contractual liability; DUE_UNKNOWN: no same-run planning proxy date", due=None),
        _no_term(6, "AIRCRAFT_NOT_MAPPED; NO_EFFECTIVE_TERM; DUE_UNKNOWN: no same-run planning proxy date", due=None),
        _no_term(7, "CHECKPOINT_SEQUENCE_CONFLICT: two checkpoints share sequence 3; no floor is assumed; DUE_UNKNOWN: no proxy", due=None),
        _no_term(8, "UNSUPPORTED_CURRENCY:EUR", status="unsupported"),
        _no_term(9, "ECONOMICS_MISSING"),
    ]
    completions = [_finish(1, "known"), _finish(2, "conditional"), _finish(3, "unknown", ("J3",))] + [_finish(n, "known") for n in range(4, 10)]
    s = _status(lines, completions, [_job("J3", Disposition.BLOCKED, "not a code")])
    assert s["cost_steering"] == "partial" and (s["aircraft"], s["cost_known"]) == (9, 2)
    assert "2 of 9 aircraft" in s["message"] and "other 7" in s["message"] and "they are left out" in s["message"]
    assert s["aircraft_without_rate"] == [4, 6, 8, 9]                    # 5 and 7 have a usable term: only their due date is missing
    assert s["aircraft_without_due_date"] == [5, 6, 7]
    assert (s["rate_known"], s["due_date_known"]) == (5, 6)
    assert s["cost_unknown_by_reason"] == {"DUE_UNKNOWN": 2, "AIRCRAFT_NOT_MAPPED": 1, "COMPLETION_UNKNOWN": 1, "ECONOMICS_MISSING": 1,
                                           "NO_EFFECTIVE_TERM": 1, "UNSUPPORTED_CURRENCY": 1}
    assert s["unplanned_open_jobs_by_reason"] == {"BLOCKED": 1}          # a reason that is not a code falls back to the disposition
    assert s["finish_known"] + s["finish_conditional"] + s["finish_unknown"] == 9


def test_every_aircraft_priced_is_full_steering_and_one_left_out_reads_in_the_singular() -> None:
    full = _status([_known(1), _known(2, status="conditional")], [_finish(1, "known"), _finish(2, "conditional")])
    assert full["cost_steering"] == "full" and full["message"] == "The cost objective compared all 2 aircraft."
    assert full["cost_unknown_by_reason"] == {} and full["unplanned_open_jobs"] == 0 and full["unplanned_open_jobs_by_reason"] == {}
    one = _status([_known(1), _no_finish(2)], [_finish(1, "known"), _finish(2, "unknown", ("J2",))])
    assert one["cost_steering"] == "partial" and "The cost of the other 1 is unknown, so it is left out of the dollars." in one["message"]
    assert one["unplanned_open_jobs_by_reason"] == {"NOT_IN_THE_PLAN": 1}       # an unreleased job with no outcome row is still counted
    empty = _status([], [])
    assert empty["cost_steering"] == "none" and empty["aircraft"] == 0


def test_aircraft_already_past_due_when_the_plan_was_made_are_counted() -> None:
    past, today, future = NOW - timedelta(days=3), NOW, NOW + timedelta(days=1)
    lines = [_known(1, due=past, late_days=4), _no_finish(2, due=past), _known(3, due=today), _known(4, due=future),
             _no_term(5, "NO_EFFECTIVE_TERM", due=past), _no_term(6, "DUE_UNKNOWN: no proxy", due=None)]
    s = _status(lines, [_finish(n, "known") for n in range(1, 7)])
    assert s["past_due_when_planned"] == 4          # 1, 2, 3 and 5: a due instant that has arrived is past; an unknown one is not counted


def test_reason_codes_reads_codes_and_ignores_free_text() -> None:
    assert reason_codes(("A_B; C_D: detail", "E1: x; trailing words; F_G")) == ["A_B", "C_D", "E1", "F_G"]
    assert reason_codes(("ASSUMED term", "conditional completion: forecast-only evidence", "")) == []
