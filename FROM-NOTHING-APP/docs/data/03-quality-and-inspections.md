# Quality inspections and hold points

These four datasets add quality (QA) work to each job. `buyoff_plan` says which inspections a job needs. `inspection_holds` says where each in-process (IP) inspection sits inside the production work and how many inspector minutes it takes. `inspection_standards` sizes the parallel buyoffs (inspection sign-offs that run beside the final inspection), and `inspection_counts` only cross-checks the buyoff plan. From them FOCUS builds, per job: WORK segments split at the IP holds, IP_QA phases, one FINAL_QA phase, parallel OTHER_BUYOFF branches, and a zero-work AND RELEASE milestone that every successor waits for. The IP total and the final inspection are 45 minutes each by policy (`qa.total_ip_minutes`, `qa.final_minutes`), not by data, and every QA phase needs one qualified inspector from the QA pool. FOCUS makes forecast plans, which may rely on conditional facts, and executable plans, which are meant for dispatch and may not.

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`buyoff_plan`](#buyoff_plan) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Which inspections and buyoffs each job requires |
| [`inspection_counts`](#inspection_counts) | SOURCE | No | forecast | Reference SQL query | No (cross-check notes only) | Second count of inspections, compared with the buyoff plan |
| [`inspection_standards`](#inspection_standards) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Minutes for each parallel buyoff kind |
| [`inspection_holds`](#inspection_holds) | SUPPLEMENTAL | No | executable | None yet: reports NOT_PROBED | Yes | Where each IP hold sits and its inspector minutes |

## `buyoff_plan`

**Purpose.** The buyoff plan lists, per job, which inspections are required: final quality, in-process quality, and five kinds of parallel buyoff. FOCUS turns any count above zero into phases: a 45-minute FINAL_QA after all production work, IP_QA holds (placed only from `inspection_holds`), and one OTHER_BUYOFF branch per other required kind. All of them must finish before the job's AND RELEASE, so every successor waits for them. A job with no row gets no QA phases at all.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_BUYOFF_PLAN`, query `queries/source_inputs/buyoff_plan.sql` (explicit column list, no filter) |
| Grain and key | One row per job on a line; key `SHOP_ORDER_IDENTIFIER` + `LINE_NUMBER` (S04 primary key; S04 is the manual's section of verified source table contracts). FOCUS matches rows to census jobs on the same pair. |
| Required | Yes: a capture error (for example `SCHEMA_DRIFT`) rejects the source run (one capture of all datasets at one cutoff) with `MANDATORY_DATASET_FAILED`. An empty result does not reject the run. |
| Capabilities | forecast, executable |
| If missing or empty | Capture error: run `REJECTED`, `MANDATORY_DATASET_FAILED`. Empty without a certificate (every empty live read): readiness gap `DATASET_EMPTY_UNCERTIFIED`, labelled BLOCKS_EXECUTABLE but not enforced by the plan validator; every job is then planned with no QA phases. A census job with no row is also planned without QA, and nothing flags it. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP` (stored without a zone; read in the dataset's declared source timezone). The newest value must be within 36 h of the cutoff, the source run's as-of instant (`admission.max_source_age_hours`), else `SOURCE_STALE`; no readable clock gives `SOURCE_CLOCK_UNKNOWN`. Both fail EXECUTABLE plans; forecasts still run. A producer receipt `loaded_at` replaces the row clock. No cutoff column. |
| Read by | `focus/data/inspections.py` (`build_triggers`, `phase_plan`), `focus/data/graph.py`, `focus/data/freshness.py`; called from `focus/data/snapshot_builder.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SHOP_ORDER_IDENTIFIER` | varchar(30), not null | The job's SOI: its native identifier, the same text as `SHOP_ORDER_IDENTIFIER` in `master_task_list`. | Matched exactly, with `LINE_NUMBER`, to a census job. Rows that match no census job are skipped without a note. |
| `LINE_NUMBER` | int, not null | The aircraft line number. | Second half of the job match. |
| `SHOP_ORDER_STATUS` | varchar(20), nullable | The job's shop-order status as this table carries it. FOCUS takes job status from `master_task_list`. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `MANUFACTURING_INSP` | int, nullable | Count of manufacturing buyoffs the job requires. | Above 0 adds one parallel MANUFACTURING buyoff. Its minutes come from `inspection_standards`, else `qa.other_buyoff_minutes` (20). |
| `QUALITY_INSP` | int, nullable | Count of quality buyoffs required. | Above 0 adds one parallel QUALITY buyoff, sized the same way. |
| `CC_INSP` | int, nullable | Count of CC buyoffs required. The repo does not spell out CC; the synthetic fixture calls it a customer contract buyoff. | Above 0 adds one parallel CC buyoff, sized the same way. |
| `ODAUNITMEMBER_INSP` | int, nullable | Count of ODA unit member buyoffs required. The repo does not spell out ODA. | Above 0 adds one parallel ODAUNITMEMBER buyoff, sized the same way. |
| `PARTNER_QT_INSP` | int, nullable | Count of partner QT buyoffs required. The repo does not spell out QT. | Above 0 adds one parallel PARTNER_QT buyoff, sized the same way. |
| `FINAL_QUALITY_INSP` | int, nullable | Count of final quality inspections required. | Above 0 adds one FINAL_QA phase of `qa.final_minutes` (45) after all production and IP phases. Also compared with `inspection_counts` (note only). |
| `IN_PROCESS_QUALITY_INSP` | int, nullable | Count of in-process (IP) quality inspections required. | Above 0 makes IP QA required: the job needs sourced holds in `inspection_holds` that total 45 inspector minutes, or it is blocked. A count that differs from the number of holds only adds a note. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row (no time zone stored). | Freshness check only (see Time and cutoff). |

**Rules and pitfalls.**
- Send a row for every job that needs any inspection. A job without a row is planned with no QA phases, and no gap or note says so.
- A count only switches a phase on. Null, 0 and non-numeric text all mean "not required"; text is read as 0 without a warning. A final count of 2 still gives one 45-minute final inspection, and each parallel kind gives one branch.
- The buyoff plan never locates IP holds. A job with `IN_PROCESS_QUALITY_INSP` above 0 and no valid sourced holds is BLOCKED in every plan, forecast included, with `IP_HOLD_UNKNOWN_LOCATION`, `IP_HOLD_ALLOCATION_MISMATCH` or `IP_HOLD_DUPLICATE_POSITION`. Its successors get `PREDECESSOR_NOT_SCHEDULABLE`.
- Final and IP minutes are user-required policy (45 each), not data. If the flow standard already contains them, set `qa.standard_includes_inspection_minutes` (false today); FOCUS then carves them out once, and a standard too short to hold them gives `QA_RECONCILIATION_INVALID`.
- Final QA follows the last production segment. The parallel buyoffs also start after the last production segment and run beside final QA. All of them feed the AND release, and each successor starts at or after it (finish-to-start).
- Keep the key unique. With two rows for one job, the last row read wins.

## `inspection_counts`

**Purpose.** A second, keyless table of inspection counts per job. FOCUS uses it only to cross-check the final and IP counts against `buyoff_plan`, and writes a note when they differ. It never creates QA phases, never locates IP holds and never authorizes placement.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_INSPECTION_COUNTS`, query `queries/source_inputs/inspection_counts.sql` |
| Grain and key | Meant to be one row per job on a line, but the S04 contract has no primary key. FOCUS matches on `LINE_NUMBER` + `SHOP_ORDER_IDENTIFIER`; if a job has several rows, the last one read wins without a note. |
| Required | No |
| Capabilities | forecast |
| If missing or empty | Capture error: status FAILED, gap `DATASET_FAILED`. Empty without a certificate: gap `DATASET_EMPTY_UNCERTIFIED`. Plans do not change either way; only the cross-check notes disappear. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP`, 36 h limit, as for `buyoff_plan`. A stale table gives `SOURCE_STALE`, which fails EXECUTABLE plans even though nothing schedules from this table. No cutoff column. |
| Read by | `focus/data/inspections.py` (`build_triggers`), `focus/data/freshness.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SHOP_ORDER_IDENTIFIER` | varchar(50), not null | The job's SOI (wider here than the varchar(30) in `buyoff_plan`). | Matched exactly, with `LINE_NUMBER`, to a census job; unmatched rows are skipped. |
| `LINE_NUMBER` | int, not null | The aircraft line number. | Second half of the job match. |
| `MANUFACTURING_INSPECTIONS` | int, not null | Count of manufacturing inspections for the job. | Stored in the job's inspection trigger (`counts`) in the snapshot; nothing compares it or schedules from it. |
| `QUALITY_INSPECTIONS` | int, not null | Count of quality inspections. | Stored in the trigger only, as above. |
| `FINAL_QUALITY_INSPECTIONS` | int, not null | Count of final quality inspections. | Compared with `buyoff_plan.FINAL_QUALITY_INSP`; a difference adds the note "inspection counts ... differ from buyoff plan". It never changes the plan. |
| `IN_PROCESS_QUALITY_INSPECTIONS` | int, not null | Count of in-process (IP) inspections. | Compared with `buyoff_plan.IN_PROCESS_QUALITY_INSP` the same way (note only). It never locates a hold. |
| `CC_INSPECTIONS` | int, not null | Count of CC inspections. | Stored in the trigger only. |
| `ODAUNITMEMBER_INSPECTIONS` | int, not null | Count of ODA unit member inspections. | Stored in the trigger only. |
| `PARTNER_QT_INSPECTIONS` | int, not null | Count of partner QT inspections. | Stored in the trigger only. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row. | Freshness check only. |

**Rules and pitfalls.**
- Counts are a cross-check, never an authority. They do not add phases, locate IP holds or release work.
- Deduplicate per job before delivery: the table has no key, and FOCUS keeps the last row. The readiness query `queries/quality/inspection_profile.sql` aggregates duplicates and flags `COUNT_ROWS_CONFLICT`; the planner does not.
- Only the final and IP counts are compared, and only for jobs that also have a `buyoff_plan` row.
- A disagreement is a note in the acquisition result, not a readiness gap, and it is not stored in the snapshot.

## `inspection_standards`

**Purpose.** Standard minutes per inspection type, with a scheduling relationship. FOCUS uses only rows whose `SCHEDULING_RELATIONSHIP` is `PARALLEL_BRANCH`: their `DURATION_MINUTES` set the length of each parallel buyoff of that kind. Final and IP minutes come from policy, so rows for those types change nothing.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_INSPECTION_STANDARDS`, query `queries/source_inputs/inspection_standards.sql` |
| Grain and key | One row per inspection type; key `INSPECTION_TYPE` (S04 primary key). |
| Required | Yes: a capture error rejects the source run with `MANDATORY_DATASET_FAILED`. An empty result does not reject the run. |
| Capabilities | forecast, executable |
| If missing or empty | Capture error: run `REJECTED`, `MANDATORY_DATASET_FAILED`. Empty without a certificate: gap `DATASET_EMPTY_UNCERTIFIED` (not enforced by the plan validator); every parallel buyoff then lasts `qa.other_buyoff_minutes` (20, a fixture default). A kind with no `PARALLEL_BRANCH` row also gets 20 minutes, silently. |
| Time and cutoff | No time column, so no freshness check (the table is not in `admission.max_source_age_hours`). No cutoff column. |
| Read by | `focus/data/snapshot_builder.py` (builds the minutes map), used by `focus/data/inspections.py` (`phase_plan`) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `INSPECTION_TYPE` | varchar(30), not null | Inspection type code. Fixture values: FINAL_QUALITY, IN_PROCESS_QUALITY, MANUFACTURING, QUALITY, CC, ODAUNITMEMBER, PARTNER_QT. | Lookup key for parallel buyoff minutes. It must equal, as exact text, a kind FOCUS derives from `buyoff_plan`: MANUFACTURING, QUALITY, CC, ODAUNITMEMBER or PARTNER_QT. |
| `DURATION_MINUTES` | int, not null | Standard length of that inspection, in minutes. | On `PARALLEL_BRANCH` rows, the length of the OTHER_BUYOFF phase of that kind. Ignored on other rows. |
| `SCHEDULING_RELATIONSHIP` | varchar(20), not null | How the inspection relates to the production work. Fixture values: AFTER_WORK_FS (final), TOTAL_ACROSS_HOLDS (IP), PARALLEL_BRANCH (all others). | Only the exact text `PARALLEL_BRANCH` is used; rows with any other value are ignored. |
| `DESCRIPTION` | varchar(200), nullable | Free-text description of the inspection type. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Use the exact kind codes for parallel buyoffs. Another spelling (for example `ODA`) is never looked up, and that buyoff silently gets 20 minutes.
- Final and IP rows have no effect. FOCUS always uses `qa.final_minutes` (45) and `qa.total_ip_minutes` (45), both user requirements; changing them is a policy change, not a data change.
- This table does not tell FOCUS whether a flow standard already embeds QA minutes. That is the policy flag `qa.standard_includes_inspection_minutes`.
- A parallel buyoff is done by one inspector from the QA pool (team `QA`, certificate group `QA`), never by the production crew.

## `inspection_holds`

**Purpose.** Sourced in-process hold points: where each IP inspection sits in the job's production work, and how many active inspector minutes it takes. It is the only input that can place IP QA. FOCUS cuts the job's work at each hold and inserts an IP_QA phase of that many minutes. The minutes across one job's holds must total exactly 45 (`qa.total_ip_minutes`); 45 is never applied per hold, and FOCUS never invents a midpoint hold.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no reference object and no live producer. The live reader reports `NOT_PROBED`; fixtures read `fixtures/inputs/<scenario>/inspection_holds.json`. |
| Grain and key | One row per hold point; a job (`LINE_NUMBER` + `SOI`) can have several. Neither S04 nor the code enforces a key; two holds of one job at the same position give `IP_HOLD_DUPLICATE_POSITION`. |
| Required | No |
| Capabilities | executable (registry tag; a missing hold also blocks forecast plans) |
| If missing or empty | Live: `NOT_PROBED`, gap `DATASET_NOT_PROBED`. Without holds, every job whose buyoff plan has `IN_PROCESS_QUALITY_INSP` above 0 is BLOCKED with `IP_HOLD_UNKNOWN_LOCATION` in every plan, and its successors get `PREDECESSOR_NOT_SCHEDULABLE`. An empty fixture file without a certificate adds `DATASET_EMPTY_UNCERTIFIED`. |
| Time and cutoff | No time columns, no freshness check, no cutoff column. Positions are production minutes, not clock times. |
| Read by | `focus/data/inspections.py` (`build_triggers`, `phase_plan`), `focus/data/graph.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `LINE_NUMBER` | integer | The aircraft line number of the job. | With `SOI`, finds the census job. Holds for jobs not in the census are skipped. |
| `SOI` | text | The job's SOI (`SHOP_ORDER_IDENTIFIER` in `master_task_list`). | Exact text match on line + SOI. |
| `SEQUENCE` | integer | The hold's order number within the job. | Parsed as an integer and otherwise unused; FOCUS orders holds by `AFTER_WORK_MINUTES`. |
| `AFTER_WORK_MINUTES` | integer | Production minutes of the job done before this hold, counted from the job's start and excluding inspection time. | FOCUS places WORK up to this point, then the IP_QA phase. It must be above 0 and below the job's total production minutes, else `IP_HOLD_POSITION_INVALID` (blocked). |
| `INSPECTOR_MINUTES` | integer | Active inspector minutes allocated to this hold. | Becomes the IP_QA phase length, done by one qualified QA inspector. A job's holds must total exactly 45, else `IP_HOLD_ALLOCATION_MISMATCH` (blocked). |
| `EVIDENCE_REF` | text, optional | Source reference for the hold (fixture: `FIXTURE-OP-0040`). | Stored as the hold's evidence; defaults to `hold#<row number>`. |

**Rules and pitfalls.**
- Fixture example (ROOT3, 100 production minutes, holds after 40 and 80 with 20 and 25 inspector minutes): WORK 40, IP 20, WORK 40, IP 25, WORK 20, FINAL 45, then the AND release.
- Holds apply only when `buyoff_plan` requires IP. Holds for a job without that requirement are dropped (with a note if the job has a buyoff row); a hold count that differs from the IP count is only a note.
- A missing or invalid hold blocks the job outright, in forecast and executable plans alike, although the registry tags this dataset executable-only.
- For in-progress jobs, positions are on the whole job (performed plus remaining minutes). A hold at or before the minutes already performed is dropped with `IP_HOLD_STATUS_UNKNOWN`, which blocks executable work only.
- Send whole numbers. Non-integer text in a numeric column raises an error and stops the acquisition.
- While an inspector works a hold, the production crew is released for other work (`qa.crew_during_ip` = RELEASE, a fixture default; WAIT would hold the crew).
