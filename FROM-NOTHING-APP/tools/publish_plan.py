"""Publish a validated candidate through the canonical acceptance protocol (P16 tool; wraps ``python -m focus.cli publish``).

python tools/publish_plan.py --candidate CAND_ID [--mode fixture] [--app-root PATH]

Fixture mode rehearses the complete order (final, detached and readback validation, private stage, exact
readback, TEST-key signature, verification, immutable acceptance, head last) and labels the receipt
``fixture_readback``. Live mode runs the local rehearsal and stops UNAVAILABLE: the proposed SQL stage is not
installed, so nothing is signed or accepted. Re-running after any crash reconciles the same receipt once.
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
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--app-root", default=None)
    args = parser.parse_args()
    argv = (["--app-root", args.app_root] if args.app_root else []) + ["publish", "--candidate", args.candidate, "--mode", args.mode,
                                                                       "--rehearse-readback"]
    raise SystemExit(main(argv))
