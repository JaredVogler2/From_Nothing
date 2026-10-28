"""Independently validate a persisted candidate plan (P15 tool; wraps ``python -m focus.cli validate``).

python tools/validate_plan.py --candidate CAND_ID [--app-root PATH]

Every required check (V1-V9 and the extended checks) always runs against the candidate's own
sealed source snapshot. The receipt is persisted in the ``validations`` collection. The exit code
is 0 only for a valid plan with zero violations and zero required skips, and 2 for an invalid one.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--app-root", default=None)
    args = parser.parse_args()
    argv = (["--app-root", args.app_root] if args.app_root else []) + ["validate", "--candidate", args.candidate, "--all-required"]
    raise SystemExit(main(argv))
