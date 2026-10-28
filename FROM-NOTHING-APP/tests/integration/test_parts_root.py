"""P09: every shortage kept, exact line/root gating and precision-aware part release floors (A29, A30)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from focus.data.acquisition import load_snapshot
from focus.data.fixture_reader import FixtureSourceReader
from focus.domain import ReleaseStatus
from tests.conftest import acquire_demo, job_id

UTC = timezone.utc
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _snapshot(services_factory, key, mutate, cutoff=None):
    data = {n: json.loads((INPUTS / f"{n}.json").read_text()) for n in ("etracs_active", "part_events")}
    mutate(data)
    services = services_factory()
    kwargs = {"cutoff": cutoff} if cutoff else {}
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=data), **kwargs)
    assert outcome.status == "COMPLETE", outcome.rejection
    return load_snapshot(services.repositories, outcome.snapshot_id)


def _shortage(record: int, line: int, used_on: str | None, ecd: str | None) -> dict:
    return {"ETRAC_CREATED_DT": "2026-10-20", "ETRAC_ECD": ecd, "ITEM_NUMBER": f"DEMO-P-{record}", "LINE_NUMBER": line,
            "LOAD_TIMESTAMP": "2026-10-25T17:30:00.000", "MINOR_MODEL_IDENT": "9", "REASON_CODE": "LP", "RECORD_ID": record, "TEAM": "PROD-A",
            "USED_ON": used_on}


def _event(record: int, event: str, effective: str, observed: str = "2026-10-26T08:00:00Z") -> dict:
    return {"EFFECTIVE_AT_UTC": effective, "EVENT": event, "EVIDENCE_REF": f"T-{event}-{record}", "OBSERVED_AT_UTC": observed, "RECORD_ID": record}


def _part_edges(snap):
    return [e for e in snap.edges if e.basis == "PART_RELEASE"]


def _start(snap, jid):
    return next(p.key.id for p in snap.phases if p.job == jid and p.kind.value == "WORK" and p.ordinal == 0)


def _executable(snap, jid):
    return next(d.executable_reasons for d in snap.dispositions if d.job == jid)


def test_a29_shortage_gates_used_on_job_root_and_descendants(demo_snapshot) -> None:
    child5, root5, next5 = (job_id(demo_snapshot, 1305, s) for s in ("CHILD5", "ROOT5", "NEXT5"))
    release = next(r for r in demo_snapshot.releases if r.id == "R:PART:50501")
    assert release.scope_keys == (child5, root5)                                        # exact child-to-root chain on line 1305
    gated = {e.succ_phase for e in _part_edges(demo_snapshot) if e.pred_phase == release.id}
    assert gated == {_start(demo_snapshot, child5), _start(demo_snapshot, root5)}
    assert any(r.startswith("PREDECESSOR_NOT_EXECUTABLE") for r in _executable(demo_snapshot, next5))   # descendant via the graph
    unresolved = next(s for s in demo_snapshot.shortages if s.record_id == 50502)
    assert unresolved.status == "UNRESOLVED_USED_ON" and unresolved.release_id is None            # kept, never ignored
    tst6 = job_id(demo_snapshot, 1306, "TST6")
    assert any(r.startswith("MATERIAL_SCOPE_UNRESOLVED") for r in _executable(demo_snapshot, tst6))
    a1 = job_id(demo_snapshot, 1301, "A1")
    assert not any(r.startswith("MATERIAL_SCOPE_UNRESOLVED") for r in _executable(demo_snapshot, a1))  # other lines unaffected


def test_a29_same_soi_on_another_line_multiple_and_unknown_shortages(services_factory) -> None:
    def mutate(d):
        d["etracs_active"]["rows"] += [_shortage(60001, 1301, "FGI", "2026-10-28"),           # FGI exists on 1301 and 1302
                                       _shortage(60002, 1305, "CHILD5", "2026-10-29"),        # a second, later shortage on CHILD5
                                       _shortage(60003, 1304, "PRE4", None)]                  # no ECD, no receipt

    snap = _snapshot(services_factory, "a29-multi", mutate)
    fgi1, fgi2 = job_id(snap, 1301, "FGI"), job_id(snap, 1302, "FGI")
    targets = {e.succ_phase for e in _part_edges(snap)}
    assert _start(snap, fgi1) in targets and _start(snap, fgi2) not in targets
    child5 = job_id(snap, 1305, "CHILD5")
    floors = sorted(r.forecast_at for r in snap.releases if child5 in r.scope_keys)
    assert floors == [datetime(2026, 10, 28, 4, tzinfo=UTC), datetime(2026, 10, 30, 4, tzinfo=UTC)]   # the latest release binds
    pre4, root4 = job_id(snap, 1304, "PRE4"), job_id(snap, 1304, "ROOT4")
    unknown = next(r for r in snap.releases if r.id == "R:PART:60003")
    assert unknown.status is ReleaseStatus.UNKNOWN and unknown.forecast_at is None
    pre4_reasons = next(d.reasons for d in snap.dispositions if d.job == pre4)
    assert any(r.startswith("PART_RELEASE_UNKNOWN") for r in pre4_reasons)
    assert any(r.startswith("PREDECESSOR_NOT_SCHEDULABLE") for r in next(d.reasons for d in snap.dispositions if d.job == root4))


def test_a30_date_only_ecd_floors_at_end_of_local_date(demo_snapshot) -> None:
    release = next(r for r in demo_snapshot.releases if r.id == "R:PART:50501")
    assert release.status is ReleaseStatus.CONDITIONAL and release.precision == "DATE"
    assert release.forecast_at == datetime(2026, 10, 28, 4, tzinfo=UTC)                 # next local midnight after ECD 10-27 (EDT)
    shortage = next(s for s in demo_snapshot.shortages if s.record_id == 50501)
    assert any(r.startswith("DATE_ONLY_ECD_FLOOR") for r in shortage.reasons)
    child5 = job_id(demo_snapshot, 1305, "CHILD5")
    assert any(r.startswith("PART_NOT_RECEIVED") for r in _executable(demo_snapshot, child5))   # receipt observed after the cutoff


def test_a30_timed_eta_is_exact_and_rounds_up_to_the_minute(services_factory) -> None:
    def mutate(d):
        d["part_events"]["rows"] = [_event(50501, "ETA", "2026-10-27T14:15:00Z")]
        d["etracs_active"]["rows"].append(_shortage(60004, 1301, "A1", "2026-10-27"))
        d["part_events"]["rows"].append(_event(60004, "ETA", "2026-10-27T14:15:30Z"))

    snap = _snapshot(services_factory, "a30-eta", mutate)
    eta = next(r for r in snap.releases if r.id == "R:PART:50501")
    assert eta.precision == "TIMESTAMP" and eta.forecast_at == datetime(2026, 10, 27, 14, 15, tzinfo=UTC)
    assert eta.status is ReleaseStatus.CONDITIONAL                                        # a forecast, never physical readiness
    seconds = next(r for r in snap.releases if r.id == "R:PART:60004")
    assert seconds.forecast_at == datetime(2026, 10, 27, 14, 16, tzinfo=UTC)              # never before its precision-aware floor


def test_a30_past_ecd_is_not_receipt_and_reopen_reblocks(services_factory) -> None:
    def mutate(d):
        d["etracs_active"]["rows"][0]["ETRAC_ECD"] = "2026-10-20"                          # past ECD, no receipt
        d["part_events"]["rows"] = []
        d["etracs_active"]["rows"].append(_shortage(60005, 1301, "A2", "2026-10-27"))
        d["part_events"]["rows"] += [_event(60005, "RECEIVED", "2026-10-25T18:15:00Z", "2026-10-25T18:20:00Z"),
                                     _event(60005, "REOPENED", "2026-10-26T08:00:00Z", "2026-10-26T08:05:00Z")]
        d["etracs_active"]["rows"].append(_shortage(60006, 1302, "B1", "2026-10-27"))
        d["part_events"]["rows"].append(_event(60006, "RECEIVED", "2026-10-25T18:15:30Z", "2026-10-25T18:20:00Z"))

    snap = _snapshot(services_factory, "a30-past", mutate)
    past = next(r for r in snap.releases if r.id == "R:PART:50501")
    assert past.status is ReleaseStatus.CONDITIONAL and past.ready_at is None
    assert any(r.startswith("PAST_ECD_NOT_RECEIPT") for r in next(s for s in snap.shortages if s.record_id == 50501).reasons)
    assert any(r.startswith("PART_NOT_RECEIVED") for r in _executable(snap, job_id(snap, 1305, "CHILD5")))
    reopened = next(r for r in snap.releases if r.id == "R:PART:60005")
    assert reopened.status is ReleaseStatus.CONDITIONAL and reopened.ready_at is None    # earlier receipt revoked
    assert reopened.forecast_at == datetime(2026, 10, 28, 4, tzinfo=UTC)
    assert next(s for s in snap.shortages if s.record_id == 60005).status == "REOPENED"
    assert any(r.startswith("PART_NOT_RECEIVED") for r in _executable(snap, job_id(snap, 1301, "A2")))
    received = next(r for r in snap.releases if r.id == "R:PART:60006")
    assert received.status is ReleaseStatus.CONFIRMED and received.ready_at == datetime(2026, 10, 25, 18, 16, tzinfo=UTC)
    assert not any(r.startswith("PART_NOT_RECEIVED") for r in _executable(snap, job_id(snap, 1302, "B1")))
