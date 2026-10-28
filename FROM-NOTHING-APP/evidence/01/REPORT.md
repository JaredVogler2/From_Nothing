# Stage P01 — Scope, contracts and execution register

Status: **fixture_verified** (documentation/policy contract verified by tests; live not applicable)

## Changed paths

- `docs/execution/ROOTS.md`: resolved app root (`<repo>/FROM-NOTHING-APP`), why the substitution was needed, and the user directives (Python 3.12; Posit Connect, then Tanzu; sound API endpoints plus MCP capability).
- `docs/execution/DECISIONS.md`: capability model (inventory / FORECAST / SCENARIO / EXECUTABLE / dispatch), architecture decisions D-01..D-15, and the semantic gaps.
- `docs/execution/BUILD_PLAN.md`: layered architecture, P01–P24 roadmap with dependencies and acceptance cases, external actions that need environment authority.
- `docs/execution/STATUS.json` (via `tools/status.py init`): all 24 stages with dependencies, statuses and evidence paths.
- `config/policies/rebuild-v1.json`, `shift_fixture.json`, `grading-demo-v1.json`: one typed policy registry.

## Policy register

- Policy `rebuild-v1`, hash `e4ba19ace5bbccc11f18090149ec3b6e8136773f8baaed0dd81f67e463d94b1c` at the time of this report. The hash is recomputed on load and covers values plus authorities.
- Every value carries an authority label: `user_requirement` (S01/S09 constants such as OT 60, no-start 30, activation 85/100, IP45/final45, horizon 3, hysteresis 2/7, gate 35/5/2, virtual support 10, P75 default), `manual_specified`, `fixture_default`, `demo_default` (S14 grading) or `open_decision`.
- Open decisions OD-01..OD-12 cover: TOTAL_MECHANICS semantics, raw clock calibration, QA hold locations/roles, breaks/OT authority, CS offsets/date basis, commercial due, concurrency meaning, rework classification source, affirmative no-cert census, assignment/attendance feeds, shift-start travel origin, and economics terms/caps. Each has a fixture choice and a precise live effect.
- User constants cannot be overridden (`USER_CONSTANT`). The one exception is the statistic toggle, which the user explicitly requested; it always produces a new policy id and hash (`tests/unit/test_policy_config.py`).

## Commands and results

```
.venv/bin/python tools/status.py init        -> initialized docs/execution/STATUS.json
.venv/bin/python -m pytest tests/unit/test_policy_config.py -q   -> 12 passed
```

## Acceptance cases

P01 lists A03, A15, A64 and A72. At this stage they are traced forward as contract requirements; each is executed in its implementing stage and again in P24:

| Case | Contract recorded here | Executed in |
| --- | --- | --- |
| A03 | `scope.all_wip=true`, `scope.fixed_line_cap=null`; census must reconcile every line | P03, P22, P24 |
| A15 | OD-01 TOTAL_MECHANICS semantics; no default-1 or primary-only demand | P07 |
| A64 | `support.virtual_people_per_team_shift=10`, virtual teams MHE/PSC/TST, forecast-only | P06, P21 |
| A72 | fresh-checkout commands in BUILD_PLAN/README; non-overwriting review files | P24 |

## Remaining gaps

None for P01. Live semantics are tracked as OD-01..OD-12; they do not block local building.
