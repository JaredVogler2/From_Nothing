"""Compute and persist duration statistics for an admitted source run (P07 tool).

python tools/build_duration_stats.py [--source-run RUN_ID] [--app-root PATH]

Without --source-run the current source head of the configured scope is used. The work is done by
``python -m focus.cli duration-stats`` (focus.data.duration_gauge.cli_duration_stats): exact
continuous percentiles (P50/P55/mean/P75/P90) of completed comparable elapsed episodes, the P75
order-statistic interval, exclusions with reasons, window, models, crews and source/query/method
hashes, plus raw-clock union examples. It reads fixture or SQL-compatible run datasets and never
writes to a live database.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import emit, main  # noqa: E402


def _head_run(app_root: str | None) -> str | None:
    import os

    from focus.config import Settings
    from focus.data.acquisition import source_head_name
    from focus.runtime import build_services

    env = dict(os.environ)
    if app_root:
        env["FOCUS_APP_ROOT"] = app_root
    services = build_services(Settings.from_env(env))
    head = services.repositories.head(source_head_name(services.settings.scope_key))
    return head.record_id if head else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-run", default=None)
    parser.add_argument("--app-root", default=None)
    args = parser.parse_args()
    run_id = args.source_run or _head_run(args.app_root)
    if not run_id:
        emit({"schema_version": 1, "command": "duration-stats", "status": "unavailable",
              "error": {"code": "SOURCE_HEAD_MISSING", "message": "no admitted source run; acquire first (tools/acquire_sources.py)"}})
        raise SystemExit(3)
    argv = (["--app-root", args.app_root] if args.app_root else []) + ["duration-stats", "--source-run", run_id]
    raise SystemExit(main(argv))
