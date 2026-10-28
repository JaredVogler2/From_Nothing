"""Fixture demo pipeline (S17 LOCAL CLI: ``demo-bootstrap``; ``demo-run`` arrives with P24).

``demo-bootstrap`` does three things, and never touches a live source or SQL:

1. It verifies the deterministic S18 inputs (source datasets, the previous accepted plan and the
   S14 observation feeds) against the builder, or writes them when absent. Drift is reported,
   never silently overwritten.
2. With ``--reset`` it first removes only ``<runtime>/<scenario>``.
3. It records the S18 previous accepted plan as the ACCEPTED incumbent for both purposes in
   the persistent fixture runtime. A later process therefore reloads it from the repository,
   not from memory.
"""
from __future__ import annotations

import json
import shutil
from typing import Any

from .data.fixture_builder import DEMO_INCUMBENT, build_manufacturing_demo, build_observations
from .engine.commitments import import_fixture_incumbent

DEMO_SCENARIO = "manufacturing_demo"


def _demo_files() -> dict[str, str]:
    files = {f"{name}.json": json.dumps(ds.to_json(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
             for name, ds in build_manufacturing_demo(DEMO_SCENARIO).items()}
    files["incumbent_plan.json"] = json.dumps(DEMO_INCUMBENT, indent=1, sort_keys=True) + "\n"
    for name, payload in build_observations(DEMO_SCENARIO).items():
        files[f"observations/{name}.json"] = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    return files


def cli_demo_bootstrap(services: Any, args: Any) -> dict[str, Any]:
    settings = services.settings
    scenario = getattr(args, "scenario", None) or settings.scenario_id
    if settings.mode != "fixture":
        return {"status": "invalid", "error": {"code": "FIXTURE_ONLY", "message": "demo-bootstrap runs in fixture mode only"}}
    if scenario != DEMO_SCENARIO:
        return {"status": "invalid", "error": {"code": "SCENARIO_UNKNOWN", "message": f"no deterministic builder for {scenario!r}"}}
    runtime = settings.runtime_dir(scenario)
    if getattr(args, "reset", False) and runtime.is_dir():
        shutil.rmtree(runtime)
    target = settings.fixture_inputs_dir / scenario
    expected = _demo_files()
    written, drift = [], []
    for name, text in sorted(expected.items()):
        path = target / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(name)
        elif path.read_text(encoding="utf-8") != text:
            drift.append(name)
    if drift:
        return {"status": "invalid", "error": {"code": "FIXTURE_DRIFT", "message": f"inputs differ from the deterministic builder: {drift}"}}
    incumbents = {purpose: import_fixture_incumbent(services, scenario, purpose).id for purpose in ("EXECUTABLE", "FORECAST")}
    return {"status": "ok", "scenario": scenario, "inputs": {"verified": len(expected) - len(written), "written": written},
            "runtime_dir": str(runtime), "incumbents": incumbents, "policy": services.policy.id}
