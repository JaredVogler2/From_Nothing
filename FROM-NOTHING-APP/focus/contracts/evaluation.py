"""Evaluation and recognition contracts (S03, S14)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from ..domain import EvaluationStatus
from .planning import GateContext


@dataclass(frozen=True, slots=True)
class Evaluation:
    id: str
    scope: str
    goal_ids: tuple[str, ...]
    cutoff: datetime
    policy_id: str
    policy_hash: str
    B0: Fraction
    B: Fraction
    E: Fraction
    D: Fraction
    Cp: Fraction
    U: Fraction
    O: Fraction
    attainment: Fraction | None
    adherence: Fraction | None
    base: Fraction | None
    score: Decimal | None
    grade: str | None
    status: EvaluationStatus
    reasons: tuple[str, ...]
    evidence_hash: str
    revision: int
    supersedes: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecognitionEvent:
    id: str
    dedupe_key: str
    event_type: str            # completion_credited | gate_recovery | dependency_unlocked | commitment_kept | excusal_decided | badge_earned | correction_applied
    scope: str
    earning_unit_ids: tuple[str, ...]
    value: Fraction
    frozen_gate_context: GateContext | None
    evidence: tuple[str, ...]
    revision: int
    supersedes: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BadgeAward:
    id: str
    dedupe_key: str
    badge: str
    scope: str
    evidence_ids: tuple[str, ...]
    awarded_at: datetime
    revision: int
