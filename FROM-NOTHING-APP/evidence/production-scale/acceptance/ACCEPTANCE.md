# P24 acceptance run

Local acceptance: **PASS**. Cases 72/72 pass; requirements 39/39 pass with 149/149 caller paths verified.

Tests: {"collected": 496, "exitstatus": 0, "passed": 493, "skipped": 3}. Python 3.12.3. Implementation `df2e2ce070cf3fcf`, tests `fe3b92a8c99919e9`.

This is fixture evidence. Live verification is a separate capability (see FINAL_ACCEPTANCE.md).

## Cases (S19)

| Case | Stage | Result | Tests | Outcome asserted |
| --- | --- | --- | --- | --- |
| A01 | P02 | PASS | 6/6 | No credentials and no source run: imports and pages succeed with typed no-data states; nothing acquires, solves, writes or grades on import or GET |
| A02 | P02 | PASS | 4/4 | Only the local focus package is imported; optional solver, LLM and SQL packages are lazy and the fixture path works without them |
| A03 | P03 | PASS | 4/4 | All 51 aircraft reconciled, including a fully blocked one and one missing standards; no eligibility or top-50 exclusion; reasons and inventory kept |
| A04 | P03 | PASS | 3/3 | Same SOI on two lines and two revisions keep distinct lossless keys through graph, durations, parts, serialization and UI |
| A05 | P04 | PASS | 3/3 | A failed mandatory dataset never becomes head; certified empty differs from unavailable; the old head stays |
| A06 | P04 | PASS | 3/3 | A CS dataset from another run or generation fails admission; a changed declared hash cannot cure the evidence mismatch |
| A07 | P04 | PASS | 3/3 | A crash before the head update leaves no partial run; replaying the idempotency key gives one receipt and head, no duplicate facts |
| A08 | P05 | PASS | 5/5 | SQL team inference breaks ties deterministically with tie flag, count, window and confidence; department fallback is labeled and persisted |
| A09 | P05 | PASS | 4/4 | An effective assignment overrides inference at the exact instant; inference never asserts present executable capacity |
| A10 | P06 | PASS | 3/3 | True repeats dedupe; a conflicting overlapping assignment fails admission; no double headcount or double booking |
| A11 | P06 | PASS | 3/3 | A certified mechanic of the wrong team or shift is never borrowed; work waits with a reason; no phantom mechanic |
| A12 | P06 | PASS | 4/4 | Only an individual superset of the required certs qualifies; a union across two people is rejected; approved OR alternatives are checked separately |
| A13 | P06 | PASS | 4/4 | Whole-interval eligibility rejects expiring, revoked, future, blank or malformed certs; equivalent date encodings agree; the sentinel policy is explicit |
| A14 | P06 | PASS | 2/2 | Unknown rework requirements block and are never cleared; an affirmative no-cert receipt may proceed; a SUBST label alone grants nothing |
| A15 | P07 | PASS | 5/5 | Composite flow lookup keeps each line's source fields; TOTAL drives demand; zero, null or negative counts are unavailable, never a default of 1 |
| A16 | P07 | PASS | 3/3 | Running-max union gives active elapsed 100 without bridging gaps or double-counting nested intervals; owner unions give effort independently |
| A17 | P07 | PASS | 3/3 | P50/P55/mean/P75/P90 persist with n, window and units; scheduled rework P75 uses the elapsed cohort, not effort divided by crew |
| A18 | P07 | PASS | 5/5 | Selecting P90 makes a new policy and plan only, prior goals unchanged; no false mean ordering; an unavailable cohort is never a flat 60 |
| A19 | P08 | PASS | 2/2 | The per-line active calendar wins; holes and out-of-bounds dates block; no weekday arithmetic fallback or automatic extension |
| A20 | P08 | PASS | 2/2 | S3 on a nonworking day needs same-line next-day working evidence; Saturday before a nonworking Sunday is denied; S1/S2 on nonworking days are denied |
| A21 | P08 | PASS | 2/2 | A normal working-day S3 is allowed in its admitted window; UTC endpoints are built independently across DST |
| A22 | P08 | PASS | 3/3 | Starts at cutoff-1 and cutoff are allowed, cutoff+1 is rejected; OT never moves the cutoff; finish fit is checked separately |
| A23 | P08 | PASS | 3/3 | The exact station is chosen; earliest, gate and due stay distinct; past and day-0 gates are retained; no min/max widening |
| A24 | P08 | PASS | 4/4 | Raw CS signs and lineage are preserved; conflicting loads or a missing date policy block; no guessed MAX(load), anchor or date convention |
| A25 | P09 | PASS | 3/3 | An incomplete predecessor must finish first; a missing prerequisite, self-edge or cycle is diagnosed and never satisfied by deletion |
| A26 | P09 | PASS | 5/5 | An in-progress job keeps its incoming edge and actual history; unsafe continuation is held; no backdating, restart fiction or OOS-only legalization |
| A27 | P09 | PASS | 2/2 | Each rework gate is injected once; the unstarted root waits for rework and QA; a started root uses the documented hold |
| A28 | P09 | PASS | 2/2 | Reverse or cyclic rework is quarantined and closure blocked; verified lifecycle revisions decide release; no cycle-edge deletion |
| A29 | P09 | PASS | 2/2 | A shortage gates the correct line-A root and descendants by the latest release; the same SOI on line B is unaffected; unknown rows stay blocked or conditional |
| A30 | P09 | PASS | 3/3 | A forecast never precedes its precision-aware floor; date-only ECD floors at end of day and is labeled; dispatch needs current ready evidence; reopen reblocks |
| A31 | P10 | PASS | 5/5 | Production elapsed is conserved; in-process QA allocations sum to 45, not 90; no invented midpoint when hold points are unknown |
| A32 | P10 | PASS | 2/2 | Separate 90 QA minutes when both apply; every successor is gated by all required releases; FS means >= |
| A33 | P10 | PASS | 3/3 | One qualified inspector never overlaps; production hold and retained crew duty follow policy; no unlimited QA pool or empty-cert inspector |
| A34 | P11 | PASS | 3/3 | An exact full crew of distinct eligible people or wait; roles respect team, shift and individual certs; no demand clamp |
| A35 | P11 | PASS | 2/2 | Work 420 plus two 15-minute transfers is duty 450 with 20 OT; without OT it is rejected; transfers count once |
| A36 | P11 | PASS | 2/2 | Duty 490 is allowed within authorized windows, 491 is rejected; carry-in occupancy is checked across shift labels |
| A37 | P11 | PASS | 3/3 | Pure pools of 0, 1, 2 and 10 cap at exactly 0, 0, 1 and 8 on the deduplicated admitted denominator; no minimum of one or second derate |
| A38 | P11 | PASS | 4/4 | Every binder, repair or continuity move that would activate a ninth of ten people is rejected or returns an explicit failed candidate; V8 catches a forged plan |
| A39 | P11 | PASS | 1/1 | A-B-A switching costs a transition each way for every member; B starts at 75 with baseline 15; real reservations for the whole crew |
| A40 | P11 | PASS | 3/3 | Insert, remove or swap recomputes both neighbor transitions atomically; travel across a protected break or a busy tool blocks |
| A41 | P11 | PASS | 2/2 | Prefer the feasible lower opportunity-cost person with fewer usable certs and aircraft affinity; overrides are explained with real travel |
| A42 | P14 | PASS | 3/3 | Ready commitments first; attempt 1 is the exact slot and crew, attempt 2 the same time with an alternate full crew; day 3 is free; outcomes recorded |
| A43 | P14 | PASS | 4/4 | Only the accepted incumbent is reloaded; a late part, expired cert or changed calendar overrides the defense immediately with a reason |
| A44 | P12 | PASS | 2/2 | The CLI rich MAX consumes registry weights and complete aircraft completion; economic steering improves exact fleet cost over the neutral seed |
| A45 | P12 | PASS | 4/4 | The exact full-candidate evaluator governs acceptance; no improvement claim from weights alone; unknown completion is never zero |
| A46 | P12 | PASS | 4/4 | Assumed and actual terms are labeled; no fabricated rate, liability or due; supported zero differs from unknown |
| A47 | P15 | PASS | 5/5 | Every V1-V9 and extended corruption is caught after each mutation; in-progress work is not skipped; coverage is nonzero; the plan cannot publish |
| A48 | P16 | PASS | 4/4 | Detached and pre-sign readback each reject changed crew, root edge, timezone, station/load, offset or source hash; corrupted bytes are never signed or promoted |
| A49 | P14 | PASS | 5/5 | Under 2 days holds; 7 or more days worse is immediate; other changes need two distinct confirmations; safety risk is immediately visible |
| A50 | P14 | PASS | 6/6 | Replays, rejections, cohort exit and re-entry, and a crash before acknowledgment add no streak or duplicate change and forget no commitment |
| A51 | P16 | PASS | 2/2 | A crash at stage, readback, signing, acceptance, pointer update or client timeout keeps the previous pointer until atomic acceptance; recovery reconciles exactly |
| A52 | P16 | PASS | 4/4 | Tampered signature or hash, replayed artifact, wrong scope, skipped check, synthetic capability or unauthorized signer never accepts or dispatches |
| A53 | P16 | PASS | 2/2 | A revoked cert, reopened shortage, incomplete predecessor or withdrawn attendance blocks current dispatch and requests repair; history stays immutable |
| A54 | P17 | PASS | 4/4 | All eight causes and duplicate claims: five nominal categories exact; evidence and decision separate; relief union bounded by frozen missed effort |
| A55 | P17 | PASS | 3/3 | Explicit defect-origin evidence decides culpability; no causation guess from DAG direction; no automatic 3x overrun excuse |
| A56 | P17 | PASS | 6/6 | No credit for cancellation; prior verified work follows the ordered policy; original goals preserved; corrections append a new evaluation |
| A57 | P18 | PASS | 5/5 | Demonstration scores 65, 50 and 0; no free 30 points, throughput substitute or UI formula drift |
| A58 | P18 | PASS | 3/3 | Approved relief case scores 100; all-excused or no plan is neutral null; missing evidence is unknown null; B0 retained |
| A59 | P18 | PASS | 5/5 | Pooled D400/Cp200 with D100/Cp100 scores 72, not the 82.5 average; team, factory and history surfaces agree |
| A60 | P18 | PASS | 3/3 | Planned OOS gives O20 and score 90 with Cp unchanged; unapproved off-plan OOS is not double charged; XP and economics never subtract O again |
| A61 | P18 | PASS | 3/3 | Actual gate ages 0,2,3,5,7 give raw 0,.4,.6,1,1; proxy grace applies once; a future gate is 0 and an absent one unavailable |
| A62 | P18 | PASS | 2/2 | A frozen recognition value earns once, never ages after completion, and a correction appends a revision instead of rewriting XP |
| A63 | P18 | PASS | 3/3 | No duplicated XP or badge across repeats and month transitions; awards name verified events; qualification never derives from XP |
| A64 | P21 | PASS | 5/5 | Exactly 10 distinct virtual people per configured MHE/PSC/TST team-shift with union certs; forecast-labeled; rejected from executable crews by origin evidence |
| A65 | P21 | PASS | 4/4 | Twin drilldowns give the same canonical totals and revisions; observed, planned, forecast and unknown stay separate; scenarios never move live pointers, goals or commitments |
| A66 | P21 | PASS | 4/4 | Mock and recorded explanations use actual ids; forged candidates, numbers, entities, units or narrative are rejected; the deterministic fallback is unchanged |
| A67 | P21 | PASS | 6/6 | Prompt injection, nonfinite or extra fields, timeouts and missing BCAI credentials cause no tool use, mutation or secret leak; schedule and rank unchanged |
| A68 | P19 | PASS | 14/14 | Scope before fetch; CSRF, version and idempotency enforced; GET never solves, writes or grades; commands return honest worker status |
| A69 | P20 | PASS | 5/5 | iOS and Classic are accessible with the same facts at 375/390, tablet and desktop, keyboard and reduced motion; no overflow or hardcoded Ready/100; travel, parts and QA reasons visible |
| A70 | P22 | PASS | 5/5 | Whole-WIP benchmark: zero required skips and violations, complete inventory, bounded runtime and memory; solver timeout, unavailability or invalid incumbent falls back truthfully |
| A71 | P23 | PASS | 9/9 | Needs, locks and idempotency block races and promotion; migrations are reviewable and reversible; unsigned readback strictly precedes signing |
| A72 | P24 | PASS | 6/6 | A fresh checkout completes the documented fixture flow; a new versioned review preserves historical review files; unresolved live gates are listed precisely |

