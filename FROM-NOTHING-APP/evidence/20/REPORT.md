# Stage P20: Operational dashboards and audited workflows

Status: **fixture_verified**. All 17 S15 views and the Arena, quality, turnover and history panels render in
both layouts from canonical API projections, with working command workflows. Everything ran against
deterministic fixture runtimes built by `demo_run`. No SQL, live identity, live signing or deploy was used.

## Changed paths

### Projections and API

- `focus/web/serializers.py`: new projections:
  - `team_lead`: server-ranked actions with the shared dispatch rule and exact crews, plus emergency-addition
    proposals with reasons;
  - `standards` (IE), `budget`, `gantt`, `development`, `arena`, `turnover`, `history_view` and
    `scenarios_view`.
  - Extended projections:
    - My Day: certificates, a certificate check per duty, the current duty and planned dates.
    - Capacity: skill demand (crew size against qualified admitted people) and unknown attendance.
    - Shortages: descendants and receipt versus ECD.
    - Aircraft: timeline and completion history across accepted plans.
    - Fleet: burndown.
    - Quality: inspector competition.
    - Performance: unit, offplan and OOS rows.
  - Shared immutable caches: validation input, holding index and accepted plans.
- `focus/web/api_views.py`: `GET /team-lead`, `/standards`, `/budget`, `/gantt`, `/development`, `/arena`,
  `/turnover`, `/history`, `/scenarios` and `/scenarios/{id}`, for 39 registry operations in all.
- `focus/web/api.py`: `/context` choices (teams with real teams first, lines, obligation dates, default date),
  `POST /scenarios` and `POST /runs/{run_id}/cancel`.

### Domain and workers

- `focus/publication/dispatch.py`: pure `assess_action`, shared by `dispatch_check` and the Team Lead ranking.
- `focus/performance/amendments.py`: pure `assess_addition`, shared by proposals and `amend_goal`.
- `focus/analytics/scenarios.py`: isolated scenarios.
  - Typed changes: `virtual_staffing`, `eta_forecast`, `duration_statistic`, `daily_cost` and `calendar_day`.
  - The snapshot is copied into its own namespace; a same-settings baseline and the changed copy run with
    purpose SCENARIO.
  - Results are immutable. Also `worker_scenario` and `cli_scenario`.
- `focus/engine/max/optimizer/orchestrator.py`: `schedule_snapshot`, used by `schedule` and scenarios.
- `focus/publication/service.py`: `publish` refuses non-publishable purposes first.
- `focus/engine/commitments.py`: incumbent heads move only forward in acceptance time. This fixes a
  re-bootstrap regressing the head from the published plan back to the fixture's previous plan.
- `focus/pipeline.py`: `demo_run` and `cli_demo_run`, the idempotent end-to-end fixture pipeline.

### Front end

- `focus/web/templates/partials/`: header, side nav, view selector, view host and bottom nav, shared by both
  layouts.
- `focus/web/static/js/views/`:
  - `common.js`: selectors, command submission with honest run polling, CSV export, bars and timelines.
  - `operate.js`: Team Lead, Mechanic, My Day, Shift Book, Worker Gantt and Turnover.
  - `plan.js`: Management, Project, Budget, Commitments, Economics, Capacity, Staffing and Scenario.
  - `panels.js`: Supply, Quality, IE, Development, Arena and History.
  - `performance.js`: claims and reviewed decisions per goal.
- `focus/web/static/js/workspace.js`: views render into detached containers, and the hash listener starts only
  after the context loads.
- `focus/web/static/js/core.js`: styles go through the CSSOM (strict CSP).
- `focus/web/static/css/focus.css`: styles for the new elements.

### Docs and tests

- `docs/execution/UI_COVERAGE.md` and `DECISIONS.md` (D-18).
- `tests/ui/check_workflows.py`, `tests/ui/test_workflows.py` (5) and
  `tests/integration/test_views_api.py` (11).
- `tests/integration/test_api_scope.py`: the A68 coverage list now includes the ten new reads.

## Commands and results

