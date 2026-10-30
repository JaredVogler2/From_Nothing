"""Runtime preflight for a host before it serves or works (S17, P23). It never writes and never prints a secret.

``python tools/runtime_preflight.py`` (or ``python -m focus.cli preflight``) reports PASS / WARN / FAIL checks:

* **interpreter**: CPython 3.12, the user directive for Posit Connect and Tanzu;
* **settings**: the adapter accepts the environment, names ignored ``FOCUS_*`` keys and detects the platform;
* **identity**: live mode needs a trusted provider (Posit Connect credentials or a trusted SSO header), never the
  demo login;
* **secrets**, by presence only:
  - live mode needs a configured, non-ephemeral session secret shared by every instance, and SQL credentials;
  - signing material belongs only on the signer role;
* **policy**: it loads, and its id and hash are reported;
* **storage**: the fixture runtime is writable, or live SQL is configured;
* **app**: ``create_app`` builds without side effects;
* **migrations**: the shipped versions discover and lint clean. With ``--probe-sql``, the configured principal
  compares the installed ledger (checksums) with the shipped set, read-only;
* **options**: the solver dependency when the stage is switched on, MCP exposure and the explanation provider.

The run fails (exit 1) when any check FAILs.
"""
from __future__ import annotations

import os
import platform as _platform
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

ROLES = ("web", "worker", "signer", "installer")


@dataclass(frozen=True)
class Check:
    name: str
    status: str            # PASS | WARN | FAIL
    detail: str


def _interpreter(version: tuple[int, int] | None = None, implementation: str | None = None) -> Check:
    major, minor = version or sys.version_info[:2]
    impl = implementation or _platform.python_implementation()
    if (major, minor) == (3, 12) and impl == "CPython":
        return Check("interpreter", "PASS", f"{impl} {major}.{minor}")
    return Check("interpreter", "FAIL", f"{impl} {major}.{minor}: FOCUS is built and tested on CPython 3.12 (runtime.txt, requires-python)")


def _settings_checks(settings: Any, role: str) -> list[Check]:
    out = [Check("platform", "PASS", f"{settings.platform} ({'Posit Connect' if settings.platform == 'posit_connect' else 'VMware Tanzu' if settings.platform == 'tanzu' else 'local'})")]
    if settings.ignored_keys:
        out.append(Check("settings.ignored", "WARN", f"ignored settings (not in the allowlist): {', '.join(settings.ignored_keys)}"))
    live = settings.mode == "live"
    if live:
        trusted = settings.identity_provider in ("posit_connect", "trusted_header")
        out.append(Check("identity", "PASS" if trusted else "FAIL",
                         f"{settings.identity_provider}" + ("" if trusted else ": live mode needs posit_connect or trusted_header identity")))
        out.append(Check("session_secret", "FAIL" if settings.session_secret_ephemeral else "PASS",
                         "ephemeral per process: sessions break across instances; set FOCUS_SESSION_SECRET from the secret store"
                         if settings.session_secret_ephemeral else "configured (value not shown)"))
        out.append(Check("sql", "PASS" if settings.sql_configured else "FAIL",
                         "host, database, user and password present (values not shown)" if settings.sql_configured
                         else "live mode needs FOCUS_SQL_HOST, FOCUS_SQL_DATABASE, FOCUS_SQL_USER and FOCUS_SQL_PASSWORD (or the Tanzu focus-sql service)"))
        if not settings.secure_cookies:
            out.append(Check("cookies", "WARN", "FOCUS_SECURE_COOKIES is off; hosted HTTPS deployments should turn it on"))
    else:
        out.append(Check("identity", "PASS", f"{settings.identity_provider} (fixture mode is a labeled demo)"))
        out.append(Check("session_secret", "WARN" if settings.session_secret_ephemeral else "PASS",
                         "ephemeral: fine for one local process, not for several instances" if settings.session_secret_ephemeral
                         else "configured (value not shown)"))
    signer_material = bool(settings.sign_key_ref)
    if role == "signer":
        if live and not (settings.sign_key_id and signer_material):
            out.append(Check("signing", "FAIL", "the signer role needs FOCUS_SIGN_KEY_ID and FOCUS_SIGN_KEY_REF (referenced, never inline)"))
        else:
            out.append(Check("signing", "PASS", "signing key referenced" if signer_material else "fixture TEST key (labeled, never live)"))
    elif signer_material:
        out.append(Check("signing", "WARN", f"signing material is referenced on a {role} process; keep FOCUS_SIGN_KEY_REF on the signer only"))
    if settings.mcp_enabled:
        out.append(Check("mcp", "WARN", "MCP is exposed at /mcp (read-only, same identity and grants as REST); confirm it is approved"))
    if settings.llm_provider == "bcai" and not (settings.bcai_endpoint and settings.bcai_credential_ref):
        out.append(Check("explanations", "FAIL", "FOCUS_LLM_PROVIDER=bcai needs FOCUS_BCAI_ENDPOINT and FOCUS_BCAI_CREDENTIAL_REF"))
    return out


