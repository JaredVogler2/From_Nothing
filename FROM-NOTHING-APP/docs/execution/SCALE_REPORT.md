# Complete-scope scale report (P22)

> **Production-shaped measurements** (about 7,500 tasks and 25,000 dependencies per aircraft) and the optional native core
> are in `PRODUCTION_SCALE.md` and `NATIVE_CORE.md`. The numbers below use the P22 14-operation template.

Every number here was measured on this build's benchmark machine: 4 vCPU Intel Xeon at 2.10 GHz, 16 GB RAM,
CPython 3.12.3. The inputs are synthetic scale fixtures, labeled `SYNTHETIC_SCALE_FIXTURE (real readiness not
established)`. They are not live proof. Raw receipts are in `evidence/22/` (`benchmark_solver_off.json`,
`benchmark_solver_on.json`, `SUMMARY.md`).

## What the fixture contains (nothing shrunk to meet a budget)

`build_scale_fixture(n)` builds `n` aircraft from a 14-operation template:

- **Graph.** 16 hard FS edges: a long precedence cone that joins two chains at J07 and J13.
- **Sharing.** Four production teams share pools across every aircraft, with `max(8, ceil(n/8))` mechanics per team
  and shift.
- **Scarce certificates.** 60 % of mechanics hold one cert, 25 % hold two, 15 % hold three.
- **QA.** Final QA on three operations. Sourced in-process QA holds on J04 and J09 (two holds each, 45 inspector
  minutes) are derived rows added in P22, so the random stream is unchanged.
- **Rework and parts.** Rework children on about 12 % of aircraft; part shortages with and without ECDs on about
  12 %.
- **Blocked inventory.** One aircraft is entirely ON HOLD and one is missing flow standards. Both stay in the census
  with reasons (A03).
- **Virtual support.** A forecast-only functional test (team TST, virtual support) on one aircraft in ten, also
  derived. It is an honest readiness gap in EXECUTABLE plans.
- **Calendars.** The horizon crosses the 2026-11-01 fall-back, so S1 and S2 instants move one UTC hour. The only
  length-adjusted shift (S3 on 10-31) has no roster here; its duty rules are covered by the calendar and ledger
  tests.

## Measured results

```bash
python tools/benchmark_solver.py --sizes 51,100,150,300 --budget-seconds 600 --max-moves 2 --out evidence/22
```

The command runs the solver off, then on. Each size runs in its own process, with its own fresh fixture runtime. The
search is bounded by moves, so the runs are reproducible.

**Contention.** The full test suite (one process) ran on the same 4 vCPUs during part of the run: the end of the
300-aircraft solver-off pass, the 51/100/150 solver-on runs and the start of the 300-aircraft solver-on run. Those
timings include that contention.

| Aircraft | Jobs | Phases (IP QA / final QA) | People (virtual) | EXECUTABLE wall s | FORECAST wall s | Neutral-seed decode s | Peak RSS MB, solver off / on |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 51 | 724 | 1,709 (154 / 139) | 162 (90) | 16.5 | 17.5 | 5.2 | 107 / 206 |
| 100 | 1,420 | 3,326 (292 / 279) | 208 (90) | 46.7 | 57.0 | 14.6 | 184 / 314 |
| 150 | 2,142 | 4,928 (426 / 410) | 262 (90) | 129.2 | 139.0 | 33.4 | 259 / 382 |
| 300 | 4,266 | 9,892 (874 / 821) | 434 (90) | 518.9 | 568.6 | 155.2 | 401 / 567 |

All 16 plans (4 sizes × 2 purposes × solver off/on) passed:

- **Validation.** Independent validation: zero violations and **zero required skips**. Every V1-V9 check has a nonzero
  count; at 300 aircraft V1 alone ran 8,254 checks in EXECUTABLE and 8,600 in FORECAST.
- **Inventory.** Complete: every census job has a disposition (724, 1,420, 2,142 and 4,266).
- **Blocked aircraft.** The blocked and missing-standards aircraft stay BLOCKED with reasons.
- **Support work.** Support tests stay deferred in EXECUTABLE (`SUPPORT_RESOURCE_UNAVAILABLE`) and conditional in
  FORECAST.
- **Search.** Every retained search candidate also passed the in-search validator: 2 to 5 per plan, 0 rolled back.
- **Proof.** `global_optimality_proven` is false everywhere.

**Growth.** The neutral-seed decode grows ×2.8 from 51 to 100 aircraft, ×2.3 from 100 to 150, and ×4.6 from 150 to
300 (fleet doubled). The superlinear term is explained below.

**Memory.** Peak memory stays under 0.6 GB even with OR-Tools loaded. That fits the 1G Tanzu instance in
`manifest.yml`, with headroom.

### The optional solver at each size

These are exact plan facts from the receipts. The comparison is lexicographic (S12), so a level above wins even when
a lower level moves the other way. Example: 300 FORECAST lowers controllable cost but raises overtime.

