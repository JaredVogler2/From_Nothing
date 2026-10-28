"""Economics repository: one effective term per aircraft from the S04 objects (S12, P12).

Resolution:

* current mapping revision -> aircraft mapping (customer, model);
* one active scenario effective at the cutoff;
* the line's active contract assignment;
* the effective term by selector precedence ``AIRCRAFT_OVERRIDE > CONTRACT >
  CUSTOMER_MODEL``, latest TermVersion.

Rules:

* Money stays exact: a ``decimal(19,4)`` daily rate becomes Fraction cents.
* Unsupported currency is UNSUPPORTED. Missing mapping, term or due is UNKNOWN,
  never zero. A true zero rate is known zero.
* Caps are explicit only. The scenario ``CapFraction`` schema field is never a
  silent 25% cap.
* ASSUMED planning terms are labeled ASSUMED. ACTUAL contractual liability
  needs actual provenance, an approval and an original-sale basis.
* The due date comes from the explicit due policy. The fixture uses the
  same-run CS744 planning proxy; a populated field alone is never contractual
  due.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from ..contracts.planning import AircraftEconomics, CSFacts
from ..data.sourcetime import TimezonePolicy, local_midnight_utc
from ..policy import Policy

UTC = timezone.utc


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def _effective(row: Mapping[str, Any], at: datetime, tzp: TimezonePolicy, dataset: str, start_col: str = "EffectiveFromUtc",
               end_col: str = "EffectiveToUtc") -> bool:
    start = tzp.parse_local(dataset, row.get(start_col)) if row.get(start_col) else None
    end = tzp.parse_local(dataset, row.get(end_col)) if row.get(end_col) else None
    return (start is None or start <= at) and (end is None or at < end)


def cents(amount: Decimal) -> Fraction:
    return Fraction(amount) * 100


def build_economics(*, datasets: Mapping[str, tuple[Mapping[str, Any], ...]], lines: Iterable[int], cs_facts_by_line: Mapping[int, CSFacts | None],
                    policy: Policy, tzp: TimezonePolicy, tz: ZoneInfo, cutoff: datetime) -> dict[int, AircraftEconomics]:
    supported = set(policy.seq("economics.supported_currencies"))
    precedence = list(policy.seq("economics.selector_precedence"))
    cap_policy = policy.str("economics.cap_policy")
    curve = policy.str("economics.penalty_curve")
    due_basis = policy.str("due.basis")
    due_instant = policy.str("due.instant")
    mappings = [r for r in datasets.get("econ_mapping_revision", ()) if r.get("IsCurrent")]
    mapping_id = str(mappings[0]["MappingRevisionId"]) if len(mappings) == 1 else None
    aircraft = {int(r["LineNumber"]): r for r in datasets.get("econ_aircraft_mapping", ()) if mapping_id and str(r["MappingRevisionId"]) == mapping_id}
    scenarios = [r for r in datasets.get("econ_scenario", ()) if r.get("IsActive") and _effective(r, cutoff, tzp, "econ_scenario")]
    scenario = scenarios[0] if len(scenarios) == 1 else None
    scenario_id = str(scenario["ScenarioRevisionId"]) if scenario else None
    assignments: dict[int, list[Mapping[str, Any]]] = {}
    for r in datasets.get("econ_contract_assignment", ()):
        if r.get("IsActive") and str(r.get("ScenarioRevisionId")) == scenario_id and _effective(r, cutoff, tzp, "econ_contract_assignment"):
            assignments.setdefault(int(r["LineNumber"]), []).append(r)
    terms = [r for r in datasets.get("econ_term", ()) if str(r.get("ScenarioRevisionId")) == scenario_id and _effective(r, cutoff, tzp, "econ_term")]
    sale_basis: dict[int, list[Mapping[str, Any]]] = {}
    for r in datasets.get("econ_original_sale_basis", ()):
        sale_basis.setdefault(int(r["LineNumber"]), []).append(r)
    checkpoints: dict[str, Mapping[str, Any]] = {}
    for r in datasets.get("econ_liability_checkpoint", ()):
        prev = checkpoints.get(str(r["OriginalBasisId"]))
        if prev is None or int(r["SequenceNumber"]) > int(prev["SequenceNumber"]):
            checkpoints[str(r["OriginalBasisId"])] = r
    out: dict[int, AircraftEconomics] = {}
    for line in sorted(set(lines)):
        reasons: list[str] = []
        if mapping_id is None:
            reasons.append("NO_CURRENT_MAPPING_REVISION" if not mappings else "MULTIPLE_CURRENT_MAPPING_REVISIONS")
        if scenario is None:
            reasons.append("NO_SINGLE_ACTIVE_SCENARIO" if not scenarios else "MULTIPLE_ACTIVE_SCENARIOS")
        mapping = aircraft.get(line)
        if mapping is None:
            reasons.append("AIRCRAFT_NOT_MAPPED")
        effective = assignments.get(line, [])
        if len(effective) > 1:                    # one assignment per aircraft: ambiguity is never resolved by row order
            reasons.append(f"CONTRACT_ASSIGNMENT_CONFLICT: {len(effective)} effective active assignments")
        contract = effective[0] if len(effective) == 1 else None
        contract_key = str(contract["ContractKey"]) if contract else None
        chosen = None
        selector = None
        if mapping is not None and scenario is not None and len(effective) <= 1:
            for sel in precedence:
                if sel == "AIRCRAFT_OVERRIDE":
                    cands = [t for t in terms if t["SelectorType"] == sel and t.get("LineNumber") is not None and int(t["LineNumber"]) == line]
                elif sel == "CONTRACT":
                    cands = [t for t in terms if t["SelectorType"] == sel and contract_key is not None and t.get("ContractKey") == contract_key]
                else:
                    cands = [t for t in terms if t["SelectorType"] == sel and t.get("LineNumber") is None and t.get("ContractKey") is None
                             and str(t.get("CustomerCode")) == str(mapping["CustomerCode"]) and int(t["MinorModel"]) == int(mapping["MinorModel"])]
                if cands:
                    cands.sort(key=lambda t: int(t["TermVersion"]))
                    if len({int(t["TermVersion"]) for t in cands}) != len(cands):
                        reasons.append(f"TERM_VERSION_CONFLICT:{sel}")
                        break
                    chosen, selector = cands[-1], sel
                    break
            if chosen is None and not any(r.startswith("TERM_VERSION_CONFLICT") for r in reasons):
                reasons.append("NO_EFFECTIVE_TERM")
        due_at = None
        due_label = None
        if due_basis == "CS_PLANNING_PROXY":
            facts = cs_facts_by_line.get(line)
            if facts is None or facts.due_proxy is None:
                reasons.append("DUE_UNKNOWN: no same-run planning proxy date for the due policy")
            else:
                day = facts.due_proxy + timedelta(days=1) if due_instant == "END_OF_LOCAL_DATE" else facts.due_proxy
                due_at = local_midnight_utc(day, tz)
                due_label = f"CS{facts.station} planning proxy {facts.due_proxy.isoformat()} ({policy.optional('due.proxy_date_basis')})"
        else:
            reasons.append(f"DUE_POLICY_UNSUPPORTED:{due_basis}")
        if chosen is None:
            out[line] = AircraftEconomics(line, "UNKNOWN", "UNKNOWN", None, None, None, None, None, None, None, due_at, due_label, curve, tuple(reasons))
            continue
        currency = str(chosen.get("Currency"))
        rate = _dec(chosen.get("DailyRate"))
        if currency not in supported:
            out[line] = AircraftEconomics(line, "UNSUPPORTED", "UNKNOWN", str(chosen["TermId"]), selector, currency, None, None, None, None,
                                          due_at, due_label, curve, tuple(reasons + [f"UNSUPPORTED_CURRENCY:{currency}"]))
            continue
        if rate is None or rate < 0:
            out[line] = AircraftEconomics(line, "UNSUPPORTED", "UNKNOWN", str(chosen["TermId"]), selector, currency, None, None, None, None,
                                          due_at, due_label, curve, tuple(reasons + ["DAILY_RATE_INVALID"]))
            continue
        provenance = str(chosen.get("Provenance") or "")
        bases = [b for b in sale_basis.get(line, []) if str(b.get("TermId")) == str(chosen["TermId"])]   # FK: the basis names its term
        if len(bases) > 1:
            reasons.append(f"SALE_BASIS_CONFLICT: {len(bases)} original sale bases for term {chosen['TermId']}")
        basis_row = bases[0] if len(bases) == 1 else None
        if provenance == "ACTUAL" and chosen.get("ApprovedBy") and basis_row is not None:
            authority = "ACTUAL"
        elif provenance == "ASSUMED":
            authority = "ASSUMED"
            reasons.append("ASSUMED_PLANNING_TERM: modeled cost, not contractual liability")
        else:
            authority = "UNKNOWN"
            reasons.append("TERM_AUTHORITY_UNPROVEN: actual liability needs approval and an original sale basis")
        accrued = None
        if basis_row is not None:
            cp = checkpoints.get(str(basis_row["OriginalBasisId"]))
            if cp is not None and _dec(cp.get("CumulativeAccounted")) is not None:
                accrued = cents(_dec(cp["CumulativeAccounted"]))  # type: ignore[arg-type]
        cap = None
        if cap_policy == "EXPLICIT_ONLY":
            if scenario is not None and _dec(scenario.get("CapFraction")) not in (None, Decimal(0)):
                reasons.append(f"CAP_FIELD_NOT_APPLIED: scenario CapFraction={scenario.get('CapFraction')} needs explicit cap policy")
        status = "KNOWN" if due_at is not None else "UNKNOWN"
        out[line] = AircraftEconomics(
            line=line,
            status=status,
            authority=authority,
            term_id=str(chosen["TermId"]),
            selector_type=selector,
            currency=currency,
            daily_rate_cents=cents(rate),
            cap_cents=cap,
            original_sale_cents=cents(_dec(basis_row["OriginalSalePrice"])) if basis_row is not None and _dec(basis_row.get("OriginalSalePrice")) is not None else None,  # type: ignore[arg-type]
            accrued_floor_cents=accrued,
            due_at=due_at,
            due_basis=due_label,
            curve=curve,
            reasons=tuple(reasons),
        )
    return out
