# 05 — Shift performance, score incentives and operational fairness

**Source revision:** `5d1236c`.

**Evidence:**
- Scores below are computed with the repository's own `performance.score.formula` and the demonstration policy (`grading-demo-v1.json`: weights 0.70 and 0.30, OOS 0.10, bands A90 / B80 / C70 / D60). Script output is recorded in `EVIDENCE_INDEX.md`.
- Ledgers are synthetic.

## 1. Core answer

The number that orders dispatch should **not** be the number a team earns.
- A dispatch value describes *what the plan needs*.
- An earned value must describe *what the team did with what it controlled*.

The optimizer chooses which teams get critical work (`greedy.py:194-202`, `make_priority`). So an earned currency weighted by task importance partly measures the assignment (`04`, F2: identical execution, 69% different earned total). Importance remains valuable for **dispatch** (what to do first, and why) and for **diagnosis** (did critical work get done?). It does not belong in the **accountability** denominator.

## 2. Audit of the existing score

All components are verified in `performance/score.py:95-150`:

| Component | Meaning |
|---|---|
| B0 | Frozen obligation (publication) |
| B | B0 + approved feasible emergency additions (`amendments.py`) |
| E | Approved relief on still-missed units |
| D | B − E |
| Cp | **Verified** earned planned effort. Credit comes only from verified lifecycle completion; a mechanic's report is a claim, never a check-off. |
| U | Verified completions in no goal of record, or late |
| O | 0.10 × affected effort of evidenced discretionary out-of-sequence work |

*A* = min(1, Cp/D); *H* = Cp/(Cp+U); base = 100(0.7*A* + 0.3*H*); final = clamp(base − 100·O/D).

**Can the combination hide a large execution shortfall? Yes, in three measured ways** (computed with `formula`):

1. **Adherence pays 30 points for doing nothing off-plan.** With U = 0, *H* = 1 for any Cp > 0.
   - The attainment needed for each band falls to A 85.7%, B 71.4%, C 57.1% and D 42.9%.
   - 60% attainment scores **72.0 (C)**; 72.7% scores **80.9 (B)**.
   - *H* only reaches 0 when nothing at all is done.
2. **Relief shrinks the denominator.** 1,800 pm planned, 1,400 relieved and 400 done scores **100.0 (A)**. That is correct if the relief is truly independent, which FOCUS enforces (the decision command requires the server-granted `excusal_decider` role, `web/api.py:393`; relief is the union of effectively decided units, `disruption.py:297-313`), but the grade alone cannot show it.
3. **Helpful off-plan work is punished.** 600 of 1,000 planned plus 400 off-plan (for example, unblocking another area) scores **60.0 (D)**. The same planned work with no off-plan work scores 72.0 (C).

**Recommendation.** Never show the grade without *A*, *H*, E/B and Cp in counts and minutes. Those are already on the Shift Performance and Performance Over Time views. Treat the 0.3·*H* term as an open policy question (OD-16), not a fact.

## 2a. The owner's proposal, worked: one scheduler-derived priority score as the grade

**Proposal (owner, 2026-11-02).** The scheduler determines a global priority score for every task, and the same score grades shifts, teams, superintendents and the business unit. Example: 20,000 points are scheduled across 300 tasks; the team completes 400 tasks worth only 16,000 points, so performance is 80%, not up to par.

**What the existing score already does with this example.** Computed with `formula`, demo policy. Count the 300 scheduled tasks in standard minutes (20,000):

| Case | Attainment *A* | Adherence *H* | Demo score |
|---|---|---|---|
| Scheduled work done: 16,000 of 20,000; the extra 100+ tasks are 3,000 off-plan minutes | 0.800 | 0.842 | 81.3 (B) |
| The same with no off-plan work | 0.800 | 1.000 | 86.0 (B) |

The proposal's 80% already exists as effort attainment. The unscheduled tasks earn nothing and lower *H*. The adherence quirk in §2 lifts both cases to a B.

**What weighting adds.** It tells *which* 16,000 were done. Take an illustrative mix of the 20,000 scheduled minutes: 2,000 CRITICAL (×3), 4,000 AT_RISK (×2) and 14,000 FLOAT (×1):

| The same 16,000 minutes done | Weighted attainment |
|---|---|
| All critical skipped, all at-risk done | 0.714 |
| All critical, half of at-risk | 0.786 |
| All critical and at-risk | 0.857 |

Unweighted attainment is 0.800 in every row. Weighting moves the grade ±7–9 points around effort attainment, depending on whether the important work got done.

**Assessment.**
- **The intent is right.** Reward doing what the plan needed, not volume, and make "50 easy jobs instead of the 10 that matter" visible.
- **The mechanism is the problem**, as shown in `03`, `04` and §1:
  - the scheduler's priority is a sort key, not a value (rank agreement 0.47–0.58);
  - impact values are non-additive (2.2–6.2× over-claims), and near-term work is almost all FLOAT on the synthetic fleets (`06` §2.6), so any weights would be arbitrary multipliers on effort;
  - the grade would partly measure the assignment (`04` F2: a 69% gap for identical execution);
  - re-valued points reward stalling.
