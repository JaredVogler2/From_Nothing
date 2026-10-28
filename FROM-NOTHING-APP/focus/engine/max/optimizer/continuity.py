"""Crew continuity across a job's consecutive work segments (S12 MAX step 7, P13).

Continuity is a preference, never a constraint. Crew selection already ranks same-aircraft
people ahead (affinity: no transfer). This module measures the remaining breaks, where
consecutive WORK segments of one job use different crews, and proposes those jobs as
neighbourhood focus. A candidate with fewer breaks wins only after feasibility, commitment
defense, exact economics and non-value-added minutes are equal.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Mapping

from ....domain import PhaseKind


def continuity_breaks(placements: Mapping[str, object]) -> dict[str, int]:
    """job -> number of crew changes between consecutive WORK segments."""
    by_job: dict[str, list] = defaultdict(list)
    for p in placements.values():
        if getattr(p, "kind", None) is PhaseKind.WORK and getattr(p, "crew", ()):
            by_job[p.job].append(p)                                             # type: ignore[attr-defined]
    out: dict[str, int] = {}
    for job, segs in by_job.items():
        segs.sort(key=lambda p: p.start)
        out[job] = sum(1 for a, b in zip(segs, segs[1:]) if set(a.crew) != set(b.crew))
    return out
