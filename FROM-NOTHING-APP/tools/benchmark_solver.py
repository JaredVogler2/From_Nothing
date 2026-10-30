"""Complete-scope shadow benchmark with the optional solver off and on (P22 tool).

python tools/benchmark_solver.py --sizes 51,100,150,300 --budget-seconds 600 --max-moves 2 [--solver-budget-seconds 20] [--out DIR]

Runs ``focus.cli benchmark --complete-scope`` once per solver mode (each size in its own process, so peak memory is
honest per size). With ``--out`` it writes both JSON receipts and ``SUMMARY.md``, a side-by-side table of inventory,
runtime per stage, peak memory, validation, economics, duty and the solver's typed status. Exit status is 1 when any
plan is invalid, skips a required check, loses a job or claims global optimality.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus.config import Settings  # noqa: E402
from focus.engine.max.optimizer.solver import cli_benchmark  # noqa: E402

PURPOSES = ("EXECUTABLE", "FORECAST")


def _row(n: int, purpose: str, mode: str, run: dict[str, Any]) -> str:
    plan = run["plans"][purpose]
    v = plan["validation"]
    solver = plan.get("solver") or {}
    duty = plan["duty_minutes"]
    stages = ", ".join(f"{k} {v / 1000:.1f}s" for k, v in plan["stages_ms"].items())
    status = solver.get("status", "?")
    if solver.get("probes"):
        status += " (" + "; ".join(f"{p['order']}: {'valid' if p['valid'] else 'invalid'}, {p['improved_level'] or 'not better'}, "
                                   f"{p['changed_placements']} changed" for p in solver["probes"]) + ")"
    return (f"| {n} | {purpose} | {mode} | {plan['wall_seconds']:.1f} | {stages} | {run['max_rss_mb']} | "
            f"{'yes' if v['valid'] else 'NO'} / {v['violations']} / {len(v['required_skips'])} | {plan['jobs']} | "
            f"{plan['dispositions']} | {plan['final_deferred_phases']} | {plan['unknown_cost_lines']} | {plan['known_cost_cents']} | "
            f"{plan['travel_minutes']} / {plan['wait_minutes']} / {plan['ot_minutes']} | {duty.get('WORK', 0)} | "
            f"{plan['commitment_outcomes'] or '-'} | {status} |")


def summary(results: dict[str, dict[str, Any]]) -> str:
    lines = ["# Complete-scope benchmark (synthetic scale fixtures)", "",
             "Runtimes and memory are this machine's. Every plan is a full named decode checked by the independent validator;",
             "the solver column is the optional CP-SAT stage's typed status (never a proof).", ""]
    first = next(iter(results.values()))
    lines += ["## Inventory", "", "| Aircraft | Source task rows | Jobs | Phases (by kind) | People (virtual) | Pools | Edges | Rework | Shortages | Census dispositions |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for run in first["runs"]:
        if run.get("status") != "COMPLETE":
            lines.append(f"| {run.get('aircraft')} | {run.get('status')} | | | | | | | | |")
            continue
        inv = run["inventory"]
        lines.append(f"| {run['aircraft']} | {inv['source_task_rows']} | {inv['jobs']} | {inv['phases']} {inv['phase_kinds']} | "
                     f"{inv['people']} ({inv['virtual_people']}) | {inv['pools']} | {inv['edges']} | {inv['rework_links']} | "
                     f"{inv['shortages']} | {inv['dispositions']} |")
    lines += ["", "## Plans", "",
              "| Aircraft | Purpose | Solver | Wall s | Stages | Peak RSS MB | Valid / violations / skipped checks | Jobs | Dispositions | "
              "Deferred phases | Unknown-cost aircraft | Known cost (cents) | Travel / wait / OT min | Work min | Commitments | Solver status |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    by_size: dict[int, dict[str, dict[str, Any]]] = {}
    for mode, result in results.items():
        for run in result["runs"]:
            by_size.setdefault(run.get("aircraft"), {})[mode] = run
    for n in sorted(by_size):
        for purpose in PURPOSES:
            for mode, run in by_size[n].items():
                if run.get("status") == "COMPLETE" and purpose in run.get("plans", {}):
                    lines.append(_row(n, purpose, mode, run))
    failures = [f"{mode}: {f}" for mode, r in results.items() for f in r.get("failures", [])]
    lines += ["", "## Failures", "", *(f"- {f}" for f in failures)] if failures else ["", "No failures: every plan valid, no required check skipped, no job lost."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default="51,100,150,300")
    parser.add_argument("--budget-seconds", type=float, default=600.0)
    parser.add_argument("--max-moves", type=int, default=2)
    parser.add_argument("--solver-budget-seconds", type=float, default=None)
    parser.add_argument("--modes", default="off,on", help="solver modes to run, in order")
    parser.add_argument("--out", default=None, help="directory for benchmark_solver_<mode>.json and SUMMARY.md")
    args = parser.parse_args(argv)
    services = types.SimpleNamespace(settings=Settings.from_env(None, app_root=Path(__file__).resolve().parent.parent))
    results = {}
    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        results[mode] = cli_benchmark(services, argparse.Namespace(sizes=args.sizes, budget_seconds=args.budget_seconds, max_moves=args.max_moves,
                                                                   solver=mode, solver_budget_seconds=args.solver_budget_seconds))
    rejected = {mode: r for mode, r in results.items() if "runs" not in r}
    if rejected:
        sys.stdout.write(json.dumps(rejected, indent=1, sort_keys=True) + "\n")
        return 2
    text = summary(results)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for mode, result in results.items():
            (out / f"benchmark_solver_{mode}.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (out / "SUMMARY.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if all(r["status"] == "ok" for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
