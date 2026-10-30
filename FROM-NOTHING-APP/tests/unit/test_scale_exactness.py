"""P22: scale shortcuts are exact. Each one is pinned to the full rule it replaces or to a brute-force definition.

* ``DutyLedger.person_conflict`` and ``cap_problem`` return exactly the error ``plan`` raises.
* ``transaction`` restores the complete ledger state, including the containers mutated in place.
* The cross-shift overlap check (a keyed bisect over a person's rows) equals a brute-force scan.
* ``EngineContext.qualifies_within`` equals the exact interval check, including certificates expiring mid-window.
* The memoized snapshot verification still rejects a tampered snapshot on every validation.
* Transport ids are unchanged by the id memo.
"""
from __future__ import annotations

import copy
import dataclasses
import random
import types
from datetime import date, datetime

import pytest

from focus.calendar import ShiftPolicy, build_shift
from focus.domain import DutyKind, JobKey, PersonKey, PoolKey, RequirementBundle, digest_id, minute_floor, to_minute
from focus.eligibility import Eligibility
from focus.engine.ledger import DutyLedger, LedgerError, PersonWindow, ToolCalendar, _Span
from focus.engine.model import EngineContext
from focus.engine.travel import TravelPolicy
from tests.unit.test_eligibility import _holding

DAY = date(2026, 10, 27)


@pytest.fixture()
def shift_policy(policy) -> ShiftPolicy:
    return ShiftPolicy.from_policy(policy)


@pytest.fixture()
def s1(shift_policy):
    return build_shift(DAY, 1, shift_policy)


def _m(shift_policy, hhmm: str, day: date = DAY) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return to_minute(datetime(day.year, day.month, day.day, h, m, tzinfo=shift_policy.tz))


def _window(person: str, shift, pool: str = "POOL") -> PersonWindow:
    return PersonWindow(person, shift.id, pool, shift.regular_windows, (shift.ot_window,), shift.regular_cap, 60)


def _ledger(policy, windows, caps=None, tools=None, counted=(DutyKind.WORK, DutyKind.TRAVEL, DutyKind.TOOL, DutyKind.WAIT, DutyKind.OTHER)):
    return DutyLedger({(w.person, w.shift_id): w for w in windows}, TravelPolicy.from_policy(policy), caps or {"POOL": 8}, list(counted), tools)


def _plan_error(ledger, crew, shift_id, pool, segments, kind=DutyKind.WORK) -> tuple[str, str] | None:
    try:
        ledger.plan("PROBE", crew, 1301, shift_id, pool, segments, kind)
    except LedgerError as exc:
        return exc.code, exc.detail
    return None


# ---------------------------------------------------------------------- person_conflict == plan's first per-person checks


