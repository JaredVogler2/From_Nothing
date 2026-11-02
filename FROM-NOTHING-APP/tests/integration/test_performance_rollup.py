"""Performance over any period on the observed demo world (D-25): rows, job records, rollups, corrections, rebuild, API, retention.

The world is the published 2026-10-26 plan observed at the three S18 collection points (PROD-B and QA are revised once).

* every evaluation writes its team-shift's light row and its job record, and the job record adds up to the score;
* a team, a superintendent and the business unit over any period equal the pooled canonical evaluation of the same members;
* a correction after the org chart changed keeps the superintendent the row was first written with;
* performance-ledger rebuilds the same rows and job records, and names a job once the graph knows it;
* both read endpoints check scope (a team before any read, a group only when all its teams are allowed) and parameters;
* detail retention deletes per-job evidence only where the job record carries the score; scores, rows, job records,
  rollups and recognition stay, and a day without job records holds the watermark until they exist.
"""
from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.codec import parse_utc
from focus.config import Settings
from focus.data.fixture_builder import build_observations
from focus.data.org import build_org_chart
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.performance import history
from focus.performance import rollup as R
from focus.performance.actuals import FixtureObservationReader, collect_observations
from focus.performance.details import STATE, WATERMARK, run_detail_retention
from focus.performance.score import aggregate, canonical, evaluate_shift
from focus.runtime import build_services
from tests.conftest import OBSERVATION_SHIFT as S1, login, observed_world

P1, P2, P3 = "2026-10-26T19:30:00Z", "2026-10-27T16:00:00Z", "2026-10-28T16:00:00Z"
DAY = date(2026, 10, 26)
TEAMS = ("PROD-A", "PROD-B", "QA")
SUPERINTENDENTS = {"PROD-A": "DEMO-SUPT-PROD", "PROD-B": "DEMO-SUPT-PROD", "QA": "DEMO-SUPT-QA"}   # FOCUS_PERSON_TEAM_MAP
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"
LATER = datetime(2027, 2, 1, 18, tzinfo=UTC)          # 2026-10-26 is more than 90 days back


@pytest.fixture(scope="module")
def template(tmp_path_factory, app_root):
    return observed_world(tmp_path_factory.mktemp("rollup") / "rt", app_root, points=(P1, P2, P3))


def services_on(root: Path, app_root: Path, at: datetime = FIXTURE_CUTOFF):
    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False)
    clock = FixedClock(at)
    return build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                          clock=clock)


@pytest.fixture()
def world(template, tmp_path, app_root):
    root = tmp_path / "rt"
    shutil.copytree(template.root, root)
    return SimpleNamespace(root=root, services=services_on(root, app_root), t=template)


def client_on(root: Path, app_root: Path, persona: str, at: datetime = FIXTURE_CUTOFF):
    from focus.web.app import create_app

    app = create_app(services=services_on(root, app_root, at))
    app.config["TESTING"] = True
    client = app.test_client()
    login(client, persona)
    return client


def rows(services, day: date = DAY) -> dict:
    return services.repositories.get_state(R.LEDGER, day.isoformat())[0]["rows"]


def job_doc(services, day: date = DAY) -> dict:
    return services.repositories.get_state(R.JOBS, day.isoformat())[0]


def minutes(record: dict, outcome: str) -> int:
    return sum(j[4] for j in record["jobs"] if j[5] == outcome)


def components(result: dict) -> list[Fraction]:
    return [Fraction(result[k]) for k in ("B0", "B", "E", "Cp", "U", "O")]


def void_a1(services) -> datetime:
    """The late correction of A62: A1's completion was recorded in error, so PROD-A's score gets a revision."""
    life = build_observations()["lifecycle_events"]
    late = dict(life["rows"][1], SOURCE_EVENT_KEY="EV-A1-DONE-VOID", SOURCE_SEQUENCE=50, KIND="VOIDED", PHASE_KIND="WORK", VERIFIED=False,
                VERIFIED_BY=None, SUPERSEDES_EVENT_KEY="EV-A1-DONE", EFFECTIVE_AT_UTC="2026-10-29T10:00:00Z", RECORDED_AT_UTC="2026-10-29T10:00:00Z",
                OBSERVED_AT_UTC="2026-10-29T10:05:00Z", EVIDENCE_REF="FIXTURE-VOID-A1", DETAIL="late correction: A1 completion recorded in error")
    at = parse_utc("2026-10-29T12:00:00Z")
    collect_observations(services, as_of=at, reader=FixtureObservationReader(INPUTS, overrides={"lifecycle_events": dict(life, rows=life["rows"] + [late])}))
    return at


