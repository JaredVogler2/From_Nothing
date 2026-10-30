"""Non-destructive retention plan and archive (P23 tool; thin wrapper over `python -m focus.cli retain-history`).

python tools/retain_history.py --keep-days N [--legal-hold holds.json] [--apply]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["retain-history", *sys.argv[1:]]))
