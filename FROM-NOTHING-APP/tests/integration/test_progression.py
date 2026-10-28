"""P18: one canonical evaluation across surfaces, frozen recognition earned once, idempotent progression (A59, A62, A63).

The world is the published 2026-10-26 plan observed at the three S18 collection points. Expected
values are derived from the frozen goals and S14 formulas in this file; the projection never
grades itself.
"""
from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus.analytics import progression
from focus.analytics.points import gate_context
from focus.analytics.progression import BADGES, RECOGNITION, inflation_monitor, project, team_progress
from focus.codec import parse_utc
from focus.contracts.evaluation import BadgeAward, RecognitionEvent
from focus.data.fixture_builder import build_observations
from focus.domain import PhaseKind
from focus.performance import history
from focus.performance.actuals import EVENTS, FixtureObservationReader, ObservationStore, collect_observations
from focus.performance.lifecycle import GraphView, ObservationView, PhaseLifecycle
from focus.performance.score import aggregate, canonical, evaluate_shift
from tests.conftest import OBSERVATION_SHIFT as S1, observed_world

P1, P2, P3 = "2026-10-26T19:30:00Z", "2026-10-27T16:00:00Z", "2026-10-28T16:00:00Z"
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
UTC = timezone.utc


@pytest.fixture(scope="module")
def template(tmp_path_factory, app_root):
    return observed_world(tmp_path_factory.mktemp("progression") / "rt", app_root, points=(P1, P2, P3))


@pytest.fixture()
def world(template, tmp_path, app_root):
    from tests.conftest import Settings, FixedClock, FIXTURE_CUTOFF, Repositories, FixtureStore, build_services

    root = tmp_path / "rt"
    shutil.copytree(template.root, root)
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    services = build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                              clock=clock)
    return SimpleNamespace(services=services, t=template, snap=template.snap)


def events(services) -> list[RecognitionEvent]:
    repos = services.repositories
    return [repos.get_typed(RECOGNITION, i, "RecognitionEvent", RecognitionEvent) for i in repos.list_ids(RECOGNITION)]


def badges(services) -> list[BadgeAward]:
    repos = services.repositories
    return [repos.get_typed(BADGES, i, "BadgeAward", BadgeAward) for i in repos.list_ids(BADGES)]


# ---------------------------------------------------------------------- A59: one canonical evaluation on every surface

def test_a59_team_factory_history_surfaces_pool_the_same_units(world) -> None:
    services = world.services
    at = parse_utc(P3)
    goals = [canonical(services, team, S1) for team in ("PROD-A", "PROD-B", "QA")]
    assert [g.id for g in goals] == [world.t.evals[at][t].id for t in ("PROD-A", "PROD-B", "QA")]   # surfaces read the persisted revision
    factory = aggregate(services, goals, label="FACTORY 2026-10-26", as_of=at)
    B = sum(g.B for g in goals)
    E, Cp, U, O = (sum(getattr(g, k) for g in goals) for k in ("E", "Cp", "U", "O"))
    D = B - E
    expected = Fraction(100) * (Fraction(7, 10) * min(Fraction(1), Cp / D) + Fraction(3, 10) * Cp / (Cp + U)) - 100 * O / D
    assert (factory.B, factory.E, factory.Cp, factory.U, factory.O) == (B, E, Cp, U, O) and factory.final == expected
    average = sum(g.final for g in goals) / 3
    assert factory.final != average                                                          # never the average of percentages
    team = aggregate(services, [goals[1]], label="TEAM PROD-B 2026-10", as_of=at)
    assert team.final == goals[1].final and team.score == goals[1].score                     # a one-goal team pool is that goal
    again = aggregate(services, list(reversed(goals)), label="FACTORY 2026-10-26", as_of=at)
    assert again.id == factory.id and again.revision == factory.revision                     # same members: same canonical record
    assert all(g.policy_id == factory.policy_id == services.policy.id for g in goals)


# ---------------------------------------------------------------------- A62: frozen recognition earns once; corrections append

