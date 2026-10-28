"""FOCUS command line (S17 LOCAL CLI INTERFACE).

Every command prints one schema-versioned JSON result
``{"schema_version": 1, "command", "status", ...}`` and exits non-zero on an
invalid input or error. A queued request is never reported as a successful
schedule or publication. Handlers are imported lazily so ``import focus.cli``
stays side-effect free.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from typing import Any, Callable, Sequence

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID = 2
EXIT_UNAVAILABLE = 3

# command -> "module:function"; each handler(services, args) -> dict result
COMMANDS: dict[str, str] = {
    "demo-bootstrap": "focus.pipeline:cli_demo_bootstrap",
    "demo-run": "focus.pipeline:cli_demo_run",
    "source-discover": "focus.data.discovery:cli_source_discover",
    "acquire": "focus.data.acquisition:cli_acquire",
    "duration-stats": "focus.data.duration_gauge:cli_duration_stats",
    "schedule": "focus.engine.max.optimizer.orchestrator:cli_schedule",
    "validate": "focus.validation.plan:cli_validate",
    "publish": "focus.publication.service:cli_publish",
    "dispatch-check": "focus.publication.dispatch:cli_dispatch_check",
    "collect-observations": "focus.performance.actuals:cli_collect_observations",
    "evaluate": "focus.performance.score:cli_evaluate",
    "scenario": "focus.analytics.scenarios:cli_scenario",
    "benchmark": "focus.engine.max.optimizer.solver:cli_benchmark",
    "migrate": "focus.migrations:cli_migrate",
    "worker": "focus.workers:cli_worker",
    "serve": "focus.cli:cli_serve",
    "status": "focus.cli:cli_status",
    "mcp-catalog": "focus.cli:cli_mcp_catalog",
    "mcp-stdio": "focus.cli:cli_mcp_stdio",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m focus.cli", description="FOCUS manufacturing scheduling CLI")
    parser.add_argument("--app-root", help="override FOCUS_APP_ROOT for this command")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo-bootstrap", help="materialize the deterministic manufacturing_demo fixture and the accepted incumbent")
    p.add_argument("--scenario", default="manufacturing_demo")
    p.add_argument("--reset", action="store_true", help="remove fixtures/runtime/<scenario> first (fixture only)")

    p = sub.add_parser("demo-run", help="run the complete fixture pipeline end to end, carrying returned IDs through every stage")
    p.add_argument("--scenario", default="manufacturing_demo")
    p.add_argument("--budget-seconds", type=float, default=None)

    p = sub.add_parser("source-discover", help="read-only schema discovery and drift report")
    p.add_argument("--read-only", action="store_true", required=True)
    p.add_argument("--mode", choices=["fixture", "live"], default=None)
    p.add_argument("--scenario", default=None)

    p = sub.add_parser("acquire", help="atomic source acquisition into a private run, then admission")
    p.add_argument("--mode", choices=["fixture", "live"], required=True)
    p.add_argument("--scenario", default=None)
    p.add_argument("--cutoff", default=None, help="RFC3339 cutoff (fixture default: S18 cutoff)")
    p.add_argument("--idempotency-key", default=None)

    p = sub.add_parser("duration-stats", help="compute and persist duration statistics for a source run")
    p.add_argument("--source-run", required=True)
    p.add_argument("--measurement", choices=["elapsed"], default="elapsed")

    p = sub.add_parser("schedule", help="run the rich MAX optimizer for an admitted source run")
    p.add_argument("--source-run", required=True)
    p.add_argument("--policy", required=True)
    p.add_argument("--budget-seconds", type=float, default=30.0)
    p.add_argument("--purpose", choices=["FORECAST", "EXECUTABLE"], default="EXECUTABLE")

    p = sub.add_parser("validate", help="independent nine-check validation of a candidate")
    p.add_argument("--candidate", required=True)
    p.add_argument("--all-required", action="store_true", required=True)

    p = sub.add_parser("publish", help="canonical acceptance protocol (fixture rehearses pre-sign readback)")
    p.add_argument("--candidate", required=True)
    p.add_argument("--mode", choices=["fixture", "live"], required=True)
    p.add_argument("--rehearse-readback", action="store_true")

    p = sub.add_parser("dispatch-check", help="current-action dispatch check against current facts")
    p.add_argument("--plan", required=True)
    p.add_argument("--action", required=True)

    p = sub.add_parser("collect-observations", help="collect lifecycle observations")
    p.add_argument("--mode", choices=["fixture", "live"], required=True)
    p.add_argument("--scenario", default=None)

    p = sub.add_parser("evaluate", help="canonical shift evaluation")
    p.add_argument("--plan", required=True)
    p.add_argument("--shift", required=True)
    p.add_argument("--policy", required=True)
    p.add_argument("--team", default=None)

    p = sub.add_parser("scenario", help="isolated what-if scenario")
    p.add_argument("--source-run", required=True)
    p.add_argument("--changes", required=True, help="JSON object of allowlisted typed changes")
    p.add_argument("--budget-seconds", type=float, default=30.0)

    p = sub.add_parser("benchmark", help="complete-scope scale benchmark")
    p.add_argument("--complete-scope", action="store_true", required=True)
    p.add_argument("--solver", choices=["off", "on"], default="off")
    p.add_argument("--sizes", default="51,100,150,300")
    p.add_argument("--budget-seconds", type=float, default=20.0)

    p = sub.add_parser("migrate", help="SQL migration preflight/dry-run (never applied implicitly)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true", help="apply with an authorized installer principal")
    p.add_argument("--emit-dir", default=None)

    p = sub.add_parser("worker", help="process queued worker runs")
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true", help="keep polling for queued runs (separate worker process)")
    p.add_argument("--interval-seconds", type=float, default=5.0)
    p.add_argument("--max-runs", type=int, default=100)

    p = sub.add_parser("serve", help="serve the Flask app with Waitress (never kills a port occupant)")
    p.add_argument("--bind", default=None)
    p.add_argument("--port", type=int, default=None)

    sub.add_parser("status", help="print settings summary, heads and execution register status")
    sub.add_parser("mcp-catalog", help="print the MCP tool/resource catalog generated from the operation registry")
    p = sub.add_parser("mcp-stdio", help="serve read-only MCP over stdio for a chosen identity")
    p.add_argument("--persona", default="manager", help="fixture demo persona (fixture mode only)")
    return parser


def _load(target: str) -> Callable[[Any, argparse.Namespace], dict[str, Any]]:
    module_name, func_name = target.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def emit(result: dict[str, Any]) -> None:
    from .codec import to_jsonable

    sys.stdout.write(json.dumps(to_jsonable(result), indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    from .config import Settings, SettingsError

    try:
        overrides: dict[str, Any] = {}
        if getattr(args, "mode", None) in ("fixture", "live") and command in ("acquire", "source-discover", "collect-observations", "publish"):
            overrides["mode"] = args.mode
        scenario = getattr(args, "scenario", None)
        if scenario:
            overrides["scenario_id"] = scenario
        env = None
        if args.app_root:
            import os

            env = dict(os.environ)
            env["FOCUS_APP_ROOT"] = args.app_root
        settings = Settings.from_env(env, **overrides)
    except SettingsError as exc:
        emit({"schema_version": 1, "command": command, "status": "invalid", "error": {"code": exc.code, "message": exc.message}})
        return EXIT_INVALID
    from .runtime import build_services

    services = build_services(settings)
    try:
        handler = _load(COMMANDS[command])
    except (ImportError, AttributeError) as exc:
        emit({"schema_version": 1, "command": command, "status": "unavailable",
              "error": {"code": "COMMAND_UNAVAILABLE", "message": f"{command} handler not available: {exc}"}})
        return EXIT_UNAVAILABLE
    try:
        result = handler(services, args)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a typed result
        code = getattr(exc, "code", type(exc).__name__)
        message = getattr(exc, "message", str(exc))
        emit({"schema_version": 1, "command": command, "status": "error", "error": {"code": code, "message": message},
              "trace_tail": traceback.format_exc().splitlines()[-3:]})
        return EXIT_ERROR
    result = {"schema_version": 1, "command": command, **result}
    emit(result)
    status = result.get("status")
    if status in ("ok", "completed", "served"):
        return EXIT_OK
    if status == "invalid":
        return EXIT_INVALID
    if status == "unavailable":
        return EXIT_UNAVAILABLE
    return EXIT_ERROR


# ---------------------------------------------------------------------------
# small built-in commands
# ---------------------------------------------------------------------------

def cli_serve(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    import socket

    from .web.app import create_app

    bind = args.bind or services.settings.bind
    port = args.port or services.settings.port
    # Never kill a port occupant: probe and refuse if the port is already bound.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((bind, port))
    except OSError:
        return {"status": "error", "error": {"code": "PORT_IN_USE", "message": f"{bind}:{port} is already in use; nothing was killed"}}
    finally:
        probe.close()
    app = create_app(services=services)
    from .workers import maybe_start_inprocess_worker

    maybe_start_inprocess_worker(services)
    from waitress import serve

    sys.stderr.write(f"FOCUS serving on http://{bind}:{port} (mode={services.settings.mode}, platform={services.settings.platform})\n")
    serve(app, host=bind, port=port, threads=8, ident="focus")
    return {"status": "served", "bind": bind, "port": port}


def cli_status(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    from .codec import strict_loads

    status_path = services.settings.app_root / "docs" / "execution" / "STATUS.json"
    register = strict_loads(status_path.read_bytes()) if status_path.exists() else None
    repos = services.repositories
    heads = {}
    if repos.available:
        scope = services.settings.scope_key
        for name in (f"source/{scope}", f"plan/{scope}/EXECUTABLE", f"plan/{scope}/FORECAST"):
            head = repos.head(name)
            heads[name] = None if head is None else {"record_id": head.record_id, "version": head.version}
    return {
        "status": "ok",
        "settings": services.settings.public_view(),
        "policy": services.policy_status(),
        "heads": heads,
        "stages": {k: v.get("status") for k, v in (register or {}).get("stages", {}).items()},
    }


def cli_mcp_catalog(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    from .mcp.catalog import describe
    from .web import api, api_views  # noqa: F401 - registers operations
    from .web.operations import REGISTRY

    return {"status": "ok", "catalog": describe(REGISTRY)}


def cli_mcp_stdio(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    from .mcp.stdio import serve_stdio

    serve_stdio(services, persona=args.persona)
    return {"status": "ok"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