## Requirements (S20)

| ID | Requirement | Stages | Cases | Paths | Result |
| --- | --- | --- | --- | --- | --- |
| C01 | Task precedence and prerequisite closure | P09, P13, P15 | A25, A26 | 6/6 | PASS |
| C02 | Rework predecessor injection and affected-root closure | P09, P10, P15 | A27, A28 | 3/3 | PASS |
| C03 | Late-part child-to-root linkage | P09, P15 | A29 | 4/4 | PASS |
| C04 | Root successor of ETA/physical part-release milestone | P09, P16 | A29, A30 | 4/4 | PASS |
| C05 | Certification eligibility and individual multi-cert superset | P06, P11, P15 | A12, A13, A14 | 7/7 | PASS |
| C06 | Pure team/shift/day pools and no borrowing | P05, P06, P11, P15 | A10, A11, A34 | 5/5 | PASS |
| C07 | Mechanic home-team inference in SQL Server | P05, P23 | A08, A09 | 1/1 | PASS |
| C08 | Flow duration and NC/PU/CI P75 elapsed rework estimates | P07, P13, P15 | A15, A16, A17 | 6/6 | PASS |
| C09 | Persist P55/average/P75/P90 with future toggling | P07, P23 | A17, A18 | 2/2 | PASS |
| C10 | 45 minutes total in-process inspection | P10, P15 | A31, A32 | 3/3 | PASS |
| C11 | Separate final 45-minute FS inspection before successors | P10, P15 | A32, A33 | 4/4 | PASS |
| C12 | Distinct CS earliest/deadline/gate, same-run source lineage | P04, P08, P12, P15, P16 | A23, A24, A48 | 7/7 | PASS |
| C13 | Bounded authoritative FOCUS_CDAY_OP_PLAN per line | P08, P13, P15 | A19, A20 | 6/6 | PASS |
| C14 | FOCUS_FLOW_BUDGET.TOTAL_MECHANICS and full crew | P07, P10, P11, P15 | A15, A34 | 3/3 | PASS |
| C15 | Three shifts and working budget plus at most 60 allowance | P08, P11, P15 | A21, A35, A36 | 4/4 | PASS |
| C16 | Nonworking-d shift 3 iff d+1 working on same line | P08, P15 | A20, A21 | 3/3 | PASS |
| C17 | Exact floor(0.85*pool) distinct activation cap | P11, P13, P15 | A37, A38 | 5/5 | PASS |
| C18 | Per-person complete duty load cap and OT | P11, P15 | A35, A36 | 4/4 | PASS |
| C19 | No-start cutoff effective minus 30, equality allowed | P08, P11, P15 | A22 | 3/3 | PASS |
| C20 | Consecutive aircraft switch time and changeover penalty | P11, P13, P15 | A39, A40 | 4/4 | PASS |
| C21 | Aircraft affinity and reduced walking | P11, P13 | A40, A41 | 2/2 | PASS |
| C22 | Fewer usable certs and rare-skill crew conservation | P06, P11, P13 | A41 | 2/2 | PASS |
| C23 | Feasible committed-first dispatch | P13, P14, P16 | A42, A43 | 3/3 | PASS |
| C24 | Enabled, 3 calendar days, exact two-attempt defense | P14, P15 | A42, A43 | 3/3 | PASS |
| C25 | FOCUS_ETRACS_ACTIVE current shortage blocking | P04, P09, P16 | A29, A30 | 3/3 | PASS |
| C26 | Parts ETA floor on every scan/mutation/status | P09, P13, P15, P16 | A30, A47 | 5/5 | PASS |
| C27 | Rich EconomicsRegistry weighted whole-WIP redispatch | P12, P13, P22 | A44, A45, A46 | 5/5 | PASS |
| C28 | Two-day hysteresis and distinct-run persistence | P14, P23 | A49, A50 | 2/2 | PASS |
| C29 | Seven-day bad news immediately and sustained other changes | P14 | A49, A50 | 1/1 | PASS |
| C30 | Eight disruption codes and five nominal excusables | P17, P18 | A54, A55 | 4/4 | PASS |
| C31 | Effort points, progression, badges, streams and XP | P18, P19, P20 | A57, A60, A62, A63 | 3/3 | PASS |
| C32 | Digital twin per team/shift/aircraft | P20, P21 | A65 | 1/1 | PASS |
| C33 | Behind-station value 35/cap5/grace2 | P18, P20 | A61, A62 | 3/3 | PASS |
| C34 | certexpired removes expired holdings | P06, P15, P16 | A13, A14, A53 | 4/4 | PASS |
| C35 | Exactly 10 fictitious MHE/PSC/TST union-cert support people | P06, P21, P15 | A64 | 6/6 | PASS |
| C36 | Mock/recorded/BCAI explanation providers and strict validation | P21 | A66, A67 | 3/3 | PASS |
| C37 | Stable nine-check suite with independent mandatory enforcement | P15, P16, P24 | A47, A48, A52 | 6/6 | PASS |
| C38 | In-progress never voids incoming incomplete predecessor | P09, P13, P15, P16, P17 | A26, A47, A53 | 5/5 | PASS |
| C39 | Separate once-only OOS penalty | P17, P18, P19 | A56, A57, A60 | 4/4 | PASS |

