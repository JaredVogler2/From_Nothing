# Validation coverage (S13, P15)

The independent validator is `focus/validation/plan.py::validate`, version
`focus-independent-validator-1.0`. It reads two inputs:

- the sealed source snapshot, through `focus/validation/input.py::ValidationInput`;
- the final candidate.

It never reads what a candidate declares about itself: certificate evidence ids, estimate ids,
release ids, caps, or conditional and committed flags. It never imports the optimizer
(`focus.engine`). The only shared rule authority is the calendar geometry
(`focus.calendar.build_shift`).

**Receipt.** Each run produces a `ValidationReceipt` carrying:

- `check_counts` per check id;
- `coverage` per check: items, status `CHECKED | VACUOUS | SKIPPED`, required, and reason;
- `required_skips`;
- violations sorted by (check, code, entity, detail);
- plan, snapshot, policy and payload hashes.

**Valid** means zero violations **and** zero required skips. Zero violations with a skipped
required check is invalid, so a "validator 0" verdict without coverage is impossible.

## Checks

| Id | Reconstructed from source | Codes | Mutation test (`tests/integration/test_validator_mutations.py`) |
| --- | --- | --- | --- |
| V1 duration/identity | Snapshot phases: kind, work, status, parent elapsed. Job estimates and in-progress remainder. Segment geometry. | `UNKNOWN_PHASE`, `IDENTITY_MISMATCH`, `RELEASE_HAS_CREW`, `COMPLETE_PHASE_REPLANNED`, `SEGMENT_INVALID`, `SEGMENT_ENVELOPE_MISMATCH`, `WORK_NOT_CONSERVED`, `WORK_DECLARATION_MISMATCH`, `ESTIMATE_MISMATCH`, `DUPLICATE_PHASE_ASSIGNMENT`, `PARENT_NOT_CONSERVED` | `V1-work`, `V1-restart-fiction` (in-progress), `V1-unknown-phase`, `V1-identity` |
| V2 precedence | Every hard FS/SS/FF/SF edge with signed lag. Verified-complete anchors. Purpose release floors (confirmed receipt; forecast ETA for FORECAST only). AND releases derived in topological order. Cycles. Hard concurrency edges (OD-07 fixture meaning). | `PRECEDENCE_VIOLATION`, `PREDECESSOR_UNRESOLVED`, `UNKNOWN_ENDPOINT`, `PRECEDENCE_CYCLE`, `CONCURRENCY_VIOLATION` | `V2-in-progress-before-predecessor`, `V2-omitted-predecessor`, `V2-and-release` |
| V3 workday | The line's own admitted, bounded `FOCUS_CDAY_OP_PLAN` generation and the S3 exception, re-derived independently. | `SHIFT_ID_INVALID`, `CALENDAR_NOT_ADMITTED`, `OUTSIDE_CALENDAR_BOUNDS`, `CALENDAR_DATE_UNKNOWN`, `NONWORKING_DAY`, `S3_NONWORKING` | `V3-weekend`, `V3-s3-rule`, `V3-bounds` |
| V4 shift fit | Physical regular/OT windows, breaks and the no-start coordinate (equality allowed). The cutoff, the job earliest, and same-run CS earliest/hard latest. Maximum OT. Per-person OT authorization. Truthful duty regular/OT minutes. | `SHIFT_UNKNOWN`, `SEGMENT_OUTSIDE_SHIFT_WINDOWS`, `START_NOT_IN_REGULAR_WINDOW`, `NO_START_CUTOFF`, `BEFORE_CUTOFF`, `BEFORE_JOB_EARLIEST`, `BEFORE_CS_EARLIEST`, `AFTER_CS_HARD_LATEST`, `OT_EXCEEDS_MAXIMUM`, `OT_NOT_AUTHORIZED`, `OT_MISREPORTED` | `V4-break`, `V4-no-start` (and the equality boundary test), `V4-backdate`, `V4-ot-misreported` |
| V5 full crew | Sourced role demands and requirement status. The role's own team/shift pure pool. Admitted purpose membership (synthetic never executable) and availability. Whole-interval certificate bundles from raw holdings. | `REQUIREMENTS_UNKNOWN`, `ROLE_DEMAND_MISSING`, `CREW_DUPLICATE_PERSON`, `CREW_DEMAND_UNAVAILABLE`, `CREW_SIZE_MISMATCH`, `ROLE_MISMATCH`, `POOL_MISSING`, `POOL_KEY_MISMATCH`, `SYNTHETIC_IN_EXECUTABLE`, `NOT_POOL_MEMBER`, `NO_AVAILABILITY`, `CANDIDATE_ONLY_ROSTER`, `NOT_QUALIFIED`, `CERT_EVIDENCE_FORGED` | `V5-short`, `V5-duplicate`, `V5-unqualified-forged-evidence`, `V5-loan`, `V5-synthetic`, `V5-role`, `V5-roster`, `V5-pool-key`, `V5-evidence`, `V5-qa-pool` |
| V6 non-overlap | Person intervals across shift labels, including actual carry-in, work, travel and wait (`focus/validation/duty.py`). | `PERSON_OVERLAP` | `V6-carry-in`, `V6-overlap` |
| V7 load | Re-derived both-neighbour transfers, and the complete duty sum inside admitted regular and authorized OT windows against the caps. | `TRANSFER_MISSING`, `TRAVEL_ORIGIN_UNKNOWN`, `NO_ADMITTED_WINDOW`, `OUTSIDE_ADMITTED_WINDOW`, `REGULAR_CAP`, `OT_CAP` | `V7-transfer` |
| V8 activation | Distinct activated people per pool against `floor(85N/100)`. N is the distinct admitted denominator, recomputed with no minimum-one, duplicate or synthetic boost. | `ACTIVATION_CAP` | `V8-cap`, plus `test_named_crew::test_a38` |
| V9 parts | Every own and ancestor part release. EXECUTABLE needs a confirmed receipt. FORECAST needs the precision floor and the conditional label. Unknown, revoked or reopened material and blocking shortages block. | `PART_NOT_RECEIVED`, `BEFORE_PART_RELEASE`, `BEFORE_PART_FLOOR`, `PART_CONDITIONAL_UNLABELLED`, `PART_RELEASE_BLOCKED`, `PART_SHORTAGE_BLOCKED` | `V9-part-executable`, `test_v9_forecast_part_floor_and_label` |
| E_INVENTORY | Exactly one disposition per snapshot job. Source dispositions kept. Scheduled jobs complete and released. Every deferral has a cause. | `INVENTORY_MISSING`, `INVENTORY_DUPLICATE`, `INVENTORY_UNKNOWN_JOB`, `DISPOSITION_CONFLICT`, `SCHEDULED_NOT_ADMITTED`, `SCHEDULED_INCOMPLETE`, `SCHEDULED_UNRELEASED`, `CONDITIONAL_LABEL_MISMATCH`, `DEFERRED_WITHOUT_REASON` | `E-inventory-missing`, `E-inventory-blocked` |
| E_LINEAGE | Snapshot integrity (hash). Bindings: snapshot, source run, scope, policy, purpose. Plan hash. Truthful proof status. | `SNAPSHOT_HASH_MISMATCH` (all other checks are then skipped, so invalid), `SNAPSHOT_MISMATCH`, `SOURCE_RUN_MISMATCH`, `SCOPE_MISMATCH`, `POLICY_MISMATCH`, `PURPOSE_MISMATCH`, `PLAN_HASH_MISMATCH`, `UNTRUTHFUL_PROOF` | `E-lineage-hash`, `-snapshot`, `-policy`, `-proof` |
| E_CS | Each placed aircraft's station fact against the active same-run raw `FOCUS_CS_WINDOWS` row: run, load id, raw offsets and refs. | `CS_FACT_MISSING`, `CS_FACT_NOT_ADMITTED`, `CS_FACT_FOREIGN_RUN`, `CS_RAW_ROW_MISSING`, `CS_RAW_FIELD_MISMATCH` | `test_e_cs_forged_source_station_offset_is_caught_against_raw_rows` |
| E_COMMITMENT | Each accepted incumbent task's reported outcome against the placed slot and crew. Day offset. Horizon. The incumbent is loaded from the accepted artifact. | `COMMITMENT_RESULT_MISSING`, `COMMITMENT_OFFSET_MISMATCH`, `COMMITMENT_MISREPORTED`, `COMMITMENT_OUTCOME_UNKNOWN`, `COMMITMENT_WITHOUT_INCUMBENT`, `INCUMBENT_MISMATCH`; skipped (so invalid) when the incumbent is not supplied | `E-commitment-misreported`, `E-commitment-missing`, `test_zero_violations_with_a_skipped_required_check_is_invalid` |
| E_ECONOMICS | Exact recomputation of complete-scope aircraft completions (from derived release times) and costs, through the economics authority. | `COMPLETION_MISMATCH`, `ECONOMICS_MISMATCH` | `E-economics-cost`, `E-economics-completion` |
| E_CAPABILITY | Placeability for the purpose. EXECUTABLE plans carry no conditional evidence; FORECAST plans label it. | `NOT_PLACEABLE_FOR_PURPOSE`, `CONDITIONAL_IN_EXECUTABLE`, `CONDITIONAL_UNLABELLED`, `CONDITIONAL_MISLABELLED` | `E-capability-conditional`, `test_a64_virtual_support_is_forecast_only_by_origin_evidence` |
| E_ARTIFACT (publication bytes) | Strict canonical decode. Scope, purpose and capability. Source run, snapshot and manifest hashes. Policy and timezone. The full hard-edge graph. Same-run CS lineage. Obligation dates. Frozen goals and their hashes. (`focus/validation/publication.py`) | `ARTIFACT_UNDECODABLE`, `NON_CANONICAL`, `ARTIFACT_TYPE`, `SCOPE_MISMATCH`, `PURPOSE_MISMATCH`, `CAPABILITY_MISMATCH`, `SOURCE_RUN_MISMATCH`, `SOURCE_HASH_MISMATCH`, `MANIFEST_MISMATCH`, `POLICY_MISMATCH`, `TIMEZONE_MISMATCH`, `GRAPH_LINEAGE_MISMATCH`, `CS_LINEAGE_MISMATCH`, `OBLIGATION_DATES_MISMATCH`, `GOALS_MISMATCH`, `GOAL_HASH_MISMATCH` | `test_a48_detached_bytes_with_forged_fields_are_rejected` (crew, root edge, timezone, station/load, offset, source hash) |

