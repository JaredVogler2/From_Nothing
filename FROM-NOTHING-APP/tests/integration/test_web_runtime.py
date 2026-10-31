"""P02/P19: import-safe host, typed no-data states, scoped commands (A01, A68) and registry outputs."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import login


def _tree_snapshot(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_a01_no_credentials_no_source_pages_load_without_side_effects(make_client, tmp_path: Path) -> None:
    app, client, services = make_client()
    runtime = tmp_path / "runtime"
    before = _tree_snapshot(runtime)
    for path in ("/", "/api/docs", "/health/live"):
        response = client.get(path)
        assert response.status_code == 200, path
    for path in ("/dashboard", "/dashboard/classic", "/simulation", "/simulation/ios"):   # a visitor without a session goes to the demo sign-in
        response = client.get(path)
        assert response.status_code == 302 and response.headers["Location"].endswith("/"), path
        assert b"Demo sign-in" in client.get(path, follow_redirects=True).data, path
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    body = ready.get_json()
    codes = {r["code"] for r in body["reasons"]}
    assert {"NO_SOURCE_RUN", "NO_ACCEPTED_PLAN"} <= codes
    ctx = client.get("/api/v1/context").get_json()
    assert ctx["schema_version"] == 1
    assert ctx["status"] == "unknown"           # never "ready" without a source run
    assert ctx["source_run_id"] is None and ctx["plan_id"] is None
    assert ctx["data"]["capabilities"]["executable_plan"] is False
    assert {"NO_SOURCE_RUN"} <= {r["code"] for r in ctx["reasons"]}
    login(client, "manager")
    for path in ("/dashboard", "/dashboard/classic"):          # signed in, the workspace itself loads
        assert client.get(path).status_code == 200, path
    # GET/import produced no acquisition, solve, write or grade
    assert _tree_snapshot(runtime) == before


def test_a01_unavailable_repository_is_typed_blocked(services_factory) -> None:
    from focus.data.store import UnavailableStore
    from focus.web.app import create_app

    services = services_factory(store=UnavailableStore())
    client = create_app(services=services).test_client()
    ctx = client.get("/api/v1/context").get_json()
    assert ctx["status"] == "blocked"
    assert "REPOSITORY_UNAVAILABLE" in {r["code"] for r in ctx["reasons"]}


def test_app_module_import_is_side_effect_free(app_root: Path, tmp_path: Path) -> None:
    code = (
        "import sys, os; sys.path.insert(0, %r); os.environ['FOCUS_SCENARIO_ID']='import_probe';"
        "import app; import focus.cli;"
        "bad=[m for m in sys.modules if m.split('.')[0] in ('ortools','playwright','pymssql')];"
        "print(repr(bad))"
    ) % str(app_root)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=tmp_path)
    assert out.stdout.strip() == "[]"
    assert not (app_root / "fixtures" / "runtime" / "import_probe").exists()


def test_a68_unauthenticated_and_wrong_grant_rejected(make_client) -> None:
    app, client, services = make_client()
    r = client.get("/api/v1/runs/RUN-abcdef")
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "UNAUTHENTICATED"
    token = login(client, "mechanic-a05")
    body = {"source_run_id": "SR-1", "policy_id": "rebuild-v1", "budget_seconds": 5, "idempotency_key": "k-00000001"}
    r = client.post("/api/v1/schedule-runs", json=body, headers={"X-CSRF-Token": token})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "FORBIDDEN"


def test_a68_commands_require_csrf_json_schema_and_same_origin(make_client) -> None:
    app, client, services = make_client()
    token = login(client, "operator")
    body = {"source_run_id": "SR-1", "policy_id": "rebuild-v1", "budget_seconds": 5, "idempotency_key": "k-00000001"}
    r = client.post("/api/v1/schedule-runs", json=body)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "CSRF_INVALID"
    r = client.post("/api/v1/schedule-runs", json=body, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    r = client.post("/api/v1/schedule-runs", data="source_run_id=x", headers={"X-CSRF-Token": token, "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = client.post("/api/v1/schedule-runs", json=dict(body, extra=1), headers={"X-CSRF-Token": token})
    assert r.status_code == 422 and "unknown field" in r.get_json()["error"]["message"]
    r = client.post("/api/v1/schedule-runs", json=body, headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "ORIGIN_MISMATCH"
    r = client.post("/api/v1/schedule-runs", json=body, headers={"X-CSRF-Token": token, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/api/v1/schedule-runs", data=b'{"a":1,"a":2}', headers={"X-CSRF-Token": token, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "BAD_JSON"


def test_a68_idempotent_command_returns_honest_queued_status(make_client) -> None:
    from tests.conftest import acquire_demo

    app, client, services = make_client(inprocess_worker=False)
    token = login(client, "operator")
    missing = {"source_run_id": "SR-1", "policy_id": "rebuild-v1", "budget_seconds": 5, "idempotency_key": "k-00000000"}
    r = client.post("/api/v1/schedule-runs", json=missing, headers={"X-CSRF-Token": token})
    assert r.status_code == 404 and r.get_json()["error"]["code"] == "SOURCE_RUN_NOT_FOUND"    # refused before a doomed run is queued
    run = acquire_demo(services)
    body = {"source_run_id": run.run_id, "policy_id": "rebuild-v1", "budget_seconds": 5, "idempotency_key": "k-00000001"}
    first = client.post("/api/v1/schedule-runs", json=body, headers={"X-CSRF-Token": token})
    assert first.status_code == 202
    env = first.get_json()
    run_id = env["data"]["run_id"]
    assert env["data"]["status"] == "queued" and env["data"]["replayed"] is False
    again = client.post("/api/v1/schedule-runs", json=body, headers={"X-CSRF-Token": token})
    assert again.status_code == 202 and again.get_json()["data"]["run_id"] == run_id
    assert again.get_json()["data"]["replayed"] is True
    conflict = client.post("/api/v1/schedule-runs", json=dict(body, budget_seconds=9), headers={"X-CSRF-Token": token})
    assert conflict.status_code == 409 and conflict.get_json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    status = client.get(f"/api/v1/runs/{run_id}").get_json()
    assert status["data"]["status"] == "queued"      # queued is not a successful schedule
    assert status["data"]["result_receipt"] is None


def test_get_requests_never_mutate_runtime(make_client, tmp_path: Path) -> None:
    app, client, services = make_client()
    login(client, "manager")
    runtime = tmp_path / "runtime"
    before = _tree_snapshot(runtime)
    for path in ("/api/v1/context", "/api/v1/openapi.json", "/health/ready", "/dashboard"):
        client.get(path)
    assert _tree_snapshot(runtime) == before


def test_error_envelope_and_query_strictness(make_client) -> None:
    app, client, services = make_client()
    login(client, "manager")
    r = client.get("/api/v1/context?unexpected=1")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "INVALID_PARAMETER"
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404 and set(r.get_json()["error"]) == {"code", "message", "request_id"}
    r = client.get("/api/v1/context", headers={"X-Request-ID": "req-12345678"})
    assert r.headers["X-Request-ID"] == "req-12345678"
    assert r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors" in r.headers["Content-Security-Policy"]


def test_pages_carrying_the_session_csrf_token_are_never_cached(make_client) -> None:
    """P24 cache check: every rendered page embeds the session's CSRF token, so no browser or shared proxy may keep it."""
    app, client, services = make_client()
    token = login(client, "manager")
    page = client.get("/dashboard")
    assert page.status_code == 200 and f'content="{token}"' in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-store"
    for path in ("/", "/dashboard/classic", "/api/docs", "/health/live", "/health/ready", "/api/v1/openapi.json", "/no-such-page"):
        assert client.get(path).headers["Cache-Control"] == "no-store", path
    logout = client.post("/auth/logout", headers={"X-CSRF-Token": token})
    assert logout.headers["Cache-Control"] == "no-store"
    static = client.get("/static/css/focus.css")                    # the same bytes for everyone: still cacheable
    assert static.status_code == 200 and "no-store" not in static.headers.get("Cache-Control", "")


