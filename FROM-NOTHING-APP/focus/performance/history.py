"""Append-only evaluation history, as-known replay and IE review proposals (S14, P17/P18).

Every evaluation belongs to one revision series, keyed by scope, kind, the exact goal set and the
policy:

* **Unchanged evidence** returns the current revision (idempotent; no new record).
* **Changed evidence** appends revision n+1 with ``supersedes`` = revision n. Causes include a late
  correction, a void, a reopen, an approval, an appeal or new coverage. The series head moves by
  compare-and-set; nothing is overwritten.
* **As-known replay.** ``as_known(series, at)`` returns the revision that was current at ``at``,
  so the prior as-known evaluation stays available after any correction.

``EvaluationEvidence`` records the exact unit facts behind each revision; every display reads them
rather than recomputing.

An IE review (``POST /ie-reviews``) records a standard/role/data correction proposal. It never
rewrites an issued goal, a frozen unit or an evaluation; an accepted correction can only reach
future plans through a new policy/snapshot.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from ..codec import content_hash
from ..contracts.evaluation import Evaluation, EvaluationEvidence
from ..contracts.events import IEReview
from ..data.store import VersionConflict
from ..domain import DomainError, aware_utc, digest_id

EVALUATIONS = "evaluations"
EVIDENCE = "evaluation_evidence"


def series_key(scope: str, kind: str, goal_ids: tuple[str, ...], policy_id: str) -> str:
    return digest_id("EVS", scope, kind, sorted(goal_ids), policy_id)


def series_head(scope: str, key: str) -> str:
    return f"evaluation/{scope}/{key}"


def get(services: Any, evaluation_id: str) -> Evaluation | None:
    return services.repositories.get_typed(EVALUATIONS, evaluation_id, "Evaluation", Evaluation)


def evidence(services: Any, evidence_hash: str) -> EvaluationEvidence | None:
    return services.repositories.get_typed(EVIDENCE, evidence_hash, "EvaluationEvidence", EvaluationEvidence)


def current(services: Any, scope: str, key: str) -> Evaluation | None:
    head = services.repositories.head(series_head(scope, key))
    return None if head is None else get(services, head.record_id)


def append(services: Any, draft: Evaluation, proof: EvaluationEvidence) -> tuple[Evaluation, str]:
    """Append ``draft`` as the next revision of its series (``NEW``), or return the current one (``UNCHANGED``)."""
    repos = services.repositories
    if proof.id != draft.evidence_hash:
        raise DomainError("EVIDENCE_MISMATCH", "the evaluation's evidence hash must name its evidence record")
    if repos.get_raw(EVIDENCE, proof.id) is None:
        repos.put_record(EVIDENCE, "EvaluationEvidence", proof.id, proof, created_at=draft.cutoff)
    name = series_head(draft.scope, draft.series_key)
    with repos.store.lock(f"evaluation-{draft.series_key}"):
        for _ in range(5):
            head = repos.head(name)
            latest = None if head is None else get(services, head.record_id)
            if latest is not None and latest.evidence_hash == draft.evidence_hash and latest.policy_hash == draft.policy_hash \
                    and latest.status == draft.status and latest.reasons == draft.reasons:
                return latest, "UNCHANGED"
            if latest is not None and draft.cutoff < latest.cutoff:
                raise DomainError("CUTOFF_REGRESSION", "a revision cannot be evaluated as of a time before the current revision; use as_known")
            revision = 1 if latest is None else latest.revision + 1
            final = replace(draft, revision=revision, supersedes=None if latest is None else latest.id)
            if repos.get_raw(EVALUATIONS, final.id) is None:
                repos.put_record(EVALUATIONS, "Evaluation", final.id, final, created_at=final.cutoff)
            try:
                repos.move_head(name, final.id, 0 if head is None else head.version)
                return final, "NEW"
            except VersionConflict:
                continue
    raise DomainError("HISTORY_CONTENTION", "could not append the evaluation revision")


def revisions(services: Any, scope: str, key: str) -> list[Evaluation]:
    """Every revision of a series, oldest first (walks the supersedes chain from the head)."""
    out: list[Evaluation] = []
    cur = current(services, scope, key)
    seen: set[str] = set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        out.append(cur)
        cur = get(services, cur.supersedes) if cur.supersedes else None
    return list(reversed(out))


def as_known(services: Any, scope: str, key: str, at: datetime) -> Evaluation | None:
    at = aware_utc(at, "at")
    known = [e for e in revisions(services, scope, key) if e.cutoff <= at]
    return known[-1] if known else None


def series_for_goal(services: Any, goal_id: str) -> str:
    return series_key(services.settings.scope_key, "GOAL", (goal_id,), services.policy.id)


# ---------------------------------------------------------------------- IE review proposals

IE_REVIEWS = "ie_reviews"


def record_ie_review(services: Any, *, request_id: str, requested_by: str, job_id: str, standard_revision: str, proposed_correction: str,
                     reason: str, at: datetime) -> IEReview:
    if not job_id or not standard_revision or not proposed_correction.strip() or not reason.strip():
        raise DomainError("BAD_REQUEST", "an IE review names the job, the standard revision, the proposed correction and the reason")
    review = IEReview(digest_id("IER", services.settings.scope_key, request_id), services.settings.scope_key, job_id, standard_revision,
                      proposed_correction[:2000], reason[:2000], requested_by, aware_utc(at, "at"), "PROPOSED", False)
    repos = services.repositories
    raw = repos.get_raw(IE_REVIEWS, review.id)
    if raw is None:
        repos.put_record(IE_REVIEWS, "IEReview", review.id, review, created_at=review.requested_at)
    else:
        existing = repos.get_typed(IE_REVIEWS, review.id, "IEReview", IEReview)
        if content_hash(existing) != content_hash(review):
            raise DomainError("REVISION_CONTENT_CONFLICT", "this request id was already recorded with different content")
    return review


def worker_ie_review(services: Any, run: Any) -> dict[str, Any]:
    req = run.request
    review = record_ie_review(services, request_id=run.id, requested_by=run.requested_by, job_id=str(req.get("job_id", "")),
                              standard_revision=str(req.get("standard_revision", "")), proposed_correction=str(req.get("proposed_correction", "")),
                              reason=str(req.get("reason", "")), at=run.created_at)
    return {"status": "recorded", "ie_review_id": review.id, "affects_issued_goals": False,
            "note": "issued goals, frozen units and evaluations are immutable; a correction reaches only future plans via a new policy/snapshot"}
