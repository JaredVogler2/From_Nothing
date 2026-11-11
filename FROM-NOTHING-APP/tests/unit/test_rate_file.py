"""Economics rows from a rate file: every aircraft with a rate is priced exactly, and an aircraft without one is listed
and left unknown. Nothing is filled in (the guardrail "Unknown stays unknown")."""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from fractions import Fraction
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from focus.data.datasets import DATASETS
from focus.economics.rate_file import build_rows, coverage, read
from focus.economics.repository import build_economics

RATES = [{"CustomerCode": "AAA", "MinorModel": "9", "DailyRate": "283000"},
         {"CustomerCode": "AAA", "MinorModel": "10", "DailyRate": "322000.50", "Currency": "USD"},
         {"CustomerCode": "BBB", "MinorModel": "9", "DailyRate": "199000"},
         {"CustomerCode": "CCC", "MinorModel": "8", "DailyRate": "150000"}]                # no aircraft uses it today
AIRCRAFT = [{"LineNumber": "1201", "CustomerCode": "AAA", "MinorModel": "9"}, {"LineNumber": "1202", "CustomerCode": "AAA", "MinorModel": "10"},
            {"LineNumber": "1203", "CustomerCode": "BBB", "MinorModel": "9"}, {"LineNumber": "1204", "CustomerCode": "BBB", "MinorModel": "10"},
            {"LineNumber": "1205", "CustomerCode": "DDD", "MinorModel": "9"}]             # 1204 and 1205 have no rate
KW = dict(scenario_key="PLANNING-RATES", methodology="planning rate", owner="economics owner", source_reference="rates.csv",
          effective_from_utc="2026-01-01T00:00:00.000", created_at_utc="2026-10-20T12:00:00.000")


def _economics(rows, policy, lines):
    datasets = {name: tuple(table) for name, table in rows.items()}
    facts = {line: SimpleNamespace(due_proxy=date(2026, 11, 20), station="744") for line in lines}
    return build_economics(datasets=datasets, lines=lines, cs_facts_by_line=facts, policy=policy, tz=ZoneInfo("America/New_York"),
                           cutoff=datetime(2026, 10, 26, 12, tzinfo=timezone.utc))


def test_an_aircraft_with_a_rate_is_priced_exactly_and_one_without_is_left_unknown(policy) -> None:
    data = read(RATES, AIRCRAFT)
    assert data.problems == ()
    report = coverage(data)
    assert (report["aircraft"], report["priced"], report["rates"]) == (5, 3, 4)
    assert report["aircraft_without_rate"] == [{"line": 1204, "customer": "BBB", "minor_model": 10}, {"line": 1205, "customer": "DDD", "minor_model": 9}]
    assert report["rates_without_aircraft"] == [{"customer": "CCC", "minor_model": 8}]
    rows = build_rows(data, **KW)
    econ = _economics(rows, policy, [1201, 1202, 1203, 1204, 1205])
    assert {line: e.status for line, e in econ.items()} == {1201: "KNOWN", 1202: "KNOWN", 1203: "KNOWN", 1204: "UNKNOWN", 1205: "UNKNOWN"}
    assert econ[1201].daily_rate_cents == Fraction(28_300_000) and econ[1202].daily_rate_cents == Fraction(32_200_050)   # to the cent
    assert econ[1203].daily_rate_cents == Fraction(19_900_000) and econ[1201].authority == "ASSUMED"
    for line in (1204, 1205):                                                   # no neighbour's rate, no average, no median
        assert econ[line].daily_rate_cents is None and "NO_EFFECTIVE_TERM" in econ[line].reasons
    assert not any(str(t["DailyRate"]) in ("241000.0000", "234666.8333") for t in rows["econ_term"])        # nothing was derived


