"""Explanation providers (S16 PROVIDER PROTOCOL, P21).

Contracts:

* ``ExplainRequest``: the version, the authorized candidate ids in deterministic rank order, the grounded fact rows,
  the source, plan and policy hashes, the locale and the privacy scope.
* ``ExplainResponse``: the version and one item per candidate. An item holds a summary, claims (metric, entity,
  value, unit, evidence id), an uncertainty and reason codes.

Providers:

* ``mock`` generates deterministic fixture responses from the fact rows. The wording differs from the baseline;
  the grounding is the same. It works offline.
* ``recorded`` replays stored responses keyed by the canonical request hash, so a recording is used only for the
  exact request it was made for. Every replay goes through the same strict validation as any other provider; it
  is never trusted text.
* ``bcai`` is the optional adapter for an approved endpoint:
  * https only, with TLS verification;
  * a protected credential reference (``env:NAME`` or ``vcap:service/field``) that is never logged or stored;
  * a bounded timeout and bounded retries;
  * data minimization: the body carries only the rendered prompt, which holds authorized fact rows and no person
    names, cookies or user tokens.

  It is unavailable unless ``FOCUS_LLM_PROVIDER=bcai`` and both the endpoint and the credential reference are set.

The deterministic baseline lives in ``pipeline`` and is always available. The default operational provider is
``none``. A provider gets no tools and returns bytes, which the pipeline validates or discards.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..codec import canonical_dumps, content_hash

EXPLAIN_VERSION = "focus-explain-v1"
PROMPT_VERSION = "focus-explain-prompt-v1"
RAW_MAX_BYTES = 262_144

METRICS = ("rank", "job", "dispatch_status", "blocked_reason", "ready_after", "crew_size", "duration_minutes", "duration_basis",
           "shift", "start", "end", "commitment", "line_marginal_day_cost", "travel_minutes", "unknown_fact")
UNITS = ("rank", "label", "state", "code", "people", "min", "basis", "shift", "utc", "outcome", "USD_cents")


@dataclass(frozen=True, slots=True)
class FactRow:
    evidence_id: str
    candidate_id: str
    metric_key: str
    entity_id: str
    value: int | str | None              # exact integer or canonical text; None means unknown, never zero
    unit: str
    label: str                           # sanitized display text of the entity (native ids); data only


@dataclass(frozen=True, slots=True)
class ExplainRequest:
    version: str
    authorized_candidate_ids: tuple[str, ...]
    grounded_fact_rows: tuple[FactRow, ...]
    source_hash: str
    plan_hash: str
    policy_hash: str
    locale: str
    privacy_scope: str

    def hash(self) -> str:
        return content_hash(self)

    def facts_for(self, candidate_id: str) -> tuple[FactRow, ...]:
        return tuple(f for f in self.grounded_fact_rows if f.candidate_id == candidate_id)


@dataclass(frozen=True, slots=True)
class Claim:
    metric_key: str
    entity_id: str
    value: int | str | None
    unit: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class ExplainItem:
    candidate_id: str
    summary: str
    claims: tuple[Claim, ...]
    uncertainty: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExplainResponse:
    version: str
    items: tuple[ExplainItem, ...]


def response_bytes(response: ExplainResponse) -> bytes:
    return canonical_dumps(response)


class ProviderUnavailable(Exception):
    """The provider could not answer; the pipeline returns the complete deterministic explanation instead."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------- mock

class MockProvider:
    """Deterministic fixture responses built from the fact rows (offline; same grounding, different wording)."""

    name = "mock"

    def complete(self, request: ExplainRequest, prompt: str) -> bytes:
        from .pipeline import uncertainty_floor

        items = []
        for cid in request.authorized_candidate_ids:
            facts = request.facts_for(cid)
            by = {f.metric_key: f for f in facts}
            parts = []
            if "rank" in by and "job" in by:
                parts.append(f"Rank {by['rank'].value}: job {by['job'].label} is {str(by['dispatch_status'].value).lower()}.")
            if "crew_size" in by:
                parts.append(f"Crew {by['crew_size'].value} people from pool {by['crew_size'].entity_id}.")
            if "duration_minutes" in by:
                parts.append(f"Duration {by['duration_minutes'].value} min.")
            if "shift" in by:
                parts.append(f"Shift {by['shift'].value}.")
            if "travel_minutes" in by:
                parts.append(f"Travel {by['travel_minutes'].value} min.")
            claims = tuple(Claim(f.metric_key, f.entity_id, f.value, f.unit, f.evidence_id) for f in facts if f.value is not None)
            codes = tuple(sorted({str(f.value) for f in facts if f.metric_key in ("blocked_reason", "unknown_fact") and f.value}))[:8]
            items.append(ExplainItem(cid, " ".join(parts), claims, uncertainty_floor(facts), codes))
        return response_bytes(ExplainResponse(EXPLAIN_VERSION, tuple(items)))


