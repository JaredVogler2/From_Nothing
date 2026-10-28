-- Draft reader (P17) for the observation feed goal_amendments: approved feasible emergency additions (B = B0 + additions).
-- NOT installed; landing table proposed in sql/migrations/lifecycle_draft.sql.
SELECT s.AmendmentKey AS AMENDMENT_KEY, s.Team AS TEAM, s.ShiftDate AS SHIFT_DATE, s.ShiftNumber AS SHIFT_NUMBER, s.WorkRefJson AS WORK_REF,
 s.StandardPersonMinutes AS STANDARD_PERSON_MINUTES, s.EffortBasis AS EFFORT_BASIS, s.FeasibilityRef AS FEASIBILITY_REF,
 s.Authority AS AUTHORITY, s.DecisionAtUtc AS DECISION_AT_UTC, s.Reason AS REASON, s.ObservedAtUtc AS OBSERVED_AT_UTC
FROM WEB_APP.FOCUS_FN_GoalAmendmentSource s
WHERE s.ObservedAtUtc <= %s
ORDER BY s.AmendmentKey;
