"""P06: pure pools, exact activation caps, no borrowing, isolated virtual support (A10, A11, A64)."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest

from focus.data.workforce import activation_cap
from focus.domain import AvailabilityBasis, PersonKey, PersonOrigin, RequirementBundle, minute_floor
from focus.eligibility import Eligibility, RoleSlot, match_crew
from focus.simulation.pools import VIRTUAL_NAMESPACE, admit_executable_crew, is_synthetic_person


def _person(native: str) -> str:
    return PersonKey(native, "fixture-demo").id


def _pools(snapshot):
    return {(p.key.team, p.key.start_date, p.key.shift_number): p for p in snapshot.pools}


@pytest.mark.parametrize(("n", "cap"), [(0, 0), (1, 0), (2, 1), (7, 5), (8, 6), (10, 8), (20, 17)])
def test_activation_cap_is_exact_floor_without_minimum_one(n: int, cap: int) -> None:
    assert activation_cap(n) == cap
    with pytest.raises(ValueError):
        activation_cap(-1)


def test_a10_pool_denominators_and_caps_from_admitted_people(demo_snapshot) -> None:
    pools = _pools(demo_snapshot)
    today = pools[("PROD-A", date(2026, 10, 26), 1)]                 # A1-03 absent on the production day
    assert (today.n_executable, today.cap_executable) == (7, 5)
    tomorrow = pools[("PROD-A", date(2026, 10, 27), 1)]               # rostered, assignment-backed, all eight
    assert (tomorrow.n_executable, tomorrow.cap_executable) == (8, 6)
    leave = pools[("PROD-A", date(2026, 10, 28), 1)]                  # A1-06 planned leave
    assert (leave.n_executable, leave.cap_executable) == (7, 5) and f"{_person('DEMO-A1-06')}:LEAVE" in leave.excluded
    for pool in demo_snapshot.pools:
        assert pool.n_executable == len(set(pool.executable_members)) and pool.cap_executable == activation_cap(pool.n_executable)
        assert pool.n_forecast == len(set(pool.forecast_members)) and pool.cap_forecast == activation_cap(pool.n_forecast)
        assert set(pool.executable_members) <= set(pool.forecast_members) <= set(pool.members)
        assert len(pool.members) == len(set(pool.members))             # canonical distinct people, no double headcount


def test_a10_every_person_is_in_at_most_one_pool_per_shift(demo_snapshot) -> None:
    seen: dict[tuple[date, int, str], str] = {}
    for pool in demo_snapshot.pools:
        for person in pool.members:
            slot = (pool.key.start_date, pool.key.shift_number, person)
            assert slot not in seen, f"{person} double-booked in {seen.get(slot)} and {pool.key.team}"
            seen[slot] = pool.key.team


def _interval(day: str) -> tuple[int, int]:
    return minute_floor(datetime.fromisoformat(f"{day}T11:00:00+00:00")), minute_floor(datetime.fromisoformat(f"{day}T14:00:00+00:00"))


def test_a11_target_pool_cannot_field_crew_and_nobody_is_borrowed(demo_snapshot) -> None:
    pools = _pools(demo_snapshot)
    requirements = {r.job: r for r in demo_snapshot.requirements}
    elig = Eligibility(demo_snapshot.holdings, requirements, source_run_id=demo_snapshot.source_run_id, policy_hash=demo_snapshot.policy_hash)
    target = pools[("PROD-B", date(2026, 10, 27), 1)]
    need_c = RoleSlot("C1", "mechanic", RequirementBundle((("C",),), "mechanic", 1, "test"))
    start, end = _interval("2026-10-27")
    crew = match_crew([need_c], target.executable_members, lambda p, s: elig.index.check_bundle(p, s.bundle, start, end))
    assert crew.status == "INFEASIBLE" and crew.reason.startswith("SLOT_NO_ELIGIBLE_PERSON")   # work waits with a reason
    holder = _person("DEMO-A1-07")                                    # holds C, but belongs to PROD-A S1
    assert elig.index.holds(holder, "C", start, end)[0] is True
    assert holder not in target.members and holder not in crew.people
    revoked = _person("DEMO-B1-04")                                    # C status REVOKED: not a phantom C holder
    assert elig.index.holds(revoked, "C", start, end) == (False, "STATUS_REVOKED")


def test_a11_admission_rejects_cross_team_and_cross_shift_people(demo_snapshot) -> None:
    pools = _pools(demo_snapshot)
    people = {p.id: p for p in demo_snapshot.people}
    prod_b = pools[("PROD-B", date(2026, 10, 27), 1)]
    ok = admit_executable_crew([_person("DEMO-B1-01"), _person("DEMO-B1-03")], prod_b, people)
    assert ok.allowed
    borrowed = admit_executable_crew([_person("DEMO-B1-01"), _person("DEMO-A1-07")], prod_b, people)
    assert not borrowed.allowed and borrowed.code == "NOT_EXECUTABLE_POOL_MEMBER"
    other_shift = admit_executable_crew([_person("DEMO-B2-01")], prod_b, people)
    assert not other_shift.allowed and other_shift.code == "NOT_EXECUTABLE_POOL_MEMBER"
    twice = admit_executable_crew([_person("DEMO-B1-01"), _person("DEMO-B1-01")], prod_b, people)
    assert twice.code == "CREW_DUPLICATE_PERSON"


def test_a64_exactly_ten_virtual_people_per_configured_team_shift(demo_snapshot) -> None:
    virtual_teams = {"MHE", "PSC", "TST"}
    people = {p.id: p for p in demo_snapshot.people}
    synthetic_pools = [p for p in demo_snapshot.pools if p.key.team in virtual_teams]
    assert synthetic_pools and {p.key.team for p in synthetic_pools} == virtual_teams
    for pool in synthetic_pools:
        assert len(pool.forecast_members) == 10 == len(set(pool.forecast_members)) and pool.cap_forecast == 8
        assert pool.executable_members == () and pool.n_executable == 0 and pool.cap_executable == 0
        assert pool.synthetic and "SYNTHETIC_VIRTUAL_SUPPORT" in pool.basis
        for pid in pool.forecast_members:
            person = people[pid]
            assert person.origin is PersonOrigin.SYNTHETIC_VIRTUAL and person.synthetic and person.purpose_scope == "FORECAST_ONLY"
            assert person.key.authority_namespace == VIRTUAL_NAMESPACE
    # no virtual people for teams that are not configured (no DFR or wildcard teams)
    assert not [p for p in demo_snapshot.people if is_synthetic_person(p) and p.key.native_id.split("-")[1] not in virtual_teams]
    shifts = {(p.key.start_date, p.key.shift_number) for p in synthetic_pools if p.key.team == "MHE"}
    assert len([p for p in synthetic_pools if p.key.team == "MHE"]) == len(shifts)         # one pool per team/shift


def test_a64_virtual_people_hold_the_union_of_team_required_certs(demo_snapshot) -> None:
    by_person: dict[str, set[str]] = {}
    for h in demo_snapshot.holdings:
        by_person.setdefault(h.person, set()).add(h.cert)
    union = {"MHE": {"MHE-OP"}, "TST": {"TST-1"}, "PSC": set()}      # PSC6 is affirmatively cert-free
    for person in demo_snapshot.people:
        if is_synthetic_person(person):
            team = person.key.native_id.split("-")[1]
            assert by_person.get(person.id, set()) == union[team]
    virtual_avail = [a for a in demo_snapshot.availability if a.basis is AvailabilityBasis.SYNTHETIC]
    assert virtual_avail and all(not a.admitted_presence for a in virtual_avail)


def test_a64_spoofed_flags_cannot_make_virtual_people_executable(demo_snapshot) -> None:
    people = {p.id: p for p in demo_snapshot.people}
    pool = next(p for p in demo_snapshot.pools if p.key.team == "MHE")
    virtual = list(pool.forecast_members[:2])
    claims = {"mock_data": False, "purpose": "PRODUCTION", "synthetic": False}
    decision = admit_executable_crew(virtual, pool, people, claims=claims)
    assert not decision.allowed and decision.code == "POOL_SYNTHETIC_ONLY" and "payload claims ignored" in decision.reason
    # even a payload that rewrites the stored flags keeps the synthetic namespace in its identity
    spoofed = {pid: dataclasses.replace(people[pid], synthetic=False, origin=PersonOrigin.SOURCE, purpose_scope="ANY") for pid in virtual}
    assert all(is_synthetic_person(p) for p in spoofed.values())
    real_pool = next(p for p in demo_snapshot.pools if p.key.team == "PROD-A" and p.n_executable)
    mixed = admit_executable_crew([real_pool.executable_members[0], virtual[0]], real_pool, {**people, **spoofed}, claims=claims)
    assert not mixed.allowed and mixed.code == "SYNTHETIC_NOT_EXECUTABLE"


def test_empty_attendance_feed_leaves_the_production_day_without_executable_crew(services_factory) -> None:
    """S05: an empty current attendance feed is unready for executable crew; the roster stays forecast evidence."""
    import json
    from pathlib import Path

    from focus.data.acquisition import load_snapshot
    from focus.data.fixture_reader import FixtureSourceReader
    from tests.conftest import acquire_demo

    inputs = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
    attendance = json.loads((inputs / "attendance_shift.json").read_text())
    attendance["rows"] = []
    services = services_factory()
    outcome = acquire_demo(services, key="empty-attendance", reader=FixtureSourceReader(inputs, overrides={"attendance_shift": attendance}))
    assert outcome.status == "COMPLETE", outcome.rejection
    snapshot = load_snapshot(services.repositories, outcome.snapshot_id)
    assert {g.code: g.capability_effect for g in snapshot.gaps}.get("ATTENDANCE_FEED_EMPTY") == "BLOCKS_EXECUTABLE"
    pools = _pools(snapshot)
    today = pools[("PROD-A", date(2026, 10, 26), 1)]
    assert today.n_executable == 0 and today.cap_executable == 0 and today.n_forecast > 0     # no presence evidence today
    assert pools[("PROD-A", date(2026, 10, 27), 1)].n_executable == 8                          # later days plan from the roster


def test_attendance_for_other_days_is_no_evidence_for_the_production_day(services_factory) -> None:
    """S05: the guard looks at the production day itself; a feed holding only yesterday's rows leaves today forecast-only."""
    import json
    from pathlib import Path

    from focus.data.acquisition import load_snapshot
    from focus.data.fixture_reader import FixtureSourceReader
    from tests.conftest import acquire_demo

    inputs = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
    attendance = json.loads((inputs / "attendance_shift.json").read_text())
    attendance["rows"] = [{**r, "PRODUCTION_DAY": "2026-10-25"} for r in attendance["rows"]]      # all for the day before
    services = services_factory()
    outcome = acquire_demo(services, key="stale-attendance", reader=FixtureSourceReader(inputs, overrides={"attendance_shift": attendance}))
    assert outcome.status == "COMPLETE", outcome.rejection
    snapshot = load_snapshot(services.repositories, outcome.snapshot_id)
    assert "ATTENDANCE_FEED_EMPTY" in {g.code for g in snapshot.gaps}
    assert _pools(snapshot)[("PROD-A", date(2026, 10, 26), 1)].n_executable == 0