# ---------------------------------------------------------------------- writing

def test_every_evaluation_writes_a_row_and_a_job_record_that_add_up_to_its_score(world) -> None:
    from focus.performance.lifecycle import current_graph

    services = world.services
    ledger, doc = rows(services), job_doc(services)
    assert set(ledger) == set(doc["rows"]) == {f"{t}|{S1}" for t in TEAMS}
    assert services.repositories.store.list_state_ids(R.LEDGER) == services.repositories.store.list_state_ids(R.JOBS) == [DAY.isoformat()]
    for team in TEAMS:
        e = canonical(services, team, S1)
        row, record = ledger[f"{team}|{S1}"], doc["rows"][f"{team}|{S1}"]
        assert row["evaluation_id"] == record["evaluation_id"] == e.id and row["revision"] == record["revision"] == e.revision
        assert components(row) == [e.B0, e.B, e.E, e.Cp, e.U, e.O] and row["status"] == e.status.value
        assert row["superintendent"] == record["superintendent"] == SUPERINTENDENTS[team] and row["superintendent_note"] is None
        assert row["business_unit"] == services.settings.scope_key and record["no_plan"] is False
        # the job record adds up to the score: planned minutes to B (as frozen to B0), done to Cp, excused to E, unplanned to U
        assert sum(j[4] for j in record["jobs"]) == e.B and sum(j[4] for j in record["jobs"] if j[8] == "planned") == e.B0
        assert (minutes(record, "done"), minutes(record, "excused"), sum(u[4] for u in record["unplanned"])) == (e.Cp, e.E, e.U)
        assert len(record["jobs"]) == len(history.evidence(services, e.evidence_hash).unit_facts)
    assert [ledger[f"{t}|{S1}"]["revision"] for t in TEAMS] == [1, 2, 2]                    # rows follow the latest revision
    assert doc["names"]["A1"] == "Install wing harness" and doc["names"]["ROOT3"] == "Install floor beam fittings"
    # the emergency addition after the cutoff is not in the graph yet: it keeps its job id
    added = [j for j in doc["rows"][f"PROD-B|{S1}"]["jobs"] if j[8] == "added"]
    assert len(added) == 1 and added[0][0] is None and added[0][9] not in current_graph(services).job_names and added[0][5] == "done"
    # adherence was 61.5 for QA: which jobs were done as planned and which were not, and the causes claimed
    qa = R.job_patterns(services, level="team", node="QA", start=DAY, end=DAY, shift_number=1, group="job_aircraft")
    assert {(j["soi"], j["kind"]) for j in qa["jobs"] if j["count"]["done"]} == {("NC3", "FINAL_QA"), ("PU3", "FINAL_QA")}
    assert {(j["soi"], j["kind"]) for j in qa["jobs"] if j["count"]["missed"]} == {("ROOT3", "IP_QA"), ("ROOT3", "FINAL_QA"),
                                                                                   ("ROOT3", "OTHER_BUYOFF")}
    assert qa["jobs"][0]["soi"] == "ROOT3" and "CROSS_TEAM_PREDECESSOR" in qa["jobs"][0]["causes"] and qa["jobs"][0]["missed_on"] == [1303]


# ---------------------------------------------------------------------- reading

