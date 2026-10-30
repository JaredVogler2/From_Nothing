-- Migration 007 (P23): immutable publication, pre-sign readback and the accepted head.
-- Consolidated from the reviewed P16 draft; its statements are unchanged. PROPOSED storage, NOT executed by this
-- build. `python -m focus.cli migrate --apply` runs it only in an authorized installer session, in one transaction
-- under sp_getapplock('FOCUS_FN_MIGRATIONS'), and records this file's SHA-256 in WEB_APP.FOCUS_FN_SchemaVersion only
-- after every declared object is verified (docs/execution/INSTALL_AND_PIPELINE.md).
--
-- Protocol (S13 CANONICAL ACCEPTANCE ORDER) and where each step lives:
--   1-3  final validation, canonical serialization, detached validation      application (focus.validation, focus.publication)
--   4    private unsigned stage                                              FOCUS_FN_StagePlan -> FOCUS_FN_PlanStage
--   5    exact Payload readback while the previous head is unchanged         FOCUS_FN_ReadPlanStage
--   6    independent readback validation (receipt stored)                    FOCUS_FN_RecordValidation -> FOCUS_FN_Validation
--   7-8  sign exactly the readback-validated bytes, verify                   application signer boundary (never SQL, never the reader)
--   9    PublishedPlan + FrozenGoal + receipt, PlanHead LAST                 FOCUS_FN_AcceptPlan (one transaction, applock, expected version)
-- No step signs in SQL. The app reader principal only SELECTs the accepted-head view; the publisher principal
-- EXECUTEs the procedures; nobody updates or deletes accepted history (triggers below).
GO

-- Integrity beyond S06 001 (additive).
ALTER TABLE WEB_APP.FOCUS_FN_PlanStage ADD
    CONSTRAINT CK_FN_PlanStage_Purpose CHECK (Purpose IN ('EXECUTABLE', 'FORECAST')),
    CONSTRAINT CK_FN_PlanStage_Sha CHECK (LEN(ContentSha) = 64);
ALTER TABLE WEB_APP.FOCUS_FN_Validation ADD
    CONSTRAINT CK_FN_Validation_Kind CHECK (Kind IN ('FINAL', 'DETACHED', 'READBACK', 'DISPATCH')),
    CONSTRAINT CK_FN_Validation_Counts CHECK (Violations >= 0 AND RequiredSkips >= 0);
ALTER TABLE WEB_APP.FOCUS_FN_PublishedPlan ADD
    CONSTRAINT CK_FN_PublishedPlan_Purpose CHECK (Purpose IN ('EXECUTABLE', 'FORECAST'));
ALTER TABLE WEB_APP.FOCUS_FN_PlanHead ADD
    CONSTRAINT CK_FN_PlanHead_Version CHECK (Version >= 1);
GO

-- Accepted history and stages are append-only.
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_PlanStage_Immutable ON WEB_APP.FOCUS_FN_PlanStage AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51600, 'FOCUS_FN_PlanStage rows are immutable', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_Validation_Immutable ON WEB_APP.FOCUS_FN_Validation AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51601, 'FOCUS_FN_Validation rows are immutable', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_PublishedPlan_Immutable ON WEB_APP.FOCUS_FN_PublishedPlan AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51602, 'FOCUS_FN_PublishedPlan rows are immutable', 1; END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_FrozenGoal_Immutable ON WEB_APP.FOCUS_FN_FrozenGoal AFTER UPDATE, DELETE AS
BEGIN SET NOCOUNT ON; THROW 51603, 'FOCUS_FN_FrozenGoal rows are immutable: amendments append, B0 never shrinks', 1; END;
GO

