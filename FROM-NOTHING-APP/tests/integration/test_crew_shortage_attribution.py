"""B9: CAPACITY_SHORTAGE and SKILL_SHORTAGE are attributed from explicit source evidence (S14 taxonomy, P17 step 3).

The 2026-10-26 obligation plan is built on the 10:00Z source cutoff. Its frozen goals put DEMO-B1-03 and DEMO-B1-04
on PROD-B's ROOT3 work and DEMO-Q1-01 on QA's buyoffs; ROOT3 and most QA units are missed at the 19:30Z collection.
Each case adds attendance or holding evidence and checks the automatic claims against the accepted plan's own crews
and windows:

* a planned crew member's call-off observed after the cutoff is CAPACITY_SHORTAGE evidence for that person's missed
  units; a call-off the plan already knew, a call-off corrected by a later record and a call-off for someone with no
  missed work are not;
* a revocation of a certificate in the work's bundle that takes effect before a missed unit's planned end is
  SKILL_SHORTAGE evidence for that unit; a revocation the plan already knew, one after the planned end, one of a
  certificate the work does not need, or one reinstated by the planned start, is not.

Claims are evidence for review, never relief; malformed evidence is quarantined with a stable reason.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.codec import parse_utc
from focus.config import Settings
from focus.data.acquisition import acquire, make_reader, snapshot_for_run
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import Purpose
from focus.engine.commitments import import_fixture_incumbent
from focus.engine.max.optimizer.orchestrator import schedule
from focus.performance.actuals import FEED_BY_NAME, FixtureObservationReader, collect_observations
from focus.performance.lifecycle import ObservationView
from focus.performance.score import evaluate_shift
from focus.publication.service import accepted_plans, publish
from focus.runtime import build_services

S1 = "DEMO:2026-10-26:S1"
P1 = parse_utc("2026-10-26T19:30:00Z")
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _services(root: Path, app_root: Path):
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    return build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                          clock=clock)


@pytest.fixture(scope="module")
def published(tmp_path_factory, app_root):
    root = tmp_path_factory.mktemp("b9") / "rt"
    services = _services(root, app_root)
    run = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="b9")
    import_fixture_incumbent(services)
    candidate, _ = schedule(services, source_run=run.run_id, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=5)
    result = publish(services, candidate.id)
    assert result.status == "ACCEPTED", result.reasons
    snap = snapshot_for_run(services, run.run_id)
    (plan,) = accepted_plans(services, services.settings.scope_key, "EXECUTABLE")[0]
    return SimpleNamespace(root=root, snap=snap, assignments={a.id: a for a in plan.artifact.candidate.assignments},
                           person={p.key.native_id: p.id for p in snap.people})


def _feed(name: str, rows: list[dict]) -> dict:
    return {"schema_version": 1, "dataset": name, "columns": list(FEED_BY_NAME[name].columns), "rows": rows,
            "receipt": {"complete": True, "covered_through_utc": "2026-10-26T19:30:00Z"}}


def _att(key: str, person: str, present: bool, recorded: str, observed: str, *, shift: int = 1, ref: str | None = None) -> dict:
    return {"SOURCE_EVENT_KEY": key, "PERSON_KEY": person, "SHIFT_DATE": "2026-10-26", "SHIFT_NUMBER": shift, "PRESENT": present,
            "REASON": "called off" if not present else "on site", "RECORDED_AT_UTC": recorded, "OBSERVED_AT_UTC": observed,
            "EVIDENCE_REF": f"HR-{key}" if ref is None else ref}


def _hold(key: str, person: str, cert: str, event: str, effective: str, observed: str) -> dict:
    return {"SOURCE_EVENT_KEY": key, "PERSON_KEY": person, "CERT_CODE": cert, "EVENT": event, "EFFECTIVE_AT_UTC": effective,
            "OBSERVED_AT_UTC": observed, "EVIDENCE_REF": f"CERT-{key}"}


T = "2026-10-26T{}:00Z".format
ATTENDANCE = [
    _att("B103-OFF", "DEMO-B1-03", False, T("11:30"), T("11:35")),                 # after the cutoff: CAPACITY_SHORTAGE for ROOT3
    _att("B104-OFF", "DEMO-B1-04", False, T("11:00"), T("11:05")),                 # ... corrected by the next record
    _att("B104-BACK", "DEMO-B1-04", True, T("11:20"), T("11:25")),
    _att("Q101-OFF", "DEMO-Q1-01", False, T("09:00"), T("09:05")),                 # before the 10:00 cutoff: the plan knew
    _att("A103-OFF", "DEMO-A1-03", False, T("11:00"), T("11:05")),                 # PROD-A misses nothing
    _att("B103-S2", "DEMO-B1-03", False, T("11:30"), T("11:35"), shift=2),        # another shift's call-off
]
HOLDINGS = [
    _hold("Q101-QA", "DEMO-Q1-01", "QA", "REVOKED", T("13:00"), T("13:05")),      # QA units planned to end after 13:00
    _hold("Q101-QA-OLD", "DEMO-Q1-01", "QA", "REVOKED", T("08:00"), T("09:00")),  # observed before the cutoff: the plan knew
    _hold("B103-A", "DEMO-B1-03", "A", "REVOKED", T("12:00"), T("12:05")),        # ROOT3 needs B, not A
    _hold("B104-B", "DEMO-B1-04", "B", "REVOKED", T("11:00"), T("11:05")),        # ... reinstated before ROOT3 starts (12:44)
    _hold("B104-B-BACK", "DEMO-B1-04", "B", "REINSTATED", T("12:30"), T("12:35")),
]


def _collect(published, tmp_path, app_root, attendance=ATTENDANCE, holdings=HOLDINGS):
    root = tmp_path / "rt"
    shutil.copytree(published.root, root)
    services = _services(root, app_root)
    reader = FixtureObservationReader(INPUTS, overrides={"attendance_evidence": _feed("attendance_evidence", attendance),
                                                         "holding_evidence": _feed("holding_evidence", holdings)})
    run = collect_observations(services, as_of=P1, reader=reader)
    return services, run, ObservationView.build(services, P1)


def _auto(view, cause: str) -> list:
    return [c for c in view.claims if c.attribution == "AUTO" and c.cause == cause]


def _missed(view, published, team: str) -> dict[str, tuple]:
    """Missed units of the team's S1 goal -> (planned crew natives, planned start, planned end), read from the accepted plan."""
    native = {v: k for k, v in published.person.items()}
    record = view.record(team, S1)
    missed = {f.unit_id for f in view.unit_facts(record) if f.status == "MISSED"}
    out = {}
    for u in record.units:
        a = published.assignments.get(u.assignment_id)
        if u.id in missed and a is not None:
            out[u.id] = ({native[r.person] for r in a.crew}, a.start, a.end)
    return out


