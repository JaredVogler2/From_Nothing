"""One calendar/clock authority: shifts, protected windows, no-start cutoff, OT, line calendars (S09, P08).

All engine instants are integer UTC minutes since the Unix epoch. Every shift
boundary is built independently from its local wall-clock time in the policy
timezone and then converted to UTC, so a DST change alters real elapsed time.
There is never a packed 1440/1470-minute day.

Coordinates: ``coord(shift, t)`` is the number of open *regular* minutes in
``[shift.start, t)``. The no-start cutoff is coordinate ``regular_cap - 30``.
A start is valid when it lies in an open regular window and
``coord(start) <= cutoff``. Equality is allowed, and finish fit is checked
separately. Overtime never moves the cutoff.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from .contracts.planning import CalendarDay, LineCalendar
from .domain import DecisionStatus, Interval, RuleDecision, from_minute, minute_floor, to_minute
from .data.sourcetime import local_to_utc
from .policy import Policy

UTC = timezone.utc
WORKING = "WORKING"
SHIFT_NUMBERS = (1, 2, 3)


@dataclass(frozen=True, eq=False)
class ShiftPolicy:
    tz_name: str
    start_local: Mapping[int, time]
    wall_minutes: Mapping[int, int]
    regular_minutes: Mapping[int, int]
    protected: Mapping[int, tuple[tuple[str, int, int], ...]]
    max_ot_minutes: int
    ot_minutes: int
    no_start_buffer: int
    s3_requires_next_working: bool
    working_statuses: frozenset[str]
    nonworking_statuses: frozenset[str]
    policy_hash: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    def _key(self) -> tuple:
        return (
            self.tz_name,
            tuple(sorted(self.start_local.items())),
            tuple(sorted(self.wall_minutes.items())),
            tuple(sorted(self.regular_minutes.items())),
            tuple(sorted(self.protected.items())),
            self.max_ot_minutes, self.ot_minutes, self.no_start_buffer, self.s3_requires_next_working,
            tuple(sorted(self.working_statuses)), tuple(sorted(self.nonworking_statuses)),
        )

    def __hash__(self) -> int:
        return hash(self._key())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ShiftPolicy) and self._key() == other._key()

    @classmethod
    def from_policy(cls, policy: Policy) -> "ShiftPolicy":
        starts = {int(k): time.fromisoformat(v) for k, v in policy.map("shift.start_local").items()}
        prot = {
            int(k): tuple((w["kind"], int(w["offset"]), int(w["minutes"])) for w in v)
            for k, v in policy.map("shift.protected_windows").items()
        }
        ot = policy.map("shift.ot_window")
        if ot["anchor"] != "WALL_END":
            raise ValueError("only WALL_END overtime windows are supported by this calendar authority")
        return cls(
            tz_name=policy.str("shift.timezone"),
            start_local=starts,
            wall_minutes={int(k): int(v) for k, v in policy.map("shift.wall_minutes").items()},
            regular_minutes={int(k): int(v) for k, v in policy.map("shift.regular_minutes").items()},
            protected=prot,
            max_ot_minutes=policy.int("shift.maximum_ot_minutes"),
            ot_minutes=min(int(ot["minutes"]), policy.int("shift.maximum_ot_minutes")),
            no_start_buffer=policy.int("shift.no_start_buffer_minutes"),
            s3_requires_next_working=bool(policy.optional("shift.s3_nonworking_requires_next_working", True)),
            working_statuses=frozenset(policy.seq("calendar.working_statuses")),
            nonworking_statuses=frozenset(policy.seq("calendar.nonworking_statuses")),
            policy_hash=policy.hash,
        )


@dataclass(frozen=True)
class ShiftInstance:
    id: str
    site: str
    start_date: date
    number: int
    start: int
    end: int
    regular_windows: tuple[tuple[int, int], ...]
    protected: tuple[tuple[int, int, str], ...]
    ot_window: tuple[int, int] | None
    nominal_regular: int
    open_minutes: int
    regular_cap: int
    cutoff_coord: int
    cutoff_instant: int
    dst_adjusted: bool

    @property
    def label(self) -> str:
        return f"{self.start_date.isoformat()} S{self.number}"

    def to_interval(self) -> Interval:
        return Interval.of_minutes(self.start, self.end)

    # ---------------------------------------------------------------- coordinates
    def coord(self, t: int) -> int:
        """Open regular minutes in [start, t)."""
        total = 0
        for ws, we in self.regular_windows:
            if t <= ws:
                break
            total += min(t, we) - ws
        return total

    def in_regular_window(self, t: int) -> bool:
        return any(ws <= t < we for ws, we in self.regular_windows)

    def next_open(self, t: int) -> int | None:
        """Earliest instant >= t inside an open regular window (None when past the last window)."""
        for ws, we in self.regular_windows:
            if t < we:
                return max(t, ws)
        return None

    def valid_start(self, t: int) -> bool:
        return self.in_regular_window(t) and self.coord(t) <= self.cutoff_coord

    def latest_start(self) -> int:
        return self.cutoff_instant

    def advance(self, start: int, minutes: int, ot_allowed: int = 0) -> tuple[int, tuple[tuple[int, int, bool], ...]] | None:
        """Place ``minutes`` of active duty from ``start`` through open regular windows, then the
        authorized OT window (at most ``ot_allowed`` minutes). Returns (end, segments) where each
        segment is (seg_start, seg_end, is_ot), or None when the shift cannot hold the work.
        The per-person regular cap (DST policy) and OT budget are enforced by the duty ledger,
        which sums every duty of the person in the shift."""
        if minutes <= 0:
            return None
        remaining = minutes
        segments: list[tuple[int, int, bool]] = []
        t = start
        for ws, we in self.regular_windows:
            if remaining == 0:
                break
            if we <= t:
                continue
            s = max(t, ws)
            take = min(we - s, remaining)
            if take > 0:
                segments.append((s, s + take, False))
                remaining -= take
                t = s + take
        if remaining > 0:
            if self.ot_window is None or ot_allowed <= 0 or remaining > ot_allowed:
                return None
            os_, oe = self.ot_window
            s = max(t, os_)
            if s + remaining > oe:
                return None
            segments.append((s, s + remaining, True))
            remaining = 0
        if not segments:
            return None
        merged: list[tuple[int, int, bool]] = []
        for seg in segments:
            if merged and merged[-1][1] == seg[0] and merged[-1][2] == seg[2]:
                merged[-1] = (merged[-1][0], seg[1], seg[2])
            else:
                merged.append(seg)
        return merged[-1][1], tuple(merged)

    def contiguous_free(self, t: int, minutes: int, include_ot: bool) -> int | None:
        """Earliest s >= t such that [s, s+minutes) lies inside one open window (regular or OT)."""
        windows = list(self.regular_windows)
        if include_ot and self.ot_window:
            windows.append(self.ot_window)
        for ws, we in windows:
            s = max(t, ws)
            if s + minutes <= we:
                return s
        return None


def _local(day: date, hhmm: time) -> datetime:
    return datetime.combine(day, hhmm)


@lru_cache(maxsize=8192)
def _build_shift_cached(day: date, number: int, policy: ShiftPolicy, site: str) -> ShiftInstance:
    tz = policy.tz
    start_local = _local(day, policy.start_local[number])
    end_local = start_local + timedelta(minutes=policy.wall_minutes[number])
    start = to_minute(local_to_utc(start_local, tz))
    end = to_minute(local_to_utc(end_local, tz))
    prot: list[tuple[int, int, str]] = []
    for kind, offset, minutes in policy.protected.get(number, ()):
        ps_local = start_local + timedelta(minutes=offset)
        pe_local = ps_local + timedelta(minutes=minutes)
        ps = max(start, to_minute(local_to_utc(ps_local, tz)))
        pe = min(end, to_minute(local_to_utc(pe_local, tz)))
        if pe > ps:
            prot.append((ps, pe, kind))
    prot.sort()
    windows: list[tuple[int, int]] = []
    cursor = start
    for ps, pe, _ in prot:
        if ps > cursor:
            windows.append((cursor, ps))
        cursor = max(cursor, pe)
    if cursor < end:
        windows.append((cursor, end))
    open_minutes = sum(we - ws for ws, we in windows)
    nominal = policy.regular_minutes[number]
    regular_cap = min(nominal, open_minutes)
    cutoff_coord = max(0, regular_cap - policy.no_start_buffer)
    # earliest instant whose coordinate equals cutoff_coord
    acc = 0
    cutoff_instant = start
    for ws, we in windows:
        length = we - ws
        if acc + length >= cutoff_coord:
            cutoff_instant = ws + (cutoff_coord - acc)
            if cutoff_instant == we:
                # coordinate reached at a window end: next valid start is the next window start
                idx = windows.index((ws, we))
                cutoff_instant = windows[idx + 1][0] if idx + 1 < len(windows) else we
            break
        acc += length
    ot_window = (end, end + policy.ot_minutes) if policy.ot_minutes > 0 else None
    shift_id = f"{site}:{day.isoformat()}:S{number}"
    return ShiftInstance(
        id=shift_id,
        site=site,
        start_date=day,
        number=number,
        start=start,
        end=end,
        regular_windows=tuple(windows),
        protected=tuple(prot),
        ot_window=ot_window,
        nominal_regular=nominal,
        open_minutes=open_minutes,
        regular_cap=regular_cap,
        cutoff_coord=cutoff_coord,
        cutoff_instant=cutoff_instant,
        dst_adjusted=(end - start) != policy.wall_minutes[number] or regular_cap != nominal,
    )


def build_shift(day: date, number: int, policy: ShiftPolicy, site: str = "DEMO") -> ShiftInstance:
    if number not in SHIFT_NUMBERS:
        raise ValueError(f"shift number must be 1..3, got {number}")
    return _build_shift_cached(day, number, policy, site)


def parse_shift_id(shift_id: str) -> tuple[str, date, int]:
    site, day, label = shift_id.rsplit(":", 2)
    if not label.startswith("S"):
        raise ValueError(f"bad shift id {shift_id!r}")
    return site, date.fromisoformat(day), int(label[1:])


def shifts_between(first: date, last: date, policy: ShiftPolicy, site: str) -> list[ShiftInstance]:
    out: list[ShiftInstance] = []
    d = first
    while d <= last:
        for n in SHIFT_NUMBERS:
            out.append(build_shift(d, n, policy, site))
        d += timedelta(days=1)
    out.sort(key=lambda s: (s.start, s.number))
    return out


def local_date_of(minute: int, policy: ShiftPolicy) -> date:
    return from_minute(minute).astimezone(policy.tz).date()


def shift_containing(minute: int, policy: ShiftPolicy, site: str) -> ShiftInstance | None:
    day = local_date_of(minute, policy)
    for d in (day - timedelta(days=1), day):
        for n in SHIFT_NUMBERS:
            s = build_shift(d, n, policy, site)
            if s.start <= minute < s.end:
                return s
    return None


# ---------------------------------------------------------------------------
# bounded per-line calendars (FOCUS_CDAY_OP_PLAN)
# ---------------------------------------------------------------------------

def admit_line_calendars(rows: Iterable[Mapping[str, object]], policy: Policy, lines: Iterable[int]) -> dict[int, LineCalendar]:
    """Select one producer-approved active generation per line and dense admitted bounds.

    Missing lines, conflicting generations, conflicting duplicate days and holes are
    explicit; there is no weekday fallback and no automatic extension.
    """
    selector = policy.str("calendar.generation_selector")
    working = set(policy.seq("calendar.working_statuses"))
    nonworking = set(policy.seq("calendar.nonworking_statuses"))
    by_line: dict[int, list[Mapping[str, object]]] = {}
    for row in rows:
        if not row.get("IS_ACTIVE"):
            continue
        by_line.setdefault(int(row["LINE_NUMBER"]), []).append(row)
    out: dict[int, LineCalendar] = {}
    for line in sorted(set(lines)):
        line_rows = by_line.get(line, [])
        if not line_rows:
            out[line] = LineCalendar(line, "MISSING", None, None, None, (), (), ("CALENDAR_MISSING: no active FOCUS_CDAY_OP_PLAN rows for this line",), "missing")
            continue
        generations = sorted({int(r["LOAD_ID"]) for r in line_rows})
        reasons: list[str] = []
        if len(generations) > 1:
            if selector == "MAX_LOAD_ID":
                generation = generations[-1]
                reasons.append(f"GENERATION_SELECTED_BY_POLICY: MAX_LOAD_ID chose {generation} of {generations}")
            else:
                out[line] = LineCalendar(line, "CONFLICT", None, None, None, (), (),
                                         (f"CALENDAR_GENERATION_CONFLICT: active loads {generations} without an approved selector",), "conflict")
                continue
        else:
            generation = generations[0]
        days: dict[date, CalendarDay] = {}
        conflicts: set[date] = set()
        for r in line_rows:
            if int(r["LOAD_ID"]) != generation:
                continue
            d = date.fromisoformat(str(r["CALENDAR_DATE"]))
            status = str(r.get("DAY_STATUS") or "")
            if status not in working and status not in nonworking:
                status = "UNKNOWN"
            day = CalendarDay(d, status, (str(r["POSITION"]) if r.get("POSITION") is not None else None), generation,
                              date.fromisoformat(str(r["LAST_MODIFIED_DT"])) if r.get("LAST_MODIFIED_DT") else None)
            if d in days and days[d].status != status:
                conflicts.add(d)
            days[d] = day
        for d in conflicts:
            days[d] = CalendarDay(d, "UNKNOWN", days[d].position, generation, days[d].last_modified)
            reasons.append(f"CALENDAR_DAY_CONFLICT: {d.isoformat()} has conflicting statuses")
        lo, hi = min(days), max(days)
        holes = []
        d = lo
        while d <= hi:
            if d not in days:
                holes.append(d)
            d += timedelta(days=1)
        if holes:
            reasons.append(f"CALENDAR_HOLES: {len(holes)} missing date(s) inside admitted bounds")
        ordered = tuple(days[k] for k in sorted(days))
        from .codec import content_hash

        revision = content_hash([(c.day, c.status, c.load_id) for c in ordered])[:16]
        out[line] = LineCalendar(line, "ADMITTED", generation, lo, hi, ordered, tuple(holes), tuple(reasons), revision)
    return out


class CalendarService:
    """Shared calendar/window API for engine, repair, validator, serializer and UI."""

    def __init__(self, calendars: Mapping[int, LineCalendar], shift_policy: ShiftPolicy, site: str):
        self.calendars = dict(calendars)
        self.policy = shift_policy
        self.site = site
        self._status: dict[int, dict[date, str]] = {
            line: {d.day: d.status for d in cal.days} for line, cal in self.calendars.items()
        }

    def day_status(self, line: int, day: date) -> str | None:
        cal = self.calendars.get(line)
        if cal is None or cal.status != "ADMITTED":
            return None
        if cal.min_date is None or day < cal.min_date or day > (cal.max_date or day):
            return None
        status = self._status[line].get(day)
        if status is None or status == "UNKNOWN":
            return None
        return status

    def eligible(self, line: int, shift: ShiftInstance) -> RuleDecision:
        cal = self.calendars.get(line)
        if cal is None or cal.status != "ADMITTED":
            code = "CALENDAR_MISSING" if cal is None or cal.status == "MISSING" else "CALENDAR_CONFLICT"
            return RuleDecision.unknown(code, f"line {line} has no admitted calendar generation")
        d = shift.start_date
        status = self.day_status(line, d)
        if status is None:
            return RuleDecision.unknown("CALENDAR_DATE_UNKNOWN", f"line {line} {d.isoformat()} is a hole, outside bounds or unknown")
        if shift.number in (1, 2):
            if status == WORKING:
                return RuleDecision.allow("WORKDAY", f"{d.isoformat()} WORKING")
            return RuleDecision.deny("NONWORKING_DAY", f"{d.isoformat()} is {status}")
        if status == WORKING:
            return RuleDecision.allow("WORKDAY", f"S3 on WORKING {d.isoformat()}")
        nxt = self.day_status(line, d + timedelta(days=1))
        if nxt is None:
            return RuleDecision.unknown("S3_NEXT_DAY_UNKNOWN", f"S3 on {status} {d.isoformat()} needs next-day evidence")
        if nxt == WORKING:
            return RuleDecision.allow("S3_EXCEPTION", f"S3 on {status} {d.isoformat()} allowed: {(d + timedelta(days=1)).isoformat()} WORKING")
        return RuleDecision.deny("S3_NONWORKING", f"S3 on {status} {d.isoformat()} denied: next day {nxt}")

    def bounds(self, line: int) -> tuple[date, date] | None:
        cal = self.calendars.get(line)
        if cal is None or cal.status != "ADMITTED" or cal.min_date is None or cal.max_date is None:
            return None
        return cal.min_date, cal.max_date

    def eligible_shifts(self, line: int, start_minute: int, end_minute: int | None = None) -> list[ShiftInstance]:
        b = self.bounds(line)
        if b is None:
            return []
        lo, hi = b
        out = []
        for s in shifts_between(lo, hi, self.policy, self.site):
            if s.end <= start_minute:
                continue
            if end_minute is not None and s.start >= end_minute:
                break
            if self.eligible(line, s).allowed:
                out.append(s)
        return out


def horizon_dates(calendars: Mapping[int, LineCalendar]) -> tuple[date, date] | None:
    admitted = [c for c in calendars.values() if c.status == "ADMITTED" and c.min_date and c.max_date]
    if not admitted:
        return None
    return min(c.min_date for c in admitted), max(c.max_date for c in admitted)  # type: ignore[type-var]
