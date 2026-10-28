# Stage P04 — Atomic source runs and immutable planning snapshots

Status: **fixture_verified**

## Changed paths

- `focus/data/acquisition.py`: CAPTURING → COMPLETE/REJECTED runs, per-dataset receipts, detached readback with lineage/hash verification, snapshot admission, compare-and-set head written last and forward-only in cutoff time, idempotent replay and resume.
- `focus/data/fixture_reader.py`: fixture and live readers with capture statuses (ROWS, CERTIFIED_EMPTY, EMPTY_UNCERTIFIED, FAILED, NOT_PROBED) and cutoff visibility emulation.
- `focus/data/snapshot_builder.py`: one coherent immutable `PlanningSnapshot` (census, jobs, phases, edges, releases, durations, requirements, people, holdings, pools, availability, calendars, same-run CS rows/facts, shortages, rework links, economics, carry-in, inference, gaps, dataset lineage, hash).
- `focus/contracts/planning.py`: snapshot extended with inference, shift ids, dataset lineage; pools with executable/forecast denominators.
- `sql/migrations/001_focus_fn_core.sql` (manual S06 verbatim, not executed), `sql/migrations/source_runs_draft.sql` (promotion procedures).
- `tools/acquire_sources.py`, `tests/integration/test_source_runs.py`.
- Settings: `FOCUS_RUNTIME_DIR` (isolated or writable fixture runtime), `FOCUS_SITE`, `FOCUS_PROGRAM`.

## Commands and results

```
.venv/bin/python -m focus.cli acquire --mode fixture --scenario manufacturing_demo   -> COMPLETE, head_moved, ~1 s
.venv/bin/python -m pytest tests -q                                                  -> 56 passed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A05 | A mandatory `cs_windows` read failing midway leaves the run REJECTED (`MANDATORY_DATASET_FAILED`); the previous head is unchanged. Certified empty (`econ_liability_checkpoint`), empty-without-receipt (`EMPTY_UNCERTIFIED`) and a failed optional feed (`FAILED`) stay distinct; the latter two are readiness gaps. |
| A06 | A CS dataset envelope from another run is attached → `DATASET_FOREIGN_RUN`. Rows tampered and resealed with a forged declared hash → `DATASET_HASH_MISMATCH`. A producer-declared active generation mismatch → `CS_GENERATION_MISMATCH`. In every case the head does not move. |
| A07 | A crash before the head update leaves a COMPLETE run with no visible head. Replaying the same key reconciles exactly one run, moves the head once (version 1) and duplicates no dataset; a further replay is a no-op. A crash mid-capture resumes the same run. |
| A48 (source part) | Every CS fact and evidence reference names the snapshot's own source run. The snapshot's CS lineage hash equals the run receipt. Raw load id and signed offsets are preserved. The snapshot hash round-trips. |

Also verified: runs and heads persist across independent CLI processes (subprocess acquire, replay, status), and the head never moves backwards to an older cutoff.

## Remaining gaps

- The live `SqlStore` (FOCUS_FN persistence) is implemented with the consolidated migrations in P23. Fixture storage already implements the same protocol.
- Live supplemental producers (rosters, holds, part events, …) do not exist yet; they are reported `NOT_PROBED` and block only executable capabilities.
