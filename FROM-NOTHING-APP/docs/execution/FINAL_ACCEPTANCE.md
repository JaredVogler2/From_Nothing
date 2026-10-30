# Final acceptance (P24)

**Local application: see the result line below. Live release: BLOCKED by 13 gates (LG-01 to LG-13).**

| Claim | Status |
| --- | --- |
| Local application complete (S21 definition) on fixtures | **Yes**: `LOCAL_BUILD_COMPLETE` in `review/VERDICT-001.json`, from the acceptance run of commit `17f011e` in a fresh clone and a fresh Python 3.12 environment |
| Live verified (any capability) | **No.** Nothing ran against live SQL, a live key or a hosted platform. |
| SQL installed, grants applied, plan signed with a live key, deployed, CI activated | **None performed.** |
| Independent review | **Not performed.** `review/REVIEW-001` is an implementer self-check. |

The manual (`FOCUS_FROM_NOTHING_MASTER.txt`, SHA-256 `f81939211ef01f900ef5a3fe965e27f97f8c71a82832f5d541b3865ce205e5d5`)
is unchanged. Hosting targets are Posit Connect first, then VMware Tanzu, both on CPython 3.12.

## 1. Fresh checkout run-through (P24 step 1)

Commit `17f011e` was cloned fresh. A new virtual environment was built with `/usr/bin/python3.12 -m venv`, then
installed with `pip install --no-deps -r requirements.lock` and `pip install --no-deps -e .` (27 pinned packages,
`evidence/24/pip_freeze.txt`). Every command ran in an empty environment (`env -i`, no `FOCUS_*` settings). Receipts
are in `evidence/24/fresh/` and the environment is described in `evidence/24/fresh_environment.json`.

| Step | Command | Observed |
| --- | --- | --- |
| Import, no data | `python tools/check_imports.py`, then `python -m focus.cli status` | 110 modules; optional packages lazy; nothing written. Status: no source or plan heads, policy `rebuild-v1` ready |
| Fixture acquisition | `demo-bootstrap` | Deterministic `manufacturing_demo` runtime created |
| Durations, crews, calendars, graph; rich economic schedule; independent validation; staged-readback acceptance | `demo-run` | One source run. EXECUTABLE and FORECAST plans validated and ACCEPTED through stage, readback, sign and accept |
| Lifecycle, excusals, canonical score | `demo-run` (collections), then `evaluate --shift DEMO:2026-10-26:S1 --policy rebuild-v1 --as-of 2026-10-28T16:00:00Z` | Three collections, 14 progression entries and 3 badges appended. Three evaluations (80.9, 97.7, 61.5, labeled demonstration) and a pooled aggregate of 77.6 |
| Explanation | `explain --plan <EXECUTABLE> --team PROD-A --shift-id DEMO:2026-10-26:S1` | Complete deterministic explanation of the ranked actions |
| Dispatch | `dispatch-check --plan <EXECUTABLE> --action <first ranked action>` | `DISPATCHABLE`, with no reasons, against current facts |
| Scenario | `scenario --source-run <run> --changes '[{"type": "duration_statistic", "statistic": "P90"}]'` | `SCENARIO_ONLY`; every source and plan head unchanged afterwards |
| Migrations, preflight | `migrate --dry-run`, `preflight` | 9 versions, 0 findings. Preflight ok; the only warning is the ephemeral fixture session secret |
| Role-separated pipeline | `pipeline --key fresh-checkout --role source`, then `app`, `stager`, `signer`, `evaluator`, then `--status` | All 7 steps completed in order, with 7 outbox events |
| Served app | `serve --port <free>`, then HTTP GETs | `/health/live`, `/health/ready`, `/api/v1/context`, `/api/v1/openapi.json`, `/api/docs` and `/` answer 200. `/dashboard` redirects a signed-out visitor to the persona sign-in. `/mcp` answers 404 (off by default). Every response except the shared CSS is `no-store` |

The same flow is test A72 (`tests/acceptance/test_release.py`), which runs on every acceptance run. The browser
workflows (17 views in both layouts at phone and desktop widths) run in the Playwright suites.

## 2. Acceptance run (P24 step 2)

`python -m focus.cli acceptance` ran the whole suite once in the fresh environment, with one receipt per test node
(`evidence/24/receipts.json`). It judged every register entry (`tests/acceptance/map.json`); the per-case and
per-requirement tables are in `evidence/24/ACCEPTANCE.md`, and the full judgement is in `evidence/24/acceptance.json`.

