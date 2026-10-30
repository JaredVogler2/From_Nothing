# Team authority (S07, P05/P06)

Who belongs to which team and shift, and what each source is allowed to prove.

## Layers, strongest first

| Layer | Source | Proves | Never proves |
| --- | --- | --- | --- |
| Effective assignment | `FOCUS_PERSON_TEAM_MAP` (`IN_SCOPE`, `EFFECTIVE_FROM` inclusive, `EFFECTIVE_TO` exclusive, local wall time converted to UTC) | Team and role from the exact instant | Presence on a given day |
| Scheduled roster | `shift_roster` supplemental feed (team, shift number, date range) | Shift membership of an assigned person | Presence; a team without an assignment |
| Attendance / leave / other duty | `FOCUS_ATTENDANCE_SHIFT`, `availability_exceptions`, `carry_in` | Presence or absence on the production day, blocked intervals | Team membership |
| SQL inference | `FOCUS_FN_TeamInference` written by `queries/workforce/infer_mechanic_teams.sql` | A nominal home team for forecasts | Present executable capacity, an assignment |
| Department fallback | HR accounting department → `FOCUS_DEPT_TEAM_SKILL_REF.PRIMARY_TEAM` | A labelled nominal team when there is no team evidence | Anything executable |

A pool's membership is decided at the **shift start instant**. An assignment that becomes effective
exactly at 06:00 local governs the S1 shift starting then; one effective at 06:01 does not.

## SQL-owned inference

- `queries/workforce/infer_mechanic_teams.sql` is the only production algorithm (`S07-plurality-v1`).
  It admits the latest revision of each source event (a voided correction removes the event instead
  of counting it twice). An admitted record is unvoided, carries a team, and starts inside the
  trailing 30-day window [cutoff − 30 d, cutoff).
- Teams are ranked by transaction count, `ORDER BY n DESC,Team ASC`, with team names compared by code
  point (`Latin1_General_BIN2`), so the tie-break does not depend on server collation.
- Effort minutes are the per-(person, team) running-max interval union. They are stored separately
  from the count and never used as the count.
- Each persisted row carries the top share (`decimal(9,8)`), runner-up team and count, total count,
  tie flag, window, first and last evidence, and algorithm version. The legacy
  `FOCUS_MECH_TEAM_INFERRED` row is copied into the evidence payload for comparison only.
- People with no team evidence get a `DEPT_FALLBACK` row only through an actual mapped department.
  If they have team-less evidence and no mapping, the row is `UNKNOWN` with no team. Nobody else gets
  a row, and no team is ever invented.
- The application reads the persisted rows bound to one source run
  (`queries/workforce/read_team_inference.sql`). `focus.data.workforce.reference_inference` mirrors
  the SQL only to verify the contract. `fixtures/workforce/team_inference_contract.json` holds
  hand-derived expected rows covering:
  - ties and three-way shares
  - voided and corrected revisions
  - numeric versus text revision order
  - window boundaries
  - overlapping-interval effort
  - fallback, unknown and no-row cases

## Admission rules (application)

- **True repeats** (identical assignment or roster rows) are deduplicated.
- **Admission conflicts:** an overlapping assignment or roster with a different team or shift is a
  conflict. So is a roster that disagrees with the assignment effective at a rostered shift start.
  All of these are detected before any pool is built. A conflicted person is excluded from every
  pool, and a `WORKFORCE_CONFLICT` gap is raised (no double headcount, no double booking).
- **Stale inference:** an inference row whose window ends more than
  `workforce.inference_stale_after_days` (fixture default 7) before the cutoff is labelled `stale`
  and never supplies a home team. This raises the `TEAM_INFERENCE_STALE` gap.
- **Undefined shift:** a roster naming a shift outside 1..3 raises `ROSTER_SHIFT_UNDEFINED` and gives
  no membership.
- **Nominal versus executable:** roster-only or inference-only membership is `INFERRED_NOMINAL`, which
  counts toward the forecast denominator only. Executable members need an effective assignment that
  agrees with the roster, and on the production day an absence excludes them (`ABSENT`).
- **Empty feeds:** an empty assignment or attendance feed is a `BLOCKS_EXECUTABLE` readiness gap.
- **No borrowing:** people are never borrowed across teams or shifts (A11). Pure pools are keyed
  (site, team, shift start date, shift number, source run).

## Live status

The production adapters are written but have never been installed or probed:

- The inference query, the draft constraints, trigger, view and retention procedure
  (`sql/migrations/004_workforce_inference.sql`), and the refresh strategy are written but **not
  installed**.
- No live clock, HR or assignment feed was read. `ATTENDANCE_SHIFT`, `PERSON_TEAM_MAP` and
  `LABOR_SHIFT` were observed empty in the S04 catalog estimates.

Until those exist and are admitted, live workforce stays unadmitted for executable crews, and
inference remains nominal forecast evidence.
