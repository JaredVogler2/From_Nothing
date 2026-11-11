# Staging plan: what to copy from Teradata into SQL Server, how, and how often

**Audience.** The company-side coding agent that prepares the data and builds the GitLab pipelines. Read
[HANDOFF.md](HANDOFF.md) first: this page goes deeper on its section 4 (the staging job) and section 5 (GitLab).

**Status (2026-11-11).** A design. Nothing here has run against a company system. The row counts and change rates
come from the synthetic 50-aircraft fleet in this repository, and the timings of FOCUS's own steps from
`docs/execution/PRODUCTION_SCALE.md` ("The pre-shift flow, action by action"). No Teradata query has been timed:
measuring them is step 1 of your work (section 11).

**Confidentiality.** This repository is public. Real server names, object names, query text, mappings, credentials
and rows stay in company storage ([WORKPLACE_PORTING_GUIDE.md](WORKPLACE_PORTING_GUIDE.md)). Dataset names below,
such as `master_task_list`, are FOCUS's own names (`focus/data/datasets.py`). Which Teradata objects feed each one is
your private mapping ([SOURCE_REPLACEMENT_CROSSWALK.md](SOURCE_REPLACEMENT_CROSSWALK.md)).

## 0. The short version

- **The owner's situation (2026-11-11).** "Most of the time will be spent on running Queries through Teradata to
  gather necessary pieces ... queries will take longest unless that data is pre staged in sql server. Sql server
  holds none of the data initially and all will need taken from Teradata." And: "pulling task list of available work
  should probably be at the time of the scheduler so that it has the most up to date list of all jobs new and
  completed etc."
- **The rule that follows.** Keep a complete, current copy of every dataset in SQL Server at all times. At the
  moment the scheduler runs, pull from Teradata **only the rows that changed** since the last pass. Never run a full
  Teradata extract inside the 20 minutes.
- **Three passes keep the copy current:**

  | Pass | When | What it does | Time allowed |
  | --- | --- | --- | --- |
  | Nightly | once a day, between shifts' runs | Loads or compares everything in full against Teradata and repairs any drift | hours |
  | Before each shift | 60 minutes before the shift starts | Brings every table that changes during a day up to date | 30 minutes |
  | At the cut | 20 minutes before the shift starts, as the first job of the plan pipeline | Pulls the changes of the last 40 minutes for the 18 datasets that describe the floor right now | **2 minutes** |

- **The owner's instinct is right.** The task list is pulled at the time of the scheduler, as a change pull, together
  with new rework links, work in progress, open shortages, part receipts, attendance, leave and certificate
  revocations (section 3).
- **Most data does not need the cut.** In the synthetic fleet about 1% of the 1.2 million source rows change in a
  shift. The other 99% can be in SQL Server hours ahead.
- **Do not compute eligibility in SQL.** Stage the raw certificate tables. FOCUS works out who may do which job
  itself, in a few seconds, and a precomputed matrix never authorizes a crew (section 9).

## 1. Why staging decides whether the 20 minutes are met

The owner's requirement (D-28): from the data cut to the accepted plan and its dashboard data, 20 minutes at most.
The clock starts at the cut, which is the `--as-of` instant the loader is given, and staging counts.

| Part of the 20 minutes | Budget | Basis |
| --- | --- | --- |
| The pass at the cut (Teradata change pulls, load, publish) | 2:00 | This page |
| `verify_staged_sources` (schema check) | 0:30 | Reads metadata only |
| FOCUS: `acquire_admit`, `schedule_validate`, `stage_readback`, `sign_accept` | 13:30 | Measured at 23.2 minutes as shipped; about 13 with the changes of `DECISION_PLAYBOOK.md`, DEC-36 |
| Each job starting on a runner, and the acceptance | 3:00 | Five jobs; use a prebuilt image and automatic acceptance (DEC-11) |
| Margin | 1:00 | |

- **There is no room for a slow query at the cut.** A single Teradata query that runs for minutes breaks the budget
  by itself. Every query of the pass at the cut must return in seconds.
- **A full extract never fits.** The synthetic fleet has about 1.2 million source rows in 44 tables. Reading that
  many rows from a local database takes about 5 seconds, so the row count is not the problem; the time Teradata
  takes to produce them is. Full extracts belong to the nightly pass.
- **FOCUS reads the whole copy every run.** It has no incremental mode: each run reads every staged table in full
  from SQL Server and builds its snapshot from scratch (70 seconds at 50 aircraft). That is why the copy in SQL
  Server must always be complete, not only the changed rows.

## 2. The principle: a complete copy, kept current by change pulls

For each dataset ask three questions:

1. **Does the coming shift's plan depend on its state right now?** A job finished ten minutes ago must not be planned
   again. A calendar for next month does not move in ten minutes.
2. **How many rows change between two shifts?** If few, a change pull is small whatever the table's size.
3. **Can Teradata return the changed rows quickly?** That needs a reliable "last changed" clock on the source rows,
   or a small table that can be pulled whole.

**How much changes in a shift.** Measured on the stored 14-day simulation of the synthetic 50-aircraft fleet
(`docs/execution/SIMULATION.md`), per shift, median over 41 shifts:

