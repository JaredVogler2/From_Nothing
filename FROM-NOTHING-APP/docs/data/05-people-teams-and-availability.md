# People, teams and availability

These datasets tell FOCUS who the people are, which team and shift each person works, who is present on the production day, what time is already taken, and how much overtime (OT: minutes worked after the end of the physical shift) is allowed. Admission turns them into pure pools. A pool is one site, team, shift start date, shift number (1, 2 or 3) and source run, and people are never borrowed across teams or shifts. A real person enters a pool only through `shift_roster`. A pool member counts for an EXECUTABLE plan (a plan meant for dispatch) only with an agreeing effective assignment in `person_team_map`. On the production day, `attendance_shift` must also have rows and must not mark the person absent. Everyone else in the pool is nominal FORECAST capacity. Team inference (`team_inference`, computed in SQL; `dept_team_skill_ref` feeds its department fallback and `mech_team_inferred` is kept beside it for comparison) is forecast evidence only. `availability_exceptions`, `ot_authorizations` and `carry_in` then shape each member's time: leave, other duty, authorized OT and duty already booked. Each pool counts its distinct admitted members (N, separately for EXECUTABLE and FORECAST), and at most floor(85 × N / 100) of them may be active at once (the activation cap).

**Terms used below.**
- **Person key.** Every person column (`PERSON_KEY` or `BEMS_ID`) holds the native person key: the badge/BEMS ID in live data, an invented `DEMO-` key in the fixture. FOCUS matches these columns as exact text. Live keys sit in the `BEMS` namespace and fixture keys in `fixture-demo`, so invented demo people never share an identity with live people (`person_namespace` in `focus/data/workforce.py`). A person exists only if they appear in `shift_roster`, `person_team_map`, `team_inference` or `attendance_shift`.
- **Production day.** The local date of the run cutoff in the shift time zone (policy `shift.timezone`, `America/New_York` in the fixture policy). Attendance matters only for shifts that start on this date.
- **Shift numbers.** S1 starts 06:00, S2 14:30 and S3 23:00 local (fixture shift policy). A shift is labelled by its start date.

