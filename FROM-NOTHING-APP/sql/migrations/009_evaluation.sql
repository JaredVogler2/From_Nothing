-- Migration 009 (P23): canonical evaluation revisions, unit-fact evidence, recognition streams and badges.
-- Consolidated from the reviewed P18 draft; its statements are unchanged. PROPOSED storage, NOT executed by this
-- build. `python -m focus.cli migrate --apply` runs it only in an authorized installer session, in one transaction
-- under sp_getapplock('FOCUS_FN_MIGRATIONS'), and records this file's SHA-256 in WEB_APP.FOCUS_FN_SchemaVersion only
-- after every declared object is verified (docs/execution/INSTALL_AND_PIPELINE.md).
--
-- One authority, many readers:
--   * FOCUS_FN_Evaluation (S06 001) holds every revision. A series (ScopeKey, SeriesKey = kind + goal set + policy) appends
--     revision n+1 with Supersedes = revision n only when its evidence changed. FOCUS_FN_EvaluationHead moves by optimistic
--     Version. The prior as-known revision is never updated or deleted.
--   * FOCUS_FN_EvaluationEvidence stores the exact unit facts (EARNED/MISSED/UNKNOWN/PENDING units, relief, offplan U, OOS
--     facts, coverage) under their content SHA = Evaluation.EvidenceSha. Team, aircraft, factory and history totals pool
--     these exact components before division (D400/Cp200 + D100/Cp100 = 72, not 82.5).
--   * Every surface (iOS, Classic, Arena, API, MCP) reads FOCUS_FN_CurrentEvaluation; nothing recomputes a score in SQL
--     or in a browser. Components are exact rationals in Payload; the decimal columns are display projections only.
--   * Recognition and badges are supplementary, idempotent chains keyed by DedupeKey = '<base>#r<revision>'. A correction
--     appends a reversing revision; the original award stays. XP never feeds eligibility, crews or certification.
GO

ALTER TABLE WEB_APP.FOCUS_FN_Evaluation ADD
    SeriesKey varchar(64) NULL,
    Kind varchar(16) NULL CONSTRAINT CK_FN_Evaluation_Kind CHECK (Kind IS NULL OR Kind IN ('GOAL', 'AGGREGATE')),
    Revision int NULL CONSTRAINT CK_FN_Evaluation_Revision CHECK (Revision IS NULL OR Revision >= 1),
    PolicyStatus varchar(16) NULL CONSTRAINT CK_FN_Evaluation_PolicyStatus CHECK (PolicyStatus IS NULL OR PolicyStatus IN ('DEMONSTRATION', 'OFFICIAL')),
    Label nvarchar(256) NULL,
    CONSTRAINT CK_FN_Evaluation_Status CHECK (Status IN ('official', 'demonstration', 'neutral', 'unknown')),
    CONSTRAINT CK_FN_Evaluation_NullScore CHECK ((Status IN ('neutral', 'unknown') AND Score IS NULL AND Grade IS NULL)
                                                OR (Status IN ('official', 'demonstration') AND Score IS NOT NULL AND Score BETWEEN 0 AND 100));
GO

