-- Migration 006 (P23): incumbent lookup and persistent delivery commitments.
-- Consolidated from the reviewed P14 draft; its statements are unchanged. PROPOSED storage, NOT executed by this
-- build. `python -m focus.cli migrate --apply` runs it only in an authorized installer session, in one transaction
-- under sp_getapplock('FOCUS_FN_MIGRATIONS'), and records this file's SHA-256 in WEB_APP.FOCUS_FN_SchemaVersion only
-- after every declared object is verified (docs/execution/INSTALL_AND_PIPELINE.md).
--
-- Three obligations stay separate (S12):
--   * physical carry-in duty: source facts locked in the ledger, never stored here;
--   * frozen goals: WEB_APP.FOCUS_FN_FrozenGoal, written at publication (P16), immutable;
--   * defeasible task commitments: the accepted incumbent plan at WEB_APP.FOCUS_FN_PlanHead.
--
-- Incumbent (task defense)
--   Live, the incumbent for (ScopeKey, Purpose) is the plan the head names, read through
--   FOCUS_FN_IncumbentAtHead. The application accepts it only when the policy sha and scope match and the
--   plan's cutoff is not after the new snapshot's cutoff. It never reads a candidate, a stage or a file.
--   Fixture mode records the S18 previous accepted plan through the same accepted-artifact path
--   (focus.engine.commitments.record_accepted_incumbent). Defense outcomes (KEPT, CREW_CHANGED, MOVED,
--   DEFERRED, UNSAFE, OUT_OF_HORIZON, COMPLETED) with both attempt results live in the plan payload;
--   FOCUS_FN_CommitmentResult is the read projection for views.
--
-- Delivery commitments (hysteresis)
--   One FOCUS_FN_Commitment row per (ScopeKey, LineNumber) once a date is established. The observation of
--   each accepted plan is keyed (ScopeKey, LineNumber, AcceptedPlanId), so a replayed plan is a no-op at any
--   age. Change events go to FOCUS_FN_Event with DedupeKey = the application's event id, computed from
--   the plan, line, kind and the version it was derived from. FOCUS_FN_RecordDeliveryObservation writes the
--   observation, the events and the versioned row in ONE transaction with an optimistic version check:
--   an event can never exist without its state change. The fixture store emulates this with an outbox
--   that a later state write acknowledges.
GO

-- Integrity beyond S06 001 (additive).
ALTER TABLE WEB_APP.FOCUS_FN_Commitment ADD
    CONSTRAINT CK_FN_Commitment_Count CHECK (ConfirmationCount >= 0),
    CONSTRAINT CK_FN_Commitment_Version CHECK (Version >= 1),
    CONSTRAINT CK_FN_Commitment_Pending CHECK ((PendingDate IS NULL AND ConfirmationCount = 0) OR PendingDate IS NOT NULL);
GO

CREATE TABLE WEB_APP.FOCUS_FN_CommitmentObservation(
    ScopeKey nvarchar(128) NOT NULL,
    LineNumber int NOT NULL,
    AcceptedPlanId uniqueidentifier NOT NULL,
    Decision varchar(16) NOT NULL CHECK (Decision IN ('ESTABLISHED', 'HOLD', 'PENDING', 'CONFIRMED', 'BAD_NEWS', 'UNKNOWN', 'COHORT_EXIT')),
    RawProjection date NULL,
    FromVersion bigint NOT NULL CHECK (FromVersion >= 0),
    Risk nvarchar(480) NULL,
    SafetyAlert nvarchar(480) NULL,
    ObservedAtUtc datetime2(6) NOT NULL,
    Payload varbinary(max) NOT NULL,
    CONSTRAINT PK_FN_CommitmentObservation PRIMARY KEY (ScopeKey, LineNumber, AcceptedPlanId));
CREATE TABLE WEB_APP.FOCUS_FN_DeliveryReceipt(
    ScopeKey nvarchar(128) NOT NULL,
    AcceptedPlanId uniqueidentifier NOT NULL,
    Payload varbinary(max) NOT NULL,
    ContentSha char(64) NOT NULL,
    RecordedAtUtc datetime2(6) NOT NULL,
    CONSTRAINT PK_FN_DeliveryReceipt PRIMARY KEY (ScopeKey, AcceptedPlanId));
CREATE TABLE WEB_APP.FOCUS_FN_CommitmentResult(
    PlanId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_PublishedPlan(PlanId),
    Ordinal int NOT NULL,
    LineNumber int NOT NULL,
    JobKeySha char(64) NOT NULL,
    PhaseKey nvarchar(256) NOT NULL,
    IncumbentAssignmentId varchar(64) NOT NULL,
    DayOffset int NOT NULL,
    Outcome varchar(16) NOT NULL CHECK (Outcome IN ('KEPT', 'CREW_CHANGED', 'MOVED', 'DEFERRED', 'UNSAFE', 'OUT_OF_HORIZON', 'COMPLETED')),
    Attempt1 nvarchar(480) NOT NULL,
    Attempt2 nvarchar(480) NOT NULL,
    Reason nvarchar(480) NOT NULL,
    NewAssignmentId varchar(64) NULL,
    CONSTRAINT PK_FN_CommitmentResult PRIMARY KEY (PlanId, Ordinal));
GO

-- Accepted plans and their projections are immutable once written.
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_CommitmentObservation_Immutable ON WEB_APP.FOCUS_FN_CommitmentObservation
AFTER UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 51410, 'FOCUS_FN_CommitmentObservation rows are immutable', 1;
END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_DeliveryReceipt_Immutable ON WEB_APP.FOCUS_FN_DeliveryReceipt
AFTER UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 51411, 'FOCUS_FN_DeliveryReceipt rows are immutable', 1;
END;
GO

