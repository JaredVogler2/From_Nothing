-- P12 read-only: the latest liability checkpoint per original sale basis (the cumulative accounted floor).
-- The highest SequenceNumber wins. Two checkpoints with the same highest sequence are reported as
-- CHECKPOINT_SEQUENCE_CONFLICT instead of picking one; no checkpoint means no floor (unknown, never zero).
-- Bound parameters (pymssql positional placeholder): CutoffUtc (datetime2, UTC); checkpoints recorded after the cutoff are excluded.
WITH Ranked AS (
    SELECT c.OriginalBasisId, c.CheckpointId, c.SequenceNumber, c.AsOfUtc, c.ElapsedLateDays, c.AssessedAmount, c.CumulativeAccounted,
           c.AssessmentCoverage, c.CheckpointKind,
           RANK() OVER (PARTITION BY c.OriginalBasisId ORDER BY c.SequenceNumber DESC) AS rk
    FROM WEB_APP.FOCUS_SOL_TESTING2_LiabilityCheckpoint c
    WHERE c.CreatedAtUtc <= %s
)
SELECT b.LineNumber, b.OriginalBasisId, b.TermId, b.Currency, r.CheckpointId, r.SequenceNumber, r.AsOfUtc, r.ElapsedLateDays,
       r.AssessedAmount, r.CumulativeAccounted, r.AssessmentCoverage, r.CheckpointKind,
       CASE WHEN r.OriginalBasisId IS NULL THEN 'NO_CHECKPOINT'
            WHEN COUNT_BIG(*) OVER (PARTITION BY b.OriginalBasisId) > 1 THEN 'CHECKPOINT_SEQUENCE_CONFLICT'
            ELSE 'LATEST' END AS FloorState
FROM WEB_APP.FOCUS_SOL_TESTING2_OriginalSaleBasis b
LEFT JOIN Ranked r ON r.OriginalBasisId = b.OriginalBasisId AND r.rk = 1
ORDER BY b.LineNumber, b.OriginalBasisId;
