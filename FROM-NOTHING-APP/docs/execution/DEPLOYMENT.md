# Deployment: Posit Connect first, then VMware Tanzu

User directive (ROOTS.md): FOCUS is hosted on **Posit Connect** first and
then on **VMware Tanzu**. The interpreter is **Python 3.12** on both. The same
WSGI object `app:app` and the same settings allowlist (`focus/config.py`)
serve both platforms. Nothing below has been deployed from this build; these
are exact, reviewable steps for an operator who holds the platform
credentials.

## Common rules

- Hosting never changes application semantics. Fixture mode stays labeled
  DEMO. Live mode needs SQL credentials, server-owned grants and a trusted
  identity provider.
- Secrets (`FOCUS_SQL_PASSWORD`, `FOCUS_SESSION_SECRET`, signing keys) come
  from the platform's secret store. They never go in `manifest.yml`,
  `.env.example`, bundles or logs.
- The web process is read-only for GETs. Background work (schedule,
  scenarios, explanations) runs as worker runs, either in-process
  (`FOCUS_INPROCESS_WORKER=true`) or as a separate worker
  (`python -m focus.cli worker`).
- Signing and promotion run in a separate trusted worker/CLI. The app reader
  principal never signs.
- Health: `/health/live` (process up; use it for platform health checks) and
  `/health/ready` (source/plan/policy readiness; 503 with reasons until ready).

## Plan signing (P16)

- Publication signs with a domain-separated HMAC-SHA256 (`FOCUS_PLAN_V1`). The trusted key is
  named by `FOCUS_SIGN_KEY_ID`, and its material is referenced, never inlined, by
  `FOCUS_SIGN_KEY_REF`:
  - `env:NAME`: an environment variable on the signer process only;
  - `vcap:service/field`: a Tanzu user-provided service credential, for example
    `vcap:focus-secrets/sign_key`. This form is set automatically when `focus-secrets` carries
    `sign_key`.
- The material must be at least 32 bytes, as hex or base64url. HMAC is symmetric: verifying the
  current plan needs the same key, so verify-only custody is never claimed.
- Fixture mode without a configured key uses a public TEST key (`fixture-test-key-v1`).
  Receipts are labelled `fixture_readback`. A TEST key, or any key other than the configured
  one, is refused (`TEST_KEY_NOT_LIVE`, `UNAUTHORIZED_SIGNER`).
- Live publication stays `UNAVAILABLE` until the proposed `FOCUS_FN` stage, readback and
  acceptance objects (`sql/migrations/007_publication.sql`) are installed by an authorized
  migration (P23). No live signature, acceptance or head move happens before that.

## Explanation providers (P21)

- `FOCUS_LLM_PROVIDER` is `none` by default. Explanations are then deterministic templates over grounded facts, and
  nothing leaves the process.
- `mock` and `recorded` are fixture-only providers. In live mode they are refused (`FIXTURE_ONLY`), and the
  deterministic explanation is returned.
- `bcai` needs all of the following; any missing piece returns the complete deterministic explanation
  (`BCAI_NOT_CONFIGURED`, `BCAI_ENDPOINT_NOT_TLS`, `BCAI_CREDENTIAL_MISSING`, `PROVIDER_TIMEOUT`):
  - `FOCUS_BCAI_ENDPOINT`: an approved `https://` URL. TLS is verified with the system trust store.
  - `FOCUS_BCAI_CREDENTIAL_REF`: `env:NAME` (Connect content variable or Tanzu env) or `vcap:service/field` (a Tanzu
    user-provided service). The credential is sent only as a bearer header, and is never logged or stored.
  - Bounds from the policy: `explanations.timeout_seconds` per attempt, `explanations.max_retries` after a timeout,
    and `explanations.max_candidates` per request.
- What a provider receives: a POST body `{"schema", "prompt_version", "input"}`, where `input` is the data-only prompt.
  The prompt holds authorized fact rows with no person names or ids.
- What it must return: the `focus-explain-v1` JSON (`ExplainResponse`). Anything else, any ungrounded claim or narrative
  word, or any extra field is discarded.
- The audit record keeps hashes only (request, prompt, raw response, output, facts), never the raw text.
- The approved endpoint, its response adapter and the credential are operator decisions. No endpoint is built into
  the application.

## Optional CP-SAT solver stage (P22)

