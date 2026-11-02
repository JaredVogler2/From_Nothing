# FOCUS From Nothing — decisions register

The manual (`FOCUS_FROM_NOTHING_MASTER.txt`) is the only specification. This
register records how its rules map to code, which values are user
requirements versus fixture choices, and which live semantics stay open.
The machine-readable policy is `config/policies/rebuild-v1.json`, which
includes `shift_fixture.json` and `grading-demo-v1.json`. Every policy value
carries an authority label:

| Label | Meaning |
| --- | --- |
| `user_requirement` | A constant the user specified (S01/S09). Code never changes it, and it is not re-approved. |
| `manual_specified` | A value the manual fixes explicitly. |
| `fixture_default` | An explicit demonstration choice, valid in fixture mode only. |
| `demo_default` | A proposed S14 grading/recognition demo default, never an approved labor grade. |
| `open_decision` | A live semantic with no owner decision yet. The fixture choice is recorded and the live effect is a precise readiness gap (OD-02..OD-17 in the policy file). |
| `owner_decision` | A live semantic the owner has decided. The value's ref records the decision and its date, and the decision leaves the open list (`decided_decisions` in the policy file). |

## Capabilities (P01 step 3)

Four separate products are kept apart (S01). Each candidate plan carries
exactly one purpose.

| Capability | Allowed inputs | Never allowed |
| --- | --- | --- |
| Complete inventory | Every census job, including blocked, quarantined and excluded jobs, each with a reason. | Silent exclusion; a fixed line cap. |
| `FORECAST` | Conditional part ETAs (precision-aware floors), virtual MHE/PSC/TST support pools (10 synthetic people per team/shift), inferred nominal availability. Every such fact is labeled conditional. | Claiming a part is received or a named mechanic is present. |
| `SCENARIO` | Same as forecast, inside an isolated namespace copied from an immutable snapshot. | Writing source/plan heads, goals, commitments, actuals or official grades. |
| `EXECUTABLE` | Real named people with admitted roster/attendance availability, confirmed part releases, known requirements, sourced QA hold locations. | Synthetic or virtual people; conditional ETAs; unknown requirement census. |
| Current dispatch | An accepted `EXECUTABLE` plan, rechecked against current certs, attendance, graph, QA, parts, calendar, CS and source freshness. | Treating a stale accepted plan as executable. |

Inferred home team (S07) is nominal forecast evidence. It is never current
attendance. A conditional ETA is never physical receipt.

## Architecture decisions