| Dataset | Rows in the copy | Rows changed or added in a shift | Share |
| --- | --- | --- | --- |
| `master_task_list` | 298,009 | 3,765 (about 2,360 completions and 1,430 new jobs) | 1.3% |
| `neo_graph_rework` | 73,009 | 3,251 | 4.5% |
| `flow_budget` | 298,009 | 1,433 (rows for the new jobs) | 0.5% |
| `soi_cert_requirements` | 326,593 | 1,433 (rows for the new jobs) | 0.4% |
| `rework_classification` | 73,009 | 1,433 (rows for the new jobs) | 2.0% |
| `etracs_active` | 23,301 | 62 changed; up to 1,123 closed in a shift | 0.3% |
| `attendance_shift` | 4,176 | 8; 2,101 at the first shift of a day | |
| Every other dataset | 122,713 | 0 in the simulation | |
| **All 44** | **1,218,819** | **about 11,400** | **0.9%** |

- These are synthetic numbers, from a fleet generated before the owner's rates (D-31), so its counts differ a
  little from the table in section 3. The real rates come from your loader's receipts (section 12). The shape is
  what matters: a few tables change a little each shift, and most do not change at all.
- **New jobs bring rows in several tables.** A job that appears in the task list needs its duration and crew
  (`flow_budget`), its certificate requirements (`soi_cert_requirements`), its inspections (`buyoff_plan`) and, for
  rework, its class and its link. A change pull of the task list alone would leave every new job blocked or planned
  without QA. So the pass at the cut also pulls those rows **for the new job keys** (section 5, rule 6).

## 3. What to stage, and how often

The 44 planning datasets fall into seven groups. "Clock limit" is the age at which FOCUS refuses an executable plan
(`admission.max_source_age_hours`; section 9). "Needed for a run" means the run is rejected when the dataset fails or
is empty. Rows are from the synthetic 50-aircraft fleet. A dataset with 0 rows there still needs a producer on real
data (`DECISION_PLAYBOOK.md`, DEC-09).

| Group | What it is | Nightly | Before each shift | At the cut |
| --- | --- | --- | --- | --- |
| Floor state | What is open, in work, done, short | Compare in full, repair | Change pull | **Change pull** |
| Job facts | Standing facts of each job: duration, crew, certificates, inspections, rework class | Compare in full, repair | Rows for new jobs | **Rows for new jobs** |
| Calendar | Working days per line and control-station dates | Full load | Check the active load, reload a changed line | **Check the active load again** |
| People | Certificates held, teams, rosters, attendance, leave, overtime authority | Full load | Change pull | **New events and attendance only** |
| Reference | Templates and lookups that change with a release, not with the floor | Check, reload on change | none | none |
| History | Completed episodes and labor, for statistics | Append | none | none |
| Economics | Rates and the customer and model of each line | Check | Rows for new lines | **Rows for new lines** |

**Floor state**

| Dataset | Needed for a run | Rows in the synthetic 50-aircraft fleet | Clock limit | Nightly | Before each shift | At the cut | Load by | Why it matters |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `master_task_list` | yes | 298,009 | 36 h | reconcile keys and counts | changes since the last pass | **changes since the last pass** | `LINE_NUMBER` + `SHOP_ORDER_IDENTIFIER` | The census of jobs. A stale copy plans work already done and misses new jobs |
| `neo_graph_rework` | yes | 73,009 | 48 h | reconcile | changes | **changes** | `LINE_NUMBER` + `CHILD_SOI` | Links each rework job to its root, with the child's status |
| `work_progress` | no | 0 | none | none | every job in work | **every job in work, as of the cut** | `LINE_NUMBER` + `SOI` | Without a row a job in work is blocked (`REMAINING_UNKNOWN`) |
| `etracs_active` | yes | 23,301 | 36 h | reconcile | all open shortages | **all open shortages** | `RECORD_ID` | A closed shortage left in the copy still blocks its job; a new one is missed |
| `part_events` | no | 0 | none | none | new events | **new events up to the cut** | append, once per source event | Only a receipt event confirms a part. Without it the job stays out of the executable plan (`PART_NOT_RECEIVED`) |

**Job facts**

| Dataset | Needed for a run | Rows in the synthetic 50-aircraft fleet | Clock limit | Nightly | Before each shift | At the cut | Load by | Why it matters |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `flow_budget` | yes | 298,009 | 36 h | full compare | rows for new jobs | **rows for new jobs** | `JOB_NUMBER` + `LINE_NUMBER` | A job without a row is blocked (`STANDARD_MISSING`) |
| `soi_cert_requirements` | yes | 326,657 | 48 h | full compare | rows for new jobs | **rows for new jobs** | all rows of a job (`LINE_NUMBER` + `SOI_ID`) | A job without rows, and without a census row, is blocked (`REQUIREMENTS_UNKNOWN`) |
| `buyoff_plan` | yes | 17,666 | 36 h | full compare | rows for new jobs | **rows for new jobs** | `SHOP_ORDER_IDENTIFIER` + `LINE_NUMBER` | A job without a row is planned without QA, and nothing flags it |
| `inspection_holds` | no | 0 | none | full compare | rows for new jobs | **rows for new jobs** | all rows of a job (`LINE_NUMBER` + `SOI`) | A job with in-process inspections and no hold rows is blocked (`IP_HOLD_UNKNOWN_LOCATION`) |
| `rework_classification` | no | 73,009 | none | full compare | rows for new rework jobs | **rows for new rework jobs** | `LINE_NUMBER` + `CHILD_SOI` | An open rework job without a row is blocked (`REWORK_CLASS_UNKNOWN`) |
| `requirement_census` | no | 0 | none | full compare | rows for new jobs | **rows for new jobs** | `LINE_NUMBER` + `SOI_ID` | The only way a job with no certificate requirement becomes plannable |
| `requirement_alternatives` | no | 0 | none | full compare | none | none | all rows of a job (`LINE_NUMBER` + `SOI_ID`) | Approved OR groups. Absent: every certificate stays required |
| `inspection_counts` | no | 0 | 36 h | full load | none | none | `LINE_NUMBER` + `SHOP_ORDER_IDENTIFIER` | Cross-check notes only, but its clock is checked: stale fails executable plans |

