"""A source run ends REJECTED or COMPLETE with a coded reason, never stuck in CAPTURING (S05 ATOMIC ACQUISITION).

* A database or connection error on a live query is a failed capture: an optional dataset is FAILED, a required one
  rejects the run (MANDATORY_DATASET_FAILED).
* A value admission cannot read (no offset, null, not a number) rejects the run (SOURCE_VALUE_INVALID).
* A required dataset with no rows and no certified-empty receipt rejects the run (MANDATORY_DATASET_EMPTY); a
  certified-empty one is admitted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from focus.data.acquisition import get_run
from focus.data.datasets import DATASETS, REQUIRED
from focus.data.fixture_reader import CaptureError, FixtureSourceReader, LiveSourceReader
from tests.conftest import acquire_demo

APP = Path(__file__).resolve().parents[2]
INPUTS = APP / "fixtures" / "inputs" / "manufacturing_demo"


def _doc(name: str) -> dict:
    return json.loads((INPUTS / f"{name}.json").read_text())


class _FailingSql:
    """Stands in for the SQL Server reader: the driver raises its own error type, as pymssql does."""

    class DriverError(Exception):
        pass

    def fetch(self, name, sql, params=()):
        raise self.DriverError(f"Invalid object name for {name} (208)")


class _OneLiveDataset:
    """Fixture captures, except one dataset read through the live reader."""

    kind = "fixture"

    def __init__(self, live_dataset: str):
        self.fixture = FixtureSourceReader(INPUTS)
        self.live = LiveSourceReader(APP, _FailingSql(), scope_key="DEMO-787")
        self.live_dataset = live_dataset

    def capture(self, spec, cutoff, tz_map, captured_at):
        reader = self.live if spec.name == self.live_dataset else self.fixture
        return reader.capture(spec, cutoff, tz_map, captured_at)


def test_live_reader_turns_a_driver_error_into_a_failed_capture() -> None:
    reader = LiveSourceReader(APP, _FailingSql(), scope_key="DEMO-787")
    for name in ("econ_term", "team_inference"):                # a SOURCE query and a supplemental SQL path
        with pytest.raises(CaptureError) as caught:
            reader.capture(DATASETS[name], None, {}, None)
        assert caught.value.code == "SOURCE_QUERY_FAILED" and "DriverError" in caught.value.message


def test_a_database_error_on_an_optional_dataset_marks_only_that_dataset_failed(services_factory) -> None:
    services = services_factory()
    outcome = acquire_demo(services, key="db-error-optional", reader=_OneLiveDataset("econ_term"))
    assert outcome.status == "COMPLETE", outcome.rejection
    assert outcome.datasets["econ_term"] == "FAILED"


def test_a_database_error_on_a_required_dataset_rejects_the_run(services_factory) -> None:
    services = services_factory()
    outcome = acquire_demo(services, key="db-error-required", reader=_OneLiveDataset("pnet_dag"))
    assert outcome.status == "REJECTED"
    assert outcome.rejection["code"] == "MANDATORY_DATASET_FAILED" and outcome.rejection["dataset"] == "pnet_dag"
    assert get_run(services.repositories, outcome.run_id)[0].status == "REJECTED"


@pytest.mark.parametrize("dataset, column, value", [
    ("cs_windows", "LAST_MODIFIED_DT", None),
    ("work_progress", "PERFORMED_ELAPSED_MINUTES", None),
    ("holding_events", "EFFECTIVE_AT_UTC", "2026-10-20T08:00:00"),       # a _UTC value without an offset
    ("inspection_holds", "AFTER_WORK_MINUTES", "abc"),
])
def test_an_unreadable_value_rejects_the_run_instead_of_leaving_it_capturing(services_factory, dataset, column, value) -> None:
    doc = _doc(dataset)
    doc["rows"][0][column] = value
    services = services_factory()
    outcome = acquire_demo(services, key=f"bad-{dataset}", reader=FixtureSourceReader(INPUTS, overrides={dataset: doc}))
    assert outcome.status == "REJECTED" and outcome.rejection["code"] == "SOURCE_VALUE_INVALID", outcome.rejection
    assert get_run(services.repositories, outcome.run_id)[0].status == "REJECTED"


@pytest.mark.parametrize("dataset", [name for name in REQUIRED if name != "master_task_list"])
def test_an_empty_required_dataset_without_a_certificate_rejects_the_run(services_factory, dataset) -> None:
    doc = _doc(dataset)
    doc["rows"], doc["receipt"] = [], {**doc["receipt"], "certified_empty": False}
    services = services_factory()
    outcome = acquire_demo(services, key=f"empty-{dataset}", reader=FixtureSourceReader(INPUTS, overrides={dataset: doc}))
    assert outcome.status == "REJECTED"
    assert outcome.rejection["code"] == "MANDATORY_DATASET_EMPTY" and dataset in outcome.rejection["detail"]


def test_a_certified_empty_required_dataset_is_admitted(services_factory) -> None:
    doc = _doc("neo_graph_rework")
    doc["rows"], doc["receipt"] = [], {**doc["receipt"], "certified_empty": True}
    services = services_factory()
    outcome = acquire_demo(services, key="certified-empty", reader=FixtureSourceReader(INPUTS, overrides={"neo_graph_rework": doc}))
    assert outcome.status == "COMPLETE", outcome.rejection
    assert outcome.datasets["neo_graph_rework"] == "CERTIFIED_EMPTY"
