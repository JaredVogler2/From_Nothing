# FOCUS From Nothing — resolved roots

This file records the one-time app-root substitution that S00/P01 require
when the declared default root is not used.

| Item | Value |
| --- | --- |
| Manual (unchanged, read-only input) | `<repo>/FOCUS_FROM_NOTHING_MASTER.txt` |
| Manual SHA-256 at build start | `f81939211ef01f900ef5a3fe965e27f97f8c71a82832f5d541b3865ce205e5d5` |
| Declared default APP_ROOT (manual S00) | `C:\Users\zy588d\PycharmProjects\Applications\FOCUS\FROM-NOTHING-APP` |
| **Resolved APP_ROOT (this build)** | `<repo>/FROM-NOTHING-APP` |
| Absolute path in the build container | `/home/user/From_Nothing/FROM-NOTHING-APP` |
| Repository | `jaredvogler2/from_nothing`, branch `claude/magical-ramanujan-dxyt8a` |

## Why the substitution

The build ran in a Linux cloud container where the Windows default path does
not exist, and all output has to be committed to the `From_Nothing` repository
that holds the manual. The application therefore lives in the sibling folder
`FROM-NOTHING-APP/` next to the manual. That mirrors the declared layout
(`...\FOCUS\FROM-NOTHING\` holds the manual, `...\FOCUS\FROM-NOTHING-APP\`
holds the app). The manual itself is never modified.

On a Windows checkout of the repository at
`C:\Users\zy588d\PycharmProjects\Applications\FOCUS\FROM-NOTHING`, the app
root becomes `...\FOCUS\FROM-NOTHING\FROM-NOTHING-APP`. The folder can also
be moved verbatim to the declared default
`C:\Users\zy588d\PycharmProjects\Applications\FOCUS\FROM-NOTHING-APP`.
The application never hardcodes either path: it resolves its root from
`FOCUS_APP_ROOT` when set, otherwise from the location of the installed
`focus` package (`focus/config.py::resolve_app_root`).

## Operating directives recorded from the user during the build

These directives came from the user while the build was running. They narrow
choices the manual leaves open; none of them weakens a manual requirement.

1. **Python 3.12.** The virtual environment, `requirements.lock` and every
   test run use CPython 3.12 (`/usr/bin/python3.12`, 3.12.3 in the container).
   `pyproject.toml` declares `requires-python = ">=3.12"` as S02 requires, and
   `.python-version` / `runtime.txt` pin 3.12 for the hosting platforms.
2. **Hosting: Posit Connect first, then VMware Tanzu.** The Flask host is a
   plain WSGI object `app:app` that works under a URL prefix (Posit Connect
   serves content at `/content/<guid>/`). Connect's trusted
   `RStudio-Connect-Credentials` identity header is supported. Tanzu
   (Cloud Foundry) support covers `$PORT`, `VCAP_SERVICES` user-provided
   SQL credentials, a `manifest.yml` / `Procfile` / `runtime.txt`, and the
   HTTP health check at `/health/live`. See `docs/execution/DEPLOYMENT.md`.
3. **Sound API endpoints and MCP capability for the future.** A single
   operation registry (`focus/web/operations.py`) defines every `/api/v1`
   operation once: path, method, grants, input/output JSON Schema, and
   whether it is read-only. Three things are generated from that registry:
   the Flask routes, an OpenAPI 3.1 document at `/api/v1/openapi.json`, and an
   MCP tool/resource catalog (`focus/mcp/`). An optional, default-disabled
   read-only MCP JSON-RPC endpoint (`FOCUS_MCP_ENABLED=false`) uses the same
   scope and grants as the REST API.

## Interpreter and dependency resolution

- Build interpreter: CPython 3.12.3 (Linux x86_64).
- Exact package versions resolved and tested are in `requirements.lock`.
- Browser verification uses the Chromium build pre-installed in the container
  (`/opt/pw-browsers`) driven by Python Playwright 1.63.0, which matches that
  Chromium build. Do not run `playwright install` in the container. On a
  workstation, run `python -m playwright install chromium` as S02 states.
