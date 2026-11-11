"""What waits on a planned job: the facts behind a job's priority within its shift (reported; no score reads them).

Two planned jobs of the same size are not equally important to the flow. One may hold up other jobs that are planned
for this shift or the next; another has nothing after it. The dependency graph and the plan say which:

* ``dependents``: the phases of *other* jobs that cannot start until this job is released (through its own later
  phases, its inspections and its RELEASE node);
* ``gated``: those dependents that the plan has placed before the horizon ends (this shift and the next), with their
  standard person-minutes, in total and for other teams. If this job is not done, that work is blocked;
* ``tier``: ``MUST`` when planned work waits on the job inside the horizon, ``FILL`` when nothing depends on it at
  all, ``PLAN`` otherwise.

These are facts of the accepted plan and the graph, the same for every reader. The aircraft's own cost of delay is a
separate fact (``valuation.criticality``). Whether a tier should weight a team's score is the owner's decision
(DECISION_PLAYBOOK.md, DEC-33).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..domain import PhaseKind

MUST, PLAN, FILL = "MUST", "PLAN", "FILL"
TIERS = (MUST, PLAN, FILL)
GATE_HORIZON_MINUTES = 480          # work planned up to one shift past this one counts as waiting on the job


@dataclass(frozen=True, slots=True)
class JobPriority:
    phase: str
    tier: str                       # MUST | PLAN | FILL
    dependents: int                 # phases of other jobs that wait on this job, planned or not
    gated_jobs: int                 # other jobs planned inside the horizon that wait on this job
    gated_minutes: int              # their standard person-minutes
    gated_other_team_minutes: int   # the part of it that belongs to other teams
    tail_minutes: int               # work on the longest chain after this phase (the engine's CPM tail)


def dependents(ctx: Any, phase_id: str) -> tuple[str, ...]:
    """The first phases of other jobs downstream of ``phase_id``: reached through this job's own later phases and
    through RELEASE nodes, never through another job's work."""
    phases = ctx.phases
    node = phases.get(phase_id)
    if node is None:
        return ()
    found: set[str] = set()
    seen: set[str] = set()
    frontier = [e.succ_phase for e in ctx.succs.get(phase_id, ())]
    while frontier:
        n = frontier.pop()
        if n in seen:
            continue
        seen.add(n)
        target = phases.get(n)
        if target is None:
            continue
        if target.kind is PhaseKind.RELEASE or target.job == node.job:
            frontier.extend(e.succ_phase for e in ctx.succs.get(n, ()))
        else:
            found.add(n)
    return tuple(sorted(found))


def _team(phase: Any) -> str | None:
    return phase.role_demands[0].team if phase.role_demands else None


def plan_priorities(ctx: Any, starts: Mapping[str, int], phase_ids: Iterable[str], horizon_end: int) -> dict[str, JobPriority]:
    """The priority facts of each phase in ``phase_ids``. ``starts`` gives every placed phase's planned start minute;
    a dependent counts as gated when the plan starts it before ``horizon_end``."""
    out: dict[str, JobPriority] = {}
    for pid in phase_ids:
        node = ctx.phases.get(pid)
        if node is None:
            continue
        deps = dependents(ctx, pid)
        soon = [q for q in deps if q in starts and starts[q] < horizon_end]
        team = _team(node)
        minutes = sum(int(ctx.phases[q].standard_person_minutes or 0) for q in soon)
        other = sum(int(ctx.phases[q].standard_person_minutes or 0) for q in soon if _team(ctx.phases[q]) != team)
        out[pid] = JobPriority(pid, MUST if soon else (FILL if not deps else PLAN), len(deps), len({ctx.phases[q].job for q in soon}),
                               minutes, other, int(ctx.tails.get(pid, 0)))
    return out
