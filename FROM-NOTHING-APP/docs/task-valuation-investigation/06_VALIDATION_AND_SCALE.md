# 06 — Falsifiable validation, scaling and efficient approximation

**Source revision:** `5d1236c`.

**What this report contains:**
- **EXPERIMENT:** only runs whose scripts and outputs are listed in `EVIDENCE_INDEX.md`.
- **NOT_MEASURED:** marked as such.
- No application code, tests or policies were changed. The study scripts inject a deferral floor by patching `greedy._floors` inside their own process only.

## 1. Measured baseline facts

### 1.1 Repository measurements (from repository docs, not re-run here)

| Fact | Value | Source |
|---|---|---|
| Full-fleet decode at 50 aircraft | about 85 s, 286,024 phases placed | `docs/execution/PRODUCTION_SCALE.md:102` |
| Independent validation at 50 aircraft | about 45 s | `PRODUCTION_SCALE.md:104` |
| Admission + plan at 50 aircraft | 9.6 min | `PRODUCTION_SCALE.md:80` |
| Peak memory of the heaviest job at 50 aircraft | 7.5 GB | `PRODUCTION_SCALE.md:161` |
| Late-shift plans at 50 aircraft (replayed shift 40) | FORECAST 371.5 s, EXECUTABLE 222.9 s | `docs/execution/SIMULATION.md` (cost table) |

### 1.2 Study runs (EXPERIMENT, synthetic)

Fleet: `FleetSpec` with team sizes scaled down. Plans: seed + cost-of-delay redispatch, no search moves. Runs re-done at `5d1236c` are marked **R_**.

| Run | Command (scripts in `evidence/scripts/`) | Scale | Key outputs |
|---|---|---|---|
| H5 (earlier) and **R_H5** (re-run) | `study.py 5 5,8 R_H5 --units horizon --d1 all --d2 14 --joint 15 --late-share 0.02 --late-max 3` | 5 aircraft: 29,698 jobs, 46,030 phases | **Identical plan** (controllable $4,846,815.97 in both). R_H5: 121-unit stratified sample, *d1* for all, repair-mode *d2* for 14, 24 joint sets. |
| B5 (earlier) and **R_B5** (re-run) | `study.py 5 5,8 R_B5 --d1 all --exec --late-share 0.02 --late-max 3` | 5 aircraft | Every first-shift unit (378): *d1* for all, plus the EXECUTABLE plan. Results in §2.3. |
| B10 (earlier) | `study.py 10 5,10 B10 --d1 60 --joint 11 --exec ...` (flags reconstructed from metadata) | 10 aircraft: 59,397 jobs, 89,193 phases | 60 first-shift units sampled. |
| INV20 (earlier) and **R_INV20** (re-run) | `inversions.py 20 8,16 R_INV20` | 20 aircraft: 118,021 jobs, 178,568 phases | 93 team-shifts, 3,532 units. **R_INV20 reproduced INV20 exactly** (every figure identical). |
| Fidelity | `fidelity.py out/R_H5.json` and `out/H5.json` | — | Spearman, top-decile overlap and zero/nonzero agreement per signal |

## 2. Results

### 2.1 Fidelity to the exact one-shift counterfactual

Values are R_H5 / H5. The exact counterfactual *d1* re-decodes with a floor at the next shift and the plan's priority keys.

| Signal | Spearman vs *d1* | Top-10% overlap | $0 vs nonzero agreement |
|---|---|---|---|
| A effort (standard pm) | −0.20 / −0.23 | 0.17 / 0.12 | — |
| B1 CPM tail | −0.37 / −0.44 | 0.08 / 0.00 | — |
| B2 −precedence float | +0.66 / +0.73 | 0.25 / 0.38 | — |
| C FOCUS `cost_of_delay` | +0.49 / +0.58 | 0.42 / 0.50 | 0.40 / 0.41 |
| D-proxy (b), float dollars | +0.76 / +0.80 | 0.50 / 0.62 | 0.87 / 0.90 |
| D-proxy (c), schedule-graph right shift | +0.71 / +0.83 | 0.58 / 0.62 | 0.89 / 0.91 |
| F effort × band(c) | +0.17 / +0.25 | 0.17 / 0.25 | — |

**Reference noise.**
- R_H5: *d1* was nonzero for 47 of 121 units; **4 were negative** (down to −$472,639). These are list-scheduling anomalies: delaying one task produced a better fleet decode.
- In 10 of 121 units, *d1* moved another aircraft's cost.
- Repair-mode *d2* agreed with *d1* on all 14 sampled units in R_H5. In the earlier H5 run, *d2* gave nonzero values for 7 of 14 (up to $1.1M) where *d1* was $0: repair artifacts depend on the unit.

### 2.2 Non-additivity

From R_H5, joint counterfactual versus the sum of individual *d1*:

