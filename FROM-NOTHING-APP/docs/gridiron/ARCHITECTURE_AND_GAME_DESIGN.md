# FOCUS Gridiron — architecture, gameplay, scoring and acceptance

## 1. Product definition
FOCUS Gridiron is an optional, integrated, mobile-friendly football league. **Every opted-in mechanic may have a football avatar.** Real production teams may field fantasy football squads; teams with fewer opted-in players receive explicitly labeled neutral fictional fill-ins, never synthetic workers masquerading as real people. Avoid workplace participation pressure. Weekly games can be watched in minutes with optional full play-by-play and box scores. Default to automated games, not an interactive sports game requiring employees to act during work.

Suggested UI flows:
- My Player: alias, preferred position, 0–100 overall/position ratings, confidence, week-over-week movement, five attribute cards, explanations and privacy controls.
- League: teams, standings, weekly matchups, last week's 2–3 highlighted plays, fixtures, postseason and championship.
- Game Center: football field with 2D animated paths/players, scoreboard and controls, short replay / text commentary / box score, speed selector and accessibility reduced-motion mode.
- Captain/administration: roster completeness, neutral-fillins count, proof of eligible evidence, source freshness, governance states, reporting and corrections. Do not show sensitive raw personnel details.
- Admin rulebook: policy version, weights, normalization, minimum sample and freeze logic; transparent no-payroll scope.
- Optional fantasy mode after pilot; real production teams first, employee transfers freeze according to approved week-close membership.

## 2. Integration with existing FOCUS
Backend: `focus/gridiron/{contracts,policy,metrics,ratings,roster,simulator,repository,worker,serializers}.py` (proposed). Integrate through `focus/runtime.py` DI and existing `focus/web/operations.py` registry; `focus/web/routes.py` already dynamically registers operations; avoid bypassing permission and CSRF mechanisms. Read-only projections should use the same API envelope/status `ready|unknown|blocked|stale|empty`. POST commands are queued through the existing durable worker pattern with idempotency keys; GET never computes scores, ingests source evidence or advances league week. APIs must be per caller's server-owned scopes (not trust URL ids).

Frontend: add `gridiron` supplementary entry to `focus/web/views.py` and `focus/web/static/js/views/registry.js`; import a dedicated `focus/web/static/js/views/gridiron.js` from `workspace.js`, using existing DOM helpers in `core.js` and `common.js` plus stylesheet(s). Works in both iOS/Classic, nested Posit paths and mobile. Do not replace existing `arena` view. A 2D SVG/Canvas field with low-cost animation is a first-class complete product; no React/Three.js dependency required. If additional packages are proposed, justify bundle size, server compatibility and test coverage.

Storage: model design is in SQL_SERVER_SCHEMA.md and review-only `sql/migrations/gridiron_001_draft.sql`. Fixture store should support the same immutable snapshot semantics with no live SQL dependency. All new tables are isolated `WEB_APP.FOCUS_FN_Gridiron*`; no changes to existing scheduling tables, no write to source views. Connectors and table allowlist additions are strictly gated.

## 3. Football simulation (v1 deterministic seeded engine)
Simulate recognizable American football—not a score-generation shortcut:
- 4 quarters, possession, play clock abstraction, down/distance, yard line (0..100), first downs, touchdowns, PAT/2-point, field goals, punts, turnovers, safeties, timeouts and end-game.
- Offense uses QB/RB/WR/TE/OL positions, defense DL/LB/DB. Deterministic roster selection given frozen snapshot; no illegal duplicate player on a single on-field side. Two-way players across offensive and defensive lineups can be allowed by explicit policy, with fatigue.
- Selection AI handles run/pass/kick/punt based on down/distance/clock/score, not hidden game-winner manipulation.
- A play's outcome is sampled via seeded RNG from position ratings, matchups, fatigue, field position and stochastic noise. Provide explicit formulas and bounded probabilities; nonzero upset odds. Rating changes must cause directionally measurable effects in Monte Carlo tests, not automatic football stat conversion.
- Game engine is a pure Python function of immutable roster+ratings+policy+seed, produces play trace, clock transitions, box score, and replay hash. Seed/engine_version are persisted. Backend simulates once in a durable worker; UI only replays.
- Define safety bounds (plays max per game, valid 0..100 yards, overtime bounded, no negative time) and invariant tests: possessions, sum scoring events = scoreboard, box scores = plays, replay deterministic across processes.

## 4. Performance-to-rating design (proposals, NOT approved workplace grading)
**Do not reuse an official FOCUS team score as a personal score.** Existing B0/B/E/Cp/U/O are team-shift evaluation units; attribution to individuals is a distinct evidence problem. Build separate, policy-versioned *game ratings* in 0..100, with null/unavailable status when missing.

Suggested hypothetical overall weights, pending pilot:
- Quality/verified first-pass execution 30%.
- Complexity/crew-adjusted labor efficiency 25%.
- Validated impact on feasible aircraft progress 20%.
- Verified support, safe handoffs and teamwork 15%.
- Documentation quality/clocking correctness as source integrity 10%.
Weights sum to 100. Do not use raw clock punch compliance or number of jobs alone.

Attributes: SPEED (adjusted pace), HANDS (execution / quality), AWARENESS (verified defect identification and constraints), POWER (complex work + sustained reliability), TEAMWORK, DISCIPLINE (data reliability not attendance), VERSATILITY (approved training breadth), PLAYMAKING (validated flow impact). Define position fit from these attributes in a JSON policy with explainable mappings. All positions open; employee preference can override best-fit as a game preference.

