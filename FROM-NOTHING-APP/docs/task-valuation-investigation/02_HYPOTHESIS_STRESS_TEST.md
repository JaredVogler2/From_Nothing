# 02 — Trying to falsify "the most points go to the highest-impact task, which is scheduled earliest because it has no slack"

**Source revision:** `5d1236c`.

**Evidence:**
- The arithmetic of every counterexample is computed by `evidence/counterexamples.py`. It uses the repository's own urgency rule (`urgency.COD_DECAY`, `COD_STEP_MINUTES`) and the LINEAR_DAILY penalty.
- Money in the counterexamples is **ILLUSTRATIVE**: aircraft X is priced at $300,000/day and Y at $150,000/day, inside the synthetic fleet's $138k–$380k/day range. It is a planning proxy, never a contract or a realized loss.
- Measured results come from the synthetic study runs in `06` (EXPERIMENT).

## 1. Definitions

| Term | Definition used here | Where FOCUS has it |
|---|---|---|
| Feasibility | A schedule meeting every hard constraint: typed precedence and lags, calendars and shift windows, qualified full crews, part and release floors, QA holds, defended commitments. | `greedy.Decoder`, `validation/plan.py` (VERIFIED) |
| Urgency *u_i* | The scheduler's dispatch quantity: *w_a* × decay(slack_i). *w_a* is the aircraft's marginal $/day; slack_i = max(0, (due − CPM tail_i) − seed start_i). | `urgency.cost_of_delay` (VERIFIED) |
| CPM float *TF_i* | Latest minus earliest start in the precedence-only network, against the aircraft completion or the due date (stated each time). | Partial: the tail exists, float does not (NOT_IMPLEMENTED) |
| Resource-constrained float *RCF_i* | The largest delay of *i* in a fixed feasible schedule (precedence plus resource-sequence edges) that moves no aircraft completion. | NOT_IMPLEMENTED |
| Marginal impact *MI_i(Δ, P)* | *J*(plan with *i* deferred by Δ and re-planned under protocol *P*) − *J*(plan). *J* is the lexicographic objective vector; its money component is controllable modeled cost. | NOT_IMPLEMENTED (prototype in `06`) |
| Effort *e_i* | Standard person-minutes: elapsed × crew, frozen at publication. | `Phase.standard_person_minutes`, `EarningUnit` (VERIFIED) |
| Sequence position | The rank of a unit by planned start within its team-shift. | `ranked_actions` uses planned start after readiness and commitment (VERIFIED) |
| Operational importance | Not one quantity. Here: marginal impact on the objective, plus safety, quality and enabling effects that the objective does not price. | — |
| Quality | First-pass acceptance: no rework generated, no reversal. | Rework and void events (VERIFIED feeds, live producers proposed) |
| Accountability | What a team controls: executing its frozen, feasible obligation, adjusted only for independently approved relief. | `score.py` with `disruption.py` (VERIFIED, DEMO weights) |

## 2. Claim-by-claim verdicts

| # | Premise | Verdict | Basis |
|---|---|---|---|
| C1 | The earliest scheduled start identifies the most important task. | **False** | Counterexamples 4, 5 and 14. EXPERIMENT (`06`): at 20 aircraft, later units outrank earlier ones by job size in 96% of team-shifts. The later unit was waiting for a crew in about 70% of cases and for a predecessor in about 30%. Start order is mostly resource timing. |
| C2 | Zero CPM float implies high economic or operational value. | **False in general**; conditional on exposure | Counterexamples 2, 11 and 13. The value depends on whether the aircraft is late, early or unknown, and on the deferral horizon. |
| C3 | Resource-constrained float differs from CPM float in this implementation. | **True**, and FOCUS has no RCF measure | Counterexample 3. EXPERIMENT: one final-QA unit with 2.2 days of precedence float pushed another aircraft 40 hours (+$421k) through a 4-person QA team. Precedence-only methods priced it at $0. |
| C4 | Modeled task urgency equals the task's marginal effect on fleet outcomes. | **False** (weakly correlated) | EXPERIMENT: rank correlation of `cost_of_delay` with the exact one-shift counterfactual was 0.47–0.58 across two samples. It was positive for 99% of units, against 39% nonzero in the exact counterfactual (`06`). |
| C5 | If the optimizer already dispatches tasks, points add decision support. | **Conditional** | They add information only when the published sequence can no longer be followed (disruption, overrun, shortage). They must then reflect impact *given the new state*. A point frozen at publication cannot. Otherwise they repeat the plan. |
| C6 | A team should be graded higher for being assigned more important tasks. | **False for accountability** | The assignment is the optimizer's choice, not the team's (counterexample 9, and `05`). It is useful for diagnosis, not for grading. |

