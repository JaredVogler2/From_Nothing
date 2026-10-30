"""Versioned FOCUS_FN migrations (P23 tool; thin wrapper over `python -m focus.cli migrate`).

python tools/migrate.py --dry-run [--catalog fixture|live|PATH] [--apply --installer NAME] [--rollback N] [--emit-dir DIR] [--write-generated]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["migrate", *sys.argv[1:]]))
