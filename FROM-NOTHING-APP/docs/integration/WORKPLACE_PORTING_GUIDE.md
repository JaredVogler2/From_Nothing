# FOCUS From Nothing — confidential workplace integration handoff

> **Goal:** Transfer the source-driven FOCUS manufacturing scheduler from the synthetic demonstration to an **approved company-only environment** without sending any real source names beyond this repository's existing reference contract, data rows, employee information, credentials, proprietary query results or company policy decisions to this chat or personal GitHub.
>
> Read together with [SOURCE_REPLACEMENT_CROSSWALK.md](SOURCE_REPLACEMENT_CROSSWALK.md), [SCHEDULING_CONSTRAINTS.md](SCHEDULING_CONSTRAINTS.md), the [machine-generated contract exporter](../../scripts/export_integration_contract.py), [SQL_SCHEMA_LEDGER.md](../execution/SQL_SCHEMA_LEDGER.md), [SOURCE_READINESS.md](../execution/SOURCE_READINESS.md), [VALIDATION_COVERAGE.md](../execution/VALIDATION_COVERAGE.md), [DECISIONS.md](../execution/DECISIONS.md) and the source code.

## 1. Confidentiality and authority

- **Never** copy live datasets, commercial terms, actual SQL connection parameters, employee identifiers, company-specific schema discoveries, internal mapping manifests, screenshots or production query results into this public repository, personal agents or external chat.
- The SQL object names documented in this repository are **already-existing development reference contracts**, not proof of the internal environment or authorization. Discover actual internal systems only through a company-approved agent, in company-approved storage, with approved model/data controls.
- Keep all filled-in mappings and work-side secrets in a separate **internal** repo/config or secrets manager. The generated blank manifest is safe to commit; a filled manifest is NOT.
- First acquire read-only permissions. Do not run drafts in sql/migrations or post accepted plans without independent company approval. In live mode, never fall back to fixture data or a test signing key.
- The public Vercel demonstration must remain **synthetic-only**. Host the work version through approved internal infrastructure such as Posit Connect/Tanzu, using company SSO and managed secrets.

## 2. What the coding agent must replace

The replaceable boundary is **DatasetSpec**, not the scheduling engine.