## 3. Counterexamples

Each lists a tiny graph, constraints, the baseline feasible schedule and an alternative. It then ranks the tasks under each measure and states which inference is and is not justified. Hours are elapsed. A one-shift deferral is Δ = 8 h. Values come from `evidence/counterexamples.py`.

### CE1 — Short zero-float release versus long slack-rich job
- **Graph.**
  - Aircraft X is due at 30 h.
  - A (2 h, 1 person) is followed by C (30 h).
  - L (20 h, 2 persons) runs in parallel.
- **Baseline.**
  - Completion is 32 h, 2 h late, a penalty of $25,000.
  - TF_A = 0 and TF_L = 12 h.
- **Alternative.** Defer A by one shift: completion 40 h, so *MI_A* = **$100,000**. Defer L by one shift: L ends at 28 h, so *MI_L* = **$0**.
- **Rankings:**

  | Measure | A | L | Higher |
  |---|---|---|---|
  | Effort | 120 pm | 2,400 pm | L, 20× |
  | FOCUS urgency | $300,000 (decay 1.0) | $233,640 (decay 0.7788) | A, by 1.3× |
  | Marginal impact | $100,000 | $0 | A |

- **Justified:** effort points mis-rank operational importance here. Urgency ranks correctly but compresses a $100k-versus-$0 gap into 1.3×.
- **Not justified:** that A should earn 20× L's points. A's impact is shared with C (CE7), and L's work content still has to be done.

### CE2 — Two zero-float jobs on different aircraft
- **Graph.**
  - X is due at 30 h: P (4 h) is followed by Q (26 h), completing at 30 h, exactly on time.
  - Y is due at 50 h: M (4 h) is followed by N (26 h), completing at 30 h, 20 h early.
- **Floats.** Both P and M have zero float against their aircraft completion.
- **Alternative.**
  - Defer P one shift: X finishes 8 h late, so *MI_P* = **$100,000**.
  - Defer M one shift: Y is still 12 h early, so *MI_M* = **$0**.
- **FOCUS urgency.** *w_X* = $300,000, so *u_P* = $300,000. *w_Y* = $25,000, because one more day would take Y 4 h late. Slack_M = 20 h gives decay 0.4724, so *u_M* = **$11,809**.
- **Justified:** zero float is not value. Exposure decides. Urgency keeps a nonzero value for M even though a one-shift deferral costs nothing.

### CE3 — Scarce inspector: CPM float > 0, resource-constrained float = 0
- **Graph.** One inspector is shared by both aircraft.
  - X is due at 20 h: W1 (8) → I1 (2) → W2 (8).
  - Y is due at 22 h: V1 (8) → I2 (2) → V2 (12).
- **Baseline.**
  - Without the resource limit, X completes at 18 h, so the CPM float of I1 is 2 h.
  - The feasible plan inspects I2 at 8–10 h (Y is critical), then I1 at 10–12 h. X completes at exactly 20 h, so the RCF of I1 is **0**.
- **Alternative.**
  - Defer I1 one shift: X completes at 28 h, so *MI_I1* = **$100,000**.
  - Defer I2 one shift: Y completes at 30 h, so *MI_I2* = **$50,000**.
