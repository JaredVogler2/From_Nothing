"""Canonical acceptance protocol (S13 CANONICAL ACCEPTANCE ORDER, SIGNING, GOAL FREEZE, RECOVERY, P16).

``publish(services, candidate_id)`` runs exactly one order and stops at the first failure:

1. Independently validate the final candidate (FINAL; every required check, the incumbent
   reloaded from the accepted artifact).
2. Serialize it canonically into a ``PlanArtifact`` with today's frozen goals.
3. Detached decode and independent source-derived validation (DETACHED).
4. Write the private, unsigned, unaccepted stage, keyed by the publication idempotency key.
5. Read back the exact staged bytes. They must equal the serialized bytes, and the previous
   head must be unchanged.
6. Independently validate the readback (READBACK): source, crew, CS, graph, duty, economics
   and goals.
7. Sign exactly those readback-validated bytes together with source, policy and purpose.
8. Verify the signed artifact.
9. Write the immutable PublishedPlan, FrozenGoals and AcceptanceReceipt, then move the plan head
   LAST in the scoped acceptance (lock plus expected version).
10. Run the idempotent post-acceptance obligations:
    * the new accepted incumbent for task defense (P14);
    * delivery commitments (``projections.record_accepted``).

**Recovery.** Every step is deterministic for a given candidate, so a retry after a crash at any
point recomputes the same bytes, stage and plan ids. If a receipt already exists, the retry
compares exact content and reconciles the head once. Nothing is reported accepted before the
receipt and the head are durable: a signed but uncommitted publication is never a success. The
old head stays until a valid acceptance.

**Fixture versus live.** The fixture rehearses the whole order with the published TEST key and
labels its receipts ``fixture_readback``. In live mode the proposed SQL stage is not installed:
the local rehearsal runs, then the result is UNAVAILABLE with no signature, acceptance or head
move. A TEST key can never accept live.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..contracts.planning import (AcceptanceReceipt, CandidatePlan, IncumbentAssignment, IncumbentPlan, PlanArtifact, PlanningSnapshot,
                                  PublishedPlan)
from ..domain import digest_id
from ..validation.plan import CANDIDATES, load_incumbent, persist_receipt, validate
from ..validation.publication import publication_capability, validate_artifact
from .repository import FixturePublicationRepository, PlanStage, PublicationError, PublishedPayload, repository_for
from .serializer import artifact_bytes, build_artifact, decode_artifact, payload_sha256
from .signing import Signer, resolve_signer


@dataclass
class PublishResult:
    status: str                            # ACCEPTED | ALREADY_ACCEPTED | RECOVERED | REJECTED | UNAVAILABLE | NOT_CURRENT
    candidate_id: str
    plan_id: str | None = None
    reasons: tuple[str, ...] = ()
    receipt: AcceptanceReceipt | None = None
    validations: dict[str, str] = field(default_factory=dict)
    stage_id: str | None = None
    head_version: int | None = None
    steps: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        r = self.receipt
        return {"publication_status": self.status, "candidate_id": self.candidate_id, "plan_id": self.plan_id, "reasons": list(self.reasons),
                "validations": dict(self.validations), "stage_id": self.stage_id, "head_version": self.head_version, "steps": list(self.steps),
                "receipt": None if r is None else {"plan_id": r.plan_id, "payload_sha256": r.payload_sha256, "key_id": r.key_id,
                                                    "fixture_readback": r.fixture_readback, "accepted_at": r.accepted_at.isoformat(),
                                                    "head_version": r.head_version, "scope": r.scope, "purpose": r.purpose.value}}


def _ids(plan: CandidatePlan, sha: str) -> tuple[str, str]:
    return (digest_id("STG", plan.scope, plan.purpose.value, plan.id, sha), digest_id("PLAN", plan.scope, plan.purpose.value, plan.id, sha))


def incumbent_from(snapshot: PlanningSnapshot, published: PublishedPlan, plan: CandidatePlan, tz) -> IncumbentPlan:
    """The accepted plan becomes the next replan's incumbent (task defense input), real people only."""
    jobs = {j.id: j for j in snapshot.jobs}
    phases = {p.key.id: p for p in snapshot.phases}
    people = {p.id: p for p in snapshot.people}
    namespaces = {p.namespace for p in snapshot.people if not p.synthetic}
    namespace = sorted(namespaces)[0] if namespaces else "unknown"
    items = []
    for a in sorted(plan.assignments, key=lambda x: (x.start, x.id)):
        phase, job = phases.get(a.phase), jobs.get(a.job)
        crew = [people.get(r.person) for r in a.crew]
        if phase is None or job is None or not a.shift_id or not crew or any(p is None or p.synthetic or p.namespace != namespace for p in crew):
            continue
        _, day, label = a.shift_id.rsplit(":", 2)
        items.append(IncumbentAssignment(job.line, job.key.native_id, phase.kind.value, phase.ordinal, datetime.fromisoformat(day).date(),
                                         int(label[1:]), a.start.astimezone(tz).strftime("%H:%M"), tuple(p.key.native_id for p in crew)))
    return IncumbentPlan(digest_id("INC", published.id), published.scope, published.purpose.value, snapshot.policy_id, "PUBLISHED",
                         published.accepted_at, snapshot.cutoff, namespace, tuple(items))


