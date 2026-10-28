"""Worker run contract (S06 FOCUS_FN_WorkerRun, S15 /runs/<id>, S17 WORKERS)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain import DomainError, aware_utc

WORKER_KINDS = (
    "schedule",
    "scenario",
    "amendment",
    "ie_review",
    "disruption_claim",
    "excusal_decision",
    "explanation",
    "reported_observation",
)
RUN_STATUSES = ("queued", "running", "failed", "completed", "cancelled")
TERMINAL_STATUSES = frozenset({"failed", "completed", "cancelled"})


@dataclass(frozen=True, slots=True)
class WorkerRun:
    id: str
    kind: str
    scope: str
    idempotency_key: str
    status: str
    requested_by: str
    request: dict[str, Any]
    request_sha256: str
    budget_seconds: float
    lease_owner: str | None
    lease_until: datetime | None
    attempts: int
    result_receipt: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.kind not in WORKER_KINDS:
            raise DomainError("BAD_WORKER_KIND", f"unknown worker kind {self.kind!r}")
        if self.status not in RUN_STATUSES:
            raise DomainError("BAD_RUN_STATUS", f"unknown run status {self.status!r}")
        object.__setattr__(self, "created_at", aware_utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", aware_utc(self.updated_at, "updated_at"))
        if self.lease_until is not None:
            object.__setattr__(self, "lease_until", aware_utc(self.lease_until, "lease_until"))
        if self.status == "completed" and self.result_receipt is None:
            raise DomainError("COMPLETED_WITHOUT_RECEIPT", "a completed run carries a durable result receipt")

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
