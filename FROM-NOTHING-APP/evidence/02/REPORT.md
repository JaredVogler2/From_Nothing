# Stage P02 — Single Flask runtime and canonical domain

Status: **fixture_verified**

## Changed paths

- Root: `app.py` (WSGI `app = create_app()` plus a guarded serve), `pyproject.toml` (Python >=3.12, baseline deps, verify/solver extras), `requirements.lock` (exact, tested on CPython 3.12.3), `requirements.txt` (runtime pins for Posit Connect/Tanzu), `README.md`, `.gitignore`, `.env.example`, `.python-version`, `runtime.txt`, `manifest.yml`, `Procfile`.
- Domain and contracts: `focus/codec.py` (canonical JSON, strict decode, typed records, sealed envelopes), `focus/domain.py` (JobKey/PhaseKey/PersonKey/PoolKey, Interval, EvidenceRef, RuleDecision, RequirementBundle, Holding, enums, integer-minute helpers), `focus/contracts/{planning,events,evaluation,runs}.py`.
- Configuration: `focus/config.py` (immutable FOCUS_* allowlist; Tanzu `PORT`/`VCAP_SERVICES` and Posit Connect detection adapters; `Secret`), `focus/policy.py` (one policy registry), `focus/clock.py`, `focus/runtime.py` (DI container).
- Persistence: `focus/data/store.py` (FixtureStore: atomic write-once records, versioned state, CAS heads written last, idempotency index, lock directories; UnavailableStore), `focus/data/repositories.py` (envelope helpers, worker runs with idempotency/leases), `focus/data/sql.py` (lazy pymssql, timeouts, SELECT-only allowlisted reader).
- Web: `focus/web/app.py` (injectable, import-safe `create_app`), `security.py` (fixture/Posit Connect/trusted-header identity, server-owned grants, scope, CSRF, origin checks, hardening headers), `operations.py` (operation registry, envelope, signed cursors), `schema.py` (strict validator), `api.py` (context, runs, schedule-run command), `routes.py`, `openapi.py`, `views.py`, templates and static assets.
- MCP: `focus/mcp/catalog.py`, `server.py` (read-only JSON-RPC, default disabled), `stdio.py`.
- CLI and workers: `focus/cli.py` (all S17 commands parsed; schema-versioned JSON output, non-zero on error), `focus/workers.py` (leased worker runs, in-process worker started lazily).
- Tools and tests: `tools/check_imports.py`, `tools/status.py`, `tests/conftest.py`, `tests/unit/test_codec_domain.py`, `tests/unit/test_policy_config.py`, `tests/unit/test_imports.py`, `tests/integration/test_web_runtime.py`.

## Interfaces

`create_app(settings=None, repositories=None, clock=None)`; `build_services()`; `Store` protocol (FixtureStore / UnavailableStore; SqlStore lands with the migrations); `Repositories` facade; `OperationRegistry` → Flask + OpenAPI 3.1 + MCP catalog; CLI `main(argv)`.

## Commands and results

```
.venv/bin/python -m pytest tests -q                       -> 38 passed
.venv/bin/python tools/check_imports.py                   -> ok=true (41 modules; optional packages blocked and never loaded; local package; nothing written)
.venv/bin/python -m focus.cli status                      -> exit 0, heads null, policy ready
.venv/bin/python -m focus.cli mcp-catalog                 -> tools [get_context, get_run], resources [focus://context]
FOCUS_PORT=5077 python app.py                             -> /health/live 200, /health/ready 503 (NO_SOURCE_RUN, NO_ACCEPTED_PLAN)
second `focus.cli serve` on the same port                 -> PORT_IN_USE error, nothing killed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A01 | With no SQL credentials and no source run, all supported pages load; `/api/v1/context` reports `status=unknown` with `NO_SOURCE_RUN`. With an unavailable repository it reports `blocked`. Import and GETs write nothing (runtime tree unchanged; subprocess import probe). |
| A02 | `tools/check_imports.py` imports all modules with ortools/playwright/pymssql/LLM SDKs blocked: no failures, no optional modules loaded, the package resolves to the local `focus/`, no `sys.path` manipulation. A missing SQL driver yields typed `DRIVER_MISSING`. |
| A04 (identity part) | The same SOI on two lines, two revisions, or whitespace/case variants yields distinct transport ids; collisions are rejected by `ensure_unique_ids`; keys round-trip through canonical JSON. The full graph/duration/parts/UI part runs in P03+. |
| A68 (P02 part) | Unauthenticated → 401; missing grant → 403; missing/forged CSRF → 403; cross-origin or `Sec-Fetch-Site: cross-site` → 403; non-JSON → 415; unknown fields → 422; duplicate JSON keys → 400; idempotent replay returns the same queued run; a reused key with a different body → 409; GET never mutates. |

Also verified: OpenAPI is generated from the registry (POST documents CSRF and 202). MCP is disabled by default (404); when enabled, only read-only tools are listed and callable, commands are refused, and resources resolve. Posit Connect identity header mapping and URL-prefix-safe rendering (`SCRIPT_NAME` and `X-Forwarded-Prefix`) work.

## Remaining gaps

- The live `SqlStore` adapter (FOCUS_FN_* persistence) arrives with the P04 migration drafts and is completed in P23.
- Projection endpoints and the full iOS/Classic views arrive in P19/P20. Until then the workspace shows typed "not available in this build" states, never fabricated data.
