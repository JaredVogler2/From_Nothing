"""Turn a rate file into the economics rows FOCUS reads, and list what it cannot price.

    python tools/build_economics_rows.py --rates rates.csv --aircraft aircraft.csv --out DIR \\
        --scenario-key PLANNING-RATES-2027 --owner "economics owner" --source-reference "rates.csv 2027-01" \\
        --effective-from 2027-01-01T00:00:00.000 [--created-at 2027-01-04T12:00:00.000] [--methodology TEXT]

``rates.csv`` has the columns ``CustomerCode, MinorModel, DailyRate`` and optionally ``Currency`` (default USD).
``aircraft.csv`` has ``LineNumber, CustomerCode, MinorModel``. Extra columns are ignored.

It writes one JSON file per table into DIR (``econ_scenario``, ``econ_mapping_revision``, ``econ_aircraft_mapping``,
``econ_term``; each a list of rows with the columns of docs/data/08-economics.md) and ``coverage.json``, and prints the
coverage. Loading the rows into the staging tables is the company loader's job (docs/integration/HANDOFF.md, section 4).

An aircraft whose customer and minor model have no rate is listed under ``aircraft_without_rate`` and gets no term.
FOCUS then reports it as ``NO_EFFECTIVE_TERM`` and leaves its cost unknown. This tool never fills a rate in. The exit
code is 0 when every aircraft has a rate and no row was refused, and 2 otherwise, so a pipeline can stop on a gap.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.economics.rate_file import build_rows, coverage, read  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rates", type=Path, required=True)
    ap.add_argument("--aircraft", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scenario-key", required=True)
    ap.add_argument("--owner", required=True, help="who answers for these rates (OwnerSubject)")
    ap.add_argument("--source-reference", required=True, help="where the rates came from, as a person would cite it")
    ap.add_argument("--effective-from", required=True, help="UTC, YYYY-MM-DDTHH:MM:SS.000")
    ap.add_argument("--created-at", default=None, help="UTC, YYYY-MM-DDTHH:MM:SS.000 (default: now)")
    ap.add_argument("--methodology", default="Planning rate per customer and minor model: a modeled cost, not a contract liability")
    ap.add_argument("--assembly-location", default=None)
    args = ap.parse_args(argv)
    with args.rates.open(encoding="utf-8-sig", newline="") as fh:
        rate_rows = list(csv.DictReader(fh))
    with args.aircraft.open(encoding="utf-8-sig", newline="") as fh:
        aircraft_rows = list(csv.DictReader(fh))
    data = read(rate_rows, aircraft_rows)
    created = args.created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000")
    rows = build_rows(data, scenario_key=args.scenario_key, methodology=args.methodology, owner=args.owner,
                      source_reference=args.source_reference, effective_from_utc=args.effective_from, created_at_utc=created,
                      assembly_location=args.assembly_location)
    report = coverage(data)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, table in rows.items():
        (args.out / f"{name}.json").write_text(json.dumps(table, indent=1), encoding="utf-8")
    (args.out / "coverage.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({**report, "rows": {name: len(table) for name, table in rows.items()}, "out": str(args.out)}, indent=1))
    return 0 if not report["aircraft_without_rate"] and not report["problems"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
