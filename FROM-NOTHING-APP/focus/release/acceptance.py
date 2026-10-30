"""The P24 acceptance run: every A01-A72 case and C01-C39 requirement, executed and traced (S19, S20, S21).

``tests/acceptance/map.json`` is the register. For each case it names the tests that assert the case's observable
outcome. For each requirement it names the rule authorities, the caller paths that enforce the rule, the files the
rule depends on and its own tests. It also lists the counterexamples P24 calls out and the P24 verification themes.

The runner executes the suite once, in a separate process, with one receipt per test node, and judges every entry
from those receipts:

* ``PASS``: every named test ran and passed;
* ``FAIL``: a named test failed or errored;
* ``BLOCKED_SKIP``: a named test was skipped (a required skip blocks an executable release);
* ``NOT_RUN``: a named test was never collected.

A requirement also needs every caller path to hold: the caller exists and calls the callee by name, and a module-level
callee is the very object that the caller's module, or an import inside the caller, binds to that name. A path that no
longer holds fails the requirement even when its tests pass. Any failing test anywhere in the run, mapped or not,
fails the local acceptance.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import inspect
import json
import subprocess
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..codec import strict_loads
from .hashes import build_hashes

MAP_PATH = Path("tests") / "acceptance" / "map.json"
CASES = tuple(f"A{i:02d}" for i in range(1, 73))
CONSTRAINTS = tuple(f"C{i:02d}" for i in range(1, 40))
COUNTEREXAMPLES = ("in_progress_edge", "activation_repair", "rework_p75", "same_run_cs_readback", "oos_double_charge")
PASS, FAIL, BLOCKED_SKIP, NOT_RUN = "PASS", "FAIL", "BLOCKED_SKIP", "NOT_RUN"


def load_map(app_root: Path) -> dict[str, Any]:
    return strict_loads((app_root / MAP_PATH).read_bytes())


def map_problems(m: dict[str, Any]) -> list[str]:
    """Structural gaps in the register itself: a missing case or requirement, an entry without tests."""
    problems = []
    if tuple(sorted(m.get("cases", {}))) != CASES:
        problems.append(f"cases must be exactly A01-A72, got {sorted(set(CASES) ^ set(m.get('cases', {})))} different")
    if tuple(sorted(m.get("constraints", {}))) != CONSTRAINTS:
        problems.append(f"constraints must be exactly C01-C39, got {sorted(set(CONSTRAINTS) ^ set(m.get('constraints', {})))} different")
    if tuple(sorted(m.get("counterexamples", {}))) != tuple(sorted(COUNTEREXAMPLES)):
        problems.append("counterexamples must be exactly " + ", ".join(COUNTEREXAMPLES))
    for group in ("cases", "counterexamples", "verification"):
        for key, entry in m.get(group, {}).items():
            if not entry.get("tests"):
                problems.append(f"{group}.{key} names no test")
    for key, entry in m.get("constraints", {}).items():
        if not entry.get("rule") or not entry.get("paths"):
            problems.append(f"constraints.{key} needs rule authorities and caller paths")
        unknown = [c for c in entry.get("cases", []) if c not in CASES]
        if unknown:
            problems.append(f"constraints.{key} names unknown cases {unknown}")
    return problems


# ---------------------------------------------------------------------- static path receipts

def resolve(target: str) -> Any:
    """``package.module:Qual.name`` -> the object; LookupError with the reason otherwise."""
    module_name, _, qual = target.partition(":")
    try:
        obj: Any = importlib.import_module(module_name)
    except ImportError as exc:
        raise LookupError(f"cannot import {module_name}: {exc}") from None
    for part in filter(None, qual.split(".")):
        if not hasattr(obj, part):
            raise LookupError(f"{module_name} has no {qual}")
        obj = getattr(obj, part)
    return obj


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def check_path(caller: str, callee: str) -> str | None:
    """None when ``caller`` really calls ``callee``; otherwise why not."""
    try:
        fn = resolve(caller)
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (LookupError, OSError, TypeError, SyntaxError) as exc:
        return f"caller {caller}: {exc}"
    module_name, _, qual = callee.partition(":")
    short = qual.rsplit(".", 1)[-1]
    if short not in _called_names(tree):
        return f"{caller} does not call {short}"
    try:
        target = resolve(callee)
    except LookupError as exc:
        return f"callee {callee}: {exc}"
    if "." in qual:
        return None                                       # a method: called on an instance, matched by name
    caller_module = sys.modules[fn.__module__]
    if getattr(caller_module, short, None) is target:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):              # an import inside the caller
            base = importlib.util.resolve_name("." * node.level + (node.module or ""), caller_module.__package__) if node.level else node.module
            if not base:
                continue
            for alias in node.names:
                if (alias.asname or alias.name) == short and getattr(importlib.import_module(base), alias.name, None) is target:
                    return None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == short
                and isinstance(node.func.value, ast.Name)):  # module.function(...)
            owner = getattr(caller_module, node.func.value.id, None)
            if inspect.ismodule(owner) and getattr(owner, short, None) is target:
                return None
    return f"{caller} calls a {short} that is not {callee}"


# ---------------------------------------------------------------------- judging receipts

def _matching(ref: str, results: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(node, entry) for node, entry in results.items() if node == ref or node.startswith(ref + "[")]


def judge_tests(tests: Iterable[str], results: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    rows = []
    for ref in tests:
        found = _matching(ref, results)
        outcomes = Counter(entry["outcome"] for _, entry in found)
        if not found:
            status = NOT_RUN
        elif outcomes.keys() - {"passed", "skipped"}:
            status = FAIL
        elif outcomes.get("skipped"):
            status = BLOCKED_SKIP
        else:
            status = PASS
        rows.append({"test": ref, "status": status, "nodes": len(found), "outcomes": dict(sorted(outcomes.items())),
                     **({"skip_reasons": sorted({e.get("reason", "") for _, e in found if e["outcome"] == "skipped"})} if outcomes.get("skipped") else {})})
    return _worst(row["status"] for row in rows), rows


def _worst(statuses: Iterable[str]) -> str:
    seen = set(statuses)
    for status in (FAIL, NOT_RUN, BLOCKED_SKIP):
        if status in seen:
            return status
    return PASS if seen else NOT_RUN


def evaluate(m: dict[str, Any], receipts: dict[str, Any], app_root: Path) -> dict[str, Any]:
    results = receipts.get("results", {})
    cases = {}
    for cid in CASES:
        entry = m["cases"].get(cid, {})
        status, rows = judge_tests(entry.get("tests", []), results)
        cases[cid] = {"stage": entry.get("stage"), "summary": entry.get("summary"), "status": status, "tests": rows}
    constraints = {}
    for cid in CONSTRAINTS:
        entry = m["constraints"].get(cid, {})
        rules = [{"rule": r, "problem": None if _resolves(r) else f"{r} does not resolve"} for r in entry.get("rule", [])]
        paths = [{"caller": c, "callee": t, "problem": check_path(c, t)} for c, t in entry.get("paths", [])]
        files = [{"file": f, "problem": None if (app_root / f).is_file() else "missing"} for f in entry.get("files", [])]
        own_status, rows = judge_tests(entry.get("tests", []), results)
        statuses = [cases[c]["status"] for c in entry.get("cases", []) if c in cases]
        if entry.get("tests"):
            statuses.append(own_status)
        if any(x["problem"] for x in rules + paths + files):
            statuses.append(FAIL)
        constraints[cid] = {"requirement": entry.get("requirement"), "stages": entry.get("stages"), "cases": entry.get("cases"),
                            "status": _worst(statuses), "rules": rules, "paths": paths, "files": files, "tests": rows}
    grouped = {}
    for group in ("counterexamples", "verification"):
        grouped[group] = {}
        for key, entry in m.get(group, {}).items():
            status, rows = judge_tests(entry.get("tests", []), results)
            grouped[group][key] = {"title": entry.get("title"), "refs": entry.get("refs", []), "status": status, "tests": rows}
    mapped = {ref for entry in [*m["cases"].values(), *m["constraints"].values(), *m.get("counterexamples", {}).values(),
                                *m.get("verification", {}).values()] for ref in entry.get("tests", [])}
    outcomes = Counter(entry["outcome"] for entry in results.values())
    unmapped_failures = sorted(node for node, entry in results.items() if entry["outcome"] in ("failed", "error", "xfailed")
                               and not any(node == ref or node.startswith(ref + "[") for ref in mapped))
    required_skips = sorted(node for node, entry in results.items() if entry["outcome"] == "skipped"
                            and any(node == ref or node.startswith(ref + "[") for ref in mapped))
    problems = map_problems(m)
    every = [x["status"] for x in [*cases.values(), *constraints.values(), *grouped["counterexamples"].values(), *grouped["verification"].values()]]
    clean_run = receipts.get("exitstatus", 0) == 0 and not (outcomes.keys() & {"failed", "error"})
    local = PASS if not problems and not unmapped_failures and all(s == PASS for s in every) and clean_run else FAIL
    summary = {
        "local_acceptance": local,
        "cases": {"total": len(CASES), "passed": sum(c["status"] == PASS for c in cases.values()),
                  "statuses": dict(Counter(c["status"] for c in cases.values()))},
        "constraints": {"total": len(CONSTRAINTS), "passed": sum(c["status"] == PASS for c in constraints.values()),
                        "statuses": dict(Counter(c["status"] for c in constraints.values())),
                        "paths_verified": sum(1 for c in constraints.values() for p in c["paths"] if p["problem"] is None),
                        "paths_total": sum(len(c["paths"]) for c in constraints.values())},
        "counterexamples": {k: v["status"] for k, v in grouped["counterexamples"].items()},
        "verification": {k: v["status"] for k, v in grouped["verification"].items()},
        "tests": {"collected": receipts.get("collected"), "exitstatus": receipts.get("exitstatus"), **dict(sorted(outcomes.items()))},
        "unmapped_failures": unmapped_failures,
        "required_skips": required_skips,
        "map_problems": problems,
    }
    return {"summary": summary, "cases": cases, "constraints": constraints, **grouped}


def _resolves(target: str) -> bool:
    try:
        resolve(target)
    except LookupError:
        return False
    return True


# ---------------------------------------------------------------------- running and reporting

def mapped_targets(m: dict[str, Any]) -> list[str]:
    """The smallest node list that runs every mapped test once (a whole test covers its parametrized ids)."""
    refs = {ref for group in ("cases", "constraints", "counterexamples", "verification") for entry in m.get(group, {}).values()
            for ref in entry.get("tests", [])}
    return sorted(ref for ref in refs if "[" not in ref or ref.split("[", 1)[0] not in refs)


def run_pytest(app_root: Path, out_dir: Path, targets: list[str], extra: Iterable[str] = ()) -> dict[str, Any]:
    receipts_path = out_dir / "receipts.json"
    receipts_path.unlink(missing_ok=True)
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "focus.release.pytest_receipts",
               f"--focus-receipts={receipts_path}", *targets, *extra]
    started = time.monotonic()
    with (out_dir / "pytest.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, cwd=app_root, stdout=log, stderr=subprocess.STDOUT, check=False)
    receipts = json.loads(receipts_path.read_text(encoding="utf-8")) if receipts_path.is_file() else {"results": {}, "collected": 0}
    receipts["returncode"] = completed.returncode
    receipts["wall_seconds"] = round(time.monotonic() - started, 1)
    receipts["command"] = ["python", "-m", "pytest", *(a if not a.startswith("--focus-receipts=") else "--focus-receipts=<out>/receipts.json" for a in command[3:])]
    return receipts


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# P24 acceptance run",
        "",
        f"Local acceptance: **{s['local_acceptance']}**. Cases {s['cases']['passed']}/{s['cases']['total']} pass; "
        f"requirements {s['constraints']['passed']}/{s['constraints']['total']} pass with "
        f"{s['constraints']['paths_verified']}/{s['constraints']['paths_total']} caller paths verified.",
        "",
        f"Tests: {json.dumps(s['tests'], sort_keys=True)}. Python {report['run']['python']}. "
        f"Implementation `{report['hashes']['implementation']['sha256'][:16]}`, tests `{report['hashes']['tests']['sha256'][:16]}`.",
        "",
        "This is fixture evidence. Live verification is a separate capability (see FINAL_ACCEPTANCE.md).",
        "",
        "## Cases (S19)",
        "",
        "| Case | Stage | Result | Tests | Outcome asserted |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cid, c in report["cases"].items():
        passed = sum(r["status"] == PASS for r in c["tests"])
        lines.append(f"| {cid} | {c['stage']} | {c['status']} | {passed}/{len(c['tests'])} | {c['summary']} |")
    lines += ["", "## Requirements (S20)", "", "| ID | Requirement | Stages | Cases | Paths | Result |", "| --- | --- | --- | --- | --- | --- |"]
    for cid, c in report["constraints"].items():
        ok = sum(p["problem"] is None for p in c["paths"])
        lines.append(f"| {cid} | {c['requirement']} | {', '.join(c['stages'] or [])} | {', '.join(c['cases'] or [])} | {ok}/{len(c['paths'])} | {c['status']} |")
    for group, title in (("counterexamples", "Named counterexamples (P24 step 2)"), ("verification", "Verification themes (P24 step 3)")):
        lines += ["", f"## {title}", "", "| Item | Result | Tests |", "| --- | --- | --- |"]
        for key, item in report[group].items():
            lines.append(f"| {item['title'] or key} | {item['status']} | {sum(r['status'] == PASS for r in item['tests'])}/{len(item['tests'])} |")
    problems = [f"- {cid}: {p['caller']} -> {p['callee']}: {p['problem']}" for cid, c in report["constraints"].items() for p in c["paths"] if p["problem"]]
    problems += [f"- unmapped failure: {n}" for n in s["unmapped_failures"]] + [f"- required skip: {n}" for n in s["required_skips"]]
    problems += [f"- map: {p}" for p in s["map_problems"]]
    lines += ["", "## Problems", ""] + (problems or ["None."])
    return "\n".join(lines) + "\n"


def run(app_root: Path, out_dir: Path, *, only_mapped: bool = False, receipts_file: Path | None = None,
        extra: Iterable[str] = ()) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = load_map(app_root)
    before = build_hashes(app_root)
    if receipts_file is not None:
        receipts = json.loads(receipts_file.read_text(encoding="utf-8"))
    else:
        receipts = run_pytest(app_root, out_dir, mapped_targets(m) if only_mapped else ["tests"], extra)
    after = build_hashes(app_root)
    report = evaluate(m, receipts, app_root)
    report["hashes"] = before
    report["run"] = {"python": sys.version.split()[0], "scope": "mapped tests" if only_mapped else "full suite",
                     "wall_seconds": receipts.get("wall_seconds"), "command": receipts.get("command"),
                     "returncode": receipts.get("returncode"), "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "map_sha256": _sha256(app_root / MAP_PATH)}
    if any(before[k]["sha256"] != after[k]["sha256"] for k in ("implementation", "tests")):
        report["summary"]["local_acceptance"] = FAIL
        report["summary"]["map_problems"].append("implementation or tests changed during the run: the receipts bind no single build")
    (out_dir / "acceptance.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "ACCEPTANCE.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def cli_acceptance(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    app_root = services.settings.app_root
    out_dir = Path(args.out) if Path(args.out).is_absolute() else app_root / args.out
    report = run(app_root, out_dir, only_mapped=args.only_mapped, receipts_file=Path(args.receipts) if args.receipts else None,
                 extra=args.pytest_arg or ())
    s = report["summary"]
    return {"status": "ok" if s["local_acceptance"] == PASS else "error", "summary": s, "out": str(out_dir),
            "implementation_sha256": report["hashes"]["implementation"]["sha256"], "tests_sha256": report["hashes"]["tests"]["sha256"]}
