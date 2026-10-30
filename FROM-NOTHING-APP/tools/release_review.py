"""Versioned release review (P24 tool; thin wrapper over `python -m focus.cli review`).

python tools/release_review.py [--acceptance evidence/24/acceptance.json] [--reviewer NAME] [--kind IMPLEMENTER_SELF_CHECK|INDEPENDENT_REVIEW] | --list | --verify NNN
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["review", *sys.argv[1:]]))
