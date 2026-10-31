"""Run a shift-by-shift fleet simulation into a SQLite database.

python -m focus.sim.runner --aircraft 50 --days 14 --db runtime/sim/fleet.db [--purposes EXECUTABLE,FORECAST] [--max-moves 6]

Per shift (three a day): attendance for the day, admission of the sources at the cutoff (15 minutes before the shift),
the plan for each purpose with the previous shift's published plan defended, independent validation, the published
plan's tables, the shop-floor execution of the dispatch purpose's assignments in the shift, and the source changes it
causes. Repeatable: the same arguments give the same tables (decision digests in ``sim_shift``).
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import random
import re
import resource
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]

from ..calendar import ShiftPolicy, build_shift  # noqa: E402
from ..clock import FixedClock  # noqa: E402
from ..config import Settings  # noqa: E402
from ..data.fullscale_fixture import FLEET_FACTS, FleetSpec, build_fullscale_fixture  # noqa: E402
from ..data.repositories import Repositories  # noqa: E402
from ..data.store import FixtureStore  # noqa: E402
from ..domain import Disposition, DutyKind, Purpose, digest_id, from_minute, to_minute  # noqa: E402
from ..engine.max.optimizer.orchestrator import decision_digest  # noqa: E402
from ..runtime import build_services  # noqa: E402
from .db import SimDB  # noqa: E402
from .engine import admit, plan, published_incumbent  # noqa: E402
from .execute import DayGoals, FloorSpec, attendance_rows, execute_shift, freeze_day  # noqa: E402
from .state import SourceState  # noqa: E402

UTC = timezone.utc
VIOLATION_ROWS = 500                                            # violations kept per shift and plan: enough to diagnose a failed check
UNSAFE_CODE = re.compile(r"^[A-Z][A-Z_]+(?=:|$)")            # FLOOR_AFTER_COMMITTED_START, SAFETY_OVERRIDE, ...
DUTY_DAYS = 1                                   # duties kept per plan: the published shift and the rest of its day


@dataclass(frozen=True)
class SimSpec:
    fleet: FleetSpec = field(default_factory=FleetSpec)
    floor: FloorSpec = field(default_factory=FloorSpec)
    days: int = 14
    purposes: tuple[str, ...] = ("EXECUTABLE", "FORECAST")    # the floor works the executable plan; the forecast projects
    max_moves: int = 3
    max_repairs: int = 2
    budget_seconds: float = 3600.0
    cutoff_lead_minutes: int = 15
    seed: int = 20261026
    shifts: int | None = None                   # stop after this many shifts (a trial); None runs every shift of every day


def _iso(minute: int | None) -> str | None:
    return None if minute is None else from_minute(minute).isoformat()


def _release_memory() -> float:
    """Collect and hand freed heap back to the operating system between shifts (glibc), so each shift starts small; the
    resident size afterwards in MB (Linux), or 0.0 where it cannot be read."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 20
    except (OSError, ValueError, IndexError):
        return 0.0