| Measure | Result |
| --- | --- |
| Tests | 483 collected: **480 passed, 0 failed**, 3 skipped. The skips are the opt-in 100/150/300-aircraft benchmark sizes, which no case maps to; their subprocess runs are the P22 receipts |
| Cases A01-A72 | **72/72 PASS**: every named test ran and passed |
| Requirements C01-C39 | **39/39 PASS**, with **149/149 caller paths verified** |
| Required skips | none |
| Failures outside the register | none |
| Wall time | 892.0 s on CPython 3.12.3 |

A requirement passes only when all of these hold:

- its S20 cases pass;
- its own tests pass;
- every rule authority resolves;
- every source file exists;
- every caller path holds. A path holds when the caller exists, calls the callee by name, and a module-level callee is
  the very object the caller's module (or a local import) binds.

The checker rejected two wrong entries while the register was being written; see section 6.

Cases by primary stage:

| Stage | Cases | Result |
| --- | --- | --- |
| P02 | A01, A02 | all PASS |
| P03 | A03, A04 | all PASS |
| P04 | A05, A06, A07 | all PASS |
| P05 | A08, A09 | all PASS |
| P06 | A10, A11, A12, A13, A14 | all PASS |
| P07 | A15, A16, A17, A18 | all PASS |
| P08 | A19, A20, A21, A22, A23, A24 | all PASS |
| P09 | A25, A26, A27, A28, A29, A30 | all PASS |
| P10 | A31, A32, A33 | all PASS |
| P11 | A34, A35, A36, A37, A38, A39, A40, A41 | all PASS |
| P12 | A44, A45, A46 | all PASS |
| P14 | A42, A43, A49, A50 | all PASS |
| P15 | A47 | all PASS |
| P16 | A48, A51, A52, A53 | all PASS |
| P17 | A54, A55, A56 | all PASS |
| P18 | A57, A58, A59, A60, A61, A62, A63 | all PASS |
| P19 | A68 | all PASS |
| P20 | A69 | all PASS |
| P21 | A64, A65, A66, A67 | all PASS |
| P22 | A70 | all PASS |
| P23 | A71 | all PASS |
| P24 | A72 | all PASS |

## 3. Named counterexamples (P24 step 2)

| Counterexample | Case and requirement | Result | Tests |
| --- | --- | --- | --- |
| Repair or continuity move that would breach the activation cap | A38, C17 | PASS | 4 |
| In-progress job with an incomplete incoming predecessor | A26, C38 | PASS | 6 |
| Out-of-sequence charged once, never again in XP or economics | A60, C39 | PASS | 4 |
| Rework P75 from the elapsed cohort, not effort divided by crew | A17, C08 | PASS | 3 |
| Same-run CS lineage through serialization and pre-sign readback | A48, C12 | PASS | 5 |

## 4. Verification themes (P24 step 3)

| Theme | Result | Tests |
| --- | --- | --- |
| Bounded scale | PASS | 4 |
| Complete WIP census | PASS | 6 |
| Evaluation crash-recovery and correction boundaries | PASS | 5 |
| Publication crash-recovery boundaries | PASS | 4 |
| Source crash-recovery boundaries | PASS | 5 |
| Economics, commitment and scoring parity across stages and surfaces | PASS | 6 |
| Conditional and virtual forecast versus real dispatch | PASS | 6 |
| Posit Connect and Tanzu on Python 3.12; REST and MCP for application-to-application calls | PASS | 10 |
| Mobile and browser workflows in both layouts | PASS | 7 |
| Security: scope, cache and CSRF | PASS | 9 |

## 5. Capability register (P24 step 4)

`implemented`, `fixture_verified` and `live_verified` are separate claims. No capability is live-verified.

### Stages

