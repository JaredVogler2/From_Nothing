# Stage P05: SQL-owned mechanic team inference and assignment

Status: **fixture_verified**. Live inference is not installed (see Remaining gaps).

## Changed paths

- `queries/workforce/infer_mechanic_teams.sql`: S07 plurality inference as one run-isolated transaction. It:
  - admits the latest revision per event and drops voided corrections
  - takes the window `StartUtc >= @WindowStart AND StartUtc < @WindowEnd`
  - ranks with `ORDER BY n DESC,Team ASC` using a code-point (BIN2) team collation
  - computes running-max interval-union effort, kept separately from the count
  - stores top share as `decimal(9,8)` with explicit half-away rounding, plus runner-up, tie flag
    and first/last evidence
  - writes an explicit `DEPT_FALLBACK` via `IE787.BERT_DAILY` → `FOCUS_DEPT_TEAM_SKILL_REF`, or
    `UNKNOWN`
  - uses the legacy `FOCUS_MECH_TEAM_INFERRED` only as comparison evidence
- `sql/migrations/workforce_draft.sql`: additive constraints, access-path indexes, sealed-run
  trigger, head readback view, and a retention procedure keeping head/plan-referenced runs.
- `focus/data/workforce.py`:
  - `reference_inference` now mirrors the SQL step for step (revisions, unions, rounding,
    fallback/unknown).
  - Assignments carry exact UTC instants, and membership is decided at the shift start instant.
  - Roster/assignment mismatches and assignment or roster overlaps are detected before any pool is
    built. A conflicted person is excluded everywhere.
  - Stale inference is labelled and never supplies a home team (`TEAM_INFERENCE_STALE`).
  - Undefined roster shifts raise `ROSTER_SHIFT_UNDEFINED`.
- `focus/contracts/planning.py`: `TeamInferenceRecord.stale`.
- `config/policies/rebuild-v1.json`: `workforce.inference_stale_after_days` = 7 (fixture_default).
- `fixtures/workforce/team_inference_contract.json`: SQL-compatible contract. The expected rows are
  derived by hand, not produced by the reference implementation.
- `docs/execution/TEAM_AUTHORITY.md`, `tests/integration/test_team_inference.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/integration/test_team_inference.py -q   -> 13 passed
.venv/bin/python -m pytest tests -q                                       -> 113 passed
.venv/bin/python tools/check_imports.py                                   -> no offenders, no runtime writes
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A08 | **Tie and fallback.** A 2–2 tie between PROD-A and PROD-B goes to PROD-A by team name; minutes never decide. The tie flag is set, top share is 0.50000000, runner-up is PROD-B/2 and effort is kept separately. The persisted SQL rows are consumed with their labels (DEMO-B2-04 tie, DEMO-Q1-04 `DEPT_FALLBACK`, algorithm `S07-plurality-v1`). **Contract fixture.** The hand-derived rows match on voided/corrected revisions, numeric-over-text revision order, window boundaries (start included, end excluded), overlap-union effort, three-way 1/3 shares, half-away rounding (10 s → 0.166667; 30 µs → 0.000001), fallback, `UNKNOWN`, and the no-row case. |
| A09 | **Assignment overrides inference.** DEMO-A1-08 is inferred PROD-B but assigned PROD-A, and is executable only in PROD-A. A move effective exactly at the 06:00 S1 start governs that shift. A move at 06:01 leaves the 10-28 S1 shift on the old assignment, which then conflicts with the roster move: `WORKFORCE_CONFLICT`, excluded from every pool. **Inference never proves presence.** DEMO-B1-03 (inference only) is `INFERRED_NOMINAL`, forecast-only and never admitted presence. DEMO-A1-03 is absent on the production day and excluded (`ABSENT`) even though inference says PROD-A. |
| A10 (P05 part) | True repeats deduplicate. An overlapping inconsistent membership fails admission with a gap, and there is no double headcount. |
| A11 (P05 part) | No member of a real pool comes from another team or shift. |

Also verified:
- Stale inference (window ending 39 days before the cutoff) is labelled `stale` and supplies no
  effective team; a fresh row (1 day) is not stale.
- A roster on shift 4 is a gap, not a pool.

## Fixture versus live

All inference evidence is fixture or contract data. The SQL was not executed against any server. No
claim of installed inference is made.

## Remaining gaps

- Installing the FOCUS_FN migrations, the inference query/procedure and the draft constraints needs
  write authority (consolidated in P23).
- Live clock (`FOCUS_FN_ClockObservation`), HR department and assignment/attendance producers are not
  probed. `ATTENDANCE_SHIFT` and `PERSON_TEAM_MAP` were observed empty in the S04 catalog estimates,
  so live workforce stays unadmitted for executable crews.
- The stale threshold (7 days) and the 30-day window are fixture or manual values awaiting live
  calibration.
