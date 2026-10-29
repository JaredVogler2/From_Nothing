# FOCUS Gridiron — SQL Server physical-data contract (proposed)

**Authority:** Design proposal; NO production migrations executed. Target SQL Server 2022; proposed prefix `WEB_APP.FOCUS_FN_Gridiron*` mirrors the existing `FOCUS_FN_*` pattern, but an authorized DBA must select the final company schema. Accompanying `sql/migrations/gridiron_001_draft.sql` is a review-only initial DDL, not a guaranteed drop-in for an uninspected live environment.

## 0. Objectives, isolation and explicit non-goals

The Grid DB stores **consented game participation, source-evidence receipts, calculated weekly game ratings, team rosters, immutable matchplays, and corrections**. It does not become a shadow HR, personnel, attendance or manufacturing scheduling database. Never write to `FOCUS_FN_Evaluation`, `FOCUS_FN_FrozenGoal`, any canonical source tables or accepted plans from Gridiron. Existing FOCUS evaluations are team-shift authority, NOT personal performance scores. Use approved source adapters to create separate, game-only measurements.

Use `uniqueidentifier` PKs, SQL Server `datetime2(6)` UTC timestamps, `char(64)` lowercase SHA-256 hex over canonical serialized bytes for evidence/policy hashes, `nvarchar` for UI strings, `decimal` for bounded precision, `bit` for booleans, and string codes with CHECK constraints. Reference hashes are not anonymization by themselves: native person IDs must never be retained in personal/public GitHub; use HMAC-SHA256 with a work-only secret, rotate with strategy. No actual employee values in this document.

**Tenant isolation:** `ScopeKey nvarchar(128) NOT NULL` on every operational entity. All application queries filter scope BEFORE fetching. For multi-table constraints add composite parent candidate keys `UNIQUE(ScopeKey, Id)` and composite FKs where feasible, or enforce scope transactionally in an approved repository, with tests; do not rely on authorization by client-provided ScopeKey.

**Week** intervals are half-open `[WeekStartUtc, WeekEndUtc)`. Snapshot after end at `EvidenceCutoffUtc`; require source watermark sufficient for the run. SQL changes are **append-only for accepted/frozen snapshots**, and no game recalculation in GET.

## 1. Core table catalog with explicit grain, columns and keys

Each table is a new proposed object. The DDL gives complete implementation-ready column sets for the first iteration; this document records semantics, governing rules and logical relationships. Identity lookup linking approved internal personnel to `PersonId` belongs in a **separate company-only vault/service**, not a public app-side mapping.

### G01 `FOCUS_FN_GridironPolicy`
**Grain:** one approved or demonstration rulebook version per scope. PK `PolicyId uniqueidentifier`. Columns:
- `ScopeKey nvarchar(128) NOT NULL`; `Version int NOT NULL` (unique with scope); `Status varchar(16) NOT NULL` [DRAFT, DEMONSTRATION, APPROVED, RETIRED]; `DefinitionJson nvarchar(max) NOT NULL` (ISJSON=1; canonical weights, eligible evidence, minimum samples, positions, seeds, privacy, games, retention); `DefinitionSha char(64) NOT NULL`; `ApprovalRefHash char(64) NULL`; `CreatedAtUtc datetime2(6) NOT NULL`.
**Checks:** sum normalized weights exactly 1.0 at the application boundary; all score bounds and rules validated; no unapproved policy promoted live. No UPDATE once used by RatingRun.

### G02 `FOCUS_FN_GridironSeason`
**Grain:** one sports season in scope. PK `SeasonId uniqueidentifier`; `ScopeKey`, `PolicyId` FK; `SeasonCode varchar(40)`, `Title nvarchar(100)`, `StartUtc datetime2(6)`, `EndUtc datetime2(6)`, `State varchar(16)` [DRAFT, ACTIVE, FINISHED, CANCELLED], `CreatedAtUtc`. UNIQUE(ScopeKey, SeasonCode). End strictly greater than start.

