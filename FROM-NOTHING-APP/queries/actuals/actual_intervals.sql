-- Draft reader (P17) for the observation feed actual_intervals: raw owner clock intervals (union-method evidence,
-- never completion credit). NOT installed; landing table proposed in sql/migrations/lifecycle_draft.sql.
SELECT s.SourceEventKey AS SOURCE_EVENT_KEY, s.SourceRevision AS SOURCE_REVISION, s.LineNumber AS LINE_NUMBER, s.Soi AS SOI,
 s.PhaseKind AS PHASE_KIND, s.PhaseOrdinal AS PHASE_ORDINAL, s.PersonKey AS PERSON_KEY, s.StartUtc AS START_UTC, s.EndUtc AS END_UTC,
 s.IsVoided AS IS_VOIDED, s.ObservedAtUtc AS OBSERVED_AT_UTC, s.EvidenceRef AS EVIDENCE_REF
FROM WEB_APP.FOCUS_FN_ActualIntervalSource s
WHERE s.ObservedAtUtc <= %s
ORDER BY s.SourceEventKey, s.SourceRevision;
