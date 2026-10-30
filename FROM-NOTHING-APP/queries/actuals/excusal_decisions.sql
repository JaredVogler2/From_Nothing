-- Draft reader (P17) for the observation feed excusal_decisions: reviewed decisions and appeals (superseding decisions append).
-- NOT installed; landing table proposed in sql/migrations/008_lifecycle.sql.
SELECT s.DecisionKey AS DECISION_KEY, s.Team AS TEAM, s.ShiftDate AS SHIFT_DATE, s.ShiftNumber AS SHIFT_NUMBER, s.ClaimKeysJson AS CLAIM_KEYS,
 s.ApprovedWorkRefsJson AS APPROVED_WORK_REFS, s.Decision AS DECISION, s.Authority AS AUTHORITY, s.AuthorityRole AS AUTHORITY_ROLE,
 s.DecisionAtUtc AS DECISION_AT_UTC, s.Reason AS REASON, s.ReviewTrack AS REVIEW_TRACK, s.SupersedesDecisionKey AS SUPERSEDES_DECISION_KEY,
 s.ObservedAtUtc AS OBSERVED_AT_UTC
FROM WEB_APP.FOCUS_FN_ExcusalDecisionSource s
WHERE s.ObservedAtUtc <= %s
ORDER BY s.DecisionKey;
