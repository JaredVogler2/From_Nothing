"""Quality holds, final buyoffs and phase conservation (S10, P10).

* IP QA is 45 minutes TOTAL of active inspector effort, allocated across the
  sourced operation hold points (e.g. after 40 and 80 production minutes, with
  20 + 25 inspector minutes). It is never 45 per hold and never placed at an
  arbitrary midpoint. A required IP with an unknown hold location blocks the job.
* Final QA is a separate 45-minute inspection after all production/IP phases,
  finish-to-start before every successor.
* Other required buyoffs (manufacturing, quality, CC, ODA unit member, partner
  QT) are parallel branches. All of them feed one AND release milestone.
* Production elapsed and standard person effort are conserved across segments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..contracts.planning import InspectionHold, InspectionTrigger
from ..domain import EvidenceRef
from ..policy import Policy

OTHER_BUYOFF_COLUMNS = {
    "MANUFACTURING_INSP": "MANUFACTURING",
    "QUALITY_INSP": "QUALITY",
    "CC_INSP": "CC",
    "ODAUNITMEMBER_INSP": "ODAUNITMEMBER",
    "PARTNER_QT_INSP": "PARTNER_QT",
}


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_triggers(*, buyoff_rows: Iterable[Mapping[str, Any]], hold_rows: Iterable[Mapping[str, Any]], count_rows: Iterable[Mapping[str, Any]],
                   job_index: Mapping[tuple[int, str], str], policy: Policy, dataset_ids: Mapping[str, str], run_id: str) -> tuple[dict[str, InspectionTrigger], list[str]]:
    total_ip = policy.int("qa.total_ip_minutes")
    holds: dict[str, list[InspectionHold]] = {}
    hold_seq: dict[str, list[int]] = {}
    for ordinal, row in enumerate(hold_rows):
        job = job_index.get((int(row["LINE_NUMBER"]), str(row["SOI"])))
        if job is None:
            continue
        holds.setdefault(job, []).append(InspectionHold(
            after_work_minutes=int(row["AFTER_WORK_MINUTES"]),
            inspector_minutes=int(row["INSPECTOR_MINUTES"]),
            evidence=(EvidenceRef(dataset_ids.get("inspection_holds", ""), str(row.get("EVIDENCE_REF") or f"hold#{ordinal}"), source_run_id=run_id),),
        ))
        hold_seq.setdefault(job, []).append(int(row["SEQUENCE"]))
    counts: dict[str, dict[str, int]] = {}
    for row in count_rows:
        job = job_index.get((int(row["LINE_NUMBER"]), str(row["SHOP_ORDER_IDENTIFIER"])))
        if job is not None:
            counts[job] = {k: _count(v) for k, v in row.items() if k.endswith("INSPECTIONS")}
    triggers: dict[str, InspectionTrigger] = {}
    notes: list[str] = []
    for ordinal, row in enumerate(buyoff_rows):
        job = job_index.get((int(row["LINE_NUMBER"]), str(row["SHOP_ORDER_IDENTIFIER"])))
        if job is None:
            continue
        final_required = _count(row.get("FINAL_QUALITY_INSP")) > 0
        ip_count = _count(row.get("IN_PROCESS_QUALITY_INSP"))
        ip_required = ip_count > 0
        others = tuple(name for col, name in OTHER_BUYOFF_COLUMNS.items() if _count(row.get(col)) > 0)
        job_holds = tuple(sorted(holds.get(job, []), key=lambda h: h.after_work_minutes))
        if ip_required:
            if not job_holds:
                status = "UNKNOWN_LOCATION"
            elif sum(h.inspector_minutes for h in job_holds) != total_ip:
                status = "ALLOCATION_MISMATCH"
                notes.append(f"{job}: IP allocations {[h.inspector_minutes for h in job_holds]} do not total {total_ip}")
            elif len({h.after_work_minutes for h in job_holds}) != len(job_holds):
                status = "DUPLICATE_POSITION"
            else:
                status = "SOURCED"
                if len(job_holds) != ip_count:
                    notes.append(f"{job}: buyoff plan lists {ip_count} IP inspections but {len(job_holds)} sourced holds")
        else:
            status = "NONE"
            if job_holds:
                notes.append(f"{job}: sourced IP holds without an IP buyoff requirement were not applied")
            job_holds = ()
        triggers[job] = InspectionTrigger(
            final_required=final_required,
            ip_required=ip_required,
            ip_holds=job_holds,
            ip_hold_status=status,
            other_buyoffs=others,
            counts=counts.get(job, {}),
            basis="FOCUS_BUYOFF_PLAN + sourced inspection_holds",
            evidence=(EvidenceRef(dataset_ids.get("buyoff_plan", ""), f"buyoff_plan#{ordinal}", source_run_id=run_id),),
        )
        buyoff_counts = {"FINAL_QUALITY_INSPECTIONS": _count(row.get("FINAL_QUALITY_INSP")), "IN_PROCESS_QUALITY_INSPECTIONS": ip_count}
        cross = counts.get(job)
        if cross is not None:
            for key, value in buyoff_counts.items():
                if cross.get(key, value) != value:
                    notes.append(f"{job}: inspection counts {key}={cross.get(key)} differ from buyoff plan {value}")
    return triggers, notes


@dataclass(frozen=True)
class PhaseSpec:
    kind: str                # WORK | IP_QA | FINAL_QA | OTHER_BUYOFF
    minutes: int
    label: str
    parallel: bool = False   # OTHER_BUYOFF branches run parallel to FINAL_QA


def phase_plan(*, work_minutes: int, performed: int, trigger: InspectionTrigger | None, policy: Policy,
               other_minutes: Mapping[str, int]) -> tuple[list[PhaseSpec], list[str]]:
    """Ordered production/QA phases for the remaining work of one job.

    ``work_minutes`` is the remaining production elapsed; ``performed`` the actual
    performed elapsed before it (hold positions are measured on the full job).
    """
    final_minutes = policy.int("qa.final_minutes")
    max_seg = policy.int("duration.max_segment_minutes")
    problems: list[str] = []
    pieces: list[PhaseSpec] = []
    if policy.bool("qa.standard_includes_inspection_minutes") and trigger:
        # the standard already contains the QA minutes: carve them out once instead of adding a duplicate 45
        embedded = (policy.int("qa.total_ip_minutes") if trigger.ip_required else 0) + (final_minutes if trigger.final_required else 0)
        if work_minutes - embedded <= 0:
            return [PhaseSpec("WORK", work_minutes, "work (QA reconciliation failed)")], [
                f"QA_RECONCILIATION_INVALID: standard {work_minutes} cannot contain {embedded} embedded inspection minutes"]
        work_minutes -= embedded
    total = performed + work_minutes
    cursor = performed
    holds = list(trigger.ip_holds) if (trigger and trigger.ip_required and trigger.ip_hold_status == "SOURCED") else []
    for hold in holds:
        if hold.after_work_minutes <= 0 or hold.after_work_minutes >= total:
            problems.append(f"IP_HOLD_POSITION_INVALID: hold at {hold.after_work_minutes} outside (0,{total})")
            continue
        if hold.after_work_minutes <= performed:
            problems.append(f"IP_HOLD_STATUS_UNKNOWN: hold at {hold.after_work_minutes} precedes performed {performed} without a sourced buyoff")
            continue
        pieces.append(PhaseSpec("WORK", hold.after_work_minutes - cursor, "work"))
        pieces.append(PhaseSpec("IP_QA", hold.inspector_minutes, f"in-process hold @{hold.after_work_minutes}"))
        cursor = hold.after_work_minutes
    if total - cursor > 0:
        pieces.append(PhaseSpec("WORK", total - cursor, "work"))
    split: list[PhaseSpec] = []
    for piece in pieces:
        if piece.kind == "WORK" and piece.minutes > max_seg:
            chunks = -(-piece.minutes // max_seg)
            base, extra = divmod(piece.minutes, chunks)
            for i in range(chunks):
                split.append(PhaseSpec("WORK", base + (1 if i < extra else 0), f"work segment {i + 1}/{chunks}"))
        else:
            split.append(piece)
    if trigger and trigger.final_required:
        split.append(PhaseSpec("FINAL_QA", final_minutes, "final inspection"))
    if trigger:
        for kind in trigger.other_buyoffs:
            split.append(PhaseSpec("OTHER_BUYOFF", other_minutes.get(kind, policy.int("qa.other_buyoff_minutes")), f"{kind} buyoff", parallel=True))
    return split, problems
