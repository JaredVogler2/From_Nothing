"""Injectable clocks (S02/S03: clock policies are injected, never hardcoded)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

UTC = timezone.utc

# Fixture cutoff from S18: 2026-10-26T10:00:00Z (06:00 America/New_York).
FIXTURE_CUTOFF = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for fixtures and tests; advances only explicitly."""

    def __init__(self, instant: datetime = FIXTURE_CUTOFF):
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant

    def advance(self, minutes: int = 0, seconds: int = 0) -> datetime:
        self._instant = self._instant + timedelta(minutes=minutes, seconds=seconds)
        return self._instant

    def set(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._instant = instant.astimezone(UTC)
