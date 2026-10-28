"""P17: lifecycle actuals, sequence violations, disruption claims and reviewed excusals (A54, A55, A56, A26).

One deterministic world:

1. publish the 2026-10-26 obligation plan;
2. collect the S18 observation feeds at the three manifest points;
3. evaluate every closed goal.

Expected values are re-derived here from the source facts and the frozen goal's own units, never
read back from the evaluator. Mutating cases run on private copies of the world.
"""
from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.codec import parse_utc
from focus.config import Settings
from focus.contracts.events import DISRUPTION_CAUSES, NOMINALLY_EXCUSABLE, LifecycleEvent
from focus.data.acquisition import acquire, make_reader, snapshot_for_run
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import DomainError, EvaluationStatus, PhaseKind, Purpose, digest_id
from focus.engine.commitments import import_fixture_incumbent
from focus.engine.max.optimizer.orchestrator import schedule
from focus.policy import PolicyError
from focus.performance import disruption, history
from focus.performance.actuals import (CLAIMS, DECISIONS, EVENTS, FEED_BY_NAME, VIOLATIONS, FixtureObservationReader, LiveObservationReader,
                                       ObservationStore, collect_observations, manifest_points)
from focus.performance.lifecycle import GoalRegistry, ObservationView, derive_disappearances, phase_lifecycle, resolve_revisions
from focus.performance.score import evaluate_shift
from focus.publication.dispatch import dispatch_check
from focus.publication.service import publish
from focus.runtime import build_services
from tests.conftest import job_id

UTC = timezone.utc
S1 = "DEMO:2026-10-26:S1"
P1, P2, P3 = (parse_utc(x) for x in ("2026-10-26T19:30:00Z", "2026-10-27T16:00:00Z", "2026-10-28T16:00:00Z"))
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _services(root: Path, app_root: Path, policy=None):
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock)
    return build_services(settings, repositories=repos, clock=clock, policy=policy)


