"""Duration.resolve: consumed, unit-correct elapsed and crew estimates (S08, P07).

* Baseline: ``FOCUS_FLOW_BUDGET`` by exact JOB_NUMBER+LINE_NUMBER. Every field is
  kept (CRITICAL_FLOW, TOTAL/PRIMARY/ASSIST, total labor). Crew demand is
  TOTAL_MECHANICS under the admitted SIMULTANEOUS semantics. Missing, zero or
  negative counts are unavailable; they are never defaulted or clamped to 1, and
  TOTAL is never silently replaced by PRIMARY.
* Rework (NC/PU/CI): the selected statistic (default P75) of comparable
  completed elapsed-active episodes. Group fallback: the exact context (class,
  team, model, crew), then the approved broader groups of ``duration.group_fallback``
  ((class, team), then (class)), then unavailable. Process and revision have no
  source column yet, so they are not matched. There is no flat 60 and no
  person-effort percentile divided by crew.
* In-progress jobs: the remaining elapsed from sourced work progress. Actual
  performed work is preserved, never restarted.
* Standard person effort = elapsed x crew (explicit fixture policy for
  simultaneous work).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

from ..contracts.planning import DurationEstimate, FlowBudget, Job
from ..domain import digest_id
from ..policy import Policy
from .duration_gauge import DurationStatistics, exact_group, scheduled_ticks


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FlowIndex(dict):
    """Exact (LINE_NUMBER, JOB_NUMBER) -> FlowBudget; ``conflicts`` lists keys whose rows disagree.

    The lookup is composite: the same JOB_NUMBER on two lines is two independent standards.
    Repeated rows with identical values deduplicate (the latest LOAD_TIMESTAMP is kept); rows
    that disagree on any standard field are excluded and reported, never "last row wins".
    """

    def __init__(self) -> None:
        super().__init__()
        self.conflicts: dict[tuple[int, str], str] = {}


_STANDARD_FIELDS = ("critical_flow_minutes", "total_mechanics", "primary_mechanic_count", "primary_mechanic_duration", "assist_count",
                    "assist_labor_minutes", "total_labor_minutes", "duration_source")


def flow_index(rows: Iterable[Mapping[str, Any]]) -> FlowIndex:
    out = FlowIndex()
    for row in rows:
        key = (int(row["LINE_NUMBER"]), str(row["JOB_NUMBER"]))
        budget = FlowBudget(
            job_number=str(row["JOB_NUMBER"]),
            line=int(row["LINE_NUMBER"]),
            critical_flow_minutes=_opt_int(row.get("CRITICAL_FLOW_MINUTES")),
            total_mechanics=_opt_int(row.get("TOTAL_MECHANICS")),
            primary_mechanic_count=_opt_int(row.get("PRIMARY_MECHANIC_COUNT")),
            primary_mechanic_duration=_opt_int(row.get("PRIMARY_MECHANIC_DURATION")),
            assist_count=_opt_int(row.get("ASSIST_COUNT")),
            assist_labor_minutes=_opt_int(row.get("ASSIST_LABOR_MINUTES")),
            total_labor_minutes=_opt_int(row.get("TOTAL_LABOR_MINUTES")),
            duration_source=str(row["DURATION_SOURCE"]) if row.get("DURATION_SOURCE") is not None else None,
            load_timestamp=str(row["LOAD_TIMESTAMP"]) if row.get("LOAD_TIMESTAMP") is not None else None,
        )
        if key in out.conflicts:
            continue
        held = out.get(key)
        if held is None:
            out[key] = budget
        elif any(getattr(held, f) != getattr(budget, f) for f in _STANDARD_FIELDS):
            diff = [f for f in _STANDARD_FIELDS if getattr(held, f) != getattr(budget, f)]
            out.conflicts[key] = f"FLOW_CONFLICT: FOCUS_FLOW_BUDGET rows for {key[1]}@{key[0]} disagree on {', '.join(diff)}"
            del out[key]
        elif (budget.load_timestamp or "") > (held.load_timestamp or ""):
            out[key] = budget
    return out


@dataclass(frozen=True)
class DurationResolution:
    estimate: DurationEstimate | None
    blocked: tuple[str, ...]
    executable_only: tuple[str, ...]


def crew_demand(budget: FlowBudget | None) -> tuple[int | None, str | None]:
    if budget is None:
        return None, "STANDARD_MISSING: no FOCUS_FLOW_BUDGET row for this exact job/line"
    total = budget.total_mechanics
    if total is None or total <= 0:
        return None, f"FLOW_CREW_INVALID: TOTAL_MECHANICS={total!r} (no default or clamp to 1)"
    return total, None


def resolve(job: Job, *, status_class: str, policy: Policy, mode: str, stats: Mapping[tuple[str, ...], DurationStatistics],
            snapshot_key: str, flow_problem: str | None = None) -> DurationResolution:
    statistic = policy.str("duration.default_statistic")
    rework_classes = set(policy.seq("duration.rework_classes"))
    fallback = tuple(policy.seq("duration.group_fallback"))
    budget = job.raw_budget
    blocked: list[str] = []
    executable_only: list[str] = []
    crew, crew_problem = crew_demand(budget)
    if flow_problem:
        blocked.append(flow_problem)
    elif crew_problem:
        blocked.append(crew_problem)
    semantics = policy.str("duration.total_mechanics_semantics")
    if semantics != "SIMULTANEOUS":
        blocked.append(f"FLOW_SEMANTICS_{semantics}: role phases required for sequential assignment counts")
    if mode == "live" and policy.authority_of("duration.total_mechanics_semantics") == "open_decision":
        executable_only.append("FLOW_SEMANTICS_UNCONFIRMED: TOTAL_MECHANICS simultaneity not admitted by the producer (OD-01)")
    elapsed: int | None = None
    precise: Decimal | None = None
    basis = "FLOW_CRITICAL_MINUTES"
    stats_id = None
    method = "FOCUS_FLOW_BUDGET.CRITICAL_FLOW_MINUTES by exact JOB_NUMBER+LINE_NUMBER"
    uncertainty = "standard"
    if status_class == "IN_PROGRESS" and job.remaining_elapsed is not None and job.remaining_elapsed > 0:
        # remaining actual work is preserved (S08), for rework as for any job: never the full estimate again
        elapsed = job.remaining_elapsed
        basis = "REMAINING_ELAPSED"
        method = f"sourced remaining elapsed after performed {job.performed_elapsed} minutes"
    elif job.rework_class in rework_classes:
        chosen = None
        exact = exact_group(job.rework_class, job.team or "", job.model, crew)
        keys = [] if exact is None else [exact]
        keys += [(job.rework_class, job.team or "") if level == "CLASS_TEAM" else (job.rework_class,) for level in fallback]
        for key in keys:
            candidate = stats.get(key)
            if candidate is not None and candidate.usable:
                chosen = candidate
                break
        if chosen is None:
            blocked.append(f"DURATION_UNAVAILABLE: no sufficient, current comparable {job.rework_class} cohort (no flat default)")
        else:
            precise = chosen.statistic(statistic)
            elapsed = scheduled_ticks(precise)
            basis = "ELAPSED_ACTIVE_EPISODES"
            stats_id = chosen.id
            method = (f"{statistic} of {chosen.admitted_count} completed {'/'.join(chosen.group)} elapsed episodes "
                      f"({'exact context' if chosen.group == exact else 'approved broader group'})")
            ci = (f"; P75 {chosen.confidence_level} interval [{chosen.p75_ci_low}, {chosen.p75_ci_high}]"
                  if chosen.p75_ci_low is not None else "; P75 interval unavailable")
            uncertainty = (f"n={chosen.admitted_count} window {chosen.sample_start:%Y-%m-%d}..{chosen.sample_end:%Y-%m-%d}; "
                           f"P50={chosen.p50} P55={chosen.p55} mean={chosen.mean} P75={chosen.p75} P90={chosen.p90}{ci}")
    elif status_class == "IN_PROGRESS":
        if job.remaining_elapsed is None:
            blocked.append("REMAINING_UNKNOWN: in-progress job without sourced remaining elapsed work")
        else:
            blocked.append("REMAINING_NONPOSITIVE: sourced remaining work is not positive; completion needs verified evidence")
    else:
        flow = budget.critical_flow_minutes if budget else None
        if budget is not None and (flow is None or flow <= 0):
            blocked.append(f"STANDARD_INVALID: CRITICAL_FLOW_MINUTES={flow!r}")
        elif budget is not None:
            elapsed = flow
    if elapsed is None or crew is None or blocked:
        return DurationResolution(None, tuple(blocked), tuple(executable_only))
    effort_basis = policy.str("duration.standard_effort_basis")
    if effort_basis == "ELAPSED_TIMES_CREW":
        effort = elapsed * crew
    elif effort_basis == "TOTAL_LABOR_MINUTES" and budget and budget.total_labor_minutes:
        effort = budget.total_labor_minutes
    else:
        return DurationResolution(None, (f"STANDARD_EFFORT_UNAVAILABLE: basis {effort_basis}",), tuple(executable_only))
    estimate = DurationEstimate(
        id=digest_id("DUR", snapshot_key, job.id, statistic),
        job=job.id,
        elapsed_minutes=elapsed,
        precise_elapsed=precise,
        standard_person_minutes=effort,
        measurement_basis=basis,
        stats_id=stats_id,
        statistic=statistic if stats_id else None,
        crew_profile="SIMULTANEOUS",
        crew_size=crew,
        method=method,
        uncertainty=uncertainty,
        source_hash=digest_id("SRC", budget.job_number if budget else job.key.native_id, job.line, budget.load_timestamp if budget else None),
        policy_id=policy.id,
    )
    return DurationResolution(estimate, (), tuple(executable_only))
