# Stage P19: iOS/Classic shell and scoped canonical API

Status: **fixture_verified**. Every read and command runs against the fixture repository through the
real Flask app (test client and a live local server driven by Chromium). No SQL, live identity
provider, signing key or deploy was used.

## Changed paths

- `focus/web/serializers.py`: the canonical read projections.
  - **Read-only loading.** `Canon.load` loads the admitted snapshot read-only and verifies the accepted
    EXECUTABLE and FORECAST heads end to end. It reports source staleness, a policy change since
    admission and missing plans as typed reasons. It never calls `snapshot_for_run`, which may rebuild.
  - **Projections:** fleet, aircraft, jobs, job detail, plans (accepted, historical or candidate),
    shift book, My Day, workforce, capacity, commitments, economics, shortages, quality,
    performance, history, recognition, progression, the excusal review queue and IE reviews.
  - **Scope.** `require_team`, `default_team` and `myday_person` run before any fetch. People outside
    the scope are redacted, and a job outside it is not found. Cost and terms need an economics
    grant; without one they are null with `ECONOMICS_RESTRICTED`.
  - **Caches.** `CACHE` holds content-addressed objects (snapshot, verified plan, goal registry).
    `PROJECTIONS` is keyed by authorized scope, filters and canonical revision. Absence and
    verification failures are never cached.
- `focus/web/api_views.py`: 19 read operations registered once. Each is a Flask route, an OpenAPI
  operation and a read-only MCP tool, with explicit query schemas. `GET /api/v1/jobs` pages with
  HMAC cursors bound to subject, scope, filters and revision (default 100, max 500).
- `focus/web/api.py`:
  - **Context.** `/context` adds the current shift, the observation evidence (run, freshness, feed
    coverage) and the observation and economics capabilities.
  - **Commands.** `POST /amendments`, `/disruption-claims`, `/excusal-decisions`, `/ie-reviews` and
    `/reported-observations` are new. `/schedule-runs` now refuses a missing or incomplete source
    run or a policy mismatch before queueing.
  - **Server-owned authority.** `enqueue` stores server-derived authority under `_server`.
- `focus/web/security.py`, `focus/config.py`: `FOCUS_GRANTS_JSON.scopes` gives live team leads and
  mechanics a server-owned team or person scope, validated strictly. Without one they see
  aggregates only.
- `focus/performance/amendments.py`: `amend_goal` and `worker_amendment`, the approved feasible
  emergency addition (B = B0 + additions).
  - **Authorization.** Plan of record, goal amendment version (optimistic, under a per-goal lock) and
    scope.
  - **Readiness.** Open work of the goal's team, executable job, verified predecessors (AND releases
    recursive), confirmed parts, no blocking shortage, and a calendar that admits the shift.
  - **Feasibility.** The work is already assigned in the accepted plan, or enough qualified, admitted,
    non-synthetic pool members have regular-capacity headroom for the whole work.
  - **Effort** is the sourced standard, never a client number.
- `focus/engine/max/optimizer/orchestrator.py`: `worker_schedule`. It runs named rich MAX plus FINAL
  validation, returns the candidate receipt and `publishable`, and never signs or promotes.
- `focus/performance/actuals.py`: `knowledge_time`, the clock but never before the latest collected
  observations.
  - It is used by every command worker, the version pre-checks and the review queue, so a version
    read is the version the worker checks.
  - Live it equals the clock.
- `focus/performance/disruption.py`: `worker_decision` reads the reviewer role from `_server` only.
- `focus/performance/score.py`: `canonical(..., registry=)`. `cli_evaluate` also stores the FACTORY
  aggregate of the shift, so the pooled surface reads a stored evaluation.
- `focus/workers.py`: the amendment handler now lives in `focus.performance.amendments`.
- **Front end.**
  - `focus/web/static/js/views/performance.js` is the shared performance partial (score block,
    components, units, offplan, OOS, explanation) for both layouts.
  - `core.js` gains display-only helpers: `fmtRational`, `bindings`, `facts`, `reasonBanner`,
    `viewTitle` and `table` classes.
  - `workspace.js` registers the view and parses `#view?params`.
  - `focus.css` adds the performance styles and the mobile metric grid.
  - `workspace.html` labels the bottom tab "Performance" (S15).
- **Docs.** `docs/execution/DECISIONS.md` (D-17) and `docs/execution/DEPLOYMENT.md` (scopes and the
  command protocol).
- **Tests.**
  - `tests/integration/test_api_scope.py` (13 tests).
  - `tests/ui/test_layout_parity.py` (8 browser tests).
  - `tests/integration/test_web_runtime.py`: the schedule command now uses a real source run and
    asserts the 404 for a missing one.
  - `tests/integration/test_lifecycle_excusals.py`: the decision worker run carries `_server`.

