# 03 — Alternative task-valuation models, compared without favoritism

**Source revision:** `5d1236c`.

**Measured inputs (EXPERIMENT, synthetic):**
- Rank agreement of each signal with the exact one-shift deferral counterfactual (*d1*) on two stratified samples at 5 aircraft: 121 units (re-run at `5d1236c`) and 80 units (an earlier run of the same plan). Source: `evidence/fidelity.txt`, script `evidence/scripts/fidelity.py`.
- Runtimes from the study logs (`06`).
- Everything else in this file is labeled **judgment**.

## 1. The models

### A. Engineered standard person-minute points (current concept)
- **Unit and formula.** *P_u* = *e_u*, the frozen standard person-minutes of earning unit *u* (`EarningUnit.standard_person_minutes`). Shift points *P_shift* = Σ *e_u* = B0.
- **What one point means.** One minute of one qualified person's standard work.
- **Additivity.** Additive and comparable across teams and shifts in work-content units. This is exactly what the canonical score pools.
- **Edge cases.**
  - Zero effort (release milestones) earns nothing, which is correct.
  - A missing standard makes the job blocked at admission, never defaulted (`FINAL_ACCEPTANCE.md:142`).
  - Unknown economics are irrelevant to it.
  - Blocked work: relief, not points.
- **Weaknesses.**
  - Cannot tell a delivery-protecting 15-minute job from slack filler.
  - Inherits estimation error: effort = elapsed × crew, and rework uses P75.
- **Role.** An earning currency. Not a priority indicator.
- **Gaming.** Cherry-picking easy units (shown as missed units and OOS); inflated standards (an IE review problem, not a team lever).
- **Cost.** O(1) per unit. Already computed.

### B. Criticality and float points
- **Variants.**
  - B1: CPM tail.
  - B2: CPM total float.
  - B3: resource-constrained float on the published schedule graph (precedence plus person and position sequence edges).
  - B4: stochastic criticality index (the probability of being critical under duration noise).
- **Unit.** Minutes of float, or a probability. One point means nothing additive; it is an ordinal signal.
- **Measured fidelity:**

  | Signal | Spearman vs *d1* |
  |---|---|
  | B1, CPM tail | **−0.37 / −0.44** (anti-correlated: long downstream paths far from completion carry float) |
  | B2, −TF | +0.66 / +0.73 |

  B3 and B4 were not measured (NOT_IMPLEMENTED).
- **Edge cases.** Unknown economics do not matter, but exposure is ignored: zero float on an early aircraft is not urgent (CE2).
- **Role.** A priority indicator (B2, B3). Not a currency.
- **Cost.** B2: O(V+E), measured 0.10–0.16 s for all phases at 5 aircraft. B3: one graph pass, about 1 s at 5 aircraft.

