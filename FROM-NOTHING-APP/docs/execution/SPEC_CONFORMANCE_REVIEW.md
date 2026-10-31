# Spec conformance review: code against `FOCUS_FROM_NOTHING_MASTER.txt` (October 31, 2026)

Kind: implementer-commissioned review, **not independent**. Five reviewers each read part of the master prompt in full
and compared it with the code:
- optimizer and economics;
- scheduling rules;
- lifecycle, scoring and excusals;
- UI, API and twin;
- data, fixtures and operations.

Every major finding was checked against the code before being listed; the "verified" column says how. Status values
are FIXED (in this branch), NEXT (being fixed now), OPEN (planned) and DECISION (needs the owner).

## A. Introduced by the recent engine, simulation and dashboard work

| # | Sev. | Requirement | Deviation | Verified | Status |
| --- | --- | --- | --- | --- | --- |
| A1 | major | S01 l.128, S12 l.1564, P13 l.3355, A44: "economic dispatch through `EconomicsRegistry.derive_priority_weights`" | Cost-of-delay urgency used each aircraft's raw daily rate and due date. Registry weights were only recorded, so caps, the curve and floors were ignored. | code + stub | FIXED: urgency = Registry marginal weight × slack decay |
| A2 | minor | S12 l.1600 `atomic_mutate_repair(best, move)` | When the redispatch was rejected, repairs and moves still decoded with its order. | code | FIXED: candidates use the best's own order |
| A3 | major | S14, S01 l.130 (effort scoring, eight causes, five excusable) | The simulation scored teams with an invented 0.6/0.4 task-share formula and had no excuse engine. | code | FIXED: canonical `score.formula` over unit facts; S14 claims. See A8-A13. |
| A4 | major | P19 l.3759 "Session grants scope before fetching"; A68 | Simulation reads were open to any reader, so a mechanic saw every team's crews. | test client | FIXED: fleet-wide sessions only, on all eleven operations (the Gantt, capacity and profile reads were missed at first) |
| A5 | major | S15 l.1915 "One backend score"; S01 l.159 | The simulation served stored grades from a local database. | code | FIXED: scores recomputed by the one formula from stored components; payloads labelled synthetic/DEMONSTRATION |
| A6 | major | S01 l.145, S13 l.1710 | The simulation dispatched and defended the FORECAST plan, and executed invalid plans. | code | FIXED: the floor works the EXECUTABLE plan; only valid plans become incumbents; defense labelled SIMULATED |
| A7 | minor | S15 l.1858, l.1908, A45 "unknown never zero" | Unknown penalties counted as zero; capacity used hard-coded shift minutes; utilization was computed in the browser. | code | FIXED |
| A8 | blocker | S14 l.1745 "DAG direction does not prove culpability" | Inter-job links run through RELEASE nodes, which never get assignments. Successors of work finished earlier in the same shift looked blocked, and the claim blamed team "?" and was approved: all 19 cross-team claims in a 6-aircraft run. | data | FIXED: RELEASE nodes finish with their inputs; blame passes through them. No unknown origin in the rerun. |
| A9 | blocker | S14 l.1755 "Cp once-only", P18 l.3692 | Completion was credited again in later shifts, because progress was written back only when a whole job finished. | data | FIXED: per-job write-back (`work_progress`); no unit earned twice in a day |
| A10 | major | S13 l.1710, P17 l.3625 "Original goals survive" | Goals (B0) came from every shift's replan instead of being frozen per day from the first accepted EXECUTABLE plan. | code | FIXED: frozen per production day, as `GoalRegistry` does |
| A11 | major | S14 l.1743 "never automatic approval of relief", P17 l.3619 | Absences were random per task and never written to attendance. Decisions approved automatically, with no reviewer rules and no rejections. | data | FIXED: call-offs written to attendance; an `excusal_decider` reviewer approves or rejects on the evidence, never its own claim |
| A12 | major | S14 l.1781, l.1759 | OOS used only the first blocker and only finish-to-start edges; it was charged only when the task finished, had no effective time, and directed OOS had no evidence. | code | FIXED: every unfinished predecessor by edge type, stored at the actual start, charged once by choice; directed OOS cites a directive |
| A13 | major | S14 l.1787-1817 | No gate points, streams, XP, badges or levels in the simulation. | grep | OPEN |
| A14 | minor | S14 l.1762, P18 l.3686 | Simulation scores skip `status_and_score`. Base/O/final/policy are not stored. There is no aircraft score or team history, DEVIATION is shown like a cause, and the defect origin is always the parent's team. | code | PART: the defect origin is now an explicit fact (another team's for 35 % of floor rework); the rest is OPEN |
| A15 | major | S18 l.2042, l.2046 "fake keys e.g. DEMO-…" | The synthetic fleet uses real airline names for the penalty proxy, which the owner asked for. | code | DECISION |
| A16 | minor | S15 l.1829-1842, l.1877 | The `/simulation` shell has no source/plan/as-of header, user/scope or iOS variant, tab roles and SVG-only values, and no pagination. Team pickers were hard-coded to T01-T30. | code | PART: plan/source/as-of and user/scope header, tablist roles, every chart's values in text, 50-row pages, teams from the data; the iOS variant is OPEN |
| A17 | blocker | S08 l.1298 "remaining actual work is preserved" | Write-back stamped `work_progress` at the shift end, after the next cutoff, so the next admission dropped the row and blocked every in-progress job for a shift (REMAINING_UNKNOWN). | data | FIXED: stamped at the same load time as the status rows |
| A18 | major | S08 rework lifecycle | The fixture and the simulation wrote a closed rework link as `CLOSED`, which the master status vocabulary does not map. The census saw a conflict and held the parent and its successors out of the executable plan. | data | FIXED: `COMPLETE` |
| A19 | major | S14 l.1753 "B = B0 + approved feasible emergency additions", S13 amendments | Rework found after the goals froze and dispatched by a later plan counted as offplan work against adherence; the simulation had no amendments. REWORK_INJECTION was submitted only for another team's defect. | code | FIXED: the lead adds dispatched emergency rework (basis `ASSIGNED_IN_ACCEPTED_PLAN`); rework claims are submitted whenever the origin is known, and the reviewer rejects a team's own defect |

