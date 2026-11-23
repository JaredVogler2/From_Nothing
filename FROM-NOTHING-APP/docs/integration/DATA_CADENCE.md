# Data cadence: what is gathered, processed and staged, and how often

**Audience.** The owner, and the company-side coding agent that builds the loader and the GitLab schedules.
[STAGING_PLAN.md](STAGING_PLAN.md) has the loader's detail; this page is the determination itself.

**The owner asked for it (2026-11-12, D-34).** "mechanics to certs certs to jobs and therefore mechanics to eligible
jobs doesn't need ran shiftly it may only need to be weekly so that isn't needed to run all the time. For all data for
the scheduler and dashboard we need to determine cadence for all data gathering and processing and staging to sql
server."

**Status.** Starting values. No Teradata query has been timed and no company system has been read, so each dataset is
placed by how its content changes, by what a stale copy would do and by its size in the synthetic 50-aircraft fleet.
The loader's receipts then move a dataset to a slower or faster pass (section 5). The registry is
`focus/data/cadence.py`; the tables below, the staging plan, the pipeline draft and the freshness limits are held to it
by tests (`tests/unit/test_staging_cadence.py`).

## 1. The answer on one page

Five cadences. Each is its own GitLab schedule, so nothing runs more often than its data needs.

| Cadence | When (site time) | Gathered from Teradata and staged in SQL Server | Processed by FOCUS | The dashboard gets |
| --- | --- | --- | --- | --- |
| **Weekly** | Sunday 07:30 | In full, the large tables that change with engineering releases and training, not with the floor: each job's duration, crew, certificate requirements, inspections and rework class; the certificates people hold; lookups; history no plan reads. 18 datasets, 61% of all rows | Nothing by itself: the next run reads the refreshed copy | Nothing new |
| **Nightly** | 01:15 | Floor state compared in full with its sources and repaired; calendars, teams, rosters, overtime authority and rates whole; the rows that changed in the weekly tables; yesterday's completed work. 28 datasets | The full solve with the improvement search, in a plan run of its own after the pass | A re-optimized plan: the plan the next run repairs |
| **Before each shift** | 60 minutes before the shift | Everything that changes during a day, so that the last pull is small. 21 datasets | Nothing | Nothing new |
| **At the scheduler run** | 20 minutes before the shift | Only what changed since the pass before: jobs new, started and completed, rework, work in progress, shortages and receipts, attendance, leave, revoked certificates, re-planned calendars, new aircraft. 18 datasets, 2 minutes | Everything the schedule needs, from the whole copy: readiness, eligibility, durations, the repair of the plan, its checks, its acceptance, and the dashboard's data | The shift's task lists, then delivery dates and late cost. No person accepts anything: once the checks pass, the schedule is the answer (D-34) |
| **After each shift** | 65 minutes after its end | What happened: the 8 observation feeds | The scores of the closed shift | Team scores and performance |

Before this determination the nightly pass loaded or compared nearly every row. It now reads 39% of the rows whole,
and the weekly pass 59%.

## 2. Every dataset

"whole": the pass reads the complete table or the complete current set again and repairs the copy. "changes": the pass
pulls only rows that are new or changed. "–": the pass does not touch it. [STAGING_PLAN.md](STAGING_PLAN.md) section
3 says exactly what each pass does with each dataset, the key it loads by and what goes wrong when it is stale.

**Floor state**

| Dataset | What it is | Weekly | Nightly | Before each shift | At the run |
| --- | --- | --- | --- | --- | --- |
| `master_task_list` | Every job on every aircraft with open work, and its status | – | whole | changes | changes |
| `neo_graph_rework` | Which rework job belongs to which job | – | whole | changes | changes |
| `work_progress` | How much of a job in work is done | – | – | whole | whole |
| `etracs_active` | Open part shortages | – | whole | whole | whole |
| `part_events` | Parts received, reopened or cancelled | – | – | changes | changes |

**Job facts**

| Dataset | What it is | Weekly | Nightly | Before each shift | At the run |
| --- | --- | --- | --- | --- | --- |
| `flow_budget` | Each job's duration and crew size | whole | changes | changes | changes |
| `soi_cert_requirements` | The certificates each job requires | whole | changes | changes | changes |
| `buyoff_plan` | The inspections each job needs | whole | changes | changes | changes |
| `inspection_holds` | Where in a job an inspector must stop it | whole | changes | changes | changes |
| `rework_classification` | The class and origin of each rework job | whole | changes | changes | changes |
| `requirement_census` | Jobs confirmed to need no certificate | whole | changes | changes | changes |
| `requirement_alternatives` | Certificates approved as alternatives to each other | whole | changes | – | – |
| `inspection_counts` | Inspection counts, for cross-checks only | whole | – | – | – |

