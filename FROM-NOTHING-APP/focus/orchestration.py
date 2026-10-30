"""Durable pipeline orchestration with roles, leases, dependency hashes and an outbox (S17 WORKERS, P23).

``acquire -> admit -> schedule -> validate -> stage_readback -> sign_accept -> observe_evaluate``

Every step:

* **Has one role.** The roles are ``source``, ``app``, ``stager``, ``signer`` and ``evaluator``. A worker runs only its
  own role's steps, so the app principal never signs and the signer never acquires.
* **Needs the durable outputs of the steps before it.** Its input hash is the hash of those outputs. A missing or
  failed input blocks it and everything after it; it never runs on stale or partial data.
* **Takes a scoped lease** (pipeline, step) by an optimistic version update. A live lease blocks a competitor. An
  expired lease may be taken over, and the new owner re-runs the step's idempotent handler, which reconciles from
  durable receipts.
* **Is idempotent.**
  - A completed step is never re-run.
  - Replays reuse the same keys (the acquisition idempotency key, deterministic candidate and publication ids).
  - A crash at any point therefore resumes without duplicate source facts, commitments or earning events.
* **Has exactly one outbox event once it completes.** The event is built from the step's recorded final state and
  keyed by pipeline and step, so it is byte-identical on every emission. Every run first re-emits any event missing
  for a completed step, so a crash between completion and emission never loses one.

Signing can never precede a clean readback. ``sign_accept`` needs ``stage_readback``, whose output names the readback
validation of each plan. The signer's own ``publish`` re-reads and re-validates those exact staged bytes before
signing, and moves the accepted head last.

The repository is the fixture store or, live, the proposed ``FOCUS_FN_PipelineRun/PipelineStep/Outbox`` tables
(migration 002). The live adapter is not installed, so live pipelines report ``UNAVAILABLE`` from the steps that need it.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from .codec import canonical_dumps, sha256_hex
from .data.store import StoreError, VersionConflict
from .domain import digest_id

PIPELINES = "pipelines"
OUTBOX = "outbox"
ROLES = ("source", "app", "stager", "signer", "evaluator")
MAX_ATTEMPTS = 3
PURPOSES = ("EXECUTABLE", "FORECAST")


class StepFailed(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StepDef:
    name: str
    role: str
    needs: tuple[str, ...]
    handler: str


STEPS: tuple[StepDef, ...] = (
    StepDef("acquire", "source", (), "step_acquire"),
    StepDef("admit", "source", ("acquire",), "step_admit"),
    StepDef("schedule", "app", ("admit",), "step_schedule"),
    StepDef("validate", "app", ("schedule", "admit"), "step_validate"),
    StepDef("stage_readback", "stager", ("validate",), "step_stage_readback"),
    StepDef("sign_accept", "signer", ("stage_readback",), "step_sign_accept"),
    StepDef("observe_evaluate", "evaluator", ("sign_accept",), "step_observe_evaluate"),
)
BY_NAME = {s.name: s for s in STEPS}


def pipeline_id(scope: str, key: str) -> str:
    return digest_id("PIPE", scope, key)


def _sha(value: Any) -> str:
    return sha256_hex(canonical_dumps(value))


def _blank_step(defn: StepDef, ordinal: int) -> dict[str, Any]:
    return {"name": defn.name, "ordinal": ordinal, "role": defn.role, "needs": list(defn.needs), "status": "pending", "input_sha": None,
            "output": None, "output_sha": None, "owner": None, "lease_until": None, "attempts": 0, "error": None, "updated_at": None}


# ---------------------------------------------------------------------- state


def create(services: Any, *, key: str, scenario: str | None = None, budget_seconds: float | None = None,
           max_moves: int | None = None) -> dict[str, Any]:
    """Create (or return) the pipeline for ``key`` in this scope; the request is fixed at creation (idempotent)."""
    repos = services.repositories
    scope = services.settings.scope_key
    pid = pipeline_id(scope, key)
    found = repos.get_state(PIPELINES, pid)
    if found is not None:
        return found[0]
    doc = {"schema_version": 1, "pipeline_id": pid, "scope": scope, "key": key, "created_at": services.clock.now(),
           "request": {"scenario": scenario or services.settings.scenario_id, "budget_seconds": budget_seconds, "max_moves": max_moves,
                       "mode": services.settings.mode, "policy_id": services.policy.id},
           "steps": {d.name: _blank_step(d, i) for i, d in enumerate(STEPS)}}
    try:
        repos.put_state(PIPELINES, pid, doc, 0)
    except VersionConflict:
        return repos.get_state(PIPELINES, pid)[0]            # a concurrent creator won: same key, same request
    return repos.get_state(PIPELINES, pid)[0]


def load(services: Any, pid: str) -> tuple[dict[str, Any], int] | None:
    return services.repositories.get_state(PIPELINES, pid)


def _parse(at: Any) -> datetime | None:
    if at is None or isinstance(at, datetime):
        return at
    from .codec import parse_utc

    return parse_utc(at)


@dataclass(frozen=True)
class Claim:
    outcome: str              # CLAIMED | TAKEN_OVER | ALREADY_COMPLETED | LEASED | BLOCKED | ROLE_MISMATCH | EXHAUSTED | CONTENDED
    detail: str = ""
    version: int = 0
    inputs: Mapping[str, Any] | None = None


def claim(services: Any, pid: str, step: str, *, owner: str, role: str, lease_seconds: float = 900.0) -> Claim:
    """Lease one step for ``owner`` if its role matches, its needs are complete and no live lease holds it."""
    defn = BY_NAME[step]
    if role != defn.role:
        return Claim("ROLE_MISMATCH", f"{step} belongs to the {defn.role} role, not {role}")
    found = load(services, pid)
    if found is None:
        return Claim("BLOCKED", "PIPELINE_NOT_FOUND")
    doc, version = found
    state = doc["steps"][step]
    if state["status"] == "completed":
        return Claim("ALREADY_COMPLETED", state["output_sha"], version)
    needs = {n: doc["steps"][n] for n in defn.needs}
    missing = sorted(n for n, s in needs.items() if s["status"] != "completed")
    if missing:
        failed = sorted(n for n in missing if needs[n]["status"] in ("failed", "blocked"))
        return Claim("BLOCKED", f"needs not completed: {', '.join(missing)}" + (f" (failed: {', '.join(failed)})" if failed else ""), version)
    now = services.clock.now()
    taken_over = False
    if state["status"] == "running":
        lease_until = _parse(state["lease_until"])
        if lease_until is not None and lease_until > now and state["owner"] != owner:
            return Claim("LEASED", f"held by {state['owner']} until {lease_until.isoformat()}", version)
        taken_over = state["owner"] != owner
    if state["attempts"] >= MAX_ATTEMPTS and state["status"] in ("failed", "running"):
        return Claim("EXHAUSTED", f"{state['attempts']} attempts; a new pipeline key is needed", version)
    inputs = {n: s["output"] for n, s in needs.items()}
    new = dict(state, status="running", owner=owner, lease_until=now + timedelta(seconds=lease_seconds), attempts=state["attempts"] + 1,
               input_sha=_sha({n: s["output_sha"] for n, s in needs.items()}), error=None, updated_at=now)
    doc = dict(doc, steps=dict(doc["steps"], **{step: new}))
    try:
        new_version = services.repositories.put_state(PIPELINES, pid, doc, version)
    except (VersionConflict, StoreError):
        return Claim("CONTENDED", "another worker changed the pipeline first", version)
    return Claim("TAKEN_OVER" if taken_over else "CLAIMED", "", new_version, inputs)


def _finish(services: Any, pid: str, step: str, owner: str, **changes: Any) -> tuple[dict[str, Any] | None, str]:
    """Record a step's terminal state if this owner still holds its lease; else report the lease as lost."""
    for _ in range(5):
        doc, version = load(services, pid)
        state = doc["steps"][step]
        if state["status"] != "running" or state["owner"] != owner:
            return None, "LEASE_LOST"
        new = dict(state, owner=None, lease_until=None, updated_at=services.clock.now(), **changes)
        try:
            services.repositories.put_state(PIPELINES, pid, dict(doc, steps=dict(doc["steps"], **{step: new})), version)
            return new, "OK"
        except (VersionConflict, StoreError):
            continue
    return None, "CONTENDED"


