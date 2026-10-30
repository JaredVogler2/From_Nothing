# REVIEW-002: FOCUS From Nothing release review

- Kind: **IMPLEMENTER_SELF_CHECK** (independent: false)
- Reviewer: Claude Code build session (implementer self-check)
- Recorded (host clock, UTC): 2026-10-30T18:49:38Z
- Local verdict: **LOCAL_BUILD_COMPLETE**
- Live release: **BLOCKED** (13 blocking gates)

## Bound hashes

| Item | SHA-256 | Detail |
| --- | --- | --- |
| Manual | `f81939211ef01f900ef5a3fe965e27f97f8c71a82832f5d541b3865ce205e5d5` | MATCH against STATUS.json |
| Implementation | `df2e2ce070cf3fcff409354e2eabb0b9c624eb9005feb0ddd4ec43c60a179120` | 299 files (git) |
| Tests | `fe3b92a8c99919e94ae1f52d9355e63a1f019219faffe0cfd95735ad4f533472` | 56 files |
| Fixture source | `04df23dcaaa2a9a1aa5eed7f118814a89901d6ecc5ddbd8cd8200e28d8622649` | 55 files; live: NOT_ACQUIRED: no live source run exists in this build |
| Policy `rebuild-v1` | `98a57f5eb641a65d1c332e524d399fcf94d46a6c9954cdf3bdb97104c71f8ac4` | canonical policy hash |
| Acceptance run | `b8fdb89a2bf1984f49896d7253d7d2b7b4a2d73f157c0eeb4980b0fc61ae8cb7` | `evidence/production-scale/acceptance/acceptance.json`, Python 3.12.3 |
| Git HEAD | `edd1e35c53a16aa0a9a38a8f77eba2ea9ea7eaba` | uncommitted changes in bound files: false |

## Acceptance

- Cases: 72/72 pass
- Requirements: 39/39 pass, 149/149 caller paths verified
- Named counterexamples: activation_repair PASS, in_progress_edge PASS, oos_double_charge PASS, rework_p75 PASS, same_run_cs_readback PASS
- Verification themes: bounded_scale PASS, complete_wip_census PASS, crash_recovery_evaluation PASS, crash_recovery_publication PASS, crash_recovery_source PASS, economics_commitment_scoring_parity PASS, forecast_versus_dispatch PASS, hosting_api_mcp PASS, mobile_browser_workflows PASS, security_scope_cache_csrf PASS
- Test run: {"collected": 496, "exitstatus": 0, "passed": 493, "skipped": 3}

Local blockers: none.

## Live release gates

| Gate | Kind | Area | Effect today | Resolution |
| --- | --- | --- | --- | --- |
| LG-01 | BLOCKING | SQL schema | A live host starts with REPOSITORY_UNAVAILABLE: /health/live is 200, /health/ready is 503, nothing is stored and pipeline steps stop with typed unavailability. | A DBA with DDL rights re-runs queries/discovery/fn_collisions.sql, then applies the reviewed bundle with `migrate --apply --catalog live --installer <subject> --confirm-manifest <sha256>`. |
| LG-02 | BLOCKING | Live repository adapter | Even with the schema installed, live storage stays REPOSITORY_UNAVAILABLE. | Engineering builds the adapter and verifies it against an installed non-production database. |
| LG-03 | BLOCKING | Roles and grants | No live process holds its role's permissions; the separation of duties exists only as a reviewed script. | A DBA applies the grants after 001-009 and maps one login per process (web, source, stager, signer, evaluator). |
| LG-04 | BLOCKING | Signing key custody | Live publication refuses, so there is no executable acceptance and no executable dispatch. | The security owner provisions the key, referenced only in the signer's environment. |
| LG-05 | BLOCKING | Pre-sign SQL readback | An executable live release cannot be claimed; only the fixture rehearsal of the same protocol exists. | Rehearse on a non-production database once LG-01 to LG-04 are resolved, and keep the receipts. |
| LG-06 | BLOCKING | Live source readiness | Live snapshots have no executable crews; jobs that depend on an open semantic are blocked or conditional with typed reasons. | Source owners populate the feeds and record decisions OD-01 to OD-14 in the policy. |
| LG-07 | BLOCKING | Live observation producers | The evaluator refuses live (LIVE_OBSERVATIONS_UNAVAILABLE), so there are no live scores, points or XP. | Approve and install the producers, granted FOCUS_FN_ObservationProducer. |
| LG-08 | BLOCKING | Score policy approval | Every evaluation is labeled demonstration and personal grades stay disabled. | The policy owner approves the score policy and records score.policy_approval_ref. |
| LG-09 | BLOCKING | Excusal reviewers | Claims can be filed, but no live decision can be recorded, so no relief is applied. | The operations owner names the reviewers in FOCUS_GRANTS_JSON. |
| LG-10 | BLOCKING | Host identity and grants | Every scoped read and command refuses with UNAUTHENTICATED or FORBIDDEN; only health and the landing page answer. | The platform administrator configures the identity provider and the server-owned grants per host. |
| LG-11 | BLOCKING | Deployment | No hosted instance exists. | The platform owner deploys the reviewed commit (`rsconnect deploy api`, then `cf push`) after preflight passes. |
| LG-12 | BLOCKING | Independent review | A real release still needs an independent verdict on the same hashes. | An independent reviewer writes the next review/REVIEW-NNN.md and VERDICT-NNN.json against the bound hashes. |
| LG-13 | BLOCKING | CI and scheduling | Pipeline roles have no scheduler; they run only by hand or as Tanzu tasks. | The CI owner activates .gitlab-ci.yml with protected environments per role. |
| OPT-01 | OPTIONAL | CP-SAT ordering stage | Plans come from the named heuristic alone, with the typed solver status DISABLED. Every rule is still enforced. | The owner decides with SOLVER_ENABLEMENT.md and installs requirements-solver.txt. |
| OPT-02 | OPTIONAL | BCAI explanations | Explanations are the complete deterministic ones; the schedule and ranking are unchanged. | Approve the provider and configure it; responses still pass the strict validators. |
| OPT-03 | OPTIONAL | MCP endpoint | POST /mcp answers 404. When enabled, it is read-only and uses the same identity, grants and scope as REST; commands, signing and grading are never exposed over MCP. | Set FOCUS_MCP_ENABLED=true on the host that serves application-to-application callers. |
| OPT-04 | OPTIONAL | Unsupported economic curves | A term with any other curve is UNSUPPORTED: its line cost is never zero, it is listed as unknown, and no improvement is claimed from it. | Add the curve to the economics registry with its exact evaluator and tests. |

## Earlier reviews

- 001: `review/REVIEW-001.md` `1bdf615e71ec0066`, `review/VERDICT-001.json` `43d0d8fd74e73d2a`

## Statement

This verdict is an implementer self-check, not an independent review. It binds the hashes above; a changed file needs a new review.
