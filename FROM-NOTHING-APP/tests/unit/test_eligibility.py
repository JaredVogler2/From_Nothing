"""P06: individual AND/OR bundles, whole-interval validity, unknown census, role matching (A12, A13, A14)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from focus.contracts.planning import JobRequirement
from focus.data.qualifications import build_holdings, build_requirements, parse_cert_date
from focus.domain import DecisionStatus, Holding, RequirementBundle, RequirementStatus, minute_floor
from focus.eligibility import Eligibility, HoldingIndex, RoleSlot, match_crew

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
FORMS = ("YYYY/MM/DD", "YYYY-MM-DD", "YYYYMMDD")


def _m(text: str) -> int:
    return minute_floor(datetime.fromisoformat(text).astimezone(UTC))


def _holding(person: str, cert: str, start: str = "2026-01-01T05:00:00+00:00", end: str | None = "2028-01-01T05:00:00+00:00",
             status: str = "ACTIVE", validity: str = "BOUNDED", revoked: str | None = None) -> Holding:
    return Holding(person=person, cert=cert, status=status, validity=validity,
                   valid_from=datetime.fromisoformat(start) if start else None,
                   valid_to=datetime.fromisoformat(end) if end else None,
                   revoked_at=datetime.fromisoformat(revoked) if revoked else None, applicability="ALL_JOBS_REQUIRING_CODE",
                   raw_status=status, raw_last_cert_date=None, raw_expiry=None, evidence=())


def _required(job: str, groups: tuple[tuple[str, ...], ...], count: int = 1) -> JobRequirement:
    return JobRequirement(job, RequirementStatus.REQUIRED, RequirementBundle(groups, "mechanic", count, "exact SOI/line"), "test")


PEOPLE = {
    "a_only": ("A",),
    "b_only": ("B",),
    "abc": ("A", "B", "C"),
    "a_b2": ("A", "B2"),
}
WORK = (_m("2026-10-27T10:00:00+00:00"), _m("2026-10-27T13:00:00+00:00"))


@pytest.fixture()
def eligibility() -> Eligibility:
    holdings = [_holding(p, c) for p, certs in PEOPLE.items() for c in certs]
    reqs = {"J_AB": _required("J_AB", (("A",), ("B",)), 2), "J_ALT": _required("J_ALT", (("A",), ("B", "B2"))),
            "J_C": _required("J_C", (("C",),)), "J_A": _required("J_A", (("A",),))}
    return Eligibility(holdings, reqs, source_run_id="RUN-1", policy_hash="P1")


def test_a12_only_an_individual_superset_qualifies(eligibility: Eligibility) -> None:
    decide = {p: eligibility.check(p, None, "J_AB", *WORK) for p in PEOPLE}
    assert decide["abc"].allowed                                       # A+B+C covers {A,B}
    assert decide["a_only"].status is DecisionStatus.DENIED and "B:NOT_HELD" in decide["a_only"].reason
    assert decide["b_only"].status is DecisionStatus.DENIED and "A:NOT_HELD" in decide["b_only"].reason


def test_a12_union_across_two_insufficient_people_is_rejected(eligibility: Eligibility) -> None:
    bundle = eligibility.requirements["J_AB"].bundle
    slots = [RoleSlot("S1", "mechanic", bundle), RoleSlot("S2", "mechanic", bundle)]
    qualifies = lambda person, slot: eligibility.check(person, slot.bundle, "J_AB", *WORK)  # noqa: E731
    crew = match_crew(slots, ["a_only", "b_only", "abc"], qualifies)
    assert crew.status == "INFEASIBLE" and crew.people == ()           # A-only + B-only never pool certificates
    assert dict(crew.candidates) == {"S1": 1, "S2": 1}
    assert match_crew(slots[:1], ["a_only", "b_only", "abc"], qualifies).people == ("abc",)


def test_a12_approved_or_alternatives_are_checked_separately(eligibility: Eligibility) -> None:
    assert eligibility.check("a_b2", None, "J_ALT", *WORK).allowed    # A AND (B OR B2) via B2
    assert eligibility.check("abc", None, "J_ALT", *WORK).allowed     # via B
    denied = eligibility.check("a_only", None, "J_ALT", *WORK)
    assert not denied.allowed and "(B:NOT_HELD OR B2:NOT_HELD)" in denied.reason
    assert not eligibility.check("a_b2", None, "J_AB", *WORK).allowed  # B2 is not an approved alternative for J_AB


def test_role_matching_is_most_constrained_first_and_bounded(eligibility: Eligibility) -> None:
    slots = [RoleSlot("R_A", "mechanic", RequirementBundle((("A",),), "mechanic", 1, "t")),
             RoleSlot("R_C", "mechanic", RequirementBundle((("C",),), "mechanic", 1, "t"))]

    def qualifies(person, slot):
        return eligibility.index.check_bundle(person, slot.bundle, *WORK)

    # preferring "abc" for every seat would starve R_C; constrained-first ordering keeps the only C holder for R_C
    crew = match_crew(slots, PEOPLE, qualifies, preference=lambda p: (0 if p == "abc" else 1,))
    assert crew.status == "MATCHED" and dict(crew.assignment) == {"R_C": "abc", "R_A": "a_b2"}
    # a feasibility veto (e.g. duty overlap) on that crew falls back to the next distinct crew
    vetoed = match_crew(slots, PEOPLE, qualifies, preference=lambda p: (0 if p == "abc" else 1,), feasible=lambda c: "a_b2" not in c)
    assert dict(vetoed.assignment) == {"R_C": "abc", "R_A": "a_only"}
    # an incomplete search returns UNKNOWN rather than relaxing a role
    bounded = match_crew(slots, PEOPLE, qualifies, feasible=lambda c: False, max_nodes=2)
    assert bounded.status == "UNKNOWN" and bounded.people == ()


def test_scarcity_counts_usable_applicable_certs_only() -> None:
    holdings = [_holding("p", "A"), _holding("p", "B"), _holding("p", "C"),
                _holding("p", "X", end="2026-02-01T05:00:00+00:00")]          # expired, irrelevant extra
    elig = Eligibility(holdings, {"J": _required("J", (("A",), ("B",)))})
    assert elig.scarcity("p", "J", *WORK) == 2


def test_cache_keys_change_with_source_or_policy_revision() -> None:
    reqs = {"J": _required("J", (("A",),))}
    one = Eligibility([_holding("p", "A")], reqs, source_run_id="RUN-1", policy_hash="P1")
    assert one.fingerprint == Eligibility([_holding("p", "A")], reqs, source_run_id="RUN-1", policy_hash="P1").fingerprint
    assert one.fingerprint != Eligibility([_holding("p", "A")], reqs, source_run_id="RUN-2", policy_hash="P1").fingerprint
    assert one.fingerprint != Eligibility([_holding("p", "A")], reqs, source_run_id="RUN-1", policy_hash="P2").fingerprint
    revised = Eligibility([_holding("p", "A", end="2026-10-27T12:00:00+00:00")], reqs, source_run_id="RUN-1", policy_hash="P1")
    assert revised.fingerprint != one.fingerprint
    assert one.check("p", None, "J", *WORK).allowed and not revised.check("p", None, "J", *WORK).allowed


# ---------------------------------------------------------------------------- A13 dates and status

def _certs(policy, rows, events=()):
    ids = {r["BEMS_ID"]: r["BEMS_ID"] for r in rows}
    return build_holdings(rows, events, ids, policy, NY, "ds", "RUN-1")


def _row(person: str, expiry: str | None, start: str | None = "2026/01/01", status: str | None = "ACTIVE", cert: str = "A") -> dict:
    return {"BEMS_ID": person, "CERT_CODE": cert, "STATUS": status, "LAST_CERT_DATE": start, "CERT_EXP_DATE": expiry}


def test_a13_equivalent_encodings_agree_and_expiry_is_inclusive(policy) -> None:
    built = _certs(policy, [_row("slash", "2026/10/27"), _row("iso", "2026-10-27"), _row("compact", "20261027")])
    ends = {h.person: h.valid_to for h in built.holdings}
    assert len(set(ends.values())) == 1                                     # all three encodings agree
    assert ends["slash"] == datetime(2026, 10, 28, 4, 0, tzinfo=UTC)        # inclusive 10-27 -> next local midnight (EDT)
    index = HoldingIndex(built.holdings)
    assert index.holds("iso", "A", _m("2026-10-27T20:00:00-04:00"), _m("2026-10-28T00:00:00-04:00")) == (True, "VALID")   # boundary
    assert index.holds("iso", "A", _m("2026-10-27T22:00:00-04:00"), _m("2026-10-28T01:00:00-04:00")) == (False, "EXPIRES_DURING_INTERVAL")
    assert index.holds("iso", "A", _m("2026-10-28T06:00:00-04:00"), _m("2026-10-28T08:00:00-04:00")) == (False, "EXPIRED")


def test_a13_future_blank_malformed_and_unknown_status_reject(policy) -> None:
    built = _certs(policy, [_row("future", "2027/12/31", start="2026/11/02"), _row("blank", ""), _row("malformed", "27/12/2027"),
                            _row("range", "2027/13/45"), _row("nostart", "2027/12/31", start=None),
                            _row("weird", "2027/12/31", status="PENDING"), _row("inactive", "2027/12/31", status="SUSPENDED")])
    index = HoldingIndex(built.holdings)
    job = (_m("2026-10-28T10:00:00+00:00"), _m("2026-10-28T12:00:00+00:00"))
    assert index.holds("future", "A", *job) == (False, "NOT_YET_VALID")
    for person in ("blank", "malformed", "range", "nostart"):
        assert index.holds(person, "A", *job) == (False, "VALIDITY_UNKNOWN"), person
    assert index.holds("weird", "A", *job) == (False, "STATUS_UNKNOWN")
    assert index.holds("inactive", "A", *job) == (False, "STATUS_SUSPENDED")
    assert {"HOLDING_EXPIRY_BLANK:blank:A", "HOLDING_EXPIRY_MALFORMED:malformed:A", "HOLDING_EXPIRY_OUT_OF_RANGE:range:A",
            "HOLDING_START_BLANK:nostart:A"} <= set(built.problems)


def test_a13_revocation_wins_for_its_interval_and_reinstatement_restores(policy) -> None:
    events = [{"BEMS_ID": "r", "CERT_CODE": "A", "EVENT": "REVOKED", "EFFECTIVE_AT_UTC": "2026-10-29T12:00:00Z", "EVIDENCE_REF": "E1"}]
    index = HoldingIndex(_certs(policy, [_row("r", "2027/12/31")], events).holdings)
    assert index.holds("r", "A", _m("2026-10-29T08:00:00+00:00"), _m("2026-10-29T12:00:00+00:00"))[0] is True
    assert index.holds("r", "A", _m("2026-10-29T11:00:00+00:00"), _m("2026-10-29T13:00:00+00:00")) == (False, "REVOKED")
    restored = events + [{"BEMS_ID": "r", "CERT_CODE": "A", "EVENT": "REINSTATED", "EFFECTIVE_AT_UTC": "2026-10-30T12:00:00Z",
                          "EVIDENCE_REF": "E2"}]
    again = HoldingIndex(_certs(policy, [_row("r", "2027/12/31")], restored).holdings)
    assert again.holds("r", "A", _m("2026-10-29T11:00:00+00:00"), _m("2026-10-29T13:00:00+00:00"))[0] is True


def test_a13_perpetual_only_under_explicit_sentinel_policy(policy) -> None:
    for encoding in ("9999/12/31", "9999-12-31", "99991231"):
        assert parse_cert_date(encoding, FORMS, "9999/12/31").sentinel is True
    with_sentinel = _certs(policy, [_row("p", "9999-12-31")])
    assert with_sentinel.holdings[0].validity == "PERPETUAL"
    no_sentinel = policy.with_overrides({"qualifications.perpetual_sentinel": None}, new_id="no-sentinel", reason="A13")
    built = _certs(no_sentinel, [_row("p", "9999/12/31")])                   # no year-10000 arithmetic
    assert built.holdings[0].validity == "UNKNOWN" and "HOLDING_EXPIRY_UNREPRESENTABLE:p:A" in built.problems
    assert HoldingIndex(built.holdings).holds("p", "A", *WORK) == (False, "VALIDITY_UNKNOWN")


# ---------------------------------------------------------------------------- A14 requirement census

def test_a14_unknown_requirements_block_and_affirmative_none_allows() -> None:
    jobs = {(1303, "NC3"): "J-NC3", (1306, "PSC6"): "J-PSC6", (1303, "PU3"): "J-PU3"}
    rows = [{"LINE_NUMBER": 1303, "SOI_ID": "PU3", "CERT_CODE": "B", "REQ_CERT": "Y"}]
    census = [{"LINE_NUMBER": 1306, "SOI_ID": "PSC6", "STATUS": "AFFIRMATIVELY_NONE"}]
    reqs = build_requirements(jobs, rows, [], census, {"J-NC3": 2, "J-PSC6": 1, "J-PU3": None}, {}, "ds", "RUN-1")
    assert reqs["J-NC3"].status is RequirementStatus.UNKNOWN and reqs["J-NC3"].bundle is None
    assert reqs["J-PSC6"].status is RequirementStatus.AFFIRMATIVELY_NONE
    assert reqs["J-PU3"].bundle.count == 0                                  # unavailable crew demand is not clamped to 1
    elig = Eligibility([_holding("anyone", "B")], reqs)
    nc = elig.check("anyone", None, "J-NC3", *WORK)
    assert nc.status is DecisionStatus.UNKNOWN and nc.code == "REQUIREMENTS_UNKNOWN"   # rework never cleared
    assert elig.check("nobody", None, "J-PSC6", *WORK).allowed                          # affirmative cert-free
    assert elig.check("anyone", None, "J-PU3", *WORK).allowed


def test_a14_subst_matrix_label_grants_nothing(policy) -> None:
    from focus.data.qualifications import matrix_label_notes

    reqs = {"J-ROOT3": _required("J-ROOT3", (("B",),))}
    elig = Eligibility([_holding("DEMO-A1-01", "A")], reqs)
    rows = [{"BEMS_ID": "DEMO-A1-01", "JOB_SOI": "ROOT3", "LINE_NUMBER": 1303, "ELIGIBLE": "ELIGIBLE", "PCT_CERTS_HELD": "100.0",
             "SUBSTITUTION": "SUBST"}]
    notes = matrix_label_notes(rows * 12, elig, {"DEMO-A1-01": "DEMO-A1-01"}, {(1303, "ROOT3"): "J-ROOT3"}, WORK[0])
    assert len(notes) == 1                                                          # bounded: one note per label/decision
    code, scope, detail, effect = notes[0]
    assert (code, scope, effect) == ("CERT_MATRIX_PROJECTION_ONLY", "FOCUS_CERT_ELIGIBILITY", "INFO")
    assert detail.startswith("12 row(s) ELIGIBLE=ELIGIBLE SUBSTITUTION=SUBST: authoritative denied CERT_BUNDLE_NOT_HELD; e.g. DEMO-A1-01 ROOT3@1303")
    assert detail.count("DEMO-A1-01") == 5                                          # at most five examples
    assert not elig.check("DEMO-A1-01", None, "J-ROOT3", *WORK).allowed