**Gaps that apply to every dataset here.**
- A dataset captured as `NOT_PROBED`, `EMPTY_UNCERTIFIED` or `FAILED` adds the gap `DATASET_NOT_PROBED`, `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`, with effect `BLOCKS_EXECUTABLE` (`focus/data/snapshot_builder.py`). The live reader never certifies an empty result. Every supplemental dataset in this group except `team_inference` reports `NOT_PROBED` live today.
- Freshness applies only to datasets listed in policy `admission.max_source_age_hours` (fixture defaults; live load cadences are an open decision). The newest row clock (`LOAD_TIMESTAMP` here), read in the dataset's declared time zone, must be within the limit at the cutoff. Otherwise the gap is `SOURCE_STALE`; a clock that cannot be read is `SOURCE_CLOCK_UNKNOWN`.
- `BLOCKS_EXECUTABLE` is a label. The validator fails an EXECUTABLE plan outright only for `SOURCE_STALE`, `SOURCE_CLOCK_UNKNOWN` and `CAPTURE_SKEW` (`focus/validation/plan.py`). Other gaps act through the data, for example by leaving a pool with no executable members.
- In live mode every open decision in the policy file is also added as a `BLOCKS_EXECUTABLE` gap. The ones for this group are `OD-04` (OT authority) and `OD-10` (assignment and attendance feeds).
- The reference SOURCE queries take no as-of parameter. The company adapter must enforce the run cutoff itself (`docs/integration/SOURCE_REPLACEMENT_CROSSWALK.md`, section C).

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`people_directory`](#people_directory) | SUPPLEMENTAL | No | inventory | None yet: reports NOT_PROBED | Display only | Display names and origin labels for people found in other workforce data |
| [`person_team_map`](#person_team_map) | SOURCE | No | executable | Reference SQL query | Yes | Current effective team assignment; needed for any executable crew member |
| [`team_inference`](#team_inference) | SUPPLEMENTAL | No | forecast | Supplemental SQL path; producer table not installed | Display only | SQL-computed nominal home team; labels and review queue only |
| [`mech_team_inferred`](#mech_team_inferred) | SOURCE | No | forecast | Reference SQL query | No | Legacy inferred team; comparison evidence for the SQL inference |
| [`dept_team_skill_ref`](#dept_team_skill_ref) | SOURCE | No | forecast | Reference SQL query | No | Department-to-team map behind the SQL inference's department fallback |
| [`shift_roster`](#shift_roster) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Who works which team and shift over which dates; the only way into a pool |
| [`attendance_shift`](#attendance_shift) | SOURCE | No | executable | Reference SQL query | Yes | Presence or absence per person and shift on the production day |
| [`availability_exceptions`](#availability_exceptions) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Leave and other duty that remove or reduce availability |
| [`ot_authorizations`](#ot_authorizations) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Authorized OT minutes per team, shift and date; none means 0 |
| [`carry_in`](#carry_in) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Duty already occupying people in the horizon; locked, never double-booked |

## `people_directory`

**Purpose.** Gives each person a display name for the UI and an origin label. It never adds a person: a row only names someone already found in the roster, assignment, inference or attendance data. Live data should carry minimal display identity and no private HR fields (registry description).

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/people_directory.json`. |
| Grain and key | One row per person; key `PERSON_KEY` (from code: rows are indexed by `PERSON_KEY`, and a repeated key keeps the last row). |
| Required | No |
| Capabilities | inventory |
| If missing or empty | Names fall back to the native person key; nothing else changes. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. |
| Time and cutoff | No time columns, no cutoff column, no freshness check. |
| Read by | `focus/data/workforce.py` (`build_workforce`). Names and origin reach the UI through `person_view` in `focus/web/serializers.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `PERSON_KEY` | text, matched exactly | Native person key (live: the badge/BEMS ID; fixture: a key such as `DEMO-A1-01`). | Looks up the name and origin of a person already known from other workforce datasets. Rows for anyone else are ignored. |
| `DISPLAY_NAME` | text | Display name of the person. Fixture names are invented. | Becomes the person's name in every person view. Without a row, the native key is shown. |
| `ORIGIN` | text; only `FIXTURE_DEMO` is recognized | Origin label of the person record. | `FIXTURE_DEMO` marks an invented demonstration person; any other value gives `SOURCE` in live mode, and in fixture mode everyone is `FIXTURE_DEMO`. The label is displayed and grants nothing. |

**Rules and pitfalls.**
- Send minimal display identity only, never private HR fields (registry description; crosswalk section B).
- A directory row does not create a person. The person must also appear in `shift_roster`, `person_team_map`, `team_inference` or `attendance_shift`.
- Do not send a null `DISPLAY_NAME`. The code converts it to the text `None` (`focus/data/workforce.py`, `build_workforce`).
- Use the same native key text as `BEMS_ID` in the SOURCE tables.
- Keep native keys to letters, digits, `.`, `_` and `-`, starting with a letter or digit, at most 64 characters. The My Day API (`GET /api/v1/myday`) accepts only that person-key pattern (`focus/web/api_views.py`).

## `person_team_map`

**Purpose.** The current effective team assignment of each person, dated to the exact instant. It is the strongest team authority: a rostered person counts for EXECUTABLE plans only while an assignment is active at the shift start, and the assignment must agree with the roster. It proves team and role, never presence (`docs/execution/TEAM_AUTHORITY.md`). The S04 catalog observed this table empty on 2026-10-27, so live executable crews are not ready today (`OD-10`).

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_PERSON_TEAM_MAP`, query `queries/source_inputs/person_team_map.sql` (no filter). |
| Grain and key | One row per person and effective start; S04 primary key (`BEMS_ID`, `EFFECTIVE_FROM`). The code drops exact repeats of (start, end, team, role) and flags overlapping rows with a different team or role. |
| Required | No |
| Capabilities | executable |
| If missing or empty | Gap `ASSIGNMENT_FEED_EMPTY` (`BLOCKS_EXECUTABLE`): no pool has executable members on any day, and inference stays nominal. Dataset status gap `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`; live mode also raises `OD-10`. An unparseable `EFFECTIVE_FROM` or `EFFECTIVE_TO`, or a naive value with no time zone policy, rejects the run (`SOURCE_VALUE_INVALID` with `BAD_SOURCE_DATETIME` or `SOURCE_TIMEZONE_POLICY_MISSING`). |
| Time and cutoff | `EFFECTIVE_FROM` (inclusive) and `EFFECTIVE_TO` (exclusive) are local wall times, converted to UTC with this dataset's time zone policy (fixture receipt or `FOCUS_SOURCE_TIMEZONE_MAP_JSON`); an explicit offset is honored. `LOAD_TIMESTAMP` is the freshness clock (limit 36 h). No cutoff column: rows that become effective later apply to later shifts. |
| Read by | `focus/data/workforce.py` (`build_workforce`); `focus/data/freshness.py` (`LOAD_TIMESTAMP`). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `BEMS_ID` | varchar(10), not null | Native person key (the badge/BEMS ID). | Adds the person to the person list, even when `IN_SCOPE` is false, and keys the assignment. |
| `ROLE` | varchar(10), not null | Role held under the assignment (fixture: `MECH`, `QA`). | Part of the assignment's identity: overlapping in-scope rows with a different role raise `ASSIGNMENT_CONFLICT`. It does not set crew roles. |
| `TEAM` | varchar(40), nullable | Team the person is assigned to (fixture: `PROD-A`). | Must equal the roster team at every rostered shift start where the assignment is active, or `ROSTER_ASSIGNMENT_MISMATCH`. Also the effective team at the cutoff on the person's inference record. |
| `IN_SCOPE` | bit, not null | In-scope flag of the assignment row. | Rows with a false value are skipped as assignments. |
| `BASIS` | varchar(30), not null | Meaning not established in code or docs (fixture: `ASSIGNED`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `EFFECTIVE_FROM` | datetime2(0), not null | Instant the assignment starts, inclusive, in source local time. | Decides membership at each shift start instant and the effective team at the cutoff; also used in the overlap checks. |
| `EFFECTIVE_TO` | datetime2(0), nullable | Instant the assignment ends, exclusive; null means open-ended. | Same uses as `EFFECTIVE_FROM`. |
| `RUN_ID` | varchar(40), nullable | Meaning not established in code or docs (fixture: `FIXTURE-ASSIGN-01`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `EVIDENCE_COUNT` | int, not null | Meaning not established in code or docs (fixture: `0`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LOOKBACK_DAYS` | smallint, not null | Meaning not established in code or docs (fixture: `30`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Time the row was loaded at the source. | Freshness clock: the newest value must be within 36 h of the cutoff, or `SOURCE_STALE`. |

**Rules and pitfalls.**
- Membership is decided at the shift start instant. An assignment effective exactly at 06:00 local governs the S1 shift that starts then; one effective at 06:01 does not (`docs/execution/TEAM_AUTHORITY.md`).
- One team and role at a time. Overlapping in-scope rows with a different team or role raise `ASSIGNMENT_CONFLICT`. The person is then excluded from every pool for the whole horizon, with a `WORKFORCE_CONFLICT` gap (effect `INFO`).
- The assignment must agree with the roster at every rostered shift start in the horizon where it is active. A mismatch is `ROSTER_ASSIGNMENT_MISMATCH`, with the same exclusion.
- `TEAM` is nullable in S04, but an in-scope row with a null team reads as an empty team name and conflicts with any roster team.
- Send `IN_SCOPE` as a bit. The code tests truthiness, so text such as `'0'` would count as in scope.
- An assignment never proves presence. Presence comes from `attendance_shift`.

## `team_inference`

**Purpose.** SQL Server computes each person's nominal home team from 30 days of clock evidence and stores it in `FOCUS_FN_TeamInference`. FOCUS reads those rows and never runs its own production inference. The rows are forecast evidence only: they label people on the snapshot (inferred team, effective team, tie and stale flags) and feed the Standards view's review queue. In the current code they do not change pool membership, availability basis or any crew.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: live read `queries/workforce/read_team_inference.sql` from the proposed table `WEB_APP.FOCUS_FN_TeamInference` (not installed), bound to this scope's source head run. Rows are written by `queries/workforce/infer_mechanic_teams.sql` (algorithm `S07-plurality-v1`), which no application code runs today. |
| Grain and key | One row per person per run; the producer table's primary key is (`RunId`, `PersonKey`) (`sql/migrations/001_focus_fn_core.sql`). The code indexes rows by `PERSON_KEY`; a repeated key keeps the last row. |
| Required | No |
| Capabilities | forecast |
| If missing or empty | Pools and crews do not change; the Standards view shows no inference. Dataset status gap `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED` (for example `SCOPE_KEY_MISSING` when no scope key is set). If the table is not installed, the driver error marks the dataset `FAILED` (`SOURCE_QUERY_FAILED`) and the run continues. Rows whose window ended more than 7 days before the cutoff give one `TEAM_INFERENCE_STALE` gap (effect `INFO`). |
| Time and cutoff | `WINDOW_START_UTC` and `WINDOW_END_UTC` are UTC with an explicit offset; the window is half-open and ends at the run cutoff. A row is stale when `WINDOW_END_UTC` is missing or more than `workforce.inference_stale_after_days` (7, a fixture default; the live value is an open calibration) before the cutoff. No row clock, no freshness check, no cutoff column. |
| Read by | `focus/data/workforce.py` (`build_workforce`); shown by `standards` in `focus/web/serializers.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `RUN_ID` | text (not parsed) | Source run the inference rows belong to (`RunId` in the producer table). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `PERSON_KEY` | text, matched exactly | Native person key. | Adds the person to the person list and joins the row to the person's assignment and roster. |
| `INFERRED_TEAM` | text or null | Team with the most admitted clock transactions in the window; the mapped department team for `DEPT_FALLBACK`; null for `UNKNOWN`. | Shown as the inferred team. It becomes the effective team only when there is no current assignment and the row is not stale. |
| `WINDOW_START_UTC` | UTC timestamp, ISO 8601 with offset | Start of the evidence window, inclusive. | Copied onto the snapshot's inference record; no rule or view uses it further. |
| `WINDOW_END_UTC` | UTC timestamp, ISO 8601 with offset | End of the evidence window, exclusive (the run cutoff). | Decides staleness against the cutoff. |
| `EVIDENCE_COUNT` | integer (null reads as 0) | Number of admitted clock transactions for the winning team. | Copied onto the record and shown in the Standards view. |
| `EFFORT_MINUTES` | decimal (null reads as 0) | Winning team's effort minutes as a union of overlapping intervals, kept separate from the count. | Copied onto the record; no rule or view uses it further. |
| `TOP_SHARE` | decimal or null | Winning team's count divided by the person's total count, at decimal(9,8). | Copied onto the record and shown. |
| `RUNNER_UP_TEAM` | text or null | Second-ranked team. | Copied onto the record and shown. |
| `RUNNER_UP_COUNT` | integer or null | Second-ranked team's count. | Copied onto the record; no rule or view uses it further. |
| `IS_TIE` | boolean | True when the runner-up count equals the winning count. | Shown; ties are listed in the Standards review queue as "tie between teams". |
| `BASIS` | text (null reads as `UNKNOWN`) | `LABOR_PLURALITY`, `DEPT_FALLBACK` or `UNKNOWN`. | Copied onto the record and shown. |
| `ALGORITHM_VERSION` | text | Inference algorithm version (`S07-plurality-v1`). | Copied onto the record and shown. |
| `FIRST_EVIDENCE_UTC` | timestamp (not parsed) | Start of the earliest admitted evidence. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LAST_EVIDENCE_UTC` | timestamp (not parsed) | End of the latest admitted evidence. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Forecast evidence only, never presence or an assignment. A current effective assignment overrides inference from its exact instant (`docs/execution/TEAM_AUTHORITY.md`; `docs/execution/DECISIONS.md`).
- Inference never puts anyone in a pool. Membership needs a `shift_roster` row, and a rostered person without an active assignment is nominal (`INFERRED_NOMINAL`) whether or not the inference agrees (`focus/data/workforce.py`).
- SQL owns the algorithm: latest revision of each clock event, voided events removed, teams ranked by count, ties broken by team name in code-point order (`Latin1_General_BIN2`), effort never used as the count.
- `DEPT_FALLBACK` comes only through an actual mapped department. Team-less evidence with no mapping is `UNKNOWN` with no team; no team is invented.
- The window timestamps are UTC. The live reader writes the `datetime2(6)` values of these `_UTC` columns with their offset, so they parse; in a fixture file, naive text rejects the run (`SOURCE_VALUE_INVALID`, `NAIVE_DATETIME`).
- The live read takes the rows of the scope's current source head (a COMPLETE run). The migration notes say inference is written for the run being captured, so confirm which run's rows the capture should carry.

## `mech_team_inferred`

**Purpose.** A legacy table of inferred home teams. FOCUS captures it with every source run, but no planner code reads the captured rows. The SQL inference reads the table directly and copies one legacy row per person into its evidence payload for comparison; it never decides the inferred team.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_MECH_TEAM_INFERRED`, query `queries/source_inputs/mech_team_inferred.sql`. |
| Grain and key | One row per person and inferred team; S04 primary key (`BEMS_ID`, `INFERRED_TEAM`). |
| Required | No |
| Capabilities | forecast |
| If missing or empty | No planning effect. Dataset status gap `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED` only. The fixture certifies it empty because fixture person keys are not numeric. |
| Time and cutoff | `LOAD_TIMESTAMP` is not freshness-checked (the dataset is not in `admission.max_source_age_hours`). No cutoff column. |
| Read by | No planner code. Separately, `queries/workforce/infer_mechanic_teams.sql` reads the table. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `BEMS_ID` | bigint, not null | Native person key, stored as a number. | Fetched and kept with the source run; nothing in the planner reads it today. The SQL inference converts it to text to match `PersonKey`. |
| `INFERRED_TEAM` | varchar(30), not null | Legacy inferred home team. | Fetched and kept with the source run; nothing in the planner reads it today. The SQL inference copies it as `legacy_team`. |
| `SOURCE` | varchar(15), not null | Basis of the legacy inference; S04 observed the values `LABOR` and `DEPT_FALLBACK`. | Fetched and kept with the source run; nothing in the planner reads it today. The SQL inference copies it as `legacy_source`. |
| `LABOR_RECORDS` | int, not null | Meaning not established in code or docs beyond its name. | Fetched and kept with the source run; nothing in the planner reads it today. The SQL inference ranks a person's legacy rows by it (highest first) and copies it as `legacy_labor_records`. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Time the row was loaded at the source. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Legacy evidence only. It is not a current roster and never decides a team; `FOCUS_FN_TeamInference` is the authority (registry description; crosswalk section A; `infer_mechanic_teams.sql` step 6).
- `BEMS_ID` is `bigint` here but `varchar(10)` in `person_team_map`, `attendance_shift` and `mechanic_certs`. Keys that rely on leading zeros cannot match across them.

## `dept_team_skill_ref`

**Purpose.** Maps an HR accounting department to a primary team. The SQL inference uses it for explicit `DEPT_FALLBACK` rows: a person with no admitted team evidence gets the mapped department's team, never a guessed one. The planner reads only its load time, for freshness.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_DEPT_TEAM_SKILL_REF`, query `queries/source_inputs/dept_team_skill_ref.sql`. |
| Grain and key | One row per accounting department; S04 primary key `ACCTG_DEPT_CD`. |
| Required | No |
| Capabilities | forecast |
| If missing or empty | No planning effect. Dataset status gap `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. With rows, a stale or unreadable `LOAD_TIMESTAMP` raises `SOURCE_STALE` or `SOURCE_CLOCK_UNKNOWN`, and those fail every EXECUTABLE plan. |
| Time and cutoff | `LOAD_TIMESTAMP` is the freshness clock (limit 168 h), read in this dataset's time zone. No cutoff column. |
| Read by | `focus/data/freshness.py` (`LOAD_TIMESTAMP` only). Separately, `queries/workforce/infer_mechanic_teams.sql` joins the table. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `ACCTG_DEPT_CD` | varchar(10), not null | HR accounting department code (fixture: `D100`). | Fetched and kept with the source run; nothing in the planner reads it today. The SQL inference joins each person's latest active HR department to it. |
| `PRIMARY_TEAM` | varchar(30), nullable | Team mapped to the department (fixture: `PROD-A`). | Fetched and kept with the source run; nothing in the planner reads it today. The SQL inference uses the trimmed value as the `DEPT_FALLBACK` team, and a blank value means no mapping. |
| `DOMINANT_SKILL` | varchar(5), nullable | Meaning not established in code or docs (fixture: `MECH`, `QA`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LABOR_RECORDS` | int, nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Time the row was loaded at the source. | Freshness clock: the newest value must be within 168 h of the cutoff, or `SOURCE_STALE`. |

**Rules and pitfalls.**
- It proves only a labelled nominal team, never availability or certification (crosswalk section A; `docs/execution/TEAM_AUTHORITY.md`).
- Keep it fresh even though the planner ignores its content. A stale clock blocks every EXECUTABLE plan.

## `shift_roster`

**Purpose.** The scheduled roster: which team and shift each person works over a date range. It is the only way a real person enters a pool, so it sets pool membership and the denominator N for the activation cap. It proves shift membership, not presence and not a team assignment (`docs/execution/TEAM_AUTHORITY.md`).

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/shift_roster.json`. |
| Grain and key | One row per person, team, shift and date range; no declared key. The code drops exact repeats of (person, from, to, team, shift, role) and flags overlapping rows with a different team or shift. |
| Required | No |
| Capabilities | executable |
| If missing or empty | No real person joins any pool, so no real crew can be formed for any purpose. Only the configured virtual support teams (`MHE`, `PSC`, `TST`) still get synthetic, forecast-only people. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. A shift number other than 1, 2 or 3 gives `ROSTER_SHIFT_UNDEFINED` (`BLOCKS_EXECUTABLE`) and no membership. |
| Time and cutoff | `EFFECTIVE_FROM` (inclusive) and `EFFECTIVE_TO` (exclusive) are shift start dates. Pools are built for every day of the admitted line-calendar horizon and every shift. No row clock, no freshness check, no cutoff column. |
| Read by | `focus/data/workforce.py` (`build_workforce`). The pools it produces are used by `focus/engine/model.py`, `focus/engine/crew_selection.py` and `focus/validation/`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `PERSON_KEY` | text, matched exactly | Native person key. | Adds the person to the person list. Roster rows are the only source of pool membership. |
| `TEAM` | text, required | Team the person is rostered to (fixture: `PROD-A`, `QA`). | Names the pool the person joins. Work phases use the job's team from `master_task_list`, and QA phases use the policy team `qa.inspector_team` (`QA` in the fixture). |
| `ROLE` | text | Rostered role (fixture: `MECH`, `QA`). | Part of the row identity for de-duplication only; no conflict, pool or crew rule uses it today. |
| `SITE` | text (not parsed) | Site of the roster row (fixture: `DEMO`). | Fetched and kept with the source run; nothing in the planner reads it today. Pools always use the scope's site. |
| `SHIFT_NUMBER` | integer: 1, 2 or 3 | Shift the person works. | Selects the shift pool. Any other number gives `ROSTER_SHIFT_UNDEFINED` and no membership. |
| `EFFECTIVE_FROM` | date `YYYY-MM-DD`, or null | First shift start date the row covers, inclusive; null means no start bound. | Membership applies from this date. |
| `EFFECTIVE_TO` | date `YYYY-MM-DD`, or null | End of the range, exclusive; null means open-ended. | Membership stops on this date. |
| `BASIS` | text (not parsed) | Meaning not established in code or docs (fixture: `SCHEDULED_ROSTER`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `EVIDENCE_REF` | text (not parsed) | Reference to the roster's source evidence (fixture: `FIXTURE-ROSTER-2026`). | Fetched and kept with the source run; nothing in the planner reads it today. Availability evidence uses a generated `person@date S<n>` reference instead. |

**Rules and pitfalls.**
- Dates only, and `EFFECTIVE_TO` is exclusive. (`ot_authorizations.DATE_TO` is inclusive.)
- One team and one shift per person at a time. Overlapping rows with a different team or shift raise `ROSTER_CONFLICT`; the person leaves every pool, with a `WORKFORCE_CONFLICT` gap.
- The roster team must match the assignment active at each rostered shift start in the horizon, or `ROSTER_ASSIGNMENT_MISMATCH` (same exclusion).
- A rostered person counts for EXECUTABLE plans only with an assignment active at the shift start. Otherwise they are nominal forecast capacity (`INFERRED_NOMINAL`).
- FOCUS does not check `SITE`, so send only rows for the scope's site. `TEAM` must not be null: the code would turn it into a team named `None`.
- `TEAM` codes must match the job team codes in `master_task_list` exactly; a job demands its own team's pool and never borrows another.

## `attendance_shift`

**Purpose.** Actual presence or absence per person, production day and shift. FOCUS uses it only for the production day. There, a rostered, assigned person marked present becomes `ATTENDANCE_CONFIRMED`, and one marked absent leaves the pool. The S04 catalog observed this table empty on 2026-10-27 (`OD-10`).

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_ATTENDANCE_SHIFT`, query `queries/source_inputs/attendance_shift.sql` (no filter). |
| Grain and key | One row per production day, shift and person; S04 primary key (`PRODUCTION_DAY`, `SHIFT_NUMBER`, `BEMS_ID`). The code keys rows the same way; a repeat keeps the last row. |
| Required | No |
| Capabilities | executable |
| If missing or empty | Gap `ATTENDANCE_FEED_EMPTY` (`BLOCKS_EXECUTABLE`): nobody is an executable crew member on the production day. Later days still use roster plus assignment. Dataset status gap `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`; live mode also raises `OD-10`. |
| Time and cutoff | `PRODUCTION_DAY` is the shift start date. `LOAD_TIMESTAMP` is the registry cutoff column: fixture captures hide rows loaded after the cutoff, but the live reader applies no cutoff. It is also the freshness clock (limit 18 h). |
| Read by | `focus/data/workforce.py` (`build_workforce`); `focus/data/freshness.py`; `focus/data/fixture_reader.py` (cutoff, fixture mode only). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `PRODUCTION_DAY` | date, not null | Shift start date the record covers. | Only rows for the production day change anything. |
| `SHIFT_NUMBER` | tinyint, not null | Shift 1, 2 or 3. | Matches the record to the shift pool. |
| `BEMS_ID` | varchar(10), not null | Native person key. | Adds the person to the person list and matches the record to their roster membership. |
| `PRESENT` | bit, not null | Whether the person attended the shift. | On the production day, 1 makes an assigned member `ATTENDANCE_CONFIRMED`. 0 removes the person from that pool for every purpose (listed in the pool's exclusions as `ABSENT`). |
| `HOURS` | decimal(5,2), nullable | Meaning not established in code or docs (fixture: `8.50`, `0.00`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SOURCE` | varchar(20), not null | Meaning not established in code or docs (fixture: `FIXTURE_BADGE`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Time the row was loaded at the source. | Cutoff column in fixture captures, and the freshness clock (newest value within 18 h of the cutoff, or `SOURCE_STALE`). |

**Rules and pitfalls.**
- The emptiness check looks at the production day itself. A feed with rows only for other days raises `ATTENDANCE_FEED_EMPTY`, and nobody is executable that day. Always send the production day's rows.
- A missing row is not an absence. Only `PRESENT = 0` excludes a person.
- Attendance never creates team membership. A present person still needs a roster row and an agreeing assignment (`docs/execution/TEAM_AUTHORITY.md`).
- The reference query has no as-of filter. The adapter must keep out rows loaded after the cutoff, as the fixture reader does (crosswalk section C).
- Send `PRESENT` as a bit. The code tests truthiness, so text such as `'0'` would read as present.
- Dispatch rechecks current attendance. A crew member who is no longer an admitted pool member gets `ATTENDANCE_WITHDRAWN` (`focus/publication/dispatch.py`).

## `availability_exceptions`

**Purpose.** Planned leave and other duty that remove or reduce a person's availability. A `LEAVE` row takes the person out of one shift's pool. Any other kind with start and end times becomes a locked "other duty" interval, which the duty ledger, validator and dispatch treat like `carry_in`.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/availability_exceptions.json`. |
| Grain and key | One row per person and exception; no declared key. Leave is keyed by (`PERSON_KEY`, `PRODUCTION_DAY`, `SHIFT_NUMBER`). |
| Required | No |
| Capabilities | executable |
| If missing or empty | Nobody is excluded for leave and no other duty is booked. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. An unparseable local time, or a missing time zone policy, rejects the run (`SOURCE_VALUE_INVALID` with `BAD_SOURCE_DATETIME` or `SOURCE_TIMEZONE_POLICY_MISSING`). |
| Time and cutoff | `START_LOCAL` and `END_LOCAL` are local wall times, converted to UTC with this dataset's time zone policy (a time inside a DST gap moves forward; an ambiguous time takes its first occurrence). `OBSERVED_AT_UTC` is the cutoff column: fixture captures hide rows observed after the cutoff. No freshness check. |
| Read by | `focus/data/workforce.py` (`build_workforce`); `focus/data/fixture_reader.py` (cutoff). The other-duty intervals are used by `focus/engine/model.py`, `focus/validation/duty.py` and `focus/publication/dispatch.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `PERSON_KEY` | text, matched exactly | Native person key. | Leave applies to any key. Other duty is kept only for people FOCUS already knows. |
| `PRODUCTION_DAY` | date `YYYY-MM-DD`, required | Shift start date of the exception. | With `SHIFT_NUMBER`, picks the shift a `LEAVE` row removes. Parsed on every row. |
| `SHIFT_NUMBER` | integer, required | Shift of the exception. | With `PRODUCTION_DAY`, picks the shift a `LEAVE` row removes. Parsed on every row. |
| `KIND` | text | Type of exception (fixture: `LEAVE`). | `LEAVE` excludes the person from that shift's pool for every purpose (listed in the pool's exclusions as `LEAVE`). Any other value becomes an `OTHER` duty interval when both local times are present. |
| `START_LOCAL` | local date-time, ISO 8601 without offset (an offset is honored), or null | Start of the other duty, in source local time. | Start of the duty interval; ignored for `LEAVE`. |
| `END_LOCAL` | local date-time, ISO 8601 without offset (an offset is honored), or null | End of the other duty, in source local time. | End of the duty interval. It must be after `START_LOCAL`, or the row is dropped. |
| `OBSERVED_AT_UTC` | UTC timestamp, ISO 8601 with offset | When the source observed the exception. | Cutoff column: fixture captures hide rows observed after the cutoff. Nothing else reads it. |
| `EVIDENCE_REF` | text | Reference to the source evidence (fixture: `FIXTURE-LEAVE-A106`). | Stored as evidence on the other-duty interval. |

**Rules and pitfalls.**
- `LEAVE` covers the whole shift and its times are ignored. For part of a shift, use another `KIND` with both local times.
- Other-duty rows without both times, with an end not after the start, or for an unknown person are dropped without any gap.
- `PRODUCTION_DAY` and `SHIFT_NUMBER` are parsed on every row, before `KIND`. A bad value rejects the run (`SOURCE_VALUE_INVALID`).
- Declare a time zone for `availability_exceptions`: the fixture receipt's `source_timezone`, or `FOCUS_SOURCE_TIMEZONE_MAP_JSON` with this name or `*`.
- The live producer must apply the observed-at cutoff. An exception observed after the cutoff must not enter the run (crosswalk section C).
- Other duty uses up the shift's regular and OT budget and blocks overlapping work (`PERSON_OVERLAP`). Dispatch reports `DUTY_CONFLICT`.

## `ot_authorizations`

**Purpose.** Explicit overtime authority. Each row authorizes OT minutes for one team and shift over a date range. FOCUS gives every member of a matching pool an OT window that starts at the end of the physical shift and lasts the authorized minutes. Absent authority means OT 0 (registry description; `OD-04`).

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/ot_authorizations.json`. |
| Grain and key | One row per team, shift and date range; no declared key. When rows overlap, the largest `MINUTES` applies; rows never add up. |
| Required | No |
| Capabilities | executable |
| If missing or empty | OT is 0 for every pool, with no gap of its own. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`; live mode also raises `OD-04`. |
| Time and cutoff | `DATE_FROM` and `DATE_TO` are shift start dates, both inclusive. No row clock, no cutoff column, no freshness check. |
| Read by | `focus/data/workforce.py` (`build_workforce`). The resulting OT windows and caps are checked by `focus/validation/plan.py` (V4) and `focus/validation/duty.py` (V7). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `TEAM` | text, required | Team the authority covers (fixture: `PROD-A`). | Must equal the pool's team exactly. |
| `SHIFT_NUMBER` | integer, required | Shift the authority covers. | Must equal the pool's shift number. |
| `DATE_FROM` | date `YYYY-MM-DD`, required | First shift start date covered, inclusive. | Pool dates from this date match. |
| `DATE_TO` | date `YYYY-MM-DD`, required | Last shift start date covered, inclusive. | Pool dates up to and including this date match. |
| `MINUTES` | integer, required | Authorized OT minutes per person per shift. | Capped at policy `shift.maximum_ot_minutes` (60). Sets the length of each member's OT window and their OT cap. |
| `AUTHORITY` | text (not parsed) | Overtime authority reference (fixture: `FIXTURE-OT-APPROVAL`); not defined further in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `EVIDENCE_REF` | text (not parsed) | Reference to the source evidence (fixture: `FIXTURE-OT-2026-10`). | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- No authority, no OT. A missing row means 0 minutes, never a default (`OD-04`: without a labor-source authorization, live OT is 0).
- `DATE_TO` is inclusive, unlike `shift_roster.EFFECTIVE_TO`.
- Minutes above 60 are cut to 60 (policy `shift.maximum_ot_minutes`, a user requirement).
- Authority is per team, shift and date and applies to every member of that pool. The dataset has no person column, although the crosswalk lists person-level authority as a source need.
- Work in the OT window beyond a member's authorized minutes fails validation (`OT_NOT_AUTHORIZED` in V4, `OT_CAP` in V7).
- Every column except `AUTHORITY` and `EVIDENCE_REF` must be present. A missing date or number rejects the run (`SOURCE_VALUE_INVALID`).

## `carry_in`

**Purpose.** Duty that already occupies people inside the planning horizon: actual work, travel, waiting, tool time or other duty. FOCUS books each row as a locked duty that no algorithm moves, so no plan can double-book the person. The validator includes it in the person's non-overlap check and shift budget, dispatch rechecks it, and the My Day, Gantt and Turnover views show it.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet; the live reader reports `NOT_PROBED`. Fixture file `fixtures/inputs/manufacturing_demo/carry_in.json`. |
| Grain and key | One row per duty interval; no declared key. The carry-in id is a digest of person, start, end and `EVIDENCE_REF`. |
| Required | No |
| Capabilities | executable |
| If missing or empty | No duty is pre-booked, so plans assume people are free. Dataset status gap `DATASET_NOT_PROBED` (live today), `DATASET_EMPTY_UNCERTIFIED` or `DATASET_FAILED`. A row for an unknown person is skipped with `CARRY_IN_PERSON_UNKNOWN` (effect `INFO`). |
| Time and cutoff | `START_UTC` and `END_UTC` are UTC instants; the interval is half-open. No cutoff column, no freshness check. |
| Read by | `focus/data/workforce.py` (`build_workforce`); `focus/engine/model.py` (locked ledger duty); `focus/validation/duty.py`; `focus/publication/dispatch.py`; `focus/web/serializers.py` (`myday`, `gantt`, `turnover`). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `PERSON_KEY` | text, matched exactly | Native person key. | Must be a known person; otherwise the row is skipped (`CARRY_IN_PERSON_UNKNOWN`). |
| `KIND` | text: `WORK`, `TRAVEL`, `TOOL`, `OTHER` or `WAIT` | Type of duty. | Kept as the duty kind in the ledger and the views. Any other value becomes `OTHER`. |
| `START_UTC` | UTC timestamp, ISO 8601 with offset | Start of the duty. | Start of the locked interval; the duty joins the person's shift window that contains its start. |
| `END_UTC` | UTC timestamp, ISO 8601 with offset | End of the duty, exclusive. | End of the locked interval. |
| `LINE_NUMBER` | integer or null | Aircraft line where the duty happens. | Origin for travel to the person's next job, and shown in the views. Null means an unknown origin, which costs a full transfer (`travel.unknown_origin_minutes`, 15 in the fixture policy). |
| `SOI` | text (not parsed) | Shop-order identifier (SOI) of the job the duty belongs to, if any. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `EVIDENCE_REF` | text | Reference to the source evidence (fixture: `FIXTURE-TRAINING-B102`). | Part of the carry-in id, and stored as evidence. |

**Rules and pitfalls.**
- One person, one duty at any instant, across shift and day labels. Planned work that overlaps carry-in fails validation (`PERSON_OVERLAP`, V6).
- Carry-in uses up the shift's regular and OT budget like other duty (V7). A carry-in that already overruns the budget is recorded, never rejected, and blocks further booking in that shift.
- A person with any duty in a pool, carry-in included, counts as active toward the activation cap (V8).
- Dispatch rechecks current carry-in: a crew member with overlapping carry-in gets `DUTY_CONFLICT`.
- Send timestamps with an explicit offset (for example `2026-10-26T10:15:00Z`). The parser rejects naive text, and admission stops.
