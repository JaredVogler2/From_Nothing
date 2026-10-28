"""Persistent build register ``docs/execution/STATUS.json`` (S21).

``python tools/status.py init`` creates the register for all 24 stages.
``python tools/status.py show`` prints stage statuses.
Stages are updated programmatically with :func:`update_stage` from the
per-stage evidence scripts. Unknown statuses or stages are rejected.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
REGISTER = APP_ROOT / "docs" / "execution" / "STATUS.json"
MANUAL = APP_ROOT.parent / "FOCUS_FROM_NOTHING_MASTER.txt"

STATUSES = ("not_started", "in_progress", "implemented", "fixture_verified", "live_verified", "blocked_live")

STAGES: dict[str, dict[str, Any]] = {
    "01": {"title": "Scope, contracts and execution register", "depends_on": []},
    "02": {"title": "Single Flask runtime and canonical domain", "depends_on": ["01"]},
    "03": {"title": "SQL discovery and complete WIP census", "depends_on": ["01", "02"]},
    "04": {"title": "Atomic source runs and immutable planning snapshots", "depends_on": ["02", "03"]},
    "05": {"title": "SQL-owned mechanic team inference and assignment", "depends_on": ["03", "04"]},
    "06": {"title": "Certification applicability and pure named workforce", "depends_on": ["04", "05"]},
    "07": {"title": "Duration gauge, standards and SQL descriptive statistics", "depends_on": ["03", "04", "06"]},
    "08": {"title": "Per-line calendars, three shifts and CS windows", "depends_on": ["04", "07"]},
    "09": {"title": "Precedence, rework and material-release graph", "depends_on": ["04", "06", "07", "08"]},
    "10": {"title": "Quality holds, final buyoffs and phase conservation", "depends_on": ["06", "07", "08", "09"]},
    "11": {"title": "Named duty ledger, activation, travel and crew selection", "depends_on": ["06", "07", "08", "09", "10"]},
    "12": {"title": "SQL economics Registry and complete-fleet cost evaluation", "depends_on": ["04", "08", "09", "11"]},
    "13": {"title": "Rich MAX whole-WIP scheduling and economic search", "depends_on": ["09", "10", "11", "12"]},
    "14": {"title": "Incumbent defense and persistent delivery commitments", "depends_on": ["11", "12", "13"]},
    "15": {"title": "Independent nine-check and extended final validation", "depends_on": ["04", "06", "07", "08", "09", "10", "11", "12", "13", "14"]},
    "16": {"title": "Immutable publication, pre-sign readback and dispatch", "depends_on": ["14", "15"]},
    "17": {"title": "Lifecycle actuals and disruption/excusal evidence", "depends_on": ["09", "14", "16"]},
    "18": {"title": "Canonical effort scoring, gate points and progression", "depends_on": ["16", "17"]},
    "19": {"title": "Complete iOS/Classic shell and scoped API", "depends_on": ["02", "16", "18"]},
    "20": {"title": "Complete operational dashboards and audited workflows", "depends_on": ["19", "13", "14", "17", "18"]},
    "21": {"title": "Digital twin, isolated scenarios and grounded explanations", "depends_on": ["16", "18", "20"]},
    "22": {"title": "Complete-scope scaling and optional named solver", "depends_on": ["13", "14", "15", "16", "21"]},
    "23": {"title": "SQL migrations, worker orchestration and CI", "depends_on": ["04", "05", "07", "14", "16", "17", "18", "22"]},
    "24": {"title": "End-to-end acceptance, versioned review and release", "depends_on": [f"{i:02d}" for i in range(1, 24)]},
}


def manual_sha256() -> str | None:
    if not MANUAL.exists():
        return None
    return hashlib.sha256(MANUAL.read_bytes()).hexdigest()


def _blank(stage_id: str) -> dict[str, Any]:
    meta = STAGES[stage_id]
    return {
        "title": meta["title"],
        "depends_on": meta["depends_on"],
        "status": "not_started",
        "fixture_verified": False,
        "live_verified": False,
        "changed_paths": [],
        "interfaces": [],
        "case_results": [],
        "commands": [],
        "evidence": [f"evidence/{stage_id}/REPORT.md"],
        "remaining_gaps": [],
    }


def load() -> dict[str, Any]:
    if REGISTER.exists():
        return json.loads(REGISTER.read_text(encoding="utf-8"))
    return init(write=False)


def save(register: dict[str, Any]) -> None:
    REGISTER.parent.mkdir(parents=True, exist_ok=True)
    register["manual_sha256"] = manual_sha256()
    tmp = REGISTER.with_suffix(".tmp")
    tmp.write_text(json.dumps(register, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(REGISTER)


def init(write: bool = True) -> dict[str, Any]:
    register = {
        "schema_version": 1,
        "app_root": str(APP_ROOT),
        "app_root_note": "Resolved app root inside the From_Nothing repository; see docs/execution/ROOTS.md for the substitution.",
        "manual_sha256": manual_sha256(),
        "python": "3.12",
        "stages": {sid: _blank(sid) for sid in STAGES},
    }
    if REGISTER.exists():
        existing = json.loads(REGISTER.read_text(encoding="utf-8"))
        for sid, data in existing.get("stages", {}).items():
            if sid in register["stages"]:
                register["stages"][sid].update(data)
    if write:
        save(register)
    return register


def update_stage(stage_id: str, **fields: Any) -> dict[str, Any]:
    if stage_id not in STAGES:
        raise ValueError(f"unknown stage {stage_id}")
    register = load()
    entry = register["stages"].setdefault(stage_id, _blank(stage_id))
    allowed = set(_blank(stage_id)) - {"title", "depends_on"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown stage fields {sorted(unknown)}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"unknown status {fields['status']}")
    entry.update(fields)
    if entry["status"] in ("fixture_verified", "live_verified"):
        entry["fixture_verified"] = True
    save(register)
    return entry


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "show":
        register = load()
        for sid, data in register["stages"].items():
            print(f"P{sid} {data['status']:<17} {data['title']}")
        return 0
    if argv[0] == "init":
        init()
        print(f"initialized {REGISTER}")
        return 0
    print("usage: tools/status.py [init|show]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
