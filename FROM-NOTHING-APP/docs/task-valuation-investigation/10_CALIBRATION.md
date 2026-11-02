# 10 — Calibration: the exchange rate w and the grade bands

**Owner question (2026-11-02).** With each shift now scored against its own plan (D-26), what tests can set the factors
and bands for off-plan completions? The owner said "Go ahead", and will re-run the tests on real data.

**Status.** This is an investigation; the score has not changed. R5, the off-plan credit rule, is still a proposal
(`09` §8) and is not implemented. Every number here comes from the synthetic fleet. §8 explains how to re-run the
analysis on real data.

## 1. Answer

- **Exchange rate: w = 0.8.** Use 0.7 if no misread swap may ever pay.
  - **Production.** R5 tracks production equally well at w = 0.6, 0.7 and 0.8 (Spearman 0.81 over 16 paired runs).
    Today's score gets the direction of the production change right in only 2 of the 16.
  - **Swaps.** When a swap is read correctly it never pays, at any w. Misread swaps are the risk. If 15% of swap
    minutes are misread, 0.09% of swap team-shifts gain at w = 0.8, by at most 0.09 points. At w = 0.7 none gain.
  - **w = 1.0 is not recommended.** It tracks production worst (0.64), and misread swaps pay the most.
- **Bands: the simulation cannot set them.** The synthetic floor is too forgiving. At 10 aircraft, 90% of well-run
  team-shifts score 100, and the demonstration bands give 99% of them an A. At 50 aircraft every band set gives
  nearly every team-shift an A.
  - Without off-plan work, R5 = 100 × (1 − unexcused shortfall) − the out-of-sequence charge, for every w. A band
    is therefore a tolerance on the share of planned work left undone without an excuse.
  - Set the tolerances from real shortfall data with `tools/calibrate_scores.py` (§8).
- **Late plans hurt teams more than any floor behavior tested.** In the simulation, 30% of shift-2 and shift-3
  plans were accepted after their shift started. Under D-26 each such shift is scored against the previous plan
  while the crews work from the new one. The result:
  - the 95th-percentile shortfall doubled (6% to 13%);
  - cross-team relief rose 25–28 times.

  The pipeline before each shift has to finish on time; this needs an owner decision (§7).
- **The cutoff gap works as designed.** Work finished after the next plan's data cut is credited once, in its own
  shift. The next shift's goal holds it as `DONE_BEFORE_SHIFT` relief. That covers 0.8–4.7% of goal minutes at
  10 aircraft and 0.4% at 50.
- **Timing fits.** At 50 aircraft each shift's run took 3.6–5.2 minutes: load, plan, validate and write. That fits
  inside the 15–20 minute window. The live data pull has not been measured.

## 2. What was run

**Paired floor scenarios.** Each run was 10 aircraft, team size 5–10, 12 shifts (4 days) and two seeds (20261026
"s6" and 20261027 "s7"). Other settings:

- late parts on 2% of jobs, up to 3 days late;
- the next plan's data cut 20 minutes before the shift end (D-26);
- every run of a seed starts from the same fleet, so only the floor's behavior differs;
- revision `b871dd6`. The two DL runs were repeated at `0e12945`, which seeds late plans per shift.

| Code | Floor behavior | Settings (`offplan_paired.py`) |
| --- | --- | --- |
| N0 | Works the plan only | `--deviation 0 --pull-ahead 0` |
| PL | Some teams with spare time pull planned work forward | `--pull-ahead 0.35` |
| PH | Every team with spare time pulls planned work forward | `--pull-ahead 1.0` |
| PF | As PH, looking 24 hours ahead instead of 10 | `--pull-ahead 1.0 --horizon 1440` |
| SE | Half of team-shifts swap a planned task for an off-plan one of about the same size | `--deviation 0.5` |
| SC | As SE, but the team picks the largest ready task (cherry-picking) | `--deviation 0.5 --swap-pick largest` |
| SF | As SC, looking 24 hours ahead | `--deviation 0.5 --swap-pick largest --horizon 1440` |
| DF | A realistic mix: some pull-forward, rare swaps | `--deviation 0.05 --pull-ahead 0.35` |
| DL | DF, with 30% of shift-2 and shift-3 plans accepted after their shift starts | DF + `--late-plans 0.3` |

**At scale.** B50 is the DF behavior at 50 aircraft (default team sizes), seed s6, over 12 shifts.

