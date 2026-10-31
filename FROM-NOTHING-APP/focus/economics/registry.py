"""EconomicsRegistry: exact modeled penalties and marginal priority weights (S12, P12).

One effective term per aircraft comes from ``focus.economics.repository`` (S04 objects,
selector precedence AIRCRAFT_OVERRIDE > CONTRACT > CUSTOMER_MODEL). The registry turns it
into exact money:

* **Supported curve.** Only ``LINEAR_DAILY``:
  ``raw = rate_cents_per_day * max(0, completion - due) / 1440``, totalled as
  ``min(cap, raw)`` when an explicit cap exists. An unsupported curve is UNSUPPORTED, never
  silently linear.
* **Exact money.** Cents stay exact Fractions. A known zero rate is known zero pressure; a
  missing term, due or authority is UNKNOWN, never zero.
* **Priority weights.** ``derive_priority_weights`` evaluates the marginal penalty of one
  more day (``economics.weight_horizon_days``) at each aircraft's projected completion,
  respecting the cap. Weights are normalized exactly by the largest marginal, with no
  rounding that could erase rate differences, and raw rate, marginal and normalized values
  are all exposed. Weights only guide dispatch. Acceptance always compares exact
  complete-candidate costs (``focus.economics.evaluator``).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from ..contracts.planning import AircraftEconomics
from ..domain import to_minute
from ..policy import Policy

SUPPORTED_CURVES = ("LINEAR_DAILY",)


@dataclass(frozen=True)
class WeightRecord:
    line: int
    status: str                       # known | unknown | unsupported
    raw_rate_cents: Fraction | None
    marginal_cents: Fraction | None   # over the weight horizon at the projected completion
    normalized: Fraction              # 0..1, exact
    basis: str


class EconomicsRegistry:
    def __init__(self, economics: Mapping[int, AircraftEconomics], policy: Policy):
        self.economics = dict(economics)
        self.curve = policy.str("economics.penalty_curve")
        self.horizon_days = policy.int("economics.weight_horizon_days")
        self.tolerance = Fraction(policy.int("economics.monetary_tolerance_cents"))

    def term(self, line: int) -> AircraftEconomics | None:
        return self.economics.get(line)

    def supported(self, line: int) -> tuple[bool, str]:
        econ = self.economics.get(line)
        if econ is None:
            return False, "ECONOMICS_MISSING"
        if econ.status == "UNSUPPORTED":
            return False, "; ".join(econ.reasons) or "UNSUPPORTED_TERM"
        if econ.status != "KNOWN" or econ.daily_rate_cents is None or econ.due_at is None:
            return False, "; ".join(econ.reasons) or "TERM_OR_DUE_UNKNOWN"
        if (econ.curve or self.curve) not in SUPPORTED_CURVES:
            return False, f"UNSUPPORTED_CURVE: {econ.curve or self.curve}"
        return True, "KNOWN"

    def penalty(self, line: int, completion_minute: int) -> Fraction | None:
        """Exact modeled penalty (Fraction cents) if the aircraft completes at ``completion_minute``.

        The cumulative already-accounted exposure is a floor (S12): the modeled exposure never counts below what accounting
        has booked, so the controllable increment and the marginal priority weight both start above it.
        """
        ok, _ = self.supported(line)
        if not ok:
            return None
        econ = self.economics[line]
        delay = max(0, completion_minute - to_minute(econ.due_at))   # type: ignore[arg-type]
        raw = econ.daily_rate_cents * delay / 1440                    # type: ignore[operator]
        modeled = min(raw, econ.cap_cents) if econ.cap_cents is not None else raw
        return max(modeled, Fraction(econ.accrued_floor_cents)) if econ.accrued_floor_cents is not None else modeled

    def marginal(self, line: int, completion_minute: int, days: int | None = None) -> Fraction | None:
        span = 1440 * (days if days is not None else self.horizon_days)
        now = self.penalty(line, completion_minute)
        later = self.penalty(line, completion_minute + span)
        return None if now is None or later is None else later - now

    def derive_priority_weights(self, completions: Mapping[int, int | None]) -> dict[int, WeightRecord]:
        """Marginal penalty of one more day at each projected completion, normalized exactly."""
        raw: dict[int, tuple[str, Fraction | None, Fraction | None, str]] = {}
        for line in sorted(self.economics):
            ok, why = self.supported(line)
            econ = self.economics[line]
            if not ok:
                raw[line] = ("unsupported" if econ.status == "UNSUPPORTED" else "unknown", None, None, why)
                continue
            completion = completions.get(line)
            if completion is None:
                # completion unknown: pressure is the marginal once the aircraft is past due
                at = to_minute(econ.due_at)                            # type: ignore[arg-type]
                basis = "COMPLETION_UNKNOWN: marginal evaluated at the due instant"
            else:
                at, basis = completion, "projected completion"
            raw[line] = ("known", econ.daily_rate_cents, self.marginal(line, at), basis)
        peak = max((m for _, _, m, _ in raw.values() if m is not None), default=Fraction(0))
        out: dict[int, WeightRecord] = {}
        for line, (status, rate, marginal, basis) in raw.items():
            normalized = (marginal / peak) if (marginal is not None and peak > 0) else Fraction(0)
            out[line] = WeightRecord(line, status, rate, marginal, normalized, basis)
        return out
