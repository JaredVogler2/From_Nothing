# Stage P21: Digital twin, isolated scenarios and grounded explanations

Status: **fixture_verified**. Everything ran against deterministic fixture runtimes built by `demo_run`. No SQL,
live identity, live signing, BCAI endpoint or deploy was used.

## Changed paths

### Digital twin

- `focus/analytics/twin.py` (new): `build_twin`, a read-only twin of the current canonical revision.
  - **Keys.** Cells are keyed by source run, accepted EXECUTABLE and FORECAST plans, policy and the current evaluation
    revision, plus team, shift start date, shift number and aircraft line.
  - **Layers**, never mixed:
    - observed: stored canonical evaluation evidence and verified lifecycle facts;
    - planned: the accepted EXECUTABLE plan;
    - forecast: the accepted FORECAST plan, conditional, with virtual duty;
    - unknown: jobs, evaluations and attendance that no layer can state.
  - **Reconciliation checks**, returned with the twin:
    - cells sum to team, shift, line and grand totals;
    - job states partition the census in every layer;
    - planned effort equals the frozen B0;
    - observed effort equals each stored evaluation;
    - duty equals the ledger.
- `focus/publication/serializer.py`: `earning_phase`, the one rule behind frozen B0 units and the twin's planned effort.
- `focus/web/serializers.py` and `focus/web/api_views.py`: `GET /twin` with team, date, shift and line drilldowns,
  scope before fetch, and `unknown` status when reconciliation fails.
- `focus/web/static/js/views/twin.js` (new): the Digital Twin view (both layouts) and `focus.css`.

### Scenarios

- `focus/analytics/scenarios.py`:
  - **New changes.** `travel_minutes` (policy toggle) and `task_priority` (rank 1..20, which leads the dispatch key).
  - **Cancellation.** A cancel request stops a run cooperatively (`RunCancelled`).
  - **Testable timeout.** The timer is injectable, so the TIMED_OUT path can be driven in tests.
  - **Namespace.** Rebuilt snapshots stay out of the live namespace.
- `focus/data/acquisition.py`: `snapshot_for_run(store=False)`.
- `focus/engine/max/optimizer/orchestrator.py`:
  - `make_priority` and `MaxOptimizer` take fixed scenario ranks;
  - `schedule_snapshot` passes `fixed_ranks` and `cancel`, and refuses priorities for EXECUTABLE and FORECAST.
- `focus/workers.py`: `RunCancelled`, `request_cancel`, `cancel_requested` and `WorkerService.execute`. A cancelled
  handler ends its run `cancelled`.
- `focus/web/api.py`:
  - `POST /runs/{id}/cancel` now records a durable cancel request for a running run;
  - the scenario schema allows the two new change types.

### Explanations

- `focus/explanations/provider.py` (new):
  - contracts: `ExplainRequest`, `ExplainResponse`, `FactRow` and `Claim`;
  - providers: `MockProvider`, `RecordedProvider` (keyed by request hash) and `BCAIProvider` (https only, referenced
    credential, bounded timeout and retries, injectable transport);
  - `provider_for`, with `none` as the default.
- `focus/explanations/validators.py` (new):
  - a strict parser: exact keys, duplicate keys rejected, no floats or non-finite numbers, bounded sizes;
  - grounding of candidates, claims, narrative, uncertainty and reason codes.
- `focus/explanations/pipeline.py` (new):
  - the deterministic candidate set, from the shared ranking;
  - privacy-minimized fact rows;
  - a data-only versioned prompt;
  - the deterministic S16 template;
  - `explain`, with complete fallback in the same rank;
  - immutable records with audit hashes;
  - `worker_explanation` and `cli_explain`.
- `focus/publication/dispatch.py`: `ranked_actions`, the one ranking for Team Lead and explanations.
- `focus/publication/signing.py`: `secret_from_ref`, shared by the signer and the BCAI adapter.
- `focus/web/api.py`, `api_views.py` and `serializers.py`:
  - `POST /explanations`: current plan, scope, one team-shift, bounded candidates;
  - `GET /explanations/{id}`: team scope, and the economics grant when the record carries cost facts.
