"""P08: same-run typed CS facts, exact station grain and preserved date semantics (A23, A24)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from focus.calendar import admit_line_calendars
from focus.data.acquisition import snapshot_for_run
from focus.data.cs_windows import build_cs_facts, raw_rows

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def _row(line: int, station: int, load: str, unload: str, *, load_id: int = 7001, load_off: int = 0, unload_off: int = 0,
         load_ref: str = "P744", unload_ref: str = "P744", active: bool = True) -> dict:
    return {"MBU": "DEMO", "CONTROL_STATION": station, "LINE_NUMBER": line, "LOAD_REF_POS": load_ref, "LOAD_OFFSET": load_off,
            "CS_OP_LOAD": load, "UNLOAD_REF_POS": unload_ref, "UNLOAD_OFFSET": unload_off, "CS_OP_UNLOAD": unload,
            "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": load_id, "LOAD_TS": "2026-10-25T05:00:00", "IS_ACTIVE": active}


def _facts(policy, rows, needed, calendars=None, tz=NY):
    return build_cs_facts(rows=raw_rows(rows), run_id="RUN-1", needed=needed, calendars=calendars or {}, policy=policy, tz=tz, dataset_id="ds-cs")


def test_a23_exact_station_with_past_day0_gate_retained_and_due_distinct(policy) -> None:
    rows = [_row(1301, 744, "2026-10-26", "2026-10-26"), _row(1301, 750, "2026-10-29", "2026-11-04")]
    facts = _facts(policy, rows, [(1301, "744"), (1301, "750")])
    s744, s750 = facts[(1301, "744")], facts[(1301, "750")]
    assert s744.status == s750.status == "ADMITTED"
    assert s744.earliest_at == datetime(2026, 10, 26, 4, tzinfo=UTC) and s744.gate_date == date(2026, 10, 26)   # day-0 gate kept
    assert s750.earliest_at == datetime(2026, 10, 29, 4, tzinfo=UTC) and s750.gate_date == date(2026, 11, 4)    # no min/max widening
    assert s744.due_proxy == date(2026, 10, 28) and s750.due_proxy is None            # baseline proxy +2 once, only for station 744
    assert s744.hard_latest_at is None                                               # a missed gate never prohibits remaining work
    assert s744.source_run_id == "RUN-1" and s744.evidence[0].source_run_id == "RUN-1" and s744.evidence[0].row_id == "cs_windows#0"
    past = _facts(policy, [_row(1301, 744, "2026-10-20", "2026-10-22")], [(1301, "744")])[(1301, "744")]
    assert past.status == "ADMITTED" and past.gate_date == date(2026, 10, 22)        # a past gate stays a recognition fact


def test_a23_demo_snapshot_separates_earliest_gate_and_due(demo_snapshot) -> None:
    facts = {(f.line, f.station): f for f in demo_snapshot.cs_facts}
    f = facts[(1301, "744")]
    assert f.earliest_at == datetime(2026, 10, 26, 4, tzinfo=UTC) and f.gate_date == date(2026, 10, 28) and f.due_proxy == date(2026, 10, 30)
    assert all(x.source_run_id == demo_snapshot.source_run_id for x in demo_snapshot.cs_facts)
    assert {r.row_id for r in demo_snapshot.raw_cs_rows} >= {e.row_id for x in demo_snapshot.cs_facts for e in x.evidence}


def test_a24_signed_offsets_preserved_and_offset_mode_uses_workdays(policy) -> None:
    statuses = {"2026-10-23": "WORKING", "2026-10-24": "WORKING", "2026-10-25": "WEEKEND", "2026-10-26": "WORKING", "2026-10-27": "WORKING",
                "2026-10-28": "WORKING"}
    cal_rows = [{"LINE_NUMBER": 1301, "CALENDAR_DATE": d, "DAY_STATUS": s, "POSITION": "P744" if d == "2026-10-27" else f"X{d[-2:]}",
                 "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001, "IS_ACTIVE": True} for d, s in statuses.items()]
    calendars = admit_line_calendars(cal_rows, policy, [1301])
    offsets = policy.with_overrides({"cs.date_mode": "OFFSET_FROM_REFERENCE", "cs.offset_day_basis": "WORKDAY"}, new_id="cs-offset",
                                    authority="fixture_default", reason="A24 signed offsets")
    rows = [_row(1301, 744, None, None, load_off=-2, unload_off=1)]
    f = _facts(offsets, rows, [(1301, "744")], calendars)[(1301, "744")]
    assert f.raw_offset_fields == {"load_ref_pos": "P744", "load_offset": -2, "unload_ref_pos": "P744", "unload_offset": 1}   # signs kept
    assert f.earliest_at == datetime(2026, 10, 24, 4, tzinfo=UTC)                     # 2 workdays before 10-27 skips the weekend
    assert f.gate_date == date(2026, 10, 28)
    no_basis = policy.with_overrides({"cs.date_mode": "OFFSET_FROM_REFERENCE"}, new_id="cs-offset-nobasis", reason="A24")
    missing = _facts(no_basis, rows, [(1301, "744")], calendars)[(1301, "744")]
    assert missing.status == "POLICY_MISSING" and missing.reasons[0].startswith("CS_CONVERSION_POLICY_MISSING")


def test_a24_conflicting_loads_and_rows_block_without_max(policy) -> None:
    rows = [_row(1301, 744, "2026-10-26", "2026-10-28", load_id=7001), _row(1301, 744, "2026-10-27", "2026-10-29", load_id=7002)]
    f = _facts(policy, rows, [(1301, "744")])[(1301, "744")]
    assert f.status == "CONFLICT" and "CS_LOAD_CONFLICT: active loads [7001, 7002]" in f.reasons[0] and f.earliest_at is None
    dup = _facts(policy, [_row(1301, 744, "2026-10-26", "2026-10-28"), _row(1301, 744, "2026-10-26", "2026-10-29")], [(1301, "744")])
    assert dup[(1301, "744")].status == "CONFLICT" and dup[(1301, "744")].reasons[0].startswith("CS_ROW_CONFLICT")
    inactive = _facts(policy, [_row(1301, 744, "2026-10-26", "2026-10-28", active=False)], [(1301, "744")])
    assert inactive[(1301, "744")].reasons[0].startswith("CS_WINDOW_GAP")              # inactive rows never fill a gap
    neighbour = _facts(policy, [_row(1301, 745, "2026-10-26", "2026-10-28")], [(1301, "744")])
    assert neighbour[(1301, "744")].status == "GAP"                                    # no neighbouring-station substitute


def test_a24_missing_date_conversion_or_timezone_blocks(policy) -> None:
    rows = [_row(1301, 744, "2026-10-26", "2026-10-28")]
    no_instant = policy.with_overrides({"cs.date_instant": None}, new_id="cs-no-instant", reason="A24")
    f = _facts(no_instant, rows, [(1301, "744")])[(1301, "744")]
    assert f.status == "POLICY_MISSING" and "date-to-UTC convention None is not admitted" in f.reasons[0]
    no_mode = policy.with_overrides({"cs.date_mode": None}, new_id="cs-no-mode", authority="open_decision", reason="A24")
    assert _facts(no_mode, rows, [(1301, "744")])[(1301, "744")].status == "POLICY_MISSING"
    no_tz = _facts(policy, rows, [(1301, "744")], tz=None)[(1301, "744")]
    assert no_tz.status == "POLICY_MISSING" and no_tz.reasons[0].startswith("CS_SOURCE_TIMEZONE_MISSING")


def test_a24_policy_gap_blocks_jobs_in_the_snapshot(demo) -> None:
    services, outcome, _ = demo
    no_instant = services.policy.with_overrides({"cs.date_instant": None}, new_id="rebuild-v1-cs-gap", reason="A24")
    snap = snapshot_for_run(services, outcome.run_id, policy=no_instant)
    assert all(f.status == "POLICY_MISSING" for f in snap.cs_facts)
    reasons = {d.job: d.reasons for d in snap.dispositions}
    open_jobs = [j for j in snap.jobs if j.status != "COMPLETE"]
    assert open_jobs and all(any(r.startswith("CS_CONVERSION_POLICY_MISSING") for r in reasons[j.id]) for j in open_jobs)
