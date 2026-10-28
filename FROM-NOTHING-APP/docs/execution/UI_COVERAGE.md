# UI coverage: the 17 operational views and supplementary panels (S15/S16, P19-P21)

Both layouts (`/dashboard` for iOS, `/dashboard/classic`) share one template set and one set of view modules:

- `templates/workspace.html` and `templates/partials/` (header, side nav, view selector, view host, bottom nav);
- `static/js/workspace.js` and `static/js/views/*.js`.

Every view reads canonical `/api/v1` projections only. There is no browser scheduling, qualification, grading
or assignment, and actions are commands that return worker runs.

## States

| State | How it is shown |
| --- | --- |
| loading | Skeleton card while the host is `aria-busy`. |
| empty | Dashed card with the reason. |
| unknown | Info card with typed reasons and the next action. |
| blocked | Warning card, for example a missing source, policy or grant, or sign-in needed. |
| stale | Warning banner; the data stays visible for history. |
| error | Alert card with the request id. |

Each envelope's source, plan, purpose, policy, evaluation and as-of bindings are printed under the title.

## Views

| # | View (hash) | API operations | Commands | Key content |
| --- | --- | --- | --- | --- |
| 1 | Team Lead (`team-lead`) | `GET /team-lead`, `GET /explanations/{id}` | `POST /amendments`, `POST /explanations` | Server-ranked actions (dispatchable first), exact crews, current dispatch reasons, commitment outcomes and feasible emergency-addition proposals with reasons. "Why this order?" explains the top five in the same rank: validated provider text or the deterministic explanation, with grounded facts and audit hashes. |
| 2 | Management (`management`) | `GET /fleet`, `GET /capacity` | — | Fleet completion (known, conditional or unknown), CS gate, modeled cost (with an economics grant), burndown, capacity summary and readiness gaps. |
| 3 | Mechanic (`mechanic`) | `GET /myday` | `POST /reported-observations` | Own scope, current duty, certificate readiness per duty and holdings. Reporting is a claim, never a check-off. |
| 4 | Industrial Engineering (`ie`) | `GET /standards` | `POST /ie-reviews` | Review queue (weak durations, unknown requirements, team ties, data gaps), duration provenance, requirement bundles and team authority. |
| 5 | Staffing (`staffing`) | `GET /workforce`, `GET /capacity` | — | Pure pools: executable versus forecast N and cap, members' presence (unknown shown), skill demand against qualified admitted people. |
| 6 | Supply Chain (`supply`) | `GET /shortages` | — | Used-on, root and descendant impact, ECD versus confirmed receipt (RECEIVED, ETA_CONDITIONAL and so on), release status. |
| 7 | Scenario (`scenario`) | `GET /scenarios`, `GET /scenarios/{id}` | `POST /scenarios`, `POST /runs/{id}/cancel` | Typed what-if inputs, a same-settings baseline comparison per aircraft and SCENARIO_ONLY labeling. |
| 8 | Shift Performance (`performance`) | `GET /performance`, `GET /disruption-claims` | `POST /disruption-claims`, `POST /excusal-decisions` | One stored evaluation per goal (B0/B/E/D/Cp/U/O/base/final/grade), units, offplan output, OOS charged once, the factory aggregate, and claims versus reviewed decisions. |
| 9 | Project (`project`) | `GET /aircraft/{line}` | — | Job timeline from the accepted plan, CS gate and due proxy, completion, completion history across accepted plans, shortages and calendar. |
| 10 | Schedule Budget (`budget`) | `GET /budget` | — | Prorated standard person effort against planned duty, NVA, OT and capacity per team-shift; job flow budget versus planned span. |
| 11 | Worker Gantt (`gantt`) | `GET /gantt` | — | Every person's work, inspection, travel, wait, tool, carry-in and OT, with shift boundaries. |
| 12 | Development (`development`) | `GET /development` | — | IE proposals, skill gaps with partially qualified training candidates, and team XP, levels and badges (no qualification effect). |
| 13 | Commitments (`commitments`) | `GET /commitments` | — | Original, current, raw and pending dates, raw risk, alerts, the change ledger and two-attempt task outcomes. |
| 14 | Economics (`economics`) | `GET /economics` (economics grant) | — | Known, conditional and unknown totals; per-line unavoidable, controllable and marginal cost; terms with authority. Withheld, never $0, without the grant. |
| 15 | Shift Book (`shiftbook`) | `GET /shiftbook` | — | The team-shift's ordered jobs with full crews and conditions, the goal of record, the stored evaluation and CSV export. |
| 16 | My Day (`myday`) | `GET /myday` | — | Ordered WORK/TRAVEL/WAIT/TOOL duties with OT, crewmates, readiness per action, arrival and planned dates. |
| 17 | Capacity (`capacity`) | `GET /capacity` | `POST /scenarios` (+1 virtual) | Activation against the cap (distinct from utilization), OT, unknown attendance, skill bottlenecks, weighted unscheduled reasons, and a +1 virtual what-if that is explicitly a scenario. |
| — | Quality (`quality`) | `GET /quality` | — | In-process, final and other buyoff holds with inspectors, and inspector competition per shift. |
| — | Arena & Recognition (`arena`) | `GET /arena` | — | Standings from stored evaluations pooled exactly, XP, badges and recent recognition with lineage. |
| — | Turnover (`turnover`) | `GET /turnover` | — | Open frozen units (from evaluation evidence), unreviewed claims, open holds, the next shift's actions with blocked reasons, and carry-in. |
| — | History (`history`) | `GET /history` | — | Accepted plans with head versions, evaluation revisions, corrections and delivery changes. |
| — | Digital Twin (`twin`) | `GET /twin` | — | Observed, planned, forecast and unknown layers keyed by source, plans, policy and evaluation revision. Totals by team, shift and line, inventory per layer, unknown jobs, evaluations and attendance, and the reconciliation checks. Drill down by team, date, shift and line. |

## Verification

### Browser checks

`tests/ui/check_workflows.py` builds two `demo_run` runtimes and drives Chromium.

- **Matrix:** every view in both layouts at 375, 390, 820 and 1280 px, plus scoped lead views at 390. It
  checks for a heading, `aria-busy=false`, no console or page error, no error card and no horizontal overflow
  (22 views since P21: 220 checks, 0 failures).
- **Workflows:**
  - a claim, then a reviewer approval;
  - a +1 virtual what-if;
  - a mechanic report and an IE proposal;
  - a Team Lead emergency addition;
  - a Team Lead explanation of the top five actions (P21).

Results are in `evidence/20/check_workflows.json` and `evidence/21/check_workflows.json`. Curated JPEG screenshots
are in `evidence/20/screenshots/` (six views) and `evidence/21/screenshots/` (twin and Team Lead, × two layouts ×
four widths).

### Pytest

- `tests/ui/test_workflows.py` (6) runs the same checks.
- `tests/ui/test_layout_parity.py` (8) covers iOS and Classic score parity, widths, reduced motion, the
  keyboard and no-data states.
- `tests/integration/test_views_api.py` (11), `tests/integration/test_api_scope.py` (13),
  `tests/integration/test_twin_scenarios.py` (8) and `tests/unit/test_explanations.py` (40) cover the projections
  and commands behind every view.
