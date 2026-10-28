"""Publication-path validation: detached and readback artifacts and publication capability (S13, P15/P16).

``validate_artifact(data, snapshot, policy, ...)`` is the independent check run twice in the
acceptance order:

1. on the detached canonical bytes;
2. on the exact bytes read back from the private stage.

It decodes the bytes strictly (canonical only) and re-derives every declared section from the
sealed source snapshot (check ``E_ARTIFACT``):

* scope, purpose, capability, source run, snapshot and manifest hashes, policy and timezone;
* the full hard-edge graph, so a changed root or rework edge is caught;
* the same-run CS lineage: station, raw row, load and offsets;
* today's frozen goals.

It then runs the full nine-check validator on the embedded candidate. A changed crew, edge,
timezone, station, load, offset or source hash in the bytes therefore fails before anything is
signed (A48).

``publication_capability`` lists every reason a plan may not be staged, signed or accepted.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..contracts.planning import CandidatePlan, IncumbentPlan, PlanArtifact, PlanningSnapshot, ValidationReceipt, Violation
from ..domain import Purpose, digest_id
from ..policy import Policy
from ..publication.serializer import (ARTIFACT_SCHEMA, ARTIFACT_TYPE, CAPABILITY, ArtifactError, cs_lineage, decode_artifact, freeze_goals,
                                      graph_lineage, obligation_dates, payload_sha256)
from .input import VALIDATOR_VERSION
from .plan import REQUIRED_CHECKS, validate

ARTIFACT_CHECKS = REQUIRED_CHECKS + ("E_ARTIFACT",)
PUBLISHABLE = (Purpose.EXECUTABLE, Purpose.FORECAST)


def _artifact_violations(artifact: PlanArtifact, snapshot: PlanningSnapshot, policy: Policy) -> tuple[list[Violation], int]:
    out: list[Violation] = []
    checks = 0

    def need(ok: bool, code: str, detail: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            out.append(Violation("E_ARTIFACT", code, artifact.candidate.id, detail[:600]))

    plan = artifact.candidate
    need(artifact.artifact_type == ARTIFACT_TYPE and artifact.schema_version == ARTIFACT_SCHEMA, "ARTIFACT_TYPE", "unknown artifact type/schema")
    need(artifact.scope == snapshot.scope_key == plan.scope, "SCOPE_MISMATCH", f"artifact {artifact.scope}, source {snapshot.scope_key}")
    need(artifact.purpose is plan.purpose and artifact.purpose in PUBLISHABLE, "PURPOSE_MISMATCH", f"{artifact.purpose.value}")
    need(artifact.capability == CAPABILITY.get(artifact.purpose), "CAPABILITY_MISMATCH",
         f"{artifact.capability} is not the capability of {artifact.purpose.value}")
    need(artifact.source_run_id == snapshot.source_run_id == plan.source_run_id, "SOURCE_RUN_MISMATCH", "source run binding differs")
    need(artifact.snapshot_id == snapshot.id and artifact.snapshot_hash == snapshot.hash, "SOURCE_HASH_MISMATCH",
         "artifact names another snapshot hash")
    need(artifact.manifest_hash == snapshot.manifest_hash, "MANIFEST_MISMATCH", "source manifest hash differs")
    need(artifact.policy_id == policy.id and artifact.policy_hash == policy.hash, "POLICY_MISMATCH", "policy binding differs")
    need(artifact.timezone == policy.str("shift.timezone"), "TIMEZONE_MISMATCH", f"artifact {artifact.timezone}, policy "
                                                                                 f"{policy.str('shift.timezone')}")
    need(artifact.graph == graph_lineage(snapshot), "GRAPH_LINEAGE_MISMATCH", "declared hard-edge graph (root/rework/QA/parts edges) differs "
                                                                              "from the source snapshot")
    need(artifact.cs_lineage == cs_lineage(snapshot, plan), "CS_LINEAGE_MISMATCH", "declared same-run CS station/load/offset lineage differs "
                                                                                   "from the raw source rows")
    need(artifact.obligation_dates == obligation_dates(snapshot, policy), "OBLIGATION_DATES_MISMATCH", "obligation set dates differ")
    goals = freeze_goals(snapshot, policy, plan)
    need(artifact.frozen_goals == goals, "GOALS_MISMATCH", "frozen goals are not the obligation set derived from the plan")
    need(all(g.hash == g.computed_hash() for g in artifact.frozen_goals), "GOAL_HASH_MISMATCH", "a frozen goal's content hash is wrong")
    return out, checks


def validate_artifact(data: bytes, snapshot: PlanningSnapshot, policy: Policy, *, incumbent: IncumbentPlan | None = None, kind: str = "DETACHED",
                      created_at: Any = None) -> tuple[PlanArtifact | None, ValidationReceipt]:
    sha = payload_sha256(data)
    try:
        artifact = decode_artifact(data)
    except ArtifactError as exc:
        receipt = ValidationReceipt(
            id=digest_id("VAL", "UNDECODABLE", sha, snapshot.hash, policy.hash, kind, VALIDATOR_VERSION), candidate_id="", kind=kind,
            snapshot_hash=snapshot.hash, policy_hash=policy.hash, validator_version=VALIDATOR_VERSION, check_counts={"E_ARTIFACT": 1},
            coverage={"E_ARTIFACT": {"items": 1, "status": "CHECKED", "required": True}},
            violations=(Violation("E_ARTIFACT", exc.code, sha, exc.message[:600]),), required_skips=tuple(c for c in REQUIRED_CHECKS),
            plan_hash="", payload_sha256=sha, valid=False, created_at=created_at or snapshot.cutoff)
        return None, receipt
    extra, checks = _artifact_violations(artifact, snapshot, policy)
    base = validate(snapshot, policy, artifact.candidate, purpose=artifact.candidate.purpose, incumbent=incumbent, kind=kind, payload_sha256=sha,
                    created_at=created_at)
    violations = tuple(sorted(base.violations + tuple(extra), key=lambda v: (v.check, v.code, v.entity, v.detail)))
    coverage = dict(base.coverage)
    coverage["E_ARTIFACT"] = {"items": checks, "status": "CHECKED", "required": True}
    receipt = replace(base, id=digest_id("VAL", base.id, "ARTIFACT"), violations=violations, check_counts={**base.check_counts, "E_ARTIFACT": checks},
                      coverage=coverage, valid=not violations and not base.required_skips)
    return artifact, receipt


def publication_capability(plan: CandidatePlan, receipt: ValidationReceipt | None, *, kinds: tuple[str, ...] = ("FINAL",)) -> list[str]:
    """Every reason this plan may not be staged/signed/accepted under this receipt (empty list: allowed)."""
    reasons: list[str] = []
    if plan.purpose not in PUBLISHABLE:
        reasons.append(f"PURPOSE_NOT_PUBLISHABLE: {plan.purpose.value}")
    if receipt is None:
        return reasons + ["VALIDATION_MISSING: no independent validation receipt"]
    if receipt.kind not in kinds:
        reasons.append(f"VALIDATION_KIND: {receipt.kind} is not {kinds}")
    if receipt.plan_hash != plan.hash or receipt.candidate_id != plan.id:
        reasons.append("VALIDATION_FOR_ANOTHER_PLAN: receipt is bound to different plan content")
    if receipt.snapshot_hash != plan.snapshot_hash or receipt.policy_hash != plan.policy_hash:
        reasons.append("VALIDATION_LINEAGE: receipt snapshot/policy differ from the plan")
    if receipt.required_skips:
        reasons.append(f"REQUIRED_CHECKS_SKIPPED: {list(receipt.required_skips)}")
    if receipt.violations:
        codes = sorted({f"{v.check}:{v.code}" for v in receipt.violations})
        reasons.append(f"VALIDATION_FAILED: {len(receipt.violations)} violation(s) {codes}")
    if not receipt.valid and not reasons:
        reasons.append("VALIDATION_INVALID")
    return reasons
