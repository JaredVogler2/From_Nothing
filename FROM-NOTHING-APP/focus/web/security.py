"""Trusted identity, server-owned grants, scope-before-fetch, CSRF and response hardening (S17, D-14).

Identity providers:

* ``fixture``: the demo login picks an invented persona. It is visibly labeled
  DEMO and refused in live mode (``Settings`` enforces this).
* ``posit_connect``: Posit Connect authenticates the viewer (browser session or
  Connect API key) and passes ``RStudio-Connect-Credentials`` as
  ``{"user": ..., "groups": [...]}``. The application trusts this header only
  when the platform is Posit Connect.
* ``trusted_header``: a Tanzu SSO route service / oauth2-proxy sets the user
  header named by ``FOCUS_TRUSTED_USER_HEADER``.
* ``none``: unauthenticated. Only health and the landing page are served.

Grants always come from server configuration (``FOCUS_GRANTS_JSON``), never
from a client claim. A role name in a header is not a mechanic identity.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from flask import Flask, g, request, session

from ..config import GRANTS, Settings

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class ApiError(Exception):
    """An error rendered as ``{"error": {code, message, request_id}}``."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Scope:
    """Data scope applied *before* fetching (S17 scope-before-fetch)."""

    all_teams: bool
    teams: frozenset[str]
    person_native: str | None
    label: str

    def allows_team(self, team: str | None) -> bool:
        return self.all_teams or (team is not None and team in self.teams)

    def describe(self) -> dict[str, Any]:
        return {
            "all_teams": self.all_teams,
            "teams": sorted(self.teams),
            "person": self.person_native,
            "label": self.label,
        }


NO_SCOPE = Scope(all_teams=False, teams=frozenset(), person_native=None, label="none")


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    subject: str | None
    display_name: str | None
    grants: frozenset[str]
    scope: Scope
    provider: str
    demo: bool
    persona: str | None = None
    groups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def authenticated(self) -> bool:
        return self.subject is not None

    def has_any(self, grants: Iterable[str]) -> bool:
        wanted = set(grants)
        return not wanted or bool(self.grants & wanted)


# ---------------------------------------------------------------------------
# demo personas (fixture mode only; invented people, never real identities)
# ---------------------------------------------------------------------------

DEMO_PERSONAS: dict[str, dict[str, Any]] = {
    "manager": {"display": "Demo Manager", "grants": ("reader", "manager", "scenario_planner"), "teams": "*", "person": None},
    "lead-prod-a": {"display": "Demo Team Lead PROD-A", "grants": ("reader", "team_lead"), "teams": ("PROD-A",), "person": None},
    "lead-prod-b": {"display": "Demo Team Lead PROD-B", "grants": ("reader", "team_lead"), "teams": ("PROD-B",), "person": None},
    "mechanic-a05": {"display": "Demo Mechanic A1-05", "grants": ("reader", "mechanic"), "teams": ("PROD-A",), "person": "DEMO-A1-05"},
    "ie": {"display": "Demo Industrial Engineer", "grants": ("reader", "IE"), "teams": "*", "person": None},
    "supply": {"display": "Demo Supply Chain", "grants": ("reader", "supply_chain"), "teams": "*", "person": None},
    "quality": {"display": "Demo Quality Lead", "grants": ("reader", "QA"), "teams": "*", "person": None},
    "excusal": {"display": "Demo Excusal Decider", "grants": ("reader", "excusal_decider", "manager"), "teams": "*", "person": None},
    "operator": {"display": "Demo Pipeline Operator", "grants": ("reader", "manager", "source_worker", "signer", "scenario_planner"), "teams": "*", "person": None},
}


def _scope_from(teams: Any, person: str | None, label: str) -> Scope:
    if teams == "*":
        return Scope(True, frozenset(), person, label)
    return Scope(False, frozenset(teams or ()), person, label)


def _grants_for(settings: Settings, subject: str, groups: Iterable[str]) -> frozenset[str]:
    config = settings.grants
    out: set[str] = set(config.get("default", ()))
    out.update(config.get("subjects", {}).get(subject, ()))
    for group in groups:
        out.update(config.get("groups", {}).get(group, ()))
    return frozenset(g for g in out if g in GRANTS)


def _scope_for_live(settings: Settings, subject: str, grants: frozenset[str]) -> Scope:
    broad = {"manager", "IE", "supply_chain", "QA", "excusal_decider", "source_worker", "signer", "installer", "scenario_planner"}
    if grants & broad:
        return Scope(True, frozenset(), None, "all teams")
    # Team leads / mechanics need explicit server-owned team or person scope; without
    # it they see fleet-level aggregates only (no personal or team detail).
    return Scope(False, frozenset(), None, "aggregates only (no team/person scope configured)")


# ---------------------------------------------------------------------------
# identity resolution
# ---------------------------------------------------------------------------

def _request_id() -> str:
    incoming = request.headers.get("X-Request-ID", "")
    return incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex


