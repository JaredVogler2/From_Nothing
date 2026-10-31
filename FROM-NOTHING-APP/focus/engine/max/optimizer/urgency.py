"""Dispatch urgency for the economic passes and the search's long-wait focus (S12 MAX steps 5-7).

**Cost of delay.** Which aircraft gets a mechanic is decided, among ready phases of one dispatch class, by the cost of
delaying each phase. That cost is its aircraft's economic weight from ``EconomicsRegistry.derive_priority_weights``: the
marginal penalty of one more day at the aircraft's projected completion in the seed, under its customer and minor-model
terms, penalty curve and cap. An aircraft already at its cap carries no weight. The weight is reduced by how much slack
the phase has before it would make the aircraft late. A phase on the critical chain of a late aircraft carries the full
weight; a phase with days of slack carries little, whatever its aircraft. This is the apparent-tardiness-cost rule for
weighted lateness, made exact for repeatability:

* slack = latest start - earliest start. The latest start is the last instant from which the phase's CPM tail (work
  minutes on its critical chain) still fits into the line's open regular minutes before the due instant: a walk back
  through the actual admitted shift windows, with no averaged or packed days (``OpenClock``). The earliest start is the
  phase's start in the neutral seed (the cutoff when unplaced);
* decay = ``COD_DECAY[slack // COD_STEP_MINUTES]``, an integer table of exp(-i/4) in millionths, so one day of slack
  divides the urgency by e; no floating point is involved, so every platform orders phases identically;
* aircraft without a known weight carry no urgency (unknown is never zero cost; it simply does not rank).

**Long waits.** A phase that waited more than ``LONG_WAIT_MINUTES`` past its earliest start lost that time to other jobs
of its team. The search aims part of its moves at the jobs with the longest waits, moving each ahead of the jobs it
competes with (the ones that held its team) and keeping the result only when the whole fleet is strictly better.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import TYPE_CHECKING, Iterable, Mapping

from ....domain import to_minute

if TYPE_CHECKING:  # pragma: no cover
    from ....economics.registry import EconomicsRegistry, WeightRecord
    from ...model import EngineContext
    from .greedy import DecodeResult

COD_STEP_MINUTES = 360                    # 6 hours per table step; 4 steps (one day) divide urgency by e
COD_DECAY = (1000000, 778801, 606531, 472367, 367879, 286505, 223130, 173774, 135335, 105399, 82085, 63928, 49787, 38774,
             30197, 23518, 18316, 14264, 11109, 8652, 6738, 5248, 4087, 3183, 2479, 1930, 1503, 1171, 912, 710, 553, 431, 335,
             261, 203, 158, 123, 96, 75, 58, 45, 35, 28, 21, 17, 13, 10, 8, 6, 5, 4, 3, 2, 2, 1, 1, 1, 1, 1)
LONG_WAIT_MINUTES = 480                   # more than a shift past the phase's earliest start
LONG_WAIT_FOCUS = 40                      # the longest waits the search aims at


class OpenClock:
    """The open regular minutes of one line's admitted shifts as a cumulative clock (S12: no packed-day shortcut).

    ``before(t)`` counts open minutes before ``t``. ``latest_start(due, minutes)`` inverts it: the last instant from which
    ``minutes`` open minutes still end by ``due``. Outside the admitted calendar nothing is known to be open, so a remainder
    before the first window is counted minute for minute (earlier, so never less urgent).
    """

    def __init__(self, windows: Iterable[tuple[int, int]]):
        self.starts: list[int] = []
        self.ends: list[int] = []
        self.cum: list[int] = []                         # open minutes before each window
        total = 0
        for ws, we in sorted(windows):
            if we <= ws:
                continue
            if self.ends and ws < self.ends[-1]:          # overlapping windows count once
                ws = self.ends[-1]
                if we <= ws:
                    continue
            self.starts.append(ws)
            self.ends.append(we)
            self.cum.append(total)
            total += we - ws
        self.total = total

    @classmethod
    def of_line(cls, ctx: "EngineContext", line: int) -> "OpenClock":
        return cls(w for s in ctx.line_shifts(line) for w in s.regular_windows)

    def before(self, t: int) -> int:
        i = bisect_right(self.starts, t) - 1
        if i < 0:
            return 0
        return self.cum[i] + min(t, self.ends[i]) - self.starts[i]

    def latest_start(self, due: int, minutes: int) -> int:
        if not self.starts:
            return due - minutes
        before_due = self.before(due)
        target = before_due - minutes                       # open minutes allowed before the start
        if minutes <= 0 or target >= self.total:
            return due
        if target < 0:
            return self.starts[0] + target
        i = bisect_right(self.cum, target) - 1              # the last window that opens with at most target minutes behind it
        return self.starts[i] + target - self.cum[i]        # inside it: later windows open with more than target behind them


def cost_of_delay(ctx: "EngineContext", registry: "EconomicsRegistry", seed: "DecodeResult",
                  weights: Mapping[int, "WeightRecord"]) -> dict[str, int]:
    """phase -> the aircraft's registry weight (marginal cents of one more day at its projected completion) x decay(slack);
    phases of aircraft without a known, positive weight are absent."""
    clocks: dict[int, OpenClock] = {}
    out: dict[str, int] = {}
    for pid, phase in ctx.phases.items():
        line = phase.line
        weight = weights.get(line)
        if weight is None or weight.status != "known" or not weight.marginal_cents:
            continue
        econ = registry.economics[line]
        clock = clocks.get(line)
        if clock is None:
            clock = clocks[line] = OpenClock.of_line(ctx, line)
        latest = clock.latest_start(to_minute(econ.due_at), ctx.tails.get(pid, 0))     # type: ignore[arg-type]
        placed = seed.placements.get(pid)
        earliest = placed.start if placed is not None else ctx.cutoff_minute
        step = max(0, latest - earliest) // COD_STEP_MINUTES
        decay = COD_DECAY[step] if step < len(COD_DECAY) else 0
        if decay:
            out[pid] = int(weight.marginal_cents * decay)
    return out


def long_waits(decode: "DecodeResult", focus: int = LONG_WAIT_FOCUS) -> list[str]:
    """Jobs whose phases waited longest past their earliest start (more than ``LONG_WAIT_MINUTES``), longest first."""
    jobs: list[str] = []
    seen: set[str] = set()
    for wait, pid in sorted(((w, pid) for pid, w in decode.waits.items() if w > LONG_WAIT_MINUTES), key=lambda r: (-r[0], r[1])):
        job = decode.placements[pid].job
        if job not in seen:
            seen.add(job)
            jobs.append(job)
            if len(jobs) >= focus:
                break
    return jobs


def urgency_summary(urgency: Mapping[str, int]) -> str:
    return f"cost of delay for {len(urgency)} phases (registry marginal weight at the projected completion, decayed by slack)"
