"""Shared setup for the delay-dollar study (scratchpad only; never writes into the repository).

Builds the synthetic FleetSpec fleet, admits shift 0 through the simulation's production snapshot builder and returns a
services object and the snapshot. Everything runs in memory; the fixture store lives in a scratchpad temp dir.
"""
from __future__ import annotations

import os
import random
import resource
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = Path("/home/user/From_Nothing/FROM-NOTHING-APP")
SCRATCH = Path("/tmp/claude-0/-home-user/47aa349d-9776-5640-b3ae-942939c6de88/scratchpad/delay")
os.environ.setdefault("TMPDIR", str(SCRATCH / "tmp"))
(SCRATCH / "tmp").mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(SCRATCH / "tmp")
sys.path.insert(0, str(APP))

from focus.calendar import ShiftPolicy, build_shift  # noqa: E402
from focus.clock import FixedClock  # noqa: E402
from focus.config import Settings  # noqa: E402
from focus.data.fullscale_fixture import FLEET_FACTS, FleetSpec, build_fullscale_fixture  # noqa: E402
from focus.data.repositories import Repositories  # noqa: E402
from focus.data.store import FixtureStore  # noqa: E402
from focus.domain import digest_id, from_minute  # noqa: E402
from focus.runtime import build_services  # noqa: E402
from focus.sim.engine import admit  # noqa: E402
from focus.sim.execute import attendance_rows  # noqa: E402
from focus.sim.state import SourceState  # noqa: E402

UTC = timezone.utc


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def cur_rss_mb() -> float:
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 20
    except Exception:
        return 0.0


def build(n_aircraft: int, team_size: tuple[int, int], teams: int = 30, log=print, **fleet):
    settings = Settings.from_env({"FOCUS_NATIVE_CORE": "true"}, app_root=APP, inprocess_worker=False)
    spec = FleetSpec(n_aircraft=n_aircraft, team_size=team_size, teams=teams, **fleet)
    first_day = spec.cutoff_day
    clock = FixedClock(datetime(first_day.year, first_day.month, first_day.day, 9, 0, tzinfo=UTC))
    store_dir = Path(tempfile.mkdtemp(prefix="focus-delay-", dir=str(SCRATCH / "tmp")))
    services = build_services(settings, repositories=Repositories(FixtureStore(store_dir), mode="fixture", scope_key=settings.scope_key,
                                                                  clock=clock), clock=clock)
    shift_policy = ShiftPolicy.from_policy(services.policy)
    t = time.time()
    datasets = build_fullscale_fixture(spec)
    facts = dict(FLEET_FACTS)
    state = SourceState(datasets)
    log(f"fixture n={n_aircraft} team_size={team_size}: {time.time() - t:.1f}s scale={facts['scale']} mech/shift={facts['mechanics_per_shift']} "
        f"qa={facts['qa_per_shift']} late_parts={facts['late_parts']}")
    shift = build_shift(first_day, 1, shift_policy, settings.site)
    cutoff = from_minute(shift.start - 15)
    clock._instant = cutoff
    load_ts = (cutoff - timedelta(minutes=5)).astimezone(shift_policy.tz).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.000")
    for row in attendance_rows(state, first_day, random.Random(20261026 * 7 + 0), 0.03, load_ts):
        state.upsert("attendance_shift", row)
    state.take_changes()
    t = time.time()
    snapshot, _build = admit(services, state, cutoff=cutoff, run_id=digest_id("SR-DELAY", n_aircraft, 0, length=20), created_at=cutoff)
    del _build
    log(f"admitted {len(snapshot.jobs)} jobs, {len(snapshot.phases)} phases, {len(snapshot.edges)} edges in {time.time() - t:.1f}s; "
        f"rss peak {rss_mb():.0f} MB")
    return services, snapshot, shift, facts, spec