def resolve_identity(settings: Settings) -> RequestContext:
    rid = _request_id()
    provider = settings.identity_provider
    if provider == "fixture":
        persona_id = session.get("persona")
        persona = DEMO_PERSONAS.get(persona_id) if isinstance(persona_id, str) else None
        if persona is None:
            return RequestContext(rid, None, None, frozenset(), NO_SCOPE, provider, True)
        return RequestContext(
            request_id=rid,
            subject=f"demo:{persona_id}",
            display_name=persona["display"],
            grants=frozenset(persona["grants"]),
            scope=_scope_from(persona["teams"], persona["person"], persona["display"]),
            provider=provider,
            demo=True,
            persona=persona_id,
        )
    if provider == "posit_connect":
        raw = request.headers.get("RStudio-Connect-Credentials")
        if not raw:
            return RequestContext(rid, None, None, frozenset(), NO_SCOPE, provider, False)
        try:
            creds = json.loads(raw)
        except ValueError:
            raise ApiError(400, "BAD_IDENTITY_HEADER", "malformed Posit Connect credentials header") from None
        user = creds.get("user") if isinstance(creds, dict) else None
        groups = creds.get("groups") if isinstance(creds, dict) else None
        if not isinstance(user, str) or not user:
            return RequestContext(rid, None, None, frozenset(), NO_SCOPE, provider, False)
        group_list = tuple(str(x) for x in groups) if isinstance(groups, list) else ()
        subject = f"posit:{user}"
        grants = _grants_for(settings, subject, group_list) | _grants_for(settings, user, ())
        return RequestContext(rid, subject, user, grants, _scope_for_live(settings, subject, grants), provider, False, None, group_list)
    if provider == "trusted_header":
        assert settings.trusted_user_header
        user = request.headers.get(settings.trusted_user_header, "").strip()
        if not user:
            return RequestContext(rid, None, None, frozenset(), NO_SCOPE, provider, False)
        group_list: tuple[str, ...] = ()
        if settings.trusted_groups_header:
            group_list = tuple(x.strip() for x in request.headers.get(settings.trusted_groups_header, "").split(",") if x.strip())
        subject = f"sso:{user}"
        grants = _grants_for(settings, subject, group_list) | _grants_for(settings, user, ())
        return RequestContext(rid, subject, user, grants, _scope_for_live(settings, subject, grants), provider, False, None, group_list)
    return RequestContext(rid, None, None, frozenset(), NO_SCOPE, provider, False)


# ---------------------------------------------------------------------------
# CSRF (synchronizer token bound to subject + session nonce + time bucket)
# ---------------------------------------------------------------------------

_BUCKET_SECONDS = 43_200


def _csrf_for(settings: Settings, subject: str, nonce: str, bucket: int) -> str:
    key = hashlib.sha256(b"FOCUS_CSRF_V1\0" + settings.session_secret.reveal().encode("utf-8")).digest()
    mac = hmac.new(key, f"{subject}\0{nonce}\0{bucket}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def _session_nonce() -> str:
    nonce = session.get("csrf_nonce")
    if not isinstance(nonce, str):
        nonce = uuid.uuid4().hex
        session["csrf_nonce"] = nonce
    return nonce


def csrf_token(settings: Settings, ctx: RequestContext) -> str | None:
    if not ctx.authenticated:
        return None
    nonce = _session_nonce() if ctx.provider == "fixture" else "header-identity"
    return _csrf_for(settings, ctx.subject or "", nonce, int(time.time()) // _BUCKET_SECONDS)


def verify_csrf(settings: Settings, ctx: RequestContext, token: str | None) -> bool:
    if not ctx.authenticated or not token:
        return False
    nonce = session.get("csrf_nonce") if ctx.provider == "fixture" else "header-identity"
    if not isinstance(nonce, str):
        return False
    bucket = int(time.time()) // _BUCKET_SECONDS
    for b in (bucket, bucket - 1):
        if hmac.compare_digest(_csrf_for(settings, ctx.subject or "", nonce, b), token):
            return True
    return False


def check_same_origin() -> None:
    """Reject cross-site state-changing requests (Origin / Sec-Fetch-Site)."""
    if request.headers.get("Sec-Fetch-Site", "") == "cross-site":
        raise ApiError(403, "CROSS_SITE_REQUEST", "cross-site requests cannot change state")
    origin = request.headers.get("Origin")
    if origin:
        expected = f"{request.scheme}://{request.host}"
        if origin.rstrip("/") != expected:
            raise ApiError(403, "ORIGIN_MISMATCH", "request origin does not match this application")


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------

def init_app(app: Flask, settings: Settings) -> None:
    @app.before_request
    def _identity() -> None:
        g.focus_ctx = resolve_identity(settings)

    @app.after_request
    def _harden(response):  # type: ignore[no-untyped-def]
        ctx = getattr(g, "focus_ctx", None)
        if ctx is not None:
            response.headers["X-Request-ID"] = ctx.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        frame = "'self'" if settings.platform == "posit_connect" else "'none'"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            f"connect-src 'self'; frame-ancestors {frame}; base-uri 'self'; form-action 'self'",
        )
        if settings.platform != "posit_connect":
            response.headers.setdefault("X-Frame-Options", "DENY")
        if request.path.startswith("/api/") or request.path.startswith("/mcp"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def current_context() -> RequestContext:
    ctx = getattr(g, "focus_ctx", None)
    if ctx is None:  # pragma: no cover - before_request always runs
        raise ApiError(500, "NO_CONTEXT", "request context missing")
    return ctx
