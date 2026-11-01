"""P14: incumbent task defense through the real MAX pipeline, and the accepted-plan delivery feed (A42, A43)."""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from focus.calendar import build_shift
from focus.codec import parse_utc
from focus.contracts.planning import CandidatePlan, work_to_go
from focus.data.acquisition import snapshot_for_run
from focus.data.fixture_reader import FixtureSourceReader
from focus.domain import DutyKind, PersonKey, PhaseKind, Purpose, to_minute
from focus.engine.commitments import (DeliveryCommitments, IncumbentAssignment, IncumbentPlan, defense_for, import_fixture_incumbent,
                                      incumbent_head, record_accepted_incumbent)
from focus.engine.max.optimizer.greedy import Decoder, DefendedTask
from focus.engine.max.optimizer.orchestrator import CANDIDATES, schedule
from focus.engine.model import EngineContext
from focus.engine.projections import delivery_projections, record_accepted
from focus.validation.duty import reconstruct
from tests.conftest import acquire_demo, job_id

UTC = timezone.utc
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
KEEP = {"DEMO-A1-05", "DEMO-A1-06", "DEMO-A1-07"}


def _p(native: str) -> str:
    return PersonKey(native, "fixture-demo").id


def _crew(assignment) -> set[str]:
    return {r.person for r in assignment.crew}


def _tz(services) -> ZoneInfo:
    return ZoneInfo(services.policy.str("shift.timezone"))


def _at(tz: ZoneInfo, day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz).astimezone(UTC)


def _run(services, outcome, purpose=Purpose.EXECUTABLE, **kw):
    kw.setdefault("budget_seconds", 5)
    kw.setdefault("max_moves", 5)
    plan, run = schedule(services, source_run=outcome.run_id, purpose=purpose, **kw)
    return plan, {r.job: r for r in plan.commitment_results}, {a.id: a for a in plan.assignments}


def _acquire(services_factory, key: str, mutate, names: tuple[str, ...]):
    data = {n: json.loads((INPUTS / f"{n}.json").read_text()) for n in names}
    mutate(data)
    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection
    return services, outcome, snapshot_for_run(services, outcome.run_id)


def late_part_on_b1(data) -> None:
    data["etracs_active"]["rows"].append({"ETRAC_CREATED_DT": "2026-10-24", "ETRAC_ECD": "2026-10-29", "ITEM_NUMBER": "DEMO-P-0009",
                                          "LINE_NUMBER": 1302, "LOAD_TIMESTAMP": "2026-10-25T17:30:00.000", "MINOR_MODEL_IDENT": "9",
                                          "REASON_CODE": "LP", "RECORD_ID": 50509, "TEAM": "PROD-A", "USED_ON": "B1"})


def shared_crew_only(data) -> None:
    for name, col in (("person_team_map", "BEMS_ID"), ("shift_roster", "PERSON_KEY"), ("attendance_shift", "BEMS_ID")):
        data[name]["rows"] = [r for r in data[name]["rows"] if not (str(r[col]).startswith("DEMO-A1-") and r[col] not in KEEP)]


