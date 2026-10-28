"""P12: exact registry money, caps, known zero versus unknown, and the exact evaluator governing acceptance (A45, A46)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

from focus.contracts.planning import AircraftCompletion, AircraftEconomics
from focus.data.acquisition import load_snapshot
from focus.data.fixture_reader import FixtureSourceReader
from focus.domain import to_minute
from focus.economics.evaluator import line_costs, summarize
from focus.economics.registry import EconomicsRegistry
from tests.conftest import acquire_demo

UTC = timezone.utc
DUE = datetime(2026, 10, 27, 4, tzinfo=UTC)
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _econ(line: int, rate: Fraction | None, cap: Fraction | None = None, status: str = "KNOWN", curve: str = "LINEAR_DAILY") -> AircraftEconomics:
    return AircraftEconomics(line, status, "ASSUMED", f"T{line}" if rate is not None else None, "AIRCRAFT_OVERRIDE", "USD", rate, cap, None, None,
                             DUE if status == "KNOWN" else None, "test due", curve, ())


def _done(line: int, at: datetime | None) -> AircraftCompletion:
    return AircraftCompletion(line, "known" if at else "unknown", at, 1, 1 if at else 0, () if at else ("J",), ())


def test_exact_linear_penalty_with_subcent_rates_and_caps(policy) -> None:
    reg = EconomicsRegistry({1: _econ(1, Fraction(10000)), 2: _econ(2, Fraction(1000001, 100)), 3: _econ(3, Fraction(10000), cap=Fraction(5000))},
                            policy)
    due = to_minute(DUE)
    assert reg.penalty(1, due - 60) == 0                                             # early or on time: no penalty
    assert reg.penalty(1, due + 90) == Fraction(625)                                 # 10000 * 90 / 1440 exactly
    assert reg.penalty(2, due + 1) == Fraction(1000001, 144000)                      # fractional cents kept exact, never rounded
    assert reg.penalty(3, due + 2 * 1440) == Fraction(5000)                          # explicit cap only
    assert reg.marginal(3, due + 2 * 1440) == 0 and reg.marginal(1, due) == Fraction(10000)


def test_known_zero_differs_from_unknown_and_unsupported(policy) -> None:
    reg = EconomicsRegistry({1: _econ(1, Fraction(0)), 2: _econ(2, None, status="UNKNOWN"), 3: _econ(3, Fraction(100), curve="STEP_WEEKLY")},
                            policy)
    due = to_minute(DUE)
    assert reg.penalty(1, due + 5000) == 0 and reg.supported(1) == (True, "KNOWN")      # supported true zero stays known zero
    assert reg.penalty(2, due + 5000) is None and reg.penalty(3, due) is None          # unknown/unsupported never become 0 or linear
    weights = reg.derive_priority_weights({1: due + 100, 2: None, 3: due})
    assert weights[1].status == "known" and weights[1].normalized == 0
    assert weights[2].status == "unknown" and weights[2].marginal_cents is None
    assert reg.supported(3)[1] == "UNSUPPORTED_CURVE: STEP_WEEKLY"


def test_weights_are_exact_marginals_normalized_without_rounding(policy) -> None:
    reg = EconomicsRegistry({1: _econ(1, Fraction(10000)), 2: _econ(2, Fraction(1000)), 3: _econ(3, Fraction(1003))}, policy)
    late = to_minute(DUE) + 60
    w = reg.derive_priority_weights({1: late, 2: late, 3: late})
    assert (w[1].normalized, w[2].normalized, w[3].normalized) == (1, Fraction(1, 10), Fraction(1003, 10000))   # 1000 vs 1003 stay distinct
    assert w[1].raw_rate_cents == 10000 and w[1].marginal_cents == 10000


def test_a45_exact_evaluator_beats_weighted_tardiness_and_keeps_unknowns(policy) -> None:
    reg = EconomicsRegistry({1: _econ(1, Fraction(10000), cap=Fraction(5000)), 2: _econ(2, Fraction(4000)), 3: _econ(3, Fraction(2000))}, policy)
    cutoff = to_minute(DUE) - 600
    x = {1: _done(1, DUE + timedelta(days=2)), 2: _done(2, DUE), 3: _done(3, None)}    # aircraft 1 late (capped), 2 on time
    y = {1: _done(1, DUE), 2: _done(2, DUE + timedelta(days=2)), 3: _done(3, None)}    # aircraft 1 on time, 2 late
    weighted = lambda c: sum(reg.term(l).daily_rate_cents * max(0, (c[l].completion_at - DUE).days) for l in (1, 2))  # noqa: E731,E741
    assert weighted(y) < weighted(x)                                                   # tardiness weights prefer Y ...
    sx, sy = summarize(line_costs(reg, x, cutoff)), summarize(line_costs(reg, y, cutoff))
    assert sx.controllable_known_cents == 5000 and sy.controllable_known_cents == 8000 # ... the exact dollars prefer X
    assert sx.unknown_lines == sy.unknown_lines == (3,) and not sx.complete            # unknown aircraft listed, never zero
    unknown = next(c for c in sx.lines if c.line == 3)
    assert unknown.status == "unknown" and unknown.total_cents is None and unknown.marginal_day_cents == 2000


def _snapshot(services_factory, key, mutate):
    names = ("econ_term", "econ_original_sale_basis", "econ_contract_assignment", "cs_windows")
    data = {n: json.loads((INPUTS / f"{n}.json").read_text()) for n in names}
    mutate(data)
    services = services_factory()
    outcome = acquire_demo(services, key=key, reader=FixtureSourceReader(INPUTS, overrides=data))
    return load_snapshot(services.repositories, outcome.snapshot_id)


def test_a46_authority_labels_no_fabricated_rates_and_missing_due(demo_snapshot, services_factory) -> None:
    econ = {e.line: e for e in demo_snapshot.economics}
    assert {econ[line].authority for line in (1301, 1302, 1303, 1304, 1305)} == {"ASSUMED"}
    assert all(any(r.startswith("ASSUMED_PLANNING_TERM") for r in econ[line].reasons) for line in (1301, 1305))
    assert econ[1301].daily_rate_cents == 10000 and econ[1302].daily_rate_cents == 1000
    assert econ[1306].status == "UNKNOWN" and econ[1306].daily_rate_cents is None      # no fabricated 100k/day
    assert all(e.cap_cents is None for e in econ.values())                              # CapFraction field is never a silent cap

    def mutate(d):
        for t in d["econ_term"]["rows"]:
            if t.get("LineNumber") == 1301:
                t["Provenance"] = "ACTUAL"                                              # actual without approval or sale basis
            if t.get("LineNumber") == 1302:
                t["DailyRate"] = "0.0000"                                               # a supported true zero
        d["cs_windows"]["rows"] = [r for r in d["cs_windows"]["rows"] if r["LINE_NUMBER"] != 1303]   # no due proxy fact

    snap = _snapshot(services_factory, "a46", mutate)
    econ = {e.line: e for e in snap.economics}
    assert econ[1301].authority == "UNKNOWN" and any(r.startswith("TERM_AUTHORITY_UNPROVEN") for r in econ[1301].reasons)
    assert econ[1302].status == "KNOWN" and econ[1302].daily_rate_cents == 0
    assert econ[1303].status == "UNKNOWN" and econ[1303].due_at is None
    assert any(r.startswith("DUE_UNKNOWN") for r in econ[1303].reasons)               # no missing-policy due invented


def test_a46_ambiguous_assignment_or_sale_basis_is_never_resolved_by_row_order(services_factory) -> None:
    def mutate(d):
        k1 = next(r for r in d["econ_contract_assignment"]["rows"] if r["LineNumber"] == 1303)
        d["econ_contract_assignment"]["rows"].append({**k1, "AssignmentId": "6a1f0c64-0000-5000-8000-000000000001", "ContractKey": "DEMO-K2"})
        term = next(t for t in d["econ_term"]["rows"] if t.get("LineNumber") == 1301)
        term["Provenance"], term["ApprovedBy"] = "ACTUAL", "fixture-contracts-office"
        basis = {"OriginalBasisId": "6a1f0c64-0000-5000-8000-000000000002", "LineNumber": 1301, "ContractKey": "DEMO-K9",
                 "ScenarioRevisionId": term["ScenarioRevisionId"], "AssignmentId": "6a1f0c64-0000-5000-8000-000000000003",
                 "OriginalSalePrice": "1000000.0000", "Currency": "USD", "Provenance": "ACTUAL", "TermId": term["TermId"],
                 "SourceReference": "FIXTURE-BASIS", "CreatedAtUtc": "2026-10-20T12:00:00.000"}
        d["econ_original_sale_basis"]["rows"] = [basis, {**basis, "OriginalBasisId": "6a1f0c64-0000-5000-8000-000000000004"}]
        d["econ_original_sale_basis"]["receipt"]["certified_empty"] = False

    econ = {e.line: e for e in _snapshot(services_factory, "a46-ambiguous", mutate).economics}
    assert econ[1303].status == "UNKNOWN" and econ[1303].term_id is None                  # two contracts: no term picked by row order
    assert any(r.startswith("CONTRACT_ASSIGNMENT_CONFLICT: 2") for r in econ[1303].reasons)
    assert econ[1301].authority == "UNKNOWN" and econ[1301].original_sale_cents is None   # two bases: no actual liability claimed
    assert any(r.startswith("SALE_BASIS_CONFLICT: 2") for r in econ[1301].reasons)
    assert econ[1304].status == "KNOWN" and econ[1304].daily_rate_cents == 2500            # unaffected aircraft keep their term


def test_a23_economic_due_is_the_explicit_station_proxy_not_the_gate_or_earliest(demo_snapshot, policy) -> None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(policy.str("shift.timezone"))
    econ = {e.line: e for e in demo_snapshot.economics}
    facts = {(f.line, f.station): f for f in demo_snapshot.cs_facts}
    f = facts[(1301, policy.str("due.proxy_station"))]
    assert f.status == "ADMITTED" and f.due_proxy == f.gate_date + timedelta(days=2)                 # baseline proxy adds grace once
    end_of_proxy_day = datetime.combine(f.due_proxy + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(UTC)
    assert econ[1301].due_at == end_of_proxy_day                                                     # END_OF_LOCAL_DATE instant
    assert econ[1301].due_at != f.hard_latest_at and econ[1301].due_at != f.earliest_at              # due, gate and earliest stay distinct
    assert econ[1301].due_basis.startswith(f"CS{f.station} planning proxy {f.due_proxy.isoformat()}")
