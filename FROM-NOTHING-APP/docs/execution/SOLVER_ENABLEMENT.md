# Optional CP-SAT solver: what it is and how to enable it (P22)

Enabling the solver is a concrete later operational decision. This page is the decision record's input. It is not a
recommendation to enable it. Nothing here was deployed from this build.

## What the stage is, and is not

S12 allows a CP-SAT plan only from a model that represents every named rule:

- distinct named roles and whole-interval qualifications;
- pure pools and activation;
- calendars, CS, parts and the graph;
- breaks, overtime, travel and setup;
- commitments.

This build has no such model. So CP-SAT **never produces a plan** here. It is an optional ordering stage
(`focus/engine/max/optimizer/solver.py`) that runs after the named heuristic:

1. It builds a **pooled relaxation** of the validated same-input baseline and warm-starts from it:
   - **kept:** every phase the baseline placed, work durations, the hard graph with lags, job/CS/release floors, team
     capacity, and an aircraft-completion objective weighted by the economics registry;
   - **dropped:** named people, qualifications, travel, budgets, breaks and shift windows.
2. It solves the relaxation under a wall-clock limit, a memory limit and a worker count.
3. It reads job orders from the solution (by relaxation start, and by relaxation aircraft completion). Each order is one
   **probe**: dispatch ranks for the **same named decoder** that builds every crew, duty and transfer.
4. The **independent validator** checks each probe. A probe replaces the baseline only if two things hold:
   - the **exact complete-fleet comparison** (S12 levels 0-6) improves at a substantive level; a tie broken only by the
     deterministic signature is not an improvement;
   - no commitment the baseline keeps is lost.

The relaxation's objective and bound describe the pooled proxy only. They are never a plan, never a bound on real cost
and never a proof. `global_optimality_proven` stays `false` and `proof_status` stays `HEURISTIC_NO_PROOF`.

## Truthful status (recorded on every run)

`run.solver` (CLI `schedule`, worker `schedule-runs`) and `algorithm_status.baseline_comparison["solver"]` (the
stored candidate) carry the receipt. It records:

- the status and which switch enabled the stage (`request`, `policy`, `setting`, or empty);
- OR-Tools availability and version, the budgets and the model size;
- the relaxation status, objective and bound;
- each probe: valid, improved level, changed placements versus the baseline, deferred phases, unknown-cost aircraft and
  controllable cost;
- solve and wall seconds, the reason and the proof scope.

When the stage is enabled, a `solver` stage metric is appended.

| Status | Meaning | Plan |
| --- | --- | --- |
| `DISABLED` | default: policy off, setting unset | the heuristic's |
| `UNAVAILABLE` | switch on, OR-Tools not installed | same-input validated baseline |
| `TIMEOUT` / `UNKNOWN` | no relaxation solution within the limit / CP-SAT returned UNKNOWN | same-input validated baseline |
| `INFEASIBLE` / `MODEL_INVALID` | relaxation infeasible / rejected by CP-SAT | same-input validated baseline |
| `INVALID_CANDIDATE` | no probe passed the validator, or the baseline itself was invalid (nothing is published) | baseline (never published if invalid) |
| `NOT_BETTER` | a valid probe, none strictly better at a substantive level | same-input validated baseline |
| `CANCELLED` | cancel request before, during (watchdog `StopSearch`) or between probes | same-input validated baseline |
| `ERROR` | unexpected adapter failure (typed reason kept) | same-input validated baseline |
| `IMPROVED` | a probe is valid and exactly better; the level is named | the probe's named plan |

The stage never publishes, signs or moves a head. Plans still pass FINAL validation and the publication protocol (P16).
An old accepted source revision is never an automatic executable fallback.

## Switches and bounds

| Setting | Default | Notes |
| --- | --- | --- |
| policy `solver.enabled` | `false` (user requirement) | A revision gives a new policy id/hash. |
| `FOCUS_SOLVER_ENABLED` | unset | Deployment switch; keeps the policy hash. An explicit request (benchmark `--solver on`) overrides both. |
| `solver.budget_seconds` | 20 | Relaxation wall-clock limit (fixture default). |
| `solver.max_probes` | 2 | Complete named decodes per run. Each costs about one heuristic decode (see `SCALE_REPORT.md`). |
| `solver.max_memory_mb` | 2048 | CP-SAT memory limit. Size it below the host's memory. |
| `solver.num_workers` | 1 | One worker with the policy seed is reproducible whenever the solve ends before its limit. More workers trade that for speed. |

## Measured behavior

See `SCALE_REPORT.md` and `evidence/22/`. Summary:

- On the demo fixture the relaxation order removes waiting: travel+wait drops from 60 to 45 minutes. Everything above
  that level ties: commitments, unknown-cost aircraft, deferred phases and controllable cost.
- On the synthetic scale fixtures (8 plans: 4 sizes × 2 purposes):
  - **6 were `IMPROVED`.** At 51 aircraft EXECUTABLE improved travel+wait; the other five improved controllable cost.
    For example, the 300-aircraft EXECUTABLE known cost fell from $2,873.04 to $2,705.30 and travel from 79,560 to
    72,075 minutes.
  - **Both 150-aircraft plans were `NOT_BETTER`.** The probes were valid, so the baseline stood unchanged.
  - **The relaxation never closed.** It always stopped `FEASIBLE` at its 20 s limit.
  - **Churn is large.** Accepted probes moved 1,200 to 7,700 placements, so enabling the stage means accepting that
    churn.
- The stage's cost is the relaxation solve plus up to `max_probes` complete decodes. It took 29 s per plan at 51
  aircraft and 235-330 s at 300 aircraft on the benchmark machine, where one probe is 100-160 s.

## Decision checklist (for the owner)

1. **Benchmark on live-shaped data.** Run `python tools/benchmark_solver.py --sizes <live fleet size> --out <dir>`
   against a live-shaped census once live readiness gaps OD-01..OD-18 close. Synthetic fixtures are not live proof.
2. **Shadow first.** Enable the solver with `FOCUS_SOLVER_ENABLED=true` on a non-publishing worker and compare receipts
   (`IMPROVED` share, improved levels, churn) before any published plan depends on it.
3. **Resources.**
   - Install from `requirements-solver.txt`.
   - Size host memory and `solver.max_memory_mb` from the measured peak RSS: Tanzu `manifest.yml` gives 1G per
     instance; Posit Connect process limits are set per content item.
   - Keep worker budgets above `solver.budget_seconds` plus `max_probes` decodes.
4. **Reproducibility.** Keep `solver.num_workers: 1` unless the owner accepts run-to-run variation.
5. **Rollback.** Unset `FOCUS_SOLVER_ENABLED`, or return to the previous policy. Disabled runs never depend on anything
   the stage produced.

## What would allow a CP-SAT plan (not built)

A complete named model would need:

- per-person interval variables with whole-interval certificate validity;
- pool purity and activation caps;
- calendars with breaks and the OT window;
- travel/setup transitions between consecutive duties;
- CS, parts and QA floors;
- defended commitments.

It would also need an independent proof that the model represents the validator's checks. Until then the only
executable plans are the named decoder's.