- It is off by default: policy `solver.enabled` is `false` and `FOCUS_SOLVER_ENABLED` is unset. The default
  `requirements.txt` does not install it, and every plan comes from the named heuristic and the independent
  validator.
- Turning it on is an operational decision (`docs/execution/SOLVER_ENABLEMENT.md`). There are two switches:
  - `FOCUS_SOLVER_ENABLED=true`: a Connect content variable or a Tanzu `env` entry. It keeps the policy hash.
  - A policy revision with `solver.enabled: true`. This gives a new policy hash; accepted artifacts keep the hash they
    were built with.
- Install by deploying `requirements-solver.txt` as the bundle's `requirements.txt` (same base pins plus OR-Tools).
  If the switch is on without the dependency, every run records `UNAVAILABLE` and keeps the validated baseline.
- Where it runs: inside schedule and scenario worker runs only, never in a GET request.
- Bounds come from the policy:
  - `solver.budget_seconds`: wall-clock limit of the relaxation solve;
  - `solver.max_probes`: complete named decodes;
  - `solver.max_memory_mb`: solver memory;
  - `solver.num_workers`: 1 with the policy seed is reproducible.
  A cancel request stops the solve.
- Memory: importing OR-Tools added about 67 MB of peak RSS in the small benchmark (61 MB to 128 MB). Size the Tanzu
  `memory` (1G in `manifest.yml`) and `solver.max_memory_mb` from the measured `SCALE_REPORT.md` before enabling it.
- Rollback: unset the variable, or return to the previous policy. The stage never wrote anything a disabled run
  depends on.

## Optional native scheduling core

`FOCUS_NATIVE_CORE=true` uses the Rust ledger and crew core when the `focus-native` wheel is installed, with plans
byte-identical to the Python core's. Without the wheel, the app runs the Python core and the schedule receipt says
`engine: python`. Build, vendoring and the Connect and Tanzu steps are in `NATIVE_CORE.md`.

## Posit Connect

The content type is a Python API (WSGI/Flask), entrypoint `app:app`.

```bash
python3.12 -m pip install rsconnect-python
rsconnect add --server https://connect.example.com --name focus-connect --api-key "$CONNECT_API_KEY"
rsconnect deploy api --name focus-connect --entrypoint app:app --title "FOCUS Manufacturing Scheduling" .
```

For git-backed publishing, generate a manifest and commit it:
`rsconnect write-manifest api --entrypoint app:app .`. The manifest records
Python 3.12 from the active interpreter. `requirements.txt` holds the exact
runtime pins.

Connect content settings ("Vars"):

| Variable | Value |
| --- | --- |
| `FOCUS_MODE` | `live` (or `fixture` for a labeled demo) |
| `FOCUS_IDENTITY_PROVIDER` | `posit_connect` (the default when Connect is detected) |
| `FOCUS_GRANTS_JSON` | server-owned grants and data scopes, e.g. `{"subjects": {"posit:alice": ["reader","manager"], "posit:bob": ["reader","team_lead"]}, "groups": {"focus-leads": ["team_lead"]}, "scopes": {"posit:bob": {"teams": ["PROD-A"]}, "carol": {"teams": ["PROD-A"], "person": "<badge id>"}}}` |
| `FOCUS_SQL_HOST`, `FOCUS_SQL_DATABASE`, `FOCUS_SQL_USER`, `FOCUS_SQL_PASSWORD` | read principal (mark the password as secret) |
| `FOCUS_SESSION_SECRET` | random 32+ bytes (secret) |
| `FOCUS_SOURCE_TIMEZONE_MAP_JSON` | source datetime2 timezone policy: `{"*": "UTC"}`, because database columns hold UTC (owner, 2026-10-31). The app shows times in the site's zone (policy `shift.timezone`). |
| `FOCUS_MCP_ENABLED` | `false` until MCP access is approved |

Before publishing, run `python -m focus.cli preflight --role web` with the content's variables. It checks:

- CPython 3.12;
- trusted identity;
- the session secret;
- SQL by presence;
- the policy;
- `create_app`;
- migrations.

How the app behaves under Connect:

- Connect serves the content under `/content/<guid>/`. Every generated URL
  uses `request.script_root`, and the browser code reads
  `<meta name="focus-base">`, so links, static files and API calls keep the
  prefix.
