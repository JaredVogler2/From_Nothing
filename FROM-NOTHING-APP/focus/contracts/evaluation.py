"""Evaluation and recognition contracts (S03, S14)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

from ..domain import EvaluationStatus
from .planning import GateContext


@dataclass(frozen=True, slots=True)
class UnitFact:
    """One frozen earning unit joined to verified ordered actuals (canonical evaluation evidence)."""

    unit_id: str
    goal_id: str
    job: str
    phase: str
    segment: int
    team: str
    effort: int                         # frozen standard person minutes
    origin: str                         # ORIGINAL (B0) | AMENDMENT (approved feasible emergency addition)
    status: str                         # EARNED | MISSED | UNKNOWN | PENDING
    completed_at: datetime | None
    credit_event: str | None
    relief: bool                        # approved relief applies (MISSED units only, never earned ones)
    oos_charged: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OffplanFact:
    """Verified completion credited to no goal-of-record unit: unapproved offplan output (U) of the team-shift where it happened."""

    id: str
    team: str
    shift_id: str
    job: str
    phase: str
    effort: int | None                  # None: no sourced standard, so U is unknown
    completed_at: datetime
    event_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OOSFact:
    violation_id: str
    phase: str
    unit_ids: tuple[str, ...]
    affected_effort: int
    discretionary: bool | None
    planned: bool
    charged: bool
    reason: str


@dataclass(frozen=True, slots=True)
class EvaluationEvidence:
    """The exact canonical unit facts behind an evaluation; ``id`` is their content hash (the evaluation's evidence hash)."""

    id: str
    unit_facts: tuple[UnitFact, ...]
    offplan: tuple[OffplanFact, ...]
    oos: tuple[OOSFact, ...]
    offplan_oos: tuple[str, ...]        # violations on offplan work: already in U, no second numeric charge
    relief: dict[str, Any]
    coverage: tuple[str, ...]
    observation_run_id: str | None      # provenance only; excluded from the hash


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One canonical effort evaluation (S14). Components are exact; only ``score`` is rounded, after every sum."""

    id: str
    scope: str
    kind: str                           # GOAL | AGGREGATE
    label: str                          # e.g. "PROD-B DEMO:2026-10-26:S1" or "TEAM PROD-B 2026-10"
    series_key: str                     # one revision series per goal set + policy
    goal_ids: tuple[str, ...]
    goal_set_hash: str
    cutoff: datetime                    # as-known time of the evidence
    policy_id: str
    policy_hash: str
    policy_status: str                  # DEMONSTRATION | OFFICIAL (from the applicable policy receipt, never hardcoded)
    B0: Fraction
    B: Fraction
    E: Fraction
    D: Fraction
    Cp: Fraction
    U: Fraction | None
    O: Fraction | None
    attainment: Fraction | None
    adherence: Fraction | None
    base: Fraction | None
    final: Fraction | None              # unrounded clamp(base - 100*O/D, 0, 100)
    score: Decimal | None               # final rounded to the policy decimals
    grade: str | None
    status: EvaluationStatus
    reasons: tuple[str, ...]
    evidence_hash: str
    revision: int
    supersedes: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecognitionEvent:
    """One supplementary progression event. A chain of revisions shares a base key; corrections append, never rewrite."""

    id: str
    dedupe_key: str            # "<base key>#r<revision>"
    base_key: str
    event_type: str            # completion_credited | gate_recovery | dependency_unlocked | commitment_kept | excusal_decided | badge_earned | correction_applied
    scope: str
    team: str
    goal_id: str | None
    earning_unit_ids: tuple[str, ...]
    value: Fraction            # XP delta (frozen recognition value; corrections are negative)
    frozen_gate_context: GateContext | None
    evidence: tuple[str, ...]  # evaluation id, verified credit events, decisions ...
    policy_id: str
    revision: int
    supersedes: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BadgeAward:
    id: str
    dedupe_key: str
    badge: str                 # Recovery | Firebreak | CommitmentKeeper | SteadyShift | SkillSteward | MonthlyChampion
    scope: str
    team: str
    period: str                # goal id, team run or YYYY-MM
    evidence_ids: tuple[str, ...]
    statement: str             # what the badge does (and does not) mean
    awarded_at: datetime
    revision: int
    revoked: bool
    supersedes: str | None
