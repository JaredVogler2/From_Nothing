"""P20 browser workflows (A59, A63, A68, A69): every view in both layouts, and the audited command workflows.

Reuses ``tests/ui/check_workflows.py`` so the same checks run in pytest and as the evidence script.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.ui import check_workflows as cw  # noqa: E402


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def observed(tmp_path_factory) -> Iterator[str]:
    services = cw.build_runtime(tmp_path_factory.mktemp("ui_observed") / "runtime", observe=True)
    with cw.serve(services) as base:
        yield base


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> Iterator[str]:
    services = cw.build_runtime(tmp_path_factory.mktemp("ui_published") / "runtime", observe=False)
    with cw.serve(services) as base:
        yield base


def test_a69_every_view_renders_in_both_layouts_at_phone_and_desktop(browser, observed) -> None:
    results = cw.check_views(browser, observed, "manager", widths=(390, 1280))
    assert len(results) == len(cw.VIEWS) * 2 * 2
    assert [r for r in results if r["problems"]] == []
    scoped = cw.check_views(browser, observed, "lead-prod-b", widths=(375,), views=("team-lead", "shiftbook", "turnover", "performance", "gantt"))
    assert [r for r in scoped if r["problems"]] == []


def test_a68_claim_then_reviewed_decision_through_worker_runs(browser, observed) -> None:
    result = cw.workflow_claim_and_decision(browser, observed)
    assert result["ok"], result


def test_what_if_is_an_isolated_scenario_result(browser, observed) -> None:
    result = cw.workflow_what_if(browser, observed)
    assert result["ok"], result


def test_reported_completion_is_a_claim_and_ie_proposal_is_listed(browser, observed) -> None:
    result = cw.workflow_report_and_proposal(browser, observed)
    assert result["ok"], result


def test_team_lead_emergency_addition_completes_as_a_worker_run(browser, published) -> None:
    result = cw.workflow_amendment(browser, published)
    assert result["ok"], result
