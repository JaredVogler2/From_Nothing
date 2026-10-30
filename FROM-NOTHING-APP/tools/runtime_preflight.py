"""Host preflight before serving or working (P23 tool; thin wrapper over `python -m focus.cli preflight`).

python tools/runtime_preflight.py [--role web|worker|signer|installer] [--probe-sql]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["preflight", *sys.argv[1:]]))
