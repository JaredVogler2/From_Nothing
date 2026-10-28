-- S07 SQL-owned mechanic team inference (P05). Production authority for home-team inference:
-- the application consumes the persisted WEB_APP.FOCUS_FN_TeamInference rows bound to one source
-- run (queries/workforce/read_team_inference.sql) and never runs its own production inference.
-- focus.data.workforce.reference_inference re-implements these semantics only to verify the
-- fixture contract before an authorized installation.
--
-- NOT executed by this build: it needs the FOCUS_FN migrations (consolidated in P23) and write
-- authority. Run by the source-run refresh worker while the run is still CAPTURING, so the
-- inference is part of the run's atomic capture and a rejected run never exposes it.
--
-- Bound parameters (pymssql positional placeholders, in order): RunId, WindowStartUtc, WindowEndUtc.
-- Default window: trailing 30 days ending at the run cutoff, half-open [start, end).
--
-- Semantics (S07):
--  1. Admit this run's raw clock records only: the latest SourceRevision of each SourceEventKey
--     (a voided correction removes the event instead of counting it twice), unvoided, with a
--     non-blank team, StartUtc inside the window.
--  2. Rank teams per person by transaction count: ORDER BY n DESC,Team ASC. Team is compared by
--     code point (Latin1_General_BIN2) so the tie-break never depends on server collation.
--     Minutes never stand in for the count and there is no unordered "first row".
--  3. Effort minutes are the per-(person, team) running-max interval union (S08 island method),
--     persisted separately from the count. EffortMinutes is the winning team's union.
--  4. TopShare = n/total_n as decimal(9,8); runner-up team/count, total count and first/last
--     evidence instants are kept in EvidencePayload; IsTie = 1 when the runner-up count equals n.
--  5. People without admitted team evidence get an explicit DEPT_FALLBACK row only through an
--     actual HR accounting department (IE787.BERT_DAILY, latest EFFDT, active) mapped to a
--     PRIMARY_TEAM in WEB_APP.FOCUS_DEPT_TEAM_SKILL_REF. Team-less evidence with no mapping is
--     persisted as UNKNOWN with no team. No team is guessed or backfilled.
--  6. The legacy WEB_APP.FOCUS_MECH_TEAM_INFERRED table is input evidence only; it is copied into
--     EvidencePayload.legacy_team for comparison and never decides the inferred team.

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @RunId uniqueidentifier = %s;
DECLARE @WindowStart datetime2(6) = %s;
DECLARE @WindowEnd datetime2(6) = %s;
DECLARE @AlgorithmVersion varchar(64) = 'S07-plurality-v1';

IF @WindowStart IS NULL OR @WindowEnd IS NULL OR @WindowEnd <= @WindowStart
    THROW 51101, 'TEAM_INFERENCE_WINDOW_INVALID', 1;
IF NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_SourceRun WHERE RunId = @RunId AND Status = 'CAPTURING')
    THROW 51102, 'TEAM_INFERENCE_RUN_NOT_CAPTURING', 1;

BEGIN TRANSACTION;

DECLARE @lock int;
EXEC @lock = sp_getapplock @Resource = N'FOCUS_FN_TEAM_INFERENCE', @LockMode = 'Exclusive',
                           @LockOwner = 'Transaction', @LockTimeout = 30000;
IF @lock < 0 THROW 51103, 'TEAM_INFERENCE_LOCK_UNAVAILABLE', 1;

-- A refresh of a still-CAPTURING run replaces only that run's rows (run isolation).
DELETE FROM WEB_APP.FOCUS_FN_TeamInference WHERE RunId = @RunId;

-- 1. Admitted raw clock evidence.
WITH Latest AS (
    SELECT c.PersonKey, c.Team, c.StartUtc, c.EndUtc, c.IsVoided,
           ROW_NUMBER() OVER (PARTITION BY c.SourceEventKey
                              ORDER BY TRY_CONVERT(bigint, c.SourceRevision) DESC,
                                       c.SourceRevision COLLATE Latin1_General_BIN2 DESC) AS RevisionRank
    FROM WEB_APP.FOCUS_FN_ClockObservation c
    WHERE c.RunId = @RunId
)
SELECT PersonKey,
       CONVERT(nvarchar(80), NULLIF(LTRIM(RTRIM(Team)), N'')) COLLATE Latin1_General_BIN2 AS Team,
       StartUtc, EndUtc
