-- P12 read-only mirror of focus/economics/repository.py: one effective economics term per mapped aircraft.
-- The application owns the final labels and money; this query is the reviewable live readiness check that
-- compares source rows with the repository's resolution. It never reads a cap into the term.
--
-- Bound parameters (pymssql positional placeholders, in order): CutoffUtc six times (datetime2, UTC, the source-run cutoff).
--
-- Rules (S12 ECONOMICS REPOSITORY):
--  1. Exactly one current mapping revision and exactly one active scenario effective at the cutoff, else every
--     line is reported with the blocking reason and no term.
--  2. Exactly one active effective contract assignment per aircraft. Two or more is CONTRACT_ASSIGNMENT_CONFLICT
--     and no term is chosen: ambiguity is never resolved by row order.
--  3. Selector precedence AIRCRAFT_OVERRIDE > CONTRACT > CUSTOMER_MODEL (policy economics.selector_precedence),
--     then the latest TermVersion. Any duplicated TermVersion inside the winning selector is TERM_VERSION_CONFLICT.
--  4. Effectivity is half-open [EffectiveFromUtc, EffectiveToUtc). NULL ContractKey/LineNumber are matched only
--     by the CUSTOMER_MODEL selector, never by a defaulted key.
--  5. ACTUAL authority needs Provenance ACTUAL, an approval and exactly one original sale basis naming the term.
--     ASSUMED terms stay planning assumptions. Anything else is TERM_AUTHORITY_UNPROVEN.
--  6. The scenario CapFraction is shown as not applied (economics.cap_policy = EXPLICIT_ONLY).
WITH CurrentMapping AS (
    SELECT MappingRevisionId, COUNT_BIG(*) OVER () AS CurrentCount
    FROM WEB_APP.FOCUS_SOL_TESTING2_MappingRevision
    WHERE IsCurrent = 1
), ActiveScenario AS (
    SELECT ScenarioRevisionId, CapFraction, COUNT_BIG(*) OVER () AS ActiveCount
    FROM WEB_APP.FOCUS_SOL_TESTING2_EconomicsScenario
    WHERE IsActive = 1 AND EffectiveFromUtc <= %s AND (EffectiveToUtc IS NULL OR %s < EffectiveToUtc)
), Aircraft AS (
    SELECT a.LineNumber, a.CustomerCode, a.MinorModel, m.CurrentCount
    FROM WEB_APP.FOCUS_SOL_TESTING2_AircraftMapping a
    JOIN CurrentMapping m ON m.MappingRevisionId = a.MappingRevisionId
), Assignment AS (
    SELECT c.LineNumber, c.AssignmentId, c.ContractKey, COUNT_BIG(*) OVER (PARTITION BY c.LineNumber) AS AssignmentCount
    FROM WEB_APP.FOCUS_SOL_TESTING2_ContractAssignment c
    JOIN ActiveScenario s ON s.ScenarioRevisionId = c.ScenarioRevisionId AND s.ActiveCount = 1
    WHERE c.IsActive = 1 AND c.EffectiveFromUtc <= %s AND (c.EffectiveToUtc IS NULL OR %s < c.EffectiveToUtc)
), Candidate AS (
    SELECT a.LineNumber, t.TermId, t.SelectorType, t.TermVersion, t.DailyRate, t.Currency, t.Provenance, t.ApprovedBy,
           CASE t.SelectorType WHEN 'AIRCRAFT_OVERRIDE' THEN 1 WHEN 'CONTRACT' THEN 2 WHEN 'CUSTOMER_MODEL' THEN 3 END AS Precedence
    FROM Aircraft a
    JOIN ActiveScenario s ON s.ActiveCount = 1
    JOIN WEB_APP.FOCUS_SOL_TESTING2_EconomicsTerm t ON t.ScenarioRevisionId = s.ScenarioRevisionId
    LEFT JOIN Assignment c ON c.LineNumber = a.LineNumber AND c.AssignmentCount = 1
    WHERE a.CurrentCount = 1
      AND NOT EXISTS (SELECT 1 FROM Assignment x WHERE x.LineNumber = a.LineNumber AND x.AssignmentCount > 1)
      AND t.EffectiveFromUtc <= %s AND (t.EffectiveToUtc IS NULL OR %s < t.EffectiveToUtc)
      AND ((t.SelectorType = 'AIRCRAFT_OVERRIDE' AND t.LineNumber = a.LineNumber)
        OR (t.SelectorType = 'CONTRACT' AND c.ContractKey IS NOT NULL AND t.ContractKey = c.ContractKey)
        OR (t.SelectorType = 'CUSTOMER_MODEL' AND t.LineNumber IS NULL AND t.ContractKey IS NULL
            AND t.CustomerCode = a.CustomerCode AND t.MinorModel = a.MinorModel))
), Winning AS (
    SELECT c.LineNumber, c.TermId, c.SelectorType, c.TermVersion, c.DailyRate, c.Currency, c.Provenance, c.ApprovedBy,
           COUNT_BIG(*) OVER (PARTITION BY c.LineNumber, c.TermVersion) AS SameVersion,
           ROW_NUMBER() OVER (PARTITION BY c.LineNumber ORDER BY c.TermVersion DESC, c.TermId) AS rn
    FROM Candidate c
    WHERE c.Precedence = (SELECT MIN(b.Precedence) FROM Candidate b WHERE b.LineNumber = c.LineNumber)
), Chosen AS (
    SELECT w.LineNumber, w.TermId, w.SelectorType, w.TermVersion, w.DailyRate, w.Currency, w.Provenance, w.ApprovedBy, w.rn,
           MAX(w.SameVersion) OVER (PARTITION BY w.LineNumber) AS MaxSameVersion
    FROM Winning w
), Basis AS (
    SELECT TermId, LineNumber, COUNT_BIG(*) AS BasisCount
    FROM WEB_APP.FOCUS_SOL_TESTING2_OriginalSaleBasis
    GROUP BY TermId, LineNumber
)
SELECT a.LineNumber, a.CustomerCode, a.MinorModel,
       CASE WHEN ch.MaxSameVersion > 1 THEN NULL ELSE ch.TermId END AS TermId,
       ch.SelectorType, ch.TermVersion, ch.DailyRate, ch.Currency, ch.Provenance,
       CASE WHEN ch.ApprovedBy IS NULL THEN 0 ELSE 1 END AS HasApproval,
       COALESCE(b.BasisCount, 0) AS SaleBasisCount,
       CASE WHEN a.CurrentCount <> 1 THEN 'MULTIPLE_CURRENT_MAPPING_REVISIONS'
            WHEN NOT EXISTS (SELECT 1 FROM ActiveScenario s WHERE s.ActiveCount = 1) THEN 'NO_SINGLE_ACTIVE_SCENARIO'
            WHEN EXISTS (SELECT 1 FROM Assignment x WHERE x.LineNumber = a.LineNumber AND x.AssignmentCount > 1)
                 THEN 'CONTRACT_ASSIGNMENT_CONFLICT'
            WHEN ch.TermId IS NULL THEN 'NO_EFFECTIVE_TERM'
            WHEN ch.MaxSameVersion > 1 THEN 'TERM_VERSION_CONFLICT'
            WHEN ch.Provenance = 'ASSUMED' THEN 'ASSUMED_PLANNING_TERM'
            WHEN ch.Provenance = 'ACTUAL' AND ch.ApprovedBy IS NOT NULL AND b.BasisCount = 1 THEN 'ACTUAL'
            WHEN b.BasisCount > 1 THEN 'SALE_BASIS_CONFLICT'
            ELSE 'TERM_AUTHORITY_UNPROVEN' END AS TermState,
       (SELECT MAX(s.CapFraction) FROM ActiveScenario s) AS ScenarioCapFractionNotApplied
FROM Aircraft a
LEFT JOIN Chosen ch ON ch.LineNumber = a.LineNumber AND ch.rn = 1
LEFT JOIN Basis b ON b.TermId = ch.TermId AND b.LineNumber = a.LineNumber
ORDER BY a.LineNumber;
