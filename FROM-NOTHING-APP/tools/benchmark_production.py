"""Production-shaped benchmark: the Python and native ledger/crew cores on the same snapshots (scale work).

python tools/benchmark_production.py --sizes 1,5 [--purposes FORECAST] [--max-moves 0] [--engines python,native] [--out DIR]

Each size runs in its own process (honest peak memory). Inside a size, every engine schedules the SAME admitted
snapshot, so the plans are compared byte for byte (candidate id and content hash). With ``--out`` it writes the JSON
receipts and ``SUMMARY.md``. Exit status is 1 when a plan is invalid, skips a required check, loses a job, claims global
optimality or when the engines disagree.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from focus.engine.max.optimizer.solver import benchmark_failures  # noqa: E402


def run_size(n: int, args: argparse.Namespace) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "focus.engine.max.optimizer.solver", "--size", str(n), "--shape", "production", "--engines", args.engines,
           "--purposes", args.purposes, "--budget-seconds", str(args.budget_seconds), "--app-root", str(APP_ROOT),
           "--shape-options", args.shape_options]
    if args.max_moves is not None:
        cmd += ["--max-moves", str(args.max_moves)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
    if done.returncode != 0:
        return {"aircraft": n, "status": "ERROR", "stderr": done.stderr[-3000:]}
    return json.loads(done.stdout)


def summary(runs: list[dict[str, Any]], args: argparse.Namespace) -> str:
    lines = [
        "# Production-shaped benchmark",
        "",
        "Synthetic fixture shaped like the program's aircraft (`focus/data/production_fixture.py`); not live proof. Every engine",
        "schedules the same admitted snapshot in one process, so the plans are compared byte for byte.",
        "",
        f"Command: `python tools/benchmark_production.py --sizes {args.sizes} --purposes {args.purposes} --engines {args.engines} "
        f"--max-moves {args.max_moves} --budget-seconds {args.budget_seconds}`",
        "",
        "| Aircraft | Jobs | Phases | Dependencies | People | Load s | Purpose | Engine | Plan s | First pass s | Re-plan s | Identical | Valid |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        if r.get("status") != "COMPLETE":
            lines.append(f"| {r.get('aircraft')} | {r.get('status')} | | | | | | | | | | | |")
            continue
        inv = r["inventory"]
        for purpose, cmp in r["engines"].items():
            for engine, e in cmp["runs"].items():
                st = e["stages_ms"]
                lines.append(f"| {r['aircraft']} | {inv['jobs']:,} | {inv['phases']:,} | {inv['edges']:,} | {inv['people']:,} | {r['acquire_seconds']} | "
                             f"{purpose} | {engine} | {e['wall_seconds']} | {st.get('neutral_seed', 0) / 1000:.1f} | "
                             f"{st.get('economic_redispatch', 0) / 1000:.1f} | {'yes' if cmp['identical'] else 'NO'} | {'yes' if e['valid'] else 'NO'} |")
    memory = ", ".join(f"{r.get('aircraft')} aircraft {r.get('max_rss_mb')}" for r in runs)
    lines += ["", f"Peak memory per size process (MB): {memory}.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default="1,5")
    parser.add_argument("--purposes", default="FORECAST")
    parser.add_argument("--engines", default="python,native")
    parser.add_argument("--max-moves", type=int, default=0)
    parser.add_argument("--budget-seconds", type=float, default=600.0)
    parser.add_argument("--shape-options", default="{}")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    runs = [run_size(int(n), args) for n in args.sizes.split(",") if n.strip()]
    failures = benchmark_failures(runs)
    report = {"status": "ok" if not failures else "error", "sizes": args.sizes, "runs": runs, "failures": failures,
              "note": "production-shaped synthetic fixtures; runtimes are this machine's; the independent validator decides validity"}
    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "benchmark_production.json").write_text(text + "\n", encoding="utf-8")
        (out / "SUMMARY.md").write_text(summary(runs, args), encoding="utf-8")
    print(text)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