### C. Aircraft cost-of-delay / urgency points
- **Formula.** *P_i* = `cost_of_delay` = *w_a* × COD_DECAY[slack // 6 h], in cents × 10⁶ per day (`urgency.py:92-116`).
- **What one point means.** "If this phase's aircraft slips a day, this much, discounted by the phase's slack against the due date."
- **Additivity.** Not additive: it is an aircraft value replicated across phases.
- **Measured fidelity.**
  - Spearman +0.49 / +0.58.
  - Positive for 99% of sampled units, against 39% nonzero in *d1*, so zero/nonzero agreement is 0.40 / 0.41.
  - Every phase of a late aircraft gets the full weight.
- **Edge cases.**
  - Unknown economics leave no key, so the phase is undefined (must not be read as 0).
  - Early aircraft: decays.
  - Caps: not applied today.
- **Role.** A dispatch heuristic. It is already the scheduler's job, and is weak as a value.
- **Cost.** 0.13–0.20 s for all phases at 5 aircraft.

### D. Counterfactual deferral marginal impact
- **Formula.** *V_i* = *J*(plan with *i* deferred one shift, re-planned) − *J*(plan). *J* is a vector (`04`). The money component is controllable modeled cost.
- **Variants:**
  - D1: exact re-decode with the plan's priority keys;
  - D2: repair-mode re-decode;
  - D-proxies: (b) precedence-float dollars, (c) schedule-graph right shift.
- **What one point means.** Modeled dollars of fleet delivery penalty if this one task slips one shift and nothing else does.
- **Additivity.** **Not additive.** Measured: the joint value of 4–10 critical units on one aircraft equals the largest single value, so summing overstates 2.2–6.2×. Mixed sets can go either way (`06`).
- **Measured fidelity of the proxies:**

  | Proxy | Spearman | Zero/nonzero agreement |
  |---|---|---|
  | (b) precedence-float dollars | +0.76 / +0.80 | 0.87 / 0.90 |
  | (c) schedule-graph right shift | +0.71 / +0.83 | 0.89 / 0.91 |

- **D1 itself is noisy.** 3.3% of sampled units had *negative* exact values, the greedy list-scheduling anomaly: delaying one task gave a better fleet schedule, down to −$473k.
- **Edge cases.** Unknown economics: not priced. Early aircraft: $0 until the due date. In synthetic first shifts, **every** unit had *d1* = $0 (378 of 378 at 5 aircraft, 60 of 60 sampled at 10).
- **Role.** An analytic and management value. Unfit as a currency (non-additive, mostly $0 in the near term, endogenous).
- **Cost.**
  - D1: one full decode per unit, measured 2.5–2.7 s at 5 aircraft. At 50 aircraft, about 85 s per decode (`PRODUCTION_SCALE.md`) × about 3,000 units is **about 70 hours per shift** on one process.
  - Proxies: about 1 s at 5 aircraft.

### E. Dependency-unlocking, bottleneck relief and shadow prices
- **Variants.**
  - E1: count or effort of downstream jobs whose last prerequisite this is (the realized "last finisher" rule, as in `progression._unlocks`).
  - E2: planned downstream unlock count.
  - E3: resource shadow prices (Lagrangian multipliers on crew-time slots, `04`).
- **What one point means.** "Work released", or "dollars per unit of scarce capacity freed".
- **Additivity.** E1 is additive by construction (each unlock is credited once, to the last finisher) but arbitrary between AND-prerequisites (CE8). E2 double counts shared successors and ignores downstream feasibility (CE6).
- **Edge cases.** Blocked downstream work makes the count meaningless without feasibility (CE6).
- **Role.** A diagnostic. E1 is already recorded at zero value.
- **Cost.** E1 and E2: one graph walk. E3 would need an LP or Lagrangian relaxation that FOCUS does not have: NOT_IMPLEMENTED, high engineering cost, and caps or lexicographic levels make it non-convex.
- **Fidelity.** NOT_MEASURED.

### F. Hybrid: effort × bounded impact multiplier
- **Formula.** *P_u* = *e_u* × *m*(band_i), with *m* ∈ [1, *m*_max] (for example 1, 2, 3) and the band taken from B or D at publication, frozen.
- **What one point means.** A weighted standard minute.
- **Additivity.** Additive within a shift by construction, but the weights are arbitrary. A team's total depends on how much of its work the model labels critical, which is the optimizer's assignment, not the team's execution.
- **Measured fidelity.** Effort × band(c), with 1 for $0, 2 for nonzero and 3 for the top decile: **+0.17 / +0.25**. Effort dominates the ranking, so the hybrid neither tracks impact nor stays a clean work measure.
- **Precedent.** FOCUS recognition already does this in a mild form: the gate bonus adds up to 35% of base for work at stations behind their gate (`points.py:57-70`).
- **Role.** It would be both priority and currency, which is the source of its problems (`05`).
- **Gaming.** Lobbying over band thresholds, and clustering effort on multiplied work.

### G (proposed). Two currencies: effort attainment plus frozen priority classes with critical-coverage
- **Definition.**
  - Earned points stay A (effort).
  - At publication, each phase gets a frozen **priority class**: CRITICAL, AT_RISK, FLOAT, UNKNOWN or UNPRICED, with a reason code. The class comes from a cheap schedule-graph proxy (B3 or D-proxy), at a horizon long enough to be informative (resource-constrained float to the due date in shifts, not one-shift dollars, which are almost all $0).
  - Two separate numbers are reported:
    1. effort attainment (the existing score);
    2. **critical coverage** = critical units done ÷ critical units scheduled, adjusted for approved relief.
- **What one point means.** Effort keeps its meaning. Classes are labels with reasons, not points.
- **Additivity.** Effort is additive. Coverage is a ratio of counts or effort within the class. No impact dollars are summed.
- **Edge cases.** UNKNOWN or UNPRICED is shown as such and never counted as FLOAT. A shift with no critical units has coverage "n/a", not 100%.
- **Role.** Priority indicator (the class), earning currency (effort) and a diagnostic (coverage), kept separate.
- **Cost.** One graph pass per publication, about 1 s at 5 aircraft, to be measured at 50 (`06`).
- **Companion (management only).** An **expected-versus-realized delay-cost ledger**: the per-aircraft change in projected cost between consecutive forecast plans, attributed to missed units and claim causes so that the parts add up to the total. It is not team points.

### Optional H. Statistical importance learned from history
Requires executed-plan histories with disruption outcomes. None exist in this repository (OD-15: no live producers). **Not feasible now.** The data requirement is stated in `06`.

## 2. The 15-minute task versus the 20-hour job

- **T** is 15 min with 1 person. It is the last prerequisite on a late aircraft's critical chain: slipping it one shift moves delivery 8 h, $100,000 at the illustrative $300k/day.
- **J** is 20 h with 2 persons. It has three days of float, so slipping it one shift is $0. It fills spare crew time and must be done this week.

| Model | T | J | What it tells the floor |
|---|---|---|---|
| A, effort | 15 | 2,400 | J is worth 160× T, so T looks trivial (wrong for priority) |
| B2, float | TF 0, critical | TF 3 days, slack | T first (right) |
| C, urgency (late aircraft) | full weight | full weight × e^(−3) ≈ 5% | T first, but J is never worthless |
| D, marginal | $100,000 | $0 | J is worthless (wrong for accountability and for next week) |
| F, effort × band | 15 × 3 = 45 | 2,400 × 1 | J still 53× T, so the band barely moves the incentive |
| G, class + effort | CRITICAL, 15 earned minutes | FLOAT, 2,400 earned minutes | T is flagged first, and both count fully for work done; missing T shows in critical coverage |

Only G avoids making either task "worthless" while still signalling which one protects delivery.

## 3. Decision matrix

Ratings are **judgment** unless the cell cites a measurement:
- **H**, **M**, **L**: good, mixed, poor on the criterion.
- **(m)**: measured.
- **n/m**: not measured.

| Criterion | A effort | B2/B3 float | C urgency | D counterfactual | E unlock / shadow | F hybrid | G classes + effort |
|---|---|---|---|---|---|---|---|
| 1. Fidelity to measured fleet impact | **L (m)**: Spearman −0.20/−0.23 | **M (m)**: B2 +0.66/+0.73; B3 n/m | **M−(m)**: +0.49/+0.58, zero agreement 0.40 | D1 is the reference but noisy (3.3% negative); proxies **M+(m)** +0.71–0.83 | n/m | **L (m)**: +0.17/+0.25 | Class from the D/B3 proxy: **M+(m)** for the zero/nonzero split, 0.87–0.91 |
| 2. Coherence and additivity | **H**: additive minutes | n/a (ordinal) | L: replicated aircraft value | **L (m)**: non-additive, 2.2–6.2× overstatement | E1 H (last finisher), E2 L | M: additive, arbitrary weights | H: effort additive, classes not summed |
| 3. Feasibility correctness | H | B2 L (no resources), B3 M | M | D1 H (full decode), proxies M | M | as its band source | M (as its proxy; misses some capacity effects (m)) |
| 4. Stability under perturbation | H (frozen) | M | M | **L (m)**: the proxy's Spearman moved 0.71↔0.83 between samples; D1 anomalies | n/m | M | M (classes are coarse; thresholds cause cliff effects) |
| 5. Fairness and controllability | H | n/a | L | L (endogenous) | L | **L** (assignment-dependent totals) | H for the grade (effort); coverage is diagnostic |
| 6. Cost at production scale | H (trivial) | H (seconds) | H | **D1 L (m)**: about 70 h/shift extrapolated; proxies H | E3 L | as its source | H (one graph pass) |
| 7. Interpretability | H | M | L ("cents × 10⁶") | M ("$ if this slips") but misleading when summed | M | M | H (class + reason + minutes) |
| 8. Provenance and maintenance | H (exists) | M | H (exists) | L (counterfactual versioning, solver variance) | M | M | M (new artifact, small) |
| 9. Fit with current scoring and goals | H (it is B0) | — | — | L | — | M (changes B0 semantics) | H (B0 unchanged; adds a side metric) |
| 10. Incentive risk | M (easy-unit cherry-picking; standards) | M | M | **H risk**: teams chase $ tasks; the bow wave | M | H risk (band lobbying, critical-only focus) | L–M (coverage could be gamed by misclassifying; it is computed, not reported, so no team lever) |

**Contenders:**
- **G**: classes plus effort, two currencies.
- **F**: hybrid single currency.
- **D**: counterfactual impact, as an analytic.

**Least complex:** A, effort only, the status quo.

**Rejected:**
- C as a value: weak fidelity, uninterpretable units.
- B1, CPM tail: anti-correlated in the measurements.
- E2, unlock counts: ignores feasibility and double counts.
- H, history: no data.

## 4. Formalized in 04
1. **G**: frozen priority classes plus effort attainment plus critical coverage.
2. **F**: effort × bounded multiplier, formalized to show exactly where it breaks.
3. **D**: counterfactual marginal impact with a lexicographic *J* and sum-to-total attribution. This is the only defensible way to talk about task dollars, and it belongs in analytics.

## 5. The argument against my provisional favorite (G)
- **Two numbers are harder to explain than one.** Leadership will be tempted to combine them, re-creating F.
- **Class thresholds are arbitrary and create cliffs.** A unit just over the threshold is "FLOAT" and invisible to coverage.
- **The proxy behind the class is imperfect.** It misclassifies about 10–13% of units on the zero/nonzero split (measured), and it misses capacity effects (measured: the QA-bottleneck unit priced at $0).
- **In the near term the class may be empty.** On synthetic first shifts every unit was $0 for a one-shift slip. A class based on one-shift dollars would flag nothing, so G must use a longer horizon (float in shifts), which is weaker evidence of consequence.
- **Critical coverage can have tiny denominators.** One or two critical units per team-shift make percentages volatile. It must be shown as counts ("2 of 3"), with "n/a" when there are none.
- **The coverage gap may simply duplicate the existing score.** If critical units are rarely missed, coverage adds little beyond attainment and OOS. That is an empirical question for the shadow study (`06`). If it turns out to be true, A alone is the better system.
