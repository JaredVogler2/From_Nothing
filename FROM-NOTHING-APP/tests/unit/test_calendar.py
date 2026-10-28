"""P08: bounded per-line calendars, independent DST-correct shifts and the no-start cutoff (A19, A20, A21, A22)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from focus.calendar import CalendarService, ShiftPolicy, admit_line_calendars, build_shift
from focus.domain import DecisionStatus, from_minute, to_minute

UTC = timezone.utc


def _rows(line: int, statuses: dict[str, str], load: int = 7001, active: bool = True) -> list[dict]:
    return [{"LINE_NUMBER": line, "CALENDAR_DATE": d, "DAY_STATUS": s, "POSITION": f"P{d[-2:]}", "LAST_MODIFIED_DT": "2026-10-20",
             "LOAD_ID": load, "IS_ACTIVE": active} for d, s in statuses.items()]


S18 = {"2026-10-24": "WORKING", "2026-10-25": "WEEKEND", "2026-10-26": "WORKING", "2026-10-27": "WORKING", "2026-10-28": "WORKING",
       "2026-10-29": "WORKING", "2026-10-30": "WORKING", "2026-10-31": "WEEKEND", "2026-11-01": "WEEKEND", "2026-11-02": "NONWORKING",
       "2026-11-03": "WORKING"}


@pytest.fixture()
def shift_policy(policy) -> ShiftPolicy:
    return ShiftPolicy.from_policy(policy)


@pytest.fixture()
def service(policy, shift_policy) -> CalendarService:
    rows = _rows(1301, S18)
    rows = [r for r in rows if r["CALENDAR_DATE"] != "2026-10-29"]                 # a hole inside the bounds
    return CalendarService(admit_line_calendars(rows, policy, [1301]), shift_policy, "DEMO")


def _decide(service: CalendarService, shift_policy: ShiftPolicy, day: str, number: int):
    return service.eligible(1301, build_shift(date.fromisoformat(day), number, shift_policy))


def test_a19_per_line_calendar_wins_and_holes_or_outside_block(service, shift_policy) -> None:
    assert _decide(service, shift_policy, "2026-10-24", 1).allowed                    # working Saturday
    holiday = _decide(service, shift_policy, "2026-11-02", 1)
    assert holiday.status is DecisionStatus.DENIED and holiday.code == "NONWORKING_DAY"   # weekday holiday
    hole = _decide(service, shift_policy, "2026-10-29", 1)
    assert hole.status is DecisionStatus.UNKNOWN and hole.code == "CALENDAR_DATE_UNKNOWN"  # no weekday fallback
    outside = _decide(service, shift_policy, "2026-11-04", 1)
    assert outside.status is DecisionStatus.UNKNOWN                                     # no automatic extension
    assert service.bounds(1301) == (date(2026, 10, 24), date(2026, 11, 3))
    cal = service.calendars[1301]
    assert cal.holes == (date(2026, 10, 29),) and any(r.startswith("CALENDAR_HOLES") for r in cal.reasons)
    assert not service.eligible(1302, build_shift(date(2026, 10, 26), 1, shift_policy)).allowed   # another line is not borrowed


def test_a19_conflicting_generations_block_without_max_guess(policy, shift_policy) -> None:
    rows = _rows(1301, S18, load=7001) + _rows(1301, S18, load=7002)
    cals = admit_line_calendars(rows, policy, [1301])
    assert cals[1301].status == "CONFLICT" and "CALENDAR_GENERATION_CONFLICT" in cals[1301].reasons[0]
    decision = CalendarService(cals, shift_policy, "DEMO").eligible(1301, build_shift(date(2026, 10, 26), 1, shift_policy))
    assert decision.status is DecisionStatus.UNKNOWN and decision.code == "CALENDAR_CONFLICT"
    explicit = policy.with_overrides({"calendar.generation_selector": "MAX_LOAD_ID"}, new_id="cal-max", reason="producer-approved selector")
    chosen = admit_line_calendars(rows, explicit, [1301])[1301]
    assert chosen.status == "ADMITTED" and chosen.generation == 7002 and chosen.reasons[0].startswith("GENERATION_SELECTED_BY_POLICY")
    inactive = admit_line_calendars(_rows(1301, S18, active=False), policy, [1301])
    assert inactive[1301].status == "MISSING"


def test_a20_s3_exception_needs_same_line_next_day_evidence(service, shift_policy) -> None:
    sunday = _decide(service, shift_policy, "2026-10-25", 3)                           # nonworking Sunday, working Monday
    assert sunday.allowed and sunday.code == "S3_EXCEPTION"
    holiday = _decide(service, shift_policy, "2026-11-02", 3)                           # holiday before working Tuesday
    assert holiday.allowed and holiday.code == "S3_EXCEPTION"
    saturday = _decide(service, shift_policy, "2026-10-31", 3)                          # nonworking Saturday before nonworking Sunday
    assert saturday.status is DecisionStatus.DENIED and saturday.code == "S3_NONWORKING"
    for n in (1, 2):
        assert _decide(service, shift_policy, "2026-10-25", n).status is DecisionStatus.DENIED
    edge = _decide(service, shift_policy, "2026-10-28", 3)                              # next day is the hole: working d needs nothing more
    assert edge.allowed and edge.code == "WORKDAY"


def test_a20_exception_without_next_day_evidence_is_unknown(policy, shift_policy) -> None:
    rows = _rows(1301, {"2026-10-24": "WORKING", "2026-10-25": "WEEKEND"})              # bounds end on the nonworking day
    svc = CalendarService(admit_line_calendars(rows, policy, [1301]), shift_policy, "DEMO")
    decision = svc.eligible(1301, build_shift(date(2026, 10, 25), 3, shift_policy))
    assert decision.status is DecisionStatus.UNKNOWN and decision.code == "S3_NEXT_DAY_UNKNOWN"


def test_a21_working_day_s3_allowed_before_nonworking_day(service, shift_policy) -> None:
    friday = _decide(service, shift_policy, "2026-10-30", 3)                            # Saturday 10-31 is WEEKEND
    assert friday.allowed and friday.code == "WORKDAY"
    s3 = build_shift(date(2026, 10, 30), 3, shift_policy)
    assert s3.label == "2026-10-30 S3"                                                  # labelled by its 23:00 start date
    assert from_minute(s3.start) == datetime(2026, 10, 31, 3, 0, tzinfo=UTC)


def test_a21_dst_endpoints_are_built_independently(shift_policy) -> None:
    fall = build_shift(date(2026, 10, 31), 3, shift_policy)                             # 23:00 EDT -> 06:00 EST
    assert (from_minute(fall.start), from_minute(fall.end)) == (datetime(2026, 11, 1, 3, tzinfo=UTC), datetime(2026, 11, 1, 11, tzinfo=UTC))
    assert fall.end - fall.start == 480 and fall.regular_cap == 340 and fall.dst_adjusted    # real 8 h, nominal budget unchanged
    spring = build_shift(date(2027, 3, 13), 3, shift_policy)                            # 23:00 EST -> 06:00 EDT
    assert spring.end - spring.start == 360
    assert spring.regular_cap == spring.open_minutes < 340                              # no manufactured hour of work
    normal = build_shift(date(2026, 10, 27), 3, shift_policy)
    assert normal.end - normal.start == 420 and normal.regular_cap == 340 and not normal.dst_adjusted
    s1_after = build_shift(date(2026, 11, 1), 1, shift_policy)                          # next shift starts at local 06:00 EST
    assert from_minute(s1_after.start) == datetime(2026, 11, 1, 11, tzinfo=UTC) == from_minute(fall.end)
    days = [build_shift(date(2026, 10, 31) + timedelta(days=i), 1, shift_policy).start for i in range(3)]
    assert [b - a for a, b in zip(days, days[1:])] == [1500, 1440]                      # real elapsed, no packed 1470-minute day


def test_s1_fixture_protected_windows_and_budget(shift_policy) -> None:
    s1 = build_shift(date(2026, 10, 27), 1, shift_policy)
    local = lambda m: from_minute(m).astimezone(shift_policy.tz).strftime("%H:%M")  # noqa: E731
    assert [(local(a), local(b), k) for a, b, k in s1.protected] == [("06:00", "06:15", "PRE"), ("09:00", "09:20", "BREAK"),
                                                                     ("11:00", "11:30", "LUNCH"), ("14:15", "14:30", "POST")]
    assert s1.open_minutes == 430 == s1.regular_cap and s1.cutoff_coord == 400
    assert [b - a for a, b in s1.regular_windows] == [165, 100, 165]
    assert build_shift(date(2026, 10, 27), 2, shift_policy).regular_cap == 430


def test_a22_no_start_cutoff_equality_and_ot_does_not_move_it(shift_policy) -> None:
    s1 = build_shift(date(2026, 10, 27), 1, shift_policy)
    cutoff = s1.cutoff_instant
    assert from_minute(cutoff).astimezone(shift_policy.tz).strftime("%H:%M") == "13:45"   # coordinate 400 mapped through breaks
    assert s1.coord(cutoff) == 400
    assert s1.valid_start(cutoff - 1) and s1.valid_start(cutoff) and not s1.valid_start(cutoff + 1)
    assert s1.ot_window == (s1.end, s1.end + 60)                                        # OT60 exists...
    assert s1.cutoff_instant == cutoff                                                  # ...and never shifts the cutoff
    assert not s1.valid_start(s1.ot_window[0])                                          # OT is not a new-start window
    lunch_start = to_minute(datetime(2026, 10, 27, 15, 0, tzinfo=UTC))                  # 11:00 EDT, inside the lunch break
    assert not s1.valid_start(lunch_start)


def test_a22_finish_fit_is_checked_separately_from_start(shift_policy) -> None:
    s1 = build_shift(date(2026, 10, 27), 1, shift_policy)
    start = s1.cutoff_instant                                                           # 13:45: valid start, 30 regular minutes left
    assert s1.advance(start, 60, ot_allowed=0) is None                                  # does not fit without overtime
    end, segments = s1.advance(start, 60, ot_allowed=60)
    assert [(b - a, ot) for a, b, ot in segments] == [(30, False), (30, True)]           # 14:15-14:30 POST is skipped, OT counted once
    assert s1.advance(start, 120, ot_allowed=60) is None                                # +60 at most
    assert s1.advance(start - 30, 30, ot_allowed=0)[0] == start                         # exact regular fit
