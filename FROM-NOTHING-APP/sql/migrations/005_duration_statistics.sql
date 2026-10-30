-- Migration 005 (P23): duration observation and statistics integrity, readback and retention.
-- Consolidated from the reviewed P07 draft; its statements are unchanged. PROPOSED storage, NOT executed by this
-- build. `python -m focus.cli migrate --apply` runs it only in an authorized installer session, in one transaction
-- under sp_getapplock('FOCUS_FN_MIGRATIONS'), and records this file's SHA-256 in WEB_APP.FOCUS_FN_SchemaVersion only
-- after every declared object is verified (docs/execution/INSTALL_AND_PIPELINE.md).
--
-- Refresh strategy
--   * Inside the atomic source run (status CAPTURING), the worker runs queries/durations/build_observations.sql
--     and then queries/durations/percentiles.sql once per active policy. It reads back the statistics and
--     compares them with focus.data.duration_gauge.compute_statistics over the same admitted observations.
--     Any drift beyond the 6 dp quantization blocks run completion. Completing the run seals both tables.
--   * Statistics are bound to (RunId, GroupKeySha, PolicyId): a P55/mean/P75/P90 selection or a new minimum
--     sample produces a new policy and new rows. Rows of an accepted run are never rewritten, so issued goals
--     and historical scores keep the statistics they were computed from.
--   * The application consumes P75 (duration.default_statistic) of admitted comparable elapsed episodes; a
--     group below the minimum sample, or a stale one, is unavailable (no flat default, no effort/crew division).
-- Retention
--   * Keep every run that is a source head or referenced by a plan stage; purge others after @KeepDays.

ALTER TABLE WEB_APP.FOCUS_FN_DurationObservation ADD
    CONSTRAINT CK_FN_DurationObservation_Class CHECK (Class IN ('NC', 'PU', 'CI')),
    CONSTRAINT CK_FN_DurationObservation_Cycle CHECK (CycleMinutes >= ActiveElapsedMinutes);
GO
ALTER TABLE WEB_APP.FOCUS_FN_DurationStatistics ADD
    CONSTRAINT UX_FN_DurationStatistics_Group UNIQUE (RunId, GroupKeySha, PolicyId),
    CONSTRAINT CK_FN_DurationStatistics_Basis CHECK (MeasurementBasis = 'ELAPSED_ACTIVE'),
    CONSTRAINT CK_FN_DurationStatistics_Window CHECK (SampleEndUtc >= SampleStartUtc);
GO

CREATE INDEX IX_FN_Clock_EpisodeRevision ON WEB_APP.FOCUS_FN_ClockObservation (RunId, ReworkClass, SourceEventKey, SourceRevision)
    INCLUDE (EpisodeKey, PersonKey, StartUtc, EndUtc, IsVoided);
CREATE INDEX IX_FN_DurationObservation_Group ON WEB_APP.FOCUS_FN_DurationObservation (RunId, GroupKeySha, Admitted)
    INCLUDE (ActiveElapsedMinutes, PersonEffortMinutes, CompletedAtUtc);
GO

-- Sealed runs are immutable; only the retention procedure may purge whole runs.
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_DurationObservation_Sealed ON WEB_APP.FOCUS_FN_DurationObservation
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF CONVERT(int, SESSION_CONTEXT(N'focus_retention')) = 1 AND NOT EXISTS (SELECT 1 FROM inserted)
        RETURN;
    IF EXISTS (SELECT 1 FROM (SELECT RunId FROM inserted UNION SELECT RunId FROM deleted) touched
               JOIN WEB_APP.FOCUS_FN_SourceRun r ON r.RunId = touched.RunId WHERE r.Status <> 'CAPTURING')
        THROW 51210, 'DURATION_RUN_SEALED', 1;
END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_DurationStatistics_Sealed ON WEB_APP.FOCUS_FN_DurationStatistics
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF CONVERT(int, SESSION_CONTEXT(N'focus_retention')) = 1 AND NOT EXISTS (SELECT 1 FROM inserted)
        RETURN;
    IF EXISTS (SELECT 1 FROM (SELECT RunId FROM inserted UNION SELECT RunId FROM deleted) touched
               JOIN WEB_APP.FOCUS_FN_SourceRun r ON r.RunId = touched.RunId WHERE r.Status <> 'CAPTURING')
        THROW 51211, 'DURATION_STATISTICS_RUN_SEALED', 1;
END;
GO

-- Readback of the statistics bound to the current source head (what the application consumes).
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_DurationStatisticsAtHead
AS
SELECT h.ScopeKey, s.RunId, s.PolicyId, s.GroupKeySha, s.MeasurementBasis, s.RawCount, s.AdmittedCount, s.ExcludedCount,
       s.MeanMinutes, s.P50Minutes, s.P55Minutes, s.P75Minutes, s.P90Minutes, s.SampleStartUtc, s.SampleEndUtc, s.MethodSha,
       JSON_VALUE(CONVERT(nvarchar(max), s.EvidencePayload), '$.class') AS Class,
       JSON_VALUE(CONVERT(nvarchar(max), s.EvidencePayload), '$.team') AS Team,
       TRY_CONVERT(bit, JSON_VALUE(CONVERT(nvarchar(max), s.EvidencePayload), '$.sufficient')) AS Sufficient
FROM WEB_APP.FOCUS_FN_SourceHead h
JOIN WEB_APP.FOCUS_FN_SourceRun r ON r.RunId = h.RunId AND r.Status = 'COMPLETE'
JOIN WEB_APP.FOCUS_FN_DurationStatistics s ON s.RunId = h.RunId;
GO

CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_PurgeDurationEvidence
    @KeepDays int, @PurgedObservations int OUTPUT, @PurgedStatistics int OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    IF @KeepDays IS NULL OR @KeepDays < 30 THROW 51212, 'DURATION_RETENTION_TOO_SHORT', 1;
    BEGIN TRANSACTION;
    DECLARE @lock int;
    EXEC @lock = sp_getapplock @Resource = N'FOCUS_FN_DURATIONS', @LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 30000;
    IF @lock < 0 THROW 51213, 'DURATION_LOCK_UNAVAILABLE', 1;
    EXEC sp_set_session_context @key = N'focus_retention', @value = 1;
    DECLARE @Purgeable TABLE (RunId uniqueidentifier PRIMARY KEY);
    INSERT INTO @Purgeable (RunId)
    SELECT r.RunId FROM WEB_APP.FOCUS_FN_SourceRun r
    WHERE r.Status IN ('COMPLETE', 'REJECTED')
      AND r.CaptureEndUtc < DATEADD(day, -@KeepDays, SYSUTCDATETIME())
      AND NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_SourceHead h WHERE h.RunId = r.RunId)
      AND NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_PlanStage s WHERE s.RunId = r.RunId);
    DELETE s FROM WEB_APP.FOCUS_FN_DurationStatistics s JOIN @Purgeable p ON p.RunId = s.RunId;
    SET @PurgedStatistics = @@ROWCOUNT;
    DELETE o FROM WEB_APP.FOCUS_FN_DurationObservation o JOIN @Purgeable p ON p.RunId = o.RunId;
    SET @PurgedObservations = @@ROWCOUNT;
    EXEC sp_set_session_context @key = N'focus_retention', @value = 0;
    COMMIT TRANSACTION;
END;
GO