- **FOCUS urgency.** Slack_I1 = 2 h falls in step 0 (6-hour steps), so decay is 1.0. Urgency happens to rank I1 high here, because its 6-hour step is coarse. The pure-work tail cannot see the inspector conflict in general.
- **Measured analogue.** A final-QA unit with 2.2 days of precedence float moved another aircraft by 40 h (+$421k). The precedence and person-edge proxies gave $0 (`06`).
- **Justified:** a valuation that ignores resource sequencing misses real criticality.

### CE4 — High cost of delay, scheduled late because it is not ready
- **Graph.** The shift runs 0–8 h. The part for critical job H (3 h, on late aircraft X) arrives at 5 h. F (4 h) has 72 h of float.
- **Baseline.** F runs 0–4 h, then H runs 5–8 h.
- **Alternative.**
  - Defer H one shift: *MI_H* = **$100,000**.
  - Defer F one shift: *MI_F* = **$0**.
- **Start order says F first.** Importance says H.
- **Justified:** "earliest start = most important" fails whenever a floor (part, predecessor, QA) holds the important job.

### CE5 — Early only to use idle capacity
- **Graph.** Crew K waits for predecessor Q2, done at 2 h, before critical job Z (2–8 h). The decoder places a low-value job G (2 h, large float) at 0–2 h.
- **Mechanism (VERIFIED).** The decoder places each popped phase at its earliest feasible opening (`greedy.py:205-270`), so lower-key phases fill earlier gaps.
- **Measured.** The crew-wait share of start-order inversions is 68–71% (`06`).
- **Justified:** early placement can be pure backfill.

### CE6 — A predecessor unlocking many jobs whose path is blocked elsewhere
- **Graph.** P (2 h) precedes D1 to D10 (4 h each). All the D jobs also need part M, arriving at 72 h.
- **Counts.** Ten downstream jobs depend on P.
- **Alternative.** Defer P one shift: the D jobs are still waiting for M, so *MI_P* = **$0**.
- **Not justified:** valuing P by its unlock count. Unlocking matters only if the downstream work is otherwise feasible. FOCUS's `dependency_unlocked` recognition (`progression.py:139-177`) correctly requires the completion to be the **last** prerequisite, which here it is not.

### CE7 — Two tasks on one critical chain each claim the same delay
- **Graph.** On aircraft X, due at 20 h: A (4 h, 0–4) → B (4 h, 4–8) → C (16 h, 8–24). Baseline: 4 h late, $50,000.
- **Alternative.** Defer A alone: **$100,000**. Defer B alone: **$100,000**. Defer both, so both move to the next shift: also **$100,000**, not $200,000.
- **Justified:** summed single-task impacts double count. Measured in `06`: for 4–10 critical units on one aircraft, the joint impact equalled the **largest single** value exactly, so summing overstated it 2.2–6.2×.

### CE8 — AND prerequisites (complementarity)
- **Graph.** Critical job Z needs both A (team 1) and B (team 2), each planned at 0–4 h.
- **Alternative.** If either misses its shift, Z slips one shift ($100,000). *MI_A* = *MI_B* = $100,000, and the joint impact is also $100,000.
- **Attribution.**
  - Shapley gives $50,000 each.
  - The realized "last finisher" rule (FOCUS `_unlocks`) gives everything to whichever finishes last, which is arbitrary between equally necessary tasks.
- **Measured.** In `06`, some mixed sets had joint impact **above** the sum of singles, from $5.8k summed to $40.4k joint and from $291.5k to $814.6k. So summing can also understate.

### CE9 — A team with low-impact but essential, high-volume work
- **Setup.**
  - Team R has 40 routine jobs (30 min × 2 persons each), all with three or more days of float. Today's marginal impact of each is $0.
  - Team S has two critical jobs.
- **Under impact points.** R's whole shift is worth about 0.
- **Consequence.** R's work becomes critical in three days if skipped, the bow wave.
- **Justified:** impact-weighted grading punishes teams assigned necessary, non-urgent work. Effort attainment does not.

