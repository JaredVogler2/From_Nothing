"""The optional native core (``focus_native``, Rust) decides exactly what the Python reference decides.

* Ledger: thousands of seeded random operations (bookings, removals, carry-in, nested transactions with rollback,
  conflicts, caps, tools, travel through breaks) give the same items, the same errors with the same messages and the
  same capacity answers on both ledgers.
* Plans: the demo, the P22 scale template and the production shape schedule to the same candidate (same id, same
  content hash) with the native core on and off, both purposes, search moves included.
* Fallback: the setting without the extension uses the Python ledger and says so.

Skipped when the extension is not installed (``pip install ./native``); the Python ledger stays the reference.
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path

import pytest

from focus.config import Settings, SettingsError
from focus.domain import DutyKind
from focus.engine import native
from focus.engine.ledger import DutyLedger, LedgerError, PersonWindow, ToolCalendar
from focus.engine.travel import TravelPolicy

PEOPLE = {"POOL-A": ("P1", "P2", "P3"), "POOL-B": ("P4", "P5", "P6")}
SHIFTS = {"S1": 0, "S2": 1440}
TRAVEL = TravelPolicy(0, 15, 15, "MUSTER_AT_FIRST_ASSIGNMENT", False)
CAPS = {"POOL-A": 2, "POOL-B": 3}
COUNTED = (DutyKind.WORK, DutyKind.TRAVEL, DutyKind.TOOL, DutyKind.WAIT, DutyKind.OTHER)


def _windows() -> dict[tuple[str, str], PersonWindow]:
    out = {}
    for shift, base in SHIFTS.items():
        for pool, people in PEOPLE.items():
            for person in people:
                out[(person, shift)] = PersonWindow(person, shift, pool, ((base, base + 240), (base + 270, base + 480)),
                                                    ((base + 480, base + 540),), 300, 60)
    return out


def _pair():
    pytest.importorskip("focus_native")
    windows = _windows()
    py = DutyLedger(windows, TRAVEL, CAPS, COUNTED, ToolCalendar({"JACK": 1}))
    ctx = native._module().Context(
        [(p, s, w.pool_id, list(w.regular), list(w.ot), w.regular_cap, w.ot_cap) for (p, s), w in windows.items()],
        (0, 15, 15), list(CAPS.items()), [k.value for k in COUNTED], [("JACK", 1)], [(pool, list(m)) for pool, m in PEOPLE.items()], [], [])
    rs = native.NativeDutyLedger(ctx, windows, TRAVEL, CAPS, COUNTED)
    return py, rs


def _outcome(fn):  # type: ignore[no-untyped-def]
    try:
        value = fn()
    except LedgerError as exc:
        return ("ERR", exc.code, exc.detail)
    return ("OK", value)


def _state(ledger) -> tuple:  # type: ignore[no-untyped-def]
    items = tuple(ledger.all_items())
    minutes = tuple(ledger.duty_minutes(p, s) for p in sum(PEOPLE.values(), ()) for s in SHIFTS)
    points = tuple(tuple(ledger.free_points(p, s)) for p in sum(PEOPLE.values(), ()) for s in SHIFTS)
    active = tuple(sorted(ledger.active(pool)) for pool in PEOPLE)
    return items, minutes, points, active, tuple(sorted(ledger.bookings()))


def _random_segments(rng: random.Random, base: int) -> list[tuple[int, int, bool]]:
    start = base + rng.choice((0, 15, 60, 120, 225, 240, 250, 300, 420, 470, 500))
    length = rng.choice((15, 30, 60, 90, 120))
    if rng.random() < 0.2:                                                      # two segments around the break
        return [(start, start + length, False), (start + length + 30, start + length + 60, rng.random() < 0.3)]
    if rng.random() < 0.05:
        return [(start, start, False)]                                          # invalid on purpose
    return [(start, start + length, rng.random() < 0.15)]


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_ledger_operations_match_the_python_reference(seed: int) -> None:
    py, rs = _pair()
    rng = random.Random(seed)
    everyone = sum(PEOPLE.values(), ()) + ("P9",)
    booked: list[str] = []
    for step in range(450):
        op = rng.random()
        shift = rng.choice(tuple(SHIFTS))
        pool = rng.choice(tuple(PEOPLE))
        if op < 0.55:
            crew = rng.sample(everyone, rng.choice((1, 1, 2, 3)))
            if rng.random() < 0.05:
                crew = crew + crew[:1]
            aid = f"A{step}" if rng.random() > 0.03 or not booked else rng.choice(booked)
            args = (aid, crew, rng.choice((1, 2, 3)), shift, pool, _random_segments(rng, SHIFTS[shift]),
                    rng.choice((DutyKind.WORK, DutyKind.WORK, DutyKind.WAIT, DutyKind.OTHER)), rng.choice(((), (), ("JACK",), ("SAW",))))
            a = _outcome(lambda: py.book(*args) and None)
            b = _outcome(lambda: rs.book(*args) and None)
            assert a == b, (step, args)
            if a[0] == "OK":
                booked.append(aid)
        elif op < 0.65 and booked:
            aid = rng.choice(booked + ["NOPE"])
            a = _outcome(lambda: py.remove(aid).crew)
            b = _outcome(lambda: rs.remove(aid).crew)
            assert a == b, (step, "remove", aid)
            if a[0] == "OK":
                booked.remove(aid)
        elif op < 0.72:
            person = rng.choice(everyone)
            start = SHIFTS[shift] + rng.choice((-30, 0, 100, 200, 470))
            args = (f"CI{step}", person, start, start + rng.choice((30, 60)), rng.choice((DutyKind.OTHER, DutyKind.WORK)),
                    rng.choice((None, 1, 2)), shift, rng.choice((None, pool)))
            assert _outcome(lambda: py.add_locked(*args)) == _outcome(lambda: rs.add_locked(*args)), (step, "locked")
            assert py.problems == rs.problems
        else:                                                                    # nested transaction, sometimes rolled back
            fail = rng.random() < 0.5
            plans = [(f"T{step}-{i}", rng.sample(PEOPLE[pool], rng.choice((1, 2))), 1, shift, pool, _random_segments(rng, SHIFTS[shift]))
                     for i in range(rng.choice((1, 2, 3)))]
            for ledger in (py, rs):
                try:
                    with ledger.transaction():
                        for p in plans:
                            try:
                                with ledger.transaction():
                                    ledger.book(*p)
                            except LedgerError:
                                pass
                        if fail:
                            raise RuntimeError("roll back")
                except RuntimeError:
                    pass
            if not fail:
                booked.extend(aid for aid in (p[0] for p in plans) if aid in py.bookings())
        assert _state(py) == _state(rs), step
        person, segs = rng.choice(everyone), _random_segments(rng, SHIFTS[shift])
        assert _outcome(lambda: py.person_conflict(person, shift, pool, segs)) .__repr__() == _outcome(lambda: rs.person_conflict(person, shift, pool, segs)).__repr__()
        crew = rng.sample(everyone, 2)
        a, b = py.cap_problem(pool, crew, DutyKind.WORK), rs.cap_problem(pool, crew, DutyKind.WORK)
        assert (a and (a.code, a.detail)) == (b and (b.code, b.detail))
        when = SHIFTS[shift] + rng.choice((0, 200, 500))
        assert py.last_located(person, shift, when) == rs.last_located(person, shift, when)


def _plans_identical(app_root: Path, shape: str, n: int, max_moves: int, **options) -> dict:
    from focus.engine.max.optimizer.solver import benchmark_size

    run = benchmark_size(app_root, n, budget_seconds=120, max_moves=max_moves, solver=False, purposes=("EXECUTABLE", "FORECAST"), shape=shape,
                         engines=("python", "native"), shape_options=options or None)
    assert run["status"] == "COMPLETE"
    for purpose, cmp in run["engines"].items():
        runs = cmp["runs"]
        assert runs["native"]["engine"]["engine"] == "native" and runs["python"]["engine"]["engine"] == "python"
        assert cmp["identical"], (purpose, runs)
        assert runs["native"]["valid"] and runs["python"]["valid"]
    return run


def test_scale_template_plans_are_identical(app_root: Path) -> None:
    pytest.importorskip("focus_native")
    _plans_identical(app_root, "scale", 12, max_moves=4)


def test_production_shape_plans_are_identical(app_root: Path) -> None:
    pytest.importorskip("focus_native")
    _plans_identical(app_root, "production", 2, max_moves=3, baseline=600, edges=2800, rework_total=400)


def test_demo_plans_are_identical_with_search_moves(app_root: Path) -> None:
    pytest.importorskip("focus_native")
    from focus.clock import FIXTURE_CUTOFF, FixedClock
    from focus.data.acquisition import acquire, load_snapshot, make_reader
    from focus.data.repositories import Repositories
    from focus.data.store import FixtureStore
    from focus.domain import Purpose
    from focus.engine.max.optimizer.orchestrator import schedule_snapshot
    from focus.runtime import build_services

    with tempfile.TemporaryDirectory() as tmp:
        clock = FixedClock(FIXTURE_CUTOFF)

        def services(flag: bool):  # type: ignore[no-untyped-def]
            settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False, native_core=flag)
            return build_services(settings, repositories=Repositories(FixtureStore(Path(tmp)), mode="fixture", scope_key=settings.scope_key,
                                                                      clock=clock), clock=clock)

        base = services(False)
        snapshot_id = acquire(base, make_reader(base, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="native").snapshot_id
        for purpose in (Purpose.EXECUTABLE, Purpose.FORECAST):
            out = {}
            for flag in (False, True):
                svc = services(flag)
                plan, run = schedule_snapshot(svc, load_snapshot(svc.repositories, snapshot_id), purpose=purpose, budget_seconds=60, max_moves=20)
                out[flag] = (plan.id, plan.hash, run.native["engine"], run.moves)
            assert out[False][:2] == out[True][:2] and out[True][2] == "native" and out[False][3] > 0, out


def test_setting_without_the_extension_uses_the_python_ledger(monkeypatch) -> None:
    monkeypatch.setitem(native._LOADED, "module", None)
    assert native.available() is False
    assert native.status(True) == {"requested": True, "available": False, "engine": "python"}
    assert native.status(False)["engine"] == "python"


def test_native_core_setting_is_strict() -> None:
    assert Settings.from_env({"FOCUS_NATIVE_CORE": "true"}).native_core is True
    assert Settings.from_env({}).native_core is False
    with pytest.raises(SettingsError):
        Settings.from_env({"FOCUS_NATIVE_CORE": "maybe"})
