# Execute FOCUS Gridiron prompts in order

**Main kickoff:** [00_GOAL_AND_RECONNAISSANCE.md](00_GOAL_AND_RECONNAISSANCE.md). Read `../README.md`, `../ARCHITECTURE_AND_GAME_DESIGN.md`, `../SQL_SERVER_SCHEMA.md`, `../SOURCE_MAPPING_AND_GOVERNANCE.md` first.

| Stage | Prompt file | Deliverable |
|---|---|---|
| 00 | [00_GOAL_AND_RECONNAISSANCE.md](00_GOAL_AND_RECONNAISSANCE.md) | Verified codebase paths, baseline tests, implementation plan |
| 01 | [01_FOUNDATION_AND_SYNTHETIC_DATA.md](01_FOUNDATION_AND_SYNTHETIC_DATA.md) | Opt-in contracts, fixture teams/teammates, policy and feature flags |
| 02 | [02_METRICS_AND_EVIDENCE.md](02_METRICS_AND_EVIDENCE.md) | Provenance-aware fair normalized source metrics |
| 03 | [03_PLAYER_RATINGS_AND_POSITION_FIT.md](03_PLAYER_RATINGS_AND_POSITION_FIT.md) | Weekly OVR/position fit, confidence, immutable rating runs |
| **04** | [04_TEAM_ROSTER_AND_STARTING_XI.md](04_TEAM_ROSTER_AND_STARTING_XI.md) | **Each production team's top position-fit teammate starting XI + depth charts** |
| 05 | [05_FOOTBALL_SIMULATION_ENGINE.md](05_FOOTBALL_SIMULATION_ENGINE.md) | Seeded 11v11 full-game football simulator and box scores |
| 06 | [06_SQL_PERSISTENCE_AND_WEEKLY_WORKERS.md](06_SQL_PERSISTENCE_AND_WEEKLY_WORKERS.md) | Migration drafts, fixture/SQL parity, immutable results and workers |
| 07 | [07_REST_API_AND_AUTHORIZATION.md](07_REST_API_AND_AUTHORIZATION.md) | Secure API, idempotent commands, OpenAPI/MCP registry integration |
| 08 | [08_UI_VISUALS_AND_GAMECENTER.md](08_UI_VISUALS_AND_GAMECENTER.md) | Polished mobile/desktop scoreboard, lineup, highlights and replay |
| 09 | [09_INTERNAL_SOURCE_ADAPTERS.md](09_INTERNAL_SOURCE_ADAPTERS.md) | **Company-only** vetted producer mapping; no sensitive commits |
| 10 | [10_ADVERSARIAL_TESTING_AND_CALIBRATION.md](10_ADVERSARIAL_TESTING_AND_CALIBRATION.md) | Independent fairness, roster, simulation, auth and DB tests |
| 11 | [11_RELEASE_OPERATIONS_AND_ROADMAP.md](11_RELEASE_OPERATIONS_AND_ROADMAP.md) | Synthetic demo release + separately approved live-run gate |

### Copy/paste kickoff command to Claude Code or Kiro

```text
/goal Implement the FOCUS Gridiron football gamification module in this From_Nothing repository, methodically and without replacing the current FOCUS codebase. Start in FROM-NOTHING-APP, first read docs/gridiron/README.md, ARCHITECTURE_AND_GAME_DESIGN.md, SQL_SERVER_SCHEMA.md, SOURCE_MAPPING_AND_GOVERNANCE.md and ALL docs/gridiron/prompts/README.md plus the numbered prompt 00. Then execute prompts 00 through 11 strictly in order, keeping intermediate tests passing and committing verified work after every prompt. Do NOT just write plans; implement tested code, UI, simulator, fixtures, SQL review artifacts and APIs. Start with synthetic data and keep the public Vercel demo synthetic-only.

Core rule: Each real production team must field its own strongest eligible, voluntarily opted-in teammate starting XI, optimizing position-fit for each required football slot every week; do not mixed-draft, borrow mechanics, or create fictional live starters. Produce 11 distinct offensive and 11 distinct defensive positions from that SAME team's roster (two-way players allowed across units), and block live 11v11 if <11 eligible teammates. Football attributes derive only from verified, fair, opportunity-adjusted production data; protect quality and safety incentives; never penalize LWOP/PTO, punch corrections, legitimate leave or quality reporting. Missing sources are UNKNOWN, not 0. No live database migration, live personal scoring, or company data export without internal approvals. Use current Python 3.12 Flask/Jinja/ES-module frontend, FOCUS SQL Server migration conventions, API registry, SSO grants and work-side integration docs. Finish each phase with evidence, exact tests and clear outstanding gaps; leave the scheduler/validator unchanged.
```

**Don't attempt stage 09 with personal/public agents using real company information.** In this repository stage 09 may implement only generic adapters, contracts and synthetic tests; actual mappings and validations must be executed with an approved internal work coding agent.

### Design decisions
- Starting 11 selected by **maximum-weight position matching**, not just highest raw OVR.
- Same-team roster and consent validated as of freeze cutoff for every athlete.
- Offensive 11 = QB1 RB1 WR3 TE1 OL5; Defensive 11 = DL4 LB3 DB4 (configurable versions).
- The same person may play both ways; never two simultaneous positions within one unit.
- 11 eligible teammates required for a legitimate live standard 11v11 game; insufficient team gives `ROSTER_INCOMPLETE`.
- Bench/rotation/awards include nonstarters without penalizing them, and no supervisor can quietly edit the published lineup.
- Only synthetic fiction in public repo and Vercel; live mappings/records stay private inside company infrastructure.