@pytest.fixture(scope="module")
def published(tmp_path_factory, app_root):
    root = tmp_path_factory.mktemp("lifecycle") / "rt"
    services = _services(root, app_root)
    run = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="life-1")
    import_fixture_incumbent(services)
    candidate, _ = schedule(services, source_run=run.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    result = publish(services, candidate.id)
    assert result.status == "ACCEPTED", result.reasons
    return SimpleNamespace(root=root, run=run, plan_id=result.plan_id, snapshot=snapshot_for_run(services, run.run_id))


@pytest.fixture(scope="module")
def world(published, tmp_path_factory, app_root):
    """The published world after all three collections, with the evaluations made at each point."""
    root = tmp_path_factory.mktemp("lifecycle-world") / "rt"
    shutil.copytree(published.root, root)
    services = _services(root, app_root)
    runs, evals = {}, {}
    for at in manifest_points(services):
        runs[at] = collect_observations(services, as_of=at)
        evals[at] = {team: evaluate_shift(services, team, S1, as_of=at) for team in ("PROD-A", "PROD-B", "QA")}
    return SimpleNamespace(services=services, root=root, runs=runs, evals=evals, snap=published.snapshot, plan_id=published.plan_id)


def copy_of(published, tmp_path, app_root, policy=None):
    root = tmp_path / "rt"
    shutil.copytree(published.root, root)
    return _services(root, app_root, policy)


def phase(snap, line, soi, kind="WORK", ordinal=0) -> str:
    return f"{job_id(snap, line, soi)}.{PhaseKind(kind).value}.{ordinal}"


def facts_by(view, team):
    record = view.record(team, S1)
    return record, {(f.phase, f.segment): f for f in view.unit_facts(record)}


# ---------------------------------------------------------------------- the frozen goals and the canonical outcomes

def test_goal_of_record_freezes_b0_and_amendment_adds_without_mutating(world) -> None:
    w = world
    view = ObservationView.build(w.services, P3)
    reg = view.registry
    assert set(reg.day_plan.values()) == {w.plan_id}
    record, facts = facts_by(view, "PROD-B")
    frozen = reg.goals[record.goal_id]
    assert record.goal is frozen and frozen.hash == frozen.computed_hash()                  # the accepted goal, unchanged
    assert record.B0 == frozen.standard_person_minutes == 408                               # NC3 104 + PU3 104 + ROOT3 200
    nc3b = next(f for f in facts.values() if f.origin == "AMENDMENT")
    assert nc3b.effort == 104 and nc3b.status == "EARNED"                                   # approved feasible addition
    assert sum(f.effort for f in facts.values()) == 512 and {f.unit_id for f in facts.values()} >= {u.id for u in frozen.earning_units}


def test_canonical_outcomes_at_first_collection(world) -> None:
    ev = world.evals[P1]
    a, b, q = ev["PROD-A"], ev["PROD-B"], ev["QA"]
    # PROD-A: planned A1 240 earned; offplan B1 240 + B2 180 in U; offplan OOS (B2) is not charged again
    assert (a.B, a.E, a.Cp, a.U, a.O) == (240, 0, 240, 420, 0)
    assert a.base == Fraction(100) * (Fraction(7, 10) + Fraction(3, 10) * Fraction(240, 660)) and str(a.score) == "80.9"
    # PROD-B: B = 408 + 104 approved addition; Cp = NC3 + PU3 + ROOT3 seg0 + NC3B; O = 10% of the 80 planned ROOT3 WORK0 minutes
    assert (b.B0, b.B, b.E, b.Cp, b.U, b.O) == (408, 512, 0, 344, 0, 8)
    assert b.final == Fraction(100) * (Fraction(7, 10) * Fraction(344, 512) + Fraction(3, 10)) - Fraction(800, 512) and str(b.score) == "75.5"
    # QA: NC3 final QA kept after the administrative cancel; PU3 final QA reopened (not earned); ODA buyoff cancelled (stays in B0)
    assert (q.B, q.E, q.Cp, q.U, q.O) == (200, 0, 45, 0, 0) and str(q.score) == "45.8"
    assert {e.status for e in ev.values()} == {EvaluationStatus.DEMONSTRATION}


# ---------------------------------------------------------------------- A56: cancel, prior completion, reopen, late correction

def test_a56_cancel_before_completion_earns_nothing_and_stays_in_b0(world) -> None:
    view = ObservationView.build(world.services, P3)
    record, facts = facts_by(view, "QA")
    oda = facts[(phase(world.snap, 1303, "ROOT3", "OTHER_BUYOFF"), 0)]
    assert oda.status == "MISSED" and not oda.completed_at and "CANCELLED_BEFORE_COMPLETION_EARNS_NOTHING" in oda.reasons
    assert record.B0 == 200 and oda.unit_id in {u.id for u in record.original_units}      # cancellation never shrinks B0


def test_a56_prior_verified_completion_follows_ordered_policy(world, published, tmp_path, app_root) -> None:
    view = ObservationView.build(world.services, P3)
    nc3_qa = phase(world.snap, 1303, "NC3", "FINAL_QA")
    _, qa = facts_by(view, "QA")
    _, pb = facts_by(view, "PROD-B")
    assert qa[(nc3_qa, 0)].status == "EARNED" and "CANCELLED_AFTER_VERIFIED_COMPLETION_KEEPS_PRIOR_CREDIT" in qa[(nc3_qa, 0)].reasons
    assert pb[(phase(world.snap, 1303, "NC3"), 0)].status == "EARNED"
    # the same ordered facts under the explicit alternative policy revoke the prior credit
    events = [e for e in view.effective if e.job == job_id(world.snap, 1303, "NC3") and (e.phase in (None, nc3_qa))]
    policy = world.services.policy.with_overrides({"lifecycle.cancel_after_verified_completion": "CANCELLATION_REVOKES_CREDIT"},
                                                  new_id="rebuild-v1-cancel-revokes")
    keep = phase_lifecycle(nc3_qa, job_id(world.snap, 1303, "NC3"), events, world.services.policy)
    revoke = phase_lifecycle(nc3_qa, job_id(world.snap, 1303, "NC3"), events, policy)
    assert keep.completed_at is not None and revoke.completed_at is None and revoke.disposition == "CANCELLED"
    with pytest.raises(DomainError) as err:
        phase_lifecycle(nc3_qa, "J", events, world.services.policy.with_overrides({"lifecycle.cancel_after_verified_completion": "WHATEVER"},
                                                                                 new_id="bad"))
    assert err.value.code == "POLICY_VALUE_UNSUPPORTED"


def test_a56_reopen_revokes_and_late_void_appends_corrected_evaluation(world) -> None:
    first, second = world.evals[P1]["QA"], world.evals[P2]["QA"]
    assert first.Cp == 45 and second.Cp == 90                                              # PU3 final QA re-earned once the reopen is voided
    assert second.revision == first.revision + 1 and second.supersedes == first.id
    series = history.revisions(world.services, world.services.settings.scope_key, first.series_key)
    assert [e.id for e in series][:2] == [first.id, second.id]                              # prior as-known evaluation preserved
    assert history.as_known(world.services, world.services.settings.scope_key, first.series_key, parse_utc("2026-10-27T08:00:00Z")).id == first.id
    assert history.get(world.services, first.id) == first                                  # immutable, not rewritten
    # the third collection changed nothing for QA beyond what is already known: no new revision
    assert world.evals[P3]["QA"].id == second.id


def test_a56_reported_completion_is_a_claim_never_credit(world) -> None:
    view = ObservationView.build(world.services, P3)
    _, pb = facts_by(view, "PROD-B")
    w1 = pb[(phase(world.snap, 1303, "ROOT3", "WORK", 1), 0)]
    assert w1.status == "MISSED" and "REPORTED_COMPLETION_IS_A_CLAIM" in w1.reasons


def test_a56_terminal_status_and_disappearance_are_unknown_not_credit(world, published, tmp_path, app_root) -> None:
    policy = world.services.policy
    t0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

    def ev(kind, minute, verified=False, phase_id="J.WORK.0", origin="SOURCE"):
        at = t0.replace(minute=minute)
        return LifecycleEvent(digest_id("T", kind, minute), "S", "lifecycle_events" if origin == "SOURCE" else "derived", f"k:{kind}:{minute}", origin,
                              kind, "J", phase_id if kind != "DISAPPEARED" else None, (), at, at, at, at, minute, "1", None, verified,
                              "Q" if verified else None, None, None, (), "")

    terminal = phase_lifecycle("J.WORK.0", "J", [ev("TERMINAL_STATUS", 5)], policy)
    assert terminal.unknown and terminal.completed_at is None and terminal.disposition == "TERMINAL_UNVERIFIED"
    gone = phase_lifecycle("J.WORK.0", "J", [ev("DISAPPEARED", 6, origin="DERIVED")], policy)
    assert gone.unknown and gone.disposition == "DISAPPEARED"
    verified = phase_lifecycle("J.WORK.0", "J", [ev("TERMINAL_STATUS", 5), ev("COMPLETED", 9, verified=True)], policy)
    assert not verified.unknown and verified.completed_at == t0.replace(minute=9)       # only verified completion resolves it
    # integrated: planned work whose job leaves the census with no terminal fact makes the official result unknown
    services = copy_of(published, tmp_path, app_root)
    collect_observations(services, as_of=P1)
    view = ObservationView.build(services, P1)
    shrunk = replace(view.graph, job_ids=view.graph.job_ids - {job_id(world.snap, 1303, "ROOT3")})
    gone_events = derive_disappearances(view, shrunk, ingested_at=P1)
    assert {e.job for e in gone_events} == {job_id(world.snap, 1303, "ROOT3")} and all(e.kind == "DISAPPEARED" for e in gone_events)
    store = ObservationStore(services.repositories, services.settings.scope_key)
    for e in gone_events:
        store.put(EVENTS, e)
    qa = evaluate_shift(services, "QA", S1, as_of=P1)
    assert qa.status is EvaluationStatus.UNKNOWN and qa.score is None and any(r.startswith("UNKNOWN_DISPOSITION") for r in qa.reasons)
    assert qa.B == 200                                                                     # the denominator is never deleted


def test_a56_revisions_voids_and_corrections_append(world) -> None:
    e = lambda key, kind, minute, rev="1", sup=None, verified=True: LifecycleEvent(  # noqa: E731
        digest_id("T", key, rev), "S", "lifecycle_events", f"lifecycle_events:{key}", "SOURCE", kind, "J",
        None if kind in ("VOIDED",) else "J.WORK.0", (), datetime(2026, 10, 26, 12, minute, tzinfo=UTC), datetime(2026, 10, 26, 13, minute, tzinfo=UTC),
        datetime(2026, 10, 26, 13, minute, tzinfo=UTC), datetime(2026, 10, 26, 14, 0, tzinfo=UTC), minute, rev, None,
        verified and kind not in ("VOIDED", "CORRECTION"), "Q" if verified and kind not in ("VOIDED", "CORRECTION") else None, None, sup, (), "")
    done_r1 = e("DONE", "COMPLETED", 10)
    done_r2 = e("DONE", "COMPLETED", 20, rev="2")
    eff, notes = resolve_revisions([done_r1, done_r2])
    assert [x.effective_at.minute for x in eff] == [20] and notes[done_r1.id].startswith("SUPERSEDED_BY_REVISION")
    void = e("VOID", "VOIDED", 30, sup="lifecycle_events:DONE")
    eff, notes = resolve_revisions([done_r1, void])
    assert eff == [] and notes[done_r1.id] == "VOIDED"
    unvoid = e("UNVOID", "VOIDED", 31, sup="lifecycle_events:VOID")
    eff, _ = resolve_revisions([done_r1, void, unvoid])
    assert [x.id for x in eff] == [done_r1.id]                                              # a void of the void restores the fact
    fix = replace(e("FIX", "CORRECTION", 15, sup="lifecycle_events:DONE"), verified=True, verified_by="QA-2")
    eff, notes = resolve_revisions([done_r1, fix])
    assert eff[0].effective_at.minute == 15 and eff[0].recorded_at == fix.recorded_at and notes[done_r1.id].startswith("CORRECTED_BY")


# ---------------------------------------------------------------------- A54: taxonomy, claims, decisions, union relief

def test_a54_taxonomy_is_exact_and_policy_cannot_redefine_it(world) -> None:
    assert len(DISRUPTION_CAUSES) == 8 and len(set(DISRUPTION_CAUSES)) == 8 and len(NOMINALLY_EXCUSABLE) == 5
    assert NOMINALLY_EXCUSABLE == {"LATE_PART", "CROSS_TEAM_PREDECESSOR", "CAPACITY_SHORTAGE", "SKILL_SHORTAGE", "REWORK_INJECTION"}
    policy = world.services.policy
    assert disruption.taxonomy_errors(policy) == []
    for key, value in (("disruption.causes", list(DISRUPTION_CAUSES) + ["WEATHER"]), ("disruption.nominally_excusable", ["LATE_PART", "DURATION_OVERRUN"]),
                       ("disruption.overrun_auto_excuse", "RATIO_3X")):
        with pytest.raises(PolicyError) as err:                                            # user requirements are not overridable
            policy.with_overrides({key: value}, new_id="tampered")
        assert err.value.code == "USER_CONSTANT"

    class Tampered:                                                                         # a bundle edited on disk is still refused at runtime
        def __init__(self, key, value):
            self.key, self.value = key, value

        def seq(self, key):
            return tuple(self.value) if key == self.key else policy.seq(key)

        def str(self, key):
            return self.value if key == self.key else policy.str(key)

    for key, value in (("disruption.causes", list(DISRUPTION_CAUSES) + ["WEATHER"]), ("disruption.nominally_excusable", ["LATE_PART"]),
                       ("disruption.overrun_auto_excuse", "RATIO_3X")):
        assert disruption.taxonomy_errors(Tampered(key, value))


def _prod_b(services, at=P1):
    view = ObservationView.build(services, at)
    record, facts = facts_by(view, "PROD-B")
    missed = sorted(f.unit_id for f in facts.values() if f.status == "MISSED")
    earned = sorted(f.unit_id for f in facts.values() if f.status == "EARNED")
    return view, record, facts, missed, earned


def test_a54_all_eight_causes_duplicates_and_union_relief(published, tmp_path, app_root) -> None:
    services = copy_of(published, tmp_path, app_root)
    collect_observations(services, as_of=P1)
    view, record, facts, missed, earned = _prod_b(services)
    missed_effort = sum(f.effort for f in facts.values() if f.status == "MISSED")
    assert missed_effort == 168
    at = parse_utc("2026-10-26T20:00:00Z")
    claims = {}
    specs = {
        "LATE_PART": dict(evidence=["RELEASE:R:PART:demo"], notes=""),
        "CROSS_TEAM_PREDECESSOR": dict(origin_team="QA", evidence=["PHASE:qa"], notes=""),
        "SAME_TEAM_PREDECESSOR": dict(evidence=[], notes="PU3 overran and gates ROOT3 through final QA"),
        "CAPACITY_SHORTAGE": dict(evidence=["ATTENDANCE:B1-02 pulled to training"], notes=""),
        "SKILL_SHORTAGE": dict(evidence=["CERT:B revoked mid-shift"], notes=""),
        "DURATION_OVERRUN": dict(evidence=[], notes="Standard looks too low for this repair; IE review requested"),
        "REWORK_INJECTION": dict(origin_team="PROD-A", evidence=["DEFECT:FIXTURE-NCR-303"], notes=""),
        "PLAN_CHURN": dict(evidence=["REPLAN:demo"], notes=""),
    }
    for i, (cause, spec) in enumerate(specs.items()):
        claims[cause] = disruption.submit_claim(services, request_id=f"c{i}", requested_by="demo:lead-prod-b", goal_id=record.goal_id, unit_ids=missed,
                                                cause=cause, at=at, **spec)
    dup = disruption.submit_claim(services, request_id="dup", requested_by="demo:mechanic", goal_id=record.goal_id, unit_ids=missed,
                                  cause="LATE_PART", evidence=["RELEASE:R:PART:demo"], notes="", at=at)
    assert dup.id != claims["LATE_PART"].id                                                 # duplicates are kept as separate evidence
    for cause in ("SAME_TEAM_PREDECESSOR", "DURATION_OVERRUN"):                             # notes enforced
        with pytest.raises(DomainError) as err:
            disruption.submit_claim(services, request_id=f"nonote-{cause}", requested_by="demo:lead-prod-b", goal_id=record.goal_id, unit_ids=missed,
                                    cause=cause, evidence=["x"], notes="  short ", at=at)
        assert err.value.code == "NOTES_REQUIRED"
    with pytest.raises(DomainError) as err:
        disruption.submit_claim(services, request_id="earned", requested_by="demo:lead-prod-b", goal_id=record.goal_id, unit_ids=earned[:1],
                                cause="LATE_PART", evidence=["x"], notes="", at=at)
    assert err.value.code == "UNIT_ALREADY_EARNED"
    # claims alone change nothing: evidence is not relief
    assert evaluate_shift(services, "PROD-B", S1, as_of=at).E == 0
    decide_at = parse_utc("2026-10-27T10:00:00Z")
    nominal = [claims[c].id for c in ("LATE_PART", "CROSS_TEAM_PREDECESSOR", "CAPACITY_SHORTAGE", "SKILL_SHORTAGE", "REWORK_INJECTION")] + [dup.id]
    d1 = disruption.decide(services, request_id="d1", authority="demo:excusal", authority_role="excusal_decider", claim_ids=nominal,
                           decision="APPROVED", unit_ids=missed, reason="evidenced", at=decide_at)
    assert d1.exception is False and d1.review_track == "STANDARD"
    with pytest.raises(DomainError) as err:                                                # PLAN_CHURN is reviewed separately
        disruption.decide(services, request_id="mix", authority="demo:excusal", authority_role="excusal_decider",
                          claim_ids=[claims["PLAN_CHURN"].id, claims["LATE_PART"].id], decision="APPROVED", unit_ids=missed,
                          reason="mixed review attempt", at=decide_at)
    assert err.value.code == "PLAN_CHURN_SEPARATE_REVIEW"
    with pytest.raises(DomainError) as err:                                                # an exception approval needs a substantive reason
        disruption.decide(services, request_id="thin", authority="demo:excusal", authority_role="excusal_decider",
                          claim_ids=[claims["DURATION_OVERRUN"].id], decision="APPROVED", unit_ids=missed, reason="ok", at=decide_at)
    assert err.value.code == "REASON_REQUIRED"
    d2 = disruption.decide(services, request_id="d2", authority="demo:excusal", authority_role="excusal_decider",
                           claim_ids=[claims["SAME_TEAM_PREDECESSOR"].id, claims["DURATION_OVERRUN"].id], decision="APPROVED", unit_ids=missed,
                           reason="Reviewed exception with IE standard review", at=decide_at)
    d3 = disruption.decide(services, request_id="d3", authority="demo:excusal", authority_role="excusal_decider",
                           claim_ids=[claims["PLAN_CHURN"].id], decision="APPROVED", unit_ids=missed, reason="Replan withdrew this work",
                           at=decide_at)
    assert d2.exception and d3.exception and d3.review_track == "PLAN_CHURN"
    ev = evaluate_shift(services, "PROD-B", S1, as_of=decide_at)
    claimed_sum = sum(facts[(f.phase, f.segment)].effort for c in list(claims.values()) + [dup] for f in facts.values() if f.unit_id in c.units)
    assert claimed_sum == 9 * 168 and ev.E == 168 == missed_effort                         # union of approved missed units, capped at frozen effort
    assert ev.D == ev.B - 168 and ev.B0 == 408                                               # B0 preserved


def test_a54_review_authority_self_review_and_appeals(world, published, tmp_path, app_root) -> None:
    w = world
    view = ObservationView.build(w.services, P3)
    record = view.record("PROD-B", S1)
    decisions = [d for d in view.decisions if d.goal_id == record.goal_id]
    key = lambda k: digest_id("DXD", w.services.settings.scope_key, "feed", k)  # noqa: E731
    assert {d.id for d in decisions} == {key("DEC-B-01"), key("DEC-B-02"), key("DEC-B-03"), key("DEC-B-04")}
    effective = {d.id for d in disruption.effective_decisions(view, record.goal_id)}
    assert key("DEC-B-03") not in effective and key("DEC-B-04") in effective                # the appeal supersedes, the original stays stored
    assert next(d for d in decisions if d.id == key("DEC-B-02")).exception is True           # SAME_TEAM approval is a recorded exception
    services = copy_of(published, tmp_path, app_root)
    collect_observations(services, as_of=P3)
    _, record, _, missed, _ = _prod_b(services, P3)
    claim = disruption.submit_claim(services, request_id="mine", requested_by="demo:excusal", goal_id=record.goal_id, unit_ids=missed[:1],
                                    cause="CAPACITY_SHORTAGE", evidence=["ATTENDANCE:x"], notes="", at=P3)
    for role, who, code in (("excusal_decider", "demo:excusal", "SELF_REVIEW"), ("team_lead", "demo:other", "AUTHORITY_NOT_REVIEWER")):
        with pytest.raises(DomainError) as err:
            disruption.decide(services, request_id=f"x-{code}", authority=who, authority_role=role, claim_ids=[claim.id], decision="APPROVED",
                              unit_ids=missed[:1], reason="approve", at=P3)
        assert err.value.code == code
    with pytest.raises(DomainError) as err:
        disruption.decide(services, request_id="again", authority="demo:excusal-2", authority_role="excusal_decider",
                          claim_ids=[digest_id("DCL", services.settings.scope_key, "feed", "CLM-B-06")], decision="REJECTED", unit_ids=[],
                          reason="second appeal of the original decision", supersedes=key("DEC-B-03"), at=P3)
    assert err.value.code == "DECISION_ALREADY_SUPERSEDED"
    with pytest.raises(DomainError) as err:
        disruption.decide(services, request_id="stale", authority="demo:excusal-2", authority_role="excusal_decider", claim_ids=[claim.id],
                          decision="REJECTED", unit_ids=[], reason="not evidenced enough", expected_version=0, at=P3)
    assert err.value.code == "STALE_VERSION"


def test_a54_feed_intake_quarantines_with_stable_reasons_and_replays_idempotently(world) -> None:
    run = world.runs[P1]
    codes = {(q.feed, q.source_key, q.code) for q in run.quarantine}
    assert codes == {("disruption_claims", "CLM-B-04", "NOTES_REQUIRED"), ("disruption_claims", "CLM-B-05", "ORIGIN_TEAM_UNEVIDENCED"),
                     ("lifecycle_events", "EV-A1-DONE", "REVISION_CONTENT_CONFLICT"), ("lifecycle_events", "EV-BAD-DISAPPEARED", "DERIVED_ONLY"),
                     ("lifecycle_events", "EV-BAD-KIND", "BAD_EVENT_KIND"), ("lifecycle_events", "EV-BAD-VERIFIED-REPORT", "REPORTED_NOT_VERIFIED")}
    life = run.feed("lifecycle_events")
    assert life.duplicates == 1 and life.admitted + life.duplicates + life.quarantined == life.rows
    store = ObservationStore(world.services.repositories, world.services.settings.scope_key)
    before = {c: len(store.all(c)) for c in (EVENTS, CLAIMS, DECISIONS, VIOLATIONS)}
    again = collect_observations(world.services, as_of=P3)
    assert again.id == world.runs[P3].id                                                   # replay: same run, nothing new appended
    assert {c: len(ObservationStore(world.services.repositories, world.services.settings.scope_key).all(c)) for c in before} == before
    a1 = next(e for e in store.all(EVENTS) if e.source_key == "lifecycle_events:EV-A1-DONE")
    assert a1.effective_at == parse_utc("2026-10-26T12:20:00Z")                               # the first ingestion stands


# ---------------------------------------------------------------------- A55: explicit defect origin, no DAG guess, no automatic 3x

def _auto(view, team, cause):
    record = view.record(team, S1)
    latest = {c.id: c for c in disruption.effective_claims(view, record.goal_id).values()}    # each chain's current revision, once
    return [c for c in latest.values() if c.attribution == "AUTO" and c.cause == cause and c.status != "WITHDRAWN"]


def test_a55_rework_origin_comes_from_defect_evidence_not_executing_team(world, published, tmp_path, app_root) -> None:
    view = ObservationView.build(world.services, P3)
    rework = _auto(view, "PROD-B", "REWORK_INJECTION")
    assert len({c.id for c in rework}) == 1
    claim = rework[0]
    assert claim.origin_team == "PROD-A" and claim.executing_team == "PROD-B" and "DEFECT:FIXTURE-NCR-303" in claim.evidence
    assert claim.status == "SUBMITTED" and not any(claim.id in d.claim_ids for d in view.decisions)  # evidence, not an approval
    # without the explicit defect row the injected NC3B is not attributable as rework at all: origin is never guessed from the DAG
    services = copy_of(published, tmp_path, app_root)
    reader = FixtureObservationReader(INPUTS, overrides={"defect_evidence": {
        "schema_version": 1, "dataset": "defect_evidence", "columns": ["SOURCE_EVENT_KEY", "LINE_NUMBER", "SOI", "ROOT_SOI", "REWORK_CLASS",
                                                                        "DEFECT_ORIGIN_TEAM", "EVIDENCE_REF", "OBSERVED_AT_UTC"],
        "rows": [], "receipt": {"complete": True, "certified_empty": True}}})
    run = collect_observations(services, as_of=P1, reader=reader)
    assert run.feed("defect_evidence").status == "CERTIFIED_EMPTY"
    blind = ObservationView.build(services, P1)
    assert _auto(blind, "PROD-B", "REWORK_INJECTION") == []
    assert {q.code for q in run.quarantine if q.source_key == "CLM-B-01"} == {"ORIGIN_TEAM_UNEVIDENCED"}   # the manual claim's origin is unproven now


def test_a55_culpability_walks_past_blocked_neighbours(world) -> None:
    """QA's ROOT3 holds were blocked by ROOT3 WORK 0, itself blocked by PU3: the late, unblocked PU3 WORK (PROD-B) is culpable."""
    view = ObservationView.build(world.services, P3)
    cross = _auto(view, "QA", "CROSS_TEAM_PREDECESSOR")
    assert len(cross) == 1 and cross[0].origin_team == "PROD-B"
    assert cross[0].evidence == (f"PHASE:{phase(world.snap, 1303, 'PU3')}",)                 # not the immediate neighbour ROOT3 WORK 0
    same = _auto(view, "PROD-B", "SAME_TEAM_PREDECESSOR")
    assert same and all(c.status == "PROPOSED" for c in same)                               # same-team: a proposal awaiting notes
    assert not _auto(view, "PROD-B", "CROSS_TEAM_PREDECESSOR")                              # QA is never blamed by DAG direction
    rejected = [d for d in view.decisions if d.decision == "REJECTED"]
    assert rejected and all(digest_id("DCL", world.services.settings.scope_key, "feed", "CLM-B-06") in d.claim_ids for d in rejected)
    # the reversed-DAG variant: flipping every rework edge leaves the defect origin untouched (it never reads edges)
    flipped = replace(view.graph, hard_in={})
    injected = disruption._injected_rework(view, view.record("PROD-B", S1), flipped)
    assert [(o, e) for _, o, e, _ in injected] == [("PROD-A", "PROD-B")]


def test_a55_no_automatic_overrun_excuse_below_or_above_3x(world) -> None:
    view = ObservationView.build(world.services, P3)
    overruns = _auto(view, "PROD-B", "DURATION_OVERRUN")
    ratios = sorted(float(c.evidence[0].split("ratio=")[1]) for c in overruns)
    assert 1.0 < ratios[0] < 3.0 and ratios[-1] > 3.0                                       # NC3 1.06x below, PU3 5.00x above
    assert all(c.status == "PROPOSED" for c in overruns)                                     # never submitted, never approved automatically
    auto_ids = {c.id for c in overruns}
    assert not any(auto_ids & set(d.claim_ids) for d in view.decisions)
    assert all(d.authority != disruption.SYSTEM_ATTRIBUTOR for d in view.decisions)


# ---------------------------------------------------------------------- A26 + OOS: actual history kept, never legalizing

def test_a26_oos_record_survives_predecessor_completion_and_never_legalizes(world) -> None:
    w = world
    store = ObservationStore(w.services.repositories, w.services.settings.scope_key)
    root4 = phase(w.snap, 1304, "ROOT4")
    first = [v for v in store.known(VIOLATIONS, P1) if v.phase == root4]
    assert len(first) == 1
    v = first[0]
    assert v.effective_at == parse_utc("2026-10-23T12:00:00Z")                              # the actual start, not backdated or re-anchored
    assert v.incomplete_predecessors == (phase(w.snap, 1304, "PRE4"),) and v.discretionary and v.detected_at == P1
    later = ObservationView.build(w.services, P3)
    pre4 = later.lifecycles[phase(w.snap, 1304, "PRE4")]
    assert pre4.completed_at == parse_utc("2026-10-28T11:20:00Z")                            # PRE4 really completed afterwards
    assert [x for x in store.all(VIOLATIONS) if x.phase == root4] == [v]                     # and the original OOS record is unchanged
    # readiness is still governed by the graph: the accepted plan's ROOT4 continuation is not dispatchable before PRE4
    from focus.publication.service import current_plan
    plan = current_plan(w.services, "DEMO-787", "EXECUTABLE")[0].artifact.candidate
    action = next(a for a in plan.assignments if a.job == job_id(w.snap, 1304, "ROOT4") and a.kind is PhaseKind.WORK)
    decision = dispatch_check(w.services, w.plan_id, action.id, now=parse_utc("2026-10-26T12:00:00Z"))
    assert not decision.dispatchable and "PREDECESSOR_INCOMPLETE" in {c for c, _ in decision.reasons}
    pre = next(a for a in plan.assignments if a.job == job_id(w.snap, 1304, "PRE4") and a.kind is PhaseKind.WORK)
    assert pre.end <= action.start                                                           # incoming edge retained by the planner


def test_planned_oos_charged_once_offplan_oos_not_double_charged(world) -> None:
    view = ObservationView.build(world.services, P3)
    record, facts = facts_by(view, "PROD-B")
    w0 = phase(world.snap, 1303, "ROOT3")
    oos = [o for o in view.oos_for(record) if o.charged]
    assert len(oos) == 1 and oos[0].affected_effort == sum(f.effort for (p, _), f in facts.items() if p == w0) == 80
    assert facts[(w0, 0)].status == "EARNED" and facts[(w0, 0)].oos_charged                  # Cp keeps the earned unit; O is separate
    a_record = view.record("PROD-A", S1)
    assert view.oos_for(a_record) == []
    b2 = phase(world.snap, 1302, "B2")
    assert any(v.phase == b2 for v in view.violations) and view.offplan_oos("PROD-A", S1)    # recorded as compliance evidence
    assert world.evals[P3]["PROD-A"].O == 0 and world.evals[P3]["PROD-A"].U == 420           # its effort is in U once, no numeric O


# ---------------------------------------------------------------------- coverage, freshness and unknown semantics

def test_missing_or_uncertified_feeds_withhold_the_result(published, tmp_path, app_root) -> None:
    services = copy_of(published, tmp_path, app_root)
    empty = {"schema_version": 1, "dataset": "excusal_decisions", "rows": [], "receipt": {"complete": True},
             "columns": ["DECISION_KEY", "TEAM", "SHIFT_DATE", "SHIFT_NUMBER", "CLAIM_KEYS", "APPROVED_WORK_REFS", "DECISION", "AUTHORITY",
                         "AUTHORITY_ROLE", "DECISION_AT_UTC", "REASON", "REVIEW_TRACK", "SUPERSEDES_DECISION_KEY", "OBSERVED_AT_UTC"]}
    reader = FixtureObservationReader(INPUTS, overrides={"excusal_decisions": empty})
    run = collect_observations(services, as_of=P1, reader=reader)
    assert run.feed("excusal_decisions").status == "EMPTY_UNCERTIFIED"                       # an empty feed without an affirmative receipt
    ev = evaluate_shift(services, "PROD-B", S1, as_of=P1)
    assert ev.status is EvaluationStatus.UNKNOWN and ev.score is None and ev.E == 0
    assert any(r.startswith("COVERAGE_EXCUSAL_DECISIONS_EMPTY_UNCERTIFIED") for r in ev.reasons)
    missing = FixtureObservationReader(tmp_path / "nowhere")
    run2 = collect_observations(services, as_of=parse_utc("2026-10-26T19:45:00Z"), reader=missing)
    assert {f.status for f in run2.feeds} == {"FAILED"}
    ev2 = evaluate_shift(services, "PROD-B", S1, as_of=parse_utc("2026-10-26T19:45:00Z"))
    assert ev2.status is EvaluationStatus.UNKNOWN and ev2.score is None                      # missing evidence is unknown, never zero excuses


def test_open_window_and_live_reader_are_honest(published, tmp_path, app_root) -> None:
    services = copy_of(published, tmp_path, app_root)
    mid = parse_utc("2026-10-26T15:00:00Z")
    collect_observations(services, as_of=mid)
    ev = evaluate_shift(services, "PROD-A", S1, as_of=mid)
    assert ev.status is EvaluationStatus.UNKNOWN and any(r.startswith("WINDOW_OPEN") for r in ev.reasons)
    live = LiveObservationReader(app_root)
    cap = live.capture(FEED_BY_NAME["lifecycle_events"], mid)
    assert cap.status == "NOT_PROBED" and not cap.complete and cap.reason.startswith("NOT_INSTALLED")


def test_later_replan_goals_are_proposals_not_b0(published, tmp_path, app_root) -> None:
    services = copy_of(published, tmp_path, app_root)
    run2 = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="life-2")
    candidate, _ = schedule(services, source_run=run2.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=7)
    second = publish(services, candidate.id)
    assert second.status == "ACCEPTED", second.reasons
    reg = GoalRegistry.load(services)
    assert set(reg.day_plan.values()) == {published.plan_id}                                 # the first acceptance of the day stays of record
    record = reg.records[("PROD-B", S1)]
    assert record.plan_id == published.plan_id and record.later_goal_ids                     # the replan's goal is only a proposal


