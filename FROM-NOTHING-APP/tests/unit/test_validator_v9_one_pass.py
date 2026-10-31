"""V9 in one pass equals the per-assignment ancestor walk it replaced (production-scale validator work).

The one-pass ``_v9`` reaches the nodes below every part release and every blocking shortage once, instead of building
and sorting each assignment's whole ancestor set. It must decide exactly what the per-assignment reference decided:
the same violations (check, code, entity, detail) and the same coverage count, on clean plans, on every corruption of
the A47 corpus, on a forecast plan judged as executable, and on forged work behind blocking shortages.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from focus.domain import Purpose, ReleaseKind, ReleaseStatus, minute_ceil, to_minute
from focus.validation import plan as vplan
from focus.validation.input import ValidationInput
from tests.integration.test_validator_mutations import CORPUS, base  # noqa: F401  (``base`` is the shared fixture)


def v9_reference(run) -> None:  # type: ignore[no-untyped-def]
    """The per-assignment V9 kept as the oracle: each assignment's hard-edge ancestors up to verified completion."""
    inp = run.inp
    memo: dict[str, frozenset[str]] = {}
    blocking = [s for s in inp.snapshot.shortages if s.status in ("UNKNOWN_ECD", "REOPENED")]
    for a in run.plan.assignments:
        phase = inp.phases.get(a.phase)
        if phase is None:
            continue
        run.count("V9")
        start = to_minute(a.start)
        ancestors = inp.ancestors(a.phase, memo)
        for node in sorted(ancestors):
            rel = inp.releases.get(node)
            if rel is None or rel.kind is not ReleaseKind.PART:
                continue
            if rel.status is ReleaseStatus.CONFIRMED and rel.ready_at is not None:
                if start < minute_ceil(rel.ready_at):
                    run.bad("V9", "BEFORE_PART_RELEASE", a.id, f"{rel.label} received {rel.ready_at.isoformat()}")
            elif inp.purpose is Purpose.EXECUTABLE:
                run.bad("V9", "PART_NOT_RECEIVED", a.id, f"{rel.label} has no confirmed receipt: not executable")
            elif rel.status is ReleaseStatus.CONDITIONAL and rel.forecast_at is not None:
                if start < minute_ceil(rel.forecast_at):
                    run.bad("V9", "BEFORE_PART_FLOOR", a.id, f"{rel.label} forecast floor {rel.forecast_at.isoformat()} ({rel.precision})")
                if not a.conditional_reasons:
                    run.bad("V9", "PART_CONDITIONAL_UNLABELLED", a.id, f"{rel.label} is a forecast ETA: the assignment must be conditional")
            else:
                run.bad("V9", "PART_RELEASE_BLOCKED", a.id, f"{rel.label} is {rel.status.value}")
        jobs = {phase.job} | {inp.phases[n].job for n in ancestors if n in inp.phases}
        for s in blocking:
            if jobs & set(s.affected_jobs):
                run.bad("V9", "PART_SHORTAGE_BLOCKED", a.id, f"shortage {s.record_id} is {s.status}")


def both(snapshot, policy, plan, purpose: Purpose):  # type: ignore[no-untyped-def]
    inp = ValidationInput.build(snapshot, policy, purpose)
    new, old = vplan._Run(inp, plan), vplan._Run(inp, plan)
    vplan._v9(new)
    v9_reference(old)
    key = lambda v: (v.check, v.code, v.entity, v.detail)  # noqa: E731
    return sorted(new.violations, key=key), sorted(old.violations, key=key), new.counts["V9"], old.counts["V9"]


def test_one_pass_v9_equals_the_reference_on_clean_and_corrupted_plans(base) -> None:  # noqa: F811
    cases = [(base.plans[p], p) for p in (Purpose.EXECUTABLE, Purpose.FORECAST)]
    cases.append((base.plans[Purpose.FORECAST], Purpose.EXECUTABLE))              # forecast work judged as executable
    cases += [(mutate(base, base.plans[Purpose.EXECUTABLE]), Purpose.EXECUTABLE) for _, mutate, _ in CORPUS]
    flagged = 0
    for plan, purpose in cases:
        new, old, n_new, n_old = both(base.snapshot, base.policy, plan, purpose)
        assert new == old and n_new == n_old, (plan.id, purpose)
        flagged += bool(new)
    assert flagged >= 2                                                         # the comparison covers real V9 findings