def test_the_rows_carry_the_columns_focus_reads_and_are_marked_assumed() -> None:
    rows = build_rows(read(RATES, AIRCRAFT), **KW)
    for name, table in rows.items():
        columns = DATASETS[name].columns
        assert table and all(tuple(row) == tuple(columns) for row in table), name
    assert (len(rows["econ_scenario"]), len(rows["econ_mapping_revision"]), len(rows["econ_aircraft_mapping"]), len(rows["econ_term"])) == (1, 1, 5, 4)
    assert all(t["Provenance"] == "ASSUMED" and t["ApprovedBy"] is None and t["SelectorType"] == "CUSTOMER_MODEL" for t in rows["econ_term"])
    assert rows["econ_scenario"][0]["Provenance"] == "ASSUMED" and rows["econ_scenario"][0]["CapFraction"] == "0.0000"
    assert json.loads(rows["econ_mapping_revision"][0]["CoverageJson"]) == {"aircraft": 5, "priced": 3, "without_rate": [1204, 1205]}
    again = build_rows(read(list(reversed(RATES)), list(reversed(AIRCRAFT))), **KW)
    assert again == rows                                                         # the same files give the same rows and ids
    changed = build_rows(read(RATES[:3], AIRCRAFT), **KW)
    assert changed["econ_scenario"][0]["ScenarioRevisionId"] != rows["econ_scenario"][0]["ScenarioRevisionId"]


def test_rows_that_cannot_be_read_exactly_are_refused_and_reported() -> None:
    rates = RATES + [{"CustomerCode": "AAA", "MinorModel": "9", "DailyRate": "283000.00"},        # the same rate again: fine
                     {"CustomerCode": "BBB", "MinorModel": "9", "DailyRate": "205000"},           # a second, different rate: conflict
                     {"CustomerCode": "EEE", "MinorModel": "nine", "DailyRate": "1"}, {"CustomerCode": "EEE", "MinorModel": "9", "DailyRate": "-5"},
                     {"CustomerCode": "", "MinorModel": "9", "DailyRate": "5"}, {"CustomerCode": "FFF", "MinorModel": "9", "DailyRate": "about 200k"}]
    aircraft = AIRCRAFT + [{"LineNumber": "1201", "CustomerCode": "AAA", "MinorModel": "9"},      # the same aircraft again: fine
                           {"LineNumber": "1202", "CustomerCode": "BBB", "MinorModel": "9"},      # a second customer for 1202: conflict
                           {"LineNumber": "x", "CustomerCode": "AAA", "MinorModel": "9"}, {"LineNumber": "1299", "CustomerCode": "", "MinorModel": "9"}]
    data = read(rates, aircraft)
    codes = sorted(p.split(":")[0] for p in data.problems)
    assert codes == ["AIRCRAFT_CONFLICT", "AIRCRAFT_ROW_INVALID", "AIRCRAFT_ROW_UNREADABLE", "RATE_CONFLICT", "RATE_ROW_INVALID", "RATE_ROW_INVALID",
                     "RATE_ROW_UNREADABLE", "RATE_ROW_UNREADABLE"]
    assert ("BBB", 9) not in data.rates and ("AAA", 9) in data.rates and 1202 not in data.aircraft and 1299 not in data.aircraft
    report = coverage(data)
    assert {a["line"] for a in report["aircraft_without_rate"]} == {1203, 1204, 1205}     # 1203 lost its rate to the conflict: unknown, not guessed


def test_the_tool_writes_the_tables_and_fails_the_run_on_a_gap(tmp_path, app_root) -> None:
    import subprocess
    import sys

    def write(name, rows):
        path = tmp_path / name
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
            writer.writeheader()
            writer.writerows(rows)
        return path

    base = [sys.executable, str(app_root / "tools" / "build_economics_rows.py"), "--scenario-key", "PLANNING-RATES", "--owner", "economics owner",
            "--source-reference", "rates.csv", "--effective-from", "2026-01-01T00:00:00.000", "--created-at", "2026-10-20T12:00:00.000",
            "--methodology", "planning rate"]
    gap = subprocess.run(base + ["--rates", str(write("rates.csv", RATES)), "--aircraft", str(write("aircraft.csv", AIRCRAFT)),
                                 "--out", str(tmp_path / "gap")], capture_output=True, text=True)
    assert gap.returncode == 2, gap.stderr
    printed = json.loads(gap.stdout)
    assert [a["line"] for a in printed["aircraft_without_rate"]] == [1204, 1205] and printed["rows"]["econ_term"] == 4
    assert json.loads((tmp_path / "gap" / "econ_term.json").read_text()) == build_rows(read(RATES, AIRCRAFT), **KW)["econ_term"]
    full = subprocess.run(base + ["--rates", str(write("rates.csv", RATES)), "--aircraft", str(write("aircraft3.csv", AIRCRAFT[:3])),
                                  "--out", str(tmp_path / "full")], capture_output=True, text=True)
    assert full.returncode == 0 and json.loads((tmp_path / "full" / "coverage.json").read_text())["aircraft_without_rate"] == []


