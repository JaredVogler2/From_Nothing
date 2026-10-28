"""Strict validation of provider explanations (S16 PIPELINE step 5, P21).

A provider's output is untrusted data. It is used only when every check passes; otherwise the pipeline returns the
complete deterministic explanation with unchanged candidate order.

**Schema** (``parse_response``):

* exact keys at every level, no extra fields, duplicate keys rejected;
* bounded strings and arrays;
* values are integers, strings or null; floats and non-finite numbers are rejected, because every unit is exact.

**Grounding** (``validate``):

* *Candidates.* Exactly one item per requested candidate; unknown, duplicate and missing ids are rejected.
* *Claims.* Each claim cites an evidence id of its own candidate. Its metric, entity, unit and value must equal
  that fact row. A tolerance applies only where an explicit policy permits it, and never to money.
* *Narrative.* Every word is checked, not just the structured ids:
  * ids, labels and values must come from the candidate's own facts;
  * a number or id-like token that is not grounded is rejected;
  * any other word must belong to a small approved explanatory vocabulary;
  * markup, links and control characters are rejected.

  An injected instruction therefore cannot survive even when the structured claims are right.
* *Uncertainty* may never be lower than the floor the facts imply.
* *Reason codes* must be codes present in the facts.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, NamedTuple

from ..codec import CodecError, strict_loads
from .provider import EXPLAIN_VERSION, METRICS, RAW_MAX_BYTES, UNITS, Claim, ExplainItem, ExplainRequest, ExplainResponse, FactRow

SUMMARY_MAX = 600
CLAIMS_MAX = 32
REASON_CODES_MAX = 8
ITEMS_MAX = 20
ID_MAX = 160
UNCERTAINTY = ("LOW", "MEDIUM", "HIGH")
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
FORBIDDEN = re.compile(r"https?://|www\.|[<>`{}\[\]\\|]")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
NEVER_TOLERANT = frozenset({"USD_cents", "rank", "people", "code", "state", "outcome", "label", "shift", "utc", "basis"})

# Approved explanatory words. Deliberately small: no imperative or agent vocabulary (ignore, instruction, system,
# tool, call, execute, approve, assign, certify, grant, promote, override, delete, send ...), so prose that does not
# restate the facts is rejected and the deterministic explanation is returned instead.
VOCABULARY = frozenset("""
a about after all also an and are as at be because before blocked but by can candidate cents change changes commitment committed
complete conditional cost crew current currently day days delay dispatchable dispatch duration each end ends estimate estimated
facts fact first fits fit for from has have in is it its job keeps kept known line min minute minutes modeled moves no none
not now of on open or out per people person plan planned pool predecessor predecessors preserves rank ranked ready release
releases requires reserves same shift slot start starts statistic the this to travel uncertain uncertainty unknown uses using
via was when which with work basis crews jobs lines shifts reasons reason status
""".split())


class Violation(NamedTuple):
    code: str
    detail: str


class ResponseError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------- schema

def _keys(obj: Any, expected: set[str], path: str) -> None:
    if not isinstance(obj, dict):
        raise ResponseError("SCHEMA_INVALID", f"{path} must be an object")
    extra, missing = set(obj) - expected, expected - set(obj)
    if extra or missing:
        raise ResponseError("SCHEMA_INVALID", f"{path}: unexpected {sorted(extra)} / missing {sorted(missing)}")


def _text(value: Any, path: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ResponseError("SCHEMA_INVALID", f"{path} must be a string of at most {limit} characters")
    return value


def _value(value: Any, path: str) -> int | str | None:
    if value is None or isinstance(value, str):
        if isinstance(value, str) and len(value) > ID_MAX:
            raise ResponseError("SCHEMA_INVALID", f"{path} is longer than {ID_MAX} characters")
        return value
    if isinstance(value, bool):
        raise ResponseError("SCHEMA_INVALID", f"{path} must be an integer, a string or null")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ResponseError("NONFINITE_OR_FLOAT", f"{path}: units are exact; floats are not accepted")
    raise ResponseError("SCHEMA_INVALID", f"{path} must be an integer, a string or null")


def parse_response(raw: bytes) -> ExplainResponse:
    """Strictly decode a provider response (any failure raises ``ResponseError`` with a stable code)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > RAW_MAX_BYTES:
        raise ResponseError("SCHEMA_INVALID", f"the response must be at most {RAW_MAX_BYTES} bytes of JSON")
    try:
        data = strict_loads(bytes(raw))
    except CodecError as exc:
        code = {"NONFINITE": "NONFINITE_OR_FLOAT", "DUPLICATE_KEY": "DUPLICATE_KEY"}.get(exc.code, "SCHEMA_INVALID")
        raise ResponseError(code, str(exc)) from None
    _keys(data, {"version", "items"}, "$")
    if data["version"] != EXPLAIN_VERSION:
        raise ResponseError("VERSION_MISMATCH", f"version must be {EXPLAIN_VERSION}")
    if not isinstance(data["items"], list) or not 1 <= len(data["items"]) <= ITEMS_MAX:
        raise ResponseError("SCHEMA_INVALID", f"items must be a list of 1..{ITEMS_MAX}")
    items = []
    for i, it in enumerate(data["items"]):
        path = f"$.items[{i}]"
        _keys(it, {"candidate_id", "summary", "claims", "uncertainty", "reason_codes"}, path)
        if not isinstance(it["claims"], list) or len(it["claims"]) > CLAIMS_MAX:
            raise ResponseError("SCHEMA_INVALID", f"{path}.claims must be a list of at most {CLAIMS_MAX}")
        if not isinstance(it["reason_codes"], list) or len(it["reason_codes"]) > REASON_CODES_MAX:
            raise ResponseError("SCHEMA_INVALID", f"{path}.reason_codes must be a list of at most {REASON_CODES_MAX}")
        claims = []
        for j, c in enumerate(it["claims"]):
            cpath = f"{path}.claims[{j}]"
            _keys(c, {"metric_key", "entity_id", "value", "unit", "evidence_id"}, cpath)
            evidence = _text(c["evidence_id"], f"{cpath}.evidence_id", 96)
            if not EVIDENCE_ID.match(evidence):
                raise ResponseError("SCHEMA_INVALID", f"{cpath}.evidence_id is not an evidence id")
            claims.append(Claim(_text(c["metric_key"], f"{cpath}.metric_key", 40), _text(c["entity_id"], f"{cpath}.entity_id", ID_MAX),
                                _value(c["value"], f"{cpath}.value"), _text(c["unit"], f"{cpath}.unit", 20), evidence))
        codes = tuple(_text(r, f"{path}.reason_codes", 64) for r in it["reason_codes"])
        items.append(ExplainItem(_text(it["candidate_id"], f"{path}.candidate_id", ID_MAX), _text(it["summary"], f"{path}.summary", SUMMARY_MAX),
                                 tuple(claims), _text(it["uncertainty"], f"{path}.uncertainty", 10), codes))
    return ExplainResponse(data["version"], tuple(items))


