-- Draft reader (P17) for the observation feed defect_evidence: explicit defect-origin facts for rework observed after
-- the planning cutoff. The origin team is a source fact, never inferred from DAG direction. NOT installed.
SELECT s.SourceEventKey AS SOURCE_EVENT_KEY, s.LineNumber AS LINE_NUMBER, s.Soi AS SOI, s.RootSoi AS ROOT_SOI,
 s.ReworkClass AS REWORK_CLASS, s.DefectOriginTeam AS DEFECT_ORIGIN_TEAM, s.EvidenceRef AS EVIDENCE_REF, s.ObservedAtUtc AS OBSERVED_AT_UTC
FROM WEB_APP.FOCUS_FN_DefectEvidenceSource s
WHERE s.ObservedAtUtc <= %s
ORDER BY s.SourceEventKey;