# ---------------------------------------------------------------------- recorded

class RecordedProvider:
    """Stored responses keyed by the canonical request hash; each replay is validated like any provider output."""

    name = "recorded"

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path_for(self, request: ExplainRequest) -> Path:
        return self.directory / f"{request.hash()}.json"

    def complete(self, request: ExplainRequest, prompt: str) -> bytes:
        path = self.path_for(request)
        if not path.is_file():
            raise ProviderUnavailable("RECORDING_NOT_FOUND", f"no recorded response for request {request.hash()[:16]}")
        if path.stat().st_size > RAW_MAX_BYTES:
            raise ProviderUnavailable("RECORDING_TOO_LARGE", f"the recording exceeds {RAW_MAX_BYTES} bytes")
        return path.read_bytes()

    def record(self, request: ExplainRequest, raw: bytes) -> Path:
        """Store a response for later replay (fixture tooling; the stored bytes are still validated on every replay)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(request)
        path.write_bytes(raw)
        return path


# ---------------------------------------------------------------------- BCAI (optional, default off)

Transport = Callable[[str, bytes, Mapping[str, str], float], bytes]


def https_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    """POST with TLS verification (system trust store) and a bounded response size."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310 - https enforced by caller
            data = response.read(RAW_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderUnavailable("PROVIDER_HTTP_ERROR", f"the endpoint answered HTTP {exc.code}") from None
    except (urllib.error.URLError, ssl.SSLError, ConnectionError) as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise TimeoutError("endpoint timed out") from None
        raise ProviderUnavailable("PROVIDER_UNREACHABLE", f"the endpoint could not be reached ({type(exc).__name__})") from None
    if len(data) > RAW_MAX_BYTES:
        raise ProviderUnavailable("RESPONSE_TOO_LARGE", f"the response exceeds {RAW_MAX_BYTES} bytes")
    return data


class BCAIProvider:
    name = "bcai"

    def __init__(self, endpoint: str | None, credential_ref: str | None, *, timeout_seconds: float, retries: int,
                 transport: Transport | None = None, environ: Mapping[str, str] | None = None):
        self.endpoint = endpoint
        self.credential_ref = credential_ref
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.transport = transport or https_transport
        self.environ = environ

    def complete(self, request: ExplainRequest, prompt: str) -> bytes:
        from ..publication.signing import secret_from_ref

        if not self.endpoint or not self.credential_ref:
            raise ProviderUnavailable("BCAI_NOT_CONFIGURED", "FOCUS_BCAI_ENDPOINT and FOCUS_BCAI_CREDENTIAL_REF are both required")
        if not self.endpoint.startswith("https://"):
            raise ProviderUnavailable("BCAI_ENDPOINT_NOT_TLS", "the BCAI endpoint must be an https URL")
        credential, supported = secret_from_ref(self.credential_ref, self.environ)
        if not supported:
            raise ProviderUnavailable("BCAI_CREDENTIAL_REF_UNSUPPORTED", "use env:NAME or vcap:service/field")
        if not credential:
            raise ProviderUnavailable("BCAI_CREDENTIAL_MISSING", "the referenced credential is not present")
        body = canonical_dumps({"schema": EXPLAIN_VERSION, "prompt_version": PROMPT_VERSION, "input": prompt})
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {credential}"}
        for _ in range(self.retries + 1):
            try:
                return self.transport(self.endpoint, body, headers, self.timeout_seconds)
            except TimeoutError:
                continue
        raise ProviderUnavailable("PROVIDER_TIMEOUT", f"no answer within {self.timeout_seconds}s after {self.retries + 1} attempt(s)")


def provider_for(services: Any) -> tuple[Any | None, str]:
    """(provider, name) from the server settings; ``None`` means deterministic only (the default ``none``)."""
    settings = services.settings
    name = settings.llm_provider
    if name == "none":
        return None, "none"
    if name in ("mock", "recorded") and settings.mode != "fixture":
        return None, f"{name}:FIXTURE_ONLY"
    if name == "mock":
        return MockProvider(), name
    if name == "recorded":
        return RecordedProvider(settings.app_root / "fixtures" / "explanations" / "recorded"), name
    policy = services.policy
    return BCAIProvider(settings.bcai_endpoint, settings.bcai_credential_ref, timeout_seconds=float(policy.int("explanations.timeout_seconds")),
                        retries=policy.int("explanations.max_retries")), name
