"""The dispatch rule decides the same on display work parts as on the whole admitted graph.

A synthetic two-aircraft fleet has unconfirmed part releases upstream of most work and an AND release phase in front of
every job. For every team, the work parts of that team (with its pools) must give ``ValidationInput`` and the dispatch rule
(``publication.dispatch.assess_action``, ``performance.amendments.assess_addition``) the same answers as the whole
snapshot: satisfaction, placeability, readiness of every hard predecessor, the unconfirmed part releases among the
ancestors, and the full outcome of an emergency addition or of releasing a planned action in each early shift.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from focus.calendar import ShiftPolicy, build_shift, parse_shift_id
from focus.clock import FixedClock
from focus.config import Settings
from focus.contracts.planning import Assignment, CrewRole
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.display.build import phase_team, release_ancestors
from focus.display.store import load_part, write_snapshot_display
from focus.display.view import DisplayCanon, slice_input
from focus.domain import DomainError, Purpose, ReleaseKind, ReleaseStatus, from_minute
from focus.performance.amendments import _ready, assess_addition
from focus.publication.dispatch import assess_action
from focus.runtime import build_services
from focus.validation.input import ValidationInput


@pytest.fixture(scope="module")
def fleet(tmp_path_factory, app_root):
    from focus.data.fullscale_fixture import FleetSpec, build_fullscale_fixture
    from focus.sim.engine import admit
    from focus.sim.state import SourceState

    spec = FleetSpec(n_aircraft=2, baseline=240, edges=1000, rework_total=90, teams=4, team_size=(4, 6))
    state = SourceState(build_fullscale_fixture(spec))
    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False)
    clock = FixedClock(datetime(2026, 10, 26, 9, 0, tzinfo=UTC))
    repos = Repositories(FixtureStore(tmp_path_factory.mktemp("display_dispatch") / "runtime"), mode="fixture", scope_key=settings.scope_key,
                         clock=clock)
    services = build_services(settings, repositories=repos, clock=clock)
    shift = build_shift(spec.cutoff_day, 1, ShiftPolicy.from_policy(services.policy), settings.site)
    cutoff = from_minute(shift.start - 15)
    clock._instant = cutoff
    snap, _ = admit(services, state, cutoff=cutoff, run_id="SR-DISPATCH", created_at=cutoff)
    manifest = write_snapshot_display(repos, snap)
    summary = load_part(repos, manifest.part("summary"))
    canon = DisplayCanon(services, snap.source_run_id, summary, manifest, {"EXECUTABLE": None, "FORECAST": None}, "NO_ACCEPTED_PLAN",
                         "NO_ACCEPTED_PLAN", False, [])
    full = ValidationInput.build(snap, services.policy, Purpose.EXECUTABLE)
    return services, snap, canon, full, cutoff


def unconfirmed_parts(snap: Any, nodes: Any) -> list[str]:
    releases = {r.id: r for r in snap.releases}
    return sorted(n for n in nodes if n in releases and releases[n].kind is ReleaseKind.PART and releases[n].status is not ReleaseStatus.CONFIRMED)


def decided(call: Any) -> Any:
    try:
        return ("ok", call())
    except DomainError as exc:
        return (exc.code, exc.message)


def test_the_fleet_exercises_part_ancestors_and_and_releases(fleet) -> None:
    _, snap, _, _, _ = fleet
    anc = release_ancestors(snap)
    assert sum(1 for p in snap.phases if anc.get(p.key.id)) > 500                    # most work waits on an unreceived part
    assert any(p.kind.value == "RELEASE" for p in snap.phases)


def test_release_ancestors_are_the_unconfirmed_parts_the_whole_graph_finds(fleet) -> None:
    _, snap, _, full, _ = fleet
    anc, memo = release_ancestors(snap), {}
    for p in snap.phases:
        assert list(anc.get(p.key.id, ())) == unconfirmed_parts(snap, full.ancestors(p.key.id, memo)), p.label


def test_dispatch_rule_decides_the_same_on_each_teams_work_parts(fleet) -> None:
    services, snap, canon, full, cutoff = fleet
    shift_ids = [s for s in snap.shift_ids if parse_shift_id(s)[1] <= cutoff.date() + timedelta(days=1)]
    days = sorted({parse_shift_id(s)[1] for s in shift_ids})
    at, memo = cutoff + timedelta(minutes=30), {}
    now = int(at.timestamp() // 60)
    checked = 0
    for team in canon.summary.teams:
        part = canon.work([team]).pools([team], days)
        inp = slice_input(part)
        mine = [p for p in snap.phases if phase_team(p) == team]
        assert {p.key.id for p in part.snapshot.phases if phase_team(p) == team} == {p.key.id for p in mine}
        for p in mine:
            pid = p.key.id
            assert inp.satisfied(inp.phases[pid]) == full.satisfied(full.phases[pid])
            assert inp.placeable(p.job) == full.placeable(p.job)
            assert sorted(inp.ancestors(pid, {})) == unconfirmed_parts(snap, full.ancestors(pid, memo))
            for e in full.hard_in.get(pid, ()):
                assert _ready(inp, e.pred_phase, now) == _ready(full, e.pred_phase, now), (p.label, e.pred_phase)
            for shift_id in shift_ids:
                assert decided(lambda: assess_addition(inp, part.snapshot, None, None, team=team, shift_id=shift_id, phase_id=pid, at=at)) == \
                    decided(lambda: assess_addition(full, snap, None, None, team=team, shift_id=shift_id, phase_id=pid, at=at)), (p.label, shift_id)
                action = planned_action(full, p, shift_id)
                if action is not None:
                    assert assess_action(services, action, part.snapshot, inp, at) == assess_action(services, action, snap, full, at), (p.label, shift_id)
                    checked += 1
    assert checked > 100


def planned_action(full: ValidationInput, phase: Any, shift_id: str) -> Assignment | None:
    """A release of this phase in the shift by the first members of its team's pool (a plausible planned action)."""
    demand = phase.role_demands[0] if phase.role_demands else None
    shift = full.shift(shift_id)
    if demand is None or shift is None or phase.work_minutes <= 0:
        return None
    pool = full.pool_by_key.get((demand.team, shift.start_date, shift.number))
    members = sorted(pool.members) if pool is not None else []
    if not members:
        return None
    start, end = from_minute(shift.start), from_minute(min(shift.end, shift.start + phase.work_minutes))
    crew = tuple(CrewRole(m, demand.bundle.role, (), "") for m in members[:max(1, demand.bundle.count)])
    job = full.jobs[phase.job]
    return Assignment(f"A-{phase.key.id}-{shift_id}", phase.key.id, phase.job, job.line, phase.kind, start, end, (), phase.work_minutes, shift_id,
                      pool.id, crew, Purpose.EXECUTABLE, (), None, (), False, None)