def test_every_level_and_period_equals_the_pooled_canonical_evaluation(world) -> None:
    services = world.services
    at = parse_utc(P3)
    goals = {t: canonical(services, t, S1) for t in TEAMS}

    def same(result: dict, members: list, label: str) -> None:
        pool = aggregate(services, members, label=label, as_of=at)
        o = result["official"]
        assert components(o) == [pool.B0, pool.B, pool.E, pool.Cp, pool.U, pool.O]
        assert (Fraction(o["final"]), o["score"], o["grade"], o["status"]) == (pool.final, str(pool.score), pool.grade, pool.status.value)

    bu = R.rollup(services, level="business_unit", node=None, start=DAY, end=DAY)
    same(bu, list(goals.values()), f"FACTORY {S1}")
    assert bu["teams"] == list(TEAMS) and bu["node"] == services.settings.scope_key and bu["unscored"] == 0
    assert Fraction(bu["official"]["final"]) != sum(g.final for g in goals.values()) / 3        # never the average of team scores
    assert [c["node"] for c in bu["children"]] == ["DEMO-SUPT-PROD", "DEMO-SUPT-QA"]
    for supt in ("DEMO-SUPT-PROD", "DEMO-SUPT-QA"):
        members = [goals[t] for t in TEAMS if SUPERINTENDENTS[t] == supt]
        one = R.rollup(services, level="superintendent", node=supt, start=DAY, end=DAY)
        same(one, members, f"SUPERINTENDENT {supt} {S1}")
        assert next(c for c in bu["children"] if c["node"] == supt)["official"] == one["official"]
        assert [c["node"] for c in one["children"]] == [t for t in TEAMS if SUPERINTENDENTS[t] == supt]
    for team in TEAMS:
        one = R.rollup(services, level="team", node=team, start=DAY, end=DAY)
        assert one["official"]["score"] == str(goals[team].score) and Fraction(one["official"]["final"]) == goals[team].final
        assert [c["node"] for c in one["children"]] == [S1] and one["spread"] is None
    spread = bu["spread"]
    scores = {t: goals[t].score for t in TEAMS}
    assert {t["team"]: t["score"] for t in spread["teams"]} == {t: str(s) for t, s in scores.items()}
    assert spread["on_target"] == sum(1 for s in scores.values() if s >= 80)
    assert (spread["lowest"]["team"], spread["highest"]["team"]) == (min(scores, key=scores.get), max(scores, key=scores.get))
    assert spread["range"] == str(max(scores.values()) - min(scores.values()))
    for period in ("shift", "day", "week", "rolling30", "month", "quarter", "year"):     # every period holding the day
        first, last = R.period_range(period, DAY)
        other = R.rollup(services, level="business_unit", node=None, start=first, end=last, shift_number=1 if period == "shift" else None, by="day")
        assert other["official"] == bu["official"] and [b["bucket"] for b in other["series"]] == [DAY.isoformat()]
    assert R.rollup(services, level="business_unit", node=None, start=DAY, end=DAY, shift_number=2)["team_shifts"] == 0
    assert R.rollup(services, level="business_unit", node=None, start=date(2026, 10, 27), end=date(2026, 10, 31))["official"] is None
    # the jobs behind each group add up to its score, and job adherence is the score's adherence
    for level, node, result in (("business_unit", None, bu),
                                ("superintendent", "DEMO-SUPT-PROD", R.rollup(services, level="superintendent", node="DEMO-SUPT-PROD", start=DAY, end=DAY))):
        jobs = R.job_patterns(services, level=level, node=node, start=DAY, end=DAY)
        o, m = result["official"], jobs["totals"]["minutes"]
        assert (m["done"], m["excused"], m["unplanned"]) == (Fraction(o["Cp"]), Fraction(o["E"]), Fraction(o["U"]))
        assert sum(m[k] for k in ("done", "missed", "excused", "open")) == Fraction(o["B"])
        assert jobs["totals"]["adherence_from_jobs"] == o["adherence"] and jobs["team_shifts"] == result["team_shifts"]


# ---------------------------------------------------------------------- corrections and rebuild

def test_a_correction_after_the_org_chart_changed_keeps_the_first_superintendent(world, monkeypatch) -> None:
    services = world.services
    moved = build_org_chart([("DEMO-A1-01", "PROD-A", "DEMO-SUPT-NEW", datetime(2026, 1, 1, tzinfo=UTC), None)], source_run_id="SR-MOVED",
                            scope=services.settings.scope_key)
    monkeypatch.setattr(R, "latest_org_chart", lambda services: moved)
    before = canonical(services, "PROD-A", S1)
    revised = evaluate_shift(services, "PROD-A", S1, as_of=void_a1(services))
    assert revised.revision == before.revision + 1 and revised.Cp == 0
    row, record = rows(services)[f"PROD-A|{S1}"], job_doc(services)["rows"][f"PROD-A|{S1}"]
    assert row["evaluation_id"] == record["evaluation_id"] == revised.id and (row["revision"], row["Cp"]) == (2, "0")
    assert row["superintendent"] == record["superintendent"] == "DEMO-SUPT-PROD"             # the numbers change, never the attribution
    assert [j[5] for j in record["jobs"]] == ["missed"] and minutes(record, "done") == 0
    prod = R.rollup(services, level="superintendent", node="DEMO-SUPT-PROD", start=DAY, end=DAY)
    assert prod["teams"] == ["PROD-A", "PROD-B"] and Fraction(prod["official"]["Cp"]) == canonical(services, "PROD-B", S1).Cp
    assert R.rollup(services, level="superintendent", node="DEMO-SUPT-NEW", start=DAY, end=DAY)["team_shifts"] == 0


