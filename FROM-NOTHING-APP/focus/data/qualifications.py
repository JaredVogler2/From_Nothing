"""Certification requirements and holdings (S07, P06).

Holdings:

* Dates are parsed strictly in the declared forms ``YYYY/MM/DD``,
  ``YYYY-MM-DD`` and ``YYYYMMDD``. Blank, malformed or out-of-range values
  give UNKNOWN validity, never unlimited authorization.
* An inclusive expiry date converts to the next local midnight (exclusive
  ``valid_to``). The perpetual sentinel is honored only when the policy
  declares it.
* Status follows the declared active/inactive vocabularies. An unknown
  status is UNKNOWN. Revocation events set ``revoked_at`` at their effective
  instant, and the latest event wins.

Requirements per exact (SOI, line):

* positive NEO rows are AND groups; explicitly approved alternatives form OR
  groups;
* an affirmative census receipt with no rows is ``affirmatively_none``;
* no rows and no receipt is ``unknown`` (blocked). Missing rows never mean
  cert-free, and NC/PU/CI never clear requirements.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from ..codec import parse_utc
from ..domain import EvidenceRef, Holding, RequirementBundle, RequirementStatus
from ..policy import Policy
from ..contracts.planning import JobRequirement
from .sourcetime import local_midnight_utc

UTC = timezone.utc
_FORMS = {
    "YYYY/MM/DD": re.compile(r"^(\d{4})/(\d{2})/(\d{2})$"),
    "YYYY-MM-DD": re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),
    "YYYYMMDD": re.compile(r"^(\d{4})(\d{2})(\d{2})$"),
}


class DateParse:
    """Result of strict date parsing: ``value`` or a stable ``problem`` code."""

    __slots__ = ("value", "problem", "form", "sentinel")

    def __init__(self, value: date | None, problem: str | None, form: str | None, sentinel: bool = False):
        self.value = value
        self.problem = problem
        self.form = form
        self.sentinel = sentinel


def parse_cert_date(raw: Any, accepted_forms: Iterable[str], sentinel: str | None) -> DateParse:
    """Strict parse in the declared forms; the sentinel is compared by date value, so every
    approved encoding of it (``9999/12/31``, ``9999-12-31``, ``99991231``) agrees."""
    forms = tuple(accepted_forms)
    if raw is None:
        return DateParse(None, "BLANK", None)
    text = str(raw)
    if text.strip() == "":
        return DateParse(None, "BLANK", None)
    if text != text.strip():
        return DateParse(None, "MALFORMED", None)
    sentinel_day = parse_cert_date(sentinel, forms, None).value if sentinel else None
    for form in forms:
        pattern = _FORMS.get(form)
        if pattern is None:
            continue
        found = pattern.match(text)
        if found:
            y, m, d = (int(x) for x in found.groups())
            try:
                value = date(y, m, d)
            except ValueError:
                return DateParse(None, "OUT_OF_RANGE", form)
            if sentinel_day is not None and value == sentinel_day:
                return DateParse(None, None, form, True)
            return DateParse(value, None, form)
    return DateParse(None, "MALFORMED", None)


@dataclass(frozen=True)
class HoldingsBuild:
    holdings: tuple[Holding, ...]
    problems: tuple[str, ...]


def build_holdings(cert_rows: Iterable[Mapping[str, Any]], events: Iterable[Mapping[str, Any]], person_ids: Mapping[str, str],
                   policy: Policy, tz: ZoneInfo | None, dataset_id: str, run_id: str) -> HoldingsBuild:
    """Holdings with validity in UTC; ``tz`` is the mechanic_certs source timezone (None: every validity is UNKNOWN)."""
    forms = list(policy.seq("qualifications.accepted_date_forms"))
    sentinel = policy.optional("qualifications.perpetual_sentinel")
    active = set(policy.seq("qualifications.active_statuses"))
    inactive = set(policy.seq("qualifications.inactive_statuses"))
    inclusive = policy.bool("qualifications.inclusive_expiry")
    revoked: dict[tuple[str, str], tuple[datetime, str]] = {}
    reinstated: dict[tuple[str, str], datetime] = {}
    for ev in sorted(events, key=lambda e: (str(e["EFFECTIVE_AT_UTC"]), str(e.get("EVIDENCE_REF")))):
        key = (str(ev["BEMS_ID"]), str(ev["CERT_CODE"]))
        when = parse_utc(str(ev["EFFECTIVE_AT_UTC"]))
        if ev["EVENT"] == "REVOKED":
            revoked[key] = (when, str(ev.get("EVIDENCE_REF") or ""))
            reinstated.pop(key, None)
        elif ev["EVENT"] == "REINSTATED":
            reinstated[key] = when
    out: list[Holding] = []
    problems: list[str] = []
    for ordinal, row in enumerate(cert_rows):
        native = str(row["BEMS_ID"])
        person = person_ids.get(native)
        if person is None:
            problems.append(f"HOLDING_PERSON_UNKNOWN:{native}")
            continue
        cert = row.get("CERT_CODE")
        if cert is None or str(cert) == "":
            problems.append(f"HOLDING_CERT_BLANK:{native}")
            continue
        cert = str(cert)
        raw_status = row.get("STATUS")
        status_text = "" if raw_status is None else str(raw_status)
        if status_text in active:
            status = "ACTIVE"
        elif status_text in inactive:
            status = status_text
        else:
            status = "UNKNOWN"
        start = parse_cert_date(row.get("LAST_CERT_DATE"), forms, None)
        expiry = parse_cert_date(row.get("CERT_EXP_DATE"), forms, sentinel)
        if tz is None:
            problems.append(f"HOLDING_SOURCE_TIMEZONE_MISSING:{native}:{cert}")
            start = DateParse(None, "TIMEZONE_MISSING", start.form)
            expiry = DateParse(None, "TIMEZONE_MISSING", expiry.form) if not expiry.sentinel else expiry
        valid_from = local_midnight_utc(start.value, tz) if start.value and tz else None
        validity = "UNKNOWN"
        valid_to = None
        if expiry.sentinel:
            validity = "PERPETUAL"
        elif expiry.value is not None:
            try:
                end_day = expiry.value + timedelta(days=1) if inclusive else expiry.value
                valid_to = local_midnight_utc(end_day, tz)  # type: ignore[arg-type]  # tz is set whenever expiry.value is
                validity = "BOUNDED"
            except (OverflowError, ValueError):
                # no year-10000 arithmetic: without an explicit sentinel policy the expiry stays unknown
                problems.append(f"HOLDING_EXPIRY_UNREPRESENTABLE:{native}:{cert}")
        if start.value is None:
            # an unknown start date cannot prove validity at any instant
            validity = "UNKNOWN"
            valid_to = None
        if valid_from is not None and valid_to is not None and valid_from >= valid_to:
            validity, valid_to = "UNKNOWN", None
            problems.append(f"HOLDING_RANGE_INVALID:{native}:{cert}")
        revoked_at = None
        rev = revoked.get((native, cert))
        if rev is not None and (native, cert) not in reinstated:
            revoked_at = rev[0]
        evidence = (EvidenceRef(dataset_id, f"mechanic_certs#{ordinal}", source_run_id=run_id),)
        out.append(Holding(
            person=person,
            cert=cert,
            status=status,
            validity=validity,
            valid_from=valid_from,
            valid_to=valid_to,
            revoked_at=revoked_at,
            applicability="ALL_JOBS_REQUIRING_CODE",
            raw_status=None if raw_status is None else str(raw_status),
            raw_last_cert_date=None if row.get("LAST_CERT_DATE") is None else str(row.get("LAST_CERT_DATE")),
            raw_expiry=None if row.get("CERT_EXP_DATE") is None else str(row.get("CERT_EXP_DATE")),
            evidence=evidence,
        ))
        if expiry.problem:
            problems.append(f"HOLDING_EXPIRY_{expiry.problem}:{native}:{cert}")
        if start.problem:
            problems.append(f"HOLDING_START_{start.problem}:{native}:{cert}")
    return HoldingsBuild(tuple(out), tuple(problems))


def build_requirements(job_index: Mapping[tuple[int, str], str], rows: Iterable[Mapping[str, Any]],
                       alternatives: Iterable[Mapping[str, Any]], census: Iterable[Mapping[str, Any]],
                       crew_sizes: Mapping[str, int | None], role_for_job: Mapping[str, str],
                       dataset_id: str, run_id: str) -> dict[str, JobRequirement]:
    """Exact (line, SOI) requirement bundles per job."""
    positive: dict[str, list[str]] = {}
    evidence: dict[str, list[EvidenceRef]] = {}
    for ordinal, row in enumerate(rows):
        key = (int(row["LINE_NUMBER"]), str(row["SOI_ID"]))
        job = job_index.get(key)
        if job is None:
            continue
        if str(row.get("REQ_CERT") or "Y") != "Y":
            continue
        code = str(row["CERT_CODE"])
        if code not in positive.setdefault(job, []):
            positive[job].append(code)
        evidence.setdefault(job, []).append(EvidenceRef(dataset_id, f"soi_cert_requirements#{ordinal}", source_run_id=run_id))
    alt_groups: dict[str, dict[str, list[str]]] = {}
    for row in alternatives:
        job = job_index.get((int(row["LINE_NUMBER"]), str(row["SOI_ID"])))
        if job is None:
            continue
        alt_groups.setdefault(job, {}).setdefault(str(row["GROUP_ID"]), []).append(str(row["CERT_CODE"]))
    affirmative: set[str] = set()
    for row in census:
        job = job_index.get((int(row["LINE_NUMBER"]), str(row["SOI_ID"])))
        if job is not None and str(row["STATUS"]) == "AFFIRMATIVELY_NONE":
            affirmative.add(job)
    out: dict[str, JobRequirement] = {}
    for job in job_index.values():
        codes = positive.get(job, [])
        groups: list[tuple[str, ...]] = []
        grouped: set[str] = set()
        for gid, certs in sorted(alt_groups.get(job, {}).items()):
            options = tuple(dict.fromkeys(certs))
            groups.append(options)
            grouped.update(options)
        for code in codes:
            if code not in grouped:
                groups.append((code,))
        crew = crew_sizes.get(job)
        count = crew if isinstance(crew, int) and not isinstance(crew, bool) and crew > 0 else 0   # unavailable, never 1
        role = role_for_job.get(job, "mechanic")
        if groups:
            bundle = RequirementBundle(tuple(groups), role, count, "exact SOI/line")
            out[job] = JobRequirement(job, RequirementStatus.REQUIRED, bundle, "NEO_SOI_CERT_REQUIREMENTS+approved alternatives", tuple(evidence.get(job, ())))
        elif job in affirmative:
            bundle = RequirementBundle((), role, count, "affirmative no-cert census")
            out[job] = JobRequirement(job, RequirementStatus.AFFIRMATIVELY_NONE, bundle, "requirement_census AFFIRMATIVELY_NONE", ())
        else:
            out[job] = JobRequirement(job, RequirementStatus.UNKNOWN, None, "no positive rows and no affirmative census", ())
    return out


def matrix_label_notes(rows: Iterable[Mapping[str, Any]], eligibility: Any, person_ids: Mapping[str, str],
                       job_index: Mapping[tuple[int, str], str], at_minute: int, max_examples: int = 5) -> list[tuple[str, str, str, str]]:
    """Compare the convenience matrix (FOCUS_CERT_ELIGIBILITY) with authoritative decisions.

    ELIGIBLE, PCT_CERTS_HELD and SUBSTITUTION are display projections, never permission. They
    are summarized next to the authoritative decision and change nothing. Notes are bounded:
    one per (labels, decision) with a count and a few examples, so a large matrix cannot flood
    the snapshot. MECHANIC names are not part of the projection.
    """
    counts: dict[tuple[str, str, str, str], int] = {}
    examples: dict[tuple[str, str, str, str], list[str]] = {}
    for row in rows:
        native = str(row.get("BEMS_ID") or "")
        person = person_ids.get(native)
        line = row.get("LINE_NUMBER")
        job = job_index.get((int(line), str(row.get("JOB_SOI")))) if line is not None and row.get("JOB_SOI") else None
        labels = f"ELIGIBLE={row.get('ELIGIBLE')!s} SUBSTITUTION={row.get('SUBSTITUTION')!s}"
        if person is None or job is None:
            key = ("CERT_MATRIX_UNMATCHED", labels, "no admitted person/job", "ignored")
        else:
            decision = eligibility.check(person, None, job, at_minute, at_minute + 1)
            key = ("CERT_MATRIX_PROJECTION_ONLY", labels, decision.status.value, decision.code)
        counts[key] = counts.get(key, 0) + 1
        sample = examples.setdefault(key, [])
        if len(sample) < max_examples:
            sample.append(f"{native or '?'} {row.get('JOB_SOI')}@{line} PCT={row.get('PCT_CERTS_HELD')!s}")
    return [(code, "FOCUS_CERT_ELIGIBILITY",
             f"{counts[(code, labels, status, why)]} row(s) {labels}: authoritative {status} {why}; e.g. {'; '.join(examples[(code, labels, status, why)])}",
             "INFO")
            for code, labels, status, why in sorted(counts)]