**Calendar**

| Dataset | What it is | Weekly | Nightly | Before each shift | At the run |
| --- | --- | --- | --- | --- | --- |
| `cday_op_plan` | The working days of each aircraft's line | – | whole | changes | changes |
| `cs_windows` | Control-station dates, and with them the delivery due date | – | whole | changes | changes |

**People**

| Dataset | What it is | Weekly | Nightly | Before each shift | At the run |
| --- | --- | --- | --- | --- | --- |
| `mechanic_certs` | The certificates each person holds, with their dates | whole | changes | – | – |
| `holding_events` | Certificates revoked or reinstated | – | – | changes | changes |
| `person_team_map` | Which team each person is on | – | whole | changes | – |
| `shift_roster` | Who is rostered on which shift | – | whole | whole | – |
| `team_inference` | Each person's team as guessed from past labor, for review only | whole | – | – | – |
| `attendance_shift` | Who is present for the coming shift | – | – | whole | whole |
| `availability_exceptions` | Planned leave and other absence | – | – | changes | changes |
| `ot_authorizations` | Authorized overtime | – | whole | whole | – |
| `carry_in` | People already on duty when the shift starts | – | – | whole | whole |

**Reference**

| Dataset | What it is | Weekly | Nightly | Before each shift | At the run |
| --- | --- | --- | --- | --- | --- |
| `pnet_dag` | The template order of jobs: which must come before which | – | whole | – | – |
| `inspection_standards` | Minutes per kind of inspection | whole | – | – | – |
| `cert_course_map` | Which course gives which certificate | whole | – | – | – |
| `dept_team_skill_ref` | Department to team and skill lookup | whole | – | – | – |
| `scope_receipt` | Aircraft left out of scope on purpose | – | changes | – | – |
| `people_directory` | Display names | – | whole | – | – |
| `cert_eligibility_matrix` | A ready-made table of who may do which job: display only | whole | – | – | – |
| `mech_team_inferred` | An older team guess, for comparison only | whole | – | – | – |

**History**

| Dataset | What it is | Weekly | Nightly | Before each shift | At the run |
| --- | --- | --- | --- | --- | --- |
| `duration_episodes` | Completed jobs, for the duration statistics | – | changes | – | – |
| `clock_observations` | Raw clock intervals, for the duration statistics | – | changes | – | – |
| `etracs_history` | Closed shortages | changes | – | – | – |
| `actual_labor` | Labor hours booked per job | whole | – | – | – |
| `labor_shift` | Labor per person, job and shift | changes | – | – | – |

**Economics**

| Dataset | What it is | Weekly | Nightly | Before each shift | At the run |
| --- | --- | --- | --- | --- | --- |
| `econ_mapping_revision` | Which customer mapping is current | – | whole | – | – |
| `econ_aircraft_mapping` | Each aircraft's customer and model | – | whole | changes | changes |
| `econ_scenario` | The active rate scenario | – | changes | – | – |
| `econ_contract_assignment` | Contract terms assigned to an aircraft (optional) | – | changes | – | – |
| `econ_term` | The daily late-delivery rates | – | changes | – | – |
| `econ_original_sale_basis` | Sale prices, for booked liability (optional) | – | changes | – | – |
| `econ_liability_checkpoint` | Liability already booked (optional) | – | changes | – | – |

**The certificate chain, the owner's example.** `soi_cert_requirements` (what each job requires) and `mechanic_certs`
(what each person holds) are read in full once a week, with the course lookup and the ready-made matrix. Four small
things keep a plan right in between:

- the requirement rows of a **new job** arrive with the job, at every run;
- a certificate's **expiry date** is in its row, so a certificate that runs out during the week stops counting on
  its day without any load;
- a **revoked** certificate arrives as an event at the next run (`holding_events`);
- a certificate **gained or renewed** during the week is picked up by the nightly pass, where the source can say
  which rows changed. Where it cannot, it counts from the next weekly pass: nobody is planned on work they are not
  qualified for, but for a few days somebody may not be planned on work they have just qualified for.

## 3. What FOCUS processes, and when

| When | What FOCUS computes | Time at 50 aircraft on the test machine |
| --- | --- | --- |
| Every run | Reads the whole copy and digests it: which jobs are ready, who may do which job, how long each takes. Repairs the accepted plan, checks it, accepts it and builds the dashboard's data; then the same for the delivery plan | 9 to 11 minutes to the accepted shift plan and 19 to 22 for the whole schedule, in four jobs, depending on the machine |
| Nightly | The full solve with the improvement search, in place of the repair | 10.8 and 22.9 minutes |
| After each shift | Collects what happened and scores the shift that closed | not timed at this size |
| Weekly | Nothing of its own | |

