# Stage P08: Per-line calendars, three shifts and CS windows

Status: **fixture_verified**

## Changed paths

- `focus/data/cs_windows.py`:
  - The date mode and the date-to-UTC convention must be admitted values (`RESOLVED` /
    `OFFSET_FROM_REFERENCE`, `LOCAL_MIDNIGHT`); otherwise every needed (line, station) is
    `POLICY_MISSING`, with no guessed convention.
  - Dates use the `cs_windows` dataset's declared source timezone, never the site default
    (missing zone → `CS_SOURCE_TIMEZONE_MISSING`).
- `focus/data/snapshot_builder.py`: per-dataset zones for CS, certificate and part dates (`_zone`).
- `focus/data/qualifications.py`: a missing source timezone makes holding validity UNKNOWN.
- Already in place and now covered by tests:
  - `focus/calendar.py`: independent local→UTC shift endpoints, fixture protected windows,
    the cutoff mapped through breaks, OT window, bounded line calendars and the S3 rule.
- `queries/calendars/calendar_generations.sql`, `queries/calendars/cs_window_grain.sql`: read-only
  readiness.
- `tests/unit/test_calendar.py`, `tests/integration/test_cs_lineage.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_calendar.py tests/integration/test_cs_lineage.py -q   -> 15 passed
.venv/bin/python -m pytest tests -q                                                               -> 148 passed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A19 | Working Saturday 10-24 is allowed and the weekday holiday 11-02 is denied. A hole (10-29) and an out-of-bounds date (11-04) are UNKNOWN: no weekday fallback, no extension, no borrowing of another line. Two active generations are `CALENDAR_GENERATION_CONFLICT`. An explicit producer selector (`MAX_LOAD_ID`) is used only when declared by policy and is labelled. Inactive rows give `MISSING`. |
| A20 | S3 on nonworking Sunday 10-25 (working Monday) and on holiday 11-02 (working Tuesday) is allowed (`S3_EXCEPTION`). S3 on nonworking Saturday 10-31 before nonworking Sunday is denied. S1/S2 on a nonworking day are denied. With no next-day evidence the result is UNKNOWN (`S3_NEXT_DAY_UNKNOWN`). |
| A21 | **Working-day S3.** S3 on working Friday 10-30 is allowed although 10-31 is a weekend. It is labelled by its 23:00 start date. **Fall back.** 10-31 S3 runs 03:00Z–11:00Z (480 real minutes, budget 340) and the next S1 starts at 11:00Z. **Spring forward.** 2027-03-13 S3 is 360 minutes and its budget is capped at the open minutes (< 340); no hour is manufactured. **No packed day.** Consecutive S1 starts are 1500 then 1440 minutes apart. |
| A22 | **Cutoff.** S1 cutoff coordinate 400 maps through the breaks to 13:45 EDT: cutoff−1 and cutoff are valid, cutoff+1 is invalid. OT60 exists after 14:30 but never moves the cutoff, and is not a new-start window. **Finish fit** is separate: 60 minutes from 13:45 needs OT (30 regular + 30 OT, POST skipped); 120 minutes does not fit (+60 at most). |
| A23 | **Exact station.** Station 744 (gate on day 0) and station 750 (later window) stay separate, with no min/max widening. Earliest (10-26 04:00Z), gate (10-28) and planning-proxy due (gate + 2 = 10-30, station 744 only) are distinct. **Past gates** are retained, and there is no hard latest. Facts and evidence name the same source run. |
| A24 | **Raw values preserved.** Signed offsets (−2/+1) are kept raw. In offset mode with a WORKDAY basis, −2 workdays from 10-27 skips the weekend to 10-24. **Blocking.** Missing offset basis → `CS_CONVERSION_POLICY_MISSING`. Two active loads → `CS_LOAD_CONFLICT` (no MAX). Disagreeing duplicates → `CS_ROW_CONFLICT`. Inactive rows and a neighbouring station never fill a gap. **Missing policy.** No date-to-UTC convention, no date mode, or no source timezone gives `POLICY_MISSING`. In a snapshot this blocks every open job with `CS_CONVERSION_POLICY_MISSING`. |

## Fixture versus live

Calendars and CS rows are S18 fixture data (load 7001). The live calendar generation choice and
the CS date semantics (OD-05) remain open; the readiness SQL was not run against a live database.

## Remaining gaps

- The live producer must confirm the calendar generation and grain, and the CS date mode, offset
  basis and anchor semantics (OD-05).
- Real labor-policy break and OT placement replaces the fixture protected windows when sourced.