- **The same signal, without those failure modes.** Effort attainment (the 80%) plus a critical-coverage count ("did 0 of 6 critical tasks") reports exactly the "busy but not on the right things" case. It needs no invented exchange rate between a critical minute and a slack minute.
- **The weighted number as an option.** If leadership still wants it, show it as a secondary diagnostic beside attainment, never as the grade.
- **Measured disagreement.** In the shadow simulation (`06` §2.7) the weighted number agreed with attainment almost perfectly: Spearman 0.9997 over 465 team-shifts, and only 0.43% differed by more than 0.05. No task was ever CRITICAL. On this fleet the weighted grade adds nothing.
- **Unscheduled work.** For what unscheduled work should be worth, see `09`.

## 3. Adversarial cases

| # | Case | Effort attainment (A, existing) | Importance-weighted earning (F) | G: classes + coverage | Verdict |
|---|---|---|---|---|---|
| 1 | Team A 600/600 on a light shift; Team B 1,530/1,800 on a bottleneck shift | A 100.0, B 89.5. Output shows B did 2.55× the work. | B's total depends on the critical share of its work | B covered 9 of 10 critical, A 2 of 2 | No grade captures difficulty. Show output and attainment side by side; do not rank on one number. |
| 2 | Small team, high-importance work; large team, routine essential work | Both can be 100% | The small team earns more per hour for its assignment | The class is shown; earning is equal per minute | F rewards assignment (unfair). |
| 3 | An operator does easy low-value work for points | Unplanned easy work goes to U, earns nothing and lowers *H*. Skipping planned work lowers *A*. | Cherry-picks multiplied units | Coverage exposes skipped critical units | Effort does not pay for cherry-picking unplanned work. |
| 4 | An operator delays ordinary tasks until they become urgent | Value is frozen at publication; no gain | **Delayed tasks re-publish with a higher class, so more points next shift** | The class is display-only; no gain | **Dynamic importance earning rewards procrastination.** Disqualifying for F with re-valuation. |
| 5 | Points updated during the shift | B0 frozen; additions separate (B − B0) | Moving denominators unless frozen | Class frozen at publication; the live priority display is separate | Freeze every graded number at publication. |
| 6 | Task blocked by parts or inspection outside the team's control | Relief (E) via claim and independent decision; an unknown feed is UNKNOWN, not 0 | A blocked critical task is a big loss until relieved | The relieved unit leaves the coverage denominator | All need the independent relief path. Impact makes unrelieved blocks costlier. |
| 7 | Work assigned with insufficient staff or certificates | CAPACITY_SHORTAGE / SKILL_SHORTAGE claims (automatic attribution, B9) | Same, amplified | Same | Plan feasibility is planning's accountability, not the team's. |
| 8 | Partial milestones, phase crossing, shared crews, reopened work | Per-segment units, credited once each (`lifecycle.py:768-794`). A 3-person crew earns one unit's effort (elapsed × 3), never three. A VOID reverses the credit via a revision. | Must split impact over segments or partial work earns 0 (CE10) | Same as A | Effort's existing mechanics are sound. |
| 9 | Rework from a team's own defects creates extra work | Rework SOIs add planned effort at P75. The team may earn for fixing its own defects. | **Critical-path rework becomes high-value: perverse** | Same as A for earning; the class flags it | Needs `defect_origin_team` (exists on `Job`) and a quality view. Impact makes it worse. |
| 10 | A team hides blockers or self-excuses | Claims are separated from decisions; a decision needs the independent role | Same | Same | Already guarded. |
| 11 | The team follows FOCUS exactly but the model assumptions were poor | Unaffected: it executed the plan | **Points move with model error** (rates, durations, proxies) | The class may be wrong; earning is unaffected | Impact earning makes the model's errors the team's problem. |
| 12 | A schedule amendment for a genuine emergency | Approved additions go into B; B0 and B are reported separately | Needs a class for added units | The added unit is classed at amendment time and labeled "added" | Existing mechanics are sound; report original and adjusted. |
| 13 | Cross-aircraft critical path, unbalanced assignment | Fair (per obligation) | Concentrates points on a few teams | Shows where critical work sits | F rewards placement; G informs it. |

## 4. Policies

