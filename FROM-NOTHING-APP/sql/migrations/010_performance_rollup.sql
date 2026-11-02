-- Migration 010 (D-25): the light per-team-shift performance rows behind every period rollup.
-- PROPOSED storage, NOT executed by this build. `python -m focus.cli migrate --apply` runs it only in an authorized
-- installer session, in one transaction under sp_getapplock('FOCUS_FN_MIGRATIONS'), and records this file's SHA-256 in
-- WEB_APP.FOCUS_FN_SchemaVersion only after every declared object is verified (docs/execution/INSTALL_AND_PIPELINE.md).
--
-- One row per (ScopeKey, Team, ShiftId) carries the current revision of that team-shift's canonical evaluation
-- (focus.performance.rollup):
--   * The six components (B0, B, E, Cp, U, O) are exact rationals in ComponentsPayload. The decimal columns are display
--     projections only. SQL filters rows by production day and group; the application adds the exact components and
--     applies the one formula. Nothing computes a score in SQL, as 009 requires.
--   * The superintendent is the one the team reported to at the shift start (the org chart admitted with the source run,
--     from FOCUS_PERSON_TEAM_MAP). It is fixed when the row is first written; a correction changes the numbers, never the
--     attribution.
--   * EvaluationId names the revision the row carries. FOCUS_FN_Evaluation keeps every revision.
-- A team, a superintendent or the business unit over any period reads only the rows of its production days. A row is
-- about 200 bytes: about 34,000 rows a year for 31 teams on three shifts.
-- The evaluator writes the row in the same unit of work as each evaluation revision.
--
-- The jobs behind each score stay too, compactly (FOCUS_FN_JobOutcome, kept for good):
--   * one row per planned unit and per unplanned completion of a team-shift's evaluation, append-only and keyed by that
--     evaluation, so a correction adds rows and the current ones are those FOCUS_FN_TeamShiftPerformance names;
--   * the job's shop order id shared across aircraft (Soi), its aircraft, phase, planned minutes and outcome (done, missed,
--     excused with the claim causes, open, unplanned, or no_plan for work on a team-shift no plan covered, which its neutral
--     score leaves out) and whether it was done out of sequence;
--   * the job descriptions once per shop order id (FOCUS_FN_JobName).
-- It answers which jobs made a score and which jobs miss aircraft after aircraft. At 50 aircraft that is about 107 rows
-- per team-shift, about 3.6 million a year at roughly 50 bytes each. The bulky per-unit evidence
-- (FOCUS_FN_EvaluationEvidence) goes after the owner's 90 days, run by the retention role.
GO

CREATE TABLE WEB_APP.FOCUS_FN_TeamShiftPerformance(
    ScopeKey nvarchar(128) NOT NULL,
    Team nvarchar(80) NOT NULL,
    ShiftId varchar(48) NOT NULL,
    ProductionDay date NOT NULL,
    ShiftNumber tinyint NOT NULL CONSTRAINT CK_FN_TeamShiftPerformance_Shift CHECK (ShiftNumber BETWEEN 1 AND 3),
    WindowStartUtc datetime2(6) NOT NULL,
    WindowEndUtc datetime2(6) NOT NULL,
    Superintendent nvarchar(50) NULL,
    SuperintendentNote nvarchar(400) NULL,
    BusinessUnit nvarchar(128) NOT NULL,
    Status varchar(24) NOT NULL
        CONSTRAINT CK_FN_TeamShiftPerformance_Status CHECK (Status IN ('official', 'demonstration', 'neutral', 'unknown')),
    EvaluationId uniqueidentifier NOT NULL
        CONSTRAINT FK_FN_TeamShiftPerformance_Evaluation REFERENCES WEB_APP.FOCUS_FN_Evaluation(EvaluationId),
    Revision int NOT NULL CONSTRAINT CK_FN_TeamShiftPerformance_Revision CHECK (Revision >= 1),
    PolicySha char(64) NOT NULL,
    PlannedMinutesDisplay decimal(19,4) NULL,
    EarnedMinutesDisplay decimal(19,4) NULL,
    ComponentsPayload varbinary(max) NOT NULL,
    EvaluatedAtUtc datetime2(6) NOT NULL,
    Version bigint NOT NULL CONSTRAINT CK_FN_TeamShiftPerformance_Version CHECK (Version >= 1),
    CONSTRAINT PK_FN_TeamShiftPerformance PRIMARY KEY (ScopeKey, Team, ShiftId),
    CONSTRAINT CK_FN_TeamShiftPerformance_Window CHECK (WindowStartUtc < WindowEndUtc));
CREATE INDEX IX_FN_TeamShiftPerformance_Day ON WEB_APP.FOCUS_FN_TeamShiftPerformance (ScopeKey, ProductionDay)
    INCLUDE (Team, Superintendent, Status);
CREATE INDEX IX_FN_TeamShiftPerformance_Superintendent ON WEB_APP.FOCUS_FN_TeamShiftPerformance (ScopeKey, Superintendent, ProductionDay);
GO

CREATE TABLE WEB_APP.FOCUS_FN_JobOutcome(
    ScopeKey nvarchar(128) NOT NULL,
    EvaluationId uniqueidentifier NOT NULL
        CONSTRAINT FK_FN_JobOutcome_Evaluation REFERENCES WEB_APP.FOCUS_FN_Evaluation(EvaluationId),
    Seq int NOT NULL CONSTRAINT CK_FN_JobOutcome_Seq CHECK (Seq >= 0),
    Team nvarchar(80) NOT NULL,
    ShiftId varchar(48) NOT NULL,
    ProductionDay date NOT NULL,
    Superintendent nvarchar(50) NULL,
    Soi varchar(30) NULL,
    JobKey varchar(64) NULL,
    LineNumber int NULL,
    PhaseKind varchar(16) NULL,
    PhaseOrdinal int NULL,
    Minutes int NULL,
    Outcome varchar(12) NOT NULL
        CONSTRAINT CK_FN_JobOutcome_Outcome CHECK (Outcome IN ('done', 'missed', 'excused', 'open', 'unplanned', 'no_plan')),
    OutOfSequence bit NOT NULL,
    Causes varchar(256) NULL,
    Origin varchar(8) NULL CONSTRAINT CK_FN_JobOutcome_Origin CHECK (Origin IS NULL OR Origin IN ('planned', 'added')),
    CONSTRAINT PK_FN_JobOutcome PRIMARY KEY (ScopeKey, EvaluationId, Seq),
    CONSTRAINT CK_FN_JobOutcome_Identity CHECK (Soi IS NOT NULL OR JobKey IS NOT NULL));
CREATE INDEX IX_FN_JobOutcome_Day ON WEB_APP.FOCUS_FN_JobOutcome (ScopeKey, ProductionDay)
    INCLUDE (Team, Superintendent, Soi, LineNumber, Outcome, Minutes);
CREATE INDEX IX_FN_JobOutcome_Soi ON WEB_APP.FOCUS_FN_JobOutcome (ScopeKey, Soi, ProductionDay)
    INCLUDE (LineNumber, PhaseKind, Outcome, Minutes, Causes);
CREATE TABLE WEB_APP.FOCUS_FN_JobName(
    ScopeKey nvarchar(128) NOT NULL,
    Soi varchar(30) NOT NULL,
    Description nvarchar(500) NOT NULL,
    UpdatedAtUtc datetime2(6) NOT NULL,
    CONSTRAINT PK_FN_JobName PRIMARY KEY (ScopeKey, Soi));
GO