- **D-01 One package, one host.** The installable `focus` package (Python 3.12) and one Flask app built by `create_app(settings, repositories, clock)`. Importing it is side-effect free: no database, acquisition, file write, solver or port action.
- **D-02 One rule authority per concern.** Calendar/shift windows live in `focus/calendar.py`, eligibility in `focus/eligibility.py`, graph construction in `focus/data/graph.py`, duty accounting in `focus/engine/ledger.py`, economics in `focus/economics/`, scoring in `focus/performance/score.py`. The optimizer, repair, validator, serializer and UI consume these services. None of them re-implements a rule with different units.
- **D-03 Independent validator.** `focus/validation/` reconstructs duty, crews, windows, graph releases and parts floors from the immutable snapshot plus the candidate's assignments. It never reads optimizer feasibility flags or candidate-declared hashes. It shares only the pure rule authorities from D-02 and reimplements duty reconstruction (`validation/duty.py`) separately from the ledger.
- **D-04 Integer UTC minutes inside the engine.** Domain contracts carry aware UTC datetimes (half-open). The engine converts them once to integer minutes since the Unix epoch, so arithmetic stays exact and DST is handled by actual instants, never by a packed day. Source timestamps with seconds become "not before" floors by ceiling to the minute.
- **D-05 Exact money.** Integer cents or `fractions.Fraction` cents. Floats are never an authority. A decimal daily rate that converts to a fractional cent stays an exact rational.
- **D-06 Canonical JSON.** UTF-8, sorted keys, compact separators, `allow_nan=False`. Decimals are strings, fractions are `{num,den}`, dates ISO, datetimes RFC3339 UTC with preserved precision. Decoding rejects duplicate keys, unknown fields and non-finite numbers. Hashes cover the canonical payload, excluding the record's own hash/signature.
- **D-07 Lossless identity.** `JobKey(site, program, line, native_id, revision, operation, effectivity)` keeps native text byte-for-byte. Transport IDs are SHA-256 digests mapped reversibly in the snapshot index, and construction rejects collisions. The same SOI on two lines, or in two revisions, always gives two keys.
- **D-08 Phases.** Each job expands into typed phases: `WORK` segments, `IP_QA` holds, `FINAL_QA`, `OTHER_BUYOFF` branches and a zero-work `RELEASE` (AND) milestone. Successors depend on `RELEASE`. Work longer than `duration.max_segment_minutes` is split into chained segments that sum back to the parent's elapsed time and standard effort.
- **D-09 Duty ledger with derived transfers.** The ledger stores anchor duties (WORK/WAIT/TOOL/OTHER/carry-in) per person. TRAVEL duties are derived from each consecutive anchor pair inside a shift. Insert, remove and swap therefore always re-derive both neighbors, and a failed mutation leaves nothing behind.
- **D-10 Rich MAX = list scheduling + exact-cost local search.** The neutral named seed is a serial schedule-generation pass over the ready frontier (committed first, then critical-path urgency, then FIFO availability, then stable key). `EconomicsRegistry.derive_priority_weights` then reorders dispatch. Delay repair, continuity and bounded neighborhoods (priority swaps plus atomic ledger relocations) are kept only when the exact complete-fleet lexicographic comparison improves and the independent validator passes. `global_optimality_proven` is always false for the heuristic.
- **D-11 Fixture repository = the same protocol as SQL.** `fixtures/runtime/<scenario>/` stores canonical immutable envelopes with atomic writes (temp file + `os.replace`), idempotency indexes, scoped lock directories and versioned heads that are written last. The SQL repository implements the same interface against the proposed `FOCUS_FN_*` tables (S06). Production never falls back to fixture storage.
- **D-12 Test signing keys are fixture-only.** HMAC-SHA256 with domain separation `FOCUS_PLAN_V1\0`. A fixture key ID always starts with `TEST-` and cannot accept an executable live plan.
- **D-13 Operation registry (user directive: sound API, MCP-ready).** `focus/web/operations.py` declares every API operation once. Flask routes, `/api/v1/openapi.json` (OpenAPI 3.1) and the MCP catalog (`focus/mcp/`) are generated from it. Read operations are safe and idempotent GETs. Commands are POSTs that require trusted scope, a CSRF token, an idempotency key and an expected version, and return `202` with a worker run ID.
- **D-14 Hosting (user directive).** Posit Connect first, then VMware Tanzu. The WSGI entrypoint is `app:app`. URLs are prefix-safe (`request.script_root` plus a `<meta name="focus-base">` read by the JS). Identity providers: `fixture` (demo login, visibly labeled), `posit_connect` (trusted `RStudio-Connect-Credentials` header) and `trusted_header` (Tanzu SSO / oauth2-proxy header named by `FOCUS_TRUSTED_USER_HEADER`). Grants are always server-owned (`FOCUS_GRANTS_JSON`). Tanzu `$PORT` and `VCAP_SERVICES` are read only by `focus/config.py`.
- **D-16 Performance semantics (P17/P18).** Recorded in `PERFORMANCE_POLICY.md`:
  - B0 comes from the first accepted executable plan of each obligation day; later replans only propose.
  - A completion credits its goal inside the shift window, then the authorized OT window, then early; otherwise it is
    offplan U where it happened.
  - Bands apply to the displayed one-decimal score, rounded once after every sum.
  - Planned OOS work keeps its frozen XP: the OOS charge lives once, in O ("no second charge in bonus/XP").
  - Offplan output never earns XP.
