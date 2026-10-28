# Stage P12: SQL economics Registry and complete-fleet cost evaluation

Status: **fixture_verified**

## Changed paths

- `focus/economics/repository.py` (from P04, tightened here). Exactly one current mapping,
  one active scenario and **at most one** effective contract assignment per aircraft
  (`CONTRACT_ASSIGNMENT_CONFLICT`, no row-order pick). The original sale basis must name the
  chosen term; two bases give `SALE_BASIS_CONFLICT`.
- `focus/economics/registry.py`: `EconomicsRegistry`.
  - **Penalty.** Exact `Fraction` cents. `LINEAR_DAILY` only, with explicit caps only. Known
    zero differs from unknown. Unsupported curves are explicit, never silently linear.
  - **Weights.** `derive_priority_weights` gives exact marginal weights normalized by the
    largest marginal, with raw, marginal and normalized values all exposed.
- `focus/economics/evaluator.py`.
  - **Completions.** Complete-scope aircraft completions: every required job must be
    released, never the max of the scheduled subset.
  - **Costs.** Per-line cost is split into unavoidable and controllable parts, with the
    accrued floor reported separately. The summary is never complete while any aircraft is
    unknown.
- `queries/economics/effective_terms.sql`, `queries/economics/liability_floor.sql`: read-only
  SQL mirrors of the resolution and the checkpoint floor.
- `docs/execution/ECONOMICS_POLICY.md`.
- **Placeholder fix.** Literal `%s` removed from comments in `queries/durations/*.sql` and
  `queries/workforce/infer_mechanic_teams.sql`; pymssql binds client-side.
- `tests/unit/test_economics.py`, `tests/integration/test_economic_steering.py`,
  `tests/unit/test_query_files.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_economics.py -q                 -> 7 passed
.venv/bin/python -m pytest tests/integration/test_economic_steering.py -q  -> 2 passed
.venv/bin/python -m pytest tests/unit/test_query_files.py -q               -> 3 passed
.venv/bin/python -m pytest tests -q                                        -> 203 passed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A44 | **Setup.** PROD-A S1 is restricted to one shared crew (N=3, cap 2). Lines 1301/1302 have due 10-26 04:00Z, with fixture terms $100/day and $10/day. **Weights.** The registry weights are exactly 1 and 1/10. **Redispatch.** Economic redispatch is accepted (stage accepted = 1). The first shared-crew assignment moves from 1302 to 1301, 1301 completes earlier, and the exact controllable cents fall below the neutral seed. Travel and OT are not worse. **CLI.** `cli_schedule` reports the weights as "1" and "1/10", 19 dispositions, the baseline and final cents (final < baseline), `complete_fleet_cost = false` and the stage list. A foreign policy id gives `POLICY_MISMATCH`. |
| A45 | **Exact evaluator governs.** With a capped term, lower weighted tardiness (Y) costs more exact dollars than X: 8000 vs 5000 cents. The exact evaluator governs. Unknown aircraft 3 is listed, never zero: `total=None`, marginal shown. |
| A46 | **Labels.** Fixture terms are labelled ASSUMED. 1306 has no term and stays UNKNOWN: no fabricated 100k/day. The CapFraction field is never applied. **Unproven actual.** An ACTUAL term without approval or sale basis gives `TERM_AUTHORITY_UNPROVEN`. **Zero and due.** A true 0.0000 rate is known zero. A missing CS proxy gives `DUE_UNKNOWN`. **Ambiguity.** Two effective contract assignments make the aircraft UNKNOWN; two sale bases block ACTUAL. |
| A23 (P12 part) | The economic due is the explicit CS744 planning proxy: the gate date plus 2 days of grace, applied once, at the end of the local date. It stays distinct from the gate and the CS earliest. |

## Fixture versus live

Fixture terms are ASSUMED planning assumptions. Live economics needs owner terms, approvals,
sale bases and checkpoints (OD-12) and a commercial due policy (OD-06). Until then live costs
are unknown or assumed, never invented. The SQL mirrors are written and read-only, but not
run against a server.

## Remaining gaps

- No live economics terms or approvals (OD-12); no commercial due (OD-06).
- Only `LINEAR_DAILY` is supported. Other curves stay `UNSUPPORTED_CURVE` until typed policy
  pieces are approved.
