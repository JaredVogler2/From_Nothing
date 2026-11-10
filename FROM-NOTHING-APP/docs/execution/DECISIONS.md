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
| `open_decision` | A live semantic with no owner decision yet. The fixture choice is recorded and the live effect is a readiness gap named by the decision's id (OD-02 to OD-05 and OD-07 to OD-17 in the policy file; OD-01 and OD-06 are decided). |
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
    *Superseded by D-26:* each shift is scored against the plan accepted before it starts.
  - A completion credits its goal inside the shift window, then the authorized OT window, then early; otherwise it is
    offplan U where it happened. *Superseded by D-26:* never early.
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
  - **One amendment rule.** `performance.amendments.assess_addition` is the pure readiness and crew-feasibility rule behind both amendment proposals and `amend_goal`. `amend_goal` also refuses `PLAN_NOT_OF_RECORD` and `SHIFT_ENDED`, and since D-26 `SHIFT_NOT_STARTED`; the Team Lead view lists no proposal outside the shift window, and the `goal_amendments` feed quarantines a row whose `DECISION_AT_UTC` is outside it.
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
    - `sql/migrations/001`-`010` (001 is S06 verbatim; 002 is new; 003-009 are the reviewed drafts with their
      statements unchanged; 010 was added by D-25).
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
    - `SqlStore` reports one typed unavailability until 001-010 are installed and the live repository adapter exists.
      The host starts, and `/health/ready` explains.
    - The preflight's `--probe-sql` reads the ledger read-only.
  - **Retention never deletes.** The operator supplies the period. Accepted history, evidence, references and legal
    holds are always preserved. Fixture archives are SHA-256 receipted and restorable; live retention is the installed
    procedures only. Two owner-directed exceptions delete records: display parts, a cache rebuilt from those kept records
    (D-24), and the per-job evidence behind each evaluation after 90 days, once its compact job record exists (D-25).
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
- **D-25 Performance over any period, and the jobs behind each score (owner direction, November 2, 2026).**
  - **One light row per team-shift** (`team_shift_performance`, `focus.performance.rollup`). Each evaluation revision
    writes its team-shift's canonical components (seven since D-27, with the credited extra work Uc), status, evaluation id, policy hash, the team and the
    superintendent. About 200 bytes; no snapshot, plan or per-unit evidence is needed to read it.
  - **Superintendent as of the shift.** FOCUS_PERSON_TEAM_MAP's `SUPERINTENDENT` is admitted with each source run as an
    org chart (`focus.data.org`): each team's members name a superintendent over time, and the team reports to the
    superintendent most of its members name at the shift start (a split is flagged). The attribution is fixed when the
    row is first written: a correction changes the numbers, never the superintendent. A team with no superintendent
    rolls up under `UNASSIGNED`.
  - **Any group, any period.** A team, a superintendent or the business unit (the scope), over a shift, day, ISO week,
    rolling 30 days, month, quarter, year or custom dates (at most 400 days). The components of the group's
    team-shifts are summed and the one formula is applied once, so a team counts in proportion to its planned work.
    This equals the pooled canonical evaluation of the same members (`score.aggregate`).
  - **Strict, with a labeled partial.** The official result is unknown while any team-shift in the period has no
    score. Beside it, a partial score of the scored team-shifts says how many are missing; it is never official.
  - **Team spread.** For a superintendent or the business unit: each team's own score, how many are at or above the
    target (80 by default), the lowest, the highest, the range and the grades. The official score weights teams by
    planned work; the spread shows whether one large team carries the rest.
  - **The jobs behind each score, kept for good** (`job_outcomes`). Each planned unit records its job's shop order id
    (shared across aircraft), aircraft, phase, planned minutes and outcome (done, missed, excused or open), the causes
    (every submitted claim cause on the unit whatever its outcome, plus `DONE_BEFORE_SHIFT` for work finished before the
    shift, D-26) and whether the unit drew the out-of-sequence charge. Each unplanned completion records the same identity and minutes. A
    record adds up to its score: planned minutes to B, done to Cp, excused to E, unplanned to U. Work on a team-shift no
    plan covered is listed as `no_plan`: its neutral score leaves it out of U. This answers which jobs made a score and
    which jobs miss, or are done out of plan, aircraft after aircraft (`job_patterns`). About 107 jobs per team-shift
    at 50 aircraft, under 0.2 GB a year.
  - **Per-job evidence for 90 days.** The bulky evidence behind each score (`evaluation_evidence`, about 58 KB per
    team-shift) is deleted after the owner's 90 days, and only once the team-shift's job record carries the current
    evaluation (`focus.performance.details`). The store allows the deletion for this collection only
    (`RETAINED_DETAIL_COLLECTIONS`). Recognition and badges hold what they awarded, and the views say `DETAILS_EXPIRED`.
  - **Reads.** `GET /api/v1/performance/rollup` and `/performance/jobs`, also MCP tools for a future assistant. A team
    is checked against the caller's scope before any read; a superintendent or the business unit opens only when every
    team in it is in scope.
  - **Commands.** `performance-ledger` rebuilds rows and job records; `performance-details` reports or, with
    `--apply`, deletes. CI runs `detail_retention` with retention credentials, `allow_failure`.
  - **Live.** Migration 010 is the proposed storage (`FOCUS_FN_TeamShiftPerformance`, `FOCUS_FN_JobOutcome`,
    `FOCUS_FN_JobName`). It is a draft and has not been executed.
