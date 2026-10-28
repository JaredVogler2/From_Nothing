"""Import-safety checker (P02, A02).

Checks, in a fresh interpreter:

1. every ``focus`` module imports with optional packages (ortools, playwright,
   pymssql, any LLM SDK) *blocked*, so optional dependencies are lazy;
2. the imported ``focus`` package is the local one under the app root (no
   sibling application or donor package is picked up);
3. importing modules writes nothing under ``fixtures/runtime``;
4. no module manipulates ``sys.path`` or imports absolute sibling paths.

Usage: ``python tools/check_imports.py`` (exit 0 when clean). Prints a JSON report.
"""
from __future__ import annotations

import json
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
BLOCKED = ("ortools", "playwright", "pymssql", "openai", "anthropic", "requests_oauthlib")

PROBE = r"""
import importlib, importlib.abc, json, pkgutil, sys
from pathlib import Path
BLOCKED = %(blocked)r
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in BLOCKED:
            raise ImportError(f'optional package {name} blocked by check_imports')
        return None
sys.meta_path.insert(0, _Block())
sys.path.insert(0, %(root)r)
import focus
report = {"focus_file": focus.__file__, "modules": [], "failures": []}
for info in pkgutil.walk_packages(focus.__path__, prefix="focus."):
    try:
        importlib.import_module(info.name)
        report["modules"].append(info.name)
    except Exception as exc:
        report["failures"].append({"module": info.name, "error": f"{type(exc).__name__}: {exc}"})
import app  # root WSGI entrypoint
report["loaded_optional"] = sorted({m.split('.')[0] for m in sys.modules if m.split('.')[0] in BLOCKED})
print(json.dumps(report))
"""


def run() -> dict:
    runtime = APP_ROOT / "fixtures" / "runtime" / "check_imports_probe"
    code = PROBE % {"blocked": BLOCKED, "root": str(APP_ROOT)}
    env = {"FOCUS_SCENARIO_ID": "check_imports_probe", "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(APP_ROOT), env=env)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr[-4000:]}
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    focus_file = Path(report["focus_file"]).resolve()
    report["local_package"] = str(focus_file).startswith(str(APP_ROOT / "focus"))
    report["runtime_written"] = runtime.exists()
    offenders = []
    for path in (APP_ROOT / "focus").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"sys\.path\.(insert|append)|sys\.path\s*=", text):
            offenders.append(str(path.relative_to(APP_ROOT)))
        if re.search(r"PycharmProjects|[\\/]FOCUS[\\/]FROM-NOTHING|[\\/]From_Nothing[\\/]", text):
            offenders.append(str(path.relative_to(APP_ROOT)) + " (absolute sibling path)")
    report["sys_path_offenders"] = offenders
    report["ok"] = (
        not report["failures"]
        and not report["loaded_optional"]
        and report["local_package"]
        and not report["runtime_written"]
        and not offenders
    )
    return report


def main() -> int:
    report = run()
    summary = {k: v for k, v in report.items() if k != "modules"}
    summary["module_count"] = len(report.get("modules", []))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