### G03 `FOCUS_FN_GridironWeek`
**Grain:** one numbered match/scoring week per season. PK `WeekId`; `ScopeKey`, `SeasonId` FK, `WeekNumber int`, `WeekStartUtc`, `WeekEndUtc`, `EvidenceCutoffUtc datetime2(6) NULL`, `State varchar(16)` [UPCOMING, OPEN, RATING_FROZEN, SIMULATED, CLOSED], `CreatedAtUtc`. UNIQUE(SeasonId, WeekNumber); reject overlapping weeks in application/serialized writer. `EvidenceCutoffUtc >= WeekEndUtc` when present; no earlier future records.

### G04 `FOCUS_FN_GridironTeam`
**Grain:** a sports franchise, potentially mapped to production team internally. PK `TeamId`; `ScopeKey`, `TeamCode varchar(40)` (unique per scope), `DisplayName nvarchar(100)`, `ShortName nvarchar(20)`, `ThemeCode varchar(32)`, `IsActive bit`, `CreatedAtUtc`. No real work team/code in public fixture. The private authorized mapping from work team keys to this sports franchise is kept in the internal mapping manifest.

### G05 `FOCUS_FN_GridironSeasonTeam`
**Grain:** franchise participating in one season. Composite PK (`SeasonId`, `TeamId`); `ScopeKey`, `DivisionCode varchar(32) NULL`, `JoinedAtUtc`, `Seed int NULL`. Season and team must belong to same scope.

### G06 `FOCUS_FN_GridironPerson`
**Grain:** game participant, not an HR record. PK `PersonId`; `ScopeKey`, `IdentityTokenHash binary(32) NULL` (HMAC of canonical approved identity, internal only), `PublicAlias nvarchar(40)`, `PreferredPosition varchar(4) NULL`, `CreatedAtUtc`, `RetiredAtUtc NULL`. UNIQUE(scope, token hash) where not null, UNIQUE(scope, alias) if authorized and collision-safe. No name, badge, DOB, shift absence, payroll, gender, health, manager notes, profile picture or discipline fields. Public mock participants may have null hash.

### G07 `FOCUS_FN_GridironEnrollment`
**Grain:** person opt-in decision per season, append decision history rather than destructive erase. PK `EnrollmentId`; `ScopeKey`, `SeasonId`, `PersonId`, `Decision varchar(16)` [OPT_IN, OPT_OUT], `DecisionAtUtc`, `EffectiveAtUtc`, `Source varchar(16)` [SELF, ADMIN_APPROVED], `ProofHash char(64) NULL`, `SupersedesEnrollmentId NULL`. Effective latest event determines eligibility; revocation prevents future games, existing history per approved retention/privacy policy. Allow nonparticipants no implicit default consent.

### G08 `FOCUS_FN_GridironRoster`
**Grain:** one effective-dated season/team/person roster assignment revision. PK `RosterId`; `ScopeKey`, `SeasonId`, `TeamId`, `PersonId`, `EffectiveFromWeek int`, `EffectiveThroughWeek int NULL` (inclusive), `PositionPreference varchar(4) NULL`, `Status varchar(16)` [ACTIVE, BENCH, TRANSFERRED, RELEASED], `OriginReceiptHash char(64) NULL`, `CreatedAtUtc`. An eligible participant has at most one active team in a week; enforce via application validation across interval overlaps, not just a unique key.

### G09 `FOCUS_FN_GridironSourceReceipt`
**Grain:** one feed/dataset and as-of capture for a week. PK `ReceiptId`; `ScopeKey`, `WeekId`, `FeedCode varchar(40)`, `CoverageStatus varchar(24)` [ROWS,CERTIFIED_EMPTY,EMPTY_UNCERTIFIED,NOT_PROBED,FAILED], `CapturedThroughUtc datetime2(6) NULL`, `SourceRunRef varchar(80) NULL`, `ObservationRunRef varchar(80) NULL`, `EvaluationRef varchar(80) NULL`, `RecordCount bigint NULL`, `SourceDigestSha char(64) NULL`, `QueryDigestSha char(64) NULL`, `CapturedAtUtc`, `IssueCode varchar(60) NULL`. Source refs are opaque approved snapshot identifiers, **not physical table names/queries or raw rows**. UNIQUE(WeekId, FeedCode, SourceDigestSha) can use a filtered unique index with appropriate null semantics; immutable after accepted.

