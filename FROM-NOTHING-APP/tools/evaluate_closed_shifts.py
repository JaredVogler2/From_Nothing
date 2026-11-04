"""Score every shift whose credit window has closed: the evaluator-only run after a shift's overtime (D-26).

    python tools/evaluate_closed_shifts.py [--as-of RFC3339] [--app-root PATH]

The pipeline before each shift starts while the previous shift is still working, so that shift's score comes from a
later run. This tool gives it promptly: scheduled just after a shift's authorized overtime window closes, it collects
the observation feeds as of now and evaluates every goal of record whose credit window has closed
(``score.evaluate_all``). Each evaluation appends a revision only when its evidence changed, and writes the
team-shift's light performance row and job record (D-25), so re-running is harmless.

It needs the evaluator role's credentials. The exit code is 0 when it ran, 2 when any evaluation is UNKNOWN (missing
coverage or data), 3 when the store or the feeds are unavailable (live: until the FOCUS_FN tables, the live adapter and
the observation producers exist).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run(services: Any, at: datetime) -> dict[str, Any]:
    from focus.performance.actuals import collect_observations
    from focus.performance.score import evaluate_all

    observation = collect_observations(services, as_of=at)
    evaluations = evaluate_all(services, as_of=at)
    return {"status": "ok", "as_of": at.isoformat(), "observation_run": observation.id,
            "feeds": {f.feed: f.status for f in observation.feeds},
            "evaluations": len(evaluations), "by_status": dict(Counter(e.status.value for e in evaluations)),
            "shifts": sorted({e.label.split(" ", 1)[1] for e in evaluations if " " in e.label})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None, help="RFC3339 instant (default: the runtime clock)")
    ap.add_argument("--app-root", default=None)
    args = ap.parse_args(argv)
    from focus.codec import parse_utc
    from focus.config import Settings
    from focus.runtime import build_services

    try:
        services = build_services(Settings.from_env(**({"app_root": Path(args.app_root)} if args.app_root else {})))
        at = parse_utc(args.as_of) if args.as_of else services.clock.now()
        result = run(services, at)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a typed result, like the CLI
        result = {"status": "unavailable", "error": {"code": getattr(exc, "code", type(exc).__name__),
                                                      "message": getattr(exc, "message", str(exc))}}
    print(json.dumps(result, indent=1, default=str))
    if result["status"] != "ok":
        return 3
    return 2 if result["by_status"].get("unknown") else 0


if __name__ == "__main__":
    raise SystemExit(main())