def test_a42_ready_commitments_first_exact_then_alternate_crew_and_day3_free(demo) -> None:
    services, outcome, _ = demo
    snapshot = snapshot_for_run(services, outcome.run_id)
    tz = _tz(services)
    plain, _, _ = _run(services, outcome)
    assert plain.incumbent_id is None and not plain.commitment_results
    assert plain.algorithm_status.incumbent["status"] == "NO_INCUMBENT"            # the inputs' incumbent_plan.json is not an incumbent
    a1_job, a1_start = job_id(snapshot, 1301, "A1"), _at(tz, date(2026, 10, 26), "06:15")
    rivals = lambda plan: [a for a in plan.assignments if a.start == a1_start and _p("DEMO-A1-02") in _crew(a) and a.job != a1_job]  # noqa: E731
    assert rivals(plain)                                                            # contested: uncommitted work wants A1-02 then
    incumbent = import_fixture_incumbent(services)
    plan, results, by_id = _run(services, outcome)
    assert plan.incumbent_id == incumbent.id and plan.algorithm_status.incumbent["status"] == "LOADED"
    for line, soi, day, crew in ((1301, "A1", date(2026, 10, 26), ("DEMO-A1-01", "DEMO-A1-02")),
                                 (1303, "NC3", date(2026, 10, 26), ("DEMO-B1-03", "DEMO-B1-04")),
                                 (1302, "B1", date(2026, 10, 27), ("DEMO-A1-03", "DEMO-A1-04"))):
        r = results[job_id(snapshot, line, soi)]
        a = by_id[r.new_assignment_id]
        assert (r.outcome, r.attempt1, r.attempt2) == ("KEPT", "OK", "not needed")                       # attempt 1: exact
        assert a.start == _at(tz, day, "06:15") and _crew(a) == {_p(c) for c in crew} and a.committed and a.commitment_outcome == "KEPT"
    assert not rivals(plan)                                                         # the ready commitment was dispatched first
    a2 = results[job_id(snapshot, 1301, "A2")]
    alt = by_id[a2.new_assignment_id]
    assert a2.outcome == "CREW_CHANGED" and a2.attempt1.startswith("NOT_POOL_MEMBER") and _p("DEMO-A1-06") in a2.attempt1
    assert a2.attempt2 == "OK" and alt.start == _at(tz, date(2026, 10, 28), "06:15") and alt.shift_id == "DEMO:2026-10-28:S1"
    assert len(alt.crew) == 2 and _p("DEMO-A1-06") not in _crew(alt) and _p("DEMO-A1-05") in _crew(alt)   # attempt 2: same minute
    ctx = EngineContext(snapshot, services.policy, Purpose.EXECUTABLE)
    bundle = ctx.phases[alt.phase].role_demands[0].bundle
    assert all(ctx.qualifies(p, bundle, to_minute(alt.start), to_minute(alt.end)) for p in _crew(alt))   # every member individually
    b2 = results[job_id(snapshot, 1302, "B2")]
    b2_work = [a for a in plan.assignments if a.job == b2.job and a.kind is PhaseKind.WORK]
    assert b2.outcome == "OUT_OF_HORIZON" and b2.day_offset == 3 and b2_work and not any(a.committed for a in b2_work)
    assert min(a.start for a in b2_work) < _at(tz, date(2026, 10, 29), "06:15")    # day 3 freely repacked, not pinned
    stored = services.repositories.get_typed(CANDIDATES, plan.id, "CandidatePlan", CandidatePlan)
    assert stored.commitment_results == plan.commitment_results and stored.algorithm_status.incumbent["id"] == incumbent.id
    assert not reconstruct(snapshot, services.policy, plan).violations


def test_a42_both_attempts_fail_then_minimal_audited_move(services_factory) -> None:
    services, outcome, snapshot = _acquire(services_factory, "a42-move", shared_crew_only, ("person_team_map", "shift_roster", "attendance_shift"))
    tz = _tz(services)
    record_accepted_incumbent(services, IncumbentPlan(
        "INC-A42-MOVE", services.settings.scope_key, "EXECUTABLE", services.policy.id, "FIXTURE_ACCEPTED", parse_utc("2026-10-25T22:30:00Z"),
        parse_utc("2026-10-25T22:00:00Z"), "fixture-demo",
        (IncumbentAssignment(1301, "A1", "WORK", 0, date(2026, 10, 26), 1, "06:15", ("DEMO-A1-05", "DEMO-A1-07")),
         IncumbentAssignment(1302, "B1", "WORK", 0, date(2026, 10, 26), 1, "06:15", ("DEMO-A1-05", "DEMO-A1-06")))))
    plan, results, by_id = _run(services, outcome)
    kept, moved = sorted(results.values(), key=lambda r: r.outcome)                 # N=3 -> two heads: one crew at a time
    assert (kept.outcome, moved.outcome) == ("KEPT", "MOVED")
    a_kept, a_moved = by_id[kept.new_assignment_id], by_id[moved.new_assignment_id]
    committed = _at(tz, date(2026, 10, 26), "06:15")
    assert moved.attempt1.split(":")[0] in {"PERSON_OVERLAP", "CREW_UNAVAILABLE", "ACTIVATION_CAP"}
    assert moved.attempt2.startswith("CREW_UNAVAILABLE")                            # both attempts failed, both recorded
    assert a_kept.start == committed and a_moved.start == a_kept.end + timedelta(minutes=15)   # nearest slot: crew free + one transfer
    minutes = (a_moved.start - committed) // timedelta(minutes=1)
    assert moved.reason == f"minimal audited move +{minutes} minutes to {a_moved.shift_id}" and a_moved.commitment_outcome == "MOVED"
    assert not reconstruct(snapshot, services.policy, plan).violations


