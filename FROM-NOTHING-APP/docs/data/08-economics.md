# Economics: contracts, prices and late-delivery liability

These seven tables let FOCUS put a price on late delivery. They are captured in the same source run (one capture of all datasets at one cutoff) as the WIP census. For each aircraft line, FOCUS picks one economics term: a row of `econ_term` that sets a daily late-delivery penalty rate. FOCUS turns the rate into exact cents and charges it for each minute the aircraft is projected to finish after its due date. This modeled penalty drives the cost of delay: the extra penalty if an aircraft finishes one more day late, reduced by each task's slack. The optimizer uses it to rank ready work (`focus/engine/max/optimizer/urgency.py`). The same penalty lets FOCUS compare complete candidate plans by exact money (`focus/economics/evaluator.py`). If the mapping, the scenario or the term cannot be resolved, or is ambiguous, that aircraft's cost is UNKNOWN. An unknown cost is listed, never counted as zero, and never replaced by a default rate. The due date does not come from these tables: policy `due.basis` = `CS_PLANNING_PROXY` takes it from the same-run CS744 planning proxy in `cs_windows` (open decision OD-06). A term without a due date still leaves the cost UNKNOWN (`DUE_UNKNOWN`).

**How the tables chain together.** `econ_mapping_revision` → `econ_aircraft_mapping` (on `MappingRevisionId`) → `econ_contract_assignment` (on `LineNumber`) → `econ_scenario` (on `ScenarioRevisionId`) → `econ_term` (on `ScenarioRevisionId`, then a selector key) → `econ_original_sale_basis` (on `TermId` and `LineNumber`) → `econ_liability_checkpoint` (on `OriginalBasisId`). For each census line, `focus/economics/repository.py` resolves the chain like this:

1. **Mapping revision.** Take the one `econ_mapping_revision` row with `IsCurrent` = 1. A mapping revision is one stored version of the line-to-customer-and-model mapping.
2. **Aircraft mapping.** Keep the `econ_aircraft_mapping` rows of that revision. Find the census line by `LineNumber`. The row gives the aircraft's `CustomerCode` and `MinorModel`.
3. **Contract assignment.** Find the line's active, effective `econ_contract_assignment` row by `LineNumber`, among rows of the active scenario (step 4). It gives the `ContractKey`.
4. **Scenario.** The active scenario is the one `econ_scenario` row that is active and effective at the source-run cutoff (the run's as-of instant). Its `ScenarioRevisionId` filters both assignments and terms.
5. **Term.** Among the scenario's effective `econ_term` rows, try each selector in policy order (`economics.selector_precedence`). A selector is how a term targets aircraft. AIRCRAFT_OVERRIDE matches the line's `LineNumber`, CONTRACT matches the assignment's `ContractKey`, and CUSTOMER_MODEL matches the mapping's `CustomerCode` and `MinorModel`. The first selector with a match wins; within it, the highest `TermVersion` wins.
6. **Original sale basis.** Find the one `econ_original_sale_basis` row for the line whose `TermId` is the chosen term.
7. **Liability checkpoints.** Among that basis's `econ_liability_checkpoint` rows (same `OriginalBasisId`), the highest `SequenceNumber` is the latest. Its `CumulativeAccounted` is the accounted floor.

S04 is the manual's section of verified source-table contracts. Of its eight economics foreign keys (`ECONOMICS_FOREIGN_KEYS` in `focus/data/discovery.py`), FOCUS does not use three: `ContractAssignment.MappingRevisionId`, `OriginalSaleBasis.AssignmentId` and `OriginalSaleBasis.ScenarioRevisionId`. The line, contract, customer and model joins are FOCUS's own; no foreign key backs them.

**Rules for all seven tables.**
- **Exact money.** Each `decimal(19,4)` amount FOCUS uses (`DailyRate`, the sale basis's `OriginalSalePrice`, `CumulativeAccounted`) becomes exact cents: the amount times 100, held as a Python `Fraction`. Nothing is rounded, so sub-cent rates stay exact.
- **One currency, no conversion.** Only `economics.supported_currencies` are supported (shipped policy: USD). FOCUS checks the chosen term's currency only, and never converts.
- **No cap today.** `economics.cap_policy` = `EXPLICIT_ONLY`, and no table supplies an explicit cap, so no penalty is capped. A non-zero `CapFraction` on the scenario is only reported as `CAP_FIELD_NOT_APPLIED`.
- **No double charging.** The accounted floor is a lower bound on the modeled penalty: penalty = max(modeled penalty, floor). The booked amount is never added on top.
- **Capture.** Each table is read by its reference SQL query. A failed capture (for example `SCHEMA_DRIFT`) adds the readiness gap `DATASET_FAILED`. Zero rows from a live query add `DATASET_EMPTY_UNCERTIFIED`, because a live capture cannot certify an empty table. A readiness gap is a data finding recorded on the snapshot and shown to users. Both gaps carry the effect label `BLOCKS_EXECUTABLE`, but the plan validator does not act on them (for executable plans it checks only `SOURCE_STALE`, `SOURCE_CLOCK_UNKNOWN` and `CAPTURE_SKEW`). The run continues, and each aircraft carries the reason codes listed below. A database error on the query (for example a missing object or denied access) is a failed capture too: `DATASET_FAILED`, and the run continues.
- **Freshness.** None of the tables has a source clock (`LOAD_TIMESTAMP`, `SNAPSHOT_TS` or `LOAD_TS`) or a cutoff column, and none is listed in `admission.max_source_age_hours`. So no `SOURCE_STALE` check applies; only the run-wide `CAPTURE_SKEW` check does.
- **Who reads the result.** Only `focus/economics/repository.py` reads the raw rows, at admission. The engine reads the per-aircraft result (`AircraftEconomics` on the snapshot) through `focus/economics/registry.py`. Users without an economics grant (`manager` or `scenario_planner`) see costs withheld as `ECONOMICS_RESTRICTED`, never as zero.

| Dataset | Kind | Required for a source run | Capabilities | Live reader today | Read by the engine | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| [`econ_mapping_revision`](#econ_mapping_revision) | SOURCE | No | economics | Reference SQL query | Yes | Picks the one current revision of the aircraft mapping. |
| [`econ_aircraft_mapping`](#econ_aircraft_mapping) | SOURCE | No | economics | Reference SQL query | Yes | Gives each aircraft line its customer code and minor model. |
| [`econ_scenario`](#econ_scenario) | SOURCE | No | economics | Reference SQL query | Yes | Picks the one active scenario whose assignments and terms apply. |
| [`econ_contract_assignment`](#econ_contract_assignment) | SOURCE | No | economics | Reference SQL query | Yes | Assigns an aircraft line to at most one contract. |
| [`econ_term`](#econ_term) | SOURCE | No | economics | Reference SQL query | Yes | Daily late-delivery penalty rates and the aircraft they target. |
| [`econ_original_sale_basis`](#econ_original_sale_basis) | SOURCE | No | economics | Reference SQL query | Yes | Ties the chosen term to the aircraft's original sale price; needed for ACTUAL liability. |
| [`econ_liability_checkpoint`](#econ_liability_checkpoint) | SOURCE | No | economics | Reference SQL query | Yes | Liability already booked, used as a floor under the modeled penalty. |

## `econ_mapping_revision`

**Purpose.** Names the aircraft mapping revision that is in force. FOCUS reads only the row flagged `IsCurrent` and uses its `MappingRevisionId` to choose which `econ_aircraft_mapping` rows apply. Without exactly one current revision, no aircraft has a customer and model, so every aircraft's cost is UNKNOWN.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_SOL_TESTING2_MappingRevision`, query `queries/source_inputs/econ_mapping_revision.sql` |
| Grain and key | One row per mapping revision; key `MappingRevisionId` (S04 primary key). Code rule: exactly one row has `IsCurrent` = 1. |
| Required | No: a failed or empty capture is recorded as a readiness gap and the run continues. |
| Capabilities | economics |
| If missing or empty | Gap `DATASET_FAILED` or `DATASET_EMPTY_UNCERTIFIED`. Every census line then gets `NO_CURRENT_MAPPING_REVISION` and `AIRCRAFT_NOT_MAPPED`, and its cost is UNKNOWN. Two or more current rows give `MULTIPLE_CURRENT_MAPPING_REVISIONS` and `AIRCRAFT_NOT_MAPPED` on every line. |
| Time and cutoff | No row clock for freshness and no cutoff column. `ImportedAtUtc` is not read. The revision in force is whatever `IsCurrent` says when the table is captured. |
| Read by | `focus/economics/repository.py` (`build_economics`, called at admission from `focus/data/snapshot_builder.py`) |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `MappingRevisionId` | uniqueidentifier, not null | Identifier of one mapping revision. `econ_aircraft_mapping` and `econ_contract_assignment` rows point to it (S04 foreign keys). | The current row's value selects the `econ_aircraft_mapping` rows FOCUS uses. |
| `SourceName` | varchar(128), not null | Meaning not established in code or docs; the crosswalk says only that this table carries the mapping's source provenance. Demo row: `fixture-aircraft-mapping`. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SourcePredicate` | nvarchar(500), not null | Meaning not established in code or docs. Demo row: `SITE='DEMO'`. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `QuerySha256` | char(64), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ContentSha256` | char(64), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `RowCount` | int, not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. FOCUS does not compare it with the aircraft rows it receives. |
| `ImportedAtUtc` | datetime2(3), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CoverageJson` | nvarchar(4000), not null | Meaning not established in code or docs. Demo row: `{"lines": [1301, 1302, 1303, 1304, 1305, 1306]}`. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `IsCurrent` | bit, not null | Marks the revision in force (ECONOMICS_POLICY.md: exactly one `IsCurrent` mapping revision). | Exactly one row must be 1. Zero gives `NO_CURRENT_MAPPING_REVISION`; more gives `MULTIPLE_CURRENT_MAPPING_REVISIONS`. |

**Rules and pitfalls.**
- Exactly one row may have `IsCurrent` = 1. FOCUS never picks the newest revision by date or by row order.
- Do not reduce the query to one row (for example with TOP 1). FOCUS counts the current rows to detect a second one.
- FOCUS does not check `RowCount`, `CoverageJson` or the hashes against the aircraft rows. The demo revision lists line 1306 and `RowCount` 6, but only five aircraft rows exist, so line 1306 is `AIRCRAFT_NOT_MAPPED`.
- The current revision filters only `econ_aircraft_mapping`. FOCUS does not check `econ_contract_assignment.MappingRevisionId` against it.
- The crosswalk describes an approved mapping revision. FOCUS checks no approval, only `IsCurrent`, so flag only an approved revision as current.

## `econ_aircraft_mapping`

**Purpose.** Gives each aircraft line its customer code and minor model, for the current mapping revision. FOCUS uses the pair to match CUSTOMER_MODEL terms, the lowest-precedence selector. A census line with no row in the current revision is `AIRCRAFT_NOT_MAPPED`. FOCUS then looks for no term at all for that line, not even an aircraft override, so its cost is UNKNOWN.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_SOL_TESTING2_AircraftMapping`, query `queries/source_inputs/econ_aircraft_mapping.sql` |
| Grain and key | One row per aircraft line per mapping revision; key (`MappingRevisionId`, `LineNumber`) (S04 primary key). |
| Required | No: a failed or empty capture is recorded as a readiness gap and the run continues. |
| Capabilities | economics |
| If missing or empty | Gap `DATASET_FAILED` or `DATASET_EMPTY_UNCERTIFIED`. Every census line then gets `AIRCRAFT_NOT_MAPPED`, and its cost is UNKNOWN. |
| Time and cutoff | No row clock for freshness, no cutoff column and no effective dating. `SourceStart`, `SourceEnd`, `RevisionDate`, `ShopCompleteDate` and `ImportedAtUtc` are not read. |
| Read by | `focus/economics/repository.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `MappingRevisionId` | uniqueidentifier, not null | The mapping revision this row belongs to (S04 foreign key to `econ_mapping_revision`). | A row is kept only when this equals the current revision's `MappingRevisionId`. |
| `LineNumber` | int, not null | The aircraft line number. | Looked up for each line of the WIP census (`master_task_list` `LINE_NUMBER`); no row gives `AIRCRAFT_NOT_MAPPED`. |
| `CustomerCode` | varchar(7), not null | The aircraft's customer code. | Compared as exact text with `econ_term.CustomerCode` to match CUSTOMER_MODEL terms. |
| `MinorModel` | tinyint, not null | The aircraft's minor model number. | Compared as an integer with `econ_term.MinorModel` to match CUSTOMER_MODEL terms. |
| `SourceStart` | datetimeoffset(6), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SourceEnd` | datetimeoffset(6), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `RevisionDate` | date, nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ShopCompleteDate` | date, nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. It is not used as a due date. |
| `AssemblyLocation` | char(1), nullable | Meaning not established in code or docs. Demo rows: `D`. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SourceContentSha256` | char(64), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ImportedAtUtc` | datetime2(3), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Give every census line a row in the current revision. A missing line is `AIRCRAFT_NOT_MAPPED`: FOCUS invents no customer or model and skips the term search for it.
- Only rows of the current revision count. Rows of other revisions are ignored.
- Spell `CustomerCode` exactly as on the CUSTOMER_MODEL terms (case and trailing spaces count), and use the same minor model number.
- FOCUS takes the economics customer and model only from this table. It does not compare them with `CUSTOMER_CODE` or `MINOR_MODEL_IDENT` in `master_task_list`.
- No date in this table is used as the due date. The due date comes only from the due policy (`due.basis`); the repository's rule is that "a populated field alone is never contractual due".
- The crosswalk mentions version-conflict and stale-mapping checks for this table. FOCUS performs neither: there is no freshness clock, and the revision is chosen by `IsCurrent` alone.

## `econ_scenario`

**Purpose.** Chooses the economics scenario: the revision whose contract assignments and terms apply. FOCUS needs exactly one scenario that is active and effective at the source-run cutoff. Its `ScenarioRevisionId` filters `econ_contract_assignment` and `econ_term`. With zero or several such scenarios, every aircraft's cost is UNKNOWN.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_SOL_TESTING2_EconomicsScenario`, query `queries/source_inputs/econ_scenario.sql` |
| Grain and key | One row per scenario revision; key `ScenarioRevisionId` (S04 primary key). Code rule: exactly one row is active and effective at the cutoff. |
| Required | No: a failed or empty capture is recorded as a readiness gap and the run continues. |
| Capabilities | economics |
| If missing or empty | Gap `DATASET_FAILED` or `DATASET_EMPTY_UNCERTIFIED`. Every line then gets `NO_SINGLE_ACTIVE_SCENARIO`, or `MULTIPLE_ACTIVE_SCENARIOS` when more than one row qualifies. No assignment or term is considered, and costs are UNKNOWN. |
| Time and cutoff | The effective window runs from `EffectiveFromUtc` (included) to `EffectiveToUtc` (excluded; NULL means no end). It is tested once, at the source-run cutoff. The values are UTC, as their names say: a naive value is read as UTC and an explicit offset is honored, whatever the dataset's source timezone policy. No freshness clock and no cutoff column. |
| Read by | `focus/economics/repository.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `ScenarioRevisionId` | uniqueidentifier, not null | Identifier of one scenario revision. Assignments, terms and sale bases point to it (S04 foreign keys). | The chosen scenario's value filters assignments and terms. |
| `ScenarioKey` | varchar(64), not null | Meaning not established in code or docs. Demo row: `DEMO-PLANNING`. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ScenarioRevision` | int, not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. FOCUS does not use it to choose between scenarios. |
| `Provenance` | varchar(24), not null | Meaning not established in code or docs for this table. Demo row: `ASSUMED`. | Fetched and kept with the source run; nothing in the planner reads it today. Only the term's `Provenance` is read. |
| `Methodology` | nvarchar(1000), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `OwnerSubject` | nvarchar(128), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `EffectiveFromUtc` | datetime2(3), not null | Start of the scenario's effective window, included. | The scenario counts only if this is at or before the cutoff. |
| `EffectiveToUtc` | datetime2(3), nullable | End of the effective window, excluded; NULL means no end. | The scenario counts only if the cutoff is before this. |
| `Currency` | char(3), not null | The scenario's currency (the crosswalk lists currency among the scenario's contents). | Fetched and kept with the source run; nothing in the planner reads it today. The currency check uses each term's own `Currency`. |
| `CapFraction` | decimal(5,4), not null | A cap stated as a fraction. The demo row holds 0.2500, and ECONOMICS_POLICY.md says this field is "never a silent 25% cap"; code and docs do not say what it is a fraction of. | Never applied as a cap. A non-zero value only adds the reason `CAP_FIELD_NOT_APPLIED` (policy `economics.cap_policy` = `EXPLICIT_ONLY`). |
| `PriorityWeight` | decimal(6,5), not null | Meaning not established in code or docs. Demo row: `1.00000`. | Fetched and kept with the source run; nothing in the planner reads it today. FOCUS derives its own priority weights from marginal penalties. |
| `CreatedAtUtc` | datetime2(3), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `IsActive` | bit, not null | Marks the scenario as active. | Only active rows can be chosen. |

**Rules and pitfalls.**
- Exactly one row may be active and effective at the cutoff. FOCUS never breaks a tie by `ScenarioRevision`, `CreatedAtUtc` or row order.
- FOCUS checks no approval and no `Provenance` on the scenario; `IsActive` is the only switch. The crosswalk expects an authorized scenario, so keep drafts inactive.
- FOCUS computes no cap. `CapFraction` only adds `CAP_FIELD_NOT_APPLIED`, and nothing else supplies an explicit cap, so every aircraft's `cap_cents` is empty.
- The penalty curve comes from policy `economics.penalty_curve` (`LINEAR_DAILY`), not from this table.
- Store the effective times in UTC, as the column names say. FOCUS reads them as UTC whatever `FOCUS_SOURCE_TIMEZONE_MAP_JSON` says, the same way the mirror query `queries/economics/effective_terms.sql` compares them with the cutoff.
- An effective time that cannot be parsed rejects the run (`SOURCE_VALUE_INVALID`, `BAD_SOURCE_DATETIME`). The same holds for `econ_contract_assignment` and `econ_term`.

## `econ_contract_assignment`

**Purpose.** Assigns an aircraft line to a contract inside the active scenario. The `ContractKey` lets FOCUS apply one CONTRACT term to every aircraft on that contract. An assignment is optional: without one, the line skips the CONTRACT selector. Two or more effective assignments for one line block that aircraft's term entirely.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_SOL_TESTING2_ContractAssignment`, query `queries/source_inputs/econ_contract_assignment.sql` |
| Grain and key | One row per assignment of a line to a contract; key `AssignmentId` (S04 primary key). Code rule: at most one active, effective row per line in the active scenario. |
| Required | No: a failed or empty capture is recorded as a readiness gap and the run continues. |
| Capabilities | economics |
| If missing or empty | Gap `DATASET_FAILED` or `DATASET_EMPTY_UNCERTIFIED`. No line has a contract, so CONTRACT terms never match. Lines fall through to CUSTOMER_MODEL terms where they exist, else `NO_EFFECTIVE_TERM`. Two or more active, effective rows for one line give `CONTRACT_ASSIGNMENT_CONFLICT: <n> effective active assignments` and no term for that aircraft. |
| Time and cutoff | Same effective-window rule as `econ_scenario`: `[EffectiveFromUtc, EffectiveToUtc)` at the cutoff, read as UTC. `CreatedAtUtc` is not read. No freshness clock and no cutoff column. |
| Read by | `focus/economics/repository.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `AssignmentId` | uniqueidentifier, not null | Identifier of one assignment. `econ_original_sale_basis.AssignmentId` points to it (S04 foreign key). | Fetched and kept with the source run; nothing in the planner reads it today. |
| `LineNumber` | int, not null | The aircraft line being assigned. | Assignments are grouped by line; more than one active, effective row is a conflict. |
| `MappingRevisionId` | uniqueidentifier, not null | The mapping revision the assignment refers to (S04 foreign key to `econ_mapping_revision`). | Fetched and kept with the source run; nothing in the planner reads it today. FOCUS does not check that it is the current revision. |
| `ScenarioRevisionId` | uniqueidentifier, not null | The scenario the assignment belongs to (S04 foreign key to `econ_scenario`). | Only rows of the chosen scenario count. |
| `ContractKey` | varchar(128), not null | The contract the line is assigned to. | Compared as exact text with `econ_term.ContractKey` to match CONTRACT terms. |
| `Provenance` | varchar(24), not null | Meaning not established in code or docs for this table. Demo rows: `ASSUMED`. | Fetched and kept with the source run; nothing in the planner reads it today. Only the term's `Provenance` is read. |
| `EffectiveFromUtc` | datetime2(3), not null | Start of the assignment's effective window, included. | The row counts only if this is at or before the cutoff. |
| `EffectiveToUtc` | datetime2(3), nullable | End of the effective window, excluded; NULL means no end. | The row counts only if the cutoff is before this. |
| `OwnerSubject` | nvarchar(128), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ApprovedBy` | nvarchar(128), nullable | Meaning not established in code or docs for this table; the crosswalk expects assignments to be owner-approved. | Fetched and kept with the source run; nothing in the planner reads it today. Only the term's `ApprovedBy` is read. |
| `SourceReference` | nvarchar(512), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CreatedAtUtc` | datetime2(3), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `IsActive` | bit, not null | Marks the assignment as active. | Only active rows count. |

**Rules and pitfalls.**
- At most one active, effective assignment per line in the active scenario. FOCUS never resolves two by row order. It chooses no term for that aircraft, not even an aircraft override.
- To replace an assignment, end-date the old row (`EffectiveToUtc`) or set `IsActive` = 0. A new row added without ending the old one creates a conflict once both are effective.
- A line without an assignment silently uses a CUSTOMER_MODEL term if one exists; no per-aircraft reason is recorded. Without its assignment, demo line 1303 would move from the 50.00 CONTRACT rate to the 40.00 CUSTOMER_MODEL rate.
- Use the same `ContractKey` text as on the CONTRACT terms. FOCUS compares it exactly (case and trailing spaces count).
- Assignments of other scenarios are ignored, even if active.
- FOCUS does not check `ApprovedBy`, `Provenance` or `MappingRevisionId` here. The crosswalk expects owner-approved assignments, so make only approved rows active.

## `econ_term`

**Purpose.** Holds the daily late-delivery penalty rates. Each term targets aircraft through a selector: one line (AIRCRAFT_OVERRIDE), one contract (CONTRACT), or a customer code and minor model (CUSTOMER_MODEL). FOCUS chooses one term per aircraft and charges its `DailyRate` per minute late: rate × minutes late ÷ 1440 (policy `economics.penalty_curve` = `LINEAR_DAILY`). This modeled penalty, never below the accounted floor, drives the cost of delay and the exact comparison of plans.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_SOL_TESTING2_EconomicsTerm`, query `queries/source_inputs/econ_term.sql` |
| Grain and key | One row per term version; key `TermId` (S04 primary key). Code rule: among the effective terms that match one aircraft through its winning selector, each `TermVersion` appears once. |
| Required | No: a failed or empty capture is recorded as a readiness gap and the run continues. |
| Capabilities | economics |
| If missing or empty | Gap `DATASET_FAILED` or `DATASET_EMPTY_UNCERTIFIED`. Every mapped line under a resolved scenario then gets `NO_EFFECTIVE_TERM`, and its cost is UNKNOWN; no default rate is used. Other per-aircraft codes: `TERM_VERSION_CONFLICT:<selector>`; `UNSUPPORTED_CURRENCY:<code>` and `DAILY_RATE_INVALID` (status UNSUPPORTED); authority labels `ASSUMED_PLANNING_TERM` and `TERM_AUTHORITY_UNPROVEN`. |
| Time and cutoff | Effective window `[EffectiveFromUtc, EffectiveToUtc)`, tested once at the source-run cutoff and read as UTC (same rule as `econ_scenario`). Versions are ordered by `TermVersion`, not by time. `CreatedAtUtc` is not read. |
| Read by | `focus/economics/repository.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `TermId` | uniqueidentifier, not null | Identifier of one term. `econ_original_sale_basis.TermId` points to it (S04 foreign key). | Reported as the aircraft's term; a sale basis counts only if it names this ID. |
| `ScenarioRevisionId` | uniqueidentifier, not null | The scenario the term belongs to (S04 foreign key to `econ_scenario`). | Only terms of the chosen scenario count. |
| `SelectorType` | varchar(24), not null | How the term targets aircraft: `AIRCRAFT_OVERRIDE`, `CONTRACT` or `CUSTOMER_MODEL`. | Selectors are tried in the order of `economics.selector_precedence`; any other value never matches. |
| `SelectorLength` | smallint, not null | Meaning not established in code or docs. The S04 table also has a `SelectorKey varbinary(256)` column that FOCUS does not fetch. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CustomerCode` | varchar(7), not null | The customer code the term applies to. | For CUSTOMER_MODEL terms, must equal the aircraft mapping's `CustomerCode` exactly; ignored for other selectors. |
| `MinorModel` | tinyint, not null | The minor model the term applies to. | For CUSTOMER_MODEL terms, must equal the aircraft mapping's `MinorModel`; ignored for other selectors. |
| `LineNumber` | int, nullable | The line an AIRCRAFT_OVERRIDE term applies to. | AIRCRAFT_OVERRIDE matches on it. A CUSTOMER_MODEL term must have it NULL. |
| `ContractKey` | varchar(128), nullable | The contract a CONTRACT term applies to. | CONTRACT matches it exactly against the line's assignment. A CUSTOMER_MODEL term must have it NULL. |
| `OriginalSalePrice` | decimal(19,4), not null | Meaning not established in code or docs. Demo rows: `0.0000`. | Fetched and kept with the source run; nothing in the planner reads it today. The sale price FOCUS reports comes from `econ_original_sale_basis`. |
| `DailyRate` | decimal(19,4), not null | Late-delivery penalty per day late, in the term's currency. | Converted to exact cents. Negative or unreadable gives `DAILY_RATE_INVALID`; zero is a known zero (no penalty, but known). |
| `Currency` | char(3), not null | Currency of the term's amounts. | Must be in `economics.supported_currencies` (USD), else `UNSUPPORTED_CURRENCY:<code>` and status UNSUPPORTED. |
| `EffectiveFromUtc` | datetime2(3), not null | Start of the term's effective window, included. | The term counts only if this is at or before the cutoff. |
| `EffectiveToUtc` | datetime2(3), nullable | End of the effective window, excluded; NULL means no end. | The term counts only if the cutoff is before this. |
| `Provenance` | varchar(24), not null | Where the rate comes from: `ACTUAL` (contractual) or `ASSUMED` (a planning assumption). | Sets the authority label: ACTUAL (with approval and one sale basis), ASSUMED, or otherwise UNKNOWN. |
| `Methodology` | nvarchar(1000), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `SourceReference` | nvarchar(512), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `OwnerSubject` | nvarchar(128), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ApprovedBy` | nvarchar(128), nullable | The term's approval (ECONOMICS_POLICY.md: ACTUAL needs "an approval"). | Must be non-empty for ACTUAL authority. |
| `TermVersion` | bigint, not null | Version number of the term. | The highest wins within the winning selector; a repeated value gives `TERM_VERSION_CONFLICT:<selector>`. |
| `CreatedAtUtc` | datetime2(3), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Precedence (policy `economics.selector_precedence`, manual_specified): AIRCRAFT_OVERRIDE, then CONTRACT, then CUSTOMER_MODEL. The first selector with any effective match wins, even against a higher `TermVersion` lower down.
- Within the winning selector, any repeated `TermVersion` among the matches, not only the top one, gives `TERM_VERSION_CONFLICT:<selector>` and no term. FOCUS does not fall back to a lower selector. End-date superseded versions or give each a new version.
- Use NULL, not an empty string, for `LineNumber` and `ContractKey` on CUSTOMER_MODEL terms. FOCUS never defaults a key, so a CUSTOMER_MODEL term with an empty string there never matches.
- Effectivity is checked once, at the cutoff. A rate that starts after the cutoff is ignored, even for lateness after its start date.
- ACTUAL authority needs `Provenance` = `ACTUAL`, a non-empty `ApprovedBy` and exactly one sale basis naming the `TermId`. The label never changes the amount: an unproven term still gives a modeled cost, labelled UNKNOWN authority (`TERM_AUTHORITY_UNPROVEN`).
- FOCUS never converts currencies and does not compare this `Currency` with the scenario's or the sale basis's. Plan cost totals are labelled USD.

## `econ_original_sale_basis`

**Purpose.** Records an aircraft's original sale price and ties it to the term chosen for that aircraft. FOCUS needs exactly one basis naming the chosen term before it labels liability ACTUAL. The basis is also the only link to the liability checkpoints. The price is kept as exact cents and shown in the economics view, but it enters no cost today because FOCUS computes no cap.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_SOL_TESTING2_OriginalSaleBasis`, query `queries/source_inputs/econ_original_sale_basis.sql` |
| Grain and key | One row per original sale basis; key `OriginalBasisId` (S04 primary key). Code rule: exactly one row per line names the chosen `TermId`. |
| Required | No: a failed or empty capture is recorded as a readiness gap and the run continues. |
| Capabilities | economics |
| If missing or empty | Gap `DATASET_FAILED` or `DATASET_EMPTY_UNCERTIFIED`. No aircraft can be ACTUAL (ACTUAL-provenance terms get `TERM_AUTHORITY_UNPROVEN`), no accounted floor applies, and the sale price is empty. Modeled costs are still computed. Two or more bases for the chosen term give `SALE_BASIS_CONFLICT: <n> original sale bases for term <TermId>`. |
| Time and cutoff | No effective dating, no freshness clock and no cutoff column. `CreatedAtUtc` is not read. |
| Read by | `focus/economics/repository.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `OriginalBasisId` | uniqueidentifier, not null | Identifier of one sale basis. `econ_liability_checkpoint.OriginalBasisId` points to it (S04 foreign key). | Used to find the basis's checkpoints. |
| `LineNumber` | int, not null | The aircraft line the basis is for. | Bases are grouped by line; only the aircraft's own line is searched. |
| `ContractKey` | varchar(128), not null | Meaning not established in code or docs. It has the same name as the assignment's contract key, but no foreign key or code links them. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ScenarioRevisionId` | uniqueidentifier, not null | The scenario of the basis (S04 foreign key to `econ_scenario`). | Fetched and kept with the source run; nothing in the planner reads it today. FOCUS does not check it against the chosen scenario. |
| `AssignmentId` | uniqueidentifier, not null | The contract assignment of the basis (S04 foreign key to `econ_contract_assignment`). | Fetched and kept with the source run; nothing in the planner reads it today. FOCUS does not check it against the line's assignment. |
| `OriginalSalePrice` | decimal(19,4), not null | The aircraft's original sale price (the crosswalk's "actual approved original sale basis"). | Converted to exact cents and shown as `original_sale_cents`; it is not used in any cost. |
| `Currency` | char(3), not null | Currency of the original sale price (crosswalk). | Fetched and kept with the source run; nothing in the planner reads it today. FOCUS does not compare it with the term's currency. |
| `Provenance` | varchar(24), not null | Meaning not established in code or docs for this table. | Fetched and kept with the source run; nothing in the planner reads it today. Only the term's `Provenance` is read. |
| `TermId` | uniqueidentifier, not null | The term this basis supports (S04 foreign key to `econ_term`). | The basis counts only if this equals the chosen term's `TermId`. |
| `SourceReference` | nvarchar(512), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CreatedAtUtc` | datetime2(3), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |

**Rules and pitfalls.**
- Exactly one basis per line may name the chosen `TermId`. Two or more give `SALE_BASIS_CONFLICT`: no ACTUAL label, no sale price and no accounted floor for that aircraft.
- A basis that names another `TermId` is ignored. After a new term version (a new `TermId`), add a basis for it, or the aircraft loses its ACTUAL label and its accounted floor.
- FOCUS does not check `Currency`, `Provenance`, `ScenarioRevisionId`, `AssignmentId` or `ContractKey` here. Keep them consistent with the chosen term and assignment at the source.
- No cap is derived from `OriginalSalePrice` today. The crosswalk says this basis is what "any cap" would be computed from, but no cap formula exists in code.
- An ASSUMED term with a basis keeps the ASSUMED label, but its checkpoints still set the accounted floor.

## `econ_liability_checkpoint`

**Purpose.** Records late-delivery liability that accounting has already booked against an original sale basis. FOCUS takes the latest checkpoint (highest `SequenceNumber`) and uses its `CumulativeAccounted` as the accounted floor. The modeled penalty never counts below what is booked, and the booked amount is never added on top. So the floor can raise the unavoidable part of the cost (the penalty even if the aircraft finished at the cutoff), and only the excess above it counts as controllable cost and cost of delay.

| | |
| --- | --- |
| Kind | SOURCE: reference object `WEB_APP.FOCUS_SOL_TESTING2_LiabilityCheckpoint`, query `queries/source_inputs/econ_liability_checkpoint.sql` |
| Grain and key | One row per checkpoint; key `CheckpointId` (S04 primary key). FOCUS orders the checkpoints of one `OriginalBasisId` by `SequenceNumber`, which should be unique per basis. |
| Required | No: a failed or empty capture is recorded as a readiness gap and the run continues. |
| Capabilities | economics |
| If missing or empty | Gap `DATASET_FAILED` or `DATASET_EMPTY_UNCERTIFIED`. No floor applies, and the modeled penalty is used alone. A missing floor is never treated as a zero floor. |
| Time and cutoff | A checkpoint counts only if its `CreatedAtUtc` (UTC) is at or before the cutoff, as in the mirror query `queries/economics/liability_floor.sql`. Among those, the highest `SequenceNumber` is the latest; `AsOfUtc` is not read. No freshness clock and no cutoff column. |
| Read by | `focus/economics/repository.py` |

| Column | Type | Meaning | How FOCUS uses it |
| --- | --- | --- | --- |
| `CheckpointId` | uniqueidentifier, not null | Identifier of one checkpoint. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `OriginalBasisId` | uniqueidentifier, not null | The sale basis the checkpoint accounts against (S04 foreign key to `econ_original_sale_basis`). | Checkpoints are grouped by it; only the chosen basis's checkpoints are used. |
| `SequenceNumber` | bigint, not null | Order of the checkpoints of one basis; the highest is the latest (`liability_floor.sql`). | The highest wins among checkpoints created by the cutoff. Two at the same highest sequence give `CHECKPOINT_SEQUENCE_CONFLICT` and no floor. |
| `AsOfUtc` | datetime2(3), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `ElapsedLateDays` | decimal(18,6), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `AssessedAmount` | decimal(19,4), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CumulativeAccounted` | decimal(19,4), not null | Total liability accounted for the basis up to this checkpoint: the "cumulative accounted floor" (`liability_floor.sql`). | Converted to exact cents as the accounted floor: penalty = max(modeled penalty, floor). |
| `AssessmentCoverage` | varchar(32), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `AssessmentReference` | nvarchar(512), nullable | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CheckpointKind` | varchar(16), not null | Meaning not established in code or docs. | Fetched and kept with the source run; nothing in the planner reads it today. |
| `CreatedAtUtc` | datetime2(3), not null | When the checkpoint was recorded (UTC). | A checkpoint created after the cutoff is not known to the run and is skipped. An unparsable value rejects the run (`SOURCE_VALUE_INVALID`). |

**Rules and pitfalls.**
- Keep `SequenceNumber` unique per `OriginalBasisId`. A tie gives `CHECKPOINT_SEQUENCE_CONFLICT` and no floor (unknown, never a row-order pick), as the mirror query reports it.
- `CumulativeAccounted` must be the running total, not the amount of one assessment: only the latest row is used.
- The floor is a lower bound, never an addition. While the modeled penalty is under the floor, one more day late adds nothing, so the aircraft gets no cost-of-delay weight.
- Checkpoints apply only through the one sale basis that names the chosen term (see `econ_original_sale_basis`).
- The table has no currency column. FOCUS treats `CumulativeAccounted` as being in the term's currency.
- The planner does not filter out checkpoints recorded after the cutoff. Leave them out of the source if they should not count yet.