**Calendar**

| Dataset | Needed for a run | Rows in the synthetic 50-aircraft fleet | Clock limit | Nightly | Before each shift | At the cut | Load by | Why it matters |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cday_op_plan` | yes | 6,150 | 240 h | full load | check the active load per line; reload a changed line | **check the active load again** | all rows of a line's active load | A line without a calendar is blocked. A calendar that ends before the due date hides lateness |
| `cs_windows` | yes | 250 | 240 h | full load | check the active load per line; reload a changed line | **check the active load again** | all rows of a line's active load | Start floors, gate dates and the due date (station 744) |

**People**

| Dataset | Needed for a run | Rows in the synthetic 50-aircraft fleet | Clock limit | Nightly | Before each shift | At the cut | Load by | Why it matters |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mechanic_certs` | yes | 3,468 | 48 h | full load | changes | none | all rows of a person (`BEMS_ID`) | Who is qualified for what |
| `holding_events` | no | 0 | none | none | new events | **new events up to the cut** | append, once per source event | A revoked certificate still counts until its event is staged |
| `person_team_map` | no | 2,088 | 36 h | full load | changes | none | `BEMS_ID` + `EFFECTIVE_FROM` | Empty: no pool has an executable member |
| `shift_roster` | no | 2,088 | none | full load | full load | none | the whole table | The only way a person joins a team's pool |
| `team_inference` | no | 0 | none | computed in SQL Server | none | none | `RUN_ID` + `PERSON_KEY` | Labels and a review queue only |
| `attendance_shift` | no | 2,088 | 18 h | none | the coming production day and shift | **again, as known at the cut** | `PRODUCTION_DAY` + `SHIFT_NUMBER` + `BEMS_ID` | Empty: nobody is an executable crew member that day |
| `availability_exceptions` | no | 0 | none | none | new rows | **new rows up to the cut** | append, once per source row | Leave that is not staged means absent people are planned |
| `ot_authorizations` | no | 93 | none | full load | full load | none | the whole table | Absent: overtime is 0 |
| `carry_in` | no | 0 | none | none | full load for the horizon | **again, as of the cut** | the whole table | Absent: people already on duty are booked twice |

**Reference**

| Dataset | Needed for a run | Rows in the synthetic 50-aircraft fleet | Clock limit | Nightly | Before each shift | At the cut | Load by | Why it matters |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `pnet_dag` | yes | 64,098 | 36 h | check; reload on change | none | none | the whole table | Template precedence between jobs |
| `inspection_standards` | yes | 2 | none | check; reload on change | none | none | `INSPECTION_TYPE` | Minutes per buyoff kind |
| `cert_course_map` | no | 0 | 168 h | check; reload on change | none | none | `CRITICAL_PROCESS_NAME` + `CERT_CODE` | Not planned from, but its clock is checked |
| `dept_team_skill_ref` | no | 0 | 168 h | check; reload on change | none | none | `ACCTG_DEPT_CD` | Not planned from, but its clock is checked |
| `scope_receipt` | no | 1 | none | on change | none | none | `LINE_NUMBER` | Excludes whole lines. Absent: every line with open work is in scope |
| `people_directory` | no | 2,088 | none | full load | none | none | `PERSON_KEY` | Display names only |
| `cert_eligibility_matrix` | no | 0 | none | full load, or not at all | none | none | none | Display only. It never authorizes a crew |
| `mech_team_inferred` | no | 0 | none | full load | none | none | `BEMS_ID` + `INFERRED_TEAM` | Comparison evidence only |

**History**

| Dataset | Needed for a run | Rows in the synthetic 50-aircraft fleet | Clock limit | Nightly | Before each shift | At the cut | Load by | Why it matters |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `duration_episodes` | no | 2,700 | none | append completed episodes | none | none | `EPISODE_KEY` | Without episodes, rework with no sourced remaining work is blocked (`DURATION_UNAVAILABLE`). FOCUS does not remove repeats: an episode loaded twice counts twice |
| `clock_observations` | no | 0 | none | append | none | none | `SOURCE_EVENT_KEY` + `SOURCE_REVISION` | Feeds the duration statistics report |
| `etracs_history` | no | 21,942 | 36 h | append closed shortages | none | none | `RECORD_ID` | Not planned from, but its clock is checked |
| `actual_labor` | no | 0 | 36 h | full load | none | none | `SHOP_ORDER_IDENTIFIER` + `LINE_NUMBER` | Not planned from, but its clock is checked |
| `labor_shift` | no | 0 | 36 h | append | none | none | day + shift + line + SOI + person | Not planned from, but its clock is checked |

**Economics**

