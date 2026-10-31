"""Source freshness and capture skew at admission (S05 ATOMIC ACQUISITION step 5).

* **Freshness.** A dataset is as fresh as its producer says it was loaded (a receipt's ``loaded_at``, UTC) or, without
  that, as its newest row's source clock (``LOAD_TIMESTAMP``, ``SNAPSHOT_TS`` or ``LOAD_TS``, read in the dataset's
  declared source timezone; an explicit offset is honored). Policy ``admission.max_source_age_hours`` names the datasets
  checked and each one's limit. A dataset older than its limit at the cutoff is SOURCE_STALE. Rows with no readable clock,
  or a naive clock without a declared timezone, are SOURCE_CLOCK_UNKNOWN: unknown is never assumed fresh. An empty dataset
  is left to the emptiness receipts.
* **Capture skew.** The datasets of one run are captured within ``admission.max_capture_skew_minutes`` of each other;
  beyond that the run is CAPTURE_SKEW, because its tables may straddle a source load.

Each finding is a readiness gap that blocks executable work: the validator's lineage check fails an executable plan on
such a snapshot. Forecast plans still run on it, under the gap.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..contracts.planning import ReadinessGap
from .datasets import DATASETS
from .sourcetime import SourceTimeError, TimezonePolicy, local_to_utc

UTC = timezone.utc
SOURCE_CLOCKS = ("LOAD_TIMESTAMP", "SNAPSHOT_TS", "LOAD_TS")
EXECUTABLE_BLOCKERS = ("SOURCE_STALE", "SOURCE_CLOCK_UNKNOWN", "CAPTURE_SKEW")


def source_clock(name: str) -> str | None:
    """The dataset's source clock column by its contract, if it has one."""
    spec = DATASETS.get(name)
    return None if spec is None else next((c for c in SOURCE_CLOCKS if c in spec.columns), None)


def _instant(raw: Any, zone: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    if zone is None:
        return None
    try:
        return local_to_utc(value, zone)
    except SourceTimeError:
        return None


def freshness_gaps(*, datasets: Mapping[str, tuple[Mapping[str, Any], ...]], receipts: Mapping[str, Mapping[str, Any]] | None,
                   captured: Mapping[str, datetime] | None, tzp: TimezonePolicy, policy: Any, cutoff: datetime) -> list[ReadinessGap]:
    gaps: list[ReadinessGap] = []
    for name, limit in sorted(policy.map("admission.max_source_age_hours").items()):
        rows = datasets.get(name) or ()
        loaded = (receipts or {}).get(name, {}).get("loaded_at")
        if loaded:
            newest, basis = _instant(loaded, None), "producer receipt loaded_at"
        else:
            column = source_clock(name)
            if not rows or column is None:
                continue
            zone = tzp.zone(name) if tzp.has(name) else None
            stamps = {r.get(column) for r in rows} - {None, ""}
            instants = [t for t in (_instant(s, zone) for s in stamps) if t is not None]
            newest, basis = (max(instants) if instants else None), f"newest {column}"
        if newest is None:
            gaps.append(ReadinessGap("SOURCE_CLOCK_UNKNOWN", name, f"no readable source clock ({basis}); freshness cannot be shown",
                                     "BLOCKS_EXECUTABLE"))
            continue
        age_hours = (cutoff - newest).total_seconds() / 3600
        if age_hours > float(limit):
            gaps.append(ReadinessGap("SOURCE_STALE", name, f"{basis} {newest.isoformat()} is {age_hours:.1f} h before the cutoff "
                                     f"(limit {limit} h)", "BLOCKS_EXECUTABLE"))
    times = sorted((captured or {}).values())
    limit_minutes = policy.int("admission.max_capture_skew_minutes")
    if len(times) > 1 and (times[-1] - times[0]).total_seconds() > limit_minutes * 60:
        gaps.append(ReadinessGap("CAPTURE_SKEW", "source_run", f"datasets were captured {(times[-1] - times[0]).total_seconds() / 60:.1f} min "
                                 f"apart (limit {limit_minutes} min): the tables may straddle a source load", "BLOCKS_EXECUTABLE"))
    return gaps
