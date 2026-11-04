# Source readiness (P03-P07)

The readiness of each source is recorded separately for fixture and live use.
"Verified" for a fixture means the persistent fixture protocol was exercised by
tests. Live entries come from the manual's S04 authoring observations
(2026-10-27); this build environment could not probe live SQL.

## Census coverage

| Capability | Fixture (`manufacturing_demo`, 6 aircraft, 19 jobs) | Live |
| --- | --- | --- |
| Complete WIP census | Verified: 19/19 jobs reconciled (admitted 17, blocked 1, verified complete 1). Scale fixture: 51/51 aircraft reconciled, including one entirely blocked line and one missing standards (A03). | Not probed. S04 observed 150 distinct non-COMPLETE lines and 192,525 catalog rows; all would be reconciled without a cap. |
| Statuses | Verified: raw status counts preserved; unknown statuses quarantined. | OD-13: live status semantics need owner confirmation; PREVIEW/FORECASTED stay forecast-only. |
| Standards and crews | Verified: TOTAL drives demand; TOTAL=0 is blocked. | 984 invalid/null/nonpositive TOTAL_MECHANICS (S04) become blocked jobs, never defaulted. OD-01 decided (owner, 2026-10-31): TOTAL_MECHANICS is the simultaneous crew. |
| Certification requirements | Verified: AND/OR bundles; affirmative census; unknown is blocked; slash/ISO/compact dates agree; sentinel only by policy. | Positive-only rows cannot prove cert-free work (OD-09). Date encodings to profile with `queries/qualifications/holding_validity_profile.sql`. |
| Convenience eligibility matrix | Certified empty in the demo (non-numeric fixture keys); projection-only notes verified by tests. | `FOCUS_CERT_ELIGIBILITY` is readable as a display projection without names; labels never authorize. |
| Workforce assignment/attendance | Verified with fixture roster and attendance (synthetic=false demonstration origin); membership decided at the shift start instant; conflicts excluded everywhere. | Empty feeds (PERSON_TEAM_MAP 0, ATTENDANCE_SHIFT 0): no executable crews (OD-10). |
| Team inference | Verified: SQL-owned rows consumed; hand-derived contract (`fixtures/workforce/team_inference_contract.json`) matches the reference semantics; stale rows labelled. | `infer_mechanic_teams.sql` not installed; no clock or HR department feed probed. |
| Rework durations | Verified: S18 cohorts (P75 51.75 → 52 ticks, 90% interval [48, 56]); insufficient or stale cohorts unavailable. | No raw clock/episode producer; FOCUS_ACTUAL_LABOR is aggregate person effort only. |
| Per-line calendars | Verified: one active load (7001), dense bounds 2026-10-24..2026-11-15. | 5,079 WORKING / 2,409 WEEKEND / 1,305 NONWORKING active rows observed; generation selection per line required. |
| CS windows | Verified: same-run lineage, station 744, signed offsets preserved. | 40,840 rows; date basis open (OD-05). |
| Parts | Verified: linked, unresolved used-on and unknown ECD cases. | 450 active shortages with null ECD and 1,484 blank USED_ON become blocked/conditional gaps. |
| Economics | Verified: assumed terms labeled; one aircraft unmapped stays unknown. | No production default rate; due date decided (OD-06); terms, caps and checkpoints open (OD-12). |
| Timezone policy | Fixture producer receipts declare America/New_York. | The database holds UTC (owner, 2026-10-31); live runs set `FOCUS_SOURCE_TIMEZONE_MAP_JSON` to `{"*": "UTC"}` (`manifest.yml`, `.gitlab-ci.yml`). Without a map, master_task_list, cday_op_plan and cs_windows carry `SOURCE_TIMEZONE_POLICY_MISSING`. |

## Clock semantics (P03 step 5)

- `datetime2(0)` (e.g. `FIRST_AVAILABLE`) and `datetime2(3)` (`LOAD_TIMESTAMP`)
  have different precision. Raw text is preserved; aware UTC is derived only
  through the declared dataset timezone policy.
- S04 observed server `SYSDATETIMEOFFSET=2026-10-27T21:38:02.5354146Z`. A UTC
  server clock alone does not prove that source columns are UTC; the owner
  decided on 2026-10-31 that the database columns hold UTC, and live runs
  declare it (`{"*": "UTC"}`).
- DST: local times inside a spring-forward gap shift forward to the
  transition; ambiguous local times use their first occurrence (fixture
  policy).
- Capture skew: all datasets of a run must be captured within
  `admission.max_capture_skew_minutes` (30) of each other, else `CAPTURE_SKEW`.
  In fixture mode only the five datasets with a cutoff column
  (attendance_shift, availability_exceptions, holding_events, part_events,
  work_progress) hide rows after the cutoff; the live reader filters nothing,
  so the loading process must publish each load atomically
  (`docs/integration/HANDOFF.md`).

## Probe commands

```bash
python -m focus.cli source-discover --read-only --mode fixture
python -m focus.cli source-discover --read-only --mode live
python -m focus.cli acquire --mode fixture --scenario manufacturing_demo
```

The live discovery command needs the FOCUS_SQL_* settings. Without them it
reports `not_probed`.
