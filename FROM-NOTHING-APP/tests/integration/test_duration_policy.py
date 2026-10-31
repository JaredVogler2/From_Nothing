"""P07: flow standards, consumed P75 elapsed estimates and the statistics toggle (A15, A17, A18)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from focus.data.acquisition import load_snapshot, snapshot_for_run
from focus.data.duration_gauge import DurationStatistics
from focus.data.durations import flow_index
from focus.data.fixture_reader import FixtureSourceReader
from tests.conftest import acquire_demo, job_id

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _estimates(snapshot):
    return {d.job: d for d in snapshot.durations}


def _reasons(snapshot, job: str) -> tuple[str, ...]:
    return next(d.reasons for d in snapshot.dispositions if d.job == job)


def _snapshot(services_factory, key: str, mutate):
    data = {name: json.loads((INPUTS / f"{name}.json").read_text()) for name in ("flow_budget", "duration_episodes")}
    mutate(data)
    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection
    return services, outcome, load_snapshot(services.repositories, outcome.snapshot_id)


# ---------------------------------------------------------------------------- A15

def test_a15_composite_lookup_keeps_each_line_standard_and_all_fields(demo_snapshot) -> None:
    est = _estimates(demo_snapshot)
    fgi1, fgi2 = job_id(demo_snapshot, 1301, "FGI"), job_id(demo_snapshot, 1302, "FGI")
    assert est[fgi1].elapsed_minutes == 30 and est[fgi2].elapsed_minutes == 40      # same JOB_NUMBER, two lines, two standards
    root5 = next(j for j in demo_snapshot.jobs if j.line == 1305 and j.key.native_id == "ROOT5")
    budget = root5.raw_budget
    assert (budget.critical_flow_minutes, budget.total_mechanics, budget.primary_mechanic_count, budget.assist_count,
            budget.total_labor_minutes) == (90, 3, 2, 1, 270)                        # every source field preserved
    assert est[root5.id].crew_size == 3 and est[root5.id].crew_profile == "SIMULTANEOUS"   # TOTAL, never PRIMARY=2
    assert est[root5.id].standard_person_minutes == 270


def test_a15_zero_null_negative_and_conflicting_counts_are_unavailable(demo_snapshot, services_factory) -> None:
    tst6b = job_id(demo_snapshot, 1306, "TST6B")                                    # TOTAL_MECHANICS = 0 in the fixture
    assert tst6b not in _estimates(demo_snapshot)
    assert any(r.startswith("FLOW_CREW_INVALID: TOTAL_MECHANICS=0") for r in _reasons(demo_snapshot, tst6b))

    def mutate(d):
        rows = d["flow_budget"]["rows"]
        for r in rows:
            if (r["JOB_NUMBER"], r["LINE_NUMBER"]) == ("CHILD5", 1305):
                r["TOTAL_MECHANICS"] = None
            if (r["JOB_NUMBER"], r["LINE_NUMBER"]) == ("NEXT5", 1305):
                r["TOTAL_MECHANICS"] = -2
        a1 = next(r for r in rows if (r["JOB_NUMBER"], r["LINE_NUMBER"]) == ("A1", 1301))
        rows.append(dict(a1, CRITICAL_FLOW_MINUTES=150))                              # same exact key, different standard
        a2 = next(r for r in rows if (r["JOB_NUMBER"], r["LINE_NUMBER"]) == ("A2", 1301))
        rows.append(dict(a2))                                                         # a true repeat dedupes

    _, _, snap = _snapshot(services_factory, "a15-counts", mutate)
    est = _estimates(snap)
    for soi, text in (("CHILD5", "TOTAL_MECHANICS=None"), ("NEXT5", "TOTAL_MECHANICS=-2")):
        jid = job_id(snap, 1305, soi)
        assert jid not in est and any(r.startswith(f"FLOW_CREW_INVALID: {text}") for r in _reasons(snap, jid)), soi
    a1 = job_id(snap, 1301, "A1")
    assert a1 not in est and any(r.startswith("FLOW_CONFLICT: FOCUS_FLOW_BUDGET rows for A1@1301 disagree on critical_flow_minutes")
                                 for r in _reasons(snap, a1))
    assert est[job_id(snap, 1301, "A2")].elapsed_minutes == 90


def test_a15_flow_index_is_composite_and_reports_conflicts() -> None:
    base = {"CRITICAL_FLOW_MINUTES": 60, "TOTAL_MECHANICS": 3, "PRIMARY_MECHANIC_COUNT": 2, "LOAD_TIMESTAMP": "2026-10-25T05:00:00"}
    index = flow_index([dict(base, JOB_NUMBER="J", LINE_NUMBER=1), dict(base, JOB_NUMBER="J", LINE_NUMBER=2, CRITICAL_FLOW_MINUTES=75),
                        dict(base, JOB_NUMBER="K", LINE_NUMBER=1), dict(base, JOB_NUMBER="K", LINE_NUMBER=1, TOTAL_MECHANICS=4)])
    assert index[(1, "J")].critical_flow_minutes == 60 and index[(2, "J")].critical_flow_minutes == 75
    assert (1, "K") not in index and "total_mechanics" in index.conflicts[(1, "K")]


def test_a15_sequential_assignment_counts_require_role_phases(demo, services_factory) -> None:
    services, outcome, _ = demo
    sequential = services.policy.with_overrides({"duration.total_mechanics_semantics": "SEQUENTIAL"}, new_id="rebuild-v1-seq",
                                                reason="A15 assignment counts are sequential")
    snap = snapshot_for_run(services, outcome.run_id, policy=sequential)
    root5 = job_id(snap, 1305, "ROOT5")
    assert root5 not in _estimates(snap)
    assert any(r.startswith("FLOW_SEMANTICS_SEQUENTIAL") for r in _reasons(snap, root5))


# ---------------------------------------------------------------------------- A17

def test_a17_rework_uses_p75_of_elapsed_cohort_not_effort_over_crew(services_factory) -> None:
    def mutate(d):
        for r in d["duration_episodes"]["rows"]:
            elapsed = Decimal(r["ELAPSED_MINUTES"])
            r["PERSON_EFFORT_MINUTES"] = str(3 * elapsed + 7)                          # distinct effort, not crew x elapsed

    services, outcome, snap = _snapshot(services_factory, "a17-effort", mutate)
    est = _estimates(snap)
    for soi in ("NC3", "PU3"):
        e = est[job_id(snap, 1303, soi)]
        assert e.measurement_basis == "ELAPSED_ACTIVE_EPISODES" and e.statistic == "P75"
        assert e.precise_elapsed == Decimal("51.750000") and e.elapsed_minutes == 52   # ceiling only after estimation
        assert e.crew_size == 2 and e.standard_person_minutes == 104
        assert "P75=51.750000" in e.uncertainty and "interval [48.000000, 56.000000]" in e.uncertainty
    stat = services.repositories.get_typed("statistics", est[job_id(snap, 1303, "NC3")].stats_id, "DurationStatistics", DurationStatistics)
    assert stat is not None and stat.id in snap.statistics_ids
    assert stat.p75 == Decimal("51.750000") and stat.effort_mean == Decimal("140.500000")
    assert stat.effort_mean / 2 != stat.p75                                          # effort/crew would have given 70.25
    assert (stat.p50, stat.p55, stat.mean, stat.p90) == (Decimal("44.500000"), Decimal("45.950000"), Decimal("44.500000"), Decimal("56.100000"))
    assert stat.admitted_count == 30 and stat.excluded_reasons == {"STATUS_REOPENED": 1, "STATUS_UNVERIFIED": 1} and stat.unit == "minutes"


def test_b14_exact_model_and_crew_context_first_then_the_approved_broader_group(services_factory) -> None:
    _, _, snap = _snapshot(services_factory, "b14-exact", lambda d: None)
    nc3 = _estimates(snap)[job_id(snap, 1303, "NC3")]
    assert "of 30 completed NC/PROD-B/MODEL=9/CREW=2 elapsed episodes (exact context)" in nc3.method

    def other_model(d):                       # half of the NC cohort was another model, so the exact context is below the sample
        for r in d["duration_episodes"]["rows"]:
            if r["REWORK_CLASS"] == "NC" and r["EPISODE_KEY"] < "NC-015":
                r["MODEL"] = "10"

    _, _, mixed = _snapshot(services_factory, "b14-broader", other_model)
    e = _estimates(mixed)[job_id(mixed, 1303, "NC3")]
    assert "of 30 completed NC/PROD-B elapsed episodes (approved broader group)" in e.method and e.elapsed_minutes == 52


# ---------------------------------------------------------------------------- A18

def test_a18_p90_toggle_is_a_new_policy_and_snapshot_original_unchanged(demo) -> None:
    services, outcome, original = demo
    p90 = services.policy.with_overrides({"duration.default_statistic": "P90"}, new_id="rebuild-v1-p90", authority="user_requirement",
                                         reason="A18 select P90")
    toggled = snapshot_for_run(services, outcome.run_id, policy=p90)
    assert p90.hash != services.policy.hash and toggled.id != original.id and toggled.policy_hash == p90.hash
    nc3 = job_id(original, 1303, "NC3")
    assert _estimates(original)[nc3].statistic == "P75" and _estimates(original)[nc3].elapsed_minutes == 52
    assert _estimates(toggled)[nc3].statistic == "P90" and _estimates(toggled)[nc3].precise_elapsed == Decimal("56.100000")
    assert _estimates(toggled)[nc3].elapsed_minutes == 57
    reread = load_snapshot(services.repositories, original.id)                       # the accepted input is immutable
    assert reread.hash == original.hash and _estimates(reread)[nc3].statistic == "P75"
    assert set(toggled.statistics_ids).isdisjoint(original.statistics_ids)          # statistics are bound to their policy
    assert snapshot_for_run(services, outcome.run_id).id == original.id              # the active policy still resolves the original


def test_a18_insufficient_or_stale_cohort_is_unavailable_never_flat_60(services_factory) -> None:
    def thin(d):
        d["duration_episodes"]["rows"] = [r for r in d["duration_episodes"]["rows"] if not (r["REWORK_CLASS"] == "NC" and r["EPISODE_KEY"] >= "NC-029")]

    _, _, snap = _snapshot(services_factory, "a18-thin", thin)
    nc3 = job_id(snap, 1303, "NC3")
    assert nc3 not in _estimates(snap)
    assert any(r.startswith("DURATION_UNAVAILABLE: no sufficient, current comparable NC cohort (no flat default)") for r in _reasons(snap, nc3))
    assert job_id(snap, 1303, "PU3") in _estimates(snap)                             # other classes are unaffected

    def stale(d):
        for r in d["duration_episodes"]["rows"]:
            if r["REWORK_CLASS"] == "PU":
                r["COMPLETED_AT_UTC"] = "2024-09-01T14:00:00Z"

    _, _, old = _snapshot(services_factory, "a18-stale", stale)
    pu3 = job_id(old, 1303, "PU3")
    assert pu3 not in _estimates(old) and any(r.startswith("DURATION_UNAVAILABLE") for r in _reasons(old, pu3))
    assert all(e.elapsed_minutes != 60 or e.measurement_basis != "ELAPSED_ACTIVE_EPISODES" for e in old.durations)


def test_in_progress_rework_keeps_its_remaining_minutes_not_a_fresh_p75(services_factory) -> None:
    """S08: remaining actual work is preserved for rework as for any job; the full P75 is never scheduled again."""
    data = {name: json.loads((INPUTS / f"{name}.json").read_text()) for name in ("master_task_list", "work_progress")}
    for r in data["master_task_list"]["rows"]:
        if (r["LINE_NUMBER"], r["SHOP_ORDER_IDENTIFIER"]) == (1303, "NC3"):
            r.update({"SHOP_ORDER_STATUS": "IN WORK", "ACT_START": "2026-10-25T08:00:00"})
    data["work_progress"]["rows"].append({"AS_OF_UTC": "2026-10-26T09:30:00Z", "EVIDENCE_REF": "TEST-PROGRESS-NC3", "LINE_NUMBER": 1303,
                                          "PERFORMED_ELAPSED_MINUTES": 30, "REMAINING_ELAPSED_MINUTES": 20, "SOI": "NC3"})
    services = services_factory()
    outcome = acquire_demo(services, key="rework-remaining", reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection
    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    nc3 = _estimates(snap)[job_id(snap, 1303, "NC3")]
    assert nc3.measurement_basis == "REMAINING_ELAPSED" and nc3.elapsed_minutes == 20      # not the cohort's P75 of 52
    pu3 = _estimates(snap)[job_id(snap, 1303, "PU3")]
    assert pu3.statistic == "P75" and pu3.elapsed_minutes == 52                              # open rework still uses the cohort
