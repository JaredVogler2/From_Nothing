# Observation feeds: what happened after publication

After FOCUS publishes a plan and freezes each team-shift's goal, these eight feeds tell it what actually happened. FOCUS collects all of them at one as-of instant into an observation run. `lifecycle_events` and `actual_intervals` record the work itself: lifecycle facts and raw clock time. `goal_amendments` adds approved emergency work to frozen goals. `defect_evidence`, `attendance_evidence` and `holding_evidence` are source evidence that automatic attribution turns into disruption claims. `disruption_claims` and `excusal_decisions` carry manual claims and the reviewed decisions that alone create relief. Together they produce the unit facts behind the canonical team-shift score and its excusal process.

| Dataset | Kind | Required for official evaluation | Store collection | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`lifecycle_events`](#lifecycle_events) | FEED | Yes | lifecycle_events | None: NOT_PROBED (NOT_INSTALLED) | Yes | What happened to each job and phase: starts, verified completions, cancellations, reopens, voids and corrections. |
| [`actual_intervals`](#actual_intervals) | FEED | Yes | actual_intervals | None: NOT_PROBED (NOT_INSTALLED) | Yes | Raw clock intervals per person and phase: evidence of actual starts and effort burn, never credit. |
| [`defect_evidence`](#defect_evidence) | FEED | No | defect_evidence | None: NOT_PROBED (NOT_INSTALLED) | Yes | Which team caused the defect behind a rework job: explicit rework origin evidence. |
| [`attendance_evidence`](#attendance_evidence) | FEED | No | attendance_evidence | None: NOT_PROBED (QUERY_MISSING) | Yes | Call-offs observed after planning: evidence for automatic CAPACITY_SHORTAGE claims. |
| [`holding_evidence`](#holding_evidence) | FEED | No | holding_evidence | None: NOT_PROBED (QUERY_MISSING) | Yes | Certificate revocations and reinstatements observed after planning: evidence for automatic SKILL_SHORTAGE claims. |
| [`goal_amendments`](#goal_amendments) | FEED | Yes | goal_amendments | None: NOT_PROBED (NOT_INSTALLED) | Yes | Approved feasible emergency additions to a frozen goal (B = B0 + additions). |
| [`disruption_claims`](#disruption_claims) | FEED | Yes | disruption_claims | None: NOT_PROBED (NOT_INSTALLED) | Yes | Manual claims that missed frozen units had a cause: evidence for review, never relief. |
| [`excusal_decisions`](#excusal_decisions) | FEED | Yes | excusal_decisions | None: NOT_PROBED (NOT_INSTALLED) | Yes | Reviewed excusal decisions and appeals: the only source of relief (E). |

## How FOCUS reads these feeds

The feed contract is `FEEDS` in `focus/performance/actuals.py`. The typed facts are in `focus/contracts/events.py`.

### Terms used below

- **Job, line and SOI.** A job is one SOI (the master task list's shop-order identifier) on one line. FOCUS identifies it as `JobKey(site, program, LINE_NUMBER, SOI)`.
- **Phase, phase kind and ordinal.** FOCUS splits each job into typed phases: WORK segments, IP_QA (in-process QA holds), FINAL_QA, OTHER_BUYOFF branches and a zero-work RELEASE milestone. The phase kind is that type. The ordinal numbers the phases of one kind within the job, from 0.
- **Goal of record and B0.** For each obligation day, the first accepted EXECUTABLE plan freezes a goal for every team-shift. B0 is that goal's original effort in standard person-minutes. Later replans never change it.
- **Earning unit and segment.** A frozen goal is a set of earning units: one per time segment of one planned assignment, numbered from 0, each with its share of the phase's standard effort.
- **Credit window and credit deadline.** A verified completion credits the goal whose shift window contains it, then the goal whose authorized OT window contains it, then the next goal that planned it (early). The credit deadline is the end of the authorized OT window, or the shift end.
- **Offplan output (U).** A verified completion that falls in no goal's credit window. It counts for the team-shift whose window contains it.
- **OOS (sequence violation).** Out-of-sequence work: an actual start before a hard FS or SS predecessor is satisfied, or a finish before an FF predecessor finished. A discretionary violation draws the O charge, once per unit. A DIRECTED one does not.
- **Claim, decision, relief (E) and review (excusal) track.** A claim says missed units had one of eight causes. An excusal decision reviews claims. Relief E is the effort of approved units that are still missed. The review track is STANDARD or PLAN_CHURN.
- **Plan's source cutoff.** The cutoff of the source run an accepted plan was built on. Attendance and certificate evidence count only if observed after it.
- **As-of.** The instant a collection runs at. It is also the ingestion time of everything that run admits.

### One collection run

`collect_observations` builds one run (`python -m focus.cli collect-observations`, or the pipeline's `observe_evaluate` step):

1. **Capture.** All eight feeds are read at one as-of. A row is visible when `OBSERVED_AT_UTC` ≤ as-of. A missing or malformed `OBSERVED_AT_UTC` leaves the row visible so that it can be quarantined. But `goal_amendments`, `disruption_claims` and `excusal_decisions` never parse that column again, so on those feeds such a row is admitted at every as-of.
2. **Normalize.** Each row becomes a typed fact. Natural keys resolve to the ids source admission assigns: the job from `JobKey(site, program, LINE_NUMBER, SOI)`, the phase as `<job id>.<PHASE_KIND>.<PHASE_ORDINAL>`, and the person from `PersonKey(PERSON_KEY, namespace)` with the same native person ids as the workforce datasets. FOCUS never trims or case-folds a key. A malformed row is quarantined with a stable code; nothing is defaulted.
3. **Append.** Records are write-once. Record ids derive from the source key, plus the revision for `lifecycle_events` and `actual_intervals`. A replayed row is a counted duplicate. The same id with different content is quarantined as `REVISION_CONTENT_CONFLICT`, and the first ingestion stays the as-known truth.
4. **Derive.** FOCUS derives sequence violations, disappearances and then automatic claims. Automatic attribution yields claims only, never decisions or relief.
5. **Receipt.** The run stores one receipt per feed plus the quarantine list, and moves the `observation/<scope>` head forward by compare-and-set.

Feeds are processed in this order: `lifecycle_events`, `actual_intervals`, `defect_evidence`, `attendance_evidence`, `holding_evidence`, `goal_amendments`, `disruption_claims`, `excusal_decisions`, then derived facts. So a claim can name a unit added by an amendment in the same run, and a decision can review a claim from the same run.

**Fixture and live readers.** Fixture mode reads `fixtures/inputs/<scenario>/observations/<feed>.json`. Each file holds `dataset`, `schema_version` 1, `columns` in exactly the `FEEDS` order, `rows` whose keys are exactly those columns, and a `receipt`. The live reader (`LiveObservationReader`) runs no SQL. It reports NOT_PROBED for every feed, with reason `NOT_INSTALLED` when the draft query file exists and `QUERY_MISSING` when it does not (`attendance_evidence` and `holding_evidence`).

### Formats the parser accepts

- **text:** a JSON string. Required text must not be blank and is kept exactly as sent. Optional text may be null or empty.
- **integer:** a JSON number, or text of digits. Exception: `LINE_NUMBER`, `PHASE_ORDINAL` and the numbers inside work references must be JSON numbers.
- **boolean:** true or false, or text Y, N, TRUE, FALSE, 1 or 0 in any case. The numbers 1 and 0 are rejected.
- **date:** ISO date text, for example `2026-10-26`.
- **timestamp:** ISO 8601 (RFC 3339) text with an explicit offset, for example `2026-10-26T12:35:00Z`. FOCUS converts it to UTC. Text without an offset is `NAIVE_DATETIME`; anything else is `BAD_DATETIME`.
- **JSON list:** a JSON array, or text holding one. Null or empty text means an empty list.
- **work reference:** a JSON object with exactly `line` (integer ≥ 1), `soi` (text), `kind` (a phase kind) and `ordinal` (integer ≥ 0), plus an optional `segment` (integer ≥ 0). Fixture example: `{"kind": "WORK", "line": 1303, "ordinal": 0, "segment": 1, "soi": "ROOT3"}`.

### Rejected rows (quarantine)

A rejected row is not stored. The run's quarantine list records the feed, the row's key (or a hash when the key is missing), a stable code and a detail. The draft queries return every row observed up to the as-of, so a quarantined row is tried again on the next collection. Common codes, where the column type applies: `FIELD_REQUIRED`, `TYPE_MISMATCH`, `OUT_OF_RANGE`, `EMPTY_KEY`, `BAD_DATE`, `BAD_DATETIME`, `NAIVE_DATETIME`, `BAD_JSON`, `BAD_PHASE_KIND`, `BAD_ORDINAL`, `BAD_SHIFT` and `REVISION_CONTENT_CONFLICT`. Each feed below lists its own codes. Derived facts can add `VIOLATION_REDERIVATION_DIFFERS`.

### Coverage and official evaluation

Five feeds are required (`FeedSpec.required`): `lifecycle_events`, `actual_intervals`, `goal_amendments`, `disruption_claims` and `excusal_decisions`. Each team-shift evaluation needs every required feed usable in the latest observation run: a complete receipt and status ROWS or CERTIFIED_EMPTY. Each must also be covered through the shift's credit deadline. Otherwise the evaluation gets a reason `COVERAGE_<FEED>_<STATUS>` or `COVERAGE_<FEED>_BEHIND` and is UNKNOWN with no score, under a DEMONSTRATION or an OFFICIAL policy alike. The optional feeds never withhold an evaluation. See [Producer receipts](#producer-receipts).

### How the feeds reach the score

`focus/performance/score.py` computes one canonical score per team-shift in standard person-minutes:

- **Cp** (verified planned or amended completion, counted once): the effort of goal units credited by verified COMPLETED and PROGRESS facts from `lifecycle_events`, under the credit-window rule above.
- **U** (offplan output): verified completions from `lifecycle_events` outside every credit window.
- **O** (OOS charge): `score.oos_rate` (demo 0.10) times the planned effort of discretionary sequence violations, once per unit. Violations come from actual starts and finishes in `lifecycle_events` and from `actual_intervals`.
- **B = B0 + approved emergency additions**, from `goal_amendments`.
- **E** (relief): the effort of units approved by `excusal_decisions` on submitted claims (from `disruption_claims` or automatic attribution) that are still missed at the cutoff.
- **D = B − E.** When D > 0: A = min(1, Cp/D); H = Cp/(Cp+U), or 0 when Cp+U = 0; final = clamp(100·(wA·A + wH·H) − 100·O/D, 0, 100), with demo weights 0.70 and 0.30. D = 0 is NEUTRAL. Missing coverage, an UNKNOWN unit, unknown U or O, or an open window makes the evaluation UNKNOWN.

## `lifecycle_events`

**Purpose.** The record of what happened to each job and phase after publication: starts, verified completions, verified segment progress, cancellations, detabs, terminal statuses, reopens, and the voids and corrections that revise them. FOCUS replays these facts in effective order to decide which frozen units were earned, missed or unknown, which completions are offplan, and which starts broke the sequence (OOS). Only verified completion earns credit.

| | |
| --- | --- |
| Kind | FEED. Proposed landing table `WEB_APP.FOCUS_FN_LifecycleSource` (`sql/migrations/008_lifecycle.sql`); draft query `queries/actuals/lifecycle_events.sql`, which the live reader does not run. Fixture file `observations/lifecycle_events.json`. |
| Grain and key | One row per revision of one source lifecycle fact. Key (`SOURCE_EVENT_KEY`, `SOURCE_REVISION`): the record id digests scope, feed, key and revision, and the 008 landing table has the same primary key. |
| Required for official evaluation | Yes (`FeedSpec.required`). |
| Store collection | `lifecycle_events` (type `LifecycleEvent`). The same collection also holds reported observations (origin REPORTED) and derived DISAPPEARED facts. Proposed SQL history: `FOCUS_FN_Event`. |
| If missing or empty | Live today: NOT_PROBED (`NOT_INSTALLED`), so every evaluation gets `COVERAGE_LIFECYCLE_EVENTS_NOT_PROBED` and is UNKNOWN. Fixture: FAILED gives `COVERAGE_LIFECYCLE_EVENTS_FAILED`; EMPTY_UNCERTIFIED gives `COVERAGE_LIFECYCLE_EVENTS_EMPTY_UNCERTIFIED`; a watermark short of the credit deadline gives `COVERAGE_LIFECYCLE_EVENTS_BEHIND`. CERTIFIED_EMPTY is a valid empty feed. |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`. Four clocks: effective (when it happened), recorded (by the source system), observed (by the producer) and ingested (the run's as-of, set by FOCUS). FOCUS requires recorded ≤ observed, and effective ≤ recorded for physical kinds. As-known replays filter by ingestion time. |
| Quarantine codes | Common codes, plus `DERIVED_ONLY`, `BAD_EVENT_KIND`, `BAD_SEGMENTS`, `SEGMENTS_REQUIRED`, `SEGMENTS_UNEXPECTED`, `PHASE_REQUIRED`, `REPORTED_NOT_VERIFIED`, `VERIFIER_REQUIRED`, `DIRECTIVE_UNEXPECTED`, `SUPERSEDES_REQUIRED`, `SUPERSEDES_UNEXPECTED`, `OBSERVED_BEFORE_RECORDED` and `EFFECTIVE_AFTER_RECORDED`. |
| Read by | `focus/performance/actuals.py` (normalization); `focus/performance/lifecycle.py` (revisions, ordered lifecycle, credit, offplan, sequence violations, disappearance); `focus/performance/score.py` (through unit facts); `focus/performance/disruption.py` (PLAN_CHURN and predecessor attribution); `focus/analytics/twin.py` (observed job states); `focus/analytics/progression.py` (credits and unlocks). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOURCE_EVENT_KEY` | text, required | The source system's stable key for one lifecycle fact. | Part of the record id. Revisions of one key compete, and VOIDED or CORRECTION rows name it in `SUPERSEDES_EVENT_KEY`. |
| `SOURCE_SEQUENCE` | integer ≥ 0, required | The source system's sequence number for the fact. | Breaks ties when ordering facts with the same effective time, and between revisions of equal rank. |
| `SOURCE_REVISION` | text, required | The source's revision label for this fact. | Part of the record id. The highest revision of a key supersedes the others; numeric revisions outrank text ones. |
| `LINE_NUMBER` | integer ≥ 1, required; a JSON number (text is `TYPE_MISMATCH`) | The job's line number, as in the master task list. | With `SOI`, builds the job id exactly as source admission does. |
| `SOI` | text, required | The job's native identifier: the master task list's shop-order identifier. | With `LINE_NUMBER`, builds the job id. It must match byte for byte. |
| `PHASE_KIND` | text: WORK, IP_QA, FINAL_QA, OTHER_BUYOFF or RELEASE; null for a job-level fact | The type of phase the fact is about. | With `PHASE_ORDINAL`, builds the phase id. Null makes a job-level fact that applies to every phase of the job. |
| `PHASE_ORDINAL` | integer ≥ 0; a JSON number (text is `BAD_ORDINAL`); ignored when `PHASE_KIND` is null | Which phase of that kind within the job, counted from 0. | Part of the phase id `<job id>.<kind>.<ordinal>`. |
| `SEGMENTS` | JSON list of distinct integers ≥ 0; null or empty for none | The frozen segment earning units that a PROGRESS fact verifies. | A verified PROGRESS credits exactly these units. Required for PROGRESS, and allowed only on PROGRESS and its CORRECTION. |
| `KIND` | text, one of the lifecycle kinds listed below; required | What happened. | Drives the ordered lifecycle (see the kinds below). DISAPPEARED is refused as `DERIVED_ONLY`. |
| `PERSON_KEY` | text, optional | The native id of the person the fact names; the code does not define the person's role further. | Resolved to the FOCUS person id and stored; a CORRECTION can replace it. Nothing reads it after normalization. |
| `EFFECTIVE_AT_UTC` | timestamp, required | When the fact took effect (008: "when it physically happened"). | Orders each phase's facts and sets the credit time, the start time for sequence checks, and cancel-versus-completion order. |
| `RECORDED_AT_UTC` | timestamp, required | When the source system recorded the fact. | Decides what was known when work started (OOS classification), picks the latest CORRECTION, and breaks revision ties. |
| `OBSERVED_AT_UTC` | timestamp, required | When the producer saw the row (008: "seen by the producer query"). | The visibility cutoff. Stored; nothing reads it after capture. |
| `VERIFIED` | boolean, required | Whether the source verified the fact. | Only a verified COMPLETED or PROGRESS earns credit. A REPORTED_COMPLETION must be false (`REPORTED_NOT_VERIFIED`). |
| `VERIFIED_BY` | text; required when `VERIFIED` is true | The verifying authority. | Presence is checked (`VERIFIER_REQUIRED`) and the value stored; nothing else reads it. |
| `DIRECTIVE_REF` | text, optional; STARTED only | The explicit authority that directed an out-of-sequence start. | A directed start is classified DIRECTED: not discretionary, so no OOS charge. |
| `SUPERSEDES_EVENT_KEY` | text; required for VOIDED and CORRECTION, empty otherwise | The `SOURCE_EVENT_KEY` of the fact this row voids or corrects. | VOIDED removes that fact. CORRECTION replaces its clocks, person, verification and, for PROGRESS, its segments. |
| `EVIDENCE_REF` | text, optional | A reference to the source evidence for the fact. | Only its SHA-256 hash is kept, inside the record. A changed value under the same key and revision is `REVISION_CONTENT_CONFLICT`. |
| `DETAIL` | text, optional; first 500 characters kept | Free-text detail about the fact. | Stored; nothing reads it after normalization. |

**Lifecycle kinds** (`focus/contracts/events.py`, `focus/performance/lifecycle.py`):

- **STARTED:** work began. It sets the phase start and is an actual start for sequence checks.
- **PROGRESS:** verified completion of the segment earning units in `SEGMENTS`. Unverified PROGRESS earns nothing.
- **COMPLETED:** verified physical completion of the whole phase. It credits that phase's units in exactly one goal. Unverified COMPLETED earns nothing.
- **REPORTED_COMPLETION:** a reported completion. Always a claim, never verified credit.
- **CANCELLED and DETABBED:** administrative withdrawal; the code treats both the same and does not define detab further. Before completion they earn nothing and the unit stays in B0. After verified completion, `lifecycle.cancel_after_verified_completion` decides (shipped: PRIOR_VERIFIED_COMPLETION_EARNS).
- **TERMINAL_STATUS:** a terminal source status. Without verified completion the disposition is UNKNOWN.
- **REOPENED:** revokes credit until the work is verified again.
- **VOIDED and CORRECTION:** revisions of another fact, named in `SUPERSEDES_EVENT_KEY`.
- **SEQUENCE_VIOLATION:** a source-asserted out-of-sequence fact. It counts as an actual start.
- **OOS_DISCRETIONARY:** a source classification of a violation as discretionary. It is attached as corroborating evidence to the derived violation; it does not change the classification.
- **DISAPPEARED** is derived by FOCUS when planned work's job leaves the admitted census. A feed may not send it.

**Rules and pitfalls.**
- Credit comes only from a verified COMPLETED or a verified PROGRESS. REPORTED_COMPLETION, TERMINAL_STATUS, CANCELLED, DETABBED and DISAPPEARED never earn. A person's report through `POST /api/v1/reported-observations` lands in this collection as an unverified REPORTED_COMPLETION claim.
- Keys must match source admission exactly. FOCUS does not check that the job or phase exists, so a row with a mistyped SOI or phase is stored but never credits anything.
- Never edit a row. A replayed row is a counted duplicate; new content under the same key and revision is `REVISION_CONTENT_CONFLICT`, and the first version stands. Correct with a higher `SOURCE_REVISION`, or with a VOIDED or CORRECTION row.
- A correction is known only from its own clocks: as-known replay filters by ingestion time, and OOS classification uses only facts recorded before the start.
- TERMINAL_STATUS or disappearance without verified completion makes the unit UNKNOWN and the team-shift evaluation UNKNOWN. The frozen unit is never deleted.
- Physical kinds (STARTED, PROGRESS, COMPLETED, REPORTED_COMPLETION) need a phase (`PHASE_REQUIRED`) and must not be effective after they were recorded (`EFFECTIVE_AFTER_RECORDED`).

## `actual_intervals`

**Purpose.** Raw clock intervals: which person was on the clock for which phase, and when. FOCUS uses them as evidence of when work actually started, for sequence-violation detection, and of actual effort burn, for proposed DURATION_OVERRUN claims by the union method. They never earn completion credit.

| | |
| --- | --- |
| Kind | FEED. Proposed landing table `WEB_APP.FOCUS_FN_ActualIntervalSource` (008); draft query `queries/actuals/actual_intervals.sql`, which the live reader does not run. Fixture file `observations/actual_intervals.json`. |
| Grain and key | One row per revision of one raw clock interval of one person on one phase. Key (`SOURCE_EVENT_KEY`, `SOURCE_REVISION`): the record id digests scope, key and revision, and the 008 landing table has the same primary key. |
| Required for official evaluation | Yes (`FeedSpec.required`). |
| Store collection | `actual_intervals` (type `ActualInterval`). Proposed SQL history: `FOCUS_FN_ActualInterval`. |
| If missing or empty | Live today: NOT_PROBED (`NOT_INSTALLED`), so every evaluation gets `COVERAGE_ACTUAL_INTERVALS_NOT_PROBED` and is UNKNOWN. Fixture: FAILED gives `COVERAGE_ACTUAL_INTERVALS_FAILED`; EMPTY_UNCERTIFIED gives `COVERAGE_ACTUAL_INTERVALS_EMPTY_UNCERTIFIED`; a watermark short of the credit deadline gives `COVERAGE_ACTUAL_INTERVALS_BEHIND`. CERTIFIED_EMPTY is a valid empty feed. |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`. `START_UTC` and `END_UTC` are the clock instants, and the start must be before the end. Ingestion time is the run's as-of. |
| Quarantine codes | Common codes, plus `EMPTY_INTERVAL`. |
| Read by | `focus/performance/actuals.py` (normalization); `focus/performance/lifecycle.py` (`derive_sequence_violations`: interval starts are actual starts; voided intervals invalidate OOS triggers); `focus/performance/disruption.py` (`phase_burn_minutes` for DURATION_OVERRUN). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOURCE_EVENT_KEY` | text, required | The source key of one raw clock interval. | Part of the record id; revisions of one key compete. |
| `SOURCE_REVISION` | text, required | The source's revision label for the interval. | Part of the record id. Only the latest revision of each key counts; numeric revisions outrank text ones. |
| `LINE_NUMBER` | integer ≥ 1, required; a JSON number | The job's line number. | With `SOI`, builds the job id as source admission does. |
| `SOI` | text, required | The job's native identifier (shop-order identifier). | With `LINE_NUMBER`, builds the job id. |
| `PHASE_KIND` | text: WORK, IP_QA, FINAL_QA, OTHER_BUYOFF or RELEASE; required | The type of phase the clock was charged to. | With `PHASE_ORDINAL`, builds the phase id. |
| `PHASE_ORDINAL` | integer ≥ 0, required; a JSON number | Which phase of that kind within the job, counted from 0. | Part of the phase id. |
| `PERSON_KEY` | text, required | The native id of the interval's owner: the person on the clock. | Resolved to the FOCUS person id. Intervals are unioned per owner before burn is summed. |
| `START_UTC` | timestamp, required | When the clock interval started. | Every interval start is an actual start, and the earliest actual start per phase is checked for sequence violations. It also feeds the burn union. |
| `END_UTC` | timestamp, required; after `START_UTC` | When the clock interval ended. | Feeds the burn union. A start at or after the end is `EMPTY_INTERVAL`. |
| `IS_VOIDED` | boolean, required | Whether the source voided the interval. | A voided latest revision is dropped from both uses, and OOS violations it triggered stop charging (`TRIGGER_EVIDENCE_VOIDED`). |
| `OBSERVED_AT_UTC` | timestamp, required | When the producer saw the row. | The visibility cutoff. Stored; nothing reads it after capture. |
| `EVIDENCE_REF` | text, optional | A reference to the source evidence for the interval. | Only its SHA-256 hash is kept, inside the record; it is part of the content identity. |

**Rules and pitfalls.**
- Intervals are evidence, never completion credit. Credit comes only from verified lifecycle facts.
- The union method (the repo's raw clock island method) merges each owner's intervals by running maximum, so overlapping and nested clocks merge and gaps stay. The owners' totals are then summed. Send raw intervals per owner; do not pre-merge, bridge gaps or clip them.
- Burn above a phase's frozen standard only proposes DURATION_OVERRUN for the goal's other missed units (status PROPOSED: a person must add notes). There is no automatic 3x overrun allowance.
- An interval that starts before a hard predecessor is satisfied creates a permanent sequence violation. Voiding the interval later stops the charge but keeps the record.
- Correct an interval with a new `SOURCE_REVISION` of the same key, with `IS_VOIDED` true to remove it. A changed row under the same key and revision is `REVISION_CONTENT_CONFLICT`.
- These rows do not feed duration statistics; that is the separate `clock_observations` planning input.

## `defect_evidence`

**Purpose.** Explicit defect-origin facts for rework jobs: which team caused the defect that a rework job repairs. Rework origin comes only from this feed or from the admitted rework classification. FOCUS never infers it from DAG direction or from the executing team.

| | |
| --- | --- |
| Kind | FEED. Proposed landing table `WEB_APP.FOCUS_FN_DefectEvidenceSource` (008); draft query `queries/actuals/defect_evidence.sql`, which the live reader does not run. Fixture file `observations/defect_evidence.json`. |
| Grain and key | One row per defect-origin record for one rework job. Key `SOURCE_EVENT_KEY`: the record id digests scope and key, with no revision. The 008 landing table has the same primary key. |
| Required for official evaluation | No (`FeedSpec.required` is false). |
| Store collection | `defect_evidence` (type `DefectEvidence`). Proposed SQL history: `FOCUS_FN_DefectEvidence`. |
| If missing or empty | Live today: NOT_PROBED (`NOT_INSTALLED`). The status is recorded on the run receipt, but no coverage code is raised and evaluations are not withheld. Without rows, a rework origin can still come from an admitted rework link; otherwise automatic REWORK_INJECTION claims stay PROPOSED and manual origins are `ORIGIN_TEAM_UNEVIDENCED`. |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`, the only clock. It is not compared with the plan's source cutoff. |
| Quarantine codes | Common codes, plus `BAD_REWORK_CLASS` and `ORIGIN_EVIDENCE_REQUIRED`. |
| Read by | `focus/performance/actuals.py` (normalization); `focus/performance/disruption.py` (`Intake._origin_evidenced` for manual claims, `_injected_rework` for automatic claims). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOURCE_EVENT_KEY` | text, required | The source key of one defect-origin record. | The record id. There is no revision, so any change under the same key is `REVISION_CONTENT_CONFLICT`. |
| `LINE_NUMBER` | integer ≥ 1, required; a JSON number | The line of the rework job, and of `ROOT_SOI`. | Builds the job ids of `SOI` and `ROOT_SOI`. |
| `SOI` | text, required | The rework job's native identifier. | Matched against jobs worked as approved additions or offplan output in a team-shift (automatic REWORK_INJECTION). |
| `ROOT_SOI` | text, optional | The root job of the rework, on the same line. | Resolved to a job id and stored; nothing reads it after normalization. |
| `REWORK_CLASS` | text: NC, PU or CI; required | The rework class. The repo does not expand these codes. | Checked (`BAD_REWORK_CLASS`) and stored; nothing reads it after normalization. |
| `DEFECT_ORIGIN_TEAM` | text, required (not empty) | The team where the defect originated, as a source fact. | Becomes the origin team of automatic REWORK_INJECTION claims, and admits a manual claim's `ORIGIN_TEAM` when the claim cites this row. |
| `EVIDENCE_REF` | text, required (not empty) | The reference of the source defect record (fixture example: `FIXTURE-NCR-303`). | Cited as `DEFECT:<ref>` in automatic claims. A manual claim must cite this value, `DEFECT:<value>` or the record id. |
| `OBSERVED_AT_UTC` | timestamp, required | When the producer saw the row. | The visibility cutoff. Stored; nothing reads it after capture. |

**Rules and pitfalls.**
- The origin team is a source fact. Never fill it from the DAG direction, the predecessor or the executing team.
- A manual REWORK_INJECTION claim with an `ORIGIN_TEAM` is admitted only if its `EVIDENCE_REFS` cite a row with that origin team, or an admitted rework link. Otherwise it is `ORIGIN_TEAM_UNEVIDENCED`.
- Automatic attribution looks at rework jobs worked in a team-shift outside B0 (approved additions or offplan output). A matching row makes the REWORK_INJECTION claim SUBMITTED with this origin. A claim is still never relief.
- Send one row per rework job and never reuse a key. Rows have no revision, and if several rows name one rework job, automatic attribution uses only one of them.
- The feed's description says "observed after the planning cutoff", but no code checks `OBSERVED_AT_UTC` against the plan's source cutoff.

## `attendance_evidence`

**Purpose.** Source attendance records observed after the plan was made: call-offs, and later records that correct them, for named people on named shifts. FOCUS uses a planned crew member's call-off as evidence for an automatic CAPACITY_SHORTAGE claim on that person's missed units. What the plan already knew is not a disruption.

| | |
| --- | --- |
| Kind | FEED. No live path yet: `queries/actuals/attendance_evidence.sql` does not exist, and 008 has no landing table for this feed. Fixture file `observations/attendance_evidence.json` (certified empty in the demo). |
| Grain and key | One row per attendance record for one person and one shift. Key `SOURCE_EVENT_KEY` (from code): the record id digests scope and key, with no revision. |
| Required for official evaluation | No (`FeedSpec.required` is false). |
| Store collection | `attendance_evidence` (type `AttendanceEvidence`). No SQL history table is proposed. |
| If missing or empty | Live today: NOT_PROBED with reason `QUERY_MISSING: queries/actuals/attendance_evidence.sql`. No coverage code is raised and evaluations are not withheld. Without rows FOCUS makes no automatic CAPACITY_SHORTAGE claim; people claim it by hand. |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`. `RECORDED_AT_UTC` (≤ `OBSERVED_AT_UTC`) picks the latest record per person and shift. A call-off counts only when `OBSERVED_AT_UTC` is after the plan's source cutoff. |
| Quarantine codes | Common codes, plus `OBSERVED_BEFORE_RECORDED` and `ATTENDANCE_EVIDENCE_REQUIRED`. |
| Read by | `focus/performance/actuals.py` (normalization); `focus/performance/disruption.py` (`_latest_attendance` and `_crew_shortages` in `auto_attribute`). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOURCE_EVENT_KEY` | text, required | The source key of one attendance record. | The record id. There is no revision, so a correction is a new record. |
| `PERSON_KEY` | text, required | The native id of the person. | Resolved to the FOCUS person id and matched against the planned crew of each missed unit. |
| `SHIFT_DATE` | date, required | The shift's production day. | With `SHIFT_NUMBER`, builds the shift id `<site>:<date>:S<n>`, which must equal the goal's shift. |
| `SHIFT_NUMBER` | integer 1, 2 or 3, required | The shift number on that day. | Part of the shift id. Any other value is `BAD_SHIFT`. |
| `PRESENT` | boolean, required | Whether the person attended; false is a call-off. | Only a latest record with `PRESENT` false counts as CAPACITY_SHORTAGE evidence. |
| `REASON` | text, optional; first 200 characters kept | The source's free-text reason for the attendance status. | Stored; nothing reads it after normalization. |
| `RECORDED_AT_UTC` | timestamp, required | When the source recorded the attendance record. | The latest record per person and shift wins, so a later record corrects an earlier one. It must not be after `OBSERVED_AT_UTC`. |
| `OBSERVED_AT_UTC` | timestamp, required | When the producer saw the row. | The visibility cutoff. A call-off counts only if observed after the plan's source cutoff. |
| `EVIDENCE_REF` | text, required (not empty) | The reference of the source attendance record. | Cited as `ATTENDANCE:<ref>` in the automatic claim. Empty is `ATTENDANCE_EVIDENCE_REQUIRED`. |

**Rules and pitfalls.**
- Automatic attribution runs on goals whose credit window has closed. For each MISSED unit, each person on its planned crew whose latest record for that shift has `PRESENT` false, observed after the plan's source cutoff, yields an automatic CAPACITY_SHORTAGE claim with status SUBMITTED. It cites `ATTENDANCE:<EVIDENCE_REF>` and the record id.
- Those claims are evidence for review, never relief. E changes only when an excusal decision approves the units.
- A call-off observed at or before the plan's source cutoff is ignored: the plan already knew it.
- To correct a call-off, send a new record for the same person and shift with a later `RECORDED_AT_UTC`. The next collection appends a WITHDRAWN revision of the automatic claim.
- `PERSON_KEY` must be the same native id that source admission used for the plan's crews. A mismatch matches nothing, silently.
- Units of a cancelled or detabbed phase get a PLAN_CHURN claim instead.

## `holding_evidence`

**Purpose.** Certificate revocations and reinstatements observed after the plan was made. FOCUS uses a planned crew member's loss of a certificate the work needs as evidence for an automatic SKILL_SHORTAGE claim. The feed uses the same REVOKED and REINSTATED vocabulary as the `holding_events` planning input.

| | |
| --- | --- |
| Kind | FEED. No live path yet: `queries/actuals/holding_evidence.sql` does not exist, and 008 has no landing table for this feed. Fixture file `observations/holding_evidence.json` (certified empty in the demo). |
| Grain and key | One row per certificate event for one person. Key `SOURCE_EVENT_KEY` (from code): the record id digests scope and key, with no revision. |
| Required for official evaluation | No (`FeedSpec.required` is false). |
| Store collection | `holding_evidence` (type `HoldingEvidence`). No SQL history table is proposed. |
| If missing or empty | Live today: NOT_PROBED with reason `QUERY_MISSING: queries/actuals/holding_evidence.sql`. No coverage code is raised and evaluations are not withheld. Without rows FOCUS makes no automatic SKILL_SHORTAGE claim; people claim it by hand. |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`. `EFFECTIVE_AT_UTC` is compared with the unit's planned start and end from the accepted plan. A revocation counts only if observed after the plan's source cutoff; reinstatements are not checked against the cutoff. |
| Quarantine codes | Common codes, plus `BAD_HOLDING_EVENT` and `HOLDING_EVIDENCE_REQUIRED`. |
| Read by | `focus/performance/actuals.py` (normalization); `focus/performance/disruption.py` (`_crew_shortages` in `auto_attribute`). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOURCE_EVENT_KEY` | text, required | The source key of one certificate event. | The record id. There is no revision. |
| `PERSON_KEY` | text, required | The native id of the certificate holder. | Resolved to the FOCUS person id and matched against planned crews. |
| `CERT_CODE` | text, required | The certificate code. | Counts only if it is one of the codes in the work's requirement bundle. |
| `EVENT` | text: REVOKED or REINSTATED; required | Whether the certificate was revoked or reinstated. | REVOKED is the evidence, and a matching REINSTATED can cancel it. Other values are `BAD_HOLDING_EVENT`. |
| `EFFECTIVE_AT_UTC` | timestamp, required | When the revocation or reinstatement takes effect. | A revocation counts if effective before the unit's planned end. A reinstatement effective between the revocation and the unit's planned start cancels it. |
| `OBSERVED_AT_UTC` | timestamp, required | When the producer saw the row. | The visibility cutoff. A revocation counts only if observed after the plan's source cutoff. |
| `EVIDENCE_REF` | text, required (not empty) | The reference of the source certificate record. | Cited as `HOLDING:<ref>` in the automatic claim. Empty is `HOLDING_EVIDENCE_REQUIRED`. |

**Rules and pitfalls.**
- Automatic attribution runs on goals whose credit window has closed. A REVOKED event yields an automatic SKILL_SHORTAGE claim with status SUBMITTED when the person is on a missed unit's planned crew, the certificate is in that work's bundle, the event was observed after the plan's source cutoff, and it takes effect before the unit's planned end.
- No claim is made if a REINSTATED event for the same person and certificate takes effect between the revocation and the unit's planned start.
- Claims are evidence for review, never relief.
- A revocation the plan already knew (observed at or before the source cutoff) is ignored.
- `CERT_CODE` must use the requirement bundle's codes, and `PERSON_KEY` must use source admission's native ids. Mismatches match nothing, silently.
- Rows have no revision. Reverse a revocation with a REINSTATED event, not by editing the row.

## `goal_amendments`

**Purpose.** Approved, feasible emergency additions: one whole open phase added to one frozen team-shift goal. Each row becomes a new earning unit, so B = B0 + additions, while the original frozen units and B0 never change. Without an amendment (from this feed or the amendment command), a verified completion of work that no goal of record planned counts only as offplan output (U).

| | |
| --- | --- |
| Kind | FEED. Proposed landing table `WEB_APP.FOCUS_FN_GoalAmendmentSource` (008); draft query `queries/actuals/goal_amendments.sql`, which the live reader does not run. Fixture file `observations/goal_amendments.json`. |
| Grain and key | One row per approved addition of one phase to one team-shift goal. Key `AMENDMENT_KEY`: the record id digests scope and key, with no revision. The 008 landing table has the same primary key. |
| Required for official evaluation | Yes (`FeedSpec.required`). |
| Store collection | `goal_amendments` (type `GoalAmendment`). It also holds amendments made with `POST /api/v1/amendments`. Proposed SQL history: `FOCUS_FN_GoalAmendment`, written by `FOCUS_FN_RecordAmendment`. |
| If missing or empty | Live today: NOT_PROBED (`NOT_INSTALLED`), so every evaluation gets `COVERAGE_GOAL_AMENDMENTS_NOT_PROBED` and is UNKNOWN. Fixture: FAILED gives `COVERAGE_GOAL_AMENDMENTS_FAILED`; EMPTY_UNCERTIFIED gives `COVERAGE_GOAL_AMENDMENTS_EMPTY_UNCERTIFIED`; a watermark short of the credit deadline gives `COVERAGE_GOAL_AMENDMENTS_BEHIND`. CERTIFIED_EMPTY is a valid empty feed (no additions). |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`, used only for visibility. `DECISION_AT_UTC` is stored but not used. The addition counts from the run that ingests it. |
| Quarantine codes | Common codes, plus `GOAL_NOT_FOUND`, `BAD_WORK_REF`, `ALREADY_IN_GOAL`, `ALREADY_AMENDED` and `FEASIBILITY_REQUIRED`. |
| Read by | `focus/performance/actuals.py` (normalization and the `ALREADY_AMENDED` check); `focus/performance/lifecycle.py` (addition units in goal records, credit); `focus/performance/score.py` (B); `focus/performance/disruption.py` (claim units; additions as rework candidates); `focus/web/serializers.py` (`goal_versions`, `team_lead`); `focus/web/api.py` (`create_amendment` version check). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `AMENDMENT_KEY` | text, required | The source key of one approved addition. | The record id; it also names the new earning unit. There is no revision. |
| `TEAM` | text, required | The team whose goal gains the work. | With the shift, finds the goal of record. It is also the new unit's team. |
| `SHIFT_DATE` | date, required | The production day of the goal's shift. | Part of the shift id. |
| `SHIFT_NUMBER` | integer 1, 2 or 3, required | The shift number. | Part of the shift id. Below 1 is `OUT_OF_RANGE`; above 3 is `BAD_SHIFT`. |
| `WORK_REF` | work reference (a JSON object, or text holding one), without `segment` | The one whole phase being added. | Resolved to the phase id. It must not be an original unit (`ALREADY_IN_GOAL`) or already added (`ALREADY_AMENDED`). |
| `STANDARD_PERSON_MINUTES` | integer ≥ 1, required | The added phase's standard effort in person-minutes. | The new unit's frozen effort, added to B. It is not compared with the phase's sourced standard. |
| `EFFORT_BASIS` | text, required | How the effort figure was derived. | Stored; nothing reads it after normalization. |
| `FEASIBILITY_REF` | text, required (not empty) | A reference to the feasibility check that approved the addition. | Must be present (`FEASIBILITY_REQUIRED`); stored; nothing else reads it. |
| `AUTHORITY` | text, required | Who approved the addition. | Stored; nothing reads it after normalization. No role is checked on this feed. |
| `DECISION_AT_UTC` | timestamp, required | When the addition was approved. | Parsed and stored; nothing reads it after normalization. |
| `REASON` | text, required | Why the work was added. | Stored; nothing reads it after normalization. |
| `OBSERVED_AT_UTC` | timestamp | When the producer saw the row. | Visibility cutoff only. The normalizer neither parses nor stores it. |

**Rules and pitfalls.**
- B = B0 + approved emergency additions. The added unit can earn credit like a planned unit. B0 and the original unit ids never change.
- One whole phase per row: `WORK_REF` carries no segment.
- The feed path trusts the producer. It does not rerun the command path's readiness and crew-feasibility check (`amendments.assess_addition`), check that the phase belongs to `TEAM`, or take the effort from the phase's sourced standard. It only requires a non-empty `FEASIBILITY_REF`, so send only additions that passed an equivalent check.
- There is no withdrawal or correction path. A changed row under the same key is `REVISION_CONTENT_CONFLICT`, and a new key for the same phase is `ALREADY_AMENDED`.
- An amendment is not relief. Relief comes only from an excusal decision.
- The team-shift must fall on a day covered by an accepted EXECUTABLE plan of record (`GOAL_NOT_FOUND` otherwise). A team-shift whose B0 is 0 can still receive additions.

## `disruption_claims`

**Purpose.** Manual disruption claims: a team states that named frozen units were missed for one of the eight causes, and cites evidence. Claims are evidence for review, never relief by themselves. They fill the excusal review queue, and only a reviewed decision turns them into relief.

| | |
| --- | --- |
| Kind | FEED. Proposed landing table `WEB_APP.FOCUS_FN_DisruptionClaimSource` (008); draft query `queries/actuals/disruption_claims.sql`, which the live reader does not run. Fixture file `observations/disruption_claims.json`. |
| Grain and key | One row per claim, revision or withdrawal, each under its own key. Key `CLAIM_KEY`: the record id digests scope, the word "feed" and the key, with no revision. The 008 landing table has the same primary key. |
| Required for official evaluation | Yes (`FeedSpec.required`). |
| Store collection | `disruption_claims` (type `DisruptionClaim`). It also holds automatic claims (attribution AUTO, reported by `focus-attribution`) and claims made with `POST /api/v1/disruption-claims`. Proposed SQL history: `FOCUS_FN_DisruptionClaim`. |
| If missing or empty | Live today: NOT_PROBED (`NOT_INSTALLED`), so every evaluation gets `COVERAGE_DISRUPTION_CLAIMS_NOT_PROBED` and is UNKNOWN. Fixture: FAILED gives `COVERAGE_DISRUPTION_CLAIMS_FAILED`; EMPTY_UNCERTIFIED gives `COVERAGE_DISRUPTION_CLAIMS_EMPTY_UNCERTIFIED`; a watermark short of the credit deadline gives `COVERAGE_DISRUPTION_CLAIMS_BEHIND`. CERTIFIED_EMPTY is a valid empty feed (no manual claims). |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`, used only for visibility. `REPORTED_AT_UTC` orders a claim's revisions. |
| Quarantine codes | Common codes, plus `GOAL_NOT_FOUND`, `BAD_WORK_REF`, `DUPLICATE_WORK_REF`, `NO_UNITS`, `UNIT_NOT_IN_GOAL`, `BAD_CLAIM_STATUS`, `BAD_CAUSE`, `POLICY_TAXONOMY_MISMATCH`, `UNIT_ALREADY_EARNED`, `NOTES_REQUIRED`, `EVIDENCE_REQUIRED`, `CROSS_TEAM_SAME_TEAM`, `SAME_TEAM_CROSS_TEAM`, `ORIGIN_TEAM_UNEVIDENCED` and `SUPERSEDED_CLAIM_NOT_FOUND`. |
| Read by | `focus/performance/actuals.py` (normalization); `focus/performance/disruption.py` (`Intake`, `effective_claims`, `approved_units`, `relief_summary`); `focus/performance/score.py` (relief summary and the `CLAIMS_PENDING_REVIEW` note); `focus/web/serializers.py` (`claims_view` review queue, `turnover`); `focus/web/api.py` (`create_excusal_decision`). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `CLAIM_KEY` | text, required | The source key of one claim row. | The record id. `CLAIM_KEYS` and `SUPERSEDES_CLAIM_KEY` refer to it. |
| `TEAM` | text, required | The team of the goal the claim is against. | With the shift, finds the goal of record. The `ORIGIN_TEAM` rules compare against it. |
| `SHIFT_DATE` | date, required | The production day of the goal's shift. | Part of the shift id. |
| `SHIFT_NUMBER` | integer 1, 2 or 3, required | The shift number. | Part of the shift id. |
| `WORK_REFS` | JSON list of work references; at least one | The missed frozen units the claim is about. | Each reference resolves to frozen units of the goal. Without a segment it takes every unit of that phase. |
| `CAUSE` | text: one of the eight causes; required | The claimed cause. | Validated against the taxonomy. It sets the evidence, notes, origin and review-track rules. |
| `ORIGIN_TEAM` | text, optional | The team the claim says caused the disruption. | CROSS_TEAM_PREDECESSOR must name another team, SAME_TEAM_PREDECESSOR cannot, and a REWORK_INJECTION origin needs cited defect evidence. Shown in the review queue. |
| `EXECUTING_TEAM` | text, optional | The team that executed the affected work. | Stored and shown in the review queue; no rule checks it. |
| `EVIDENCE_REFS` | JSON list of text; null or empty for none | Citations of explicit source evidence. | Required for submitted claims of every cause except SAME_TEAM_PREDECESSOR and DURATION_OVERRUN (`EVIDENCE_REQUIRED`). Shown in the review queue. |
| `NOTES` | text, optional; first 2000 characters kept | The claimant's explanation. | Submitted SAME_TEAM_PREDECESSOR and DURATION_OVERRUN claims need at least `disruption.notes_min_chars` (10) characters (`NOTES_REQUIRED`). |
| `STATUS` | text: SUBMITTED or WITHDRAWN; required | Whether the row submits the claim or withdraws it. | Only an effective SUBMITTED claim can be reviewed, and a WITHDRAWN revision lapses approvals that rest only on it. PROPOSED is reserved for automatic claims (`BAD_CLAIM_STATUS`). |
| `REPORTED_BY` | text, required | Who reported the claim. | A decision by the same person is `SELF_REVIEW`. Shown in the review queue. |
| `REPORTED_AT_UTC` | timestamp, required | When the claim was reported. | Orders a claim's revisions; the latest wins. |
| `OBSERVED_AT_UTC` | timestamp | When the producer saw the row. | Visibility cutoff only. The normalizer neither parses nor stores it. |
| `SUPERSEDES_CLAIM_KEY` | text, optional | The `CLAIM_KEY` of an earlier feed claim of the same goal that this row revises or withdraws. | Links the revision chain. An unknown target, or one from another goal, is `SUPERSEDED_CLAIM_NOT_FOUND`. |

**Rules and pitfalls.**
- Claims are evidence for review, never relief. Relief (E) exists only after an excusal decision approves units. Pending claims only add a `CLAIMS_PENDING_REVIEW` note to the evaluation.
- The eight causes are fixed: LATE_PART, CROSS_TEAM_PREDECESSOR, SAME_TEAM_PREDECESSOR, CAPACITY_SHORTAGE, SKILL_SHORTAGE, DURATION_OVERRUN, REWORK_INJECTION and PLAN_CHURN. Five are nominally excusable: LATE_PART, CROSS_TEAM_PREDECESSOR, CAPACITY_SHORTAGE, SKILL_SHORTAGE and REWORK_INJECTION. A policy that changes these lists quarantines every claim (`POLICY_TAXONOMY_MISMATCH`).
- Never edit a row. Revise or withdraw with a new `CLAIM_KEY` that names the old one in `SUPERSEDES_CLAIM_KEY`.
- Rows are processed in the order the reader returns them, and the draft query sorts by `CLAIM_KEY`. A row whose target comes later in the same run is quarantined as `SUPERSEDED_CLAIM_NOT_FOUND` and admitted on the next collection.
- Units already verified complete cannot be claimed (`UNIT_ALREADY_EARNED`). Duplicate and overlapping claims are kept as separate records; relief is a union, so they never multiply it.

## `excusal_decisions`

**Purpose.** Reviewed decisions on submitted claims, including appeals that supersede earlier decisions. An approval names the missed units it relieves. The union of approved units still missed at the evaluation cutoff is the relief E in D = B − E, and this is the only path to relief.

| | |
| --- | --- |
| Kind | FEED. Proposed landing table `WEB_APP.FOCUS_FN_ExcusalDecisionSource` (008); draft query `queries/actuals/excusal_decisions.sql`, which the live reader does not run. Fixture file `observations/excusal_decisions.json`. |
| Grain and key | One row per decision or appeal. Key `DECISION_KEY`: the record id digests scope, the word "feed" and the key, with no revision. The 008 landing table has the same primary key. |
| Required for official evaluation | Yes (`FeedSpec.required`). |
| Store collection | `excusal_decisions` (type `ExcusalDecision`). It also holds decisions made with `POST /api/v1/excusal-decisions`. Proposed SQL history: `FOCUS_FN_ExcusalDecision`, extended by 008. |
| If missing or empty | Live today: NOT_PROBED (`NOT_INSTALLED`), so every evaluation gets `COVERAGE_EXCUSAL_DECISIONS_NOT_PROBED` and is UNKNOWN. Fixture: FAILED gives `COVERAGE_EXCUSAL_DECISIONS_FAILED`; EMPTY_UNCERTIFIED gives `COVERAGE_EXCUSAL_DECISIONS_EMPTY_UNCERTIFIED`; a watermark short of the credit deadline gives `COVERAGE_EXCUSAL_DECISIONS_BEHIND`. CERTIFIED_EMPTY is a valid empty feed (no feed decisions). |
| Time and cutoff | Cutoff `OBSERVED_AT_UTC`, used only for visibility. `DECISION_AT_UTC` orders decisions. A decision can review only claims already ingested by the run's as-of. |
| Quarantine codes | Common codes, plus `GOAL_NOT_FOUND`, `BAD_CLAIM_REF`, `BAD_WORK_REF`, `DUPLICATE_WORK_REF`, `UNIT_NOT_IN_GOAL`, `POLICY_TAXONOMY_MISMATCH`, `CLAIM_NOT_FOUND`, `CLAIMS_ACROSS_GOALS`, `CLAIM_NOT_SUBMITTED`, `PLAN_CHURN_SEPARATE_REVIEW`, `AUTHORITY_NOT_REVIEWER`, `SELF_REVIEW`, `BAD_DECISION`, `APPROVAL_WITHOUT_UNITS`, `APPROVED_UNIT_NOT_CLAIMED`, `UNIT_ALREADY_EARNED`, `UNITS_WITHOUT_APPROVAL`, `REASON_REQUIRED`, `SUPERSEDED_DECISION_NOT_FOUND`, `DECISION_ALREADY_SUPERSEDED` and `BAD_REVIEW_TRACK`. |
| Read by | `focus/performance/actuals.py` (normalization); `focus/performance/disruption.py` (`Intake.admit_decision`, `effective_decisions`, `approved_units`); `focus/performance/lifecycle.py` (`relief_units`); `focus/performance/score.py` (E); `focus/analytics/progression.py` (`excusal_decided` events); `focus/web/serializers.py` (`claims_view`, `goal_versions`, `turnover`); `focus/web/api.py` (`create_excusal_decision` version check). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `DECISION_KEY` | text, required | The source key of one decision. | The record id, and the target of `SUPERSEDES_DECISION_KEY`. |
| `TEAM` | text, required | The team of the goal under review. | With the shift, finds the goal. The reviewed claims must belong to it (`CLAIMS_ACROSS_GOALS`). |
| `SHIFT_DATE` | date, required | The production day of the goal's shift. | Part of the shift id. |
| `SHIFT_NUMBER` | integer 1, 2 or 3, required | The shift number. | Part of the shift id. |
| `CLAIM_KEYS` | JSON list of non-empty text | The claims reviewed: feed `CLAIM_KEY` values or FOCUS claim ids. | Each entry is tried as a FOCUS claim id first, then as a feed `CLAIM_KEY`. All must be effective SUBMITTED claims of one goal. |
| `APPROVED_WORK_REFS` | JSON list of work references; empty unless APPROVED | The missed units the approval relieves. | Resolved to frozen units, which must come from the reviewed claims and not be earned. APPROVED needs at least one; REJECTED and PENDING must have none. |
| `DECISION` | text: APPROVED, REJECTED or PENDING; required | The outcome. | Only effective APPROVED decisions create relief. REJECTED and PENDING are recorded without relief. |
| `AUTHORITY` | text, required | Who decided. | Must not be the `REPORTED_BY` of a reviewed claim (`SELF_REVIEW`). Shown in the review queue. |
| `AUTHORITY_ROLE` | text, required | The decider's role. | Must be listed in `disruption.reviewer_roles` (shipped: `excusal_decider`), else `AUTHORITY_NOT_REVIEWER`. |
| `DECISION_AT_UTC` | timestamp, required | When the decision was made. | Orders the effective decisions. Shown in the review queue. |
| `REASON` | text: at least 1 character, and at least 10 for REJECTED and exception approvals | The decider's reason. | Too short is `REASON_REQUIRED`; the longer minimum is `disruption.notes_min_chars`. Shown in the review queue. |
| `REVIEW_TRACK` | text: STANDARD or PLAN_CHURN; optional | The review track. | Null derives PLAN_CHURN for PLAN_CHURN claims and STANDARD otherwise. PLAN_CHURN claims must be decided on their own (`PLAN_CHURN_SEPARATE_REVIEW`). |
| `SUPERSEDES_DECISION_KEY` | text, optional | The `DECISION_KEY` of an earlier feed decision of the same goal that this appeal replaces. | The earlier decision stays in history but stops counting. A decision can be superseded only once (`DECISION_ALREADY_SUPERSEDED`). |
| `OBSERVED_AT_UTC` | timestamp | When the producer saw the row. | Visibility cutoff only. The normalizer neither parses nor stores it. |

**Rules and pitfalls.**
- Who may decide: an authority whose role is in `disruption.reviewer_roles` (shipped: `excusal_decider`), never on a claim they reported (`disruption.self_review_allowed` is false). On this feed the role is whatever the producer writes. The API takes it from server-owned grants, and mapping live reviewers to that grant is open decision OD-17.
- Review tracks: STANDARD for every cause except PLAN_CHURN. PLAN_CHURN is reviewed on its own track and never mixed with other causes in one decision.
- Approving a cause outside the five nominal categories is recorded as an exception and needs a substantive reason.
- Appeals append: send a new `DECISION_KEY` with `SUPERSEDES_DECISION_KEY`. The draft query sorts by `DECISION_KEY`, so an appeal whose target sorts later in the same run is admitted on the next collection.
- Relief E is the union of units approved by effective APPROVED decisions whose reviewed claims are still SUBMITTED, limited to units still missed at the cutoff. Earned units are never relief, and overlapping approvals never multiply it.
- Automatic claims are created after decisions in each run. A decision can therefore cite an automatic claim, by its FOCUS claim id, only from an earlier collection.

## Producer receipts

**What certifies an empty feed.** An empty feed proves nothing by itself. FOCUS counts a feed as covered only when the producer's receipt for it is complete. When the feed also has no visible rows, the receipt must either certify it empty or carry a covered-through watermark; then the status is CERTIFIED_EMPTY. Anything less is EMPTY_UNCERTIFIED, or FAILED when the receipt is incomplete. For official evaluation, every required feed's watermark must also reach the team-shift's credit deadline. FOCUS caps the watermark at the run's as-of, so a receipt can never cover the future.

**Where receipts come from.** In fixture mode, each `observations/<feed>.json` file carries a `receipt` object. The reader uses `complete`, `certified_empty` and `covered_through_utc`; the demo files also carry `producer` and `upstream_revision`, which it ignores. Live, the draft query `queries/actuals/producer_receipts.sql` would supply the same facts from the proposed table `WEB_APP.FOCUS_FN_ObservationProducerReceipt` (008). The live reader does not run it today: every feed is NOT_PROBED.

**The producer_receipts query.** It returns one row per feed (FEED is the 008 primary key), ordered by FEED. Unlike the feed queries, it takes no as-of parameter.

| Column | Type (008 table; fixture key) | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `FEED` | varchar(32), primary key; the CHECK allows only the six feeds that have landing tables | The feed the receipt covers. | Would match the receipt to its feed. Not read live today. |
| `COMPLETE` | bit, not null; fixture `complete`, read as true or false | The producer affirms the feed delivery is complete. | False makes the feed FAILED (`RECEIPT_INCOMPLETE`). True is needed for ROWS and CERTIFIED_EMPTY. |
| `CERTIFIED_EMPTY` | bit, not null; fixture `certified_empty` | The producer affirms the feed has no rows. | With a complete receipt and no visible rows, gives CERTIFIED_EMPTY. |
| `COVERED_THROUGH_UTC` | datetime2(6), nullable; fixture `covered_through_utc`, a timestamp | The instant through which the producer affirms the feed is complete: the watermark. | Capped at the run's as-of and must reach the shift's credit deadline; with no visible rows it also certifies emptiness. An unparsable value makes the feed FAILED (`RECEIPT_INVALID`). |
| `PRODUCER` | nvarchar(128), not null | Meaning not established in code or docs. | Nothing reads it. |
| `UPSTREAM_REVISION` | nvarchar(128), not null | Meaning not established in code or docs. | Nothing reads it. |
| `UPDATED_AT_UTC` | datetime2(6), not null | Meaning not established in code or docs. | Nothing reads it. |

**Feed statuses.**

| Status | When the reader sets it | Effect on official evaluation (required feeds) |
| --- | --- | --- |
| ROWS | The receipt is complete and at least one row is visible at the as-of. | Usable. Coverage still needs a watermark at or after the shift's credit deadline, else `COVERAGE_<FEED>_BEHIND`. |
| CERTIFIED_EMPTY | The receipt is complete, no row is visible, and the receipt certifies empty or carries a watermark. | Usable, with the same watermark rule. Certified empty with no watermark is `COVERAGE_<FEED>_BEHIND` ("covered through unknown"). |
| EMPTY_UNCERTIFIED | The receipt is complete, no row is visible, and there is neither certification nor watermark. | Not usable: `COVERAGE_<FEED>_EMPTY_UNCERTIFIED`, so the evaluation is UNKNOWN. |
| FAILED | No producer file (`FEED_MISSING`), a bad file or header (`FEED_INVALID`), columns or row keys that differ from `FEEDS` (`SCHEMA_DRIFT`), a bad watermark (`RECEIPT_INVALID`) or an incomplete receipt (`RECEIPT_INCOMPLETE`). | Not usable: `COVERAGE_<FEED>_FAILED: <reason>`, so the evaluation is UNKNOWN. With `RECEIPT_INCOMPLETE`, the visible rows are still normalized and stored. |
| NOT_PROBED | The live reader, for every feed today (`NOT_INSTALLED` or `QUERY_MISSING`). | Not usable: `COVERAGE_<FEED>_NOT_PROBED: <reason>`, so every live evaluation is UNKNOWN. |

With no observation run at all, the reason is `OBSERVATIONS_NOT_COLLECTED`. UNKNOWN means no score: missing evidence is never read as "zero excuses". The optional feeds (`defect_evidence`, `attendance_evidence`, `holding_evidence`) never produce a coverage code, whatever their status.

**Run receipts.** Each run stores, per feed, the status, the complete flag, rows seen, admitted, duplicates, quarantined, the covered-through instant, a content hash and the reason, plus the quarantine list. The proposed SQL tables are `FOCUS_FN_ObservationRun`, `FOCUS_FN_ObservationFeedReceipt` and `FOCUS_FN_ObservationQuarantine` (008). The `GET /api/v1/context` response shows each feed's status, usability and watermark. It labels the run STALE when it is older than `observations.max_feed_age_hours` (24 in the shipped policy); that label does not change evaluations.

**Gaps before live receipts can work** (from the SQL drafts and the proposed role grants):
- 008's receipt CHECK lists six feeds, so `attendance_evidence` and `holding_evidence` cannot have a receipt row. `docs/execution/SPEC_CONFORMANCE_REVIEW.md` (B9) says a migration after 009 is needed.
- FEED is the primary key, so each feed has one receipt row whose watermark must move forward. But the proposed `FOCUS_FN_ObservationProducer` role is granted INSERT only, not UPDATE.
- None of the proposed roles in `focus/migrations.py` (`ROLE_GRANTS`) has SELECT on the receipt table or on the landing tables.