- **D-15 Python 3.12 (user directive).** The development, lock and test interpreter is CPython 3.12. `requires-python = ">=3.12"` per S02.
- **D-17 Scoped canonical API (P19).**
  - Reads go through `focus/web/serializers.py`. They load the admitted snapshot read-only (never `snapshot_for_run`, which may rebuild and store) and the end-to-end verified accepted plans.
  - Explicit team or person parameters are checked against the server-owned scope before any repository read.
  - People outside the scope are redacted. A job or goal outside the scope is "not found".
  - Cost and contract terms need an economics grant (`manager`, `scenario_planner`); otherwise they are withheld (null with `ECONOMICS_RESTRICTED`), never zero.
  - Caches hold only content-addressed objects (snapshot by id, verified plan by head and version, goal registry by accepted set and policy). Scoped projections are keyed by scope, filters and canonical revision.
  - Commands store server-derived authority (for example the excusal reviewer role and the requester's teams) under `_server`. Client bodies cannot carry it because every command schema rejects unknown fields.
  - Amendments use the goal's amendment count as the optimistic version, and excusal decisions use its decision count.
  - "Knowledge time" is the clock, never before the latest collected observations, so reads, version checks and workers agree. Live it equals the clock; in a fixture replay it moves to the last observation point.
- **D-18 Operational views and scenarios (P20).**
  - **One dispatch rule.** `publication.dispatch.assess_action` is the pure current-facts rule that `dispatch_check` applies, so the Team Lead ranking and the dispatch check can never disagree. Only `dispatch_check` records repair requests.
  - **One amendment rule.** `performance.amendments.assess_addition` is the pure readiness and crew-feasibility rule behind both amendment proposals and `amend_goal`.
  - **Scenarios** (`analytics/scenarios.py`) copy the admitted snapshot into their own namespace and apply typed, allowlisted changes. A same-settings baseline and the changed copy run through the same optimizer and validator with purpose SCENARIO.
  - **Scenario isolation.** Publication refuses SCENARIO candidates outright (`PURPOSE_NOT_PUBLISHABLE`), so no head, frozen goal, commitment, actual or evaluation can change. Queued runs can be cancelled; running ones are bounded by their budget.
  - **`demo-run`** (`pipeline.demo_run`) runs the whole fixture pipeline idempotently: a rerun leaves the runtime byte-identical.
  - **Incumbent heads only move forward in acceptance time.** Re-bootstrapping a runtime that already published a newer plan no longer regresses the head to the fixture's previous plan.
  - **Views render into detached containers.** Only the latest navigation is swapped into the host, so overlapping hash changes never interleave content.
- **D-19 Twin, scenario completion and grounded explanations (P21).**
  - **Twin** (`analytics/twin.py`, `GET /twin`). Cells are keyed by source run, accepted plans, policy and evaluation revision, plus team, shift start date, shift number and line. The layers stay separate:
    - observed: the stored canonical evaluation evidence and verified lifecycle facts, never recomputed;
    - planned: the accepted EXECUTABLE plan, with effort under the frozen-goal rule `publication.serializer.earning_phase` and duty from the ledger;
    - forecast: the accepted FORECAST plan, labeled conditional, with virtual duty;
    - unknown: what no layer can state.
  - **Twin reconciliation.** The checks are returned with every response; a failed check makes the twin `unknown`, never `ready`.
    - Cells sum exactly to team, shift, line and grand totals.
    - Job states partition the census in every layer.
    - Planned effort equals the frozen B0 of the plan of record.
    - Observed effort equals each stored evaluation.
    - Duty equals the ledger.
  - **Scenario changes.** Two typed changes are added:
    - `travel_minutes`, a policy toggle. Tool capacity stays a sourced fact.
    - `task_priority`. A fixed rank leads the dispatch key, so search cannot undo it, but readiness, parts, QA, crews and defended commitments still decide. It is refused for EXECUTABLE and FORECAST.
  - **Scenario namespace and cancellation.** Rebuilt snapshots are never written to the live namespace (`snapshot_for_run(store=False)`). A running scenario stops cooperatively on a durable cancel request (`workers.RunCancelled`, run status `cancelled`, no result).
  - **One ranking.** `publication.dispatch.ranked_actions` is the one ranking, shared by Team Lead and explanations.
  - **Explanations** (`explanations/`).
    - Pipeline: deterministic candidates, then privacy-minimized fact rows (no person ids), then a data-only versioned prompt, then the provider (none, mock, recorded or BCAI), then strict validation, then an immutable record with audit hashes.
    - Validation covers the complete schema, every claim against its fact (exact; tolerance only by explicit policy and never for money) and every narrative word: grounded ids, labels and values, or a small approved vocabulary.
    - Any failure returns the complete deterministic explanation in the unchanged rank.
    - The default provider is none. BCAI is optional, https-only, uses a referenced credential, and has bounded timeouts and retries.
- **D-20 Complete-scope scale and the optional solver (P22).**
  - **Scale shortcuts are exact.** No shortcut prunes a candidate, so the only feasible rare-role crew is never
    removed. Each is pinned by tests to the rule it replaces or to a brute-force definition.
    - `DutyLedger.person_conflict` runs `plan`'s first per-person checks (window, pool, cross-shift and located
      overlap).
    - `DutyLedger.cap_problem` runs `plan`'s pool-level activation check.
    - Transactions snapshot only the containers that are mutated in place.
    - The cross-shift check bisects a person's sorted rows by end, instead of rebuilding the list on every call.
    - Qualification reuses a whole-shift-window answer, because validity is interval-monotone.
    - Transport ids and snapshot verification are memoized.
  - **Outputs verified identical.** Old and new code gave the same assignments, duties, dispositions, commitments
    and costs on the demo and at 51 and 150 aircraft.
  - **Scale fixture.** The synthetic scale census also carries sourced in-process QA holds and a forecast-only
    support test on one aircraft in ten. These are derived rows, so the random stream and every other row are
    unchanged.
  - **The solver proposes orders, never plans.** CP-SAT solves a pooled relaxation and proposes job orders. The
    same named decoder, the independent validator and the exact comparison judge each order.
    - A probe is accepted only when it is strictly better at a substantive level; a signature-only tie is refused.
    - The outcome is a typed status (`DISABLED` ... `IMPROVED`) recorded in every receipt.
    - The stage is off by default; switches are `solver.enabled` and `FOCUS_SOLVER_ENABLED`.
    - Bounds: wall-clock, probes, memory, one seeded worker, and cancellation through `StopSearch`.
    - `global_optimality_proven` stays false.
  - **Benchmark.** `benchmark --complete-scope` and `tools/benchmark_solver.py` run each size in its own process.
    The bounded 51-aircraft cases are in the default test suite; 100, 150 and 300 aircraft run with
    `--benchmark-sizes`.
