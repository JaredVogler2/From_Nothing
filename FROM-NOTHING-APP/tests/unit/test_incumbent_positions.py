"""P14: an incumbent task is found again where its work is, whatever the clock or the renumbering says.

* A local start inside S3 after midnight is on the day after the shift's production day.
* An incumbent phase is located by the production minutes still to go in its job, so written-back progress (which
  re-splits and renumbers the remaining work from ordinal 0) neither moves a commitment onto the wrong piece nor loses it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from focus.calendar import ShiftPolicy, build_shift, local_wall, shift_local_minute
from focus.contracts.planning import locate_position, work_to_go
from focus.data.sourcetime import local_to_utc
from focus.domain import PhaseKind, to_minute

DAY = date(2026, 10, 26)


@pytest.fixture()
def shift_policy(policy) -> ShiftPolicy:
    return ShiftPolicy.from_policy(policy)


def _local(shift_policy: ShiftPolicy, day: date, hhmm: str) -> int:
    return to_minute(local_to_utc(datetime.combine(day, time.fromisoformat(hhmm)), shift_policy.tz))


def test_s3_starts_after_local_midnight_are_on_the_next_day(shift_policy) -> None:
    s3 = build_shift(DAY, 3, shift_policy)
    first = shift_policy.start_local[3].strftime("%H:%M")
    assert shift_local_minute(DAY, 3, first, shift_policy) == s3.start
    after_midnight = shift_local_minute(DAY, 3, "02:30", shift_policy)
    assert after_midnight == _local(shift_policy, DAY + timedelta(days=1), "02:30")
    assert s3.start < after_midnight < s3.end and s3.valid_start(after_midnight)
    # day shifts never cross midnight
    s1 = build_shift(DAY, 1, shift_policy)
    assert shift_local_minute(DAY, 1, "06:15", shift_policy) == _local(shift_policy, DAY, "06:15")
    assert s1.start <= shift_local_minute(DAY, 1, "06:15", shift_policy) < s1.end


def test_a_slot_in_the_hour_the_clocks_repeat_is_read_back_as_the_same_instant(shift_policy) -> None:
    """On the night the clocks go back (November 1, 2026 at this site) the hour from 01:00 happens twice inside S3. The
    second one records its UTC offset; without it the slot was read back an hour early and could not be kept."""
    night = date(2026, 10, 31)
    s3 = build_shift(night, 3, shift_policy)
    for hour, wall in ((4, "00:30"), (5, "01:30"), (6, "01:30-05:00"), (7, "02:30")):
        instant = datetime(2026, 11, 1, hour, 30, tzinfo=timezone.utc)
        assert local_wall(instant, shift_policy.tz) == wall
        minute = shift_local_minute(night, 3, wall, shift_policy)
        assert minute == to_minute(instant) and s3.start <= minute < s3.end
    assert shift_local_minute(night, 3, "01:30", shift_policy) + 60 == shift_local_minute(night, 3, "01:30-05:00", shift_policy)
    # every other night, and the spring change, write the plain wall time
    assert local_wall(datetime(2026, 10, 27, 6, 30, tzinfo=timezone.utc), shift_policy.tz) == "02:30"
    spring = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)                       # 03:30 local, after the skipped hour
    assert local_wall(spring, shift_policy.tz) == "03:30"
    assert shift_local_minute(date(2026, 3, 7), 3, "03:30", shift_policy) == to_minute(spring)


def _phase(job: str, kind: PhaseKind, ordinal: int, minutes: int):
    return SimpleNamespace(key=SimpleNamespace(id=f"{job}.{kind.value}.{ordinal}"), kind=kind, ordinal=ordinal, work_minutes=minutes,
                           label=f"{job} {kind.value} {ordinal}")


def _job(*pieces: tuple[PhaseKind, int]):
    counters: dict[PhaseKind, int] = {}
    out = []
    for kind, minutes in pieces:
        out.append(_phase("J", kind, counters.get(kind, 0), minutes))
        counters[kind] = counters.get(kind, 0) + 1
    return out


W, IP, FQ = PhaseKind.WORK, PhaseKind.IP_QA, PhaseKind.FINAL_QA


def test_minutes_to_go_survive_the_resplit_after_progress() -> None:
    before = _job((W, 200), (W, 200), (IP, 45), (W, 200), (FQ, 45))
    togo = work_to_go(before)
    assert [togo.get(p.key.id) for p in before] == [600, 400, 200, 200, None]
    # the first piece was finished and written back: what is left is renumbered from 0
    after = _job((W, 200), (IP, 45), (W, 200), (FQ, 45))
    for p in before:
        if p.key.id not in togo:
            continue
        located = locate_position(after, p.kind, togo[p.key.id])
        assert located is not None
        if p.key.id == "J.WORK.0":
            assert located == (None, True)                       # performed since acceptance
        else:
            phase, performed = located
            assert not performed and work_to_go(after)[phase.key.id] == togo[p.key.id] and phase.kind is p.kind
    assert locate_position(after, W, 400)[0].key.id == "J.WORK.0"    # the old WORK.1 is the new WORK.0
    assert locate_position(after, IP, 200)[0].key.id == "J.IP_QA.0"


def test_partial_progress_maps_each_committed_piece_by_where_its_work_starts() -> None:
    # 600 minutes in three pieces; 180 minutes performed, the remaining 420 re-split into two pieces of 210
    after = _job((W, 210), (W, 210))
    assert locate_position(after, W, 600) == (None, True)
    assert locate_position(after, W, 400)[0].key.id == "J.WORK.0"
    assert locate_position(after, W, 200)[0].key.id == "J.WORK.1"
    assert locate_position(after, IP, 300) == (None, False)          # no hold there any more: nothing to defend


def test_positions_do_not_apply_without_known_remaining_work() -> None:
    assert locate_position(_job((W, 0)), W, 120) is None                # duration unavailable: matched by ordinal instead
    assert locate_position(_job((W, 200), (FQ, 45)), FQ, 0) is None      # final inspection keeps its ordinal