## B. Pre-existing gaps in the original build

| # | Sev. | Requirement | Deviation | Verified | Status |
| --- | --- | --- | --- | --- | --- |
| B1 | major | S12 l.1564-1575; policy `objective.order` | The acceptance key ranks the deferred-phase count above money and compares known cost across different sets of unknown aircraft. The policy order is never read. | code | FIXED: the key follows `objective.order`; the deferred count is a last backlog tie; a cost win needs the same unknown aircraft (or a strict subset of them) |
| B2 | major | S10 l.1400-1410 | Hard CONCURRENT/NONCONCURRENT edges are dropped before placement (`model.unsupported_edges`); only the validator rejects them. | code | OPEN |
| B3 | major | S01 l.126, S11 l.1514-1516, P11 l.3226 | Crew choice has no rare-certification preservation or opportunity cost (both cores). | code | OPEN |
| B4 | major | S07 l.1203 "latest revocation … wins" | A later reinstatement erases the revocation window; a unit test asserts this behavior. A second revocation while revoked also moved the start later. | code | FIXED: closed windows kept on the holding (`revoked_windows`) and checked by the index, the validator and the Rust core; both cores tested on a window |
| B5 | major | S05 l.927 | An empty attendance feed does not block executable crews. | probe | FIXED: with an empty feed, nobody is an executable crew member on the production day (forecast evidence only) |
| B6 | major (live) | P05 l.2828 | Team inference and clock evidence SQL read the newest source head of any scope, not the run being admitted. | SQL | PART: bound to this scope's head (a parameter); reading the run being captured needs the SQL source-run lifecycle, which the app does not drive yet |
| B7 | major (live) | S05 l.935 | Admission has no freshness or capture-skew checks. | search | OPEN |
| B8 | major | S15 l.1889, A68 | The reported-observation command enqueues without a scope check; a test asserted a mechanic could report on another team's job. | code | FIXED: a job or phase outside the caller's teams gets the same 404 as a missing one |
| B9 | major | P17 l.3619 | No automatic capacity or skill attribution: no attendance or revocation evidence feed. | code | OPEN |
| B10 | minor | S12 l.1581 | CPM tails use summed work minutes (packed days). | code | OPEN |
| B11 | minor | S12 l.1541, l.1560 | The accrued exposure floor is never applied. | code | FIXED: the modeled exposure never counts below the already-accounted floor, so the increment and the priority weight start above it |
| B12 | minor | S13 l.1723 | Dispatch does not recheck crew size against current demand. | code | FIXED: an action whose crew is no longer the full current demand is blocked (CREW_SIZE_CHANGED) |
| B13 | minor | S08 l.1298 | In-progress rework is rescheduled for its full P75. | script | FIXED: remaining minutes are kept for rework as for any job |
| B14 | minor | S08 l.1259 | The rework comparison group ignores model/process/revision/crew. | code | OPEN |
| B15 | major | S05 l.906, P09 l.3091 | Template prerequisites missing on an aircraft are dropped silently. Each minor model's network spans every customer, and customer options leave tasks off some aircraft (A>B>C for one customer, A>C for another), so the successor lost its real prerequisite. | code + owner | FIXED: missing tasks are bridged to their nearest carried ancestors (composed type, summed lag, hard only if every edge is); never dropped or taken as done |
| B16 | minor (inactive) | S09 l.1372 | A CS hard latest date is not enforced in the search (the policy sets NONE today). | code | FIXED: the search and the defense defer work that cannot end by it; both cores tested |
| B17 | minor | S17 l.2022, P23 l.4018 | Live CI jobs do not wait for the UI and rehearsal jobs, and the scheduler installs an index wheel from any commit. | CI file | FIXED: acquisition needs every verify job; the scheduler installs the same-commit wheel, and the native core refuses a wheel of another protocol |
| B18 | minor | S17 l.1985 | Worker retries are unbounded, with no deadline or lease renewal. | code | FIXED: three attempts, a 24-hour deadline, and a lease covering twice the run's budget |
| B19 | minor | S01 l.131 | The twin does not show the behind-station value or its date basis (the math is right). | code | FIXED: each twin cell carries the gate recognition frozen in its goal (base and bonus, by date basis), reconciled to the goal's effort; `behind_station` values every in-scope line and station gate on the selected or knowledge date with weight, cap, grace and basis; the Twin page shows both |
| B20 | minor | S14 l.1801 | Badge awards carry no earning unit IDs or policy. | code | FIXED: awards and their badge events carry the exact earning units and the policy id |
| B21 | major | S12 l.1617 "Attempt1 exact incumbent day/shift/start" | The defense rebuilt an incumbent start from its shift's date and local time, so an S3 start after local midnight came back 24 hours early. Most S3 commitments were dropped as UNSAFE, with false safety alerts on delivery. The validator rebuilt starts the same way, so it agreed. | data | FIXED: `shift_local_minute` in both; S3 UNSAFE fell from about 116 to 14 per shift in a 6-aircraft run |
| B22 | major | S12 l.1617, S08 l.1298 | Incumbent tasks were matched by phase ordinal. Written-back progress re-splits a job's remaining work and renumbers it from 0, so commitments landed on the wrong pieces or went missing. | data | FIXED: matched by minutes still to go (`IncumbentPlan.positions`); done work is PERFORMED, a folded piece MERGED; the validator checks both |

## Checked and correct

- Delivery hysteresis (2-day hold, 7-day bad news, sustained replans).
- Commitment defense: three-day horizon, two attempts, minimal move, committed first.
- The nine checks with stable IDs and the extended checks; the publication order (validate, stage, readback, sign, head last).
- Calendars and shifts: S3 rule, 60-minute allowance, no start after effective − 30.
- Activation quota floor(0.85N); full crews from the team's own pool; certification expiry filtering.
- QA phases (45 + 45, one AND release); the CS earliest date as a start floor.
- The canonical formula on the S14 examples (pooled 72); the eight-cause, five-excusable taxonomy, notes, relief union, PLAN_CHURN track and appeals.
- Gate math (35/100, five-day cap, two-day grace).
- Virtual MHE/PSC/TST pools (10 people, union of certifications, forecast only).
- Isolated scenarios and grounded explanations.
- The recent native-core changes (exact start and shift skipping, FxHash, GIL-free search, no 48-start cap) alter no rule.
