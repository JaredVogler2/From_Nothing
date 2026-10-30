"""P19 browser checks: the iOS and Classic layouts render the same canonical facts from one API (A57, A59, A69 basics).

A real Chromium drives a live local server over a published and observed fixture world. Both layouts must
show the identical stored scores, honest no-data states, keyboard access, no horizontal overflow at 375,
390, tablet and desktop widths, and reduced-motion support.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterator

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from focus.clock import FIXTURE_CUTOFF, FixedClock  # noqa: E402
from focus.codec import parse_utc  # noqa: E402
from focus.config import Settings  # noqa: E402
from focus.data.repositories import Repositories  # noqa: E402
from focus.data.store import FixtureStore  # noqa: E402
from focus.runtime import build_services  # noqa: E402
from focus.web.app import create_app  # noqa: E402
from tests.conftest import OBSERVATION_SHIFT, observed_world  # noqa: E402

POINTS = ("2026-10-26T19:30:00Z", "2026-10-27T16:00:00Z")
TEAMS = ("PROD-A", "PROD-B", "QA")
LAYOUTS = ("/dashboard", "/dashboard/classic")


def _serve(root: Path, app_root: Path) -> tuple[Any, str]:
    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False)
    clock = FixedClock(FIXTURE_CUTOFF)
    services = build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                              clock=clock)
    server = make_server("127.0.0.1", 0, create_app(services=services), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture(scope="module")
def world_server(tmp_path_factory, app_root) -> Iterator[tuple[str, Any]]:
    from focus.performance.score import aggregate

    root = tmp_path_factory.mktemp("ui_world") / "runtime"
    world = observed_world(root, app_root, points=POINTS)
    last = parse_utc(POINTS[-1])
    world.factory = aggregate(world.services, list(world.evals[last].values()), label=f"FACTORY {OBSERVATION_SHIFT}", as_of=last)
    world.last = last
    server, base = _serve(root, app_root)
    yield base, world
    server.shutdown()


@pytest.fixture(scope="module")
def empty_server(tmp_path_factory, app_root) -> Iterator[str]:
    server, base = _serve(tmp_path_factory.mktemp("ui_empty") / "runtime", app_root)
    yield base
    server.shutdown()


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def sign_in(page: Any, base: str, persona: str) -> None:
    page.goto(base + "/")
    page.select_option("#persona", persona)
    page.click("button:has-text('Sign in as demo persona')")
    page.wait_for_url("**/dashboard**")


def open_view(page: Any, base: str, layout: str, view: str) -> None:
    page.goto(f"{base}{layout}#{view}")
    page.wait_for_selector("main#main[aria-busy='false']")              # the strict CSP forbids eval-based predicates


def score_text(page: Any, testid: str) -> str:
    return page.inner_text(f"[data-testid={testid}] .metric-score .metric-value").strip()


def test_ios_and_classic_show_the_same_stored_scores(world_server, browser) -> None:
    base, world = world_server
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(page, base, "manager")
    seen = {}
    for layout in LAYOUTS:
        open_view(page, base, layout, "performance")
        page.wait_for_selector("[data-testid=pooled-score]")
        seen[layout] = (score_text(page, "pooled-score"), {t: score_text(page, f"score-{t}") for t in TEAMS},
                        page.get_attribute("[data-testid=pooled-score]", "data-evaluation-id"))
        assert page.get_attribute(f"nav.side-nav [data-view=performance]", "aria-current") == "page"
        assert page.locator(".state-error").count() == 0
    assert seen["/dashboard"] == seen["/dashboard/classic"]
    pooled, teams, evaluation_id = seen["/dashboard"]
    assert pooled == str(world.factory.score) and evaluation_id == world.factory.id
    assert teams == {t: str(world.evals[world.last][t].score) for t in TEAMS}


def test_team_lead_sees_only_their_goal_in_both_layouts(world_server, browser) -> None:
    base, world = world_server
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(page, base, "lead-prod-b")
    for layout in LAYOUTS:
        open_view(page, base, layout, "performance")
        page.wait_for_selector("[data-testid=score-PROD-B]")
        assert page.locator("[data-testid=score-PROD-A]").count() == 0 and page.locator("[data-testid=score-QA]").count() == 0
        assert score_text(page, "score-PROD-B") == str(world.evals[world.last]["PROD-B"].score)


@pytest.mark.parametrize("width", [375, 390, 820, 1280])
def test_layouts_fit_every_width_with_reduced_motion(world_server, browser, width) -> None:
    base, _ = world_server
    context = browser.new_context(viewport={"width": width, "height": 860}, reduced_motion="reduce")
    page = context.new_page()
    sign_in(page, base, "manager")
    for layout in LAYOUTS:
        open_view(page, base, layout, "performance")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, (layout, width, overflow)
        mobile_tabs = page.is_visible(".bottom-nav")
        assert mobile_tabs == (layout == "/dashboard" and width <= 760), (layout, width)
        duration = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--focus-probe') || "
                                 "(() => { const el = document.createElement('div'); el.className = 'skeleton'; document.body.appendChild(el);"
                                 " const d = getComputedStyle(el).animationDuration; el.remove(); return d; })()")
        assert duration in ("1e-05s", "0.00001s", "0s"), duration          # reduced motion honored


def test_keyboard_reaches_skip_link_and_views(world_server, browser) -> None:
    base, _ = world_server
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(page, base, "manager")
    open_view(page, base, "/dashboard/classic", "performance")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip-link"
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash").startswith("#main") or page.evaluate("document.activeElement.id") == "main"


def test_no_data_states_are_honest_in_both_layouts(empty_server, browser) -> None:
    page = browser.new_context(viewport={"width": 390, "height": 860}).new_page()
    sign_in(page, empty_server, "manager")
    for layout in LAYOUTS:
        open_view(page, empty_server, layout, "performance")
        assert page.locator("[data-testid=pooled-score]").count() == 0
        states = page.locator("main .state-empty, main .state-unknown, main .state-blocked").count()
        assert states >= 1
        text = page.inner_text("main")
        assert "100" not in text.split("How the canonical score")[0]              # no invented perfect score
    anonymous = browser.new_context().new_page()
    anonymous.goto(empty_server + "/dashboard#performance")     # no session: guided to the demo sign-in, never a data view
    anonymous.wait_for_selector("#persona-title")
    assert "Demo sign-in" in anonymous.inner_text("body") and "/dashboard" not in anonymous.url