### G10 `FOCUS_FN_GridironRatingRun`
**Grain:** one week-scoped, policy-bound computed rating batch/revision. PK `RatingRunId`; `ScopeKey`, `WeekId`, `PolicyId`, `Revision int`, `State varchar(16)` [PREVIEW,FROZEN,SUPERSEDED,REJECTED], `EvidenceCutoffUtc`, `SourceDigestSha char(64)`, `SnapshotSha char(64)`, `AlgorithmVersion varchar(32)`, `SupersedesRatingRunId uniqueidentifier NULL`, `CreatedAtUtc`, `FrozenAtUtc NULL`, `IssueCode NULL`. UNIQUE(WeekId,Revision). One frozen rating run selected by an explicitly controlled head projection, not max wall-clock of unreviewed records.

### G11 `FOCUS_FN_GridironPlayerRating`
**Grain:** one participating person per rating run. Composite PK (`RatingRunId`, `PersonId`); `ScopeKey`, `Status varchar(16)` [READY,PROVISIONAL,UNKNOWN,EXCLUDED], `OverallRating tinyint NULL` (0..100), `Confidence decimal(5,4) NULL` (0..1), `EligibleShiftCount int NULL`, `EligiblePersonMinutes decimal(18,3) NULL`, `RoleFitJson nvarchar(max) NULL`, `ComponentsJson nvarchar(max) NULL`, `EvidenceSha char(64) NULL`, `ReasonCode varchar(64) NULL`. READY cannot have null score; UNKNOWN/EXCLUDED must have null; PROVISIONAL may be null or scored with explicit fixture-only prior. Snapshot immutability. Same person/scope as run.

### G12 `FOCUS_FN_GridironAttribute`
**Grain:** player x rating-run x attribute. Composite PK (`RatingRunId`, `PersonId`, `AttributeCode`); `ScopeKey`, `AttributeCode varchar(24)` [SPEED,HANDS,AWARENESS,POWER,TEAMWORK,DISCIPLINE,VERSATILITY,PLAYMAKING], `Score tinyint NULL`, `NormalizedNumerator decimal(19,6) NULL`, `NormalizedDenominator decimal(19,6) NULL`, `SampleCount int NULL`, `Confidence decimal(5,4) NULL`, `EvidenceSha char(64) NULL`, `ReasonCode varchar(64) NULL`. Zero is an actual validated score; missing is NULL. No sensitive evidence rows. This is **the canonical per-attribute record**, not a web calculation.

### G13 `FOCUS_FN_GridironMatch`
**Grain:** one immutable game simulation revision. PK `MatchId`; `ScopeKey`, `SeasonId`, `WeekId`, `HomeTeamId`, `AwayTeamId`, `RatingRunId`, `MatchSlot int`, `Revision int`, `Seed bigint`, `EngineVersion varchar(32)`, `State varchar(16)` [SCHEDULED,SIMULATED,VOIDED], `HomeScore smallint NULL`, `AwayScore smallint NULL`, `BoxScoreSha char(64) NULL`, `PlayTraceSha char(64) NULL`, `SupersedesMatchId uniqueidentifier NULL`, `CreatedAtUtc`, `SimulatedAtUtc NULL`, `CorrectionCode varchar(60) NULL`. UNIQUE(WeekId, MatchSlot, Revision); team IDs differ; scores nonnegative. Ratings held fixed as of selected run. Never silently mutate prior game because of late correction.

