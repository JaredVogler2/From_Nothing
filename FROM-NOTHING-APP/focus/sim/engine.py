"""One shift's admission and plan, in memory, through the production builder, optimizer and validator.

``admit`` captures every dataset from the simulated source system at the cutoff and runs the production snapshot builder
(``build_snapshot``: census, admission, durations, eligibility, pools, calendars, station windows, parts, graph,
economics). It skips only the file-store round trip of ``acquire`` (store each dataset, read it back, re-hash it, store
the snapshot and read it again): the builder's input and output are the same, and the snapshot is sealed once.

``plan`` runs the rich MAX optimizer with the previous shift's published plan as the incumbent (three-day commitment
defense), builds the candidate and validates it independently against the snapshot and that incumbent, all checks
required. Nothing here writes to the application's record store; the simulation tables keep the results.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from ..codec import content_hash
from ..contracts.planning import CandidatePlan, IncumbentPlan, PlanningSnapshot
from ..data.datasets import DATASETS
from ..data.snapshot_builder import build_snapshot
from ..data.sourcetime import policy_from_receipts
from ..domain import Purpose, digest_id
from ..engine.commitments import defense_for
from ..engine.max.optimizer.orchestrator import MaxOptimizer, MaxRun, build_candidate
from ..engine.model import EngineContext
from ..engine.native import status as native_status
from ..publication.service import incumbent_from
from ..validation.plan import validate
from .state import SourceState


def admit(services: Any, state: SourceState, *, cutoff: datetime, run_id: str, created_at: datetime) -> tuple[PlanningSnapshot, Any]:
    settings, policy = services.settings, services.policy
    tz_map = dict(settings.source_timezone_map)
    reader_tz = tz_map if tz_map else {"*": "America/New_York"}
    datasets, lineage, statuses, receipts, ids, queries = {}, {}, {}, {}, {}, {}
    for name, spec in DATASETS.items():
        cap = state.capture(spec, cutoff, reader_tz, created_at)
        datasets[name] = cap.rows
        lineage[name] = state.lineage(cap)
        statuses[name] = cap.status
        receipts[name] = cap.receipt
        ids[name] = f"{run_id}.{name}"
        queries[name] = cap.query_sha
    tzp = policy_from_receipts(receipts, tz_map, settings.mode)
    manifest = {"run_id": run_id, "scope": settings.scope_key, "cutoff": cutoff, "policy_hash": policy.hash,
                "datasets": {n: {"content_sha": lineage[n], "rows": len(datasets[n]), "status": statuses[n]} for n in DATASETS}}
    build = build_snapshot(run_id=run_id, scope_key=settings.scope_key, site=settings.site, program=settings.program, mode=settings.mode,
                           cutoff=cutoff, created_at=created_at, datasets=datasets, dataset_status=statuses, dataset_ids=ids,
                           dataset_lineage=lineage, manifest_hash=content_hash(manifest), tzp=tzp, policy=policy, dataset_queries=queries)
    return build.snapshot, build


@dataclass
class ShiftPlan:
    purpose: Purpose
    plan: CandidatePlan
    run: MaxRun
    incumbent: IncumbentPlan | None
    context: EngineContext
    validate_s: float = 0.0                     # the FINAL independent validation's wall time


def plan(services: Any, snapshot: PlanningSnapshot, purpose: Purpose, *, incumbent: IncumbentPlan | None, max_moves: int,
         budget_seconds: float, created_at: datetime, max_repairs: int = 20) -> ShiftPlan:
    policy = services.policy
    native = bool(getattr(services.settings, "native_core", False))
    ctx = EngineContext(snapshot, policy, purpose, native=native)
    defense = (defense_for(ctx, incumbent, status="SIMULATED", detail="the previous shift's valid plan, accepted by the simulation without the "
                           "stage/readback/sign protocol (synthetic demonstration)") if incumbent else None)
    optimizer = MaxOptimizer(ctx, defended=defense.defended if defense else None, budget_seconds=budget_seconds, max_moves=max_moves,
                             incumbent_id=defense.incumbent_id if defense else None, max_repairs=max_repairs)
    run = optimizer.run()
    run.native = native_status(native)
    run.validated, run.validator_rejections = optimizer._gate_count, list(optimizer._gate_log)
    candidate = build_candidate(ctx, run, created_at=created_at, incumbent_id=defense.incumbent_id if defense else None,
                                extra_commitments=defense.other_results if defense else (), incumbent=defense.receipt() if defense else None)
    began = time.perf_counter()
    run.validation = validate(snapshot, policy, candidate, incumbent=incumbent, kind="FINAL", inp=optimizer._vinp, created_at=created_at)
    return ShiftPlan(purpose, candidate, run, incumbent, ctx, time.perf_counter() - began)


def published_incumbent(snapshot: PlanningSnapshot, shift_plan: ShiftPlan, accepted_at: datetime) -> IncumbentPlan:
    """The published plan as the next shift's incumbent (real people only), built while its snapshot is in memory."""
    published = SimpleNamespace(id=digest_id("SIMPUB", shift_plan.plan.id), scope=shift_plan.plan.scope, purpose=shift_plan.purpose,
                                accepted_at=accepted_at)
    return incumbent_from(snapshot, published, shift_plan.plan, shift_plan.context.shift_policy.tz)