-- 4. Private unsigned stage, idempotent per (scope, idempotency key). Replaying identical bytes returns the
-- existing stage; different bytes under the same key are a conflict.
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_StagePlan
    @StageId uniqueidentifier, @RunId uniqueidentifier, @ScopeKey nvarchar(128), @Purpose varchar(24), @PolicySha char(64),
    @Payload varbinary(max), @ContentSha char(64), @IdempotencyKey varchar(128), @NowUtc datetime2(6)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DECLARE @existing uniqueidentifier, @existingSha char(64);
    SELECT @existing = StageId, @existingSha = ContentSha FROM WEB_APP.FOCUS_FN_PlanStage WITH (UPDLOCK, HOLDLOCK)
    WHERE ScopeKey = @ScopeKey AND IdempotencyKey = @IdempotencyKey;
    IF @existing IS NOT NULL
    BEGIN
        IF @existingSha <> @ContentSha THROW 51610, 'stage idempotency key already holds different bytes', 1;
        COMMIT TRANSACTION;
        SELECT @existing AS StageId, CAST(1 AS bit) AS Replayed;
        RETURN;
    END;
    IF CONVERT(char(64), HASHBYTES('SHA2_256', @Payload), 2) COLLATE Latin1_General_BIN2 <> UPPER(@ContentSha)
        THROW 51611, 'stage payload does not match its declared sha', 1;
    INSERT INTO WEB_APP.FOCUS_FN_PlanStage(StageId, RunId, ScopeKey, Purpose, PolicySha, Payload, ContentSha, IdempotencyKey, CreatedAtUtc)
    VALUES (@StageId, @RunId, @ScopeKey, @Purpose, @PolicySha, @Payload, LOWER(@ContentSha), @IdempotencyKey, @NowUtc);
    COMMIT TRANSACTION;
    SELECT @StageId AS StageId, CAST(0 AS bit) AS Replayed;
END;
GO

-- 5. Exact readback of the staged bytes (the application compares them byte for byte and re-validates from source).
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_ReadPlanStage @StageId uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    SELECT StageId, ScopeKey, Purpose, PolicySha, ContentSha, Payload FROM WEB_APP.FOCUS_FN_PlanStage WHERE StageId = @StageId;
END;
GO

-- 6. Durable validation receipts (FINAL / DETACHED / READBACK / DISPATCH).
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_RecordValidation
    @ValidationId uniqueidentifier, @StageId uniqueidentifier, @Kind varchar(24), @SourceSha char(64), @PolicySha char(64),
    @ContentSha char(64), @ValidatorVersion varchar(64), @Violations int, @RequiredSkips int, @ReceiptPayload varbinary(max), @NowUtc datetime2(6)
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_Validation WHERE ValidationId = @ValidationId)
        INSERT INTO WEB_APP.FOCUS_FN_Validation(ValidationId, StageId, Kind, SourceSha, PolicySha, ContentSha, ValidatorVersion, Violations,
                                                RequiredSkips, ReceiptPayload, CreatedAtUtc)
        VALUES (@ValidationId, @StageId, @Kind, @SourceSha, @PolicySha, @ContentSha, @ValidatorVersion, @Violations, @RequiredSkips,
                @ReceiptPayload, @NowUtc);
END;
GO

