# Parts shortages and receipts

These datasets tell FOCUS which parts are short, which jobs need them, when they are expected and whether they have physically arrived. `etracs_active` lists every open shortage with its used-on job and its ECD (the predicted date for the part; the repo does not spell out the acronym). `part_events` adds sourced receipt, deletion, reopen and timed ETA (expected arrival instant) events for each shortage. FOCUS turns each linked shortage into a zero-work PART release milestone that gates the start of the used-on job and of every job up its same-line parent chain to the root; later jobs follow through the job graph. Forecast plans may place that work after the ECD or ETA floor, labelled conditional; executable plans need a confirmed receipt. `etracs_history` holds closed shortages, and nothing in the planner reads it today.

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`etracs_active`](#etracs_active) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Every open shortage, its used-on job and ECD |
| [`etracs_history`](#etracs_history) | SOURCE | No | forecast | Reference SQL query | No (freshness check only) | Closed shortages with their actual closure dates |
| [`part_events`](#part_events) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Receipt, deletion, reopen and ETA events per shortage |

## `etracs_active`

**Purpose.** The current open material shortages. FOCUS keeps every row, including an unknown ECD and a blank `USED_ON`, and links each one to the exact job on the same line. A linked shortage becomes a PART release milestone (`R:PART:<RECORD_ID>`) that gates the used-on job and its parent chain to the root. Without a receipt in `part_events`, that work is forecast-only after the ECD or ETA floor, or blocked in every plan when neither exists.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_ETRACS_ACTIVE`, query `queries/source_inputs/etracs_active.sql` (explicit column list, no filter) |
| Grain and key | One row per shortage record; key `RECORD_ID` (S04 primary key; S04 is the manual's section of verified source table contracts). |
| Required | Yes: a capture error (for example `SCHEMA_DRIFT`) rejects the source run (one capture of all datasets at one cutoff) with `MANDATORY_DATASET_FAILED`. An empty result does not reject the run. |
| Capabilities | forecast, executable |
| If missing or empty | Capture error: run `REJECTED`, `MANDATORY_DATASET_FAILED`. Empty without a certificate (every empty live read): gap `DATASET_EMPTY_UNCERTIFIED`, labelled BLOCKS_EXECUTABLE but not enforced by the plan validator, and FOCUS plans as if no part were short. Per row: a blank or unmatched `USED_ON` gives shortage status `UNRESOLVED_USED_ON`, and every open job on that line gets `MATERIAL_SCOPE_UNRESOLVED`; no ECD and no receipt gives `PART_RELEASE_UNKNOWN` (blocked in every plan); an ECD without a receipt gives `PART_NOT_RECEIVED` (executable blocked, forecast conditional). |
| Time and cutoff | Row clock `LOAD_TIMESTAMP` (stored without a zone; read in the dataset's declared source timezone), 36 h limit to the cutoff, the source run's as-of instant (`admission.max_source_age_hours`). Too old gives `SOURCE_STALE`, unreadable gives `SOURCE_CLOCK_UNKNOWN`; both fail EXECUTABLE plans. `ETRAC_ECD` is a local calendar date in the etracs_active source timezone (`FOCUS_SOURCE_TIMEZONE_MAP_JSON` live, the producer receipt in fixtures). No cutoff column. |
| Read by | `focus/data/parts.py` (`build_parts`), `focus/data/graph.py`, `focus/data/freshness.py`; the release milestones are then read by `focus/engine/model.py`, `focus/validation/plan.py` (parts check V9), `focus/publication/dispatch.py`, `focus/performance/disruption.py` and `focus/web/serializers.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `RECORD_ID` | int, not null | The shortage record's ID. | Key: names the release `R:PART:<RECORD_ID>` and links `part_events` rows with the same ID. |
| `LINE_NUMBER` | int, not null | The aircraft line of the shortage. | With `USED_ON`, finds the job. An unresolved shortage flags every open job on this line. |
| `MINOR_MODEL_IDENT` | varchar(10), nullable | The aircraft's minor model code (same name as the model column in `master_task_list`). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `USED_ON` | varchar(50), nullable | The job the part is used on, as an SOI (the job's native identifier, `SHOP_ORDER_IDENTIFIER` in `master_task_list`). | Exact text match to a census job on the same line, with no trimming. Blank gives `UNRESOLVED_USED_ON`; no match gives `USED_ON_NOT_IN_CENSUS`. |
| `ETRAC_ECD` | date, nullable | The ECD: the predicted date for the part, date only. A forecast, never a receipt. | Without a receipt or ETA, the forecast floor is the next local midnight after the ECD (end of the local ECD date), status CONDITIONAL. A floor at or before the cutoff adds `PAST_ECD_NOT_RECEIPT`. |
| `TEAM` | varchar(50), nullable | Team recorded on the shortage. The code and docs do not define which team it is. | Copied to the shortage record for the shortages view; not used for scheduling. |
| `REASON_CODE` | varchar(10), nullable | Shortage reason code (fixture: `LP`). The code list is not defined in code or docs. | Copied to the shortage record for display; not used for scheduling. |
| `ETRAC_CREATED_DT` | date, nullable | Date the shortage record was created. | Copied to the shortage record for display (left empty when `USED_ON` is not in the census). |
| `ITEM_NUMBER` | varchar(50), nullable | The part (item) number. | Used only in the release label "Part <ITEM_NUMBER> for <job>"; falls back to `RECORD_ID`. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row (no time zone stored). | Freshness check only (see Time and cutoff). |

**Rules and pitfalls.**
- Send every active shortage, including a null ECD and a blank `USED_ON`. FOCUS never drops them; an unlinked shortage holds back executable work on the whole line (`MATERIAL_SCOPE_UNRESOLVED`).
- `USED_ON` must equal the master `SHOP_ORDER_IDENTIFIER` exactly, on the same line. FOCUS does not trim; the readiness query `queries/parts/shortage_profile.sql` does, so its counts can differ.
- An ECD is never a receipt, even after it has passed. Only a `RECEIVED` or `DELETED` event in `part_events` confirms a part.
- Declare a source timezone for `etracs_active` (or `*`). Without one, every dated shortage becomes UNKNOWN (`SOURCE_TIMEZONE_MISSING`) and blocks its jobs, and the freshness clock cannot be read.
- Several shortages on one job each add a release. The latest floor binds, and any unknown release blocks the job.
- In the validator's parts check (V9) for executable plans, and at dispatch, a shortage whose status is `UNKNOWN_ECD` or `REOPENED` blocks every job it affects (`SHORTAGE_BLOCKED` at dispatch).

## `etracs_history`

**Purpose.** Closed (historical) shortages with the ECD they had and their actual closure date. The docs describe it as closure context that must not confuse a predicted ECD with an actual receipt. Nothing in the planner reads its rows today; only `LOAD_TIMESTAMP` is checked for freshness.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_ETRACS_HISTORY`, query `queries/source_inputs/etracs_history.sql` |
| Grain and key | One row per shortage record; key `RECORD_ID` (S04 primary key). |
| Required | No |
| Capabilities | forecast (registry tag; no code uses it) |
| If missing or empty | Capture error: status FAILED, gap `DATASET_FAILED`. Empty without a certificate: gap `DATASET_EMPTY_UNCERTIFIED`. Plans do not change. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP`, 36 h limit, as for `etracs_active`. A stale table gives `SOURCE_STALE`, which fails EXECUTABLE plans even though nothing else reads it. No cutoff column. |
| Read by | `focus/data/freshness.py` (`LOAD_TIMESTAMP` only) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `RECORD_ID` | int, not null | The shortage record's ID. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LINE_NUMBER` | int, not null | The aircraft line of the shortage. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `USED_ON` | varchar(50), nullable | The job (SOI) the part was used on. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `PARENT_SOI` | varchar(50), nullable | Meaning not established in code or docs; the docs only list "parent SOI" as closure context. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `PRIMARY_JOB` | varchar(50), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ETRAC_ECD` | date, nullable | The ECD (predicted date) the shortage had. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ACTUAL_CLOSURE_DATE` | date, nullable | Date the shortage was actually closed. The manual says it alone does not prove current inventory was received. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row. | Freshness check only. |

**Rules and pitfalls.**
- It is not a receipt source. A closure date does not prove a part is physically ready now; receipts must come through `part_events`.
- Although no rows are read, the policy checks its freshness: a load more than 36 h old blocks every executable plan.
- `docs/integration/SCHEDULING_CONSTRAINTS.md` (C22) and `docs/execution/SQL_SCHEMA_LEDGER.md` list it as a parts input, but no code reads its rows.

## `part_events`

**Purpose.** Sourced events for each shortage: physical receipt (`RECEIVED`), deletion or cancellation (`DELETED`), reopen (`REOPENED`) and a timed ETA (`ETA`, an expected arrival instant). Only `RECEIVED` or `DELETED` makes a shortage CONFIRMED, which executable plans need. A `REOPENED` after a receipt revokes it, and an `ETA` gives a minute-precise conditional floor for forecasts.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no reference object and no live producer. The live reader reports `NOT_PROBED`; fixtures read `fixtures/inputs/<scenario>/part_events.json`. |
| Grain and key | One row per event; a shortage can have many. Neither S04 nor the code enforces a key. Events join `etracs_active` on `RECORD_ID` and are ordered by `EFFECTIVE_AT_UTC`. |
| Required | No |
| Capabilities | executable |
| If missing or empty | Live: `NOT_PROBED`, gap `DATASET_NOT_PROBED`. With no events nothing can be CONFIRMED: shortages with an ECD stay CONDITIONAL (`PART_NOT_RECEIVED`, executable blocked) and those without stay UNKNOWN (`PART_RELEASE_UNKNOWN`, blocked in every plan). In fixtures, an `OBSERVED_AT_UTC` without an offset fails the capture (`DATASET_FAILED`). |
| Time and cutoff | Cutoff column `OBSERVED_AT_UTC`: the fixture reader hides events observed after the cutoff; the live reader applies no filter. `EFFECTIVE_AT_UTC` is when the event took effect and drives all the logic. Both are UTC with an explicit offset. Fixture example: the receipt for shortage 50501 is effective 2026-10-27T18:15Z and observed 18:20Z, after the 2026-10-26T10:00Z cutoff, so the shortage stays CONDITIONAL at its ECD floor, 2026-10-28T04:00Z. |
| Read by | `focus/data/parts.py` (`build_parts`); `focus/data/fixture_reader.py` (cutoff filter) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `RECORD_ID` | integer | The shortage the event belongs to (`etracs_active.RECORD_ID`). | Groups events per shortage; events for records not in `etracs_active` have no effect. |
| `EVENT` | text: `RECEIVED`, `DELETED`, `REOPENED` or `ETA` | The event type. | `RECEIVED` or `DELETED` confirms; `REOPENED` after a receipt revokes it; `ETA` sets a conditional floor. Any other value is ignored without a note. |
| `EFFECTIVE_AT_UTC` | UTC timestamp, ISO 8601 with offset | When the event took effect; for `ETA`, the expected arrival instant. | Orders the events. A receipt's ready time and an ETA floor are this instant rounded up to the minute. |
| `OBSERVED_AT_UTC` | UTC timestamp, ISO 8601 with offset | When the source observed or recorded the event. | Fixture reader only: hides rows observed after the cutoff. The parts logic does not read it. |
| `EVIDENCE_REF` | text, optional | Source reference for the event (fixture: `FIXTURE-RCV-50501`). | Quoted in the reopen reason; otherwise it only breaks ties when sorting. |

**Rules and pitfalls.**
- A past ECD is not a receipt. Only `RECEIVED` or `DELETED` confirms; `DELETED` closes the shortage (`SHORTAGE_DELETED`) and also confirms it.
- `REOPENED` counts only after a receipt; on its own it is ignored. After a reopen the shortage status is `REOPENED`, and its floor is the later of the reopen time and the ETA or ECD floor.
- The ETA with the latest `EFFECTIVE_AT_UTC` wins, not the most recently observed one, so a revised earlier ETA does not replace a later one.
- FOCUS does not compare `EFFECTIVE_AT_UTC` with the cutoff: a `RECEIVED` effective in the future becomes a confirmed release at that future time. Only the fixture reader applies the cutoff, so a live producer must apply it.
- Timestamps need an explicit offset (for example a trailing `Z`). An `EFFECTIVE_AT_UTC` without one raises `NAIVE_DATETIME` and stops the acquisition.
- Dispatch rechecks at action time: a release confirmed for a time after "now" still gives `PART_NOT_RECEIVED`.
