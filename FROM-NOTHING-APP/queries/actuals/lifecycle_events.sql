-- Draft reader (P17) for the observation feed lifecycle_events (focus/performance/actuals.py FEEDS). NOT installed:
-- the landing table is proposed in sql/migrations/lifecycle_draft.sql. Read-only, explicit projection; the one
-- parameter is the collection as-of instant (rows become visible once observed).
SELECT s.SourceEventKey AS SOURCE_EVENT_KEY, s.SourceSequence AS SOURCE_SEQUENCE, s.SourceRevision AS SOURCE_REVISION,
 s.LineNumber AS LINE_NUMBER, s.Soi AS SOI, s.PhaseKind AS PHASE_KIND, s.PhaseOrdinal AS PHASE_ORDINAL, s.SegmentsJson AS SEGMENTS,
 s.Kind AS KIND, s.PersonKey AS PERSON_KEY, s.EffectiveAtUtc AS EFFECTIVE_AT_UTC, s.RecordedAtUtc AS RECORDED_AT_UTC,
 s.ObservedAtUtc AS OBSERVED_AT_UTC, s.Verified AS VERIFIED, s.VerifiedBy AS VERIFIED_BY, s.DirectiveRef AS DIRECTIVE_REF,
 s.SupersedesEventKey AS SUPERSEDES_EVENT_KEY, s.EvidenceRef AS EVIDENCE_REF, s.Detail AS DETAIL
FROM WEB_APP.FOCUS_FN_LifecycleSource s
WHERE s.ObservedAtUtc <= %s
ORDER BY s.SourceEventKey, s.SourceRevision;