def test_a42_no_feasible_slot_defers_without_forcing(demo_snapshot, policy) -> None:
    ctx = EngineContext(demo_snapshot, policy, Purpose.EXECUTABLE)
    shift = ctx.shift(date(2026, 10, 26), 1)
    a1 = next(p for p in ctx.job_phases[job_id(demo_snapshot, 1301, "A1")] if p.kind is PhaseKind.WORK)
    only, other = [s for s in ctx.line_shifts(1301) if s.id == shift.id], ctx.line_shifts
    ctx.line_shifts = lambda line: only if line == 1301 else other(line)            # the committed shift is the line's last window
    pool, base = ctx.pool_for("PROD-A", shift), ctx.new_ledger

    def busy_ledger():
        ledger = base()
        for i, person in enumerate(ctx.pool_members(pool.id)):
            if person != _p("DEMO-A1-01"):                                          # one qualified person stays free: never a crew of two
                ledger.add_locked(f"BUSY{i}", person, shift.start, shift.end, DutyKind.OTHER, None, shift.id, pool.id)
        return ledger

    ctx.new_ledger = busy_ledger
    d = DefendedTask(a1.key.id, "INCA-TEST", shift.id, shift.regular_windows[0][0], (_p("DEMO-A1-01"), _p("DEMO-A1-02")), 0)
    res = Decoder(ctx, lambda pid: (-ctx.tails.get(pid, 0), pid), defended={a1.key.id: d}).run()
    r = next(r for r in res.commitment_results if r.incumbent_assignment_id == "INCA-TEST")
    assert r.outcome == "DEFERRED" and r.attempt1.startswith("PERSON_OVERLAP") and r.attempt2.startswith("CREW_UNAVAILABLE: 1 of 2")
    assert r.reason.startswith("NO_FEASIBLE_PLACEMENT") and r.new_assignment_id is None
    assert a1.key.id in res.deferred and a1.key.id not in res.placements            # no forced or partial placement


