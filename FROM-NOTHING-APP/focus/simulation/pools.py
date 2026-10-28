"""Virtual MHE/PSC/TST support pools for labeled forecasts (S01, S07, S16, P06, P21).

Only the configured nonproduction planning teams (``support.virtual_teams``) get
exactly ``support.virtual_people_per_team_shift`` (10) distinct virtual people per
team and shift. Each holds the union of that team's required certifications and
carries an immutable synthetic origin, namespace and FORECAST_ONLY purpose.
Executable crew acceptance rejects them by origin, whatever flags a payload claims.
Missing real support capacity stays a visible readiness gap; support tasks are never
removed from the full inventory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from ..calendar import SHIFT_NUMBERS, ShiftPolicy, build_shift
from ..contracts.planning import Availability, JobRequirement, Person, Pool
from ..domain import (AvailabilityBasis, EvidenceRef, Holding, Interval, PersonKey, PersonOrigin, PoolKey, RequirementStatus, RuleDecision,
                      digest_id, from_minute)
from ..policy import Policy
from ..data.sourcetime import local_midnight_utc
from ..data.workforce import activation_cap

VIRTUAL_NAMESPACE = "synthetic-virtual-support"


@dataclass
class VirtualSupport:
    people: list[Person] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    availability: list[Availability] = field(default_factory=list)
    pool_members: dict[str, tuple[str, ...]] = field(default_factory=dict)     # pool id -> virtual person ids
    pool_keys: dict[str, PoolKey] = field(default_factory=dict)
    union_certs: dict[str, tuple[str, ...]] = field(default_factory=dict)


def is_synthetic_person(person: Person) -> bool:
    """Origin evidence decides; a spoofed flag cannot make a virtual person real."""
    return person.origin is PersonOrigin.SYNTHETIC_VIRTUAL or person.synthetic or person.key.authority_namespace == VIRTUAL_NAMESPACE


def build_virtual_support(*, policy: Policy, job_teams: Mapping[str, str | None], requirements: Mapping[str, JobRequirement],
                          horizon: tuple[date, date] | None, shift_policy: ShiftPolicy, site: str, run_id: str) -> VirtualSupport:
    out = VirtualSupport()
    if horizon is None:
        return out
    teams = tuple(policy.seq("support.virtual_teams"))
    per = policy.int("support.virtual_people_per_team_shift")
    num, den = policy.int("activation.fraction_numerator"), policy.int("activation.fraction_denominator")
    tz = ZoneInfo(shift_policy.tz_name)
    lo, hi = horizon
    valid_from = local_midnight_utc(lo, tz)
    valid_to = local_midnight_utc(hi + timedelta(days=1), tz)
    for team in teams:
        certs: set[str] = set()
        for job, job_team in job_teams.items():
            req = requirements.get(job)
            if job_team == team and req is not None and req.status is RequirementStatus.REQUIRED and req.bundle is not None:
                certs |= set(req.bundle.all_codes)
        out.union_certs[team] = tuple(sorted(certs))
        for n in SHIFT_NUMBERS:
            ids = []
            for i in range(1, per + 1):
                key = PersonKey(f"VIRTUAL-{team}-S{n}-{i:02d}", VIRTUAL_NAMESPACE)
                person = Person(key=key, display_name=f"Virtual {team} S{n} #{i:02d}", origin=PersonOrigin.SYNTHETIC_VIRTUAL,
                                synthetic=True, namespace=VIRTUAL_NAMESPACE, purpose_scope="FORECAST_ONLY",
                                evidence=(EvidenceRef("policy:support.virtual_people_per_team_shift", f"{team}/S{n}/{i}"),))
                out.people.append(person)
                ids.append(person.id)
                for cert in sorted(certs):
                    out.holdings.append(Holding(
                        person=person.id, cert=cert, status="ACTIVE", validity="BOUNDED", valid_from=valid_from, valid_to=valid_to,
                        revoked_at=None, applicability="SYNTHETIC_TEAM_UNION", raw_status="SYNTHETIC", raw_last_cert_date=None,
                        raw_expiry=None, evidence=(EvidenceRef("synthetic-union", f"{team}:{cert}"),),
                    ))
            d = lo
            while d <= hi:
                shift = build_shift(d, n, shift_policy, site)
                pool_key = PoolKey(site, team, d, n, run_id)
                out.pool_keys[pool_key.id] = pool_key
                out.pool_members[pool_key.id] = tuple(sorted(ids))
                for pid in ids:
                    out.availability.append(Availability(
                        id=digest_id("AV", pool_key.id, pid),
                        person=pid,
                        pool=pool_key.id,
                        intervals=tuple(Interval(from_minute(ws), from_minute(we)) for ws, we in shift.regular_windows),
                        other_duty=(),
                        ot_intervals=(),
                        regular_cap=shift.regular_cap,
                        ot_cap=0,
                        admitted_presence=False,
                        basis=AvailabilityBasis.SYNTHETIC,
                        evidence=(EvidenceRef("synthetic-virtual-support", f"{team}/{d.isoformat()}/S{n}"),),
                    ))
                d += timedelta(days=1)
    return out


def merge_virtual_pools(pools: Iterable[Pool], virtual: VirtualSupport, policy: Policy) -> list[Pool]:
    """Add virtual members to forecast membership only; executable membership never changes."""
    num, den = policy.int("activation.fraction_numerator"), policy.int("activation.fraction_denominator")
    by_id = {p.id: p for p in pools}
    for pool_id, members in virtual.pool_members.items():
        existing = by_id.get(pool_id)
        if existing is None:
            key = virtual.pool_keys[pool_id]
            by_id[pool_id] = Pool(key=key, members=members, executable_members=(), forecast_members=members, synthetic=True,
                                  n_executable=0, cap_executable=0, n_forecast=len(members), cap_forecast=activation_cap(len(members), num, den),
                                  basis="SYNTHETIC_VIRTUAL_SUPPORT", excluded=())
        else:
            fore = tuple(sorted(set(existing.forecast_members) | set(members)))
            by_id[pool_id] = Pool(key=existing.key, members=tuple(sorted(set(existing.members) | set(members))),
                                  executable_members=existing.executable_members, forecast_members=fore, synthetic=True,
                                  n_executable=existing.n_executable, cap_executable=existing.cap_executable,
                                  n_forecast=len(fore), cap_forecast=activation_cap(len(fore), num, den),
                                  basis=existing.basis + "+SYNTHETIC_VIRTUAL_SUPPORT", excluded=existing.excluded)
    return [by_id[k] for k in sorted(by_id, key=lambda pid: (by_id[pid].key.start_date, by_id[pid].key.shift_number, by_id[pid].key.team))]


def admit_executable_crew(crew: Iterable[str], pool: Pool, people: Mapping[str, Person],
                          claims: Mapping[str, object] | None = None) -> RuleDecision:
    """Executable crew admission: stored origin evidence and pure-pool membership decide.

    ``claims`` (payload flags such as ``mock_data``, ``synthetic`` or ``purpose``) are recorded
    in the reason and never trusted: a virtual person stays forecast-only whatever a request
    says. Every member must be a distinct, admitted, non-synthetic executable member of this
    exact pool (no borrowing from another team or shift).
    """
    members = list(crew)
    if not members:
        return RuleDecision.deny("CREW_EMPTY", "an executable crew needs at least one person")
    if len(set(members)) != len(members):
        return RuleDecision.deny("CREW_DUPLICATE_PERSON", "one person cannot fill two simultaneous seats")
    ignored = f" (payload claims ignored: {sorted(claims)})" if claims else ""
    if pool.synthetic and not pool.executable_members:
        return RuleDecision.deny("POOL_SYNTHETIC_ONLY", f"pool {pool.id} has no admitted executable people{ignored}")
    for pid in members:
        person = people.get(pid)
        if person is None:
            return RuleDecision.deny("PERSON_NOT_ADMITTED", f"{pid} is not an admitted person of this snapshot{ignored}")
        if is_synthetic_person(person):
            return RuleDecision.deny("SYNTHETIC_NOT_EXECUTABLE", f"{pid} has synthetic origin {person.origin.value}; forecast only{ignored}")
        if pid not in pool.executable_members:
            return RuleDecision.deny("NOT_EXECUTABLE_POOL_MEMBER", f"{pid} is not an executable member of {pool.id}{ignored}")
    return RuleDecision.allow("CREW_ADMITTED", f"{len(members)} distinct admitted executable members of {pool.id}")