# ---------------------------------------------------------------------- a rate file with its own layout (D-31)

OWN_LAYOUT = [{"airline": "Alpha Air", "model": "787-9", "proxy_usd_per_day": "61234.5678912", "another_column": "1"},
              {"airline": "Alpha Air", "model": "787-10", "proxy_usd_per_day": "70500.4", "another_column": "1"},
              {"airline": "Beta Airways", "model": "787-10", "proxy_usd_per_day": "81000.07", "another_column": "1"},
              {"airline": "Gamma Air", "model": "787-8", "proxy_usd_per_day": "40000.36", "another_column": "1"}]
CODES = [{"CustomerCode": "AAA", "RateCustomer": "Alpha Air"}, {"CustomerCode": "AAX", "RateCustomer": "Alpha Air"},   # two codes, one airline
         {"CustomerCode": "BBB", "RateCustomer": "Beta Airways"}]
OWN_KW = dict(customer_column="airline", model_column="model", rate_column="proxy_usd_per_day", model_prefix="787-")


def test_a_rate_file_with_its_own_layout_is_read_by_named_columns_a_model_prefix_and_an_explicit_customer_list(policy) -> None:
    from focus.economics.rate_file import read_customer_codes

    codes, code_problems = read_customer_codes(CODES)
    assert code_problems == () and codes == {"Alpha Air": ("AAA", "AAX"), "Beta Airways": ("BBB",)}
    aircraft = [{"LineNumber": "1201", "CustomerCode": "AAA", "MinorModel": "9"}, {"LineNumber": "1202", "CustomerCode": "AAX", "MinorModel": "787-9"},
                {"LineNumber": "1203", "CustomerCode": "BBB", "MinorModel": "10"}, {"LineNumber": "1204", "CustomerCode": "BBB", "MinorModel": "9"},
                {"LineNumber": "1205", "CustomerCode": "GGG", "MinorModel": "8"}]
    data = read(OWN_LAYOUT, aircraft, customer_codes=codes, **OWN_KW)
    assert data.problems == () and data.rate_customers_without_code == ("Gamma Air",)       # no code names it: set aside, not an error
    assert sorted(data.rates) == [("AAA", 9), ("AAA", 10), ("AAX", 9), ("AAX", 10), ("BBB", 10)]
    report = coverage(data)
    assert report["priced"] == 3 and report["rate_customers_without_code"] == ["Gamma Air"]
    assert report["aircraft_without_rate"] == [{"line": 1204, "customer": "BBB", "minor_model": 9},      # no 787-9 rate for that airline
                                               {"line": 1205, "customer": "GGG", "minor_model": 8}]      # no code-to-name row for GGG
    rows = build_rows(data, **KW)
    econ = _economics(rows, policy, [1201, 1202, 1203, 1204, 1205])
    assert econ[1201].daily_rate_cents == econ[1202].daily_rate_cents == Fraction(612345679, 100)       # four decimals kept, exactly
    assert econ[1203].daily_rate_cents == Fraction(8_100_007) and econ[1204].daily_rate_cents is None and econ[1205].daily_rate_cents is None
    assert {t["DailyRate"] for t in rows["econ_term"]} == {"61234.5679", "70500.4000", "81000.0700"}     # the unused column is never read
    assert rows["econ_scenario"][0]["CapFraction"] == "0.0000"                                           # no cap (owner, D-31)