## Commands and results

```
.venv/bin/python -m pytest tests/integration/test_api_scope.py -q          -> 13 passed
.venv/bin/python -m pytest tests/ui -q                                     -> 8 passed (Chromium 153, live local server)
.venv/bin/python -m pytest tests/integration/test_web_runtime.py -q        -> 11 passed
.venv/bin/python -m pytest tests/integration/test_lifecycle_excusals.py -q -> 21 passed
```

```
.venv/bin/python -m pytest tests -q                                        -> 344 passed (7:35)
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A01 | With an empty runtime, every read operation returns 200 with a typed `unknown`, `blocked` or `empty` envelope and reasons, never `ready` and never a 500. Id-addressed reads return 404. The runtime directory is never created. Both layouts render honest no-data states with no invented score, and an anonymous visitor gets the blocked "Sign in" state. |
| A57 | `/performance`, `/shiftbook` and `/performance/history` return the persisted evaluation ids and scores verbatim, equal to `history.current`. The static JavaScript contains no score weights, bands, `mockReady`, constant `ready` status or browser grade storage (asserted). Both layouts show identical scores (Chromium). |
| A59 | After evaluation the factory view is the stored canonical aggregate (`persisted: true`, its evaluation id in the envelope). Its score equals the S14 formula over the summed member components, not the average of member scores. A team lead's pool is their single goal, and history, shift book and performance name the same record. |
| A68 | **Identity and grants.** Every scoped read needs an identity (401) and its grants (403). Coverage is asserted against the registry. **Scope before fetch.** An out-of-scope team or person is refused (403 `OUT_OF_SCOPE`) with repository reads patched to fail, for shift book, jobs, performance, history, recognition, progression, claims, amendments and another mechanic's day. **Redaction.** Another team's job is 404, QA inspectors are redacted for a PROD-A lead, and costs are withheld. **Cursors** cover every job exactly once; a changed filter or subject gives 400 `CURSOR_SCOPE_MISMATCH`, a tampered cursor 400 `BAD_CURSOR`, a new source run 409 `CURSOR_STALE`, and limits are bounded. **GET** across 7 personas and every operation leaves every runtime file byte- and mtime-identical. **Commands.** CSRF is required. Forged `_server`, `authority_role` or `reported_by` fields give 422. A replay returns the same run (`replayed: true`); a changed body gives 409 `IDEMPOTENCY_CONFLICT`. A queued run is not success, and the worker completes or fails with a typed code. Excusal decisions are reviewer-only (403) and versioned (409 `STALE_VERSION`), and self-review is refused. An amendment is versioned, adds the sourced effort with B0 unchanged, and a part-blocked phase fails `NOT_EXECUTABLE: PART_NOT_RECEIVED`. A schedule run completes with a candidate that is never promoted (the head version is unchanged). IE reviews never affect issued goals, a reported completion is never verified, and another person's run is 404. |
| A69 (P19 part) | At 375, 390, 820 and 1280 px both layouts have no horizontal overflow. The iOS bottom tabs show only at phone width. Reduced motion stops the skeleton animation. The keyboard reaches the skip link, `aria-current` marks the view, and `aria-busy` clears after load. |

Further checks:

- MCP `tools/list` exposes the new reads as read-only tools and no commands. `tools/call
  get_performance` returns the same envelope data and evaluation id as REST, and an out-of-scope
  call is an `isError` result with `OUT_OF_SCOPE`.
- Live scopes come from `FOCUS_GRANTS_JSON.scopes` by subject or bare user. Without one, a lead gets
  `TEAM_REQUIRED` and `OUT_OF_SCOPE`, never detail. Malformed scope entries are refused at
  settings load.

## Fixture versus live

All reads and commands ran against the fixture repository with the fixture clock and the
demonstration score policy. Live use needs:

- a configured identity provider (Posit Connect header or Tanzu SSO proxy);
- `FOCUS_GRANTS_JSON` with grants and scopes;
- the SQL repository (P23);
- a separately run worker process.

None was exercised here.

## Remaining gaps

- **The 16 other operational views** render through the same API in P20. In this build they show an
  explicit "projection not available in this build" unknown state.
- **Scenario and explanation commands and `/twin`** arrive with their handlers in P21. They are not
  registered as stubs.
- **SQL.** The SQL store adapter for the API and worker reads is P23.
- **Excusal semantics.** The worker check is authoritative for SELF_REVIEW and version races; the
  enqueue pre-checks are fast feedback only.
