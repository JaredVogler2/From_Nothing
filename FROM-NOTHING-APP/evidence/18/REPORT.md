# Stage P18: Canonical effort scoring, gate points and progression

Status: **fixture_verified** under the DEMONSTRATION score policy. No official grade exists: there
is no approval receipt (OD-16), and personal grades are disabled.

## Changed paths

- `focus/performance/score.py`: the one formula authority on exact rationals.
  - **Components.** `components` builds B0/B/E/Cp/U/O from unit facts. Earned, relief and offplan
    units are disjoint. OOS is charged once per planned unit; offplan OOS is already in U.
  - **Formula.** `formula`: A = min(1, Cp/D), H = Cp/(Cp+U) or 0, base = 100(0.7A + 0.3H),
    final = clamp(base − 100·O/D, 0, 100), rounded half-up once (`round_half_up`), banded by
    `grade_for`.
  - **Pooling.** `pooled` sums exact components before division.
  - **Status semantics** (`status_and_score`, `score_policy_errors`):
    - NEUTRAL for D0 / no plan;
    - UNKNOWN for missing coverage, unknown disposition, unknown U/O, an open window, or a
      mixed or invalid policy;
    - DEMONSTRATION, or OFFICIAL only with `score.policy_approval_ref`.
  - **Services.** `evaluate_shift`, `evaluate_all`, `aggregate`, `canonical` (what every surface
    reads), `personal_grade_refusal` and `cli_evaluate`.
- `focus/performance/history.py`: series per scope, kind, goal set and policy. A revision is
  appended only when the evidence changes, with `supersedes`, and the head moves by
  compare-and-set. `as_known` replay and an `EvaluationEvidence` record carrying the exact unit
  facts.
- `focus/analytics/points.py`: the single gate-recognition rule. `publication.serializer` now
  delegates goal freezing to it:
  - base = effort/5 and bonus = base·35%·min(age/5, 1);
  - an actual gate is used unchanged, a baseline proxy gets +2 days once, an already-adjusted
    proxy is unchanged;
  - an absent gate is unavailable, a future gate has factor 0, and completion stops aging;
  - `unit_values` gives the per-unit frozen value.
- `focus/analytics/progression.py`: idempotent projection from persisted canonical evaluations
  and frozen goals.
  - **Streams.** All seven streams use dedupe keys `<base>#r<n>`, exact earning units and the
    evaluation plus verified-event lineage.
  - **XP.** XP is the frozen value of verified planned or approved units. Offplan output earns
    nothing, and OOS is not subtracted again.
  - **Corrections.** Reversing `correction_applied` events; badge revocations are appended
    revisions.
  - **Badges.** Recovery, Firebreak (≥3 previously blocked jobs, same graph), CommitmentKeeper,
    SteadyShift (3 consecutive ≥ 90), SkillSteward (≤ 2 admitted holders; explicitly not a
    certification) and MonthlyChampion (closed month, pooled score, ties by team id).
  - **Reporting.** `team_progress` (level = 1 + floor(xp/1000), progress, next threshold, no
    qualification effect) and `inflation_monitor` (awards per team per ISO week).
- `focus/contracts/evaluation.py`: `UnitFact`, `OffplanFact`, `OOSFact`, `EvaluationEvidence`,
  and `Evaluation` (kind, series key, goal-set hash, policy status, exact components, unrounded
  final, rounded score). `RecognitionEvent` and `BadgeAward` are revisioned chains.
- `config/policies/grading-demo-v1.json`: `score.policy_approval_ref` (open_decision),
  `badges.skill_steward_max_holders`, `xp.inflation_alert_awards_per_team_week`.
- `sql/migrations/evaluation_draft.sql`, `tools/evaluate_shift.py`.
- `tests/unit/test_score_facts.py` (18 tests), `tests/integration/test_progression.py` (7 tests).

## Commands and results

