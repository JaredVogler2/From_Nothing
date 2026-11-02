# 04 — Formal mathematics, counterfactuals, non-additivity and attribution

**Source revision:** `5d1236c`.

**Evidence:**
- Illustrative money is computed by `evidence/scripts/attribution.py` and `evidence/scripts/counterexamples.py`.
- Measured non-additivity comes from the re-run study (`06`, `evidence/R_H5.analysis.txt`).
- Identifiers such as `1301 / A1-WIRE-12` are synthetic examples, not real SOIs.

## 0. Notation

| Symbol | Domain | Unit | Source |
|---|---|---|---|
| *u* | earning units: (plan, assignment, segment) | — | `EarningUnit` |
| *i* | phases: (job key, kind, ordinal) | — | `PhaseKey` |
| *a(i)* | aircraft line of phase *i* | — | `Phase.line` |
| *T(u)* | team-shift of unit *u* | — | `EarningUnit.team`, `shift_id` |
| *e_u* | standard person-minutes, frozen | pm | `freeze_goals` |
| *s(u)* | status: EARNED, MISSED, PENDING, UNKNOWN | — | `lifecycle.unit_facts` |
| *r(u)* | relief approved (MISSED only) | bool | `disruption.approved_units` |
| *C_a* | projected completion of aircraft *a* (FORECAST plan) | minute | `evaluator.aircraft_completions` |
| *d_a*, *ρ_a* | due instant (CS744 unload + 2 days, OD-06), daily rate (proxy) | minute, $/day | `repository`, `registry` |
| *π_a(t)* | modeled penalty if *a* completes at *t*: *ρ_a* · max(0, *t* − *d_a*)/1440, no cap | $ | `registry.penalty` |
| *J* | the objective vector (*j*₁ … *j*₇) | mixed | `orchestrator.OBJECTIVE_FIELDS` |
| Δ | deferral horizon, here one shift | minutes | — |

The components of *J*, ordered lexicographically:

| Component | Meaning |
|---|---|
| *j*₁ | commitments lost (the negated defended and exact counts) |
| *j*₂ | aircraft with unknown completion |
| *j*₃ | controllable known cents |
| *j*₄ | travel plus wait minutes |
| *j*₅ | overtime minutes |
| *j*₆ | continuity breaks |
| *j*₇ | deferred phases |

## 1. Model G — frozen priority classes, effort attainment and critical coverage

**Cost float.** In the published FORECAST plan, take the schedule graph *G* = (phases, precedence edges with lags ∪ person and position sequence edges). Let *F_i^C* be the largest right-shift of *i* that leaves its aircraft's completion unchanged (resource-constrained float to completion). Then

$$ CF_i = F_i^C + \max(0,\ d_{a(i)} - C_{a(i)}) $$

is the delay *i* can absorb before **any** modeled cost appears (THEORETICAL PROPERTY, LINEAR_DAILY uncapped). For a one-shift horizon, *CF_i* < Δ is exactly the zero/nonzero rule of the right-shift proxy (c), measured as agreeing with the exact counterfactual on 89–91% of units.

**Class at publication**, frozen with the goal (illustrative policy *K* = 9 shifts):

$$ \text{class}(i)=\begin{cases}\text{UNKNOWN} & a(i)\ \text{economics or completion unknown}\\ \text{CRITICAL} & CF_i < \Delta\\ \text{AT\_RISK} & \Delta \le CF_i < K\Delta\\ \text{FLOAT} & \text{otherwise}\end{cases} $$

**Two reported numbers per team-shift *T*:**
- *Effort attainment* (unchanged): *A_T* = min(1, *Cp_T* / *D_T*), where *Cp_T* = Σ_{s(u)=EARNED} *e_u* and *D_T* = *B_T* − Σ_{s(u)=MISSED, r(u)} *e_u*.
- *Critical coverage*, as counts, with "n/a" when the denominator is 0:

$$ Cov_T = \frac{|\{u\in T:\ \text{class}=\text{CRITICAL},\ s(u)=\text{EARNED}\}|}{|\{u\in T:\ \text{class}=\text{CRITICAL}\}| - |\{u:\ \text{CRITICAL},\ \text{MISSED},\ r(u)\}|} $$

A phase's class applies to all its units. The class is never summed or weighted into effort.

**Worked example G1** (team T11, shift S1, synthetic):

| Unit (line / SOI / phase / segment) | *e_u* | Class | Status | Relief |
|---|---|---|---|---|
| 1301 / A1-WIRE-12 / WORK.0 / 0 | 240 | CRITICAL | EARNED | — |
| 1301 / A1-WIRE-12 / WORK.0 / 1 | 120 | CRITICAL | MISSED | yes (LATE_PART) |
| 1302 / B7-BRKT-03 / WORK.1 / 0 | 480 | FLOAT | EARNED | — |
| 1303 / C2-SEAL-44 / WORK.0 / 0 | 180 | AT_RISK | EARNED | — |
| 1304 / D9-PANEL-08 / WORK.2 / 0 | 360 | FLOAT | MISSED | no |
| 1306 / B8-CLIP-19 / WORK.0 / 0 | 60 | UNKNOWN (no economics) | EARNED | — |

- B = 1,440; E = 120; D = 1,320; Cp = 960; *A* = 0.727; U = 0, so *H* = 1.
- Demo base: 100(0.7 × 0.727 + 0.3 × 1) = **80.9**.
- Critical coverage: **1 of 1** (one critical unit relieved). At risk: 1 of 1. Float: 1 of 2. Unknown: 1 unit, shown separately.
- Reading: the team protected the critical work. Its shortfall is float work (D9-PANEL-08), which coaching can address without implying a delivery hit.

## 2. Model F — effort × bounded multiplier (formalized to show where it breaks)

$$ P_u = e_u \cdot m(\text{class}(u)),\quad m:\{\text{CRITICAL},\text{AT\_RISK},\text{FLOAT},\text{UNKNOWN}\}\to\{3,2,1,1\} $$

$$ A_T^w = \min\!\left(1,\ \frac{\sum_{s(u)=\text{EARNED}} P_u}{\sum_u P_u - \sum_{s(u)=\text{MISSED}, r(u)} P_u}\right) $$

**Worked example F1** (the same ledger):
- *B^w* = 720 + 360 + 480 + 360 + 360 + 60 = 2,340.
- Relief^w = 360, so *D^w* = 1,980.
- *Cp^w* = 720 + 480 + 360 + 60 = 1,620.
- *A^w* = **0.818**, against 0.727 unweighted.

**Worked example F2** (identical execution, different assignment). Team T12 executes exactly like T11, but the optimizer assigned it no critical work: the same efforts, all FLOAT, and the same missed unit, unrelieved.

- Unweighted *A* = 960 / 1,320 = 0.727 for both teams.
- T11's weighted XP-style total **earned** is 1,620. T12's is 960.
- **The 69% gap measures the assignment, not the execution.** This is the circularity of `05`.

**Where F breaks:**
- The weights *m* are arbitrary, with no measured basis.
- Totals scale with the optimizer's labels (endogeneity).
- The ranking stays dominated by effort: measured Spearman with exact impact was only +0.17 / +0.25.
- A single number conflates execution with assignment.

