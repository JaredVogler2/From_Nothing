"""Write deterministic fixture inputs (S18).

python tools/build_fixtures.py                     # fixtures/inputs/manufacturing_demo
python tools/build_fixtures.py --scale 100 --out fixtures/inputs/scale_100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from focus.data.fixture_builder import DEMO_INCUMBENT, build_manufacturing_demo, build_scale_fixture, write_inputs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if args.scale:
        out = Path(args.out or APP_ROOT / "fixtures" / "inputs" / f"scale_{args.scale}")
        written = write_inputs(out, build_scale_fixture(args.scale))
    else:
        out = Path(args.out or APP_ROOT / "fixtures" / "inputs" / "manufacturing_demo")
        written = write_inputs(out, build_manufacturing_demo(), extra={"incumbent_plan": DEMO_INCUMBENT})
    print(f"wrote {len(written)} files to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