| Set | Sum of singles | Largest single | Joint | Sum ÷ joint |
|---|---|---|---|---|
| 10 critical units, one aircraft | $1,064,802 | $172,302 | **$172,302** | 6.2× |
| 7 critical units, one aircraft | $619,306 | $121,479 | **$121,479** | 5.1× |
| 5 critical units, one aircraft | $492,000 | $127,556 | **$127,556** | 3.9× |
| 4 critical units, one aircraft | $296,875 | $134,583 | **$134,583** | 2.2× |
| 4 random missed units | $5,844 | $118,292 | $40,375 | **0.14×** (under-claims) |
| 10 sampled units, one aircraft | $291,514 | $421,458 | $814,646 | **0.36×** (under-claims) |
| 4 random missed units | $557,205 | $421,458 | $557,205 | 1.0× |

- On one aircraft's critical chain, the joint value equals the largest single exactly.
- Mixed sets can go either way. The **double-claim ratio** (sum ÷ joint) ranged from 0.14 to 6.2 across 24 sets.

### 2.3 First-shift values

- **B5 and R_B5:** *d1* was $0 for **all 378** first-shift units at 5 aircraft, reproduced exactly at `5d1236c` (378 decodes, 521.7 s wall on 2 processes). The minimum precedence float of those units was 3.70 days.
  - (b) and (c) were also $0 for every unit.
  - `cost_of_delay` was positive for 97.4%.
  - The EXECUTABLE plan had 5 of 5 aircraft unknown and $0 money.
  - Only 240 of the 378 FORECAST first-shift units (63%) were also EXECUTABLE first-shift units.
  - 334 of the 378 units sat on one aircraft (1205), an artifact of the synthetic station stagger.
- **B10** (earlier): $0 for all 60 sampled first-shift units at 10 aircraft (minimum float 0.58 days); 538 of 696 overlapped (77%).
- **Interpretation:** in this synthetic fleet, near-term work has days of float to delivery. A one-shift-dollar valuation of today's obligation is empty, so any class must use a longer horizon (`04` §1). Real data may be tighter (stations, takt), so this must be re-measured there.

### 2.4 Schedule order versus value

R_INV20 = INV20; EXECUTABLE plan, the cutoff day's 3 shifts.

| Value in start order | Team-shifts with an inversion | Units changed by order-preserving smoothing | Median / p90 change | Largest equal block (median / max) |
|---|---|---|---|---|
| Effort | 95.7% | 79.8% | 4.8% / 37% | 22 / 61 units |
| Effort × weight(C) | 100% | 76.6% | 4.7% / 45% | 20 / 51 |
| C dollars + floor | 100% | 58.6% | 5.1% / 125% | 11 / 31 |

- Per-person sequences: 60% inverted, 41% of units changed, blocks of at most 11.
- **Causes**, classified by what set the later, higher-valued unit's start: crew or capacity wait 91% of all units (3,212 of 3,532); a precedence predecessor about 9%.
- Every aircraft was unknown in the EXECUTABLE plan (20 of 20). Unknown completions remove all money there (also 5 of 5 at 5 aircraft and 9 of 10 at 10).

### 2.5 Runtime and memory

| Method | 5 aircraft | 10 aircraft | 50 aircraft |
|---|---|---|---|
| C `cost_of_delay`, all phases | 0.13–0.20 s | 0.27 s | NOT_MEASURED (seconds expected; the computation is linear) |
| B2 / (b), all phases | 0.10–0.16 s | 0.24 s | NOT_MEASURED |
| (c) graph build + all units | 1.06 s + 0.65 s | 1.7 s + 0.04 s | NOT_MEASURED (about 5–15 s estimated, linear in edges; to measure) |
| D1 per decode | 2.5–2.7 s | 5.8 s | about 85 s (repository) |
| D1 for one shift's obligation | 378 units: about 8 min on 2 processes | — | about 3,000 units × 85 s ≈ **70 h on one process** (extrapolated) |
| Peak memory | 0.6–0.7 GB | 1.0 GB | 5.9–7.5 GB for any full-context method (repository) |

## 3. Scale strategy, challenged

| Stage | Proposal | Verdict |
|---|---|---|
| 1. Cheap proxy for every open task | (c) or (b) on the published FORECAST plan | **Viable.** Seconds at 5–10 aircraft. Fidelity on the zero/nonzero split was 0.87–0.91; the ranking is moderate. Must still be measured at 50 aircraft. Misses capacity effects (the QA unit priced $0 that moved another aircraft +$421k). |
| 2. Selected counterfactuals | *d1* for the top-K or most uncertain units | **Only asynchronously**, and only small K: K = 50 at 85 s is about 70 min on one process. It cannot block publication. The reference is itself noisy (anomalies), so it needs more than one variant. |
| 3. Confidence labels and caching | A class with "proxy agrees / disagrees with *d1*"; cache by (snapshot, plan, policy) | Viable. Snapshots and plans are immutable and content-addressed, so the cache key is natural. Invalidate on a new plan. |
| Change-impact cones | Re-value only units downstream of changed phases or resources | Plausible for the proxy. **Invalid for *d1***, because shared crews couple the whole fleet; the repository's own measurement says every candidate is a full decode (`PRODUCTION_SCALE.md:106`). |
| Pooled solve concurrency | Run *d1* in parallel processes | Linear speed-up at best, and memory per process is 5.9–7.5 GB at 50 aircraft. Not a path to per-shift coverage. |
| Shadow prices (Lagrangian) | Price crew-time slots | NOT_IMPLEMENTED: high engineering cost; the lexicographic levels and caps make the relaxation non-convex. Not recommended now. |