-- 9. The scoped acceptance transaction: verify the stage bytes and a clean READBACK receipt for exactly those
-- bytes, write PublishedPlan + FrozenGoal rows, then move PlanHead LAST with the expected version. A moved head,
-- a missing/failed readback or a sha mismatch rolls everything back; the previous head stays.
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_AcceptPlan
    @PlanId uniqueidentifier, @StageId uniqueidentifier, @ReadbackValidationId uniqueidentifier, @ScopeKey nvarchar(128), @Purpose varchar(24),
    @ContentSha char(64), @KeyId varchar(64), @Signature varbinary(512), @AcceptanceReceipt varbinary(max), @GoalsJson nvarchar(max),
    @ExpectedHeadVersion bigint, @NowUtc datetime2(6)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DECLARE @lock int;
    EXEC @lock = sp_getapplock @Resource = N'FOCUS_FN_PUBLISH', @LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 10000;
    IF @lock < 0 THROW 51620, 'publication lock not acquired', 1;
    IF EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_PublishedPlan WHERE PlanId = @PlanId)
    BEGIN
        COMMIT TRANSACTION;                                   -- recovery: the acceptance already committed
        SELECT PlanId, Version FROM WEB_APP.FOCUS_FN_PlanHead WHERE ScopeKey = @ScopeKey AND Purpose = @Purpose;
        RETURN;
    END;
    DECLARE @payload varbinary(max) = (SELECT Payload FROM WEB_APP.FOCUS_FN_PlanStage WHERE StageId = @StageId AND ScopeKey = @ScopeKey
                                       AND Purpose = @Purpose AND ContentSha = LOWER(@ContentSha));
    IF @payload IS NULL THROW 51621, 'no stage with exactly these bytes', 1;
    IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_Validation WHERE ValidationId = @ReadbackValidationId AND StageId = @StageId
                   AND Kind = 'READBACK' AND ContentSha = LOWER(@ContentSha) AND Violations = 0 AND RequiredSkips = 0)
        THROW 51622, 'no clean READBACK validation for these bytes', 1;
    DECLARE @current bigint = (SELECT Version FROM WEB_APP.FOCUS_FN_PlanHead WITH (UPDLOCK, HOLDLOCK)
                               WHERE ScopeKey = @ScopeKey AND Purpose = @Purpose);
    IF COALESCE(@current, 0) <> @ExpectedHeadVersion THROW 51623, 'plan head moved: the previous head stays', 1;
    INSERT INTO WEB_APP.FOCUS_FN_PublishedPlan(PlanId, StageId, ReadbackValidationId, ScopeKey, Purpose, Payload, ContentSha, KeyId, Signature,
                                               AcceptanceReceipt, AcceptedAtUtc)
    VALUES (@PlanId, @StageId, @ReadbackValidationId, @ScopeKey, @Purpose, @payload, LOWER(@ContentSha), @KeyId, @Signature,
            @AcceptanceReceipt, @NowUtc);
    INSERT INTO WEB_APP.FOCUS_FN_FrozenGoal(GoalId, PlanId, Team, ShiftId, OriginalPersonMinutes, MembershipSha, Payload)
    SELECT g.GoalId, @PlanId, g.Team, g.ShiftId, g.OriginalPersonMinutes, g.MembershipSha, CONVERT(varbinary(max), g.Payload)
    FROM OPENJSON(@GoalsJson) WITH (GoalId uniqueidentifier, Team nvarchar(80), ShiftId varchar(128), OriginalPersonMinutes decimal(19, 6),
                                    MembershipSha char(64), Payload nvarchar(max) AS JSON) g;
    IF @current IS NULL
        INSERT INTO WEB_APP.FOCUS_FN_PlanHead(ScopeKey, Purpose, PlanId, Version, UpdatedAtUtc) VALUES (@ScopeKey, @Purpose, @PlanId, 1, @NowUtc);
    ELSE
        UPDATE WEB_APP.FOCUS_FN_PlanHead SET PlanId = @PlanId, Version = Version + 1, UpdatedAtUtc = @NowUtc
        WHERE ScopeKey = @ScopeKey AND Purpose = @Purpose AND Version = @ExpectedHeadVersion;              -- LAST
    COMMIT TRANSACTION;
    SELECT @PlanId AS PlanId, COALESCE(@current, 0) + 1 AS Version;
END;
GO

-- Readers select the accepted immutable head; signature verification happens in the application.
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_CurrentPlan
AS
SELECT h.ScopeKey, h.Purpose, h.Version AS HeadVersion, h.UpdatedAtUtc, p.PlanId, p.StageId, p.ReadbackValidationId, p.ContentSha, p.KeyId,
       p.Signature, p.AcceptanceReceipt, p.AcceptedAtUtc, p.Payload
FROM WEB_APP.FOCUS_FN_PlanHead h
JOIN WEB_APP.FOCUS_FN_PublishedPlan p ON p.PlanId = h.PlanId;
GO
