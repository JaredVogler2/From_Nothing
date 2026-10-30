"""Immutable, allowlisted FOCUS_* settings with explicit hosting adapters (S02, D-14).

Only the names in ``ALLOWED_KEYS`` are read from the environment. Unknown
``FOCUS_*`` names are ignored and reported (names only, never values) so a typo
cannot silently change behavior. Platform variables are read by explicit
adapters only:

* VMware Tanzu / Cloud Foundry: ``PORT`` (listen port), ``VCAP_APPLICATION``
  (platform detection) and ``VCAP_SERVICES`` (user-provided SQL credentials and
  secrets services named by ``FOCUS_SQL_SERVICE_NAME`` /
  ``FOCUS_SECRETS_SERVICE_NAME``).
* Posit Connect: ``RSTUDIO_PRODUCT=CONNECT`` / ``CONNECT_CONTENT_GUID``
  (platform detection). Connect serves the WSGI app under ``/content/<guid>/``
  and supplies the trusted ``RStudio-Connect-Credentials`` identity header.

Secrets are wrapped in :class:`Secret` so they never appear in ``repr``, logs,
error envelopes or API responses.
"""
from __future__ import annotations

import json
import os
import re
import secrets as _secrets
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

ALLOWED_KEYS = frozenset({
    "FOCUS_MODE",
    "FOCUS_APP_ROOT",
    "FOCUS_BIND",
    "FOCUS_PORT",
    "FOCUS_SQL_HOST",
    "FOCUS_SQL_DATABASE",
    "FOCUS_SQL_USER",
    "FOCUS_SQL_PASSWORD",
    "FOCUS_SQL_READ_TIMEOUT_SECONDS",
    "FOCUS_SQL_LOGIN_TIMEOUT_SECONDS",
    "FOCUS_SOURCE_TIMEZONE_MAP_JSON",
    "FOCUS_POLICY_ID",
    "FOCUS_SCENARIO_ID",
    "FOCUS_RUNTIME_DIR",
    "FOCUS_SCOPE_KEY",
    "FOCUS_SITE",
    "FOCUS_PROGRAM",
    "FOCUS_WORKER_BUDGET_SECONDS",
    "FOCUS_SOLVER_ENABLED",
    "FOCUS_NATIVE_CORE",
    "FOCUS_LLM_PROVIDER",
    "FOCUS_BCAI_ENDPOINT",
    "FOCUS_BCAI_CREDENTIAL_REF",
    "FOCUS_SESSION_SECRET",
    "FOCUS_SIGN_KEY_ID",
    "FOCUS_SIGN_KEY_REF",
    # hosting / API directives (DECISIONS D-13, D-14)
    "FOCUS_IDENTITY_PROVIDER",
    "FOCUS_TRUSTED_USER_HEADER",
    "FOCUS_TRUSTED_GROUPS_HEADER",
    "FOCUS_GRANTS_JSON",
    "FOCUS_MCP_ENABLED",
    "FOCUS_PROXY_FIX",
    "FOCUS_INPROCESS_WORKER",
    "FOCUS_SQL_SERVICE_NAME",
    "FOCUS_SECRETS_SERVICE_NAME",
    "FOCUS_MAX_CONTENT_LENGTH",
    "FOCUS_SECURE_COOKIES",
    "FOCUS_LOG_LEVEL",
})

PLATFORM_KEYS = frozenset({"PORT", "VCAP_APPLICATION", "VCAP_SERVICES", "RSTUDIO_PRODUCT", "CONNECT_CONTENT_GUID", "CONNECT_SERVER"})

MODES = ("fixture", "live")
LLM_PROVIDERS = ("none", "mock", "recorded", "bcai")
IDENTITY_PROVIDERS = ("fixture", "posit_connect", "trusted_header", "none")
GRANTS = (
    "reader", "team_lead", "mechanic", "IE", "supply_chain", "QA", "excusal_decider",
    "source_worker", "signer", "installer", "scenario_planner", "manager",
)


class SettingsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Secret:
    """Opaque secret holder; never rendered by repr/str."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not isinstance(value, str) or not value:
            raise SettingsError("BAD_SECRET", "secret values must be non-empty strings")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and _secrets.compare_digest(self._value, other._value)

    def __hash__(self) -> int:
        return hash("Secret")


def resolve_app_root(explicit: str | None = None) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            raise SettingsError("APP_ROOT_MISSING", f"FOCUS_APP_ROOT {root} is not a directory")
        return root
    return Path(__file__).resolve().parent.parent


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise SettingsError("BAD_BOOLEAN", f"{name} must be true/false")


def _parse_int(name: str, raw: str | None, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise SettingsError("BAD_INTEGER", f"{name} must be an integer") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise SettingsError("OUT_OF_RANGE", f"{name} out of range")
    return value


def _parse_float(name: str, raw: str | None, default: float, minimum: float = 0.0, maximum: float = 3600.0) -> float:
    if raw is None or raw == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise SettingsError("BAD_NUMBER", f"{name} must be a number") from exc
    if not (minimum <= value <= maximum):
        raise SettingsError("OUT_OF_RANGE", f"{name} must be within [{minimum}, {maximum}]")
    return value


def _parse_json_object(name: str, raw: str | None) -> dict[str, Any]:
    if raw is None or raw.strip() == "":
        return {}
    from .codec import CodecError, strict_loads

    try:
        value = strict_loads(raw)
    except CodecError as exc:
        raise SettingsError("BAD_JSON", f"{name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SettingsError("BAD_JSON", f"{name} must be a JSON object")
    return value


def _choice(name: str, raw: str | None, allowed: tuple[str, ...], default: str) -> str:
    value = (raw or default).strip()
    if value not in allowed:
        raise SettingsError("BAD_CHOICE", f"{name} must be one of {allowed}")
    return value


def _detect_platform(env: Mapping[str, str]) -> str:
    if env.get("VCAP_APPLICATION"):
        return "tanzu"
    if env.get("RSTUDIO_PRODUCT", "").upper() == "CONNECT" or env.get("CONNECT_CONTENT_GUID"):
        return "posit_connect"
    return "local"


def _vcap_credentials(env: Mapping[str, str], service_name: str) -> dict[str, Any] | None:
    raw = env.get("VCAP_SERVICES")
    if not raw:
        return None
    try:
        services = json.loads(raw)
    except ValueError as exc:
        raise SettingsError("BAD_VCAP_SERVICES", "VCAP_SERVICES is not valid JSON") from exc
    if not isinstance(services, dict):
        raise SettingsError("BAD_VCAP_SERVICES", "VCAP_SERVICES must be an object")
    for instances in services.values():
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if isinstance(instance, dict) and (instance.get("name") == service_name or instance.get("instance_name") == service_name):
                creds = instance.get("credentials")
                return creds if isinstance(creds, dict) else None
    return None


_SCOPE_TEAM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
_SCOPE_PERSON = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _normalize_scope(name: str, entry: Any) -> dict[str, Any]:
    """One server-owned data scope: ``{"teams": [...] | "*", "person": "<native id>" | null}``."""
    if not isinstance(entry, dict) or set(entry) - {"teams", "person"}:
        raise SettingsError("BAD_GRANTS", f"FOCUS_GRANTS_JSON.scopes.{name} must be an object with only teams and person")
    teams = entry.get("teams", [])
    if teams != "*":
        if not isinstance(teams, list) or any(not isinstance(t, str) or not _SCOPE_TEAM.match(t) for t in teams):
            raise SettingsError("BAD_GRANTS", f"FOCUS_GRANTS_JSON.scopes.{name}.teams must be \"*\" or a list of team ids")
        teams = tuple(sorted(set(teams)))
    person = entry.get("person")
    if person is not None and (not isinstance(person, str) or not _SCOPE_PERSON.match(person)):
        raise SettingsError("BAD_GRANTS", f"FOCUS_GRANTS_JSON.scopes.{name}.person must be a native person id")
    return {"teams": teams, "person": person}


def _normalize_grants(raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"subjects": {}, "groups": {}, "default": [], "scopes": {}}
    unknown_sections = set(raw) - set(out)
    if unknown_sections:
        raise SettingsError("BAD_GRANTS", f"FOCUS_GRANTS_JSON has unknown sections {sorted(unknown_sections)}")
    for section in ("subjects", "groups"):
        entries = raw.get(section, {})
        if not isinstance(entries, dict):
            raise SettingsError("BAD_GRANTS", f"FOCUS_GRANTS_JSON.{section} must be an object")
        for name, grants in entries.items():
            if not isinstance(grants, list) or any(g not in GRANTS for g in grants):
                raise SettingsError("BAD_GRANTS", f"FOCUS_GRANTS_JSON.{section}.{name} has unknown grants")
            out[section][str(name)] = tuple(sorted(set(grants)))
    default = raw.get("default", [])
    if not isinstance(default, list) or any(g not in GRANTS for g in default):
        raise SettingsError("BAD_GRANTS", "FOCUS_GRANTS_JSON.default must list known grants")
    out["default"] = tuple(sorted(set(default)))
    scopes = raw.get("scopes", {})
    if not isinstance(scopes, dict):
        raise SettingsError("BAD_GRANTS", "FOCUS_GRANTS_JSON.scopes must be an object")
    out["scopes"] = {str(name): _normalize_scope(str(name), entry) for name, entry in scopes.items()}
    return out


@dataclass(frozen=True)
class Settings:
    mode: str = "fixture"
    app_root: Path = field(default_factory=resolve_app_root)
    bind: str = "127.0.0.1"
    port: int = 5055
    sql_host: str = "SQL-D-22D10090"
    sql_database: str = "SHARED"
    sql_user: str | None = None
    sql_password: Secret | None = None
    sql_read_timeout_seconds: int = 60
    sql_login_timeout_seconds: int = 15
    source_timezone_map: Mapping[str, str] = field(default_factory=dict)
    policy_id: str = "rebuild-v1"
    scenario_id: str = "manufacturing_demo"
    runtime_root: Path | None = None
    scope_key: str = "DEMO-787"
    site: str = "DEMO"
    program: str = "787"
    worker_budget_seconds: float = 30.0
    solver_enabled: bool = False
    native_core: bool = False             # optional Rust ledger/crew core (exact parity); off by default
    llm_provider: str = "none"
    bcai_endpoint: str | None = None
    bcai_credential_ref: str | None = None
    session_secret: Secret = field(default_factory=lambda: Secret(_secrets.token_hex(32)))
    session_secret_ephemeral: bool = True
    sign_key_id: str | None = None
    sign_key_ref: str | None = None
    identity_provider: str = "fixture"
    trusted_user_header: str | None = None
    trusted_groups_header: str | None = None
    grants: Mapping[str, Any] = field(default_factory=lambda: {"subjects": {}, "groups": {}, "default": (), "scopes": {}})
    mcp_enabled: bool = False
    proxy_fix: int = 0
    inprocess_worker: bool = False
    max_content_length: int = 1_048_576
    secure_cookies: bool = False
    log_level: str = "INFO"
    platform: str = "local"
    ignored_keys: tuple[str, ...] = ()
    secrets_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise SettingsError("BAD_MODE", f"mode must be one of {MODES}")
        if self.llm_provider not in LLM_PROVIDERS:
            raise SettingsError("BAD_LLM_PROVIDER", f"llm_provider must be one of {LLM_PROVIDERS}")
        if self.identity_provider not in IDENTITY_PROVIDERS:
            raise SettingsError("BAD_IDENTITY_PROVIDER", f"identity_provider must be one of {IDENTITY_PROVIDERS}")
        if self.identity_provider == "trusted_header" and not self.trusted_user_header:
            raise SettingsError("TRUSTED_HEADER_MISSING", "trusted_header identity requires FOCUS_TRUSTED_USER_HEADER")
        if self.mode == "live" and self.identity_provider == "fixture":
            raise SettingsError("FIXTURE_IDENTITY_IN_LIVE", "live mode cannot use the demo fixture login")
        if not (1 <= self.port <= 65535):
            raise SettingsError("BAD_PORT", "port must be within 1..65535")

    # ------------------------------------------------------------------
    @property
    def policy_dir(self) -> Path:
        return self.app_root / "config" / "policies"

    @property
    def fixture_inputs_dir(self) -> Path:
        return self.app_root / "fixtures" / "inputs"

    def runtime_dir(self, scenario: str | None = None) -> Path:
        name = scenario or self.scenario_id
        if not name.replace("_", "").replace("-", "").isalnum():
            raise SettingsError("BAD_SCENARIO", f"scenario id {name!r} must be alphanumeric/underscore/dash")
        base = self.runtime_root if self.runtime_root is not None else self.app_root / "fixtures" / "runtime"
        return base / name

    @property
    def sql_configured(self) -> bool:
        return bool(self.sql_host and self.sql_database and self.sql_user and self.sql_password)

    def public_view(self) -> dict[str, Any]:
        """Redacted settings summary safe for health/diagnostics."""
        return {
            "mode": self.mode,
            "platform": self.platform,
            "bind": self.bind,
            "port": self.port,
            "policy_id": self.policy_id,
            "scenario_id": self.scenario_id if self.mode == "fixture" else None,
            "scope_key": self.scope_key,
            "sql_configured": self.sql_configured,
            "solver_enabled": self.solver_enabled,
            "native_core": self.native_core,
            "llm_provider": self.llm_provider,
            "identity_provider": self.identity_provider,
            "mcp_enabled": self.mcp_enabled,
            "inprocess_worker": self.inprocess_worker,
            "session_secret": "ephemeral" if self.session_secret_ephemeral else "configured",
            "sign_key": "configured" if self.sign_key_ref else ("fixture-test-key" if self.mode == "fixture" else "absent"),
            "ignored_setting_names": list(self.ignored_keys),
            "secrets_from": list(self.secrets_from),
        }

    def replace(self, **changes: Any) -> "Settings":
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update(changes)
        return Settings(**data)

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> "Settings":
        env = dict(os.environ if env is None else env)
        focus_keys = {k for k in env if k.startswith("FOCUS_")}
        ignored = tuple(sorted(focus_keys - ALLOWED_KEYS))
        get = env.get
        platform = _detect_platform(env)
        mode = _choice("FOCUS_MODE", get("FOCUS_MODE"), MODES, "fixture")
        secrets_from: list[str] = []

        bind_default = "0.0.0.0" if platform == "tanzu" else "127.0.0.1"
        port_raw = get("FOCUS_PORT") or (get("PORT") if platform == "tanzu" else None)
        port = _parse_int("FOCUS_PORT/PORT", port_raw, 5055, 1, 65535)

        sql_host = get("FOCUS_SQL_HOST") or "SQL-D-22D10090"
        sql_database = get("FOCUS_SQL_DATABASE") or "SHARED"
        sql_user = get("FOCUS_SQL_USER") or None
        sql_password_raw = get("FOCUS_SQL_PASSWORD") or None
        if platform == "tanzu":
            creds = _vcap_credentials(env, get("FOCUS_SQL_SERVICE_NAME") or "focus-sql")
            if creds:
                sql_host = get("FOCUS_SQL_HOST") or str(creds.get("host") or sql_host)
                sql_database = get("FOCUS_SQL_DATABASE") or str(creds.get("database") or sql_database)
                sql_user = sql_user or (str(creds["user"]) if creds.get("user") else None)
                sql_password_raw = sql_password_raw or (str(creds["password"]) if creds.get("password") else None)
                secrets_from.append("VCAP_SERVICES:" + (get("FOCUS_SQL_SERVICE_NAME") or "focus-sql"))

        session_raw = get("FOCUS_SESSION_SECRET") or None
        sign_key_ref = get("FOCUS_SIGN_KEY_REF") or None
        sign_key_id = get("FOCUS_SIGN_KEY_ID") or None
        bcai_ref = get("FOCUS_BCAI_CREDENTIAL_REF") or None
        if platform == "tanzu":
            secret_creds = _vcap_credentials(env, get("FOCUS_SECRETS_SERVICE_NAME") or "focus-secrets")
            if secret_creds:
                session_raw = session_raw or (str(secret_creds["session_secret"]) if secret_creds.get("session_secret") else None)
                if not sign_key_ref and secret_creds.get("sign_key"):
                    sign_key_ref = "vcap:" + (get("FOCUS_SECRETS_SERVICE_NAME") or "focus-secrets") + "/sign_key"
                    sign_key_id = sign_key_id or str(secret_creds.get("sign_key_id") or "") or None
                secrets_from.append("VCAP_SERVICES:" + (get("FOCUS_SECRETS_SERVICE_NAME") or "focus-secrets"))

        identity_default = {"posit_connect": "posit_connect", "tanzu": "trusted_header"}.get(platform, "fixture")
        if mode == "live" and identity_default == "fixture":
            identity_default = "none"
        identity = _choice("FOCUS_IDENTITY_PROVIDER", get("FOCUS_IDENTITY_PROVIDER"), IDENTITY_PROVIDERS, identity_default)
        trusted_header = get("FOCUS_TRUSTED_USER_HEADER") or ("X-Forwarded-User" if identity == "trusted_header" else None)

        tz_map = _parse_json_object("FOCUS_SOURCE_TIMEZONE_MAP_JSON", get("FOCUS_SOURCE_TIMEZONE_MAP_JSON"))
        for key, value in tz_map.items():
            if not isinstance(value, str):
                raise SettingsError("BAD_TIMEZONE_MAP", f"timezone for {key} must be a string")

        values: dict[str, Any] = dict(
            mode=mode,
            app_root=resolve_app_root(get("FOCUS_APP_ROOT")),
            bind=get("FOCUS_BIND") or bind_default,
            port=port,
            sql_host=sql_host,
            sql_database=sql_database,
            sql_user=sql_user,
            sql_password=Secret(sql_password_raw) if sql_password_raw else None,
            sql_read_timeout_seconds=_parse_int("FOCUS_SQL_READ_TIMEOUT_SECONDS", get("FOCUS_SQL_READ_TIMEOUT_SECONDS"), 60, 1, 3600),
            sql_login_timeout_seconds=_parse_int("FOCUS_SQL_LOGIN_TIMEOUT_SECONDS", get("FOCUS_SQL_LOGIN_TIMEOUT_SECONDS"), 15, 1, 600),
            source_timezone_map=tz_map,
            policy_id=get("FOCUS_POLICY_ID") or "rebuild-v1",
            scenario_id=get("FOCUS_SCENARIO_ID") or "manufacturing_demo",
            runtime_root=Path(get("FOCUS_RUNTIME_DIR")).expanduser().resolve() if get("FOCUS_RUNTIME_DIR") else None,
            scope_key=get("FOCUS_SCOPE_KEY") or "DEMO-787",
            site=get("FOCUS_SITE") or "DEMO",
            program=get("FOCUS_PROGRAM") or "787",
            worker_budget_seconds=_parse_float("FOCUS_WORKER_BUDGET_SECONDS", get("FOCUS_WORKER_BUDGET_SECONDS"), 30.0, 0.1, 3600.0),
            solver_enabled=_parse_bool("FOCUS_SOLVER_ENABLED", get("FOCUS_SOLVER_ENABLED"), False),
            native_core=_parse_bool("FOCUS_NATIVE_CORE", get("FOCUS_NATIVE_CORE"), False),
            llm_provider=_choice("FOCUS_LLM_PROVIDER", get("FOCUS_LLM_PROVIDER"), LLM_PROVIDERS, "none"),
            bcai_endpoint=get("FOCUS_BCAI_ENDPOINT") or None,
            bcai_credential_ref=bcai_ref,
            session_secret=Secret(session_raw) if session_raw else Secret(_secrets.token_hex(32)),
            session_secret_ephemeral=session_raw is None,
            sign_key_id=sign_key_id,
            sign_key_ref=sign_key_ref,
            identity_provider=identity,
            trusted_user_header=trusted_header,
            trusted_groups_header=get("FOCUS_TRUSTED_GROUPS_HEADER") or None,
            grants=_normalize_grants(_parse_json_object("FOCUS_GRANTS_JSON", get("FOCUS_GRANTS_JSON"))),
            mcp_enabled=_parse_bool("FOCUS_MCP_ENABLED", get("FOCUS_MCP_ENABLED"), False),
            proxy_fix=_parse_int("FOCUS_PROXY_FIX", get("FOCUS_PROXY_FIX"), 1 if platform in ("tanzu", "posit_connect") else 0, 0, 5),
            inprocess_worker=_parse_bool("FOCUS_INPROCESS_WORKER", get("FOCUS_INPROCESS_WORKER"), mode == "fixture"),
            max_content_length=_parse_int("FOCUS_MAX_CONTENT_LENGTH", get("FOCUS_MAX_CONTENT_LENGTH"), 1_048_576, 1024, 16 * 1_048_576),
            secure_cookies=_parse_bool("FOCUS_SECURE_COOKIES", get("FOCUS_SECURE_COOKIES"), platform != "local"),
            log_level=(get("FOCUS_LOG_LEVEL") or "INFO").upper(),
            platform=platform,
            ignored_keys=ignored,
            secrets_from=tuple(secrets_from),
        )
        values.update(overrides)
        return cls(**values)