- `focus/web/static/js/views/operate.js`: "Why this order?" on Team Lead.
- `focus/cli.py`: `python -m focus.cli explain`, and `demo-run --max-moves`.
- `focus/analytics/scenarios.py` `cli_scenario`: reports `status: ok` with `scenario_status`, so a completed or
  TIMED_OUT scenario exits 0. This fixes a P20 defect where the scenario's own status overwrote `ok`.
- `config/policies/rebuild-v1.json`: `explanations.timeout_seconds`, `explanations.max_retries` and
  `explanations.max_candidates`.

### Docs and tests

- `docs/execution/DECISIONS.md` (D-19, OD-18), `DEPLOYMENT.md` (explanation providers, run cancellation) and
  `UI_COVERAGE.md`.
- `tests/integration/test_twin_scenarios.py` (9) and `tests/unit/test_explanations.py` (40).
- `tests/ui/check_workflows.py`: the twin view, the explanation workflow and `--shot-views`.
- `tests/ui/test_workflows.py` (6) and `tests/integration/test_api_scope.py` (A68 coverage of the new reads).

## Commands and results

```
.venv/bin/python -m pytest tests -q                                      -> 409 passed (9:32)
.venv/bin/python -m pytest tests/integration/test_twin_scenarios.py tests/unit/test_explanations.py \
    tests/integration/test_api_scope.py tests/integration/test_views_api.py -q   -> 73 passed (after the final fixes below)
.venv/bin/python -m pytest tests/integration/test_source_runs.py tests/integration/test_web_runtime.py -q -> 21 passed
.venv/bin/python -m pytest tests/ui -q                                   -> 14 passed (Chromium 153)
.venv/bin/python tests/ui/check_workflows.py --out evidence/21/screenshots --shot-views twin,team-lead
    -> 220 view checks (22 views x 2 layouts x 375/390/820/1280 + lead at 390), 0 failures; 5/5 workflows completed
python -m focus.cli demo-run --budget-seconds 5                          -> both plans ACCEPTED (evidence/21/cli/demo_run.json)
python -m focus.cli explain --plan <id> --team PROD-B --shift-id DEMO:2026-10-26:S1 --provider none
    -> DETERMINISTIC, 5 items in rank order
python -m focus.cli explain ... --provider mock --record-dir <dir>      -> VALIDATED; response recorded under its request hash
python -m focus.cli explain ... --provider recorded --record-dir <dir>  -> VALIDATED replay (recorded_input_hash = request hash)
python -m focus.cli scenario --source-run <id> --changes '[{"type":"travel_minutes","changed_aircraft_minutes":60}]'
    -> status ok, scenario_status COMPLETED, valid; line 1301 -120 min, 1302 +280 min; a rerun returns the same scenario id
python -m focus.cli scenario --source-run <id> --changes '[{"type":"task_priority","job_id":"J-not-a-job","rank":1}]'
    -> exit 2, JOB_NOT_FOUND
```

The full-suite run came before four small changes:

- the twin reports `no_accepted_plan` instead of `absent` when a plan does not exist, with a new test;
- `cli_scenario` no longer lets the scenario's own status overwrite `status: ok`;
- `cli_explain` defaults the recorded directory;
- `demo-run` gained `--max-moves`.

The affected test files were rerun afterwards: 73 and 21 passed.

**Recordings and fresh runtimes.** A recording is keyed by the canonical request hash, which covers the source run,
plan and policy hashes. Source runs are unique events (`SR-` + uuid), so a fresh demo runtime never matches a
recording made in another one; it falls back honestly with `RECORDING_NOT_FOUND`, as observed. Recordings are
therefore made per runtime with `explain --provider mock --record-dir`, and none is committed as a cross-runtime
fixture.

Evidence files:

- `evidence/21/check_workflows.json` (checker summary);
- `evidence/21/screenshots/`: 16 JPEGs, twin and Team Lead, both layouts, four widths;
- `evidence/21/cli/*.json` (the CLI outputs above);
- `evidence/21/twin_manager.json`, `twin_lead_prod_a.json` and `twin_drilldown_prod_b_1303.json` (`GET /twin`
  envelopes; every reconciliation check passes).

## Acceptance cases

