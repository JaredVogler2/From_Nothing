-- Migration 001: FOCUS_FN core output storage (manual S06 MIGRATION 001, reproduced verbatim).
-- PROPOSED storage. NOT executed by this build. Apply only through `python -m focus.cli migrate --apply`
-- with an authorized installer principal after `migrate --dry-run` shows no collisions (S06, P23).
-- The runner takes sp_getapplock('FOCUS_FN_MIGRATIONS'), validates collisions/schema, executes this
-- batch, verifies every expected object/constraint and only then records the checksum in
-- WEB_APP.FOCUS_FN_SchemaVersion. Reruns validate the stored checksum instead of re-creating tables.
SET XACT_ABORT ON;
BEGIN TRANSACTION;
CREATE TABLE WEB_APP.FOCUS_FN_SchemaVersion(
 Version int NOT NULL PRIMARY KEY, ScriptSha char(64) NOT NULL,
 InstalledAtUtc datetime2(6) NOT NULL, InstallerSubject nvarchar(128) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_Policy(
 PolicyId uniqueidentifier NOT NULL PRIMARY KEY,
 ScopeKey nvarchar(128) NOT NULL, Purpose varchar(24) NOT NULL,
 Revision int NOT NULL, Payload varbinary(max) NOT NULL,
 ContentSha char(64) NOT NULL, EffectiveFromUtc datetime2(6) NOT NULL,
 EffectiveToUtc datetime2(6) NULL, AuthoritySubject nvarchar(128) NULL,
 AuthorityEvidence varbinary(max) NULL,
 CONSTRAINT UX_FN_PolicyRevision UNIQUE(ScopeKey,Purpose,Revision));
CREATE TABLE WEB_APP.FOCUS_FN_SourceRun(
 RunId uniqueidentifier NOT NULL PRIMARY KEY,
 ScopeKey nvarchar(128) NOT NULL, IdempotencyKey varchar(128) NOT NULL,
 Status varchar(16) NOT NULL CHECK(Status IN('CAPTURING','COMPLETE','REJECTED')),
 CutoffUtc datetime2(6) NOT NULL, CaptureStartUtc datetime2(6) NOT NULL,
 CaptureEndUtc datetime2(6) NULL, ManifestSha char(64) NULL,
 ManifestPayload varbinary(max) NULL,
 CONSTRAINT UX_FN_SourceIdempotency UNIQUE(ScopeKey,IdempotencyKey));
CREATE TABLE WEB_APP.FOCUS_FN_SourceDataset(
 DatasetId uniqueidentifier NOT NULL PRIMARY KEY,
 RunId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceRun(RunId),
 Name varchar(64) NOT NULL, SchemaSha char(64) NOT NULL,
 QuerySha char(64) NOT NULL, ContentSha char(64) NOT NULL,
 RowCount bigint NOT NULL CHECK(RowCount>=0), CompleteReceipt varbinary(max) NOT NULL,
 CapturedAtUtc datetime2(6) NOT NULL, Payload varbinary(max) NOT NULL,
 CONSTRAINT UX_FN_Dataset UNIQUE(RunId,Name));
CREATE TABLE WEB_APP.FOCUS_FN_SourceHead(
 ScopeKey nvarchar(128) NOT NULL PRIMARY KEY,
 RunId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceRun(RunId),
 Version bigint NOT NULL, UpdatedAtUtc datetime2(6) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_SourceRow(
 DatasetId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceDataset(DatasetId),
 Ordinal bigint NOT NULL, EntityKind varchar(32) NOT NULL,
 LineNumber int NULL, NativeKeySha char(64) NOT NULL,
 Payload varbinary(max) NOT NULL, ContentSha char(64) NOT NULL,
 PRIMARY KEY(DatasetId,Ordinal));
CREATE INDEX IX_FN_SourceEntity ON WEB_APP.FOCUS_FN_SourceRow
 (DatasetId,EntityKind,LineNumber,NativeKeySha);

CREATE TABLE WEB_APP.FOCUS_FN_ClockObservation(
 ObservationId uniqueidentifier NOT NULL PRIMARY KEY,
 RunId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceRun(RunId),
 SourceEventKey varchar(256) NOT NULL, JobKeySha char(64) NOT NULL,
 LineNumber int NOT NULL, NativeSoi nvarchar(256) NOT NULL,
 PersonKey nvarchar(256) NOT NULL, EpisodeKey nvarchar(256) NOT NULL,
 Team nvarchar(80) NULL, ReworkClass varchar(8) NULL,
 StartUtc datetime2(6) NOT NULL, EndUtc datetime2(6) NOT NULL,
 SourceRevision varchar(128) NOT NULL, IsVoided bit NOT NULL,
 EvidencePayload varbinary(max) NOT NULL,
 CHECK(EndUtc>StartUtc),
 CONSTRAINT UX_FN_ClockEvent UNIQUE(RunId,SourceEventKey,SourceRevision));
CREATE TABLE WEB_APP.FOCUS_FN_TeamInference(
 RunId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceRun(RunId),
 PersonKey nvarchar(256) NOT NULL, InferredTeam nvarchar(80) NULL,
 WindowStartUtc datetime2(6) NOT NULL, WindowEndUtc datetime2(6) NOT NULL,
 EvidenceCount int NOT NULL CHECK(EvidenceCount>=0),
 EffortMinutes decimal(19,6) NOT NULL CHECK(EffortMinutes>=0),
 TopShare decimal(9,8) NULL, IsTie bit NOT NULL, Basis varchar(24) NOT NULL,
 AlgorithmVersion varchar(64) NOT NULL, EvidencePayload varbinary(max) NOT NULL,
 PRIMARY KEY(RunId,PersonKey));
CREATE TABLE WEB_APP.FOCUS_FN_DurationObservation(
 ObservationId uniqueidentifier NOT NULL PRIMARY KEY,
 RunId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceRun(RunId),
 EpisodeKeySha char(64) NOT NULL, GroupKeySha char(64) NOT NULL,
 Class varchar(8) NOT NULL, Team nvarchar(80) NULL,
 ActiveElapsedMinutes decimal(19,6) NOT NULL CHECK(ActiveElapsedMinutes>0),
 PersonEffortMinutes decimal(19,6) NULL CHECK(PersonEffortMinutes>=0),
 CycleMinutes decimal(19,6) NOT NULL CHECK(CycleMinutes>0),
 CompletedAtUtc datetime2(6) NOT NULL, Admitted bit NOT NULL,
 EvidencePayload varbinary(max) NOT NULL,
 CONSTRAINT UX_FN_DurationEpisode UNIQUE(RunId,EpisodeKeySha));
CREATE TABLE WEB_APP.FOCUS_FN_DurationStatistics(
 StatisticsId uniqueidentifier NOT NULL PRIMARY KEY,
 RunId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceRun(RunId),
 GroupKeySha char(64) NOT NULL, MeasurementBasis varchar(32) NOT NULL,
 RawCount int NOT NULL, AdmittedCount int NOT NULL, ExcludedCount int NOT NULL,
 MeanMinutes decimal(19,6) NOT NULL, P50Minutes decimal(19,6) NOT NULL,
 P55Minutes decimal(19,6) NOT NULL, P75Minutes decimal(19,6) NOT NULL,
 P90Minutes decimal(19,6) NOT NULL,
 SampleStartUtc datetime2(6) NOT NULL, SampleEndUtc datetime2(6) NOT NULL,
 MethodSha char(64) NOT NULL, PolicyId uniqueidentifier NOT NULL,
 EvidencePayload varbinary(max) NOT NULL,
 CHECK(P50Minutes<=P55Minutes AND P55Minutes<=P75Minutes AND P75Minutes<=P90Minutes),
 CHECK(RawCount=AdmittedCount+ExcludedCount AND AdmittedCount>0));

CREATE TABLE WEB_APP.FOCUS_FN_PlanStage(
 StageId uniqueidentifier NOT NULL PRIMARY KEY,
 RunId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_SourceRun(RunId),
 ScopeKey nvarchar(128) NOT NULL, Purpose varchar(24) NOT NULL,
 PolicySha char(64) NOT NULL, Payload varbinary(max) NOT NULL,
 ContentSha char(64) NOT NULL, IdempotencyKey varchar(128) NOT NULL,
 CreatedAtUtc datetime2(6) NOT NULL,
 CONSTRAINT UX_FN_Stage UNIQUE(ScopeKey,IdempotencyKey));
CREATE TABLE WEB_APP.FOCUS_FN_Validation(
 ValidationId uniqueidentifier NOT NULL PRIMARY KEY,
 StageId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_PlanStage(StageId),
 Kind varchar(24) NOT NULL, SourceSha char(64) NOT NULL,
 PolicySha char(64) NOT NULL, ContentSha char(64) NOT NULL,
 ValidatorVersion varchar(64) NOT NULL, Violations int NOT NULL,
 RequiredSkips int NOT NULL, ReceiptPayload varbinary(max) NOT NULL,
 CreatedAtUtc datetime2(6) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_PublishedPlan(
 PlanId uniqueidentifier NOT NULL PRIMARY KEY,
 StageId uniqueidentifier NOT NULL UNIQUE REFERENCES WEB_APP.FOCUS_FN_PlanStage(StageId),
 ReadbackValidationId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_Validation(ValidationId),
 ScopeKey nvarchar(128) NOT NULL, Purpose varchar(24) NOT NULL,
 Payload varbinary(max) NOT NULL, ContentSha char(64) NOT NULL,
 KeyId varchar(64) NOT NULL, Signature varbinary(512) NOT NULL,
 AcceptanceReceipt varbinary(max) NOT NULL, AcceptedAtUtc datetime2(6) NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_PlanHead(
 ScopeKey nvarchar(128) NOT NULL, Purpose varchar(24) NOT NULL,
 PlanId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_PublishedPlan(PlanId),
 Version bigint NOT NULL, UpdatedAtUtc datetime2(6) NOT NULL,
 PRIMARY KEY(ScopeKey,Purpose));
CREATE TABLE WEB_APP.FOCUS_FN_FrozenGoal(
 GoalId uniqueidentifier NOT NULL PRIMARY KEY,
 PlanId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_PublishedPlan(PlanId),
 Team nvarchar(80) NOT NULL, ShiftId varchar(128) NOT NULL,
 OriginalPersonMinutes decimal(19,6) NOT NULL CHECK(OriginalPersonMinutes>=0),
 MembershipSha char(64) NOT NULL, Payload varbinary(max) NOT NULL,
 CONSTRAINT UX_FN_Goal UNIQUE(PlanId,Team,ShiftId));
CREATE TABLE WEB_APP.FOCUS_FN_Commitment(
 ScopeKey nvarchar(128) NOT NULL, LineNumber int NOT NULL,
 CurrentDate date NOT NULL, OriginalDate date NOT NULL,
 PendingDate date NULL, ConfirmationCount int NOT NULL,
 LastAcceptedPlanId uniqueidentifier NULL, Version bigint NOT NULL,
 Payload varbinary(max) NOT NULL, PRIMARY KEY(ScopeKey,LineNumber));
CREATE TABLE WEB_APP.FOCUS_FN_Event(
 EventId uniqueidentifier NOT NULL PRIMARY KEY,
 ScopeKey nvarchar(128) NOT NULL, DedupeKey varchar(256) NOT NULL,
 EventType varchar(48) NOT NULL, EffectiveAtUtc datetime2(6) NOT NULL,
 RecordedAtUtc datetime2(6) NOT NULL, ObservedAtUtc datetime2(6) NOT NULL,
 SourceRevision varchar(128) NOT NULL, Payload varbinary(max) NOT NULL,
 ContentSha char(64) NOT NULL, Supersedes uniqueidentifier NULL,
 CONSTRAINT UX_FN_EventDedupe UNIQUE(ScopeKey,DedupeKey));
CREATE TABLE WEB_APP.FOCUS_FN_ExcusalDecision(
 DecisionId uniqueidentifier NOT NULL PRIMARY KEY,
 GoalId uniqueidentifier NOT NULL REFERENCES WEB_APP.FOCUS_FN_FrozenGoal(GoalId),
 Decision varchar(16) NOT NULL CHECK(Decision IN('APPROVED','REJECTED','PENDING')),
 AuthoritySubject nvarchar(128) NOT NULL, DecisionAtUtc datetime2(6) NOT NULL,
 Payload varbinary(max) NOT NULL, Supersedes uniqueidentifier NULL);
CREATE TABLE WEB_APP.FOCUS_FN_Evaluation(
 EvaluationId uniqueidentifier NOT NULL PRIMARY KEY,
 ScopeKey nvarchar(128) NOT NULL, CutoffUtc datetime2(6) NOT NULL,
 PolicySha char(64) NOT NULL, GoalSetSha char(64) NOT NULL,
 EvidenceSha char(64) NOT NULL, Status varchar(24) NOT NULL,
 Score decimal(5,1) NULL, Grade varchar(8) NULL,
 Payload varbinary(max) NOT NULL, Supersedes uniqueidentifier NULL,
 CONSTRAINT UX_FN_Eval UNIQUE(ScopeKey,CutoffUtc,PolicySha,GoalSetSha,EvidenceSha));
CREATE TABLE WEB_APP.FOCUS_FN_WorkerRun(
 RunId uniqueidentifier NOT NULL PRIMARY KEY, Kind varchar(32) NOT NULL,
 ScopeKey nvarchar(128) NOT NULL, IdempotencyKey varchar(128) NOT NULL,
 Status varchar(16) NOT NULL, LeaseOwner varchar(128) NULL,
 LeaseUntilUtc datetime2(6) NULL, Version bigint NOT NULL,
 RequestPayload varbinary(max) NOT NULL, ResultReceipt varbinary(max) NULL,
 CONSTRAINT UX_FN_WorkerDedupe UNIQUE(Kind,ScopeKey,IdempotencyKey));
COMMIT TRANSACTION;
