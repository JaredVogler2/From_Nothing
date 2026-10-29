# FOCUS Gridiron: private source replacement, metric provenance and governance

**Target:** internal work-only integration runbook. This public document contains canonical *reference contracts and desired semantics only*, not the names/locations of any new proprietary work database objects. Do not fill sensitive mapping fields here.

## Read first
- `docs/integration/WORKPLACE_PORTING_GUIDE.md` and `SOURCE_REPLACEMENT_CROSSWALK.md`.
- `focus/data/datasets.py`, `queries/source_inputs/*.sql`, `focus/data/sql.py`.
- `focus/performance/actuals.py`, `lifecycle.py`, `focus/contracts/evaluation.py`.
- `config/policies/grading-demo-v1.json` and `sql/migrations/evaluation_draft.sql`.
- `docs/execution/SOURCE_READINESS.md` and `SQL_SCHEMA_LEDGER.md`.

## Work-side data mapping template: keep filled copy private

For each requested metric create a company-controlled record with:
`metric_code`, `data_owner`, `approval_ref`, `source_system_class`, `internal_database/schema/table_or_view`, `source_query_reference_and_SHA`, `row_grain`, `natural_key`, `revision_key`, `effective_timestamp`, `observed_timestamp`, `source_timezone/DST_policy`, `row_count`, `coverage_window`, `completeness_receipt`, `status_mapping`, `person_identity_resolution`, `team_assignment_authority`, `job_identity_resolution`, `contributing_crew_semantics`, `valid_corrections`, `inclusions`, `exclusions`, `confidence`, `test_method`, `ingestion_cutoff`, `audit_retention`, `review_signoff`.

Never persist those filled columns in personal GitHub. Approvals must explicitly cover **secondary use of individual employee production records for optional entertainment**, not just operational access.

## Metric/proposed producer matrix (do not assume any feed is live)

| Code | Business meaning / grain | Candidate repo contract | Proof / common error |
| --- | --- | --- | --- |
| LABOR_EXPECTED | Expected person-minutes for a precisely identified job-phase and authorized crew | `flow_budget` | Distinguish CRITICAL_FLOW elapsed minutes from person-effort standard; require crew semantics and valid standard |
| LABOR_OBSERVED | Corrected actual person work intervals by job/person, effective timestamps, revision | `clock_observations`, `actual_labor`, `labor_shift` | `actual_labor` is aggregate; corrected overlaps/voids and shared labor require genuine interval producer |
| QUALITY_FIRST_PASS | Validated accepted job quality outcome, not rework volume | `defect_evidence`, `rework_classification` | Distinguish reporter/originator/fixer; do not punish reporting |
| QUALITY_FIND | Unique independently verified issue, person role, detection window | `defect_evidence` | Dedup and validate; exclude noncomparable opportunities; avoid quotas |
| PLAN_ADHERENCE | Verified person's work followed an applicable *accepted* assignment or approved override | `FOCUS_FN_PublishedPlan` / `frozen_goal`, `ActualInterval`, `LifecycleEvent` | Forecast is not a directive; team aggregate is not an individual measure |
| SUCCESSOR_UNLOCK | Release of successors after approved verified job completion | `pnet_dag`, `neo_graph_rework`, plan and verified actuals | AND-dependencies, rework and constrained resources mean unlock ≠ guaranteed schedule benefit |
| FLOW_MARGINAL | Matched constrained counterfactual incremental aircraft-flow effect | optimizer + independent validator + accepted snapshot | Never assert employee causality from correlations; UNKNOWN until authorized stable method |
| CREW_SUPPORT | Shared labor contribution, handoff, mentorship or skill-sharing event | verified `ActualInterval` and approved new producer | Evidence of individual help; split team job work correctly |
| WORK_SELECTION | Approved task/priority when person worked and local override | accepted schedule and accepted dispatch checks | Do not punish assigned work or unsupported source feed |
| RECORD_ACCURACY | Accurate work record with valid edits/review | corrected clock intervals and audit trail | A correction is positive data stewardship, not guilt; never count raw edit frequency as negative |
| ELIGIBLE_EXPOSURE | Participation denominator (eligible shift/minute range) | `attendance_shift`, `availability_exceptions`, approved roster | No specific leave codes, LWOP, PTO, injury or absence reasons in game tables |
| ROSTER_MEMBERSHIP | Person's *actual production-team* effective-dated membership | `person_team_map`, `shift_roster`, `people_directory` | Historical inferred team is forecast-only; no mixed teams; require explicit identity mapping and consent |
| CERT_SKILL_BREADTH | Approved effective certifications as of cutoff | `mechanic_certs`, `soi_cert_requirements`, requirement census | Eligibility matrix is diagnostic, not authority; certs never unlocked by game achievements |