| Case | Observed |
| --- | --- |
| A64 | **Pools.** Every MHE, PSC and TST pool per date and shift has exactly 10 distinct virtual people. They are SYNTHETIC_VIRTUAL and FORECAST_ONLY, outside executable membership, and hold exactly the union of their team's REQUIRED certificates. **Forecast.** Assignments with virtual crew carry SUPPORT_RESOURCE_UNAVAILABLE; the twin's forecast layer reports their duty, and the planned layer has none. **Spoofs.** The FORECAST candidate relabeled EXECUTABLE fails the independent validator with `(V5, SYNTHETIC_IN_EXECUTABLE)`. Crew admission ignores the payload claims `mock_data=false`, `purpose=EXECUTABLE` and `synthetic=false`. A scenario change carrying `mock_data` gets 422, and a scenario candidate is refused with `PURPOSE_NOT_PUBLISHABLE`. |
| A65 | **Drilldowns.** They give the same canonical totals and revisions: the sums over teams, over lines (plus the no-line bucket) and over shifts equal the whole twin, and each part names the same revision. A lead sees exactly the manager's row for the team; another team gets 403 before fetch. **Exact sources.** Planned effort equals the signed artifact's frozen B0 per team-shift. Observed Cp, E, B0, B and U equal the stored evaluations. Planned duty equals the plan's duty ledger, and every census job appears once per layer. **Scenarios change nothing live.** The checks covered a +1 virtual staffing what-if, a queued cancel, a running cancel (durable request, cooperative stop, run `cancelled`, no result), a TIMED_OUT scenario and a retry returning the identical result. Afterwards heads, goals, plans, commitments, actuals, evaluations and recognition are byte-identical, the live snapshot collection is untouched, and the twin is identical. **Correction.** An approved excusal plus re-evaluation appends evaluation revision r+1: the twin key moves to the new id, the old revision stays readable, and the relief equals the claimed effort. |
| A66 | **Actual ids.** Mock and recorded explanations of the published world use actual opaque ids (`ASG…`, `J…`) and native SOI labels (`1303/…`), and validate. A recording is used only for its exact request hash; another request falls back with `RECORDING_NOT_FOUND`, and a tampered recording is rejected on replay. **Forgeries.** Each of 18 forgeries returns the complete deterministic explanation in the unchanged rank, rejected with its own code: candidate, duplicate, missing, value, money (off by one and fractional), entity, unit, metric, another candidate's evidence, an unknown shown as known, and the narrative cases (new number, unknown id, unsupported claim, markup), plus too-low uncertainty, an ungrounded reason code and the version. Tolerance applies only by explicit policy and never to money. |
| A67 | **Injection.** Injected source text is reduced to a safe single-token label, appears only inside the DATA section and never changes the fixed instruction header. An obedient provider that flips a blocked action to dispatchable and reorders items is rejected; `tool_calls` is a schema violation. **Malformed output.** NaN, Infinity, 1e999, floats, booleans, duplicate keys, extra fields at every level, oversize, empty and non-JSON output all fall back. **Provider failures.** A timeout (bounded: retries plus one attempts), HTTP errors, a missing BCAI endpoint, a non-TLS endpoint, an unsupported or missing credential and arbitrary provider errors all fall back. The secret travels only in the Authorization header: never in the body, prompt, audit, record or a stored error. **No side effects.** The worker writes only its explanation record and run state, and the Team Lead ranking is unchanged. No person id reaches a provider or the record. |

## Fixture versus live

All facts come from the deterministic S18 fixture with the TEST signing key and the demonstration score policy.

- **Twin.** It reads the same projections live once the SQL store adapter exists (P23).
- **Explanations.** The default provider is `none`, so they are deterministic. No BCAI endpoint, credential or
  response adapter is approved (OD-18), and no external call was made. The BCAI adapter was exercised only with
  injected transports.

## Remaining gaps

- **BCAI provisioning (OD-18).** An approved https endpoint returning `focus-explain-v1`, and its credential
  reference, are operator decisions. Until then live explanations are deterministic.
- **Tool scenarios.** Tool capacity is a sourced fact (`tools.reservation_policy` NONE_UNLESS_SOURCED), so there is
  no tool what-if until a tool source exists.
- **Live stores.** The SQL store adapter for twin and explanation reads arrives in P23, and live identity is not
  exercised.