def test_performance_ledger_rebuilds_the_same_rows_and_job_records(world, monkeypatch) -> None:
    from focus.performance import lifecycle

    services = world.services
    ledger, doc = rows(services), job_doc(services)
    for collection in (R.LEDGER, R.JOBS):
        shutil.rmtree(world.root / "state" / collection)
    assert services.repositories.get_state(R.LEDGER, DAY.isoformat()) is None
    out = R.rebuild(services)                          # at the fixture clock, before any observation: claims are read as of each score's cutoff
    assert (out["team_shifts"], out["written"], out["job_records_written"], out["job_records_without_evidence"]) == (3, 3, 3, 0)
    assert rows(services) == ledger and job_doc(services) == doc
    assert R.rebuild(services)["written"] == R.rebuild(services)["job_records_written"] == 0   # finished records are left alone
    # once a snapshot names the emergency addition, a rebuild fills in its shop order id, aircraft and name
    job = next(j for j in doc["rows"][f"PROD-B|{S1}"]["jobs"] if j[8] == "added")[9]
    graph = lifecycle.current_graph(services)
    named = replace(graph, job_names={**graph.job_names, job: ("EMRG-7", 1303, "Emergency fastener rework")})
    monkeypatch.setattr(lifecycle, "current_graph", lambda services: named)
    assert R.rebuild(services)["job_records_written"] == 1
    again = job_doc(services)
    added = next(j for j in again["rows"][f"PROD-B|{S1}"]["jobs"] if j[8] == "added")
    assert (added[0], added[1], added[9]) == ("EMRG-7", 1303, None) and again["names"]["EMRG-7"] == "Emergency fastener rework"
    assert again["rows"][f"PROD-B|{S1}"]["evaluation_id"] == doc["rows"][f"PROD-B|{S1}"]["evaluation_id"]
    # a lost ledger takes the org chart's superintendent as of each shift start again; the job records follow the rows
    shutil.rmtree(world.root / "state" / R.LEDGER)
    moved = build_org_chart([("DEMO-A1-01", "PROD-A", "DEMO-SUPT-NEW", datetime(2026, 1, 1, tzinfo=UTC), None)], source_run_id="SR-MOVED",
                            scope=services.settings.scope_key)
    monkeypatch.setattr(R, "latest_org_chart", lambda services: moved)
    R.rebuild(services)
    assert rows(services)[f"PROD-A|{S1}"]["superintendent"] == job_doc(services)["rows"][f"PROD-A|{S1}"]["superintendent"] == "DEMO-SUPT-NEW"


# ---------------------------------------------------------------------- the API

def test_both_endpoints_check_scope_and_parameters(world, app_root) -> None:
    services = world.services
    manager = client_on(world.root, app_root, "manager")
    env = manager.get("/api/v1/performance/rollup?level=business_unit&period=week&by=day").get_json()
    expected = R.rollup(services, level="business_unit", node=None, start=date(2026, 10, 26), end=date(2026, 11, 1), by="day")
    assert env["status"] == "ready" and env["data"]["official"] == expected["official"] and env["data"]["period"]["anchor"] == DAY.isoformat()
    assert env["data"]["choices"] == {"teams": list(TEAMS), "superintendents": ["DEMO-SUPT-PROD", "DEMO-SUPT-QA"],
                                      "business_unit": services.settings.scope_key}
    jobs = manager.get("/api/v1/performance/jobs?level=business_unit&period=month&outcome=missed").get_json()
    assert jobs["status"] == "ready" and all(j["count"]["missed"] for j in jobs["data"]["jobs"])
    assert jobs["data"]["totals"]["adherence_from_jobs"] == env["data"]["official"]["adherence"]
    lead = client_on(world.root, app_root, "lead-prod-a")
    own = lead.get("/api/v1/performance/rollup?level=team&period=week").get_json()
    assert own["status"] == "ready" and own["data"]["node"] == "PROD-A" and own["data"]["teams"] == ["PROD-A"]
    assert own["data"]["choices"] == {"teams": ["PROD-A"], "superintendents": [], "business_unit": None}   # PROD-B is not theirs
    mine = lead.get("/api/v1/performance/jobs?level=team&period=shift&shift=1&group=job_aircraft").get_json()
    assert mine["status"] == "ready" and [(j["soi"], j["line"]) for j in mine["data"]["jobs"]] == [("B1", 1302), ("B2", 1302), ("A1", 1301)]
    for path in ("/api/v1/performance/rollup", "/api/v1/performance/jobs"):
        for query in ("level=team&node=PROD-B", "level=superintendent&node=DEMO-SUPT-PROD", "level=business_unit"):
            refused = lead.get(f"{path}?{query}&period=week")
            assert refused.status_code == 403 and refused.get_json()["error"]["code"] == "OUT_OF_SCOPE", (path, query)
        for query, code in (("period=fortnight", "INVALID_PARAMETER"), ("level=county", "INVALID_PARAMETER"),
                            ("period=custom&from=2026-10-27&to=2026-10-26", "BAD_PERIOD"), ("period=custom&from=2025-01-01&to=2026-10-26", "BAD_PERIOD"),
                            ("period=custom&from=2026-10-26", "BAD_PERIOD"), ("shift=4&period=shift", "INVALID_PARAMETER")):
            bad = manager.get(f"{path}?{query}")
            assert bad.status_code == 400 and bad.get_json()["error"]["code"] == code, (path, query, bad.get_json())
    assert manager.get("/api/v1/performance/rollup?target=120").status_code == 400
    assert manager.get("/api/v1/performance/jobs?group=team").status_code == 400


