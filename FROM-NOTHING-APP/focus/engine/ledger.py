"""Named per-person duty ledger: the single placement authority (S11, P11).

Every algorithm (seed, redispatch, repair, continuity, neighborhoods, commitment
defense, solver import) books people only through this ledger.

* **Duty items.** One chronological list per person holds WORK, TRAVEL, TOOL,
  WAIT and OTHER duty (actual carry-in is OTHER and locked). Non-overlap is
  checked on physical UTC minutes across shift and day labels.
* **Transfers.** Inside a shift, the transfers between consecutive located duties
  are re-derived from scratch whenever the person's duty changes, so inserting,
  removing or swapping a middle duty recomputes both neighbouring transitions.
  A transfer is a contiguous slot of open time after the previous duty that
  ends no later than the next duty starts (all crew members arrive before the
  common start). It never crosses a protected break, because protected time is
  not an open window.
* **Capacity.** Each person and shift has one budget. Duty inside the regular
  windows is counted against the regular cap; duty inside the authorized OT window
  is counted against the OT cap (at most 60). WORK + TRAVEL + TOOL + WAIT + OTHER
  all consume it, each minute once. A duty outside the admitted windows is
  rejected.
* **Activation.** For each pool (team, shift), the activated set is the union of
  people holding counted duty there. Its size never exceeds the pool cap
  ``floor(85*N/100)``. Reusing an active person adds no head.
* **Tools.** Shared tool capacity is reserved per interval when a phase
  declares a sourced tool requirement.
* **Atomicity.** A booking is planned completely (every member, both neighbours,
  activation, tools) before anything changes, and ``transaction()`` restores the
  whole ledger when a multi-step move fails.
"""
from __future__ import annotations

import bisect
import contextlib
import copy
from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator, Mapping, Sequence

from ..domain import DutyKind
from .travel import TravelPolicy


class LedgerError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PersonWindow:
    """One person's admitted physical windows in one shift (integer UTC minutes)."""

    person: str
    shift_id: str
    pool_id: str
    regular: tuple[tuple[int, int], ...]
    ot: tuple[tuple[int, int], ...]
    regular_cap: int
    ot_cap: int

    @property
    def span(self) -> tuple[int, int]:
        windows = self.regular + self.ot
        return min(a for a, _ in windows), max(b for _, b in windows)

    def open_windows(self) -> tuple[tuple[int, int], ...]:
        return tuple(sorted(self.regular + self.ot))


