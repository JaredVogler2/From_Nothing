"""B10: a phase's latest start walks back through its line's actual open shift windows (S12 MAX step 3: no packed days)."""
from __future__ import annotations

from focus.engine.max.optimizer.urgency import OpenClock

DAY = 1440


def test_latest_start_walks_back_through_open_windows() -> None:
    clock = OpenClock([(0, 100), (200, 300)])
    assert [clock.latest_start(300, m) for m in (0, 50, 100, 150, 200, 250)] == [300, 250, 200, 50, 0, -50]
    assert clock.latest_start(150, 30) == 70 and clock.latest_start(250, 30) == 220      # due in a gap, due inside a window
    assert clock.before(250) == 150 and clock.before(-5) == 0 and clock.before(10_000) == 200
    assert OpenClock([(0, 100), (50, 150)]).total == 150                                  # overlapping windows count once
    assert OpenClock([]).latest_start(500, 60) == 440                                     # no admitted calendar: minute for minute


def test_a_closed_weekend_is_never_averaged_away() -> None:
    """Weekdays open 08:00-16:00, the weekend closed, due Monday 16:00 with two days of work left on the critical chain.
    The work must start by Friday 08:00. A whole-horizon average (calendar minutes per open minute) would allow Saturday."""
    windows = [(d * DAY + 480, d * DAY + 960) for d in range(8) if d % 7 not in (5, 6)]
    clock = OpenClock(windows)
    due, tail = 7 * DAY + 960, 2 * 480
    assert clock.latest_start(due, tail) == 4 * DAY + 480                                 # Friday 08:00
    span, opened = windows[-1][1] - windows[0][0], sum(e - s for s, e in windows)
    averaged = due - -(-tail * span // opened)
    assert averaged > 5 * DAY                                                             # the averaged latest start falls on Saturday
