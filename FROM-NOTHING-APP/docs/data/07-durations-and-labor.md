# Durations, clock intervals and labor

These datasets cover how long rework takes and how much labor was spent. FOCUS sizes rework jobs (classes NC, PU and CI; the repo does not spell these out) from `duration_episodes`: completed, verified, comparable episodes, summarized as P50, P55, mean, P75 and P90 of active elapsed minutes, with P75 (the 75th percentile) used by default. `clock_observations` holds the raw clock intervals that such episodes are measured from, but today only the `duration-stats` report reads them. `actual_labor` and `labor_shift` hold aggregate labor, which is person effort rather than elapsed time; the planner does not read them. Jobs that are not rework take their duration from `flow_budget`, which another part of this reference covers.

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`clock_observations`](#clock_observations) | SUPPLEMENTAL | No | duration | Supplemental SQL path; producer table not installed | No (`duration-stats` report only) | Raw clock intervals per person and episode, with revisions and voids |
| [`duration_episodes`](#duration_episodes) | SUPPLEMENTAL | No | duration | None yet: reports NOT_PROBED | Yes | Completed comparable rework episodes behind the P75 |
| [`actual_labor`](#actual_labor) | SOURCE | No | performance | Reference SQL query | No (freshness check only) | Aggregate labor minutes charged per job |
| [`labor_shift`](#labor_shift) | SOURCE | No | performance | Reference SQL query | No (freshness check only) | Labor minutes per person, job and shift |

## `clock_observations`

**Purpose.** Raw clock-on and clock-off intervals per person, with source revisions and voids (the proposed `FOCUS_FN_ClockObservation` table). The union method turns one episode's intervals into active elapsed time (the union of everyone's intervals), person effort (each person's own union, summed) and cycle time (first clock-on to last clock-off). Today only the `duration-stats` command reads this dataset, to report those union results. The planner's rework estimates come from `duration_episodes`, not from these rows.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL. Live reader: `queries/durations/read_clock_observations.sql` over `WEB_APP.FOCUS_FN_ClockObservation`, a proposed producer table that is not installed. Fixtures read `fixtures/inputs/<scenario>/clock_observations.json`. |
| Grain and key | One row per clock event revision. The drafted table is unique on (RunId, SourceEventKey, SourceRevision); FOCUS keeps only the latest `SOURCE_REVISION` of each `SOURCE_EVENT_KEY`. |
| Required | No |
| Capabilities | duration |
| If missing or empty | Plans do not change. Empty without a certificate (every empty live read) gives `DATASET_EMPTY_UNCERTIFIED`; a capture error gives `DATASET_FAILED`. In live mode a database error, such as the missing producer table, is not a handled capture error: it stops the whole acquisition. |
| Time and cutoff | `START_UTC` and `END_UTC` are UTC instants. Nothing compares them with the cutoff, the as-of instant of a source run (one capture of all datasets): there is no cutoff column and no freshness check. The live query returns the rows stored under this scope's current head (`FOCUS_FN_SourceHead`, the last completed source run promoted for the scope), not rows of the run being captured. |
| Read by | `focus/data/duration_gauge.py` (`cli_duration_stats` and `episode_metrics`, the `duration-stats` command); `focus/data/fixture_reader.py` (live query) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SOURCE_EVENT_KEY` | text | The source's key for one clock event; it stays the same across corrections. | `duration-stats` keeps only the latest revision per key, so a correction replaces the original. |
| `LINE_NUMBER` | not parsed (drafted table: int) | The aircraft line of the clocked job. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `NATIVE_SOI` | not parsed (drafted table: nvarchar(256)) | The job the clock was charged to, as an SOI (the job's native identifier, `SHOP_ORDER_IDENTIFIER` in `master_task_list`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `PERSON_KEY` | text | The clock's owner: the person who did the work, not someone who entered or audited the clock. | Person effort is each owner's own union, summed; owners also give the crew-concurrency profile. |
| `EPISODE_KEY` | text | The rework episode the interval belongs to. | `duration-stats` groups intervals into episodes by this key. |
| `TEAM` | not parsed (drafted table: nvarchar(80), nullable) | Team on the clock record. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `REWORK_CLASS` | not parsed (drafted table: varchar(8), nullable) | The episode's rework class: NC, PU or CI. | Fetched and kept with the source run; nothing in the planner reads it today. The drafted SQL `queries/durations/build_observations.sql` (not installed) keeps only NC, PU and CI. |
| `START_UTC` | UTC timestamp, ISO 8601 with offset | Clock-on instant. | Interval start. An interval that ends at or before its start is counted as invalid and dropped. |
| `END_UTC` | UTC timestamp, ISO 8601 with offset | Clock-off instant. | Interval end. Intervals are half-open, so touching intervals merge. |
| `SOURCE_REVISION` | text | Revision of the event; a correction carries a higher revision. | The latest revision wins: whole-number revisions outrank text, then the larger value wins, as in SQL `TRY_CONVERT(bigint, …) DESC`. |
| `IS_VOIDED` | boolean | True when the event's latest revision voids it. | A voided latest revision removes the event. The value is tested for truthiness, so text such as `"false"` or `"0"` counts as voided. |

**Rules and pitfalls.**
- Union method (running maximum): sort by start; an interval that starts at or before the island's furthest end joins it. Gaps are kept, long clocks are not capped and short clocks are not dropped. Fixture episode `UNION-DEMO`: active 100, effort 110, cycle 110 minutes, one 10-minute gap, one voided clock excluded.
- Send every revision and void as raw rows; FOCUS picks the latest revision itself. Do not merge or sum intervals before delivery.
- Timestamps must carry an explicit offset. The live query selects `datetime2` columns, which arrive without one, and the parser rejects them (`NAIVE_DATETIME`), so `duration-stats` fails on live rows as written.
- These rows do not feed the planner. Filling `FOCUS_FN_ClockObservation` alone does not make a live P75 available (see `duration_episodes`).
- In live mode this query runs on every acquisition. `FOCUS_FN_ClockObservation` and `FOCUS_FN_SourceHead` must exist first, or the acquisition stops.

## `duration_episodes`

**Purpose.** One row per completed rework episode with its active elapsed minutes. FOCUS groups verified-complete episodes by the exact context (class, team, model, crew), then by (class, team), then by (class), and computes P50, P55, mean, P75 and P90 for each group, plus a 90% interval for P75. A rework job takes the selected statistic (default P75), rounded up to whole minutes, from the first group with enough current episodes; otherwise it is blocked with `DURATION_UNAVAILABLE` and never given a flat default. In the fixture, 30 verified episodes per class (elapsed 30 to 59 minutes) give P75 = 51.75, scheduled as 52 minutes.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live reader; reports `NOT_PROBED`. The drafted producer `queries/durations/build_observations.sql` writes `FOCUS_FN_DurationObservation` (not installed), whose columns differ from this projection (hashed episode key, an `Admitted` flag instead of `STATUS`), and no reader maps it. Fixtures read `fixtures/inputs/<scenario>/duration_episodes.json`. |
| Grain and key | One row per completed episode. Neither S04 nor the code enforces a key, and rows are not deduplicated: a repeated episode counts twice. |
| Required | No |
| Capabilities | duration |
| If missing or empty | Live: `NOT_PROBED`, gap `DATASET_NOT_PROBED`. With no episodes there are no statistics, so every job that `rework_classification` classes NC, PU or CI, and that has no sourced remaining work, is BLOCKED with `DURATION_UNAVAILABLE` in every plan; its successors get `PREDECESSOR_NOT_SCHEDULABLE`. |
| Time and cutoff | `COMPLETED_AT_UTC` (UTC with offset) sets each group's sample window and staleness: a group is stale when its newest admitted completion is more than `duration.stale_after_days` (365) days before the cutoff. No cutoff column; FOCUS does not drop episodes completed after the cutoff. |
| Read by | `focus/data/duration_gauge.py` (`compute_statistics`), called from `focus/data/snapshot_builder.py` and the `duration-stats` command; estimates in `focus/data/durations.py` (`resolve`) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `EPISODE_KEY` | text | ID of the completed episode. | Part of each statistic's source hash. Not used to deduplicate. |
| `REWORK_CLASS` | text (NC, PU or CI) | The episode's rework class. | First level of every group. A job looks up its own class, taken from `rework_classification`. |
| `TEAM` | text, optional | Team that did the episode. | Second level of grouping. It must equal the job's `master_task_list.TEAM` text to match. |
| `MODEL` | text, optional | Aircraft model of the episode. | Exact group only; it must equal the job's `master_task_list.MINOR_MODEL_IDENT`. Blank means the episode joins no exact group. |
| `ELAPSED_MINUTES` | decimal (number or decimal text, read exactly) | Active elapsed minutes of the episode: the union of everyone's clock intervals. | The measured value behind every percentile and the mean. Unreadable gives `ELAPSED_INVALID`; 0 or less gives `ELAPSED_NONPOSITIVE`. |
| `PERSON_EFFORT_MINUTES` | decimal, optional | Person effort: each person's own clock union, summed. | Kept as a descriptive mean only; never used for the estimate. |
| `CYCLE_MINUTES` | not parsed (fixture: decimal text) | First clock-on to last clock-off. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `STATUS` | text | Completion status. Fixture values: `verified_complete`, `reopened`, `unverified`. | Only the exact lowercase `verified_complete` is admitted. Other rows are censored and counted as `STATUS_<VALUE>`, for example `STATUS_REOPENED`. |
| `CREW` | integer, optional | Crew size during the episode. | Exact group only; it must equal the job's crew demand (`flow_budget.TOTAL_MECHANICS`). Blank means the episode joins no exact group. |
| `COMPLETED_AT_UTC` | UTC timestamp, ISO 8601 with offset | The verified completion instant. | Required for admission (`COMPLETED_AT_MISSING` otherwise); sets the sample window and staleness. |
| `SOURCE` | not parsed (fixture: `fixture`) | Source label of the row; its values are not defined in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Measure active elapsed time (the job union), not person effort. FOCUS never divides a person-effort figure by crew.
- Send reopened and unverified episodes too. FOCUS censors them with reasons, so the sample is not survivors only.
- Send one row per episode, not per person or per clock; duplicates count twice.
- The exact group needs team, model and crew to match the job's master `TEAM`, `MINOR_MODEL_IDENT` and `TOTAL_MECHANICS`. A group needs at least 30 admitted episodes (`duration.minimum_fixture_sample`) and a newest completion within 365 days of the cutoff, or FOCUS tries the next group.
- Timestamps need an explicit offset: a `COMPLETED_AT_UTC` without one raises `NAIVE_DATETIME` and stops the acquisition. Leave out episodes completed after the cutoff, because FOCUS does not.
- An in-progress rework job with sourced remaining minutes (`work_progress`) keeps those minutes instead of a fresh P75.

## `actual_labor`

**Purpose.** Aggregate labor per job: how many labor records, and how many minutes and hours, were charged. The docs class it as aggregate person effort with no raw intervals, so it cannot measure elapsed time or calibrate rework durations. Nothing in the planner reads its rows today; only `LOAD_TIMESTAMP` is checked for freshness.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_ACTUAL_LABOR`, query `queries/source_inputs/actual_labor.sql` |
| Grain and key | One row per job on a line; key `SHOP_ORDER_IDENTIFIER` + `LINE_NUMBER` (S04 primary key; S04 is the manual's section of verified source table contracts). |
| Required | No |
| Capabilities | performance (registry tag; no code uses it) |
| If missing or empty | Capture error: status FAILED, gap `DATASET_FAILED`. Empty without a certificate: gap `DATASET_EMPTY_UNCERTIFIED`. Plans do not change. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP` (stored without a zone; read in the dataset's declared source timezone), 36 h limit (`admission.max_source_age_hours`). A stale table gives `SOURCE_STALE` and an unreadable clock `SOURCE_CLOCK_UNKNOWN`; both fail EXECUTABLE plans (the plans meant for dispatch) even though nothing else reads the table. No cutoff column. |
| Read by | `focus/data/freshness.py` (`LOAD_TIMESTAMP` only) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SHOP_ORDER_IDENTIFIER` | varchar(30), not null | The job's SOI. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LINE_NUMBER` | int, not null | The aircraft line number. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SHOP_ORDER_STATUS` | varchar(20), nullable | The job's shop-order status as this table carries it. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LABOR_RECORDS` | int, nullable | Number of labor records behind the totals, going by the column name; the code and docs define it no further. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ACTUAL_MINUTES_BURNED` | decimal(18,2), nullable | Total labor minutes charged to the job: person effort summed over people, not elapsed time. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ACTUAL_HOURS_BURNED` | decimal(18,2), nullable | Labor hours charged to the job. In the synthetic fixture it equals the minutes divided by 60, to 2 decimals. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row (no time zone stored). | Freshness check only (see Time and cutoff). |

**Rules and pitfalls.**
- Never use it as elapsed rework time: minutes summed across people are person effort, not elapsed minutes.
- It has no intervals, corrections or voids, so it cannot feed the union method or `duration_episodes` (policy open decision OD-02).
- A stale load still fails every executable plan, because the policy checks its freshness.

## `labor_shift`

**Purpose.** Labor minutes per person, per job, per shift and day, with two timestamps named first-on and last-off. The docs call it shift-level labor observation, usable only once the owner defines its time and person-effort meaning. Nothing in the planner reads its rows today; only `LOAD_TIMESTAMP` is checked for freshness.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_LABOR_SHIFT`, query `queries/source_inputs/labor_shift.sql` |
| Grain and key | One row per production day, shift, line, SOI and person; key `PRODUCTION_DAY`, `SHIFT_NUMBER`, `LINE_NUMBER`, `SOI`, `BEMS_ID` (S04 primary key). |
| Required | No |
| Capabilities | performance (registry tag; no code uses it) |
| If missing or empty | Capture error: status FAILED, gap `DATASET_FAILED`. Empty without a certificate: gap `DATASET_EMPTY_UNCERTIFIED`. Plans do not change. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP`, 36 h limit, as for `actual_labor` (`SOURCE_STALE` and `SOURCE_CLOCK_UNKNOWN` fail EXECUTABLE plans). `FIRST_ON_ET` and `LAST_OFF_ET` are `datetime2(0)` values with no stored zone, and no doc says which zone they use. No cutoff column. |
| Read by | `focus/data/freshness.py` (`LOAD_TIMESTAMP` only) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `PRODUCTION_DAY` | date, not null | The production day of the labor (part of the key). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SHIFT_NUMBER` | tinyint, not null | The shift of the labor; the shift policy defines shifts 1, 2 and 3 (part of the key). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LINE_NUMBER` | int, not null | The aircraft line number. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SOI` | varchar(40), not null | The job's SOI. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `BEMS_ID` | varchar(10), not null | The person's ID, the native person key used across the workforce datasets. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `MINUTES` | decimal(9,2), not null | Labor minutes for this person, job and shift. The docs class it as person effort, not elapsed time. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `FIRST_ON_ET` | datetime2(0), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LAST_OFF_ET` | datetime2(0), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row. | Freshness check only. |

**Rules and pitfalls.**
- Treat `MINUTES` as person effort, not elapsed time.
- The owner must define the table's time and person-effort meaning before any use, including what `FIRST_ON_ET` and `LAST_OFF_ET` hold and in which time zone.
- A stale load still fails every executable plan, because the policy checks its freshness.
