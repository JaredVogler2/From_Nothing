"""Run the rich MAX optimizer on an admitted source run (P13 tool; wraps ``python -m focus.cli schedule``).

python tools/run_schedule.py [--source-run RUN_ID] [--purpose EXECUTABLE|FORECAST] [--budget-seconds 30] [--app-root PATH]

Without --source-run the current source head of the configured scope is used. The result is a
candidate plan receipt: complete job dispositions, baseline and final exact costs (unknown aircraft
listed, never zero), commitment outcomes, stage runtimes and global_optimality_proven=false. Nothing
is published (P16 publishes only after independent validation).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import emit, main  # noqa: E402


def _head_run(app_root: str | None) -> tuple[str | None, str]:
    from focus.config import Settings
    from focus.data.acquisition import source_head_name
    from focus.runtime import build_services

    env = dict(os.environ)
    if app_root:
        env["FOCUS_APP_ROOT"] = app_root
    services = build_services(Settings.from_env(env))
    head = services.repositories.head(source_head_name(services.settings.scope_key))
    return (head.record_id if head else None), services.policy.id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-run", default=None)
    parser.add_argument("--purpose", choices=["EXECUTABLE", "FORECAST"], default="EXECUTABLE")
    parser.add_argument("--budget-seconds", type=float, default=30.0)
    parser.add_argument("--app-root", default=None)
    args = parser.parse_args()
    head, policy_id = _head_run(args.app_root)
    run_id = args.source_run or head
    if not run_id:
        emit({"schema_version": 1, "command": "schedule", "status": "unavailable",
              "error": {"code": "SOURCE_HEAD_MISSING", "message": "no admitted source run; acquire first (tools/acquire_sources.py)"}})
        raise SystemExit(3)
    argv = (["--app-root", args.app_root] if args.app_root else []) + [
        "schedule", "--source-run", run_id, "--policy", policy_id, "--purpose", args.purpose, "--budget-seconds", str(args.budget_seconds)]
    raise SystemExit(main(argv))