- **Eligibility stays in every run, and costs seconds.** The chain the owner names (people to certificates,
  certificates to jobs, people to jobs) takes FOCUS about 5 seconds of a run at 50 aircraft, and it has to be right
  for the people who are present today. Computing it weekly inside FOCUS would save nothing. What costs is gathering
  the certificate tables from Teradata, and that is what moved to the weekly pass. A ready-made matrix
  (`cert_eligibility_matrix`) is for display only and never authorizes a crew.
- **What is not yet at its own cadence.** FOCUS has no incremental mode: every run digests, stores and loads all of
  the data again and rebuilds every part of the dashboard's data, although about 1% of the rows changed since the run
  before. In the measured run that is roughly 450 of 1,225 seconds, over a third: the snapshot built, stored and
  turned into dashboard data, and three later loads of it. Digesting the slow tables when they change and only the
  floor state at each run is the same cadence idea applied inside FOCUS. It is not built, and it
  is the largest step left towards a whole schedule well inside 20 minutes (`DECISION_PLAYBOOK.md`, DEC-36).

## 4. The schedules

| Schedule (site time, America/New_York) | Cron | Variables | Jobs |
| --- | --- | --- | --- |
| Weekly staging | `30 7 * * 0` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=weekly` | `weekly_sources`, `verify_weekly_sources` |
| Nightly staging | `15 1 * * *` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=nightly` | `nightly_sources`, `verify_nightly_sources` |
| Nightly full solve | `45 2 * * *`, after the nightly staging | `FOCUS_MODE=live`, `FOCUS_PLANNING=full` | the plan pipeline |
| Staging before shift 1, 2, 3 | `0 5`, `30 13`, `0 22` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=preshift` | `prestage_sources`, `verify_prestaged_sources` |
| Plan for shift 1, 2, 3 | `40 5`, `10 14`, `40 22` | `FOCUS_MODE=live` | `stage_sources`, `verify_staged_sources`, `plan_unsigned`, `sign_accept`, `delivery_unsigned`, `forecast_sign_accept` |
| Score shift 1, 2, 3 | `35 15`, `5 0`, `5 7` | `FOCUS_MODE=live`, `FOCUS_PIPELINE_PART=evaluate` | `stage_observations`, `evaluate_closed_shifts` |

- **One pipeline or several.** The owner leaves this free. The draft (`.gitlab-ci.yml`) is one definition with one
  variable that says which pipeline a schedule runs, so each cadence is already its own pipeline run.
- **No two of them run at once.** Every live job shares one resource group, so a pass never changes the copy while a
  plan run reads it. A pass that runs for hours therefore holds back whatever starts behind it: the weekly pass is
  placed where six hours hold no other schedule, and has a 4-hour limit.
- **An alert, not a person.** Nobody promotes a plan (D-34). The alert when a plan pipeline has not accepted its plan
  five minutes before the shift is what tells a person that a shift has no new plan; the plan before it stays in
  force.

## 5. What decides a cadence, and how it changes

1. **How often the content changes in a way that changes a plan.** Floor state changes all day. Job facts,
   certificates and lookups change with releases and training.
2. **What a stale copy does.** Where a missing row blocks a job with a named gap, a slower pass is safe. Where a
   stale copy makes the plan wrong and nothing flags it (the template order of jobs, calendars and due dates, the
   rates), the check stays daily although the data seldom changes.
3. **Size.** A large table is read whole seldom, and what changed is pulled in between.
4. **The source's own refresh.** Never pull a dataset more often than its source is refreshed.

Then the receipts decide (`STAGING_PLAN.md` section 12): a dataset whose pull returns no row for two weeks moves to a
slower pass, and one that a full compare keeps repairing moves to a faster one. A change is one edit of the registry,
with the tables and the limit beside it.

**Freshness limits follow the cadence** (policy `admission.max_source_age_hours`). FOCUS refuses an executable plan
when a dataset it rests on is older than its limit. Each limit is at least twice the interval of the pass that reads
the dataset whole, so one missed pass never stops a plan: 18 hours for attendance, 36 for open shortages, 48 for what
the nightly pass reads whole, 240 for the calendars and 336 (two weeks) for what the weekly pass reads whole. At 48
and at 336 hours a second missed pass in a row does stop executable plans. Only the twelve datasets a plan is built from are on the list: a table no plan reads can no longer
leave the floor without a plan.
