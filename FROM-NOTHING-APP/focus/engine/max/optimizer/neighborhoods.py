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
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Move:
    kind: str                      # swap | boost | line
    jobs: tuple[str, ...]
    note: str


class Competition:
    """Which jobs compete for the same team: team -> jobs (in placement order) and job -> teams.

    A job's competitors are the other jobs sharing any of its teams. They are never listed: ``PriorityState.apply``
    needs only the lowest rank among them, which it computes from the ranked jobs alone (an unranked competitor counts
    as rank 0.0), so a fleet with thousands of jobs per team costs nothing to set up.
    """

    def __init__(self, team_jobs: dict[str, list[str]]):
        self.team_jobs = team_jobs
        self.team_size = {team: len(jobs) for team, jobs in team_jobs.items()}
        self.job_teams: dict[str, tuple[str, ...]] = {}
        teams_of: dict[str, list[str]] = defaultdict(list)
        for team, jobs in team_jobs.items():
            for job in jobs:
                teams_of[job].append(team)
        self.job_teams = {job: tuple(teams) for job, teams in teams_of.items()}


class _TeamLows:
    """Per team: how many jobs are ranked, and the lowest and second-lowest (rank, job) among them."""

    def __init__(self, competition: Competition, ranks: dict[str, float]):
        self.competition = competition
        self.count: dict[str, int] = defaultdict(int)
        self.lows: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for job, rank in ranks.items():
            for team in competition.job_teams.get(job, ()):
                self.count[team] += 1
                low = self.lows[team]
                low.append((rank, job))
                low.sort()
                del low[2:]

    def floor(self, job: str, ranks: dict[str, float]) -> float:
        """min(ranks.get(c, 0.0) for c in competitors(job)), and 0.0 when the job has no competitor."""
        teams = self.competition.job_teams.get(job, ())
        found: list[float] = []
        ranked = job in ranks
        for team in teams:
            others = self.competition.team_size[team] - 1
            if others <= 0:
                continue
            if others > self.count[team] - (1 if ranked else 0):
                found.append(0.0)                               # an unranked competitor
            for rank, other in self.lows[team]:
                if other != job:
                    found.append(rank)
                    break
        return min(found) if found else 0.0

    def lowered(self, job: str, rank: float, was_ranked: bool) -> None:
        """``job`` now has ``rank``, below every competitor's: it is the lowest in each of its teams."""
        for team in self.competition.job_teams.get(job, ()):
            if not was_ranked:
                self.count[team] += 1
            low = self.lows[team]
            rest = [entry for entry in low if entry[1] != job][:1]
            self.lows[team] = [(rank, job)] + rest


@dataclass
class PriorityState:
    """Job dispatch ranks; lower runs first among ready phases (after defended commitments)."""

    ranks: dict[str, float] = field(default_factory=dict)

    def apply(self, move: Move, competition: Competition) -> "PriorityState":
        ranks = dict(self.ranks)
        if move.kind == "swap" and len(move.jobs) == 2:
            a, b = move.jobs
            ranks[a], ranks[b] = ranks.get(b, 0.0), ranks.get(a, 0.0)
        elif move.kind in ("boost", "line"):
            lows = _TeamLows(competition, ranks)
            for job in move.jobs:
                floor = lows.floor(job, ranks)
                was_ranked = job in ranks
                ranks[job] = min(ranks.get(job, 0.0), floor) - 1.0
                lows.lowered(job, ranks[job], was_ranked)
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