def run(spec: SimSpec, db_path: Path, *, log=print) -> dict[str, Any]:
    settings = Settings.from_env({"FOCUS_NATIVE_CORE": "true"}, app_root=APP_ROOT, inprocess_worker=False)
    first_day = spec.fleet.cutoff_day
    clock = FixedClock(datetime(first_day.year, first_day.month, first_day.day, 9, 0, tzinfo=UTC))
    store_dir = Path(tempfile.mkdtemp(prefix="focus-sim-"))
    services = build_services(settings, repositories=Repositories(FixtureStore(store_dir), mode="fixture", scope_key=settings.scope_key,
                                                                  clock=clock), clock=clock)
    shift_policy = ShiftPolicy.from_policy(services.policy)
    tz = shift_policy.tz
    db = SimDB(db_path)
    t0 = time.time()
    datasets = build_fullscale_fixture(spec.fleet)
    facts = dict(FLEET_FACTS)
    state = SourceState(datasets)
    sim_id = digest_id("SIM", asdict(spec))
    facts.update({"policy_id": services.policy.id, "policy_hash": services.policy.hash, "synthetic": True,
                  "score_policy_status": services.policy.str("score.policy_status")})
    db.run_meta(sim_id, datetime.now(UTC).isoformat(), asdict(spec), facts, "RUNNING")
    for name, ds in datasets.items():
        db.source_rows(name, ds.rows, 0)
    db.commit()
    log(f"generated fleet in {time.time() - t0:.1f}s: {facts['mix'][:3]}..., {facts['mechanics_per_shift']} mechanics per shift")
    cert_of_team = dict(facts["team_certs"])
    rework_minutes = sorted({int(r["ELAPSED_MINUTES"]) for r in state.all("duration_episodes")})
    rework_rate = spec.fleet.rework_total / (5 * spec.fleet.stay_days * 3)
    incumbent = None
    day_goals: DayGoals | None = None                            # goals of record: frozen by the day's first accepted plan
    summary: list[dict[str, Any]] = []
    for k in range(min(spec.days * 3, spec.shifts or spec.days * 3)):
        day = first_day + timedelta(days=k // 3)
        number = k % 3 + 1
        week = k // 21 + 1
        shift = build_shift(day, number, shift_policy, settings.site)
        cutoff = from_minute(shift.start - spec.cutoff_lead_minutes)
        clock._instant = cutoff                                       # the fixed clock reads the shift's cutoff
        rng = random.Random(spec.seed * 1000 + k)
        timings: dict[str, float] = {}
        if number == 1:
            load_ts = (cutoff - timedelta(minutes=5)).astimezone(tz).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.000")
            for row in attendance_rows(state, day, random.Random(spec.seed * 7 + k), spec.floor.planned_absence, load_ts):
                state.upsert("attendance_shift", row)
        changes = state.take_changes()
        for name, items in changes.items():
            for op in ("upsert", "delete"):
                db.source_rows(name, (r for o, r in items if o == op), k, op)
        t = time.time()
        run_id = digest_id("SR-SIM", sim_id, k, length=20)
        snapshot, build = admit(services, state, cutoff=cutoff, run_id=run_id, created_at=cutoff)
        timings["load_s"] = time.time() - t
        log(f"shift {k} ({shift.id}): admitted {len(snapshot.jobs)} jobs, {len(snapshot.phases)} phases in {timings['load_s']:.1f}s")
        # the dispatch purpose is planned last: every other purpose is written and released first, so only one engine
        # context is alive at a time (at 50 aircraft one context is several GB)
        dispatch_name = "EXECUTABLE" if "EXECUTABLE" in spec.purposes else spec.purposes[0]
        order = [name for name in spec.purposes if name != dispatch_name] + [dispatch_name]
        timings.update(plan_s=0.0, validate_s=0.0, write_s=0.0)
        accepted: dict[str, Any] = {}
        dispatch = None
        for purpose_name in order:
            t = time.time()
            sp = plan(services, snapshot, Purpose(purpose_name), incumbent=incumbent.get(purpose_name) if incumbent else None,
                      max_moves=spec.max_moves, budget_seconds=spec.budget_seconds, created_at=cutoff, max_repairs=spec.max_repairs)
            timings["plan_s"] += time.time() - t
            timings["validate_s"] += sp.validate_s
            t = time.time()
            _write_plan(db, k, purpose_name, sp, shift, tz)
            # only a plan the independent validator passed is accepted: it becomes the next incumbent and, for the dispatched
            # purpose, the work of the floor; an invalid one leaves the previous incumbent in force and the floor without goals
            if sp.run.validation.valid:
                accepted[purpose_name] = published_incumbent(snapshot, sp, cutoff)
            timings["write_s"] += time.time() - t
            if purpose_name == dispatch_name:
                dispatch = sp
            else:
                del sp
                _release_memory()
        assert dispatch is not None
        t = time.time()
        if k == 0:
            _write_people(db, snapshot, state)
        _write_calc(db, k, snapshot, dispatch.context, state)
        timings["write_s"] += time.time() - t
        incumbent = {**(incumbent or {}), **accepted}
        t = time.time()
        floor_plan = dispatch if dispatch.run.validation.valid else replace(dispatch, plan=replace(dispatch.plan, assignments=()))
        if day_goals is not None and day_goals.day != day:
            day_goals = None
        if day_goals is None and dispatch.run.validation.valid:
            windows = {n: (w.start, w.end) for n in range(number, 4) for w in [build_shift(day, n, shift_policy, settings.site)]}
            day_goals = freeze_day(dispatch.plan, dispatch.context, day, windows)
            db.insert("perf_goal_day", [(k, day.isoformat(), dispatch.plan.id, team, n, len(us), sum(u.effort for u in us))
                                        for (team, n), us in sorted(day_goals.units.items())])
        outcome = execute_shift(state, floor_plan, shift, shift_no=k, week=week, rng=rng, spec=spec.floor, rework_rate=rework_rate,
                                rework_minutes=rework_minutes, cert_of_team=cert_of_team, tz=tz, policy=services.policy, goals=day_goals)
        timings["execute_s"] = time.time() - t
        db.insert("exec_assignment", outcome.rows)
        db.insert("exec_event", outcome.events)
        db.insert("exec_offplan", outcome.offplan)
        db.insert("exec_oos", outcome.oos)
        db.insert("perf_unit", outcome.units)
        db.insert("perf_claim", outcome.claims)
        db.insert("perf_amendment", outcome.amendments)
        db.insert("perf_unlock", outcome.unlocks)
        db.insert("perf_team_shift", outcome.perf)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        val = dispatch.run.validation
        db.insert("sim_shift", [(k, shift.id, day.isoformat(), number, week, cutoff.isoformat(), _iso(shift.start), _iso(shift.end), run_id,
                                 snapshot.id, snapshot.hash, dispatch.plan.id, dispatch.plan.hash, decision_digest(dispatch.plan), int(val.valid),
                                 dispatch.run.native.get("engine"), dispatch.run.stop_reason, dispatch.run.moves, round(timings["load_s"], 2),
                                 round(timings["plan_s"], 2), round(timings["validate_s"], 2), round(timings["execute_s"], 2),
                                 round(timings["write_s"], 2), round(rss, 1))])
        db.commit()
        done = Counter(r[11] for r in outcome.rows)
        row = {"shift": k, "id": shift.id, "valid": val.valid, "assignments": len(dispatch.plan.assignments), "executed": dict(done),
               "jobs_completed": outcome.completed_jobs, **{key: round(v, 1) for key, v in timings.items()}, "rss_mb": round(rss)}
        del snapshot, build, dispatch, outcome
        row["rss_after_mb"] = round(_release_memory())
        summary.append(row)
        log(json.dumps(row))
    db.run_meta(sim_id, datetime.now(UTC).isoformat(), asdict(spec), facts, "COMPLETE")
    db.close()
    return {"sim_id": sim_id, "shifts": summary, "facts": facts}


# ---------------------------------------------------------------------------------------------- table writers

def _write_people(db: SimDB, snapshot: Any, state: SourceState) -> None:
    roster = {r["PERSON_KEY"]: r for r in state.all("shift_roster")}
    names = {r["PERSON_KEY"]: r["DISPLAY_NAME"] for r in state.all("people_directory")}
    rows = []
    for p in snapshot.people:
        native = p.key.native_id
        r = roster.get(native, {})
        rows.append((p.id, native, names.get(native, native), r.get("TEAM"), r.get("SHIFT_NUMBER"), r.get("ROLE")))
    db.insert("sim_person", rows)


def _write_calc(db: SimDB, k: int, snapshot: Any, ctx: Any, state: SourceState) -> None:
    disp = {d.job: d for d in snapshot.dispositions}
    counts = Counter(d.disposition.value for d in snapshot.dispositions)
    rows = [(k, f"disposition:{name}", n) for name, n in sorted(counts.items())]
    rows += [(k, item, n) for item, n in (("jobs", len(snapshot.jobs)), ("phases", len(snapshot.phases)), ("edges", len(snapshot.edges)),
                                         ("people", len(snapshot.people)), ("pools", len(snapshot.pools)), ("shortages", len(snapshot.shortages)),
                                         ("releases", len(snapshot.releases)), ("availability", len(snapshot.availability)))]
    db.insert("calc_count", rows)
    cs = {(f.line, f.station): f for f in snapshot.cs_facts}
    ecd = {(int(r["LINE_NUMBER"]), str(r["USED_ON"])): r.get("ETRAC_ECD") for r in state.all("etracs_active")}
    work: dict[str, int] = defaultdict(int)
    crew: dict[str, int] = {}
    for p in snapshot.phases:
        work[p.job] += p.work_minutes
        if p.role_demands and p.job not in crew:
            crew[p.job] = p.role_demands[0].bundle.count
    jobs = []
    lines: dict[int, dict[str, Any]] = {}
    for j in snapshot.jobs:
        d = disp.get(j.id)
        state_name = d.disposition.value if d else "missing"
        info = lines.setdefault(j.line, {"open": 0, "done": 0, "short": 0, "rework": 0, "customer": j.customer, "model": j.model, "station": None})
        if state_name == "verified_complete":
            info["done"] += 1
            continue
        info["open"] += 1
        info["rework"] += 1 if j.is_rework else 0
        facts = cs.get((j.line, j.station))
        floor, _ = ctx.job_floor(j.id)
        short = ecd.get((j.line, j.key.native_id), "none")
        if short != "none":
            info["short"] += 1
        jobs.append((k, j.id, j.line, j.key.native_id, j.station, j.team, int(bool(j.is_rework)), j.status, state_name,
                     (d.reasons[0] if d and d.reasons else None), work.get(j.id), crew.get(j.id), _iso(floor),
                     facts.earliest_at.isoformat() if facts and facts.earliest_at else None,
                     str(facts.gate_date) if facts and getattr(facts, "gate_date", None) else None,
                     None if short == "none" else (short or "UNDATED"), int(bool(d and d.executable_reasons))))
    db.insert("calc_job", jobs)
    econ = {e.line: e for e in snapshot.economics}
    for f in snapshot.cs_facts:
        info = lines.get(f.line)
        if info is not None and f.earliest_at is not None and f.earliest_at <= snapshot.cutoff and (info["station"] is None or f.station > info["station"]):
            info["station"] = f.station
    db.insert("calc_aircraft", [(k, line, info["customer"], info["model"], info["station"],
                                 econ[line].due_at.isoformat() if line in econ and econ[line].due_at else None,
                                 float(econ[line].daily_rate_cents) / 100 if line in econ and econ[line].daily_rate_cents is not None else None,
                                 info["open"], info["done"], info["short"], info["rework"]) for line, info in sorted(lines.items())])
    horizon = snapshot.cutoff.date() + timedelta(days=3)
    db.insert("calc_pool", [(k, p.key.start_date.isoformat(), p.key.shift_number, p.key.team, len(p.forecast_members), p.cap_forecast)
                            for p in snapshot.pools if p.key.start_date <= horizon])


def _write_plan(db: SimDB, k: int, purpose: str, sp: Any, shift: Any, tz) -> None:
    plan, run, ctx = sp.plan, sp.run, sp.context
    db.insert("method_stage", [(k, purpose, n, s.stage, s.runtime_ms, s.candidates, s.accepted, s.note) for n, s in enumerate(run.stages)])
    db.insert("method_move", [(k, purpose, n, m) for n, m in enumerate(run.accepted_moves)])
    val = run.validation
    bad = Counter(v.check for v in val.violations)
    db.insert("method_check", [(k, purpose, name, count, bad.get(name, 0)) for name, count in sorted(val.check_counts.items())])
    db.insert("method_violation", [(k, purpose, v.check, v.code, v.entity, v.detail[:500]) for v in val.violations[:VIOLATION_ROWS]])
    outcomes: Counter = Counter()
    for r in plan.commitment_results:
        code = next((m.group(0) for m in map(UNSAFE_CODE.match, (r.attempt1, r.reason)) if m), "") if r.outcome == "UNSAFE" else ""
        outcomes[(r.outcome, code)] += 1
    db.insert("method_commitment", [(k, purpose, outcome, code, n) for (outcome, code), n in sorted(outcomes.items())])
    team_of = {pid: (p.role_demands[0].team if p.role_demands else None) for pid, p in ctx.phases.items()}
    rows = []
    for a in plan.assignments:
        job = ctx.jobs.get(a.job)
        rows.append((k, purpose, a.id, a.job, a.line, job.key.native_id if job else None, a.kind.value, team_of.get(a.phase), a.start.isoformat(),
                     a.end.isoformat(), a.shift_id, len(a.crew), ",".join(c.person for c in a.crew), int(bool(a.conditional_reasons)),
                     int(bool(a.committed)), a.commitment_outcome))
    db.insert("plan_assignment", rows)
    econ = {e.line: e for e in ctx.snapshot.economics}
    out = []
    for c in plan.aircraft_completion:
        e = econ.get(c.line)
        late = None
        cost = None
        if c.completion_at is not None and e is not None and e.due_at is not None:
            late = max(0.0, (c.completion_at - e.due_at).total_seconds() / 86400)
            cost = round(late * float(e.daily_rate_cents) / 100, 2) if e.daily_rate_cents is not None else None
        out.append((k, purpose, c.line, c.status, c.completion_at.isoformat() if c.completion_at else None,
                    e.due_at.isoformat() if e and e.due_at else None, None if late is None else round(late, 3), cost))
    db.insert("plan_aircraft", out)
    db.insert("plan_exception", [(k, purpose, o.job, ctx.jobs[o.job].line if o.job in ctx.jobs else None, o.disposition.value,
                                  (o.reasons[0] if o.reasons else None)) for o in plan.job_dispositions
                                 if o.disposition in (Disposition.DEFERRED, Disposition.BLOCKED)])
    team_of_person = {m: pool.key.team for pool in ctx.snapshot.pools for m in pool.forecast_members}
    until = shift.end + DUTY_DAYS * 1440
    db.insert("plan_duty", [(k, purpose, d.person, team_of_person.get(d.person), d.start.isoformat(), d.end.isoformat(), d.kind.value,
                             d.aircraft_from, d.aircraft_to, d.assignment_id, d.shift_id) for d in plan.duties
                            if shift.start <= to_minute(d.start) < until])
    _ = DutyKind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aircraft", type=int, default=50)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--db", default=str(APP_ROOT / "runtime" / "sim" / "fleet.db"))
    parser.add_argument("--purposes", default="EXECUTABLE,FORECAST")
    parser.add_argument("--max-moves", type=int, default=3)
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument("--baseline", type=int, default=4500)
    parser.add_argument("--edges", type=int, default=22000)
    parser.add_argument("--rework", type=int, default=3000)
    parser.add_argument("--teams", type=int, default=30)
    parser.add_argument("--team-size", default="10,30")
    parser.add_argument("--shifts", type=int, default=None, help="stop after this many shifts (a trial run)")
    args = parser.parse_args(argv)
    lo, hi = (int(x) for x in args.team_size.split(","))
    spec = SimSpec(fleet=FleetSpec(n_aircraft=args.aircraft, baseline=args.baseline, edges=args.edges, rework_total=args.rework, teams=args.teams,
                                   team_size=(lo, hi)), days=args.days, purposes=tuple(p.strip() for p in args.purposes.split(",")),
                   max_moves=args.max_moves, max_repairs=args.max_repairs, shifts=args.shifts)
    db_path = Path(args.db)
    if db_path.exists():
        print(f"{db_path} exists; remove it or choose another --db", file=sys.stderr)
        return 2
    run(spec, db_path, log=lambda m: print(m, flush=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
