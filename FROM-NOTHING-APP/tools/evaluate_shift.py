"""Canonical shift evaluation and supplementary recognition (P18 tool; wraps ``python -m focus.cli evaluate``).

python tools/evaluate_shift.py --shift DEMO:2026-10-26:S1 --policy rebuild-v1 [--team PROD-B] [--as-of RFC3339] [--project] [--app-root PATH]

The tool appends a new revision only when the unit-fact evidence changed; every display reads that persisted revision.
With ``--project`` it also projects the idempotent recognition streams and badges from the canonical evaluations, and
prints each team's verified XP and level (never a qualification). The exit code is 0 when every evaluation was scored
or neutral, and 2 when any evaluation is unknown (missing evidence or policy, or an open window).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shift", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--team", default=None)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--project", action="store_true")
    parser.add_argument("--app-root", default=None)
    args = parser.parse_args()
    base = ["--app-root", args.app_root] if args.app_root else []
    argv = base + ["evaluate", "--shift", args.shift, "--policy", args.policy] + (["--team", args.team] if args.team else []) \
        + (["--as-of", args.as_of] if args.as_of else [])
    code = main(argv)
    if args.project:
        from focus.analytics.progression import project, team_progress
        from focus.codec import parse_utc
        from focus.config import Settings
        from focus.runtime import build_services

        services = build_services(Settings.from_env(**({"app_root": Path(args.app_root)} if args.app_root else {})))
        at = parse_utc(args.as_of) if args.as_of else services.clock.now()
        result = project(services, as_of=at)
        teams = sorted({e.team for e in result.appended + result.corrections} | ({args.team} if args.team else set()))
        print(json.dumps({"recognition": {"appended": len(result.appended), "corrections": len(result.corrections),
                                          "badges": [{"badge": b.badge, "team": b.team, "period": b.period, "revoked": b.revoked} for b in result.badges],
                                          "skipped": result.skipped},
                          "progress": [{k: (str(v) if not isinstance(v, (int, str, list)) else v) for k, v in team_progress(services, t).items()}
                                       for t in teams]}, indent=1, sort_keys=True))
    raise SystemExit(code)
