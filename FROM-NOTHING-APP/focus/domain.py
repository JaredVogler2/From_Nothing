"""Canonical domain identity and value types (S03).

All instants are aware UTC; all intervals are half-open ``[start, end)``; all
durations are explicit minutes. Native keys keep their source text exactly
(no strip/casefold/suffix removal). Transport IDs are SHA-256 digests mapped
reversibly through the snapshot index; collisions are rejected there.
"""
from __future__ import annotations

import enum
import functools
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .codec import canonical_dumps, sha256_hex

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_US_PER_MINUTE = 60_000_000


class DomainError(ValueError):
    """Invalid domain value with a stable reason code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# time helpers (engine uses integer minutes since the Unix epoch, UTC)
# ---------------------------------------------------------------------------

def aware_utc(value: Any, field_name: str = "datetime") -> datetime:
    if not isinstance(value, datetime):
        raise DomainError("TYPE_MISMATCH", f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError("NAIVE_DATETIME", f"{field_name} {value!r} must be timezone-aware")
    return value.astimezone(UTC)


def _total_us(value: datetime) -> int:
    delta = aware_utc(value) - EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def minute_floor(value: datetime) -> int:
    return _total_us(value) // _US_PER_MINUTE


def minute_ceil(value: datetime) -> int:
    return -((-_total_us(value)) // _US_PER_MINUTE)


def to_minute(value: datetime) -> int:
    """Exact conversion; raises if ``value`` is not on a whole minute."""
    total = _total_us(value)
    if total % _US_PER_MINUTE:
        raise DomainError("NOT_WHOLE_MINUTE", f"{value.isoformat()} is not on a whole minute")
    return total // _US_PER_MINUTE


def from_minute(minute: int) -> datetime:
    if isinstance(minute, bool) or not isinstance(minute, int):
        raise DomainError("TYPE_MISMATCH", f"minute must be int, got {minute!r}")
    return EPOCH + timedelta(minutes=minute)


def iso_minute(minute: int) -> str:
    return from_minute(minute).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# enumerations
# ---------------------------------------------------------------------------

class PhaseKind(str, enum.Enum):
    WORK = "WORK"
    IP_QA = "IP_QA"
    FINAL_QA = "FINAL_QA"
    OTHER_BUYOFF = "OTHER_BUYOFF"
    RELEASE = "RELEASE"


class EdgeType(str, enum.Enum):
    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"
    NONCONCURRENT = "NONCONCURRENT"
    CONCURRENT = "CONCURRENT"


class DutyKind(str, enum.Enum):
    WORK = "WORK"
    TRAVEL = "TRAVEL"
    TOOL = "TOOL"
    OTHER = "OTHER"
    WAIT = "WAIT"


class Purpose(str, enum.Enum):
    FORECAST = "FORECAST"
    EXECUTABLE = "EXECUTABLE"
    SCENARIO = "SCENARIO"


class RunStatus(str, enum.Enum):
    CAPTURING = "CAPTURING"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"


class DecisionStatus(str, enum.Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


class Disposition(str, enum.Enum):
    SCHEDULED = "scheduled"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    EXCLUDED = "excluded"
    VERIFIED_COMPLETE = "verified_complete"
    CONDITIONAL = "conditional"
    DEFERRED = "deferred"


class ReleaseKind(str, enum.Enum):
    PART = "PART"
    QA = "QA"
    REWORK = "REWORK"
    ENGINEERING = "ENGINEERING"


class ReleaseStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    CONDITIONAL = "conditional"
    CONFIRMED = "confirmed"
    REVOKED = "revoked"


class RequirementStatus(str, enum.Enum):
    REQUIRED = "required"
    AFFIRMATIVELY_NONE = "affirmatively_none"
    UNKNOWN = "unknown"


class AvailabilityBasis(str, enum.Enum):
    ATTENDANCE_CONFIRMED = "ATTENDANCE_CONFIRMED"
    SCHEDULED_ROSTER = "SCHEDULED_ROSTER"
    INFERRED_NOMINAL = "INFERRED_NOMINAL"
    SYNTHETIC = "SYNTHETIC"


class PersonOrigin(str, enum.Enum):
    SOURCE = "SOURCE"                  # live HR/assignment source
    FIXTURE_DEMO = "FIXTURE_DEMO"      # invented demonstration person, synthetic=false origin
    SYNTHETIC_VIRTUAL = "SYNTHETIC_VIRTUAL"  # virtual MHE/PSC/TST support person


class EvaluationStatus(str, enum.Enum):
    OFFICIAL = "official"
    DEMONSTRATION = "demonstration"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=262_144)
def _digest(prefix: str, payload: bytes, length: int) -> str:
    return prefix + sha256_hex(payload)[:length]


def digest_id(prefix: str, *parts: Any, length: int = 20) -> str:
    return _digest(prefix, canonical_dumps(list(parts)), length)


def _require_text(value: Any, name: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise DomainError("TYPE_MISMATCH", f"{name} must be a string, got {type(value).__name__}")
    if not allow_empty and value == "":
        raise DomainError("EMPTY_KEY", f"{name} must not be empty")


def _require_int(value: Any, name: str, minimum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError("TYPE_MISMATCH", f"{name} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise DomainError("OUT_OF_RANGE", f"{name} must be >= {minimum}, got {value}")


@dataclass(frozen=True, slots=True)
class JobKey:
    site: str
    program: str
    line: int
    native_id: str
    revision: str | None = None
    operation: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None

    def __post_init__(self) -> None:
        _require_text(self.site, "site")
        _require_text(self.program, "program")
        _require_int(self.line, "line", 1)
        _require_text(self.native_id, "native_id")
        if self.revision is not None:
            _require_text(self.revision, "revision", allow_empty=True)
        if self.operation is not None:
            _require_text(self.operation, "operation", allow_empty=True)
        for name in ("effective_from", "effective_to"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
                raise DomainError("TYPE_MISMATCH", f"{name} must be a date")
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise DomainError("BAD_EFFECTIVITY", "effective_from is after effective_to")

    @property
    def id(self) -> str:
        return digest_id(
            "J", "JobKey", self.site, self.program, self.line, self.native_id,
            self.revision, self.operation, self.effective_from, self.effective_to,
        )

    def label(self) -> str:
        rev = "" if self.revision is None else f" rev {self.revision}"
        return f"{self.line}/{self.native_id}{rev}"


@dataclass(frozen=True, slots=True)
class PhaseKey:
    job: JobKey
    kind: PhaseKind
    ordinal: int

    def __post_init__(self) -> None:
        if not isinstance(self.job, JobKey):
            raise DomainError("TYPE_MISMATCH", "phase job must be a JobKey")
        if not isinstance(self.kind, PhaseKind):
            raise DomainError("TYPE_MISMATCH", "phase kind must be a PhaseKind")
        _require_int(self.ordinal, "ordinal", 0)

    @property
    def id(self) -> str:
        return f"{self.job.id}.{self.kind.value}.{self.ordinal}"


@dataclass(frozen=True, slots=True)
class PersonKey:
    native_id: str
    authority_namespace: str

    def __post_init__(self) -> None:
        _require_text(self.native_id, "person native_id")
        _require_text(self.authority_namespace, "authority_namespace")

    @property
    def id(self) -> str:
        return digest_id("H", "PersonKey", self.authority_namespace, self.native_id)


@dataclass(frozen=True, slots=True)
class PoolKey:
    site: str
    team: str
    start_date: date
    shift_number: int
    source_run_id: str

    def __post_init__(self) -> None:
        _require_text(self.site, "site")
        _require_text(self.team, "team")
        if not isinstance(self.start_date, date) or isinstance(self.start_date, datetime):
            raise DomainError("TYPE_MISMATCH", "pool start_date must be a date")
        if self.shift_number not in (1, 2, 3):
            raise DomainError("BAD_SHIFT", f"shift_number must be 1, 2 or 3, got {self.shift_number!r}")
        _require_text(self.source_run_id, "source_run_id")

    @property
    def id(self) -> str:
        return digest_id("Q", "PoolKey", self.site, self.team, self.start_date, self.shift_number, self.source_run_id)

    @property
    def label(self) -> str:
        return f"{self.team} {self.start_date.isoformat()} S{self.shift_number}"


# ---------------------------------------------------------------------------
# base value types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", aware_utc(self.start, "interval start"))
        object.__setattr__(self, "end", aware_utc(self.end, "interval end"))
        if not self.start < self.end:
            raise DomainError("EMPTY_INTERVAL", f"interval start {self.start} must precede end {self.end}")

    @property
    def minutes(self) -> int:
        return to_minute(self.end) - to_minute(self.start)

    @classmethod
    def of_minutes(cls, start: int, end: int) -> "Interval":
        return cls(from_minute(start), from_minute(end))

    def as_minutes(self) -> tuple[int, int]:
        return to_minute(self.start), to_minute(self.end)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    dataset_id: str
    row_id: str
    revision: str | None = None
    query_hash: str | None = None
    schema_hash: str | None = None
    content_hash: str | None = None
    source_clock: str | None = None
    source_timezone: str | None = None
    source_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuleDecision:
    code: str
    status: DecisionStatus
    reason: str
    evidence_refs: tuple[EvidenceRef, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.status is DecisionStatus.ALLOWED

    @classmethod
    def allow(cls, code: str, reason: str = "", evidence: Iterable[EvidenceRef] = ()) -> "RuleDecision":
        return cls(code, DecisionStatus.ALLOWED, reason, tuple(evidence))

    @classmethod
    def deny(cls, code: str, reason: str, evidence: Iterable[EvidenceRef] = ()) -> "RuleDecision":
        return cls(code, DecisionStatus.DENIED, reason, tuple(evidence))

    @classmethod
    def unknown(cls, code: str, reason: str, evidence: Iterable[EvidenceRef] = ()) -> "RuleDecision":
        return cls(code, DecisionStatus.UNKNOWN, reason, tuple(evidence))


@dataclass(frozen=True, slots=True)
class RequirementBundle:
    """AND across groups, OR within a group (approved alternatives)."""

    groups: tuple[tuple[str, ...], ...]
    role: str
    count: int
    applicability: str

    def __post_init__(self) -> None:
        _require_text(self.role, "role")
        _require_int(self.count, "role count", 1)
        for group in self.groups:
            if not group:
                raise DomainError("EMPTY_GROUP", "requirement group must offer at least one certificate")
            if len(set(group)) != len(group):
                raise DomainError("DUPLICATE_ALTERNATIVE", f"duplicate alternative in {group}")
            for cert in group:
                _require_text(cert, "cert code")

    def satisfied_by(self, certs: set[str] | frozenset[str]) -> bool:
        return all(any(c in certs for c in group) for group in self.groups)

    def usable_certs(self, certs: set[str] | frozenset[str]) -> frozenset[str]:
        needed = {c for group in self.groups for c in group}
        return frozenset(certs & needed)

    @property
    def all_codes(self) -> frozenset[str]:
        return frozenset(c for group in self.groups for c in group)


@dataclass(frozen=True, slots=True)
class Holding:
    person: str
    cert: str
    status: str
    validity: str                 # BOUNDED | PERPETUAL | UNKNOWN
    valid_from: datetime | None
    valid_to: datetime | None     # exclusive instant
    revoked_at: datetime | None
    applicability: str
    raw_status: str | None
    raw_last_cert_date: str | None
    raw_expiry: str | None
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if self.validity not in ("BOUNDED", "PERPETUAL", "UNKNOWN"):
            raise DomainError("BAD_VALIDITY", f"unknown holding validity {self.validity!r}")
        for name in ("valid_from", "valid_to", "revoked_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, aware_utc(value, name))
        if self.validity == "BOUNDED" and self.valid_to is None:
            raise DomainError("BAD_VALIDITY", "bounded holding needs valid_to")
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise DomainError("BAD_VALIDITY", "holding valid_from must precede valid_to")


def ensure_unique_ids(items: Iterable[Any], kind: str) -> dict[str, Any]:
    """Map ``item.id -> item`` rejecting digest collisions between distinct keys."""
    out: dict[str, Any] = {}
    for item in items:
        key_id = item.id
        existing = out.get(key_id)
        if existing is not None and existing != item:
            raise DomainError("ID_COLLISION", f"{kind} transport id {key_id} collides for distinct keys")
        out[key_id] = item
    return out