def test_a_call_off_after_the_cutoff_is_capacity_evidence_for_that_persons_missed_units(published, tmp_path, app_root) -> None:
    services, run, view = _collect(published, tmp_path, app_root)
    assert run.feed("attendance_evidence").status == "ROWS" and run.feed("attendance_evidence").admitted == len(ATTENDANCE)
    expected = {u for u, (crew, _, _) in _missed(view, published, "PROD-B").items() if "DEMO-B1-03" in crew}
    assert len(expected) == 3                                                       # ROOT3 WORK 0 segment 1, WORK 1, WORK 2
    (claim,) = _auto(view, "CAPACITY_SHORTAGE")
    assert set(claim.units) == expected and claim.goal_id == view.record("PROD-B", S1).goal_id
    assert claim.status == "SUBMITTED" and claim.origin_team is None and claim.executing_team == "PROD-B"
    assert "ATTENDANCE:HR-B103-OFF" in claim.evidence                                 # the source record, not a guess
    assert evaluate_shift(services, "PROD-B", S1, as_of=P1).E == 0                   # a claim is never relief by itself


def test_a_revocation_before_the_planned_end_is_skill_evidence_for_units_needing_the_cert(published, tmp_path, app_root) -> None:
    _, run, view = _collect(published, tmp_path, app_root)
    assert run.feed("holding_evidence").status == "ROWS"
    revoked = parse_utc(T("13:00"))
    churned = {f.unit_id for f in view.unit_facts(view.record("QA", S1)) if "WITHDRAWN_WORK_STAYS_IN_B0" in f.reasons}
    missed = _missed(view, published, "QA")
    expected = {u for u, (crew, _, end) in missed.items() if "DEMO-Q1-01" in crew and end > revoked and u not in churned}
    before = {u for u, (crew, _, end) in missed.items() if "DEMO-Q1-01" in crew and end <= revoked}
    assert len(expected) == 4 and len(before) == 1                                  # PU3's final QA was planned to end at 12:44
    (claim,) = _auto(view, "SKILL_SHORTAGE")
    assert set(claim.units) == expected and claim.status == "SUBMITTED" and claim.executing_team == "QA"
    assert "HOLDING:CERT-Q101-QA" in claim.evidence


def test_known_corrected_unrelated_and_reinstated_evidence_makes_no_claim(published, tmp_path, app_root) -> None:
    _, _, view = _collect(published, tmp_path, app_root)
    cited = {e for c in view.claims if c.attribution == "AUTO" for e in c.evidence}
    for ref in ("HR-B104-OFF", "HR-B104-BACK", "HR-Q101-OFF", "HR-A103-OFF", "HR-B103-S2"):
        assert f"ATTENDANCE:{ref}" not in cited
    for ref in ("CERT-Q101-QA-OLD", "CERT-B103-A", "CERT-B104-B", "CERT-B104-B-BACK"):
        assert f"HOLDING:{ref}" not in cited
    assert not [c for c in _auto(view, "CAPACITY_SHORTAGE") if c.goal_id == view.record("QA", S1).goal_id]


def test_without_evidence_there_is_no_capacity_or_skill_claim(published, tmp_path, app_root) -> None:
    _, run, view = _collect(published, tmp_path, app_root, attendance=[], holdings=[])
    assert run.feed("attendance_evidence").status == run.feed("holding_evidence").status == "CERTIFIED_EMPTY"
    assert not _auto(view, "CAPACITY_SHORTAGE") and not _auto(view, "SKILL_SHORTAGE")


def test_malformed_evidence_is_quarantined_with_a_stable_reason(published, tmp_path, app_root) -> None:
    bad_att = [_att("CLOCKS", "DEMO-B1-03", False, T("11:40"), T("11:35")), _att("NO-REF", "DEMO-B1-03", False, T("11:30"), T("11:35"), ref="")]
    bad_hold = [_hold("SUSPEND", "DEMO-Q1-01", "QA", "SUSPENDED", T("13:00"), T("13:05"))]
    _, run, view = _collect(published, tmp_path, app_root, attendance=bad_att, holdings=bad_hold)
    reasons = {(q.feed, q.source_key): q.code for q in run.quarantine}
    assert reasons[("attendance_evidence", "CLOCKS")] == "OBSERVED_BEFORE_RECORDED"
    assert reasons[("attendance_evidence", "NO-REF")] == "ATTENDANCE_EVIDENCE_REQUIRED"
    assert reasons[("holding_evidence", "SUSPEND")] == "BAD_HOLDING_EVENT"
    assert not _auto(view, "CAPACITY_SHORTAGE") and not _auto(view, "SKILL_SHORTAGE")