**Variant F′ (normalized to the shift's effort).** Rescale so that Σ*P_u* = *B_T*, then *P_u* = *e_u* · *m_u* · *B_T* / Σ*e_v m_v*. Cross-team totals are fair again, because they equal effort, but within the shift a missed critical unit now costs more grade than a missed float unit. That is a priority-execution grade. Its weights are still arbitrary, and the class still comes from a noisy proxy. G reports the same information without changing B0.

## 3. Model D — counterfactual marginal impact (an analytic, not a currency)

### 3.1 Protocol
- **Reference.**
  - The accepted FORECAST plan *S* (money exists only there: executable plans have unknown completions, `01` trap 4), with its snapshot id, policy hash, `ALGORITHM` / `VERSION` (`orchestrator.py:88-89`) and native-core protocol.
  - Units are mapped from the executable obligation by phase id. Measured overlap: 63% (5 aircraft) and 77% (10 aircraft) of FORECAST first-shift units are also EXECUTABLE first-shift units, so a valuation must name which plan's placement it values.
- **Held fixed:**
  - the snapshot (requirements, releases, calendars, people and attendance, economics assumptions);
  - the policy;
  - in-progress locks (carry-in);
  - defended commitments;
  - priority keys (D1), or incumbent hints (D2);
  - solve budget and seed.
- **Defer.** A phase-level earliest-start floor at the start of the phase's next shift on its line and team. A started phase cannot be deferred. Alternatives (whole-job deferral, removal for the horizon) answer different questions and must be labeled.
- **Freed crews.** The decoder re-plans them (D1, D2). The right-shift proxy does not re-plan, so it ignores mitigation and anomalies alike.
- **Output.** The vector Δ*J* = *J*(*S*_defer *i*) − *J*(*S*), plus its **first changed level**. It is reported as:

  | First changed level | Reported as |
  |---|---|
  | *j*₁ | COMMITMENT_IMPACT, with the count |
  | *j*₂ | VISIBILITY_IMPACT: an aircraft becomes unknown |
  | *j*₃ | MONEY *x* $ (only when *j*₁ and *j*₂ are unchanged) |
  | *j*₄ … *j*₇ | MINUTES or BACKLOG, with the deltas |

  Converting levels into dollars would invent exchange rates the policy deliberately does not have (`orchestrator.better`, `:278-299`).
- **What it is.** An *optimized-candidate change under a heuristic*. It is neither a causal effect nor an optimum delta.
  - Measured: 3.3% of sampled units had negative Δ*j*₃ (down to −$472,639): a list-scheduling anomaly.
  - Values must be reported unclipped, with a range across variants (D1 and D2, and where affordable more than one seed). They must never be presented as guaranteed.
- **Multiple aircraft.** Δ*j*₃ decomposes exactly by aircraft (`LineCost` per line). Measured: 10 of 121 units moved **another** aircraft's cost.

### 3.2 The mandatory shared-impact example (worked example D1)
- **Setup.**
  - Aircraft 1301 is late. On its critical chain: A = `1301 / A1-WIRE-12 / WORK.0` (team T11) → B = `1301 / A1-HARN-14 / WORK.0` (team T12) → the rest of the chain.
  - Independently, C = `1302 / B7-BRKT-03 / WORK.1` (team T11) costs $50,000 if deferred one shift.
  - At $300k/day, one shift of 1301 delay is $100,000.
- **Cost of missing a set *S*:** *v*(*S*) = Δ*j*₃(defer *S*):

  | *S* | {A} | {B} | {C} | {A,B} | {A,B,C} |
  |---|---|---|---|---|---|
  | *v*(*S*) | 100,000 | 100,000 | 50,000 | 100,000 | 150,000 |

- **Sum of singles** = $250,000, against a joint $150,000. **Double counting: $100,000.**
- **Proof sketch (THEORETICAL PROPERTY).** With A and B on one chain, deferring both one shift delays the chain end by Δ, not 2Δ, because the chain's *makespan* is a max-plus function of start times. The penalty is linear in completion, so *v* is submodular on such sets: *v*({A,B}) = max(*v*({A}), *v*({B})) < *v*({A}) + *v*({B}). Summing singles therefore over-claims whenever two or more deferred tasks share a critical path to the same completion.
- **Measured:** for 4, 5, 7 and 10 critical units on one aircraft, the joint value equalled the largest single value exactly. Summing overstated by 2.2×, 3.9×, 5.1× and 6.2×.

### 3.3 Complementarity (worked example D2)
- **Setup.** Z needs P (team T11) and Q (team T14). Each alone has one shift of float, but missing both slips Z one shift.
- *v*({P}) = *v*({Q}) = $0, while *v*({P,Q}) = $100,000. *v* is **supermodular** here.
- **Sum of singles = $0**, which **under**-claims.
- **Measured:** mixed sets with joint above the sum, e.g. $5,844 summed against $40,375 joint, and $291,514 against $814,646.
- **Conclusion.** Neither direction can be assumed. Context (which other tasks also miss) determines each task's marginal value.

### 3.4 Attribution rules (all sum to *v*(*N*) except the first)

| Rule | D1 allocation (A, B, C) | D2 allocation (P, Q) | Cost | Problem |
|---|---|---|---|---|
| Singles (each task's own *v*) | 100k, 100k, 50k (sum 250k) | 0, 0 | *n* counterfactuals | Not additive: double counts or misses |
| Sequential, in planned order | A,B,C: 100k, 0, 50k; B,A,C: 0, 100k, 50k | P,Q: 0, 100k | *n* | Order-dependent; non-unique |
| Proportional to singles | 60k, 60k, 30k | equal split, 50k each (0/0 fallback) | *n*+1 | Penalizes the independent C ($30k < $50k) |
| **Shapley** | **50k, 50k, 50k** | **50k, 50k** | 2ⁿ exact; *m*·*n* sampled | The only rule satisfying efficiency, symmetry, null player and additivity. Still, see below. |
| Last finisher (`progression._unlocks`) | whoever completes last | whoever completes last | cheap | Arbitrary between equally necessary tasks |

- **Shapley is not an earning currency either.** For D1, consider completing A while B still misses:
  - the avoided cost is *v*(*N*) − *v*(*N* ∖ {A}) = **$0**;
  - but A's Shapley share is **$50,000**.
- In general Σ_{i∈S} φ_i ≠ *v*(*N*) − *v*(*N* ∖ *S*) unless *v* is additive. So paying earned points equal to impact shares credits value that was never realized.
- **Computational burden.**
  - With the exact decode at 50 aircraft (about 85 s each), exact Shapley over even *n* = 20 at-risk units needs 2²⁰ decodes: impossible.
  - Sampled Shapley with *m* = 100 permutations needs about 100·*n* decodes: still days.
  - Only with the right-shift proxy, where one joint evaluation is a graph pass, is sampled Shapley plausible. Even then it is only as good as the proxy (Spearman 0.71–0.83).

### 3.5 Must Σ task points = shift scheduled points?
- **Effort (A, G):** yes, by construction. Σ_u *e_u* = B0, frozen and hashed. This gives a consistent denominator (B0, B, D) for every team-shift. It is what makes pooling, rollups and job records add up (D-25).
- **Impact:** only by **definition**, never by **fact**.
  - One can define the shift's stake *v*(*N_T*) (defer the whole team-shift: one counterfactual per team-shift) and allocate it by Shapley. The shares sum to the stake.
  - But earned impact Σ_{done} φ_i does not equal the avoided cost of the completed subset (above).
  - Stakes of different team-shifts do not add up to the fleet's stake: *v*(*N_T1* ∪ *N_T2*) ≠ *v*(*N_T1*) + *v*(*N_T2*) for the same reasons.
  - So there is **no consistent additive impact denominator** across team-shifts.
- **No double claims across grains.**
  - Earn only at the unit (plan, assignment, segment).
  - A phase-level value must be split over its units, e.g. by *e_u*, before anything is earned.
  - Never add phase, SOI or aircraft aggregates to unit totals.
  - A deferred task re-enters the next publication with a new valuation; it never carries the old one.

### 3.6 Is an additive impact point system mathematically defensible?
**Only if *v* is additive (modular)** on every set of tasks that can miss together, i.e. all of:
- no two tasks share a path to the same aircraft completion;
- no AND-gates;
- no shared resources whose sequencing couples tasks;
- an uncapped linear penalty;
- known economics for every aircraft.

None of these hold in FOCUS (measured: same-aircraft sets are exactly non-additive, and mixed sets go both ways). **Conclusion: additive impact points are not defensible here.** Additive **effort** points with a separate, non-additive impact **classification** are.
