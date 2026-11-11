"""How critical a job is toward the cost objective (owner direction of 2026-11-10, DECISIONS.md D-29).

The scheduler minimizes the fleet's modeled late-delivery cost. What a lost day of work costs depends on the aircraft:
nothing in modeled dollars while the aircraft still finishes ahead of its due date, and its daily rate once it is late.
The scheduler's own search orders work by the same measure, taken on its first schedule
(``EconomicsRegistry.derive_priority_weights``). So:

    criticality of a job = the modeled cost of one more day late for its aircraft, in the accepted forecast plan,
                           as a share of the highest such cost in the fleet (0 to 1).

It is read from the plan's cost lines (``LineCost.marginal_day_cents``, the registry's marginal at the projected
finish), so it needs no second model and nothing is estimated:

* ``LATE``: the plan finishes the aircraft after its due date; one more day costs its daily rate.
* ``ON_TIME``: the plan finishes it before the due date; one more day costs only what would fall past the due date.
* ``PAST_DUE``: the due date has passed and the finish date is unknown; every further day costs the rate whatever the
  finish turns out to be, so the criticality is known although the cost is not.
* ``FINISH_UNKNOWN``: not yet due and no finish date; whether a lost day costs anything is unknown. Never read as zero.
* ``NO_RATE_OR_DUE_DATE``: no usable rate or no due date. Unknown, never read as zero and never filled in.
* ``NO_OPEN_WORK``: nothing left to do on the aircraft.

It is a property of the aircraft, shared by its jobs. On the synthetic fleets a single job slipping one shift moved no
delivery (378 of 378 jobs at 5 aircraft, re-planned exactly; 3,189 of 3,189 at 50 aircraft by the schedule graph):
one job can be re-sequenced, and what moves a delivery is how much of the aircraft's work gets done
(docs/task-valuation-investigation/06 and 08). Which job of an aircraft comes first is the plan's sequence.

Reported only. No plan, goal or score reads it; whether it should weight a team's score is the owner's decision
(DECISION_PLAYBOOK.md, DEC-31).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any, Mapping

from ..contracts.planning import CostSummary
from ..economics.evaluator import reason_codes

LATE, ON_TIME, PAST_DUE = "LATE", "ON_TIME", "PAST_DUE"
FINISH_UNKNOWN, NO_RATE_OR_DUE_DATE, NO_OPEN_WORK = "FINISH_UNKNOWN", "NO_RATE_OR_DUE_DATE", "NO_OPEN_WORK"
STATES = (LATE, PAST_DUE, ON_TIME, NO_OPEN_WORK, FINISH_UNKNOWN, NO_RATE_OR_DUE_DATE)
UNKNOWN_STATES = frozenset({FINISH_UNKNOWN, NO_RATE_OR_DUE_DATE})


@dataclass(frozen=True, slots=True)
class AircraftCriticality:
    line: int
    state: str                              # one of STATES
    cost_per_day_cents: Fraction | None     # modeled cost of one more day late; None when unknown
    weight: Fraction | None                 # share of the fleet's highest cost per day, 0 to 1; None when unknown


def aircraft_criticality(costs: CostSummary, planned_at: datetime) -> dict[int, AircraftCriticality]:
    """Each aircraft's criticality in one plan. ``planned_at`` is when the plan was made, to tell a due date that has
    passed from one that has not."""
    found: dict[int, tuple[str, Fraction | None]] = {}
    for c in costs.lines:
        if c.status in ("known", "conditional"):
            if c.completion_at is None:
                found[c.line] = (NO_OPEN_WORK, Fraction(0))
            else:
                found[c.line] = (LATE if (c.delay_minutes or 0) > 0 else ON_TIME, c.marginal_day_cents)
        elif "COMPLETION_UNKNOWN" in reason_codes(c.reasons) and c.due_at is not None and c.marginal_day_cents is not None:
            # the rate and the due date are known and the finish is not: past due, a further day costs the rate at any finish
            found[c.line] = (PAST_DUE, c.marginal_day_cents) if c.due_at <= planned_at else (FINISH_UNKNOWN, None)
        else:
            found[c.line] = (NO_RATE_OR_DUE_DATE, None)
    peak = max((cost for _, cost in found.values() if cost is not None), default=Fraction(0))
    return {line: AircraftCriticality(line, state, cost, None if cost is None else (cost / peak if peak > 0 else Fraction(0)))
            for line, (state, cost) in sorted(found.items())}


def job_criticality(criticality: Mapping[int, AircraftCriticality], line: int) -> Fraction | None:
    """A job's criticality is its aircraft's (0 to 1); None when unknown, which is never zero."""
    entry = criticality.get(line)
    return None if entry is None else entry.weight


def report(criticality: Mapping[int, AircraftCriticality]) -> dict[str, Any]:
    """The plan's criticality for a status page: counts by state, the highest cost per day, and each aircraft's line."""
    states = Counter(c.state for c in criticality.values())
    known = [c for c in criticality.values() if c.cost_per_day_cents is not None]
    peak = max((c.cost_per_day_cents for c in known), default=None)

    def usd(cents: Fraction | None) -> int | None:
        return None if cents is None else int((cents + 50) // 100)            # whole dollars, half up

    return {"aircraft": len(criticality), "known": len(known), "unknown": len(criticality) - len(known),
            "states": {s: states[s] for s in STATES if states[s]},
            "highest_cost_per_day_usd": usd(peak),
            "lines": [{"line": c.line, "state": c.state, "cost_per_day_usd": usd(c.cost_per_day_cents),
                       "weight": None if c.weight is None else round(float(c.weight), 4)} for c in criticality.values()]}
