"""Economics rows from a rate file: one daily rate per customer and minor model (docs/data/08-economics.md).

Before contract terms exist, a rate file is the plain source of late-delivery rates: a planning assumption per customer
and minor model. This module turns it, with the list of aircraft, into the rows FOCUS reads (``econ_scenario``,
``econ_mapping_revision``, ``econ_aircraft_mapping``, ``econ_term``) and reports what it could not price.

**It never fills a gap.** An aircraft whose customer and minor model have no rate gets its mapping row and no term, so
FOCUS reports it as ``NO_EFFECTIVE_TERM`` and its cost stays unknown. No average, median or neighbouring rate is used,
here or anywhere else (DECISION_PLAYBOOK.md, guardrail "Unknown stays unknown"). A rate given twice with two values is
a conflict: neither is used.

Every row is marked ``ASSUMED``: a modeled planning cost, never a contract liability. Ids are derived from the content,
so the same files give the same rows.

**A rate file with its own layout.** The column names can be given (``customer_column``, ``model_column``,
``rate_column``), a minor model may carry its family first (``model_prefix="787-"`` reads ``787-9`` as 9), and a file
keyed by a customer's name instead of its code is joined through an explicit list of ``CustomerCode, RateCustomer``
pairs (``customer_codes``). Names are matched exactly as written: no case folding, no partial match. A code that
the list gives to two names is not used. The owner's rate table is such a file (DECISIONS.md, D-31).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..codec import content_hash

NAMESPACE = uuid.UUID("6f1d2a2e-8c54-5b0e-9d2f-4a7c1b9e0f31")
RATE_COLUMNS = ("CustomerCode", "MinorModel", "DailyRate")
AIRCRAFT_COLUMNS = ("LineNumber", "CustomerCode", "MinorModel")


@dataclass(frozen=True)
class RateFile:
    rates: dict[tuple[str, int], tuple[Decimal, str]]          # (customer, minor model) -> (daily rate, currency)
    aircraft: dict[int, tuple[str, int]]                        # line -> (customer, minor model)
    problems: tuple[str, ...]                                   # rows that were not used, each with its reason
    rate_customers_without_code: tuple[str, ...] = ()           # with a customer list: rate customers no code points to


def _text(row: Mapping[str, Any], column: str) -> str:
    value = row.get(column)
    return "" if value is None else str(value).strip()


def _minor_model(text: str, prefix: str = "") -> int:
    """``9``, or ``<prefix>9`` when the file writes the family first. Anything else is not a minor model."""
    if prefix and text.startswith(prefix):
        text = text[len(prefix):]
    if not (text.isascii() and text.isdigit()):
        raise ValueError(text)
    return int(text)


def read_customer_codes(rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """``CustomerCode, RateCustomer`` rows -> (rate customer -> its codes, problems). One name may have several codes.
    A code given to two different names is a conflict: it is left out, so its aircraft stay unpriced."""
    problems: list[str] = []
    names: dict[str, set[str]] = {}
    for n, row in enumerate(rows, start=1):
        code, name = _text(row, "CustomerCode"), _text(row, "RateCustomer")
        if not code or not name:
            problems.append(f"CUSTOMER_ROW_INVALID: customer row {n} ({code or '?'} / {name or '?'}) needs a CustomerCode and a RateCustomer")
            continue
        names.setdefault(code, set()).add(name)
    out: dict[str, list[str]] = {}
    for code, given in sorted(names.items()):
        if len(given) > 1:
            problems.append(f"CUSTOMER_CONFLICT: {code} is given {len(given)} different rate customers; it is left out")
        else:
            out.setdefault(next(iter(given)), []).append(code)
    return {name: tuple(codes) for name, codes in out.items()}, tuple(problems)


def read(rate_rows: Iterable[Mapping[str, Any]], aircraft_rows: Iterable[Mapping[str, Any]], *, currency: str = "USD",
         customer_column: str = "CustomerCode", model_column: str = "MinorModel", rate_column: str = "DailyRate", model_prefix: str = "",
         customer_codes: Mapping[str, Iterable[str]] | None = None) -> RateFile:
    """Validate both tables. A row that cannot be read exactly is left out and reported; nothing is guessed.

    With ``customer_codes`` the rate file's customer is a name: each rate is filed under the codes listed for exactly
    that name, and a rate whose name has no code is set aside (``rate_customers_without_code``), not an error."""
    problems: list[str] = []
    seen: dict[tuple[str, int], set[tuple[Decimal, str]]] = {}
    unmapped: set[str] = set()
    for n, row in enumerate(rate_rows, start=1):
        code, model, rate = _text(row, customer_column), _text(row, model_column), _text(row, rate_column)
        cur = _text(row, "Currency") or currency
        try:
            minor, value = _minor_model(model, model_prefix), Decimal(rate)
        except (ValueError, InvalidOperation):
            problems.append(f"RATE_ROW_UNREADABLE: rate row {n} ({code or '?'} / {model or '?'} / {rate or '?'})")
            continue
        if not code or not value.is_finite() or value < 0:
            problems.append(f"RATE_ROW_INVALID: rate row {n} ({code or '?'} / {model}): the customer is empty or the rate is negative")
            continue
        codes = (code,) if customer_codes is None else tuple(customer_codes.get(code, ()))
        if not codes:
            unmapped.add(code)
        for customer in codes:
            seen.setdefault((customer, minor), set()).add((value, cur))
    rates: dict[tuple[str, int], tuple[Decimal, str]] = {}
    for key, values in sorted(seen.items()):
        if len(values) > 1:
            problems.append(f"RATE_CONFLICT: {key[0]} / {key[1]} has {len(values)} different rates; none is used")
        else:
            rates[key] = next(iter(values))
    lines: dict[int, set[tuple[str, int]]] = {}
    for n, row in enumerate(aircraft_rows, start=1):
        line, code, model = _text(row, "LineNumber"), _text(row, "CustomerCode"), _text(row, "MinorModel")
        try:
            minor = _minor_model(model, model_prefix)
            lines.setdefault(int(line), set()).add((code, minor))
        except ValueError:
            problems.append(f"AIRCRAFT_ROW_UNREADABLE: aircraft row {n} ({line or '?'} / {code or '?'} / {model or '?'})")
            continue
        if not code:
            lines[int(line)].discard((code, minor))
            problems.append(f"AIRCRAFT_ROW_INVALID: aircraft row {n} (line {line}) has no customer")
    aircraft: dict[int, tuple[str, int]] = {}
    for line_no, values in sorted(lines.items()):
        if len(values) > 1:
            problems.append(f"AIRCRAFT_CONFLICT: line {line_no} is given {len(values)} different customers or minor models; it is left out")
        elif values:
            aircraft[line_no] = next(iter(values))
    return RateFile(rates, aircraft, tuple(problems), tuple(sorted(unmapped)))


def coverage(data: RateFile) -> dict[str, Any]:
    """Which aircraft the file prices, and which it does not. The aircraft without a rate are listed, never filled."""
    missing = [{"line": line, "customer": code, "minor_model": model} for line, (code, model) in sorted(data.aircraft.items())
               if (code, model) not in data.rates]
    used = {key for key in data.aircraft.values()}
    return {"aircraft": len(data.aircraft), "priced": len(data.aircraft) - len(missing), "aircraft_without_rate": missing,
            "rates": len(data.rates),
            "rates_without_aircraft": [{"customer": c, "minor_model": m} for c, m in sorted(data.rates) if (c, m) not in used],
            "rate_customers_without_code": list(data.rate_customers_without_code), "problems": list(data.problems)}


def build_rows(data: RateFile, *, scenario_key: str, methodology: str, owner: str, source_reference: str, effective_from_utc: str,
               created_at_utc: str, assembly_location: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """The four tables' rows. ``effective_from_utc`` and ``created_at_utc`` are UTC, ``YYYY-MM-DDTHH:MM:SS.000``.

    Every aircraft in the file gets a mapping row. A term is written for each rate in the file, whether or not an
    aircraft uses it today. An aircraft without a rate therefore has a mapping and no term: ``NO_EFFECTIVE_TERM``.
    """
    def uid(*parts: Any) -> str:
        return str(uuid.uuid5(NAMESPACE, "|".join(str(p) for p in (scenario_key, *parts))))

    content = content_hash({"rates": [[c, m, str(r), cur] for (c, m), (r, cur) in sorted(data.rates.items())],
                            "aircraft": [[line, c, m] for line, (c, m) in sorted(data.aircraft.items())]})
    mapping_id, scenario_id = uid("mapping", content), uid("scenario", content)
    currencies = sorted({cur for _, cur in data.rates.values()}) or ["USD"]
    cov = coverage(data)
    rows: dict[str, list[dict[str, Any]]] = {
        "econ_mapping_revision": [{
            "MappingRevisionId": mapping_id, "SourceName": source_reference, "SourcePredicate": "rate file: customer and minor model per line",
            "QuerySha256": "0" * 64, "ContentSha256": content, "RowCount": len(data.aircraft), "ImportedAtUtc": created_at_utc,
            "CoverageJson": json.dumps({"aircraft": cov["aircraft"], "priced": cov["priced"],
                                        "without_rate": [a["line"] for a in cov["aircraft_without_rate"]]}, sort_keys=True),
            "IsCurrent": True}],
        "econ_scenario": [{
            "ScenarioRevisionId": scenario_id, "ScenarioKey": scenario_key, "ScenarioRevision": 1, "Provenance": "ASSUMED",
            "Methodology": methodology, "OwnerSubject": owner, "EffectiveFromUtc": effective_from_utc, "EffectiveToUtc": None,
            "Currency": currencies[0], "CapFraction": "0.0000", "PriorityWeight": "1.00000", "CreatedAtUtc": created_at_utc, "IsActive": True}],
        "econ_aircraft_mapping": [{
            "MappingRevisionId": mapping_id, "LineNumber": line, "CustomerCode": code, "MinorModel": model, "SourceStart": None,
            "SourceEnd": None, "RevisionDate": created_at_utc[:10], "ShopCompleteDate": None, "AssemblyLocation": assembly_location,
            "SourceContentSha256": content, "ImportedAtUtc": created_at_utc} for line, (code, model) in sorted(data.aircraft.items())],
        "econ_term": [{
            "TermId": uid("term", code, model), "ScenarioRevisionId": scenario_id, "SelectorType": "CUSTOMER_MODEL", "SelectorLength": len(code),
            "CustomerCode": code, "MinorModel": model, "LineNumber": None, "ContractKey": None, "OriginalSalePrice": "0.0000",
            "DailyRate": f"{rate:.4f}", "Currency": cur, "EffectiveFromUtc": effective_from_utc, "EffectiveToUtc": None,
            "Provenance": "ASSUMED", "Methodology": methodology, "SourceReference": source_reference, "OwnerSubject": owner,
            "ApprovedBy": None, "TermVersion": 1, "CreatedAtUtc": created_at_utc} for (code, model), (rate, cur) in sorted(data.rates.items())],
    }
    return rows
