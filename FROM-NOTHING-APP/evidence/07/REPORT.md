# Stage P07: Duration gauge, standards and SQL descriptive statistics

Status: **fixture_verified**

## Changed paths

- `focus/data/durations.py`:
  - `FlowIndex` uses an exact composite key and reports conflicts (`FLOW_CONFLICT`); true repeats
    deduplicate to the latest load.
  - The uncertainty text names n, window, all statistics and the P75 interval.
- `focus/data/duration_gauge.py`:
  - **Clock admission.** The latest revision per event is admitted (corrections replace, voided
    corrections remove). Metrics are exact microseconds, minutes are exact fractions, and 6 dp
    display quantization is labelled.
  - **Statistics.** They now carry models, a source hash, the capture query hash, and a
    distribution-free order-statistic interval for P75 (`quantile_interval`). Admission requires a
    completion instant.
  - Statistic ids are policy-bound.
- `focus/data/acquisition.py` and `snapshot_builder.py`: capture query hashes reach the statistics.
  `snapshot_for_run` persists policy-bound statistics for a toggled policy.
- `config/policies/rebuild-v1.json`: `duration.confidence_level` = 0.90 (fixture_default).
- `queries/durations/build_observations.sql`: SQL episode metrics (island unions per job and per
  owner, cycle), with censored episodes kept as `Admitted=0` plus a reason.
- `queries/durations/percentiles.sql`: `PERCENTILE_CONT` P50/P55/P75/P90 and mean per exact and
  broader group. Group hashes equal the application's canonical hashes (lowercase SHA-256 of UTF-8
  JSON), and the method hash is passed in.
- `sql/migrations/durations_draft.sql`: constraints, `UX(RunId, GroupKeySha, PolicyId)`, indexes,
  sealed triggers, head readback view, retention.
- `tools/build_duration_stats.py`, `docs/execution/DURATION_METHOD.md`,
  `tests/unit/test_duration_gauge.py`, `tests/integration/test_duration_policy.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_duration_gauge.py tests/integration/test_duration_policy.py -q  -> 18 passed
.venv/bin/python -m pytest tests -q                                                                      -> 113 passed
FOCUS_RUNTIME_DIR=<scratch> .venv/bin/python tools/acquire_sources.py --mode fixture --scenario manufacturing_demo -> ok, head moved
FOCUS_RUNTIME_DIR=<scratch> .venv/bin/python tools/build_duration_stats.py
  -> NC/PU/CI (class and class+team): n=30, mean 44.5, P50 44.5, P55 45.95, P75 51.75, P90 56.1,
     P75 90% interval [48, 56], excluded {STATUS_REOPENED: 1, STATUS_UNVERIFIED: 1}
  -> UNION-DEMO: active 100, effort 110, cycle 110, gap 10, max concurrent 2, one voided clock excluded
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A15 | **Composite lookup.** FGI is 30 min on 1301 and 40 min on 1302. ROOT5 keeps every field (90/3/2/1/270) and demands TOTAL = 3, never PRIMARY = 2, for 270 person-minutes. **Invalid counts.** TOTAL 0 (TST6B), null (CHILD5) and −2 (NEXT5) are `FLOW_CREW_INVALID`, unavailable and never defaulted to 1. A disagreeing duplicate row is `FLOW_CONFLICT`; a true repeat deduplicates. **Sequential semantics** block the job until role phases exist (`FLOW_SEMANTICS_SEQUENTIAL`). |
| A16 | **Unions.** `[0,60]`, `[10,20]`, `[60,90]`, `[100,110]` give active elapsed 100 with the 10-minute gap kept; the nested and touching clocks form one 90-minute island. Owner unions give effort 110 (X 90, Y 20), cycle 110 and crew profile {1: 90, 2: 10}. An auditing actor does not change effort. **Running MAX** is not previous-end, with no 600 cap and no short-clock removal. Corrections count once (numeric revision 10 beats 9), and voided corrections remove the event. |
| A17 | **Persisted statistics.** P50/P55/mean/P75/P90 = 44.5/45.95/44.5/51.75/56.1 are persisted with n (30 of 34), exclusion reasons, window, unit, models, crews, source/query/method hashes and the P75 interval. **Scheduling.** NC3/PU3 schedule P75 51.75 → 52 ticks from the elapsed cohort. The distinct effort values (3e + 7) never become the statistic: effort/crew would give 70.25. |
| A18 | **P90 toggle.** P90 is a new policy hash and a new snapshot id with policy-bound statistic ids: NC3 uses 56.1 → 57 ticks. The original snapshot re-reads unchanged with P75/52, and the active policy still resolves the original. **Skewed sample.** Mean 58.75 > P75 10 is accepted with no ordering assertion. **Unavailable cohorts.** An insufficient cohort (29 of 30) or a stale one (2024 completions) is `DURATION_UNAVAILABLE`, never a flat 60; other classes are unaffected. |

## Fixture versus live

The episodes and raw clocks are S18 fixture data. The SQL observation and percentile scripts are
drafts and were not executed.

## Remaining gaps

- There is no live raw clock/episode producer. `FOCUS_ACTUAL_LABOR` has only aggregate person effort,
  so it cannot calibrate elapsed episodes.
- Real cohort calibration is still open: minimum sample, staleness, model/process grouping and the
  TOTAL_MECHANICS simultaneity (OD-01).
- No engineered-estimate source exists, so the fallback ladder ends at unavailable.
- Installing the duration tables, procedures and triggers needs write authority (P23).