def run_step(services: Any, pid: str, step: str, *, role: str, owner: str | None = None, lease_seconds: float = 900.0,
             crash: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Claim, execute and record one step. ``crash`` (tests) can stop a run after the handler, before recording."""
    owner = owner or f"{role}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    got = claim(services, pid, step, owner=owner, role=role, lease_seconds=lease_seconds)
    if got.outcome not in ("CLAIMED", "TAKEN_OVER"):
        return {"step": step, "outcome": got.outcome, "detail": got.detail}
    doc, _ = load(services, pid)
    handler = globals()[BY_NAME[step].handler]
    try:
        output = handler(services, doc, dict(got.inputs or {}))
    except StepFailed as exc:
        state, why = _finish(services, pid, step, owner, status="failed", error={"code": exc.code, "message": exc.message[:500]})
        return {"step": step, "outcome": "FAILED" if state else why, "error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:  # noqa: BLE001 - every failure is durable and typed
        code = getattr(exc, "code", type(exc).__name__)
        state, why = _finish(services, pid, step, owner, status="failed", error={"code": code, "message": str(exc)[:500]})
        return {"step": step, "outcome": "FAILED" if state else why, "error": {"code": code, "message": str(exc)[:500]}}
    if crash is not None:
        crash(step)                                                 # simulated crash: the lease stays until it expires
    output_sha = _sha(output)
    state, why = _finish(services, pid, step, owner, status="completed", output=output, output_sha=output_sha)
    if state is None:
        return {"step": step, "outcome": why, "detail": "the lease expired and another worker owns the step; its idempotent run decides"}
    reconcile_outbox(services, pid)                                # emit from the recorded state: identical bytes on every emission
    return {"step": step, "outcome": "COMPLETED" if got.outcome == "CLAIMED" else "COMPLETED_AFTER_TAKEOVER", "output_sha": output_sha,
            "output": output}


def _emit(services: Any, pid: str, scope: str, state: Mapping[str, Any]) -> bool:
    """The one outbox event of a completed step, from its recorded state (identical bytes on every emission)."""
    return services.repositories.store.put_immutable(OUTBOX, digest_id("OBX", pid, state["name"]), canonical_dumps(
        {"schema_version": 1, "pipeline_id": pid, "scope": scope, "step": state["name"], "output_sha": state["output_sha"],
         "output": state["output"], "completed_at": state["updated_at"]}))


def reconcile_outbox(services: Any, pid: str) -> list[str]:
    """Emit any outbox event missing for a completed step (after a crash between completion and emission)."""
    found = load(services, pid)
    if found is None:
        return []
    doc, _ = found
    return [s["name"] for s in doc["steps"].values() if s["status"] == "completed" and _emit(services, pid, doc["scope"], s)]


def run_role(services: Any, pid: str, role: str, *, owner: str | None = None, lease_seconds: float = 900.0) -> list[dict[str, Any]]:
    """Run every step of ``role`` that is ready, in pipeline order, until one is blocked, leased or fails."""
    results = []
    reconcile_outbox(services, pid)
    for defn in STEPS:
        if role not in ("all", defn.role):
            continue
        result = run_step(services, pid, defn.name, role=defn.role, owner=owner, lease_seconds=lease_seconds)
        results.append(result)
        if result["outcome"] not in ("COMPLETED", "COMPLETED_AFTER_TAKEOVER", "ALREADY_COMPLETED"):
            break
    return results


def status(services: Any, pid: str) -> dict[str, Any]:
    found = load(services, pid)
    if found is None:
        return {"status": "invalid", "error": {"code": "PIPELINE_NOT_FOUND", "message": pid}}
    doc, version = found
    steps = [{k: doc["steps"][d.name][k] for k in ("name", "role", "status", "attempts", "owner", "lease_until", "output_sha", "error")}
             for d in STEPS]
    done = all(s["status"] == "completed" for s in steps)
    failed = [s["name"] for s in steps if s["status"] == "failed"]
    return {"status": "ok", "pipeline_id": pid, "scope": doc["scope"], "key": doc["key"], "version": version,
            "state": "completed" if done else ("failed" if failed else "in_progress"), "failed": failed, "steps": steps,
            "outbox": outbox_events(services, pid)}


def outbox_events(services: Any, pid: str) -> list[dict[str, Any]]:
    from .codec import strict_loads

    events = []
    for defn in STEPS:
        raw = services.repositories.store.get(OUTBOX, digest_id("OBX", pid, defn.name))
        if raw is not None:
            event = strict_loads(raw)
            events.append({"step": event["step"], "output_sha": event["output_sha"], "completed_at": event["completed_at"]})
    return events


# ---------------------------------------------------------------------- step handlers (idempotent)


def step_acquire(services: Any, doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from .clock import FIXTURE_CUTOFF
    from .data.acquisition import acquire, make_reader

    cutoff = FIXTURE_CUTOFF if services.settings.mode == "fixture" else services.clock.now()
    outcome = acquire(services, make_reader(services, doc["request"]["scenario"]), cutoff=cutoff, idempotency_key=f"pipeline:{doc['key']}")
    if outcome.status != "COMPLETE":
        raise StepFailed("ACQUISITION_REJECTED", f"source run {outcome.run_id} is {outcome.status}: {outcome.rejection}")
    return {"source_run_id": outcome.run_id, "snapshot_id": outcome.snapshot_id, "datasets": dict(sorted(outcome.datasets.items()))}


def step_admit(services: Any, doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from .data.acquisition import get_run, load_snapshot, source_head_name

    run_id = inputs["acquire"]["source_run_id"]
    head = services.repositories.head(source_head_name(doc["scope"]))
    if head is None or head.record_id != run_id:
        raise StepFailed("SOURCE_NOT_HEAD", f"the admitted source head is {head.record_id if head else 'absent'}, not {run_id}")
    found = get_run(services.repositories, run_id)
    snapshot = load_snapshot(services.repositories, found[0].snapshot_id) if found else None
    if snapshot is None:
        raise StepFailed("SNAPSHOT_MISSING", f"no admitted snapshot for {run_id}")
    snapshot.verify()
    return {"source_run_id": run_id, "snapshot_id": snapshot.id, "snapshot_hash": snapshot.hash, "head_version": head.version}


def step_schedule(services: Any, doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from .domain import Purpose
    from .engine.max.optimizer.orchestrator import schedule

    request = doc["request"]
    out: dict[str, Any] = {}
    for purpose in PURPOSES:
        plan, run = schedule(services, source_run=inputs["admit"]["source_run_id"], purpose=Purpose(purpose),
                             budget_seconds=request["budget_seconds"], max_moves=request["max_moves"])
        out[purpose] = {"candidate_id": plan.id, "valid": run.validation.valid, "violations": len(run.validation.violations),
                        "solver": run.solver.get("status")}
    return out


def step_validate(services: Any, doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Independent FINAL validation of each candidate against the admitted snapshot (same source, every required check)."""
    from .contracts.planning import CandidatePlan
    from .data.acquisition import load_snapshot
    from .validation.plan import CANDIDATES, load_incumbent, persist_receipt, validate

    snapshot = load_snapshot(services.repositories, inputs["admit"]["snapshot_id"])
    out: dict[str, Any] = {}
    for purpose, item in inputs["schedule"].items():
        plan = services.repositories.get_typed(CANDIDATES, item["candidate_id"], "CandidatePlan", CandidatePlan)
        if plan is None or plan.source_run_id != inputs["admit"]["source_run_id"] or plan.snapshot_hash != snapshot.hash:
            raise StepFailed("SOURCE_MISMATCH", f"{purpose} candidate is not bound to the admitted source run and snapshot")
        receipt = validate(snapshot, services.policy, plan, incumbent=load_incumbent(services.repositories, plan.incumbent_id), kind="FINAL",
                           created_at=services.clock.now())
        persist_receipt(services.repositories, receipt, plan)
        out[purpose] = {"candidate_id": plan.id, "validation_id": receipt.id, "valid": receipt.valid, "violations": len(receipt.violations),
                        "required_skips": list(receipt.required_skips)}
    if not out["EXECUTABLE"]["valid"]:
        raise StepFailed("EXECUTABLE_INVALID", f"{out['EXECUTABLE']['violations']} violations: nothing is staged or published")
    return out


def step_stage_readback(services: Any, doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from .publication.service import publish

    out: dict[str, Any] = {}
    for purpose, item in inputs["validate"].items():
        if not item["valid"]:
            out[purpose] = {"status": "NOT_STAGED", "reason": "invalid candidate"}
            continue
        result = publish(services, item["candidate_id"], until="readback")
        if result.status not in ("STAGED", "ACCEPTED", "RECOVERED", "ALREADY_ACCEPTED"):
            raise StepFailed("STAGE_REJECTED", f"{purpose}: {result.status} {list(result.reasons)[:3]}")
        out[purpose] = {"candidate_id": item["candidate_id"], "status": result.status, "stage_id": result.stage_id,
                        "readback_validation_id": result.validations.get("READBACK"), "plan_id": result.plan_id}
    return out


def step_sign_accept(services: Any, doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from .publication.service import publish

    out: dict[str, Any] = {}
    for purpose, item in inputs["stage_readback"].items():
        if item.get("status") == "NOT_STAGED":
            out[purpose] = {"status": "NOT_PUBLISHED"}
            continue
        if item.get("status") == "STAGED" and not item.get("readback_validation_id"):
            raise StepFailed("READBACK_MISSING", f"{purpose}: no readback validation was recorded; nothing is signed")
        result = publish(services, item["candidate_id"])
        if result.status not in ("ACCEPTED", "RECOVERED", "ALREADY_ACCEPTED"):
            raise StepFailed("ACCEPTANCE_REFUSED", f"{purpose}: {result.status} {list(result.reasons)[:3]}")
        out[purpose] = {"status": result.status, "plan_id": result.plan_id, "head_version": result.head_version}
    if out.get("EXECUTABLE", {}).get("plan_id") is None:
        raise StepFailed("NO_EXECUTABLE_PLAN", "the executable plan was not accepted")
    return out


def step_observe_evaluate(services: Any, doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Collect and evaluate only against the frozen goals of the plan this pipeline accepted (with its admitted coverage)."""
    from .pipeline import observe_and_evaluate
    from .publication.service import current_plan

    current, why = current_plan(services, doc["scope"], "EXECUTABLE")
    accepted = inputs["sign_accept"]["EXECUTABLE"]["plan_id"]
    if current is None or current.published.id != accepted:
        raise StepFailed("PLAN_NOT_CURRENT", f"the current executable plan is {current.published.id if current else why}, not {accepted}")
    if services.settings.mode != "fixture":
        raise StepFailed("LIVE_OBSERVATIONS_UNAVAILABLE", "live observation producers are not installed (OD-15); evaluation stays UNKNOWN")
    collections, recognition = observe_and_evaluate(services, doc["request"]["scenario"])
    return {"plan_id": accepted, "points": [str(c["as_of"]) for c in collections],
            "collected": sum(1 for c in collections if "observation" in c), "skipped": sum(1 for c in collections if "skipped" in c),
            "evaluations": sum(len(c.get("evaluations", [])) for c in collections), "recognition": recognition}


# ---------------------------------------------------------------------- CLI


def cli_pipeline(services: Any, args: Any) -> dict[str, Any]:
    """``pipeline --key K [--role R] [--step S] [--status]``: run a role's ready steps (``all`` in one process) or show state."""
    key = getattr(args, "key", None) or "default"
    role = getattr(args, "role", None) or "all"
    if role not in ROLES + ("all",):
        return {"status": "invalid", "error": {"code": "BAD_ROLE", "message": f"role must be one of {ROLES + ('all',)}"}}
    pid = pipeline_id(services.settings.scope_key, key)
    if getattr(args, "status", False):
        return status(services, pid)
    create(services, key=key, scenario=getattr(args, "scenario", None), budget_seconds=getattr(args, "budget_seconds", None),
           max_moves=getattr(args, "max_moves", None))
    lease = float(getattr(args, "lease_seconds", 900.0) or 900.0)
    if getattr(args, "step", None):
        step = args.step
        if step not in BY_NAME:
            return {"status": "invalid", "error": {"code": "BAD_STEP", "message": f"step must be one of {sorted(BY_NAME)}"}}
        results = [run_step(services, pid, step, role=BY_NAME[step].role if role == "all" else role, lease_seconds=lease)]
    else:
        results = run_role(services, pid, role, lease_seconds=lease)
    state = status(services, pid)
    bad = [r for r in results if r["outcome"] in ("FAILED", "ROLE_MISMATCH", "EXHAUSTED")]
    progressed = any(r["outcome"] in ("COMPLETED", "COMPLETED_AFTER_TAKEOVER", "ALREADY_COMPLETED") for r in results)
    waiting = any(r["outcome"] in ("BLOCKED", "LEASED", "CONTENDED", "LEASE_LOST") for r in results)
    # a role that only waited is not a success: CI must not show a green signer job that signed nothing
    outcome = "error" if bad else ("blocked" if waiting and not progressed else "ok")
    return {**state, "status": outcome, "ran": [{k: v for k, v in r.items() if k != "output"} for r in results]}
