# Stage P22: Complete-scope scaling and the optional solver

Status: **fixture_verified**. Everything ran on deterministic fixture runtimes: the demo fixture and the synthetic scale
fixtures at 51, 100, 150 and 300 aircraft. No SQL, live identity, signing, deploy or live census was used. The
optional solver was exercised only on fixtures; enabling it is still an operational decision
(`docs/execution/SOLVER_ENABLEMENT.md`).

## Changed paths

### Exact scale optimizations

All outputs are verified identical to the previous commit.

- `focus/engine/ledger.py`:
  - `person_conflict` and `cap_problem`: `plan`'s own per-person and pool-level checks, exposed so crew selection
    can skip a person or a crew before the full derivation;
  - a shallow transaction snapshot;
  - a keyed-bisect cross-shift check (O(log n) instead of rebuilding a person's end list on every call).
- `focus/engine/crew_selection.py`: the exact pre-checks, and a whole-shift-window qualification answer.
- `focus/engine/model.py`: `EngineContext.qualifies_within`, an interval-monotone memo.
- `focus/engine/max/optimizer/greedy.py`: passes the shift window.
- `focus/domain.py`: memoized `JobKey`/`PersonKey`/`PoolKey` transport ids.
- `focus/validation/input.py` and `focus/validation/plan.py`: snapshot verification once per validation input.

### The optional CP-SAT stage

- `focus/engine/max/optimizer/solver.py` (new):
  - the pooled relaxation, warm-started from the baseline, with probes decoded by the named decoder;
  - the truthful `SolverStatus` receipt and the `budgets` (wall-clock, probes, memory, seeded single worker);
  - cancellation by watchdog `StopSearch`;
  - the complete-scope benchmark (`benchmark_size`, `benchmark_failures`, `cli_benchmark`, a per-size subprocess).
- `focus/engine/max/optimizer/orchestrator.py`:
  - `KEY_LEVELS` and `MaxOptimizer.improved_level`;
  - `schedule_snapshot(solver=, solver_budget_seconds=)` and `MaxRun.solver`;
  - the receipt in `baseline_comparison["solver"]`, a `solver` stage metric when enabled, and the
    `cpsat_relaxation_order` path;
  - CLI and worker results carry `solver`.
- `focus/web/serializers.py`: the candidate summary shows the solver receipt.
- `focus/cli.py`: `benchmark --max-moves --solver-budget-seconds`.
- `config/policies/rebuild-v1.json`: `solver.budget_seconds` 20, `solver.max_probes` 2, `solver.max_memory_mb`
  2048, `solver.num_workers` 1 (all fixture defaults). `solver.enabled` stays `false` (user requirement).

### Scale fixture, benchmark tools and hosting files

- `focus/data/fixture_builder.py`: the scale census gains derived rows, so the random stream and every other row are
  unchanged:
  - sourced in-process QA holds on J04 and J09;
  - a forecast-only TST functional test on one aircraft in ten (virtual support).
- `tools/benchmark_solver.py` (new): the shadow benchmark with the solver off and on, and `SUMMARY.md`.
- `requirements-solver.txt` (new): the opt-in pins. `requirements.lock` is refreshed.
- Docs:
  - `docs/execution/SCALE_REPORT.md` and `SOLVER_ENABLEMENT.md` (new);
  - `DEPLOYMENT.md` (the optional solver on Posit Connect and Tanzu);
  - `DECISIONS.md` (D-20).

### Tests

- `tests/benchmarks/test_solver_status.py` (new, 18 tests): every status, switch precedence, budgets, the validator
  rolling back corrupted probes, the independently confirmed improvement level, reproducibility, and cancellation
  before and during a solve.
- `tests/benchmarks/test_complete_scope.py` (new):
  - bounded 51-aircraft A03/A47/A70 cases in the default suite;
  - `benchmark`-marked 100/150/300 runs with `--benchmark-sizes`.
- `tests/unit/test_scale_exactness.py` (new, 8 tests): each shortcut against its full rule or a brute-force oracle.
- `tests/unit/test_hosting_files.py` (new, 3 tests): solver-free default requirements, identical pins, and Python
  3.12 everywhere.
- `tests/conftest.py`: the `--benchmark-sizes` option; `benchmark` tests are skipped without it.

## Commands and results

| Command | Result |
| --- | --- |
| `python tools/benchmark_solver.py --sizes 51,100,150,300 --budget-seconds 600 --max-moves 2 --out evidence/22` | `ok`, no failures. 16 plans valid with 0 violations and 0 required skips, complete inventory. Receipts: `benchmark_solver_off.json`, `benchmark_solver_on.json`, `SUMMARY.md`. |
| Old vs new code on copies of one runtime (`compare.py` scratch harness: canonical JSON of assignments, duties, dispositions, commitments, costs) | Identical for both purposes on the demo and at 51 and 150 aircraft (ratios in `SCALE_REPORT.md`). |
| `python -m pytest tests -q` | 447 passed, 2 failed, 3 skipped (16:24). Both failures predate this stage; they are on `main` (its sign-in redirect for `/dashboard`). |
| `python -m pytest tests/integration/test_web_runtime.py tests/ui/test_layout_parity.py tests/integration/test_demo_vercel_access.py -q` | 22 passed, after updating the two tests to the redirect (signed-out visitors land on the demo sign-in; the signed-in workspace loads; still no side effects). |
| `python -m pytest tests/benchmarks tests/integration/test_source_scope.py tests/unit/test_scale_exactness.py -q` | 38 passed, 3 skipped (the `benchmark` sizes, which run with `--benchmark-sizes`). |
| `python -m pytest` on the engine, ledger and validator files (`test_duty_ledger`, `test_codec_domain`, `test_commitments`, `test_economics`, `test_max_pipeline`, `test_named_crew`, `test_validator_mutations`, `test_economic_steering`, `test_incumbent_defense`, `test_pools`) | 130 passed. |

**Benchmark machine.** 4 vCPU Xeon at 2.10 GHz, 16 GB RAM, CPython 3.12.3.

**Contention.** The full test suite ran on the same machine during part of the benchmark: the end of the 300-aircraft
solver-off pass and most of the solver-on pass. Those timings include that contention.

| Aircraft | EXECUTABLE / FORECAST wall s, solver off | Same, solver on | Peak RSS MB off / on | Solver outcome (EXECUTABLE / FORECAST) |
| --- | --- | --- | --- | --- |
| 51 | 16.5 / 17.5 | 44.7 / 52.2 | 107 / 206 | IMPROVED travel+wait / IMPROVED cost ($417.45 → $0) |
| 100 | 46.7 / 57.0 | 111.8 / 120.9 | 184 / 314 | IMPROVED cost ($1,941.86 → $1,640.16) / IMPROVED cost ($1,932.59 → $1,527.20) |
| 150 | 129.2 / 139.0 | 207.6 / 231.0 | 259 / 382 | NOT_BETTER / NOT_BETTER (baseline unchanged) |
| 300 | 518.9 / 568.6 | 743.8 / 845.1 | 401 / 567 | IMPROVED cost ($2,873.04 → $2,705.30) / IMPROVED cost ($3,455.78 → $3,392.12) |

## Acceptance cases

| Case | Result |
| --- | --- |
| A03 | **Pass.** 51 aircraft with one entirely blocked and one missing standards: all 51 reconciled with no top-50 cap, and every job dispositioned through both plans with reasons (`test_a03_every_aircraft_and_job_survives_to_the_plan`, `test_a03_fifty_one_aircraft_all_reconciled_no_cap`). The same holds at 100, 150 and 300 in the benchmark receipts. |
| A45 | **Pass on the solver path.** The exact full-candidate comparison governs. The relaxation's weighted proxy never decides. A signature-only tie is refused. The claimed level is confirmed from the plans' own duties (`test_a45_improvement_is_exact_and_never_a_proof`, `test_signature_only_difference_is_not_an_improvement`). Unknown-cost aircraft stay counted in every receipt. |
| A47 | **Pass at scale.** Every V1-V9 count is nonzero at every size. The in-search validator rolls back every corrupted solver probe (`test_a70_solver_probe_cannot_bypass_named_feasibility`). The P15 mutation suite passes unchanged. |
| A70 | **Pass.** The whole-WIP benchmark covers scarce certificates, travel, rework, parts, QA holds, shifts across the DST change and virtual support. It shows zero required skips and violations, complete inventory, and bounded runtime and memory (at most 845 s and 567 MB per plan at 300 aircraft). The solver fallbacks (`UNAVAILABLE`, `TIMEOUT`, `UNKNOWN`, `INFEASIBLE`, `MODEL_INVALID`, `ERROR`, invalid baseline, `CANCELLED` before and during a solve) each return the same-input validated baseline with a truthful status (`tests/benchmarks/test_solver_status.py`). |

## Fixture versus live

- Fixture: everything above.
- Live: nothing.
  - No live census was benchmarked, and live readiness gaps OD-01..OD-18 are unchanged.
  - The solver was never enabled on a host. `FOCUS_SOLVER_ENABLED` and `solver.enabled` are documented switches only.

## Remaining exact gaps

- **No complete named CP-SAT model** (by design). The relaxation stopped `FEASIBLE` at its 20 s limit at every scale
  size. Enabling the stage remains the owner's decision (`SOLVER_ENABLEMENT.md`), including the measured churn of
  1,200 to 7,700 changed placements per accepted probe.
- **Night-shift DST.** The night shift's DST length change has no roster in the scale fixture; the calendar and ledger
  tests cover it. Day-shift instants do cross the fall-back.
- **Superlinear growth** (pool size × congestion) remains. The next exact steps are in `SCALE_REPORT.md`.
- **`benchmark` pytest cases.** The 100/150/300 cases were not run under pytest in this session. The same per-size
  subprocesses ran through `tools/benchmark_solver.py`, and their receipts are committed here.
