"""P10: total IP45 across sourced holds, separate final45, all-branch AND release, conserved phases (A31, A32, A33)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from focus.contracts.planning import InspectionHold, InspectionTrigger
from focus.data.acquisition import load_snapshot
from focus.data.fixture_reader import FixtureSourceReader
from focus.data.graph import precedence_violations
from focus.data.inspections import phase_plan
from focus.domain import PersonKey, RequirementBundle, minute_floor
from focus.eligibility import Eligibility, RoleSlot, match_crew
from tests.conftest import acquire_demo, job_id

INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _trigger(holds=((40, 20), (80, 25)), final=True, others=("ODAUNITMEMBER",), status="SOURCED") -> InspectionTrigger:
    return InspectionTrigger(final_required=final, ip_required=bool(holds) or status != "SOURCED",
                             ip_holds=tuple(InspectionHold(a, m) for a, m in holds), ip_hold_status=status, other_buyoffs=others,
                             counts={}, basis="test")


def _phases(snap, jid):
    return sorted((p for p in snap.phases if p.job == jid), key=lambda p: (p.kind.value != "RELEASE", p.key.id))


def test_a31_phase_plan_allocates_45_total_across_sourced_holds(policy) -> None:
    specs, problems = phase_plan(work_minutes=100, performed=0, trigger=_trigger(), policy=policy, other_minutes={"ODAUNITMEMBER": 20})
    assert problems == []
    assert [(s.kind, s.minutes) for s in specs if not s.parallel] == [("WORK", 40), ("IP_QA", 20), ("WORK", 40), ("IP_QA", 25), ("WORK", 20),
                                                                    ("FINAL_QA", 45)]
    assert [(s.kind, s.minutes, s.parallel) for s in specs if s.parallel] == [("OTHER_BUYOFF", 20, True)]
    assert sum(s.minutes for s in specs if s.kind == "WORK") == 100                    # production elapsed conserved
    assert sum(s.minutes for s in specs if s.kind == "IP_QA") == 45                     # 45 total, never 45 per hold (90)


def test_a31_unknown_hold_location_never_invents_a_midpoint(policy) -> None:
    specs, _ = phase_plan(work_minutes=100, performed=0, trigger=_trigger(holds=(), status="UNKNOWN_LOCATION"), policy=policy, other_minutes={})
    assert [s.kind for s in specs if s.kind == "IP_QA"] == []                           # no IP at 50
    assert [s.minutes for s in specs if s.kind == "WORK"] == [100]


def test_a31_in_progress_split_and_embedded_qa_reconciliation(policy) -> None:
    specs, problems = phase_plan(work_minutes=50, performed=50, trigger=_trigger(), policy=policy, other_minutes={})
    assert problems == ["IP_HOLD_STATUS_UNKNOWN: hold at 40 precedes performed 50 without a sourced buyoff"]
    assert [(s.kind, s.minutes) for s in specs if not s.parallel][:3] == [("WORK", 30), ("IP_QA", 25), ("WORK", 20)]   # remaining only
    embedded = policy.with_overrides({"qa.standard_includes_inspection_minutes": True}, new_id="qa-embedded", reason="P10 reconcile")
    specs, problems = phase_plan(work_minutes=200, performed=0, trigger=_trigger(), policy=embedded, other_minutes={})
    assert problems == [] and sum(s.minutes for s in specs if s.kind == "WORK") == 110 # 200 - (45 IP + 45 final) carved out once
    assert sum(s.minutes for s in specs if s.kind in ("IP_QA", "FINAL_QA")) == 90       # no duplicate 45
    _, bad = phase_plan(work_minutes=80, performed=0, trigger=_trigger(), policy=embedded, other_minutes={})
    assert bad[0].startswith("QA_RECONCILIATION_INVALID")


def test_a31_demo_root3_phases_conserve_elapsed_and_effort(demo_snapshot) -> None:
    root3 = job_id(demo_snapshot, 1303, "ROOT3")
    phases = [p for p in demo_snapshot.phases if p.job == root3]
    work = sorted((p for p in phases if p.kind.value == "WORK"), key=lambda p: p.ordinal)
    ip = sorted((p for p in phases if p.kind.value == "IP_QA"), key=lambda p: p.ordinal)
    assert [p.work_minutes for p in work] == [40, 40, 20] and [p.work_minutes for p in ip] == [20, 25]
    assert all(p.parent_elapsed_minutes == 100 for p in work)
    estimate = next(d for d in demo_snapshot.durations if d.job == root3)
    assert sum(p.standard_person_minutes for p in work) == estimate.standard_person_minutes == 200   # effort conserved
    assert all(p.role_demands[0].bundle.count == 2 for p in work)                                   # crew demand kept on each segment


def _snapshot(services_factory, key, names, mutate):
    data = {n: json.loads((INPUTS / f"{n}.json").read_text()) for n in names}
    mutate(data)
    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=data))
    assert outcome.status == "COMPLETE", outcome.rejection
    return load_snapshot(services.repositories, outcome.snapshot_id)


def test_a31_unknown_or_mismatched_holds_block_root3(services_factory) -> None:
    def no_holds(d):
        d["inspection_holds"]["rows"] = []

    snap = _snapshot(services_factory, "a31-noholds", ("inspection_holds",), no_holds)
    root3 = job_id(snap, 1303, "ROOT3")
    reasons = next(d.reasons for d in snap.dispositions if d.job == root3)
    assert any(r.startswith("IP_HOLD_UNKNOWN_LOCATION") for r in reasons)
    assert not [p for p in snap.phases if p.job == root3 and p.kind.value == "IP_QA"]

    def mismatch(d):
        d["inspection_holds"]["rows"][1]["INSPECTOR_MINUTES"] = 30                   # 20 + 30 != 45

    snap = _snapshot(services_factory, "a31-mismatch", ("inspection_holds",), mismatch)
    reasons = next(d.reasons for d in snap.dispositions if d.job == job_id(snap, 1303, "ROOT3"))
    assert any(r.startswith("IP_HOLD_ALLOCATION_MISMATCH") for r in reasons)


def test_a32_final_and_parallel_buyoff_feed_one_and_release_before_every_successor(services_factory) -> None:
    def mutate(d):
        base = dict(next(r for r in d["master_task_list"]["rows"] if r["SHOP_ORDER_IDENTIFIER"] == "ROOT3"))
        flow = dict(next(r for r in d["flow_budget"]["rows"] if r["JOB_NUMBER"] == "ROOT3"))
        edge = dict(d["pnet_dag"]["rows"][0])
        for soi in ("SUCC3A", "SUCC3B"):
            d["master_task_list"]["rows"].append(dict(base, SHOP_ORDER_IDENTIFIER=soi, RECORD_KEY=f"T-{soi}", SHOP_ORDER_STATUS="PLANNED"))
            d["flow_budget"]["rows"].append(dict(flow, JOB_NUMBER=soi, CRITICAL_FLOW_MINUTES=30, TOTAL_LABOR_MINUTES=60))
            d["pnet_dag"]["rows"].append(dict(edge, PREDECESSOR_SOI="ROOT3", SUCCESSOR_SOI=soi))

    snap = _snapshot(services_factory, "a32-succ", ("master_task_list", "flow_budget", "pnet_dag"), mutate)
    root3 = job_id(snap, 1303, "ROOT3")
    phases = {p.kind.value: p for p in snap.phases if p.job == root3 and p.kind.value in ("FINAL_QA", "OTHER_BUYOFF", "RELEASE")}
    ip_total = sum(p.work_minutes for p in snap.phases if p.job == root3 and p.kind.value == "IP_QA")
    assert ip_total + phases["FINAL_QA"].work_minutes == 90                            # IP45 + final45 both apply
    release = phases["RELEASE"].key.id
    into_release = {(e.pred_phase, e.basis) for e in snap.edges if e.succ_phase == release}
    assert {(phases["FINAL_QA"].key.id, "AND_RELEASE"), (phases["OTHER_BUYOFF"].key.id, "AND_RELEASE")} <= into_release
    successors = [job_id(snap, 1303, s) for s in ("SUCC3A", "SUCC3B")]
    starts = {next(p.key.id for p in snap.phases if p.job == j and p.kind.value == "WORK" and p.ordinal == 0) for j in successors}
    out_edges = [e for e in snap.edges if e.pred_phase == release and e.succ_phase in starts]
    assert len(out_edges) == 2 and all(e.type.value == "FS" and e.hard for e in out_edges)   # every successor after the AND release
    placed = {release: (1000, 1000)}
    assert precedence_violations(out_edges, placed | {s: (1000, 1030) for s in starts}, ()) == []   # equality allowed
    assert precedence_violations(out_edges, placed | {s: (1200, 1230) for s in starts}, ()) == []   # later start also allowed (>=)
    assert len(precedence_violations(out_edges, placed | {s: (999, 1029) for s in starts}, ())) == 2


def test_a33_inspector_demand_is_a_real_qualified_qa_role(demo_snapshot) -> None:
    root3 = job_id(demo_snapshot, 1303, "ROOT3")
    qa_phases = [p for p in demo_snapshot.phases if p.job == root3 and p.kind.value in ("IP_QA", "FINAL_QA", "OTHER_BUYOFF")]
    for p in qa_phases:
        demand = p.role_demands[0]
        assert demand.team == "QA" and demand.bundle.role == "inspector" and demand.bundle.groups == (("QA",),) and demand.bundle.count == 1
    pool = next(p for p in demo_snapshot.pools if p.key.team == "QA" and p.key.start_date == date(2026, 10, 27) and p.key.shift_number == 1)
    elig = Eligibility(demo_snapshot.holdings, {r.job: r for r in demo_snapshot.requirements})
    slot = RoleSlot("INSPECTOR", "inspector", RequirementBundle((("QA",),), "inspector", 1, "QA policy"))
    start = minute_floor(datetime(2026, 10, 27, 11, tzinfo=timezone.utc))
    end = start + 45
    qualified = [m for m in pool.executable_members if elig.index.check_bundle(m, slot.bundle, start, end).allowed]
    assert sorted(qualified) == sorted(PersonKey(n, "fixture-demo").id for n in ("DEMO-Q1-01", "DEMO-Q1-02"))   # Q1-03/04: irrelevant certs
    two = match_crew([slot, RoleSlot("INSPECTOR-2", "inspector", slot.bundle)], pool.executable_members,
                     lambda person, s: elig.index.check_bundle(person, s.bundle, start, end))
    assert two.status == "MATCHED" and len(set(two.people)) == 2                       # two simultaneous inspections need two inspectors
    three = match_crew([RoleSlot(f"I{i}", "inspector", slot.bundle) for i in range(3)], pool.executable_members,
                       lambda person, s: elig.index.check_bundle(person, s.bundle, start, end))
    assert three.status == "INFEASIBLE"                                                # no unlimited or empty-cert inspector pool
    prod_people = {m for p in demo_snapshot.pools if p.key.team == "PROD-A" for m in p.members}
    assert not prod_people & set(pool.members)                                         # QA service is not production borrowing
