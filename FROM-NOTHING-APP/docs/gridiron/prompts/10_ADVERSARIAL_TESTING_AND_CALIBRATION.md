# Prompt 10 — adversarial verification, fairness, football plausibility and independent review

```text
Perform an adversarial review of the whole completed Gridiron implementation. Do NOT assume previous stages were correct. Start by reading all contracts/docs and generating a traceability matrix from source receipt to metric to attribute to position-fit to frozen starting XI to simulated play to score/box score to API/DOM. Re-run baseline FOCUS full tests and all new suites, reporting actual failures without claiming unrun tests pass.

Independent verify with a separate implementation or brute force for small cases that the startup lineup truly maximizes the position-adjusted assignment among eligible same-production-team colleagues, and every selected person is consented, roster-valid, current and from the correct team. Test adversarial: one superstar at QB but severe OL weakness; two employees tied; <11 teammates; 12/20/50 teammates; two-way fatigue; midseason team changes; opt-out after week freeze; late revision; external player forged via input; duplicates in offense; missing defensive slot; cross-tenant injection; fake signature/seed; bench substitution; team ID mapped to two internal departments; small cohort privacy leak. Public demo must remain fabricated and no actual employee should be in public Vercel or GitHub.

Validate fairness with synthetic counterfactuals:
- identical quality+work but different approved absence/LWOP -> equivalent ratings;
- complex vs easy job mix -> no unadjusted advantage;
- two-person crew vs single -> no doubled credit;
- truthful quality find and safety report never reduce rating;
- corrected punch and kiosk login differences alone never penalize;
- incomplete/stale/failed feed -> UNKNOWN/INCOMPLETE, not zero;
- plan following approved override not punished;
- unknown causal contribution not invented as flight improvement;
- no incentive to rush work, underreport safety or avoid a legitimate job.
Check missing approvals and opt-in/suppression.

Evaluate football game engine using many deterministic seeds: score distribution, win probability conditional on balanced vs imbalanced teams, passing/rushing ratio, completion %, scoring rates, sacks/turnovers, ties, overtime, fatigue, games terminating, score-event equality and reconstructing box scores exactly from play traces. Use reasoned range targets from the chosen football abstraction, not a claim that the sim perfectly matches the NFL. Test display replay hash equals game data, SSO scoped authorization and MCP parity, API no writes on GET, worker restart/idempotency/corrections, SQL migrations dry-run and least privilege, base prefix, mobile keyboard/reduced motion, speed/performance, team/employee info separation.

Create docs/gridiron/TEST_REPORT.md with PASS/FAIL/NOT_RUN per gate, exact commands, summary, unresolved risks and operational approvals still needed. Fix critical issues before next prompt. No official live claims if source or approval is NOT_PROBED. Maintain a clear revision history and keep changes confined to Grid functionality.
```