### G14 `FOCUS_FN_GridironLineup`
**Grain:** one match x side x slot lineup entry. Composite PK (`MatchId`, `SideCode`, `PositionCode`, `SlotNumber`); `ScopeKey`, `TeamId`, `PersonId uniqueidentifier NULL` for fictional neutral fill-ins, `SyntheticAlias nvarchar(40) NULL`, `IsSynthetic bit`, `OverallAtKickoff tinyint`, `PositionRatingAtKickoff tinyint`, `LineupDigestSha char(64)`. A real participant requires opt-in+roster+rating; synthetic entries never count as a real person or for employee achievements; one real person not duplicated on same side. `SideCode` [OFFENSE,DEFENSE,SPECIAL], positions QB/RB/WR/TE/OL/DL/LB/DB/K/P and common variants versioned in simulator policy.

### G15 `FOCUS_FN_GridironPlay`
**Grain:** one ordered simulated play per match. Composite PK (`MatchId`,`PlayNumber`); `ScopeKey`, `QuarterNumber tinyint`, `ClockSecondsRemaining smallint`, `OffenseTeamId`, `DefenseTeamId`, `DownNumber tinyint NULL`, `DistanceToGo tinyint NULL`, `YardLine tinyint`, `PlayType varchar(24)`, `OutcomeCode varchar(32)`, `YardsGained smallint`, `PointsScored tinyint`, `PrimaryPersonId uniqueidentifier NULL`, `PlayJson nvarchar(max)`, `PlaySha char(64)`. JSON stores replay move vectors and safe aliases/IDs only, not labor sources. Bound play count in engine and writer; 0..100 yard line. Index match/play number clustered.

### G16 `FOCUS_FN_GridironBoxStat`
**Grain:** one match x person-or-synthetic-athlete x stat code. PK `BoxStatId`; `ScopeKey`, `MatchId`, `TeamId`, `PersonId NULL`, `SyntheticSlotCode varchar(40) NULL`, `StatCode varchar(32)`, `StatValue decimal(18,3)`, `EvidencePlayDigestSha char(64)`. Either PersonId or SyntheticSlotCode, not both. Examples: pass_att, pass_cmp, pass_yards, rush_att, rush_yards, rec, rec_yards, sacks, tackles, interceptions, fumbles, tds, fg_made, fg_att. Recompute/verify from plays before persistence.

### G17 `FOCUS_FN_GridironAchievement`
**Grain:** one versioned sports achievement event. PK `AchievementId`; `ScopeKey`, `WeekId`, `PersonId NULL`, `TeamId NULL`, `AchievementCode varchar(40)`, `State varchar(12)` [AWARDED,REVOKED], `PolicyId`, `EvidenceSha char(64)`, `Revision int`, `SupersedesAchievementId NULL`, `AwardedAtUtc`. Award on reviewed game/approved evidence only; never for raw defect report counts. Unique dedupe key per subject, week, achievement, revision established in app and/or indexed persisted key.

### G18 `FOCUS_FN_GridironAudit`
**Grain:** one immutable administrative action/event. PK `AuditId`; `ScopeKey`, `EventKind varchar(40)`, `ActorSubjectHash char(64)`, `EntityKind varchar(40)`, `EntityId uniqueidentifier`, `RequestId varchar(80) NULL`, `IdempotencyKey varchar(128) NULL`, `ReasonCode varchar(64)`, `BeforeSha char(64) NULL`, `AfterSha char(64) NULL`, `CreatedAtUtc`. No names or free-text PII. Index (ScopeKey,CreatedAtUtc DESC). Auditable for enrollment, policy approval, freeze, correction, resimulation and administration.

## 2. Physical relationship summary

```text
GridironPolicy --< Season --< Week --< SourceReceipt
              \                \--< RatingRun --< PlayerRating --< Attribute
               \                                        |
Team --< SeasonTeam --< Season  Person --< Enrollment | Roster
Team --< Roster -- Person            Match --< Lineup
Week + RatingRun + Home/Away Team --< Match --< Play
                                            \--< BoxStat
Week + Person/Team --< Achievement                 Audit (append-only)
```

All IDs are game-domain IDs. No FK to HR/source table required or allowed for public fixture. Real company adapters resolve keys inside controlled environment only.