- **D-26 Each shift is scored against its own plan (owner direction, November 2, 2026).**
  - **The cycle.** The scheduler runs before every shift, three times a day, as a global optimization through
    delivery. Each run accounts for the previous shift's completions, what it did not complete, and new constraints
    such as rework and late parts, and sets the next shift's plan. Shift 1's performance compares shift 1's plan with
    what was verified complete by the end of shift 1 and its authorized overtime.
  - **Goals of record per shift** (`performance.lifecycle.GoalRegistry`). A team-shift's goal of record is the last
    accepted EXECUTABLE plan covering the shift's day that was accepted at or before the shift starts. When none was
    accepted before the start, the first accepted plan covering the day stands in, even though it was accepted after
    the shift started. Plans accepted later are proposals for that shift. Until this decision, the first accepted plan of a production day froze all three of the
    day's shifts, so shifts 2 and 3 were scored against the morning plan while the floor worked the newer one.
  - **Adherence is job identity, not timing.** A planned job not done in its shift is not adhered to there. If the next
    run plans it again and the next shift does it, it counts in that shift. If the next run leaves it out and a team
    does it anyway, it is offplan work. Start and finish times are never compared with the plan.
  - **No early credit.** A completion before a goal's window earns nothing early. Each shift's plan is made after the
    earlier shifts' work, so work done ahead of a later shift's plan is offplan output in the shift that did it.
  - **Done before the shift.** The next plan is made while the previous shift is still finishing (cutoff lead and
    overtime). A unit whose phase has a verified `COMPLETED` effective before its shift started is `DONE_BEFORE_SHIFT`
    (reason `VERIFIED_COMPLETE_BEFORE_SHIFT_START`). It stays in B0, counts in E (so it leaves D), is listed as excused
    with that cause in the job record, and is never earned twice. Only a whole-phase completion qualifies: a segment
    verified early by `PROGRESS` earns nothing in the goal and ends `MISSED`. The code does not check that the work
    was finished after the plan was made.
  - **Amendments** are accepted only during the shift, once its goal of record is fixed: the command refuses
    `SHIFT_NOT_STARTED` before the start and `SHIFT_ENDED` after the end; the `goal_amendments` feed applies the
    same rule to `DECISION_AT_UTC` and quarantines the row; the Team Lead view lists no addition before the start.
  - **Late events are captured after the fact (owner, November 2, 2026).** Data retrieval and the scheduler run in
    the last 15–20 minutes of a shift. Completions and new constraints in those minutes are few but real, and the
    performance record still captures them:
    - each shift's score is computed from timestamped completions once its window and overtime close, and is revised
      when data arrives late;
    - a job the next plan still holds but the previous shift already finished is `DONE_BEFORE_SHIFT` in the next shift;
    - a constraint the next plan missed (a kit found short, rework found) is excused through the claim review or added
      as an amendment, and the following plan sees it.

    The simulation models this gap (`SimSpec.cutoff_gap`, the cutoff `cutoff_lead_minutes` before the shift ends).
  - **Run time.** Each shift's plan must be accepted before the shift starts, which is why the cycle's run time is
    critical (D-22). The GitLab draft's signing step is a manual operator promotion. A plan promoted after its shift
    starts is a proposal for that shift, and the previous plan stays its goal of record (when there is no earlier plan
    covering the day, the late plan stands in). The calibration (`docs/task-valuation-investigation/10_CALIBRATION.md`
    §7) measured what late plans cost the teams and lists the owner decision this needs.
  - **Simulation.** `focus.sim` freezes each shift's goals from that shift's valid plan. The day's last valid plan
    stands in when a shift's own plan is invalid.
  - **Measured** (10 aircraft, 12 shifts, the same floor behavior and seed, so the work done is identical; evidence
    `PERSHIFT.json` in `docs/task-valuation-investigation/evidence/outputs/`):
    - **No pull-ahead.** Plan-following work counted as offplan fell from 2,335 standard minutes to 0. Units missed
      because a predecessor was unfinished fell from 16,800 to 635 minutes, since each plan now accounts for the
      previous shift. The mean team-shift score rose from 98.6 to 99.8.
    - **Heavy pull-ahead.** All pulled-ahead work is now offplan (80,302 minutes against 27,850), because none of it is
      credited early to a later shift any more. Today's adherence term penalizes it more (mean 96.3 against 97.9),
      so the offplan credit rule in `docs/task-valuation-investigation/09_OFFPLAN_CREDIT.md` matters more under
      per-shift goals.

