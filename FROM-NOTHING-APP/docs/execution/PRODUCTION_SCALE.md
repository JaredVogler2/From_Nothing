# Production-scale measurements and the path to 50 aircraft in under 10 minutes

**Status: measured on production-shaped synthetic data, not live proof.** The target is a 50-aircraft plan in under
10 minutes, inside a 15-minute window at shift end (data, plan, validation, publish). Today's engine is far from it at
that size. This page records what was measured, where the time goes, and the ordered work that closes the gap.

## The shape measured

`focus/data/production_fixture.py` follows the program's stated numbers, per aircraft:
- about 4,500 baseline tasks over positions 0-3 and roll out, each position owning its tasks;
- 3,000 rework tasks, 30 % of them open at a time;
- about 22,000 model-level dependencies, plus the rework and part links (27,000 in total), crossing teams;
- one station window per position.

Teams and QA are shared across the whole fleet, so every aircraft competes for the same mechanics. Every assumption is
an explicit parameter.

## Results

`python tools/benchmark_production.py --sizes 1,5 --purposes FORECAST --engines native,python --max-moves 0`
(4 vCPU, CPython 3.12.3; receipts in `evidence/production-scale/`). Both engines schedule the same admitted snapshot in
one process.

| Aircraft | Open jobs | Phases | Dependencies | People | Load | Python core | Native core | Same plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 5,040 | 10,766 | 27,312 | 342 | 33 s | 55.8 s | **25.5 s** (2.2×) | yes |
| 5 | 16,200 | 44,148 | 126,848 | 710 | 178 s | 1,186 s | **239 s** (5.0×) | yes |

- **Validity:** every plan is valid, with zero violations and a complete inventory. In the 5-aircraft plan, all
  16,200 open jobs are placed, 8,542 of them as conditional work behind parts not yet received.
- **Identical plans:** both cores produced the identical plan (same candidate id and content hash) at both sizes.
- **Search:** these runs are first-pass plans with no improvement search (`--max-moves 0`), to measure the cost of
  building one plan.
- **Memory:** peak 0.5 GB for 1 aircraft and 1.7 GB for 5 aircraft.

## Engine v2 (October 31, 2026)

`python tools/benchmark_production.py --sizes 1,5 --purposes FORECAST --engines native,python --max-moves 0`; receipts in
`evidence/production-scale/engine-v2/`. Both cores scheduled the same snapshot and produced identical plans at both sizes.

| Aircraft | Native core before | Native core now | Python core now | Load before | Load now |
| --- | --- | --- | --- | --- | --- |
| 1 | 25.5 s | **10.2 s** | 47.7 s | 33 s | 19 s |
| 5 | 239 s | **55.9 s** | 1,141 s | 178 s | 103 s |

What made the difference:
- **The whole time-slot search for a phase runs in one Rust call**, and every opening is tried (the 48-start limit per
  shift is gone in both cores).
- **Exact start skipping.** Before a crew attempt, the search counts the people who could be booked alone (no conflict,
  inside their open windows, a transfer slot into and out of the work, hours within caps) and are qualified, allowing at
  most cap - active new heads. A start without enough of them is skipped. At 5 aircraft, crew attempts fell from 1.28
  million to one per phase.
- **No quadratic setup.** The search no longer lists every job's competitors (quadratic in fleet size).
- **Faster records.** A faster canonical codec with identical bytes; datetime conversions cached by value; a snapshot
  verified once per process; record hashes streamed item by item (same digest, about 1 MB instead of hundreds).

Also in this version: cost-of-delay priority (the aircraft's daily penalty decayed by the phase's slack), half of the
search moves aimed at the longest waits, and repeatability (stop on the move bound; a decision digest in every receipt;
the same decisions across processes with different hash seeds).

## What changed in the native-core trial

- **Native core** (`native/`, `docs/execution/NATIVE_CORE.md`): the duty ledger and crew selection in Rust, with
  byte-identical plans. A crew choice now costs about 17-36 µs in Rust.
- **Part-release check (V9)**, a validator check, now runs in one pass. It used to build and sort every assignment's
  full predecessor set, which cost 44 s of an 87 s profiled one-aircraft run. Now it walks once forward from each part
  release and each blocking shortage. Its findings are identical to the old check's on every corruption in the A47 test
  corpus and on forged work behind blocking shortages (`tests/unit/test_validator_v9_one_pass.py`).

## Where the time goes now (5 aircraft, native core)

`evidence/production-scale/PROFILE_5_AIRCRAFT_NATIVE.md` is a cProfile of the 239 s run. The profiler roughly doubles
wall time, so the shares below are what matter.

| Share of the profiled run | What |
| --- | --- |
| **65 %** | **Two scheduling passes.** They made 2.38 million crew-selection attempts for 34,000 phases: about 69 per phase, because start times are probed one at a time. Per attempt: the Rust crew choice, a hashed assignment id, shift and calendar arithmetic in Python, and the Python loop itself. |
| 18 % | **Independent validation**, about two-thirds of it re-hashing the whole snapshot and the candidate. |
| 10 % | **Sealing and storing the candidate** (canonical JSON and SHA-256 of about 17,000 assignments and their duties). |
| 7 % | **Context construction and the rest.** |

Loading the data (acquisition, admission and snapshot build) is outside the profile: 178 s at 5 aircraft.

**Extrapolation to 50 aircraft.** From 1 to 5 aircraft, the native plan time grew 9.4× for 3.2× the open work, because
each phase searches a larger pool. Fifty aircraft is about 10× the open work of five, which puts one plan at well over
an hour today. So it is not viable yet.

## The path to under 10 minutes (ordered by payoff)

Each step keeps two guarantees:
- **Same plans:** the native path's plans stay byte-identical to the Python reference's, checked by the parity tests.
- **Independent validator:** every plan is still re-checked from source.

1. **Move the time-slot search into Rust.**
   - Ported: the shift scan, candidate starts, calendar advance, assignment ids and crew choice. Only finished
     placements cross back into Python.
   - Removes most of the Python cost per attempt, which is the bulk of the 65 %.
2. **Skip start times that cannot work, exactly.**
   - Keep a per-person index of free time, so a start time with fewer free qualified people than the crew needs is
     skipped without a full crew attempt.
   - Targets the 69 attempts per phase without changing which start is chosen.
3. **Hash less, and faster.**
   - Verify an admitted snapshot once per process, keyed by its content hash.
   - Use a faster canonical encoder, and never re-hash an unchanged candidate.
   - Targets the 28 % spent in validation and sealing.
4. **Load incrementally.** Apply the shift's changes to the last admitted snapshot instead of rebuilding it from
   scratch: completions, new rework, shortage changes and attendance. This is the delta pipeline described for the
   15-minute shift-end window.
5. **Warm start and parallel search.** Start from the current plan and evaluate search moves on several cores.

Each step will be measured with `tools/benchmark_production.py` at 1, 5, 10, 25 and 50 aircraft before the next one
starts.

## The 15-minute shift-end window (target budget)

| Step | Budget |
| --- | --- |
| Delta pull from Teradata to SQL Server (changed rows only) | ≤ 2 min |
| Apply the changes to the in-memory snapshot and admit them | ≤ 1 min |
| Plan: update the in-shift plan, improve until the deadline, always holding a valid plan | ≤ 8 min |
| Independent validation, staging, readback, sign, accept | ≤ 2 min |
| Publish (one static snapshot per shift) | ≤ 1 min |

The in-shift runs that keep a warm plan ready are internal only: never displayed, never published.
