"""P05: SQL-owned team inference, effective assignment precedence, pure membership (A08, A09, A10, A11)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from focus.data.fixture_reader import FixtureSourceReader
from focus.data.workforce import reference_inference
from focus.domain import AvailabilityBasis, PersonKey
from tests.conftest import acquire_demo

UTC = timezone.utc
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _person(native: str) -> str:
    return PersonKey(native, "fixture-demo").id


def _clock(person: str, team: str, start: str, end: str, voided: bool = False, key: str = "") -> dict:
    return {"SOURCE_EVENT_KEY": key or f"{person}-{start}", "LINE_NUMBER": 1, "NATIVE_SOI": "X", "PERSON_KEY": person, "EPISODE_KEY": "E",
            "TEAM": team, "REWORK_CLASS": None, "START_UTC": start, "END_UTC": end, "SOURCE_REVISION": "1", "IS_VOIDED": voided}


def test_a08_tie_broken_by_team_name_with_flags_and_window() -> None:
    w0, w1 = datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC)
    rows = [
        _clock("P1", "PROD-B", "2026-10-01T10:00:00Z", "2026-10-01T12:00:00Z"),
        _clock("P1", "PROD-B", "2026-10-02T10:00:00Z", "2026-10-02T12:00:00Z"),
        _clock("P1", "PROD-A", "2026-10-03T10:00:00Z", "2026-10-03T10:30:00Z"),
        _clock("P1", "PROD-A", "2026-10-04T10:00:00Z", "2026-10-04T10:30:00Z"),
        _clock("P1", "PROD-A", "2026-10-05T10:00:00Z", "2026-10-05T10:30:00Z", voided=True),      # voided: not counted
        _clock("P1", "PROD-B", "2026-09-01T10:00:00Z", "2026-09-01T12:00:00Z"),                   # outside window
        _clock("P2", "PROD-C", "2026-10-06T10:00:00Z", "2026-10-06T11:00:00Z"),
    ]
    result = reference_inference(rows, "RUN-1", w0, w1)
    p1 = result["P1"]
    assert p1["team"] == "PROD-A"            # tie 2-2 broken by team name ascending, not by minutes
    assert p1["is_tie"] is True and p1["n"] == 2 and p1["runner_up_team"] == "PROD-B" and p1["runner_up_count"] == 2
    assert p1["top_share"] == Decimal("0.50000000")
    assert p1["effort"] == Decimal(60)       # count and effort stay separately labeled
    assert result["P2"]["team"] == "PROD-C" and result["P2"]["is_tie"] is False


def test_a08_sql_text_is_the_authority(app_root) -> None:
    sql = (app_root / "queries" / "workforce" / "infer_mechanic_teams.sql").read_text()
    assert "ORDER BY n DESC,Team ASC" in sql and "IsVoided=0" in sql
    assert "INSERT INTO WEB_APP.FOCUS_FN_TeamInference" in sql
    assert "StartUtc >= @WindowStart AND StartUtc < @WindowEnd" in sql


def test_a08_persisted_sql_rows_consumed_with_tie_and_fallback_labels(demo_snapshot) -> None:
    inference = {r.person: r for r in demo_snapshot.inference}
    tie = inference[_person("DEMO-B2-04")]
    assert tie.is_tie and tie.inferred_team == "PROD-B" and tie.runner_up_team == "PROD-C" and tie.top_share == Decimal("0.50000000")
    fallback = inference[_person("DEMO-Q1-04")]
    assert fallback.basis == "DEPT_FALLBACK" and fallback.evidence_count == 0
    assert all(r.algorithm_version == "S07-plurality-v1" for r in demo_snapshot.inference)


def test_a09_assignment_overrides_inference(demo_snapshot) -> None:
    inference = {r.person: r for r in demo_snapshot.inference}
    a108 = inference[_person("DEMO-A1-08")]
    assert a108.inferred_team == "PROD-B" and a108.effective_team == "PROD-A" and a108.effective_basis == "EFFECTIVE_ASSIGNMENT"
    pools = {(p.key.team, p.key.start_date, p.key.shift_number): p for p in demo_snapshot.pools}
    assert _person("DEMO-A1-08") in pools[("PROD-A", date(2026, 10, 27), 1)].executable_members
    assert _person("DEMO-A1-08") not in pools[("PROD-B", date(2026, 10, 27), 1)].members


def _overrides(mutator) -> dict:
    out = {}
    for name in ("person_team_map", "shift_roster", "team_inference", "attendance_shift"):
        out[name] = json.loads((INPUTS / f"{name}.json").read_text())
    mutator(out)
    return out


def test_a09_assignment_moves_team_at_exact_instant_and_inference_is_nominal(services_factory) -> None:
    def mutate(d: dict) -> None:
        for name, key in (("person_team_map", "BEMS_ID"), ("shift_roster", "PERSON_KEY")):
            rows = [r for r in d[name]["rows"] if r[key] != "DEMO-B1-01"]
            d[name]["rows"] = rows
        base_assign = {"ROLE": "MECH", "IN_SCOPE": True, "BASIS": "ASSIGNED", "RUN_ID": "T", "EVIDENCE_COUNT": 0, "LOOKBACK_DAYS": 30,
                       "LOAD_TIMESTAMP": "2026-10-25T17:30:00.000", "BEMS_ID": "DEMO-B1-01"}
        d["person_team_map"]["rows"] += [
            dict(base_assign, TEAM="PROD-B", EFFECTIVE_FROM="2026-01-01T00:00:00", EFFECTIVE_TO="2026-10-28T00:00:00"),
            dict(base_assign, TEAM="PROD-A", EFFECTIVE_FROM="2026-10-28T00:00:00", EFFECTIVE_TO=None),
        ]
        base_roster = {"PERSON_KEY": "DEMO-B1-01", "ROLE": "MECH", "SITE": "DEMO", "SHIFT_NUMBER": 1, "BASIS": "SCHEDULED_ROSTER", "EVIDENCE_REF": "T"}
        d["shift_roster"]["rows"] += [dict(base_roster, TEAM="PROD-B", EFFECTIVE_FROM="2026-01-01", EFFECTIVE_TO="2026-10-28"),
                                      dict(base_roster, TEAM="PROD-A", EFFECTIVE_FROM="2026-10-28", EFFECTIVE_TO=None)]
        # an inference-only person: rostered but with no effective assignment
        d["person_team_map"]["rows"] = [r for r in d["person_team_map"]["rows"] if r["BEMS_ID"] != "DEMO-B1-03"]

    services = services_factory()
    outcome = acquire_demo(services, key="a09", reader=FixtureSourceReader(INPUTS, overrides=_overrides(mutate)))
    from focus.data.acquisition import load_snapshot

    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    pools = {(p.key.team, p.key.start_date, p.key.shift_number): p for p in snap.pools}
    b101 = _person("DEMO-B1-01")
    assert b101 in pools[("PROD-B", date(2026, 10, 27), 1)].executable_members
    assert b101 not in pools[("PROD-A", date(2026, 10, 27), 1)].members
    assert b101 in pools[("PROD-A", date(2026, 10, 28), 1)].executable_members      # moved at the exact effective date
    assert b101 not in pools[("PROD-B", date(2026, 10, 28), 1)].members
    b103 = _person("DEMO-B1-03")
    pool = pools[("PROD-B", date(2026, 10, 26), 1)]
    assert b103 in pool.forecast_members and b103 not in pool.executable_members     # nominal, not present capacity
    av = next(a for a in snap.availability if a.person == b103 and a.pool == pool.id)
    assert av.basis is AvailabilityBasis.INFERRED_NOMINAL and av.admitted_presence is False


def test_a10_duplicates_dedupe_and_conflicts_fail_admission(services_factory) -> None:
    def mutate(d: dict) -> None:
        dup = next(r for r in d["person_team_map"]["rows"] if r["BEMS_ID"] == "DEMO-A1-02")
        d["person_team_map"]["rows"].append(dict(dup))                       # true repeat
        d["shift_roster"]["rows"].append(dict(next(r for r in d["shift_roster"]["rows"] if r["PERSON_KEY"] == "DEMO-A1-02")))
        conflict = dict(next(r for r in d["person_team_map"]["rows"] if r["BEMS_ID"] == "DEMO-A1-04"))
        conflict["TEAM"] = "PROD-B"                                           # overlapping inconsistent membership
        d["person_team_map"]["rows"].append(conflict)

    services = services_factory()
    outcome = acquire_demo(services, key="a10", reader=FixtureSourceReader(INPUTS, overrides=_overrides(mutate)))
    from focus.data.acquisition import load_snapshot

    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    pool = next(p for p in snap.pools if p.key.team == "PROD-A" and p.key.start_date == date(2026, 10, 27) and p.key.shift_number == 1)
    assert pool.executable_members.count(_person("DEMO-A1-02")) == 1                # no double headcount
    assert _person("DEMO-A1-04") not in pool.members                               # conflict excluded
    assert pool.n_executable == 7 and pool.cap_executable == 5
    assert any(g.code == "WORKFORCE_CONFLICT" and g.scope == "DEMO-A1-04" for g in snap.gaps)
    assert not any(_person("DEMO-A1-04") in p.members for p in snap.pools)


def test_a11_no_cross_team_or_cross_shift_borrowing(demo_snapshot) -> None:
    pools = {(p.key.team, p.key.start_date, p.key.shift_number): p for p in demo_snapshot.pools}
    prod_b = pools[("PROD-B", date(2026, 10, 27), 1)]
    a105 = _person("DEMO-A1-05")                         # holds cert B but is PROD-A S1
    assert a105 not in prod_b.members
    assert set(prod_b.members) == {_person(f"DEMO-B1-0{i}") for i in range(1, 5)}
    assert a105 not in pools[("PROD-A", date(2026, 10, 27), 2)].members              # no cross-shift loan
    for p in demo_snapshot.pools:
        if not p.synthetic:
            assert all(m in demo_snapshot_people_of_team(demo_snapshot, p.key.team) for m in p.members)


def demo_snapshot_people_of_team(snapshot, team: str) -> set[str]:
    prefix = {"PROD-A": "DEMO-A", "PROD-B": "DEMO-B", "QA": "DEMO-Q"}.get(team, "")
    return {p.id for p in snapshot.people if p.key.native_id.startswith(prefix)}


def test_a08_sql_contract_fixture_matches_hand_derived_rows(app_root) -> None:
    from focus.codec import parse_utc

    contract = json.loads((app_root / "fixtures" / "workforce" / "team_inference_contract.json").read_text())
    got = reference_inference(contract["clock_rows"], contract["run_id"], parse_utc(contract["window_start_utc"]),
                              parse_utc(contract["window_end_utc"]), departments=contract["hr_departments"],
                              dept_team=contract["dept_team_skill_ref"])
    assert sorted(got) == sorted(r["PERSON_KEY"] for r in contract["expected"])   # P600: unmapped, no evidence -> no row
    for want in contract["expected"]:
        row = got[want["PERSON_KEY"]]
        assert row["basis"] == want["BASIS"] and row["team"] == want["INFERRED_TEAM"], want["PERSON_KEY"]
        assert row["n"] == want["EVIDENCE_COUNT"] and row["total_n"] == want["TOTAL_COUNT"]
        assert row["effort"] == Decimal(want["EFFORT_MINUTES"])
        assert row["top_share"] == (Decimal(want["TOP_SHARE"]) if want["TOP_SHARE"] else None)
        assert row["is_tie"] is want["IS_TIE"]
        assert (row["runner_up_team"], row["runner_up_count"]) == (want["RUNNER_UP_TEAM"], want["RUNNER_UP_COUNT"])
        for field, key in (("first_evidence_utc", "FIRST_EVIDENCE_UTC"), ("last_evidence_utc", "LAST_EVIDENCE_UTC")):
            assert row[field] == (parse_utc(want[key]) if want[key] else None)
        assert row["algorithm_version"] == contract["algorithm_version"]


def test_a08_sql_text_carries_contract_semantics(app_root) -> None:
    sql = (app_root / "queries" / "workforce" / "infer_mechanic_teams.sql").read_text()
    assert "TRY_CONVERT(bigint, c.SourceRevision) DESC" in sql                     # latest correction wins
    assert "Latin1_General_BIN2" in sql                                            # collation-independent tie-break
    assert "MAX(EndUtc) OVER" in sql and "StartUtc > PriorMaxEnd" in sql           # running-max union for effort
    assert "FOCUS_DEPT_TEAM_SKILL_REF" in sql and "'DEPT_FALLBACK'" in sql and "'UNKNOWN'" in sql
    assert "FOCUS_MECH_TEAM_INFERRED" in sql and "legacy_team" in sql              # legacy is evidence only
    assert "Status = 'CAPTURING'" in sql and "BEGIN TRANSACTION" in sql and "COMMIT TRANSACTION" in sql
    assert "DELETE FROM WEB_APP.FOCUS_FN_TeamInference WHERE RunId = @RunId" in sql  # run-isolated refresh


def test_a09_attendance_absent_is_not_capacity_even_with_inference(demo_snapshot) -> None:
    a103 = _person("DEMO-A1-03")
    record = next(r for r in demo_snapshot.inference if r.person == a103)
    assert record.inferred_team == "PROD-A" and record.effective_basis == "EFFECTIVE_ASSIGNMENT"
    pools = {(p.key.team, p.key.start_date, p.key.shift_number): p for p in demo_snapshot.pools}
    today = pools[("PROD-A", date(2026, 10, 26), 1)]
    assert a103 not in today.members and f"{a103}:ABSENT" in today.excluded
    assert a103 in pools[("PROD-A", date(2026, 10, 27), 1)].members                # absence is per production day
    assert not any(a.person == a103 and a.pool == today.id for a in demo_snapshot.availability)


def _move_b101(effective_from_local: str):
    def mutate(d: dict) -> None:
        d["person_team_map"]["rows"] = [r for r in d["person_team_map"]["rows"] if r["BEMS_ID"] != "DEMO-B1-01"]
        d["shift_roster"]["rows"] = [r for r in d["shift_roster"]["rows"] if r["PERSON_KEY"] != "DEMO-B1-01"]
        base = {"ROLE": "MECH", "IN_SCOPE": True, "BASIS": "ASSIGNED", "RUN_ID": "T", "EVIDENCE_COUNT": 0, "LOOKBACK_DAYS": 30,
                "LOAD_TIMESTAMP": "2026-10-25T17:30:00.000", "BEMS_ID": "DEMO-B1-01"}
        d["person_team_map"]["rows"] += [dict(base, TEAM="PROD-B", EFFECTIVE_FROM="2026-01-01T00:00:00", EFFECTIVE_TO=effective_from_local),
                                         dict(base, TEAM="PROD-A", EFFECTIVE_FROM=effective_from_local, EFFECTIVE_TO=None)]
        roster = {"PERSON_KEY": "DEMO-B1-01", "ROLE": "MECH", "SITE": "DEMO", "SHIFT_NUMBER": 1, "BASIS": "SCHEDULED_ROSTER", "EVIDENCE_REF": "T"}
        d["shift_roster"]["rows"] += [dict(roster, TEAM="PROD-B", EFFECTIVE_FROM="2026-01-01", EFFECTIVE_TO="2026-10-28"),
                                      dict(roster, TEAM="PROD-A", EFFECTIVE_FROM="2026-10-28", EFFECTIVE_TO=None)]
    return mutate


def _snapshot(services_factory, key: str, mutate):
    from focus.data.acquisition import load_snapshot

    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=_overrides(mutate)))
    assert outcome.status == "COMPLETE", outcome.rejection
    return load_snapshot(services.repositories, outcome.snapshot_id)


def test_a09_effective_instant_is_the_shift_start_boundary(services_factory) -> None:
    b101 = _person("DEMO-B1-01")
    # S1 starts 06:00 local: an assignment effective exactly then governs that shift (inclusive start)
    on_time = _snapshot(services_factory, "a09-0600", _move_b101("2026-10-28T06:00:00"))
    pools = {(p.key.team, p.key.start_date, p.key.shift_number): p for p in on_time.pools}
    assert b101 in pools[("PROD-A", date(2026, 10, 28), 1)].members
    assert not any(g.code == "WORKFORCE_CONFLICT" and g.scope == "DEMO-B1-01" for g in on_time.gaps)
    # one minute later the 10-28 S1 shift still belongs to the old assignment, which disagrees with the
    # roster move: an admission conflict, so the person is excluded everywhere (no double booking)
    late = _snapshot(services_factory, "a09-0601", _move_b101("2026-10-28T06:01:00"))
    conflict = next(g for g in late.gaps if g.code == "WORKFORCE_CONFLICT" and g.scope == "DEMO-B1-01")
    assert "ROSTER_ASSIGNMENT_MISMATCH" in conflict.detail and "2026-10-28 S1" in conflict.detail
    assert not any(b101 in p.members for p in late.pools)                          # including dates before the mismatch


def test_p05_stale_inference_is_labelled_and_never_a_home_team(services_factory) -> None:
    def mutate(d: dict) -> None:
        d["person_team_map"]["rows"] = [r for r in d["person_team_map"]["rows"] if r["BEMS_ID"] != "DEMO-B1-03"]
        for r in d["team_inference"]["rows"]:
            if r["PERSON_KEY"] == "DEMO-B1-03":
                r["WINDOW_START_UTC"], r["WINDOW_END_UTC"] = "2026-08-18T10:00:00Z", "2026-09-17T10:00:00Z"
    snap = _snapshot(services_factory, "p05-stale", mutate)
    record = next(r for r in snap.inference if r.person == _person("DEMO-B1-03"))
    assert record.stale is True and record.effective_team is None and record.inferred_team == "PROD-B"
    assert any(g.code == "TEAM_INFERENCE_STALE" for g in snap.gaps)
    fresh = next(r for r in snap.inference if r.person == _person("DEMO-B1-04"))
    assert fresh.stale is False                                                     # 1 day before cutoff: within 7


def test_p05_undefined_roster_shift_is_a_gap_not_a_pool(services_factory) -> None:
    def mutate(d: dict) -> None:
        for r in d["shift_roster"]["rows"]:
            if r["PERSON_KEY"] == "DEMO-B1-04":
                r["SHIFT_NUMBER"] = 4
    snap = _snapshot(services_factory, "p05-shift4", mutate)
    assert any(g.code == "ROSTER_SHIFT_UNDEFINED" and g.scope == "DEMO-B1-04" for g in snap.gaps)
    assert not any(_person("DEMO-B1-04") in p.members for p in snap.pools)
