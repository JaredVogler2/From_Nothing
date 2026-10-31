"""Gate recognition: the one authority for frozen recognition value (S14 GATE RECOGNITION, P18).

Rules:

* base = frozen standard person effort / ``recognition.base_divisor`` (demo 5).
* behind_raw = min(max(valuation_date - effective_gate_date, 0) / ``gate.cap_days``, 1).
* bonus = base * ``gate.weight``/100 * behind_raw (demo weight 35, cap 5).

Date basis (``gate.date_basis`` or an explicit basis):

* ``ACTUAL_CS_GATE``: the true gate date, unchanged.
* ``BASELINE_PROXY``: + ``gate.proxy_grace_days`` calendar days, applied exactly once.
* ``ALREADY_ADJUSTED_PROXY``: unchanged, so the grace is never applied twice.
* A missing gate is UNAVAILABLE (no bonus, never zero-filled).

Ages:

* A day-0 or past gate is real.
* A future gate has factor 0 (never negative).
* Completion stops aging: the valuation date never passes the verified completion date.

At a true gate, ages 0/2/3/5/7 give raw 0, 0.4, 0.6, 1 and 1.

**Freeze.** Publication freezes the basis, date, valuation, effort and factor in each
``FrozenGoal`` (``publication.serializer.freeze_goals`` calls ``gate_context`` here). Recognition is
earned once, from verified completion, at that frozen value. A later display or refresh date never
increases XP that was already earned.

**Display.** ``behind_station`` values one station gate on a display date for the twin (S01): basis, ages, grace and
factor, never XP.
"""
from __future__ import annotations

from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from ..contracts.planning import GateContext
from ..domain import DomainError

BASES = ("ACTUAL_CS_GATE", "BASELINE_PROXY", "ALREADY_ADJUSTED_PROXY")


def effective_gate(basis: str, gate_date: date, grace_days: int) -> date:
    if basis == "ACTUAL_CS_GATE" or basis == "ALREADY_ADJUSTED_PROXY":
        return gate_date
    if basis == "BASELINE_PROXY":
        return gate_date + timedelta(days=grace_days)
    raise DomainError("BAD_GATE_BASIS", f"unknown gate date basis {basis!r}")


def behind_raw(valuation: date, effective: date, cap_days: int) -> tuple[int, Fraction]:
    behind = max((valuation - effective).days, 0)
    return behind, min(Fraction(behind, cap_days), Fraction(1))


def gate_context(policy: Any, gate_date: date | None, valuation: date, effort: int, *, basis: str | None = None,
                 completed_on: date | None = None) -> GateContext:
    """The frozen recognition context of ``effort`` standard person minutes valued on ``valuation``."""
    basis = basis or policy.str("gate.date_basis")
    if basis not in BASES:
        raise DomainError("BAD_GATE_BASIS", f"unknown gate date basis {basis!r}")
    base = Fraction(effort, policy.int("recognition.base_divisor"))
    if gate_date is None:
        return GateContext("UNAVAILABLE", None, None, valuation, None, None, base, None)
    if completed_on is not None and completed_on < valuation:
        valuation = completed_on                                    # completed work stops aging
    effective = effective_gate(basis, gate_date, policy.int("gate.proxy_grace_days"))
    behind, raw = behind_raw(valuation, effective, policy.int("gate.cap_days"))
    return GateContext(basis, gate_date, effective, valuation, behind, raw, base, base * Fraction(policy.int("gate.weight"), 100) * raw)


def behind_station(policy: Any, gate_date: date | None, valuation: date, *, basis: str | None = None) -> dict[str, Any]:
    """A station gate's behind-station value on ``valuation``, with its explicit date basis (S01, S14).

    ``bonus_factor`` = ``gate.weight``/100 * behind_raw: the share of base recognition a unit completed on ``valuation``
    adds. ``grace_days`` is the grace this basis applies (``gate.proxy_grace_days`` for a baseline proxy, else 0). A missing
    gate is UNAVAILABLE: its ages and factor are None, never zero.
    """
    c = gate_context(policy, gate_date, valuation, 0, basis=basis)
    weight = policy.int("gate.weight")
    return {"basis": c.basis, "gate_date": c.gate_date, "effective_gate_date": c.effective_gate_date,
            "grace_days": policy.int("gate.proxy_grace_days") if c.basis == "BASELINE_PROXY" else 0,
            "valuation_date": c.valuation_date, "behind_days": c.behind_days, "behind_raw": c.behind_raw,
            "bonus_factor": None if c.behind_raw is None else Fraction(weight, 100) * c.behind_raw,
            "weight": weight, "cap_days": policy.int("gate.cap_days")}


def unit_values(context: GateContext | None, unit_effort: int, policy: Any) -> tuple[Fraction, Fraction]:
    """(completion value, gate bonus) of one earned unit at its job's frozen context; a missing context earns base only."""
    base = Fraction(unit_effort, policy.int("recognition.base_divisor"))
    if context is None or context.behind_raw is None:
        return base, Fraction(0)
    return base, base * Fraction(policy.int("gate.weight"), 100) * context.behind_raw