- **D-27 Extra work earns credit, below the same planned work (owner decision, November 10, 2026).**
  - **The owner's requirement.** "The teams should get credit because they burned energy getting the work done, but
    it shouldn't be as rewarded as working the right jobs. If they aren't rewarded they would rather sit stagnant than
    work ahead, and that isn't acceptable." Until this decision every unplanned minute lowered a team's score.
  - **The formula** (`performance.score.formula`). With D > 0:
    - A = min(1, (Cp + Uc)/D), output attainment: the planned work done plus the credited extra work;
    - H = min(1, Cp/D), plan adherence: the planned jobs done, with no timing test (the owner's definition, D-26);
    - base = 100(0.80A + 0.20H); the out-of-sequence charge and the clamp are unchanged.

    Below the cap a planned minute earns 100/D points and a credited extra minute 80/D. Extra work never lowers a
    score, and the 20% adherence share is earned only by planned work. Without extra work the base is 100·Cp/D.
  - **Which extra work is credited** (`performance.score.components`, `credited_offplan`). Uc = max(0, eligible
    offplan minutes − missed minutes with no reason on record):
    - offplan work started out of sequence by choice is not eligible (`OUT_OF_SEQUENCE_BY_CHOICE` on the offplan
      fact). An unclassified violation makes the credit, and so the score, unknown
      (`OFFPLAN_OOS_CLASSIFICATION_UNKNOWN`);
    - a missed unit has a reason on record when a claim that is not withdrawn names it
      (`disruption.explained_units`): one the automatic attribution raised from evidence, or one a person filed,
      whatever its review status. A claim says why the unit could not be done; whether it excuses the unit stays the
      reviewer's separate decision. A missed unit with no relief and no claim carries `NO_REASON_ON_RECORD`;
    - so unplanned work done instead of planned work that could have been done earns nothing, and costs exactly the
      planned work it displaced: one penalty, where the old formula charged two. Unplanned work done because planned
      work was blocked, or after the plan was finished, is credited.
  - **Pooling.** Uc is a stored component like the others (the evaluation, the light row, the simulation's team-shift
    row). A shift's own score stops at 100; in a day, a week or any group the minutes are summed first, so extra
    minutes beyond one shift's expected work count toward the period's output share.
  - **What this supersedes in the manual (S14).** `H = Cp/(Cp+U)` and the 70/30 demonstration split. The manual's
    examples become: B200/Cp100 scores 50 (65); B200/Cp100/U100 scores 50 when the missed work has no reason on
    record and 90 when it has (50); the pooled D400/Cp200 with D100/Cp100 scores 60, not the 75 average (72 and 82.5).
    A57's "no throughput substitute" now reads: unplanned work done instead of planned work with no reason on record
    earns nothing. The manual itself is unchanged.
  - **Grades get stricter for a team with a shortfall.** Before, a team with no unplanned work earned the 30
    adherence points whatever it finished. A team that does 80% of its expected work now scores 80, where the old
    formula gave 86; 60% scores 60 (D), where it gave 72 (C). The bands (90/80/70/60) are unchanged demonstration
    values, to be set on pilot data (`docs/integration/DECISION_PLAYBOOK.md`, DEC-23).
  - **Values that remain the owner's to set.**
    - The rate. 0.80 is the calibrated recommendation (`docs/task-valuation-investigation/10_CALIBRATION.md`:
      the rule tracks production best at 0.6 to 0.8); 0.70 is stricter. It is `score.attainment_weight`, with
      `score.adherence_weight` as the rest.
    - Approved additions. An approved emergency addition still raises B, so an approved extra job done by a team
      that is behind can score below the same job done unapproved. Open (DEC-26).
    - Recognition points are unchanged: offplan output earns no XP (S14: no offplan farming).
  - **Stored records.** Evaluations and rows written before this decision have no Uc. They pool as unknown
    (`OFFPLAN_CREDIT_UNKNOWN`, `COMPONENT_UNKNOWN`) until evaluated again, and the weights changed the policy hash, so
    mixing them with new ones is `MIXED_POLICY`. The job record now lists every cause on record for a job, proposed or
    submitted, so a missed job with no cause is exactly one the unplanned work covers first. A simulation database
    made before it is reported as outdated.
  - **Evidence.** Reports `09_OFFPLAN_CREDIT.md` and `10_CALIBRATION.md`. On the simulated floor the old formula
    ranked the most productive floor (every idle team pulling work ahead) below the floor that did nothing extra, in
    both seeds; the credit rule ranks them as production does. Those runs classified off-plan work per team-shift
    (all or nothing); the rule as built nets it minute for minute, which only moves credit toward real extra work.
    `tools/calibrate_scores.py` re-runs every figure on real scores.
  - **Limits.** The credit depends on the reasons on record. Where the attribution evidence is thin (no part,
    attendance or predecessor data), missed work shows no reason and absorbs the extra work first. DEC-20 measures how
    often that happens on real data.

## Semantic gaps (live), each a precise readiness effect

These are the policy file's `open_decisions` (OD-18 is tracked here only). None of them blocks local
fixture implementation (S00). In live mode each one still open appears in every snapshot as a readiness gap named
by its id (scope `policy`, labelled `BLOCKS_EXECUTABLE`); the validator does not act on that label. Besides OD-05
(`CS_DATE_SEMANTICS_UNCONFIRMED` on every job) and OD-07 (both jobs of a hard concurrent or non-concurrent edge
quarantined), what each one withholds comes from the data its answer unlocks:

- OD-01 TOTAL_MECHANICS simultaneity: **decided by the owner on 2026-10-31.** TOTAL_MECHANICS is the number of mechanics
  that must be on the job at once to work it (SIMULTANEOUS). It no longer withholds live jobs.
- OD-02 raw clock intervals for elapsed P75. Correction to the policy's `live_effect`: the P75 comes only from
  `duration_episodes` (no live reader), not from `FOCUS_FN_ClockObservation`, which feeds the `duration-stats` report.
- OD-03 QA hold locations/inspector roles. Correction: a job with in-process inspections and no located hold is
  blocked in every plan (`IP_HOLD_UNKNOWN_LOCATION`), not only executable ones.
- OD-04 labor breaks and OT authority
- OD-05 CS offsets/date basis. Correction: every live job is kept out of executable plans (`CS_DATE_SEMANTICS_UNCONFIRMED`), and conflicting
  active loads give `CS_LOAD_CONFLICT`.
- OD-06 commercial due: **decided by the owner on 2026-11-02.** The delivery due date is the aircraft's planned unload
  date at the last control station (744) plus the 2-day grace, as a whole local day. No source carries a customer
  delivery date. It no longer blocks live executable plans; an aircraft without that date still has an unknown cost.
- OD-07 concurrency meanings
- OD-08 rework classification source. Correction: without a classification source, open `IS_REWORK` jobs are
  blocked (`REWORK_CLASS_UNKNOWN`), not `DURATION_UNAVAILABLE`; unflagged rework uses the flow standard.
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
- OD-18 (tracked here only; not in the policy file, so it raises no live gap) explanation provider: no BCAI endpoint, credential or response adapter is approved. Live explanations stay deterministic
  (`FOCUS_LLM_PROVIDER=none`) until an approved https endpoint returns the `focus-explain-v1` contract. Its outputs are validated like
  any provider's.

## Confirmation (P01 detailed requirement)

This manual contains every specification needed to implement the
application. No prior code, chat, donor application or separate document is
a dependency. Constants supplied by the user were recorded as
`user_requirement` without asking for blanket re-approval.
