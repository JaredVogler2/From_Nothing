"""Exact complete-fleet candidate evaluation (S12 MODELED PENALTY, P12).

* **Completion scope.** An aircraft completes only when every required delivery-scope job
  on that line is released. That means every job that is not a verified-complete anchor,
  including blocked, quarantined and deferred jobs; it is never the maximum of the
  successfully scheduled subset. Any unreleased required job makes the completion
  UNKNOWN. A completion that relies on forecast-only evidence (conditional part ETA,
  nominal or virtual people) is CONDITIONAL and labelled.
* **Cost per aircraft.** The exact modeled penalty at that completion
  (``EconomicsRegistry.penalty``) is split into the unavoidable exposure (the penalty even
  at the cutoff) and the controllable increment. The accrued accounting floor and the
  assumed-versus-actual authority are reported, never merged.
* **Fleet total.** The total is known only when every aircraft is known. An unknown
  aircraft is listed and never counted as zero; a known-portfolio comparison is possible,
  but complete-fleet optimality is not claimed.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Mapping

from ..contracts.planning import AircraftCompletion, CostSummary, Job, JobDisposition, LineCost
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
            reasons.append(f"accrued accounting floor {econ.accrued_floor_cents} cents reported separately")  # type: ignore[union-attr]
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
