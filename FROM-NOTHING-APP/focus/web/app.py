"""Flask application factory (S02, S15).

``create_app(settings=None, repositories=None, clock=None)`` is injectable and
import-safe. It never opens a database, acquires sources, writes files,
starts a solver or touches a port. Hosted deployments use the WSGI object
directly:

* Posit Connect: ``rsconnect deploy api -e app:app .`` (served under
  ``/content/<guid>/``; URLs are generated with ``request.script_root``).
* VMware Tanzu: ``Procfile`` runs Waitress on ``$PORT`` (see
  ``docs/execution/DEPLOYMENT.md``).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from flask import Flask

from ..clock import Clock
from ..config import Settings
from ..data.repositories import Repositories
from ..runtime import Services, build_services
from . import routes, security

_HERE = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None, repositories: Repositories | None = None,
               clock: Clock | None = None, *, services: Services | None = None) -> Flask:
    services = services or build_services(settings, repositories=repositories, clock=clock)
    settings = services.settings
    app = Flask(
        "focus.web",
        template_folder=str(_HERE / "templates"),
        static_folder=str(_HERE / "static"),
        static_url_path="/static",
    )
    app.config.update(
        SECRET_KEY=settings.session_secret.reveal(),
        MAX_CONTENT_LENGTH=settings.max_content_length,
        SESSION_COOKIE_NAME="focus_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=False,
    )
    app.json.sort_keys = False  # type: ignore[attr-defined]
    app.logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    if settings.proxy_fix:
        from werkzeug.middleware.proxy_fix import ProxyFix

        n = settings.proxy_fix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n, x_prefix=n)  # type: ignore[method-assign]
    app.extensions["focus"] = services
    security.init_app(app, settings)
    routes.register(app, services)
    return app