def test_names_are_matched_exactly_and_what_cannot_be_read_exactly_is_refused() -> None:
    from focus.economics.rate_file import read_customer_codes

    aircraft = [{"LineNumber": "1201", "CustomerCode": "AAA", "MinorModel": "9"}, {"LineNumber": "1202", "CustomerCode": "CCC", "MinorModel": "9"}]
    # the name differs by case: no match, so the aircraft stays unpriced and the rate is set aside
    lower, _ = read_customer_codes([{"CustomerCode": "AAA", "RateCustomer": "alpha air"}])
    data = read(OWN_LAYOUT, aircraft, customer_codes=lower, **OWN_KW)
    assert data.rates == {} and "Alpha Air" in data.rate_customers_without_code and len(coverage(data)["aircraft_without_rate"]) == 2
    # one code given to two airlines, and a row with no name
    codes, problems = read_customer_codes(CODES + [{"CustomerCode": "CCC", "RateCustomer": "Alpha Air"}, {"CustomerCode": "CCC", "RateCustomer": "Beta Airways"},
                                                   {"CustomerCode": "DDD", "RateCustomer": ""}])
    assert sorted(p.split(":")[0] for p in problems) == ["CUSTOMER_CONFLICT", "CUSTOMER_ROW_INVALID"] and "CCC" not in sum(codes.values(), ())
    # the family must be the one given; without the prefix a written-out model is not a number
    other_family = read([{"airline": "Alpha Air", "model": "777-9", "proxy_usd_per_day": "1"}], aircraft, customer_codes=codes, **OWN_KW)
    assert other_family.rates == {} and other_family.problems[0].startswith("RATE_ROW_UNREADABLE")
    no_prefix = read(OWN_LAYOUT, aircraft, customer_codes=codes, **{**OWN_KW, "model_prefix": ""})
    assert no_prefix.rates == {} and len(no_prefix.problems) == len(OWN_LAYOUT)
    assert read([{"CustomerCode": "AAA", "MinorModel": "-9", "DailyRate": "5"}], aircraft).problems[0].startswith("RATE_ROW_UNREADABLE")


def test_the_tool_reads_a_rate_file_with_its_own_layout(tmp_path, app_root) -> None:
    import subprocess
    import sys

    def write(name, rows):
        path = tmp_path / name
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    rates, codes = write("rate_table.csv", OWN_LAYOUT), write("codes.csv", CODES)
    aircraft = [{"LineNumber": "1201", "CustomerCode": "AAA", "MinorModel": "9"}, {"LineNumber": "1202", "CustomerCode": "BBB", "MinorModel": "10"},
                {"LineNumber": "1203", "CustomerCode": "AAX", "MinorModel": "10"}]
    base = [sys.executable, str(app_root / "tools" / "build_economics_rows.py"), "--rates", str(rates), "--customer-column", "airline",
            "--model-column", "model", "--model-prefix", "787-", "--customer-codes", str(codes), "--scenario-key", "AIRLINE-ECONOMIC-PROXY",
            "--owner", "economics owner", "--source-reference", "rate_table.csv", "--effective-from", "2026-01-01T00:00:00.000",
            "--created-at", "2026-11-11T12:00:00.000"]
    column = ["--rate-column", "proxy_usd_per_day"]
    full = subprocess.run(base + column + ["--aircraft", str(write("aircraft.csv", aircraft)), "--out", str(tmp_path / "full")], capture_output=True, text=True)
    assert full.returncode == 0, full.stdout + full.stderr
    terms = {(t["CustomerCode"], t["MinorModel"]): t["DailyRate"] for t in json.loads((tmp_path / "full" / "econ_term.json").read_text())}
    assert terms[("AAA", 9)] == "61234.5679" and terms[("BBB", 10)] == "81000.0700" and terms[("AAX", 10)] == "70500.4000"
    printed = json.loads(full.stdout)
    assert printed["priced"] == 3 and printed["rate_customers_without_code"] == ["Gamma Air"]
    gap = subprocess.run(base + column + ["--aircraft", str(write("aircraft2.csv", aircraft + [{"LineNumber": "1204", "CustomerCode": "BBB", "MinorModel": "9"}])),
                                          "--out", str(tmp_path / "gap")], capture_output=True, text=True)
    assert gap.returncode == 2 and json.loads(gap.stdout)["aircraft_without_rate"] == [{"line": 1204, "customer": "BBB", "minor_model": 9}]
    wrong = subprocess.run(base + ["--rate-column", "daily_rate", "--aircraft", str(tmp_path / "aircraft.csv"), "--out", str(tmp_path / "wrong")],
                           capture_output=True, text=True)
    assert wrong.returncode == 2 and "has no column daily_rate" in wrong.stderr and not (tmp_path / "wrong").exists()


