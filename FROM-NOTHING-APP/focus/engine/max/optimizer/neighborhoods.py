"""Bounded deterministic neighbourhood moves over dispatch priorities (S12 MAX step 7, P13).

Every candidate is a complete re-decode of the whole WIP through the same ledger, graph,
calendar and crew interfaces, so a move can never bypass a constraint. A move only changes
dispatch order:

* ``swap``: exchange the ranks of two jobs that compete for the same team;
* ``boost``: move one job ahead of the jobs it competes with, for example a deferred job
  or one with a crew-continuity break;
* ``line``: move every job of one aircraft ahead (coupled descendant cone).

When the incumbent has long waits (phases that waited more than a shift past their earliest start), half of the moves
boost one of those jobs ahead of the jobs it competes with, longest waits first in the draw.

Moves come from a seeded ``random.Random``, so a run is reproducible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Move:
    kind: str                      # swap | boost | line
    jobs: tuple[str, ...]
    note: str


@dataclass
class PriorityState:
    """Job dispatch ranks; lower runs first among ready phases (after defended commitments)."""

    ranks: dict[str, float] = field(default_factory=dict)

    def apply(self, move: Move, peers: dict[str, list[str]]) -> "PriorityState":
        ranks = dict(self.ranks)
        if move.kind == "swap" and len(move.jobs) == 2:
            a, b = move.jobs
            ranks[a], ranks[b] = ranks.get(b, 0.0), ranks.get(a, 0.0)
        elif move.kind in ("boost", "line"):
            for job in move.jobs:
                competitors = peers.get(job, [])
                floor = min((ranks.get(c, 0.0) for c in competitors), default=0.0)
                ranks[job] = min(ranks.get(job, 0.0), floor) - 1.0
        return PriorityState(ranks)


def generate(rng: random.Random, *, team_jobs: dict[str, list[str]], line_jobs: dict[int, list[str]], focus_jobs: list[str],
             wait_jobs: list[str] | None = None) -> Move | None:
    """One deterministic random move; ``focus_jobs`` (deferred or continuity breaks) are preferred for boosts and
    ``wait_jobs`` (longest waits first) get half of the moves when there are any."""
    if wait_jobs and rng.random() < 0.5:
        job = wait_jobs[min(rng.randrange(len(wait_jobs)), rng.randrange(len(wait_jobs)))]     # leans to the longest waits
        return Move("boost", (job,), "boost a job that waited long for its team")
    choices = []
    if focus_jobs:
        choices.append("boost")
    if any(len(v) >= 2 for v in team_jobs.values()):
        choices.append("swap")
    if len(line_jobs) >= 2:
        choices.append("line")
    if not choices:
        return None
    kind = rng.choice(sorted(choices))
    if kind == "boost":
        job = rng.choice(sorted(focus_jobs))
        return Move("boost", (job,), "boost a deferred or discontinuous job")
    if kind == "swap":
        team = rng.choice(sorted(t for t, v in team_jobs.items() if len(v) >= 2))
        a, b = rng.sample(sorted(team_jobs[team]), 2)
        return Move("swap", (a, b), f"swap dispatch order within team {team}")
    line = rng.choice(sorted(line_jobs))
    return Move("line", tuple(sorted(line_jobs[line])), f"advance aircraft {line}")