### CE10 — Work crossing shifts, unverified partial progress, corrections
- **Setup.**
  - W (12 h) is cut into 4-hour segments over two shifts.
  - Segment 0 is done in shift 1, but verification arrives after the credit deadline.
  - VERIFIED: it becomes off-plan `LATE_FOR_GOAL` in a later shift (`lifecycle.py:_offplan`).
  - A later VOID reverses it. VERIFIED: a revision, with recognition corrections appended (A62).
- **Under phase-level impact.** Segment 0 alone has impact ≈ 0, because only the phase completion moves the aircraft. Teams would avoid starting long jobs near the end of a shift.
- **Justified:** impact must be split over segments, as effort already is (`serializer._split`), or partial work is unrewarded. Any earned value must follow the existing reversal path.

### CE11 — Unknown or capped economics
- **Setup.** Aircraft U has no economics term.
- **VERIFIED behaviour.** It has no weight, so no cost-of-delay key; it is excluded from money; and it is counted in `unknown_completion_aircraft`.
- **Under impact points.** Its tasks are undefined. Treating them as 0 tells teams U's work is worthless.
- **If a cap were applied** (none is today, `repository.py:188-191`): past the cap, one more day costs $0 (computed), although carrying cost and customer impact continue.
- **Justified:** unknown must stay unknown, never zero. Capped is not costless.

### CE12 — A deferral that another action fully compensates
- **Setup.** K slips one shift. Re-planning moves a substitute crew onto K's successor path. Delivery is unchanged, so money is $0, but overtime rises by 120 minutes (objective level 5).
- **Justified:** *J* is a vector. "$0" is not "no impact". A scalar money value hides the lower levels and commitment effects (`04`).

### CE13 — An already-late aircraft
- **Setup.**
  - X was due 10 days before the cutoff and is projected 12 days late.
  - Computed: total $3.6M, of which $3.0M is unavoidable and $600k controllable.
  - The marginal day on X's critical chain is **$300,000**, the same as for an on-time aircraft finishing past due.
- **Justified:** importance is the marginal penalty, not historical lateness. FOCUS's controllable split (`evaluator.py:89-91`) is consistent with this.
- **Note.** The **gate recognition bonus** (`points.py:57-70`) pays more for work at stations behind their gate, which rewards recovery rather than marginal value. Fine as motivation; wrong as a value measure.

### CE14 — A commitment leads dispatch despite lower urgency
- **Setup.** Job C was committed by the previous plan and is defended. It is dispatched first (`LEAD_DEFENDED`), ahead of uncommitted Z with a higher cost of delay on the same crew.
- **Effect.** Start order puts C first. Marginal impact may favour Z. The objective deliberately ranks commitments above money (`OBJECTIVE_FIELDS`).
- **Justified:** sequence encodes several objective levels (stability, then money, then minutes). No single value reproduces the order.

## 4. When the original claim would hold

All of these are needed together (THEORETICAL PROPERTY: the WSPT, Smith's rule, and ATC conditions):
- one shared resource, independent jobs and no release times;
- linear, known, uncapped delay costs that add across jobs (no shared critical chains);
- an order that is a priority order (no backfill, no commitment classes);
- points per unit time (cost of delay ÷ duration), not per job.

In FOCUS none of these hold at fleet scale. The scheduler is a multi-resource, precedence-constrained list scheduler with commitment classes. The measured start order is dominated by crew timing (`06`).

## 5. What survives

- Delivery-critical work does exist and can be identified: about 39% of sampled units moved fleet cost in the exact counterfactual. The cheap precedence and schedule-graph proxies separated $0 from nonzero units about 87–89% of the time (`06`).
- That supports **flagging critical work** (a priority band with a reason).
- It does not support **paying points in proportion to modeled marginal impact**: values are non-additive, often $0 for necessary work, and partly a product of the assignment.