def test_person_conflict_returns_exactly_what_plan_raises(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    s3 = build_shift(DAY, 3, shift_policy)
    next_s1 = build_shift(date(2026, 10, 28), 1, shift_policy)
    ledger = _ledger(policy, [_window("P", s1), _window("Q", s1, pool="OTHER"), _window("R", s3), _window("R", next_s1)])
    ledger.book("A", ["P"], 1301, s1.id, "POOL", [(m("08:00"), m("09:00"), False)])
    nd = date(2026, 10, 28)
    ledger.add_locked("CI", "R", _m(shift_policy, "05:00", nd), _m(shift_policy, "06:30", nd), DutyKind.OTHER, None, s3.id, "POOL")
    cases = [
        ("Z", s1.id, [(m("10:00"), m("10:30"), False)], "PERSON_NOT_AVAILABLE"),                 # no admitted window
        ("Q", s1.id, [(m("10:00"), m("10:30"), False)], "POOL_MISMATCH"),                        # another pool
        ("P", s1.id, [(m("08:30"), m("09:30"), False)], "PERSON_OVERLAP"),                       # located duty in the shift
        ("R", next_s1.id, [(_m(shift_policy, "06:15", nd), _m(shift_policy, "07:00", nd), False)], "PERSON_OVERLAP"),  # across labels
    ]
    for person, shift_id, segments, code in cases:
        conflict = ledger.person_conflict(person, shift_id, "POOL", segments)
        assert conflict is not None and conflict.code == code
        assert (conflict.code, conflict.detail) == _plan_error(ledger, [person], shift_id, "POOL", segments)
    # positive boundaries: touching duty is no overlap, and plan agrees that nothing per-person stops it
    assert ledger.person_conflict("P", s1.id, "POOL", [(m("09:00"), m("09:30"), False)]) is None
    assert ledger.person_conflict("R", next_s1.id, "POOL", [(_m(shift_policy, "06:30", nd), _m(shift_policy, "07:30", nd), False)]) is None
    assert _plan_error(ledger, ["R"], next_s1.id, "POOL", [(_m(shift_policy, "06:30", nd), _m(shift_policy, "07:30", nd), False)]) is None


def test_cap_problem_equals_the_activation_rule_of_plan(policy, s1) -> None:
    a = s1.regular_windows[0][0]
    ledger = _ledger(policy, [_window(p, s1) for p in ("P0", "P1", "P2")], caps={"POOL": 2}, counted=(DutyKind.WORK,))
    ledger.book("W0", ["P0"], 1301, s1.id, "POOL", [(a, a + 30, False)])
    assert ledger.cap_problem("POOL", ["P1"], DutyKind.WORK) is None                    # exactly at the cap: allowed
    assert _plan_error(ledger, ["P1"], s1.id, "POOL", [(a + 60, a + 90, False)]) is None
    problem = ledger.cap_problem("POOL", ["P1", "P2"], DutyKind.WORK)                    # one over the cap
    assert problem is not None and problem.code == "ACTIVATION_CAP"
    assert (problem.code, problem.detail) == _plan_error(ledger, ["P1", "P2"], s1.id, "POOL", [(a + 60, a + 90, False)])
    assert ledger.cap_problem("POOL", ["P0", "P1"], DutyKind.WORK) is None               # reuse of an active head adds none
    assert ledger.cap_problem("POOL", ["P1", "P2"], DutyKind.WAIT) is None               # an uncounted kind never activates
    held = ledger.cap_problem("POOL", ["P1", "P2"], DutyKind.WORK, active=ledger.active("POOL"))   # caller-held active set
    assert held is not None and (held.code, held.detail) == (problem.code, problem.detail)
    unknown = ledger.cap_problem("NOPE", ["P1"], DutyKind.WORK)
    assert unknown is not None and unknown.code == "POOL_UNKNOWN"


# ---------------------------------------------------------------------- transaction snapshot


def _state(ledger: DutyLedger):
    return copy.deepcopy((ledger._shift, ledger._all, ledger._bookings, ledger._active, ledger.tools.reservations))


def test_failed_transaction_restores_every_container_including_in_place_ones(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    ledger = _ledger(policy, [_window("P", s1), _window("Q", s1)], tools=ToolCalendar({"TORQUE-1": 2}))
    ledger.book("A", ["P"], 1301, s1.id, "POOL", [(m("07:00"), m("08:00"), False)], tools=["TORQUE-1"])
    ledger.book("B", ["Q"], 1302, s1.id, "POOL", [(m("07:00"), m("08:00"), False)])
    before = _state(ledger)
    with pytest.raises(LedgerError):
        with ledger.transaction():
            ledger.remove("A")                                                        # active counts change in place
            ledger.book("C", ["P", "Q"], 1303, s1.id, "POOL", [(m("10:00"), m("10:30"), False)], tools=["TORQUE-1"])  # appends
            ledger.book("D", ["P"], 1303, s1.id, "POOL", [(m("10:15"), m("10:45"), False)])                          # overlap: fails
    assert _state(ledger) == before
    ledger.book("C", ["P", "Q"], 1303, s1.id, "POOL", [(m("10:00"), m("10:30"), False)], tools=["TORQUE-1"])      # still usable
    assert set(ledger.bookings()) == {"A", "B", "C"} and len(ledger.tools.reservations["TORQUE-1"]) == 2


def test_successful_transaction_keeps_its_changes(policy, shift_policy, s1) -> None:
    m = lambda t: _m(shift_policy, t)  # noqa: E731
    ledger = _ledger(policy, [_window("P", s1)])
    with ledger.transaction():
        ledger.book("A", ["P"], 1301, s1.id, "POOL", [(m("07:00"), m("08:00"), False)])
    assert set(ledger.bookings()) == {"A"} and ledger.active("POOL") == frozenset({"P"})


# ---------------------------------------------------------------------- keyed bisect == brute force


def test_cross_shift_overlap_check_equals_brute_force(policy, s1) -> None:
    ledger = _ledger(policy, [_window("P", s1)])
    rng = random.Random(22)
    rows, t = [], 0
    for i in range(60):                                         # a person's committed, non-overlapping rows over many shifts
        t += rng.randint(0, 40)
        length = rng.randint(1, 50)
        rows.append((t, t + length, f"I{i}", rng.choice(("X", "Y", "Z"))))
        t += length
    ledger._all["P"] = rows

    def brute(shift_id: str, a: int, b: int) -> bool:
        return any(r[0] < b and a < r[1] and r[3] != shift_id for r in rows)

    for _ in range(3000):
        a = rng.randint(-10, t + 10)
        b = a + rng.randint(1, 60)
        shift_id = rng.choice(("X", "Y", "Z", "W"))
        try:
            ledger._check_cross_shift("P", shift_id, [_Span(a, b)])
            raised = False
        except LedgerError as exc:
            raised = exc.code == "PERSON_OVERLAP"
        assert raised == brute(shift_id, a, b), (shift_id, a, b)


# ---------------------------------------------------------------------- whole-window qualification memo


def test_qualifies_within_equals_the_exact_interval_check() -> None:
    def mm(text: str) -> int:
        return minute_floor(datetime.fromisoformat(text))
    holdings = [
        _holding("full", "A"), _holding("full", "B"),
        _holding("expiring", "A", end="2026-10-27T12:00:00+00:00"), _holding("expiring", "B"),          # expires mid-window
        _holding("late", "A", start="2026-10-27T11:00:00+00:00"), _holding("late", "B"),                # becomes valid mid-window
        _holding("revoked", "A", revoked="2026-10-27T11:30:00+00:00"), _holding("revoked", "B"),
        _holding("alt", "A"), _holding("alt", "B2"),
        _holding("inactive", "A", status="SUSPENDED"), _holding("inactive", "B"),
    ]
    index = Eligibility(holdings, {}, source_run_id="RUN-1", policy_hash="P1").index
    fake = types.SimpleNamespace(eligibility=types.SimpleNamespace(index=index), _whole_window={})
    bundles = [RequirementBundle((("A",), ("B", "B2")), "mechanic", 1, "exact"), RequirementBundle((), "mechanic", 1, "none")]
    window = (mm("2026-10-27T10:00:00+00:00"), mm("2026-10-27T14:00:00+00:00"))
    rng = random.Random(7)
    intervals = [window, (window[0], window[0] + 30), (window[1] - 30, window[1]), (window[0] - 30, window[0] + 30), (window[1] - 30, window[1] + 30)]
    intervals += [(a, a + rng.randint(1, 120)) for a in (rng.randint(window[0] - 60, window[1]) for _ in range(300))]
    for person in ("full", "expiring", "late", "revoked", "alt", "inactive", "nobody"):
        for bundle in bundles:
            for start, end in intervals:
                exact = (not bundle.groups) or index.check_bundle(person, bundle, start, end).allowed
                assert EngineContext.qualifies_within(fake, person, bundle, window, start, end) == exact, (person, start, end)
    assert fake._whole_window[("full", bundles[0], *window)] is True                       # memoized whole-window yes
    assert fake._whole_window[("expiring", bundles[0], *window)] is False                  # never a shortcut for a partial holder


# ---------------------------------------------------------------------- validation and identity


def test_memoized_snapshot_verification_still_rejects_tampering(demo, policy) -> None:
    from focus.domain import Purpose
    from focus.engine.max.optimizer.orchestrator import schedule_snapshot
    from focus.validation.input import ValidationInput
    from focus.validation.plan import validate

    services, _, snapshot = demo
    plan, run = schedule_snapshot(services, snapshot, purpose=Purpose.EXECUTABLE, budget_seconds=10, max_moves=5)
    assert run.validation.valid
    tampered = dataclasses.replace(snapshot, cutoff=snapshot.cutoff.replace(minute=snapshot.cutoff.minute + 1))   # content without a new hash
    inp = ValidationInput.build(tampered, policy, Purpose.EXECUTABLE)
    for _ in range(2):                                                                  # the memo must keep failing, not pass
        receipt = validate(tampered, policy, plan, inp=inp)
        assert not receipt.valid and any(v.code == "SNAPSHOT_HASH_MISMATCH" for v in receipt.violations)
    clean = ValidationInput.build(snapshot, policy, Purpose.EXECUTABLE)
    assert clean.snapshot_integrity() == "OK" and inp.snapshot_integrity() != "OK"


def test_memoized_transport_ids_are_unchanged() -> None:
    job = JobKey("DEMO", "787", 1301, "A0", None, None, None, None)
    person = PersonKey("E100", "DEMO-HR")
    pool = PoolKey("DEMO", "PROD-A", DAY, 1, "RUN-1")
    assert job.id == digest_id("J", "JobKey", "DEMO", "787", 1301, "A0", None, None, None, None)
    assert person.id == digest_id("H", "PersonKey", "DEMO-HR", "E100")
    assert pool.id == digest_id("Q", "PoolKey", "DEMO", "PROD-A", DAY, 1, "RUN-1")
    assert JobKey("DEMO", "787", 1302, "A0", None, None, None, None).id != job.id      # distinct keys stay distinct
