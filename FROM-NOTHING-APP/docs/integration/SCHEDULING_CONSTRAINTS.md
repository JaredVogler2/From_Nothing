# Rich MAX scheduling — full constraint, objective and validation handoff

> **Purpose:** document the constraints ACTUALLY modeled by the checked-in scheduler, the source that supplies each one, the owner/policy decisions still needed in a company environment, the distinction between FORECAST and EXECUTABLE, and the independent validation gates. These are **reference code rules and synthetic policy defaults**, not confirmed company operating rules.
>
> **Authoritative code:** focus/engine/max/optimizer/orchestrator.py, greedy.py, focus/engine/model.py, ledger.py, focus/data/*, focus/calendar.py, focus/eligibility.py, focus/economics/*, focus/publication/*, focus/validation/plan.py and focus/validation/duty.py. **Machine-readable policy:** config/policies/rebuild-v1.json (with shift_fixture.json, grading-demo-v1.json). **Detailed validator table:** docs/execution/VALIDATION_COVERAGE.md.
>
> This ledger is a **map of requirements**, not a replacement for executable tests; future changes must update both code and ledger and pin the source commit/hash.

## 1. What the scheduler actually optimizes

Current engine: **Rich MAX heuristic**, not an exact mathematical global optimum or certified CP-SAT solver. It creates a neutral serial schedule on the ready precedence frontier, economic redispatch, bounded deferred-phase repair, and seeded neighborhood priority/crew-continuity moves. Every retained candidate must satisfy an **independent validator**. Current policy has solver.enabled=false, budget_seconds=30, max_neighborhood_moves=400 (fixture), seed=2601027; do not represent these as approved production values.

The exact lexicographic objective in MaxOptimizer.evaluate / better is, IN ORDER:

| Lexicographic key | Prefer | Explanation |
|---|---|---|
| -defended | More | Preserve existing accepted commitment slots or allowed defended outcome. A new candidate cannot downgrade previously kept commitments. |
| -exact | More | Preserve precisely the prior incumbent crew/slot when possible. |
| count of unknown aircraft | Fewer | A line with any unreleased required delivery-scope job has UNKNOWN completion, not a false estimate. |
| count of deferred phases | Fewer | Complete all legally schedulable work, not just easy jobs. |
| controllable_known_cents | Lower | Exact modeled penalty for aircraft with known/conditional completion and approved terms; missing terms remain unknown. |
| travel + wait minutes | Lower | Reduce non-value-added duty after above priorities. |
| overtime minutes | Lower | Minimize authorized OT, not allow unauthorized OT. |
| crew continuity breaks | Fewer | Reduce assignment churn. |
| deterministic signature | Stable | Reproducible tie-breaking. |

NO monetary rounding tolerance (economics.monetary_tolerance_cents=0); normalized 1-day marginal penalty guides **priority**, but actual plan acceptance uses exact complete-candidate cost. No candidate can violate safety, qualification, precedence or commitments to gain financial benefit. A heuristic result always reports global_optimality_proven=false. An unscheduled/unknown aircraft cannot be called on time merely because its scheduled subset finishes.

## 2. All constraint groups mapped to source and validation

| Code / constraint group | Constraint enforced | Input contract(s) and rule module | Failure / caveat |
|---|---|---|---|
| C00 full WIP/scope | No fixed line cap. Preserve every relevant current aircraft/job, completed predecessor anchors, blocked and quarantined jobs; every snapshot job assigned one documented disposition. | master_task_list, scope_receipt; data/scope.py, snapshot_builder.py | Unsupported status -> quarantine, never silently omit. Validator E_INVENTORY. |
| C01 identity and revision | Job identity is site/program/line/native SOI/revision/operation/effectivity, losslessly encoded; same SOI on different line not one job. Source rows versioned, conflicts/revisions rejected or reconciled. | master_task_list and typed source metadata; domain.py, data/scope.py | Wrong join duplicates/loses work; E_LINEAGE. |
| C02 source cutoff / freshness | One source-run cutoff, source-timezone policy and evidence receipts; no future records or hot query after snapshot; dispatch rechecks up-to-date current facts. | All datasets and capture receipts; data/acquisition.py, sourcetime.py, publication/dispatch.py | Missing timezone -> BLOCKS_ALL; stale dispatch source -> no release. |
| C03 status and remaining work | Terminal COMPLETE anchors count as achieved, not re-scheduled. OPEN and IN_PROGRESS handled differently; in-progress needs sourced performed/remaining elapsed, not fictitious full restart. Preview/forecast statuses only where admitted. | master_task_list, work_progress; data/scope.py, data/graph.py | OD-13, missing remainder or unverified completion -> blocked/conditional. V1. |
| C04 work phase model | Parent jobs decompose into WORK segments, in-process QA holds, FINAL_QA, OTHER_BUYOFF and zero-work RELEASE. Each segment positive/valid; conservation of parent duration/effort. | flow_budget, buyoff_plan, inspection_holds, inspection_standards, work_progress; data/graph.py | Missing/invalid role or hold status -> blocked. V1. |
| C05 exact baseline duration | Use exact (LINE_NUMBER, JOB_NUMBER) flow standard CRITICAL_FLOW_MINUTES; crew TOTAL_MECHANICS, not PRIMARY count. Reject missing/nonpositive/conflicting standards. | flow_budget; data/durations.py, duration_gauge.py | OD-01 simultaneous semantics need source approval. V1 / V5. |
| C06 rework elapsed estimation | NC/PU/CI episodes: corrected/voided raw clocks, running union of overlapping intervals, active elapsed separate from per-person effort and cycle; verified complete cohorts only, exact P75 by default, ceiling to scheduling minute. | rework_classification, clock_observations, duration_episodes; data/durations.py, duration_gauge.py | OD-02, OD-08; insufficient/stale -> DURATION_UNAVAILABLE; no person-effort shortcut. V1. |
| C07 typed hard precedence | FS, SS, FF, SF relations with signed minute lag; predecessor release AND gates, true COMPLETE anchors, no unknown predecessor satisfaction; cycle/unresolved endpoint detection. | pnet_dag, neo_graph_rework; data/graph.py, engine/greedy.py | Hard violations block. Advisory links are not mandatory. V2. |
| C08 concurrency rules | HARD CONCURRENT requires overlap and HARD NONCONCURRENT prohibits overlap, according to approved semantics; case must be represented and verified separately from DAG. | pnet_dag; data/graph.py and validator | OD-07 live meaning open, cannot assume fixture meaning. V2. |
| C09 rework topology | Rework child linked to same-line root; root continuation/release dependencies, started-root policies; avoids prematurely freeing root and double-counting affected predecessor. | neo_graph_rework, rework_classification; data/graph.py | Missing class/root -> blocked or unknown rework estimate. V2. |
| C10 per-line operating calendar | Use one admitted active calendar generation per line, bounded date horizon, only WORKING days; no implicitly extended horizon; S3 special nonworking rule. | cday_op_plan; calendar.py | Conflict/missing generation or nonworking -> unavailable, V3. |
| C11 physical shift windows and breaks | S1/S2/S3 start instants, physical regular and authorized OT intervals, PRE/BREAK/LUNCH/POST protected windows, day rollover and DST. Shifts are not a generic 8h block. | shift_fixture.json, shift_roster, attendance_shift, availability_exceptions; calendar.py, data/workforce.py | Exact clock source/owner must approve for live, V4/V7. |
| C12 no-start and latest | No new start within last 30 regular shift minutes (equality at cutoff allowed); cannot start before source cutoff, earliest job availability, CS earliest or after hard CS latest. | master_task_list.FIRST_AVAILABLE, cs_windows, cday_op_plan, policy; engine/model.py, greedy.py | V4, E_CS; OD-05 CS date/offset interpretation. |
| C13 whole crew simultaneity | Each WORK phase needs entire role-demand crew concurrently throughout active segments; no person duplicate, no role mixing without sourced authorization. | flow_budget, workforce membership, cert requirements; data/workforce.py, engine/crew_selection.py | If TOTAL semantics unapproved or workers missing, not executable. V5. |
| C14 certification throughout interval | Every member satisfies **full** applicable AND-bundle of credentials from raw holdings; only approved OR groups allowed; start-to-end date validity, status, expiry, suspensions/revocations. Affirmative no-cert source required to claim none. | soi_cert_requirements, cert_course_map, mechanic_certs, requirement_census, requirement_alternatives, holding_events; eligibility.py | Matrix ELIGIBLE/SUBSTITUTION is display only. OD-09. V5. |
| C15 people, teams and eligibility | Real named people and current effective assignment/attendance/roster for EXECUTABLE; planned roster and effective windows for future. Historical inferred home team is forecast-only; one person cannot belong to contradictory pools. | person_team_map, attendance_shift, shift_roster, availability_exceptions, team_inference, mech_team_inferred, dept_team_skill_ref, people_directory | OD-10 means live EXECUTABLE not ready until feeds installed. V5. |
| C16 cross-shift/day non-overlap | One person cannot work two aircraft/assignments simultaneously, including actual carry-in, work, travel, waiting, tool/other. Half-open UTC minute windows and locked actuals. | carry_in, attendance, roster, source actuals; engine/ledger.py, validation/duty.py | PERSON_OVERLAP; no backdated rewrite of actuals. V6. |
| C17 travel/transfer | When changing aircraft, transfer based on both adjacent duties, within open windows, before next crew work starts; rederive on insertion/removal, no hidden travel across breaks. | assigned line/location and source-specific travel times/policy; engine/travel.py, ledger.py | Fixture cross-aircraft 15min and same-aircraft 0; work must approve OD-11 travel origin/metrics. V7. |
| C18 complete regular/OT duty budgets | WORK + TRAVEL + TOOL + WAIT + OTHER each count once against physical regular and approved OT caps; person OT source required, maximum 60 minutes fixture/user cap. | shift/OT policy, ot_authorizations, availability_exceptions, carry_in; ledger.py | No authorization -> OT=0. V4/V7. |
| C19 85% activation cap | For each distinct team/shift pool with N admitted persons, distinct activated people <= floor(85*N/100). No minimum-one exception, double-counting or synthetic boost; counted duty kinds include WORK/TRAVEL/TOOL/WAIT/OTHER under fixture. | workforce pools/availability, policy; ledger.py | Reusing activated person doesn't consume another head. V8. |
| C20 tools/shared auxiliary resources | If a phase has a sourced tool requirement, reserve interval capacity in ToolCalendar, no overlapping overbooking. No fictional unlimited inventory. | ToolCalendar source adapter required when enabled; engine/ledger.py | Current default NONE_UNLESS_SOURCED; independent validator notes tool overlap NOT_APPLICABLE without mapped source. Tool readiness gap, not proof of real capacity. |
| C21 QA IP/final/other buyoff | Required IP hold after explicitly sourced progress point; 45min TOTAL IP and 45min FINAL under the user requirement; other branches may overlap depending typed scheduling relationships. QA inspector needs valid capacity/certs. | buyoff_plan, inspection_counts, inspection_standards, inspection_holds, QA workforce | Counts alone cannot locate IP. OD-03. V1/V5. |
| C22 parts / shortage release | EXECUTABLE requires confirmed physical receipt and no reopen/hold; FORECAST may use conditional ECD with date-only end-of-local-day or timestamp ceiling floor and labels. Own + ancestor shortages matter. | etracs_active, etracs_history, part_events; data/parts.py | Missing USED_ON or ECD -> gap; ECD not physical arrival. V9. |
| C23 control-station windows | Same-run active CS load/unload references; signed offsets and approved date basis; derive station earliest/hard latest, preserve generation lineage and raw source fields. | cs_windows, cday_op_plan, policy; data/cs_windows.py | Missing/conflicting rows -> gap; OD-05. V4/E_CS. |
| C24 forecasting virtual support | Purpose FORECAST (and isolated SCENARIO) can use explicitly labeled virtual MHE/PSC/TST pools (10 per team/shift fixture) or nominal/inferred workers. EXECUTABLE must never. | workforce and policy; snapshot_builder.py, engine/model.py | Synthetic cannot enter EXECUTABLE. V5/E_CAPABILITY. |
| C25 commitment defense | Prior accepted incumbent tasks within protected commitment horizon: exact slot/crew first, same slot alternate crew second, then audited minimal move or defer; candidate cannot downgrade previously protected commitment outcomes. | current accepted plan, commitment records and scope; engine/commitments.py, optimizer/greedy.py | Three-day horizon, exact defense order are current policy. E_COMMITMENT. |
| C26 frozen shift goals | Today's accepted executable obligation B0 frozen, later replans do not rewrite previously accepted goal for scoring; compliance records versioned. | accepted plan, frozen goal & observation feeds; performance/lifecycle.py | No inferred obligation from model predictions. E_ARTIFACT/publication. |
| C27 aircraft completion | Every delivery-scope required job must have a RELEASE, including blocked/deferred work. Verified-complete anchors count satisfied. If one required job unresolved -> UNKNOWN aircraft completion. | full master_task_list, graph, dispositions, release times; engine/max/optimizer/orchestrator.py | Cannot maximize "completed aircraft" by ignoring hard jobs. E_ECONOMICS. |
| C28 economic objective | Exact cents/Fraction, current approved terms, effective contracts, due date source, caps, prior liability; prioritize marginal cost only as heuristic guidance; score full accepted candidate exactly. | econ_* datasets, CS or approved delivery due, commitment feeds; economics/registry.py and evaluator.py | OD-06, OD-12: unknown due/terms -> UNKNOWN, not zero, not demo price. E_ECONOMICS. |
| C29 scenario isolation | What-if modifications operate on isolated copied snapshot under SCENARIO purpose, no publication of scenario or modification of source/accepted head/goal/evaluation. | analytics/scenarios.py, publication/service.py | Invalid capability -> PURPOSE_NOT_PUBLISHABLE. E_CAPABILITY. |
| C30 source/plan integrity | Candidate binding to snapshot, run, policy hash, purpose, CS source, asset bytes, signing; acceptance staged/readback/validated with optimistic head revision. | repositories, publication, validation/input.py | Zero violations with required skipped checks still invalid. E_LINEAGE/E_ARTIFACT. |
| C31 dispatch freshness | A published EXECUTABLE plan is not automatically still executable. Recheck current certs, roster, QA, graph, parts, source freshness, CS & calendar before dispatch. | publication/dispatch.py, approved latest source | stale > source freshness policy means reject; not a license to execute from stale plan. |
| C32 performance/evaluation after schedule | Evaluation uses frozen accepted B0, sourced actual intervals, goal amendments, disruption/excusal review and revisions; canonical pooled score and XP are distinct from optimization; individual grading is NOT approved by demo policy. | performance/actuals.py, lifecycle.py, score.py, history.py, progression.py | OD-15–OD-17; no official grades from missing evidence. |

This matrix enumerates the materially distinct modeled rule groups; specific code conditions and validation violation subcodes remain authoritative. If a future developer adds resource categories or constraints, update this catalog, exact source contracts and independent validator tests together.

## 3. Existing demo policy values — do not infer production approval

The following values are in the checked-in configuration and are tagged by authority. **Reconfirm all fixture_default, demo_default and open_decision values inside work.**

| Rule | Reference policy | Authority / cautions |
|---|---|---|
| Three shifts; starts | S1 06:00, S2 14:30, S3 23:00, America/New_York | user_requirement in shift_fixture.json |
| Physical shift wall length | S1/S2 510 minutes, S3 420 | manual_specified |
| Regular duty caps | 430 / 430 / 340 minutes | user_requirement |
| Max OT | 60 minutes, only with explicit authorization | user_requirement, but actual authority must come from source |
| No-new-start buffer | 30 minutes | user_requirement |
| Shift 3 nonworking rule | Next working-day condition | user_requirement; calendar.py exact logic |
| Protected windows | PRE 15, BREAK 20, LUNCH 30, POST 15 with configured start offsets | fixture_default, actual break policy must be confirmed |
| Cross-aircraft travel | 15 minutes (same aircraft 0) | fixture_default, no validated live source |
| Team activation | floor(0.85*N), no minimum-one exception | user_requirement; N exact admitted people |
| IP and FINAL QA | 45 minutes each | user_requirement; hold location source required |
| Other buyoff | 20 minutes | fixture_default |
| Total Mechanics demand | TOTAL, simultaneous crew | open_decision OD-01 |
| P75 elapsed duration | Default P75; P50/P55/MEAN/P90 available | user_requirement; live episodes source gap |
| Rework cohort minimum / stale | 30 / 365 days | manual_specified / fixture_default |
| Duration segments | Max 240 minutes | fixture_default |
| Active calendar and CS generation | One active load each, bounded | fixture_default; date basis OD-05 |
| Forecast-only virtual support | MHE/PSC/TST, 10 per team/shift | user_requirement; never live executable |
| Commitment horizon | 3 calendar days; exact slot→same slot new crew→audited move/defer | user_requirement |
| Delivery hysteresis | 2 days, bad-news 7 days, confirmation 2 runs, tolerance 1 day | mixed user/manual; due semantics OD-14 |
| Delivery due | Station 744 proxy plus 2-day grace | **fixture planning proxy**, not contractual date OD-06 |
| Gate | Weight 35, capped 5 days, 2-day proxy grace | user_requirement but requires approved operational meaning |
| Economic sample terms | Fixture only ASSUMED; no production default | OD-12 |
| Source freshness | 24h max for dispatch | fixture_default |
| Objective monetary tolerance | 0 cents | manual_specified |
| Exact solver | Disabled; heuristic search | current user requirement |
| Live LLM | Disabled, explanations provider none | current user requirement |

Policy fields are defined under config/policies/rebuild-v1.json and config/policies/shift_fixture.json; use these versioned files and focus/policy.py for every specific parameter. **Do not hardcode a copied number from this document when implementing an adapter.**

## 4. Constraint interaction examples your agent MUST regression-test

1. A job with no cert requirement rows **cannot** automatically be scheduled as cert-free without affirmative requirement_census.
2. An open part with a forecast ECD might appear in a **conditional** FORECAST but NEVER an executable slot until a confirmed receipt, including when ECD is in the past.
3. Existing named person booked on another aircraft cannot be added to a simultaneous crew: both duty and travel intervals must fit.
4. Inferred team membership ≠ attendance; a highly qualified but absent employee is not executable.
5. Work done on a completed predecessor anchors successor timing, but completed jobs are not rescheduled; a child rework dependency still needs proper root relation.
6. Two links pointing to the same successor with hard FS and SS constraints must both hold; a cycle or unknown endpoint cannot be ignored.
7. Crew demand TOTAL=3 with PRIMARY=2 still needs 3 people, unless newly approved role-phase semantics replace simultaneous interpretation.
8. A work segment must not start inside the last 30-min no-start period even if it could end later; protected breaks and OT authorization still apply.
9. Crossing from one aircraft to another introduces travel and can change both neighboring travel segments; failing move rolls back atomically.
10. A single required delivery-scope job deferred due to no QA hold makes aircraft completion unknown, even if other 99% completed.
11. Altering source calendar load, CS offset or per-source timezone changes the derived schedule; the optimizer must use one captured generation.
12. Improved modeled penalty is NOT enough to accept a schedule that loses a defended commitment or independent validator pass.
13. A forecast that uses nominal MHE/PSC/TST cannot be published as an EXECUTABLE plan.
14. With no approved contract terms or due dates, show "unknown" costs; do not use fictional commercial numbers.
15. A claimed zero violations is still REJECTED when a required independent check was skipped.
16. Scenario edits cannot mutate accepted source plan heads, frozen goals, current actuals or evaluations.
17. No tool requirement source means a documented missing capability, not evidence that the shop has infinite shared tools.
18. Raw aggregate person-effort from actual_labor must not be used as active elapsed rework time.

## 5. Independent validation gate — exact implemented check IDs

From docs/execution/VALIDATION_COVERAGE.md and focus/validation/plan.py:

| ID | Independent proof |
|---|---|
| V1 | Work identity/segments, parent duration and work conservation, in-progress remainder |
| V2 | All hard predecessor relations, release floors, cycles and concurrency |
| V3 | Active per-aircraft line calendar, S3 nonworking rule and bounds |
| V4 | Physical shift fit, regular windows, breaks, earliest/latest, OT and no-start |
| V5 | Complete named crew, membership, requirements, entire-interval certs, role/pool |
| V6 | Person non-overlap across all actual/planned duty and shift labels |
| V7 | Both-neighbor transfer re-derivation and exact regular+OT caps |
| V8 | <= floor(85*N/100) distinct activated people per pool |
| V9 | All part/ancestor release evidence and correct conditional/confirmed status |
| E_INVENTORY | Exactly one disposition per job; no silent omissions or fake releases |
| E_LINEAGE | Verified snapshot, scope, policy, purpose, run and plan hashes |
| E_CS | Station fact matches original same-run source load/offset/lineage |
| E_COMMITMENT | Accepted incumbent protection and accurate per-task reported outcomes |
| E_ECONOMICS | Full-fleet aircraft completion and cost independently recomputed |
| E_CAPABILITY | Valid FORECAST/EXECUTABLE capability, correct conditional flags |
| E_ARTIFACT | Canonical publication bytes, signed lineage, frozen goals and source content |

The optimizer imports and calls the independent validator gate; the validator **never** imports optimizer feasibility flags. All material acceptance checks must be CHECKED or legitimately VACUOUS. Required SKIPPED -> invalid even without violations.

## 6. Source → algorithm → proof crosswalk

| Scheduler area | Primary source datasets | Main implementation | Main proof |
|---|---|---|---|
| WIP/identity | master_task_list, scope_receipt | data/scope.py, snapshot_builder.py | V1, E_INVENTORY |
| Estimates and phases | flow_budget, duration_episodes, clock_observations, QA | durations.py, graph.py | V1, V5 |
| DAG and rework | pnet_dag, neo_graph_rework, rework_classification | graph.py, greedy.py | V2 |
| Working calendar and CS | cday_op_plan, cs_windows | calendar.py, cs_windows.py | V3, V4, E_CS |
| People / qualification | roster, person_team_map, attendance_shift, mechanic_certs, requirement*, holding_events | workforce.py, eligibility.py, crew_selection.py | V5, V6, V8 |
| Duty/travel/OT/tools | carry_in, availability_exceptions, ot_authorizations, resource source | ledger.py, travel.py | V4, V6, V7, V8 |
| QA and inspection | buyoff_plan, standards, inspection_holds, QA pools | graph.py, crew_selection.py | V1, V5 |
| Parts/material | etracs_*, part_events | parts.py, graph.py | V2, V9 |
| Incumbent and goals | accepted plan, commitment, frozen goals | commitments.py, greedy.py | E_COMMITMENT, E_ARTIFACT |
| Delay economics | econ_* (7 sources) and approved due | economics/registry.py, optimizer | E_ECONOMICS |
| Publication/dispatch | immutable SourceRun, source head, validated candidate | publication/* | E_LINEAGE, E_ARTIFACT, dispatch check |
| Observed grades/progression | lifecycle, actual intervals, claim/decision/defect/amendment feeds | performance/* | performance-specific provenance and approval |

## 7. Do-not-change list for workplace AI coding agents

- Do not silently downscope all aircraft to the first N or only those that can be scheduled.
- Do not change unknown requirements/crews/parts/costs to zero, eligible, present or done.
- Do not combine FORECAST conditional people/parts with official executable commitments.
- Do not remove PNet hard links, QA dependencies, overtime caps, shift breaks or cert checks for speed.
- Do not treat inferred/past team assignment as verified current attendance.
- Do not bypass validation or use heuristic flags to validate themselves.
- Do not rewrite accepted incumbent history, revisions or frozen evaluation goals.
- Do not mark a candidate globally optimal when using the bounded heuristic.
- Do not execute public demo SQL against production or commit real data; obtain owner approval for new adapters and migrations.

**Rule of handoff:** the coding agent may change source **adapters, query projections and policy after company-side approval**; the scheduling and validation semantics remain identical until a separately reviewed requirements change is authorized.
