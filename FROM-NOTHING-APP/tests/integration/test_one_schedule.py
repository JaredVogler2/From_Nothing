"""One schedule through delivery (D-33): the delivery plan carries the accepted shift plan through delivery.

Each run makes the shift plan first (EXECUTABLE: only work that can really start, the plan crews are dispatched from).
The delivery plan (FORECAST: all work through delivery, where dates and late cost are worked out) then keeps every slot
of the shift plan in force and plans the rest of the flow around them. Without that, each purpose defends its own
previous plan and the two arrangements of the same shift drift apart.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import PhaseKind, Purpose
from focus.engine.commitments import Defense, defended_tasks, lookup_incumbent, plan_hints
from focus.engine.max.optimizer.greedy import DefendedTask, hint_leads
from focus.engine.max.optimizer.orchestrator import planning_mode, schedule
from focus.engine.model import EngineContext
from focus.publication.service import publish
from focus.runtime import build_services
from focus.validation.duty import reconstruct
from tests.conftest import acquire_demo

FOLLOW = "planning.forecast_follows_executable"


def _slots(plan) -> dict[str, tuple]:
    """phase -> (start, end, crew) of every assignment a real crew works."""
    return {a.phase: (a.start, a.end, frozenset(r.person for r in a.crew)) for a in plan.assignments if a.crew}


@pytest.fixture()
def services_with(tmp_path, app_root, clean_env):
    def make(policy):
        settings = Settings.from_env({}, app_root=app_root)
        clock = FixedClock(FIXTURE_CUTOFF)
        repos = Repositories(FixtureStore(tmp_path / "runtime"), mode=settings.mode, scope_key=settings.scope_key, clock=clock)
        return build_services(settings, repositories=repos, clock=clock, policy=policy)
    return make


def _accepted_shift_plan(services, run_id):
    shift_plan, run = schedule(services, source_run=run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    assert run.validation.valid
    assert publish(services, shift_plan.id).status == "ACCEPTED"
    return shift_plan


def test_the_delivery_plan_keeps_every_slot_of_the_accepted_shift_plan(demo) -> None:
    services, outcome, snapshot = demo
    assert services.policy.bool(FOLLOW)                                            # the shipped setting (owner, D-33)
    shift_plan = _accepted_shift_plan(services, outcome.run_id)
    in_force = lookup_incumbent(services, snapshot.scope_key, "EXECUTABLE").plan
    delivery, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    assert run.validation.valid and not reconstruct(snapshot, services.policy, delivery).violations
    ours, theirs = _slots(shift_plan), _slots(delivery)
    assert ours and set(ours) < set(theirs)                                        # it carries on past what can start today
    assert {phase: theirs[phase] for phase in ours} == ours                        # the same start, end and crew, slot for slot
    assert delivery.incumbent_id == in_force.id                                    # the commitments it defends are the shift plan's
    follows = run.planning["follows"]
    assert (follows["purpose"], follows["plan"]) == ("EXECUTABLE", in_force.id)
    assert follows["defended"] + follows["slots_to_keep"] == len(in_force.assignments)
    assert run.planning["rehomed"] == 0 and run.planning["kept"] == follows["slots_to_keep"]
    assert {c.outcome for c in delivery.commitment_results} <= {"KEPT", "OUT_OF_HORIZON"}
    assert delivery.algorithm_status.incumbent["follows"] == "EXECUTABLE"
    assert delivery.algorithm_status.baseline_comparison["planning"]["follows"] == follows
    again, _ = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    assert again.id == delivery.id                                                 # repeatable


def test_a_repair_of_the_delivery_plan_keeps_the_shift_plan_first_and_its_own_plan_for_the_rest(demo) -> None:
    services, outcome, snapshot = demo
    shift_plan = _accepted_shift_plan(services, outcome.run_id)
    first, _ = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    assert publish(services, first.id).status == "ACCEPTED"                        # the delivery plan's own accepted plan
    delivery, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, mode="repair")
    assert run.planning["ran"] == "repair" and run.planning["follows"]["purpose"] == "EXECUTABLE"
    assert run.planning["hints"] > run.planning["follows"]["slots_to_keep"]        # the rest of its own plan is kept as well
    assert run.planning["rehomed"] == 0                                            # nothing changed, so every slot still holds
    ours, theirs = _slots(shift_plan), _slots(delivery)
    assert {phase: theirs[phase] for phase in ours} == ours
    assert _slots(first) == theirs and run.validation.valid


def test_without_a_shift_plan_in_force_the_delivery_plan_defends_its_own(demo) -> None:
    services, outcome, snapshot = demo
    ctx = EngineContext(snapshot, services.policy, Purpose.FORECAST)
    defense = defended_tasks(services, ctx)
    assert (defense.follows, defense.status, defense.plan) == ("", "NO_INCUMBENT", None)
    delivery, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    assert run.planning == {} and delivery.incumbent_id is None and run.validation.valid


def test_the_setting_off_gives_each_purpose_its_own_previous_plan(services_with, policy) -> None:
    services = services_with(policy.with_overrides({FOLLOW: False}, new_id=policy.id, reason="test: the earlier behavior"))
    outcome = acquire_demo(services)
    shift_plan = _accepted_shift_plan(services, outcome.run_id)
    delivery, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5)
    assert run.planning == {} and delivery.incumbent_id is None                    # it never looks at the shift plan
    assert "follows" not in delivery.algorithm_status.incumbent and run.validation.valid
    assert {c.outcome for c in delivery.commitment_results} == set()


def test_only_a_delivery_plan_follows(demo) -> None:
    """The shift plan defends its own previous plan, whatever the delivery plan did."""
    services, outcome, snapshot = demo
    _accepted_shift_plan(services, outcome.run_id)
    in_force = lookup_incumbent(services, snapshot.scope_key, "EXECUTABLE").plan
    defense = defended_tasks(services, EngineContext(snapshot, services.policy, Purpose.EXECUTABLE))
    assert defense.follows == "" and defense.plan.id == in_force.id and defense.kept == {} and defense.repairable is defense.plan
    following = defended_tasks(services, EngineContext(snapshot, services.policy, Purpose.FORECAST))
    assert following.follows == "EXECUTABLE" and following.plan.id == in_force.id and following.previous is None
    assert following.repairable is None and following.defended.keys() == defense.defended.keys()
    assert len(following.defended) + len(following.kept) == len(in_force.assignments)      # every slot: defended, or kept later
    ctx = EngineContext(snapshot, services.policy, Purpose.FORECAST)
    assert plan_hints(ctx, following, repair=False) == following.kept == plan_hints(ctx, following, repair=True)
    assert plan_hints(ctx, Defense(), repair=True) == {} == plan_hints(ctx, defense, repair=False)
    mode, reason = planning_mode(ctx, following, "repair")
    assert mode == "repair"                                                        # asked for: plan_by_mode then finds nothing to repair
    _, run = schedule(services, source_run=outcome.run_id, purpose=Purpose.FORECAST, budget_seconds=5, max_moves=5, mode="repair")
    assert run.planning["ran"] == "full" and run.planning["reason"].startswith("REPAIR_NO_INCUMBENT")
    assert run.planning["follows"]["plan"] == in_force.id and run.validation.valid


def _phase(kind: PhaseKind, crew: bool):
    return SimpleNamespace(kind=kind, role_demands=(object(),) if crew else ())


def _edge(pred: str):
    return SimpleNamespace(pred_phase=pred)


def _hint(pid: str, start: int) -> DefendedTask:
    return DefendedTask(pid, f"A-{pid}", "S", start, ("p",), 3)


def test_a_release_that_a_kept_slot_waits_for_is_dispatched_with_the_kept_slots() -> None:
    """A job's release books no crew; the next job's first phase waits for it. Left in the ordinary class it would wait
    behind other work, which could take the kept slot's crew before the slot became ready."""
    phases = {"a-work": _phase(PhaseKind.WORK, True), "a-release": _phase(PhaseKind.RELEASE, False),
              "b-work": _phase(PhaseKind.WORK, True), "c-work": _phase(PhaseKind.WORK, True),
              "new-work": _phase(PhaseKind.WORK, True), "new-release": _phase(PhaseKind.RELEASE, False),
              "d-work": _phase(PhaseKind.WORK, True), "gate": _phase(PhaseKind.RELEASE, False)}
    preds = {"a-release": [_edge("a-work")], "b-work": [_edge("a-release")], "c-work": [_edge("a-release")],
             "new-release": [_edge("new-work")], "d-work": [_edge("new-release"), _edge("gate")], "gate": [_edge("a-release")]}
    ctx = SimpleNamespace(phases=phases, preds=preds)
    hints = {"a-work": _hint("a-work", 100), "b-work": _hint("b-work", 400), "c-work": _hint("c-work", 250),
             "d-work": _hint("d-work", 900)}
    leads = hint_leads(ctx, hints, {})
    assert leads == {"a-release": 250,          # the earliest of the slots it precedes (c-work), reached through the gate too
                     "new-release": 900, "gate": 900}
    assert "new-work" not in leads              # work that books a crew keeps its ordinary priority
    assert hint_leads(ctx, {"b-work": hints["b-work"]}, {"a-work": hints["a-work"]}) == {"a-release": 400}
    assert hint_leads(ctx, {}, {}) == {}