## 3. Read API data contracts and data ownership

| Proposed endpoint | Read projection | Privacy and semantics |
|---|---|---|
| `GET /api/v1/gridiron/context` | flags, current season/week, available data and policy, caller enrollment | authenticated and scoped |
| `GET /api/v1/gridiron/me` | own alias, opt-in, attribute/rating with evidence confidence, position fits | own identity only; no `person_id` request override |
| `GET /api/v1/gridiron/teams` | team names, standing, next match, roster counts | aggregate; suppress small cohorts |
| `GET /api/v1/gridiron/season/{id}` | schedule and standings from latest nonvoided match revision | league-scoped |
| `GET /api/v1/gridiron/matches/{id}` | scoreboard, play trace, lineups and box scores | only consented aliases/synthetic |
| `GET /api/v1/gridiron/ratings/{week_id}` | approved aggregate distribution/counts, no raw personal metrics | admin-only / aggregate |
| `GET /api/v1/gridiron/diagnostics` | missing feeds, stale run and published revision status | authorized operator only |
| `POST /api/v1/gridiron/enroll` | queued enrollment command with idempotency/CSRF | user can act for self only |
| `POST /api/v1/gridiron/weeks/{id}/freeze` | worker run request | authorized admin, policy/readiness gate |
| `POST /api/v1/gridiron/matches/{id}/simulate` | worker run request | admin, immutable rating run, idempotent |

Use existing `REGISTRY`, `ApiResult`, strict schemas, CSRF, durable worker runs and canonical envelope. Do NOT expose a freely listable endpoint for all individual employee scores. MCP resource exposure is separately permission gated and disabled by default live.

## 4. Recommended indexes / constraints / load profiles

- Parent unique keys: `(ScopeKey, PolicyId)`, `(ScopeKey, SeasonId)`, `(ScopeKey, WeekId)`, `(ScopeKey, TeamId)`, `(ScopeKey, PersonId)`, `(ScopeKey, RatingRunId)`, `(ScopeKey, MatchId)`.
- `Week(SeasonId,WeekNumber)` unique, `RatingRun(WeekId,Revision)` unique, `Match(WeekId,MatchSlot,Revision)` unique. Append-only superseding revisions; a view or explicit head selects the current published record.
- `Roster(ScopeKey,SeasonId,TeamId,EffectiveFromWeek)` INCLUDE person/status; validate non-overlap per individual in transaction.
- `SourceReceipt(ScopeKey,WeekId,FeedCode,CapturedAtUtc)`; `PlayerRating(RatingRunId,PersonId)`; `Attribute(RatingRunId,PersonId,AttributeCode)`; `Play(MatchId,PlayNumber)`; `BoxStat(MatchId,TeamId,StatCode)`; `Audit(ScopeKey,CreatedAtUtc)`.
- Composite tenant scope FKs for all parents in actual deployment. If DDL draft doesn't include every relationship, treat as deployment hard blocker until fixed.
- Store no raw person-time intervals or sensitive reasons in Grid tables. Raw evidence stays in its authorized FOCUS producer; hashes/receipts suffice for reproducible policy computation in work environment.
- Align with current DB collation, use Unicode for UI text and parameterized SQL for all values. Never dynamically build table names from user values.
- Transactional publication: insert children, verify hashes/counts, then publish rating/game head atomically with expected version or `sp_getapplock` and strict idempotency. No half-published simulated game.
- At most a few hundred plays per match and a few hundred players per week; bulk insert within worker, pagination on reads and no N+1 query pattern. Validate indexes under actual workload before deployment.

## 5. Retention, erasure, operations

Owner sets explicit retention (suggest illustrative: ephemeral raw derived metrics 30 days, weekly score/play snapshot 1 season + appeals window, audit per company schedule; **not a corporate policy**). Opt-out stops future inclusion; define whether historical anonymized matches may remain and how references are masked. Implement deletion or pseudonymization with approved retention/disclosure rules, NOT a silent hard delete that corrupts historical scores. Provide revisioned corrections, authorizations and a documented appeal process for inaccurate attribution.