def _post_acceptance(services: Any, snapshot: PlanningSnapshot, published: PublishedPlan, plan: CandidatePlan) -> list[str]:
    from zoneinfo import ZoneInfo

    from ..engine.commitments import record_accepted_incumbent
    from ..engine.projections import record_accepted

    tz = ZoneInfo(services.policy.str("shift.timezone"))
    record_accepted_incumbent(services, incumbent_from(snapshot, published, plan, tz))
    delivery = record_accepted(services, plan, snapshot)
    return ["incumbent_recorded", f"delivery_{delivery.status.lower()}"]


def publish(services: Any, candidate_id: str, *, signer: Signer | None = None, fault: Callable[[str], None] | None = None,
            repository: Any = None) -> PublishResult:
    from ..data.acquisition import load_snapshot

    fault = fault or (lambda step: None)
    repos = services.repositories
    result = PublishResult("REJECTED", candidate_id)
    plan = repos.get_typed(CANDIDATES, candidate_id, "CandidatePlan", CandidatePlan)
    if plan is None:
        result.reasons = ("CANDIDATE_NOT_FOUND",)
        return result
    snapshot = load_snapshot(repos, plan.snapshot_id)
    if snapshot is None:
        result.status, result.reasons = "UNAVAILABLE", ("SNAPSHOT_NOT_FOUND",)
        return result
    policy = services.policy
    repo = repository or repository_for(services)
    incumbent = load_incumbent(repos, plan.incumbent_id)
    now = services.clock.now()

    # 1. independent final validation
    final = validate(snapshot, policy, plan, incumbent=incumbent, kind="FINAL", created_at=now)
    persist_receipt(repos, final, plan)
    result.validations["FINAL"] = final.id
    result.steps.append("final_validation")
    blocking = publication_capability(plan, final)
    if blocking:
        result.reasons = tuple(blocking)
        return result
    # 2. canonical serialization (deterministic: retries recompute identical bytes and ids)
    artifact = build_artifact(snapshot, policy, plan, final.id)
    data = artifact_bytes(artifact)
    sha = payload_sha256(data)
    stage_id, plan_id = _ids(plan, sha)
    result.plan_id, result.stage_id = plan_id, stage_id
    result.steps.append("serialize")
    # recovery: an acceptance receipt for exactly this content already exists
    existing = repo.receipt(plan_id)
    if existing is not None:
        return _reconcile(services, repo, snapshot, plan, existing, result)
    current = repo.head(plan.scope, plan.purpose.value)
    if current is not None:
        prior = repo.published(current.record_id)
        if prior is not None and prior.source_run_id != plan.source_run_id and _cutoff(repos, prior) > snapshot.cutoff:
            result.reasons = (f"STALE_REPLAY: the current plan {prior.id} rests on a later source cutoff",)
            return result
    # 3. detached decode and independent validation
    _, detached = validate_artifact(data, snapshot, policy, incumbent=incumbent, kind="DETACHED", created_at=now)
    persist_receipt(repos, detached, plan)
    result.validations["DETACHED"] = detached.id
    result.steps.append("detached_validation")
    if not detached.valid:
        result.reasons = tuple(sorted({f"{v.check}:{v.code}" for v in detached.violations})) or ("DETACHED_INVALID",)
        return result
    # 4. private unsigned stage, with the head observed before it
    head_before = repo.head(plan.scope, plan.purpose.value)
    expected_version = head_before.version if head_before else 0
    try:
        repo.stage(PlanStage(stage_id, digest_id("PUBK", plan.scope, plan.purpose.value, plan.id, plan.hash), plan.scope, plan.purpose.value,
                             plan.id, plan.source_run_id, plan.policy_hash, sha, data.decode("utf-8"), False, now))
    except PublicationError as exc:
        result.status = "UNAVAILABLE" if exc.code == "SQL_STAGE_NOT_INSTALLED" else "REJECTED"
        result.reasons = (f"{exc.code}: {exc.message}",)
        return result
    result.steps.append("stage")
    fault("after_stage")
    # 5-6. exact readback, independently re-validated from source while the previous head is unchanged
    readback = repo.read_stage(stage_id) or b""
    result.steps.append("readback")
    _, rb = validate_artifact(readback, snapshot, policy, incumbent=incumbent, kind="READBACK", created_at=now)
    persist_receipt(repos, rb, plan)
    result.validations["READBACK"] = rb.id
    problems = sorted({f"{v.check}:{v.code}" for v in rb.violations}) + (["STAGE_READBACK_MISMATCH: staged bytes differ from the serialized bytes"]
                                                                       if readback != data else [])
    if problems or not rb.valid:
        result.reasons = tuple(problems) or ("READBACK_INVALID",)
        return result
    now_head = repo.head(plan.scope, plan.purpose.value)
    if (now_head.version if now_head else 0) != expected_version:
        result.reasons = ("HEAD_MOVED_DURING_PUBLICATION: another plan was accepted; re-validate against it",)
        return result
    result.steps.append("readback_validation")
    fault("after_readback")
    # 7-8. sign exactly the readback-validated bytes with the trusted key, then verify
    trusted, why = resolve_signer(services.settings)
    if trusted is None:
        result.status, result.reasons = "UNAVAILABLE", (why,)
        return result
    signer = signer or trusted
    if signer.key_id != trusted.key_id or signer.sign("probe", "probe", "", "", "") != trusted.sign("probe", "probe", "", "", ""):
        result.reasons = (f"UNAUTHORIZED_SIGNER: {signer.key_id} is not the trusted signing key",)
        return result
    if signer.test_key and (services.settings.mode != "fixture" or not isinstance(repo, FixturePublicationRepository)):
        result.reasons = ("TEST_KEY_NOT_LIVE: the fixture TEST key can never produce a live acceptance",)
        return result
    signed_sha = payload_sha256(readback)
    signature = signer.sign(plan.scope, plan.purpose.value, snapshot.hash, policy.hash, signed_sha)
    result.steps.append("sign")
    fault("after_sign")
    if not signer.verify(signature, signer.key_id, plan.scope, plan.purpose.value, snapshot.hash, policy.hash, signed_sha):
        result.reasons = ("SIGNATURE_INVALID",)
        return result
    result.steps.append("verify")
    # 9. immutable acceptance, head LAST
    prior_record = repo.published(plan_id)
    accepted_at = prior_record.accepted_at if prior_record is not None else now
    receipt = AcceptanceReceipt(plan_id, stage_id, rb.id, final.id, detached.id, signed_sha, plan.source_run_id, snapshot.hash, policy.hash,
                                plan.scope, plan.purpose, signer.key_id, signature, isinstance(repo, FixturePublicationRepository), accepted_at,
                                expected_version + 1)
    goals = artifact.frozen_goals
    published = PublishedPlan(plan_id, plan.id, signed_sha, snapshot.id, plan.source_run_id, policy.hash, plan.purpose, plan.scope, receipt,
                              signer.key_id, signature, tuple(g.id for g in goals), accepted_at)
    try:
        version = repo.accept(published, PublishedPayload(plan_id, signed_sha, readback.decode("utf-8")), goals, expected_version)
    except PublicationError as exc:
        result.reasons = (f"{exc.code}: {exc.message}",)
        return result
    result.steps.append("accept")
    fault("after_accept")
    # 10. idempotent post-acceptance obligations
    result.steps.extend(_post_acceptance(services, snapshot, published, plan))
    result.status, result.receipt, result.head_version = "ACCEPTED", receipt, version
    fault("after_post")
    return result


