-- FOCUS Gridiron | 001_draft.sql | proposal ONLY (not executed).
-- Review alongside docs/gridiron/SQL_SERVER_SCHEMA.md and SOURCE_MAPPING_AND_GOVERNANCE.md.
-- SQL Server 2022. No live personnel/source records, credentials or table mappings.
-- Proposed WEB_APP schema must exist (the company DBA chooses final schema).
-- This is not an installation permission; execution requires approved migration window,
-- rollback and backups, privacy/labor/quality/safety/security review.
-- No ALTER/DROP of FOCUS core tables; dedicated FOCUS_FN_Gridiron* objects only.
-- Additional application/transaction validations required for roster intervals, opt-in,
-- lineup best-fit, unique participants, 11 legal slots, temporal consistency, immutability.
SET XACT_ABORT ON;
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironPolicy (
  PolicyId uniqueidentifier NOT NULL,
  Version int NOT NULL CHECK (Version>=1),
  Status varchar(16) NOT NULL CHECK (Status IN ('DRAFT','DEMONSTRATION','APPROVED','RETIRED')),
  DefinitionJson nvarchar(max) NOT NULL CHECK (ISJSON(DefinitionJson)=1),
  DefinitionSha char(64) NOT NULL,
  ApprovalRefHash char(64) NULL,
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GPolicy PRIMARY KEY (PolicyId),
  CONSTRAINT UQ_GPolicy_ScopeId UNIQUE (ScopeKey,PolicyId),
  CONSTRAINT UQ_GPolicy_Version UNIQUE (ScopeKey,Version)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironSeason (
  SeasonId uniqueidentifier NOT NULL,
  PolicyId uniqueidentifier NOT NULL,
  SeasonCode varchar(40) NOT NULL,
  Title nvarchar(100) NOT NULL,
  StartUtc datetime2(6) NOT NULL,
  EndUtc datetime2(6) NOT NULL,
  State varchar(16) NOT NULL CHECK (State IN ('DRAFT','ACTIVE','FINISHED','CANCELLED')),
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GSeason PRIMARY KEY (SeasonId),
  CONSTRAINT UQ_GSeason_ScopeId UNIQUE (ScopeKey,SeasonId),
  CONSTRAINT UQ_GSeason_Code UNIQUE (ScopeKey,SeasonCode),
  CONSTRAINT FK_GSeason_PolicyId FOREIGN KEY (ScopeKey,PolicyId) REFERENCES WEB_APP.FOCUS_FN_GridironPolicy(ScopeKey,PolicyId),
  CONSTRAINT CK_GSeason_Dates CHECK (EndUtc>StartUtc)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironWeek (
  WeekId uniqueidentifier NOT NULL,
  SeasonId uniqueidentifier NOT NULL,
  WeekNumber int NOT NULL CHECK (WeekNumber>=1),
  WeekStartUtc datetime2(6) NOT NULL,
  WeekEndUtc datetime2(6) NOT NULL,
  EvidenceCutoffUtc datetime2(6) NULL,
  State varchar(16) NOT NULL CHECK (State IN ('UPCOMING','OPEN','RATING_FROZEN','SIMULATED','CLOSED')),
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GWeek PRIMARY KEY (WeekId),
  CONSTRAINT UQ_GWeek_ScopeId UNIQUE (ScopeKey,WeekId),
  CONSTRAINT UQ_GWeek_Number UNIQUE (ScopeKey,SeasonId,WeekNumber),
  CONSTRAINT FK_GWeek_SeasonId FOREIGN KEY (ScopeKey,SeasonId) REFERENCES WEB_APP.FOCUS_FN_GridironSeason(ScopeKey,SeasonId),
  CONSTRAINT CK_GWeek_Dates CHECK (WeekEndUtc>WeekStartUtc AND (EvidenceCutoffUtc IS NULL OR EvidenceCutoffUtc>=WeekEndUtc))
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironTeam (
  TeamId uniqueidentifier NOT NULL,
  TeamCode varchar(40) NOT NULL,
  DisplayName nvarchar(100) NOT NULL,
  ShortName nvarchar(20) NOT NULL,
  ThemeCode varchar(32) NOT NULL,
  IsActive bit NOT NULL DEFAULT 1,
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GTeam PRIMARY KEY (TeamId),
  CONSTRAINT UQ_GTeam_ScopeId UNIQUE (ScopeKey,TeamId),
  CONSTRAINT UQ_GTeam_Code UNIQUE (ScopeKey,TeamCode)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironSeasonTeam (
  SeasonId uniqueidentifier NOT NULL,
  TeamId uniqueidentifier NOT NULL,
  DivisionCode varchar(32) NULL,
  JoinedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  Seed int NULL CHECK (Seed IS NULL OR Seed>=1),
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GSeasonTeam PRIMARY KEY (ScopeKey,SeasonId,TeamId),
  CONSTRAINT FK_GSeasonTeam_SeasonId FOREIGN KEY (ScopeKey,SeasonId) REFERENCES WEB_APP.FOCUS_FN_GridironSeason(ScopeKey,SeasonId),
  CONSTRAINT FK_GSeasonTeam_TeamId FOREIGN KEY (ScopeKey,TeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironPerson (
  PersonId uniqueidentifier NOT NULL,
  IdentityTokenHash binary(32) NULL,
  PublicAlias nvarchar(40) NOT NULL,
  PreferredPosition varchar(4) NULL,
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  RetiredAtUtc datetime2(6) NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GPerson PRIMARY KEY (PersonId),
  CONSTRAINT UQ_GPerson_ScopeId UNIQUE (ScopeKey,PersonId),
  CONSTRAINT UQ_GPerson_Alias UNIQUE (ScopeKey,PublicAlias)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironEnrollment (
  EnrollmentId uniqueidentifier NOT NULL,
  SeasonId uniqueidentifier NOT NULL,
  PersonId uniqueidentifier NOT NULL,
  Decision varchar(16) NOT NULL CHECK (Decision IN ('OPT_IN','OPT_OUT')),
  DecisionAtUtc datetime2(6) NOT NULL,
  EffectiveAtUtc datetime2(6) NOT NULL,
  Source varchar(16) NOT NULL CHECK (Source IN ('SELF','ADMIN_APPROVED')),
  ProofHash char(64) NULL,
  SupersedesEnrollmentId uniqueidentifier NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GEnrollment PRIMARY KEY (EnrollmentId),
  CONSTRAINT UQ_GEnrollment_ScopeId UNIQUE (ScopeKey,EnrollmentId),
  CONSTRAINT FK_GEnrollment_SeasonId FOREIGN KEY (ScopeKey,SeasonId) REFERENCES WEB_APP.FOCUS_FN_GridironSeason(ScopeKey,SeasonId),
  CONSTRAINT FK_GEnrollment_PersonId FOREIGN KEY (ScopeKey,PersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,PersonId),
  CONSTRAINT FK_GEnrollment_SupersedesEnrollmentId FOREIGN KEY (ScopeKey,SupersedesEnrollmentId) REFERENCES WEB_APP.FOCUS_FN_GridironEnrollment(ScopeKey,EnrollmentId)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironRoster (
  RosterId uniqueidentifier NOT NULL,
  SeasonId uniqueidentifier NOT NULL,
  TeamId uniqueidentifier NOT NULL,
  PersonId uniqueidentifier NOT NULL,
  EffectiveFromWeek int NOT NULL CHECK (EffectiveFromWeek>=1),
  EffectiveThroughWeek int NULL,
  PositionPreference varchar(4) NULL,
  Status varchar(16) NOT NULL CHECK (Status IN ('ACTIVE','BENCH','TRANSFERRED','RELEASED')),
  OriginReceiptHash char(64) NULL,
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GRoster PRIMARY KEY (RosterId),
  CONSTRAINT UQ_GRoster_ScopeId UNIQUE (ScopeKey,RosterId),
  CONSTRAINT FK_GRoster_SeasonId FOREIGN KEY (ScopeKey,SeasonId) REFERENCES WEB_APP.FOCUS_FN_GridironSeason(ScopeKey,SeasonId),
  CONSTRAINT FK_GRoster_TeamId FOREIGN KEY (ScopeKey,TeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GRoster_PersonId FOREIGN KEY (ScopeKey,PersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,PersonId),
  CONSTRAINT FK_GRoster_SeasonTeam FOREIGN KEY (ScopeKey,SeasonId,TeamId) REFERENCES WEB_APP.FOCUS_FN_GridironSeasonTeam(ScopeKey,SeasonId,TeamId),
  CONSTRAINT CK_GRoster_Interval CHECK (EffectiveThroughWeek IS NULL OR EffectiveThroughWeek>=EffectiveFromWeek)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironSourceReceipt (
  ReceiptId uniqueidentifier NOT NULL,
  WeekId uniqueidentifier NOT NULL,
  FeedCode varchar(40) NOT NULL,
  CoverageStatus varchar(24) NOT NULL CHECK (CoverageStatus IN ('ROWS','CERTIFIED_EMPTY','EMPTY_UNCERTIFIED','NOT_PROBED','FAILED')),
  CapturedThroughUtc datetime2(6) NULL,
  SourceRunRef varchar(80) NULL,
  ObservationRunRef varchar(80) NULL,
  EvaluationRef varchar(80) NULL,
  RecordCount bigint NULL CHECK (RecordCount IS NULL OR RecordCount>=0),
  SourceDigestSha char(64) NULL,
  QueryDigestSha char(64) NULL,
  CapturedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  IssueCode varchar(60) NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GSourceReceipt PRIMARY KEY (ReceiptId),
  CONSTRAINT UQ_GSourceReceipt_ScopeId UNIQUE (ScopeKey,ReceiptId),
  CONSTRAINT FK_GSourceReceipt_WeekId FOREIGN KEY (ScopeKey,WeekId) REFERENCES WEB_APP.FOCUS_FN_GridironWeek(ScopeKey,WeekId)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironRatingRun (
  RatingRunId uniqueidentifier NOT NULL,
  WeekId uniqueidentifier NOT NULL,
  PolicyId uniqueidentifier NOT NULL,
  Revision int NOT NULL CHECK (Revision>=1),
  State varchar(16) NOT NULL CHECK (State IN ('PREVIEW','FROZEN','SUPERSEDED','REJECTED')),
  EvidenceCutoffUtc datetime2(6) NOT NULL,
  SourceDigestSha char(64) NOT NULL,
  SnapshotSha char(64) NOT NULL,
  AlgorithmVersion varchar(32) NOT NULL,
  SupersedesRatingRunId uniqueidentifier NULL,
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  FrozenAtUtc datetime2(6) NULL,
  IssueCode varchar(60) NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GRatingRun PRIMARY KEY (RatingRunId),
  CONSTRAINT UQ_GRatingRun_ScopeId UNIQUE (ScopeKey,RatingRunId),
  CONSTRAINT UQ_GRatingRun_WeekRevision UNIQUE (ScopeKey,WeekId,Revision),
  CONSTRAINT FK_GRatingRun_WeekId FOREIGN KEY (ScopeKey,WeekId) REFERENCES WEB_APP.FOCUS_FN_GridironWeek(ScopeKey,WeekId),
  CONSTRAINT FK_GRatingRun_PolicyId FOREIGN KEY (ScopeKey,PolicyId) REFERENCES WEB_APP.FOCUS_FN_GridironPolicy(ScopeKey,PolicyId),
  CONSTRAINT FK_GRatingRun_SupersedesRatingRunId FOREIGN KEY (ScopeKey,SupersedesRatingRunId) REFERENCES WEB_APP.FOCUS_FN_GridironRatingRun(ScopeKey,RatingRunId)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironPlayerRating (
  RatingRunId uniqueidentifier NOT NULL,
  PersonId uniqueidentifier NOT NULL,
  Status varchar(16) NOT NULL CHECK (Status IN ('READY','PROVISIONAL','UNKNOWN','EXCLUDED')),
  OverallRating tinyint NULL CHECK (OverallRating IS NULL OR OverallRating<=100),
  Confidence decimal(5,4) NULL CHECK (Confidence IS NULL OR Confidence BETWEEN 0 AND 1),
  EligibleShiftCount int NULL CHECK (EligibleShiftCount IS NULL OR EligibleShiftCount>=0),
  EligiblePersonMinutes decimal(18,3) NULL CHECK (EligiblePersonMinutes IS NULL OR EligiblePersonMinutes>=0),
  RoleFitJson nvarchar(max) NULL CHECK (RoleFitJson IS NULL OR ISJSON(RoleFitJson)=1),
  ComponentsJson nvarchar(max) NULL CHECK (ComponentsJson IS NULL OR ISJSON(ComponentsJson)=1),
  EvidenceSha char(64) NULL,
  ReasonCode varchar(64) NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GPlayerRating PRIMARY KEY (RatingRunId,PersonId),
  CONSTRAINT UQ_GPlayerRating_ScopeRef UNIQUE (ScopeKey,RatingRunId,PersonId),
  CONSTRAINT FK_GPlayerRating_RatingRunId FOREIGN KEY (ScopeKey,RatingRunId) REFERENCES WEB_APP.FOCUS_FN_GridironRatingRun(ScopeKey,RatingRunId),
  CONSTRAINT FK_GPlayerRating_PersonId FOREIGN KEY (ScopeKey,PersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,PersonId),
  CONSTRAINT CK_GPlayerRating_Score CHECK ((Status='READY' AND OverallRating IS NOT NULL) OR (Status IN ('UNKNOWN','EXCLUDED') AND OverallRating IS NULL) OR Status='PROVISIONAL')
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironAttribute (
  RatingRunId uniqueidentifier NOT NULL,
  PersonId uniqueidentifier NOT NULL,
  AttributeCode varchar(24) NOT NULL CHECK (AttributeCode IN ('SPEED','HANDS','AWARENESS','POWER','TEAMWORK','DISCIPLINE','VERSATILITY','PLAYMAKING')),
  Score tinyint NULL CHECK (Score IS NULL OR Score<=100),
  NormalizedNumerator decimal(19,6) NULL,
  NormalizedDenominator decimal(19,6) NULL,
  SampleCount int NULL CHECK (SampleCount IS NULL OR SampleCount>=0),
  Confidence decimal(5,4) NULL CHECK (Confidence IS NULL OR Confidence BETWEEN 0 AND 1),
  EvidenceSha char(64) NULL,
  ReasonCode varchar(64) NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GAttribute PRIMARY KEY (RatingRunId,PersonId,AttributeCode),
  CONSTRAINT FK_GAttribute_PlayerRating FOREIGN KEY (ScopeKey,RatingRunId,PersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPlayerRating(ScopeKey,RatingRunId,PersonId)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironMatch (
  MatchId uniqueidentifier NOT NULL,
  SeasonId uniqueidentifier NOT NULL,
  WeekId uniqueidentifier NOT NULL,
  HomeTeamId uniqueidentifier NOT NULL,
  AwayTeamId uniqueidentifier NOT NULL,
  RatingRunId uniqueidentifier NOT NULL,
  MatchSlot int NOT NULL CHECK (MatchSlot>=1),
  Revision int NOT NULL CHECK (Revision>=1),
  Seed bigint NOT NULL,
  EngineVersion varchar(32) NOT NULL,
  State varchar(16) NOT NULL CHECK (State IN ('SCHEDULED','SIMULATED','VOIDED','ROSTER_INCOMPLETE')),
  HomeScore smallint NULL CHECK (HomeScore IS NULL OR HomeScore>=0),
  AwayScore smallint NULL CHECK (AwayScore IS NULL OR AwayScore>=0),
  BoxScoreSha char(64) NULL,
  PlayTraceSha char(64) NULL,
  SupersedesMatchId uniqueidentifier NULL,
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  SimulatedAtUtc datetime2(6) NULL,
  CorrectionCode varchar(60) NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GMatch PRIMARY KEY (MatchId),
  CONSTRAINT UQ_GMatch_ScopeId UNIQUE (ScopeKey,MatchId),
  CONSTRAINT UQ_GMatch_SlotRev UNIQUE (ScopeKey,WeekId,MatchSlot,Revision),
  CONSTRAINT FK_GMatch_SeasonId FOREIGN KEY (ScopeKey,SeasonId) REFERENCES WEB_APP.FOCUS_FN_GridironSeason(ScopeKey,SeasonId),
  CONSTRAINT FK_GMatch_WeekId FOREIGN KEY (ScopeKey,WeekId) REFERENCES WEB_APP.FOCUS_FN_GridironWeek(ScopeKey,WeekId),
  CONSTRAINT FK_GMatch_HomeTeamId FOREIGN KEY (ScopeKey,HomeTeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GMatch_AwayTeamId FOREIGN KEY (ScopeKey,AwayTeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GMatch_RatingRunId FOREIGN KEY (ScopeKey,RatingRunId) REFERENCES WEB_APP.FOCUS_FN_GridironRatingRun(ScopeKey,RatingRunId),
  CONSTRAINT FK_GMatch_SupersedesMatchId FOREIGN KEY (ScopeKey,SupersedesMatchId) REFERENCES WEB_APP.FOCUS_FN_GridironMatch(ScopeKey,MatchId),
  CONSTRAINT CK_GMatch_Teams CHECK (HomeTeamId<>AwayTeamId),
  CONSTRAINT CK_GMatch_Scores CHECK (State<>'SIMULATED' OR (HomeScore IS NOT NULL AND AwayScore IS NOT NULL AND BoxScoreSha IS NOT NULL AND PlayTraceSha IS NOT NULL))
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironLineup (
  MatchId uniqueidentifier NOT NULL,
  TeamId uniqueidentifier NOT NULL,
  SideCode varchar(8) NOT NULL CHECK (SideCode IN ('OFFENSE','DEFENSE','SPECIAL')),
  PositionCode varchar(4) NOT NULL,
  SlotNumber tinyint NOT NULL CHECK (SlotNumber>=1),
  LineupRole varchar(12) NOT NULL CHECK (LineupRole IN ('STARTER','BENCH','SUB')),
  PersonId uniqueidentifier NULL,
  SyntheticAlias nvarchar(40) NULL,
  IsSynthetic bit NOT NULL,
  OverallAtKickoff tinyint NOT NULL CHECK (OverallAtKickoff<=100),
  PositionRatingAtKickoff tinyint NOT NULL CHECK (PositionRatingAtKickoff<=100),
  SelectionRank int NOT NULL CHECK (SelectionRank>=1),
  SelectionScore decimal(9,4) NOT NULL,
  SelectionPolicySha char(64) NOT NULL,
  EligibilityReceiptSha char(64) NOT NULL,
  LineupDigestSha char(64) NOT NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GLineup PRIMARY KEY (MatchId,TeamId,SideCode,PositionCode,SlotNumber,LineupRole),
  CONSTRAINT FK_GLineup_MatchId FOREIGN KEY (ScopeKey,MatchId) REFERENCES WEB_APP.FOCUS_FN_GridironMatch(ScopeKey,MatchId),
  CONSTRAINT FK_GLineup_TeamId FOREIGN KEY (ScopeKey,TeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GLineup_PersonId FOREIGN KEY (ScopeKey,PersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,PersonId),
  CONSTRAINT CK_GLineup_Synthetic CHECK ((IsSynthetic=1 AND PersonId IS NULL AND SyntheticAlias IS NOT NULL) OR (IsSynthetic=0 AND PersonId IS NOT NULL AND SyntheticAlias IS NULL))
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironPlay (
  MatchId uniqueidentifier NOT NULL,
  PlayNumber int NOT NULL CHECK (PlayNumber>=1),
  QuarterNumber tinyint NOT NULL CHECK (QuarterNumber BETWEEN 1 AND 8),
  ClockSecondsRemaining smallint NOT NULL CHECK (ClockSecondsRemaining BETWEEN 0 AND 900),
  OffenseTeamId uniqueidentifier NOT NULL,
  DefenseTeamId uniqueidentifier NOT NULL,
  DownNumber tinyint NULL CHECK (DownNumber IS NULL OR DownNumber BETWEEN 1 AND 4),
  DistanceToGo tinyint NULL CHECK (DistanceToGo IS NULL OR DistanceToGo BETWEEN 1 AND 100),
  YardLine tinyint NOT NULL CHECK (YardLine<=100),
  PlayType varchar(24) NOT NULL,
  OutcomeCode varchar(32) NOT NULL,
  YardsGained smallint NOT NULL,
  PointsScored tinyint NOT NULL CHECK (PointsScored<=8),
  PrimaryPersonId uniqueidentifier NULL,
  PlayJson nvarchar(max) NOT NULL CHECK (ISJSON(PlayJson)=1),
  PlaySha char(64) NOT NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GPlay PRIMARY KEY (MatchId,PlayNumber),
  CONSTRAINT FK_GPlay_MatchId FOREIGN KEY (ScopeKey,MatchId) REFERENCES WEB_APP.FOCUS_FN_GridironMatch(ScopeKey,MatchId),
  CONSTRAINT FK_GPlay_OffenseTeamId FOREIGN KEY (ScopeKey,OffenseTeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GPlay_DefenseTeamId FOREIGN KEY (ScopeKey,DefenseTeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GPlay_PrimaryPersonId FOREIGN KEY (ScopeKey,PrimaryPersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,PersonId)
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironBoxStat (
  BoxStatId uniqueidentifier NOT NULL,
  MatchId uniqueidentifier NOT NULL,
  TeamId uniqueidentifier NOT NULL,
  PersonId uniqueidentifier NULL,
  SyntheticSlotCode varchar(40) NULL,
  StatCode varchar(32) NOT NULL,
  StatValue decimal(18,3) NOT NULL,
  EvidencePlayDigestSha char(64) NOT NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GBoxStat PRIMARY KEY (BoxStatId),
  CONSTRAINT FK_GBoxStat_MatchId FOREIGN KEY (ScopeKey,MatchId) REFERENCES WEB_APP.FOCUS_FN_GridironMatch(ScopeKey,MatchId),
  CONSTRAINT FK_GBoxStat_TeamId FOREIGN KEY (ScopeKey,TeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GBoxStat_PersonId FOREIGN KEY (ScopeKey,PersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,PersonId),
  CONSTRAINT CK_GBoxStat_PersonOrSynthetic CHECK ((PersonId IS NOT NULL AND SyntheticSlotCode IS NULL) OR (PersonId IS NULL AND SyntheticSlotCode IS NOT NULL))
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironAchievement (
  AchievementId uniqueidentifier NOT NULL,
  WeekId uniqueidentifier NOT NULL,
  PersonId uniqueidentifier NULL,
  TeamId uniqueidentifier NULL,
  AchievementCode varchar(40) NOT NULL,
  State varchar(12) NOT NULL CHECK (State IN ('AWARDED','REVOKED')),
  PolicyId uniqueidentifier NOT NULL,
  EvidenceSha char(64) NOT NULL,
  Revision int NOT NULL CHECK (Revision>=1),
  SupersedesAchievementId uniqueidentifier NULL,
  AwardedAtUtc datetime2(6) NOT NULL,
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GAchievement PRIMARY KEY (AchievementId),
  CONSTRAINT UQ_GAchievement_ScopeId UNIQUE (ScopeKey,AchievementId),
  CONSTRAINT FK_GAchievement_WeekId FOREIGN KEY (ScopeKey,WeekId) REFERENCES WEB_APP.FOCUS_FN_GridironWeek(ScopeKey,WeekId),
  CONSTRAINT FK_GAchievement_PersonId FOREIGN KEY (ScopeKey,PersonId) REFERENCES WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,PersonId),
  CONSTRAINT FK_GAchievement_TeamId FOREIGN KEY (ScopeKey,TeamId) REFERENCES WEB_APP.FOCUS_FN_GridironTeam(ScopeKey,TeamId),
  CONSTRAINT FK_GAchievement_PolicyId FOREIGN KEY (ScopeKey,PolicyId) REFERENCES WEB_APP.FOCUS_FN_GridironPolicy(ScopeKey,PolicyId),
  CONSTRAINT FK_GAchievement_SupersedesAchievementId FOREIGN KEY (ScopeKey,SupersedesAchievementId) REFERENCES WEB_APP.FOCUS_FN_GridironAchievement(ScopeKey,AchievementId),
  CONSTRAINT CK_GAchievement_Subject CHECK ((PersonId IS NOT NULL AND TeamId IS NULL) OR (PersonId IS NULL AND TeamId IS NOT NULL))
);
GO

CREATE TABLE WEB_APP.FOCUS_FN_GridironAudit (
  AuditId uniqueidentifier NOT NULL,
  EventKind varchar(40) NOT NULL,
  ActorSubjectHash char(64) NOT NULL,
  EntityKind varchar(40) NOT NULL,
  EntityId uniqueidentifier NOT NULL,
  RequestId varchar(80) NULL,
  IdempotencyKey varchar(128) NULL,
  ReasonCode varchar(64) NOT NULL,
  BeforeSha char(64) NULL,
  AfterSha char(64) NULL,
  CreatedAtUtc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
  ScopeKey nvarchar(128) NOT NULL,
  CONSTRAINT PK_GAudit PRIMARY KEY (AuditId)
);
GO

-- Performance indexes and uniqueness safeguards.
CREATE UNIQUE INDEX UX_GPerson_Identity ON WEB_APP.FOCUS_FN_GridironPerson(ScopeKey,IdentityTokenHash) WHERE IdentityTokenHash IS NOT NULL;
CREATE INDEX IX_GRoster_Eligibility ON WEB_APP.FOCUS_FN_GridironRoster(ScopeKey,SeasonId,TeamId,EffectiveFromWeek,EffectiveThroughWeek) INCLUDE (PersonId,Status);
CREATE INDEX IX_GEnrollment_Decision ON WEB_APP.FOCUS_FN_GridironEnrollment(ScopeKey,SeasonId,PersonId,EffectiveAtUtc DESC);
CREATE INDEX IX_GSourceReceipt_WeekFeed ON WEB_APP.FOCUS_FN_GridironSourceReceipt(ScopeKey,WeekId,FeedCode,CapturedAtUtc DESC);
CREATE INDEX IX_GPlayerRating_Overall ON WEB_APP.FOCUS_FN_GridironPlayerRating(ScopeKey,RatingRunId,OverallRating DESC) INCLUDE(PersonId,Status,Confidence);
CREATE INDEX IX_GMatch_Week ON WEB_APP.FOCUS_FN_GridironMatch(ScopeKey,WeekId,State,MatchSlot);
CREATE INDEX IX_GLineup_Starter ON WEB_APP.FOCUS_FN_GridironLineup(ScopeKey,MatchId,TeamId,SideCode,LineupRole) INCLUDE(PersonId,PositionCode,PositionRatingAtKickoff);
CREATE INDEX IX_GBoxStat_Match ON WEB_APP.FOCUS_FN_GridironBoxStat(ScopeKey,MatchId,TeamId,StatCode);
CREATE INDEX IX_GAudit_Recent ON WEB_APP.FOCUS_FN_GridironAudit(ScopeKey,CreatedAtUtc DESC);
GO

-- HARD GATES BEFORE PRODUCTION:
-- 1) Add approved immutability enforcement (separate writer principal, audited
--    append-only permissions, controlled head selection; no update of frozen rows).
-- 2) Enforce production-team-only membership, consent, person eligibility and
--    11 distinct STARTER slots for EACH on-field unit using transaction validation.
-- 3) Enforce match/week/season/rating-run internal consistency and no overlapping weeks.
-- 4) Block insufficient eligible team roster; never fabricate LIVE starters.
-- 5) Add versioned migration ledger entry and rollback plan after DBA dry-run.
-- 6) Scope-check public API, worker, CLI and MCP; public fixture mode is synthetic.