-- Read the accepted incumbent for a scope/purpose (task defense input).
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_IncumbentAtHead
AS
SELECT h.ScopeKey, h.Purpose, h.Version AS HeadVersion, h.UpdatedAtUtc, p.PlanId, p.StageId, s.PolicySha, s.RunId AS SourceRunId,
       p.AcceptedAtUtc, p.ContentSha, p.Payload
FROM WEB_APP.FOCUS_FN_PlanHead h
JOIN WEB_APP.FOCUS_FN_PublishedPlan p ON p.PlanId = h.PlanId
JOIN WEB_APP.FOCUS_FN_PlanStage s ON s.StageId = p.StageId;
GO

-- Record one accepted plan's observation for one aircraft atomically. The application computes the
-- hysteresis decision (focus.engine.commitments.observe) from the row it read at @ExpectedVersion; this
-- procedure only commits it. A replay (the observation key exists) returns ReplayedObservation = 1 and
-- changes nothing. A concurrent writer makes the version check fail (51420); the caller re-reads and
-- re-derives with a bounded retry.
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_RecordDeliveryObservation
    @ScopeKey nvarchar(128), @LineNumber int, @AcceptedPlanId uniqueidentifier, @ExpectedVersion bigint,
    @Decision varchar(16), @RawProjection date, @Risk nvarchar(480), @SafetyAlert nvarchar(480),
    @CurrentDate date, @OriginalDate date, @PendingDate date, @ConfirmationCount int, @StatePayload varbinary(max),
    @ObservationPayload varbinary(max), @EventsJson nvarchar(max), @NowUtc datetime2(6)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    IF EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_CommitmentObservation WITH (UPDLOCK, HOLDLOCK)
               WHERE ScopeKey = @ScopeKey AND LineNumber = @LineNumber AND AcceptedPlanId = @AcceptedPlanId)
    BEGIN
        COMMIT TRANSACTION;
        SELECT CAST(1 AS bit) AS ReplayedObservation;
        RETURN;
    END;
    DECLARE @current bigint = (SELECT Version FROM WEB_APP.FOCUS_FN_Commitment WITH (UPDLOCK, HOLDLOCK)
                               WHERE ScopeKey = @ScopeKey AND LineNumber = @LineNumber);
    IF COALESCE(@current, 0) <> @ExpectedVersion
        THROW 51420, 'commitment version moved: re-read and re-derive', 1;
    INSERT INTO WEB_APP.FOCUS_FN_CommitmentObservation(ScopeKey, LineNumber, AcceptedPlanId, Decision, RawProjection, FromVersion, Risk,
                                                       SafetyAlert, ObservedAtUtc, Payload)
    VALUES (@ScopeKey, @LineNumber, @AcceptedPlanId, @Decision, @RawProjection, @ExpectedVersion, @Risk, @SafetyAlert, @NowUtc,
            @ObservationPayload);
    INSERT INTO WEB_APP.FOCUS_FN_Event(EventId, ScopeKey, DedupeKey, EventType, EffectiveAtUtc, RecordedAtUtc, ObservedAtUtc,
                                       SourceRevision, Payload, ContentSha, Supersedes)
    SELECT NEWID(), @ScopeKey, e.DedupeKey, e.EventType, @NowUtc, @NowUtc, @NowUtc, CONVERT(varchar(128), @AcceptedPlanId),
           CONVERT(varbinary(max), e.Payload), e.ContentSha, NULL
    FROM OPENJSON(@EventsJson) WITH (DedupeKey varchar(256), EventType varchar(48), Payload nvarchar(max) AS JSON, ContentSha char(64)) e
    WHERE NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_Event x WHERE x.ScopeKey = @ScopeKey AND x.DedupeKey = e.DedupeKey);
    IF @CurrentDate IS NOT NULL
    BEGIN
        IF @current IS NULL
            INSERT INTO WEB_APP.FOCUS_FN_Commitment(ScopeKey, LineNumber, CurrentDate, OriginalDate, PendingDate, ConfirmationCount,
                                                    LastAcceptedPlanId, Version, Payload)
            VALUES (@ScopeKey, @LineNumber, @CurrentDate, @OriginalDate, @PendingDate, @ConfirmationCount, @AcceptedPlanId, 1, @StatePayload);
        ELSE
            UPDATE WEB_APP.FOCUS_FN_Commitment
            SET CurrentDate = @CurrentDate, PendingDate = @PendingDate, ConfirmationCount = @ConfirmationCount,
                LastAcceptedPlanId = @AcceptedPlanId, Version = Version + 1, Payload = @StatePayload
            WHERE ScopeKey = @ScopeKey AND LineNumber = @LineNumber AND Version = @ExpectedVersion;   -- OriginalDate never changes
    END;
    COMMIT TRANSACTION;
    SELECT CAST(0 AS bit) AS ReplayedObservation;
END;
GO

-- Readback: the committed row, its acknowledged change events and one plan's receipt.
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_DeliveryCommitmentState
AS
SELECT c.ScopeKey, c.LineNumber, c.OriginalDate, c.CurrentDate, c.PendingDate, c.ConfirmationCount, c.LastAcceptedPlanId, c.Version,
       o.RawProjection AS LastRawProjection, o.Risk AS LastRisk, o.SafetyAlert AS LastSafetyAlert, o.Decision AS LastDecision
FROM WEB_APP.FOCUS_FN_Commitment c
OUTER APPLY (SELECT TOP (1) x.RawProjection, x.Risk, x.SafetyAlert, x.Decision
             FROM WEB_APP.FOCUS_FN_CommitmentObservation x
             WHERE x.ScopeKey = c.ScopeKey AND x.LineNumber = c.LineNumber
             ORDER BY x.FromVersion DESC, x.ObservedAtUtc DESC) o;
GO