**Scoring.** For every team-shift, `calibrate.py` computes three things:

- **R0**, today's canonical score: 0.70 × attainment + 0.30 × adherence, minus the out-of-sequence charge.
- **R5(w)**: 100 × (w × min(1, (Cp + additive off-plan) / D) + (1 − w) × min(1, Cp / D)), minus the
  out-of-sequence charge.
- **The output index**: (Cp + additive off-plan) / D, uncapped.

Off-plan work is classified by P2. It is substitutive when the team-shift left a ready, dispatched planned task undone
with no disruption on record, and additive otherwise. A share q of substitutive minutes can be misread as additive, to
model a classifier that misses swaps.

## 3. What each behavior does to production

The table shows each run's change against N0 of the same seed, as s6 / s7. Output is verified completions (Cp + U) in
standard person-minutes. Open work is the work still open at the last cutoff (lower is better). Cross-team relief is
approved `CROSS_TEAM_PREDECESSOR` minutes, a measure of how often a team was blocked by another team's work.

| Behavior | Output | Jobs completed | Open work | Cross-team relief |
| --- | --- | --- | --- | --- |
| PL | +19,095 / +17,828 | +143 / +177 | −28,578 / −11,995 | +105 / 0 |
| PH | +51,193 / +49,230 | +420 / +452 | −59,861 / −44,658 | +105 / +45 |
| PF | same as PH | same as PH | same as PH | same as PH |
| SE | −2,281 / −3,146 | −46 / −1 | +5,769 / −510 | +935 / +635 |
| SC | +5,481 / +9,077 | +11 / +52 | −12,252 / −6,428 | +980 / +645 |
| SF | +14,624 / +17,238 | +38 / +96 | −20,152 / −16,275 | +720 / +670 |
| DF | +17,331 / +13,392 | +117 / +119 | −31,691 / −14,718 | +150 / +55 |
| DL | +18,721 / +14,562 | +117 / +119 | −31,691 / −14,718 | +5,955 / +2,420 |

- **Pulling planned work forward raises output.** Heavy pull-forward (PH) added about 50,000 minutes and 420–450
  jobs in 12 shifts.
- **Looking further ahead changed nothing.** PF equals PH: every pull the floor made was within the 10-hour horizon.
- **An equal swap costs output (SE).**
- **Cherry-picking raised output, but at a cost (SC, SF).** The gain came from capacity that would otherwise have
  sat idle. Other teams were blocked about 15 times as often as under N0: 65 → 1,045 and 45 → 690 minutes of
  cross-team relief.
- **Late plans leave output almost unchanged** (DL against DF), but they multiply cross-team relief by 25–28
  (215 → 6,020 and 100 → 2,465). Crews dispatched on the new plan miss the old plan's sequence, and the score
  excuses or charges the difference (§6).

## 4. Which score follows production

The table compares each run's score change against N0 with its output change, over 16 pairs (8 behaviors × 2 seeds).
Spearman is the rank correlation. Sign agreement counts the runs where the score moved in the same direction as
output.

| Score | Spearman | Sign agreement |
| --- | --- | --- |
| R0 (today) | 0.08 | 2 / 16 |
| Output index | 0.85 | 12 / 16 |
| R5, w = 0.5 | 0.77 | 9 / 16 |
| R5, w = 0.6 | 0.81 | 10 / 16 |
| R5, w = 0.7 | 0.81 | 10 / 16 |
| R5, w = 0.8 | 0.81 | 10 / 16 |
| R5, w = 0.9 | 0.77 | 12 / 16 |
| R5, w = 1.0 | 0.64 | 12 / 16 |

- **Today's score punishes the most productive behavior.** R0 fell by 1.1 to 3.3 points for every pull-forward
  behavior, even though those runs added 13,000–51,000 minutes of output. R5 at w = 0.8 rose by 0.19 to 0.35 points
  for the same runs.
- **R5 disagrees with output on six runs, all on purpose.**
  - SC and SF (four runs) raised output by cherry-picking, but R5 lowers their score because a swap must never pay
    (`09` §5).
  - DL (two runs): the teams lose score for plans accepted late, which they do not control (§6).
- **The output index follows production best,** but it ignores the plan entirely, so it is not a grade (§5,
  fairness).
- The plateau runs from w = 0.6 to 0.8. Below it the grade gives too little weight to useful off-plan work; above it
  the grade starts to ignore the plan.

## 5. Can a swap pay?