| Dataset | Needed for a run | Rows in the synthetic 50-aircraft fleet | Clock limit | Nightly | Before each shift | At the cut | Load by | Why it matters |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `econ_mapping_revision` | no | 1 | none | check | none | none | `MappingRevisionId` | Exactly one current revision, or every aircraft is unmapped |
| `econ_aircraft_mapping` | no | 50 | none | check | rows for new lines | **rows for new lines** | `MappingRevisionId` + `LineNumber` | A line without a row has no customer and model, so its cost is unknown |
| `econ_scenario` | no | 1 | none | on owner change | none | none | `ScenarioRevisionId` | Exactly one active scenario |
| `econ_contract_assignment` | no | 0 | none | on owner change | none | none | `AssignmentId` | Optional: contract terms |
| `econ_term` | no | 13 | none | on owner change | none | none | `TermId` | The daily rates (D-31). Missing: cost unknown |
| `econ_original_sale_basis` | no | 0 | none | on owner change | none | none | `OriginalBasisId` | Needed only for booked liability |
| `econ_liability_checkpoint` | no | 0 | none | on owner change | none | none | `CheckpointId` | Liability already booked: a floor under the modeled cost |

- **"Load by"** is the unit the loader replaces. Where the source declares a key, load row by row on that key. Where
  it declares none, replace the whole unit (all rows of a job, a person, a line or the table) in one step: merging
  on a guessed key leaves stale rows behind.
- **Append-only tables** must take each source row exactly once. FOCUS does not remove repeats from
  `duration_episodes`.

**The observation feeds** (8 feeds; `docs/data/09-observation-feeds.md`) are not planning data. They record what
happened after a plan was published, and the scores are computed from them. Stage them with `--part observations`
five minutes after each shift's credit deadline, in the evaluator-only pipeline (section 8). They are append-only:
each row carries its source key, its revision and the time the producer first knew it.

### 3.1 What must be pulled at the cut, in one list

Eighteen datasets, each as a small pull bounded by the cut instant:

| What | Datasets | How |
| --- | --- | --- |
| Jobs: new, started, completed, status changed | `master_task_list` | Rows changed since the last pass |
| Rework links and their status | `neo_graph_rework` | Rows changed since the last pass |
| The facts of every new job | `flow_budget`, `soi_cert_requirements`, `buyoff_plan`, `inspection_holds`, `requirement_census`, `rework_classification` | Rows for the job keys that are new in this pass |
| Work already done on jobs in work | `work_progress` | Every job in work, as of the cut |
| Shortages and receipts | `etracs_active`, `part_events` | All open shortages (a small table); new events |
| Who is there | `attendance_shift`, `availability_exceptions`, `carry_in` | The coming production day and shift; new rows |
| Certificates revoked or reinstated | `holding_events` | New events |
| Calendars and station dates | `cday_op_plan`, `cs_windows` | Compare the active load id per line; reload only a line whose load changed |
| A line that is new in the task list | `econ_aircraft_mapping`, and every dataset above for that line | Rows for the new line |

Everything else is already in the copy from the earlier passes.

### 3.2 A line that is new

The fleet is whichever aircraft have open tasks (owner, D-32). When a line gets its first open job, one pass must
bring in everything for that line: its jobs, their facts, its rework links, its shortages, its calendar through the
due date, its control-station windows and its economics mapping row. Treat "new line" as "every job key of that line
is new". Report a new line in the receipt.

## 4. One day, and one pre-shift run

Shift starts are 06:00, 14:30 and 23:00 site time (`config/policies/shift_fixture.json`, America/New_York). T is a
shift's start.

```text
01:15          nightly pass            full compare with Teradata, repairs, history, statistics      (hours)
T-60 min       pass before the shift   everything that changes during a day                          (ends by T-30)
T-20 min       THE CUT                 plan pipeline starts; --as-of = pipeline creation time
  +0:00          pass at the cut       change pulls for the 18 datasets of section 3.1               (2 min)
  +2:00          verify_staged_sources schema check                                                  (30 s)
  +2:30          acquire_admit         FOCUS reads the whole copy, builds and stores its snapshot
                 schedule_validate     the shift's plan
                 stage_readback
                 sign_accept           the plan and its dashboard data are ready                     (by T-0)
T              shift starts
T+ ...         forecast jobs           delivery dates and costs (outside the 20 minutes)
end + 65 min   evaluator-only run      stage the observation feeds, score the closed shift
```

