"""Complete WIP census and job records (S01, S05, P03).

The census covers every job returned by the broad WIP/anchor query: all
statuses, including COMPLETE anchors, on every line with any non-COMPLETE
work. There is no fixed line cap, no top-50 and no economics-mapping-only
census. Each job is reconciled as admitted, blocked, quarantined, explicitly
excluded or verified complete, with a source-backed reason. A narrower
operational scope requires an explicit scope receipt row. Native keys are
preserved exactly, and a source omission of revision/operation stays an
omission.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from ..contracts.planning import FlowBudget, InspectionTrigger, Job
from ..domain import EvidenceRef, JobKey, ensure_unique_ids
from ..policy import Policy
from .sourcetime import SourceTimeError, TimezonePolicy

BLOCKING_CLASSES = {
    "BLOCKED_HOLD": "STATUS_ON_HOLD",
    "BLOCKED_SOURCE": "STATUS_SOURCE_FAILED",
    "BLOCKED_UNRELEASED": "STATUS_UNRELEASED",
}


@dataclass(frozen=True)
class ScopeConfig:
    site: str
    program: str
    basis: str
    line_admission: dict[int, tuple[str, str]]
    evidence: str


def scope_config(rows: Iterable[Mapping[str, Any]], *, default_site: str, default_program: str, policy: Policy) -> ScopeConfig:
    rows = list(rows)
    if not rows:
        return ScopeConfig(default_site, default_program, policy.str("scope.default_basis"), {}, "default ALL_WIP (no narrowing receipt)")
    site, program, basis, evidence = default_site, default_program, policy.str("scope.default_basis"), ""
    per_line: dict[int, tuple[str, str]] = {}
    for row in rows:
        if row.get("LINE_NUMBER") is None:
            site = str(row.get("SITE") or site)
            program = str(row.get("PROGRAM") or program)
            basis = str(row.get("SCOPE_BASIS") or basis)
            evidence = str(row.get("EVIDENCE_REF") or "")
        else:
            per_line[int(row["LINE_NUMBER"])] = (str(row.get("ADMISSION") or "ADMIT"), str(row.get("REASON") or ""))
    return ScopeConfig(site, program, basis, per_line, evidence)


@dataclass
class CensusBuild:
    jobs: dict[str, Job] = field(default_factory=dict)
    job_index: dict[tuple[int, str], str] = field(default_factory=dict)
    status_class: dict[str, str] = field(default_factory=dict)
    raw_status: dict[str, str] = field(default_factory=dict)
    blocked: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    executable_only: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    conditional: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    quarantined: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    excluded: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    verified_complete: set[str] = field(default_factory=set)
    parent_of: dict[str, str | None] = field(default_factory=dict)
    line_meta: dict[int, dict[str, Any]] = field(default_factory=dict)
    status_counts: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))
    problems: list[str] = field(default_factory=list)

    def label(self, job_id: str) -> str:
        job = self.jobs.get(job_id)
        return job.key.label() if job else job_id


def index_master(rows: Iterable[Mapping[str, Any]], scope: ScopeConfig) -> tuple[dict[tuple[int, str], str], dict[str, JobKey], set[tuple[int, str]]]:
    """First pass: lossless keys and duplicates (duplicates are quarantined, never collapsed)."""
    keys: dict[str, JobKey] = {}
    index: dict[tuple[int, str], str] = {}
    seen: Counter = Counter()
    for row in rows:
        seen[(int(row["LINE_NUMBER"]), str(row["SHOP_ORDER_IDENTIFIER"]))] += 1
    duplicates = {k for k, n in seen.items() if n > 1}
    for (line, native) in seen:
        key = JobKey(scope.site, scope.program, line, native)
        keys[key.id] = key
        index[(line, native)] = key.id
    ensure_unique_ids(keys.values(), "job")
    return index, keys, duplicates


def build_census(*, master_rows: list[Mapping[str, Any]], scope: ScopeConfig, policy: Policy, tzp: TimezonePolicy,
                 flow: Mapping[tuple[int, str], FlowBudget], rework: Mapping[tuple[int, str], Mapping[str, Any]],
                 progress: Mapping[tuple[int, str], Mapping[str, Any]], triggers: Mapping[str, InspectionTrigger],
                 job_index: Mapping[tuple[int, str], str], keys: Mapping[str, JobKey], duplicates: set[tuple[int, str]],
                 dataset_id: str, run_id: str, cutoff: datetime) -> CensusBuild:
    out = CensusBuild()
    out.job_index = dict(job_index)
    classes: Mapping[str, str] = policy.map("scope.status_classes")
    for ordinal, row in enumerate(master_rows):
        line = int(row["LINE_NUMBER"])
        native = str(row["SHOP_ORDER_IDENTIFIER"])
        job_id = job_index[(line, native)]
        status = str(row["SHOP_ORDER_STATUS"])
        out.status_counts[line][status] += 1
        meta = out.line_meta.setdefault(line, {"model": None, "customer": None})
        meta["model"] = meta["model"] or row.get("MINOR_MODEL_IDENT")
        meta["customer"] = meta["customer"] or row.get("CUSTOMER_CODE")
        if job_id in out.jobs:
            out.quarantined[job_id].append("DUPLICATE_SOURCE_KEY: line/SOI appears more than once in the census")
            continue
        if (line, native) in duplicates:
            out.quarantined[job_id].append("DUPLICATE_SOURCE_KEY: line/SOI appears more than once in the census")
        try:
            earliest = tzp.parse_local("master_task_list", row.get("FIRST_AVAILABLE"))
            actual_start = tzp.parse_local("master_task_list", row.get("ACT_START"))
            actual_complete = tzp.parse_local("master_task_list", row.get("ACT_COMP"))
        except SourceTimeError as exc:
            earliest = actual_start = actual_complete = None
            out.quarantined[job_id].append(f"{exc.code}: {exc.message}")
        prog = progress.get((line, native))
        rw = rework.get((line, native))
        parent_native = row.get("PARENT_SOI")
        parent_key = None
        if parent_native:
            parent_id = job_index.get((line, str(parent_native)))
            if parent_id is not None:
                parent_key = keys[parent_id]
            out.parent_of[job_id] = parent_id
        else:
            out.parent_of[job_id] = None
        job = Job(
            key=keys[job_id],
            status=status,
            team=str(row["TEAM"]) if row.get("TEAM") is not None else None,
            station=str(row["CURRENT_CONTROL_STATION"]) if row.get("CURRENT_CONTROL_STATION") is not None else None,
            model=str(row["MINOR_MODEL_IDENT"]) if row.get("MINOR_MODEL_IDENT") is not None else None,
            customer=str(row["CUSTOMER_CODE"]) if row.get("CUSTOMER_CODE") is not None else None,
            description=str(row["SHOP_ORDER_DESCRIPTION"]) if row.get("SHOP_ORDER_DESCRIPTION") is not None else None,
            parent_native=str(parent_native) if parent_native else None,
            parent_key=parent_key,
            rework_class=str(rw["REWORK_CLASS"]) if rw else None,
            defect_origin_team=str(rw["DEFECT_ORIGIN_TEAM"]) if rw and rw.get("DEFECT_ORIGIN_TEAM") else None,
            raw_budget=flow.get((line, native)),
            earliest=earliest,
            actual_start=actual_start,
            actual_complete=actual_complete,
            performed_elapsed=int(prog["PERFORMED_ELAPSED_MINUTES"]) if prog else None,
            remaining_elapsed=int(prog["REMAINING_ELAPSED_MINUTES"]) if prog else None,
            inspection_trigger=triggers.get(job_id),
            is_rework=bool(row["IS_REWORK"]) if row.get("IS_REWORK") is not None else None,
            planning_system=str(row["PLANNING_SYSTEM_NAME"]) if row.get("PLANNING_SYSTEM_NAME") is not None else None,
            evidence=(EvidenceRef(dataset_id, f"master_task_list#{ordinal}", source_run_id=run_id),),
        )
        out.jobs[job_id] = job
        out.raw_status[job_id] = status
        admission = scope.line_admission.get(line)
        if admission is not None and admission[0] == "EXCLUDE":
            out.excluded[job_id].append(f"SCOPE_EXCLUDED: {admission[1] or 'explicit scope receipt'}")
        klass = classes.get(status)
        if klass is None:
            out.status_class[job_id] = "UNKNOWN"
            out.quarantined[job_id].append(f"UNKNOWN_STATUS: {status!r} has no admitted status semantics")
            continue
        out.status_class[job_id] = klass
        if klass == "TERMINAL_COMPLETE":
            out.verified_complete.add(job_id)
        elif klass in BLOCKING_CLASSES:
            out.blocked[job_id].append(f"{BLOCKING_CLASSES[klass]}: source status {status}")
        elif klass == "OPEN_FORECAST_ONLY":
            out.executable_only[job_id].append(f"STATUS_FORECAST_ONLY: {status} is not released for executable work")
            out.conditional[job_id].append(f"status {status} is forecast-only")
        elif klass == "IN_PROGRESS" and job.actual_start is None:
            out.problems.append(f"{job.key.label()}: IN WORK without ACT_START")
        if job.is_rework and job.rework_class is None and klass not in ("TERMINAL_COMPLETE",):
            out.blocked[job_id].append("REWORK_CLASS_UNKNOWN: IS_REWORK without a sourced NC/PU/CI classification")
    return out
