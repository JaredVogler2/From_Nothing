"""Publication repository: private stage, exact readback, immutable acceptance and the head (S06, S13, P16).

The proposed SQL storage (``sql/migrations/007_publication.sql``: PlanStage, Validation,
PublishedPlan, FrozenGoal and PlanHead plus the acceptance procedure) is **not installed**.
``FixturePublicationRepository`` runs the same protocol on the persistent fixture store:

* **stage.** A write-once private, unsigned stage holding the exact canonical bytes and their
  sha. It is keyed by the publication idempotency key and never visible to readers.
* **read_stage.** Returns the exact stored bytes.
* **accept.** Write-once immutable frozen goals, the published plan, its payload and the
  acceptance receipt, then the scoped plan head LAST with compare-and-set against the version
  observed before staging. A concurrent acceptance makes this one fail, and the earlier head
  stays. Retries reuse records already written, so recovery never forks content.
* **current.** Readers select the accepted immutable head, never the newest file.

``SqlPublicationRepository`` refuses every write with ``SQL_STAGE_NOT_INSTALLED`` until P23
installs the tables. A live publication then finishes its local rehearsal but never signs,
accepts or moves a head.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..contracts.planning import AcceptanceReceipt, FrozenGoal, PublishedPlan
from ..data.store import VersionConflict

STAGES = "plan_stages"
PUBLISHED = "published_plans"
PAYLOADS = "published_payloads"
GOALS = "frozen_goals"
RECEIPTS = "acceptance_receipts"
REPAIRS = "repair_requests"


def plan_head(scope: str, purpose: str) -> str:
    return f"plan/{scope}/{purpose}"


class PublicationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PlanStage:
    id: str
    idempotency_key: str
    scope: str
    purpose: str
    candidate_id: str
    source_run_id: str
    policy_hash: str
    payload_sha256: str
    payload_text: str                      # the exact canonical UTF-8 bytes
    signed: bool                           # always False: a stage is never signed
    created_at: datetime


@dataclass(frozen=True)
class PublishedPayload:
    plan_id: str
    payload_sha256: str
    payload_text: str


class FixturePublicationRepository:
    kind = "fixture"
    supports_live_stage = False            # fixture readback is labelled as such, never live SQL proof

    def __init__(self, repositories: Any):
        self.repos = repositories

    # ------------------------------------------------------------------ stage and readback
    def stage(self, stage: PlanStage) -> PlanStage:
        existing = self.repos.get_typed(STAGES, stage.id, "PlanStage", PlanStage)
        if existing is not None:
            if existing.payload_sha256 != stage.payload_sha256:
                raise PublicationError("STAGE_CONFLICT", f"stage {stage.id} already holds different bytes")
            return existing
        self.repos.put_record(STAGES, "PlanStage", stage.id, stage, source_run_id=stage.source_run_id, policy_hash=stage.policy_hash,
                              purpose=stage.purpose, created_at=stage.created_at)
        return stage

    def read_stage(self, stage_id: str) -> bytes | None:
        stage = self.repos.get_typed(STAGES, stage_id, "PlanStage", PlanStage)
        return None if stage is None else stage.payload_text.encode("utf-8")

    # ------------------------------------------------------------------ acceptance
    def receipt(self, plan_id: str) -> AcceptanceReceipt | None:
        return self.repos.get_typed(RECEIPTS, plan_id, "AcceptanceReceipt", AcceptanceReceipt)

    def published(self, plan_id: str) -> PublishedPlan | None:
        return self.repos.get_typed(PUBLISHED, plan_id, "PublishedPlan", PublishedPlan)

    def payload(self, plan_id: str) -> PublishedPayload | None:
        return self.repos.get_typed(PAYLOADS, plan_id, "PublishedPayload", PublishedPayload)

    def goals(self, goal_ids: tuple[str, ...]) -> list[FrozenGoal]:
        return [g for g in (self.repos.get_typed(GOALS, gid, "FrozenGoal", FrozenGoal) for gid in goal_ids) if g is not None]

    def head(self, scope: str, purpose: str):
        return self.repos.head(plan_head(scope, purpose))

    def accepted_ids(self) -> list[str]:
        return sorted(self.repos.list_ids(PUBLISHED))

    def accept(self, published: PublishedPlan, payload: PublishedPayload, goals: tuple[FrozenGoal, ...], expected_head_version: int) -> int:
        """Immutable records first, the head LAST (compare-and-set). Returns the new head version."""
        repos = self.repos
        with repos.store.lock(f"publish-{published.scope}-{published.purpose.value}"):
            for g in goals:
                if repos.get_raw(GOALS, g.id) is None:
                    repos.put_record(GOALS, "FrozenGoal", g.id, g, source_run_id=published.source_run_id, policy_hash=published.policy_hash,
                                     purpose=published.purpose.value, created_at=published.accepted_at)
            if repos.get_raw(PAYLOADS, published.id) is None:
                repos.put_record(PAYLOADS, "PublishedPayload", published.id, payload, source_run_id=published.source_run_id,
                                 policy_hash=published.policy_hash, purpose=published.purpose.value, created_at=published.accepted_at)
            if repos.get_raw(PUBLISHED, published.id) is None:
                repos.put_record(PUBLISHED, "PublishedPlan", published.id, published, source_run_id=published.source_run_id,
                                 policy_hash=published.policy_hash, purpose=published.purpose.value, created_at=published.accepted_at)
            if repos.get_raw(RECEIPTS, published.id) is None:
                repos.put_record(RECEIPTS, "AcceptanceReceipt", published.id, published.acceptance_receipt, source_run_id=published.source_run_id,
                                 policy_hash=published.policy_hash, purpose=published.purpose.value, created_at=published.accepted_at)
            name = plan_head(published.scope, published.purpose.value)
            head = repos.head(name)
            if head is not None and head.record_id == published.id:
                return head.version                                    # recovery: the pointer already moved
            try:
                return repos.move_head(name, published.id, expected_head_version).version
            except VersionConflict as exc:
                raise PublicationError("HEAD_MOVED", f"{name} moved during acceptance; the earlier head stays ({exc})") from exc


class SqlPublicationRepository:
    """Live adapter placeholder for the proposed FOCUS_FN publication tables: every write is refused until installed (P23)."""

    kind = "sql"
    supports_live_stage = False

    def __init__(self, repositories: Any):
        self.repos = repositories

    def _refuse(self) -> PublicationError:
        return PublicationError("SQL_STAGE_NOT_INSTALLED", "FOCUS_FN PlanStage/PublishedPlan/PlanHead are proposed, not installed; "
                                                           "pre-sign SQL readback is unavailable, so nothing is signed or accepted")

    def stage(self, stage: PlanStage) -> PlanStage:
        raise self._refuse()

    def read_stage(self, stage_id: str) -> bytes | None:
        raise self._refuse()

    def accept(self, *args: Any, **kwargs: Any) -> int:
        raise self._refuse()

    def receipt(self, plan_id: str) -> AcceptanceReceipt | None:
        return None

    def published(self, plan_id: str) -> PublishedPlan | None:
        return None

    def payload(self, plan_id: str) -> PublishedPayload | None:
        return None

    def goals(self, goal_ids: tuple[str, ...]) -> list[FrozenGoal]:
        return []

    def head(self, scope: str, purpose: str):
        return None

    def accepted_ids(self) -> list[str]:
        return []


def repository_for(services: Any):
    return FixturePublicationRepository(services.repositories) if services.settings.mode == "fixture" else SqlPublicationRepository(services.repositories)
