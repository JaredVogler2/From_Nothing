"""Bounded delay repair (S12 MAX step 6, P13).

The decoder already moves a phase to the next truly eligible window when its first window
cannot hold it, and only within admitted calendar bounds. Repair adds bounded re-dispatch
for jobs that remain unbound for resource reasons: each such job is boosted once ahead of
its competitors and the whole WIP is re-decoded. The result is kept only when the exact
comparison improves. A job that cannot be bound keeps its verified deferral cause. Repair
never adds a fallback crew, relaxes a cap or voids a precedence edge.
"""
from __future__ import annotations

from typing import Mapping

RESOURCE_REASONS = ("NO_FEASIBLE_PLACEMENT", "CREW_", "ACTIVATION_CAP", "PERSON_OVERLAP", "TRAVEL_INFEASIBLE", "REGULAR_CAP", "OT_CAP")


def repairable_jobs(deferred: Mapping[str, str], phase_job: Mapping[str, str]) -> list[str]:
    """Jobs whose deferral is a resource conflict (not a source/safety block)."""
    jobs = set()
    for pid, reason in deferred.items():
        if reason.startswith("PREDECESSOR_UNPLACED"):
            continue
        if any(code in reason for code in RESOURCE_REASONS):
            job = phase_job.get(pid)
            if job:
                jobs.add(job)
    return sorted(jobs)
