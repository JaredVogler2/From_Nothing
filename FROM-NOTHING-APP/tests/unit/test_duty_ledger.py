"""P11: the named duty ledger: capacity, transfers, activation, atomic insert/remove/swap (A35, A36, A37, A39, A40)."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from focus.calendar import ShiftPolicy, build_shift
from focus.domain import DutyKind, to_minute
from focus.engine.ledger import DutyLedger, LedgerError, PersonWindow, ToolCalendar
from focus.engine.travel import TravelPolicy

DAY = date(2026, 10, 27)


@pytest.fixture()
def shift_policy(policy) -> ShiftPolicy:
    return ShiftPolicy.from_policy(policy)


@pytest.fixture()
def s1(shift_policy):
    return build_shift(DAY, 1, shift_policy)


def _m(shift_policy, hhmm: str, day: date = DAY) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return to_minute(datetime(day.year, day.month, day.day, h, m, tzinfo=shift_policy.tz))


def _window(person: str, shift, pool: str = "POOL", ot: bool = True, ot_cap: int = 60) -> PersonWindow:
    return PersonWindow(person, shift.id, pool, shift.regular_windows, (shift.ot_window,) if ot else (), shift.regular_cap, ot_cap if ot else 0)


def _ledger(policy, windows, caps=None, tools=None) -> DutyLedger:
    caps = caps or {"POOL": 8}
    return DutyLedger({(w.person, w.shift_id): w for w in windows}, TravelPolicy.from_policy(policy), caps, [DutyKind.WORK, DutyKind.TRAVEL,
                      DutyKind.TOOL, DutyKind.WAIT, DutyKind.OTHER], tools)


def test_a35_two_transfers_push_twenty_minutes_into_overtime(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    ledger = _ledger(policy, [_window("P", s1)])
    ledger.book("W1", ["P"], 1301, s1.id, "POOL", [(m("06:15"), m("09:00"), False)])
    ledger.book("W2", ["P"], 1302, s1.id, "POOL", [(m("09:35"), m("11:00"), False)])
    ledger.book("W3", ["P"], 1301, s1.id, "POOL", [(m("11:45"), m("14:15"), False), (m("14:30"), m("14:50"), True)])
    items = ledger.shift_items("P", s1.id)
    work = sum(i.minutes for i in items if i.kind is DutyKind.WORK)
    travel = [i for i in items if i.kind is DutyKind.TRAVEL]
    assert work == 420 and [i.minutes for i in travel] == [15, 15]                     # transfers are real reservations
    assert ledger.duty_minutes("P", s1.id) == (430, 20)                                # 450 total: 430 regular + 20 OT
    assert (travel[0].start, travel[1].start) == (m("09:20"), m("11:30"))              # never inside the 09:00 break or lunch


def test_a35_without_authorized_overtime_the_same_day_is_rejected(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    ledger = _ledger(policy, [_window("P", s1, ot=False)])
    ledger.book("W1", ["P"], 1301, s1.id, "POOL", [(m("06:15"), m("09:00"), False)])
    ledger.book("W2", ["P"], 1302, s1.id, "POOL", [(m("09:35"), m("11:00"), False)])
    with pytest.raises(LedgerError) as err:
        ledger.book("W3", ["P"], 1301, s1.id, "POOL", [(m("11:45"), m("14:15"), False), (m("14:30"), m("14:50"), True)])
    assert err.value.code == "OUTSIDE_ADMITTED_WINDOW"
    assert ledger.duty_minutes("P", s1.id) == (265, 0)                                 # 165 + 15 + 85: nothing partially booked


def test_a36_490_boundary_allowed_491_rejected(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    full = [(a, b, False) for a, b in s1.regular_windows] + [(m("14:30"), m("15:30"), True)]
    ledger = _ledger(policy, [_window("P", s1)])
    ledger.book("FULL", ["P"], 1301, s1.id, "POOL", full)
    assert ledger.duty_minutes("P", s1.id) == (430, 60)                                # 490 within physical windows
    ledger2 = _ledger(policy, [_window("P", s1)])
    with pytest.raises(LedgerError) as err:
        ledger2.book("OVER", ["P"], 1301, s1.id, "POOL", full[:-1] + [(m("14:30"), m("15:31"), True)])
    assert err.value.code == "OUTSIDE_ADMITTED_WINDOW"                                 # 491: no 61st OT minute exists
    tight = _ledger(policy, [_window("P", s1, ot_cap=59)])
    with pytest.raises(LedgerError) as err:
        tight.book("CAP", ["P"], 1301, s1.id, "POOL", full)
    assert err.value.code == "OT_CAP"


def test_a36_carry_in_overlap_is_checked_across_shift_labels(policy, shift_policy) -> None:
    s3 = build_shift(DAY, 3, shift_policy)
    next_s1 = build_shift(date(2026, 10, 28), 1, shift_policy)
    ledger = _ledger(policy, [_window("P", s3), _window("P", next_s1)])
    ledger.add_locked("CI-1", "P", _m(shift_policy, "05:00", date(2026, 10, 28)), _m(shift_policy, "06:30", date(2026, 10, 28)),
                      DutyKind.OTHER, None, s3.id, "POOL")
    with pytest.raises(LedgerError) as err:
        ledger.book("W", ["P"], 1301, next_s1.id, "POOL", [(_m(shift_policy, "06:15", date(2026, 10, 28)), _m(shift_policy, "07:00", date(2026, 10, 28)), False)])
    assert err.value.code == "PERSON_OVERLAP" and s3.id in err.value.detail          # labels never hide overlap
    ledger.book("W", ["P"], 1301, next_s1.id, "POOL", [(_m(shift_policy, "06:30", date(2026, 10, 28)), _m(shift_policy, "07:30", date(2026, 10, 28)), False)])


@pytest.mark.parametrize(("n", "cap"), [(1, 0), (2, 1), (10, 8)])
def test_a37_activation_cap_exact_with_reuse_free(policy, s1, n: int, cap: int) -> None:
    people = [f"P{i}" for i in range(n)]
    ledger = _ledger(policy, [_window(p, s1) for p in people], caps={"POOL": cap})
    a, b = s1.regular_windows[0]
    booked = 0
    for i, p in enumerate(people):
        try:
            ledger.book(f"W{i}", [p], 1301, s1.id, "POOL", [(a, a + 30, False)])
            booked += 1
        except LedgerError as err:
            assert err.code == "ACTIVATION_CAP"
    assert booked == cap and len(ledger.active("POOL")) == cap                         # N=1 -> 0 heads: intentional, no min-one
    if cap:
        ledger.book("REUSE", [people[0]], 1301, s1.id, "POOL", [(a + 60, a + 90, False)])   # reuse adds no head
        assert len(ledger.active("POOL")) == cap


def test_a39_a_b_a_needs_two_transfers_for_every_member(policy, shift_policy, s1) -> None:
    t0 = s1.regular_windows[0][0]
    ledger = _ledger(policy, [_window("X", s1), _window("Y", s1)])
    ledger.book("A1", ["X", "Y"], 1301, s1.id, "POOL", [(t0, t0 + 60, False)])
    with pytest.raises(LedgerError) as err:
        ledger.book("B", ["X", "Y"], 1302, s1.id, "POOL", [(t0 + 60, t0 + 120, False)])
    assert err.value.code == "TRAVEL_INFEASIBLE"
    ledger.book("B", ["X", "Y"], 1302, s1.id, "POOL", [(t0 + 75, t0 + 135, False)])  # earliest 75 at baseline 15
    with pytest.raises(LedgerError):
        ledger.book("A2", ["X", "Y"], 1301, s1.id, "POOL", [(t0 + 135, t0 + 155, False)])
    ledger.book("A2", ["X", "Y"], 1301, s1.id, "POOL", [(t0 + 150, t0 + 160, False)])  # the return is a second transfer
    for person in ("X", "Y"):
        travel = [i for i in ledger.shift_items(person, s1.id) if i.kind is DutyKind.TRAVEL]
        assert [(i.origin_line, i.line, i.minutes) for i in travel] == [(1301, 1302, 15), (1302, 1301, 15)]


def test_a40_insert_remove_swap_rederive_both_neighbours(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    ledger = _ledger(policy, [_window("P", s1)])
    ledger.book("A", ["P"], 1301, s1.id, "POOL", [(m("06:15"), m("07:15"), False)])
    ledger.book("C", ["P"], 1301, s1.id, "POOL", [(m("08:00"), m("09:00"), False)])
    assert not [i for i in ledger.shift_items("P", s1.id) if i.kind is DutyKind.TRAVEL]
    ledger.book("B", ["P"], 1302, s1.id, "POOL", [(m("07:30"), m("07:45"), False)])   # middle insert on another aircraft
    travel = [(i.start, i.line) for i in ledger.shift_items("P", s1.id) if i.kind is DutyKind.TRAVEL]
    assert travel == [(m("07:15"), 1302), (m("07:45"), 1301)]                          # both neighbouring transitions
    with pytest.raises(LedgerError):
        ledger.book("B2", ["P"], 1303, s1.id, "POOL", [(m("07:20"), m("07:50"), False)])   # cannot arrive or return in time
    ledger.remove("B")
    assert not [i for i in ledger.shift_items("P", s1.id) if i.kind is DutyKind.TRAVEL]   # A->C needs no transfer again
    with pytest.raises(LedgerError):
        with ledger.transaction():                                                     # swap that fails rolls back completely
            ledger.remove("C")
            ledger.book("D", ["P"], 1303, s1.id, "POOL", [(m("07:20"), m("07:40"), False)])
            ledger.book("E", ["P"], 1302, s1.id, "POOL", [(m("07:30"), m("07:50"), False)])
    assert set(ledger.bookings()) == {"A", "C"}


def test_a40_travel_cannot_cross_a_break_and_busy_tools_block(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    ledger = _ledger(policy, [_window("P", s1), _window("Q", s1)], tools=ToolCalendar({"TORQUE-1": 1}))
    ledger.book("A", ["P"], 1301, s1.id, "POOL", [(m("08:00"), m("08:55"), False)])
    with pytest.raises(LedgerError) as err:
        ledger.book("B", ["P"], 1302, s1.id, "POOL", [(m("09:20"), m("09:40"), False)])  # 08:55-09:00 + break: no 15 contiguous minutes
    assert err.value.code == "TRAVEL_INFEASIBLE"
    ledger.book("B", ["P"], 1302, s1.id, "POOL", [(m("09:35"), m("09:50"), False)])  # transfer 09:20-09:35 after the break
    ledger.book("T1", ["Q"], 1303, s1.id, "POOL", [(m("10:00"), m("10:30"), False)], tools=["TORQUE-1"])
    with pytest.raises(LedgerError) as err:
        ledger.book("T2", ["P"], 1302, s1.id, "POOL", [(m("10:15"), m("10:45"), False)], tools=["TORQUE-1"])
    assert err.value.code == "TOOL_UNAVAILABLE"
