"""The same inputs give the same plan (repeatability).

The demo source is acquired and scheduled, search moves included, in two separate processes with different string-hash
seeds (``PYTHONHASHSEED`` changes set and dict iteration of strings between processes). Both must decide exactly the
same thing: the same ``decision_digest`` (assignments, people, times, dispositions with reasons, completions, costs,
commitments). Each acquisition is its own source run, so run-minted references and stage timings differ by design and
are not part of that digest. The run must also say it stopped on its move bound, the only stop that keeps a run
repeatable (a wall-clock deadline depends on machine speed).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = r"""
import json, sys, tempfile
from pathlib import Path
app_root = Path(sys.argv[1])
sys.path.insert(0, str(app_root))
from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.data.acquisition import load_snapshot
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import Purpose
from focus.engine.max.optimizer.orchestrator import decision_digest, schedule_snapshot
from focus.runtime import build_services
from tests.conftest import acquire_demo

clock = FixedClock(FIXTURE_CUTOFF)
settings = Settings.from_env({"FOCUS_NATIVE_CORE": sys.argv[2]}, app_root=app_root, inprocess_worker=False)
repos = Repositories(FixtureStore(Path(tempfile.mkdtemp())), mode="fixture", scope_key=settings.scope_key, clock=clock)
services = build_services(settings, repositories=repos, clock=clock)
snapshot = load_snapshot(repos, acquire_demo(services, key="repeatable").snapshot_id)
out = {}
for purpose in (Purpose.EXECUTABLE, Purpose.FORECAST):
    plan, run = schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=600, max_moves=15)
    again, _ = schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=600, max_moves=15)
    assert decision_digest(again) == decision_digest(plan) and again.assignments == plan.assignments
    out[purpose.value] = [decision_digest(plan), len(plan.assignments), run.stop_reason, run.moves,
                          plan.algorithm_status.baseline_comparison["repeatable"]]
print(json.dumps(out, sort_keys=True))
"""


def _schedule(app_root: Path, hash_seed: str, native: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOCUS_")}
    env["PYTHONHASHSEED"] = hash_seed
    done = subprocess.run([sys.executable, "-c", SCRIPT, str(app_root), native], env=env, cwd=app_root, capture_output=True, text=True,
                          timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_same_inputs_give_the_same_plan_in_separate_processes(app_root: Path) -> None:
    first = _schedule(app_root, "1", "false")
    second = _schedule(app_root, "4242", "false")
    assert first == second
    for digest, assignments, stop_reason, moves, repeatable in first.values():
        assert assignments > 0 and stop_reason == "move_bound" and moves == 15 and repeatable is True


def test_the_native_core_gives_the_same_plan_in_another_process(app_root: Path) -> None:
    from focus.engine.native import available

    if not available():
        import pytest

        pytest.skip("the native core is not installed")
    assert _schedule(app_root, "7", "true") == _schedule(app_root, "99", "false")
