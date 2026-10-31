"""B3: crew choice keeps holders of rare role bundles free for ready work that needs them (S11 SCARCITY), in both cores."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.acquisition import acquire, load_snapshot, make_reader, snapshot_for_run
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import DutyKind, Purpose
from focus.engine.crew_selection import choose_crew
from focus.engine.model import EngineContext
from focus.runtime import build_services


@pytest.fixture(scope="module")
def demo(app_root):
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False)
        clock = FixedClock(FIXTURE_CUTOFF)
        services = build_services(settings, repositories=Repositories(FixtureStore(Path(tmp)), mode="fixture", scope_key=settings.scope_key,
                                                                      clock=clock), clock=clock)
        outcome = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="b3")
        yield services, load_snapshot(services.repositories, outcome.snapshot_id), outcome.run_id


def _context(services, run_id: str, limit: int) -> EngineContext:
    policy = services.policy.with_overrides({"crew.rare_holders_max": limit}, new_id=f"b3-{limit}", authority="fixture_default", reason="B3")
    return EngineContext(snapshot_for_run(services, run_id, policy=policy, store=False), policy, Purpose.EXECUTABLE)


def test_the_reserve_counts_few_holders_of_bundles_that_ready_work_needs(demo) -> None:
    services, snapshot, run_id = demo
    strict = EngineContext(snapshot, services.policy, Purpose.EXECUTABLE)
    loose, none = _context(services, run_id, 1000), _context(services, run_id, 0)
    assert none.crew_reserve() == {}
    assert set(strict.crew_reserve()) <= set(loose.crew_reserve()) and loose.crew_reserve()
    members = {(m, loose.shift(p.key.start_date, p.key.shift_number).id) for p in loose.snapshot.pools for m in loose._members.get(p.id, ())}  # noqa: SLF001
    assert set(loose.crew_reserve()) <= members                                     # only pool members of that very shift


@pytest.mark.parametrize("native", [False, True])
def test_a_reserved_person_is_passed_over_by_the_same_rule_in_both_cores(demo, native) -> None:
    if native:
        pytest.importorskip("focus_native")
        from focus.engine import native as core

        if not core.available():
            pytest.skip("the native core is not available")
    services, snapshot, _ = demo
    ctx = EngineContext(snapshot, services.policy, Purpose.EXECUTABLE, native=native)
    for pid, phase in sorted(ctx.phases.items()):
        demand = phase.role_demands[0] if phase.role_demands else None
        if demand is None or demand.bundle.count != 1 or not demand.bundle.groups:
            continue
        for shift in ctx.line_shifts(phase.line):
            pool = ctx.pool_for(demand.team, shift)
            first = shift.next_open(shift.start)
            fit = None if first is None else shift.advance(first, phase.work_minutes, 0)
            if pool is None or fit is None:
                continue
            ctx._reserve = {}                                                       # noqa: SLF001 - start from an empty table
            ctx._native_context = None                                              # noqa: SLF001 - the Rust table is registered at build
            kwargs = dict(phase_id=pid, assignment_id=f"T-{pid}", line=phase.line, team=demand.team, shift_id=shift.id, pool_id=pool.id, count=1,
                          bundle=demand.bundle, segments=fit[1], kind=DutyKind.WORK, window=(shift.start, shift.end))
            plain, _ = choose_crew(ctx, ctx.new_ledger(), **kwargs)
            if plain is None or len([k for k in plain.preference]) < 2:
                continue
            chosen = plain.crew[0]
            ctx._reserve = {(chosen, shift.id): 1}                                  # noqa: SLF001 - the chosen person is needed elsewhere
            ctx._native_context = None                                              # noqa: SLF001
            kept, _ = choose_crew(ctx, ctx.new_ledger(), **kwargs)
            assert kept is not None and kept.crew[0] != chosen
            assert dict(kept.preference)[chosen][0] == 1 and dict(kept.preference)[kept.crew[0]][0] == 0
            assert [p for p, _ in kept.preference][-1] == chosen or dict(kept.preference)[chosen] > dict(kept.preference)[kept.crew[0]]
            return
    pytest.fail("no single-person phase with two candidates in the demo")
