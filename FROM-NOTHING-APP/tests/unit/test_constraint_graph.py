"""P09: typed precedence, in-progress retention, rework injection and quarantine (A25, A26, A27, A28)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from focus.contracts.planning import Edge
from focus.data.acquisition import load_snapshot, snapshot_for_run
from focus.data.fixture_reader import FixtureSourceReader
from focus.data.graph import precedence_violations
from focus.domain import EdgeType, minute_floor
from tests.conftest import acquire_demo, job_id

UTC = timezone.utc
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _e(a: str, b: str, etype: EdgeType = EdgeType.FS, lag: int = 0, hard: bool = True) -> Edge:
    return Edge(f"E-{a}-{b}-{etype.value}", a, b, etype, lag, hard, "TEST", None, None, None, None, ())


def test_a25_incomplete_predecessor_must_finish_and_absence_never_satisfies() -> None:
    edge = [_e("A", "B")]
    assert precedence_violations(edge, {"B": (100, 160)}, ())[0][1].startswith("PREDECESSOR_MISSING_FROM_CANDIDATE")
    assert precedence_violations(edge, {"A": (0, 120), "B": (100, 160)}, ())[0][1].startswith("PRECEDENCE_FS")
    assert precedence_violations(edge, {"A": (0, 100), "B": (100, 160)}, ()) == []     # equality allowed
    assert precedence_violations(edge, {"A": (0, 100), "B": (150, 210)}, ()) == []     # FS means >=, not forced immediacy
    assert precedence_violations(edge, {"B": (100, 160)}, {"A"}) == []                 # a verified-complete anchor satisfies
    assert precedence_violations([_e("A", "B", hard=False)], {"B": (0, 10)}, ()) == []  # advisory links are recorded, not enforced


def test_a25_signed_lag_and_all_relationship_equations() -> None:
    lead = [_e("A", "B", lag=-30)]
    assert precedence_violations(lead, {"A": (0, 100), "B": (70, 90)}, ()) == []       # negative lag is kept, never clamped to 0
    assert precedence_violations(lead, {"A": (0, 100), "B": (69, 90)}, ())
    placed = {"A": (10, 50), "B": (20, 60)}
    assert precedence_violations([_e("A", "B", EdgeType.SS, 10)], placed, ()) == []    # 10+10 <= 20
    assert precedence_violations([_e("A", "B", EdgeType.SS, 11)], placed, ())
    assert precedence_violations([_e("A", "B", EdgeType.FF, 10)], placed, ()) == []    # 50+10 <= 60
    assert precedence_violations([_e("A", "B", EdgeType.FF, 11)], placed, ())
    assert precedence_violations([_e("A", "B", EdgeType.SF, 50)], placed, ()) == []    # 10+50 <= 60
    assert precedence_violations([_e("A", "B", EdgeType.SF, 51)], placed, ())


def _overrides(names, mutate):
    data = {n: json.loads((INPUTS / f"{n}.json").read_text()) for n in names}
    mutate(data)
    return data


def _snapshot(services_factory, key, names, mutate, policy=None):
    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=_overrides(names, mutate)))
    assert outcome.status == "COMPLETE", outcome.rejection
    snap = load_snapshot(services.repositories, outcome.snapshot_id)
    return snapshot_for_run(services, outcome.run_id, policy=policy(services.policy)) if policy else snap


def _reasons(snap, jid):
    return next(d.reasons for d in snap.dispositions if d.job == jid)


def _phase_of(snap, jid, kind):
    return [p for p in snap.phases if p.job == jid and p.kind.value == kind]


def test_a25_self_edge_and_cycle_are_quarantined_without_deleting_edges(services_factory) -> None:
    def mutate(d):
        base = dict(d["pnet_dag"]["rows"][0])
        d["pnet_dag"]["rows"] += [dict(base, PREDECESSOR_SOI="A1", SUCCESSOR_SOI="A1"),
                                  dict(base, PREDECESSOR_SOI="B2", SUCCESSOR_SOI="B1")]      # with B1->B2 forms a cycle

    snap = _snapshot(services_factory, "a25-cycle", ("pnet_dag",), mutate)
    a1, b1, b2 = job_id(snap, 1301, "A1"), job_id(snap, 1302, "B1"), job_id(snap, 1302, "B2")
    assert any(r.startswith("CYCLE_SELF_EDGE") for r in _reasons(snap, a1))
    for jid in (b1, b2):
        assert any(r.startswith("CYCLE: hard precedence cycle") for r in _reasons(snap, jid)), jid
    starts = {p.job: p.key.id for p in snap.phases if p.kind.value == "WORK" and p.ordinal == 0}
    finishes = {p.job: p.key.id for p in snap.phases if p.kind.value == "RELEASE"}
    pairs = {(e.pred_phase, e.succ_phase) for e in snap.edges if e.basis == "SOURCE_PNET"}
    assert (finishes[b1], starts[b2]) in pairs and (finishes[b2], starts[b1]) in pairs   # both cycle edges retained
    fgi2 = job_id(snap, 1302, "FGI")
    assert any(r.startswith("PREDECESSOR_NOT_SCHEDULABLE") for r in _reasons(snap, fgi2))    # downstream waits, never "done"


def test_a26_in_progress_keeps_incomplete_predecessor_and_actual_history(demo_snapshot) -> None:
    root4, pre4 = job_id(demo_snapshot, 1304, "ROOT4"), job_id(demo_snapshot, 1304, "PRE4")
    job = next(j for j in demo_snapshot.jobs if j.id == root4)
    assert job.actual_start == datetime(2026, 10, 23, 12, 0, tzinfo=UTC)                # history unchanged (08:00 EDT)
    assert (job.performed_elapsed, job.remaining_elapsed) == (40, 60)
    work = _phase_of(demo_snapshot, root4, "WORK")
    assert sum(p.work_minutes for p in work) == 60                                      # only the remaining work; no restart fiction
    pre_release = _phase_of(demo_snapshot, pre4, "RELEASE")[0].key.id
    edge = next(e for e in demo_snapshot.edges if e.pred_phase == pre_release and e.succ_phase == work[0].key.id)
    assert edge.type is EdgeType.FS and edge.hard and edge.basis == "SOURCE_PNET"
    codes = {(g.code, g.scope) for g in demo_snapshot.gaps}
    label = job.key.label()
    assert ("SEQUENCE_VIOLATION", label) in codes and ("IN_PROGRESS_CONTINUATION_HOLD", label) in codes
    day0, day2 = minute_floor(datetime(2026, 10, 26, 11, tzinfo=UTC)), minute_floor(datetime(2026, 10, 28, 11, tzinfo=UTC))
    pre_work = _phase_of(demo_snapshot, pre4, "WORK")
    placed = {p.key.id: (day2, day2 + 60) for p in pre_work} | {pre_release: (day2 + 60, day2 + 60)}
    early = placed | {work[0].key.id: (day0, day0 + 60)}                                # remaining work proposed on day 0
    assert any(v[1].startswith("PRECEDENCE_FS") for v in precedence_violations(demo_snapshot.edges, early, ()))
    late = placed | {work[0].key.id: (day2 + 60, day2 + 120)}
    assert not [v for v in precedence_violations(demo_snapshot.edges, late, ()) if work[0].key.id in v[1]]


def test_a27_each_rework_gate_injected_once_for_the_unstarted_root(demo_snapshot) -> None:
    root3 = job_id(demo_snapshot, 1303, "ROOT3")
    root_start = _phase_of(demo_snapshot, root3, "WORK")[0].key.id
    injected = [e for e in demo_snapshot.edges if e.basis == "REWORK_INJECTED"]
    assert len(injected) == 2 and {e.succ_phase for e in injected} == {root_start}       # NC3 and PU3, one gate each
    children = {job_id(demo_snapshot, 1303, s): s for s in ("NC3", "PU3")}
    releases = {_phase_of(demo_snapshot, c, "RELEASE")[0].key.id for c in children}
    assert {e.pred_phase for e in injected} == releases
    links = {link.child_job: link for link in demo_snapshot.rework_links}
    assert {links[c].status for c in children} == {"LINKED"}
    assert links[job_id(demo_snapshot, 1303, "PU3")].defect_origin_team == "SUPPLIER-DEMO"   # origin kept apart from DAG direction


def _start_root3(d):
    for r in d["master_task_list"]["rows"]:
        if (r["SHOP_ORDER_IDENTIFIER"], r["LINE_NUMBER"]) == ("ROOT3", 1303):
            r["SHOP_ORDER_STATUS"], r["ACT_START"] = "IN WORK", "2026-10-23T08:00:00"
    d["work_progress"]["rows"].append({"AS_OF_UTC": "2026-10-26T09:30:00Z", "EVIDENCE_REF": "T-ROOT3", "LINE_NUMBER": 1303,
                                       "PERFORMED_ELAPSED_MINUTES": 30, "REMAINING_ELAPSED_MINUTES": 70, "SOI": "ROOT3"})


def test_a27_started_root_uses_the_documented_hold(services_factory) -> None:
    names = ("master_task_list", "work_progress")
    snap = _snapshot(services_factory, "a27-started", names, _start_root3)
    root3 = job_id(snap, 1303, "ROOT3")
    holds = [e for e in snap.edges if e.basis == "REWORK_CONTINUATION_HOLD"]
    assert len(holds) == 2 and {e.succ_phase for e in holds} == {_phase_of(snap, root3, "WORK")[0].key.id}
    assert not [e for e in snap.edges if e.basis == "REWORK_INJECTED"]
    closure = _snapshot(services_factory, "a27-closure", names, _start_root3,
                        policy=lambda p: p.with_overrides({"rework.started_root_hold": "CLOSURE"}, new_id="rw-closure", reason="A27"))
    cl = [e for e in closure.edges if e.basis == "REWORK_CLOSURE_HOLD"]
    assert len(cl) == 2 and {e.succ_phase for e in cl} == {_phase_of(closure, root3, "RELEASE")[0].key.id}   # only closure waits


def test_a28_reversed_rework_linkage_is_quarantined(services_factory) -> None:
    def mutate(d):
        for r in d["master_task_list"]["rows"]:
            if (r["SHOP_ORDER_IDENTIFIER"], r["LINE_NUMBER"]) == ("ROOT3", 1303):
                r["PARENT_SOI"] = "NC3"                                                   # NC3 -> ROOT3 -> NC3

    snap = _snapshot(services_factory, "a28-reverse", ("master_task_list",), mutate)
    root3, nc3 = job_id(snap, 1303, "ROOT3"), job_id(snap, 1303, "NC3")
    for jid in (root3, nc3):
        assert any("REWORK_CYCLE" in r or r.startswith("CYCLE") for r in _reasons(snap, jid)), jid
    assert {link.status for link in snap.rework_links if link.child_job == nc3} == {"QUARANTINED"}


def test_a28_lifecycle_conflict_and_reopened_root_closure(services_factory) -> None:
    def conflict(d):
        for r in d["neo_graph_rework"]["rows"]:
            if r["CHILD_SOI"] == "NC3":
                r["CHILD_STATUS"] = "COMPLETE"                                            # NEO says complete; master says AVAILABLE

    snap = _snapshot(services_factory, "a28-conflict", ("neo_graph_rework",), conflict)
    root3 = job_id(snap, 1303, "ROOT3")
    executable = next(d.executable_reasons for d in snap.dispositions if d.job == root3)
    assert any(r.startswith("REWORK_STATUS_CONFLICT") for r in executable)
    assert len([e for e in snap.edges if e.basis == "REWORK_INJECTED"]) == 2               # the unverified completion releases nothing

    def reopened(d):
        for r in d["master_task_list"]["rows"]:
            if (r["SHOP_ORDER_IDENTIFIER"], r["LINE_NUMBER"]) == ("ROOT5", 1305):
                r["SHOP_ORDER_STATUS"], r["ACT_START"], r["ACT_COMP"] = "COMPLETE", "2026-10-20T07:00:00", "2026-10-20T09:00:00"
        d["rework_classification"]["rows"].append({"CHILD_SOI": "CHILD5", "DEFECT_ORIGIN_TEAM": "PROD-A", "EVIDENCE_REF": "T-REOPEN",
                                                   "LINE_NUMBER": 1305, "REWORK_CLASS": "NC", "ROOT_SOI": "ROOT5"})

    snap = _snapshot(services_factory, "a28-reopen", ("master_task_list", "rework_classification"), reopened)
    child5, next5 = job_id(snap, 1305, "CHILD5"), job_id(snap, 1305, "NEXT5")
    gated = [e for e in snap.edges if e.basis == "REWORK_REOPENED_ROOT"]
    assert [(e.pred_phase, e.succ_phase) for e in gated] == [(_phase_of(snap, child5, "RELEASE")[0].key.id,
                                                              _phase_of(snap, next5, "WORK")[0].key.id)]
    assert any(g.code == "ROOT_CLOSURE_REOPENED" for g in snap.gaps)
