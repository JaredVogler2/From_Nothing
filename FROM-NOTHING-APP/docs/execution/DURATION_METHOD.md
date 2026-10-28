# Duration method (S08, P07)

How FOCUS turns flow standards and completed rework episodes into scheduled elapsed minutes and crew
demand. Only `focus.data.durations.resolve` produces these estimates; the SQL in `queries/durations`
reproduces the same statistics in the database.

## Baseline work: flow standards

- **Exact composite lookup.** `FOCUS_FLOW_BUDGET` is looked up by exact `(LINE_NUMBER, JOB_NUMBER)`.
  The same job number on two lines is two standards (fixture `FGI`: 30 min on 1301, 40 min on 1302).
- **Fields kept.** Every field is retained: `CRITICAL_FLOW_MINUTES`, `TOTAL_MECHANICS`, primary
  count/duration, assist count/labor, total labor, source and load time.
- **Crew demand is `TOTAL_MECHANICS`.** It applies under the admitted SIMULTANEOUS semantics (open
  decision OD-01; live use is executable-gated until the producer confirms). `PRIMARY_MECHANIC_COUNT`
  never replaces it (`ROOT5`: TOTAL 3, PRIMARY 2 → crew 3, 270 person-minutes).
- **Sequential counts are blocked.** A SEQUENTIAL semantics policy blocks the job
  (`FLOW_SEMANTICS_SEQUENTIAL`) until explicit role phases exist.
- **Invalid standards are unavailable, never defaulted or clamped to 1:**
  - zero, null or negative counts → `FLOW_CREW_INVALID`
  - missing or non-positive flow → `STANDARD_INVALID`
  - no row → `STANDARD_MISSING`
  - rows disagreeing for the same exact key → `FLOW_CONFLICT`

  True repeats deduplicate, and the latest load is kept.
- **Standard person effort** = elapsed × crew (explicit fixture policy
  `duration.standard_effort_basis`).

## Rework (NC/PU/CI): raw clocks to episodes

- **Admitted clocks.** For each `SOURCE_EVENT_KEY`, only the latest `SOURCE_REVISION` is admitted.
  Numeric revisions outrank text, as in `TRY_CONVERT(bigint, …) DESC`. A correction replaces the
  original, and a voided correction removes the event, so nothing is counted twice.
- **Island method (running MAX).** Intervals are sorted by start. One that starts at or before the
  running maximum end merges into the island; otherwise a new island starts.
  - Nested `[0,60]`, `[10,20]`, `[60,90]` form one 90-minute island.
  - Adding `[100,110]` gives active elapsed 100, and the 10-minute gap is preserved.
  - Short gaps are not bridged, long clocks are not capped, and short clocks are not dropped.
- **Three separate measures:**
  - *Active elapsed:* the job union of all owners.
  - *Person effort:* each owner's (`PERSON_KEY`) own union, summed. An actor who entered or audited a
    clock is not its owner.
  - *Cycle:* first-on to last-off.

  A concurrency sweep over the owner unions gives the crew profile and the peak.
- **Exactness.** Values are exact microseconds and exact fractional minutes. Quantization is
  labelled: 6 dp rounded half away from zero for display and persistence (`decimal(19,6)`), and a
  ceiling to whole scheduling minutes only after the statistic is chosen (P75 51.75 → 52 ticks,
  with the precise value kept).

## Statistics

- **Admission.** Episodes are admitted only when `verified_complete` with a positive elapsed value and
  a completion instant. Reopened or unverified episodes are censored and counted with reasons, so a
  survivor-only current view cannot leak in.
- **Groups.** Exact `(class, team)` first, then the approved broader `(class)` group
  (`duration.group_fallback`). A sourced engineered estimate would come next only with a sourced,
  approved dataset; none exists, so the next step is **unavailable**.
- **Percentiles.** Continuous percentiles use h=(n−1)p, x_lo + (h−lo)(x_hi−x_lo), computed exactly
  with fractions. SQL `PERCENTILE_CONT` uses the same definition.
- **Persisted fields.** P50, P55, mean, P75 and P90, plus:
  - raw, admitted and excluded n, with exclusion reasons
  - sample window, models, crews, unit
  - source hash (admitted keys and values), query hash (capture receipt) and method hash
  - policy id
  - a distribution-free order-statistic interval for P75 (binomial coverage ≥
    `duration.confidence_level`, 0.90)
- **Mean ordering.** The mean need not lie between P55 and P75, and nothing assumes it does. A skewed
  sample with mean > P75 is legitimate.
- **Sufficiency and staleness.** `duration.minimum_fixture_sample` (30) decides sufficiency. A cohort
  is stale when its newest completion is more than `duration.stale_after_days` before the cutoff. A
  cohort that is insufficient or stale is **unavailable**: `DURATION_UNAVAILABLE`, never a flat 60 or
  1.
- **Elapsed basis only.** The consumed default is P75 of the elapsed cohort. A person-effort
  percentile divided by the current crew is never used; effort means are descriptive only.

## Statistic selection (future toggle)

`duration.default_statistic` (P75) is the one user constant that may be overridden: P55, MEAN, P75
or P90.

- An override is a new policy id and hash (`Policy.with_overrides`).
- `snapshot_for_run(services, run_id, policy=…)` rebuilds a new snapshot from the same immutable
  datasets, with statistics bound to the new policy. Statistic ids include the policy id.
- The original snapshot, its statistics and anything accepted from it are unchanged. The active
  policy still resolves the original snapshot.

## S18 fixture reference (verified by tests)

- **Cohort statistics.** Each of NC, PU and CI has 30 completed episodes with elapsed values 30…59:

  | Statistic | Value |
  | --- | --- |
  | mean | 44.5 |
  | P50 | 44.5 |
  | P55 | 45.95 |
  | P75 | 51.75 |
  | P90 | 56.1 |
  | P75 90% interval | [48, 56] |

  Scheduled ticks are 52.
- **Union demo.** The clock union demo gives active 100, effort 110, cycle 110 and a 10-minute gap,
  with one voided clock excluded.

## Live status

The production SQL is written but has never been installed or run against a live database:

- `queries/durations/build_observations.sql`, `queries/durations/percentiles.sql` and
  `sql/migrations/durations_draft.sql` are drafted but **not installed**.
- No live clock or completion source was read. `FOCUS_ACTUAL_LABOR` holds only aggregate person
  effort with no raw intervals, so it cannot calibrate elapsed episodes.
- Real cohort calibration of the minimum sample, staleness, comparable grouping and the
  TOTAL_MECHANICS semantics (OD-01) remains open.
