"""Read ahead what a shift's first requests need, in the web process (D-33: the application opens at once at shift start).

The web reads small display parts and keeps what it has read and verified in the process. So the first request for a
view after a new plan is accepted, and the first after a process starts, pays for reading, verifying and decoding the
parts it needs: seconds at 50 aircraft. ``warm`` does that reading before anyone asks:

1. the verified snapshot manifest and each plan head's signed display receipt, which every view needs;
2. the goals of record, which the team views and the scores read;
3. for every team, what its lead's view reads for the days the shift plan covers: the team's work, its pools, its plan
   team-days, the dependency graphs of the aircraft it is planned on those days (the dispatch rule walks them), and the
   plan's commitments.

It stops when the part cache is nearly full, so it never pushes out what it has just loaded; whatever is left is read
on first use, as before. It changes nothing a request sees: the same verified loaders fill the same caches, and a part
that does not verify is not shown whether it was read ahead or not.

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
        read = 0
        for team in teams:
            if PART_CACHE.size >= limit:
                break
            mine = canon.work([team]).pools([team], days).team_days("EXECUTABLE", [team], days)
            plan = mine.plan("EXECUTABLE")
            for line in sorted({a.line for a in plan.assignments} if plan is not None and plan.loaded("assignments") else ()):
                canon.graph(line)
            read += 1
        out.update(status="ok", days=[day.isoformat() for day in days], teams=len(teams), teams_read=read)
    out.update(parts_read=PART_CACHE.loads - loads_before, cache_mb=round(PART_CACHE.size / 1e6, 1),
               cache_budget_mb=round(PART_CACHE.budget / 1e6, 1), seconds=round(time.perf_counter() - began, 2))
    return out


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
