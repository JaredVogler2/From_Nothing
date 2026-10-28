"""Exact full-crew selection through the shared duty ledger (S07 ROLE MATCHING, S11 FULL CREW, P11).

A phase demanding N people of a role gets exactly N distinct people or none. Candidates are
the purpose's members of the pure pool (team, shift start date, shift number, source run).
Each candidate holds the full role bundle individually over the whole proposed interval,
has an admitted window in that shift, and can be booked by the ledger with both
neighbouring transfers, budget and windows respected. A candidate crew is accepted only
when the ledger plans the whole crew, which also enforces the activation cap. There is no
demand clamp, anonymous fallback, cross-team loan or fake person.

Preference among feasible people follows the documented tie order:

1. fewer usable applicable certificates within the team's demanded set, which keeps scarce
   multi-cert specialists for rare roles (raw, expired or irrelevant extras never count);
2. same-aircraft affinity (no transfer from the person's previous duty in the shift);
3. less duty already booked in the shift;
4. stable person id.

Incumbent defense passes an exact crew (attempt 1) or excludes nothing but records the
alternate crew (attempt 2); the caller records why a switch or generalist won.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from ..domain import DutyKind
from .ledger import Booking, DutyLedger, LedgerError

if TYPE_CHECKING:  # pragma: no cover
    from .model import EngineContext

MAX_COMBINATIONS = 2000


@dataclass(frozen=True)
class CrewChoice:
    crew: tuple[str, ...]
    booking: Booking
    considered: int
    qualified: int
    preference: tuple[tuple[str, tuple], ...]      # (person, key) in the order tried


def preference_key(ctx: "EngineContext", ledger: DutyLedger, person: str, team: str, line: int, shift_id: str, start: int,
                   end: int) -> tuple:
    usable = ctx.usable_applicable(person, team, start, end)
    last = ledger.last_located(person, shift_id, start)
    affinity = 0 if last is not None and last.line == line else 1
    regular, ot = ledger.duty_minutes(person, shift_id)
    return (usable, affinity, regular + ot, person)


def choose_crew(ctx: "EngineContext", ledger: DutyLedger, *, phase_id: str, assignment_id: str, line: int, team: str,
                shift_id: str, pool_id: str, count: int, bundle, segments: Sequence[tuple[int, int, bool]],
                kind: DutyKind = DutyKind.WORK, exact_crew: Sequence[str] | None = None,
                tools: Sequence[str] = ()) -> tuple[CrewChoice | None, str]:
    """Return (choice, reason). ``reason`` is a stable code when no full crew can be booked."""
    if count <= 0:
        return None, "CREW_DEMAND_UNAVAILABLE: no sourced positive crew demand (never clamped to 1)"
    start, end = segments[0][0], segments[-1][1]
    members = ctx.pool_members(pool_id)
    if exact_crew is not None:
        crew = tuple(exact_crew)
        if len(crew) != count:
            return None, f"CREW_SIZE_MISMATCH: {len(crew)} proposed for demand {count}"
        for person in crew:
            if person not in members:
                return None, f"NOT_POOL_MEMBER: {person} is not a {ctx.purpose.value} member of {pool_id}"
            if not ctx.qualifies(person, bundle, start, end):
                return None, f"NOT_QUALIFIED: {person} lacks the full role bundle over the interval"
        try:
            booking = ledger.plan(assignment_id, crew, line, shift_id, pool_id, segments, kind, tools)
        except LedgerError as exc:
            return None, f"{exc.code}: {exc.detail}"
        return CrewChoice(crew, booking, 1, len(crew), ()), "EXACT_CREW"
    qualified = [m for m in members if ctx.qualifies(m, bundle, start, end)]
    if len(qualified) < count:
        return None, f"CREW_UNQUALIFIED: {len(qualified)} qualified of {count} demanded in {pool_id}"
    feasible: list[str] = []
    last_problem = ""
    for person in qualified:
        try:
            ledger.plan(f"{assignment_id}?{person}", [person], line, shift_id, pool_id, segments, kind, tools)
            feasible.append(person)
        except LedgerError as exc:
            if exc.code != "ACTIVATION_CAP":
                last_problem = f"{exc.code}: {exc.detail}"
                continue
            feasible.append(person)          # the cap is a crew-level property, decided below
    if len(feasible) < count:
        return None, f"CREW_UNAVAILABLE: {len(feasible)} of {count} people free over the interval" + (f" ({last_problem})" if last_problem else "")
    keyed = sorted(((preference_key(ctx, ledger, p, team, line, shift_id, start, end), p) for p in feasible))
    ordered = [p for _, p in keyed]
    active = ledger.active(pool_id)
    room = ledger.pool_caps.get(pool_id, 0) - len(active)
    if room < count:
        # the cap forces reuse of already-active heads: try them first (reuse adds no head)
        ordered = [p for p in ordered if p in active] + [p for p in ordered if p not in active]
    tried = 0
    last_cap = ""
    for crew in itertools.combinations(ordered, count):
        tried += 1
        if tried > MAX_COMBINATIONS:
            return None, f"CREW_SEARCH_BOUND: more than {MAX_COMBINATIONS} combinations; no crew asserted"
        try:
            booking = ledger.plan(assignment_id, crew, line, shift_id, pool_id, segments, kind, tools)
        except LedgerError as exc:
            last_cap = f"{exc.code}: {exc.detail}"
            continue
        return CrewChoice(tuple(crew), booking, tried, len(qualified), tuple((p, k) for k, p in keyed)), "SELECTED"
    return None, last_cap or "NO_FEASIBLE_CREW"