## Source identity and time
- Existing source joins must use canonical `JobKey` (site/program/line/SOI/revision/operation/effectivity), never mere job-number equality.
- Resolve native-person identity in internal identity vault/service; issue random `GridironPersonId` and per-scope HMAC token for game tables. No plain badge/BEMS/native key in public artifacts or Grid payloads.
- Resolve production team membership by authoritative effective roster at the week-end cutoff; when unresolved or multiple active teams, exclude from starter consideration and surface `TEAM_MEMBERSHIP_UNVERIFIED`.
- One immutable evidence cutoff and per-feed watermark; no future events. `ROWS`, `CERTIFIED_EMPTY`, `EMPTY_UNCERTIFIED`, `NOT_PROBED`, `FAILED` have precise distinct meanings. `NOT_PROBED` is not an empty roster.
- Use physical UTC effective instants; local shift/date/week calendar conversion is pinned by explicit timezone/DST policy. Never infer timezone from SQL Server clock.
- For multiple mechanics on the same job, attribute measured person effort only from verified intervals; no awarding full job labor gain to every collaborator. Quality and unlock achievements require evidence-linked allocation.

## Starting lineup: frozen work-team proof
At week freeze, the internal source adapter must produce a **team membership receipt** for every active athlete: (game-only person ID, game-only TeamId, effective window, source-run hash, consent state, score-coverage state). Only actual production-team teammates are candidates. The rating service selects an 11-person offensive starting unit and an 11-person defensive unit from the **highest-performing eligible people for each position**, via maximum-weight matching over position fit and score. One person cannot fill two simultaneous positions on the same side. Cross-side two-way participation is permitted with policy-controlled fatigue; no mixed production teams or fantasy drafts. Special teams/substitutes drawn from same roster. Freeze lineups with match & rating run; changes append revision and require reason.

**Minimum roster:** <11 eligible opted-in members => `ROSTER_INCOMPLETE`, do not simulate a standard 11v11 live game and do not insert fictional outsiders. Only the public fictional demo may populate enough invented *same-team* employees to field eleven. Document how an organization could pilot with simulated data without live participation.

## Approval and fairness checks before live use
1. Signed purpose/authorization: privacy, data use, retention, employee notice, voluntary enrollment/withdrawal, labor relations and relevant company works policies.
2. Safety/Quality: never reward unsafe rushing, discourage quality reporting, reward unverified rework counts, penalize approved absences, or interfere with operational assignments.
3. Minimum cohorts and uncertainty: at least policy-declared sufficient sample, shrinkage/caps/outlier handling, robust comparisons by comparable work type, team, shift and complexity.
4. Shadow evaluation: compare equal-performance employees with uneven assignment mix/leave and test for unwarranted score gaps. Examine privacy leakage through small cohorts and week-over-week leaderboard trends.
5. Data minimization: raw evidence stays in approved source; Grid snapshots only derived metrics, hashes, confidence, null status and aliases. All personally attributed results default to own view only.
6. No employment decisions: separate performance entertainment from official appraisal, pay, discipline, staffing, qualification. Existing FOCUS rules remain authoritative.
7. DB: migration preview, DBA signoff, read/write least privilege, backups, retention, data-erasure handling, auditing; no public internet exposure of live records.
8. Production pilot is allowed only after all source readers return certified coverage, validated ownership and policy approval. Missing feeds keep affected attributes unknown; if not enough usable information, no live ratings/game.

## Internal acceptance examples
- Two people with identical eligible work but different approved leave produce same performance rating; fewer work minutes only affect confidence/sample eligibility.
- Valid manual clock correction changes the final actual interval, not an automatic disciplinary deduction.
- Real quality issue reporter is not punished when defect leads to rework.
- One team member moves teams mid-season: correctly assigned by frozen effective window; past match history unchanged.
- An employee cannot be nominated on another production team's lineup by any parameter or client action.
- Among same-team eligible people, algorithm chooses highest total position-adjusted 11-person assignment; a top OVR QB candidate need not be an optimal OL.
- No roster with fewer than 11 eligible real participants is presented as a valid live 11v11 football game.
- A user without company authority cannot fetch other mechanics' private metric breakdown, regardless of REST/MCP or direct URL.
- Every late source revision generates new evidence/rating/game revision, not a rewrite or hidden re-grade.

## Internal handoff prompt
```text
/goal Integrate FOCUS Gridiron into the company's approved deployment using the PUBLIC reference contracts and synthetic implementation already in From_Nothing. Read docs/gridiron/{README.md,ARCHITECTURE_AND_GAME_DESIGN.md,SQL_SERVER_SCHEMA.md,SOURCE_MAPPING_AND_GOVERNANCE.md} and the existing docs/integration guides. Do not send actual personnel, SQL records/queries, table identities, credentials, employee HR information or filled mappings to GitHub or outside company-controlled systems. Privately map and validate source column grains, coverage, effective dated team membership and opt-in, verified labor/quality attribution, and source timestamps with respective owners. Do not treat aggregate team FOCUS scores as personal ratings, and never use PTO/LWOP, injury/absence reasons, kiosk punches or correction counts to penalize workers. Live football rosters consist exclusively of eligible opted-in members of each actual production team; the top position-fit performers start and the complete lineup is frozen. With <11 eligible teammates, withhold live 11v11 simulation. Preserve missing as UNKNOWN, never zero. Run read-only analysis, privacy/fairness/security tests and approvals before proposing any production migration or score publication. Operate fixture-only until all release gates pass.
```