def test_a62_frozen_value_earns_once_and_never_ages(world) -> None:
    services = world.services
    first = project(services, as_of=parse_utc(P3))
    credited = [e for e in first.appended if e.event_type == "completion_credited"]
    view = ObservationView.build(services, parse_utc(P3))
    earned = {f.unit_id: f for r in view.records() for f in view.unit_facts(r) if f.status == "EARNED"}
    assert {e.earning_unit_ids[0] for e in credited} == set(earned)                           # every verified planned unit, once
    for e in credited:
        f = earned[e.earning_unit_ids[0]]
        assert e.value == Fraction(f.effort, 5)                                               # demo base = frozen standard effort / 5
        record = view.record_by_id(e.goal_id)
        if f.origin == "ORIGINAL":
            assert e.frozen_gate_context == record.goal.gate_contexts[f.job]                    # the context frozen at publication
            assert e.frozen_gate_context.valuation_date == date(2026, 10, 26)
    assert not any(e.event_type == "gate_recovery" for e in first.appended)                  # the gate (10-28) was ahead when frozen
    # a later display date would age the gate (raw 1 on 11-05) but cannot raise XP already earned
    later = gate_context(services.policy, date(2026, 10, 28), date(2026, 11, 5), 240)
    assert later.behind_raw == 1
    xp_before = team_progress(services, "PROD-A")["xp"]
    assert project(services, as_of=parse_utc("2026-11-05T12:00:00Z")).appended == []
    assert team_progress(services, "PROD-A")["xp"] == xp_before == 48


def test_a62_source_correction_appends_reversal_and_keeps_history(world, app_root) -> None:
    services = world.services
    project(services, as_of=parse_utc(P3))
    original = [e for e in events(services) if e.event_type == "completion_credited" and e.team == "PROD-A"]
    assert len(original) == 1 and original[0].value == 48
    feeds = build_observations()
    life = feeds["lifecycle_events"]
    late = dict(life["rows"][1], SOURCE_EVENT_KEY="EV-A1-DONE-VOID", SOURCE_SEQUENCE=50, KIND="VOIDED", PHASE_KIND="WORK", VERIFIED=False,
                VERIFIED_BY=None, SUPERSEDES_EVENT_KEY="EV-A1-DONE", EFFECTIVE_AT_UTC="2026-10-29T10:00:00Z", RECORDED_AT_UTC="2026-10-29T10:00:00Z",
                OBSERVED_AT_UTC="2026-10-29T10:05:00Z", EVIDENCE_REF="FIXTURE-VOID-A1", DETAIL="late correction: A1 completion recorded in error")
    reader = FixtureObservationReader(INPUTS, overrides={"lifecycle_events": dict(life, rows=life["rows"] + [late])})
    at = parse_utc("2026-10-29T12:00:00Z")
    collect_observations(services, as_of=at, reader=reader)
    before = canonical(services, "PROD-A", S1)
    revised = evaluate_shift(services, "PROD-A", S1, as_of=at)
    assert revised.revision == before.revision + 1 and revised.supersedes == before.id and revised.Cp == 0
    assert history.get(services, before.id) == before                                         # prior as-known evaluation preserved
    result = project(services, as_of=at)
    reversal = [e for e in result.corrections if e.base_key == original[0].base_key]
    assert len(reversal) == 1 and reversal[0].value == -48 and reversal[0].supersedes == original[0].id and reversal[0].revision == 2
    assert next(e for e in events(services) if e.id == original[0].id) == original[0]        # the original award is retained
    assert team_progress(services, "PROD-A")["xp"] == 0
    keeper = sorted((b for b in badges(services) if b.badge == "CommitmentKeeper" and b.team == "PROD-A"), key=lambda b: b.revision)
    assert [b.revoked for b in keeper] == [False, True] and keeper[1].supersedes == keeper[0].id   # revocation appended, award kept
    assert project(services, as_of=at).corrections == []                                      # a replay never reverses twice


# ---------------------------------------------------------------------- A63: idempotent streams, month transition, no qualification

def test_a63_replays_month_transition_and_named_lineage(world) -> None:
    services = world.services
    first = project(services, as_of=parse_utc(P3))
    assert first.appended and project(services, as_of=parse_utc(P3)).appended == [] and project(services, as_of=parse_utc(P3)).badges == []
    count = len(events(services))
    november = parse_utc("2026-11-01T12:00:00Z")
    champs = [b for b in project(services, as_of=november).badges if b.badge == "MonthlyChampion"]
    goals = {t: canonical(services, t, S1) for t in ("PROD-A", "PROD-B", "QA")}
    best = min(goals, key=lambda t: (-goals[t].final, t))                                      # best pooled score, ties by team id
    assert len(champs) == 1 and champs[0].team == best and champs[0].period == "2026-10"
    assert goals[best].id in champs[0].evidence_ids                                           # the award names its member evaluations
    assert project(services, as_of=november).badges == [] and len(events(services)) == count + 1   # once (plus its badge event)
    verified = {e.id for e in ObservationStore(services.repositories, services.settings.scope_key).all(EVENTS) if e.verified}
    for e in events(services):
        if e.event_type == "completion_credited":
            assert e.evidence[0] in {g.id for g in goals.values()} and e.evidence[1] in verified   # names a real evaluation and verified event
        assert e.dedupe_key == f"{e.base_key}#r{e.revision}"
    steward = [b for b in badges(services) if b.badge == "SkillSteward"]
    assert all("not a certification" in b.statement for b in steward)
    assert {r["team"] for r in inflation_monitor(services)} <= {"PROD-A", "PROD-B", "QA"} and not any(r["alert"] for r in inflation_monitor(services))


