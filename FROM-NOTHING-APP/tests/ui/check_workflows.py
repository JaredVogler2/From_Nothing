"""P20 browser workflow checker: every operational view in both layouts, at phone, tablet and desktop widths.

Run directly to produce screenshot evidence::

    python tests/ui/check_workflows.py --out evidence/20/screenshots

It builds two deterministic fixture runtimes with ``demo_run`` (one observed through the S18 collection points,
one published only, so the fixture clock still sits inside the obligation shift), serves each with the real app
and drives Chromium:

* **Views.** Every view of S15 and the supplementary panels, iOS and Classic, at 375, 390, 820 and 1280 px.
  Each must finish loading (``aria-busy=false``) with a heading, no console or page error, no error card and no
  horizontal overflow.
* **Workflows.** Commands go through the API and their worker runs:
  * a Team Lead emergency addition;
  * a disruption claim, then an excusal decision by the reviewer;
  * a +1 virtual staffing what-if from Capacity, opening its scenario result;
  * a mechanic's reported completion, recorded as a claim (never verified);
  * an IE proposal, which appears in Development.

The functions are imported by ``tests/ui/test_workflows.py``; the script exits non-zero on any failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

VIEWS = ("team-lead", "management", "mechanic", "ie", "staffing", "supply", "scenario", "performance", "project", "budget", "gantt",
         "development", "commitments", "economics", "shiftbook", "myday", "capacity", "quality", "arena", "turnover", "history", "twin")
LAYOUTS = ("/dashboard", "/dashboard/classic")
WIDTHS = (375, 390, 820, 1280)
SHOT_VIEWS = ("team-lead", "performance", "management", "myday", "gantt", "scenario")      # curated evidence screenshots


def build_runtime(root: Path, *, observe: bool) -> Any:
    from focus.clock import FIXTURE_CUTOFF, FixedClock
    from focus.config import Settings
    from focus.data.repositories import Repositories
    from focus.data.store import FixtureStore
    from focus.pipeline import demo_run
    from focus.runtime import build_services

    settings = Settings.from_env({}, app_root=APP_ROOT, inprocess_worker=True)
    clock = FixedClock(FIXTURE_CUTOFF)
    services = build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                              clock=clock)
    result = demo_run(services, budget_seconds=5, max_moves=5, observe=observe)
    if result["status"] != "ok":
        raise RuntimeError(f"demo_run failed: {json.dumps(result, default=str)[:500]}")
    return services


@contextmanager
def serve(services: Any) -> Iterator[str]:
    from werkzeug.serving import make_server

    from focus.web.app import create_app

    server = make_server("127.0.0.1", 0, create_app(services=services), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def sign_in(page: Any, base: str, persona: str) -> None:
    page.goto(base + "/")
    page.select_option("#persona", persona)
    page.click("button:has-text('Sign in as demo persona')")
    page.wait_for_url("**/dashboard**")


def open_view(page: Any, base: str, layout: str, view: str) -> None:
    page.goto(f"{base}{layout}#{view}")
    page.wait_for_selector("main#main[aria-busy='false']", timeout=30000)


def check_views(browser: Any, base: str, persona: str, *, widths: tuple[int, ...] = WIDTHS, layouts: tuple[str, ...] = LAYOUTS,
                views: tuple[str, ...] = VIEWS, out: Path | None = None, shots: tuple[str, ...] = SHOT_VIEWS) -> list[dict[str, Any]]:
    """Visit every view; return one record per (layout, width, view) with any problems found."""
    results = []
    for width in widths:
        context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
        page = context.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        sign_in(page, base, persona)
        for layout in layouts:
            for view in views:
                errors.clear()
                open_view(page, base, layout, view)
                problems = []
                if page.locator("main h1").count() == 0:
                    problems.append("no heading")
                if page.locator("main .state-error").count():
                    problems.append("error card: " + page.inner_text("main .state-error")[:200])
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if overflow > 0:
                    problems.append(f"horizontal overflow {overflow}px")
                if errors:
                    problems.append("console: " + "; ".join(errors[:3]))
                if out is not None and view in shots:
                    out.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out / f"{persona}-{view}-{layout.strip('/').replace('/', '-')}-{width}.jpg"), type="jpeg", quality=55)
                results.append({"persona": persona, "layout": layout, "width": width, "view": view, "problems": problems})
        context.close()
    return results


def _wait_run(page: Any, timeout: int = 60000) -> str:
    page.wait_for_selector(".run-card[data-run-status='completed'], .run-card[data-run-status='failed'], .run-card[data-run-status='cancelled']",
                           timeout=timeout)
    return page.get_attribute(".run-card", "data-run-status") or "?"


def workflow_amendment(browser: Any, base: str) -> dict[str, Any]:
    """Team Lead (published runtime, inside the shift): add feasible open work to the goal through a worker run."""
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(page, base, "lead-prod-a")
    open_view(page, base, "/dashboard", "team-lead?team=PROD-A&date=2026-10-26&shift=1")
    page.fill("#amend-reason", "customer escalation pulled this work into today")
    page.click("button:has-text('Add to goal')")
    status = _wait_run(page)
    open_view(page, base, "/dashboard/classic", "shiftbook?team=PROD-A&date=2026-10-26&shift=1")
    return {"workflow": "amendment", "run_status": status, "ok": status == "completed"}


def workflow_explanation(browser: Any, base: str) -> dict[str, Any]:
    """Team Lead (published runtime): explain the top ranked actions through a worker run; ranks stay the server's."""
    page = browser.new_context(viewport={"width": 390, "height": 900}).new_page()
    sign_in(page, base, "lead-prod-b")
    open_view(page, base, "/dashboard", "team-lead?team=PROD-B&date=2026-10-26&shift=1")
    ranked = page.locator("table[class*='data-table'] caption:has-text('Ranked actions')").count()
    page.click("button:has-text('Explain the top')")
    status = _wait_run(page)
    page.wait_for_selector(".explanation[data-outcome]", timeout=30000)
    outcome = page.get_attribute(".explanation", "data-outcome")
    items = page.locator(".explanation-items > li").count()
    return {"workflow": "explanation", "run_status": status, "outcome": outcome, "items": items,
            "ok": status == "completed" and outcome in ("DETERMINISTIC", "VALIDATED") and items >= 1 and ranked == 1}