- **D-21 Migrations, roles, pipeline and retention (P23).**
  - **One versioned set.**
    - `sql/migrations/001`-`009` (001 is S06 verbatim; 002 is new; 003-009 are the reviewed drafts with their
      statements unchanged).
    - Rollbacks and the grants script are generated from one authority each (`rollback_script`, `ROLE_GRANTS`) and
      checked for freshness.
    - Migration files that are not versioned are never applied, so other modules' design drafts stay out.
  - **Runner protocol.**
    - Checksum ledger; static lint; a plan against the catalog.
    - One transaction per version under `FOCUS_FN_MIGRATIONS`, verifying every declared object before the ledger row.
    - Rollback only of the latest, data-free version.
    - Live apply only with an installer subject plus the reviewed manifest SHA-256 and the CREATE TABLE privilege.
      Never implicit, and never run by this build.
  - **Query files stay authorities.** `queries/workforce` and `queries/durations` run as parameterized batches from the
    source worker. No copy is installed as a procedure, so there are not two bodies to drift.
  - **Roles.** app (reader and command), source, stager, signer, evaluator, observation producer, retention. The grant
    lint keeps separation of duties: only the signer accepts, only the stager stages and reads back, only the source
    worker promotes, and the app reader executes nothing. The installer is a DBA principal; no script grants DDL.
  - **Pipeline** (`focus/orchestration.py`).
    - Durable steps, each owned by one role.
    - Needs-gated, with input hashes; optimistic-version leases with expiry takeover; at most three attempts.
    - Exactly one outbox event per completed step, built from the recorded state, re-emitted after a crash.
    - `publish(until="readback")` splits the stager from the signer, so signing never precedes a clean readback.
    - `demo-run` and the evaluator step share one observe-and-evaluate authority.
  - **Live storage.**
    - `SqlStore` reports one typed unavailability until 001-009 are installed and the live repository adapter exists.
      The host starts, and `/health/ready` explains.
    - The preflight's `--probe-sql` reads the ledger read-only.
  - **Retention never deletes.** The operator supplies the period. Accepted history, evidence, references and legal
    holds are always preserved. Fixture archives are SHA-256 receipted and restorable; live retention is the installed
    procedures only. (Display parts, a cache rebuilt from those kept records, are the one exception: D-24.)
- **D-22 Shift cycle speed (owner direction, November 1, 2026).**
  - The pipeline publishes the executable plan first; the forecast follows on the same admitted snapshot, so dispatch
    never waits for it (`INSTALL_AND_PIPELINE.md` section 4).
  - Pipelines repair the accepted plan between daily full solves (policy `planning.*`, default `auto`). A repair keeps
    each accepted placement whose slot and crew still hold, re-places the rest over the whole horizon and redispatches
    by the cost of delay, so delivery dates and penalties still follow every shift's results. The FINAL validation gates it; a failed or impossible
    repair falls back to the full solve. Each scheduled full solve records how far the repair would have landed
    (`PLAN_REPAIR.md`).

