"""A calendar that ends before its line's work can be shown late keeps the aircraft's cost unknown (DEC-28).

FOCUS never adds a day to a line's calendar. These gaps say, at admission, which lines cannot be planned at all and
which can never show a late finish, so the cause is fixed in the calendar data instead of being found in the plan.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from focus.calendar import ShiftPolicy, admit_line_calendars, coverage_gaps
from focus.domain import to_minute

UTC = timezone.utc
START = date(2027, 3, 1)                          # a Monday


def _rows(line: int, days: int) -> list[dict]:
    out = []
    for i in range(days):
        day = START + timedelta(days=i)
        out.append({"LINE_NUMBER": line, "CALENDAR_DATE": day.isoformat(), "DAY_STATUS": "WEEKEND" if day.weekday() >= 5 else "WORKING",
                    "IS_ACTIVE": True, "LOAD_ID": 1, "POSITION": None, "LAST_MODIFIED_DT": None})
    return out


def _gaps(policy, due, cutoff: datetime, open_lines=(1, 2, 3, 4)):
    rows = _rows(1, 26) + _rows(2, 5) + _rows(3, 12) + _rows(4, 12)        # line 1 to 26 March, 2 to 5 March, 3 and 4 to 12 March
    calendars = admit_line_calendars(rows, policy, [1, 2, 3, 4, 5])           # line 5 has no calendar at all
    assert calendars[5].status == "MISSING" and all(calendars[n].status == "ADMITTED" for n in (1, 2, 3, 4))
    return coverage_gaps(calendars, due, open_lines, to_minute(cutoff), ShiftPolicy.from_policy(policy), "DEMO")


def test_a_line_with_no_working_shift_left_and_a_line_whose_calendar_ends_before_its_due_date(policy) -> None:
    cutoff = datetime(2027, 3, 9, 12, tzinfo=UTC)                             # Tuesday of the second week
    due = {1: datetime(2027, 3, 20, 4, tzinfo=UTC),                           # the calendar runs past it: a late finish can be planned
           2: datetime(2027, 3, 20, 4, tzinfo=UTC),                           # the calendar ended on 5 March: nothing can be planned
           3: datetime(2027, 3, 20, 4, tzinfo=UTC),                           # the calendar ends 12 March, before the due date
           4: None}                                                           # no due date: reported by the economics, not here
    gaps = _gaps(policy, due, cutoff)
    assert [(code, scope, effect) for code, scope, _, effect in gaps] == [("CALENDAR_NO_DAYS_LEFT", "line 2", "BLOCKS_ALL"),
                                                                           ("CALENDAR_ENDS_BEFORE_DUE", "line 3", "INFO")]
    assert "2027-03-05" in gaps[0][2] and "cost stay unknown" in gaps[0][2]
    assert "a late finish cannot be planned" in gaps[1][2] and "2027-03-20" in gaps[1][2]
    assert all(detail.startswith(code + ":") for code, _, detail, _ in gaps)   # the detail leads with its code, as other gaps do


def test_only_lines_with_open_work_and_an_admitted_calendar_are_checked(policy) -> None:
    cutoff = datetime(2027, 3, 9, 12, tzinfo=UTC)
    due = {n: datetime(2027, 3, 20, 4, tzinfo=UTC) for n in (1, 2, 3, 4, 5)}
    assert [g[1] for g in _gaps(policy, due, cutoff, open_lines=(1, 3, 5))] == ["line 3"]     # 2 has no open work; 5 has no calendar
    assert _gaps(policy, due, cutoff, open_lines=()) == []


def test_a_calendar_that_reaches_past_the_due_instant_is_not_flagged_and_one_that_stops_at_it_is(policy) -> None:
    cutoff = datetime(2027, 3, 9, 12, tzinfo=UTC)
    late = _gaps(policy, {3: datetime(2027, 3, 12, 4, tzinfo=UTC)}, cutoff, open_lines=(3,))    # due at the start of the last working day
    assert late == []                                                          # that day's shifts end after it: lateness can be planned
    at_end = _gaps(policy, {3: datetime(2027, 3, 14, 4, tzinfo=UTC)}, cutoff, open_lines=(3,))  # due after the last working shift ended
    assert [g[0] for g in at_end] == ["CALENDAR_ENDS_BEFORE_DUE"]
    after_last_shift = datetime(2027, 3, 13, 12, tzinfo=UTC)                   # the data cut falls after the calendar's last shift
    assert [g[0] for g in _gaps(policy, {3: None}, after_last_shift, open_lines=(3,))] == ["CALENDAR_NO_DAYS_LEFT"]