**Run level.** Every swap behavior (SE, SC, SF) lost score at every w, even with 30% of swap minutes misread: from
−0.92 to −3.08 points against N0.

**Team-shift level.** There are 1,176 team-shifts with a swap across all runs. Each one is compared with the same
team-shift had it done the planned work it dropped.

| Misread share q | w = 0.7 | w = 0.8 | w = 0.9 | w = 1.0 |
| --- | --- | --- | --- | --- |
| 0% | none pay | none pay | none pay | none pay |
| 5% | none pay | none pay | none pay | none pay (best case 0.00) |
| 15% | none pay | 0.09% pay, at most +0.09 points | 0.17%, at most +0.37 | 0.17%, at most +0.65 |
| 30% | 0.26%, at most +1.77 | 0.60%, at most +2.33 | 0.68%, at most +2.89 | 0.85%, at most +3.45 |

The mean gain is always negative (−3.3 to −6.2 points). A misread swap pays only when the team brought in much more
work than it dropped, and even then only by a fraction of a point at w ≤ 0.8.

**The classifier on real data.** On the synthetic floor, the production form of P2 (planned work missed with no claim
on record) matches the simulated truth exactly. The floor misses planned work without a claim only when it swaps.
Real floors will miss planned work for other reasons too:

- When a team misses work for another reason and also does extra work, P2 calls the extra work substitutive. That
  is a false alarm, and it costs the team credit.
- When a team swaps and then files a claim, P2 calls the swap additive. That is a miss, the risk the table measures.

Both rates have to be measured on real team-shifts (§8, the review sample).

**Fairness.** Lightly loaded teams have more spare time to pull work forward.

- The output index falls with load: Spearman as low as −0.39 under PH.
- The grade gain from off-plan credit barely depends on load (−0.17 to +0.15), because the grade is capped at 100
  and off-plan work counts at w.

So rank teams by grade, never by the output index.

## 6. Bands

Well-run behaviors (N0, PL, PH and DF) give 3,344 team-shifts at 10 aircraft:

| Measure | p1 | p5 | p10 | p50 |
| --- | --- | --- | --- | --- |
| R5, w = 0.8 | 89.6 | 98.5 | 100 | 100 |
| Daily pooled R5, w = 0.8 (1,116 team-days) | 95.3 | 98.6 | 99.5 | 100 |

The unexcused shortfall is zero up to the 90th percentile, 2.9% at the 95th and 12.3% at the 99th.

| Band set (A / B / C / D lower bounds) | A | B | C | D | F | Grade flips between a team's consecutive shifts |
| --- | --- | --- | --- | --- | --- | --- |
| Demonstration 90 / 80 / 70 / 60 | 3,310 | 27 | 5 | 2 | 0 | 2.1% |
| Strict 95 / 90 / 85 / 80 | 3,258 | 52 | 18 | 9 | 7 | 4.9% |
| Today's effective 85.7 / 71.4 / 57.1 / 42.9 | 3,328 | 14 | 2 | 0 | 0 | 1.0% |
| Wide 92 / 85 / 75 / 65 | 3,294 | 34 | 14 | 1 | 1 | 3.0% |

At 50 aircraft (B50, 372 team-shifts):

- R5 percentiles: p1 96.5, p5 98.3, p10 99.1.
- Shortfall: p95 1.8%, p99 3.8%.
- Every band set gives every team-shift an A, except one B under the strict set.

**Reading.** The synthetic floor rarely leaves planned work undone without an excuse, so no band set separates
anything. Real floors will spread wider. The band decision is the owner's tolerance for unexcused shortfall:

- for example, A for at most 2% undone, B for 5%, C for 10% and D for 20%, which gives bands 98 / 95 / 90 / 80;
- check those tolerances against the real shortfall percentiles and flip rates (§8).

Single-shift grades flip more often than daily or weekly ones. Recognition is better tied to pooled periods, which
the rollups already compute (D-25).

**Late plans move the bands.** DL, against DF of the same seeds:

- the shortfall's 95th percentile rose from 6.1% / 6.0% to 13.0% / 12.9%;
- R5's 1st percentile fell from 87.1 / 85.8 to 81.1 / 81.5.

A band set calibrated on shifts with on-time plans will grade late-plan shifts down for something the crew did not
cause.

## 7. Timing, the cutoff gap and late plans

