"""P11: exact full crews, activation on every path, scarcity/affinity, inspector competition (A33, A34, A38, A41)."""
from __future__ import annotations

import dataclasses
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from focus.data.acquisition import load_snapshot, snapshot_for_run
from focus.data.fixture_reader import FixtureSourceReader
from focus.domain import DutyKind, Interval, PersonKey, PhaseKind, Purpose
from focus.engine.crew_selection import choose_crew
from focus.engine.max.optimizer.greedy import Decoder
from focus.engine.model import EngineContext
from focus.validation.duty import reconstruct
from tests.conftest import acquire_demo, job_id

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _p(native: str) -> str:
    return PersonKey(native, "fixture-demo").id


def _ctx(snapshot, policy, purpose=Purpose.EXECUTABLE) -> EngineContext:
    return EngineContext(snapshot, policy, purpose)


def _work(ctx, line: int, soi: str):
    jid = job_id(ctx.snapshot, line, soi)
    return next(p for p in ctx.job_phases[jid] if p.kind is PhaseKind.WORK)


def _s1(ctx, day=date(2026, 10, 27)):
    return ctx.shift(day, 1)


def _choose(ctx, ledger, phase, shift, start, aid="T", **kw):
    demand = phase.role_demands[0]
    pool = ctx.pool_for(demand.team, shift)
    fit = shift.advance(start, phase.work_minutes, 0)
    return choose_crew(ctx, ledger, phase_id=phase.key.id, assignment_id=aid, line=phase.line, team=demand.team, shift_id=shift.id,
                       pool_id=pool.id, count=kw.pop("count", demand.bundle.count), bundle=demand.bundle, segments=fit[1], **kw)


def test_a34_exact_full_crew_or_wait(demo_snapshot, policy) -> None:
    ctx = _ctx(demo_snapshot, policy)
    ledger = ctx.new_ledger()
    root5 = _work(ctx, 1305, "ROOT5")                                                # crew 3, A AND (B OR C)
    shift = _s1(ctx)
    start = shift.regular_windows[0][0]
    choice, reason = _choose(ctx, ledger, root5, shift, start)
    assert choice is not None and len(set(choice.crew)) == 3 and reason == "SELECTED"
    assert set(choice.crew) <= {_p(f"DEMO-A1-0{i}") for i in (5, 6, 7, 8)}             # individually qualified, PROD-A S1 only
    for person in (_p("DEMO-A1-05"), _p("DEMO-A1-07")):                               # occupy two of the four qualified people
        ledger.book(f"BUSY-{person}", [person], 1301, shift.id, ctx.windows[(person, shift.id)].pool_id, [(start, start + 120, False)])
    none, why = _choose(ctx, ledger, root5, shift, start)
    assert none is None and why.startswith("CREW_UNAVAILABLE: 2 of 3")               # wait; never clamp to two
    dup, why = _choose(ctx, ledger, root5, shift, start + 200, exact_crew=[_p("DEMO-A1-06")] * 3)
    assert dup is None and why.startswith("CREW_DUPLICATE_PERSON")
    short, why = _choose(ctx, ledger, root5, shift, start + 200, exact_crew=[_p("DEMO-A1-06"), _p("DEMO-A1-08")])
    assert short is None and why.startswith("CREW_SIZE_MISMATCH")
    loan, why = _choose(ctx, ledger, root5, shift, start + 200, exact_crew=[_p("DEMO-A1-06"), _p("DEMO-A1-08"), _p("DEMO-B1-04")])
    assert loan is None and why.startswith("NOT_POOL_MEMBER")                         # no cross-team loan
    zero, why = _choose(ctx, ledger, root5, shift, start + 200, count=0)
    assert zero is None and why.startswith("CREW_DEMAND_UNAVAILABLE")


def test_a38_every_path_rejects_a_cap_breach_and_v8_catches_forgery(demo_snapshot, policy) -> None:
    ctx = _ctx(demo_snapshot, policy, Purpose.FORECAST)
    ledger = ctx.new_ledger()
    shift = _s1(ctx)
    pool = ctx.pool_for("MHE", shift)
    members = list(ctx.pool_members(pool.id))
    assert len(members) == 10 and ctx.pool_caps[pool.id] == 8
    t = shift.regular_windows[0][0]
    for i, person in enumerate(members[:8]):
        ledger.book(f"M{i}", [person], 1306, shift.id, pool.id, [(t + 20 * i, t + 20 * i + 15, False)])
    mhe6 = _work(ctx, 1306, "MHE6")                                                   # crew 2 of virtual MHE people
    later = shift.regular_windows[1][0]
    choice, why = _choose(ctx, ledger, mhe6, shift, later)
    assert choice is not None and set(choice.crew) <= set(members[:8])                 # reuse active heads only
    fresh = [m for m in members if m not in members[:8]]
    forced, why = _choose(ctx, ledger, mhe6, shift, later, exact_crew=fresh)
    assert forced is None and why.startswith("ACTIVATION_CAP")                        # a ninth/tenth head is refused
    exec_ctx = _ctx(demo_snapshot, policy)
    plan = Decoder(exec_ctx, lambda pid: (-exec_ctx.tails.get(pid, 0), pid)).run()
    from focus.engine.max.optimizer.orchestrator import MaxOptimizer, build_candidate

    run = MaxOptimizer(exec_ctx, max_moves=0).run()
    candidate = build_candidate(exec_ctx, run, created_at=demo_snapshot.cutoff)
    assert not reconstruct(demo_snapshot, policy, candidate).violations
    a1 = next(a for a in candidate.assignments if a.shift_id == "DEMO:2026-10-26:S1" and a.kind is PhaseKind.WORK)
    active = {r.person for a in candidate.assignments if a.pool_key == a1.pool_key for r in a.crew}
    extra = [m for m in exec_ctx.pool_members(a1.pool_key) if m not in active]
    forged_crew = tuple(dataclasses.replace(a1.crew[0], person=p) for p in extra[:2])
    late = Interval(a1.end.replace(hour=a1.end.hour + 3), a1.end.replace(hour=a1.end.hour + 4))
    forged = dataclasses.replace(a1, id="FORGED", crew=forged_crew, segments=(late,), start=late.start, end=late.end)
    bad = dataclasses.replace(candidate, assignments=candidate.assignments + (forged,))
    codes = {(v.check, v.code) for v in reconstruct(demo_snapshot, policy, bad).violations}
    assert ("V8", "ACTIVATION_CAP") in codes                                           # independent check, not self-attested
    assert plan.placements                                                            # the seed itself was feasible


