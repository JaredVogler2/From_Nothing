"""The comparison docs/integration/OBJECTIVE_PARITY_PROMPT.md (step 6) tells a rebuild to make is one that holds.

Two builds are compared by letting each schedule the same admitted source run in its own copy of the store. That must
give the same plan every time, or a difference would say nothing about the other build. Plans of two separate
acquisitions are not compared that way: a pool's id is derived from its source run, and a few reason texts name a pool.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.codec import to_jsonable
from focus.config import Settings
from focus.data.acquisition import acquire, make_reader
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import Purpose
from focus.engine.commitments import import_fixture_incumbent
from focus.engine.max.optimizer.orchestrator import RUN_REFERENCES, decision_digest, receipt, schedule
from focus.runtime import build_services

POOL_ID = re.compile(r"\bQ[0-9a-f]{20}\b")
PURPOSES = (Purpose.EXECUTABLE, Purpose.FORECAST)


def _services(root: Path, app_root: Path):
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock)
    return build_services(settings, repositories=repos, clock=clock)


def _plan(services, run_id: str, purpose: Purpose):
    plan, run = schedule(services, source_run=run_id, purpose=purpose, budget_seconds=600, max_moves=15)
    assert run.stop_reason == "move_bound" and run.validation.valid, (purpose, run.stop_reason)   # a repeatable stop, a valid plan
    return plan


def _decisions(plan) -> dict:
    """What the decision digest hashes, as data: the plan's decisions without the references every acquisition mints."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in RUN_REFERENCES}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return strip(to_jsonable({"assignments": plan.assignments, "duties": plan.duties, "dispositions": plan.job_dispositions,
                              "completions": plan.aircraft_completion, "costs": plan.costs, "commitments": plan.commitment_results}))


def _differences(a, b, path: str = "$") -> list[tuple[str, object, object]]:
    if type(a) is not type(b):
        return [(path, a, b)]
    if isinstance(a, dict):
        return [d for k in sorted(set(a) | set(b)) for d in (_differences(a[k], b[k], f"{path}.{k}") if k in a and k in b
                                                             else [(f"{path}.{k}", a.get(k), b.get(k))])]
    if isinstance(a, list):
        if len(a) != len(b):
            return [(path, len(a), len(b))]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _differences(x, y, f"{path}[{i}]")]
    return [] if a == b else [(path, a, b)]


def test_one_admitted_run_scheduled_in_two_copies_of_a_store_gives_the_same_plan(tmp_path, app_root) -> None:
    reference = _services(tmp_path / "reference", app_root)
    import_fixture_incumbent(reference)                                # an accepted plan to defend, as after demo-bootstrap
    run = acquire(reference, make_reader(reference, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="parity-1")
    assert run.status == "COMPLETE", run.rejection
    shutil.copytree(tmp_path / "reference", tmp_path / "rebuild")      # the store as it is after the one acquisition
    rebuild = _services(tmp_path / "rebuild", app_root)
    for purpose in PURPOSES:
        ours, theirs = _plan(reference, run.run_id, purpose), _plan(rebuild, run.run_id, purpose)
        assert ours.commitment_results and any(c.outcome in ("KEPT", "CREW_CHANGED") for c in ours.commitment_results)   # commitments are in play
        assert (ours.id, decision_digest(ours)) == (theirs.id, decision_digest(theirs)), purpose
        mine, yours = receipt(ours), receipt(theirs)
        for field in ("candidate_id", "decision_digest", "costs", "objective_status"):       # the fields the page compares
            assert mine[field] == yours[field], (purpose, field)
        assert mine["algorithm"]["weights"] == yours["algorithm"]["weights"]
        stages = lambda r: [(s["stage"], s["candidates"], s["accepted"]) for s in r["algorithm"]["stages"]]   # noqa: E731
        assert stages(mine) == stages(yours) and mine["algorithm"]["baseline_comparison"]["stop_reason"] == "move_bound"


def test_two_acquisitions_decide_the_same_and_differ_only_in_reason_texts_that_name_a_pool(tmp_path, app_root) -> None:
    """Why the page says "acquire once": the same data acquired twice gives two source runs and so two sets of pool ids."""
    services = _services(tmp_path / "store", app_root)
    import_fixture_incumbent(services)
    runs = [acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key=key) for key in ("a", "b")]
    assert runs[0].run_id != runs[1].run_id and all(r.status == "COMPLETE" for r in runs)
    for purpose in PURPOSES:
        first, second = (_decisions(_plan(services, r.run_id, purpose)) for r in runs)
        assert first["assignments"] == second["assignments"] and first["costs"] == second["costs"], purpose   # the same decisions
        assert first["completions"] == second["completions"] and first["duties"] == second["duties"], purpose
        for path, a, b in _differences(first, second):
            assert isinstance(a, str) and isinstance(b, str) and POOL_ID.search(a), (purpose, path, a, b)
            assert POOL_ID.sub("Q", a) == POOL_ID.sub("Q", b), (purpose, path)              # the same text but for the pool's id
