# FOCUS Gridiron — implementation handoff

**Status: design and agent build instructions only.** This directory is deliberately a documentation-first proposal, not deployed code, an approved individual performance system, or proof that live employee feeds exist.

## Mission
Deliver an optional football league inside the existing FOCUS Flask/JavaScript application. Real, verified and appropriately normalized manufacturing performance can inform player attributes **only after approval in the employer-controlled environment**. The public/Vercel build is permanently synthetic-only. Football simulation never changes manufacturing assignments, qualifications, staffing, standards, accepted plans, canonical grades, or HR records.

## Start here
1. Read [ARCHITECTURE_AND_GAME_DESIGN.md](ARCHITECTURE_AND_GAME_DESIGN.md).
2. Read [SQL_SERVER_SCHEMA.md](SQL_SERVER_SCHEMA.md) alongside the review-only [DDL draft](../../sql/migrations/gridiron_001_draft.sql).
3. Read [SOURCE_MAPPING_AND_GOVERNANCE.md](SOURCE_MAPPING_AND_GOVERNANCE.md) for work-side source mapping and privacy gates.
4. Execute the numbered prompts in [prompts/README.md](prompts/README.md), in order. Each stage has its own acceptance gates.
5. Existing sources of truth: `../integration/WORKPLACE_PORTING_GUIDE.md`, `../integration/SOURCE_REPLACEMENT_CROSSWALK.md`, `../execution/SQL_SCHEMA_LEDGER.md`, `../execution/SOURCE_READINESS.md`, `../execution/STATUS.json`.

## Grounded repository findings (reviewed 2026-10-29)
- App root: `FROM-NOTHING-APP/`; Python **3.12**, Flask, Jinja, vanilla ES modules; **not React**. `app.py` exports WSGI `app`.
- Server: `focus/web/app.py`, `focus/web/routes.py`, `focus/web/operations.py`; API/CSRF/grants/MCP share a registry.
- UI: `focus/web/views.py`, `focus/web/static/js/views/registry.js`, `workspace.js`, `views/panels.js`, `views/performance.js`; reuse established iOS and Classic layouts.
- Data: `focus/data/sql.py` allowlisted parameterized SQL, `focus/data/repositories.py`, `focus/data/sql_store.py`, `focus/runtime.py`.
- Evidence and canonical performance: `focus/performance/actuals.py`, `lifecycle.py`, `focus/contracts/evaluation.py`, `sql/migrations/evaluation_draft.sql`.
- `Arena & Recognition` is an existing supplementary view; Gridiron must complement it rather than redefine its canonical scores. Existing `FOCUS_FN_*` SQL migrations are drafts and must not be assumed installed.
- Source contracts `focus/data/datasets.py` and reviewed `docs/integration/SOURCE_REPLACEMENT_CROSSWALK.md` enumerate 28 source projections + 16 supplemental streams. The live readiness of many feeds is explicitly NOT_PROBED.
- Hosting targets approved Posit Connect / Tanzu; public Vercel is fixture-only.

## Functional boundaries
**Source evidence → approved metric computation → immutable weekly rating snapshot → immutable seeded football simulation → scoped views.** Rating snapshots are separate from official team-shift FOCUS evaluations. A null/unavailable measurement is never represented as zero. A high manufacturing rating increases likelihood of a successful football play; it does not predetermine a touchdown, tackle or interception.

## Repository deliverables
- This overview and integration architecture.
- SQL Server table/column/grain/type/index/retention specification and **unexecuted** T-SQL draft.
- Read-only producer mapping contract for an approved internal agent.
- Numbered staged coding prompts with explicit tests, exit gates, deployment and observability.

## Global safety rails
- Never send internal table names not already in this repo, production SQL, people/HR records, actual prices, company-specific source mappings or credentials to personal GitHub or public agents. Keep all filled mapping manifests company-only.
- Do **not** use LWOP, PTO, absence, kiosk punch timing, injury reporting, reporting frequency, or non-verified allegations to penalize an employee. They can only determine valid exposure windows, eligibility or source-data completeness as authorized. Do not infer misconduct from manual time corrections.
- Respect organizational consultation, legal/HR/labor/safety/security approval, consent and opt-out. Default individual metrics to private. Aggregate team games must not enable re-identification of nonparticipants.
- No employee appraisal/discipline/compensation impact; no substitution for verified independent QA, compliance or scheduling.
- Install no live SQL migration without a separate approved change window, operator identity, rollback and backup.
- Default `FOCUS_GRIDIRON_ENABLED=false`, `FOCUS_GRIDIRON_LIVE_RATINGS_ENABLED=false`, `FOCUS_GRIDIRON_PUBLIC_LEADERBOARDS_ENABLED=false` (names are proposed additions, not existing config).
- Maintain fake fixtures and smoke tests; schema design should be reviewed by a DBA prior to deployment.

## Recommended first command for coding agent
Read `docs/gridiron/prompts/00_GOAL_AND_RECONNAISSANCE.md` and execute it; then advance to 01 only after satisfying its gates.


## Definitive production-team starting-lineup rule (2026-10-29)
Every franchise represents one **actual production team**, never an arbitrary mixed roster or fantasy draft. Each weekly starter must be an opted-in, eligible member of that production team at the week-freeze cutoff. Select the **best eligible positional starting lineup for that team's teammates**, not merely the top 11 raw overall scores. Rank/assign by a deterministic constrained maximum-weight matching using overall and position-fit scores, with reproducible tie-breaks. Position specialization is allowed. Bench, substitute and special-teams roles may recognize more teammates, but must never supplant that week's published starters without a versioned revision. When fewer than 11 team members meet eligibility, mark the fixture as `ROSTER_INCOMPLETE` and don't present made-up or cross-team players as live starters. Fictional game rosters remain allowed in explicitly synthetic demo mode only. See architecture, schema and prompt 04.