**Not applicable here.** Tool overlap: no tool-requirement source is mapped (S04). Tool
calendars are enforced in the ledger when tool demand is present.

## Paths that run the validator

| Path | Entry | Gate |
| --- | --- | --- |
| Neutral seed | `MaxOptimizer.run` → `_valid` | `GATE_CHECKS` (all but `E_COMMITMENT`, whose incumbent is checked on the final plan) |
| Economic redispatch, delay repair, neighbourhoods (swap, boost, aircraft advance, continuity focus) | `MaxOptimizer._admit` | Validity comes first (S12 level 0), then the exact key. An invalid candidate is rolled back and logged in `MaxRun.validator_rejections`; any valid candidate beats an invalid best. Proven by `test_every_search_path_rolls_back_an_invalid_candidate`. |
| Optional solver (P22) | Same `_admit` gate | Same as above |
| Final candidate | `schedule` → `validate(kind=FINAL)`; receipt persisted in `validations` | Full `REQUIRED_CHECKS`, with the incumbent from `accepted_plans` |
| CLI | `python -m focus.cli validate --candidate <id> --all-required`, `tools/validate_plan.py` | Full; exit 0 only when valid |
| Detached serialization | `validate_artifact(kind=DETACHED)` (P16) | Full plus `E_ARTIFACT` |
| Stage readback (pre-sign) | `validate_artifact(kind=READBACK)` on the exact staged bytes (P16) | Full plus `E_ARTIFACT` |
| Current dispatch | `focus/publication/dispatch.py` (P16) | Current-fact recheck of the action |

`publication_capability(plan, receipt)` lists every reason a plan cannot be staged, signed or
accepted: purpose, missing, foreign or invalid receipt, and required skips. A corrupted plan
always has at least one reason (asserted for every corpus entry).

## Fixture versus live

Every check runs on the sealed fixture snapshot. Live validation needs the live feeds behind
the snapshot (P03–P10 readiness gaps). The validator itself has no live dependency and runs
without SQL.