def test_a63_xp_never_qualifies_and_levels_are_exact(world, app_root) -> None:
    services = world.services
    project(services, as_of=parse_utc(P3))
    progress = team_progress(services, "PROD-B")
    assert progress["qualification_effect"] == "none"
    xp = progress["xp"]
    assert progress["level"] == 1 + int(xp) // 1000 and progress["next_threshold"] == progress["level"] * 1000
    for module in ("focus/eligibility.py", "focus/validation/input.py", "focus/publication/dispatch.py"):
        text = (app_root / module).read_text(encoding="utf-8")
        assert "progression" not in text and "xp." not in text                                 # qualification never reads XP
    for path in (app_root / "focus" / "engine").rglob("*.py"):
        assert "analytics.progression" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------- deterministic badge conditions (synthetic graphs/series)

def _phase(pid: str, kind=PhaseKind.WORK, team="PROD-A"):
    job = pid.rsplit(".", 2)[0]
    return SimpleNamespace(key=SimpleNamespace(id=pid), job=job, kind=kind, role_demands=(SimpleNamespace(team=team),), team=team,
                           status="OPEN", actual_end=None, standard_person_minutes=60)


def test_firebreak_counts_distinct_previously_blocked_jobs(policy) -> None:
    t = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)
    pred = "JP.WORK.0"
    downstream = [f"JD{i}.WORK.0" for i in range(4)]
    edge = lambda a, b: SimpleNamespace(pred_phase=a, succ_phase=b, type=None, lag_minutes=0, hard=True)  # noqa: E731
    hard_in = {d: [edge(pred, d)] for d in downstream[:3]}
    hard_in[downstream[3]] = [edge(pred, downstream[3]), edge("JX.WORK.0", downstream[3])]       # still blocked by another job
    graph = GraphView("SNAP", t, {p: _phase(p) for p in [pred, "JX.WORK.0", *downstream]}, hard_in, {}, frozenset(), {}, frozenset())
    done = PhaseLifecycle(pred, "JP", "COMPLETE", False, t, t, "EVT", {}, False, (), ())
    view = SimpleNamespace(graph=graph, lifecycles={pred: done})
    record = SimpleNamespace(goal_id="GOAL1", team="PROD-A")
    earned = [SimpleNamespace(phase=pred, unit_id="U1")]
    ent = progression._unlocks(view, record, earned, SimpleNamespace(id="EVL1"), policy)
    assert len(ent) == 1 and set(ent[0].evidence[2:]) == {"JD0", "JD1", "JD2"}                 # JD3 stays blocked by JX
    assert len(ent[0].evidence) - 2 >= policy.int("badges.firebreak_min_unlocked")


def test_steady_shift_needs_three_consecutive_scored_shifts(policy) -> None:
    from focus.performance.score import build_evaluation, components
    from tests.unit.test_score_facts import facts

    services = SimpleNamespace(policy=policy)
    records, evals = [], {}
    for i, earned in enumerate((100, 90, 90, 50, 100, 100)):
        start = datetime(2026, 10, 26 + i, 10, 0, tzinfo=UTC)
        goal = f"GOAL{i}"
        records.append(SimpleNamespace(goal_id=goal, team="PROD-A", window=SimpleNamespace(start=start, start_date=start.date())))
        parts = components(facts(100, earned), [], [], policy)
        evals[goal] = build_evaluation(scope="S", kind="GOAL", label=goal, goal_ids=[goal], cutoff=start, policy=policy, parts=parts,
                                       extra_reasons=[], evidence_hash=goal)
    view = SimpleNamespace(records=lambda: records)
    desired = progression._steady_shift(services, view, evals)
    scores = [str(evals[r.goal_id].score) for r in records]
    assert scores == ["100.0", "93.0", "93.0", "65.0", "100.0", "100.0"]
    assert [d.period for d in desired] == ["RUN:GOAL0:GOAL2"]                                  # 4-5-6 is only two after the break