def test_openapi_generated_from_registry(make_client) -> None:
    app, client, services = make_client()
    doc = client.get("/api/v1/openapi.json").get_json()
    assert doc["openapi"] == "3.1.0"
    ops = {(path, method): op for path, item in doc["paths"].items() for method, op in item.items()}
    assert ("/api/v1/context", "get") in ops and ("/api/v1/schedule-runs", "post") in ops
    post = ops[("/api/v1/schedule-runs", "post")]
    assert "202" in post["responses"] and post["requestBody"]["content"]["application/json"]["schema"]["additionalProperties"] is False
    assert any(p["name"] == "X-CSRF-Token" for p in post["parameters"])
    assert post["x-mcp-tool"] is False


def test_mcp_disabled_by_default_and_read_only_when_enabled(make_client) -> None:
    app, client, services = make_client()
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 404
    app2, client2, _ = make_client(mcp_enabled=True)
    login(client2, "manager")
    init = client2.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).get_json()
    assert init["result"]["serverInfo"]["name"] == "focus-scheduling"
    tools = client2.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).get_json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "get_context" in names and "create_schedule_run" not in names
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    call = client2.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_context", "arguments": {}}}).get_json()
    assert call["result"]["isError"] is False
    assert call["result"]["structuredContent"]["schema_version"] == 1
    refused = client2.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "create_schedule_run", "arguments": {}}}).get_json()
    assert refused["error"]["code"] == -32602
    resource = client2.post("/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "focus://context"}}).get_json()
    assert json.loads(resource["result"]["contents"][0]["text"])["schema_version"] == 1
    note = client2.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202


def test_posit_connect_identity_header_and_prefix(make_client, app_root) -> None:
    app, client, services = make_client(identity_provider="posit_connect", platform="posit_connect",
                                        grants={"subjects": {"posit:alice": ("manager", "reader")}, "groups": {}, "default": ()})
    anon = client.get("/api/v1/context").get_json()
    assert anon["data"]["identity"]["authenticated"] is False
    creds = json.dumps({"user": "alice", "groups": ["eng"]})
    ctx = client.get("/api/v1/context", headers={"RStudio-Connect-Credentials": creds}).get_json()
    assert ctx["data"]["identity"]["subject"] == "posit:alice"
    assert "manager" in ctx["data"]["identity"]["grants"]
    # served under a Connect content prefix (SCRIPT_NAME), generated URLs keep the prefix
    page = client.get("/", base_url="http://localhost/content/abc123")
    html = page.get_data(as_text=True)
    assert 'content="/content/abc123"' in html
    assert "/content/abc123/static/css/focus.css" in html
    # behind a trusted proxy that strips the prefix and sends X-Forwarded-Prefix
    app2, client2, _ = make_client(identity_provider="posit_connect", platform="posit_connect", proxy_fix=1)
    html2 = client2.get("/", headers={"X-Forwarded-Prefix": "/content/xyz"}).get_data(as_text=True)
    assert "/content/xyz/static/js/landing.js" in html2


def test_pages_carry_the_site_zone_times_are_shown_in(make_client) -> None:
    """Stored and served times are UTC; every page names the site's zone (policy shift.timezone) for local display."""
    from tests.conftest import login

    app, client, services = make_client()
    zone = services.policy.str("shift.timezone")
    assert f'<meta name="focus-site-tz" content="{zone}">' in client.get("/").get_data(as_text=True)
    login(client, "manager")
    for path in ("/dashboard", "/simulation"):
        assert f'<meta name="focus-site-tz" content="{zone}">' in client.get(path).get_data(as_text=True), path
