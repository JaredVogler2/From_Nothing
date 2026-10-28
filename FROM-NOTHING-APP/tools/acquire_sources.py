"""Acquire one atomic source run (P04 tool; thin wrapper over `python -m focus.cli acquire`).

python tools/acquire_sources.py --mode fixture --scenario manufacturing_demo [--cutoff 2026-10-26T10:00:00Z]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["acquire", *sys.argv[1:]]))
