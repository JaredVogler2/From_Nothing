# FOCUS From Nothing — bounded build plan

## Target

One installable Python 3.12 package `focus`, one Flask host (iOS + Classic
layouts sharing partials), one immutable `PlanningSnapshot` per admitted
source run, one rich MAX optimizer, one independent validator, SQL-owned
persistence (fixture repository with the same protocol), and one canonical
effort score. The definition of local completion is S21, with all A01–A72
and C01–C39 evidenced.

## Layered architecture

```
sources (SQL S04/S05 or fixture inputs S18)
  -> acquisition (CAPTURING -> COMPLETE, receipts, idempotency)        focus/data/acquisition.py
  -> admission -> immutable PlanningSnapshot (+ census/dispositions)   focus/data/{scope,readiness}.py
       graph/QA/rework/parts      focus/data/{graph,inspections,parts}.py
       durations/statistics       focus/data/{durations,duration_gauge}.py
       qualifications/workforce   focus/data/{qualifications,workforce}.py, focus/eligibility.py
       calendars/shifts/CS        focus/calendar.py, focus/data/cs_windows.py
       economics                  focus/economics/repository.py
  -> MaxOptimizer (seed -> weights -> redispatch -> repair -> continuity -> neighborhoods)
       shared DutyLedger / CrewSelector / commitments                 focus/engine/
  -> IndependentValidator V1..V9 + extended                           focus/validation/
  -> publication: serialize -> detached validate -> stage -> readback validate -> sign -> verify -> accept -> head last
  -> dispatch check (current facts)                                   focus/publication/
  -> lifecycle/disruptions/excusals -> canonical score -> recognition focus/performance/, focus/analytics/
  -> read projections -> /api/v1 (operation registry) -> iOS/Classic views, OpenAPI, MCP catalog
```

## Stage roadmap

| Stage | Depends on | Main outputs | Acceptance |
| --- | --- | --- | --- |
| P01 | — | ROOTS, DECISIONS, STATUS, BUILD_PLAN, rebuild-v1 policy | A03,A15,A64,A72 (traced forward) |
| P02 | P01 | package, app.py, config, domain, codec, contracts, repositories, CLI, Flask host, operation registry | A01,A02,A04,A68 |
| P03 | P01,P02 | discovery/schema ledger, scope census, source contracts | A03,A04,A15,A46 |
| P04 | P02,P03 | acquisition, admission, snapshot, fixture repository heads | A05,A06,A07,A48 |
| P05 | P03,P04 | SQL team inference + reference check, assignment/attendance | A08,A09,A10,A11 |
| P06 | P04,P05 | qualifications, eligibility, pure pools, virtual support | A10–A14,A64 |
| P07 | P03,P04,P06 | interval union, statistics, Duration.resolve | A15–A18 |
| P08 | P04,P07 | line calendars, shifts/DST/no-start/OT, CS facts | A19–A24 |
| P09 | P04,P06,P07,P08 | typed graph, in-progress, rework, parts | A25–A30 |
| P10 | P06–P09 | IP45/final45/AND release, inspector competition | A31–A34 |
| P11 | P06–P10 | duty ledger, travel, activation, crew selection | A34–A41 |
| P12 | P04,P08,P09,P11 | economics repository/registry/evaluator | A44,A45,A46,A23 |
| P13 | P09–P12 | MAX orchestrator and search | A26,A38,A44,A45,A47,A70 |
| P14 | P11–P13 | task defense, delivery hysteresis | A42,A43,A49,A50 |
| P15 | P04,P06–P14 | validator V1–V9 + extended, mutation corpus | A47,A48,A26,A38,A64 |
| P16 | P14,P15 | publication protocol, signing, recovery, dispatch | A48,A51,A52,A53 |
| P17 | P09,P14,P16 | lifecycle, disruption claims, excusals | A54,A55,A56,A26 |
| P18 | P16,P17 | canonical score, gate recognition, XP/badges | A57–A63 |
| P19 | P02,P16,P18 | iOS/Classic shell, scoped API, security | A01,A57,A59,A68,A69 |
| P20 | P13,P14,P17–P19 | all 17 views + supplementary panels | A59,A60,A63,A68,A69 |
| P21 | P16,P18,P20 | twin, scenarios, explanations | A64–A67 |
| P22 | P13–P16,P21 | scale benchmark, optional CP-SAT | A03,A45,A47,A70 |
| P23 | P04,P05,P07,P14,P16–P18,P22 | migrations, workers, CI, deployment | A05,A07,A50,A51,A71 |
| P24 | all | final acceptance, review, release docs | A01–A72 |

## Local commands (POSIX; Windows equivalents in README)

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[verify]"
.venv/bin/python -m focus.cli demo-bootstrap --scenario manufacturing_demo
.venv/bin/python -m focus.cli demo-run --scenario manufacturing_demo
.venv/bin/python app.py
.venv/bin/python -m pytest tests -q
```

## External actions that need executing-environment authority

All of these are prepared locally as reviewable artifacts first. None of them
happens as part of recording the plan:

1. Apply `sql/migrations/001_focus_fn_core.sql` (and later versions) to `SQL-D-22D10090/SHARED` — needs an installer principal and the migration lock.
2. Grant least-privilege SELECT/EXECUTE to the app reader, a separate source promoter, stager, signer and evaluator.
3. Provision production signing key custody (`FOCUS_SIGN_KEY_REF`) outside SQL payloads.
4. Populate live supplemental feeds (OD-01..OD-12), especially attendance/assignment (OD-10).
5. Deploy to Posit Connect (`rsconnect deploy api -e app:app .`) and later VMware Tanzu (`cf push -f manifest.yml`) with environment variables or service bindings.
6. Push/activate remote GitLab CI (`.gitlab-ci.yml` is a local draft).
7. Optional: provision a BCAI endpoint/credential and enable CP-SAT after a shadow benchmark.
