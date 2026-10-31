# Certification requirements and holdings

These datasets decide whether a named person may do a job. Requirements are set per exact job, keyed by line number and SOI (shop-order identifier: the job's native ID, the same value as `SHOP_ORDER_IDENTIFIER` in `master_task_list`). Positive rows in `soi_cert_requirements` form AND groups, `requirement_alternatives` adds explicitly approved OR groups, and an affirmative `requirement_census` row is the only way to say a job needs no certificate. Holdings come from `mechanic_certs`, with revocations and reinstatements from `holding_events`. One rule authority, `focus/eligibility.py`, then requires the same person to hold at least one certificate of every AND group for the whole proposed interval; certificates are never pooled across people. `cert_course_map` and `cert_eligibility_matrix` are reference and display data and grant nothing.

**Terms used below.**
- **Requirement status.** Each job is `required` (it has certificate groups), `affirmatively_none` (a census receipt says no certificate is needed) or `unknown`. An `unknown` job is BLOCKED with the reason `REQUIREMENTS_UNKNOWN`, and the validator rejects any assignment to it (V5).
- **Holding.** One person-certificate record, with a status and a validity window in UTC.
- **BEMS_ID.** The native person key (the badge/BEMS ID in live data, an invented `DEMO-` key in the fixture). FOCUS matches it as exact text against the people built from the workforce datasets (see `05-people-teams-and-availability.md`).
- **Critical process.** A process name carried by the NEO requirement and course-map tables. The repo does not define the term further, and no code reads it.

**Gaps that apply to every dataset here.**
- A dataset captured as `NOT_PROBED`, `EMPTY_UNCERTIFIED` or `FAILED` adds the gap `DATASET_NOT_PROBED`, `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`, with effect `BLOCKS_EXECUTABLE`. The live reader never certifies an empty result. All supplemental datasets here report `NOT_PROBED` live today.
- Freshness applies to `soi_cert_requirements`, `mechanic_certs` (limit 48 h each) and `cert_course_map` (168 h): the newest `SNAPSHOT_TS`, read in the dataset's declared time zone, must be within the limit at the cutoff (policy `admission.max_source_age_hours`, fixture defaults). Otherwise `SOURCE_STALE`; a clock that cannot be read, including an all-null `SNAPSHOT_TS`, is `SOURCE_CLOCK_UNKNOWN`. Both fail every EXECUTABLE plan in the validator's lineage check.
- `BLOCKS_EXECUTABLE` is otherwise a label; the other gaps act through the data. In live mode the policy's open decision `OD-09` (affirmative no-cert census) is always added as a gap.
- The reference SOURCE queries take no as-of parameter. The company adapter must enforce the run cutoff itself (`docs/integration/SOURCE_REPLACEMENT_CROSSWALK.md`, section C).

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`soi_cert_requirements`](#soi_cert_requirements) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Positive certificate requirements per job; each code is an AND group |
| [`cert_course_map`](#cert_course_map) | SOURCE | No | forecast | Reference SQL query | No | Process, certificate and course metadata; only its clock is checked |
| [`requirement_census`](#requirement_census) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Affirmative receipt that a job needs no certificate |
| [`requirement_alternatives`](#requirement_alternatives) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Explicitly approved OR groups of certificates |
| [`mechanic_certs`](#mechanic_certs) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Certificates each person holds, with status and validity dates |
| [`holding_events`](#holding_events) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Revocation and reinstatement events with effective times |
| [`cert_eligibility_matrix`](#cert_eligibility_matrix) | SOURCE | No | projection | Reference SQL query | Display only | Convenience matrix compared with FOCUS's own decision; grants nothing |

## `soi_cert_requirements`

**Purpose.** Positive certificate requirements for each job, keyed by exact line and SOI. Each distinct certificate code becomes its own AND group unless an approved OR group covers it, and every crew member must hold every group. Rows are positive-only: a job with no row is `unknown`, never cert-free, and is blocked.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.NEO_SOI_CERT_REQUIREMENTS`, query `queries/source_inputs/soi_cert_requirements.sql`. |
| Grain and key | One row per job and certificate, possibly repeated; no S04 primary key. The code groups rows by (`LINE_NUMBER`, `SOI_ID`) and drops repeated codes per job. |
| Required | Yes: a capture failure FOCUS recognizes (for example `SCHEMA_DRIFT`) rejects the whole run with `MANDATORY_DATASET_FAILED`. An empty result without a certificate does not reject the run; it adds `DATASET_EMPTY_UNCERTIFIED`. |
| Capabilities | forecast, executable |
| If missing or empty | Every job without an affirmative census row is BLOCKED with `REQUIREMENTS_UNKNOWN`, and the validator flags any assignment to such a job (`REQUIREMENTS_UNKNOWN`, V5). |
| Time and cutoff | `SNAPSHOT_TS` is the freshness clock (limit 48 h), read in this dataset's time zone. No cutoff column. |
| Read by | `focus/data/qualifications.py` (`build_requirements`); `focus/data/snapshot_builder.py` (blocker); `focus/eligibility.py`; `focus/data/freshness.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOI_ID` | varchar(30), not null | SOI of the job (fixture: `A2`). | With `LINE_NUMBER`, must match a census job (`LINE_NUMBER`, `SHOP_ORDER_IDENTIFIER` in `master_task_list`) exactly. Rows that match no job are ignored. |
| `LINE_NUMBER` | int, not null | Aircraft line number (fixture: `1301`). | Part of the exact job key. |
| `CRITICAL_PROCESS_NAME` | varchar(200), nullable | Name of the critical process the requirement belongs to (fixture: `DEMO PROCESS A`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CERT_CODE` | varchar(100), not null | Required certificate code (fixture: `A`). | Each distinct code is one AND group, unless it sits in an approved OR group for the job. |
| `CERT_TYPE` | varchar(20), nullable | Meaning not established in code or docs (fixture: `CERT`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `REQ_CERT` | char(1), nullable | Flag that the certificate is required (fixture: `Y`). | `Y` or null keeps the row; any other value drops it. The readiness query uses the same rule. |
| `SNAPSHOT_TS` | datetime, nullable | Time of the source snapshot. | Freshness clock: the newest value must be within 48 h of the cutoff, or `SOURCE_STALE`. |

**Rules and pitfalls.**
- Positive-only. A job with no rows is `unknown` (blocked), never cert-free; only `requirement_census` can say "no certificate" (`OD-09`; crosswalk section A).
- AND by default; OR groups come only from `requirement_alternatives`. FOCUS never treats codes of one critical process as alternatives (policy note `qualifications.group_by_critical_process: false`, "approved OR alternatives must be explicit").
- Every crew member must hold every AND group alone. A requirement {A, B} is not met by one A-only and one B-only person (spec S07).
- Job keys must match the census text exactly. Native keys are never trimmed or case-folded (`docs/execution/DECISIONS.md`, D-07).
- Rework jobs (classes NC, PU, CI) never clear requirements (`focus/data/qualifications.py`).
- `queries/qualifications/requirement_census.sql` is a read-only readiness query that the app does not run. It lists every non-COMPLETE job and whether it has positive rows, which shows the jobs that will be `unknown`.

## `cert_course_map`

**Purpose.** Reference metadata that links a critical process and a certificate code to course and spec fields. The crosswalk calls it cert/course/spec metadata and says approved substitutions are controlled separately. No planner code reads its content; only its snapshot time is checked for freshness.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.NEO_CERT_COURSE_MAP`, query `queries/source_inputs/cert_course_map.sql`. |
| Grain and key | One row per critical process and certificate; S04 primary key (`CRITICAL_PROCESS_NAME`, `CERT_CODE`). |
| Required | No |
| Capabilities | forecast |
| If missing or empty | No planning effect. Dataset status gap `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. With rows, a stale or unreadable `SNAPSHOT_TS` raises `SOURCE_STALE` or `SOURCE_CLOCK_UNKNOWN`, and those fail every EXECUTABLE plan. |
| Time and cutoff | `SNAPSHOT_TS` is the freshness clock (limit 168 h), read in this dataset's time zone. No cutoff column. |
| Read by | `focus/data/freshness.py` (`SNAPSHOT_TS` only). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `CRITICAL_PROCESS_NAME` | varchar(200), not null | Name of the critical process (fixture: `DEMO PROCESS A`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CERT_CODE` | varchar(100), not null | Certificate code (fixture: `A`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CERT_TYPE` | varchar(20), nullable | Meaning not established in code or docs (fixture: `CERT`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `BAC_SPEC` | varchar(50), nullable | Meaning not established in code or docs (fixture: `DEMO-SPEC`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `BAC_VERB` | varchar(150), nullable | Meaning not established in code or docs (fixture: `PERFORM`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SNAPSHOT_TS` | datetime, nullable | Time of the source snapshot. | Freshness clock: the newest value must be within 168 h of the cutoff, or `SOURCE_STALE`. |

**Rules and pitfalls.**
- It creates no requirement and no alternative. OR groups come only from `requirement_alternatives` (crosswalk section A: "approved substitutions separately controlled").
- Keep it fresh even though the planner ignores its content. A stale or all-null `SNAPSHOT_TS` blocks every EXECUTABLE plan.

## `requirement_census`

**Purpose.** The affirmative requirement census: an explicit, source-backed receipt that a job needs no certificate. Without it, a job with no positive requirement rows stays `unknown` and blocked. It is the only way to clear such a job (`OD-09`).

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/requirement_census.json`. |
| Grain and key | One row per job; no declared key. Rows are matched to jobs by (`LINE_NUMBER`, `SOI_ID`). |
| Required | No |
| Capabilities | executable |
| If missing or empty | Jobs with no positive rows stay BLOCKED with `REQUIREMENTS_UNKNOWN`. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`; live mode also raises `OD-09`. |
| Time and cutoff | No time columns, no cutoff column, no freshness check. |
| Read by | `focus/data/qualifications.py` (`build_requirements`); the decision is applied by `focus/eligibility.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOI_ID` | text, matched exactly | SOI of the job (fixture: `PSC6`). | With `LINE_NUMBER`, must match a census job exactly. |
| `LINE_NUMBER` | integer | Aircraft line number (fixture: `1306`). | Part of the exact job key. |
| `STATUS` | text | Census result for the job; `AFFIRMATIVELY_NONE` means no certificate is required. | Only the exact value `AFFIRMATIVELY_NONE` has an effect: the job becomes `affirmatively_none` if it has no positive rows and no OR groups. Other values are ignored. |
| `APPLICABILITY` | text (not parsed) | Meaning not established in code or docs (fixture: `JOB`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `EVIDENCE_REF` | text (not parsed) | Reference to the receipt's source evidence (fixture: `FIXTURE-CENSUS-PSC6`). | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Send only affirmative, complete receipts per job. A missing row never means cert-free (crosswalk section B).
- Positive rows win. A job with any positive row or OR group stays `required` whatever the census says.
- An `affirmatively_none` job still needs its full sourced crew size; only the certificate check is waived (decision code `AFFIRMATIVELY_NONE`).
- Keys and the status value are matched exactly, including letter case.

## `requirement_alternatives`

**Purpose.** Explicitly approved OR alternatives. Rows with the same `GROUP_ID` for one job form one group, and holding any certificate in the group satisfies it. Without this dataset every requirement is AND.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/requirement_alternatives.json`. |
| Grain and key | One row per job, group and certificate; no declared key. A code repeated within a group is dropped. |
| Required | No |
| Capabilities | executable |
| If missing or empty | Every positive code stays its own AND group, with no substitution. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. |
| Time and cutoff | No time columns, no cutoff column, no freshness check. |
| Read by | `focus/data/qualifications.py` (`build_requirements`); groups are checked by `focus/eligibility.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOI_ID` | text, matched exactly | SOI of the job (fixture: `ROOT5`). | With `LINE_NUMBER`, must match a census job exactly. |
| `LINE_NUMBER` | integer | Aircraft line number (fixture: `1305`). | Part of the exact job key. |
| `GROUP_ID` | text | Identifier of one approved OR group (fixture: `G1`). | Rows with the same value for one job form one OR group; groups are ordered by this value. |
| `CERT_CODE` | text | One acceptable certificate in the group (fixture: `B`, `C`). | Added to the group. A positive code that appears in any group for the job is no longer required on its own. |
| `APPROVAL_REF` | text (not parsed) | Reference to the approval of the alternative (fixture: `FIXTURE-ALT-ROOT5`). | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Send only owner-approved alternatives (crosswalk section B). Never derive them from process names, the course map or the matrix `SUBSTITUTION` label.
- Put the code being replaced inside the group. A group that leaves it out adds a new AND group instead of offering an alternative.
- A group for a job with no positive rows still makes the job `required`.
- Each person must hold one certificate from each group alone; holdings are never combined across people.

## `mechanic_certs`

**Purpose.** The certificates each person holds, with status and validity dates. FOCUS turns each row into a holding with a validity window in UTC. It uses the holdings to decide, for every proposed interval, whether a person holds the full requirement bundle (QA inspectors need the policy's inspector certificate, `QA` in the fixture). The holdings also appear on each person's certificate list.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_MECHANIC_CERTS`, query `queries/source_inputs/mechanic_certs.sql`. |
| Grain and key | One row per person and certificate is expected; there is no S04 primary key. The code keeps every row, and any one row that covers the interval qualifies. |
| Required | Yes: a capture failure FOCUS recognizes (for example `SCHEMA_DRIFT`) rejects the whole run with `MANDATORY_DATASET_FAILED`. An empty result without a certificate does not reject the run; it adds `DATASET_EMPTY_UNCERTIFIED`. |
| Capabilities | forecast, executable |
| If missing or empty | No real person holds any certificate, so a job with certificate groups gets no qualified crew (`CREW_UNQUALIFIED` in the engine, `NOT_QUALIFIED` in the validator). Problem rows are reported as acquisition notes, not snapshot gaps: `HOLDING_PERSON_UNKNOWN`, `HOLDING_CERT_BLANK`, `HOLDING_START_` or `HOLDING_EXPIRY_` plus `BLANK`, `MALFORMED`, `OUT_OF_RANGE` or `TIMEZONE_MISSING`, `HOLDING_RANGE_INVALID`, `HOLDING_EXPIRY_UNREPRESENTABLE` and `HOLDING_SOURCE_TIMEZONE_MISSING`. |
| Time and cutoff | Dates become local midnight in this dataset's time zone, then UTC; with no time zone policy every validity is `UNKNOWN`. `SNAPSHOT_TS` is the freshness clock (limit 48 h). No cutoff column. |
| Read by | `focus/data/qualifications.py` (`build_holdings`); `focus/eligibility.py`; `focus/engine/crew_selection.py`; `focus/validation/plan.py` (V5); `focus/publication/dispatch.py`; `focus/web/serializers.py` (`person_certificates`); `focus/data/freshness.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `BEMS_ID` | varchar(10), not null | Native person key of the holder. | Must be a known person; otherwise the row is dropped (note `HOLDING_PERSON_UNKNOWN:<id>`). |
| `CERT_CODE` | varchar(20), nullable | Certificate code (fixture: `A`). | Compared as exact text with requirement codes (which allow 100 characters). Null or blank drops the row (`HOLDING_CERT_BLANK`). |
| `STATUS` | varchar(10), nullable | Source status of the holding. | Exact match against the fixture-default policy lists: `ACTIVE`, `CURRENT` and `VALID` are active; `EXPIRED`, `REVOKED`, `SUSPENDED` and `INACTIVE` are inactive. Anything else, including other letter case or null, is `UNKNOWN`; only active holdings qualify. |
| `LAST_CERT_DATE` | varchar(10), nullable | Last certification date; FOCUS treats it as the start of validity (fixture: `2026/01/01`). | Parsed strictly as `YYYY/MM/DD`, `YYYY-MM-DD` or `YYYYMMDD`; valid from local midnight of that date. Blank, malformed or impossible values make the validity `UNKNOWN`. |
| `CERT_EXP_DATE` | varchar(10), nullable | Expiry date, inclusive (fixture: `2027/12/31`). | Same strict forms; valid until the next local midnight, exclusive. `9999/12/31` in any accepted form means perpetual (policy `qualifications.perpetual_sentinel`, a fixture default); blank or malformed is `UNKNOWN`, never unlimited. |
| `SNAPSHOT_TS` | datetime, nullable | Time of the source snapshot. | Freshness clock: the newest value must be within 48 h of the cutoff, or `SOURCE_STALE`. |

**Rules and pitfalls.**
- Validity must cover the whole proposed interval: active status, known validity, valid from at or before the start, valid until at or after the end, and no revocation (`focus/eligibility.py`). The per-certificate reasons are `NOT_HELD`, `STATUS_<status>`, `VALIDITY_UNKNOWN`, `NOT_YET_VALID`, `EXPIRES_DURING_INTERVAL`, `EXPIRED` and `REVOKED`.
- Dates are strict text: one of the three forms, no surrounding spaces. Profile the live encodings and statuses first with `queries/qualifications/holding_validity_profile.sql` (read-only; the app does not run it).
- Send one current row per person and certificate. Any row that covers the interval qualifies, so a superseded active row can mask a newer expired one.
- The holder must also appear in `shift_roster`, `person_team_map`, `team_inference` or `attendance_shift`; otherwise the holding is dropped.
- Declare this dataset's time zone (fixture receipt or `FOCUS_SOURCE_TIMEZONE_MAP_JSON`). Without it every holding is `UNKNOWN`.
- Revocation dates live in `holding_events`; this table has only the status column.

## `holding_events`

**Purpose.** Revocation and reinstatement events with effective times. A revocation holds from its effective instant until a later reinstatement, which closes the window but never erases it. FOCUS applies these events on top of `mechanic_certs`, so a certificate revoked during a proposed interval fails even if its status says active.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/holding_events.json`. |
| Grain and key | One row per event; no declared key. Events are grouped by (`BEMS_ID`, `CERT_CODE`) and processed in the text order of `EFFECTIVE_AT_UTC`, then `EVIDENCE_REF`. |
| Required | No |
| Capabilities | executable |
| If missing or empty | No revocations are known, and holdings rest on `mechanic_certs` alone. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. |
| Time and cutoff | `EFFECTIVE_AT_UTC` (when the event takes effect) drives validity. `OBSERVED_AT_UTC` (when the source saw it) is the cutoff column: fixture captures hide events observed after the cutoff. Both are UTC with an explicit offset. No freshness check. |
| Read by | `focus/data/qualifications.py` (`build_holdings`); `focus/data/fixture_reader.py` (cutoff); the windows are checked by `focus/eligibility.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `BEMS_ID` | text, matched exactly | Native person key. | With `CERT_CODE`, picks the holdings the event applies to. |
| `CERT_CODE` | text, matched exactly | Certificate code. | With `BEMS_ID`, picks the holdings the event applies to. |
| `EVENT` | text: `REVOKED` or `REINSTATED` | Type of event. | `REVOKED` opens a revocation, and a second one while revoked changes nothing. `REINSTATED` closes an open revocation; other values are ignored. |
| `EFFECTIVE_AT_UTC` | UTC timestamp, ISO 8601 with offset | Instant the event takes effect. | Start or end of the revocation window. Events are sorted by this text, so use one format throughout. |
| `OBSERVED_AT_UTC` | UTC timestamp, ISO 8601 with offset | Instant the source observed the event. | Cutoff column: fixture captures hide events observed after the cutoff. Nothing else reads it. |
| `EVIDENCE_REF` | text | Reference to the source evidence (fixture: `FIXTURE-REVOKE-A108-C`). | Breaks ties between events with the same effective time. |

**Rules and pitfalls.**
- Effective time and observed time differ. A revocation observed before the cutoff but effective later already blocks intervals after its effective time (fixture: observed 2026-10-25, effective 2026-10-29).
- An open revocation fails any interval that ends after its effective instant. A closed window fails any interval that overlaps it.
- Write every timestamp in UTC with the same text format (for example `2026-10-29T12:00:00Z`). Events are ordered by raw text, and a value without an offset stops admission.
- A `REINSTATED` with no open revocation is ignored, and no event creates a holding.
- The live producer must apply the observed-at cutoff. An event observed after the cutoff must not enter the run (crosswalk section C).
- Dispatch rechecks current certificates before release (`CERT_NOT_HELD` in `focus/publication/dispatch.py`).

## `cert_eligibility_matrix`

**Purpose.** A convenience matrix from the source system that labels person-job pairs with an eligibility label, a percent of certificates held and a substitution label. FOCUS treats it as a display projection only, and its labels grant nothing. When it has rows, admission compares each label with FOCUS's own decision at the cutoff and records bounded notes. Mechanic names are never extracted.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_CERT_ELIGIBILITY`, query `queries/source_inputs/cert_eligibility_matrix.sql`. The projection leaves out `MECHANIC`, `DESCRIPTION`, `MISSING_CERTS` and `MISSING_TRAININGS`. |
| Grain and key | One row per labelled person-job pair; no S04 primary key. Notes group rows by (labels, FOCUS decision). |
| Required | No |
| Capabilities | projection |
| If missing or empty | Nothing changes and no notes are made. Dataset status gap `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. With rows: one `CERT_MATRIX_PROJECTION_ONLY` gap (effect `INFO`) per label and decision pair, and `CERT_MATRIX_UNMATCHED` (`INFO`) for rows with no admitted person or job. |
| Time and cutoff | `LOAD_TIMESTAMP` is not freshness-checked (the dataset is not in `admission.max_source_age_hours`). FOCUS's decision is taken for the cutoff minute. |
| Read by | `focus/data/qualifications.py` (`matrix_label_notes`), called from `focus/data/snapshot_builder.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `TEAM` | varchar(30), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `JOB_SOI` | varchar(30), nullable | SOI of the labelled job. | With `LINE_NUMBER`, matched to a census job for the comparison. |
| `LINE_NUMBER` | int, nullable | Aircraft line number. | Part of the job match. |
| `BEMS_ID` | bigint, nullable | Native person key, stored as a number. | Matched as text to known people; quoted in note examples. |
| `SHIFT` | varchar(5), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ELIGIBLE` | varchar(15), nullable | The matrix's eligibility label. | Shown in the note next to FOCUS's decision; grants nothing. |
| `PCT_CERTS_HELD` | decimal(5,1), nullable | The matrix's percent-of-certificates-held figure. | Quoted in note examples; grants nothing. |
| `CERTS_REQUIRED` | int, nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CERTS_HELD` | int, nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SUBSTITUTION` | varchar(8), nullable | The matrix's substitution (SUBST) label. | Shown in the note; never creates an OR alternative. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Time the row was loaded at the source. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Display only. `ELIGIBLE`, `PCT_CERTS_HELD` and `SUBSTITUTION` never authorize anything (registry description; spec S07).
- Never extract `MECHANIC` names (spec S05; the projection leaves them out).
- `BEMS_ID` is `bigint` here but `varchar(10)` in `mechanic_certs`. A person key that relies on leading zeros will not match and lands in `CERT_MATRIX_UNMATCHED`.
- Notes are bounded: one per label and decision pair, with a count and up to five examples. The examples quote native person keys in the gap detail.
- The fixture certifies this dataset empty, because fixture person keys are not numeric.
