"""Read ahead what a shift's first requests need, in the web process (D-33: the application opens at once at shift start).

The web reads small display parts and keeps what it has read and verified in the process. So the first request for a
view after a new plan is accepted, and the first after a process starts, pays for reading, verifying and decoding the
parts it needs: seconds at 50 aircraft. ``warm`` does that reading before anyone asks:

1. the verified snapshot manifest and each plan head's signed display receipt, which every view needs;
2. the goals of record, which the team views and the scores read;
3. the plan's commitments, and every team's plan team-days and pools for the days the shift plan covers;
4. what each team lead's first paint reads (``serializers.team_lead`` with ``proposals=False``): the team's work on
   the aircraft it is planned on in a shift, and those aircraft's dependency graphs (the dispatch rule walks them).
   The shift about to start is read for every team first, then the next shift, and so on.

A team's work on every aircraft is not read ahead: only the addition proposals need it, the page asks for those after
the task list is shown, and at 50 aircraft it is 60 to 90 MB a team against about 7 MB for a shift.

It stops when the part cache is nearly full, so it never pushes out what it has just loaded; whatever is left is read
on first use, as before. ``FOCUS_DISPLAY_CACHE_MB`` sizes that cache (``docs/execution/DEPLOYMENT.md``). It changes
nothing a request sees: the same verified loaders fill the same caches, and a part that does not verify is not shown
whether it was read ahead or not.

``start`` runs it on a background thread when the process starts and again whenever a head moves (a newly admitted run
or a newly accepted plan). It is off unless ``FOCUS_WARM_SECONDS`` gives the seconds between checks.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

LOG = logging.getLogger("focus.web.warm")
SHARE = 0.85                                    # of the part cache: read-ahead stops there and leaves room for requests


def heads(services: Any) -> tuple[Any, ...]:
    """What a process is showing: the source run and the two plans its heads name (None where there is none)."""
    from ..display.view import DisplayCanon
    from .serializers import CACHE

    canon = DisplayCanon.load(services, CACHE)
    return (canon.source_run_id, *(None if vp is None else vp.published.id for _, vp in sorted(canon.verified.items())))


def warm(services: Any, *, share: float = SHARE) -> dict[str, Any]:
    """Fill this process's caches for the plans now current; returns what it read."""
    from ..display.store import PART_CACHE
    from ..display.view import DisplayCanon
    from .serializers import CACHE, registry

    began = time.perf_counter()
    loads_before = PART_CACHE.loads
    canon = DisplayCanon.load(services, CACHE)
    shift_plan = canon.verified.get("EXECUTABLE")
    out: dict[str, Any] = {"source_run_id": canon.source_run_id,
                           "plans": {purpose: None if vp is None else vp.published.id for purpose, vp in sorted(canon.verified.items())},
                           "teams": 0, "teams_read": 0}
    if canon.summary is None or shift_plan is None:
        out.update(status="nothing to read ahead", reasons=sorted({str(r.get("code")) for r in canon.reasons}))
    else:
        registry(services)
        days = sorted(shift_plan.summary.obligation_dates)
        wanted = {day.isoformat() for day in days}
        planned = {key.rsplit(":", 1)[0] for key in shift_plan.summary.team_days if key.rsplit(":", 1)[1] in wanted}
        pooled = {pool.team for pool in canon.summary.pools if pool.start_date in set(days)}
        teams = sorted(planned) + sorted(pooled - planned)          # the teams with work in the plan first
        canon.commitments("EXECUTABLE")
        limit = share * PART_CACHE.budget
        slices: dict[str, tuple[Any, dict[str, set[int]]]] = {}       # team -> its canon, and its aircraft per shift
        for team in teams:
            mine = canon.team_days("EXECUTABLE", [team], days).pools([team], days)
            plan = mine.plan("EXECUTABLE")
            lines: dict[str, set[int]] = {}
            for a in (plan.assignments if plan is not None and plan.loaded("assignments") else ()):
                lines.setdefault(a.shift_id, set()).add(a.line)
            slices[team] = (mine, lines)
        shifts = sorted({shift_id for _, lines in slices.values() for shift_id in lines}, key=_shift_order)
        read: dict[str, int] = {}
        for shift_id in shifts:                                    # the shift about to start for every team, then the next
            read[shift_id] = 0
            for team in teams:
                if PART_CACHE.size >= limit:
                    break
                mine, lines = slices[team]
                wanted_lines = sorted(lines.get(shift_id, ()))
                mine.work([team], wanted_lines)
                for line in wanted_lines:
                    canon.graph(line)
                read[shift_id] += 1
            if read[shift_id] < len(teams):
                break
        first = shifts[0] if shifts else None
        out.update(status="ok", days=[day.isoformat() for day in days], teams=len(teams),
                   teams_read=len(teams) if first is None else read[first], shifts=shifts,
                   shifts_read=[shift_id for shift_id in shifts if read.get(shift_id) == len(teams)])
    out.update(parts_read=PART_CACHE.loads - loads_before, cache_mb=round(PART_CACHE.size / 1e6, 1),
               cache_budget_mb=round(PART_CACHE.budget / 1e6, 1), seconds=round(time.perf_counter() - began, 2))
    return out


def _shift_order(shift_id: str) -> tuple[str, int]:
    """A shift id's place in time: its production day, then its number (``SITE:YYYY-MM-DD:S2``)."""
    _, day, label = shift_id.rsplit(":", 2)
    return day, int(label[1:])


class Warmer:
    """Reads ahead once, then again each time a head has moved since the last read."""

    def __init__(self, services: Any):
        self.services = services
        self.seen: tuple[Any, ...] | None = None
        self.last: dict[str, Any] | None = None

    def check(self) -> dict[str, Any] | None:
        """One check: read ahead when the heads differ from the last ones read for; None when nothing moved."""
        now = heads(self.services)
        if now == self.seen:
            return None
        self.last = warm(self.services)
        self.seen = now
        return self.last


def interval(env: Any = None) -> float:
    """Seconds between checks from ``FOCUS_WARM_SECONDS``; 0 (the default, and anything unreadable) turns it off."""
    raw = (os.environ if env is None else env).get("FOCUS_WARM_SECONDS", "")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return seconds if seconds > 0 else 0.0


def start(services: Any, seconds: float | None = None) -> threading.Thread | None:
    """Start the background reader of this process, or return None when it is off. A failed check is logged and tried
    again at the next one: reading ahead must never take the web down."""
    seconds = interval() if seconds is None else seconds
    if seconds <= 0:
        return None
    warmer = Warmer(services)

    def loop() -> None:
        while True:
            try:
                result = warmer.check()
                if result is not None:
                    LOG.info("read ahead: %s", result)
            except Exception:                                # noqa: BLE001 - never fatal to the web process
                LOG.exception("read-ahead check failed")
            time.sleep(seconds)

    thread = threading.Thread(target=loop, name="focus-warm", daemon=True)
    thread.start()
    return thread
