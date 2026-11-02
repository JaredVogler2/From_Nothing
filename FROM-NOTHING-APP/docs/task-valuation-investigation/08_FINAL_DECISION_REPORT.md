# 08 — Final decision report: should FOCUS value tasks for priority and for earned points?

**Source revision:** `5d1236c` (2026-11-02).

**Scope:** investigation only. No application code, tests, policies, schemas, UI or scheduling were changed.

**Evidence labels:**
- **VERIFIED CODE FACT**: read in the code at this commit.
- **EXPERIMENT**: a synthetic run, reproducible from `EVIDENCE_INDEX.md`.
- **THEORETICAL PROPERTY**: follows from the definitions.
- **ASSUMPTION** / **UNVERIFIED**: stated where used.

Modeled money is the airline-revenue proxy: a planning assumption, never a contract or a realized loss.

## A. Executive decision

**Should task importance be scheduler-derived?**
- **Yes, as a classification**: CRITICAL, AT_RISK, FLOAT or UNKNOWN, with a reason.
- It should be computed from the **published** FORECAST plan's schedule graph, as resource-aware float to delivery. It should not come from the dispatch key.
- The scheduler's own `cost_of_delay` is a sort heuristic. Its rank agreement with exact one-shift counterfactuals was only 0.49–0.58, and it was positive for 99% of units where 61% had no impact (EXPERIMENT).
- A schedule-graph proxy separated zero from nonzero impact for 87–91% of units, in about a second at 5–10 aircraft (EXPERIMENT).

**Should it determine dispatch priority?**
- It should **inform** dispatch: show the class and the reason beside each action. Under disruption, the published sequence cannot be followed and leads need to know what protects delivery.
- The scheduler's ranking (`dispatch.ranked_actions`) stays the authority. Reordering by class is a later, evidence-gated decision.

**Should it determine earned points or the shift grade?**
- **No.**
- Task impact is **non-additive**: a measured double claim of 2.2–6.2× for critical units sharing an aircraft, and under-claims for complementary sets (EXPERIMENT; proofs in `04`).
- It is **endogenous**: a team's total would measure what the optimizer assigned it (`04` F2).
- It **rewards procrastination**: tasks deferred until urgent re-price higher (`05`).
- It is **empty in the near term**: every one of 378 synthetic first-shift units had $0 one-shift impact (EXPERIMENT, reproduced).
- Even fair (Shapley) shares credit value that is never realized when a partner task misses (THEORETICAL, `04` §3.4).

**Should effort and mission impact share a currency?** **No.**
- Earned points stay **standard person-minutes**: frozen, verified, credited once and additive. That is the existing B0 / Cp machinery.
- Impact is reported **beside** them, as a class plus a **critical coverage** count (critical units done ÷ critical units scheduled, minus relieved).

**Recommendation.**
- Choice **(4)**: separate effort and impact metrics, implemented as frozen priority classes plus critical coverage beside the unchanged effort grade. Start in shadow mode in the synthetic simulation and promote only through the gates in `06` §6.
- Fallback: **(1)**, effort only, the status quo, if the classes prove unstable or uninformative.
- Option (3), unified impact points, is rejected.

**Confidence.**
- **High** that additive impact earning is wrong for FOCUS.
- **Moderate** that classes add value.
- **Low** on real-world magnitudes. All evidence is synthetic, with scaled-down teams and a light parts scenario for the counterfactual comparisons.

**Biggest unknowns.**
- Whether real near-term work is ever delivery-critical. In the synthetic fleet it had at least half a day of float.
- The proxy's fidelity at 50 aircraft with real capacity constraints. It missed a QA-bottleneck case.
- Live economics (no terms or caps, OD-12).
- Live observation producers (OD-15).

## B. Current state, with evidence (summary of `01`)

