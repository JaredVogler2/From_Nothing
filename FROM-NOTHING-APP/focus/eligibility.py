"""Certification eligibility: the single rule authority (S07, P06).

For each AND group at least one approved OR alternative must be held by the
SAME person throughout the whole proposed active interval ``[start, end)``.
That means status ACTIVE, known validity, ``valid_from <= start``, a bounded
``valid_to >= end`` (or perpetual by sentinel policy), and no revocation
effective before ``end``. Certificates are never unioned across people.
Unknown requirements are UNKNOWN (blocked). An affirmative no-cert census
allows. Percent-held and SUBST labels grant nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .contracts.planning import JobRequirement
from .domain import EvidenceRef, Holding, RequirementBundle, RequirementStatus, RuleDecision, minute_floor

_PERPETUAL = 2**62


@dataclass(frozen=True, slots=True)
class _H:
    cert: str
    active: bool
    status: str
    known: bool
    start: int | None
    end: int            # exclusive; _PERPETUAL when perpetual
    revoked: int | None
    evidence: tuple[EvidenceRef, ...]


class HoldingIndex:
    """Per-person certificate holdings in integer UTC minutes."""

    def __init__(self, holdings: Iterable[Holding]):
        self._by_person: dict[str, dict[str, list[_H]]] = {}
        for h in holdings:
            start = minute_floor(h.valid_from) if h.valid_from else None
            if h.validity == "PERPETUAL":
                end = _PERPETUAL
            elif h.validity == "BOUNDED" and h.valid_to is not None:
                end = minute_floor(h.valid_to)
            else:
                end = -1
            item = _H(
                cert=h.cert,
                active=h.status == "ACTIVE",
                status=h.status,
                known=h.validity in ("BOUNDED", "PERPETUAL") and start is not None,
                start=start,
                end=end,
                revoked=minute_floor(h.revoked_at) if h.revoked_at else None,
                evidence=h.evidence,
            )
            self._by_person.setdefault(h.person, {}).setdefault(h.cert, []).append(item)

    def certs_of(self, person: str) -> tuple[str, ...]:
        return tuple(sorted(self._by_person.get(person, {})))

    @staticmethod
    def _covers(h: _H, start: int, end: int) -> tuple[bool, str]:
        if not h.active:
            return False, f"STATUS_{h.status}"
        if not h.known or h.start is None:
            return False, "VALIDITY_UNKNOWN"
        if h.start > start:
            return False, "NOT_YET_VALID"
        if h.end < end:
            return False, "EXPIRED" if h.end <= start else "EXPIRES_DURING_INTERVAL"
        if h.revoked is not None and h.revoked < end:
            return False, "REVOKED"
        return True, "VALID"

    def holds(self, person: str, cert: str, start: int, end: int) -> tuple[bool, str]:
        items = self._by_person.get(person, {}).get(cert)
        if not items:
            return False, "NOT_HELD"
        reasons = []
        for h in items:
            ok, why = self._covers(h, start, end)
            if ok:
                return True, why
            reasons.append(why)
        return False, sorted(reasons)[0]

    def usable_certs(self, person: str, start: int, end: int, applicable: Iterable[str] | None = None) -> frozenset[str]:
        """Certificates validly held over the interval (optionally restricted to applicable codes)."""
        wanted = None if applicable is None else set(applicable)
        out = set()
        for cert in self._by_person.get(person, {}):
            if wanted is not None and cert not in wanted:
                continue
            if self.holds(person, cert, start, end)[0]:
                out.add(cert)
        return frozenset(out)

    def check_bundle(self, person: str, bundle: RequirementBundle, start: int, end: int) -> RuleDecision:
        missing: list[str] = []
        for group in bundle.groups:
            group_reasons = []
            for cert in group:
                ok, why = self.holds(person, cert, start, end)
                if ok:
                    break
                group_reasons.append(f"{cert}:{why}")
            else:
                missing.append("(" + " OR ".join(group_reasons) + ")")
        if missing:
            return RuleDecision.deny("CERT_BUNDLE_NOT_HELD", "missing " + " AND ".join(missing))
        return RuleDecision.allow("CERT_BUNDLE_HELD", "every AND group held by this person for the whole interval")


class Eligibility:
    """``Eligibility.check(person, role, job, interval, snapshot) -> RuleDecision`` (S03 SERVICES)."""

    def __init__(self, holdings: Iterable[Holding], requirements: Mapping[str, JobRequirement]):
        self.index = HoldingIndex(holdings)
        self.requirements = dict(requirements)
        self._cache: dict[tuple[str, str, int, int, int], RuleDecision] = {}

    def requirement_status(self, job: str) -> RequirementStatus:
        req = self.requirements.get(job)
        return RequirementStatus.UNKNOWN if req is None else req.status

    def check(self, person: str, bundle: RequirementBundle | None, job: str, start: int, end: int) -> RuleDecision:
        req = self.requirements.get(job)
        if req is None or req.status is RequirementStatus.UNKNOWN:
            return RuleDecision.unknown("REQUIREMENTS_UNKNOWN", f"job {job} has no positive requirement rows and no affirmative census")
        if req.status is RequirementStatus.AFFIRMATIVELY_NONE and (bundle is None or not bundle.groups):
            return RuleDecision.allow("AFFIRMATIVELY_NONE", "affirmative no-certification census receipt")
        effective = bundle if bundle is not None else req.bundle
        if effective is None or not effective.groups:
            return RuleDecision.allow("NO_GROUPS", "role bundle has no certificate groups")
        key = (person, job, start, end, id(effective))
        cached = self._cache.get(key)
        if cached is None:
            cached = self.index.check_bundle(person, effective, start, end)
            self._cache[key] = cached
        return cached