```
.venv/bin/python -m pytest tests -q                                                -> 323 passed (6:19)
.venv/bin/python -m pytest tests/unit/test_score_facts.py -q             -> 18 passed
.venv/bin/python -m pytest tests/integration/test_progression.py -q      -> 7 passed
python -m focus.cli evaluate --shift DEMO:2026-10-26:S1 --policy rebuild-v1 --as-of <point>   -> revisioned canonical evaluations
python tools/evaluate_shift.py --shift DEMO:2026-10-26:S1 --policy rebuild-v1 --as-of 2026-11-01T12:00:00Z --project
   -> 14 recognition events; CommitmentKeeper (PROD-A, PROD-B), SkillSteward (QA), MonthlyChampion 2026-10 (PROD-B); levels 1, no qualification effect
```

## Acceptance cases (S14 numeric examples asserted exactly)

| Case | Observed |
| --- | --- |
| A57 | B200/Cp100 gives 65.0. B200/Cp100/U100 gives 50.0. B200/Cp0 gives 0.0 (H = 0: no free 30 points). B200/Cp200 gives 100.0. One formula serves every surface; there is no UI arithmetic. |
| A58 | B200/E100/Cp100 gives 100.0 with B0 = 200 retained. B200/E200/Cp0 and no-plan B0 = 0 give NEUTRAL (score and grade null, not 100 or F). Unknown disposition, uncertified or missing coverage, unknown U or unclassified OOS give UNKNOWN null. |
| A59 | D400/Cp200 plus D100/Cp100 pools to 72.0, not the 82.5 average; unit-level pooling is identical. For the three real 2026-10-26 goals, the factory aggregate equals the S14 formula over the summed components and differs from the average. A one-goal team pool equals its goal, and a replay is the same canonical record. `canonical()` returns the persisted revision ids. |
| A60 | B200/Cp200 with 200 affected planned OOS minutes gives O = 20, base 100, final 90.0, and Cp stays 200. Unapproved offplan OOS (U100) lowers H only; O stays 20, with no second charge. XP for the 200 minutes is 40, not reduced by O. Economics (P12) has no OOS term. |
| A61 | Actual gate at ages 0/2/3/5/7 gives raw 0, 0.4, 0.6, 1, 1. The baseline proxy gets +2 days once; an already-adjusted proxy is unchanged. A future gate has factor 0 with no negative offset; day 0 is real; an absent gate is UNAVAILABLE (base only). Completion stops aging. An unknown basis is refused. |
| A62 | Published goals freeze the gate context at valuation 2026-10-26. Verified completion earns once at that frozen value (effort/5; gate ahead, so no bonus). A refresh on 2026-11-05, where the gate would have raw 1, adds nothing (XP stays 48). A late source void of A1's completion appends evaluation revision 2 (Cp 0) and a −48 `correction_applied` event. The original award and the prior evaluation are retained, the CommitmentKeeper badge gets an appended revocation, and a replay never reverses twice. |
| A63 | Repeated projections append nothing and repeat no badge. The month transition (2026-11-01) awards MonthlyChampion 2026-10 exactly once, to the best pooled team, naming its member evaluations. Every completion award names the canonical evaluation and a verified lifecycle event. The SkillSteward statement says it is not a certification. Eligibility, validation input, dispatch and the engine never read XP (`qualification_effect: none`). The inflation monitor reports no alert. |

## Fixture versus live

Scores are DEMONSTRATION. An OFFICIAL status needs the owner's approval receipt
(`score.policy_approval_ref`, OD-16), and personal grades need their own policy. Evaluation and
recognition storage is the fixture store; `evaluation_draft.sql` is drafted, not installed.
Surfaces (iOS, Classic, Arena, API, MCP) arrive in P19–P21 and read `canonical()` and history
only.

## Remaining gaps

- Install `evaluation_draft.sql` (P23).
- Wire `/performance`, `/performance/history`, `/recognition` and `/progression` plus the 17 views
  to `canonical()` and the progression projections (P19–P21).
- A real approval receipt for OFFICIAL grading (OD-16).
