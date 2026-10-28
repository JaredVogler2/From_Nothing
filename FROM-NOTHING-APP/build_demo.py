"""Prepare an immutable synthetic FOCUS demo snapshot during Vercel's Flask build.

Never uses live SQL, identities, or production data. Build-time generation avoids
trying to run the scheduling pipeline during an HTTP request, or relying on
Vercel's ephemeral function filesystem for source/plan records.

Locally, the normal CLI remains the source of truth:
  python -m focus.cli demo-bootstrap
  python -m focus.cli demo-run
"""
from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    # In a live deployment, never materialize fixture data. The script can be
    # left configured while deployment identity is switched to real SSO.
    if os.environ.get("FOCUS_MODE", "fixture").strip().lower() != "fixture":
        print("FOCUS: live mode; skipping demo fixture build")
        return

    # The project root is the directory Vercel builds, not the monorepo root.
    from focus.config import Settings
    from focus.runtime import build_services
    from focus.pipeline import demo_run

    root = Path(__file__).resolve().parent
    settings = Settings.from_env(os.environ, app_root=root)
    services = build_services(settings)
    # Keep every effect local to the deterministic, invented manufacturing
    # fixture. A non-OK result is a deployment failure, not a blank dashboard.
    result = demo_run(services, scenario="manufacturing_demo", budget_seconds=10.0)
    if result.get("status") != "ok":
        raise RuntimeError(
            "FOCUS fixture build failed: " +
            str(result.get("error") or result.get("plans") or result.get("status"))
        )
    from focus.web.api import source_head_name, plan_head_name
    # Source/accepted plan must be accessible from the same read-only fixture
    # repository that the deployed Flask app uses.
    source = services.repositories.head(source_head_name(settings.scope_key))
    executable = services.repositories.head(plan_head_name(settings.scope_key, "EXECUTABLE"))
    if source is None or executable is None:
        raise RuntimeError("FOCUS demo build produced no source or accepted executable plan")
    print(
        "FOCUS: synthetic demo built; source=%s, executable=%s, fixture=%s"
        % (bool(source), bool(executable), settings.runtime_dir())
    )


if __name__ == "__main__":
    main()