```
.venv/bin/python tests/ui/check_workflows.py --out evidence/20/screenshots
   -> 210 view checks (21 views x 2 layouts x 375/390/820/1280 + lead at 390), 0 failures; 4/4 workflows completed
.venv/bin/python -m pytest tests/ui -q                                -> 13 passed (Chromium 153)
.venv/bin/python -m pytest tests/integration/test_views_api.py -q    -> 11 passed
.venv/bin/python -m pytest tests -q                                   -> 359 passed, 1 failed (8:06)
.venv/bin/python -m pytest tests/integration/test_api_scope.py -q    -> 13 passed (after the fix below)
python -m focus.cli demo-run --budget-seconds 5                       -> both plans ACCEPTED, 3 collection points, factory aggregates, 14 recognition events
python -m focus.cli demo-run --budget-seconds 5                       -> ALREADY_ACCEPTED, ALREADY_COLLECTED, 0 appended, runtime byte-identical
python -m focus.cli scenario --source-run <id> --changes '[{"type":"duration_statistic","statistic":"P90"}, ...]' -> line 1303 +10 min vs baseline
```

The one full-suite failure was the A68 coverage test: it compares the registry's scoped reads with its own
list of request paths, and that list did not yet include the ten new P20 reads. The paths were added (with
`get_scenario` allowed a 404 when there is no data), and the whole file then passed.

`evidence/20/check_workflows.json` holds the full checker output. `evidence/20/screenshots/` holds 48 curated
JPEGs: Team Lead, Performance, Management, My Day, Gantt and Scenario, each in both layouts at all four widths.

## Acceptance cases

| Case | Observed |
| --- | --- |
| A59 | **Arena.** Standings pool the stored goal evaluations; each team's pool is its goal record (same id and score). The ranking follows the exact final values. **Factory.** The view equals the S14 formula over summed components. **History.** It names the same current evaluation ids. **Layouts.** iOS and Classic show the same scores (Chromium). |
| A60 | The planned OOS is charged once, in O. Charged units equal the union of the charged violations' units, and O = 0.10 × their affected effort. Cp stays the earned effort. Team XP equals the sum of recognition events: no OOS term, never reduced by O. |
| A63 | A projection replay appends no event and no badge, and `/recognition` is unchanged. Every Arena award shows its earning lineage, and progression has no qualification effect. A `demo-run` replay leaves the runtime byte-identical. |
| A68 | **Scope.** The Team Lead view for another team is refused before fetch (403). **No writes.** New reads leave every runtime file untouched. **Scenarios.** Spoofed or unsupported changes (`approve_plan`, `mock_data`, add 11) give 422, and a lead without the planner grant gets 403. A scenario run completes as SCENARIO_ONLY: no head moves, and frozen goals, plans, evaluations, recognition, decisions, amendments, delivery events and incumbents keep their counts. Publication refuses the candidate (`PURPOSE_NOT_PUBLISHABLE`). **Cancel.** A queued run can be cancelled by its requester (anyone else gets 404), cancelling again replays, and a cancelled run is never claimed. **Workflows.** Claim then reviewed decision, the amendment, the report and the IE proposal each complete through worker runs. |
| A69 | All 17 views plus panels pass in both layouts at 375, 390, 820 and 1280 px: a heading, loading done, no console error, no error card and no horizontal overflow. **States.** Honest empty, unknown and blocked states (an aggregates-only scope, missing economics grant or no person). **Visible reasons.** My Day and Gantt show travel, wait, tool and OT; parts, QA and predecessor reasons are visible in Team Lead, Shift Book, Supply and Quality. **Motion and keyboard.** Reduced motion is honored and the keyboard skip link works. |

Further checks:

- Team Lead reasons equal `assess_action` for every action, and blocked work shows `PREDECESSOR_INCOMPLETE`.
- After the observed points, proposals state `SHIFT_ENDED` (knowledge time).
- Turnover's open units equal the non-earned units of the stored evaluation evidence.
- Budget NVA equals travel, wait, tool and other duty, and Budget and Capacity share one duty ledger.
- Gantt bars equal the plan's duties for the day, and every skill shortfall equals max(0, crew size − qualified).
- Supply readiness is typed (ETA_CONDITIONAL for the CHILD5 part), and the IE queue includes snapshot data
  gaps.

## Fixture versus live

All facts come from the deterministic S18 fixture, with the TEST signing key and the demonstration score
policy. The fixture clock stays at the source cutoff, while observations replay to later points. This is why
amendment proposals for the 10-26 shift read `SHIFT_ENDED` in the observed runtime, and why the published-only
runtime is used for the amendment workflow. Live use needs the SQL repository (P23), live identity and grants,
and a separately run worker.

## Remaining gaps

- **Digital Twin, explanations and the `/explanations` command (P21).** The twin panel shows its explicit
  unknown state until then.
- **Cancelling running work.** A running scenario is bounded by its budget; only queued runs can be cancelled.
- **Screenshots.** They are curated (six views); the checker covers every view at every width.