def _cutoff(repos: Any, published: PublishedPlan) -> datetime:
    from ..data.acquisition import load_snapshot

    snap = load_snapshot(repos, published.snapshot_id)
    return snap.cutoff if snap is not None else datetime.min.replace(tzinfo=published.accepted_at.tzinfo)


def _reconcile(services: Any, repo: Any, snapshot: PlanningSnapshot, plan: CandidatePlan, receipt: AcceptanceReceipt,
               result: PublishResult) -> PublishResult:
    published = repo.published(receipt.plan_id)
    payload = repo.payload(receipt.plan_id)
    result.receipt = receipt
    if published is None or payload is None or payload.payload_sha256 != receipt.payload_sha256 or published.payload_hash != receipt.payload_sha256:
        result.status, result.reasons = "REJECTED", ("RECOVERY_CONTENT_MISMATCH: stored acceptance does not match its receipt",)
        return result
    head = repo.head(plan.scope, plan.purpose.value)
    if head is not None and head.record_id == receipt.plan_id:
        result.status, result.head_version = "ALREADY_ACCEPTED", head.version
    elif (head.version if head else 0) == receipt.head_version - 1:
        result.head_version = repo.accept(published, payload, tuple(repo.goals(published.frozen_goal_ids)), receipt.head_version - 1)
        result.status = "RECOVERED"
    else:
        result.status, result.reasons = "NOT_CURRENT", ("SUPERSEDED: another plan became current after this staging; it is history only",)
        return result
    result.steps.append("reconcile")
    result.steps.extend(_post_acceptance(services, snapshot, published, plan))
    return result


