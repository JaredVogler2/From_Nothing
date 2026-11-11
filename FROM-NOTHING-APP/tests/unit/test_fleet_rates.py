"""The daily late-delivery rates (owner decision of 2026-11-11, DECISIONS.md D-31): the owner's airline economic proxy
per customer airline and minor model, read exactly from its table. The owner approved keeping the table here ("they are
derived from public data"). A customer and minor model the table does not cover has no rate, and no aircraft of the
synthetic fleet."""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from focus.data import fullscale_fixture as F

ROOT = Path(__file__).resolve().parents[2]
README = (F.ECONOMICS / "README.md").read_text(encoding="utf-8")
DECISIONS = (ROOT / "docs" / "execution" / "DECISIONS.md").read_text(encoding="utf-8")


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_the_rate_table_holds_the_owners_column_and_nothing_the_owner_set_aside() -> None:
    table = _rows(F.DAILY_RATES)
    assert list(table[0]) == ["operator", "minor_model", "mission_profile", "estimated_flights_per_day", "expected_ticket_sales_usd_per_day",
                              F.RATE_COLUMN, "caveat"]
    assert F.RATE_COLUMN == "daily_airline_economic_proxy_usd"                         # the owner's words
    # every airline and minor model of the owner's table: the fleet is whatever has open work, so any of them may come up
    assert len(table) == 94 and len({(r["operator"], r["minor_model"]) for r in table}) == 94 and len({r["operator"] for r in table}) == 63
    assert {r["customer"] for r in _rows(F.OPEN_ORDERS)} <= {r["operator"] for r in table}       # the synthetic fleet's twelve are among them
    assert {r["minor_model"] for r in table} == {"787-8", "787-9", "787-10"}
    for r in table:
        rate, sales = Decimal(r[F.RATE_COLUMN]), Decimal(r["expected_ticket_sales_usd_per_day"])
        assert 0 < rate < sales and rate == rate.quantize(Decimal("0.01")) and r["caveat"]
    # no liquidated-damages figure and no cap: the owner chose the economic proxy, without a cap
    assert not any("ld_" in column or "cap" in column for column in table[0])
    assert F.daily_rates() == {(r["operator"], int(r["minor_model"].removeprefix("787-"))): Decimal(r[F.RATE_COLUMN]) for r in table}


def test_the_superseded_ticket_revenue_table_no_longer_offers_a_daily_penalty() -> None:
    old = _rows(F.OPEN_ORDERS)
    assert "daily_penalty_usd" not in old[0] and "ticket_revenue_usd_per_day" in old[0]        # nobody can read the old rate by its old name
    assert len(old) == 28 and sum(int(r["open_orders"]) for r in old) == 509
    assert not (F.ECONOMICS / "penalty_proxy.csv").exists()


def test_every_customer_and_minor_model_takes_the_owners_rate_or_none() -> None:
    rates, terms = F.daily_rates(), F.penalty_terms()
    assert len(terms) == 28
    by_name = {(r["customer"], int(r["minor_model"])): F.ICAO[r["customer_code"]] for r in _rows(F.OPEN_ORDERS)}
    for code, customer, model, orders, rate in terms:
        assert by_name[(customer, model)] == code and rate == rates.get((customer, model))     # the table's value, or nothing
    priced = [t for t in terms if t[4] is not None]
    assert len(priced) == 26 and min(t[4] for t in priced) == Decimal("46167.96") and max(t[4] for t in priced) == Decimal("84372.07")
    assert max(priced, key=lambda t: t[4])[:3] == ("BAW", "British Airways", 10)
    # the two the table does not cover: listed, never filled in, and named in the README so the gap stays visible
    assert F.unpriced_pairs() == [("QTR", "Qatar Airways", 10, 75), ("THY", "Turkish Airlines", 10, 15)]
    for _, customer, model, orders in F.unpriced_pairs():
        assert f"{customer} 787-{model}" in README and str(orders) in README
    assert F.RATE_COLUMN in README and "D-31" in README and "D-31" in DECISIONS


def test_the_synthetic_fleet_is_fully_priced_at_the_owners_rates() -> None:
    terms = F.penalty_terms()
    unpriced = {(code, model) for code, _, model, _ in F.unpriced_pairs()}
    for n in (2, 5, 10, 50):
        mix = F.fleet_mix(n, terms)
        assert len(mix) == n and all(rate is not None for *_, rate in mix) and not {(m[0], m[2]) for m in mix} & unpriced
    spec = F.FleetSpec(n_aircraft=10, baseline=60, edges=150, rework_total=12, teams=4, team_size=(4, 6))
    data = F.build_fullscale_fixture(spec)
    mapping = {r["LineNumber"]: (r["CustomerCode"], r["MinorModel"]) for r in data["econ_aircraft_mapping"].rows}
    term = {(r["CustomerCode"], r["MinorModel"]): r for r in data["econ_term"].rows}
    rate_of = {(code, model): rate for code, _, model, _, rate in terms}
    assert len(mapping) == 10 and set(mapping.values()) == set(term)                   # a term for every aircraft, and no other
    for key, row in term.items():
        assert row["DailyRate"] == f"{rate_of[key]:.4f}" and row["Provenance"] == "ASSUMED" and row["Currency"] == "USD"
        assert row["Methodology"].startswith("Airline economic proxy") and (ROOT / row["SourceReference"]) == F.DAILY_RATES
    scenario = data["econ_scenario"].rows[0]
    assert scenario["CapFraction"] == "0.0000" and scenario["Provenance"] == "ASSUMED"  # no cap
    # a caller's own rates still work, whole dollars included
    own = F.fleet_mix(3, [("AAA", "Alpha", 9, 2, 100000), ("BBB", "Beta", 10, 1, Decimal("50000.50")), ("CCC", "Gamma", 8, 5, None)])
    assert sorted((code, rate) for code, _, _, rate in own) == [("AAA", 100000), ("AAA", 100000), ("BBB", Decimal("50000.50"))]