INTO #Admitted
FROM Latest
WHERE RevisionRank = 1 AND IsVoided=0 AND StartUtc >= @WindowStart AND StartUtc < @WindowEnd;

-- 2./3. Count per (person, team) and running-max interval union for effort.
WITH Ordered AS (
    SELECT PersonKey, Team, StartUtc, EndUtc,
           MAX(EndUtc) OVER (PARTITION BY PersonKey, Team ORDER BY StartUtc, EndUtc
                             ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS PriorMaxEnd
    FROM #Admitted
    WHERE Team IS NOT NULL
), Marked AS (
    SELECT PersonKey, Team, StartUtc, EndUtc,
           CASE WHEN PriorMaxEnd IS NULL OR StartUtc > PriorMaxEnd THEN 1 ELSE 0 END AS NewIsland
    FROM Ordered
), Numbered AS (
    SELECT PersonKey, Team, StartUtc, EndUtc,
           SUM(NewIsland) OVER (PARTITION BY PersonKey, Team ORDER BY StartUtc, EndUtc
                                   ROWS UNBOUNDED PRECEDING) AS IslandNo
    FROM Marked
), Islands AS (
    SELECT PersonKey, Team, MIN(StartUtc) AS IslandStart, MAX(EndUtc) AS IslandEnd
    FROM Numbered
    GROUP BY PersonKey, Team, IslandNo
), Effort AS (
    SELECT PersonKey, Team,
           -- decimal(38,12)/int keeps scale 12; ROUND(...,6) rounds half away from zero exactly once
           CONVERT(decimal(19,6), ROUND(CONVERT(decimal(38,12), SUM(DATEDIFF_BIG(microsecond, IslandStart, IslandEnd))) / 60000000, 6)) AS effort
    FROM Islands
    GROUP BY PersonKey, Team
), Evidence AS (
    SELECT PersonKey, Team, COUNT_BIG(*) AS n
    FROM #Admitted
    WHERE Team IS NOT NULL
    GROUP BY PersonKey, Team
), Counted AS (
    SELECT e.PersonKey, e.Team, e.n, f.effort
    FROM Evidence e
    JOIN Effort f ON f.PersonKey = e.PersonKey AND f.Team = e.Team
), Ranked AS (
    SELECT PersonKey, Team, n, effort,
           ROW_NUMBER() OVER(PARTITION BY PersonKey ORDER BY n DESC,Team ASC) AS rn,
           SUM(n) OVER(PARTITION BY PersonKey) AS total_n,
           LEAD(n) OVER(PARTITION BY PersonKey ORDER BY n DESC,Team ASC) AS second_n,
           LEAD(Team) OVER(PARTITION BY PersonKey ORDER BY n DESC,Team ASC) AS second_team
    FROM Counted
), Span AS (
    SELECT PersonKey, MIN(StartUtc) AS first_utc, MAX(EndUtc) AS last_utc
    FROM #Admitted
    WHERE Team IS NOT NULL
    GROUP BY PersonKey
), Legacy AS (
    SELECT CONVERT(nvarchar(256), l.BEMS_ID) AS PersonKey, l.INFERRED_TEAM, l.SOURCE, l.LABOR_RECORDS,
           ROW_NUMBER() OVER (PARTITION BY l.BEMS_ID ORDER BY l.LABOR_RECORDS DESC, l.INFERRED_TEAM COLLATE Latin1_General_BIN2 ASC) AS rn
    FROM WEB_APP.FOCUS_MECH_TEAM_INFERRED l
)
INSERT INTO WEB_APP.FOCUS_FN_TeamInference(RunId, PersonKey, InferredTeam, WindowStartUtc, WindowEndUtc, EvidenceCount,
                                           EffortMinutes, TopShare, IsTie, Basis, AlgorithmVersion, EvidencePayload)
SELECT @RunId, r.PersonKey, r.Team, @WindowStart, @WindowEnd, CONVERT(int, r.n), r.effort,
       -- decimal(38,20)/bigint keeps scale 20 under SQL Server's precision rules; ROUND(...,8) rounds
       -- half away from zero exactly once (the reference query's n*1.0/total_n, made explicit).
       CONVERT(decimal(9,8), ROUND(CONVERT(decimal(38,20), r.n) / r.total_n, 8)),
       CASE WHEN r.n = r.second_n THEN 1 ELSE 0 END,
       'LABOR_PLURALITY', @AlgorithmVersion,
       CONVERT(varbinary(max), CONVERT(nvarchar(max), (
           SELECT r.second_team AS runner_up_team, r.second_n AS runner_up_count, r.total_n AS total_count,
                  CONVERT(varchar(33), s.first_utc, 127) AS first_evidence_utc,
                  CONVERT(varchar(33), s.last_utc, 127) AS last_evidence_utc,
                  'COUNT_PLURALITY;TIE=TEAM_BIN2_ASC;EFFORT=OWNER_TEAM_UNION' AS method,
                  lg.INFERRED_TEAM AS legacy_team, lg.SOURCE AS legacy_source, lg.LABOR_RECORDS AS legacy_labor_records
           FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)))
FROM Ranked r
JOIN Span s ON s.PersonKey = r.PersonKey
LEFT JOIN Legacy lg ON lg.PersonKey = r.PersonKey AND lg.rn = 1
WHERE r.rn = 1;

-- 5. Explicit department fallback (HR department -> FOCUS_DEPT_TEAM_SKILL_REF) and UNKNOWN.
WITH Hr AS (
    SELECT CONVERT(nvarchar(256), b.BEMS_ID) AS PersonKey, b.ACCTG_DEPT_CD, b.EMPL_STAT_CD,
           ROW_NUMBER() OVER (PARTITION BY b.BEMS_ID ORDER BY b.EFFDT DESC) AS rn
    FROM IE787.BERT_DAILY b
    WHERE b.EFFDT IS NULL OR b.EFFDT < CONVERT(date, @WindowEnd)
), Mapped AS (
    SELECT h.PersonKey, h.ACCTG_DEPT_CD, NULLIF(LTRIM(RTRIM(d.PRIMARY_TEAM)), '') AS PrimaryTeam
    FROM Hr h
    JOIN WEB_APP.FOCUS_DEPT_TEAM_SKILL_REF d ON d.ACCTG_DEPT_CD = h.ACCTG_DEPT_CD
    WHERE h.rn = 1 AND h.EMPL_STAT_CD = 'A'
), Teamless AS (
    SELECT DISTINCT a.PersonKey FROM #Admitted a WHERE a.Team IS NULL
), Candidates AS (
    SELECT m.PersonKey FROM Mapped m WHERE m.PrimaryTeam IS NOT NULL
    UNION
    SELECT t.PersonKey FROM Teamless t
)
INSERT INTO WEB_APP.FOCUS_FN_TeamInference(RunId, PersonKey, InferredTeam, WindowStartUtc, WindowEndUtc, EvidenceCount,
                                           EffortMinutes, TopShare, IsTie, Basis, AlgorithmVersion, EvidencePayload)
SELECT @RunId, c.PersonKey, m.PrimaryTeam, @WindowStart, @WindowEnd, 0, 0, NULL, 0,
       CASE WHEN m.PrimaryTeam IS NOT NULL THEN 'DEPT_FALLBACK' ELSE 'UNKNOWN' END, @AlgorithmVersion,
       CONVERT(varbinary(max), CONVERT(nvarchar(max), (
           SELECT NULL AS runner_up_team, NULL AS runner_up_count, 0 AS total_count,
                  NULL AS first_evidence_utc, NULL AS last_evidence_utc,
                  CASE WHEN m.PrimaryTeam IS NOT NULL THEN 'DEPT_FALLBACK:FOCUS_DEPT_TEAM_SKILL_REF'
                       ELSE 'UNKNOWN:NO_TEAM_EVIDENCE_NO_DEPARTMENT_MAPPING' END AS method,
                  m.ACCTG_DEPT_CD AS acctg_dept_cd
           FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)))
FROM Candidates c
LEFT JOIN Mapped m ON m.PersonKey = c.PersonKey
WHERE NOT EXISTS (SELECT 1 FROM WEB_APP.FOCUS_FN_TeamInference ti WHERE ti.RunId = @RunId AND ti.PersonKey = c.PersonKey);

DROP TABLE #Admitted;

COMMIT TRANSACTION;

-- Readback for the run receipt (the worker hashes these rows into the team_inference dataset).
SELECT ti.PersonKey, ti.InferredTeam, ti.EvidenceCount, ti.EffortMinutes, ti.TopShare, ti.IsTie, ti.Basis, ti.AlgorithmVersion
FROM WEB_APP.FOCUS_FN_TeamInference ti
WHERE ti.RunId = @RunId
ORDER BY ti.PersonKey;
