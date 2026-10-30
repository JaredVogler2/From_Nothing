"""Final acceptance over A01-A72 and C01-C39 (P24 tool; thin wrapper over `python -m focus.cli acceptance`).

python tools/final_acceptance.py [--out evidence/24] [--only-mapped] [--receipts FILE] [--pytest-arg=ARG ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["acceptance", *sys.argv[1:]]))
