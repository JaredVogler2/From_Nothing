"""The calibration tool reads the store as production keeps it (tools/calibrate_scores.py ``store``).

On the observed demo world (the published 2026-10-26 plan observed at the three S18 collection points):

* one record per light performance row, with the job record's missed and excused minutes;
* today's score in the record equals the stored evaluation's score (same policy, same components);
* completion times come from the evidence while it is kept, and each shift's plan of record is checked against the
  previous shift's;
* the tool writes nothing to the store.

The evaluator-only run after a shift's overtime (tools/evaluate_closed_shifts.py) scores every closed shift once and is
harmless to repeat.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from focus.performance import history
from focus.performance import rollup as R
from tests.conftest import observed_world

SPEC = importlib.util.spec_from_file_location("calibrate_scores", Path(__file__).resolve().parents[2] / "tools" / "calibrate_scores.py")
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)

POINTS = ("2026-10-26T19:30:00Z", "2026-10-27T16:00:00Z", "2026-10-28T16:00:00Z")
DAY = date(2026, 10, 26)


@pytest.fixture(scope="module")
def world(tmp_path_factory, app_root):
    return observed_world(tmp_path_factory.mktemp("calibration") / "rt", app_root, points=POINTS)


def _files(root: Path) -> dict[str, int]:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


def test_store_records_match_the_rows_and_the_stored_scores(world):
    services = world.services
    before = _files(world.root)
    records, meta = C.from_store(services, DAY, DAY)
    rows = R.rows_between(services, DAY, DAY)
    assert len(records) == len(rows) > 0
    assert meta["source"] == "store" and meta["stale_plans"]["status"] == "ok"
    for rec, row in zip(records, rows):
        assert rec["key"] == f"{row['team']}|{row['shift_id']}"
        evaluation = history.get(services, row["evaluation_id"])
        if evaluation.score is not None:
            assert rec["r0"] == pytest.approx(float(evaluation.score))
        assert rec["completions"] is not None                       # the evidence is still kept
        assert rec["missed_unclaimed"] >= 0 and rec["D"] == pytest.approx(float(evaluation.B - evaluation.E))
    res = C.analyze(records, lead=20, bands=C.BAND_SETS)
    assert res["coverage"]["team_shifts"] == len(rows)
    json.dumps(res, default=str)                                    # the report serializes
    assert _files(world.root) == before                             # read-only


def test_the_evaluator_only_run_scores_closed_shifts_and_repeats_harmlessly(world, tmp_path, app_root):
    import shutil

    from focus.clock import FixedClock
    from focus.codec import parse_utc
    from focus.config import Settings
    from focus.data.repositories import Repositories
    from focus.data.store import FixtureStore
    from focus.runtime import build_services

    spec = importlib.util.spec_from_file_location("evaluate_closed_shifts", Path(__file__).resolve().parents[2] / "tools" / "evaluate_closed_shifts.py")
    E = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(E)
    root = tmp_path / "rt"
    shutil.copytree(world.root, root)
    at = parse_utc(POINTS[-1])
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(at)
    services = build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                              clock=clock)
    first = E.run(services, at)
    assert first["status"] == "ok" and first["evaluations"] > 0 and "DEMO:2026-10-26:S1" in first["shifts"]
    rows = R.rows_between(services, DAY, DAY)
    again = E.run(services, at)
    assert again["evaluations"] == first["evaluations"] and R.rows_between(services, DAY, DAY) == rows   # no new revision, same rows