def test_one_pass_v9_equals_the_reference_behind_blocking_shortages(app_root, tmp_path) -> None:
    """A production-shaped snapshot whose every shortage is undated (UNKNOWN_ECD blocks the root): forged work on
    affected jobs and below them must draw the same PART_SHORTAGE_BLOCKED findings from both implementations."""
    from focus.clock import FIXTURE_CUTOFF, FixedClock
    from focus.config import Settings
    from focus.data.acquisition import acquire, load_snapshot
    from focus.data.fixture_reader import FixtureSourceReader
    from focus.data.production_fixture import build_production_fixture
    from focus.data.repositories import Repositories
    from focus.data.store import FixtureStore
    from focus.engine.max.optimizer.orchestrator import schedule_snapshot
    from focus.runtime import build_services

    clock = FixedClock(FIXTURE_CUTOFF)
    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False)
    services = build_services(settings, repositories=Repositories(FixtureStore(tmp_path), mode="fixture", scope_key=settings.scope_key, clock=clock),
                              clock=clock)
    data = build_production_fixture(1, baseline=300, edges=1300, rework_total=100, no_ecd_share=1.0)
    reader = FixtureSourceReader(app_root / "fixtures" / "inputs" / "manufacturing_demo", overrides={k: v.to_json() for k, v in data.items()})
    snapshot = load_snapshot(services.repositories, acquire(services, reader, cutoff=FIXTURE_CUTOFF, idempotency_key="v9").snapshot_id)
    plan, _ = schedule_snapshot(services, snapshot, purpose=Purpose.FORECAST, budget_seconds=30, max_moves=0)
    inp = ValidationInput.build(snapshot, services.policy, Purpose.EXECUTABLE)
    blocking = [s for s in snapshot.shortages if s.status in ("UNKNOWN_ECD", "REOPENED")]
    assert blocking and plan.assignments
    template = plan.assignments[0]
    forged = []
    for s in blocking:
        seeds = [pid for pid, ph in inp.phases.items() if ph.job in set(s.affected_jobs)]
        below = sorted(vplan._reached(inp, seeds) & set(inp.phases))
        for pid in (seeds[:1] + below[:3]):
            forged.append(replace(template, id=f"FORGED-{s.record_id}-{pid}", phase=pid))
    for candidate, purpose in ((replace(plan, assignments=tuple(forged)), Purpose.EXECUTABLE), (plan, Purpose.EXECUTABLE), (plan, Purpose.FORECAST)):
        new, old, n_new, n_old = both(snapshot, services.policy, candidate, purpose)
        assert new == old and n_new == n_old, purpose
    new, *_ = both(snapshot, services.policy, replace(plan, assignments=tuple(forged)), Purpose.EXECUTABLE)
    assert any(v.code == "PART_SHORTAGE_BLOCKED" for v in new)


def test_one_pass_v9_equals_the_reference_on_a_cyclic_graph(base) -> None:  # noqa: F811
    """A hard-edge cycle through placed work (a corrupt graph the topological pass cannot order): the phases on and
    below the cycle take the exact walk, and both implementations still agree, on forecast and executable judgement."""
    plan = base.plans[Purpose.FORECAST]
    checked = 0
    for purpose in (Purpose.FORECAST, Purpose.EXECUTABLE):
        inp = ValidationInput.build(base.snapshot, base.policy, purpose)
        memo: dict[str, frozenset[str]] = {}
        parts = {rid for rid, rel in inp.releases.items() if rel.kind is ReleaseKind.PART}
        placed = sorted({a.phase for a in plan.assignments if a.phase in inp.phases})
        phase, edge = next((pid, e) for pid in placed if inp.ancestors(pid, memo) & parts
                           for e in inp.hard_out.get(pid, ()) if e.succ_phase in inp.phases)
        back = replace(edge, pred_phase=edge.succ_phase, succ_phase=phase)       # successor -> phase closes the loop
        inp.hard_out.setdefault(back.pred_phase, []).append(back)
        inp.hard_in.setdefault(phase, []).append(back)
        new, old = vplan._Run(inp, plan), vplan._Run(inp, plan)
        vplan._v9(new)
        v9_reference(old)
        key = lambda v: (v.check, v.code, v.entity, v.detail)  # noqa: E731
        assert sorted(new.violations, key=key) == sorted(old.violations, key=key), purpose
        assert new.counts["V9"] == old.counts["V9"]
        checked += bool(old.violations)
    assert checked                                                              # executable judgement draws real findings


def test_v9_stops_at_verified_completion(base) -> None:  # noqa: F811
    """A verified-complete phase satisfies its successors (V2, the decoder): a part or a blocking shortage above it gates
    nothing below it. A 50-aircraft run hit this: an open rework child whose part had not arrived fed the completed
    successor of its completed root (REWORK_REOPENED_ROOT), the decoder rightly placed the open work after that successor,
    and V9 walked through the completion and failed the plan."""
    from focus.domain import EdgeType

    checked = 0
    for purpose in (Purpose.EXECUTABLE, Purpose.FORECAST):
        inp = ValidationInput.build(base.snapshot, base.policy, purpose)
        plan = base.plans[purpose]
        placed = {a.phase: a for a in plan.assignments}
        done, below = next((pid, e.succ_phase) for pid in sorted(inp.phases) if inp.satisfied_node(pid)
                           for e in inp.hard_out.get(pid, ()) if e.succ_phase in placed)
        part = next(rid for rid, rel in sorted(inp.releases.items()) if rel.kind is ReleaseKind.PART and rel.status is not ReleaseStatus.CONFIRMED)
        edge = replace(inp.hard_out[done][0], id="E-TEST-PART-ABOVE-DONE", pred_phase=part, succ_phase=done, type=EdgeType.FS, lag_minutes=0)
        inp.hard_out.setdefault(part, []).append(edge)
        inp.hard_in.setdefault(done, []).append(edge)
        shortage = replace(base.snapshot.shortages[0], record_id=999001, affected_jobs=(inp.phases[done].job,), status="UNKNOWN_ECD")
        inp.snapshot = replace(inp.snapshot, shortages=inp.snapshot.shortages + (shortage,))
        raw, stack = set(), [below]                                             # a walk through completion reaches the part
        while stack:
            for e in inp.hard_in.get(stack.pop(), ()):
                if e.pred_phase not in raw:
                    raw.add(e.pred_phase)
                    stack.append(e.pred_phase)
        assert part in raw and part not in inp.ancestors(below, {})
        new, old = vplan._Run(inp, plan), vplan._Run(inp, plan)
        vplan._v9(new)
        v9_reference(old)
        key = lambda v: (v.check, v.code, v.entity, v.detail)  # noqa: E731
        assert sorted(new.violations, key=key) == sorted(old.violations, key=key), purpose
        assert not [v for v in new.violations if v.entity == placed[below].id], purpose
        checked += 1
    assert checked == 2
