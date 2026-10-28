"""Transfer time between consecutive located duties (S11 TRANSITION, P11).

Every crew member reserves a transfer for each consecutive aircraft change:
the fixture uses 15 minutes for a changed aircraft and 0 for the same aircraft,
so A -> B -> A is two transfers (never a visited-aircraft set). The first located
duty of a shift starts at the muster point of that assignment
(``travel.shift_start_origin = MUSTER_AT_FIRST_ASSIGNMENT``). After a duty with
no known aircraft location (for example off-aircraft training) the next aircraft
needs ``travel.unknown_origin_minutes``; when that policy is absent the transfer
is impossible rather than assumed free. Transfers occupy contiguous open time and
never cross a protected break.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..policy import Policy


@dataclass(frozen=True)
class TravelPolicy:
    same_aircraft_minutes: int
    changed_aircraft_minutes: int
    unknown_origin_minutes: int | None
    shift_start_origin: str
    allow_during_protected: bool

    @classmethod
    def from_policy(cls, policy: Policy) -> "TravelPolicy":
        unknown = policy.optional("travel.unknown_origin_minutes")
        origin = policy.str("travel.shift_start_origin")
        if origin != "MUSTER_AT_FIRST_ASSIGNMENT":
            raise ValueError(f"unsupported travel.shift_start_origin {origin!r}")
        if policy.bool("travel.allow_during_protected_windows"):
            raise ValueError("travel during protected windows is not an admitted fixture policy")
        return cls(
            same_aircraft_minutes=policy.int("travel.same_aircraft_minutes"),
            changed_aircraft_minutes=policy.int("travel.changed_aircraft_minutes"),
            unknown_origin_minutes=int(unknown) if unknown is not None else None,
            shift_start_origin=origin,
            allow_during_protected=False,
        )

    def minutes(self, origin_line: int | None, origin_known: bool, target_line: int) -> int | None:
        """Transfer minutes into ``target_line``; None when the origin is unknown and no bounded policy exists."""
        if not origin_known:
            return self.unknown_origin_minutes
        return self.same_aircraft_minutes if origin_line == target_line else self.changed_aircraft_minutes
