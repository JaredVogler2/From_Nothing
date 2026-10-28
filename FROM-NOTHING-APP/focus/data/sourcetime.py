"""Source clock semantics (S04, S05, P03).

SQL ``datetime2``/``datetime`` columns carry no timezone. A server clock in
UTC does not prove every source field is UTC, so each dataset needs an
explicit timezone policy:

* fixture mode: the producer receipt declares ``source_timezone``;
* live mode: ``FOCUS_SOURCE_TIMEZONE_MAP_JSON`` maps dataset -> IANA zone
  (``"*"`` is an explicit default).

A missing policy is a ``SOURCE_TIMEZONE_POLICY_MISSING`` gap; values stay
unknown rather than being guessed. Local times inside a DST gap shift forward
to the transition; ambiguous local times use their first occurrence. Both
rules are fixture policy and are recorded with each conversion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


class SourceTimeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def local_to_utc(local: datetime, tz: ZoneInfo) -> datetime:
    """Convert a naive local wall time to aware UTC (DST gap: shift forward; overlap: first occurrence)."""
    if local.tzinfo is not None:
        raise SourceTimeError("NOT_NAIVE", "expected a naive local wall time")
    candidate = local.replace(tzinfo=tz, fold=0)
    if candidate.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == local:
        return candidate.astimezone(UTC)
    # Nonexistent local time (spring-forward gap): the first valid local minute after
    # the gap is the transition instant itself.
    probe = local.replace(second=0, microsecond=0)
    for _ in range(240):
        probe = probe + timedelta(minutes=1)
        cand = probe.replace(tzinfo=tz, fold=0)
        if cand.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == probe:
            return cand.astimezone(UTC)
    raise SourceTimeError("DST_GAP_UNRESOLVED", f"cannot resolve local time {local.isoformat()}")


def local_midnight_utc(day: date, tz: ZoneInfo) -> datetime:
    return local_to_utc(datetime(day.year, day.month, day.day), tz)


@dataclass(frozen=True)
class TimezonePolicy:
    mapping: Mapping[str, str]
    basis: str        # FIXTURE_RECEIPT | SETTINGS_MAP

    def zone(self, dataset: str) -> ZoneInfo:
        name = self.mapping.get(dataset) or self.mapping.get("*")
        if not name:
            raise SourceTimeError("SOURCE_TIMEZONE_POLICY_MISSING", f"no timezone policy for dataset {dataset}")
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            raise SourceTimeError("BAD_TIMEZONE", f"unknown timezone {name!r} for {dataset}") from None

    def has(self, dataset: str) -> bool:
        return bool(self.mapping.get(dataset) or self.mapping.get("*"))

    def parse_local(self, dataset: str, text: str | None) -> datetime | None:
        """Parse naive datetime2 text from ``dataset`` into aware UTC; None stays unknown."""
        if text is None or text == "":
            return None
        try:
            naive = datetime.fromisoformat(str(text))
        except ValueError:
            raise SourceTimeError("BAD_SOURCE_DATETIME", f"{dataset}: unparseable datetime {text!r}") from None
        if naive.tzinfo is not None:
            return naive.astimezone(UTC)
        return local_to_utc(naive, self.zone(dataset))


def policy_from_receipts(receipts: Mapping[str, Mapping[str, object]], settings_map: Mapping[str, str], mode: str) -> TimezonePolicy:
    if mode == "fixture":
        mapping = {name: str(r.get("source_timezone")) for name, r in receipts.items() if r.get("source_timezone")}
        mapping.update(settings_map)
        return TimezonePolicy(mapping, "FIXTURE_RECEIPT")
    return TimezonePolicy(dict(settings_map), "SETTINGS_MAP")
