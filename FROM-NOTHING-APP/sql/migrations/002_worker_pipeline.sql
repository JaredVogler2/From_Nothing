-- Migration 002 (P23): worker-run invariants, durable pipeline steps and the outbox (S17 WORKERS).
-- PROPOSED storage, NOT executed by this build. `python -m focus.cli migrate --apply` runs it only in an authorized
-- installer session, in one transaction under sp_getapplock('FOCUS_FN_MIGRATIONS'), and records this file's SHA-256 in
-- WEB_APP.FOCUS_FN_SchemaVersion only after every declared object is verified (docs/execution/INSTALL_AND_PIPELINE.md).
--
-- * FOCUS_FN_WorkerRun (001) gains its status and lease invariants, the claiming role, an attempt count and timestamps.
--   A worker claims a run by an optimistic version update. An expired lease may be taken over; the new owner
--   reconciles from durable receipts before it does anything.
-- * FOCUS_FN_PipelineStep holds one durable receipt per pipeline step:
--   - its role, so the app principal never signs and the signer never acquires;
--   - InputSha, the hash of the outputs it consumed, and OutputSha, the hash of what it produced;
--   - its lease and attempts.
--   A step runs only when every step it needs is completed with the outputs InputSha names. A missing or failed input
--   blocks it and everything after it.
-- * FOCUS_FN_Outbox is append-only: one event per completed step, deduplicated by (ScopeKey, DedupeKey). Only
--   DeliveredAtUtc may be set, once.
SET XACT_ABORT ON;
BEGIN TRANSACTION;
ALTER TABLE WEB_APP.FOCUS_FN_WorkerRun ADD
    Role varchar(16) NULL CONSTRAINT CK_FN_WorkerRun_Role CHECK (Role IS NULL OR Role IN ('app', 'source', 'stager', 'signer', 'evaluator')),
    Attempts int NULL CONSTRAINT CK_FN_WorkerRun_Attempts CHECK (Attempts IS NULL OR Attempts >= 0),
    CreatedAtUtc datetime2(6) NULL,
    FinishedAtUtc datetime2(6) NULL,
    CONSTRAINT CK_FN_WorkerRun_Status CHECK (Status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT CK_FN_WorkerRun_Lease CHECK ((LeaseOwner IS NULL AND LeaseUntilUtc IS NULL) OR (LeaseOwner IS NOT NULL AND LeaseUntilUtc IS NOT NULL));
CREATE INDEX IX_FN_WorkerRun_Queue ON WEB_APP.FOCUS_FN_WorkerRun (Status, Kind, LeaseUntilUtc) INCLUDE (ScopeKey, Version);

CREATE TABLE WEB_APP.FOCUS_FN_PipelineRun(
 PipelineId uniqueidentifier NOT NULL PRIMARY KEY,
 ScopeKey nvarchar(128) NOT NULL, IdempotencyKey varchar(128) NOT NULL,
 CreatedAtUtc datetime2(6) NOT NULL, RequestPayload varbinary(max) NOT NULL,
 CONSTRAINT UX_FN_PipelineRun UNIQUE (ScopeKey, IdempotencyKey));
CREATE TABLE WEB_APP.FOCUS_FN_PipelineStep(
 PipelineId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_PipelineRun(PipelineId),
 Step varchar(32) NOT NULL, Ordinal int NOT NULL, Role varchar(16) NOT NULL,
 Status varchar(16) NOT NULL, InputSha char(64) NULL, OutputSha char(64) NULL,
 LeaseOwner varchar(128) NULL, LeaseUntilUtc datetime2(6) NULL, Attempts int NOT NULL,
 Version bigint NOT NULL, UpdatedAtUtc datetime2(6) NOT NULL, ReceiptPayload varbinary(max) NULL,
 CONSTRAINT PK_FN_PipelineStep PRIMARY KEY (PipelineId, Step),
 CONSTRAINT CK_FN_PipelineStep_Role CHECK (Role IN ('source', 'app', 'stager', 'signer', 'evaluator')),
 CONSTRAINT CK_FN_PipelineStep_Status CHECK (Status IN ('pending', 'running', 'completed', 'failed', 'blocked')),
 CONSTRAINT CK_FN_PipelineStep_Lease CHECK ((Status = 'running' AND LeaseOwner IS NOT NULL AND LeaseUntilUtc IS NOT NULL)
                                         OR (Status <> 'running' AND LeaseOwner IS NULL AND LeaseUntilUtc IS NULL)),
 CONSTRAINT CK_FN_PipelineStep_Output CHECK (Status <> 'completed' OR OutputSha IS NOT NULL),
 CONSTRAINT CK_FN_PipelineStep_Attempts CHECK (Attempts >= 0));
CREATE TABLE WEB_APP.FOCUS_FN_Outbox(
 OutboxId bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
 ScopeKey nvarchar(128) NOT NULL, DedupeKey varchar(256) NOT NULL,
 Topic varchar(64) NOT NULL, Payload varbinary(max) NOT NULL, ContentSha char(64) NOT NULL,
 CreatedAtUtc datetime2(6) NOT NULL, DeliveredAtUtc datetime2(6) NULL,
 CONSTRAINT UX_FN_OutboxDedupe UNIQUE (ScopeKey, DedupeKey));
GO

CREATE OR ALTER TRIGGER WEB_APP.TR_FN_Outbox_AppendOnly ON WEB_APP.FOCUS_FN_Outbox AFTER UPDATE, DELETE AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (SELECT 1 FROM deleted d LEFT JOIN inserted i ON i.OutboxId = d.OutboxId
               WHERE i.OutboxId IS NULL OR i.Payload <> d.Payload OR i.DedupeKey <> d.DedupeKey OR i.Topic <> d.Topic
                  OR i.ContentSha <> d.ContentSha OR i.CreatedAtUtc <> d.CreatedAtUtc OR d.DeliveredAtUtc IS NOT NULL)
        THROW 51700, 'FOCUS_FN_Outbox events are append-only; only DeliveredAtUtc may be set, once', 1;
END;
GO
COMMIT TRANSACTION;