# ---------------------------------------------------------------------- read path

@dataclass(frozen=True)
class CurrentPlan:
    published: PublishedPlan
    artifact: PlanArtifact
    head_version: int


def current_plan(services: Any, scope: str, purpose: str, *, signer: Signer | None = None) -> tuple[CurrentPlan | None, str]:
    """The accepted immutable head, verified end to end. A failure is explicit and makes nothing current."""
    repo = repository_for(services)
    head = repo.head(scope, purpose)
    if head is None:
        return None, "NO_ACCEPTED_PLAN"
    try:
        published, receipt, payload = repo.published(head.record_id), repo.receipt(head.record_id), repo.payload(head.record_id)
    except Exception as exc:  # noqa: BLE001 - unreadable or tampered storage is a security failure, never an accepted plan
        return None, f"SECURITY_FAILURE: stored acceptance is unreadable ({getattr(exc, 'code', type(exc).__name__)})"
    if published is None or receipt is None or payload is None:
        return None, "SECURITY_FAILURE: head names an incomplete acceptance"
    data = payload.payload_text.encode("utf-8")
    sha = payload_sha256(data)
    if not (sha == payload.payload_sha256 == receipt.payload_sha256 == published.payload_hash):
        return None, "SECURITY_FAILURE: payload hash does not match the signed receipt"
    if published.scope != scope or published.purpose.value != purpose or receipt.scope != scope:
        return None, "SECURITY_FAILURE: wrong scope or purpose"
    signer = signer or resolve_signer(services.settings)[0]
    if signer is None or not signer.verify(receipt.signature_hex, receipt.key_id, receipt.scope, receipt.purpose.value, receipt.snapshot_hash,
                                           receipt.policy_hash, sha) or published.signature != receipt.signature_hex:
        return None, "SECURITY_FAILURE: signature does not verify with the trusted key"
    try:
        artifact = decode_artifact(data)
    except Exception as exc:  # noqa: BLE001 - any decode failure is a security failure
        return None, f"SECURITY_FAILURE: {exc}"
    if artifact.snapshot_hash != receipt.snapshot_hash or artifact.policy_hash != receipt.policy_hash:
        return None, "SECURITY_FAILURE: artifact bindings differ from the receipt"
    return CurrentPlan(published, artifact, head.version), "OK"


def cli_publish(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    if args.mode != services.settings.mode:
        return {"status": "invalid", "error": {"code": "MODE_MISMATCH", "message": f"runtime mode is {services.settings.mode}"}}
    if args.mode == "fixture" and not args.rehearse_readback:
        return {"status": "invalid", "error": {"code": "REHEARSAL_FLAG_REQUIRED",
                                               "message": "fixture publication is a labelled readback rehearsal: pass --rehearse-readback"}}
    result = publish(services, args.candidate)
    status = {"ACCEPTED": "ok", "ALREADY_ACCEPTED": "ok", "RECOVERED": "ok", "UNAVAILABLE": "unavailable"}.get(result.status, "invalid")
    return {"status": status, **result.summary()}
