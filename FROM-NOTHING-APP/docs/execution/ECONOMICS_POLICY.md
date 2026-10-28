# Economics policy (S12, P12)

FOCUS steers dispatch with modeled delivery penalties and judges candidates by exact money.
This page is the policy the code implements. Each rule names its authority:

- `focus/economics/repository.py` resolves one effective term per aircraft;
- `focus/economics/registry.py` turns a term into exact penalties and weights;
- `focus/economics/evaluator.py` costs a complete candidate.

`queries/economics/` holds the read-only SQL mirrors used to check live source rows.

## 1. Sources and one effective term per aircraft

The S04 objects are `WEB_APP.FOCUS_SOL_TESTING2_*`:

- `MappingRevision`
- `AircraftMapping`
- `EconomicsScenario`
- `ContractAssignment`
- `EconomicsTerm`
- `OriginalSaleBasis`
- `LiabilityCheckpoint`

They are captured in the same atomic source run as the WIP census.

| Step | Rule | Failure is |
| --- | --- | --- |
| Mapping | Exactly one `IsCurrent` mapping revision; the aircraft row comes from it | `NO_CURRENT_MAPPING_REVISION`, `MULTIPLE_CURRENT_MAPPING_REVISIONS`, `AIRCRAFT_NOT_MAPPED` |
| Scenario | Exactly one active scenario effective at the cutoff, half-open `[from, to)` | `NO_SINGLE_ACTIVE_SCENARIO`, `MULTIPLE_ACTIVE_SCENARIOS` |
| Assignment | At most one active effective contract assignment for the aircraft in that scenario | `CONTRACT_ASSIGNMENT_CONFLICT`: no term is picked by row order |
| Term | Selector precedence `AIRCRAFT_OVERRIDE > CONTRACT > CUSTOMER_MODEL` (`economics.selector_precedence`, manual_specified), then the latest `TermVersion` | `NO_EFFECTIVE_TERM`, `TERM_VERSION_CONFLICT` |
| Keys | A NULL `LineNumber`/`ContractKey` matches only the CUSTOMER_MODEL selector (customer code and minor model); keys are compared exactly, never defaulted | no match |

Every failure leaves the aircraft **UNKNOWN** with its reasons. Unknown is never zero.

## 2. Money, currency and authority

- A `decimal(19,4)` daily rate becomes exact rational cents (`Fraction`). Sub-cent rates stay
  exact; nothing is rounded before the final display.
- Only `economics.supported_currencies` (fixture: USD) are supported. Any other currency is
  `UNSUPPORTED_CURRENCY`. A negative or missing rate is `DAILY_RATE_INVALID`.
- A true zero rate is **known zero**: no economic pressure, but still known. It differs from
  unknown.
- Authority labels:
  - `ASSUMED`: a planning assumption, reported as modeled cost and never as contractual liability.
  - `ACTUAL`: needs Provenance ACTUAL, an approval, and exactly one original sale basis
    that names the chosen term. Two bases give `SALE_BASIS_CONFLICT`.
  - `UNKNOWN`: anything else (`TERM_AUTHORITY_UNPROVEN`).
- No production default rate exists. A fabricated 100k/day, or an arbitrary sale value, is never used.

## 3. Due date

`due.basis` = `CS_PLANNING_PROXY` (open_decision OD-06). The fixture uses the same-run CS744
unload date as an explicit planning proxy (`due.proxy_date_basis` = `BASELINE_PROXY`, which adds
the S09 grace of `gate.proxy_grace_days` = 2 exactly once). The instant is the end of that local date
(`due.instant`). A populated field alone is never contractual due. With no proxy fact the
due date is `DUE_UNKNOWN` and the cost is unknown.

## 4. Modeled penalty

Only `LINEAR_DAILY` is supported (`economics.penalty_curve`, fixture_default):

```
delay_minutes = max(0, completion_utc - due_utc)
raw           = rate_cents_per_day * delay_minutes / 1440     (exact)
total         = min(cap_cents, raw)                            only when an explicit cap exists
```

- **Caps are explicit only** (`economics.cap_policy` = `EXPLICIT_ONLY`). The scenario
  `CapFraction` schema field is reported as `CAP_FIELD_NOT_APPLIED`. It is never a silent 25% cap.
- **Unsupported curves are explicit.** A curve such as `STEP_WEEKLY` is `UNSUPPORTED_CURVE`,
  never silently linear.
- **Two parts of the cost.** Each aircraft reports the unavoidable exposure (the penalty at
  the cutoff) and the controllable increment separately.
- **Accrued floor.** The latest liability checkpoint (highest `SequenceNumber`) is reported
  as a separate accrued accounting floor and is never merged into the modeled cost.

## 5. Aircraft completion

An aircraft completes only when **every** required delivery-scope job on its line is
released: blocked, quarantined and deferred jobs included, verified-complete anchors
excluded. It is never the maximum over the successfully scheduled subset.

- Any unreleased required job makes the completion and the cost **unknown**.
- A completion that rests on forecast-only evidence (part ETA, nominal or virtual people) is
  **conditional**, and is labelled.

## 6. Priority weights (dispatch guidance only)

`EconomicsRegistry.derive_priority_weights(completions)` takes, for each aircraft, the
marginal penalty of one more day (`economics.weight_horizon_days` = 1) at the projected
completion, respecting the cap and the curve. The marginals are normalized exactly by the
largest one (for example $100/day versus $10/day gives 1 and 1/10), with no coarse
rounding. Raw rate, marginal and normalized values are all exposed in the receipt.

- Known zero gets zero weight.
- Unknown aircraft get no weight. They stay visible and their marginal is reported where it
  is known.

## 7. Acceptance: exact complete-candidate comparison

Weights never decide acceptance. `MaxOptimizer` keeps a candidate only when it is strictly
better under the exact lexicographic key (no monetary tolerance,
`economics.monetary_tolerance_cents` = 0). The key, in order:

1. commitments defended (same slot), then exact incumbent crews (P14). A candidate may never
   lose or downgrade a commitment the current best keeps;
2. aircraft with unknown completion (fewer is better);
3. deferred phases;
4. exact controllable modeled penalty over known and conditional aircraft;
5. travel plus wait minutes, then overtime;
6. crew-continuity breaks;
7. a deterministic signature.

A candidate whose unknown scope is unchanged can improve the known portfolio, but it cannot
claim complete-fleet optimality. `CostSummary.complete` is false while any aircraft is
unknown, and the heuristic always reports `global_optimality_proven = false`.

## 8. Fixture versus live

The fixture terms are labelled ASSUMED. Line 1301 is $100/day and 1302 is $10/day
(A44 steering). Line 1306 has no term and stays unknown.

Live economics needs the owner's terms, approvals, sale bases and checkpoints (OD-12) and a
commercial due policy (OD-06). Until then live costs are unknown or assumed, never invented.