- **Run time at 50 aircraft.** Each of the 12 shifts:

  | Step | Time |
  | --- | --- |
  | Load the snapshot | 69–90 s |
  | Plan | 118–192 s |
  | Validate | 17–24 s |
  | Write | 5–7 s |
  | **Total** | **3.6–5.2 min** |

  Peak memory was 6.6 GB. This fits the 15–20 minute window (`PRODUCTION_SCALE.md`) with room for the data pull,
  staging and signing, which were not measured here.
- **The cutoff gap.** The next plan's data is cut 20 minutes before the shift ends.
  - At 10 aircraft, 5.4–6.3% of verified completions fall in those last 20 minutes and 5.5–6.5% in overtime. At 50
    aircraft the shares are 3.4% and 1.0%.
  - That work counts once, in the shift where it was done. The next shift's goal, frozen before it was finished,
    holds it as `DONE_BEFORE_SHIFT` relief: 0.8–4.7% of goal minutes at 10 aircraft and 0.4% at 50.
  - The integration test `test_work_finished_after_the_next_cutoff_counts_once_in_its_own_shift` checks this.
- **Late plans.** Under D-26, a plan accepted after its shift starts is only a proposal for that shift. The previous
  plan stays the goal of record, while the floor works from the new plan.
  - The simulation shows the cost: more relief, more off-plan work outside any goal (2,803 and 1,760 minutes), and
    lower scores. R0 fell by 1.2 / 0.9 points and R5 by 0.3 / 0.2 against DF.
  - **Decision for the owner.**
    1. Make the run before each shift finish in time: an automatic `sign_accept`, or a staffed promotion window
       with an alert.
    2. Protect the teams when it does not: for example, review the frozen units the late plan moved under the
       existing separately reviewed `PLAN_CHURN` cause.
  - `tools/calibrate_scores.py` reports the share of shifts whose plan of record is the previous shift's plan
    (`stale_plans`). It should be zero.

## 8. Re-running on real data

The incentive tests (§3 to §5) need counterfactual floors, which only the simulation can produce. Everything else can
be measured on real team-shifts once FOCUS scores them.

**Prerequisites.**

- FOCUS runs the pipeline before every shift against the company's data. Shadow mode is enough: plans accepted
  and goals frozen, whether or not crews are dispatched on them.
- The observation feeds are installed (OD-15). At minimum: `lifecycle_events`, `actual_intervals`,
  `disruption_claims` and `excusal_decisions` (`docs/data/09-observation-feeds.md`).
- The evaluator runs after every shift's overtime window (`docs/execution/INSTALL_AND_PIPELINE.md` §4).
- At least four weeks of scored shifts, about 1,000 or more team-shifts. Bands need the tails (p1, p5), which take
  volume.

**Step 1. Measure.** Run this in the company environment, with the evaluator's read credentials:

```bash
python tools/calibrate_scores.py store --from 2027-01-04 --to 2027-01-31 --out calibration.json --review-sample 60 --review-csv review.csv
```

It reads only what FOCUS keeps for every team-shift: the light performance row, the job record and, within the 90-day
evidence window, each completion's time. It writes nothing to the store. The JSON holds:

| Section | What it answers |
| --- | --- |
| `coverage` | Scored, unknown, neutral and no-plan team-shifts. Unknown ones are excluded. |
| `shortfall` | The unexcused shortfall percentiles: the basis of the bands. |
| `scores`, `output_index` | R0 and R5(w) for w = 0.5 … 1.0, and the output index. |
| `grades` | Grades under each band set (`--bands 98,95,90,80` adds your own), flips between consecutive shifts, and R0 → R5 grade changes. |
| `daily_pooled_R5_w0.8` | Pooled daily grades per team. |
| `offplan` | How often teams work off plan, how much, and P2's split into additive and substitutive. |
| `swap_gain` | On the real distributions: could a misread swap pay, at each w and q? |
| `relief` | Excused minutes by cause, and `DONE_BEFORE_SHIFT` minutes. |
| `boundary` | The share of completions in the last `--lead` minutes and in overtime. |
| `stale_plans` | The share of shifts scored against the previous shift's plan. |
| `fairness` | Planned minutes against the grade gain and against the output index. |

**Step 2. Estimate the classifier's error rates.**

1. Supervisors label each row of `review.csv` as `SWAP` (the team dropped planned work for it) or `EXTRA` (the work
   was additional). The rows are half P2-substitutive and half P2-additive.