def test_a41_scarce_specialists_are_preserved_and_affinity_wins_ties(demo_snapshot, policy) -> None:
    ctx = _ctx(demo_snapshot, policy)
    ledger = ctx.new_ledger()
    shift = _s1(ctx)
    a1 = _work(ctx, 1301, "A1")                                                       # needs A only, crew 2
    start = shift.regular_windows[0][0]
    choice, _ = _choose(ctx, ledger, a1, shift, start)
    assert set(choice.crew) <= {_p(f"DEMO-A1-0{i}") for i in (1, 2, 3, 4)}            # A-only people, B holders kept for A2/B2
    keys = dict(choice.preference)
    assert keys[_p("DEMO-A1-07")][0] == 3 and keys[_p("DEMO-A1-01")][0] == 1          # usable applicable certs drive the order
    ledger2 = ctx.new_ledger()
    pool = ctx.windows[(_p("DEMO-A1-03"), shift.id)].pool_id
    ledger2.book("AT1301", [_p("DEMO-A1-03")], 1301, shift.id, pool, [(start, start + 60, False)])
    ledger2.book("AT1302", [_p("DEMO-A1-01")], 1302, shift.id, pool, [(start, start + 60, False)])
    choice2, _ = _choose(ctx, ledger2, a1, shift, start + 90, aid="T2")
    assert choice2.preference[0][0] == _p("DEMO-A1-03")                               # same aircraft first: no transfer
    assert _p("DEMO-A1-03") in choice2.crew


def test_a33_one_inspector_never_overlaps_and_wait_duty_is_booked(services_factory) -> None:
    data = {"mechanic_certs": json.loads((INPUTS / "mechanic_certs.json").read_text())}
    data["mechanic_certs"]["rows"] = [r for r in data["mechanic_certs"]["rows"]
                                      if not (r["BEMS_ID"] in ("DEMO-Q1-02", "DEMO-Q2-02", "DEMO-Q3-02") and r["CERT_CODE"] == "QA")]
    services = services_factory()
    outcome = acquire_demo(services, key="a33-one-inspector", reader=FixtureSourceReader(INPUTS, overrides=data))
    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    ctx = _ctx(snap, services.policy)
    result = Decoder(ctx, lambda pid: (-ctx.tails.get(pid, 0), pid)).run()
    qa = [p for p in result.placements.values() if p.kind in (PhaseKind.IP_QA, PhaseKind.FINAL_QA, PhaseKind.OTHER_BUYOFF)]
    assert qa and {c for p in qa for c in p.crew} <= {_p("DEMO-Q1-01"), _p("DEMO-Q2-01"), _p("DEMO-Q3-01")}
    by_person = defaultdict(list)
    for item in result.ledger.all_items():
        by_person[item.person].append((item.start, item.end))
    for spans in by_person.values():
        spans.sort()
        assert all(b[0] >= a[1] for a, b in zip(spans, spans[1:]))                   # one inspector, serialized, never overlapping
    wait_policy = services.policy.with_overrides({"qa.crew_during_ip": "WAIT"}, new_id="qa-wait", reason="A33 retained crew")
    wsnap = snapshot_for_run(services, outcome.run_id, policy=wait_policy)
    wctx = _ctx(wsnap, wait_policy)
    wres = Decoder(wctx, lambda pid: (-wctx.tails.get(pid, 0), pid)).run()
    waits = [i for i in wres.ledger.all_items() if i.kind is DutyKind.WAIT]
    root3 = job_id(wsnap, 1303, "ROOT3")
    ip = sorted((p for p in wres.placements.values() if p.job == root3 and p.kind is PhaseKind.IP_QA), key=lambda p: p.start)
    assert waits and ip and "wait_duty" in wres.paths_used
    crew_before = next(p for p in wres.placements.values() if p.job == root3 and p.kind is PhaseKind.WORK and p.end <= ip[0].start)
    assert {i.person for i in waits if i.end == ip[0].end} >= set(crew_before.crew)    # retained crew waits until the IP ends
    person = crew_before.crew[0]
    regular, ot = wres.ledger.duty_minutes(person, crew_before.shift_id)
    assert regular + ot >= sum(p.end - p.start for p in wres.placements.values() if person in p.crew and p.shift_id == crew_before.shift_id)
