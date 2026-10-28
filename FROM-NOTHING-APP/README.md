# FOCUS Manufacturing Scheduling (From-Nothing rebuild)

FOCUS schedules **all aircraft with work in process and all applicable jobs**
using named workforce, individual certifications, per-line calendars, typed
precedence, rework, parts shortages, quality holds, aircraft economics and
stable commitments. It optimizes shared capacity across the whole fleet,
then publishes an immutable, independently validated plan.

This application was built from an empty folder using only
`../FOCUS_FROM_NOTHING_MASTER.txt`. Its execution register is
`docs/execution/STATUS.json`, with per-stage evidence under `evidence/`.

## Quick start

The interpreter is **Python 3.12**.

POSIX:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[verify]"
.venv/bin/python -m playwright install chromium
.venv/bin/python -m focus.cli demo-bootstrap --scenario manufacturing_demo
.venv/bin/python -m focus.cli demo-run --scenario manufacturing_demo
.venv/bin/python app.py
.venv/bin/python -m pytest tests -q
```

Windows (PowerShell or cmd), as stated in the manual:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[verify]"
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m focus.cli demo-bootstrap
.venv\Scripts\python.exe -m focus.cli demo-run
.venv\Scripts\python.exe app.py
.venv\Scripts\python.exe -m pytest tests -q
```

Then open <http://127.0.0.1:5055/>. In fixture mode, sign in with one of the
invented demo personas. A DEMO banner marks every fixture page.

## What runs where

| Path | Purpose |
| --- | --- |
| `app.py` | WSGI entrypoint `app = create_app()` plus a guarded `serve` command |
| `focus/` | the single installable package (domain, data, engine, validation, publication, performance, analytics, explanations, web, mcp) |
| `config/policies/` | one policy registry (`rebuild-v1` + includes) with authority labels |
| `queries/`, `sql/migrations/` | read-only source queries and reviewable migrations for the proposed `FOCUS_FN_*` tables |
| `fixtures/inputs/` | deterministic `manufacturing_demo` inputs; `fixtures/runtime/<scenario>/` is the persistent fixture repository (git-ignored) |
| `tests/` | unit, integration, UI (Playwright) and benchmark suites |
| `docs/execution/` | roots, decisions, build plan, status register and stage documents |

## CLI

`python -m focus.cli <command>`. Every command prints a schema-versioned JSON
result and exits non-zero on error:

`demo-bootstrap`, `demo-run`, `source-discover --read-only`, `acquire`,
`duration-stats`, `schedule`, `validate --all-required`,
`publish --rehearse-readback`, `dispatch-check`, `collect-observations`,
`evaluate`, `scenario`, `benchmark --complete-scope`, `migrate --dry-run`,
`worker`, `serve`, `status`, `mcp-catalog`, `mcp-stdio`.

## API and MCP

- The versioned REST API lives under `/api/v1`. Every response is the
  canonical envelope `{schema_version, status, source_run_id, plan_id,
  policy_id, evaluation_id, as_of_utc, purpose, coverage, data, reasons,
  next_cursor}`, where `null` means unknown, never zero. Errors use
  `{"error": {code, message, request_id}}`.
- `GET` never acquires sources, optimizes, writes or regrades.
- Commands (`POST`) need the CSRF token from `GET /api/v1/context`, an
  idempotency key and strict JSON. They return `202` with a durable worker
  run (`GET /api/v1/runs/{id}`).
- `GET /api/v1/openapi.json` is generated from the same operation registry
  that defines the routes. A human-readable list is at `/api/docs`.
- **MCP:** `python -m focus.cli mcp-catalog` prints the tool/resource catalog
  generated from that registry. Setting `FOCUS_MCP_ENABLED=true` exposes a
  read-only MCP JSON-RPC endpoint at `/mcp`, with the same identity and grants
  as the API. `python -m focus.cli mcp-stdio` serves it over stdio.

## Hosting

Hosting is Posit Connect first, then VMware Tanzu. See
`docs/execution/DEPLOYMENT.md`. The WSGI object `app:app` works under a URL
prefix. Posit Connect identity (`RStudio-Connect-Credentials`) and Tanzu
`$PORT` / `VCAP_SERVICES` are supported by explicit adapters in
`focus/config.py` and `focus/web/security.py`.

## Honesty rules

- Fixture data is invented and labeled; it never claims real people, parts or
  contracts.
- A conditional part ETA is never physical receipt.
- Virtual MHE/PSC/TST support people are forecast-only.
- Unknown is never rendered as zero.
- Live readiness is a separate capability with precise gaps (`OD-01..OD-12`
  in the policy). See `docs/execution/SOURCE_READINESS.md` once P03 exists.