def test_the_tool_reads_the_owners_rate_table_kept_in_the_repository(tmp_path, app_root) -> None:
    """D-31: the documented command, on ``fixtures/economics/airline_economic_proxy.csv`` as it is."""
    import subprocess
    import sys

    def write(name, rows):
        path = tmp_path / name
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    table = app_root / "fixtures" / "economics" / "airline_economic_proxy.csv"
    with table.open(encoding="utf-8", newline="") as fh:
        rates = {(r["operator"], r["minor_model"]): r["daily_airline_economic_proxy_usd"] for r in csv.DictReader(fh)}
    codes = write("codes.csv", [{"CustomerCode": "UAL", "RateCustomer": "United Airlines"}, {"CustomerCode": "BAW", "RateCustomer": "British Airways"},
                                {"CustomerCode": "QTR", "RateCustomer": "Qatar Airways"}])
    aircraft = [{"LineNumber": "1201", "CustomerCode": "UAL", "MinorModel": "9"}, {"LineNumber": "1202", "CustomerCode": "BAW", "MinorModel": "10"},
                {"LineNumber": "1203", "CustomerCode": "QTR", "MinorModel": "9"}]
    base = [sys.executable, str(app_root / "tools" / "build_economics_rows.py"), "--rates", str(table), "--customer-column", "operator",
            "--model-column", "minor_model", "--model-prefix", "787-", "--rate-column", "daily_airline_economic_proxy_usd",
            "--customer-codes", str(codes), "--scenario-key", "AIRLINE-ECONOMIC-PROXY", "--owner", "economics owner",
            "--source-reference", "airline_economic_proxy.csv", "--effective-from", "2026-01-01T00:00:00.000", "--created-at", "2026-11-11T12:00:00.000"]
    full = subprocess.run(base + ["--aircraft", str(write("aircraft.csv", aircraft)), "--out", str(tmp_path / "full")], capture_output=True, text=True)
    assert full.returncode == 0, full.stdout + full.stderr
    terms = {(t["CustomerCode"], t["MinorModel"]): t["DailyRate"] for t in json.loads((tmp_path / "full" / "econ_term.json").read_text())}
    assert terms[("UAL", 9)] == f"{rates[('United Airlines', '787-9')]}00" and terms[("BAW", 10)] == f"{rates[('British Airways', '787-10')]}00"
    assert ("QTR", 10) not in terms                                        # the table has no Qatar 787-10 row, and none is made up
    printed = json.loads(full.stdout)
    assert printed["priced"] == 3 and len(printed["rate_customers_without_code"]) == 60          # 63 airlines, three with a code here
    gap = subprocess.run(base + ["--aircraft", str(write("aircraft2.csv", aircraft + [{"LineNumber": "1204", "CustomerCode": "QTR", "MinorModel": "10"}])),
                                 "--out", str(tmp_path / "gap")], capture_output=True, text=True)
    assert gap.returncode == 2 and json.loads(gap.stdout)["aircraft_without_rate"] == [{"line": 1204, "customer": "QTR", "minor_model": 10}]
