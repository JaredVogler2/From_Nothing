-- Draft reader (P17) for the observation feed disruption_claims: manual claims are evidence for review, never relief.
-- NOT installed; landing table proposed in sql/migrations/lifecycle_draft.sql.
SELECT s.ClaimKey AS CLAIM_KEY, s.Team AS TEAM, s.ShiftDate AS SHIFT_DATE, s.ShiftNumber AS SHIFT_NUMBER, s.WorkRefsJson AS WORK_REFS,
 s.Cause AS CAUSE, s.OriginTeam AS ORIGIN_TEAM, s.ExecutingTeam AS EXECUTING_TEAM, s.EvidenceRefsJson AS EVIDENCE_REFS, s.Notes AS NOTES,
 s.Status AS STATUS, s.ReportedBy AS REPORTED_BY, s.ReportedAtUtc AS REPORTED_AT_UTC, s.ObservedAtUtc AS OBSERVED_AT_UTC,
 s.SupersedesClaimKey AS SUPERSEDES_CLAIM_KEY
FROM WEB_APP.FOCUS_FN_DisruptionClaimSource s
WHERE s.ObservedAtUtc <= %s
ORDER BY s.ClaimKey;