- **D-23 Display package (owner direction, November 1, 2026).** The web reads small, partitioned display parts that the
  pipeline publishes, and never loads a snapshot or decodes a plan artifact (`DISPLAY_PACKAGE.md`).
  - Acquisition writes the snapshot's parts before the source head moves. The signer writes each plan's parts and signs
    their manifest under its own domain (`FOCUS_DISPLAY_V1`) before the plan head moves.
  - Every projection runs on the slice it needs. Whole-fleet lists page. The goal registry comes from the signed
    summaries.
  - The digital twin stays on the whole-snapshot path behind `FOCUS_TWIN_FULL_LOAD` until its fleet-wide base is
    precomputed.
- **D-24 Display retention (owner direction, November 2, 2026).** Display parts are deleted once nothing reads them
  (`DISPLAY_PACKAGE.md`, Retention). Without this, every shift adds up to 5.9 GB of parts at 50 aircraft.
  - **Why deleting is allowed here.** A display part is a cache. It is rebuilt from records kept forever (the admitted
    snapshot or the accepted payload) and must hash to its manifest, which for a plan is signed. Manifests, display
    receipts and candidate digests are never deleted, so every deleted part can be rebuilt and checked against the
    original. The store refuses deletion from every other collection (`DERIVED_COLLECTIONS`).
  - **Kept whole:** the current packages; any package that can still become current (an unpromoted run, an acceptance
    whose head has not moved yet, a plan that can still be accepted); and every package of a head that moved less than
    the grace period ago (60 minutes by default).
  - **Kept forever:** each accepted plan's `summary` and `goals:<date>` parts, which the history views and the goals of
    record read (3.3 MB per shift at 50 aircraft).
  - **When it runs.** A pipeline job before acquisition, holding the display parts lock that every package writer
    holds. It never holds back the shift's plan.
  - **Live** needs the pending live adapter to implement the store's three retention operations for `display_parts`,
    run by the retention role.

## Semantic gaps (live), each a precise readiness effect

These are the policy file's `open_decisions`. None of them blocks local
fixture implementation (S00). Each one blocks the matching live executable
capability until the owner admits the semantic:

- OD-01 TOTAL_MECHANICS simultaneity: **decided by the owner on 2026-10-31.** TOTAL_MECHANICS is the number of mechanics
  that must be on the job at once to work it (SIMULTANEOUS). It no longer withholds live jobs.
- OD-02 raw clock intervals for elapsed P75
- OD-03 QA hold locations/inspector roles
- OD-04 labor breaks and OT authority
- OD-05 CS offsets/date basis
- OD-06 commercial due
- OD-07 concurrency meanings
- OD-08 rework classification source
- OD-09 affirmative no-cert census
- OD-10 assignment/attendance feeds (currently empty)
- OD-11 shift-start travel origin
- OD-12 economics terms/caps/checkpoints
- OD-13 master shop-order status meanings
- OD-14 which accepted plan purpose drives delivery commitments (fixture: FORECAST)
- OD-15 live observation producers: no S04 table carries lifecycle facts, raw actual intervals, claims, decisions,
  amendments or defect evidence. The `queries/actuals` readers and the `008_lifecycle.sql` landing tables are
  proposed. Live collection reports `NOT_PROBED`, so official evaluation stays UNKNOWN until producers and receipts exist.
- OD-16 score policy approval: `score.policy_status` is DEMONSTRATION with no `score.policy_approval_ref`. An OFFICIAL
  grade needs the owner's approval receipt; personal grades need their own policy (both disabled).
- OD-17 excusal reviewer identity: decisions require the server-granted `excusal_decider` role (`disruption.reviewer_roles`)
  and never self-review; the live mapping of reviewer subjects to that grant is the operator's.
- OD-18 explanation provider: no BCAI endpoint, credential or response adapter is approved. Live explanations stay deterministic
  (`FOCUS_LLM_PROVIDER=none`) until an approved https endpoint returns the `focus-explain-v1` contract. Its outputs are validated like
  any provider's.

## Confirmation (P01 detailed requirement)

This manual contains every specification needed to implement the
application. No prior code, chat, donor application or separate document is
a dependency. Constants supplied by the user were recorded as
`user_requirement` without asking for blanket re-approval.
