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
`publish --rehearse-readback`, `dispatch-check`, `display`, `display-retention`,
`collect-observations`, `evaluate`, `scenario`, `explain`, `benchmark --complete-scope`,
`migrate --dry-run`, `preflight`, `pipeline`, `retain-history`, `acceptance`, `review`,
`worker`, `serve`, `status`, `mcp-catalog`, `mcp-stdio`.

## Final acceptance and release (P24)

```bash
python -m focus.cli acceptance --out evidence/24
python -m focus.cli review --acceptance evidence/24/acceptance.json --reviewer "<name>"
python -m focus.cli review --verify 1
```

- `acceptance` runs the whole suite once and judges every case A01-A72 and requirement C01-C39 from per-test
  receipts, including each requirement's caller paths. The register is `tests/acceptance/map.json`.
- `review` writes the next unused `review/REVIEW-NNN.md` and `VERDICT-NNN.json`. It never overwrites a review, and it
  binds the manual, implementation, test, fixture-source, policy and acceptance hashes.
- Results and live gates: `docs/execution/FINAL_ACCEPTANCE.md`. Rollout: `docs/execution/RELEASE_CHECKLIST.md`.
  Rollback: `docs/execution/ROLLBACK.md`.

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

## Transfer into an approved company environment (no live data shared)

**Work-agent handoff:** [Workplace Porting Guide](docs/integration/WORKPLACE_PORTING_GUIDE.md) · [Source/Table/Query Crosswalk](docs/integration/SOURCE_REPLACEMENT_CROSSWALK.md) · [Rich MAX Scheduling Constraints](docs/integration/SCHEDULING_CONSTRAINTS.md).

Generate an exact **reference-only** machine manifest for the internal agent:

~~~bash
python scripts/export_integration_contract.py --output docs/integration/contract_manifest.generated.json
~~~

The handoff inventories all canonical dataset contracts, currently checked-in SQL source/query files, supplemental feeds and gaps, objective/validation rules and rollout gates. **Do not commit actual internal table mappings, SQL results, employee/commercial data or credentials here.** Produce/complete private mappings solely inside approved company-controlled systems. A valid company schedule requires owner-approved source semantics and independent verification; the current demo is not proof of live readiness.

## Optional FOCUS Gridiron football module (design and staged build prompts)

[Gridiron architectural handoff](docs/gridiron/README.md) · [12 ordered coding prompts](docs/gridiron/prompts/README.md) · [proposed SQL Server schema](docs/gridiron/SQL_SERVER_SCHEMA.md) · [private-source mapping guide](docs/gridiron/SOURCE_MAPPING_AND_GOVERNANCE.md). These are design artifacts; football code, live employee mappings and SQL migrations have **not** been deployed. The existing scheduler and all work-sensitive readiness gates remain authoritative.

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
- Live readiness is a separate capability with precise gaps (`OD-01..OD-18`
  in the policy, and release gates `LG-01..LG-13`). See `docs/execution/SOURCE_READINESS.md`
  and `docs/execution/FINAL_ACCEPTANCE.md`.