- Connect authenticates viewers (browser session or `Authorization: Key …`
  API key) and sends `RStudio-Connect-Credentials: {"user","groups"}`. FOCUS
  trusts that header only with `FOCUS_IDENTITY_PROVIDER=posit_connect` and
  maps users and groups to grants from `FOCUS_GRANTS_JSON`.
- Data scope is server-owned too. Broad roles (manager, IE, supply chain, QA,
  excusal decider, workers) see all teams. A team lead or mechanic sees team or
  person detail only through a `scopes` entry keyed by subject (`posit:<user>`,
  `sso:<user>`) or bare user name: `teams` is a list of team ids or `"*"`, and
  `person` is the native badge id that My Day opens. Without an entry they see
  fleet aggregates only. A role name in a header never grants scope.
- The Connect dashboard embeds content in a frame. In Connect mode FOCUS
  therefore sends `frame-ancestors 'self'` instead of `X-Frame-Options: DENY`.
- For a fixture demo on Connect, run `demo-bootstrap` and `demo-run` locally
  and deploy with `fixtures/runtime/manufacturing_demo` included. The fixture
  repository is file-based and is lost on redeploy by design.

## VMware Tanzu (Cloud Foundry)

Files: `manifest.yml`, `Procfile` (`web: python app.py`), `runtime.txt`
(`python-3.12.x`) and `requirements.txt`.

```bash
cf create-user-provided-service focus-sql -p '{"host":"SQL-D-22D10090","database":"SHARED","user":"<reader>","password":"<secret>"}'
cf create-user-provided-service focus-secrets -p '{"session_secret":"<random>","sign_key_id":"KEY-2026-01","sign_key":"<signer-only>"}'
cf push -f manifest.yml
```

How the app behaves under Tanzu:

- The settings adapter detects `VCAP_APPLICATION`. It binds `0.0.0.0:$PORT`
  and reads SQL credentials from the `focus-sql` user-provided service
  (`FOCUS_SQL_SERVICE_NAME`) and secrets from `focus-secrets`
  (`FOCUS_SECRETS_SERVICE_NAME`). An explicit `FOCUS_*` variable always wins.
- Identity comes from a trusted SSO route service or oauth2-proxy that sets
  `X-Forwarded-User` (configurable with `FOCUS_TRUSTED_USER_HEADER`). Never
  expose the app route without that proxy in front of it in live mode.
- The health check is `http` on `/health/live`.
- Workers run as a separate process type (`python -m focus.cli worker --loop --role app`) or as a task
  (`cf run-task focus-scheduling --command "python -m focus.cli worker --once --role app"`).
- Evaluator-role runs and the pipeline's other roles (source, stager, signer, evaluator) run in separate apps bound to
  their own user-provided services. Each process then holds only its own role's credentials
  (`docs/execution/INSTALL_AND_PIPELINE.md`).
- Run `python -m focus.cli preflight --role web` (or `worker`, `signer`) before a process serves or works.
- The filesystem is ephemeral. Live mode persists everything in SQL Server
  (`FOCUS_FN_*`). Fixture mode on Tanzu is for demos only.

## API and MCP in hosted environments

- REST: `/api/v1/*`, OpenAPI at `/api/v1/openapi.json`, human docs at
  `/api/docs`.
- MCP (future): set `FOCUS_MCP_ENABLED=true` to expose read-only
  Streamable-HTTP JSON-RPC at `/mcp`. It uses the same identity (Connect API
  key or Tanzu SSO) and the same grants and scope as REST. Commands are not
  MCP tools.
- Commands (`POST /api/v1/schedule-runs`, `/amendments`,
  `/disruption-claims`, `/excusal-decisions`, `/ie-reviews`,
  `/reported-observations`, `/scenarios`, `/explanations`) need the `X-CSRF-Token` from `GET /api/v1/context`,
  a same-origin request, an `idempotency_key` and, where state can race, an
  `expected_version`. They return `202` with a worker run id; poll
  `/api/v1/runs/<id>`. Run the worker as described above, or set
  `FOCUS_INPROCESS_WORKER=true` for a single-process demo.
- `POST /api/v1/runs/<id>/cancel` cancels a queued run. For a running run it records a durable cancel request:
  scenarios stop cooperatively and end `cancelled` with no result, and other handlers finish within their budget.
