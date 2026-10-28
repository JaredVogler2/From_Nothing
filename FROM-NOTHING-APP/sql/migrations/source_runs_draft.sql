-- Draft (P04): transactional source-run admission and head promotion for the proposed FOCUS_FN tables.
-- NOT executed by this build. Consolidated into the versioned migration set in P23 (003_source_promotion.sql).
-- Enforces S06 TRANSACTIONAL PROMOTION RULES in the database, not by an unverified caller flag:
--   * SourceHead can reference only a COMPLETE run;
--   * the head moves only forward in cutoff time, under an application lock and an optimistic version;
--   * a failed or rejected run never moves the head;
--   * replaying the same (ScopeKey, IdempotencyKey) returns the existing run.

CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_BeginSourceRun
    @ScopeKey nvarchar(128), @IdempotencyKey varchar(128), @CutoffUtc datetime2(6), @RunId uniqueidentifier OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DECLARE @lock int;
    EXEC @lock = sp_getapplock @Resource = N'FOCUS_FN_SOURCE', @LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 30000;
    IF @lock < 0 THROW 51001, 'source acquisition lock unavailable', 1;
    SELECT @RunId = RunId FROM WEB_APP.FOCUS_FN_SourceRun WHERE ScopeKey = @ScopeKey AND IdempotencyKey = @IdempotencyKey;
    IF @RunId IS NULL
    BEGIN
        SET @RunId = NEWID();
        INSERT WEB_APP.FOCUS_FN_SourceRun(RunId, ScopeKey, IdempotencyKey, Status, CutoffUtc, CaptureStartUtc)
        VALUES (@RunId, @ScopeKey, @IdempotencyKey, 'CAPTURING', @CutoffUtc, SYSUTCDATETIME());
    END
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_CompleteSourceRun
    @RunId uniqueidentifier, @ManifestSha char(64), @ManifestPayload varbinary(max)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    UPDATE WEB_APP.FOCUS_FN_SourceRun
       SET Status = 'COMPLETE', CaptureEndUtc = SYSUTCDATETIME(), ManifestSha = @ManifestSha, ManifestPayload = @ManifestPayload
     WHERE RunId = @RunId AND Status = 'CAPTURING';
    IF @@ROWCOUNT <> 1 THROW 51002, 'only a CAPTURING run can complete', 1;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_RejectSourceRun
    @RunId uniqueidentifier, @ReasonPayload varbinary(max)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    UPDATE WEB_APP.FOCUS_FN_SourceRun SET Status = 'REJECTED', CaptureEndUtc = SYSUTCDATETIME(), ManifestPayload = @ReasonPayload
     WHERE RunId = @RunId AND Status = 'CAPTURING';
END;
GO

CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_PromoteSourceHead
    @ScopeKey nvarchar(128), @RunId uniqueidentifier, @ExpectedVersion bigint, @NewVersion bigint OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DECLARE @lock int;
    EXEC @lock = sp_getapplock @Resource = N'FOCUS_FN_SOURCE_HEAD', @LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 30000;
    IF @lock < 0 THROW 51003, 'source head lock unavailable', 1;
    IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_SourceRun WHERE RunId = @RunId AND ScopeKey = @ScopeKey AND Status = 'COMPLETE')
        THROW 51004, 'only an admitted COMPLETE run of this scope can become the source head', 1;
    DECLARE @CurrentVersion bigint, @CurrentRun uniqueidentifier;
    SELECT @CurrentVersion = Version, @CurrentRun = RunId FROM WEB_APP.FOCUS_FN_SourceHead WITH (UPDLOCK, HOLDLOCK) WHERE ScopeKey = @ScopeKey;
    IF ISNULL(@CurrentVersion, 0) <> @ExpectedVersion THROW 51005, 'source head version conflict', 1;
    IF @CurrentRun IS NOT NULL AND (SELECT CutoffUtc FROM WEB_APP.FOCUS_FN_SourceRun WHERE RunId = @CurrentRun)
                                   > (SELECT CutoffUtc FROM WEB_APP.FOCUS_FN_SourceRun WHERE RunId = @RunId)
        THROW 51006, 'source head only moves forward in cutoff time', 1;
    SET @NewVersion = ISNULL(@CurrentVersion, 0) + 1;
    IF @CurrentRun IS NULL
        INSERT WEB_APP.FOCUS_FN_SourceHead(ScopeKey, RunId, Version, UpdatedAtUtc) VALUES (@ScopeKey, @RunId, @NewVersion, SYSUTCDATETIME());
    ELSE
        UPDATE WEB_APP.FOCUS_FN_SourceHead SET RunId = @RunId, Version = @NewVersion, UpdatedAtUtc = SYSUTCDATETIME() WHERE ScopeKey = @ScopeKey;
    COMMIT TRANSACTION;
END;
GO