Define eligible exposure from hours actually available for authorized work; approved absence is removed from the denominator rather than given a bad score. Missing live feeds give UNKNOWN or provisional rating with coverage. Never set NULL=0. Minimum evidence requirements: e.g. 3 different eligible shifts AND 120 audited person-minutes (hypothetical configurable), plus reliability checks. Use shrinkage, e.g. posterior_estimate = (n * normalized_measure + k * neutral_prio​r)/(n+k), and limit week-to-week OVR deltas (e.g. 5) until statistically stable. Version all parameters.

**Labor standard caution:** distinguish elapsed critical-flow standard from standard person-minutes. Exact flow baseline comes from `flow_budget` keyed (line_number,job_number) in existing reference contract; valid actual person effort needs attributed corrected intervals, legitimate breaks/exclusions, accurate crew attribution and correction review; rework duration P75 is *not* an individual labor efficiency benchmark. Normalize expected work by job family/complexity, aircraft variant, crew, handoffs and legitimate externally caused delays; use comparable cohorts, positive expected effort, explicit low sample uncertainty. Prevent a work allocator from making one person look better by assigning only easy work.

**Quality:** reward validated reporting of defects and hazards, but do not reward creation of rework or quantity of non-substantiated findings; never discourage reporting. Use source verification/review and separate reporter, defect-origin and resolver roles; don't assign causation by mere job association.

**Plan / causal impact:** plan alignment only when an accepted executable assignment (or explicit approved override) applies to the mechanic, not when a FORECAST recommends a job. For schedule impact distinguish unlocking successors from reducing integrated fleet makespan or delay; evaluate marginal change only in matched counterfactual snapshots with shared crew, constraints, and stable economics authority; absent authority, display `impact_unavailable`. A correlation with aircraft flow is not personal causation. Never use modeled commercial liability to rank employees.

**Record integrity:** count valid corrected manual punches as proper corrections, not punishment. Clock kiosk/attendance data are never converted to morality, reliability, or disciplinary labels. No LWOP, PTO or medical details in game DB.

## 5. Rating lifecycle
1. A qualified internal approved adapter publishes source evidence receipts with as-of cutoff, granularity, completeness and source version; receipt can be ROWS/CERTIFIED_EMPTY/NOT_PROBED/EMPTY_UNCERTIFIED/FAILED.
2. Compute eligible participation and effective roster for the season/week only from authoritative effective-dated memberships; reject ambiguous dates/identity.
3. Normalize verified metrics and exposures, preserve exclusions, uncertainty and source types. Quality gate may block a component or full rating.
4. Freeze a `GridironRatingRun` after declared week end and final source watermark; persist policy SHA, source receipt digest, raw evidence hash, seed and revision. PENDING/UNKNOWN don't get a zero.
5. Generate lineups and games from the frozen published rating run; never query changing labor data during a football game.
6. On late correction, append a new rating revision; the old game stays historical. A reviewed resimulation is a **new game revision with supersedes link**, never a silent historical rewrite.
7. Optional year/season championship from officialized games in this game-only domain (not HR official).

## 6. Privacy and anti-gaming
- Default opt-in; pseudonymous public alias, private individual performance details; admin see only aggregates or authoritatively granted reviews. `FOCUS_GRANTS_JSON` scope-before-fetch on every endpoint; mitigate small cohort re-identification; do not leak aliases via standings when opt-out.
- Distinguish analytics workforce identity from sports alias. No names, native person keys, sensitive absence reasons, payroll rates, discipline data or health data in the proposed Grid DB. Keep ID mapping in an internal secured adapter; salted/HMAC identity hashes, secret managed in workplace only.
- Require owner-signed rule versions, sample threshold and shift coverage; log ratings corrections + conflicts; users may request correction of factual game inputs.
- No incentives for unsafe acceleration, underreporting defects or delaying an inspection. High pace score never compensates for missing QA validation or safety holds.
- Leaderboards need explicit governance approval; team standings are fine with suppression for small cohorts. Football player ratings are for voluntary entertainment, not employment appraisal.
- Team/shift schedule and workload distribution can be unfair; stratify drift/fairness by eligible job type, assignment mix and availability without revealing protected data in UI.
- Separate platform demo and live flags; prohibit real data in public Vercel. A live-ready gate cannot be fulfilled by invented fixture data.

## 7. Measurable gates
- Every simulated play meets invariants, replay same seeded input byte-for-byte; 10k games finish within agreed budget, no infinite loops.
- Sample fairness: identical eligible work + differing approved absence yields same rating; complex versus easy job mix parity after normalization; unknown measurements never become 0; clock correction alone not a penalty; validated quality reporting cannot lower a rating.
- Authorization: individual results limited to own person scope and authorized administrators; team aggregated outputs meet size limits; arbitrary query params, MCP and deep links cannot bypass scope.
- SQL migration dry-run only in public build, reviewed constraints, indexes, immutable snapshots and tenant isolation; future approvals mandatory.
- UI passes mobile 375px, iOS/Classic, keyboard, reduced-motion and Posit base-prefix tests.
- No scheduler or existing performance regressions; full `python -m pytest tests -q` remains green. New feature default disabled and existing operational functionality unchanged.
