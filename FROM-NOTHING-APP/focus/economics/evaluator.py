"""Exact complete-fleet candidate evaluation (S12 MODELED PENALTY, P12).

* **Completion scope.** An aircraft completes only when every required delivery-scope job
  on that line is released. That means every job that is not a verified-complete anchor,
  including blocked, quarantined and deferred jobs; it is never the maximum of the
  successfully scheduled subset. Any unreleased required job makes the completion
  UNKNOWN. A completion that relies on forecast-only evidence (conditional part ETA,
  nominal or virtual people) is CONDITIONAL and labelled.
* **Cost per aircraft.** The exact modeled penalty at that completion
  (``EconomicsRegistry.penalty``) is split into the unavoidable exposure (the penalty even
  at the cutoff) and the controllable increment. The accrued accounting floor bounds both
  from below, so the increment is only what remains controllable above it. The
  assumed-versus-actual authority is reported, never merged.
* **Fleet total.** The total is known only when every aircraft is known. An unknown
  aircraft is listed and never counted as zero; a known-portfolio comparison is possible,
  but complete-fleet optimality is not claimed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any, Iterable, Mapping

from ..contracts.planning import AircraftCompletion, CostSummary, Job, JobDisposition, JobOutcome, LineCost
from ..domain import Disposition, from_minute, to_minute
from .registry import EconomicsRegistry


@dataclass(frozen=True)
class JobRelease:
    job: str
    end: int | None                 # release minute; None when not released
    conditional: bool
    reason: str


def aircraft_completions(jobs: Iterable[Job], dispositions: Mapping[str, JobDisposition], releases: Mapping[str, JobRelease],
                         lines: Iterable[int]) -> dict[int, AircraftCompletion]:
    required: dict[int, list[str]] = {line: [] for line in lines}
    for job in jobs:
        disp = dispositions.get(job.id)
        if disp is not None and disp.disposition is Disposition.VERIFIED_COMPLETE:
            continue
        required.setdefault(job.line, []).append(job.id)
    out: dict[int, AircraftCompletion] = {}
    for line, ids in sorted(required.items()):
        unresolved = tuple(sorted(j for j in ids if releases.get(j) is None or releases[j].end is None))
        scheduled = len(ids) - len(unresolved)
        if not ids:
            out[line] = AircraftCompletion(line, "known", None, 0, 0, (), ("no open delivery scope (all verified complete)",))
            continue
        if unresolved:
            reasons = tuple(f"{j}: {releases[j].reason if j in releases else 'not released'}"[:200] for j in unresolved[:10])
            out[line] = AircraftCompletion(line, "unknown", None, len(ids), scheduled, unresolved, reasons)
            continue
        end = max(releases[j].end for j in ids)                      # type: ignore[type-var]
        conditional = any(releases[j].conditional for j in ids)
        out[line] = AircraftCompletion(line, "conditional" if conditional else "known", from_minute(end), len(ids), len(ids), (),
                                       ("relies on forecast-only evidence",) if conditional else ())
    return out


def line_costs(registry: EconomicsRegistry, completions: Mapping[int, AircraftCompletion], cutoff_minute: int) -> dict[int, LineCost]:
    out: dict[int, LineCost] = {}
    for line, comp in sorted(completions.items()):
        econ = registry.term(line)
        authority = econ.authority if econ else "UNKNOWN"
        ok, why = registry.supported(line)
        due = econ.due_at if econ else None
        if not ok:
            status = "unsupported" if econ is not None and econ.status == "UNSUPPORTED" else "unknown"
            out[line] = LineCost(line, status, authority, comp.completion_at, due, None, None, None, None, None, (why,))
            continue
        if comp.completion_at is None and comp.required_jobs == 0:
            out[line] = LineCost(line, "known", authority, None, due, 0, Fraction(0), Fraction(0), Fraction(0), Fraction(0),
                                 ("no open delivery scope",))
            continue
        if comp.status == "unknown" or comp.completion_at is None:
            unavoidable = registry.penalty(line, cutoff_minute)
            out[line] = LineCost(line, "unknown", authority, None, due, None, None, unavoidable, None,
                                 registry.marginal(line, max(cutoff_minute, to_minute(due))),       # type: ignore[arg-type]
                                 (f"COMPLETION_UNKNOWN: {len(comp.unresolved_jobs)} required job(s) unreleased",))
            continue
        completion = to_minute(comp.completion_at)
        total = registry.penalty(line, completion)
        unavoidable = registry.penalty(line, cutoff_minute)
        delay = max(0, completion - to_minute(due))                                                   # type: ignore[arg-type]
        reasons = [f"{authority} term"]
        if econ.accrued_floor_cents is not None:                                                     # type: ignore[union-attr]
            reasons.append(f"accrued accounting floor {econ.accrued_floor_cents} cents: the modeled exposure never counts below it")  # type: ignore[union-attr]
        if comp.status == "conditional":
            reasons.append("conditional completion: forecast-only evidence")
        out[line] = LineCost(line, comp.status, authority, comp.completion_at, due, delay, total, unavoidable,
                             total - unavoidable if total is not None and unavoidable is not None else None,
                             registry.marginal(line, completion), tuple(reasons))
    return out


def summarize(costs: Mapping[int, LineCost], currency: str = "USD") -> CostSummary:
    known = Fraction(0)
    conditional = Fraction(0)
    controllable = Fraction(0)
    unknown: list[int] = []
    for line, c in sorted(costs.items()):
        if c.status in ("unknown", "unsupported") or c.total_cents is None:
            unknown.append(line)
            continue
        if c.status == "conditional":
            conditional += c.total_cents
        else:
            known += c.total_cents
        controllable += c.controllable_cents or Fraction(0)
    return CostSummary(tuple(costs[k] for k in sorted(costs)), known, conditional, controllable, tuple(unknown), not unknown, currency)


# Reasons on an aircraft's cost line that concern its due date, and reasons that are information only (the term is usable).
# Every other reason on a line with no usable term concerns the rate or the term itself.
DUE_REASONS = frozenset({"DUE_UNKNOWN", "DUE_POLICY_UNSUPPORTED"})
INFORMATION_REASONS = frozenset({"ASSUMED_PLANNING_TERM", "TERM_AUTHORITY_UNPROVEN", "SALE_BASIS_CONFLICT", "CHECKPOINT_SEQUENCE_CONFLICT",
                                 "CAP_FIELD_NOT_APPLIED"})


_CODE = re.compile(r"[A-Z][A-Z0-9_]+")


def reason_codes(reasons: Iterable[str]) -> list[str]:
    """The reason codes in a line's or a job's reasons, in order: ``"A; B: detail"`` gives ``["A", "B"]``. A part that
    does not start with a code (free text, or the rest of a detail that holds a semicolon) is not a code."""
    codes = (part.split(":")[0].strip() for reason in reasons for part in reason.split(";"))
    return [code for code in codes if _CODE.fullmatch(code)]


def objective_status(costs: CostSummary, completions: Iterable[AircraftCompletion], outcomes: Iterable[JobOutcome],
                     planned_at: datetime) -> dict[str, Any]:
    """Did the cost objective steer this plan? Counts a person can check (docs/execution/OBJECTIVE_EXPLAINED.md, section 4).

    An aircraft's late-delivery cost is known only when its rate, its due date and its finish date are all known. The
    optimizer compares dollars only over the aircraft whose cost is known, so with none known every candidate ties on
    cost: the run still returns a plan, but it compared no dollars. Nothing here is estimated: an aircraft with no rate
    is listed as having none.
    """
    lines = list(costs.lines)
    completions = list(completions)
    priced = [c for c in lines if c.status in ("known", "conditional")]
    no_term: dict[int, list[str]] = {}                       # no usable rate or due date: the term did not resolve
    for c in lines:
        codes = reason_codes(c.reasons)
        if c.status not in ("known", "conditional") and "COMPLETION_UNKNOWN" not in codes:
            no_term[c.line] = [code for code in codes if code not in INFORMATION_REASONS]
    no_due = sorted(c.line for c in lines if c.due_at is None)
    no_rate = sorted(line for line, codes in no_term.items() if not codes or set(codes) - DUE_REASONS)
    no_cost: Counter[str] = Counter()
    for c in lines:
        if c.status not in ("known", "conditional"):
            no_cost[(no_term[c.line] or ["TERM_UNKNOWN"])[0] if c.line in no_term else "COMPLETION_UNKNOWN"] += 1
    finish = Counter(c.status for c in completions)
    unresolved = {job for c in completions for job in c.unresolved_jobs}
    why: Counter[str] = Counter()
    seen: set[str] = set()
    for o in outcomes:
        if o.job in unresolved:
            seen.add(o.job)
            why[(reason_codes(o.reasons) or [o.disposition.value.upper()])[0]] += 1
    if unresolved - seen:
        why["NOT_IN_THE_PLAN"] += len(unresolved - seen)
    n, k = len(lines), len(priced)
    steering = "full" if n and k == n else ("partial" if k else "none")
    if steering == "full":
        message = f"The cost objective compared all {n} aircraft."
    elif steering == "partial":
        message = (f"The cost objective compared {k} of {n} aircraft. The cost of the other {n - k} is unknown, so "
                   f"{'it is' if n - k == 1 else 'they are'} left out of the dollars.")
    else:
        message = (f"The cost objective did not steer this plan: the cost of all {n} aircraft is unknown, so every candidate tied on cost "
                   f"and the plan was chosen on the later levels (travel, waiting, overtime).")
    by_count = lambda counter: dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))   # noqa: E731
    return {"cost_steering": steering, "message": message, "aircraft": n, "cost_known": k,
            "rate_known": n - len(no_rate), "due_date_known": n - len(no_due),
            "finish_known": finish["known"], "finish_conditional": finish["conditional"], "finish_unknown": finish["unknown"],
            "past_due_when_planned": sum(1 for c in lines if c.due_at is not None and c.due_at <= planned_at),
            "aircraft_without_rate": no_rate, "aircraft_without_due_date": no_due,
            "aircraft_without_finish": sorted(c.line for c in completions if c.status == "unknown"),
            "cost_unknown_by_reason": by_count(no_cost),
            "unplanned_open_jobs": len(unresolved), "unplanned_open_jobs_by_reason": by_count(why)}
