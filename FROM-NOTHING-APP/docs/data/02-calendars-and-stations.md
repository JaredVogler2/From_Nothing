# Production calendars and control-station windows

These two datasets say when each aircraft line can work and when work may start at each control station (CS).
`cday_op_plan` gives every line its own calendar: one status per date and hard first and last dates. FOCUS places
shifts only on eligible days of the job's own line, with no weekday fallback. `cs_windows` gives, for each line and
station, the earliest date work there may start and the station gate date, either as resolved dates or as signed
offsets from positions on the line calendar. Each dataset must carry exactly one active load (generation, `LOAD_ID`)
per line, or per line and station. Station facts come only from the same source run as the rest of the snapshot, and
the validator checks them against that run's raw rows.

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`cday_op_plan`](#cday_op_plan) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Per-line working, weekend and non-working days, with hard date bounds. |
| [`cs_windows`](#cs_windows) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Per line and control station: the earliest start date and the gate date. |

## `cday_op_plan`

**Purpose.** Each line's production calendar: one row per date with status `WORKING`, `WEEKEND` or `NONWORKING`, from
one active load. FOCUS admits one calendar per line, and its first and last dates are hard bounds. Shifts 1 and 2
(S1, S2) run only on `WORKING` days. Shift 3 (S3, starting at 23:00) on a non-working day is allowed only when the
next date on the same line is `WORKING`. There is no weekday fallback and no extension past the last date.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_CDAY_OP_PLAN`, query `queries/source_inputs/cday_op_plan.sql` (`WHERE IS_ACTIVE=1`). Profile query: `queries/calendars/calendar_generations.sql`. |
| Grain and key | One row per line per date per load. No S04 primary key. The code groups active rows by `LINE_NUMBER` and requires one `LOAD_ID` per line; within that load, one status per date. |
| Required | Yes. A failed capture rejects the run (`MANDATORY_DATASET_FAILED`). An uncertified empty result does not reject it; it adds `DATASET_EMPTY_UNCERTIFIED` and every line is then `CALENDAR_MISSING`. |
| Capabilities | forecast, executable |
| If missing or empty | Line gaps that block every job on the line (effect `BLOCKS_ALL`): `CALENDAR_MISSING` (no active rows for the line) and `CALENDAR_GENERATION_CONFLICT` (more than one active `LOAD_ID`). The calendar is still admitted, with an information gap, for `CALENDAR_HOLES` (missing dates inside the bounds) and `CALENDAR_DAY_CONFLICT` (one date with two statuses); those dates are unusable. `GENERATION_SELECTED_BY_POLICY` appears only with the selector `MAX_LOAD_ID`. No admitted calendar on any line: `HORIZON_UNKNOWN`, and no worker pools are built. Engine and validator decisions: `CALENDAR_DATE_UNKNOWN`, `NONWORKING_DAY`, `S3_NONWORKING`, `S3_NEXT_DAY_UNKNOWN`, `CALENDAR_NOT_ADMITTED`, `OUTSIDE_CALENDAR_BOUNDS` (check V3); dispatch: `CALENDAR_CHANGED`. |
| Time and cutoff | Row clock `LOAD_TS`, read in the dataset's declared zone; stale after 240 h (`SOURCE_STALE`). No cutoff column. `CALENDAR_DATE` is compared with each shift's local start date in policy `shift.timezone`; shift 3 belongs to the date it starts (23:00). Without a zone policy for this dataset the snapshot carries `SOURCE_TIMEZONE_POLICY_MISSING`; the calendar is still admitted, but `LOAD_TS` cannot be read (`SOURCE_CLOCK_UNKNOWN`). |
| Read by | `focus/calendar.py` (`admit_line_calendars`, `CalendarService`), `focus/data/snapshot_builder.py`, `focus/data/cs_windows.py`, `focus/data/freshness.py`. Admitted calendars are then read by `focus/engine/model.py`, `focus/validation/input.py`, `focus/validation/plan.py` and `focus/publication/dispatch.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `LINE_NUMBER` | int, not null | The aircraft line number. | Calendars are admitted only for lines in the census, and each line uses only its own rows. |
| `LINE` | nvarchar(10), nullable | Meaning not established in code or docs (the fixture uses `L1301` for line 1301). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CALENDAR_DATE` | date, not null | The calendar date the row describes. | Builds the line's day map. The first and last dates are the line's hard bounds, and missing dates between them are holes (`CALENDAR_HOLES`). |
| `POSITION` | nvarchar(10), nullable | A position label the calendar gives that date; the repo does not define it further (the fixture uses `P744`). | Kept on each day. Used only when CS dates are offsets: the first day on the line whose position equals a CS reference position is that offset's anchor. |
| `DAY_STATUS` | nvarchar(15), nullable | The type of day: `WORKING`, `WEEKEND` or `NONWORKING`. | Policies `calendar.working_statuses` and `calendar.nonworking_statuses` admit these values; S1 and S2 need `WORKING`, and S3 on a `WEEKEND` or `NONWORKING` day needs the next day `WORKING`. Any other value, including null, becomes `UNKNOWN`: no shift can use that date, and no readiness gap names it. |
| `LAST_MODIFIED_DT` | date, not null | The source's last-modified date for the row. | Copied onto each calendar day in the snapshot; nothing in the planner reads it today. |
| `LOAD_ID` | int, not null | The calendar load (generation) the row belongs to. | A line may have only one active `LOAD_ID` (policy `calendar.generation_selector` = `SINGLE_ACTIVE_LOAD`), or it is `CALENDAR_GENERATION_CONFLICT`. The load is part of the calendar's revision hash. |
| `LOAD_TS` | datetime2(0), not null | When the load was written. | Row clock for freshness (240 h limit). |
| `IS_ACTIVE` | bit, not null | The source's active flag. | Inactive rows are ignored. The reference query already returns only `IS_ACTIVE=1`. |

**Rules and pitfalls.**
- Send a dense run of dates for every census line, covering the whole planning horizon. FOCUS never adds days, so work
  that does not fit before a line's last date cannot be placed.
- Keep exactly one active `LOAD_ID` per line. Switch loads by changing `IS_ACTIVE` in one step: two active loads block
  the whole line.
- Use only `WORKING`, `WEEKEND` and `NONWORKING`. Any other text, or null, silently makes the date unusable.
- FOCUS reads the status, never the weekday: a Saturday can be `WORKING` and a weekday holiday `NONWORKING`. `WEEKEND`
  and `NONWORKING` behave the same.
- The shift 3 exception needs the next date on the same line. On the calendar's last date it cannot be proven, so S3
  there is refused when the date is not `WORKING` (engine `S3_NEXT_DAY_UNKNOWN`, validator `S3_NONWORKING`).
- The snapshot's horizon, its shift list and its worker pools run from the earliest to the latest admitted date over
  all lines.

## `cs_windows`

**Purpose.** For each line and control station: the earliest date work there may start (the load date) and the
station gate date (the unload date), as resolved dates or as signed offsets from calendar positions. FOCUS builds
station facts for every line and station where open jobs sit, plus station 744 on every line for the planning delivery
proxy. The earliest date is a hard start floor; the gate date is recognition pressure, not a deadline. The rows must come
from the same source run as the snapshot: scheduling never re-queries them, and the validator checks each placed job's
station fact against this run's raw row.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_CS_WINDOWS`, query `queries/source_inputs/cs_windows.sql` (`WHERE IS_ACTIVE=1`). Profile query: `queries/calendars/cs_window_grain.sql`. |
| Grain and key | One active row per line and station within one load. No S04 primary key. The code indexes active rows by `LINE_NUMBER` + `CONTROL_STATION`; all active rows for one pair must share one `LOAD_ID` and agree on dates, offsets, reference positions and `MBU`. |
| Required | Yes. A failed capture rejects the run (`MANDATORY_DATASET_FAILED`). If the producer receipt declares `active_load_ids` per line, a row with another `LOAD_ID` also rejects it (`CS_GENERATION_MISMATCH`). An uncertified empty result does not reject the run; it adds `DATASET_EMPTY_UNCERTIFIED`, and every open job is then blocked (`CS_WINDOW_GAP`). |
| Capabilities | forecast, executable |
| If missing or empty | Each needed line and station without an admitted fact blocks its open jobs (policy `cs.required_for_scheduling`) with one of: `CS_WINDOW_GAP`, `CS_STATION_UNPARSEABLE`, `CS_LOAD_CONFLICT`, `CS_ROW_CONFLICT`, `CS_EARLIEST_MISSING`, `CS_ANCHOR_UNRESOLVED`, `CS_CONVERSION_POLICY_MISSING`, `CS_SOURCE_TIMEZONE_MISSING`. No station 744 fact: the line's economic due is unknown (`DUE_UNKNOWN`). In live mode every job also gets `CS_DATE_SEMANTICS_UNCONFIRMED` (withheld from executable plans) until open decision OD-05 is settled. Validator: `CS_FACT_MISSING`, `CS_FACT_NOT_ADMITTED`, `CS_FACT_FOREIGN_RUN`, `CS_RAW_ROW_MISSING`, `CS_RAW_FIELD_MISMATCH` (check E_CS), `BEFORE_CS_EARLIEST`, `AFTER_CS_HARD_LATEST` (check V4); dispatch: `CS_NOT_ADMITTED`. |
| Time and cutoff | Row clock `LOAD_TS`, stale after 240 h (`SOURCE_STALE`). No cutoff column. Dates become instants at local midnight in this dataset's declared zone (policy `cs.date_instant` = `LOCAL_MIDNIGHT`). With no zone the snapshot carries `SOURCE_TIMEZONE_POLICY_MISSING` and every station gets `CS_SOURCE_TIMEZONE_MISSING`. |
| Read by | `focus/data/cs_windows.py`, `focus/data/snapshot_builder.py`, `focus/data/acquisition.py`, `focus/data/freshness.py`. Station facts are then read by `focus/engine/model.py`, `focus/validation/plan.py`, `focus/publication/serializer.py`, `focus/publication/dispatch.py`, `focus/economics/repository.py` and `focus/analytics/twin.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `MBU` | nvarchar(15), nullable | Meaning not established in code or docs; the repo never expands it (the fixture uses `DEMO`). | Kept on the station facts and compared between duplicate active rows: rows that differ in it conflict (`CS_ROW_CONFLICT`). |
| `CONTROL_STATION` | int, not null | The control station number. | Matched to each job's `CURRENT_CONTROL_STATION`, read as an integer. Station 744 (policy `due.proxy_station`) is also looked up on every line. |
| `LINE_NUMBER` | int, not null | The aircraft line number. | Matched to census lines. A missing pair is never filled from another line, another station or the line calendar. |
| `LOAD_REF_POS` | nvarchar(5), nullable | The calendar position the load date is counted from. | Used only when policy `cs.date_mode` is `OFFSET_FROM_REFERENCE`: the first `cday_op_plan` day on the line with this `POSITION` is the anchor (`CS_ANCHOR_UNRESOLVED` if none). Always kept, published in the plan's CS lineage and checked by the validator (`CS_RAW_FIELD_MISMATCH`). |
| `LOAD_OFFSET` | int, nullable | Signed number of days from the load reference position. | In offset mode the days are working days or calendar days, per policy `cs.offset_day_basis` (unset today, which gives `CS_CONVERSION_POLICY_MISSING`); null counts as 0. The sign is always kept. |
| `CS_OP_LOAD` | date, nullable | The station's load date, already resolved. | In `RESOLVED` mode (current policy) this is the earliest date work at the station may start: local midnight of this date is a start floor (validator `BEFORE_CS_EARLIEST`). Null makes the station a gap (`CS_EARLIEST_MISSING`), which blocks its jobs. |
| `UNLOAD_REF_POS` | nvarchar(5), nullable | The calendar position the unload date is counted from. | Offset mode only, as for `LOAD_REF_POS`; always kept and checked. |
| `UNLOAD_OFFSET` | int, nullable | Signed number of days from the unload reference position. | Offset mode only, as for `LOAD_OFFSET`; always kept and checked. |
| `CS_OP_UNLOAD` | date, nullable | The station's unload date, already resolved. | Becomes the station gate date, used for behind-station recognition (frozen goal points and the twin), not as a deadline (policy `cs.hard_latest` = `NONE`). For station 744 it is also the planning delivery proxy: gate + 2 days (`gate.proxy_grace_days`, basis `BASELINE_PROXY`), used as the economic due date; null leaves no gate and no due. |
| `LAST_MODIFIED_DT` | date, not null | The source's last-modified date for the row. | Parsed on every row and kept on the raw CS row; nothing in the planner reads it today. A null or malformed value stops admission with an error instead of a coded gap. |
| `LOAD_ID` | int, not null | The load (generation) the row belongs to. | Two active loads for one line and station conflict (`CS_LOAD_CONFLICT`); FOCUS never takes the highest. The load is recorded on the station fact, and the validator requires an active raw row with that load (`CS_RAW_ROW_MISSING`). |
| `LOAD_TS` | datetime2(0), not null | When the load was written. | Row clock for freshness (240 h limit). |
| `IS_ACTIVE` | bit, not null | The source's active flag. | Only active rows are used; an inactive row never fills a gap. The reference query already returns only `IS_ACTIVE=1`. |

**Rules and pitfalls.**
- Capture CS windows in the same run as the census, and never re-query or attach another load later. The validator
  rejects a station fact from another run (`CS_FACT_FOREIGN_RUN`).
- Keep one active `LOAD_ID` per line and station. Duplicate active rows must agree on both dates, both offsets, both
  reference positions and `MBU`. FOCUS never takes MAX(load) and never widens a window across stations.
- Every line needs an active station 744 row. Without it the line's delivery proxy, and so its due date and delay cost,
  is unknown (`DUE_UNKNOWN`).
- The current policy reads only the resolved dates (`cs.date_mode` = `RESOLVED`), so fill `CS_OP_LOAD`. If the source
  supplies offsets only, the owner must approve `OFFSET_FROM_REFERENCE` with a day basis first (OD-05). Keep offset
  signs as they are.
- The unload date is a gate, not a deadline. A missed or past gate does not stop remaining work unless policy
  `cs.hard_latest` changes; past and day-zero gates are kept.
- Station numbers must match the master list as text too: the validator looks up raw rows by the integer printed as
  text, so `CURRENT_CONTROL_STATION` `0744` fails every plan that places the job (`CS_RAW_ROW_MISSING`).
