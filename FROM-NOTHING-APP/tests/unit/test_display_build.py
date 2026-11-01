"""The aircraft graph parts' ancestor walk against ``ValidationInput.ancestors`` on random graphs.

Random graphs over several aircraft, with release milestones of every kind and status, completed phases and
verified-complete jobs, concurrency edges, edges between aircraft and cycles: for every phase, the walk over its aircraft's
graph part (``display.build.line_graphs``, ``display.records.unreceived_ancestors``) must find exactly the unconfirmed
PART releases among the ancestors the validator walks (up to verified completion, never through it).
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from focus.display.build import line_graphs
from focus.display.records import unreceived_ancestors
from focus.domain import Disposition, EdgeType, Purpose, ReleaseKind, ReleaseStatus
from focus.validation.input import CONCURRENCY_EDGES, ValidationInput


def graph(seed: int):
    rng = random.Random(seed)
    jobs = [f"J{seed:03d}{i:03d}" for i in range(rng.randint(3, 8))]
    phases = []
    for n, j in enumerate(jobs):
        for k in range(rng.randint(1, 4)):
            phases.append(SimpleNamespace(key=SimpleNamespace(id=f"{j}.WORK.{k}"), job=j, line=1 + n % 3, actual_end=None,
                                          status=rng.choice(["OPEN"] * 4 + ["COMPLETE"])))
    dispositions = [SimpleNamespace(job=j, disposition=rng.choice([Disposition.ADMITTED] * 5 + [Disposition.VERIFIED_COMPLETE]),
                                    reasons=(), executable_reasons=()) for j in jobs]
    releases = [SimpleNamespace(id=f"R{seed:03d}{i}", kind=rng.choice(list(ReleaseKind)), status=rng.choice(list(ReleaseStatus)))
                for i in range(rng.randint(1, 5))]
    nodes = [p.key.id for p in phases] + [r.id for r in releases]
    edges = []
    for n in range(rng.randint(len(nodes), 3 * len(nodes))):
        pred, succ = rng.choice(nodes), rng.choice([p.key.id for p in phases])
        edges.append(SimpleNamespace(id=f"E{n}", pred_phase=pred, succ_phase=succ, hard=rng.random() < 0.9,
                                     type=rng.choice([EdgeType.FS] * 6 + list(CONCURRENCY_EDGES))))
    return SimpleNamespace(phases=tuple(phases), dispositions=tuple(dispositions), releases=tuple(releases), edges=tuple(edges))


def validator(snap) -> ValidationInput:
    inp = ValidationInput(snapshot=snap, policy=None, purpose=Purpose.EXECUTABLE, shift_policy=None, cutoff=0)
    inp.phases = {p.key.id: p for p in snap.phases}
    inp.dispositions = {d.job: d for d in snap.dispositions}
    inp.releases = {r.id: r for r in snap.releases}
    for e in snap.edges:
        if e.hard and e.type not in CONCURRENCY_EDGES:
            inp.hard_in.setdefault(e.succ_phase, []).append(e)
    return inp


@pytest.mark.parametrize("seed", range(60))
def test_the_graph_walk_matches_the_validators_walk(seed) -> None:
    snap = graph(seed)
    inp = validator(snap)
    graphs = {g.line: g for g in line_graphs(snap)}
    unconfirmed = {r.id for r in snap.releases if r.kind is ReleaseKind.PART and r.status is not ReleaseStatus.CONFIRMED}
    shared: dict[int, dict] = {}
    for p in snap.phases:
        expected = frozenset(unconfirmed & inp.ancestors(p.key.id, {}))
        assert unreceived_ancestors(graphs[p.line], p.key.id) == expected, p.key.id
        assert unreceived_ancestors(graphs[p.line], p.key.id, shared.setdefault(p.line, {})) == expected, p.key.id   # memo reuse
    for g in graphs.values():                                           # each graph carries the records of its unreceived parts
        assert {r.id for r in g.releases} == {g.nodes[i] for i in g.unreceived} <= unconfirmed


def test_a_completed_phase_hides_what_is_above_it() -> None:
    part = SimpleNamespace(id="R1", kind=ReleaseKind.PART, status=ReleaseStatus.CONDITIONAL)
    a = SimpleNamespace(key=SimpleNamespace(id="J1.WORK.0"), job="J1", line=1, status="COMPLETE", actual_end=None)
    b = SimpleNamespace(key=SimpleNamespace(id="J1.WORK.1"), job="J1", line=1, status="OPEN", actual_end=None)
    snap = SimpleNamespace(phases=(a, b), dispositions=(SimpleNamespace(job="J1", disposition=Disposition.ADMITTED),), releases=(part,),
                           edges=(SimpleNamespace(pred_phase="R1", succ_phase="J1.WORK.0", hard=True, type=EdgeType.FS),
                                  SimpleNamespace(pred_phase="J1.WORK.0", succ_phase="J1.WORK.1", hard=True, type=EdgeType.FS)))
    (g,) = line_graphs(snap)
    assert unreceived_ancestors(g, "J1.WORK.0") == {"R1"}                       # the completed phase still names its own input
    assert unreceived_ancestors(g, "J1.WORK.1") == frozenset()                  # but nothing above it gates the work below
