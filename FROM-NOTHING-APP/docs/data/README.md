# Data sources FOCUS needs

This folder describes every input FOCUS reads: what it is for, its grain and key, whether a run needs it, what happens
when it is missing or empty, its clocks, and what each column means and how FOCUS uses it.

- **44 planning datasets** (387 columns), captured together at each cutoff into one source run:
  - 28 **SOURCE** datasets, each a projection of a reference table with checked-in SQL;
  - 16 **SUPPLEMENTAL** datasets: typed inputs that table metadata alone cannot establish (rosters, leave, overtime
    authority, certificate census, part receipts and so on).
- **8 observation feeds** (96 columns): what happened after a plan was published, used for lifecycle tracking and team
  scores.

The tables come from the code: `focus/data/datasets.py` (`DATASETS`) and `focus/performance/actuals.py` (`FEEDS`).
`tests/unit/test_data_reference_docs.py` fails if a dataset, feed or column changes and these pages do not. Types for
SOURCE columns are the S04 reference contract types (`focus/data/discovery.py`).

This repository is public and holds synthetic data only. Object names such as `WEB_APP.FOCUS_MASTER_TASK_LIST` are the
checked-in reference contract, not a claim about any company database. Keep the real mapping (servers, objects, query
text, owners) in company storage, as `docs/integration/WORKPLACE_PORTING_GUIDE.md` and
`docs/integration/SOURCE_REPLACEMENT_CROSSWALK.md` describe.

## Pages

| Page | What it covers |
| --- | --- |
| [Work scope and the job graph](01-work-scope-and-graph.md) | The WIP census, scope, template and rework precedence, job durations and crews, and work already in progress. |
| [Production calendars and control-station windows](02-calendars-and-stations.md) | Working days per line, and the control-station dates that bound each job. |
| [Quality inspections and hold points](03-quality-and-inspections.md) | Required inspections, in-process hold locations and buyoff minutes. |
| [Parts shortages and receipts](04-parts-and-shortages.md) | Open shortages, expected dates and receipt events. |
| [People, teams and availability](05-people-teams-and-availability.md) | Assignments, rosters, attendance, leave, overtime authority and duty already booked. |
| [Certification requirements and holdings](06-certifications.md) | What each job requires and who holds which certificate, and when. |
| [Durations, clock intervals and labor](07-durations-and-labor.md) | Rework duration evidence, raw clock intervals and labor totals. |
| [Economics: contracts, prices and late-delivery liability](08-economics.md) | Aircraft mapping, scenario, penalty terms, original sale basis and booked liability. |
| [Observation feeds: what happened after publication](09-observation-feeds.md) | Lifecycle facts, actual intervals, goal amendments, claims and excusal decisions, and producer receipts. |

## Every input

"Required" means required for a source run for datasets, and required for official evaluation for feeds. "Read by
FOCUS" says whether anything beyond freshness checks and display uses the content.