2. Then run:

   ```bash
   python tools/calibrate_scores.py store --from 2027-01-04 --to 2027-01-31 --labels review.csv
   ```

   This reports q, the share of true swaps that P2 calls additive, and the false-alarm rate, the share of extra work
   P2 calls substitutive. Because the sample is stratified, weight each class by its population share before quoting
   a site-wide rate.

**Step 3. Decide.**

| Decision | Rule |
| --- | --- |
| w | Take the largest w at which `swap_gain` shows no paying swap at the measured q, or a share and size within the owner's tolerance. On the synthetic floor: q ≤ 5% allows w = 0.9; q ≤ 15% gives w = 0.8; zero tolerance gives w = 0.7. If q is above 30%, improve the classifier before crediting off-plan work: match each off-plan job to the dispatched task it displaced. |
| False alarms | A high false-alarm rate means teams lose credit for real extra work. Check that missed work with a real cause gets a claim. |
| Bands | Pick tolerances on unexcused shortfall per grade, convert them (band = 100 × (1 − tolerance)), and check the grade counts and flip rates. Prefer bands whose flip rate between a team's consecutive shifts stays low, under about 5%, and grade recognition on pooled days or weeks. |
| Pipeline timing | `stale_plans.share` must be about 0 before bands are set. Otherwise late plans are mixed into the shortfall. |
| Cutoff | If more than about 10% of completions land in the last `--lead` minutes, the next plan misses them. Consider a shorter pipeline or a later cutoff. |

**Step 4. Recalibrate the simulation to re-run the incentive tests.**

1. Set the simulated floor's rates to the measured ones: the share of team-shifts with off-plan work
   (`--pull-ahead`), P2's substitutive share (`--deviation`), and the late-part share.
2. Re-run `evidence/scripts/calib_run.sh` and `calibrate.py`.

The fleet stays synthetic: the simulation does not read a real snapshot. The incentive conclusions are structural (a
correctly read swap never pays), and the `swap_gain` section re-checks them on the real distributions.

**Owner decisions this feeds.**

- w;
- the band tolerances;
- implementing R5 (`09` §8);
- signing: automatic or manual promotion before each shift;
- protection for teams when a plan is late.

## 9. Limitations

- **The synthetic floor is forgiving.** It misses planned work without an excuse only when it swaps, and utilization
  is about 60%. Real shortfall will be larger and spread wider.
- **Small, extreme samples.** Two seeds, 12 shifts and 10 aircraft, with deliberately strong behaviors (every idle
  team pulls work forward; half the teams swap). One 50-aircraft run.
- **Not owner-approved.** w = 0.8 is a recommendation. The bands are undecided.
- **The tool's P2 is a team-shift rule.** It reads planned work missed with no claim from the job record. A task-level
  match of each off-plan job to the dispatched task it displaced would be stricter. Building that needs dispatch
  records per task.

## 10. Evidence

Scripts are in `evidence/scripts/`; run them from the app root virtualenv. `common.SCRATCH` is the output folder.

| Run | Command | Output |
| --- | --- | --- |
| Matrix | `calib_run.sh NAME SFX SEED ARGS…` for each line of `calib_jobs.txt` (18 runs: 9 behaviors × 2 seeds), which calls `offplan_paired.py 10 5,10 12 C_NAME_SFX ARGS --seed SEED --late-share 0.02 --late-max 3 --lead 20` | `evidence/outputs/CALIB_RUNS.json` (per-run settings, timing and per-shift plan facts), `evidence/outputs/CALIB.log` |
| B50 | `offplan_paired.py 50 10,30 12 B50_DF_s6 --deviation 0.05 --pull-ahead 0.35 --seed 20261026 --lead 20` | in `CALIB_RUNS.json` |
| Calibration | `calibrate.py CALIB C_N0_s6 … C_DL_s7 B50_DF_s6`; `calibrate.py CAL_B50 B50_DF_s6` | `evidence/outputs/CALIB.json`, `evidence/outputs/CAL_B50.json` |
| Tool on the simulation | `python tools/calibrate_scores.py sim OUT.db` for each run | `evidence/outputs/CALIB_TOOL.json` |

**Correction.** `CALIB.json` pairs B50 with the 10-aircraft N0 of seed s6 in its `deltas` and `alignment`. That pair
is meaningless, because the fleets differ. §4 reports the 16 valid pairs, recomputed without it. `CALIB.json` is
kept as produced.