def _leads_by_fixed_point(ctx, hints, defended) -> dict[str, int]:
    """The definition, taken in any order: lower a crewless phase's lead until nothing changes."""
    leads: dict[str, int] = {}
    stack = [(pid, hint.start) for pid, hint in hints.items()]
    while stack:
        pid, start = stack.pop()
        for e in ctx.preds.get(pid, ()):
            pred = e.pred_phase
            phase = ctx.phases.get(pred)
            if phase is None or pred in hints or pred in defended:
                continue
            if phase.kind is not PhaseKind.RELEASE and phase.role_demands:
                continue
            if leads.get(pred, start + 1) > start:
                leads[pred] = start
                stack.append((pred, start))
    return leads


def test_release_leads_are_the_earliest_slot_each_release_precedes_on_any_network() -> None:
    """The leads are computed in one pass, earliest kept slot first. On random networks, with chains of releases, gates
    many slots wait for, missing phases and defended work, that pass gives exactly what the definition gives."""
    import random

    for seed in range(40):
        rng = random.Random(seed)
        names = [f"n{i}" for i in range(rng.randint(5, 70))]
        phases = {n: _phase(PhaseKind.RELEASE, False) if rng.random() < 0.45 else
                  _phase(PhaseKind.WORK, rng.random() < 0.8) for n in names}
        preds = {n: [_edge(rng.choice(names[:i] + ["gone"])) for _ in range(rng.randint(0, 4))] for i, n in enumerate(names) if i}
        ctx = SimpleNamespace(phases=phases, preds=preds)
        crewed = [n for n in names if phases[n].role_demands]
        rng.shuffle(crewed)
        cut = rng.randint(0, len(crewed))
        hints = {n: _hint(n, rng.randint(0, 30) * 10) for n in crewed[:cut]}
        defended = {n: _hint(n, rng.randint(0, 30) * 10) for n in crewed[cut:cut + rng.randint(0, 5)]}
        assert hint_leads(ctx, hints, defended) == _leads_by_fixed_point(ctx, hints, defended), seed


def test_release_leads_visit_a_gate_once_however_many_slots_wait_for_it() -> None:
    """A gate that thousands of kept slots wait for, behind a long chain of releases: one walk of the chain, not one per
    slot. (The first version walked it again for every slot that lowered the lead.)"""
    width, depth = 3000, 300
    phases = {f"w{i}": _phase(PhaseKind.WORK, True) for i in range(width)}
    phases.update({f"r{i}": _phase(PhaseKind.RELEASE, False) for i in range(depth)})
    walked = {"edges": 0}

    class Counted(dict):
        def get(self, key, default=None):
            found = super().get(key, default)
            walked["edges"] += len(found)
            return found

    preds = Counted({f"w{i}": [_edge("r0")] for i in range(width)})
    preds.update({f"r{i}": [_edge(f"r{i + 1}")] for i in range(depth - 1)})
    hints = {f"w{i}": _hint(f"w{i}", 10 * (width - i)) for i in range(width)}          # handed over latest first
    leads = hint_leads(SimpleNamespace(phases=phases, preds=preds), hints, {})
    assert leads == {f"r{i}": 10 for i in range(depth)}
    assert walked["edges"] <= width + depth