### Work scope and the job graph

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`master_task_list`](01-work-scope-and-graph.md#master_task_list) | SOURCE | Yes | Reference SQL query | Yes | Census of every job on every line with open work, including COMPLETE anchors |
| [`scope_receipt`](01-work-scope-and-graph.md#scope_receipt) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | No (admission only) | Sets the site and program and excludes whole lines from scope |
| [`pnet_dag`](01-work-scope-and-graph.md#pnet_dag) | SOURCE | Yes | Reference SQL query | Yes | Template precedence edges between jobs, with type, hard flag, lag and model |
| [`neo_graph_rework`](01-work-scope-and-graph.md#neo_graph_rework) | SOURCE | Yes | Reference SQL query | Yes | Same-line links from each rework job to the job it reworks, with the child's status |
| [`rework_classification`](01-work-scope-and-graph.md#rework_classification) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | NC/PU/CI class, root job and defect-origin team of each rework job |
| [`flow_budget`](01-work-scope-and-graph.md#flow_budget) | SOURCE | Yes | Reference SQL query | Yes | Each job's elapsed minutes and the crew that must work it together |
| [`work_progress`](01-work-scope-and-graph.md#work_progress) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Performed and remaining minutes of jobs already in work |

### Production calendars and control-station windows

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`cday_op_plan`](02-calendars-and-stations.md#cday_op_plan) | SOURCE | Yes | Reference SQL query | Yes | Per-line working, weekend and non-working days, with hard date bounds |
| [`cs_windows`](02-calendars-and-stations.md#cs_windows) | SOURCE | Yes | Reference SQL query | Yes | Per line and control station: the earliest start date and the gate date |

### Quality inspections and hold points

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`buyoff_plan`](03-quality-and-inspections.md#buyoff_plan) | SOURCE | Yes | Reference SQL query | Yes | Which inspections and buyoffs each job requires |
| [`inspection_counts`](03-quality-and-inspections.md#inspection_counts) | SOURCE | No | Reference SQL query | No (cross-check notes only) | Second count of inspections, compared with the buyoff plan |
| [`inspection_standards`](03-quality-and-inspections.md#inspection_standards) | SOURCE | Yes | Reference SQL query | Yes | Minutes for each parallel buyoff kind |
| [`inspection_holds`](03-quality-and-inspections.md#inspection_holds) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Where each IP hold sits and its inspector minutes |

### Parts shortages and receipts

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`etracs_active`](04-parts-and-shortages.md#etracs_active) | SOURCE | Yes | Reference SQL query | Yes | Every open shortage, its used-on job and ECD |
| [`etracs_history`](04-parts-and-shortages.md#etracs_history) | SOURCE | No | Reference SQL query | No (freshness check only) | Closed shortages with their actual closure dates |
| [`part_events`](04-parts-and-shortages.md#part_events) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Receipt, deletion, reopen and ETA events per shortage |

### People, teams and availability

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`people_directory`](05-people-teams-and-availability.md#people_directory) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Display only | Display names and origin labels for people found in other workforce data |
| [`person_team_map`](05-people-teams-and-availability.md#person_team_map) | SOURCE | No | Reference SQL query | Yes | Current effective team assignment; needed for any executable crew member |
| [`team_inference`](05-people-teams-and-availability.md#team_inference) | SUPPLEMENTAL | No | Supplemental SQL path; producer table not installed | Display only | SQL-computed nominal home team; labels and review queue only |
| [`mech_team_inferred`](05-people-teams-and-availability.md#mech_team_inferred) | SOURCE | No | Reference SQL query | No | Legacy inferred team; comparison evidence for the SQL inference |
| [`dept_team_skill_ref`](05-people-teams-and-availability.md#dept_team_skill_ref) | SOURCE | No | Reference SQL query | No | Department-to-team map behind the SQL inference's department fallback |
| [`shift_roster`](05-people-teams-and-availability.md#shift_roster) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Who works which team and shift over which dates; the only way into a pool |
| [`attendance_shift`](05-people-teams-and-availability.md#attendance_shift) | SOURCE | No | Reference SQL query | Yes | Presence or absence per person and shift on the production day |
| [`availability_exceptions`](05-people-teams-and-availability.md#availability_exceptions) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Leave and other duty that remove or reduce availability |
| [`ot_authorizations`](05-people-teams-and-availability.md#ot_authorizations) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Authorized OT minutes per team, shift and date; none means 0 |
| [`carry_in`](05-people-teams-and-availability.md#carry_in) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Duty already occupying people in the horizon; locked, never double-booked |

### Certification requirements and holdings

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`soi_cert_requirements`](06-certifications.md#soi_cert_requirements) | SOURCE | Yes | Reference SQL query | Yes | Positive certificate requirements per job; each code is an AND group |
| [`cert_course_map`](06-certifications.md#cert_course_map) | SOURCE | No | Reference SQL query | No | Process, certificate and course metadata; only its clock is checked |
| [`requirement_census`](06-certifications.md#requirement_census) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Affirmative receipt that a job needs no certificate |
| [`requirement_alternatives`](06-certifications.md#requirement_alternatives) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Explicitly approved OR groups of certificates |
| [`mechanic_certs`](06-certifications.md#mechanic_certs) | SOURCE | Yes | Reference SQL query | Yes | Certificates each person holds, with status and validity dates |
| [`holding_events`](06-certifications.md#holding_events) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Revocation and reinstatement events with effective times |
| [`cert_eligibility_matrix`](06-certifications.md#cert_eligibility_matrix) | SOURCE | No | Reference SQL query | Display only | Convenience matrix compared with FOCUS's own decision; grants nothing |

### Durations, clock intervals and labor

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`clock_observations`](07-durations-and-labor.md#clock_observations) | SUPPLEMENTAL | No | Supplemental SQL path; producer table not installed | No (`duration-stats` report only) | Raw clock intervals per person and episode, with revisions and voids |
| [`duration_episodes`](07-durations-and-labor.md#duration_episodes) | SUPPLEMENTAL | No | None yet: reports NOT_PROBED | Yes | Completed comparable rework episodes behind the P75 |
| [`actual_labor`](07-durations-and-labor.md#actual_labor) | SOURCE | No | Reference SQL query | No (freshness check only) | Aggregate labor minutes charged per job |
| [`labor_shift`](07-durations-and-labor.md#labor_shift) | SOURCE | No | Reference SQL query | No (freshness check only) | Labor minutes per person, job and shift |

### Economics: contracts, prices and late-delivery liability

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`econ_mapping_revision`](08-economics.md#econ_mapping_revision) | SOURCE | No | Reference SQL query | Yes | Picks the one current revision of the aircraft mapping |
| [`econ_aircraft_mapping`](08-economics.md#econ_aircraft_mapping) | SOURCE | No | Reference SQL query | Yes | Gives each aircraft line its customer code and minor model |
| [`econ_scenario`](08-economics.md#econ_scenario) | SOURCE | No | Reference SQL query | Yes | Picks the one active scenario whose assignments and terms apply |
| [`econ_contract_assignment`](08-economics.md#econ_contract_assignment) | SOURCE | No | Reference SQL query | Yes | Assigns an aircraft line to at most one contract |
| [`econ_term`](08-economics.md#econ_term) | SOURCE | No | Reference SQL query | Yes | Daily late-delivery penalty rates and the aircraft they target |
| [`econ_original_sale_basis`](08-economics.md#econ_original_sale_basis) | SOURCE | No | Reference SQL query | Yes | Ties the chosen term to the aircraft's original sale price; needed for ACTUAL liability |
| [`econ_liability_checkpoint`](08-economics.md#econ_liability_checkpoint) | SOURCE | No | Reference SQL query | Yes | Liability already booked, used as a floor under the modeled penalty |

### Observation feeds: what happened after publication

| Input | Kind | Required | Live reader today | Read by FOCUS | Purpose |
| --- | --- | --- | --- | --- | --- |
| [`lifecycle_events`](09-observation-feeds.md#lifecycle_events) | FEED | Yes | None: NOT_PROBED (NOT_INSTALLED) | Yes | What happened to each job and phase: starts, verified completions, cancellations, reopens, voids and corrections |
| [`actual_intervals`](09-observation-feeds.md#actual_intervals) | FEED | Yes | None: NOT_PROBED (NOT_INSTALLED) | Yes | Raw clock intervals per person and phase: evidence of actual starts and effort burn, never credit |
| [`defect_evidence`](09-observation-feeds.md#defect_evidence) | FEED | No | None: NOT_PROBED (NOT_INSTALLED) | Yes | Which team caused the defect behind a rework job: explicit rework origin evidence |
| [`attendance_evidence`](09-observation-feeds.md#attendance_evidence) | FEED | No | None: NOT_PROBED (QUERY_MISSING) | Yes | Call-offs observed after planning: evidence for automatic CAPACITY_SHORTAGE claims |
| [`holding_evidence`](09-observation-feeds.md#holding_evidence) | FEED | No | None: NOT_PROBED (QUERY_MISSING) | Yes | Certificate revocations and reinstatements observed after planning: evidence for automatic SKILL_SHORTAGE claims |
| [`goal_amendments`](09-observation-feeds.md#goal_amendments) | FEED | Yes | None: NOT_PROBED (NOT_INSTALLED) | Yes | Approved feasible emergency additions to a frozen goal (B = B0 + additions) |
| [`disruption_claims`](09-observation-feeds.md#disruption_claims) | FEED | Yes | None: NOT_PROBED (NOT_INSTALLED) | Yes | Manual claims that missed frozen units had a cause: evidence for review, never relief |
| [`excusal_decisions`](09-observation-feeds.md#excusal_decisions) | FEED | Yes | None: NOT_PROBED (NOT_INSTALLED) | Yes | Reviewed excusal decisions and appeals: the only source of relief (E) |

## How one shift uses the data

1. **Source run.** At the cutoff (in live mode, when the pipeline job runs), FOCUS captures every dataset with its
   explicit column list, hashes it, reads it back and admits one immutable snapshot. The scope's source head moves only
   after the whole run completes (`focus/data/acquisition.py`).
2. **Admission.** The snapshot builder turns rows into facts: jobs and phases, the job graph, crews and pools, holdings,
   shortages, calendars and station windows. Every problem it finds becomes a readiness gap with a code
   (`focus/data/snapshot_builder.py`).
3. **Plans.** FOCUS builds two plans from the snapshot. The FORECAST plan covers all work in scope and runs even with
   gaps. The EXECUTABLE plan is what crews are dispatched on, so it only places work whose facts are proven.
4. **Validation and publication.** An independent validator recomputes every plan before it can be accepted.
5. **After the shift.** The observation feeds record what actually happened. Teams are scored by the canonical
   formula, and missed work goes through the excusal review.

## Capture statuses

Each dataset in a run ends with one status:

| Status | Meaning |
| --- | --- |
| `ROWS` | The dataset returned rows. |
| `CERTIFIED_EMPTY` | No rows, and the producer's receipt affirms that empty is complete and correct. |
| `EMPTY_UNCERTIFIED` | No rows and no such receipt. FOCUS never treats this as "nothing to do". |
| `FAILED` | The capture raised an error: a database or connection error (`SOURCE_QUERY_FAILED`), schema drift or an incomplete receipt. |
| `NOT_PROBED` | No producer is installed for this input, so FOCUS did not read it. |

What a status does to the run:

- **Required datasets.** The 11 datasets marked required are `master_task_list`, `pnet_dag`, `neo_graph_rework`,
  `flow_budget`, `buyoff_plan`, `inspection_standards`, `cday_op_plan`, `cs_windows`, `etracs_active`,
  `soi_cert_requirements` and `mechanic_certs`. If one fails to capture, the whole run is rejected
  (`MANDATORY_DATASET_FAILED`). If one is empty without a certified-empty receipt, the run is rejected too:
  `CENSUS_UNAVAILABLE` for `master_task_list`, `MANDATORY_DATASET_EMPTY` for the others.
- **Unreadable values.** A value admission cannot read (a `_UTC` text without an offset in a fixture file, a null where
  a number or date is needed, text where a number is needed) rejects the run (`SOURCE_VALUE_INVALID`). A data problem
  ends the run REJECTED with a code; it no longer leaves the run CAPTURING.
- **Optional datasets** with a status problem get a readiness gap named `DATASET_NOT_PROBED`,
  `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`, and the run continues. The gap is labelled as blocking executable work
  and shown in the data-gap queue. What it actually withholds depends on the dataset: each page's "If missing or empty"
  row says what the code does.
- **Live today.** The live reader runs the 28 reference SQL queries and the two supplemental SQL paths
  (`team_inference`, `clock_observations`). The other 14 supplemental inputs report `NOT_PROBED`. A live query that
  returns no rows is `EMPTY_UNCERTIFIED`, because the live reader has no way yet to certify an empty result. A
  legitimately empty source (for example, no open shortages) therefore needs a certification path: until then an empty
  required dataset rejects the run, and an empty optional one stays a gap. The live observation reader reports every feed `NOT_PROBED` until its `FOCUS_FN` producer is
  installed.

## Clocks and time zones

- **The database holds UTC; the app shows local time** (owner decision, 2026-10-31). Live runs set
  `FOCUS_SOURCE_TIMEZONE_MAP_JSON` to `{"*": "UTC"}` (`manifest.yml` and the live CI jobs). The live reader writes a
  naive value from a column named `..._UTC` or `...Utc` with its UTC offset, and economics reads those columns as UTC
  in fixture mode too. Every page shows times in the site's zone (policy `shift.timezone`), labelled, for example
  `06:00 EDT`.
- **SOURCE timestamps carry no zone.** SQL `datetime2` values are naive. Each dataset's zone comes from an explicit
  policy: `FOCUS_SOURCE_TIMEZONE_MAP_JSON` maps dataset names to IANA zones, with `"*"` as an explicit default; name a
  dataset only if its table holds local time. Fixture producers declare their own zone. FOCUS never guesses a zone. Without a policy for `master_task_list`, `cday_op_plan` or `cs_windows`, the
  snapshot carries `SOURCE_TIMEZONE_POLICY_MISSING`, labelled as blocking all plans; what it actually withholds depends
  on the dataset, as its page says (`focus/data/sourcetime.py`).
- **Daylight saving.** A local time inside a spring-forward gap moves forward to the transition. An ambiguous local time
  uses its first occurrence. Both rules are recorded with each conversion.
- **Columns ending in `_UTC`** are UTC instants. Columns ending in `_LOCAL` are local wall times in the site's zone.
- **Effective, recorded and observed time differ.** Effective time is when something happened. Recorded time is when
  the source wrote it. Observed time is when FOCUS could first have known it.
- **As-of rule.** Datasets and feeds with a cutoff column (the registry's `cutoff_column`) must not show rows observed
  after the cutoff. The fixture readers enforce this; a live query or producer must apply the same rule. That way a late
  correction never rewrites what a past plan knew.

## Freshness and capture skew

At admission FOCUS checks how old each dataset is (`focus/data/freshness.py`):

- **Age.** A dataset is as fresh as its producer receipt's `loaded_at`, or else its newest row clock (`LOAD_TIMESTAMP`,
  `SNAPSHOT_TS` or `LOAD_TS`, read in the dataset's declared zone). Policy `admission.max_source_age_hours` sets each
  dataset's limit:

  | Limit | Datasets |
  | --- | --- |
  | 18 h | `attendance_shift` |
  | 36 h | `master_task_list`, `pnet_dag`, `flow_budget`, `buyoff_plan`, `inspection_counts`, `etracs_active`, `etracs_history`, `actual_labor`, `labor_shift`, `person_team_map` |
  | 48 h | `neo_graph_rework`, `soi_cert_requirements`, `mechanic_certs` |
  | 168 h | `cert_course_map`, `dept_team_skill_ref` |
  | 240 h | `cday_op_plan`, `cs_windows` |

  These are fixture defaults; the live load cadences are an open decision.
- **Findings.** Older than the limit is `SOURCE_STALE`. No readable clock is `SOURCE_CLOCK_UNKNOWN`, because unknown is
  never assumed fresh.
- **Capture skew.** All datasets of a run must be captured within `admission.max_capture_skew_minutes` (30) of each
  other. Beyond that the run is `CAPTURE_SKEW`, because its tables may straddle a source load.
- **Effect.** The validator rejects an EXECUTABLE plan on a snapshot with any of these three findings (`E_LINEAGE`).
  FORECAST plans still run.

## Identity

- **Jobs** are keyed by line number and job identifier: `LINE_NUMBER` with `SHOP_ORDER_IDENTIFIER`, `SOI`, `SOI_ID` or
  `CHILD_SOI`, depending on the table. Feeds resolve to the same job, phase and person identities as admission.
- **People** are `BEMS_ID` in SOURCE tables and `PERSON_KEY` in supplemental inputs and feeds. In live mode both are in
  the `BEMS` namespace; fixture people use a separate `fixture-demo` namespace, so invented people never share an
  identity with real ones (`focus.data.workforce.person_namespace`).
- **Scope.** A run belongs to one scope, `FOCUS_SCOPE_KEY`. Supplemental reads are bound to that scope's source head.

## Column checks at capture

- Every capture must return exactly the projection's columns in order. Anything else is `SCHEMA_DRIFT`.
- There is no `SELECT *` and no row cap: the WIP census includes completed anchors on every line with open work.
- Values keep their raw types until admission parses them. A value that cannot be parsed is a row error or a
  quarantined row with a stable code. It never becomes a default.

## Owner decisions that gate live plans

Connecting the data is not enough for executable plans. In live mode, while these policy values still carry the
authority `open_decision`, every job is kept out of executable plans:

- **OD-05**, `cs.date_mode`: how control-station offsets and dates are based (`CS_DATE_SEMANTICS_UNCONFIRMED`).

OD-01 is decided (owner, 2026-10-31): `flow_budget.TOTAL_MECHANICS` is the number of mechanics that must be on the job
at once to work it. The policy records it with the authority `owner_decision`, so live jobs no longer carry
`FLOW_SEMANTICS_UNCONFIRMED`.

The other open decisions, OD-02 to OD-17 apart from OD-05, each withhold one live capability: raw clock intervals, QA hold locations,
OT authority, the commercial due date, the no-certificate census, the assignment and attendance feeds, the observation
producers and so on. `docs/execution/DECISIONS.md` lists them, and the policy file records each one's live effect.

## Known gaps before going live

Writing this reference turned up behaviour that matters for a live connection. Each item was checked in the code.

**Fixed on 2026-10-31:**
- a database or connection error marks its dataset `FAILED`, or rejects the run for a required one;
- an unreadable value, and an empty required dataset, reject the run with a code instead of leaving it CAPTURING;
- an in-progress rework job without `work_progress` is blocked (`REMAINING_UNKNOWN`), never planned again at full
  length;
- economics `...Utc` times are read as UTC, and liability checkpoints respect the cutoff and never break a tie by row
  order;
- the empty-attendance guard looks at the production day.

**Still open:**
- **`DATASET_*` gaps are labels, not blocks.** For optional datasets they carry the effect `BLOCKS_EXECUTABLE`, but the
  validator only enforces `SOURCE_STALE`, `SOURCE_CLOCK_UNKNOWN` and `CAPTURE_SKEW` (`focus/validation/plan.py`). What
  a missing dataset withholds comes from how its facts resolve, as each page describes.
- **Empty live results cannot be certified.** The live reader marks every zero-row capture `EMPTY_UNCERTIFIED`, so an
  empty required dataset rejects the run even when empty is correct.
- **14 supplemental inputs and all 8 feeds have no live producer.** They report `NOT_PROBED`. Without `work_progress`,
  every live job in work is blocked. `attendance_evidence` and `holding_evidence` also have no draft query or landing
  table in `sql/migrations/008_lifecycle.sql`.
- **Three feeds skip the as-of rule for a missing observed time.** In `goal_amendments`, `disruption_claims` and
  `excusal_decisions`, a row with a missing or unreadable `OBSERVED_AT_UTC` is admitted at every as-of.
- **The observation producer role cannot advance its receipt.** The drafted grants give `FOCUS_FN_ObservationProducer`
  INSERT only on `ObservationProducerReceipt` (`focus/migrations.py`). Check the receipt table's design before
  installing.

## Not covered here

- **Policy values** (`config/policies/rebuild-v1.json`) are owner-approved parameters, not source data. See
  `docs/integration/SCHEDULING_CONSTRAINTS.md`, section 3.
- **Tables FOCUS writes** (`FOCUS_FN_*`, `sql/migrations/`). See `docs/execution/SQL_SCHEMA_LEDGER.md`.
- **Actions entered in the app** (dispatch, mechanic reports). They are recorded as events, and a mechanic's report is
  an unverified claim until verified.

## Related documents

- `docs/integration/SOURCE_REPLACEMENT_CROSSWALK.md`: the mapping checklist for each dataset and the SQL query
  families.
- `docs/execution/SQL_SCHEMA_LEDGER.md`: the S04 contracts, reader projections and drafted FOCUS tables.
- `docs/execution/SOURCE_READINESS.md`: census coverage and clock semantics.
- `scripts/export_integration_contract.py`: a machine-readable manifest with columns, types and query hashes.