| Aircraft | Purpose | Status (improved level) | Known cost USD, off → on | Travel min, off → on | OT min, off → on | Solver stage s |
| --- | --- | --- | --- | --- | --- | --- |
| 51 | EXECUTABLE | IMPROVED (travel+wait) | 0.00 → 0.00 | 13,155 → 11,145 | 4,375 → 4,355 | 28.8 |
| 51 | FORECAST | IMPROVED (controllable cost) | 417.45 → 0.00 | 14,295 → 12,375 | 4,400 → 4,205 | 29.4 |
| 100 | EXECUTABLE | IMPROVED (controllable cost) | 1,941.86 → 1,640.16 | 24,930 → 22,560 | 9,769 → 8,534 | 51.7 |
| 100 | FORECAST | IMPROVED (controllable cost) | 1,932.59 → 1,527.20 | 26,955 → 24,405 | 9,516 → 7,803 | 50.9 |
| 150 | EXECUTABLE | NOT_BETTER: baseline unchanged | 1,817.08 → 1,817.08 | 40,635 → 40,635 | 11,186 → 11,186 | 87.2 |
| 150 | FORECAST | NOT_BETTER: baseline unchanged | 1,822.93 → 1,822.93 | 42,570 → 42,570 | 13,086 → 13,086 | 88.4 |
| 300 | EXECUTABLE | IMPROVED (controllable cost) | 2,873.04 → 2,705.30 | 79,560 → 72,075 | 23,030 → 21,160 | 235.1 |
| 300 | FORECAST | IMPROVED (controllable cost) | 3,455.78 → 3,392.12 | 83,280 → 77,595 | 21,200 → 23,306 | 329.8 |

- **Relaxation.** At every scale size CP-SAT stopped at its 20 s limit with a `FEASIBLE` relaxation (not proven
  optimal), with 849 to 5,551 phases in the model.
- **Probes.** Each probe is a complete named decode: about 4-5 s at 51 aircraft and 100-160 s at 300.
- **Churn.** Accepted probes moved 1,200 to 7,700 placements against the baseline. That churn is real; the owner
  weighs it before enabling.
- **Cost unit.** "Known cost" is the exact fractional-cent total from the receipts, rounded here to cents.

## Where the time goes

This profile is of one 300-aircraft neutral-seed decode (cProfile). It was taken before the scale fixture gained its
QA holds and support tests, and before the last two optimizations. It shows:

- 3,647 crewed phases and 133,963 booking attempts: candidate start times inside each shift, capped at 48 per shift;
- 3.9 M per-person conflict checks and 4.6 M qualification checks;
- `choose_crew` accounts for 90 % of decode time.

The decoder's work is **phases × candidate starts × pool size**:

- Pool size grows with the fleet, because capacity is shared.
- Candidate starts grow with congestion, up to the cap.

So runtime grows faster than linearly with fleet size, but no step scans all jobs squared or builds a dense
assignment matrix. Each phase looks only at its own team's pool in its own shift.

## Exact optimizations (P22)

None of these prunes a candidate. Each returns exactly what the rule it replaces returns, and each is pinned by a
test.

| Change | Why it is exact | Pinned by |
| --- | --- | --- |
| `DutyLedger.person_conflict` before the full `plan` derivation in crew selection | It is `plan`'s own first per-person checks (window, pool, cross-shift and located overlap), in the same order, with the same error. | `test_person_conflict_returns_exactly_what_plan_raises` |
| `DutyLedger.cap_problem` before planning a crew combination | Members already passed `plan` alone, so the activation cap is the only crew-level check left. `plan` still runs on the chosen crew. | `test_cap_problem_equals_the_activation_rule_of_plan` |
| Shallow transaction snapshot | Item tuples and interval rows are only ever replaced, never mutated. Activation counts and tool reservations are mutated in place, so they are copied. | `test_failed_transaction_restores_every_container_including_in_place_ones` |
| Cross-shift overlap check by keyed bisect | It used to rebuild a person's whole end-time list on every call, which is O(items). Now it bisects the sorted rows by end in O(log n), with the same comparisons. | `test_cross_shift_overlap_check_equals_brute_force` (3,000 random probes) |
| Whole-shift-window qualification memo | Certificate validity, revocation and status are interval-monotone: a bundle held over the whole window is held over every sub-interval. Anything else is checked exactly. | `test_qualifies_within_equals_the_exact_interval_check` (expiring, late, revoked, alternative and inactive holders) |
| Memoized transport ids (`JobKey`, `PersonKey`, `PoolKey`) | They are pure functions of frozen keys. | `test_memoized_transport_ids_are_unchanged` |
| Snapshot verification once per validation input | The snapshot is frozen. A tampered snapshot keeps failing on every validation. | `test_memoized_snapshot_verification_still_rejects_tampering` |

**Identical outputs.** The previous commit and this build were run on copies of the same runtime. Assignments,
duties, dispositions (including reasons), commitments and costs were byte-identical in canonical JSON for both
purposes:

| Input | EXECUTABLE before / after | FORECAST before / after |
| --- | --- | --- |
| demo fixture | 1.5 s / 0.6 s | 1.4 s / 1.1 s |
| 51 aircraft (`max_moves` 3) | 28.5 s / 12.8 s | 32.3 s / 15.8 s |
| 150 aircraft (`max_moves` 3) | 274.1 s / 92.9 s | 256.5 s / 108.8 s |

The pairs ran side by side on the same 4 vCPUs, so the absolute times include contention. Only the ratio matters.

## The optional solver at scale

See `SOLVER_ENABLEMENT.md`. The table above records each size's typed status and probes. On the demo fixture the
relaxation order is exactly better: travel+wait drops from 60 to 45 minutes, and every higher level ties.

## Uncertainty and limits

- **Machine dependence.** Runtimes belong to this machine. The search here is bounded by moves (`max_moves=2`), so the
  plans are reproducible. With the policy default of 400 moves, the 30 s wall budget binds instead; the receipt
  records `deadline_reached`.
- **Memory.** Peak memory is the high-water mark of each size's own process (`ru_maxrss`).
- **Synthetic inputs.** These fixtures stand in for live census shapes. Live readiness gaps OD-01..OD-18 still apply.
  Re-run `tools/benchmark_solver.py` on a live-shaped census before sizing hosts.
- **Remaining superlinearity.** It comes from shared-pool growth times congestion. The next exact steps would be a
  per-shift free-interval index per person, so candidate starts with fewer than `count` free qualified people are
  skipped without a per-person probe, and an incremental activation set. Neither changes any result.