@dataclass(frozen=True)
class DutyItem:
    id: str
    person: str
    start: int
    end: int
    kind: DutyKind
    line: int | None                 # aircraft location; for TRAVEL the destination
    origin_line: int | None          # TRAVEL origin
    location_known: bool
    shift_id: str
    pool_id: str | None
    assignment_id: str | None
    locked: bool = False             # actual carry-in / physical history: never moved

    @property
    def minutes(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Booking:
    assignment_id: str
    kind: DutyKind
    line: int
    shift_id: str
    pool_id: str
    crew: tuple[str, ...]
    segments: tuple[tuple[int, int, bool], ...]
    tools: tuple[str, ...]
    new_shift_items: dict[str, tuple[DutyItem, ...]] = field(default_factory=dict)   # person -> full item list for shift_id


class ToolCalendar:
    """Shared tool capacity: tool id -> capacity; reservations are (start, end, assignment)."""

    def __init__(self, capacity: Mapping[str, int]):
        self.capacity = dict(capacity)
        self.reservations: dict[str, list[tuple[int, int, str]]] = {t: [] for t in self.capacity}

    def check(self, tool: str, start: int, end: int) -> str | None:
        if tool not in self.capacity:
            return f"TOOL_UNKNOWN: {tool} has no sourced capacity"
        busy = sum(1 for s, e, _ in self.reservations[tool] if s < end and start < e)
        return None if busy < self.capacity[tool] else f"TOOL_UNAVAILABLE: {tool} checkout busy over [{start},{end})"

    def reserve(self, tool: str, start: int, end: int, assignment_id: str) -> None:
        self.reservations[tool].append((start, end, assignment_id))

    def release(self, assignment_id: str) -> None:
        for tool in self.reservations:
            self.reservations[tool] = [r for r in self.reservations[tool] if r[2] != assignment_id]


def _overlap(a: int, b: int, windows: Iterable[tuple[int, int]]) -> int:
    return sum(max(0, min(b, we) - max(a, ws)) for ws, we in windows)


def _inside(a: int, b: int, windows: Sequence[tuple[int, int]]) -> bool:
    return _overlap(a, b, windows) == b - a


class DutyLedger:
    def __init__(self, windows: Mapping[tuple[str, str], PersonWindow], travel: TravelPolicy, pool_caps: Mapping[str, int],
                 counted_kinds: Iterable[DutyKind], tools: ToolCalendar | None = None):
        self.windows = dict(windows)
        self.travel = travel
        self.pool_caps = dict(pool_caps)
        self.counted = frozenset(counted_kinds)
        self.tools = tools or ToolCalendar({})
        self._shift: dict[tuple[str, str], tuple[DutyItem, ...]] = {}     # (person, shift) -> sorted items
        self._all: dict[str, list[tuple[int, int, str, str]]] = {}         # person -> sorted (start, end, item id, shift)
        self._bookings: dict[str, Booking] = {}
        self._active: dict[str, dict[str, int]] = {}                        # pool -> person -> counted items
        self.problems: list[str] = []                                       # facts recorded about locked history

    # ------------------------------------------------------------------ queries
    def shift_items(self, person: str, shift_id: str) -> tuple[DutyItem, ...]:
        return self._shift.get((person, shift_id), ())

    def items(self, person: str) -> list[DutyItem]:
        out: list[DutyItem] = []
        for (p, _), items in self._shift.items():
            if p == person:
                out.extend(items)
        return sorted(out, key=lambda i: (i.start, i.end, i.id))

    def all_items(self) -> list[DutyItem]:
        return sorted((i for items in self._shift.values() for i in items), key=lambda i: (i.person, i.start, i.end, i.id))

    def bookings(self) -> dict[str, Booking]:
        return dict(self._bookings)

    def active(self, pool_id: str) -> frozenset[str]:
        return frozenset(p for p, n in self._active.get(pool_id, {}).items() if n > 0)

    def duty_minutes(self, person: str, shift_id: str) -> tuple[int, int]:
        """(regular, overtime) minutes booked for the person in the shift."""
        window = self.windows.get((person, shift_id))
        if window is None:
            return 0, 0
        return self._budget(window, self.shift_items(person, shift_id))

    def last_located(self, person: str, shift_id: str, before: int) -> DutyItem | None:
        located = [i for i in self.shift_items(person, shift_id) if i.kind is not DutyKind.TRAVEL and i.end <= before]
        return located[-1] if located else None

    def free_points(self, person: str, shift_id: str) -> list[int]:
        """Instants when the person's located duty in the shift ends (candidate start points)."""
        return sorted({i.end for i in self.shift_items(person, shift_id) if i.kind is not DutyKind.TRAVEL})

    # ------------------------------------------------------------------ carry-in
    def add_locked(self, item_id: str, person: str, start: int, end: int, kind: DutyKind, line: int | None, shift_id: str,
                   pool_id: str | None) -> None:
        """Actual carry-in / other duty: physical, locked, never moved by any algorithm."""
        item = DutyItem(item_id, person, start, end, kind, line, None, line is not None, shift_id, pool_id, None, locked=True)
        self._check_cross_shift(person, shift_id, [item])
        items = tuple(sorted(self.shift_items(person, shift_id) + (item,), key=lambda i: (i.start, i.end, i.id)))
        window = self.windows.get((person, shift_id))
        if window is not None:
            try:
                items = self._derive(window, items)
                self._check_budget(window, items)
            except LedgerError as exc:
                # physical history is never rejected; the overrun is a recorded fact that blocks further booking
                self.problems.append(f"{item_id}: {exc.code}: {exc.detail}")
        self._replace(person, shift_id, items)
        if pool_id is not None and kind in self.counted:
            counts = self._active.setdefault(pool_id, {})
            counts[person] = counts.get(person, 0) + 1

    # ------------------------------------------------------------------ booking
    def plan(self, assignment_id: str, crew: Sequence[str], line: int, shift_id: str, pool_id: str,
             segments: Sequence[tuple[int, int, bool]], kind: DutyKind = DutyKind.WORK, tools: Sequence[str] = ()) -> Booking:
        """Plan a full-crew booking without changing anything; raise LedgerError with a stable code on failure."""
        if assignment_id in self._bookings:
            raise LedgerError("ASSIGNMENT_EXISTS", assignment_id)
        if not crew:
            raise LedgerError("CREW_EMPTY", "a booking needs at least one person")
        if len(set(crew)) != len(crew):
            raise LedgerError("CREW_DUPLICATE_PERSON", f"{list(crew)}")
        segs = tuple((int(a), int(b), bool(ot)) for a, b, ot in segments)
        if not segs or any(b <= a for a, b, _ in segs):
            raise LedgerError("SEGMENTS_INVALID", f"{segs}")
        start, end = segs[0][0], segs[-1][1]
        for tool in tools:
            problem = self.tools.check(tool, start, end)
            if problem:
                raise LedgerError(problem.split(":")[0], problem)
        new_items: dict[str, tuple[DutyItem, ...]] = {}
        for person in crew:
            window = self.windows.get((person, shift_id))
            if window is None:
                raise LedgerError("PERSON_NOT_AVAILABLE", f"{person} has no admitted window in {shift_id}")
            if window.pool_id != pool_id:
                raise LedgerError("POOL_MISMATCH", f"{person} belongs to {window.pool_id}, not {pool_id}")
            work = [DutyItem(f"{assignment_id}#{person}#{n}", person, a, b, kind, line, None, True, shift_id, pool_id, assignment_id)
                    for n, (a, b, _) in enumerate(segs)]
            self._check_cross_shift(person, shift_id, work)
            current = [i for i in self.shift_items(person, shift_id) if i.kind is not DutyKind.TRAVEL]
            for w in work:
                for i in current:
                    if i.start < w.end and w.start < i.end:
                        raise LedgerError("PERSON_OVERLAP", f"{person} already has {i.kind.value} {i.id} over [{i.start},{i.end})")
            items = tuple(sorted(current + work, key=lambda i: (i.start, i.end, i.id)))
            items = self._derive(window, items)
            self._check_windows(window, items)
            self._check_budget(window, items)
            new_items[person] = items
        active = self.active(pool_id)
        heads = active | set(crew)
        cap = self.pool_caps.get(pool_id)
        if cap is None:
            raise LedgerError("POOL_UNKNOWN", pool_id)
        if kind in self.counted and len(heads) > cap:
            raise LedgerError("ACTIVATION_CAP", f"pool {pool_id} would activate {len(heads)} people > cap {cap}")
        return Booking(assignment_id, kind, line, shift_id, pool_id, tuple(crew), segs, tuple(tools), new_items)

    def commit(self, booking: Booking) -> None:
        for person, items in booking.new_shift_items.items():
            self._replace(person, booking.shift_id, items)
        if booking.kind in self.counted:
            counts = self._active.setdefault(booking.pool_id, {})
            for person in booking.crew:
                counts[person] = counts.get(person, 0) + 1
        start, end = booking.segments[0][0], booking.segments[-1][1]
        for tool in booking.tools:
            self.tools.reserve(tool, start, end, booking.assignment_id)
        self._bookings[booking.assignment_id] = replace(booking, new_shift_items={})

    def book(self, assignment_id: str, crew: Sequence[str], line: int, shift_id: str, pool_id: str,
             segments: Sequence[tuple[int, int, bool]], kind: DutyKind = DutyKind.WORK, tools: Sequence[str] = ()) -> Booking:
        booking = self.plan(assignment_id, crew, line, shift_id, pool_id, segments, kind, tools)
        self.commit(booking)
        return booking

    def remove(self, assignment_id: str) -> Booking:
        """Remove a booking and re-derive both neighbouring transitions for every member."""
        booking = self._bookings.pop(assignment_id, None)
        if booking is None:
            raise LedgerError("ASSIGNMENT_UNKNOWN", assignment_id)
        for person in booking.crew:
            window = self.windows[(person, booking.shift_id)]
            items = tuple(i for i in self.shift_items(person, booking.shift_id)
                          if i.kind is not DutyKind.TRAVEL and i.assignment_id != assignment_id)
            items = self._derive(window, items)
            self._check_budget(window, items)
            self._replace(person, booking.shift_id, items)
        if booking.kind in self.counted:
            counts = self._active.get(booking.pool_id, {})
            for person in booking.crew:
                counts[person] = counts.get(person, 0) - 1
        self.tools.release(assignment_id)
        return booking

    @contextlib.contextmanager
    def transaction(self) -> Iterator["DutyLedger"]:
        """Run a multi-step move; any exception restores the complete previous ledger state."""
        saved = (dict(self._shift), copy.deepcopy(self._all), dict(self._bookings), copy.deepcopy(self._active),
                 copy.deepcopy(self.tools.reservations))
        try:
            yield self
        except Exception:
            self._shift, self._all, self._bookings, self._active, self.tools.reservations = saved
            raise

    # ------------------------------------------------------------------ internals
    def _replace(self, person: str, shift_id: str, items: tuple[DutyItem, ...]) -> None:
        if items:
            self._shift[(person, shift_id)] = items
        else:
            self._shift.pop((person, shift_id), None)
        rows = [r for r in self._all.get(person, []) if r[3] != shift_id]
        rows.extend((i.start, i.end, i.id, shift_id) for i in items)
        rows.sort()
        self._all[person] = rows

    def _check_cross_shift(self, person: str, shift_id: str, new: Sequence[DutyItem]) -> None:
        # a person's committed items never overlap, so sorted starts imply sorted ends
        rows = self._all.get(person, [])
        ends = [r[1] for r in rows]
        for item in new:
            idx = bisect.bisect_right(ends, item.start)
            while idx < len(rows) and rows[idx][0] < item.end:
                r = rows[idx]
                if r[3] != shift_id:
                    raise LedgerError("PERSON_OVERLAP", f"{person} is busy over [{r[0]},{r[1]}) in {r[3]} (labels never hide overlap)")
                idx += 1

    def _derive(self, window: PersonWindow, items: tuple[DutyItem, ...]) -> tuple[DutyItem, ...]:
        """Re-derive every transfer of the person in this shift from the located duty sequence."""
        located = [i for i in items if i.kind is not DutyKind.TRAVEL]
        located.sort(key=lambda i: (i.start, i.end, i.id))
        out: list[DutyItem] = []
        open_windows = window.open_windows()
        prev: DutyItem | None = None
        for item in located:
            if prev is not None and not item.locked and item.line is not None:
                minutes = self.travel.minutes(prev.line, prev.location_known, item.line)
                if minutes is None:
                    raise LedgerError("TRAVEL_ORIGIN_UNKNOWN", f"{window.person}: no bounded policy for travel after {prev.id}")
                if minutes > 0:
                    slot = None
                    for ws, we in open_windows:
                        s = max(prev.end, ws)
                        if s + minutes <= min(we, item.start):
                            slot = (s, s + minutes)
                            break
                    if slot is None:
                        raise LedgerError("TRAVEL_INFEASIBLE", f"{window.person} cannot reach line {item.line} by {item.start} "
                                                               f"({minutes} min transfer after {prev.end} through open windows)")
                    out.append(DutyItem(f"T:{item.id}", window.person, slot[0], slot[1], DutyKind.TRAVEL, item.line, prev.line,
                                        True, window.shift_id, item.pool_id, item.assignment_id))
            out.append(item)
            prev = item
        out.sort(key=lambda i: (i.start, i.end, i.id))
        for a, b in zip(out, out[1:]):
            if b.start < a.end:
                raise LedgerError("PERSON_OVERLAP", f"{window.person}: {a.id} overlaps {b.id}")
        return tuple(out)

    @staticmethod
    def _check_windows(window: PersonWindow, items: tuple[DutyItem, ...]) -> None:
        open_windows = window.open_windows()
        for i in items:
            if not i.locked and not _inside(i.start, i.end, open_windows):
                raise LedgerError("OUTSIDE_ADMITTED_WINDOW", f"{window.person} {i.kind.value} [{i.start},{i.end}) is outside open/authorized windows")

    @staticmethod
    def _budget(window: PersonWindow, items: Iterable[DutyItem]) -> tuple[int, int]:
        regular = ot = 0
        span_start, span_end = window.span
        for i in items:
            in_ot = _overlap(i.start, i.end, window.ot)
            if i.locked:
                regular += max(0, min(i.end, span_end) - max(i.start, span_start)) - in_ot
            else:
                regular += _overlap(i.start, i.end, window.regular)
            ot += in_ot
        return regular, ot

    def _check_budget(self, window: PersonWindow, items: tuple[DutyItem, ...]) -> None:
        regular, ot = self._budget(window, items)
        if regular > window.regular_cap:
            raise LedgerError("REGULAR_CAP", f"{window.person} regular duty {regular} > {window.regular_cap} in {window.shift_id}")
        if ot > window.ot_cap:
            raise LedgerError("OT_CAP", f"{window.person} overtime {ot} > authorized {window.ot_cap} in {window.shift_id}")
