# Prompt 00 — FOCUS Gridiron /goal and repo reconnaissance

```text
/goal Build FOCUS Gridiron, an optional high-quality 11v11 simulated American-football league integrated INSIDE the existing FOCUS application. Every production team is a football franchise; its players are exclusively that team's eligible, opted-in mechanics. Each week the system selects the strongest valid starting 11 for offense and the strongest 11 for defense by deterministic position-fit optimization; where appropriate, the same person may play both ways but not two positions simultaneously on one side. The players' ratings derive from auditable, quality-first, fair, opportunity-adjusted manufacturing performance, not arbitrary hardcoded scores in live mode. Never borrow a rival's workers or manufacture fictional live employees. A team with fewer than 11 eligible opt-in people is ROSTER_INCOMPLETE and cannot play a standard live 11v11 game. Public/demo mode is entirely synthetic and may invent 11+ fake teammates per fictional production team.

You MUST develop in steps, leaving tests passing and commits after each deliverable. Read this prompt and the four docs in docs/gridiron/, plus the existing FOCUS integration, SQL, readiness and execution docs. You are working in FROM-NOTHING-APP/. Preserve existing manufacturing FOCUS scheduling, dispatch and independent plan validator unchanged. Start by inventorying the actual code, existing tests, backend, API registry, UI conventions, worker queue, SQL draft migrations, fixture store and settings. Revise proposed paths from the docs if needed, explaining differences.

Build no production app code in this reconnaissance step. Write docs/gridiron/IMPLEMENTATION_STATUS.md with code-specific integration points, dependencies, risks, actual paths and results of current baseline tests. Document source limitations; 'readiness not probed' must not become empty success. Record a phase gate checklist. Do not mutate the existing execution STATUS.json until the repository's own status workflow supports the new module.

Critical: No actual company employee records, real internal SQL mappings, commercial data or credentials to personal GitHub, public AI agents, public logs, external services or the public Vercel demo. Never treat a mechanic's timeclock edits, kiosk punch time, LWOP, PTO, absence or injury reporting as a penalty. This is opt-in recreation separate from formal appraisal, discipline, compensation, staffing and qualification.

Tests: run established Python 3.12 setup and pytest baseline; document results and any environment blockers, without inventing passing tests. Output exact integration plan, touched files, risk matrix, acceptance tests, and next prompt readiness.
```

## Exit gate
- Current architecture/code verified, no assumptions that app is React.
- Existing test suite baseline measured.
- Scope/privacy/feature flags and starter selection rules explicitly carried into implementation plan.
- No real employee data or live DB operations.
