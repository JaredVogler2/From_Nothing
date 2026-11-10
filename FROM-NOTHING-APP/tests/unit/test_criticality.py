"""Criticality toward the cost objective (D-29): a job's criticality is its aircraft's modeled cost of one more day late
in the plan, as a share of the fleet's highest. Reported only; unknown is never zero."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fractions import Fraction

from focus.contracts.planning import AircraftEconomics, AircraftCompletion, LineCost
from focus.domain import to_minute
from focus.economics.evaluator import line_costs, summarize
from focus.economics.registry import EconomicsRegistry
from focus.valuation.criticality import (FINISH_UNKNOWN, LATE, NO_OPEN_WORK, NO_RATE_OR_DUE_DATE, ON_TIME, PAST_DUE,
                                         aircraft_criticality, job_criticality, report)

NOW = datetime(2027, 3, 10, 12, 0, tzinfo=timezone.utc)


def _econ(line: int, usd_per_day: int | None, due: datetime | None, reasons: tuple[str, ...] = ()) -> AircraftEconomics:
    known = usd_per_day is not None and due is not None
    return AircraftEconomics(line, "KNOWN" if known else "UNKNOWN", "ASSUMED" if usd_per_day is not None else "UNKNOWN",
                             f"T{line}" if usd_per_day is not None else None, "CUSTOMER_MODEL" if usd_per_day is not None else None,
                             "USD" if usd_per_day is not None else None, None if usd_per_day is None else Fraction(usd_per_day * 100),
                             None, None, None, due, "test due", "LINEAR_DAILY", reasons)


def _finish(line: int, at: datetime | None, unresolved: tuple[str, ...] = (), jobs: int = 5) -> AircraftCompletion:
    if unresolved:
        return AircraftCompletion(line, "unknown", None, jobs, jobs - len(unresolved), unresolved, ())
    return AircraftCompletion(line, "known", at, jobs, jobs, (), ())


def _fleet(policy):
    day = timedelta(days=1)
    economics = {
        1: _econ(1, 380_000, NOW + 2 * day),                       # finishes 3 days after due
        2: _econ(2, 190_000, NOW + 2 * day),                       # finishes 1 day after due, half the rate
        3: _econ(3, 380_000, NOW + 30 * day),                      # finishes weeks ahead of due
        4: _econ(4, 380_000, NOW + 10 * day),                      # finishes 12 hours before due
        5: _econ(5, 95_000, NOW - 4 * day),                        # due date passed, finish unknown
        6: _econ(6, 380_000, NOW + 5 * day),                       # not yet due, finish unknown
        7: _econ(7, None, NOW + 5 * day, ("NO_EFFECTIVE_TERM",)),  # no rate
        8: _econ(8, 380_000, None, ("DUE_UNKNOWN: no proxy",)),    # no due date
        9: _econ(9, 380_000, NOW + 5 * day),                       # nothing left to do
    }
    completions = {
        1: _finish(1, NOW + 5 * day), 2: _finish(2, NOW + 3 * day), 3: _finish(3, NOW + 6 * day),
        4: _finish(4, NOW + 10 * day - timedelta(hours=12)), 5: _finish(5, None, ("J5",)), 6: _finish(6, None, ("J6",)),
        7: _finish(7, NOW + 6 * day), 8: _finish(8, NOW + 6 * day),
        9: AircraftCompletion(9, "known", None, 0, 0, (), ("no open delivery scope (all verified complete)",)),
    }
    registry = EconomicsRegistry(economics, policy)
    return summarize(line_costs(registry, completions, to_minute(NOW)))


def test_each_aircraft_state_and_its_cost_of_one_more_day(policy) -> None:
    crit = aircraft_criticality(_fleet(policy), NOW)
    assert {line: c.state for line, c in crit.items()} == {1: LATE, 2: LATE, 3: ON_TIME, 4: ON_TIME, 5: PAST_DUE, 6: FINISH_UNKNOWN,
                                                           7: NO_RATE_OR_DUE_DATE, 8: NO_RATE_OR_DUE_DATE, 9: NO_OPEN_WORK}
    usd = {line: None if c.cost_per_day_cents is None else c.cost_per_day_cents / 100 for line, c in crit.items()}
    assert usd[1] == 380_000 and usd[2] == 190_000                # late: a further day costs the daily rate
    assert usd[3] == 0                                            # weeks of margin: a further day costs nothing modeled
    assert usd[4] == 190_000                                      # 12 hours of margin: half of the next day falls past due
    assert usd[5] == 95_000                                       # past due with no finish date: a further day still costs the rate
    assert usd[6] is None and usd[7] is None and usd[8] is None   # unknown is never zero
    assert usd[9] == 0


def test_weight_is_the_share_of_the_fleets_highest_cost_and_a_job_takes_its_aircrafts(policy) -> None:
    crit = aircraft_criticality(_fleet(policy), NOW)
    assert {line: c.weight for line, c in crit.items()} == {1: Fraction(1), 2: Fraction(1, 2), 3: Fraction(0), 4: Fraction(1, 2),
                                                            5: Fraction(1, 4), 6: None, 7: None, 8: None, 9: Fraction(0)}
    assert job_criticality(crit, 1) == 1 and job_criticality(crit, 5) == Fraction(1, 4)
    assert job_criticality(crit, 6) is None and job_criticality(crit, 99) is None          # unknown aircraft: unknown, not zero


def test_a_fleet_with_no_cost_pressure_has_weight_zero_everywhere_known(policy) -> None:
    day = timedelta(days=1)
    registry = EconomicsRegistry({1: _econ(1, 380_000, NOW + 30 * day), 2: _econ(2, 190_000, NOW + 40 * day)}, policy)
    costs = summarize(line_costs(registry, {1: _finish(1, NOW + 3 * day), 2: _finish(2, NOW + 4 * day)}, to_minute(NOW)))
    crit = aircraft_criticality(costs, NOW)
    assert [(c.state, c.cost_per_day_cents, c.weight) for c in crit.values()] == [(ON_TIME, 0, 0), (ON_TIME, 0, 0)]
    assert report(crit)["highest_cost_per_day_usd"] == 0


def test_the_report_counts_states_and_lists_each_aircraft(policy) -> None:
    r = report(aircraft_criticality(_fleet(policy), NOW))
    assert (r["aircraft"], r["known"], r["unknown"], r["highest_cost_per_day_usd"]) == (9, 6, 3, 380_000)
    assert r["states"] == {LATE: 2, PAST_DUE: 1, ON_TIME: 2, NO_OPEN_WORK: 1, FINISH_UNKNOWN: 1, NO_RATE_OR_DUE_DATE: 2}
    lines = {x["line"]: x for x in r["lines"]}
    assert lines[1] == {"line": 1, "state": LATE, "cost_per_day_usd": 380_000, "weight": 1.0}
    assert lines[5] == {"line": 5, "state": PAST_DUE, "cost_per_day_usd": 95_000, "weight": 0.25}
    assert lines[6] == {"line": 6, "state": FINISH_UNKNOWN, "cost_per_day_usd": None, "weight": None}
    empty = report(aircraft_criticality(summarize({}), NOW))
    assert (empty["aircraft"], empty["known"], empty["highest_cost_per_day_usd"], empty["lines"]) == (0, 0, None, [])


def test_an_unsupported_or_reasonless_line_is_unknown() -> None:
    costs = summarize({1: LineCost(1, "unsupported", "UNKNOWN", None, NOW, None, None, None, None, None, ("UNSUPPORTED_CURRENCY:EUR",)),
                       2: LineCost(2, "unknown", "UNKNOWN", None, None, None, None, None, None, None, ())})
    crit = aircraft_criticality(costs, NOW)
    assert all(c.state == NO_RATE_OR_DUE_DATE and c.weight is None and c.cost_per_day_cents is None for c in crit.values())
