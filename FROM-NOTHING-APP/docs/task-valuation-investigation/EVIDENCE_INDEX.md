# Evidence index — task-valuation investigation

**Source revision:** `5d1236c8e5777b9075a652571707ae57bf5357ff` (`main`, 2026-11-02). Reports 01–09 changed no application code, tests, policies, schemas or UI. On the owner's direction, per-shift goals of record were then implemented as D-26 (`09` §7; commits 55e857e to 0e12945, with the simulation's cutoff gap and floor behaviors). Report 10 adds one read-only tool, `tools/calibrate_scores.py`, with its tests.

**Data:** all experiments use the synthetic `FleetSpec` fleet (`focus/data/fullscale_fixture.py`). Money is the modeled airline revenue proxy (`fixtures/economics/README.md`): a planning assumption, never a contract or a realized loss.

## 1. Reports
| File | Stage |
|---|---|
| [01_CODE_AUDIT.md](01_CODE_AUDIT.md) | Code and data-contract audit, lineage, grain, traps |
| [02_HYPOTHESIS_STRESS_TEST.md](02_HYPOTHESIS_STRESS_TEST.md) | Definitions, claim verdicts, 14 counterexamples |
| [03_MODEL_COMPARISON.md](03_MODEL_COMPARISON.md) | Models A–G (H infeasible), measured fidelity, decision matrix |
| [04_ATTRIBUTION_AND_MATH.md](04_ATTRIBUTION_AND_MATH.md) | Formal G, F and D; counterfactual protocol; non-additivity; Shapley |
| [05_SHIFT_METRICS_AND_FAIRNESS.md](05_SHIFT_METRICS_AND_FAIRNESS.md) | Score audit, 13 adversarial cases, dashboards, fairness, personnel stance |
| [06_VALIDATION_AND_SCALE.md](06_VALIDATION_AND_SCALE.md) | Measured facts, runtimes, test matrix, shadow study, decision rules |
| [07_INTEGRATION_OPTIONS.md](07_INTEGRATION_OPTIONS.md) | Options A–D, contracts, touchpoints, phases, gates, rollback |
| [08_FINAL_DECISION_REPORT.md](08_FINAL_DECISION_REPORT.md) | Decision |
| [09_OFFPLAN_CREDIT.md](09_OFFPLAN_CREDIT.md) | Owner follow-up: what unscheduled work is worth, adherence rebuttal, paired floor scenarios |
| [10_CALIBRATION.md](10_CALIBRATION.md) | Owner follow-up: the exchange rate w and the grade bands, measured on paired floor behaviors; how to re-run on real data (`tools/calibrate_scores.py`) |
| [11_PERFORMANCE_MEASURES.md](11_PERFORMANCE_MEASURES.md) | Owner question: which measure for shift, team and superintendent performance, any period and period comparisons. Ten candidates tested for fairness, sensitivity, stability, completeness and alignment; the method by level |

## 2. Reproducible runs
The scripts are in [evidence/scripts/](evidence/scripts) and run from the app root's virtualenv. Each script writes into `common.SCRATCH`, a constant at the top of `common.py`; edit it for your machine.

**The floor patch.** `study.py` injects the deferral floor by wrapping `focus.engine.max.optimizer.greedy._floors` **inside its own process**. Nothing on disk is modified.