| Policy | Existing state | Recommendation |
|---|---|---|
| Freeze the published obligation, snapshot and valuation version | B0 and gate contexts frozen and hashed (`FrozenGoal`) | Add the priority class (G) to the frozen goal, with its proxy version and plan id. Never re-grade with a later valuation. |
| Approved additions and relief, original versus adjusted | B0, B, E and D kept separate | Keep. Show B0 → B → D as a bridge. |
| Verified, reversible earning; no duplicate credit for a crew | Unit-level credit, once; revisions on VOID | Keep. Any new metric reads the same unit facts. |
| Unknown feeds are unknown, not poor performance | UNKNOWN status; coverage reasons (`score.py:183-201`) | Also make the class UNKNOWN for unpriced aircraft. Coverage of an unknown class is "n/a". |
| Importance versus individual controllability | Personal grades disabled (`score.personal_grades_enabled` = false, OD-16) | Keep disabled. Team-level only. |
| Missing high-importance work never silently earns a bonus | No bonus exists today | Under G, a missed critical unit shows in coverage. It never raises any number. |
| "Critical completed / critical scheduled" versus a weighted grade | — | Prefer the coverage count (less misleading; no weights). |
| Do not present predicted dollars as realized savings | Economics view labels assumed terms | Any modeled $ shown with a team context must say "modeled at publication, not realized". |

## 5. Two competing dashboard layouts

**Layout 1: multi-metric team-shift card (recommended).**

```
T11 · S1 2026-11-03                                   [DEMONSTRATION policy]
Obligation   B0 1,440 pm → +0 added → −120 relieved (LATE_PART) = D 1,320 pm
Execution    Done 960 of 1,320 pm (72.7%) · 4 of 5 units
Off-plan     0 pm · adherence 1.00
Sequence     OOS charge 0
Critical     1 of 1 done (1 relieved) · At risk 1 of 1 · Float 1 of 2 · Unknown 1
Quality      0 reversals · rework generated: n/a (feed pending)
Context      Assigned: 2 critical units, 2.1% of fleet modeled exposure (modeled at publication, not realized)
Grade        80.9 (B) = 70% attainment + 30% adherence − OOS        ← components always shown
```

- Formulas: as in section 2, plus the critical coverage from `04` §1.
- Strength: no information is collapsed, and difficulty and assignment are visible as context.
- Weakness: more to read, and leadership may want one number.

**Layout 2: single grade with mandatory caveats.**

```
T11 · S1    Grade 80.9 (B)   [DEMONSTRATION]
⚠ 8% of obligation relieved · critical work: 1 of 1 · no off-plan work counted in 30 of the 80.9 points
⚠ Not comparable across teams without the obligation size and critical share (open details)
```

- Strength: compact.
- Weakness: the caveats get ignored. A grade will be used for ranking whatever the caveat says (the literature on rank feedback, `06` §5). Use it only when the components are one tap away.

## 6. Sample team-shift ledger and exact denominators

The ledger is `04` §1 (G1). Denominators:
- **B0**: Σ*e_u* over units whose shift is in the obligation day (`goals.obligation_days` = 1), frozen at publication.
- **B**: B0 + Σ*e* of approved feasible additions (added units are labeled AMENDMENT).
- **E**: Σ*e* of units that are MISSED at the credit deadline **and** relieved by an effective decision.
- **D** = B − E.
- **Cp**: Σ*e* of EARNED units, credited by verified completion inside the window or grace.
- **U**: Σ*e* of verified completions credited to no goal (unknown effort makes U unknown).
- **Critical coverage**: numerator = EARNED units classed CRITICAL at publication (or at amendment, for added units); denominator = units classed CRITICAL − relieved CRITICAL units; shown as counts; "n/a" when the denominator is 0.

## 7. Fairness audit

| Source of unfairness | Effort attainment | Importance-weighted earning | Mitigation |
|---|---|---|---|
| Assignment (which work the optimizer gives a team) | Neutral per minute | **Biased** | Keep importance out of earning |
| Disruption exposure | Neutral if relief is independent | Amplified for critical work | Independent relief; show E/B |
| Team size and workload size | Ratios normalize | Ratios normalize; totals do not | Compare ratios; show output separately |
| Model error (rates, durations, proxies) | Durations only (estimates) | Durations and rates and proxy | Keep the model out of earning |
| Data coverage (unknown states) | UNKNOWN status | Undefined values | UNKNOWN is never zero |
| Gaming by procrastination | None (frozen) | **Present** (case 4) | Frozen effort |
| Rework from own defects | Present (P75 effort) | Worse | Defect-origin view; quality gate |

## 8. Suitability

| Metric | Coaching | Dispatch feedback | Team comparison | Gamification | Personnel decisions |
|---|---|---|---|---|---|
| Effort attainment with components | Yes | Partly | With context (ratios, output, E/B) | Yes, opt-in and published formula | **No** |
| Critical coverage (G) | Yes | **Yes** | Only with counts and context | Possibly (team-level) | **No** |
| Importance-weighted grade (F) | Diagnostic only | Partly | **No** (assignment-biased) | Risky (procrastination, lobbying) | **No** |
| Modeled delay dollars | Management diagnosis | Management triage | **No** | **No** | **No** |

**Personnel-level scoring.** Not recommended under any model. It is already disabled by policy (`score.personal_grades_enabled` = false; OD-16 requires an approved policy). The controllability, assignment and model-error issues above would compound at the individual level. Any future individual use would need its own validation, governance, labor-relations review and consent. Gamification must stay opt-in and team-level, with a published formula (literature in `06` §5).
