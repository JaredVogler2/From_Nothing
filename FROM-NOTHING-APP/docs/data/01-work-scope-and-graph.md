# Work scope and the job graph

These datasets tell FOCUS which jobs exist, how long each one takes and with how many people, and which jobs must wait
for which. `master_task_list` is the census: every job on every aircraft line that still has open work, including
completed jobs kept as anchors. `scope_receipt` can set the site and program and exclude whole lines. `flow_budget`
gives each job's elapsed minutes and crew size, and `work_progress` gives the minutes still to do on jobs already in
work. `pnet_dag` holds template precedence edges that FOCUS copies onto each line that carries both jobs.
`neo_graph_rework` and `rework_classification` tie each rework job to its root job on the same line and give its class.
Job-level rows join on the line number and the job's SOI: the job identifier (`SHOP_ORDER_IDENTIFIER` in the master
list), which the code calls the SOI. The snapshot builder gives every job exactly one disposition: admitted, blocked
(a needed fact is missing or invalid), quarantined (the source rows contradict each other or cannot be read), excluded
(by the scope receipt) or verified complete.

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`master_task_list`](#master_task_list) | SOURCE | Yes | inventory | Reference SQL query | Yes | Census of every job on every line with open work, including COMPLETE anchors. |
| [`scope_receipt`](#scope_receipt) | SUPPLEMENTAL | No | inventory | None yet: reports NOT_PROBED | No (admission only) | Sets the site and program and excludes whole lines from scope. |
| [`pnet_dag`](#pnet_dag) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Template precedence edges between jobs, with type, hard flag, lag and model. |
| [`neo_graph_rework`](#neo_graph_rework) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Same-line links from each rework job to the job it reworks, with the child's status. |
| [`rework_classification`](#rework_classification) | SUPPLEMENTAL | No | forecast, executable | None yet: reports NOT_PROBED | Yes | NC/PU/CI class, root job and defect-origin team of each rework job. |
| [`flow_budget`](#flow_budget) | SOURCE | Yes | forecast, executable | Reference SQL query | Yes | Each job's elapsed minutes and the crew that must work it together. |
| [`work_progress`](#work_progress) | SUPPLEMENTAL | No | forecast, executable | None yet: reports NOT_PROBED | Yes | Performed and remaining minutes of jobs already in work. |

## `master_task_list`

**Purpose.** This is the census FOCUS plans from: one row per job on every aircraft line that has at least one job not
COMPLETE. Completed jobs on those lines stay in as anchors, so their successors can be timed from them and are never
treated as missing. Each row becomes a job with its team, station, model, status and times. Every row is reconciled
into exactly one disposition, and the run is rejected if the counts do not add up.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_MASTER_TASK_LIST`, query `queries/source_inputs/master_task_list.sql`. The query returns all statuses on every line that has any non-COMPLETE row, with no row cap. |
| Grain and key | One row per job per line; key `LINE_NUMBER` + `SHOP_ORDER_IDENTIFIER` (S04 primary key; the code keys on the same pair). A repeated pair is quarantined, never merged. |
| Required | Yes. A failed capture rejects the run (`MANDATORY_DATASET_FAILED`). No rows without a certified-empty receipt also rejects it (`CENSUS_UNAVAILABLE`). |
| Capabilities | inventory |
| If missing or empty | Rejected as above. If admitted + blocked + quarantined + excluded + verified complete does not equal the rows read, the run is rejected (`CENSUS_NOT_RECONCILED`). Row problems become job reasons: quarantined `DUPLICATE_SOURCE_KEY`, `UNKNOWN_STATUS`, `BAD_SOURCE_DATETIME`; blocked `STATUS_ON_HOLD`, `STATUS_SOURCE_FAILED`, `STATUS_UNRELEASED`, `TEAM_UNKNOWN`, `REWORK_CLASS_UNKNOWN`, `CS_STATION_UNPARSEABLE`; withheld from executable plans `STATUS_FORECAST_ONLY`. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP` (the newest row, unless a producer receipt gives `loaded_at`); stale after 36 h (`SOURCE_STALE`). No cutoff column: the query takes the table as it stands. `FIRST_AVAILABLE`, `ACT_START` and `ACT_COMP` are naive local times (effective times) converted to UTC with the dataset's declared zone. With no zone the snapshot carries `SOURCE_TIMEZONE_POLICY_MISSING`, and every job that has one of these times is quarantined. |
| Read by | `focus/data/snapshot_builder.py`, `focus/data/scope.py`, `focus/data/freshness.py`. The job records then feed `focus/data/graph.py`, `focus/data/durations.py`, `focus/data/cs_windows.py`, `focus/data/parts.py`, `focus/engine/model.py`, `focus/validation/plan.py` and `focus/web/serializers.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SHOP_ORDER_IDENTIFIER` | varchar(30), not null | The job's identifier (the SOI). | Half of the job key, kept byte for byte. Matched exactly to the SOI columns of the other job-level datasets on the same line (for example `flow_budget.JOB_NUMBER`). |
| `SHOP_ORDER_DESCRIPTION` | varchar(500), nullable | Free-text description of the job. | Copied onto the job and shown in the job list, job detail, shift book and My Day views. |
| `LINE_NUMBER` | int, not null | The aircraft line number; FOCUS treats each line as one aircraft. | Other half of the job key (a positive integer): the same SOI on two lines is two jobs. Calendars, station windows, graph edges and rework links are all matched within the line. |
| `SHOP_ORDER_STATUS` | varchar(20), not null | The job's status in the source. | Mapped exactly (case and spaces count) by policy `scope.status_classes`: `COMPLETE` is a verified-complete anchor; `AVAILABLE`, `PLANNED`, `IDLE` are open; `IN WORK` is in progress; `PREVIEW`, `FORECASTED` are forecast-only; `ON HOLD`, `REVISIONHOLD`, `FAILPREVIEW`, `UNRELEASED` block the job; any other value is quarantined (`UNKNOWN_STATUS`). Raw counts per line appear in the census view. |
| `TEAM` | varchar(30), nullable | The team that does the job. | FOCUS staffs the job only from that team's shift pool and groups rework statistics by it. Null blocks the job (`TEAM_UNKNOWN`); `MHE`, `PSC` and `TST` jobs without a real resource source are withheld from executable plans (`SUPPORT_RESOURCE_UNAVAILABLE`). |
| `CURRENT_CONTROL_STATION` | varchar(10), nullable | The job's control station (CS), as text. | FOCUS reads it as an integer and looks up the CS window for this line and station; the station's earliest date becomes a start floor. Null or non-numeric text blocks the job (`CS_STATION_UNPARSEABLE`). |
| `MINOR_MODEL_IDENT` | varchar(10), nullable | The aircraft's minor model. | A `pnet_dag` edge is skipped on a line whose job has a different model. Also part of the exact rework-statistics group; the first non-null value on a line is the aircraft's model in the census view. |
| `CUSTOMER_CODE` | varchar(10), nullable | The aircraft's customer code. | Copied onto the job; the first non-null value on a line is shown as the aircraft's customer in the census view. No planning rule reads it. |
| `PARENT_SOI` | varchar(30), nullable | SOI of this job's parent job on the same line. | Builds the parent chain up to the root: a part shortage on a job gates that job and every ancestor, and rework roots are checked against the chain (`REWORK_LINK_CONFLICT`, `REWORK_CYCLE`). A parent not found on the line ends the chain. |
| `SUPERINTENDENT` | varchar(50), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `FIRST_AVAILABLE` | datetime2(0), nullable | The earliest time the job may start, in local time. | Converted to UTC and rounded up to the minute; no work on the job may start before it (validator code `BEFORE_JOB_EARLIEST`). |
| `ACT_START` | datetime2(0), nullable | When work on the job actually started, in local time. | Kept as actual history and shown in job detail; it dates the `SEQUENCE_VIOLATION` diagnostic for a job in work. An `IN WORK` job without it is listed in the admission notes. |
| `ACT_COMP` | datetime2(0), nullable | When the job was actually completed, in local time. | For a COMPLETE anchor, successors and their lags are measured from the earlier of this time and the cutoff; with no value the cutoff is used. Shown in job detail. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row. | Row clock for freshness: the newest value must be within 36 h of the cutoff, or executable plans fail validation (`SOURCE_STALE`; unreadable values give `SOURCE_CLOCK_UNKNOWN`). |
| `PLANNING_SYSTEM_NAME` | varchar(50), nullable | Meaning not established in code or docs (the synthetic fixture uses `DEMO-PS`). | Copied onto the job record in the snapshot; nothing in the planner reads it today. |
| `IS_REWORK` | bit, nullable | Source flag that the job is rework. | An open job with this flag and no `rework_classification` row is blocked (`REWORK_CLASS_UNKNOWN`). Shown in job detail. |
| `RECORD_KEY` | int, nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Return every row of every line with open work, including COMPLETE rows, with no row cap. A predecessor that is
  missing from a line's census is treated as a task that aircraft does not carry: FOCUS links its successor to the
  nearest carried ancestors instead. So a dropped open job silently stops holding back its successors.
- A COMPLETE row is never rescheduled. It anchors its successors at the earlier of `ACT_COMP` and the cutoff, so send
  the real completion time.
- Keep SOI text identical in every dataset. Matching on line + SOI is exact: no trimming, no case folding, no prefix
  rules.
- Send `CURRENT_CONTROL_STATION` as plain digits that equal `cs_windows.CONTROL_STATION` (for example `744`). A leading
  zero still finds the station window, but the validator then finds no raw row for the station text and fails every
  plan that places the job (`CS_RAW_ROW_MISSING`).
- Statuses must match the policy vocabulary exactly. An unknown status quarantines the job and blocks its successors
  (`PREDECESSOR_NOT_SCHEDULABLE`). Live status meanings are open decision OD-13 in the policy file.
- Every `IN WORK` job needs a `work_progress` row. Without one it is blocked (`REMAINING_UNKNOWN`), except an NC/PU/CI
  rework job, which is planned again from its full rework estimate. Duplicate line + SOI rows are quarantined
  (`DUPLICATE_SOURCE_KEY`) and still counted, so fix them at the source.

## `scope_receipt`

**Purpose.** An optional, explicit statement of operational scope. By default FOCUS plans all work in process
(`ALL_WIP`) for the configured site and program. A receipt can name the site and program for the run and exclude whole
lines with a reason. Nothing else narrows the census.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet, so the live reader reports `NOT_PROBED`. Fixture: `fixtures/inputs/manufacturing_demo/scope_receipt.json`. |
| Grain and key | Two kinds of row. A run-level row has `LINE_NUMBER` null and carries `SITE`, `PROGRAM`, `SCOPE_BASIS` and `EVIDENCE_REF`. A line row has `LINE_NUMBER` set and carries `ADMISSION` and `REASON`. Keyed by `LINE_NUMBER` in code: a later row for the same line replaces an earlier one. |
| Required | No. |
| Capabilities | inventory |
| If missing or empty | No rows: FOCUS uses policy `scope.default_basis` (`ALL_WIP`) with the site and program from settings (`FOCUS_SITE`, `FOCUS_PROGRAM`) and admits every census line. In live mode the snapshot also lists `DATASET_NOT_PROBED` for this dataset; that gap changes nothing else. |
| Time and cutoff | No clock column and no cutoff column; not checked for freshness. |
| Read by | `focus/data/scope.py` (`scope_config`, `build_census`), called from `focus/data/snapshot_builder.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `SITE` | text, optional | Site code for the run (run-level row). | Replaces the configured site when not empty. The site is part of every job key and shift id, so a different site gives every job a different id. |
| `PROGRAM` | text, optional | Program code for the run (run-level row). | Replaces the configured program when not empty. It is part of every job key and is shown on each aircraft's census record. |
| `SCOPE_BASIS` | text, optional | Name of the scope basis, such as `ALL_WIP` (run-level row). | Stored as a label on each aircraft's census record and shown in the census view. It narrows nothing by itself. |
| `LINE_NUMBER` | integer, or null for the run-level row | Null marks the run-level row; a number marks a line row. | Line rows apply to the census line with that number. A line that is not in the census is ignored. |
| `ADMISSION` | text, optional (default `ADMIT`) | The decision for the line (line rows). | `EXCLUDE` marks every job on the line excluded (`SCOPE_EXCLUDED: <REASON>`); the jobs stay in the census and its counts. Any other value, including `ADMIT`, has no effect. |
| `REASON` | text, optional | Why the line is excluded (line rows). | Becomes the text of the `SCOPE_EXCLUDED` reason; when empty FOCUS writes "explicit scope receipt". Ignored on the run-level row. |
| `EVIDENCE_REF` | text, optional | Reference to the source evidence for the scope (the fixture uses `FIXTURE-SCOPE-01`). | Read from the run-level row into the scope configuration, but nothing downstream uses it today. Ignored on line rows. |

**Rules and pitfalls.**
- Only whole lines can be excluded. There is no job-level column, and `ADMISSION` values other than `EXCLUDE` do
  nothing.
- A narrower scope needs explicit `EXCLUDE` rows. Setting `SCOPE_BASIS` to something other than `ALL_WIP` drops no
  line.
- Keep `SITE` and `PROGRAM` stable from run to run. They are part of job identity, so changing them gives every job a
  new id.
- With several run-level rows, the last non-empty `SITE`, `PROGRAM` and `SCOPE_BASIS` win, and `EVIDENCE_REF` comes from
  the last run-level row.
- Live mode has no reader yet, so the default `ALL_WIP` scope applies until a producer is installed.

## `pnet_dag`

**Purpose.** The template precedence network (PNet in the repo): which job must finish or start before which, by SOI,
with a relationship type, a hard flag and a signed lag in minutes. FOCUS copies each template edge onto every line
whose census carries both jobs with a matching model. These edges set the order of work in every plan, and the
validator checks every hard one (check V2).

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_PNET_DAG`, query `queries/source_inputs/pnet_dag.sql`. Profile query: `queries/graph/template_edge_profile.sql`. |
| Grain and key | One row per template edge (predecessor SOI, successor SOI, model effectivity). No S04 primary key. FOCUS drops repeats per line by predecessor job, successor job, type and lag. |
| Required | Yes. A failed capture rejects the run (`MANDATORY_DATASET_FAILED`). An uncertified empty result does not reject it (see "If missing or empty"). |
| Capabilities | forecast, executable |
| If missing or empty | No rows without a certified-empty receipt gives only the gap `DATASET_EMPTY_UNCERTIFIED`. The run completes with no template edges, and executable plans still pass validation, so work is ordered with no template precedence. Edge problems: `EDGE_HARDNESS_UNKNOWN`, `EDGE_TYPE_UNSUPPORTED`, `CYCLE_SELF_EDGE` (successor quarantined), `CYCLE`, `CONCURRENCY_SEMANTICS_UNRESOLVED` (jobs quarantined). Successors of jobs that cannot be scheduled get `PREDECESSOR_NOT_SCHEDULABLE` or `PREDECESSOR_NOT_EXECUTABLE`. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP`, stale after 36 h (`SOURCE_STALE`). No cutoff column. |
| Read by | `focus/data/graph.py` (`build_graph`), `focus/data/freshness.py`. The edges are then read by `focus/engine/model.py`, `focus/engine/max/optimizer/greedy.py` and `focus/validation/plan.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `PREDECESSOR_SOI` | varchar(30), not null | SOI of the job that comes first. | Matched exactly to census SOIs on each line. If a line carries the successor but not this job, FOCUS bridges the edge to this job's nearest carried ancestors, summing lags; the missing task is never treated as done. |
| `SUCCESSOR_SOI` | varchar(30), not null | SOI of the job that waits. | Matched exactly on each line; a line that does not carry it gets no edge. The successor is quarantined when this edge has an unknown hard flag or an unsupported hard type. |
| `RELATIONSHIP_TYPE` | varchar(30), nullable | How the jobs are tied: `FINISH <= START`, `START <= START`, `FINISH <= FINISH`, `START <= FINISH`, `CONCURRENT` or `NON-CONCURRENT`. | Mapped by policy `edges.relationship_map` to FS, SS, FF, SF (for example FS: predecessor finish + lag no later than successor start) or a concurrency pair. A null or unmapped type quarantines the successor if the edge is hard (`EDGE_TYPE_UNSUPPORTED`); on an advisory edge it is only noted. |
| `IS_HARD` | char(1), nullable | `Y` for a binding edge, `N` for an advisory one. | Policy `edges.hard_flag_map`: `Y` is enforced; `N` is recorded but not enforced unless `edges.advisory_mandatory` is true. Null or any other value quarantines the successor (`EDGE_HARDNESS_UNKNOWN`). |
| `PRED_MINOR_MODEL` | varchar(10), nullable | Minor model the edge applies to, predecessor side. | When both this value and the predecessor job's `MINOR_MODEL_IDENT` are known and differ, the edge is skipped on that line. Null matches any model. |
| `SUC_MINOR_MODEL` | varchar(10), nullable | Minor model the edge applies to, successor side. | The same check against the successor job's model. Null matches any model. |
| `ORIGINAL_PRED_JOBNUMBER` | varchar(50), nullable | Meaning not established in code or docs (the fixture repeats the predecessor SOI). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ORIGINAL_SUC_JOBNUMBER` | varchar(50), nullable | Meaning not established in code or docs (the fixture repeats the successor SOI). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `OFFSET_MINUTES` | int, nullable | Signed lag in minutes between the two endpoints. | Applied as it is: negative lags are kept, and null means 0. A bridged edge carries the summed lag of its path. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row. | Row clock for freshness (36 h limit). |

**Rules and pitfalls.**
- Use the exact relationship strings in policy `edges.relationship_map` and only `Y` or `N` for `IS_HARD`. Anything
  else quarantines the successor job.
- Edges are keyed by SOI, not by line. Do not cross-join the template with aircraft: FOCUS creates an edge only on lines
  whose census holds both SOIs with matching model effectivity.
- Send the real signed lag. Negative lags are kept, and FOCUS reads null as 0.
- Hard `CONCURRENT` and `NON-CONCURRENT` edges use policy modes (`REQUIRED_OVERLAP`, `NO_OVERLAP`). Their live meaning
  is open decision OD-07, so in live mode both jobs of such an edge are quarantined
  (`CONCURRENCY_SEMANTICS_UNRESOLVED`). A cycle of hard edges quarantines every job in it (`CYCLE`); no edge is deleted.
- An empty result is not rejected and does not stop executable plans. Check the row count of every capture.
- Freshness is measured from the newest `LOAD_TIMESTAMP`, because the live reader adds no `loaded_at` receipt. Refresh
  it within the 36 h limit (a fixture default) even when the template has not changed, or executable plans fail
  (`SOURCE_STALE`).

## `neo_graph_rework`

**Purpose.** The source's rework topology: for each rework job (the child), the job it reworks (the parent) on the same
line, and the child's status. FOCUS uses it to make the root job wait for the rework. It is also the fallback root when
`rework_classification` gives none.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.NEO_GRAPH_REWORK`, query `queries/source_inputs/neo_graph_rework.sql`. Profile query: `queries/graph/rework_linkage.sql`. |
| Grain and key | One row per rework child per line; key `LINE_NUMBER` + `CHILD_SOI` (S04 primary key; the code keys on the same pair and keeps the last row if one repeats). |
| Required | Yes. A failed capture rejects the run (`MANDATORY_DATASET_FAILED`). An uncertified empty result does not reject it (see "If missing or empty"). |
| Capabilities | forecast, executable |
| If missing or empty | No rows without a certified-empty receipt gives only `DATASET_EMPTY_UNCERTIFIED`; rework links then come only from `rework_classification` roots. Link problems: `REWORK_ROOT_UNRESOLVED`, `REWORK_LINK_CONFLICT`, `REWORK_CYCLE` (quarantined); `REWORK_STATUS_CONFLICT` (root withheld from executable plans); `ROOT_CLOSURE_REOPENED` (information gap). A child that is not in the census is only listed in the admission notes. |
| Time and cutoff | Row clock `SNAPSHOT_TS`, stale after 48 h (`SOURCE_STALE`). If no row has a readable value, `SOURCE_CLOCK_UNKNOWN`. No cutoff column. |
| Read by | `focus/data/graph.py`, `focus/data/snapshot_builder.py`, `focus/data/freshness.py`. |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `LINE_NUMBER` | int, not null | Aircraft line of the child and its parent. | Child, parent and root are all looked up on this one line; there are no cross-line rework links. |
| `PARENT_SOI` | varchar(30), not null | SOI of the job the child reworks. | Used as the root when `rework_classification` has no `ROOT_SOI`. If both exist and differ, the classification root must be this parent or one of its ancestors (master `PARENT_SOI`), or both child and root are quarantined (`REWORK_LINK_CONFLICT`). |
| `CHILD_SOI` | varchar(30), not null | SOI of the rework job. | Must be in the line's census. For an open child, FOCUS adds a hard edge from the child's release to the root's start, or the hold set by policy `rework.started_root_hold` when the root is already in work. |
| `CHILD_STATUS` | varchar(30), nullable | The child's status in this source. | Read through policy `scope.status_classes`. If it says complete and the census does not, or the reverse, the root is withheld from executable plans (`REWORK_STATUS_CONFLICT`). Shown on the rework link. |
| `SNAPSHOT_TS` | datetime, nullable | Source snapshot time of the row. | Row clock for freshness (48 h limit). |

**Rules and pitfalls.**
- Links are same-line only. A root that is not on the child's line is not found, and the child is quarantined
  (`REWORK_ROOT_UNRESOLVED`).
- `CHILD_STATUS` uses the master status vocabulary: only a value mapped to `TERMINAL_COMPLETE` (today `COMPLETE`) counts
  as complete.
- A reversed or cyclic link (the child is the root or one of its ancestors) quarantines the jobs (`REWORK_CYCLE`).
  Nothing is inferred from SOI prefixes.
- If the root is already COMPLETE while the child is open, FOCUS raises `ROOT_CLOSURE_REOPENED` and makes the root's hard
  successors wait for the child.
- Each open child adds its own edge, so two children of one root both gate it.
- An empty result is not rejected. Check the row count, because with no rows only `rework_classification` can link
  rework to its root.

## `rework_classification`

**Purpose.** The authoritative class of each rework job (NC, PU or CI), its root job and the team whose defect caused
it. The class chooses the duration method: rework statistics instead of the flow standard. The root decides which job
waits for the rework. The defect-origin team is kept apart from the team that does the repair.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet, so the live reader reports `NOT_PROBED`. Fixture: `fixtures/inputs/manufacturing_demo/rework_classification.json`. The live source is open decision OD-08. |
| Grain and key | One row per rework child per line; keyed by `LINE_NUMBER` + `CHILD_SOI` in code. A repeated pair silently replaces the earlier row. |
| Required | No. |
| Capabilities | forecast, executable |
| If missing or empty | Open jobs flagged `IS_REWORK` in the master list are blocked (`REWORK_CLASS_UNKNOWN`). Rework links still come from `neo_graph_rework`. In live mode the snapshot also lists `DATASET_NOT_PROBED`. |
| Time and cutoff | No clock column and no cutoff column; not checked for freshness. |
| Read by | `focus/data/snapshot_builder.py`, `focus/data/scope.py`, `focus/data/graph.py`; then `focus/data/durations.py` (class) and `focus/performance/lifecycle.py` with `focus/performance/disruption.py` (defect origin). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `LINE_NUMBER` | integer | Aircraft line of the rework job. | Key with `CHILD_SOI`; the root is looked up on this line. |
| `CHILD_SOI` | text | SOI of the rework job. | Must match a census job on the line; otherwise the row is listed in the admission notes and skipped. |
| `REWORK_CLASS` | text: `NC`, `PU` or `CI` | The rework class. The repo does not expand these codes; its fixture labels an NC job "nonconforming fastener" and a PU job "pickup item". | For a class in policy `duration.rework_classes`, the job's elapsed time is the policy statistic (default P75, the 75th percentile) of completed comparable rework episodes, or the job is blocked (`DURATION_UNAVAILABLE`). Any other value, including null, is accepted silently and the job falls back to the flow standard. |
| `ROOT_SOI` | text, optional | SOI of the root job this rework belongs to, on the same line. | Preferred over `neo_graph_rework.PARENT_SOI` as the job that waits for the rework. Empty falls back to that parent; a root not found on the line quarantines the child (`REWORK_ROOT_UNRESOLVED`). |
| `DEFECT_ORIGIN_TEAM` | text, optional | The team whose defect caused the rework, independent of who repairs it. | Kept on the job and the rework link. After the shift it is the evidenced origin team for disruption claims about injected rework. |
| `EVIDENCE_REF` | text | Reference to the source record behind the class (the fixture uses `FIXTURE-NCR-301`). | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Send a row for every rework job, including those `neo_graph_rework` already links. Without a class, an open
  `IS_REWORK` job is blocked.
- Use exactly `NC`, `PU` or `CI`. A misspelled or null class does not raise an error; the job is simply planned from the
  flow standard.
- A row applies even when the master `IS_REWORK` is false or null: the class and the root link are still used.
- The root must be on the child's line. If it differs from the `neo_graph_rework` parent, it must be that parent or
  one of its ancestors through master `PARENT_SOI`, or both jobs are quarantined (`REWORK_LINK_CONFLICT`).
- Do not fill `DEFECT_ORIGIN_TEAM` from the executing team. Leave it empty when the origin is not evidenced; a claim
  without origin evidence stays proposed.

## `flow_budget`

**Purpose.** The engineering standard for each job: how many elapsed minutes the work takes and how many mechanics it
needs. FOCUS takes the duration of ordinary open jobs from `CRITICAL_FLOW_MINUTES` and the crew demand of every job
from `TOTAL_MECHANICS`, read as people working together. Standard person effort is elapsed minutes × crew. Every open
job needs a row, including rework and in-progress jobs, because crew demand always comes from here.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_FLOW_BUDGET`, query `queries/source_inputs/flow_budget.sql`. |
| Grain and key | One row per job per line; key `JOB_NUMBER` + `LINE_NUMBER` (S04 primary key). The code looks up line + SOI, so the same job number on two lines is two standards. Identical repeats collapse; repeats that differ in any standard field are dropped and reported (`FLOW_CONFLICT`). |
| Required | Yes. A failed capture rejects the run (`MANDATORY_DATASET_FAILED`). An uncertified empty result does not reject it (see "If missing or empty"). |
| Capabilities | forecast, executable |
| If missing or empty | Blocked job reasons: `STANDARD_MISSING` (no row), `FLOW_CREW_INVALID` (`TOTAL_MECHANICS` null, 0 or negative), `STANDARD_INVALID` (`CRITICAL_FLOW_MINUTES` null or not positive where it is the duration), `FLOW_CONFLICT`. In live mode every job also gets `FLOW_SEMANTICS_UNCONFIRMED` (withheld from executable plans) until open decision OD-01 is settled. An empty uncertified result adds `DATASET_EMPTY_UNCERTIFIED` and blocks every open job. |
| Time and cutoff | Row clock `LOAD_TIMESTAMP`, stale after 36 h (`SOURCE_STALE`). No cutoff column. |
| Read by | `focus/data/durations.py` (`flow_index`, `resolve`), `focus/data/scope.py` (attaches the row to its job), `focus/data/graph.py`, `focus/data/freshness.py`, `focus/web/serializers.py` (Schedule Budget view). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `JOB_NUMBER` | varchar(30), not null | The job's SOI. | Matched exactly to `SHOP_ORDER_IDENTIFIER` on the same line. |
| `LINE_NUMBER` | int, not null | Aircraft line. | Second half of the lookup key. |
| `CRITICAL_FLOW_MINUTES` | int, nullable | Elapsed minutes the job's work takes (elapsed time, not person-minutes). | The duration of an open job that is neither in work nor NC/PU/CI rework; null or not positive blocks it (`STANDARD_INVALID`). Shown as flow minutes in the Schedule Budget view. |
| `TOTAL_MECHANICS` | int, nullable | The job's requested number of mechanics. Whether the source counts people working at the same time or sequential assignments is open decision OD-01; policy `duration.total_mechanics_semantics` reads it as a simultaneous crew. | Crew demand of every work phase: that many distinct qualified people at the same time, and standard effort = elapsed × this number. Null, 0 or negative blocks the job (`FLOW_CREW_INVALID`); there is no default of 1. |
| `PRIMARY_MECHANIC_COUNT` | int, nullable | Number of mechanics in the primary role. The docs say it must never replace `TOTAL_MECHANICS` as crew demand. | Compared between repeated rows (`FLOW_CONFLICT`) and kept on the job; never used as crew demand. |
| `PRIMARY_MECHANIC_DURATION` | int, nullable | Minutes for the primary role. The docs list it among the role and effort fields but do not define it further. | Compared between repeated rows and kept on the job; nothing else reads it. |
| `ASSIST_COUNT` | int, nullable | Number of mechanics in the assist role. | Compared between repeated rows and kept on the job; never used as crew demand. |
| `ASSIST_LABOR_MINUTES` | int, nullable | Labor minutes of the assist role. The docs do not define it further. | Compared between repeated rows and kept on the job; nothing else reads it. |
| `TOTAL_LABOR_MINUTES` | int, nullable | Total labor of the job in person-minutes. | Used as standard effort only if policy `duration.standard_effort_basis` is `TOTAL_LABOR_MINUTES` (today it is `ELAPSED_TIMES_CREW`). Compared between repeated rows; shown as flow labor minutes in the Schedule Budget view. |
| `DURATION_SOURCE` | varchar(30), nullable | Meaning not established in code or docs (the fixture uses `FIXTURE_STANDARD` and `REWORK_STATISTIC`). | Compared between repeated rows and kept on the job; no rule reads it. |
| `LOAD_TIMESTAMP` | datetime2(3), not null | Source load time of the row. | Row clock for freshness (36 h limit). Among identical repeats the latest is kept, and it is part of the duration estimate's source hash. |

**Rules and pitfalls.**
- Every open job needs a row with `TOTAL_MECHANICS` above 0, including NC/PU/CI and `IN WORK` jobs. Their durations come
  from elsewhere, but their crew does not (`STANDARD_MISSING`, `FLOW_CREW_INVALID`).
- `TOTAL_MECHANICS` must count people who work at the same time. If the source counts sequential assignments, the spec
  requires explicit role phases, which FOCUS does not build: any policy value other than `SIMULTANEOUS` blocks every
  job (`FLOW_SEMANTICS_<value>`).
- `CRITICAL_FLOW_MINUTES` is elapsed time. Do not put labor (person) minutes there.
- For jobs that need inspection, FOCUS adds QA phases on top of the standard. If the standard already includes
  inspection minutes, the owner must set policy `qa.standard_includes_inspection_minutes` (today false), or QA time is
  counted twice.
- Repeated rows for one line and job must agree on every standard field, or the job is blocked (`FLOW_CONFLICT`). There
  is no "last row wins".
- `JOB_NUMBER` must equal the master SOI exactly; a near match gives `STANDARD_MISSING`.

## `work_progress`

**Purpose.** For each job already in work, the elapsed minutes performed so far and the elapsed minutes still to do.
FOCUS schedules only the remaining work and never restarts a job from its full standard. The performed minutes also
tell FOCUS which in-process (IP) QA hold points the job has already passed.

| | |
| --- | --- |
| Kind | SUPPLEMENTAL: no live producer yet, so the live reader reports `NOT_PROBED`. Fixture: `fixtures/inputs/manufacturing_demo/work_progress.json`. |
| Grain and key | One row per in-progress job; keyed by `LINE_NUMBER` + `SOI` in code. A repeated pair silently replaces the earlier row. |
| Required | No. |
| Capabilities | forecast, executable |
| If missing or empty | An `IN WORK` job with no row is blocked (`REMAINING_UNKNOWN`), except an NC/PU/CI rework job: that one is not blocked and gets its full rework statistic again. A remaining value of 0 or less is blocked (`REMAINING_NONPOSITIVE`). A failed capture lists `DATASET_FAILED`; in live mode the snapshot lists `DATASET_NOT_PROBED`. |
| Time and cutoff | Cutoff column `AS_OF_UTC`: rows whose as-of time is after the run cutoff must not be visible. The fixture reader drops them; a live producer must apply the same rule. Not checked for freshness. |
| Read by | `focus/data/snapshot_builder.py`, `focus/data/scope.py`, `focus/data/durations.py`, `focus/data/graph.py` with `focus/data/inspections.py`, `focus/validation/plan.py` (check V1), `focus/web/serializers.py` (job detail). |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `LINE_NUMBER` | integer | Aircraft line. | Key with `SOI`. |
| `SOI` | text | The job's SOI. | Must equal the master SOI on the same line; a row that matches no job is ignored. |
| `PERFORMED_ELAPSED_MINUTES` | integer, required | Elapsed minutes of work already done. | Places the remaining work on the job's full length (performed + remaining). A sourced IP hold at or before this point withholds the job from executable plans, because no source says whether it was bought off (`IP_HOLD_STATUS_UNKNOWN`). Shown in job detail. |
| `REMAINING_ELAPSED_MINUTES` | integer, required | Elapsed minutes of work still to do. | For an `IN WORK` job this replaces the flow standard or rework statistic as the duration; crew still comes from `flow_budget`. Must be above 0. |
| `AS_OF_UTC` | UTC timestamp, RFC 3339 with offset (for example `2026-10-26T09:30:00Z`) | As-of time of the progress figures. | Used only to hide rows that are later than the run cutoff; nothing in the planner reads it after capture. |
| `EVIDENCE_REF` | text | Reference to the source record (the fixture uses `FIXTURE-PROGRESS-ROOT4`). | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Send a row for every `IN WORK` job, rework included. An in-work NC/PU/CI job without a row is planned from its full
  rework statistic, as if it had not started.
- Send rows only for `IN WORK` jobs. A row for a job in any other status does not change its duration, but the
  validator then expects the remaining minutes and fails every plan on that snapshot (V1 `PARENT_NOT_CONSERVED`).
- Both minute columns are required on every row. A null value stops admission with an error instead of a coded gap.
- A finished job should arrive as COMPLETE in the master list, not as a progress row with 0 remaining.
- Give `AS_OF_UTC` with an offset or `Z`. The fixture reader fails the capture on a naive value, and the in-progress job
  is then blocked (`DATASET_FAILED`, `REMAINING_UNKNOWN`).
- Use the same elapsed scale as `flow_budget.CRITICAL_FLOW_MINUTES`. In-process hold positions
  (`inspection_holds.AFTER_WORK_MINUTES`) are measured from the start of the whole job, not from the remaining part.