| Run | Command | Revision | Output | Key result |
|---|---|---|---|---|
| R_H5 | `python study.py 5 5,8 R_H5 --units horizon --d1 all --d2 14 --joint 15 --late-share 0.02 --late-max 3` | 5d1236c | [R_H5.json](evidence/outputs/R_H5.json), [R_H5.analysis.txt](evidence/outputs/R_H5.analysis.txt), [R_runs.log](evidence/outputs/R_runs.log) | Plan identical to H5 ($4,846,815.97). 121 units: *d1* nonzero 47, negative 4. Proxies (b) and (c): Spearman 0.76 and 0.71, zero/nonzero agreement 0.87 and 0.89. Joint = largest single for same-aircraft critical sets. |
| H5 | (same flags, earlier run of the same plan; 80-unit sample) | working tree based on a1e62cc; engine code identical to 5d1236c | [H5.json](evidence/outputs/H5.json), [H5.analysis.txt](evidence/outputs/H5.analysis.txt), [H5.log](evidence/outputs/H5.log) | (c) Spearman 0.83; (a) 0.58; (b) 0.80 |
| R_B5 | `python study.py 5 5,8 R_B5 --d1 all --exec --late-share 0.02 --late-max 3` | 5d1236c | [R_B5.analysis.txt](evidence/outputs/R_B5.analysis.txt), [R_runs.log](evidence/outputs/R_runs.log) | All 378 first-shift units have *d1* = $0. Minimum float 3.70 days. EXECUTABLE: 5 of 5 aircraft unknown. Overlap 240 of 378. |
| B5 | (same flags, earlier) | as H5 | [B5.analysis.txt](evidence/outputs/B5.analysis.txt), [B5.log](evidence/outputs/B5.log) | Same result as R_B5 |
| B10 | `python study.py 10 5,10 B10 --d1 60 --joint 11 --exec --late-share 0.02 --late-max 3`. Flags reconstructed from the run metadata (60 *d1* units, 11 joint sets); the earlier run's command line was not recorded. | as H5 | [B10.analysis.txt](evidence/outputs/B10.analysis.txt), [B10.log](evidence/outputs/B10.log) | 60 sampled first-shift units all $0. Minimum float 0.58 days. EXECUTABLE: 9 of 10 unknown. Overlap 538 of 696. |
| R_INV20 | `python inversions.py 20 8,16 R_INV20` | 5d1236c | [R_INV20.json](evidence/outputs/R_INV20.json) | Identical to INV20 ([INV20.json](evidence/outputs/INV20.json), [INV20.log](evidence/outputs/INV20.log)). Effort in start order inverted in 95.7% of team-shifts; smoothing changes 79.8% of units. Causes: crew wait 91% of all units. |
| Fidelity | `python fidelity.py out/R_H5.json` and `out/H5.json` | — | [fidelity.txt](evidence/outputs/fidelity.txt) | Spearman versus *d1*: effort −0.20/−0.23, tail −0.37/−0.44, −TF +0.66/+0.73, urgency +0.49/+0.58, (b) +0.76/+0.80, (c) +0.71/+0.83, effort × band +0.17/+0.25 |
| Counterexamples | `python counterexamples.py` | — | [counterexamples.txt](evidence/outputs/counterexamples.txt) | Exact numbers for CE1–CE13 in `02` |
| Attribution | `python3 attribution.py` | — | [attribution.txt](evidence/outputs/attribution.txt) | Shapley, sequential and proportional allocations; avoided cost versus shares in `04` |
| Score scenarios | inline `formula` calls with the demo policy (session record) | 5d1236c | values quoted in `05` §2 | Band thresholds with U = 0: A 85.7%, B 71.4%, C 57.1%, D 42.9% |
| PC5 | `python plan_classes.py 5 5,8 PC5 --late-share 0.02 --late-max 3` | 44caf00 (engine code identical to 5d1236c) | [PC5.json](evidence/outputs/PC5.json) | 378 first-shift units on the redispatch plan, all FLOAT; neutral seed plan 336 FLOAT and 11 AT_RISK. Due ±1 day changes no class. 28 units common to both plans, 27 agree. Classes 0.47 s + 0.07 s. |
| PC50 | `python plan_classes.py 50 10,30 PC50` (default fleet) | 44caf00 | [PC50.json](evidence/outputs/PC50.json), [PC50.log](evidence/outputs/PC50.log) | 3,189 (redispatch) and 2,615 (seed) first-shift units, all FLOAT. About 10 s per publication: **T11 passes**. 29 common units, all agree. Peak 3.6 GB for the whole run. |
| OV5 | `python offplan_value.py 5 5,8 OV5 --sample 60 --late-share 0.02 --late-max 3 --procs 1` | 44caf00 | [OV5.json](evidence/outputs/OV5.json) | Pull-ahead value: 1 of 60 sampled ready jobs nonzero ($159,809; $850 per spm). On-time value of 60 planned first-shift units: all $0. |
| OR50 | `python offplan_replay.py runtime/sim/fleet.db OR50.json` (read-only replay of the 50-aircraft, 14-day run `SIM9427255df24c45204d12`, 2026-10-31, default fleet) | — | [OR50.json](evidence/outputs/OR50.json) | Current *H* < 1 on exactly the 249 team-shifts with off-plan work. Penalty mean 0.84, max 3.75 points. Classifier P1 calls 38% of pull-ahead substitutive; P2 calls 5%. Swap knock-on cost 4.8% of displaced effort. |
| OS | `python offplan_scenarios.py OS.json` | — | [OS.json](evidence/outputs/OS.json) | Worked scenarios and incentive checks for rules R0, R1, R3 and R5 (`09` §2.2, §5) |
| SH10 | `python shadow_sim.py 10 5,10 5 SH10 --late-share 0.02 --late-max 3`, then `python shadow_analyze.py SH10` and `python shadow_offplan_classes.py SH10` | 44caf00 | [SH10.analysis.json](evidence/outputs/SH10.analysis.json), [SH10.offplan_classes.json](evidence/outputs/SH10.offplan_classes.json), [SH10.log](evidence/outputs/SH10.log). The per-unit class file (2.9 MB) and the 170 MB simulation database are not committed; the commands regenerate them. | 15 publications: no CRITICAL unit; AT_RISK 7–25%. **T8 fails**: AT_RISK misses move delivery by −166 / +132 min with / without, CI [−1,112, +287]. **T9**: Spearman 0.9997. Stability 89.7%. |
| PAIRED | `python offplan_paired.py 10 5,10 12 NAME --deviation D --pull-ahead P --seed S --late-share 0.02 --late-max 3` for N0 (0, 0), PA (0, 1) and DV (1, 0), seeds 20261026 (s6) and 20261027 (s7); then `python offplan_paired_analyze.py PAIRED N0_s6 PA_s6 DV_s6 N0_s7 PA_s7 DV_s7` | 44caf00 | [PAIRED.json](evidence/outputs/PAIRED.json), [PAIRED.log](evidence/outputs/PAIRED.log) | Pull-ahead: +1.92 to +2.21 minutes of output per off-plan minute, +501 to +544 jobs. Swaps: −0.33 to −0.35 minutes of output and 0.20 minutes of other teams' work blocked per displaced minute. R0 ranks the pull-ahead floor below the no-extra-work floor; R5 ranks them by production. |
| PERSHIFT | `python offplan_paired.py 10 5,10 12 {N0new,PAnew}_s6 --deviation 0 --pull-ahead {0,1} --seed 20261026 --late-share 0.02 --late-max 3`, then `python offplan_paired_analyze.py PERSHIFT N0_s6 N0new_s6 PA_s6 PAnew_s6` | c2c729a with the per-shift goals in the working tree (committed as 55e857e) | [PERSHIFT.json](evidence/outputs/PERSHIFT.json), [PERSHIFT.log](evidence/outputs/PERSHIFT.log) | Per-shift goals of record (D-26) against the day-level freeze on the same floor: off-plan U 2,335 → 0 without pull-ahead; all pulled-ahead work becomes off-plan (80,302 minutes) with it (`09` §7). |
| CALIB | `calib_run.sh NAME SFX SEED ARGS` for the 18 lines of [calib_jobs.txt](evidence/scripts/calib_jobs.txt) (9 floor behaviors × seeds s6, s7; 10 aircraft, 12 shifts, 20-minute cutoff lead), then `python calibrate.py CALIB C_N0_s6 … C_DL_s7 B50_DF_s6` | b871dd6 (DL runs repeated at 0e12945) | [CALIB.json](evidence/outputs/CALIB.json), [CALIB_RUNS.json](evidence/outputs/CALIB_RUNS.json), [CALIB.log](evidence/outputs/CALIB.log) | R5 tracks production best at w = 0.6–0.8 (Spearman 0.81 over 16 valid pairs); R0 agrees in sign 2 of 16. No correctly read swap pays at any w; at a 15% misread share, 0.09% of swap team-shifts pay at w = 0.8 (≤ +0.09 points). The `deltas`/`alignment` entries for B50 pair it with a 10-aircraft run and are invalid (`10` §10). |
| B50 | `python offplan_paired.py 50 10,30 12 B50_DF_s6 --deviation 0.05 --pull-ahead 0.35 --seed 20261026 --lead 20`, then `python calibrate.py CAL_B50 B50_DF_s6` | b871dd6 | [CAL_B50.json](evidence/outputs/CAL_B50.json), timing in [CALIB_RUNS.json](evidence/outputs/CALIB_RUNS.json) | 3.6–5.2 minutes per shift (load, plan, validate, write), peak 6.6 GB. Every band set gives nearly every team-shift an A: the synthetic floor cannot set bands. |
| CALIB_TOOL | `python tools/calibrate_scores.py sim OUT/C_NAME.db` for each run (the real-data tool, run on the simulation) | working tree after 0e12945 | [CALIB_TOOL.json](evidence/outputs/CALIB_TOOL.json) | The production P2 rule (planned work missed with no claim) matches the simulated swaps exactly; `DONE_BEFORE_SHIFT` 0.8–4.7% of goal minutes at 10 aircraft, 0.4% at 50; completions in the last 20 minutes 5.4–6.3% (10 aircraft), 3.4% (50). |
| AW50 | `python aircraft_weighted.py runtime/sim/fleet.db AW50.json` (read-only on the 50-aircraft, 14-day run `SIM9427255df24c45204d12`, 2026-10-31) | working tree after b70f57a | [AW50.json](evidence/outputs/AW50.json) | Weighting each job by its aircraft's criticality (D-29) against minute attainment, 1,302 team-shifts, every aircraft late. Most critical aircraft counted double: mean change 0.05 points, largest 2.84, 0.46% of team-shifts move a point or more, rank agreement 0.9996. Counted four times: 0.08, 4.72, 0.92%, 0.9987. Among the 393 team-shifts below 100: 0.17 and 0.27 points on average. Spread of criticality inside one team-shift: median 0.15 on a 0 to 1 scale. |
| PM_T1 | `python perf_equal_teams.py runtime/sim/fleet.db PM_T1_equal_teams.json` | working tree after aec4687 | [PM_T1_equal_teams.json](evidence/outputs/PM_T1_equal_teams.json) | Thirty teams equal by construction, 42 shifts. Throughput and summed job points rank them the same way in both halves of the period (0.99), by head count (1.00). Output per available hour ranks the plan's load (0.97). The plan-based measures show no ranking (−0.26 to −0.08). |
| PM_T4 | `python perf_detection.py runtime/sim/fleet.db PM_T4_detection.json` (200 trials per cell, seed 20261111) | same | [PM_T4_detection.json](evidence/outputs/PM_T4_detection.json) | A 2% true shortfall in 6 of 30 teams: the score ranks a worse team below a normal one 82% of the time after one shift, 90% after a day, 99.7% after five days; attainment without excusals 68%, 70%, 84%; throughput 52% at every length. Over time, a team's score moves within ±2.0 points day to day and ±0.9 between five-day periods with nothing changed; its throughput ±11% and ±8%. |
| PM_T3 | `python perf_behaviors.py RUN_DIR CALIB.json PM_T3_behaviors.json` (the 18 C_ runs of CALIB) | same | [PM_T3_behaviors.json](evidence/outputs/PM_T3_behaviors.json) | Against jobs completed across behaviors: throughput 0.88 (same direction 16 of 16), work beyond plan 0.90 (14), pooled score 0.80 (10), attainment alone 0.90 but the wrong direction in 13 of 16. |
| KT | `python known_truth.py 10 5,10 18 KT_s6 --seed 20261026` and `KT_s7 --seed 20261027`, then `python perf_known_truth.py RUN_DIR PM_T6_known_truth.json KT_s6 KT_s7` | same | [PM_T6_known_truth.json](evidence/outputs/PM_T6_known_truth.json) | Teams made slow, fast, swapping, always or never working ahead (ten per group). The score ranks every slow and every swapping team below every unchanged one, and cannot tell fast, eager or idle teams from unchanged ones (69%, 49%, 41%). Efficiency finds pace (0% and 100%). Work beyond plan finds initiative (99% and 0%). Throughput finds none (14% to 57%). |
| PM_T5 | `python perf_levels.py runtime/sim/fleet.db PM_T5_levels.json` | same | [PM_T5_levels.json](evidence/outputs/PM_T5_levels.json) | 95.0% of planned minutes done, 4.2% excused, 0.8% missed without excuse. 84% of the loss is excused; 42% of excused minutes were caused by another team on the floor. Site: 99.2 pooled, 97.4 with its own teams' knock-on charged, 95.0 without excusals. |
| PM_T7 | `python perf_tree.py runtime/sim/fleet.db PM_T7_tree.json` | same | [PM_T7_tree.json](evidence/outputs/PM_T7_tree.json) | Output per available hour = load × share not excused × team execution × extra work, exactly. Week 2 against week 1: −6.1% = load −6.7%, excused +0.2%, team execution +0.7%, extra −0.2%. |
| PM_T8 | `python perf_bands.py runtime/sim/fleet.db PM_T8_bands.json` | same | [PM_T8_bands.json](evidence/outputs/PM_T8_bands.json) | Bands with nothing changed, by level and period: score ±3.5 (team, shift), ±1.9 (team, day), ±1.0 (team, five days), ±0.7 (five-team group, five days), ±0.3 (site, five days). |
| PM_T2 | `python perf_forecast.py runtime/sim/fleet.db PM_T2_forecast.json` | same | [PM_T2_forecast.json](evidence/outputs/PM_T2_forecast.json) | The shift-to-shift change in the fleet's forecast late days against that shift's missed minutes: correlation 0.26 over 23 pairs. Inconclusive. |