Feature flags are off by default. Backup and rollback approved before migrations. DDL `CREATE TABLE` is review-only; no `DROP`, `TRUNCATE`, or writes to existing FOCUS tables. SQLStore adapter uses service principal with only approved Grid CRUD rights, source reader remains SELECT-only, and code must not bypass `focus/data/sql.py` allowlist.

## 6. Concrete source-to-feature dependency matrix

| Rating input | Existing FOCUS reference | Additional producer/proof needed | Fail closed |
|---|---|---|---|
| Baseline work effort | `flow_budget` | accepted standards + crew semantics | UNKNOWN if invalid |
| Individual actual effort | `actual_labor`, `labor_shift`, `clock_observations` | verified attributable intervals, corrections/voids and person-share; the aggregate feeds alone may not suffice | UNKNOWN |
| First-pass quality | `rework_classification`, `defect_evidence` observation feed | approved attribution of reporter / originator / resolver and actual review status | UNKNOWN |
| Defects found | `defect_evidence` | verified true findings, classification, duplicate closure, opt-out-safe identity | UNKNOWN, no raw counts |
| Plan alignment | accepted plan, `frozen_goal`, lifecycle, `ActualInterval` | person's accepted assignment + explicit approved overrides | UNKNOWN |
| Impact on flow | `pnet_dag`, `neo_graph_rework`, validated plan, actual observations | authorized matched counterfactual and contribution allocation | unavailable until validated |
| Attendance / LWOP | `attendance_shift`, `availability_exceptions` | verified participation exposure only; never a rating/punishment | excludable window only |
| Kiosk punch and correction | `clock_observations`, `actual_labor` where verified | owner-approved correction validity; not raw number of edits | neutral until approved |
| Skill breadth | `mechanic_certs`, `soi_cert_requirements`, `requirement_census` | approved, effective qualification and privacy check | UNKNOWN |
| Assists / handoffs | actual intervals + verified lifecycle | authoritative crew contribution and handoff event | UNKNOWN |

**Caution:** specific supplemental datasets are documented as NOT_PROBED for live use in the repo as of the reviewed revision. No number of schema fields guarantees that actual internal evidence exists. Work-side agent must map and validate each producer privately.

## 7. Migration execution gate
Before any live deploy: DBA approves DDL/DDL checks and least privileges; compliance approves limited employee use; source owners validate feed definitions and clocks; HR/labor relations approve participation and messaging; Quality/Safety approve incentive design; owner signs rating policy; independent test verifies no exports from corporate data to public environments; load, permission, rollback and backup tests pass. Until then operate fully synthetic in fixture mode.


## 8. Production-team starting-lineup storage clarification
Each `GridironRoster` row is drawn from the *same production team's* consented, effective-dated membership; no mixed fantasy roster. `GridironLineup` is **a persisted, per-match, per-team, per-unit starting XI and depth-chart snapshot** chosen from that team's frozen eligible teammates via deterministic maximum-weight positional matching. Its existing columns `MatchId,TeamId,SideCode,PositionCode,SlotNumber,PersonId,IsSynthetic,OverallAtKickoff,PositionRatingAtKickoff,LineupDigestSha` allow complete starter roster capture; add `LineupRole varchar(12)` [STARTER,BENCH,SPECIAL] or a distinct, versioned `GridironDepthChart` after DBA review if starters and bench coexist. An explicit `SelectionRank int`, `SelectionScore decimal(9,4)`, `SelectionPolicySha char(64)`, and `EligibilityReceiptSha char(64)` should be included for auditable best-performer choice. In live production `IsSynthetic=0` and `PersonId IS NOT NULL` for all starters; if fewer than 11 eligible consented teammates, block simulation with `ROSTER_INCOMPLETE`, do not borrow players or fabricate starters. A team may field 11 distinct offensive and 11 distinct defensive positions from its own teammates with permitted two-way crossover; enforce all 11 slots and the production-team membership in transactional application validation. Keep historical lineup revisions immutable.
