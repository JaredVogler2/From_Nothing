"""Material shortages and part-release milestones (S10, P09).

* Every active ``FOCUS_ETRACS_ACTIVE`` row is kept, including unknown ECD and
  blank USED_ON.
* USED_ON resolves only through the exact line + SOI and the authoritative
  same-line child-to-root chain (PARENT_SOI). A missing linkage exposes
  unresolved affected scope at line level; the row is never ignored.
* Zero-work PART milestones gate the used-on job and its root (descendants
  follow through the graph). A timestamp ETA is a precise floor. A date-only
  ECD floors at the end of the local ECD date (next local midnight), an explicit
  conservative default.
* A past ECD is not receipt. Only sourced RECEIVED (or DELETED) evidence confirms
  readiness, and a later REOPENED re-blocks. A sourced ETA event is a timestamp
  forecast floor (ceiling to the minute) that refines the date-only default.
  Multiple shortages: the latest release applies; any unknown required release
  stays blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from ..codec import parse_utc
from ..contracts.planning import ReleaseMilestone, Shortage
from ..domain import EvidenceRef, ReleaseKind, ReleaseStatus, from_minute, minute_ceil
from .sourcetime import local_midnight_utc

UTC = timezone.utc


@dataclass
class PartsBuild:
    shortages: list[Shortage] = field(default_factory=list)
    releases: list[ReleaseMilestone] = field(default_factory=list)
    line_unresolved: dict[int, list[str]] = field(default_factory=dict)


def root_chain(job: str, parent_of: Mapping[str, str | None]) -> tuple[list[str], bool]:
    """Walk the same-line parent chain from ``job`` to its root; returns (chain, cyclic)."""
    chain = [job]
    seen = {job}
    current = job
    while True:
        parent = parent_of.get(current)
        if parent is None:
            return chain, False
        if parent in seen:
            return chain, True
        chain.append(parent)
        seen.add(parent)
        current = parent


def _ceil_minute(value: datetime) -> datetime:
    return from_minute(minute_ceil(value))


def build_parts(*, rows: Iterable[Mapping[str, Any]], events: Iterable[Mapping[str, Any]], job_index: Mapping[tuple[int, str], str],
                parent_of: Mapping[str, str | None], labels: Mapping[str, str], tz: ZoneInfo | None, dataset_ids: Mapping[str, str],
                run_id: str, cutoff: datetime | None = None, date_floor: str = "END_OF_LOCAL_DATE",
                timestamp_floor: str = "CEIL_MINUTE") -> PartsBuild:
    """Shortages and PART release milestones. ``tz`` is the etracs_active source timezone (None: date floors unknown)."""
    out = PartsBuild()
    if date_floor != "END_OF_LOCAL_DATE" or timestamp_floor != "CEIL_MINUTE":
        raise ValueError(f"unsupported part floor policy {date_floor}/{timestamp_floor}")
    by_record: dict[int, list[tuple[datetime, str, str]]] = {}
    for ev in events:
        by_record.setdefault(int(ev["RECORD_ID"]), []).append((parse_utc(str(ev["EFFECTIVE_AT_UTC"])), str(ev["EVENT"]), str(ev.get("EVIDENCE_REF") or "")))
    for ordinal, row in enumerate(rows):
        record = int(row["RECORD_ID"])
        line = int(row["LINE_NUMBER"])
        used_on = row.get("USED_ON")
        used_on = None if used_on is None or str(used_on).strip() == "" else str(used_on)
        ecd = date.fromisoformat(str(row["ETRAC_ECD"])) if row.get("ETRAC_ECD") else None
        evidence = (EvidenceRef(dataset_ids.get("etracs_active", ""), f"RECORD_ID={record}", source_run_id=run_id),)
        reasons: list[str] = []
        history = sorted(by_record.get(record, []))
        received_at: datetime | None = None
        reopened_at: datetime | None = None
        eta_at: datetime | None = None
        confirmed_by = "RECEIVED"
        for when, event, ref in history:
            if event in ("RECEIVED", "DELETED"):
                received_at, reopened_at, confirmed_by = when, None, event
            elif event == "REOPENED" and received_at is not None:
                reopened_at, received_at = when, None
                reasons.append(f"REOPENED at {when.isoformat()} ({ref})")
            elif event == "ETA":
                eta_at = when                       # a later sourced ETA supersedes an earlier one
        date_floor_at = local_midnight_utc(ecd + timedelta(days=1), tz) if (ecd and tz) else None
        if received_at is not None:
            status, ready_at, precision = ReleaseStatus.CONFIRMED, _ceil_minute(received_at), "TIMESTAMP"
            forecast_at = ready_at
            if confirmed_by == "DELETED":
                reasons.append("SHORTAGE_DELETED: closed by sourced deletion evidence")
        elif eta_at is not None:
            status, ready_at, precision = ReleaseStatus.CONDITIONAL, None, "TIMESTAMP"
            forecast_at = _ceil_minute(max(eta_at, reopened_at) if reopened_at else eta_at)
            reasons.append(f"TIMED_ETA: precise conditional floor {forecast_at.isoformat()}")
        elif date_floor_at is not None:
            status, ready_at, precision = ReleaseStatus.CONDITIONAL, None, "DATE"
            forecast_at = max(date_floor_at, _ceil_minute(reopened_at)) if reopened_at else date_floor_at
            reasons.append(f"DATE_ONLY_ECD_FLOOR: end of local ECD date {ecd.isoformat()} (conservative default)")  # type: ignore[union-attr]
            if cutoff is not None and date_floor_at <= cutoff:
                reasons.append("PAST_ECD_NOT_RECEIPT: the ECD has passed without receipt evidence")
        else:
            status, ready_at, precision, forecast_at = ReleaseStatus.UNKNOWN, None, "NONE", None
            if ecd is not None and tz is None:
                reasons.append("SOURCE_TIMEZONE_MISSING: etracs_active has no declared timezone; the ECD floor is unknown")
            else:
                reasons.append("UNKNOWN_ECD: no ECD and no receipt; dependent work stays blocked")
        if reopened_at is not None and received_at is None:
            reasons.append("receipt revoked by reopen: physical readiness must be re-evidenced")
        if used_on is None:
            out.shortages.append(Shortage(record, line, None, ecd, row.get("TEAM"), row.get("REASON_CODE"),
                                          date.fromisoformat(str(row["ETRAC_CREATED_DT"])) if row.get("ETRAC_CREATED_DT") else None,
                                          None, None, (), "UNRESOLVED_USED_ON", None,
                                          tuple(reasons + ["UNRESOLVED_USED_ON: blank USED_ON exposes unresolved affected scope on this line"]), evidence))
            out.line_unresolved.setdefault(line, []).append(f"shortage {record}: blank USED_ON")
            continue
        job = job_index.get((line, used_on))
        if job is None:
            out.shortages.append(Shortage(record, line, used_on, ecd, row.get("TEAM"), row.get("REASON_CODE"), None, None, None, (),
                                          "UNRESOLVED_USED_ON", None,
                                          tuple(reasons + [f"USED_ON_NOT_IN_CENSUS: {used_on} is not a job on line {line}"]), evidence))
            out.line_unresolved.setdefault(line, []).append(f"shortage {record}: used-on {used_on} not in line census")
            continue
        chain, cyclic = root_chain(job, parent_of)
        if cyclic:
            reasons.append("ROOT_CHAIN_CYCLE: parent chain is cyclic; the used-on job is gated, root unresolved")
        root = chain[-1] if not cyclic else None
        affected = tuple(dict.fromkeys(chain))
        release_id = f"R:PART:{record}"
        out.releases.append(ReleaseMilestone(
            id=release_id,
            kind=ReleaseKind.PART,
            scope_keys=affected,
            forecast_at=forecast_at,
            ready_at=ready_at,
            status=status,
            precision=precision,
            label=f"Part {row.get('ITEM_NUMBER') or record} for {labels.get(job, used_on)}",
            evidence=evidence,
        ))
        shortage_status = {"confirmed": "RECEIVED", "conditional": "REOPENED" if reopened_at else "LINKED", "unknown": "UNKNOWN_ECD"}[status.value]
        out.shortages.append(Shortage(record, line, used_on, ecd, row.get("TEAM"), row.get("REASON_CODE"),
                                      date.fromisoformat(str(row["ETRAC_CREATED_DT"])) if row.get("ETRAC_CREATED_DT") else None,
                                      job, root, affected, shortage_status, release_id, tuple(reasons), evidence))
    return out