1. Canonical datasets, exact column names and types, capability flags: focus/data/datasets.py.
2. SQL-backed read-only reference projections: queries/source_inputs/*.sql (no SELECT *, no implicit top-N fleet cap).
3. Source-contract metadata: focus/data/discovery.py, fixtures/source_contracts/s04_contracts.json.
4. Fixture reader and currently limited live reader: focus/data/fixture_reader.py.
5. SQL Server allowlisted, parameterized read-only connector: focus/data/sql.py.
6. Consistent-cutoff immutable source acquisition and receipts: focus/data/acquisition.py.
7. Normalization/admission and fail-closed readiness: focus/data/snapshot_builder.py.
8. Constraints and scheduling: focus/data/graph.py, focus/data/workforce.py, focus/data/qualifications.py, focus/data/durations.py, focus/calendar.py, focus/engine/model.py, focus/engine/ledger.py, focus/engine/max/optimizer/*.
9. Independent acceptance: focus/validation/plan.py and focus/validation/publication.py.
10. Economics, commitments, observations and publication: focus/economics/*, focus/engine/commitments.py, focus/performance/*, focus/publication/*.

The current live reader automatically covers reference SOURCE projections and only TWO supplemental query paths (team_inference and clock_observations). Other supplemental datasets return NOT_PROBED until real producers are installed and explicitly connected. Readiness gaps must never silently become empty success.

### Target architecture

~~~text
Company SQL Server / other approved internal source
    ↓ (approved read-only parameterized adapters)
Canonical dataset contracts + source owner evidence
    ↓ (one as-of instant, producer receipts)
Atomic SourceRun: immutable source datasets & hashes
    ↓ (admission + eligibility/readiness gaps)
PlanningSnapshot: all WIP, personnel, parts, QA, DAG, calendar, economics
    ↓
FORECAST or EXECUTABLE candidate from Rich MAX
    ↓
Independent V1–V9 and E_* validation
    ↓
Separate authorized acceptance/dispatch authority
    ↓
Scoped mobile/desktop views, performance and digital twin
~~~

## 3. Exact environment configuration — values stay at work

Read focus/config.py for recognized variable names and validation. The company-side agent should configure these only in internal infrastructure:

| Contract | Responsibility |
|---|---|
| FOCUS_MODE=live | Prevent fixture fallback; all live source gaps are explicit. |
| FOCUS_SQL_HOST / DATABASE / USER / PASSWORD | Approved read-only SQL Server service identity in secrets manager, not Git. |
| FOCUS_SOURCE_TIMEZONE_MAP_JSON | Explicit timezone policy per input source; UTC cannot be inferred from server clock. |
| FOCUS_APP_ROOT, policy settings | Approved app root and owner-approved, versioned scheduling policy. |
| FOCUS_IDENTITY_PROVIDER, FOCUS_GRANTS_JSON | Company SSO/Posit Connect or trusted-header mapping with server-owned team/economics grants. |
| FOCUS_SESSION_SECRET, secure cookies | Stable deployment-wide session; no generated per-process secret. |
| Separate write/acceptance/signing roles | Only after approved DB migrations and independent validation. Test keys are fixture-only. |

No live commercial delay penalty, delivery due, overtime authority, shift rule, crew requirement or source status interpretation may be guessed from a demo default.

The existing live reader uses pymssql for SQL Server. If your approved sources are Oracle/Teradata or mixed, implement separate internally approved **read-only** adapters that emit the same canonical dataset columns and capture receipts; the repository does NOT currently implement a native multi-database live reader.

## 4. Ordered workplace agent rollout, with hard gates

### Gate A — approved access and a complete blank contract
- Clone the code inside the company network; pin revision. Keep synthetic fixture available for regression.
- Run: python scripts/export_integration_contract.py --output docs/integration/contract_manifest.generated.json
- This exporter reads repository metadata and SQL **text** only: no connections/rows. Fill actual source mapping only in internal storage.
- Identify source owners and approved service accounts for each canonical dataset; document whether the contract exists or needs a new governed projection.

### Gate B — source mapping and semantics
For EACH dataset in SOURCE_REPLACEMENT_CROSSWALK.md:
- Map internal approved table/view/producer, join path, canonical fields and aliases, exact row grain and natural keys, duplicates/voided revisions, completeness, scope, status meanings, load/revision cutoff, temporal zone, column types, source owner/approval and one source query revision/hash.
- Build SELECT-only adapters and a test on synthetically generated representative rows. Do not print real rows into agent reports.
- A missing data stream is NOT_PROBED. An empty stream needs owner-certified empty evidence. A required failed stream rejects the source run.
- Preserve ALL WIP and completed predecessor anchors; do not cap by line/job or omit blocked records.

### Gate C — source readiness and owner decisions
- Run read-only schema drift against the existing reference contracts using queries/discovery/schema_reference.sql and related diagnostic queries, but adapt table location inside company and avoid exporting results.
- Have approved owners resolve OD-02 through OD-17 in config/policies/rebuild-v1.json (the owner decided OD-01, simultaneous crew, on 2026-10-31). **Fixture defaults are not live approvals.**
- Critical live blockers include: simultaneous crew semantics, actual clock/elapsed rework calibration, in-process QA hold locations, OT and protected breaks, CS offset reference, true due date/commercial authority, hard concurrency semantics, verified rework class, affirmative no-cert census, effective roster/attendance, travel origin, economics terms, statuses, delivery-commitment purpose, lifecycle actual feeds, grading approval and excusal reviewer grants.
- Preserve exact date precision, day/night shift and DST policy.

### Gate D — immutable acquisition and complete-scope forecast
- Commands (in company environment):
~~~bash
python -m focus.cli source-discover --read-only --mode live
python -m focus.cli acquire --mode live --cutoff <APPROVED_UTC_INSTANT> --idempotency-key <INTERNAL_RUN_KEY>
python -m focus.cli schedule --source-run <SOURCE_RUN_ID> --policy <APPROVED_POLICY_ID> --purpose FORECAST
python -m focus.cli validate --candidate <CANDIDATE_ID> --all-required
~~~
- The run must use one cutoff and admitted source hashes; optimization must read the frozen snapshot, not hot-query changing data.
- FORECAST may use labeled conditional shortages, virtual MHE/PSC/TST support, and nominal crew evidence where policy allows; it must never claim actual executable readiness.

### Gate E — executable certification
- EXECUTABLE requires current named real people, in-shift availability, entire-interval certs, full role-demand crews, approved OT, valid calendars/CS, confirmed parts and located QA inspection holds.
- No modeled job is executable if any required authority is missing. Missing actual attendance is not replaced with historical inferred team membership; an ECD is not a receipt.
- Independently validate V1–V9 and E_* checks, including skipped-check coverage; fail if any required validation check is SKIPPED, not merely on violation count.
- Historical incumbent/commitment protection and exact monetary optimization must be independently validated, with UNKNOWN economics preserved.

### Gate F — company-only shadow run, publication and operations
- Compare read-only plans to authorized existing schedules, actuals and staffing; review quality, false positives, blocked work and high-impact disagreements with owners.
- Only after acceptance governance and appropriately separated writer identities: install reviewed FOCUS-owned tables, publish signed accepted EXECUTABLE plans, freeze actual obligations and enable dispatch rechecks.
- Workload and performance results must remain evidence-backed and team-scoped; do not turn demo scores into official individual grades.
- Audit source run/cutoff, mapping version, policy hash, all validation receipts, publish/rollback version and query SHA. Retain prior accepted plan for rollback.

## 5. Non-negotiable verification checklist

- Source count/coverage by canonical dataset is complete; no row copies outside the workplace.
- Zero unreviewed source schema drift; explicit status and timezone semantics.
- Full fleet WIP census including all blocked/quarantined/complete anchors, without top-N cap.
- Sourced and independently checked resource qualification, attendance, QA, parts, calendars, CS, DAG and precedence/concurrency.
- Honest FORECAST versus EXECUTABLE distinction and conditional flags.
- Duration estimate is P75 elapsed or explicitly selected approved statistic, not aggregate person-effort substituted for elapsed.
- Complete delivery scope across all required aircraft jobs, not just conveniently scheduled jobs; UNKNOWN remains UNKNOWN.
- No writer action or production update on a read-only probe.
- No migrations executed without authorization; no test signing keys used live.
- UI/MCP/agents share the same authorized scoped data and approved accepted-plan authority.

**Definition of plug-and-play:** the company-side developer replaces dataset producers, not engine rules; every input is sourced/approved/validated; the independent validator accepts the artifact; the deployment is contained in the company's environment.

## 6. Copy-paste internal agent goal

~~~text
/goal Port this FOCUS From_Nothing scheduler to our approved internal environment. Read docs/integration/WORKPLACE_PORTING_GUIDE.md, SOURCE_REPLACEMENT_CROSSWALK.md, SCHEDULING_CONSTRAINTS.md and existing docs/execution/SQL_SCHEMA_LEDGER.md, SOURCE_READINESS.md, VALIDATION_COVERAGE.md and DECISIONS.md. Generate the reference-only machine contract using scripts/export_integration_contract.py. Inside our COMPANY environment only, discover and privately map every DatasetSpec to authorized source tables, views, queries and owner evidence. Never send real table identities, data, credentials, internal queries, employee/commercial details or mapping outputs to public GitHub or external chat. Implement source adapters with exact aliases/types/grains/cutoffs/completeness receipts, and maintain immutable source snapshots. Run schema-readiness and independent constraint checks and first schedule complete-scope FORECAST in read-only mode. A missing source or authority must remain an explicit NOT_PROBED/UNKNOWN/BLOCKED capability; never invent personnel, parts, durations, QA holds, commercial penalties or replace live source with fixture data. Do not modify canonical optimization/validation rules, run database migrations, publish plans or enable real dispatch until an authorized owner approves each gate. Store all internal mapping, test evidence, approvals and outputs only in our controlled company systems.
~~~
