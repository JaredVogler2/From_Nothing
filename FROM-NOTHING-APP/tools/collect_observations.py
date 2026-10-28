"""Collect lifecycle/actual/claim/decision observations into an evaluation-ready run (P17 tool; wraps ``python -m focus.cli collect-observations``).

python tools/collect_observations.py --mode fixture [--scenario manufacturing_demo] [--as-of 2026-10-26T19:30:00Z] [--all-points] [--app-root PATH]

Rows become visible only once observed (``OBSERVED_AT_UTC <= as-of``). Each feed receives a coverage receipt:
ROWS, CERTIFIED_EMPTY, EMPTY_UNCERTIFIED, FAILED or NOT_PROBED. Malformed rows are quarantined with stable reason
codes, and replays are idempotent. ``--all-points`` replays every collection point the fixture manifest declares,
in order. The exit code is 0 only when every required feed is usable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["fixture", "live"], required=True)
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--all-points", action="store_true")
    parser.add_argument("--app-root", default=None)
    args = parser.parse_args()
    base = ["--app-root", args.app_root] if args.app_root else []
    if not args.all_points:
        extra = (["--scenario", args.scenario] if args.scenario else []) + (["--as-of", args.as_of] if args.as_of else [])
        raise SystemExit(main(base + ["collect-observations", "--mode", args.mode] + extra))
    from focus.config import Settings
    from focus.performance.actuals import manifest_points
    from focus.runtime import build_services

    settings = Settings.from_env(**({"app_root": Path(args.app_root)} if args.app_root else {}))
    points = manifest_points(build_services(settings), args.scenario)
    if not points:
        print("no collection points declared by the scenario manifest", file=sys.stderr)
        raise SystemExit(2)
    code = 0
    for at in points:
        stamp = at.strftime("%Y-%m-%dT%H:%M:%SZ")
        code = max(code, main(base + ["collect-observations", "--mode", args.mode, "--as-of", stamp]
                              + (["--scenario", args.scenario] if args.scenario else [])))
    raise SystemExit(code)
