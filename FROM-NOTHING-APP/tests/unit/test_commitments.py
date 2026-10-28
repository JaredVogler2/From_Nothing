"""P14: delivery hysteresis and persistent commitment state (A49, A50)."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.engine.commitments import DeliveryCommitments, DeliveryState, HysteresisPolicy, observe
from focus.engine.max.optimizer.orchestrator import MaxOptimizer

BASE = date(2026, 11, 20)
SCOPE = "DEMO-787"


@pytest.fixture()
def hp(policy) -> HysteresisPolicy:
    return HysteresisPolicy.from_policy(policy)


def d(days: int) -> date:
    return BASE + timedelta(days=days)


def established(hp: HysteresisPolicy, line: int = 1301) -> DeliveryState:
    obs = observe(DeliveryState.empty(line), "P0", BASE, hp)
    assert obs.decision == "ESTABLISHED" and obs.state.current_date == obs.state.original_date == BASE
    return obs.state


def test_policy_constants_are_the_user_values(hp) -> None:
    assert (hp.hold_days, hp.bad_news_days, hp.confirmations, hp.tolerance_days) == (2, 7, 2, 1)


@pytest.mark.parametrize(("delta", "first", "second", "committed"), [
    (1, "HOLD", "HOLD", 0),                  # jitter below two days never moves the date
    (2, "PENDING", "CONFIRMED", 2),          # boundary: |2| is not < 2, so it needs two distinct confirmations
    (6, "PENDING", "CONFIRMED", 6),          # just below bad news: still waits for confirmation
    (7, "BAD_NEWS", "HOLD", 7),              # seven-day worsening changes immediately; the repeat is then zero delta
    (-1, "HOLD", "HOLD", 0),
    (-3, "PENDING", "CONFIRMED", -3),        # improvements always need confirmation, unlike seven-day worsening
])
def test_a49_each_delta_on_first_and_repeated_accepted_run(hp, delta, first, second, committed) -> None:
    o1 = observe(established(hp), "R1", d(delta), hp)
    o2 = observe(o1.state, "R2", d(delta), hp)
    assert (o1.decision, o2.decision) == (first, second)
    assert o2.state.current_date == d(committed) and o2.state.original_date == BASE          # original goal never rewritten
    if first == "HOLD" and delta:
        assert o1.state.risk == f"raw projection {delta:+d} days (held)" and o1.state.raw_projection == d(delta)   # raw risk visible
    if first == "PENDING":
        assert o1.state.current_date == BASE and o1.state.pending_target == d(delta) and "1/2" in o1.state.risk
    if first == "BAD_NEWS":
        assert [(e.kind, e.from_date, e.to_date) for e in o1.events] == [("BAD_NEWS", BASE, d(7))]


def test_a49_large_improvement_and_tolerance_boundary(hp) -> None:
    big = observe(established(hp), "R1", d(-10), hp)
    assert big.decision == "PENDING" and big.state.current_date == BASE                  # never immediate, however large
    agree = observe(observe(established(hp), "R1", d(3), hp).state, "R2", d(4), hp)       # targets 1 day apart agree
    assert agree.decision == "CONFIRMED" and agree.state.current_date == d(4)
    apart = observe(observe(established(hp), "R1", d(3), hp).state, "R2", d(5), hp)       # 2 days apart: restart the count
    assert apart.decision == "PENDING" and apart.state.pending_target == d(5) and apart.state.confirmations == 1
    third = observe(apart.state, "R3", d(5), hp)
    assert third.decision == "CONFIRMED" and third.state.current_date == d(5)


def test_a49_replayed_run_never_counts_and_unknown_never_fabricates(hp) -> None:
    pending = observe(established(hp), "R1", d(3), hp)
    replay = observe(pending.state, "R1", d(3), hp)
    assert replay.decision == "REPLAY" and replay.state == pending.state and not replay.events      # same id: no streak
    unknown = observe(pending.state, "R2", None, hp)
    assert unknown.decision == "UNKNOWN" and unknown.state.current_date == BASE                       # no invented date
    assert unknown.state.risk.startswith("PROJECTION_UNKNOWN") and unknown.state.confirmations == 1   # not a confirmation
    confirmed = observe(unknown.state, "R3", d(3), hp)
    assert confirmed.decision == "CONFIRMED" and confirmed.state.current_date == d(3)


def test_a49_safety_alert_is_immediate_while_the_date_is_held(hp) -> None:
    o1 = observe(established(hp), "R1", d(1), hp, safety_alert="COMMITMENT_UNSAFE: part late")
    assert o1.decision == "HOLD" and [e.kind for e in o1.events] == ["SAFETY_ALERT"]          # no hysteresis on safety
    assert o1.state.alert == "COMMITMENT_UNSAFE: part late" and o1.state.current_date == BASE
    o2 = observe(o1.state, "R2", d(1), hp, safety_alert="COMMITMENT_UNSAFE: part late")
    assert not o2.events                                                                       # an ongoing alert is not re-raised
    o3 = observe(o2.state, "R3", d(1), hp)
    assert o3.state.alert is None
    o4 = observe(o3.state, "R4", d(1), hp, safety_alert="COMMITMENT_UNSAFE: part late")
    assert [e.kind for e in o4.events] == ["SAFETY_ALERT"]                                     # recurrence is a new alert


# ---------------------------------------------------------------------- repository-backed state (A50)

class Crash(RuntimeError):
    pass


def _ledger(root, policy, fault=None) -> DeliveryCommitments:
    repos = Repositories(FixtureStore(root, fault=fault), mode="fixture", scope_key=SCOPE, clock=FixedClock(FIXTURE_CUTOFF))
    return DeliveryCommitments(repos, SCOPE, policy)


def _kinds(ledger: DeliveryCommitments, line: int) -> list[str]:
    return [e.kind for e in ledger.events(line)]


def test_a50_replay_and_rejection_add_no_streak_or_duplicate_change(tmp_path, policy) -> None:
    ledger = _ledger(tmp_path / "rt", policy)
    ledger.record_accepted_plan("P0", {1301: BASE, 1302: BASE})
    first = ledger.record_accepted_plan("P1", {1301: d(3), 1302: BASE})
    assert dict(first.decisions) == {1301: "PENDING", 1302: "HOLD"} and first.status == "RECORDED"
    outbox = ledger.outbox_ids()
    again = ledger.record_accepted_plan("P1", {1301: d(3), 1302: BASE})
    assert again.status == "REPLAY" and again.decisions == first.decisions and again.events == first.events
    rejected = ledger.record_accepted_plan("P2", {1301: d(3), 1302: BASE}, rejected=True)
    assert rejected.status == "REJECTED" and not rejected.decisions
    assert ledger.get(1301)[0].confirmations == 1 and ledger.outbox_ids() == outbox                # neither counted
    done = ledger.record_accepted_plan("P3", {1301: d(3), 1302: BASE})
    assert dict(done.decisions)[1301] == "CONFIRMED" and ledger.get(1301)[0].current_date == d(3)
    assert _kinds(ledger, 1301) == ["ESTABLISHED", "CONFIRMED"]                                    # exactly one change


def test_a50_cohort_exit_and_reentry_keep_the_commitment(tmp_path, policy) -> None:
    ledger = _ledger(tmp_path / "rt", policy)
    ledger.record_accepted_plan("P0", {1301: BASE, 1302: BASE})
    gone = ledger.record_accepted_plan("P1", {1302: BASE})
    state, _ = ledger.get(1301)
    assert dict(gone.decisions)[1301] == "COHORT_EXIT" and not state.in_cohort
    assert state.current_date == state.original_date == BASE                                         # not forgotten
    ledger.record_accepted_plan("P2", {1302: BASE})
    back = ledger.record_accepted_plan("P3", {1301: d(1), 1302: BASE})
    state, _ = ledger.get(1301)
    assert dict(back.decisions)[1301] == "HOLD" and state.in_cohort and state.current_date == BASE
    assert _kinds(ledger, 1301) == ["ESTABLISHED", "COHORT_EXIT", "COHORT_REENTRY"]                 # one exit, one reentry


def test_a50_crash_after_event_before_acknowledgment_recovers_once(tmp_path, policy, app_root) -> None:
    armed = {"point": None}

    def fault(point: str) -> None:
        if point == armed["point"]:
            armed["point"] = None
            raise Crash(point)

    root = tmp_path / "rt"
    ledger = _ledger(root, policy, fault)
    ledger.record_accepted_plan("P0", {1301: BASE})
    armed["point"] = "before_state:delivery_commitments"                  # the event is in the outbox, the state is not written
    with pytest.raises(Crash):
        ledger.record_accepted_plan("P1", {1301: d(8)})
    assert len(ledger.outbox_ids()) == 2 and _kinds(ledger, 1301) == ["ESTABLISHED"]   # unacknowledged: invisible
    assert ledger.get(1301)[0].current_date == BASE
    script = ("import json,sys; from pathlib import Path; from datetime import date\n"
              "from focus.clock import FIXTURE_CUTOFF, FixedClock\nfrom focus.data.repositories import Repositories\n"
              "from focus.data.store import FixtureStore\nfrom focus.engine.commitments import DeliveryCommitments\n"
              "from focus.policy import load_policy\n"
              "policy = load_policy(Path('config/policies'), 'rebuild-v1')\n"
              "repos = Repositories(FixtureStore(Path(sys.argv[1])), mode='fixture', scope_key='DEMO-787', clock=FixedClock(FIXTURE_CUTOFF))\n"
              "r = DeliveryCommitments(repos, 'DEMO-787', policy).record_accepted_plan('P1', {1301: date(2026, 11, 28)})\n"
              "print(json.dumps({'status': r.status, 'decisions': r.decisions, 'events': [e.id for e in r.events]}))\n")
    out = subprocess.run([sys.executable, "-c", script, str(root)], cwd=app_root, capture_output=True, text=True, timeout=120)
    recovered = json.loads(out.stdout)                                     # recovery in a fresh process
    assert recovered["status"] == "RECORDED" and recovered["decisions"] == [[1301, "BAD_NEWS"]]
    fresh = _ledger(root, policy)
    assert _kinds(fresh, 1301) == ["ESTABLISHED", "BAD_NEWS"] and fresh.get(1301)[0].current_date == d(8)
    assert len(fresh.outbox_ids()) == 2 and recovered["events"] == [e.id for e in fresh.events(1301)][1:]   # same id re-derived
    armed["point"] = "before_put:delivery_receipts"                        # state committed, receipt lost (client timeout)
    ledger = _ledger(root, policy, fault)
    with pytest.raises(Crash):
        ledger.record_accepted_plan("P2", {1301: d(8)})
    recovered = ledger.record_accepted_plan("P2", {1301: d(8)})
    assert recovered.status == "RECORDED" and dict(recovered.decisions) == {1301: "HOLD"}
    assert ledger.record_accepted_plan("P2", {1301: d(8)}).status == "REPLAY"
    assert _kinds(ledger, 1301) == ["ESTABLISHED", "BAD_NEWS"] and ledger.get(1301)[0].counted[-1] == ("P2", "HOLD")


def test_a50_concurrent_writer_retries_and_the_lost_race_stays_invisible(tmp_path, policy) -> None:
    root = tmp_path / "rt"
    a, b = _ledger(root, policy), _ledger(root, policy)
    a.record_accepted_plan("P0", {1301: BASE})
    real = a.repos.put_state
    raced = {"done": False}

    def racing_put_state(collection, record_id, value, expected_version):
        if not raced["done"]:
            raced["done"] = True
            b.record_accepted_plan("P2", {1301: d(8)})                    # another worker commits between A's read and write
        return real(collection, record_id, value, expected_version)

    a.repos.put_state = racing_put_state
    receipt = a.record_accepted_plan("P1", {1301: d(8)})
    state, version = a.get(1301)
    assert dict(receipt.decisions) == {1301: "HOLD"} and state.current_date == d(8) and version == 3   # re-derived on the new state
    assert [(e.kind, e.plan_id) for e in a.events(1301)] == [("ESTABLISHED", "P0"), ("BAD_NEWS", "P2")]  # one change, B's
    assert len(a.outbox_ids()) == 3                                                                    # A's stale event is an orphan
    assert {p for p, _ in state.counted} == {"P0", "P1", "P2"}


def test_later_search_cannot_silently_undo_a_kept_commitment() -> None:
    def ev(key, outcomes):
        results = [SimpleNamespace(incumbent_assignment_id=i, outcome=o) for i, o in outcomes.items()]
        return SimpleNamespace(key=key, decode=SimpleNamespace(commitment_results=results))

    best = ev((-2, -1, 0, 0, 100), {"I1": "KEPT", "I2": "CREW_CHANGED"})
    cheaper_but_crew_swapped = ev((-2, -1, 0, 0, 50), {"I1": "CREW_CHANGED", "I2": "KEPT"})
    assert not MaxOptimizer.better(cheaper_but_crew_swapped, best)        # same counts, but I1 lost its exact crew
    upgraded = ev((-2, -2, 0, 0, 120), {"I1": "KEPT", "I2": "KEPT"})
    assert MaxOptimizer.better(upgraded, best)
    lost = ev((-1, -1, 0, 0, 10), {"I1": "KEPT", "I2": "MOVED"})
    assert not MaxOptimizer.better(lost, best) and not MaxOptimizer.better(best, best)