def test_a43_fresh_process_reloads_only_the_accepted_incumbent(tmp_path, app_root) -> None:
    env = {"FOCUS_RUNTIME_DIR": str(tmp_path / "rt"), "PATH": "/usr/bin:/bin"}

    def cli(*args: str) -> dict:
        out = subprocess.run([sys.executable, "-m", "focus.cli", *args], cwd=app_root, env=env, capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stdout + out.stderr
        return json.loads(out.stdout)

    run_id = cli("acquire", "--mode", "fixture", "--scenario", "manufacturing_demo")["source_run_id"]
    sched = ("schedule", "--source-run", run_id, "--policy", "rebuild-v1", "--purpose", "EXECUTABLE", "--budget-seconds", "3")
    first = cli(*sched)
    assert first["incumbent"]["status"] == "NO_INCUMBENT" and first["commitments"] == []   # not the JSON input, not a candidate
    boot = cli("demo-bootstrap", "--scenario", "manufacturing_demo")
    assert boot["inputs"]["written"] == [] and set(boot["incumbents"]) == {"EXECUTABLE", "FORECAST"}
    second = cli(*sched)                                                            # a new process: reloaded from the repository
    assert second["incumbent"]["status"] == "LOADED" and second["incumbent"]["id"] == boot["incumbents"]["EXECUTABLE"]
    assert sorted(c["outcome"] for c in second["commitments"]) == ["CREW_CHANGED", "KEPT", "KEPT", "KEPT", "OUT_OF_HORIZON"]
    assert second["published"] is False and second["candidate_id"] != first["candidate_id"]


def test_a43_incompatible_or_missing_incumbent_is_reported_and_never_defended(demo) -> None:
    services, outcome, snapshot = demo
    base = import_fixture_incumbent(services)
    cases = (("INCOMPATIBLE_POLICY", dataclasses.replace(base, id="INC-OTHER-POLICY", policy_id="other-policy")),
             ("INCOMPATIBLE_SOURCE", dataclasses.replace(base, id="INC-FUTURE", previous_cutoff=snapshot.cutoff + timedelta(hours=1))),
             ("INCOMPATIBLE_SOURCE", dataclasses.replace(base, id="INC-OTHER-NS", namespace="other-namespace")))
    for status, incumbent in cases:
        record_accepted_incumbent(services, incumbent)
        plan, _, _ = _run(services, outcome, budget_seconds=2, max_moves=0)
        assert plan.algorithm_status.incumbent["status"] == status, plan.algorithm_status.incumbent
        assert plan.incumbent_id is None and not plan.commitment_results and not any(a.committed for a in plan.assignments)
    name = incumbent_head(base.scope, "EXECUTABLE")
    services.repositories.move_head(name, "INC-NEVER-WRITTEN", services.repositories.head(name).version)
    plan, _, _ = _run(services, outcome, budget_seconds=2, max_moves=0)
    assert plan.algorithm_status.incumbent["status"] == "ARTIFACT_MISSING" and not plan.commitment_results
    fplan, _, _ = _run(services, outcome, purpose=Purpose.FORECAST, budget_seconds=2, max_moves=0)
    assert fplan.algorithm_status.incumbent["status"] == "NO_INCUMBENT"            # another purpose's incumbent is never borrowed


def test_a43_late_part_overrides_the_defense_immediately(services_factory) -> None:
    services, outcome, snapshot = _acquire(services_factory, "a43-part", late_part_on_b1, ("etracs_active",))
    import_fixture_incumbent(services)
    plan, results, _ = _run(services, outcome)
    b1 = job_id(snapshot, 1302, "B1")
    r = results[b1]
    assert r.outcome == "UNSAFE" and r.reason.startswith("SAFETY_OVERRIDE: PART_NOT_RECEIVED") and "DEMO-P-0009" in r.reason
    assert r.new_assignment_id is None and not [a for a in plan.assignments if a.job == b1]        # no forced placement
    assert {results[job_id(snapshot, 1301, "A1")].outcome, results[job_id(snapshot, 1303, "NC3")].outcome} == {"KEPT"}
    import_fixture_incumbent(services, purpose="FORECAST")
    fplan, fresults, fby_id = _run(services, outcome, purpose=Purpose.FORECAST)
    fr = fresults[b1]
    fa = fby_id[fr.new_assignment_id]
    assert fr.outcome == "UNSAFE" and fr.attempt1.startswith("FLOOR_AFTER_COMMITTED_START: earliest 2026-10-30T04:00:00+00:00")
    assert fr.reason.endswith("moved to the earliest safe slot") and fa.start >= datetime(2026, 10, 30, 4, tzinfo=UTC)
    assert fa.conditional_reasons                                                   # forecast only, labelled conditional


def test_a43_expired_cert_and_changed_calendar_override_the_exact_slot(services_factory) -> None:
    def expire(data):
        for row in data["mechanic_certs"]["rows"]:
            if row["BEMS_ID"] == "DEMO-A1-01" and row["CERT_CODE"] == "A":
                row["CERT_EXP_DATE"] = "2026/10/25"

    services, outcome, snapshot = _acquire(services_factory, "a43-cert", expire, ("mechanic_certs",))
    import_fixture_incumbent(services)
    plan, results, _ = _run(services, outcome)
    r = results[job_id(snapshot, 1301, "A1")]
    assert r.outcome == "CREW_CHANGED" and r.attempt1.startswith("NOT_QUALIFIED") and _p("DEMO-A1-01") in r.attempt1
    assert not any(_p("DEMO-A1-01") in _crew(a) for a in plan.assignments)          # the expired holder is placed nowhere

    def close_1027(data):
        for row in data["cday_op_plan"]["rows"]:
            if row["LINE_NUMBER"] == 1302 and row["CALENDAR_DATE"] == "2026-10-27":
                row["DAY_STATUS"] = "NONWORKING"

    services, outcome, snapshot = _acquire(services_factory, "a43-calendar", close_1027, ("cday_op_plan",))
    import_fixture_incumbent(services)
    plan, results, by_id = _run(services, outcome)
    r = results[job_id(snapshot, 1302, "B1")]
    assert r.outcome == "UNSAFE" and r.attempt1.startswith("CALENDAR") and r.reason.endswith("moved to the earliest safe slot")
    assert not by_id[r.new_assignment_id].shift_id.startswith("DEMO:2026-10-27")
    assert results[job_id(snapshot, 1301, "A1")].outcome == "KEPT"                  # an invalidated task never displaces a feasible one


def test_accepted_forecast_plan_feeds_delivery_commitments_once(services_factory) -> None:
    services, outcome, snapshot = _acquire(services_factory, "delivery", late_part_on_b1, ("etracs_active",))
    import_fixture_incumbent(services, purpose="FORECAST")
    fplan, _, _ = _run(services, outcome, purpose=Purpose.FORECAST)
    receipt = record_accepted(services, fplan, snapshot)
    projections = delivery_projections(fplan, _tz(services))
    decisions = dict(receipt.decisions)
    assert receipt.status == "RECORDED" and set(decisions) == set(projections)     # every aircraft, unknown ones included
    assert all(decisions[line] == ("ESTABLISHED" if day else "UNKNOWN") for line, day in projections.items())
    assert any(day is None for day in projections.values())                         # unknown completion never becomes a date
    assert (1302, "SAFETY_ALERT") in {(e.line, e.kind) for e in receipt.events}     # invalidated commitment alerts immediately
    assert record_accepted(services, fplan, snapshot).status == "REPLAY"
    eplan, _, _ = _run(services, outcome, budget_seconds=2, max_moves=0)
    assert record_accepted(services, eplan, snapshot).status == "NOT_APPLICABLE"    # dispatch plans never move delivery dates
    state, _ = DeliveryCommitments(services.repositories, fplan.scope, services.policy).get(1302)
    assert state.alert.startswith("COMMITMENT_UNSAFE") and state.counted == ((fplan.id, decisions[1302]),)


def test_s3_commitment_after_local_midnight_is_defended_at_its_real_start(demo) -> None:
    services, _, snapshot = demo
    ctx = EngineContext(snapshot, services.policy, Purpose.EXECUTABLE)
    a1 = job_id(snapshot, 1301, "A1")
    phase = next(p for p in ctx.job_phases[a1] if p.kind is PhaseKind.WORK)
    day = snapshot.cutoff.astimezone(_tz(services)).date()
    incumbent = IncumbentPlan("INC-S3-MIDNIGHT", snapshot.scope_key, "EXECUTABLE", services.policy.id, "PUBLISHED", snapshot.cutoff,
                              snapshot.cutoff, "fixture-demo",
                              (IncumbentAssignment(1301, "A1", "WORK", phase.ordinal, day, 3, "01:00", ("DEMO-A1-05", "DEMO-A1-06")),),
                              (work_to_go(ctx.job_phases[a1])[phase.key.id],))
    task = defense_for(ctx, incumbent, status="LOADED", detail="test").defended[phase.key.id]
    s3 = build_shift(day, 3, ctx.shift_policy, ctx.site)
    assert task.start == to_minute(_at(_tz(services), day + timedelta(days=1), "01:00"))   # not 24 hours before the shift
    assert s3.start <= task.start < s3.end and s3.valid_start(task.start)


def test_in_process_qa_holds_are_defended_at_their_chain_position(demo) -> None:
    """Positions are measured along each job's chain when a plan is accepted; an in-process QA hold is found there again.
    The context keeps phases sorted by id elsewhere, where a hold's position would never match (it was UNSAFE)."""
    from focus.publication.service import publish

    services, outcome, _ = demo
    first, _ = schedule(services, source_run=outcome.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    assert publish(services, first.id).status == "ACCEPTED"
    holds = {a.phase for a in first.assignments if a.kind is PhaseKind.IP_QA}
    assert len(holds) == 2                                                          # ROOT3's two in-process holds
    again = acquire_demo(services, key="ip-qa-again")
    plan, _ = schedule(services, source_run=again.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    assert not [c for c in plan.commitment_results if c.outcome == "UNSAFE"]
    defended = {c.phase: c.outcome for c in plan.commitment_results if c.phase in holds}
    assert defended == {phase: "KEPT" for phase in holds}