CREATE TABLE WEB_APP.FOCUS_FN_EvaluationEvidence(
    EvidenceSha char(64) NOT NULL PRIMARY KEY,
    Payload varbinary(max) NOT NULL,
    CreatedAtUtc datetime2(6) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_EvaluationHead(
    ScopeKey nvarchar(128) NOT NULL,
    SeriesKey varchar(64) NOT NULL,
    EvaluationId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_Evaluation(EvaluationId),
    Version bigint NOT NULL CHECK (Version >= 1),
    UpdatedAtUtc datetime2(6) NOT NULL,
    CONSTRAINT PK_FN_EvaluationHead PRIMARY KEY (ScopeKey, SeriesKey));
CREATE TABLE WEB_APP.FOCUS_FN_RecognitionEvent(
    EventId varchar(64) NOT NULL PRIMARY KEY,
    ScopeKey nvarchar(128) NOT NULL,
    DedupeKey varchar(256) NOT NULL,
    BaseKey varchar(240) NOT NULL,
    EventType varchar(24) NOT NULL CHECK (EventType IN ('completion_credited', 'gate_recovery', 'dependency_unlocked', 'commitment_kept',
                                                         'excusal_decided', 'badge_earned', 'correction_applied')),
    Team nvarchar(80) NOT NULL,
    GoalId uniqueidentifier NULL,
    ValueDisplay decimal(38,12) NOT NULL,
    Revision int NOT NULL CHECK (Revision >= 1),
    Supersedes varchar(64) NULL,
    PolicyId varchar(64) NOT NULL,
    CreatedAtUtc datetime2(6) NOT NULL,
    Payload varbinary(max) NOT NULL,
    CONSTRAINT UX_FN_RecognitionDedupe UNIQUE (ScopeKey, DedupeKey),
    CONSTRAINT CK_FN_Recognition_Positive CHECK (EventType = 'correction_applied' OR ValueDisplay >= 0));
CREATE TABLE WEB_APP.FOCUS_FN_BadgeAward(
    BadgeId varchar(64) NOT NULL PRIMARY KEY,
    ScopeKey nvarchar(128) NOT NULL,
    DedupeKey varchar(256) NOT NULL,
    Badge varchar(24) NOT NULL CHECK (Badge IN ('Recovery', 'Firebreak', 'CommitmentKeeper', 'SteadyShift', 'SkillSteward', 'MonthlyChampion')),
    Team nvarchar(80) NOT NULL,
    Period varchar(160) NOT NULL,
    Revision int NOT NULL CHECK (Revision >= 1),
    Revoked bit NOT NULL,
    Supersedes varchar(64) NULL,
    AwardedAtUtc datetime2(6) NOT NULL,
    Payload varbinary(max) NOT NULL,
    CONSTRAINT UX_FN_BadgeDedupe UNIQUE (ScopeKey, DedupeKey));
GO

CREATE OR ALTER TRIGGER WEB_APP.TR_FN_Evaluation_Immutable ON WEB_APP.FOCUS_FN_Evaluation AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51800, 'FOCUS_FN_Evaluation rows are immutable: corrections append a superseding revision', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_EvaluationEvidence_Immutable ON WEB_APP.FOCUS_FN_EvaluationEvidence AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51801, 'FOCUS_FN_EvaluationEvidence rows are immutable', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_RecognitionEvent_Immutable ON WEB_APP.FOCUS_FN_RecognitionEvent AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51802, 'FOCUS_FN_RecognitionEvent rows are immutable: corrections append reversing revisions', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_BadgeAward_Immutable ON WEB_APP.FOCUS_FN_BadgeAward AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51803, 'FOCUS_FN_BadgeAward rows are immutable: revocations append', 1; END;
GO

-- Append one revision when the evidence changed (applock per series + expected head version); identical evidence is a no-op.
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_AppendEvaluation
    @ScopeKey nvarchar(128), @SeriesKey varchar(64), @ExpectedVersion bigint, @EvaluationId uniqueidentifier, @CutoffUtc datetime2(6),
    @PolicySha char(64), @GoalSetSha char(64), @EvidenceSha char(64), @EvidencePayload varbinary(max), @Status varchar(24),
    @Score decimal(5,1) NULL, @Grade varchar(8) NULL, @Kind varchar(16), @Revision int, @PolicyStatus varchar(16), @Label nvarchar(256),
    @Supersedes uniqueidentifier NULL, @Payload varbinary(max)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DECLARE @lock int, @resource nvarchar(255) = N'FOCUS_FN_Evaluation:' + @ScopeKey + N':' + @SeriesKey;
    EXEC @lock = sp_getapplock @Resource = @resource, @LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 10000;
    IF @lock < 0 THROW 51804, 'evaluation series lock unavailable', 1;
    DECLARE @current bigint = (SELECT Version FROM WEB_APP.FOCUS_FN_EvaluationHead WHERE ScopeKey = @ScopeKey AND SeriesKey = @SeriesKey);
    IF ISNULL(@current, 0) <> @ExpectedVersion THROW 51805, 'HEAD_MOVED: re-read the current revision and retry', 1;
    IF EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_EvaluationHead h JOIN WEB_APP.FOCUS_FN_Evaluation e ON e.EvaluationId = h.EvaluationId
               WHERE h.ScopeKey = @ScopeKey AND h.SeriesKey = @SeriesKey AND e.EvidenceSha = @EvidenceSha AND e.PolicySha = @PolicySha)
    BEGIN COMMIT TRANSACTION; RETURN; END;                 -- unchanged evidence: the current revision stands
    IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_EvaluationEvidence WHERE EvidenceSha = @EvidenceSha)
        INSERT INTO WEB_APP.FOCUS_FN_EvaluationEvidence(EvidenceSha, Payload, CreatedAtUtc) VALUES (@EvidenceSha, @EvidencePayload, @CutoffUtc);
    INSERT INTO WEB_APP.FOCUS_FN_Evaluation(EvaluationId, ScopeKey, CutoffUtc, PolicySha, GoalSetSha, EvidenceSha, Status, Score, Grade, Payload,
                                            Supersedes, SeriesKey, Kind, Revision, PolicyStatus, Label)
    VALUES (@EvaluationId, @ScopeKey, @CutoffUtc, @PolicySha, @GoalSetSha, @EvidenceSha, @Status, @Score, @Grade, @Payload, @Supersedes, @SeriesKey,
            @Kind, @Revision, @PolicyStatus, @Label);
    IF @current IS NULL
        INSERT INTO WEB_APP.FOCUS_FN_EvaluationHead(ScopeKey, SeriesKey, EvaluationId, Version, UpdatedAtUtc)
        VALUES (@ScopeKey, @SeriesKey, @EvaluationId, 1, @CutoffUtc);
    ELSE
        UPDATE WEB_APP.FOCUS_FN_EvaluationHead SET EvaluationId = @EvaluationId, Version = Version + 1, UpdatedAtUtc = @CutoffUtc
        WHERE ScopeKey = @ScopeKey AND SeriesKey = @SeriesKey AND Version = @ExpectedVersion;
    COMMIT TRANSACTION;
END;
GO

-- The one read projection every surface uses.
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_CurrentEvaluation AS
SELECT h.ScopeKey, h.SeriesKey, h.Version AS HeadVersion, e.EvaluationId, e.Kind, e.Label, e.Revision, e.CutoffUtc, e.PolicySha, e.PolicyStatus,
       e.Status, e.Score, e.Grade, e.EvidenceSha, e.Supersedes
FROM WEB_APP.FOCUS_FN_EvaluationHead h
JOIN WEB_APP.FOCUS_FN_Evaluation e ON e.EvaluationId = h.EvaluationId;
GO
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_TeamXp AS
SELECT r.ScopeKey, r.Team, SUM(r.ValueDisplay) AS XpDisplay, COUNT_BIG(*) AS Events
FROM WEB_APP.FOCUS_FN_RecognitionEvent r
GROUP BY r.ScopeKey, r.Team;
GO
