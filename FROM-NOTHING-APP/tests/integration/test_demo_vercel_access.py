"""Public synthetic demo access must be discoverable and survive multiple Flask instances.

A Vercel function instance is not a session authority. All instances must use
one deployment-level FOCUS_SESSION_SECRET, never a random per-process secret.
"""
from __future__ import annotations

from focus.config import Settings
from focus.web.app import create_app


def demo_app(app_root):
    settings = Settings.from_env(
        {
            "FOCUS_MODE": "fixture",
            "FOCUS_IDENTITY_PROVIDER": "fixture",
            "FOCUS_SESSION_SECRET": "test-only-fixed-session-secret-with-sufficient-entropy",
            "FOCUS_SECURE_COOKIES": "true",
        },
        app_root=app_root,
    )
    app = create_app(settings=settings)
    app.config["TESTING"] = True
    return app


def test_demo_dashboard_guides_unsigned_visitor_to_persona_form(app_root):
    client = demo_app(app_root).test_client()
    response = client.get("/dashboard", base_url="https://example.test")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    landing = client.get("/", base_url="https://example.test")
    assert landing.status_code == 200
    assert b"Demo sign-in" in landing.data
    assert b"Demo Manager" in landing.data


def test_demo_cookie_authenticates_across_independent_instances(app_root):
    client = demo_app(app_root).test_client()
    response = client.post(
        "/auth/demo-login",
        data={"persona": "ie", "next": "/dashboard"},
        base_url="https://example.test",
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard", base_url="https://example.test").status_code == 200

    # Emulate subsequent requests landing on a different Vercel instance.
    cookie = client.get_cookie("focus_session", domain="example.test", path="/")
    assert cookie is not None
    other = demo_app(app_root).test_client()
    other.set_cookie("focus_session", cookie.value, domain="example.test", path="/")
    response = other.get("/dashboard", base_url="https://example.test")
    assert response.status_code == 200
    assert b"FOCUS" in response.data


def test_real_api_still_requires_identity(app_root):
    client = demo_app(app_root).test_client()
    response = client.get("/api/v1/fleet", base_url="https://example.test")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "UNAUTHENTICATED"