| Capability | Status | Evidence |
|---|---|---|
| Canonical job, phase and segment identity | VERIFIED_AVAILABLE | `domain.py:221,257`; `contracts/planning.py:126,795` |
| Frozen team-shift obligation (B0) per earning unit; effort split over segments | VERIFIED_AVAILABLE | `serializer.py:86-145` |
| Verified, once-only crediting; reversals | VERIFIED_AVAILABLE | `lifecycle.py:768-850` |
| Independent relief | VERIFIED_AVAILABLE | `disruption.py:281-327` |
| Canonical score | DEMO weights | `score.py:95-150`; `grading-demo-v1.json` |
| Lexicographic objective; exact comparison | VERIFIED_AVAILABLE | `orchestrator.py:94-108,232-299` |
| Dispatch urgency (`cost_of_delay`) | VERIFIED_AVAILABLE, heuristic | `urgency.py:92-116` |
| Money only in FORECAST plans (EXECUTABLE completions unknown) | VERIFIED + EXPERIMENT | `evaluator.py:37-60,99-113`; 5/5, 9/10 and 20/20 aircraft unknown |
| Caps never applied | VERIFIED | `repository.py:188-191` |
| Standard effort = elapsed × crew (not engineered) | DEMO | `rebuild-v1.json:291` |
| Recognition gate bonus (a bounded multiplier up to 1.35×) | DEMO | `points.py:57-94` |
| `dependency_unlocked` (last-finisher, zero value) | VERIFIED_AVAILABLE | `progression.py:139-177` |
| Team Lead ranking = readiness, commitment, planned start | VERIFIED_AVAILABLE | `dispatch.py:178-192` |
| Resource-constrained float, task value, counterfactuals, attribution | NOT_IMPLEMENTED | `01` §3 |

## C. Alternatives (summary of `03`)

Measured columns are EXPERIMENT (two samples: R_H5 / H5). The rest is judgment.

| Model | Spearman vs exact | Additive | Controllable | Cost at 50 aircraft | Role | Verdict |
|---|---|---|---|---|---|---|
| A, effort | −0.20 / −0.23 | Yes | Yes | trivial | Earning | **Keep as the earned currency** |
| B1, CPM tail | −0.37 / −0.44 | n/a | n/a | seconds | — | Reject (anti-correlated) |
| B2, precedence float | +0.66 / +0.73 | n/a | n/a | seconds | Priority signal | Usable, but blind to resources |
| B3 / D-proxy (c), schedule-graph right shift | +0.71 / +0.83 (zero/nonzero 0.89 / 0.91) | No | n/a | seconds (to measure) | **Priority class source** | **Use, for classes** |
| C, `cost_of_delay` | +0.49 / +0.58 (zero/nonzero 0.40) | No | No | seconds | Dispatch heuristic | Keep inside the scheduler; not a value |
| D1, exact counterfactual | reference (3.3% negative anomalies) | **No** (2.2–6.2× over, 0.14× under) | No | **about 70 h per shift** | Analytic | Asynchronous spot checks only |
| E, unlock / shadow prices | not measured | E1 yes (arbitrary), E2 no | No | E3 high | Diagnostic | Reject as currency |
| F, effort × multiplier | +0.17 / +0.25 | Yes (arbitrary weights) | **No** (assignment) | seconds | Mixed | Reject |
| **G, classes + effort + coverage** | class via (c) | effort yes; classes not summed | effort yes | seconds | Separate roles | **Recommend (shadow first)** |
| H, learned from history | — | — | — | — | — | No data |

## D. Mathematics and worked cases (summary of `04`)

- **Cost float.** *CF_i* = *F_i^C* + max(0, *d_a* − *C_a*). CRITICAL when *CF_i* < Δ (one shift); AT_RISK when *CF_i* < *K*Δ; UNKNOWN when the aircraft's economics or completion are unknown.
- **Effort attainment** (unchanged): *A* = min(1, Cp/(B − E)).
- **Critical coverage**: critical units done ÷ (critical units scheduled − critical units relieved), as counts; "n/a" when 0.
- **Worked example G1:**
  - B 1,440, E 120, Cp 960, so *A* = 0.727 and the demo grade is 80.9.
  - Critical 1 of 1, at risk 1 of 1, float 1 of 2, plus 1 unknown unit.
  - Reading: the critical work was protected; the shortfall was float work.
- **F2, the same execution with a different assignment.** Unweighted *A* is 0.727 for both teams, but the weighted earned totals are 1,620 and 960, a 69% gap that measures assignment.
- **D1.** A and B on one critical chain: each alone costs $100k, together $100k, and the sum of singles is $200k. With an independent C at $50k: singles sum to $250k against $150k joint. Shapley gives 50/50/50.
- **D2.** Complementary paths: singles $0 each, joint $100k.
- **Completing A while B misses avoids $0**, yet A's Shapley share is $50k. Shares are not earnable.
- **Denominators.** Effort has one (Σ*e_u* = B0, frozen). Impact has none that is additive across tasks, team-shifts or the fleet (`04` §3.5).

