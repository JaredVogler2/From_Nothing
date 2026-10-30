-- Migration 008 (P23): lifecycle actuals, sequence violations, disruption claims, reviewed excusals and observation runs.
-- Consolidated from the reviewed P17 draft; its statements are unchanged. PROPOSED storage, NOT executed by this
-- build. `python -m focus.cli migrate --apply` runs it only in an authorized installer session, in one transaction
-- under sp_getapplock('FOCUS_FN_MIGRATIONS'), and records this file's SHA-256 in WEB_APP.FOCUS_FN_SchemaVersion only
-- after every declared object is verified (docs/execution/INSTALL_AND_PIPELINE.md).
-- S04 verified no actual-interval, lifecycle-event or excusal feed: these are proposed producer landing tables.
--
-- Two layers, never merged:
--   * Landing (producer-owned): one table per observation feed with exactly the feed columns that
--     focus/performance/actuals.py FEEDS declares, plus a producer receipt per feed (complete / certified empty /
--     covered-through watermark). The read-only queries in queries/actuals read only these. An empty landing
--     table counts as covered only when its receipt affirms it.
--   * Application history (FOCUS-owned, append-only): FOCUS_FN_Event (S06 001) holds lifecycle facts with
--     DedupeKey = '<feed>:<source key>#<revision>'. Typed tables below hold actual intervals, explicit defect
--     evidence, approved amendments, claims, decisions (S06 001 FOCUS_FN_ExcusalDecision, extended), sequence
--     violations, observation runs and IE review proposals. Nothing is updated or deleted (triggers). A
--     correction is a new revision or an explicit VOIDED / CORRECTION fact.
--
-- Clocks: EffectiveAtUtc (when it physically happened) <= RecordedAtUtc (source system) <= ObservedAtUtc
-- (seen by the producer query) <= IngestedAtUtc (the collection run's as-of). Evaluations replay any as-known
-- time by filtering IngestedAtUtc.
GO

-- ---------------------------------------------------------------- landing (producer-owned, read by queries/actuals)
CREATE TABLE WEB_APP.FOCUS_FN_ObservationProducerReceipt(
    Feed varchar(32) NOT NULL PRIMARY KEY CHECK (Feed IN ('lifecycle_events', 'actual_intervals', 'defect_evidence', 'goal_amendments',
                                                            'disruption_claims', 'excusal_decisions')),
    Complete bit NOT NULL,
    CertifiedEmpty bit NOT NULL,
    CoveredThroughUtc datetime2(6) NULL,
    Producer nvarchar(128) NOT NULL,
    UpstreamRevision nvarchar(128) NOT NULL,
    UpdatedAtUtc datetime2(6) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_LifecycleSource(
    SourceEventKey nvarchar(128) NOT NULL, SourceSequence bigint NOT NULL CHECK (SourceSequence >= 0), SourceRevision nvarchar(64) NOT NULL,
    LineNumber int NOT NULL, Soi nvarchar(64) NOT NULL, PhaseKind varchar(16) NULL, PhaseOrdinal int NULL, SegmentsJson nvarchar(400) NULL,
    Kind varchar(24) NOT NULL, PersonKey nvarchar(64) NULL, EffectiveAtUtc datetime2(6) NOT NULL, RecordedAtUtc datetime2(6) NOT NULL,
    ObservedAtUtc datetime2(6) NOT NULL, Verified bit NOT NULL, VerifiedBy nvarchar(64) NULL, DirectiveRef nvarchar(128) NULL,
    SupersedesEventKey nvarchar(128) NULL, EvidenceRef nvarchar(256) NOT NULL, Detail nvarchar(500) NULL,
    CONSTRAINT PK_FN_LifecycleSource PRIMARY KEY (SourceEventKey, SourceRevision),
    CONSTRAINT CK_FN_LifecycleSource_Clocks CHECK (RecordedAtUtc <= ObservedAtUtc));
CREATE TABLE WEB_APP.FOCUS_FN_ActualIntervalSource(
    SourceEventKey nvarchar(128) NOT NULL, SourceRevision nvarchar(64) NOT NULL, LineNumber int NOT NULL, Soi nvarchar(64) NOT NULL,
    PhaseKind varchar(16) NOT NULL, PhaseOrdinal int NOT NULL, PersonKey nvarchar(64) NOT NULL, StartUtc datetime2(6) NOT NULL,
    EndUtc datetime2(6) NOT NULL, IsVoided bit NOT NULL, ObservedAtUtc datetime2(6) NOT NULL, EvidenceRef nvarchar(256) NOT NULL,
    CONSTRAINT PK_FN_ActualIntervalSource PRIMARY KEY (SourceEventKey, SourceRevision),
    CONSTRAINT CK_FN_ActualIntervalSource_Order CHECK (StartUtc < EndUtc));
CREATE TABLE WEB_APP.FOCUS_FN_DefectEvidenceSource(
    SourceEventKey nvarchar(128) NOT NULL PRIMARY KEY, LineNumber int NOT NULL, Soi nvarchar(64) NOT NULL, RootSoi nvarchar(64) NULL,
    ReworkClass varchar(4) NOT NULL CHECK (ReworkClass IN ('NC', 'PU', 'CI')), DefectOriginTeam nvarchar(80) NOT NULL,
    EvidenceRef nvarchar(256) NOT NULL, ObservedAtUtc datetime2(6) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_GoalAmendmentSource(
    AmendmentKey nvarchar(128) NOT NULL PRIMARY KEY, Team nvarchar(80) NOT NULL, ShiftDate date NOT NULL,
    ShiftNumber tinyint NOT NULL CHECK (ShiftNumber IN (1, 2, 3)), WorkRefJson nvarchar(400) NOT NULL,
    StandardPersonMinutes int NOT NULL CHECK (StandardPersonMinutes > 0), EffortBasis nvarchar(400) NOT NULL,
    FeasibilityRef nvarchar(400) NOT NULL, Authority nvarchar(128) NOT NULL, DecisionAtUtc datetime2(6) NOT NULL, Reason nvarchar(2000) NOT NULL,
    ObservedAtUtc datetime2(6) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_DisruptionClaimSource(
    ClaimKey nvarchar(128) NOT NULL PRIMARY KEY, Team nvarchar(80) NOT NULL, ShiftDate date NOT NULL,
    ShiftNumber tinyint NOT NULL CHECK (ShiftNumber IN (1, 2, 3)), WorkRefsJson nvarchar(max) NOT NULL, Cause varchar(32) NOT NULL,
    OriginTeam nvarchar(80) NULL, ExecutingTeam nvarchar(80) NULL, EvidenceRefsJson nvarchar(max) NOT NULL, Notes nvarchar(2000) NULL,
    Status varchar(16) NOT NULL CHECK (Status IN ('SUBMITTED', 'WITHDRAWN')), ReportedBy nvarchar(128) NOT NULL, ReportedAtUtc datetime2(6) NOT NULL,
    ObservedAtUtc datetime2(6) NOT NULL, SupersedesClaimKey nvarchar(128) NULL);
CREATE TABLE WEB_APP.FOCUS_FN_ExcusalDecisionSource(
    DecisionKey nvarchar(128) NOT NULL PRIMARY KEY, Team nvarchar(80) NOT NULL, ShiftDate date NOT NULL,
    ShiftNumber tinyint NOT NULL CHECK (ShiftNumber IN (1, 2, 3)), ClaimKeysJson nvarchar(max) NOT NULL, ApprovedWorkRefsJson nvarchar(max) NOT NULL,
    Decision varchar(16) NOT NULL CHECK (Decision IN ('APPROVED', 'REJECTED', 'PENDING')), Authority nvarchar(128) NOT NULL,
    AuthorityRole varchar(32) NOT NULL, DecisionAtUtc datetime2(6) NOT NULL, Reason nvarchar(2000) NOT NULL, ReviewTrack varchar(16) NULL,
    SupersedesDecisionKey nvarchar(128) NULL, ObservedAtUtc datetime2(6) NOT NULL);
GO

-- ---------------------------------------------------------------- application history (append-only)
ALTER TABLE WEB_APP.FOCUS_FN_Event ADD
    Feed varchar(32) NULL,
    Origin varchar(16) NULL CONSTRAINT CK_FN_Event_Origin CHECK (Origin IS NULL OR Origin IN ('SOURCE', 'REPORTED', 'DERIVED')),
    IngestedAtUtc datetime2(6) NULL,
    CONSTRAINT CK_FN_Event_Clocks CHECK (RecordedAtUtc <= ObservedAtUtc AND (IngestedAtUtc IS NULL OR ObservedAtUtc <= IngestedAtUtc));
ALTER TABLE WEB_APP.FOCUS_FN_ExcusalDecision ADD
    ScopeKey nvarchar(128) NULL,
    AuthorityRole varchar(32) NULL,
    ReviewTrack varchar(16) NULL CONSTRAINT CK_FN_Excusal_Track CHECK (ReviewTrack IS NULL OR ReviewTrack IN ('STANDARD', 'PLAN_CHURN')),
    IsException bit NULL,
    IngestedAtUtc datetime2(6) NULL;
GO

CREATE TABLE WEB_APP.FOCUS_FN_ActualInterval(
    IntervalId varchar(64) NOT NULL PRIMARY KEY, ScopeKey nvarchar(128) NOT NULL, SourceKey nvarchar(160) NOT NULL,
    SourceRevision nvarchar(64) NOT NULL, PhaseId varchar(160) NOT NULL, PersonId varchar(64) NOT NULL, StartUtc datetime2(6) NOT NULL,
    EndUtc datetime2(6) NOT NULL, IsVoided bit NOT NULL, IngestedAtUtc datetime2(6) NOT NULL, Payload varbinary(max) NOT NULL,
    ContentSha char(64) NOT NULL,
    CONSTRAINT UX_FN_ActualInterval UNIQUE (ScopeKey, SourceKey, SourceRevision),
    CONSTRAINT CK_FN_ActualInterval_Order CHECK (StartUtc < EndUtc));
CREATE TABLE WEB_APP.FOCUS_FN_DefectEvidence(
    EvidenceId varchar(64) NOT NULL PRIMARY KEY, ScopeKey nvarchar(128) NOT NULL, JobId varchar(64) NOT NULL, OriginTeam nvarchar(80) NOT NULL,
    EvidenceRef nvarchar(256) NOT NULL, IngestedAtUtc datetime2(6) NOT NULL, Payload varbinary(max) NOT NULL, ContentSha char(64) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_GoalAmendment(
    AmendmentId varchar(64) NOT NULL PRIMARY KEY, ScopeKey nvarchar(128) NOT NULL,
    GoalId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_FrozenGoal(GoalId),
    AddedPersonMinutes decimal(19,6) NOT NULL CHECK (AddedPersonMinutes > 0), FeasibilityRef nvarchar(400) NOT NULL,
    Authority nvarchar(128) NOT NULL, DecisionAtUtc datetime2(6) NOT NULL, IngestedAtUtc datetime2(6) NOT NULL, Payload varbinary(max) NOT NULL,
    ContentSha char(64) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_DisruptionClaim(
    ClaimId varchar(64) NOT NULL PRIMARY KEY, ScopeKey nvarchar(128) NOT NULL,
    GoalId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_FrozenGoal(GoalId),
    Cause varchar(32) NOT NULL CHECK (Cause IN ('LATE_PART', 'CROSS_TEAM_PREDECESSOR', 'SAME_TEAM_PREDECESSOR', 'CAPACITY_SHORTAGE',
                                                 'SKILL_SHORTAGE', 'DURATION_OVERRUN', 'REWORK_INJECTION', 'PLAN_CHURN')),
    Status varchar(16) NOT NULL CHECK (Status IN ('PROPOSED', 'SUBMITTED', 'WITHDRAWN')),
    Attribution varchar(8) NOT NULL CHECK (Attribution IN ('AUTO', 'MANUAL')), OriginTeam nvarchar(80) NULL, ReportedBy nvarchar(128) NOT NULL,
    ReportedAtUtc datetime2(6) NOT NULL, IngestedAtUtc datetime2(6) NOT NULL, Supersedes varchar(64) NULL, Payload varbinary(max) NOT NULL,
    ContentSha char(64) NOT NULL,
    CONSTRAINT CK_FN_Claim_Notes CHECK (Status <> 'SUBMITTED' OR Cause NOT IN ('SAME_TEAM_PREDECESSOR', 'DURATION_OVERRUN')
                                        OR LEN(JSON_VALUE(CONVERT(nvarchar(max), Payload), '$.notes')) >= 10));
CREATE TABLE WEB_APP.FOCUS_FN_SequenceViolation(
    ViolationId varchar(64) NOT NULL PRIMARY KEY, ScopeKey nvarchar(128) NOT NULL, PhaseId varchar(160) NOT NULL,
    EffectiveAtUtc datetime2(6) NOT NULL, TriggerKind varchar(8) NOT NULL CHECK (TriggerKind IN ('START', 'FINISH')),
    TriggerRef varchar(64) NOT NULL, IsDiscretionary bit NULL, Classification varchar(48) NOT NULL, GraphSnapshotId varchar(64) NOT NULL,
    DetectedAtUtc datetime2(6) NOT NULL, Payload varbinary(max) NOT NULL, ContentSha char(64) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_ObservationRun(
    ObservationRunId varchar(64) NOT NULL PRIMARY KEY, ScopeKey nvarchar(128) NOT NULL, AsOfUtc datetime2(6) NOT NULL,
    Mode varchar(16) NOT NULL, GraphSnapshotId varchar(64) NULL, Payload varbinary(max) NOT NULL, ContentSha char(64) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_ObservationFeedReceipt(
    ObservationRunId varchar(64) NOT NULL REFERENCES WEB_APP.FOCUS_FN_ObservationRun(ObservationRunId), Feed varchar(32) NOT NULL,
    Status varchar(24) NOT NULL CHECK (Status IN ('ROWS', 'CERTIFIED_EMPTY', 'EMPTY_UNCERTIFIED', 'FAILED', 'NOT_PROBED')),
    Complete bit NOT NULL, RowsSeen int NOT NULL, Admitted int NOT NULL, Duplicates int NOT NULL, Quarantined int NOT NULL,
    CoveredThroughUtc datetime2(6) NULL, ContentSha char(64) NOT NULL, Reason nvarchar(400) NULL,
    CONSTRAINT PK_FN_ObservationFeedReceipt PRIMARY KEY (ObservationRunId, Feed));
CREATE TABLE WEB_APP.FOCUS_FN_ObservationQuarantine(
    ObservationRunId varchar(64) NOT NULL REFERENCES WEB_APP.FOCUS_FN_ObservationRun(ObservationRunId), Feed varchar(32) NOT NULL,
    SourceKey nvarchar(160) NOT NULL, Code varchar(48) NOT NULL, Detail nvarchar(400) NOT NULL,
    CONSTRAINT PK_FN_ObservationQuarantine PRIMARY KEY (ObservationRunId, Feed, SourceKey, Code));
CREATE TABLE WEB_APP.FOCUS_FN_IEReview(
    ReviewId varchar(64) NOT NULL PRIMARY KEY, ScopeKey nvarchar(128) NOT NULL, JobId varchar(64) NOT NULL,
    StandardRevision nvarchar(64) NOT NULL, RequestedBy nvarchar(128) NOT NULL, RequestedAtUtc datetime2(6) NOT NULL,
    AffectsIssuedGoals bit NOT NULL CHECK (AffectsIssuedGoals = 0), Payload varbinary(max) NOT NULL);
GO

-- Evidence and review history are append-only: corrections append revisions, appeals append superseding decisions.
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_Event_Immutable ON WEB_APP.FOCUS_FN_Event AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51700, 'FOCUS_FN_Event rows are immutable: corrections append VOIDED/CORRECTION facts', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_ExcusalDecision_Immutable ON WEB_APP.FOCUS_FN_ExcusalDecision AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51701, 'FOCUS_FN_ExcusalDecision rows are immutable: appeals append superseding decisions', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_ActualInterval_Immutable ON WEB_APP.FOCUS_FN_ActualInterval AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51702, 'FOCUS_FN_ActualInterval rows are immutable', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_DisruptionClaim_Immutable ON WEB_APP.FOCUS_FN_DisruptionClaim AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51703, 'FOCUS_FN_DisruptionClaim rows are immutable: revisions and withdrawals append', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_SequenceViolation_Immutable ON WEB_APP.FOCUS_FN_SequenceViolation AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51704, 'FOCUS_FN_SequenceViolation rows are immutable: later predecessor completion never erases them', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_GoalAmendment_Immutable ON WEB_APP.FOCUS_FN_GoalAmendment AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51705, 'FOCUS_FN_GoalAmendment rows are immutable; original units are never mutated', 1; END;
GO

-- A team-shift whose obligation plan froze no units (B0 = 0) gets a zero-minute FOCUS_FN_FrozenGoal row for that plan when
-- the first amendment or claim needs it (FOCUS_FN_RecordAmendment, below), so decisions keep their FrozenGoal reference
-- without inventing B0.
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_RecordAmendment
    @AmendmentId varchar(64), @ScopeKey nvarchar(128), @GoalId uniqueidentifier, @PlanId uniqueidentifier, @Team nvarchar(80),
    @ShiftId varchar(128), @AddedPersonMinutes decimal(19,6), @FeasibilityRef nvarchar(400), @Authority nvarchar(128),
    @DecisionAtUtc datetime2(6), @IngestedAtUtc datetime2(6), @Payload varbinary(max), @ContentSha char(64)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_FrozenGoal WHERE GoalId = @GoalId)
        INSERT INTO WEB_APP.FOCUS_FN_FrozenGoal(GoalId, PlanId, Team, ShiftId, OriginalPersonMinutes, MembershipSha, Payload)
        VALUES (@GoalId, @PlanId, @Team, @ShiftId, 0, REPLICATE('0', 64), 0x);
    IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_GoalAmendment WHERE AmendmentId = @AmendmentId)
        INSERT INTO WEB_APP.FOCUS_FN_GoalAmendment(AmendmentId, ScopeKey, GoalId, AddedPersonMinutes, FeasibilityRef, Authority, DecisionAtUtc,
                                                   IngestedAtUtc, Payload, ContentSha)
        VALUES (@AmendmentId, @ScopeKey, @GoalId, @AddedPersonMinutes, @FeasibilityRef, @Authority, @DecisionAtUtc, @IngestedAtUtc, @Payload,
                @ContentSha);
    ELSE IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_GoalAmendment WHERE AmendmentId = @AmendmentId AND ContentSha = @ContentSha)
        THROW 51706, 'REVISION_CONTENT_CONFLICT: the amendment was first recorded with different content', 1;
    COMMIT TRANSACTION;
END;
GO