| Stage | Implemented | Fixture verified | Live verified | Live dependency (exact effect today) |
| --- | --- | --- | --- | --- |
| P01-P02 runtime, policy, domain | yes | yes | no | Live mode refuses the demo identity; a live host without the schema starts `REPOSITORY_UNAVAILABLE` (LG-01, LG-02, LG-10) |
| P03-P04 census, source runs, snapshots | yes | yes | no | Live discovery is `not_probed` without a read principal; acquisition admits only complete, same-run datasets (LG-06) |
| P05-P07 team inference, eligibility, durations | yes | yes | no | Empty workforce feeds mean no executable crews (OD-10); no raw clock producer means rework cohorts are unavailable, never a flat 60 (OD-02) |
| P08-P10 calendars, graph, parts, QA phases | yes | yes | no | An open CS date basis blocks CS facts (OD-05); unknown QA hold locations block the root (OD-03); null part ECDs stay blocked or conditional |
| P11-P14 duty, crews, economics, MAX, commitments | yes | yes | no | No production cost rate means costs are unknown, never zero (OD-06, OD-12); the commitment purpose is FORECAST in the fixture (OD-14) |
| P15-P16 validator, publication, dispatch | yes | yes | no | No live key means live publish refuses (LG-04); pre-sign SQL readback has never run (LG-05) |
| P17-P18 lifecycle, excusals, score, XP | yes | yes | no | No producers means the live evaluator refuses (LG-07); the score policy is DEMONSTRATION (LG-08); no reviewers are mapped (LG-09) |
| P19-P21 UI, API, twin, scenarios, explanations | yes | yes | no | No grants on a host means every scoped call refuses (LG-10); explanations are deterministic (OPT-02) |
| P22 scale and optional solver | yes | yes | no | The solver is off by default (OPT-01); the heuristic is measured at 51 to 300 aircraft |
| P23 migrations, pipeline, CI | yes | yes | no | Schema not installed (LG-01), grants not applied (LG-03), CI not activated (LG-13) |
| P24 acceptance, review, release | yes | yes | no | Not deployed (LG-11); no independent review (LG-12) |

### Source capabilities

| Source capability | Implemented | Fixture verified | Live verified | Exact live effect today |
| --- | --- | --- | --- | --- |
| Complete WIP census | yes | yes (19/19 jobs; 51/51 aircraft at scale) | no | Not probed; every non-complete line would be reconciled without a cap |
| Status semantics (OD-13) | yes | yes | no | Unknown statuses are quarantined; PREVIEW and FORECASTED stay forecast-only |
| Flow standards and crew demand (OD-01) | yes | yes | no | Invalid, null or nonpositive TOTAL_MECHANICS rows become blocked jobs, never defaulted |
| Certification requirements and holdings (OD-09) | yes | yes | no | Positive-only requirement rows cannot prove cert-free work: unknown requirements block |
| Workforce assignment and attendance (OD-10) | yes | yes | no | Empty feeds mean no executable crews; forecasts stay conditional |
| SQL team inference | yes | yes | no | `infer_mechanic_teams.sql` is not installed; without inference or assignment no home team is asserted |
| Rework durations (OD-02) | yes | yes | no | No raw clock or episode producer: P75 cohorts are unavailable and affected work is blocked or conditional |
| Per-line calendars | yes | yes | no | Generation selection per line is required; a hole, an ambiguity or an out-of-bounds date blocks |
| CS windows (OD-05) | yes | yes | no | The date basis is open: CS facts block until the policy is decided |
| Parts shortages (ETRACS) | yes | yes | no | Null ECDs and blank USED_ON rows become blocked or conditional gaps |
| QA holds and buyoffs (OD-03) | yes | yes | no | Unknown hold locations block the root; no midpoint is invented |
| Economics (OD-06, OD-12) | yes | yes | no | No production default rate: line costs are unknown, never zero |
| Lifecycle observations (OD-15) | yes | yes | no | The live evaluator refuses (`LIVE_OBSERVATIONS_UNAVAILABLE`) |
| Source timezone policy | yes | yes | no | `FOCUS_SOURCE_TIMEZONE_MAP_JSON` is required; without it acquisition reports `SOURCE_TIMEZONE_POLICY_MISSING` |

### Optional features (disabled; exact effect)

| Gate | Feature | Effect |
| --- | --- | --- |
| OPT-01 | CP-SAT ordering stage | Plans come from the named heuristic alone; status `DISABLED`; every rule is still enforced |
| OPT-02 | BCAI explanations (OD-18) | Complete deterministic explanations; schedule and ranking unchanged |
| OPT-03 | MCP endpoint | `POST /mcp` answers 404; when enabled it is read-only with the same identity, grants and scope as REST |
| OPT-04 | Economic curves beyond LINEAR_DAILY | Such a term is UNSUPPORTED: listed as unknown, never zero, and never the basis of an improvement claim |

## 6. Defects found and fixed in P24

| Defect | Effect before | Fix | Test |
| --- | --- | --- | --- |
| Rendered pages carried the session's CSRF token without `Cache-Control: no-store` | A shared proxy or the browser cache could keep a signed-in page and its token | Every response except the shared static assets is `no-store` (`focus/web/security.py`) | `test_pages_carrying_the_session_csrf_token_are_never_cached` |
| `scenario --changes` help said "JSON object" | The documented form was refused (`BAD_CHANGE`); the command needs a list | Help text corrected, with an example | Fresh run-through step "Scenario" |
| Two register receipts named the wrong callee (`Eligibility.check_bundle`; the workforce activation cap inside the validator) | None in the application; the register would have overstated two caller paths | Corrected to `HoldingIndex.check_bundle` and to the validator's own independent `ValidationInput.activation_cap` | `test_path_receipts_reject_a_wrong_or_missing_call` |

