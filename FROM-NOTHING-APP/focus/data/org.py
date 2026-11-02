"""The org chart above the teams: each team's superintendent over time, from ``FOCUS_PERSON_TEAM_MAP`` (D-25).

Every in-scope assignment row names a person's team and the superintendent it reports to, with an effective
interval. A team's superintendent at an instant is the one its members name at that instant. The source keeps one
superintendent per team in practice; when members disagree, the team counts toward the one most of them name (ties:
the first by name) and every lookup says so (``MIXED_SUPERINTENDENT``), so the data can be fixed at the source.

Acquisition stores one small ``OrgChart`` per source run (``org_charts``): per team, the intervals in which its
members' superintendents stay the same, with how many members name each. The business unit is the scope.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

ORG_CHARTS = "org_charts"
NO_SUPERINTENDENT = ""                       # a member whose row names none


@dataclass(frozen=True)
class OrgInterval:
    team: str
    start: datetime | None                   # None: since before any row
    end: datetime | None                     # None: still effective
    superintendents: dict[str, int]          # superintendent -> members naming it ("" = members naming none)


@dataclass(frozen=True)
class OrgChart:
    source_run_id: str
    scope: str
    people: int
    intervals: tuple[OrgInterval, ...]


def build_org_chart(rows: Iterable[tuple[str, str, str, datetime | None, datetime | None]], *, source_run_id: str, scope: str) -> OrgChart:
    """``rows`` are in-scope assignments (person, team, superintendent, start, end); exact repeats count once."""
    by_team: dict[str, set[tuple[str, str, datetime | None, datetime | None]]] = {}
    people: set[str] = set()
    for person, team, superintendent, start, end in rows:
        if not team:
            continue
        people.add(person)
        by_team.setdefault(team, set()).add((person, superintendent or NO_SUPERINTENDENT, start, end))
    intervals: list[OrgInterval] = []
    for team in sorted(by_team):
        members = by_team[team]
        cuts = sorted({t for _, _, s, e in members for t in (s, e) if t is not None})
        edges: list[datetime | None] = [None, *cuts, None]
        previous: OrgInterval | None = None
        for lo, hi in pairwise(edges):
            counts: dict[str, int] = {}
            for person, superintendent, s, e in members:
                if (s is None or (lo is not None and s <= lo)) and (e is None or (hi is not None and hi <= e)):
                    counts[superintendent] = counts.get(superintendent, 0) + 1
            if not counts:
                previous = None
                continue
            if previous is not None and previous.superintendents == counts and previous.end == lo:
                previous = OrgInterval(team, previous.start, hi, counts)
                intervals[-1] = previous
                continue
            previous = OrgInterval(team, lo, hi, dict(sorted(counts.items())))
            intervals.append(previous)
    return OrgChart(source_run_id, scope, len(people), tuple(intervals))


def superintendent_at(chart: OrgChart | None, team: str, at: datetime) -> tuple[str | None, str | None]:
    """The superintendent ``team`` reports to at ``at`` and a note when that is not plain (None when nothing is clear)."""
    if chart is None:
        return None, "NO_ORG_CHART: no org chart was admitted with the source run"
    for interval in chart.intervals:
        if interval.team == team and (interval.start is None or interval.start <= at) and (interval.end is None or at < interval.end):
            named = {k: v for k, v in interval.superintendents.items() if k != NO_SUPERINTENDENT}
            if not named:
                return None, "NO_SUPERINTENDENT: no member of the team names a superintendent"
            best = sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))
            if len(best) > 1:
                return best[0][0], "MIXED_SUPERINTENDENT: members name " + ", ".join(f"{k} ({v})" for k, v in best)
            return best[0][0], None
    return None, "TEAM_NOT_IN_ORG_CHART: no member of the team was assigned at the shift start"
