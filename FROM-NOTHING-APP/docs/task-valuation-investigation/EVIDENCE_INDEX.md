# Evidence index — task-valuation investigation

**Source revision:** `5d1236c8e5777b9075a652571707ae57bf5357ff` (`main`, 2026-11-02). No application code, tests, policies, schemas or UI were changed by this investigation.

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