REST and MCP parity was also added as a test: every read operation returns the same envelope, or the same refusal,
through REST and through the MCP tool another application calls
(`test_every_read_operation_answers_identically_over_rest_and_mcp`).

## 7. Versioned review (P24 step 5)

`review/REVIEW-001.md` and `review/VERDICT-001.json` are the first versioned review. No earlier review existed, so
001 was the smallest unused number. Neither file can be overwritten: the next review gets 002.

- **Kind:** `IMPLEMENTER_SELF_CHECK` (independent: false).
- **Local verdict:** `LOCAL_BUILD_COMPLETE`.
- **Live release:** `BLOCKED`.
- `python -m focus.cli review --verify 1` returns `MATCH` for every hash below.

| Bound item | SHA-256 |
| --- | --- |
| Manual | `f81939211ef01f900ef5a3fe965e27f97f8c71a82832f5d541b3865ce205e5d5` (MATCH) |
| Implementation (292 files) | `76c22dfafa789c61c6da4aa84bc007611223162c7ed9c83ada3a97a5e25e1928` |
| Tests (53 files) | `a62146080601193733c46acc87c6e9c6743a92e8def9d4d927ca4aefc10ba715` |
| Fixture source (55 files) | `04df23dcaaa2a9a1aa5eed7f118814a89901d6ecc5ddbd8cd8200e28d8622649` |
| Policy `rebuild-v1` | `98a57f5eb641a65d1c332e524d399fcf94d46a6c9954cdf3bdb97104c71f8ac4` |
| Acceptance run (`evidence/24/acceptance.json`) | `fcf333dbdbafe52ff5e8b729fe374aa7948b14566e32c883a90482292e0cfd58` |
| Git HEAD | `17f011e5d5b7ed38d3e4b96220b14b00e7d522e9` |

An independent review (LG-12) is required for a real release and was not performed.

## 8. Live release gates (P24 step 6)

Defined once in `focus/release/gates.py`, listed in every verdict, and mapped to owners and commands in
`RELEASE_CHECKLIST.md`. The rollback plan is `ROLLBACK.md`.

| Gate | Area | Missing | Effect today |
| --- | --- | --- | --- |
| LG-01 | SQL schema | FOCUS_FN migrations 001-009 not installed | Live hosts start `REPOSITORY_UNAVAILABLE`; `/health/ready` is 503; nothing is stored |
| LG-02 | Live repository adapter | The protocol mapping onto the FOCUS_FN tables is not built | Live storage stays unavailable even with the schema installed |
| LG-03 | Roles and grants | The generated grants are not applied; no logins are mapped | No live process holds its role's permissions |
| LG-04 | Signing key custody | No production key or `FOCUS_SIGN_KEY_REF` | Live publish refuses: no executable acceptance or dispatch |
| LG-05 | Pre-sign SQL readback | Never run against SQL Server | An executable live release cannot be claimed |
| LG-06 | Live source readiness | Empty workforce feeds (OD-10); OD-01 to OD-14 open | No executable crews; open semantics block with typed reasons |
| LG-07 | Live observation producers | OD-15 producers not approved | The evaluator refuses live: no live scores, points or XP |
| LG-08 | Score policy approval | OD-16 approval reference missing | Evaluations are labeled demonstration; personal grades disabled |
| LG-09 | Excusal reviewers | OD-17 reviewers not mapped | Claims are filed, but no live decision or relief |
| LG-10 | Host identity and grants | Identity provider and `FOCUS_GRANTS_JSON` not configured on a host | Scoped reads and commands refuse |
| LG-11 | Deployment | Nothing deployed to Posit Connect or Tanzu | No hosted instance |
| LG-12 | Independent review | Only implementer self-checks | A real release needs an independent verdict on the same hashes |
| LG-13 | CI and scheduling | The GitLab draft was never activated | Pipeline roles run only by hand or as Tanzu tasks |
| OPT-01 | CP-SAT stage | Disabled by default | Named heuristic only (acceptable) |
| OPT-02 | BCAI explanations | No approved provider | Deterministic explanations (acceptable) |
| OPT-03 | MCP endpoint | Disabled by default | 404 until enabled; read-only when enabled (acceptable) |
| OPT-04 | Economic curves | Only LINEAR_DAILY | Other curves are unknown, never zero (acceptable) |