## E. Adversarial critique of the recommendation (G)

| # | Failure mode | Consequence | Mitigation | Residual risk | Evidence |
|---|---|---|---|---|---|
| 1 | The proxy misses capacity-driven criticality | Critical work labeled FLOAT; coverage misses it | Include person, position and QA sequence edges; asynchronous *d1* checks on scarce-resource units | Moderate | EXPERIMENT (QA unit $0 by proxy, +$421k exact) |
| 2 | Threshold cliffs, classes flipping between publications | Confusion, distrust | Show cost-float minutes; hysteresis; T5 stability gate | Low–moderate | THEORETICAL; T5 to measure |
| 3 | Near-term emptiness (no critical units today) | Coverage "n/a" most shifts; little value | AT_RISK horizon *K*; fall back to A if T8 fails | Unknown on real data | EXPERIMENT (synthetic: 378/378 $0) |
| 4 | Coverage redundant with attainment | Complexity without information | Gate: correlation < 0.9 | Unknown | NOT_MEASURED |
| 5 | Small denominators | Volatile, misleading percentages | Counts, not percentages; "n/a" | Low | THEORETICAL |
| 6 | Leadership merges the two numbers into one weighted grade | Re-creates F (endogenous) | Governance rule: coverage never enters the grade; publish the definitions | Moderate | Literature (metric fixation, Kerr; Holmström–Milgrom) |
| 7 | Model error (rates, durations) mislabels criticality | Teams protect the wrong work | Reasons and aircraft state shown; the ranking stays the scheduler's | Moderate | Measured proxy fidelity 0.71–0.83 |
| 8 | Live state drifts after publication | Frozen class ≠ live need | Show both; the grade uses frozen only | Low | VERIFIED freeze pattern |
| 9 | Unknown economics in live use (OD-12) | Many UNKNOWN classes | Never shown as FLOAT; classes limited to known aircraft | High until terms exist | VERIFIED code behavior |
| 10 | EXECUTABLE versus FORECAST placement mismatch | Units valued in a different placement | Classify phases from the forecast graph, map by phase id, label the plan valued | Moderate | EXPERIMENT (63–77% overlap) |
| 11 | Own-defect rework flagged critical | Coverage credit for fixing self-made defects | Defect-origin view (`Job.defect_origin_team`); quality metrics separate | Moderate | VERIFIED field; THEORETICAL incentive |
| 12 | Coverage misused for team comparison | Teams with no critical work look "n/a"; unfair rankings | Diagnostic only; never ranked | Moderate | `05` §7 |
| 13 | Cost at 50 aircraft unmeasured | Could delay the pipeline | Asynchronous step, `allow_failure`, ≤ 60 s budget (T11) | Unknown until measured | NOT_MEASURED |
| 14 | Mistaking classes for causal credit | "Critical coverage saved $X" claims | Never attach modeled $ to team results; ledger D only for management, labeled modeled | Low with governance | THEORETICAL |

**The prompt's fallacies, checked:**

| Fallacy | Finding |
|---|---|
| Zero slack means most value | False: CE2, measured −TF Spearman 0.66–0.73 |
| Earliest placement means importance | False: crew waits behind 91% of units; CE4, CE5 and CE14 |
| Double counting | Measured 2.2–6.2× |
| Unknown treated as zero | Code excludes unknowns; G must show UNKNOWN |
| Unlock counts | CE6 |
| Endogenous performance | F2 |
| Moving baselines | Freeze |
| Rewarding unverified work or gaming delays | Effort credit is verified-only and frozen; impact repricing rewards delay (`05` case 4) |
| Inadequate feeds | OD-12 and OD-15 |
| Expensive, unstable counterfactuals | About 70 h per shift; 3.3% anomalies |
| Heuristic bias | *d1* is a greedy decode, not an optimum |
| Unsupported fairness claims | Synthetic only; T9 designed, not run |

**Would a simple priority band plus standard attainment beat a dynamic point ledger?** Yes. That is what G is. The ledger (F, D) fails on additivity, controllability and incentives before any practical cost is counted.

## F. Validation (summary of `06`)

- **Performed (EXPERIMENT):**
  - R_H5, R_B5 and R_INV20 at `5d1236c`, reproducing the earlier plans exactly (identical objective values; R_INV20 identical in every figure);
  - the fidelity comparison on two samples;
  - joint sets;
  - runtimes at 5, 10 and 20 aircraft.
