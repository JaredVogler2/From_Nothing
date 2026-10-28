-- Draft (P05): reviewable workforce inference write/readback, refresh and retention for the S06 tables.
-- NOT executed by this build: no live DDL or write authority exists. Consolidated into the versioned
-- migration set in P23 (004_workforce_inference.sql) behind the migration lock and checksum ledger.
--
-- Refresh strategy
--   * Inference is part of the atomic source run: the refresh worker runs
--     queries/workforce/infer_mechanic_teams.sql (the single authority, installed verbatim as the body of
--     WEB_APP.FOCUS_FN_RefreshTeamInference by the P23 generator) while the run is CAPTURING, then hashes
--     the readback rows into the run's team_inference dataset receipt. Completing the run seals them.
--   * A refresh replaces only its own run's rows (run isolation); a sealed run's rows cannot change.
--   * Trailing window = workforce.inference_window_days (30) ending at the run cutoff, half-open.
--   * Rows older than workforce.inference_stale_after_days relative to a later cutoff are labelled
--     stale by the application and never decide a home team.
-- Retention
--   * Keep every run that is a source head or is referenced by a plan stage (published, accepted or
--     scored plans must stay explainable). Other runs' inference rows may be purged after @KeepDays.

-- Integrity beyond S06 001 (additive constraints; 001 itself stays verbatim).
ALTER TABLE WEB_APP.FOCUS_FN_TeamInference ADD
    CONSTRAINT CK_FN_TeamInference_Basis CHECK (Basis IN ('LABOR_PLURALITY', 'DEPT_FALLBACK', 'UNKNOWN')),
    CONSTRAINT CK_FN_TeamInference_Team CHECK ((Basis = 'UNKNOWN' AND InferredTeam IS NULL)
                                            OR (Basis <> 'UNKNOWN' AND InferredTeam IS NOT NULL)),
    CONSTRAINT CK_FN_TeamInference_Evidence CHECK ((Basis = 'LABOR_PLURALITY' AND EvidenceCount > 0 AND TopShare > 0 AND TopShare <= 1)
                                                OR (Basis <> 'LABOR_PLURALITY' AND EvidenceCount = 0 AND TopShare IS NULL AND IsTie = 0)),
    CONSTRAINT CK_FN_TeamInference_Window CHECK (WindowEndUtc > WindowStartUtc);
GO

-- Access paths for the refresh query (latest revision per event; per person/team union).
CREATE INDEX IX_FN_Clock_EventRevision ON WEB_APP.FOCUS_FN_ClockObservation (RunId, SourceEventKey, SourceRevision)
    INCLUDE (PersonKey, Team, StartUtc, EndUtc, IsVoided);
CREATE INDEX IX_FN_Clock_PersonTeamStart ON WEB_APP.FOCUS_FN_ClockObservation (RunId, PersonKey, Team, StartUtc)
    INCLUDE (EndUtc, IsVoided, SourceEventKey, SourceRevision);
GO

-- A sealed (COMPLETE or REJECTED) run's inference is immutable; only the retention procedure may purge.
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_TeamInference_Sealed ON WEB_APP.FOCUS_FN_TeamInference
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF CONVERT(int, SESSION_CONTEXT(N'focus_retention')) = 1 AND NOT EXISTS (SELECT 1 FROM inserted)
        RETURN;
    IF EXISTS (SELECT 1
               FROM (SELECT RunId FROM inserted UNION SELECT RunId FROM deleted) touched
               JOIN WEB_APP.FOCUS_FN_SourceRun r ON r.RunId = touched.RunId
               WHERE r.Status <> 'CAPTURING')
        THROW 51110, 'TEAM_INFERENCE_RUN_SEALED', 1;
END;
GO

-- Readback of the inference bound to the current source head (what the application consumes).
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_TeamInferenceAtHead
AS
SELECT h.ScopeKey, ti.RunId, ti.PersonKey, ti.InferredTeam, ti.WindowStartUtc, ti.WindowEndUtc, ti.EvidenceCount,
       ti.EffortMinutes, ti.TopShare, ti.IsTie, ti.Basis, ti.AlgorithmVersion,
       JSON_VALUE(CONVERT(nvarchar(max), ti.EvidencePayload), '$.runner_up_team') AS RunnerUpTeam,
       TRY_CONVERT(int, JSON_VALUE(CONVERT(nvarchar(max), ti.EvidencePayload), '$.runner_up_count')) AS RunnerUpCount,
       TRY_CONVERT(datetime2(6), JSON_VALUE(CONVERT(nvarchar(max), ti.EvidencePayload), '$.first_evidence_utc')) AS FirstEvidenceUtc,
       TRY_CONVERT(datetime2(6), JSON_VALUE(CONVERT(nvarchar(max), ti.EvidencePayload), '$.last_evidence_utc')) AS LastEvidenceUtc
FROM WEB_APP.FOCUS_FN_SourceHead h
JOIN WEB_APP.FOCUS_FN_SourceRun r ON r.RunId = h.RunId AND r.Status = 'COMPLETE'
JOIN WEB_APP.FOCUS_FN_TeamInference ti ON ti.RunId = h.RunId;
GO

-- Retention: purge inference of old runs that no head or plan stage references.
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_PurgeTeamInference
    @KeepDays int, @Purged int OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    IF @KeepDays IS NULL OR @KeepDays < 30 THROW 51111, 'TEAM_INFERENCE_RETENTION_TOO_SHORT', 1;
    BEGIN TRANSACTION;
    DECLARE @lock int;
    EXEC @lock = sp_getapplock @Resource = N'FOCUS_FN_TEAM_INFERENCE', @LockMode = 'Exclusive',
                               @LockOwner = 'Transaction', @LockTimeout = 30000;
    IF @lock < 0 THROW 51103, 'TEAM_INFERENCE_LOCK_UNAVAILABLE', 1;
    EXEC sp_set_session_context @key = N'focus_retention', @value = 1;
    DELETE ti
    FROM WEB_APP.FOCUS_FN_TeamInference ti
    JOIN WEB_APP.FOCUS_FN_SourceRun r ON r.RunId = ti.RunId
    WHERE r.Status IN ('COMPLETE', 'REJECTED')
      AND r.CaptureEndUtc < DATEADD(day, -@KeepDays, SYSUTCDATETIME())
      AND NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_SourceHead h WHERE h.RunId = r.RunId)
      AND NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_PlanStage s WHERE s.RunId = r.RunId);
    SET @Purged = @@ROWCOUNT;
    EXEC sp_set_session_context @key = N'focus_retention', @value = 0;
    COMMIT TRANSACTION;
END;
GO
