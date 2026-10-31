"""Dispatch urgency for the economic passes and the search's long-wait focus (S12 MAX steps 5-7).

**Cost of delay.** Which aircraft gets a mechanic is decided, among ready phases of one dispatch class, by the cost of
delaying each phase. That cost is its aircraft's economic weight from ``EconomicsRegistry.derive_priority_weights``: the
marginal penalty of one more day at the aircraft's projected completion in the seed, under its customer and minor-model
terms, penalty curve and cap. An aircraft already at its cap carries no weight. The weight is reduced by how much slack
the phase has before it would make the aircraft late. A phase on the critical chain of a late aircraft carries the full
weight; a phase with days of slack carries little, whatever its aircraft. This is the apparent-tardiness-cost rule for
weighted lateness, made exact for repeatability:

* slack = latest start - earliest start, where latest start = due - CPM tail x the line's calendar factor (calendar
  minutes per open work minute) and earliest start is the phase's start in the neutral seed (the cutoff when unplaced);
* decay = ``COD_DECAY[slack // COD_STEP_MINUTES]``, an integer table of exp(-i/4) in millionths, so one day of slack
  divides the urgency by e; no floating point is involved, so every platform orders phases identically;
* aircraft without a known weight carry no urgency (unknown is never zero cost; it simply does not rank).

**Long waits.** A phase that waited more than ``LONG_WAIT_MINUTES`` past its earliest start lost that time to other jobs
of its team. The search aims part of its moves at the jobs with the longest waits, moving each ahead of the jobs it
competes with (the ones that held its team) and keeping the result only when the whole fleet is strictly better.
"""
from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING, Mapping

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


def calendar_factor(ctx: "EngineContext", line: int) -> Fraction:
    """Calendar minutes per open regular minute over the line's admitted shifts (1 when it has none)."""
    shifts = ctx.line_shifts(line)
    open_minutes = sum(s.open_minutes for s in shifts)
    if not shifts or open_minutes <= 0:
        return Fraction(1)
    return Fraction(shifts[-1].end - shifts[0].start, open_minutes)


def cost_of_delay(ctx: "EngineContext", registry: "EconomicsRegistry", seed: "DecodeResult",
                  weights: Mapping[int, "WeightRecord"]) -> dict[str, int]:
    """phase -> the aircraft's registry weight (marginal cents of one more day at its projected completion) x decay(slack);
    phases of aircraft without a known, positive weight are absent."""
    factors: dict[int, Fraction] = {}
    out: dict[str, int] = {}
    for pid, phase in ctx.phases.items():
        line = phase.line
        weight = weights.get(line)
        if weight is None or weight.status != "known" or not weight.marginal_cents:
            continue
        econ = registry.economics[line]
        factor = factors.get(line)
        if factor is None:
            factor = factors[line] = calendar_factor(ctx, line)
        tail = ctx.tails.get(pid, 0) * factor
        latest = to_minute(econ.due_at) - (tail.numerator + tail.denominator - 1) // tail.denominator   # type: ignore[arg-type]
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