def workflow_claim_and_decision(browser: Any, base: str) -> dict[str, Any]:
    """Manager files a claim on a missed QA unit; the excusal reviewer approves it (observed runtime)."""
    manager = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(manager, base, "manager")
    open_view(manager, base, "/dashboard", "performance?shift_id=DEMO:2026-10-26:S1")
    manager.click("[data-claims-team=QA] summary:has-text('File a claim for QA')")
    manager.select_option("#claim-QA-cause", "CAPACITY_SHORTAGE")
    manager.fill("#claim-QA-evidence", "ATT:UI-1")
    manager.fill("#claim-QA-notes", "two inspectors were pulled to line 1306")
    manager.click("[data-claims-team=QA] details.claim-form[open] button:has-text('File claim')")
    claim = _wait_run(manager)
    reviewer = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(reviewer, base, "excusal")
    open_view(reviewer, base, "/dashboard/classic", "performance?shift_id=DEMO:2026-10-26:S1")
    reviewer.fill("#decide-notes-QA", "attendance pull confirmed by the roster")
    reviewer.locator("[data-claims-team=QA] button:has-text('Approve')").last.click()      # the newest QA claim
    decision = _wait_run(reviewer)
    return {"workflow": "claim_and_decision", "claim_run": claim, "decision_run": decision, "ok": claim == decision == "completed"}


def workflow_what_if(browser: Any, base: str) -> dict[str, Any]:
    """Capacity +1 virtual what-if: a scenario run that opens its SCENARIO_ONLY result."""
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(page, base, "manager")
    open_view(page, base, "/dashboard", "capacity?date=2026-10-26")
    page.locator("button:has-text('+1 virtual (scenario)')").first.click()
    # a completed run navigates straight to its result, so wait for the result page rather than the transient run card
    page.wait_for_selector("main h1:has-text('Scenario')", timeout=180000)
    page.wait_for_selector("main#main[aria-busy='false']")
    text = page.inner_text("main")
    failed = page.locator(".run-card[data-run-status='failed']").count() > 0
    return {"workflow": "what_if", "result_shown": "SCENARIO_ONLY" in text, "ok": not failed and "SCENARIO_ONLY" in text and "COMPLETED" in text}


def workflow_report_and_proposal(browser: Any, base: str) -> dict[str, Any]:
    """A mechanic reports a completion (a claim, never verified); IE files a proposal that Development lists."""
    mech = browser.new_context(viewport={"width": 390, "height": 860}).new_page()
    sign_in(mech, base, "mechanic-a05")
    open_view(mech, base, "/dashboard", "mechanic?date=2026-10-27")
    mech.fill("#report-notes", "done before lunch")
    mech.click("button:has-text('Report completion (claim)')")
    report = _wait_run(mech)
    verified = "verified" in mech.inner_text(".run-card")
    ie = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(ie, base, "ie")
    open_view(ie, base, "/dashboard", "ie")
    ie.locator("button:has-text('Propose correction')").first.click()
    ie.locator("textarea[placeholder='Proposed correction']").fill("raise the P75 duration for rework")
    ie.locator("textarea[placeholder='Reason and evidence']").fill("five observed overruns")
    ie.click("button:has-text('Submit proposal')")
    proposal = _wait_run(ie)
    open_view(ie, base, "/dashboard/classic", "development")
    listed = "raise the P75 duration for rework" in ie.inner_text("main")
    return {"workflow": "report_and_proposal", "report_run": report, "proposal_run": proposal, "receipt_mentions_verified": verified,
            "listed_in_development": listed, "ok": report == proposal == "completed" and listed}


def main(argv: list[str] | None = None) -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=None, help="write full-page screenshots here")
    parser.add_argument("--widths", default=",".join(map(str, WIDTHS)))
    parser.add_argument("--shot-views", default=",".join(SHOT_VIEWS), help="views to screenshot (comma separated)")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else None
    widths = tuple(int(w) for w in args.widths.split(","))
    shots = tuple(v for v in args.shot_views.split(",") if v)
    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch()
        observed = build_runtime(Path(tmp) / "observed", observe=True)
        published = build_runtime(Path(tmp) / "published", observe=False)
        with serve(observed) as base:
            report["views"] = check_views(browser, base, "manager", widths=widths, out=out, shots=shots)
            report["views"] += check_views(browser, base, "lead-prod-b", widths=(390,), out=None)
            report["workflows"] = [workflow_claim_and_decision(browser, base), workflow_what_if(browser, base), workflow_report_and_proposal(browser, base)]
        with serve(published) as base:
            report["workflows"].append(workflow_amendment(browser, base))
            report["workflows"].append(workflow_explanation(browser, base))
        browser.close()
    failures = [v for v in report["views"] if v["problems"]] + [w for w in report["workflows"] if not w["ok"]]
    print(json.dumps({"views_checked": len(report["views"]), "workflows": report["workflows"], "failures": failures}, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
