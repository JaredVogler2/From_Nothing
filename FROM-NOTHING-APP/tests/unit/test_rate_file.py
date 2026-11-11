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