## 4. Test matrix (proposed; nothing here has been implemented as tests)

| # | Scenario | Methods | Measures | Accept | Reject |
|---|---|---|---|---|---|
| T1 | Critical versus slack work (R_H5-type sample, ≥ 3 seeds and 2 scales) | (b), (c), C, A | Spearman, zero/nonzero, top-10% versus *d1* | Proxy: Spearman ≥ 0.70 and zero/nonzero ≥ 0.85 on every sample | Spearman < 0.60, or zero/nonzero < 0.80, on any sample |
| T2 | Late parts (default 20% mix) and QA holds | same | same; share of units unpriced or blocked | Proxy keeps T1 thresholds | Proxy misclassifies > 20% of critical units |
| T3 | Scarce crews or inspectors (QA team of 4) | (c) versus (b) | Capacity-effect misses (*d1* > 0 where the proxy is 0) | Misses < 10% of *d1*-positive units | ≥ 20% |
| T4 | Multiple critical chains; joint sets | joint *d1* versus Σ singles | Double-claim ratio | Any displayed total is computed jointly | Any display sums singles |
| T5 | Perturbation: ±10% durations, ±1 attendee, ±1 day due date, ±25% rates | class from (c) | Class stability (% of units unchanged), top-K Jaccard | ≥ 90% stable; Jaccard ≥ 0.7 | < 80%; < 0.6 |
| T6 | Unknown economics and unknown completion | all | Unknown share; never 0 | Unknown shown as UNKNOWN | Any unknown shown as 0 or FLOAT |
| T7 | Shift crossing, partial segments, corrections | G versus F | Credit totals versus unit facts | Totals reconcile to the job records (D-25) | Any duplicate or orphan credit |
| T8 | Shadow simulation, 14 days × 3 shifts (§5) | G classes | Realized delivery change per slipped unit, by class | CRITICAL slips move completions measurably more than FLOAT slips (difference with bootstrap CI excluding 0) | No difference, or classes unstable across consecutive publications (< 70% unchanged) |
| T9 | Fairness | G versus F | Attainment gap between high and low critical-share teams with identical execution | G: 0 by design; F gap reported | — |
| T10 | Invariants | all | Plan bytes, goals, validator outcome with and without valuation | Byte-identical plan and goals; zero violations | Any change to the plan or goals |
| T11 | Cost | (c) at 50 aircraft | Wall time, memory, after publication | ≤ 60 s and ≤ 2 GB extra, asynchronous, never delaying `sign_accept` | Over budget, or on the publication critical path |

## 5. Minimum viable shadow-mode study

1. In `focus/sim` (synthetic, production engine), compute (c) classes at each publication into a **side artifact**. The plan and goals are untouched (T10).
2. Execute the 14 × 3 shifts. For every slipped unit, record the class it had at publication and the change in the next plan's per-aircraft completion and controllable cost.
3. Report:
   - T8: CRITICAL versus FLOAT effect, with a CI;
   - class stability across publications;
   - the correlation of critical coverage with attainment (if > 0.9, coverage adds little);
   - fairness (T9);
   - cost (T11).
4. **Data requirement for real validation.** Weeks of executed plans with verified lifecycle events, claims and decisions, attendance and actual delivery outcomes. None of this exists in the repository (OD-15). Synthetic results show **internal consistency only**, never predictive validity.

## 6. Decision rules

- **Adopt G's classes in shadow** if T1–T3, T5 and T11 pass at 50 aircraft.
- **Promote coverage to the dashboard** if T8 shows a real CRITICAL-versus-FLOAT difference *and* coverage is not redundant with attainment (correlation < 0.9).
- **Promote *d1* (exact) analytics** only if:
  - the proxy and *d1* disagree on > 15% of the proxy's top decile; and
  - an asynchronous K = 50 budget fits within the shift; and
  - repeated variants bound the anomalies (range reported).
- **Reject any impact-weighted earned currency (F, D)** unless T4 shows additivity (it did not) **and** T9 shows no assignment bias (it cannot, by construction).
- **Fall back to A only** (effort attainment, status quo) if T8 fails or T5 fails: the classes would then be noise, and A is honest.
