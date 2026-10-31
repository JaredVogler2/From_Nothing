"""Optional native scheduling core (Rust, ``native/``): the duty ledger and crew-selection hot path.

``focus_native`` is a line-for-line port of ``ledger.DutyLedger``, the crew choice of ``crew_selection.choose_crew`` and
the boolean holding checks of ``eligibility.HoldingIndex``. Every decision, tie-break, error code and message is the
same, so a decode through it produces byte-identical plans (pinned by ``tests/unit/test_native_core.py``). Python stays
the rule authority, and the independent validator re-checks every plan from source either way.

It is off by default. ``FOCUS_NATIVE_CORE=true`` turns it on when the extension is installed
(``pip install ./native``, or a prebuilt ``focus_native`` wheel). When the setting is on but the extension is missing,
the engine uses the Python ledger and records the fallback in the run receipt; it never fails because of the setting.

``NativeDutyLedger`` presents the ``DutyLedger`` interface that the decoder, crew selection and the orchestrator use.
The hot path skips the conversions: the decoder's whole time-slot search for a phase (``search``: shifts, candidate
starts, calendar advance, crew choice, booking) runs in Rust, and bookings stay on the Rust side as handles until
committed.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Sequence

from ..domain import DutyKind
from .ledger import Booking, DutyItem, LedgerError

if TYPE_CHECKING:  # pragma: no cover
    from .model import EngineContext

_LOADED: dict[str, Any] = {}
PROTOCOL = 2            # the Context row formats this module sends; the extension reports its own (2: revocation windows)


def _module() -> Any:
    """The ``focus_native`` extension, imported on first use (an optional package, like the solver). None when it is absent
    or built for another protocol: a wheel from another commit is never fed rows it cannot read."""
    if "module" not in _LOADED:
        try:
            import focus_native  # type: ignore[import-not-found]
        except ImportError:
            focus_native = None
        if focus_native is not None and getattr(focus_native, "PROTOCOL", 1) != PROTOCOL:
            _LOADED["reason"] = f"focus_native protocol {getattr(focus_native, 'PROTOCOL', 1)} != {PROTOCOL}: rebuild native/ from this commit"
            focus_native = None
        _LOADED["module"] = focus_native
    return _LOADED["module"]


def available() -> bool:
    return _module() is not None


def status(requested: bool) -> dict[str, Any]:
    """The receipt entry: what was asked for and what ran (and why the Python core ran, when the extension was refused)."""
    out = {"requested": requested, "available": available(), "engine": "native" if requested and available() else "python"}
    if "reason" in _LOADED:
        out["reason"] = _LOADED["reason"]
    return out


@dataclass(frozen=True)
class NativeBooking(Booking):
    """A planned booking held on the Rust side until ``commit``."""

    handle: int = 0
    new_shift_items: dict = field(default_factory=dict)


def _item(t: tuple) -> DutyItem:
    (iid, person, start, end, kind, line, origin, known, shift, pool, aid, locked) = t
    return DutyItem(iid, person, start, end, DutyKind(kind), line, origin, known, shift, pool, aid, locked)


def _raise(problem: tuple[str, str] | None) -> None:
    if problem is not None:
        raise LedgerError(*problem)


def build_context(ctx: "EngineContext") -> Any:
    """The immutable Rust context for one engine context: windows, pools, members, holdings and team certificates."""
    windows = [(person, shift, w.pool_id, list(w.regular), list(w.ot), int(w.regular_cap), int(w.ot_cap))
               for (person, shift), w in ctx.windows.items()]
    travel = (ctx.travel.same_aircraft_minutes, ctx.travel.changed_aircraft_minutes, ctx.travel.unknown_origin_minutes)
    holdings = []
    for person, certs in ctx.eligibility.index._by_person.items():           # noqa: SLF001 - the index's own rows, read once
        for cert, items in certs.items():
            for h in items:
                holdings.append((person, cert, h.active, h.known, h.start, h.end, h.revoked, list(h.windows)))
    context = _module().Context(windows, travel, list(ctx.pool_caps.items()), sorted(k.value for k in ctx.counted_kinds),
                                list(ctx.tool_capacity.items()), [(pool, list(members)) for pool, members in ctx._members.items()],  # noqa: SLF001
                                holdings, [(team, sorted(certs)) for team, certs in ctx.team_certs.items()])
    register_calendar(context, ctx)
    return context


def register_calendar(context: Any, ctx: "EngineContext") -> None:
    """Every line's eligible shifts (``EngineContext.line_shifts``) and each shift's team pools (``pool_for``)."""
    teams_by_shift: dict[tuple, list[tuple[str, str]]] = {}
    for (team, start_date, number), pool in sorted(ctx.pool_by_key.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        teams_by_shift.setdefault((start_date, number), []).append((team, pool.id))
    shifts: dict[str, tuple] = {}
    lines = []
    for line in sorted({j.line for j in ctx.jobs.values()}):
        ids = []
        for sh in ctx.line_shifts(line):
            if sh.id not in shifts:
                shifts[sh.id] = (sh.id, sh.label, sh.start, sh.end, list(sh.regular_windows), sh.cutoff_coord, sh.cutoff_instant,
                                 sh.ot_window, teams_by_shift.get((sh.start_date, sh.number), []))
            ids.append(sh.id)
        lines.append((line, ids))
    context.register_calendar(ctx.shift_policy.ot_minutes, ctx.purpose.value, list(shifts.values()), lines)


class NativeDutyLedger:
    """``DutyLedger`` over the Rust core (same interface, same results)."""

    native = True

    def __init__(self, context: Any, windows, travel, pool_caps, counted_kinds) -> None:
        self._context = context
        self._n = context.ledger()
        self.windows = dict(windows)
        self.travel = travel
        self.pool_caps = dict(pool_caps)
        self.counted = frozenset(counted_kinds)
        self._bundles: dict[Any, int] = {}

    # ------------------------------------------------------------------ queries
    @property
    def problems(self) -> list[str]:
        return self._n.problems()

    def shift_items(self, person: str, shift_id: str) -> tuple[DutyItem, ...]:
        return tuple(_item(t) for t in self._n.shift_items(person, shift_id))

    def items(self, person: str) -> list[DutyItem]:
        return [_item(t) for t in self._n.items(person)]

    def all_items(self) -> list[DutyItem]:
        return [_item(t) for t in self._n.all_items()]

    def bookings(self) -> dict[str, Booking]:
        out = {}
        for aid, kind, line, shift, pool, crew, segments, tools in self._n.bookings():
            out[aid] = Booking(aid, DutyKind(kind), line, shift, pool, tuple(crew), tuple(segments), tuple(tools))
        return out

    def active(self, pool_id: str) -> frozenset[str]:
        return frozenset(self._n.active(pool_id))

    def duty_minutes(self, person: str, shift_id: str) -> tuple[int, int]:
        return self._n.duty_minutes(person, shift_id)

    def last_located(self, person: str, shift_id: str, before: int) -> DutyItem | None:
        t = self._n.last_located(person, shift_id, before)
        return None if t is None else _item(t)

    def free_points(self, person: str, shift_id: str) -> list[int]:
        return self._n.free_points(person, shift_id)

    def candidate_starts(self, pool_id: str, shift_id: str, first: int, cutoff: int, travel: int) -> list[int]:
        return self._n.candidate_starts(pool_id, shift_id, first, cutoff, travel)

    # ------------------------------------------------------------------ carry-in
    def add_locked(self, item_id: str, person: str, start: int, end: int, kind: DutyKind, line: int | None, shift_id: str,
                   pool_id: str | None) -> None:
        _raise(self._n.add_locked(item_id, person, start, end, kind.value, line, shift_id, pool_id))

    # ------------------------------------------------------------------ booking
    def plan(self, assignment_id: str, crew: Sequence[str], line: int, shift_id: str, pool_id: str,
             segments: Sequence[tuple[int, int, bool]], kind: DutyKind = DutyKind.WORK, tools: Sequence[str] = ()) -> Booking:
        segs = [(int(a), int(b), bool(ot)) for a, b, ot in segments]
        handle, problem = self._n.plan(assignment_id, list(crew), line, shift_id, pool_id, segs, kind.value, list(tools))
        _raise(problem)
        return NativeBooking(assignment_id, kind, line, shift_id, pool_id, tuple(crew), tuple(segs), tuple(tools), handle=handle)

    def cap_problem(self, pool_id: str, crew: Sequence[str], kind: DutyKind, active: frozenset[str] | None = None) -> LedgerError | None:
        problem = self._n.cap_problem(pool_id, list(crew), kind.value)
        return None if problem is None else LedgerError(*problem)

    def person_conflict(self, person: str, shift_id: str, pool_id: str, segments: Sequence[tuple[int, int, bool]]) -> LedgerError | None:
        problem = self._n.person_conflict(person, shift_id, pool_id, [(int(a), int(b), bool(ot)) for a, b, ot in segments])
        return None if problem is None else LedgerError(*problem)

    def commit(self, booking: Booking) -> None:
        if not isinstance(booking, NativeBooking):
            raise TypeError("a native ledger commits only bookings it planned")
        self._n.commit(booking.handle)

    def book(self, assignment_id: str, crew: Sequence[str], line: int, shift_id: str, pool_id: str,
             segments: Sequence[tuple[int, int, bool]], kind: DutyKind = DutyKind.WORK, tools: Sequence[str] = ()) -> Booking:
        booking = self.plan(assignment_id, crew, line, shift_id, pool_id, segments, kind, tools)
        self.commit(booking)
        return booking

    def remove(self, assignment_id: str) -> Booking:
        rec, problem = self._n.remove(assignment_id)
        _raise(problem)
        aid, kind, line, shift, pool, crew, segments, tools = rec
        return Booking(aid, DutyKind(kind), line, shift, pool, tuple(crew), tuple(segments), tuple(tools))

    @contextlib.contextmanager
    def transaction(self) -> Iterator["NativeDutyLedger"]:
        """Any exception restores the complete previous ledger state (an undo log on the Rust side)."""
        self._n.begin()
        try:
            yield self
        except Exception:
            self._n.rollback()
            raise
        self._n.end()

    # ------------------------------------------------------------------ crew selection
    def bundle_id(self, bundle) -> int:
        bid = self._bundles.get(bundle)
        if bid is None:
            bid = self._bundles[bundle] = self._context.register_bundle([list(g) for g in bundle.groups])
        return bid

    def choose(self, *, assignment_id: str, line: int, team: str, shift_id: str, pool_id: str, count: int, bundle,
               segments: Sequence[tuple[int, int, bool]], kind: DutyKind, tools: Sequence[str], window: tuple[int, int] | None):
        """``crew_selection.choose_crew`` without an exact crew, entirely in Rust. Returns (crew, booking, tried,
        qualified, preference) on success, or (None, reason)."""
        segs = [(int(a), int(b), bool(ot)) for a, b, ot in segments]
        crew, handle, tried, qualified, preference, reason = self._n.choose_crew(
            assignment_id, line, team, shift_id, pool_id, count, self.bundle_id(bundle), window, segs, kind.value, list(tools))
        if crew is None:
            return None, reason
        booking = NativeBooking(assignment_id, kind, line, shift_id, pool_id, tuple(crew), tuple(segs), tuple(tools), handle=handle)
        return (tuple(crew), booking, tried, qualified, tuple(preference)), reason

    def search(self, *, phase_id: str, line: int, team: str, count: int, bundle, work_minutes: int, floor: int, end_floor: int,
               tools: Sequence[str], wait: tuple | None):
        """``greedy.Decoder._search`` for one phase, entirely in Rust. Returns ((start, end, segments, shift_id, pool_id, crew,
        assignment_id) or None, reason, crew attempts, skipped starts)."""
        return self._n.search(phase_id, line, team, count, self.bundle_id(bundle), work_minutes, floor, end_floor, list(tools), wait)
