# Stage P13: Rich MAX whole-WIP scheduling and economic search

Status: **fixture_verified**. P13's part of A47 is covered: the final candidate passes the
independent V6/V7/V8 duty reconstruction. The full V1–V9 mutation corpus is P15.

## Changed paths

- `focus/engine/max/optimizer/greedy.py`: the named ready-frontier list-scheduling decoder
  over the complete WIP and one shared ledger.
  - **Floors.** Cutoff, job earliest, same-run CS, typed FS/SS/FF/SF edges with signed lag,
    and part releases.
  - **Starts.** Admitted line shifts only, with candidate starts at pool free points.
    Regular time is tried before OT.
  - **Crews.** Exact full crews only. Waiting successors get `PREDECESSOR_UNPLACED`.
  - **WAIT and defense.** QA crew WAIT duty, and the defended-commitment flow (P14).
- `focus/engine/max/optimizer/orchestrator.py`: `MaxOptimizer`.
  - **Pipeline.** Neutral seed → economic redispatch with registry weights → bounded delay
    repair → seeded neighbourhoods. The search stops at the move bound, the wall-clock budget
    or cancellation.
  - **Acceptance.** Exact lexicographic acceptance; candidates never lose a kept commitment.
  - **Receipt.** `build_candidate` assembles a substance-addressed, sealed `CandidatePlan`
    with every job disposition, duties, completions, costs, commitment results, the incumbent
    receipt, stage metrics and `global_optimality_proven = false`.
  - **Persistence.** `schedule` persists it. When the same substance already exists it
    returns the stored candidate.
  - **CLI.** `cli_schedule` checks the policy id and never publishes.
- `focus/engine/max/optimizer/neighborhoods.py` (swap, boost, aircraft advance), `repair.py`,
  `continuity.py`.
- `focus/contracts/planning.py`: `AlgorithmStatus.incumbent`.
- `tools/run_schedule.py`: a CLI wrapper that defaults to the source head.
- `tests/integration/test_max_pipeline.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/integration/test_max_pipeline.py -q   -> 6 passed
.venv/bin/python -m pytest tests -q                                    -> 203 passed
FOCUS_RUNTIME_DIR=<tmp> python -m focus.cli acquire --mode fixture --scenario manufacturing_demo
FOCUS_RUNTIME_DIR=<tmp> python -m focus.cli demo-bootstrap --scenario manufacturing_demo
FOCUS_RUNTIME_DIR=<tmp> python -m focus.cli schedule --source-run <run> --policy rebuild-v1 --purpose EXECUTABLE
FOCUS_RUNTIME_DIR=<tmp> python -m focus.cli schedule --source-run <run> --policy rebuild-v1 --purpose FORECAST
```

Default budget (30 s, 400 moves), fixture `manufacturing_demo`, this container:

| Purpose | Wall | Stages (ms) | Moves / accepted | Census (19 jobs) | Unknown aircraft | Commitments |
| --- | --- | --- | --- | --- | --- | --- |
| EXECUTABLE | 5.5 s | seed 15, redispatch 9, repair 10, neighbourhoods 4235 | 400 / 1 | 11 scheduled, 6 deferred, 1 blocked, 1 verified complete | 1305, 1306 | 3 KEPT, 1 CREW_CHANGED, 1 OUT_OF_HORIZON |
| FORECAST | 8.5 s | seed 33, redispatch 30, repair 23, neighbourhoods 7413 | 400 / 2 | 11 scheduled, 6 conditional, 1 blocked, 1 verified complete | 1306 | 3 KEPT, 1 CREW_CHANGED, 1 OUT_OF_HORIZON |

The proof status is `HEURISTIC_NO_PROOF` and `global_optimality_proven` is false in both.

## Acceptance cases

| Case | Observed |
| --- | --- |
| A26 (engine) | ROOT4's remaining 60 minutes start only after its incomplete FS predecessor PRE4 ends. History is untouched and nothing starts before the cutoff. |
| A38 | Every candidate is a full decode through the shared ledger, so no move can bypass the activation cap. The final candidate has zero independent V8 violations, and a forged plan is caught (see P11). |
| A44 | The actual CLI path consumes the registry weights and improves the exact fleet cost over the neutral seed (see P12). |
| A45 | Acceptance uses the exact key: commitments, unknown aircraft, deferred phases, exact controllable cents, NVA minutes, continuity, signature. There is no monetary tolerance, and weights never decide. |
| A47 (P13 part) | The final candidate passes the independent duty reconstruction with zero V6/V7/V8 violations, and its independent travel total equals the receipt. The V1–V9 mutation corpus is P15. |
| A70 (engine part) | The same seed and input give the same candidate id twice. A fake clock stops the search at the budget (`deadline_reached`, moves < bound). Cancellation returns the validated seed. Inventory is complete: 19 of 19 jobs, TST6B BLOCKED, A0 VERIFIED_COMPLETE, every deferral has a reason. FORECAST labels virtual support as conditional; EXECUTABLE never uses synthetic people. A fresh-process CLI schedule works. |

## Fixture versus live

Runs use the fixture snapshot. Live scheduling needs the live feeds listed in P03–P12. The
optional exact solver and the scale benchmark are P22.

## Remaining gaps

- The full V1–V9 independent validator and mutation corpus (P15). Publication (P16).
- Scale benchmark and optional CP-SAT (P22).
