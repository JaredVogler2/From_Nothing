-- S08/P07 SQL descriptive statistics over admitted duration observations (continuous percentiles).
-- NOT executed by this build. PERCENTILE_CONT uses the same continuous definition as the application
-- (h=(n-1)p, x_lo + (h-lo)(x_hi-x_lo)), so SQL and focus.data.duration_gauge.compute_statistics agree on
-- P50/P55/P75/P90; the mean is AVG over the same admitted rows (it need not lie between P55 and P75).
-- Groups: exact (class, team) and the approved broader (class) fallback (duration.group_fallback).
--
-- Group key hashes equal the application's content_hash(list(group)) (lowercase SHA-256 of UTF-8 canonical JSON)
-- for admitted ASCII class/team codes without JSON escapes; codes outside that set are rejected at admission.
--
-- Bound parameters (pymssql positional placeholders, in order): RunId, PolicyId, MinimumSample, MethodSha (the application's METHOD_SHA,
-- passed in so the method text has one authority).
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @RunId uniqueidentifier = %s;
DECLARE @PolicyId uniqueidentifier = %s;
DECLARE @MinimumSample int = %s;
DECLARE @MethodSha char(64) = %s;

IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_SourceRun WHERE RunId = @RunId AND Status = 'CAPTURING')
    THROW 51203, 'DURATION_RUN_NOT_CAPTURING', 1;

BEGIN TRANSACTION;
DELETE FROM WEB_APP.FOCUS_FN_DurationStatistics WHERE RunId = @RunId AND PolicyId = @PolicyId;

WITH Grouped AS (
    -- exact (class, team) group
    SELECT o.GroupKeySha, o.Class, o.Team, o.ActiveElapsedMinutes, o.PersonEffortMinutes, o.CompletedAtUtc, o.Admitted
    FROM WEB_APP.FOCUS_FN_DurationObservation o
    WHERE o.RunId = @RunId
    UNION ALL
    -- approved broader (class) group
    SELECT LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varbinary(max), CONVERT(varchar(max),
               CONCAT(N'["', o.Class, N'"]') COLLATE Latin1_General_100_BIN2_UTF8))), 2)),
           o.Class, NULL, o.ActiveElapsedMinutes, o.PersonEffortMinutes, o.CompletedAtUtc, o.Admitted
    FROM WEB_APP.FOCUS_FN_DurationObservation o
    WHERE o.RunId = @RunId
), Counts AS (
    SELECT GroupKeySha, MIN(Class) AS Class, MIN(Team) AS Team, COUNT_BIG(*) AS RawCount,
           SUM(CASE WHEN Admitted = 1 THEN 1 ELSE 0 END) AS AdmittedCount,
           MIN(CASE WHEN Admitted = 1 THEN CompletedAtUtc END) AS SampleStartUtc,
           MAX(CASE WHEN Admitted = 1 THEN CompletedAtUtc END) AS SampleEndUtc,
           AVG(CASE WHEN Admitted = 1 THEN PersonEffortMinutes END) AS EffortMean
    FROM Grouped GROUP BY GroupKeySha
), Pct AS (
    SELECT DISTINCT GroupKeySha,
           AVG(CONVERT(decimal(38,12), ActiveElapsedMinutes)) OVER (PARTITION BY GroupKeySha) AS MeanMinutes,
           PERCENTILE_CONT(.50) WITHIN GROUP (ORDER BY ActiveElapsedMinutes) OVER (PARTITION BY GroupKeySha) AS P50,
           PERCENTILE_CONT(.55) WITHIN GROUP (ORDER BY ActiveElapsedMinutes) OVER (PARTITION BY GroupKeySha) AS P55,
           PERCENTILE_CONT(.75) WITHIN GROUP (ORDER BY ActiveElapsedMinutes) OVER (PARTITION BY GroupKeySha) AS P75,
           PERCENTILE_CONT(.90) WITHIN GROUP (ORDER BY ActiveElapsedMinutes) OVER (PARTITION BY GroupKeySha) AS P90
    FROM Grouped
    WHERE Admitted = 1
)
INSERT INTO WEB_APP.FOCUS_FN_DurationStatistics(StatisticsId, RunId, GroupKeySha, MeasurementBasis, RawCount, AdmittedCount, ExcludedCount,
                                                MeanMinutes, P50Minutes, P55Minutes, P75Minutes, P90Minutes, SampleStartUtc, SampleEndUtc,
                                                MethodSha, PolicyId, EvidencePayload)
SELECT NEWID(), @RunId, c.GroupKeySha, 'ELAPSED_ACTIVE', c.RawCount, c.AdmittedCount, c.RawCount - c.AdmittedCount,
       -- PERCENTILE_CONT returns float while the application uses exact fractions: values agree after rounding to
       -- 6 dp except float noise exactly at a half-unit boundary, which the readback comparison reports as drift.
       CONVERT(decimal(19,6), p.MeanMinutes), CONVERT(decimal(19,6), p.P50), CONVERT(decimal(19,6), p.P55),
       CONVERT(decimal(19,6), p.P75), CONVERT(decimal(19,6), p.P90), c.SampleStartUtc, c.SampleEndUtc,
       @MethodSha,
       @PolicyId,
       CONVERT(varbinary(max), CONVERT(nvarchar(max), (
           SELECT c.Class AS class, c.Team AS team, c.AdmittedCount AS n, @MinimumSample AS minimum_sample,
                  CASE WHEN c.AdmittedCount >= @MinimumSample THEN 1 ELSE 0 END AS sufficient,
                  CONVERT(decimal(19,6), c.EffortMean) AS effort_mean_descriptive, 'minutes' AS unit
           FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)))
FROM Counts c
JOIN Pct p ON p.GroupKeySha = c.GroupKeySha
WHERE c.AdmittedCount > 0;
COMMIT TRANSACTION;

SELECT s.GroupKeySha, s.AdmittedCount, s.MeanMinutes, s.P50Minutes, s.P55Minutes, s.P75Minutes, s.P90Minutes, s.SampleStartUtc, s.SampleEndUtc
FROM WEB_APP.FOCUS_FN_DurationStatistics s
WHERE s.RunId = @RunId AND s.PolicyId = @PolicyId
ORDER BY s.GroupKeySha;