## 3. Repository sources cited (path:line at 5d1236c)
- **Engine:**
  - `focus/engine/max/optimizer/orchestrator.py:94-108` (`OBJECTIVE_FIELDS`, `key_levels`), `:153-169` (`make_priority`), `:232-267` (`evaluate`), `:278-299` (`better`), `:300-326` (`_run_repair`)
  - `focus/engine/max/optimizer/urgency.py:35-41`, `:43-89`, `:92-116`, `:119-131`
  - `focus/engine/max/optimizer/greedy.py:51`, `:106-118`, `:134-157`, `:194-202`, `:205-270`
  - `focus/engine/max/optimizer/repair.py:1-30`
  - `focus/engine/model.py:285-318`
- **Economics:**
  - `focus/economics/registry.py:65-108`
  - `focus/economics/evaluator.py:37-60`, `:63-96`, `:99-113`
  - `focus/economics/repository.py:142-152`, `:188-191`
- **Publication:**
  - `focus/publication/serializer.py:72-145`
  - `focus/publication/dispatch.py:178-192`
- **Contracts:**
  - `focus/contracts/planning.py:126`, `:534`, `:795-834`
  - `focus/domain.py:221`, `:257`
- **Performance:**
  - `focus/performance/lifecycle.py:652`, `:768-850`
  - `focus/performance/disruption.py:281-327`
  - `focus/performance/score.py:95-150`, `:183-236`, `:270`
  - `focus/performance/rollup.py:106-243`, `:432`, `:508`