- **Planned, not performed:**
  - T1–T11 at 50 aircraft: perturbation stability (T5), the shadow simulation (T8), fairness (T9) and cost (T11).
  - Real-data validation needs weeks of executed plans with verified events, claims, decisions and outcomes. None exist.
- **What would overturn the choice:**
  - **T8 fails** (CRITICAL slips do not move delivery more than FLOAT slips) or **T5 fails** (classes unstable): drop classes and keep A.
  - **Coverage correlates > 0.9 with attainment**: drop coverage; keep classes for display only, which is choice (2).
  - **Proxy fidelity < 0.6 at 50 aircraft**: classes are noise; keep A.
  - Nothing measured here supports additive impact earning. Reconsidering (3) would need additivity to hold in T4, which contradicts the measured results and the theory.

## G. Integration (summary of `07`)

- **MVP:** a read-only `focus/valuation/classify.py` and a `TaskValuation` record, computed in a new asynchronous `valuate` pipeline step after `forecast_sign_accept` (`allow_failure`). It is bound to the goal ids, immutable, and versioned by (executable plan, forecast plan, method, policy hash).
- **Shadow first:** `FOCUS_VALUATION_MODE=shadow`, synthetic simulation only, then `display`.
- **Touchpoints:**
  - Team Lead and Shift Book projections get class and reason fields; the ranking is unchanged.
  - Explanation fact rows.
  - `rollup.job_rows` gets a class column and `rollup.coverage`.
  - Draft SQL 011.
  - `GET /api/v1/valuation`.
- **Unchanged:** the scheduler, validator, goals, `score.py` and recognition.
- **Rollback:** flag off. Scores never read valuations, so no grade can change.
- **Budget:** ≤ 60 s and ≤ 2 GB at 50 aircraft; to be measured before Phase 1 exits.
- **Option D** (management delay-cost ledger, sum-to-total attribution, economics grant) comes later, behind its own gate.

## H. Explicit choice

1. **(4) Separate effort and impact metrics.** Effort stays the earned currency and the grade. Frozen priority classes plus critical coverage are shown beside it, shadow first. *Recommended.*
2. **(2) Scheduler-derived priority only.** Classes on the Team Lead view, without coverage. This is what (4) reduces to if coverage proves redundant.
3. **(1) Effort only (status quo).** The credible fallback if the class gates fail.
4. **(5)** No superior alternative was found. The management delay-cost ledger (D) is a companion to (4), not a replacement.
5. **(3) Unified impact points for priority and performance.** **Rejected**: non-additive, endogenous, procrastination incentive, near-term emptiness, uninterpretable at fleet scale.

## I. Smallest set of owner decisions

1. **Who sees priority classes:** teams and leads, or supervisors only. This is a cultural and UX tradeoff, not settled by evidence.
2. **The class horizon:** what counts as CRITICAL (one shift is proposed) and how far AT_RISK looks ahead (*K*). It decides how often classes are empty.
3. **What coverage is for:** diagnostic only, or also team comparison and gamification. The evidence advises against comparison.
4. **Economics visibility:** whether to build the management delay-cost ledger, who sees modeled dollars, and whether a **carrying cost per aircraft-day** (a finance figure) should enter the money objective. Today an early aircraft carries $0 until its due date is crossed.
5. **The demonstration grade** (OD-16): whether the 30% adherence term stays. With no off-plan work it lowers the attainment needed for a B to 71.4%. This affects any grade, independent of task valuation.

## J. Source and evidence index

See [EVIDENCE_INDEX.md](EVIDENCE_INDEX.md) for the reproducible runs, repository citations and external literature. The stage reports are [01](01_CODE_AUDIT.md), [02](02_HYPOTHESIS_STRESS_TEST.md), [03](03_MODEL_COMPARISON.md), [04](04_ATTRIBUTION_AND_MATH.md), [05](05_SHIFT_METRICS_AND_FAIRNESS.md), [06](06_VALIDATION_AND_SCALE.md) and [07](07_INTEGRATION_OPTIONS.md). No experiment, result, paper or authority in these reports is invented. Every NOT_MEASURED item is labeled.

---

**Decision gate.** This report does not authorize implementation. The next step needs explicit approval: Phase 1 of `07` §6 (shadow classifier in the synthetic simulation), or the fallback (no change).