def _policy_check(settings: Any) -> tuple[Check, Any]:
    from .policy import PolicyError, load_policy

    try:
        policy = load_policy(settings.policy_dir, settings.policy_id)
    except (PolicyError, OSError, ValueError) as exc:
        return Check("policy", "FAIL", f"{settings.policy_id}: {exc}"), None
    return Check("policy", "PASS", f"{policy.id} hash {policy.hash[:16]}"), policy


def _storage_check(settings: Any) -> Check:
    if settings.mode == "live":
        return Check("storage", "PASS" if settings.sql_configured else "FAIL", "SQL Server (FOCUS_FN tables)" if settings.sql_configured
                     else "no SQL configuration: live storage unavailable")
    path = settings.runtime_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".preflight-", delete=True):
            pass
    except OSError as exc:
        return Check("storage", "FAIL", f"fixture runtime {path} is not writable: {exc}")
    return Check("storage", "PASS", f"fixture runtime {path} writable (ephemeral on Posit Connect and Tanzu)")


def _app_check(settings: Any) -> Check:
    try:
        from .web.app import create_app

        app = create_app(settings=settings)
        rules = sum(1 for _ in app.url_map.iter_rules())
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never raised
        return Check("app", "FAIL", f"create_app failed: {type(exc).__name__}: {str(exc)[:200]}")
    return Check("app", "PASS", f"create_app built {rules} routes (app:app, Flask WSGI)")


def _migration_checks(settings: Any, probe: Callable[[], Mapping[int, str]] | None) -> list[Check]:
    from .migrations import MigrationError, all_findings, discover, migrations_root

    try:
        shipped = discover(migrations_root(Path(settings.app_root)))
    except MigrationError as exc:
        return [Check("migrations", "FAIL", f"{exc.code}: {exc.message}")]
    findings = all_findings(shipped, Path(settings.app_root))
    out = [Check("migrations", "FAIL" if findings else "PASS",
                 f"{len(findings)} lint findings, first: {findings[0].code} {findings[0].subject}" if findings
                 else f"{len(shipped)} versions shipped, lint clean, latest {shipped[-1].version:03d}" if shipped else "no versions shipped")]
    if probe is None:
        if settings.mode == "live":
            out.append(Check("schema", "WARN", "installed ledger not probed (run with --probe-sql using the reader principal)"))
        return out
    try:
        installed = dict(probe())
    except Exception as exc:  # noqa: BLE001
        return out + [Check("schema", "FAIL", f"ledger probe failed: {type(exc).__name__}: {str(exc)[:200]}")]
    expected = {m.version: m.sha256 for m in shipped}
    wrong = sorted(v for v in installed if expected.get(v) != installed[v])
    missing = sorted(v for v in expected if v not in installed)
    if wrong:
        out.append(Check("schema", "FAIL", f"installed versions differ from the shipped scripts: {wrong}"))
    elif missing:
        out.append(Check("schema", "FAIL", f"versions not installed: {missing}; the app's live storage is unavailable until an installer applies them"))
    else:
        out.append(Check("schema", "PASS", f"ledger matches all {len(expected)} shipped versions"))
    return out