# ---------------------------------------------------------------------- grounding

def money_text(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{part:02d}"


def grounded_phrases(candidate_id: str, facts: Iterable[FactRow]) -> set[str]:
    """Exact phrases a narrative may use for one candidate: its id, entity ids, labels and canonical values."""
    out = {candidate_id}
    for f in facts:
        out.add(f.entity_id)
        if f.label:
            out.add(f.label)
        if f.value is not None:
            out.add(str(f.value))
            if f.unit == "USD_cents" and isinstance(f.value, int):
                out.add(money_text(f.value))
    return {p for p in out if p}


def narrative_violations(summary: str, candidate_id: str, facts: Iterable[FactRow]) -> list[Violation]:
    out: list[Violation] = []
    if any(unicodedata.category(ch)[0] == "C" for ch in summary):
        out.append(Violation("NARRATIVE_CONTROL_CHARACTER", "control or format characters are not allowed"))
    if FORBIDDEN.search(summary):
        out.append(Violation("NARRATIVE_MARKUP", "links, markup and code characters are not allowed"))
    rest = summary
    for phrase in sorted(grounded_phrases(candidate_id, facts), key=len, reverse=True):
        rest = re.sub(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", " ", rest)
    for token in TOKEN.findall(rest):
        word = token.strip(".:/-_")
        if not word:
            continue
        if any(ch.isdigit() for ch in word):
            out.append(Violation("UNGROUNDED_NUMBER_OR_ID", f"{word!r} is not a value or id of this candidate's facts"))
        elif word.lower() not in VOCABULARY:
            out.append(Violation("UNGROUNDED_TERM", f"{word!r} is neither a grounded fact nor an approved explanatory word"))
    return out


def claim_violations(claim: Claim, facts: Mapping[str, FactRow], tolerance: Mapping[str, int]) -> list[Violation]:
    fact = facts.get(claim.evidence_id)
    if fact is None:
        return [Violation("UNKNOWN_EVIDENCE", f"{claim.evidence_id} is not a fact of this candidate")]
    out = []
    if claim.metric_key != fact.metric_key or claim.metric_key not in METRICS:
        out.append(Violation("METRIC_MISMATCH", f"{claim.metric_key} != {fact.metric_key}"))
    if claim.entity_id != fact.entity_id:
        out.append(Violation("ENTITY_MISMATCH", f"{claim.entity_id} != {fact.entity_id}"))
    if claim.unit != fact.unit or claim.unit not in UNITS:
        out.append(Violation("UNIT_MISMATCH", f"{claim.unit} != {fact.unit}"))
    if claim.value != fact.value or type(claim.value) is not type(fact.value):
        tol = tolerance.get(fact.unit, 0) if fact.unit not in NEVER_TOLERANT else 0
        close = isinstance(claim.value, int) and isinstance(fact.value, int) and not isinstance(claim.value, bool) and tol > 0 \
            and abs(claim.value - fact.value) <= tol
        if not close:
            out.append(Violation("VALUE_MISMATCH", f"{claim.metric_key}: {claim.value!r} != {fact.value!r}"))
    return out


def validate(request: ExplainRequest, response: ExplainResponse, *, floors: Mapping[str, str],
             tolerance: Mapping[str, int] | None = None) -> list[Violation]:
    """Every violation of the response against the authorized request (empty: the response may be used)."""
    tolerance = tolerance or {}
    out: list[Violation] = []
    if response.version != EXPLAIN_VERSION:
        out.append(Violation("VERSION_MISMATCH", response.version))
    wanted = list(request.authorized_candidate_ids)
    seen: list[str] = []
    for item in response.items:
        cid = item.candidate_id
        if cid not in wanted:
            out.append(Violation("UNKNOWN_CANDIDATE", f"{cid} is not an authorized candidate of this request"))
            continue
        if cid in seen:
            out.append(Violation("DUPLICATE_CANDIDATE", cid))
            continue
        seen.append(cid)
        facts = {f.evidence_id: f for f in request.facts_for(cid)}
        for claim in item.claims:
            out += claim_violations(claim, facts, tolerance)
        out += narrative_violations(item.summary, cid, facts.values())
        if item.uncertainty not in UNCERTAINTY:
            out.append(Violation("SCHEMA_INVALID", f"uncertainty must be one of {UNCERTAINTY}"))
        elif UNCERTAINTY.index(item.uncertainty) < UNCERTAINTY.index(floors.get(cid, "LOW")):
            out.append(Violation("UNCERTAINTY_TOO_LOW", f"{cid}: {item.uncertainty} is below the {floors.get(cid)} the facts imply"))
        codes = {str(f.value) for f in facts.values() if f.unit in ("code", "state", "outcome") and f.value is not None}
        for code in item.reason_codes:
            if not REASON_CODE.match(code) or code not in codes:
                out.append(Violation("UNGROUNDED_REASON_CODE", f"{code!r} is not a code in this candidate's facts"))
    for cid in wanted:
        if cid not in seen:
            out.append(Violation("MISSING_CANDIDATE", cid))
    return out