- **Analytics:**
  - `focus/analytics/points.py:1-94`
  - `focus/analytics/progression.py:99-177`, `:211`
- **Web and explanations:**
  - `focus/web/serializers.py:1360`
  - `focus/explanations/pipeline.py:104-172`
- **Policy:** `config/policies/rebuild-v1.json` (`goals.obligation_days`:103, `duration.standard_effort_basis`:291, `due.*`:356-375, `economics.cap_policy`:585, `objective.order`:607); `config/policies/grading-demo-v1.json:8-29`
- **Docs:** `docs/execution/PRODUCTION_SCALE.md:80`, `:102-106`, `:161`; `docs/execution/SIMULATION.md`; `docs/execution/DURATION_METHOD.md:25-82`; `docs/execution/DECISIONS.md` (D-21 to D-25, OD list)

## 4. External literature
Gathered by web search on 2026-11-02 in this session's literature review. Claims are summarized from abstracts and publisher summaries, and the full texts were not re-read for this investigation. They are used only for general, well-established points.
- Smith's rule (WSPT) is optimal for a single machine; weighted tardiness is strongly NP-hard: course notes, https://imada.sdu.dk/u/march/Teaching/Fall2008/DM87/Slides/dm87-lec8-2x2.pdf
- Vepsalainen & Morton (1987), apparent tardiness cost rule, *Management Science*: https://ideas.repec.org/a/inm/ormnsc/v33y1987i8p1035-1047.html
- Luh & Hoitomt (1993), Lagrangian relaxation scheduling (marginal job costs): https://dynasched.github.io/uploads/luh91scheduling.pdf
- List-scheduling anomalies (Graham): https://en.wikipedia.org/wiki/List_scheduling
- Brânzei et al. (2002), cooperative-game allocation of project delay penalties: https://ideas.repec.org/a/spr/annopr/v109y2002i1p359-37410.1023-a1016372707256.html
- Forensic schedule analysis methods disagree on the same facts (Braimah 2013): https://www.mdpi.com/2075-5309/3/3/506
- UK ORR delay attribution review (2019), attribution cost and collaboration concerns: https://www.orr.gov.uk/sites/default/files/om/delay-attribution-review-2019-01-15.pdf
- EUROCONTROL CODA 2024, reactionary delay share: https://www.eurocontrol.int/publication/all-causes-delays-air-transport-europe-annual-2024
- Holmström & Milgrom (1991), multitask principal-agent: https://people.duke.edu/~qc2/BA532/1991%20JLEO%20Holmstrom%20Milgrom.pdf
- Kerr (1975), "On the folly of rewarding A, while hoping for B": https://appstate.edu/~steelekm/classes/psy5150/Documents/Kerr1975-95.pdf
- Bandiera, Barankay & Rasul (2005), relative incentives, *QJE*: https://doi.org/10.1093/qje/120.3.917
- Gneezy & Rustichini (2000), "A fine is a price": https://rady.ucsd.edu/_files/faculty-research/uri-gneezy/fine.pdf
- Earned schedule (Lipke): https://en.wikipedia.org/wiki/Earned_schedule
- Mollick & Rothbard, "Mandatory fun" (consent in workplace gamification): https://papers.ssrn.com/abstract=2277103

**Not found:** no peer-reviewed controlled study of WSJF or CD3, and no study of delay-dollar task metrics in aircraft final assembly.