## Named counterexamples (P24 step 2)

| Item | Result | Tests |
| --- | --- | --- |
| In-progress job with an incomplete incoming predecessor | PASS | 6/6 |
| Repair or continuity move that would breach the activation cap | PASS | 4/4 |
| Rework P75 from the elapsed cohort, not effort divided by crew | PASS | 3/3 |
| Same-run CS lineage through serialization and pre-sign readback | PASS | 5/5 |
| Out-of-sequence charged once, never again in XP or economics | PASS | 4/4 |

## Verification themes (P24 step 3)

| Item | Result | Tests |
| --- | --- | --- |
| Complete WIP census | PASS | 6/6 |
| Conditional and virtual forecast versus real dispatch | PASS | 6/6 |
| Economics, commitment and scoring parity across stages and surfaces | PASS | 6/6 |
| Mobile and browser workflows in both layouts | PASS | 7/7 |
| Bounded scale | PASS | 4/4 |
| Security: scope, cache and CSRF | PASS | 9/9 |
| Publication crash-recovery boundaries | PASS | 4/4 |
| Source crash-recovery boundaries | PASS | 5/5 |
| Evaluation crash-recovery and correction boundaries | PASS | 5/5 |
| Posit Connect and Tanzu on Python 3.12; REST and MCP for application-to-application calls | PASS | 10/10 |

## Problems

None.
