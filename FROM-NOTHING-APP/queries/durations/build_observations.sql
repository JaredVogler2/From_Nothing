-- S08/P07 SQL-owned duration observations: one row per completed comparable rework episode.
-- NOT executed by this build (needs the FOCUS_FN migrations and write authority). Runs inside the
-- source-run capture while the run is CAPTURING, so observations seal with the run.
--
-- Bound parameters (pymssql positional placeholders, in order): RunId, PolicyId.
-- The worker first loads #EpisodeCompletion(EpisodeKey nvarchar(256), ReworkClass varchar(8), Team nvarchar(80),
-- CompletedAtUtc datetime2(6), Verified bit, Reopened bit) from the run's admitted source rows (verified completion,
-- authoritative NC/PU/CI classification and parent/root links). Unverified or reopened episodes are censored:
-- they are written with Admitted = 0 and an exclusion reason, never silently dropped (no survivor bias).
--
-- Semantics (identical to focus.data.duration_gauge.episode_metrics):
--  * latest SourceRevision per SourceEventKey; voided corrections remove the event;
--  * job active elapsed = running-max union of all owners' intervals (touching merges, gaps kept,
--    no cap, no short-clock removal);
--  * person effort = sum over owners (PersonKey) of each owner's own union; cycle = first-on to last-off;
--  * exact microseconds; minutes rounded half away from zero to decimal(19,6) once at persistence.
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @RunId uniqueidentifier = %s;
DECLARE @PolicyId uniqueidentifier = %s;

IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_SourceRun WHERE RunId = @RunId AND Status = 'CAPTURING')
    THROW 51201, 'DURATION_RUN_NOT_CAPTURING', 1;
IF OBJECT_ID('tempdb..#EpisodeCompletion') IS NULL
    THROW 51202, 'DURATION_COMPLETION_EVIDENCE_MISSING', 1;

BEGIN TRANSACTION;
DELETE FROM WEB_APP.FOCUS_FN_DurationObservation WHERE RunId = @RunId;   -- run-isolated refresh

WITH Latest AS (
    SELECT c.EpisodeKey, c.PersonKey, c.StartUtc, c.EndUtc, c.IsVoided,
           ROW_NUMBER() OVER (PARTITION BY c.SourceEventKey
                              ORDER BY TRY_CONVERT(bigint, c.SourceRevision) DESC,
                                       c.SourceRevision COLLATE Latin1_General_BIN2 DESC) AS RevisionRank
    FROM WEB_APP.FOCUS_FN_ClockObservation c
    WHERE c.RunId = @RunId AND c.ReworkClass IN ('NC', 'PU', 'CI')
)
SELECT EpisodeKey, PersonKey, StartUtc, EndUtc
INTO #Clock
FROM Latest
WHERE RevisionRank = 1 AND IsVoided = 0 AND EndUtc > StartUtc;

-- job union (all owners) per episode
WITH Ordered AS (
    SELECT EpisodeKey, StartUtc, EndUtc,
           MAX(EndUtc) OVER (PARTITION BY EpisodeKey ORDER BY StartUtc, EndUtc ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS PriorMaxEnd
    FROM #Clock
), Numbered AS (
    SELECT EpisodeKey, StartUtc, EndUtc,
           SUM(CASE WHEN PriorMaxEnd IS NULL OR StartUtc > PriorMaxEnd THEN 1 ELSE 0 END)
               OVER (PARTITION BY EpisodeKey ORDER BY StartUtc, EndUtc ROWS UNBOUNDED PRECEDING) AS IslandNo
    FROM Ordered
), Islands AS (
    SELECT EpisodeKey, MIN(StartUtc) AS S, MAX(EndUtc) AS E FROM Numbered GROUP BY EpisodeKey, IslandNo
)
SELECT EpisodeKey, SUM(DATEDIFF_BIG(microsecond, S, E)) AS ActiveUs
INTO #Active
FROM Islands GROUP BY EpisodeKey;

-- owner unions per episode, summed
WITH Ordered AS (
    SELECT EpisodeKey, PersonKey, StartUtc, EndUtc,
           MAX(EndUtc) OVER (PARTITION BY EpisodeKey, PersonKey ORDER BY StartUtc, EndUtc
                             ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS PriorMaxEnd
    FROM #Clock
), Numbered AS (
    SELECT EpisodeKey, PersonKey, StartUtc, EndUtc,
           SUM(CASE WHEN PriorMaxEnd IS NULL OR StartUtc > PriorMaxEnd THEN 1 ELSE 0 END)
               OVER (PARTITION BY EpisodeKey, PersonKey ORDER BY StartUtc, EndUtc ROWS UNBOUNDED PRECEDING) AS IslandNo
    FROM Ordered
), Islands AS (
    SELECT EpisodeKey, PersonKey, MIN(StartUtc) AS S, MAX(EndUtc) AS E FROM Numbered GROUP BY EpisodeKey, PersonKey, IslandNo
)
SELECT EpisodeKey, SUM(DATEDIFF_BIG(microsecond, S, E)) AS EffortUs
INTO #Effort
FROM Islands GROUP BY EpisodeKey;

INSERT INTO WEB_APP.FOCUS_FN_DurationObservation(ObservationId, RunId, EpisodeKeySha, GroupKeySha, Class, Team,
                                                 ActiveElapsedMinutes, PersonEffortMinutes, CycleMinutes, CompletedAtUtc, Admitted, EvidencePayload)
SELECT NEWID(), @RunId,
       LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varbinary(max), CONVERT(varchar(max), ec.EpisodeKey COLLATE Latin1_General_100_BIN2_UTF8))), 2)),
       LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varbinary(max), CONVERT(varchar(max),
           CONCAT(N'["', ec.ReworkClass, N'","', COALESCE(ec.Team, N''), N'"]') COLLATE Latin1_General_100_BIN2_UTF8))), 2)),
       ec.ReworkClass, ec.Team,
       CONVERT(decimal(19,6), ROUND(CONVERT(decimal(38,12), a.ActiveUs) / 60000000, 6)),
       CONVERT(decimal(19,6), ROUND(CONVERT(decimal(38,12), f.EffortUs) / 60000000, 6)),
       CONVERT(decimal(19,6), ROUND(CONVERT(decimal(38,12), DATEDIFF_BIG(microsecond, span.FirstOn, span.LastOff)) / 60000000, 6)),
       ec.CompletedAtUtc,
       CASE WHEN ec.Verified = 1 AND ec.Reopened = 0 THEN 1 ELSE 0 END,
       CONVERT(varbinary(max), CONVERT(nvarchar(max), (
           SELECT ec.EpisodeKey AS episode_key, @PolicyId AS policy_id,
                  CASE WHEN ec.Reopened = 1 THEN 'STATUS_REOPENED' WHEN ec.Verified = 0 THEN 'STATUS_UNVERIFIED' END AS excluded_reason,
                  'RUNNING_MAX_UNION;OWNER_UNIONS;FIRST_ON_LAST_OFF;US_EXACT;6DP_HALF_AWAY' AS method
           FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)))
FROM #EpisodeCompletion ec
JOIN #Active a ON a.EpisodeKey = ec.EpisodeKey
JOIN #Effort f ON f.EpisodeKey = ec.EpisodeKey
JOIN (SELECT EpisodeKey, MIN(StartUtc) AS FirstOn, MAX(EndUtc) AS LastOff FROM #Clock GROUP BY EpisodeKey) span ON span.EpisodeKey = ec.EpisodeKey
WHERE a.ActiveUs > 0;

DROP TABLE #Clock; DROP TABLE #Active; DROP TABLE #Effort;
COMMIT TRANSACTION;
