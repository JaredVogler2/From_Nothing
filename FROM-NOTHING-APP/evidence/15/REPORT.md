# Stage P15: Independent nine-check and extended final validation

Status: **fixture_verified**

## Changed paths

- `focus/validation/input.py`: `ValidationInput`, an independent source view of the sealed
  snapshot. It re-derives:
  - whole-interval certificate holding from raw holdings;
  - the S3 workday rule on each line's bounded calendar;
  - purpose membership, with synthetic people never executable;
  - the activation cap on the distinct admitted denominator;
  - release floors per purpose;
  - ancestors.

  It never imports `focus.engine`. The calendar geometry is the only shared authority.
- `focus/validation/plan.py`: `validate()` with V1–V9 and the extended checks `E_INVENTORY`,
  `E_LINEAGE`, `E_CS`, `E_COMMITMENT`, `E_ECONOMICS` and `E_CAPABILITY`.
  - **Receipts** carry counts, coverage (`CHECKED`/`VACUOUS`/`SKIPPED`), required skips and
    sorted violations. Zero violations with a required skip is invalid.
  - **Helpers:** `cli_validate` (`validate --candidate <id> --all-required`), `validate_stored`,
    `persist_receipt` and `load_incumbent`.
- `focus/validation/duty.py`: runs on the independent view. The activation cap is recomputed,
  never read from the pool. Omitted transfers are reported under V7, as the manual specifies.
- `focus/validation/publication.py`:
  - `validate_artifact` handles detached and readback bytes: strict canonical decode, then
    `E_ARTIFACT` re-derives scope, purpose, capability, source/snapshot/manifest hashes,
    policy, timezone, the full hard-edge graph, same-run CS lineage, obligation dates and
    frozen goals, plus the full plan validation;
  - `publication_capability` lists every reason a plan may not publish.
- `focus/engine/max/optimizer/orchestrator.py`: the independent validation gate.
  - **Search.** The seed and every retained candidate are validated (economic redispatch,
    repair, neighbourhoods; the solver in P22 uses the same gate). Validity comes first: an
    invalid candidate is rolled back and logged, and any valid candidate beats an invalid best.
  - **Final plan.** `schedule()` fully validates the final plan and persists the receipt.
  - **CLI.** `cli_schedule` reports `validation`, `publishable` and the search-gate counts.
- `focus/contracts/planning.py`: `IncumbentPlan` and `IncumbentAssignment` moved into the neutral
  contracts, so the validator never imports the engine. Added `PlanArtifact`, `EdgeLineage`,
  `CSLineage` and `FrozenGoal.members`.
- `tools/validate_plan.py`, `docs/execution/VALIDATION_COVERAGE.md`,
  `tests/integration/test_validator_mutations.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/integration/test_validator_mutations.py -q   -> 51 passed
.venv/bin/python -m pytest tests -q                                           -> 277 passed
FOCUS_RUNTIME_DIR=<tmp> python -m focus.cli validate --candidate <id> --all-required   (fresh process; exit 0 when valid, 2 when invalid)
```

The real final candidates validate clean with every one of the 15 required checks covered:
22 assignments for EXECUTABLE and 29 for FORECAST, in about 0.2–0.3 s each. Search validates
the seed and retained candidates (`validated` of 2 or more per run, 0 rolled back on genuine
decodes).

## Acceptance cases

| Case | Observed |
| --- | --- |
| A47 | **Corpus.** A 40-entry corpus corrupts the real EXECUTABLE candidate once per required check and per known counterexample. Attackers re-seal the hash, except in the hash-tamper case. **Results.** Every mutation is caught with its expected (check, code), coverage stays nonzero, and `publication_capability` lists blocking reasons. **Codes.** `WORK_NOT_CONSERVED` (also for the in-progress restart fiction), `UNKNOWN_PHASE`, `IDENTITY_MISMATCH`, `PRECEDENCE_VIOLATION`, `PREDECESSOR_UNRESOLVED` (omission never satisfies), AND-release precedence, `NONWORKING_DAY`, `S3_NONWORKING`, `OUTSIDE_CALENDAR_BOUNDS`, a segment in a break, `NO_START_CUTOFF` (13:46 caught, 13:45 equality allowed), `BEFORE_CUTOFF`, `OT_MISREPORTED`, short/duplicate/unqualified/loaned/synthetic crews, forged role, roster, pool key and evidence, QA by a production person, carry-in and plain overlap, `TRANSFER_MISSING`, `ACTIVATION_CAP`, executable work behind an unreceived part, inventory drop and blocked-as-scheduled, hash/snapshot/policy/proof lineage, misreported and missing commitment outcomes, forged cost and completion, conditional evidence in an executable plan. **Skips.** Zero violations with a skipped required check (`E_COMMITMENT` without the incumbent) is invalid. |
| A48 (P15 part) | **Detached bytes.** Detached bytes with a forged crew, a root edge (pred/succ swapped), the timezone, station/load, an offset or the source hash are each rejected by `validate_artifact`. Non-canonical bytes are rejected. Same-run CS lineage is checked against the raw rows. |
| A26 | In-progress work is never skipped. ROOT4 re-planned at its full 100 minutes is `WORK_NOT_CONSERVED` against the 60 remaining. ROOT4 moved before its incomplete PRE4 is a `PRECEDENCE_VIOLATION`. |
| A38 | **V8.** An extra activation in a pool beyond the cap is `ACTIVATION_CAP`; the cap is recomputed on the admitted denominator. **Every path.** A forged better-keyed candidate injected into economic redispatch and a neighbourhood decode is rolled back by the gate (`V5`/`E_CAPABILITY`) and never retained. |
| A64 (P15 part) | The FORECAST plan's virtual MHE/PSC/TST crews validate as forecast-only. The same plan relabelled EXECUTABLE is rejected by origin evidence (`SYNTHETIC_IN_EXECUTABLE`, `NOT_PLACEABLE_FOR_PURPOSE`). |
| E_CS | A self-consistent forged source is caught against the same-run raw CS rows: a re-hashed snapshot with a changed `LOAD_OFFSET` and a foreign-run fact gives `CS_RAW_FIELD_MISMATCH`, `CS_FACT_FOREIGN_RUN` and `SNAPSHOT_MISMATCH`. Tampering without re-hashing fails integrity first, and every other check is reported skipped. |

## Fixture versus live

Every check runs on the sealed fixture snapshot without SQL. Live validation needs the live
feeds behind the snapshot (P03–P10 gaps); the validator has no live dependency of its own.

## Remaining gaps

- No tool-requirement source is mapped (S04), so tool overlap is reported as not applicable.
- The solver path (P22) uses the same gate once it exists.