| Schedule (site time) | Cron | Variables |
| --- | --- | --- |
| Nightly staging | `15 1 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=nightly` |
| Staging before shift 1 | `0 5 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=preshift` |
| Staging before shift 2 | `30 13 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=preshift` |
| Staging before shift 3 | `0 22 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=preshift` |
| Plan for shift 1, 2, 3 | `40 5`, `10 14`, `40 22` | `FOCUS_MODE=live` |
| Score shift 1, 2, 3 | `35 15`, `5 0`, `5 7` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` |

- Set every schedule's time zone to America/New_York.
- GitLab starts a scheduled pipeline when it can, which may be minutes late. The cut is the pipeline's creation
  time, never the cron time: the loader always gets `--as-of "$CI_PIPELINE_CREATED_AT"`.
- **Why a pass 60 minutes before, and not only the cut.** It turns the pull at the cut from "eight hours of
  changes" into "forty minutes of changes", it finds a broken query or a schema change while there is still time to
  act, and it is where the slower checks run (key comparison, new-line coverage).
- **If the pass before the shift did not run,** the pass at the cut still works: it pulls from the last watermark,
  whatever its age. It only takes longer.

## 5. How to extract from Teradata

1. **Measure every query before you rely on it** (section 11). For each dataset record the seconds of a full
   extract, of a one-hour change pull and of a key-only pull, with the rows returned. A design that assumes a query
   is fast is not a design.
2. **Know how fresh the source itself is.** A warehouse table is as current as its own last load. Pulling at the cut
   returns nothing newer than that. For every floor-state table find out how and how often it is refreshed, and
   record in each receipt the newest source clock you saw (`source_max_clock`) and its distance from the cut
   (`source_lag_seconds`). If a floor-state source lags by more than the plan can tolerate, the fix is upstream: ask
   its owner for a fresher path. Tell the owner the lag you measure; it limits how current any plan can be.
3. **Pull changes by a source clock, with an overlap.**
   - Use the source's "last changed" column. Pull rows with a clock later than the last watermark minus an overlap,
     and no later than `--as-of`.
   - The overlap covers rows that were written late or stamped early by the source's own load. Start with 30 minutes
     and set it from what the nightly compare finds.
   - Applying the same row twice must change nothing: load by key.
   - Advance the watermark only after the pass has published. Store it per dataset in SQL Server.
4. **One instant for everything.** Every query of a pass is bounded by the same `--as-of`. Two tables must never
   show different moments of the floor.
5. **Where a source has no usable clock,** choose one of these, in this order:
   - pull the table whole, if it is small (a few tens of thousands of rows): `etracs_active`, calendars, rosters;
   - pull keys only and compare them with the copy, then pull the rows of the keys that differ;
   - ask the source owner for a change clock or a change log;
   - leave it to the nightly pass, and say in the mapping that the dataset is at most a day old.
6. **Pull the facts of new jobs by key.** After the task-list pull, collect the job keys that are new in the copy.
   Put them in a temporary table on the Teradata side and join the per-job sources to it. Never build a long
   `IN (...)` list, and never re-extract a per-job table whole at the cut.
7. **Find deletions.** A change pull does not see a row that disappeared.
   - For the small "currently open" sets, pull the whole set each time: a shortage that closed leaves the set, and
     its job becomes workable.
   - For the task list, compare keys in the pass before each shift and in the nightly pass. A job that left the
     source must leave the copy, or it stays open forever.
8. **Restrict to lines with open work.** FOCUS plans every line that has at least one job not complete, and it needs
   all jobs of those lines, completed ones included, as anchors. Build that list of lines once per pass and join
   every per-line extract to it. Never cap the number of lines or rows.
9. **Make the queries cheap for Teradata.**
   - Name the columns you need: the projection in `queries/source_inputs/<dataset>.sql`. Never `SELECT *`.
   - Filter on the columns the source is indexed or partitioned by, without wrapping them in functions.
   - Read the plan (`EXPLAIN`) of every query used at the cut. A full scan or a product join there is a defect.
   - Tag the session so the database administrators can see and prioritize the work (a query band naming the
     application, the pass and the dataset).
   - Run the pulls of one pass in parallel on a few sessions. Ask the administrators how many the workload rules
     allow, and whether bulk-export utilities are limited: use those for the nightly full extracts only, and plain
     queries for the small pulls.
   - Decide with the source owners whether reads may take an access lock. It avoids waiting behind the source's
     own load, at the price of reading rows mid-load; the overlap and the nightly compare then matter more.
10. **Keep values exactly.**
    - Job identifiers, statuses, team and certificate codes are matched character for character. Trim the padding
      of fixed-width columns, the same way in every table, and change nothing else: no case folding, no
      reformatting. Record every transformation in the mapping.
    - Convert every source timestamp to UTC in the loader, knowing each column's zone. The staged tables hold UTC
      (owner, 2026-10-31). A wrong zone moves a completion into another shift.
    - **Dates without a time** (calendar days, control-station dates, expected dates of parts, certificate dates)
      are days at the site. FOCUS reads such a date as midnight in the zone declared for its dataset
      (`FOCUS_SOURCE_TIMEZONE_MAP_JSON`; `docs/data/README.md`, "Clocks and time zones"). With every dataset
      declared UTC, a site day begins 4 or 5 hours early. Settle the zone of each dataset that carries dates in the
      mapping before the first live plan, and tell the owner what you find (OD-05 covers the control-station dates).
    - Keep nulls as nulls. FOCUS turns a missing date, crew or status into a precise gap; a default hides it.
11. **Read only.** The source account can read and nothing else. Queries are parameterized; the as-of, the watermark
    and the line list are bound values, never text pasted into the SQL.

## 6. How to load and publish in SQL Server

### 6.1 Tables

- **The staged tables** have the exact shape of the S04 contract: names, column order, types, nullability and keys
  (`fixtures/source_contracts/s04_contracts.json`; `python scripts/export_integration_contract.py` writes a blank
  manifest). FOCUS's reference queries read them unchanged and refuse any drift (`SCHEMA_DRIFT`).
- **Supplemental datasets** (16) need a landing table and a query registered in `LIVE_SUPPLEMENTAL_QUERIES`
  (`focus/data/fixture_reader.py`). Only two have one today (DEC-09 gives the order to build them in).
- **Control tables** belong to the loader and are not read by FOCUS's planning queries:

  | Table (your name) | One row per | Holds |
  | --- | --- | --- |
  | Load | pass run | pass, `--as-of`, start, end, status, mapping version, pipeline id |
  | Table load | dataset in a pass | rows read, inserted, updated, deleted; watermark before and after; `source_max_clock`; seconds in Teradata; seconds in SQL Server; status; certified empty or not |
  | Watermark | dataset | the last published watermark and the pass that set it |
  | Generation | scope | a counter that goes up by one each time a pass publishes |

- **Indexes.** Give every staged table its contract key as the clustered key. Add the index the change load joins
  on. FOCUS reads whole tables, so it needs no others; the task-list query first finds the lines with open work, so
  an index on status and line helps it.

### 6.2 Publishing

1. **Load into work tables first.** Bulk-insert what a pass pulled into empty work tables (bulk copy, not row by
   row). Nothing FOCUS reads has changed yet.
2. **Check before publishing** (section 6.4). A pass that fails a hard check publishes nothing.
3. **Publish every table of the pass in one transaction.**
   - A change pull: apply inserts, updates and deletes by key from the work tables.
   - A full load: swap the new table in (`ALTER TABLE ... SWITCH`, `sp_rename` or a view switch). Never truncate
     and insert in place.
   - In the same transaction, stamp the row clocks, advance the watermarks, raise the generation and write the load
     rows.
4. **Turn on row versioning for the database** (`READ_COMMITTED_SNAPSHOT`), so a reader never blocks the loader and
   never sees half a transaction.
5. **Never publish while FOCUS is reading.** FOCUS reads the 44 datasets with separate queries. Every staging job
   and `acquire_admit` share one GitLab `resource_group`, so they cannot overlap (section 8). As a second guard, the
   loader's `--check-generation` (section 7) lets a job fail when the generation moved during the read.

### 6.3 Row clocks and freshness

- Every staged row carries its table's row clock (`LOAD_TIMESTAMP`, `SNAPSHOT_TS` or `LOAD_TS`), in UTC.
- **Every row a pass inserts or updates gets that pass's `--as-of`.**
- **The nightly pass also restamps every table it compared in full and found correct.** FOCUS takes a dataset's age
  from its newest row clock. Without the restamp, a reference table that has not changed for a week would look a
  week old and executable plans would fail on it.
- This is a stopgap. When FOCUS reads a load receipt instead (`DECISION_PLAYBOOK.md`, DEC-07), the row clock can go
  back to meaning "when this row last changed", and an empty table can be certified as correctly empty.
- **An empty result is not "nothing to do".** Today an empty required dataset rejects the run. A shift with truly no
  open shortages needs the certified-empty path of DEC-07. Until then keep the loader's own record of "empty and
  complete" in the table-load row.

### 6.4 Checks before publishing

| Check | A failure means | The pass |
| --- | --- | --- |
| Every dataset of the pass returned, or is certified empty | A query failed | stops; publishes nothing (a required dataset) |
| No duplicate key in a work table | The mapping's grain is wrong | stops |
| No null in a key column | The mapping is wrong | stops |
| The row count of a full load is within a set band of the copy's (start with ±20%) | A broken extract | stops |
| No source clock later than `--as-of` | A clock or zone error | stops |
| Every status value is one FOCUS knows (`scope.status_classes`) | A new status in the source | publishes; reports the count |
| Every new job has its `flow_budget`, requirement and `buyoff_plan` rows | Per-job sources lag the task list | publishes; reports the count |
| Every line with open work has a calendar through its due date, station windows and an economics mapping row | A new line is not fully staged | publishes; reports the lines |

The last three are reported, not blocked: FOCUS turns each into a precise readiness gap, and hiding the rows would
hide the problem.

## 7. The loader's interface and receipt

The loader is company code, named by the protected variable `FOCUS_STAGE_COMMAND`. Its contract extends
`HANDOFF.md` §4.1:

```bash
$FOCUS_STAGE_COMMAND --part cut --as-of "$CI_PIPELINE_CREATED_AT" --receipt ci-receipts/stage.json
```

| `--part` | Pass | Used by |
| --- | --- | --- |
| `cut` | The change pulls of section 3.1 | `stage_sources`, first job of the plan pipeline |
| `preshift` | Everything that changes during a day | `prestage_sources`, 60 minutes before each shift |
| `nightly` | Full loads, full compares and repairs, history | `nightly_sources`, once a day |
| `observations` | The observation feeds | `stage_observations`, after each shift's credit deadline |
| `sources` | A full load of every planning dataset | The first load, and recovery |
| `all` | `sources` and `observations` | The first load, and recovery |

- **`--as-of`** is the single instant of the pass. No query returns anything later.
- **`--receipt`** is a JSON file with no data rows and no secrets. One entry per dataset:

  | Field | Meaning |
  | --- | --- |
  | `dataset`, `target` | FOCUS's dataset name and the staged table |
  | `status` | `PUBLISHED`, `UNCHANGED`, `CERTIFIED_EMPTY`, `FAILED` or `SKIPPED` |
  | `rows_read`, `rows_inserted`, `rows_updated`, `rows_deleted`, `rows_in_copy` | Counts of this pass and the copy's size after it |
  | `watermark_from`, `watermark_to` | The source clock range pulled |
  | `source_max_clock`, `source_lag_seconds` | The newest source clock seen, and its distance from `--as-of` |
  | `row_clock_min`, `row_clock_max` | The staged row clocks after the pass |
  | `seconds_source`, `seconds_load` | Time in Teradata and time in SQL Server |
  | `checks` | The reported checks of section 6.4, with counts |

  and for the pass: `part`, `as_of`, `generation`, `seconds`, `mapping_version`, `new_lines`, `status`.
- **`--check-generation N`** exits 0 when the published generation is still N, and 5 when another pass published
  since. A job can call it before and after it reads.
- **Exit codes:** 0 every dataset published or unchanged; 1 a required dataset failed and nothing was published;
  2 optional datasets failed and the rest were published; 3 the loader is not configured; 5 the generation moved.
- **Running it twice is safe.** The same `--part` and `--as-of` again changes nothing.
- **Credentials.** The staging environment holds a read-only source login and a login that can write the staged and
  control tables only. It never holds FOCUS_FN pipeline, signing or installer credentials.

## 8. The GitLab pipelines

The draft is `.gitlab-ci.yml` (never pushed to a GitLab remote). One variable says which pipeline a schedule runs.

| `FOCUS_PIPELINE_PART` | Pipeline | Jobs |
| --- | --- | --- |
| not set | Plan for a shift | `stage_sources` (`--part cut`), `verify_staged_sources`, `acquire_admit`, `schedule_validate`, `stage_readback`, `sign_accept`, then the forecast jobs |
| `preshift` | Staging before a shift | `prestage_sources` (`--part preshift`), `verify_prestaged_sources` |
| `nightly` | Nightly staging | `nightly_sources` (`--part nightly`), `verify_nightly_sources` |
| `evaluate` | Score a closed shift | `stage_observations`, `evaluate_closed_shifts` |

Rules the draft follows, and why:

- **An unknown part runs nothing.** A misspelled value must never start a plan pipeline.
- **One resource group for every live job** (`focus-$FOCUS_SCOPE_KEY`). A staging job and `acquire_admit` can
  therefore never run at the same time, so FOCUS never reads a copy that changes under it.
- **Live jobs are not interruptible.** A merge to the default branch must not cancel a pre-shift run.
- **`stage_sources` needs no other job.** The pass at the cut starts the second the pipeline does, beside the
  import check, not after it.
- **The staging jobs have time limits:** 5 minutes at the cut, 30 minutes before a shift, 4 hours at night. A pass
  before a shift that is still running 30 minutes before the shift is stopped, so it cannot delay the cut. A
  stopped pass has published nothing or everything (section 6.2).
- **Schema checks run in every staging pipeline.** A column that changed is found 60 minutes before the shift, not
  at the cut.
- **Receipts are artifacts**, kept 90 days: `stage.json`, `stage-preshift.json`, `stage-nightly.json`,
  `stage-observations.json`.

What to add in the company's GitLab, outside this draft:

- **A prebuilt image** for the live jobs, with the Python dependencies, the native wheel and the database drivers
  already installed. The draft installs them in every job, and at the cut that time comes out of the 20 minutes.
- **A runner that is already up** at each schedule, tagged for the scheduling jobs (at least 4 vCPU and 16 GB).
- **Protected variables** for every credential, scoped to their environment. No credential in the repository, in a
  receipt or in a job log.
- **An alert** when a staging pipeline fails or a plan pipeline has not reached `sign_accept` five minutes before
  its shift.

## 9. What FOCUS does with the copy

- **It reads every staged table in full,** at `acquire_admit`, through `queries/source_inputs/*.sql` and the
  registered supplemental queries. It stores what it read with a hash, so the copy needs no history of its own.
- **It checks age.** A dataset whose newest row clock is older than its limit gives `SOURCE_STALE`, and the
  validator then refuses the executable plan. The limits are fixture defaults (18 to 240 hours) and far looser than
  "as of the cut": they catch a loader that stopped, not a table that is an hour old. Set them from the measured
  cadences (DEC-13).
- **Six datasets are age-checked although nothing is planned from them:** `inspection_counts`, `etracs_history`,
  `actual_labor` and `labor_shift` (36 hours), and `cert_course_map` and `dept_team_skill_ref` (168 hours). If the
  nightly pass skips one past its limit, executable plans fail. Either keep them in the nightly pass or take them
  off the list (DEC-13).
- **It never fills a gap.** A job without a duration, a requirement, a calendar or a rate is blocked or left without
  a cost, and the run reports how many (`readiness_gaps` and `objective` in the pipeline status). The loader must
  not fill gaps either.
- **It works out eligibility itself.** Who may do a job comes from the certificates each person holds
  (`mechanic_certs`, `holding_events`) and the certificates each job requires (`soi_cert_requirements`,
  `requirement_census`, `requirement_alternatives`). At 50 aircraft digesting them takes about 5 seconds, and the
  scheduler's working data, which includes who is qualified for what, takes 18. A precomputed matrix
  (`cert_eligibility_matrix`) is display only and never authorizes a crew, so there is nothing to gain from
  computing eligibility in SQL. Staging the raw certificate tables early is what helps: `soi_cert_requirements`
  alone is 27% of all source rows.

## 10. When a pass fails

| What failed | What happens | What to do |
| --- | --- | --- |
| The nightly pass | The copy keeps yesterday's reference data. The passes before each shift still run | Fix and re-run by hand before the 36-hour clock limits expire |
| The pass before a shift | The pass at the cut pulls a longer period and takes longer | Watch the cut's duration; fix before the next shift |
| An optional dataset at the cut (exit 2) | The run continues; FOCUS reports the dataset's gap | Fix before the next shift |
| A required dataset at the cut (exit 1) | Nothing is published and the plan pipeline stops. The previous plan stays the goal of record | See the decision below |
| The schema check | The pipeline stops before FOCUS reads | Fix the staged table or the mapping |

**Open decision (owner; `DECISION_PLAYBOOK.md`, DEC-37).** When the pass at the cut fails but the pass before the
shift succeeded, the copy is complete and about 40 minutes old. Planning on it gives the floor a plan far closer to
reality than the previous shift's, eight hours old. Until the owner decides, the pipeline stops, as shipped.

## 11. The first load, step by step

SQL Server starts empty. Do these in order, and record each result in your progress file.

1. **Inventory.** For each of the 44 datasets and 8 feeds, fill the private mapping
   (`SOURCE_REPLACEMENT_CROSSWALK.md`): the source objects, the grain and key, the change clock and its zone, how
   and how often the source itself is refreshed, and who owns it. For the 14 supplemental datasets without a
   producer, say where the facts live.
2. **Time the queries**, off-hours: a full extract, a one-hour change pull and a key-only pull per dataset. Keep the
   seconds and the rows. Sort the datasets of section 3.1 by their change-pull time: their sum, run in parallel,
   must fit 2 minutes.
3. **Create the staged tables** in the contract shape, the landing tables and the control tables. An authorized
   installer does this; a draft is never installed by a pipeline.
4. **Run the first full load** (`--part all`), off-hours, with the checks of section 6.4.
5. **Check the schema:** `python -m focus.cli source-discover --read-only --mode live`.
6. **Let FOCUS read it once,** read-only: a source run and a FORECAST plan. Review the readiness gaps with their
   owners. Expect many on the first read; each one names the dataset and the reason.
7. **Turn on the nightly pass.** After three clean nights, the passes before each shift. After a clean week, the
   pass at the cut.
8. **Measure for a week** (section 12), then tune the schedule times and the clock limits by their rules.
9. **Then** the shadow runs of `HANDOFF.md` §6.

## 12. What to measure, and the rules for tuning

Keep every receipt. Each week, per dataset: the median and the 95th percentile of `seconds_source`, of
`seconds_load`, of the rows changed, and of `source_lag_seconds`.

| Question | Rule |
| --- | --- |
| Does the pass at the cut fit? | Its 95th percentile is 120 seconds or less. If not, find the slowest dataset and make its query cheaper (section 5, rule 9), or get a change log from its owner. Do not start the cut earlier without telling the owner (DEC-12) |
| Does the pass before a shift fit? | It ends at least 5 minutes before the cut at the 95th percentile. If not, start it earlier |
| Is a dataset in the right group? | A dataset that never changes between nightly passes for two weeks leaves the pass before each shift. One that the nightly compare keeps repairing moves to a faster pass |
| Is the overlap large enough? | The nightly compare finds no row the change pulls missed. If it does, raise the overlap to cover the latest one |
| Are the clock limits right? | Limit = 2 × the 95th percentile of the load interval, in whole hours, never under 6 hours for a dataset loaded before every shift (DEC-13) |
| How current is the plan? | Report `source_lag_seconds` of the task list with every run. With `window.minutes`, it is how old the floor state is when the shift starts |

## 13. Work list

For the company-side agent, in order:

1. The inventory and the query timings (section 11, steps 1 and 2). Report them before building.
2. The staged, landing and control tables.
3. The loader: `--part all` first, then `nightly`, `preshift`, `cut` and `observations`, each with its receipt.
4. The producers of the supplemental datasets, `work_progress` first (DEC-09).
5. The GitLab schedules of section 4, the prebuilt image and the alert.
6. A week of receipts, and the tuning of section 12.

On the FOCUS side, which this plan depends on:

| Item | Why the staging plan needs it | Where |
| --- | --- | --- |
| Read a load receipt: `loaded_at` and certified empty | Unchanged tables stay fresh without rewriting row clocks; an empty table can be correct | DEC-07 |
| The 14 supplemental producers | Without them jobs in work, rosters, leave and receipts are missing, whatever the cadence | DEC-09 |
| Bring FOCUS's own steps to about 13 minutes | The 2 minutes at the cut only help if the rest fits | DEC-36 |
| Clock limits from measured cadences | Today's limits are fixture defaults | DEC-13 |
| Plan on the earlier copy when the cut fails, or stop | An owner decision | DEC-37 |

## 14. What not to do

- **Do not run a full extract at the cut.** Not of the task list, not of anything larger than a few tens of
  thousands of rows.
- **Do not rely on an earlier load alone for floor state.** Jobs, work in progress, shortages, receipts, attendance
  and leave are brought up to the cut by a change pull, every time.
- **Do not stage only the changed rows.** FOCUS reads the whole copy. A table that holds only today's changes would
  make every other job look as if it did not exist.
- **Do not cap rows or lines,** and do not drop completed jobs of a line that still has open work.
- **Do not fill a missing value,** join in a default, or compute eligibility, durations or scores in SQL.
- **Do not truncate and insert in place,** and do not publish tables one by one while FOCUS may be reading.
- **Do not put a real object name, query, credential or row in this repository,** in a receipt or in a job log.
- **Do not move the cut earlier to make room** for a slow query. Every minute before the shift is work the plan
  cannot see. Tell the owner first (DEC-12).

## 15. Where things are

| Need | File |
| --- | --- |
| Datasets, columns, required flags, cutoff columns | `focus/data/datasets.py`; `docs/data/` |
| The contract shape of the staged tables | `fixtures/source_contracts/s04_contracts.json`; `scripts/export_integration_contract.py` |
| The queries FOCUS runs on the copy | `queries/source_inputs/*.sql`; `focus/data/fixture_reader.py` (`LiveSourceReader`) |
| Age and skew checks | `focus/data/freshness.py`; policy `admission.max_source_age_hours` |
| The pipeline draft | `.gitlab-ci.yml`; `tests/integration/test_migration_pipeline.py` |
| The staging contract this page extends | `docs/integration/HANDOFF.md` §4 and §5 |
| Measured times of FOCUS's own steps | `docs/execution/PRODUCTION_SCALE.md`; `tools/time_pre_shift.py` |
| Decisions named here | `docs/integration/DECISION_PLAYBOOK.md`: DEC-07, DEC-08, DEC-09, DEC-11, DEC-12, DEC-13, DEC-36, DEC-37 |
