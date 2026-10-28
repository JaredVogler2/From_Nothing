"""Aircraft projections from a plan (S12 MAX step 5 and delivery hysteresis, P14).

One authority turns complete-scope aircraft completions into:

* completion minutes for ``EconomicsRegistry.derive_priority_weights`` (MAX step 5);
* local calendar-day delivery projections and safety alerts for the delivery-commitment ledger.

Only accepted plans of the policy's ``forecast.delivery_commitment_purpose`` feed the ledger.
Publication (P16) calls ``record_accepted`` at acceptance; UI refreshes never do. An unknown
completion stays ``None``: it never becomes a date, a zero or a dropped aircraft.
"""
from __future__ import annotations

from datetime import date, tzinfo
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..contracts.planning import AircraftCompletion, CandidatePlan
from ..domain import to_minute
from .commitments import DeliveryCommitments, DeliveryReceipt

SAFETY_OUTCOMES = frozenset({"UNSAFE"})


def completion_minutes(completions: Mapping[int, AircraftCompletion]) -> dict[int, int | None]:
    return {line: (to_minute(c.completion_at) if c.completion_at is not None else None) for line, c in completions.items()}


def delivery_projections(plan: CandidatePlan, tz: tzinfo) -> dict[int, date | None]:
    """Local calendar date of every aircraft's complete-scope completion; unknown stays None."""
    return {c.line: (c.completion_at.astimezone(tz).date() if c.completion_at is not None and c.status != "unknown" else None)
            for c in plan.aircraft_completion}


def safety_alerts(plan: CandidatePlan, job_lines: Mapping[str, int]) -> dict[int, str]:
    """Per aircraft, the commitments a source safety fact invalidated (late part, expiry, calendar, ...)."""
    found: dict[int, set[str]] = {}
    for r in plan.commitment_results:
        if r.outcome in SAFETY_OUTCOMES and r.job in job_lines:
            found.setdefault(job_lines[r.job], set()).add(r.reason)
    return {line: ("COMMITMENT_UNSAFE: " + " | ".join(sorted(reasons)))[:480] for line, reasons in sorted(found.items())}


def record_accepted(services: Any, plan: CandidatePlan, snapshot: Any, *, rejected: bool = False) -> DeliveryReceipt:
    """Feed one accepted plan to the delivery-commitment ledger; replays return the stored receipt."""
    policy = services.policy
    if plan.purpose.value != policy.str("forecast.delivery_commitment_purpose"):
        return DeliveryReceipt(plan.id, plan.scope, "NOT_APPLICABLE", (), ())
    ledger = DeliveryCommitments(services.repositories, plan.scope, policy)
    tz = ZoneInfo(policy.str("shift.timezone"))
    job_lines = {j.id: j.line for j in snapshot.jobs}
    return ledger.record_accepted_plan(plan.id, delivery_projections(plan, tz), rejected=rejected, safety_alerts=safety_alerts(plan, job_lines))
