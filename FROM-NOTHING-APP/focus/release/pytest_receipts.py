"""pytest plugin for the acceptance runner: one outcome receipt per collected test node (P24).

Loaded only by ``focus.release.acceptance`` (``-p focus.release.pytest_receipts --focus-receipts <file>``). It needs
no pytest import of its own, so importing it at runtime stays side-effect free. A node's outcome is ``passed`` only
when setup, call and teardown all passed; a skip is recorded with its reason, because a required case that was
skipped blocks an executable release.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STATE: dict[str, Any] = {"path": None, "results": {}}


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--focus-receipts", default=None, help="write one outcome per test node to this JSON file")


def pytest_configure(config: Any) -> None:
    _STATE["path"] = config.getoption("--focus-receipts")
    _STATE["results"] = {}


def pytest_runtest_logreport(report: Any) -> None:
    if _STATE["path"] is None:
        return
    entry = _STATE["results"].setdefault(report.nodeid, {"outcome": "passed", "duration": 0.0, "phases": {}})
    entry["duration"] = round(entry["duration"] + float(report.duration), 6)
    entry["phases"][report.when] = report.outcome
    if getattr(report, "wasxfail", None) is not None:
        entry["outcome"] = "xfailed"
    elif report.outcome == "failed":
        entry["outcome"] = "failed" if report.when == "call" else "error"
    elif report.outcome == "skipped" and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"
        longrepr = report.longrepr
        entry["reason"] = str(longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else longrepr)[:500]


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    if _STATE["path"] is None:
        return
    payload = {"exitstatus": int(exitstatus), "collected": int(getattr(session, "testscollected", 0)), "results": _STATE["results"]}
    Path(_STATE["path"]).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
