# Prompt 03 — auditable weekly football ratings and positional fit

```text
Implement pure RatingEngine that consumes only approved game metrics, source receipts, frozen week and immutable sports policy. For each eligible opted-in person compute 8 football attributes (SPEED,HANDS,AWARENESS,POWER,TEAMWORK,DISCIPLINE,VERSATILITY,PLAYMAKING), normalized bounded 0..100 with coverage/confidence and explicit UNKNOWN. Proposed overall weights Quality 30%, Efficiency 25%, Flow 20%, Teamwork 15%, Data Accuracy 10%, not official; these must be policy-versioned and configurable. Missing signals must never be replaced by zeros or pretend sample; if insufficient, issue PROVISIONAL/UNKNOWN with reasons and don't use unsupported score for starter selection unless a separately explicit neutral/provisional policy permits it.

Calibrate per position (QB,RB,WR,TE,OL,DL,LB,DB,K,P). Define a documented attribute→position matrix with interpretable weights and no automatically imposed job-title stereotypes. Example QB awareness, teamwork, reliable decision/execution; RB pace/execution; LB validated quality detection. Employees may select preferred football position, but starter slot eligibility and best performance remain driven by objective, prepublished position-fit rules and source confidence.

Use shrinkage for small samples, finite bounds, confidence measure, worst-case tests and caps on weekly movement (versioned). Freeze one rating run per week with EvidenceCutoffUtc, policy SHA, SourceDigestSha, SnapshotSha, revision & replay hash, no editing frozen prior runs. Corrections append rating revision; no live employee score publishing until approvals. Build explicit score explanations: contributing verified metric categories, not underlying personal labor records.

Tests: weights sum and directionality, all 0..100, null propagation, no leave or clock-edit deductions, quality first, equal-performance invariance, frozen revision digest determinism, reproducible fixture scoring, no hidden RNG in rating calculations, position fit symmetry/tie stability, threshold stress and adversarial outlier handling. Expose pure services for lineup selection in next prompt.
```