# ---------------------------------------------------------------------- commands through durable worker runs

def test_commands_run_as_workers_with_receipts(world, published, tmp_path, app_root) -> None:
    from focus.workers import WorkerService

    services = copy_of(published, tmp_path, app_root)
    collect_observations(services, as_of=P1)
    _, record, _, missed, _ = _prod_b(services, P1)
    repos = services.repositories
    claim_run, _ = repos.create_worker_run(kind="disruption_claim", scope=services.settings.scope_key, idempotency_key="k1",
                                           requested_by="demo:lead-prod-b", request={"goal_id": record.goal_id, "unit_ids": missed,
                                                                                     "cause": "CAPACITY_SHORTAGE", "evidence_ids": ["ATT:1"],
                                                                                     "notes": ""}, budget_seconds=30)
    seen = WorkerService(services).run_once()
    assert seen.status == "completed" and seen.result_receipt["claim_status"] == "SUBMITTED"
    obs_run, _ = repos.create_worker_run(kind="reported_observation", scope=services.settings.scope_key, idempotency_key="k2",
                                         requested_by="demo:mechanic-a05", request={"job_id": job_id(world.snap, 1301, "A2"), "kind": "COMPLETED",
                                                                                     "effective_at": "2026-10-26T09:00:00Z", "notes": "done"},
                                         budget_seconds=30)
    done = WorkerService(services).run_once()
    assert done.status == "completed" and done.result_receipt["verified"] is False
    ie_run, _ = repos.create_worker_run(kind="ie_review", scope=services.settings.scope_key, idempotency_key="k3", requested_by="demo:ie",
                                        request={"job_id": job_id(world.snap, 1303, "PU3"), "standard_revision": "fixture-v1",
                                                 "proposed_correction": "raise PU P75", "reason": "5x burn observed"}, budget_seconds=30)
    ie = WorkerService(services).run_once()
    assert ie.status == "completed" and ie.result_receipt["affects_issued_goals"] is False
    bad, _ = repos.create_worker_run(kind="excusal_decision", scope=services.settings.scope_key, idempotency_key="k4", requested_by="demo:lead-prod-b",
                                     request={"claim_ids": [seen.result_receipt["claim_id"]], "decision": "APPROVED", "unit_ids": missed,
                                              "notes": "self approval", "_server": {"authority_role": "excusal_decider"}},
                                     budget_seconds=30)
    failed = WorkerService(services).run_once()
    assert failed.status == "failed" and failed.error["code"] == "SELF_REVIEW"
