# Final acceptance (P24)

**Local application: see the result line below. Live release: BLOCKED by 13 gates (LG-01 to LG-13).**

| Claim | Status |
| --- | --- |
| Local application complete (S21 definition) on fixtures | RESULT_PENDING |
| Live verified (any capability) | **No.** Nothing ran against live SQL, a live key or a hosted platform. |
| SQL installed, grants applied, plan signed with a live key, deployed, CI activated | **None performed.** |
| Independent review | **Not performed.** `review/REVIEW-001` is an implementer self-check. |

The manual (`FOCUS_FROM_NOTHING_MASTER.txt`, SHA-256 `f81939211ef01f900ef5a3fe965e27f97f8c71a82832f5d541b3865ce205e5d5`)
is unchanged. Hosting targets are Posit Connect first, then VMware Tanzu, both on CPython 3.12.

## 1. Fresh checkout run-through (P24 step 1)

RESULT_PENDING

## 2. Acceptance run (P24 step 2)

RESULT_PENDING

## 3. Named counterexamples (P24 step 2)

RESULT_PENDING

## 4. Verification themes (P24 step 3)

RESULT_PENDING

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

RESULT_PENDING

## 7. Versioned review (P24 step 5)

RESULT_PENDING

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