# ---------------------------------------------------------------------- per-job details kept for the owner's period

def test_detail_retention_deletes_evidence_only_behind_a_job_record(world, template, tmp_path, app_root) -> None:
    from focus.analytics.progression import project

    services = world.services
    project(services, as_of=parse_utc(P3))                                                  # recognition awarded while details exist
    series = {t: history.revisions(services, services.settings.scope_key, canonical(services, t, S1).series_key) for t in TEAMS}
    hashes = {e.evidence_hash for revisions in series.values() for e in revisions}
    assert all(services.repositories.exists(history.EVIDENCE, h) for h in hashes)
    services.clock.set(LATER)
    # a store from before the job records: PROD-B's day holds a score with no job record, so its evidence stays
    found = services.repositories.get_state(R.JOBS, DAY.isoformat())
    doc = {**found[0], "rows": {k: v for k, v in found[0]["rows"].items() if not k.startswith("PROD-B|")}}
    services.repositories.put_state(R.JOBS, DAY.isoformat(), doc, found[1])
    report = run_detail_retention(services)
    assert (report["applied"], report["days"], report["team_shifts"], report["kept_without_job_record"]) == (False, 1, 2, 1)
    assert all(services.repositories.exists(history.EVIDENCE, h) for h in hashes)            # a report deletes nothing
    applied = run_detail_retention(services, apply=True)
    prod_b = {e.evidence_hash for e in series["PROD-B"]}
    assert applied["deleted"] == len(hashes - prod_b) and applied["receipt_id"]
    assert all(services.repositories.exists(history.EVIDENCE, h) == (h in prod_b) for h in hashes)
    assert services.repositories.get_state(STATE, WATERMARK) is None                         # the blocked day is read again next run
    assert R.rebuild(services)["job_records_written"] == 1                                  # its evidence still exists: the record is rebuilt
    applied = run_detail_retention(services, apply=True)
    assert applied["deleted"] == len(prod_b) and applied["kept_without_job_record"] == 0
    assert not any(services.repositories.exists(history.EVIDENCE, h) for h in hashes)
    assert services.repositories.get_state(STATE, WATERMARK)[0] == {"through": DAY.isoformat()}
    again = run_detail_retention(services, apply=True)
    assert (again["days"], again["details_to_delete"]) == (0, 0)                            # each day is handled once
    # scores, rows, job records and every rollup stay; the views say the details are gone
    assert all(history.get(services, e.id) == e for revisions in series.values() for e in revisions)
    ledger, jobs = rows(services), job_doc(services)
    assert [ledger[f"{t}|{S1}"]["evaluation_id"] for t in TEAMS] == [jobs["rows"][f"{t}|{S1}"]["evaluation_id"] for t in TEAMS]
    control = services_on(template.root, app_root)
    for level, node in (("business_unit", None), ("superintendent", "DEMO-SUPT-PROD"), ("team", "QA")):
        assert R.rollup(services, level=level, node=node, start=DAY, end=DAY) == R.rollup(control, level=level, node=node, start=DAY, end=DAY)
        assert (R.job_patterns(services, level=level, node=node, start=DAY, end=DAY)
                == R.job_patterns(control, level=level, node=node, start=DAY, end=DAY))
    assert R.rebuild(services)["job_records_without_evidence"] == 0                          # finished records need no evidence
    manager = client_on(world.root, app_root, "manager", LATER)
    perf = manager.get(f"/api/v1/performance?shift_id={S1}").get_json()
    assert "DETAILS_EXPIRED" in {r["code"] for r in perf["reasons"]}
    # recognition holds what it awarded: nothing is reversed or revoked for want of the deleted details
    held = project(services, as_of=LATER)
    copy = tmp_path / "control"
    shutil.copytree(template.root, copy)
    reference = services_on(copy, app_root)
    project(reference, as_of=parse_utc(P3))
    expected = project(reference, as_of=LATER)
    assert held.corrections == [] and [e.dedupe_key for e in held.appended] == [e.dedupe_key for e in expected.appended]