def _solver_check(settings: Any, policy: Any) -> Check | None:
    switched = settings.solver_enabled or (policy is not None and policy.bool("solver.enabled"))
    if not switched:
        return None
    from .engine.max.optimizer.solver import solver_available

    ok, version, why = solver_available()
    return Check("solver", "PASS" if ok else "WARN", f"OR-Tools {version}" if ok else f"{why}; every run will record UNAVAILABLE and keep the baseline")


def sql_ledger_probe(settings: Any) -> Callable[[], Mapping[int, str]]:
    """Read-only ledger probe with the configured principal (never connected by this build's tests)."""
    def probe() -> Mapping[int, str]:
        import pymssql  # type: ignore[import-not-found]

        conn = pymssql.connect(server=settings.sql_host, database=settings.sql_database, user=settings.sql_user,
                               password=settings.sql_password.reveal() if settings.sql_password else None,
                               login_timeout=settings.sql_login_timeout_seconds, timeout=settings.sql_read_timeout_seconds)
        try:
            cur = conn.cursor()
            cur.execute("SELECT CASE WHEN OBJECT_ID(N'WEB_APP.FOCUS_FN_SchemaVersion', N'U') IS NULL THEN 0 ELSE 1 END")
            if not cur.fetchone()[0]:
                return {}
            cur.execute("SELECT Version, ScriptSha FROM WEB_APP.FOCUS_FN_SchemaVersion")
            return {int(v): str(sha) for v, sha in cur.fetchall()}
        finally:
            conn.close()
    return probe


def run(settings: Any = None, *, env: Mapping[str, str] | None = None, role: str = "web", probe: Callable[[], Mapping[int, str]] | None = None,
        interpreter: tuple[tuple[int, int], str] | None = None) -> dict[str, Any]:
    from .config import Settings, SettingsError

    if role not in ROLES:
        return {"status": "invalid", "error": {"code": "BAD_ROLE", "message": f"role must be one of {ROLES}"}}
    checks = [_interpreter(*interpreter) if interpreter else _interpreter()]
    if settings is None:
        try:
            settings = Settings.from_env(dict(os.environ if env is None else env))
        except SettingsError as exc:
            checks.append(Check("settings", "FAIL", f"{exc.code}: {exc.message}"))
            return {"status": "error", "role": role, "checks": [asdict(c) for c in checks]}
    checks.append(Check("settings", "PASS", f"mode {settings.mode}, scope {settings.scope_key}"))
    checks += _settings_checks(settings, role)
    policy_check, policy = _policy_check(settings)
    checks.append(policy_check)
    checks.append(_storage_check(settings))
    if role == "web":
        checks.append(_app_check(settings))
    checks += _migration_checks(settings, probe)
    solver = _solver_check(settings, policy)
    if solver is not None:
        checks.append(solver)
    failed = [c for c in checks if c.status == "FAIL"]
    return {"status": "error" if failed else "ok", "role": role, "mode": settings.mode, "platform": settings.platform,
            "checks": [asdict(c) for c in checks], "failed": [c.name for c in failed],
            "note": "presence checks only: no secret value is read into this report"}


def cli_preflight(services: Any, args: Any) -> dict[str, Any]:
    settings = services.settings
    probe = sql_ledger_probe(settings) if getattr(args, "probe_sql", False) and settings.sql_configured else None
    result = run(settings, role=getattr(args, "role", "web") or "web", probe=probe)
    if getattr(args, "probe_sql", False) and probe is None:
        result["checks"].append({"name": "schema", "status": "FAIL" if settings.mode == "live" else "WARN",
                                 "detail": "--probe-sql needs SQL credentials (live reader principal)"})
        if settings.mode == "live":
            result["status"] = "error"
    return result
