"""MaxOptimizer: the one real scheduling entrypoint (S12 MAX PIPELINE, P13).

1. Verify the immutable snapshot and build one purpose context: inventory, graph, calendars,
   pools, eligibility, CS facts and releases.
2. Load the defended commitments from the accepted incumbent (P14); physical carry-in is
   locked in the ledger.
3. Build a named neutral seed over all aircraft sharing pool capacity (``greedy.Decoder``,
   CPM tails as urgency).
4. Project aircraft completions and derive marginal priority weights through
   ``EconomicsRegistry``, then redispatch economically.
5. Run bounded delay repair for resource-deferred jobs.
6. Search seeded neighbourhoods (swap, boost, aircraft advance), stopping at the move bound,
   the wall-clock budget or cancellation.
7. Keep a candidate only when it is strictly better under the exact lexicographic comparison
   (S12), with no monetary tolerance, and it keeps every commitment the current best keeps
   (a later move can never silently undo a feasible defended task). The independent validator
   (``focus.validation.plan``) gates the seed and every retained candidate: an invalid candidate
   is rolled back, and any valid candidate beats an invalid best. The levels are:

   0. hard feasibility: independent validation with zero violations;
   1. commitments defended (same slot), then exact incumbent crews;
   2. aircraft with unknown completion;
   3. deferred phases;
   4. exact controllable modeled penalty (known and conditional aircraft);
   5. travel plus wait minutes, then overtime;
   6. crew-continuity breaks;
   7. deterministic signature.

Weights never decide acceptance. Every candidate is a full decode through the shared ledger,
so a move cannot bypass a constraint. The independent validator (P15) re-checks the final
plan from source. The run receipt is truthful: a heuristic, so ``global_optimality_proven``
is false.

8. Optional, default off (P22): a CP-SAT pooled relaxation proposes job orders that the same
   named decoder, validator and exact comparison judge (``solver.improve``). Its typed status is
   recorded in every receipt (``baseline_comparison["solver"]``); it never yields a plan itself.
"""
from __future__ import annotations

import random
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from fractions import Fraction
from typing import Any, Callable, Mapping

from ....codec import to_jsonable
from ....contracts.planning import (AlgorithmStatus, Assignment, CandidatePlan, CostSummary, CrewRole, Duty, JobOutcome, StageMetric,
                                    seal_candidate)
from ....domain import Disposition, DutyKind, Interval, PhaseKind, Purpose, digest_id, from_minute
from ....economics.evaluator import JobRelease, aircraft_completions, line_costs, summarize
from ....economics.registry import EconomicsRegistry
from ...model import EngineContext
from ...projections import completion_minutes
from .continuity import continuity_breaks
from .greedy import DecodeResult, Decoder, DefendedTask
from .neighborhoods import Move, PriorityState, generate
from .repair import repairable_jobs

ALGORITHM = "FOCUS-rich-MAX"
VERSION = "1.0"
DEFENDED = frozenset({"KEPT", "CREW_CHANGED"})
# the exact comparison key, level by level (``Evaluation.key``); the signature only breaks ties deterministically
KEY_LEVELS = ("commitments_defended", "commitments_exact", "unknown_completion_aircraft", "deferred_phases", "controllable_known_cents",
              "travel_plus_wait_minutes", "ot_minutes", "continuity_breaks", "signature")


@dataclass
class Evaluation:
    decode: DecodeResult
    releases: dict[str, JobRelease]
    completions: dict
    costs: dict
    summary: CostSummary
    defended: int                       # KEPT or CREW_CHANGED: the committed slot holds
    exact: int                          # KEPT: exact slot and exact full crew
    travel_minutes: int
    wait_minutes: int
    ot_minutes: int
    breaks: int
    signature: str
    key: tuple = ()


@dataclass
class MaxRun:
    baseline: Evaluation
    best: Evaluation
    weights: dict
    stages: list[StageMetric] = field(default_factory=list)
    deadline_reached: bool = False
    moves: int = 0
    accepted_moves: list[str] = field(default_factory=list)
    seed_valid: bool = True
    best_valid: bool = True
    validated: int = 0                   # retained candidates (seed included) independently validated in search
    validator_rejections: list[str] = field(default_factory=list)
    validation: Any = None               # final ValidationReceipt (schedule)
    solver: dict[str, Any] = field(default_factory=dict)   # optional CP-SAT stage receipt (P22); DISABLED by default
    native: dict[str, Any] = field(default_factory=dict)   # which ledger/crew core ran; never part of the candidate


def make_priority(ctx: EngineContext, state: PriorityState, weights: Mapping[int, Fraction] | None,
                  fixed: Mapping[str, int] | None = None) -> Callable[[str], tuple]:
    """Order within a dispatch class; the decoder puts ready commitments and their prerequisites first.

    ``fixed`` holds planned task priorities of an isolated scenario (job -> negative rank). They lead the key, so
    search moves cannot reorder them, and they act only among ready phases: graph, parts, QA, crews and defended
    commitments still decide what may start.
    """
    fixed = fixed or {}

    def key(pid: str) -> tuple:
        phase = ctx.phases[pid]
        weight = (weights or {}).get(phase.line, Fraction(0))
        return (fixed.get(phase.job, 0), state.ranks.get(phase.job, 0.0), -weight, -ctx.tails.get(pid, 0), pid)
    return key


class MaxOptimizer:
    def __init__(self, ctx: EngineContext, *, defended: Mapping[str, DefendedTask] | None = None, budget_seconds: float | None = None,
                 seed: int | None = None, max_moves: int | None = None, cancel: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic, gate: bool = True, incumbent_id: str | None = None,
                 fixed_ranks: Mapping[str, int] | None = None):
        self.ctx = ctx
        self.fixed_ranks = dict(fixed_ranks or {})
        self.gate = gate
        self.incumbent_id = incumbent_id
        self._vinp = None
        self._gate_log: list[str] = []
        self._gate_count = 0
        self.registry = EconomicsRegistry(ctx.economics, ctx.policy)
        self.defended = dict(defended or {})
        self.budget = float(budget_seconds if budget_seconds is not None else ctx.policy.int("optimizer.budget_seconds"))
        self.seed = int(seed if seed is not None else ctx.policy.int("optimizer.seed"))
        self.max_moves = int(max_moves if max_moves is not None else ctx.policy.int("optimizer.max_neighborhood_moves"))
        self.cancel = cancel or (lambda: False)
        self.clock = clock
        self.lines = sorted({j.line for j in ctx.jobs.values()})
        placeable = [j for j in ctx.jobs if ctx.placeability(j).placeable]
        self.team_jobs: dict[str, list[str]] = defaultdict(list)
        self.line_jobs: dict[int, list[str]] = defaultdict(list)
        for job in placeable:
            for p in ctx.job_phases[job]:
                for d in p.role_demands:
                    if job not in self.team_jobs[d.team]:
                        self.team_jobs[d.team].append(job)
            self.line_jobs[ctx.jobs[job].line].append(job)
        self.peers = {job: sorted({o for jobs in self.team_jobs.values() if job in jobs for o in jobs if o != job}) for job in placeable}

    # ------------------------------------------------------------------ evaluation
    def decode(self, state: PriorityState, weights: Mapping[int, Fraction] | None) -> DecodeResult:
        return Decoder(self.ctx, make_priority(self.ctx, state, weights, self.fixed_ranks), defended=self.defended).run()

    def evaluate(self, decode: DecodeResult) -> Evaluation:
        ctx = self.ctx
        releases: dict[str, JobRelease] = {}
        for job_id, phases in ctx.job_phases.items():
            rel = next((p for p in phases if p.kind is PhaseKind.RELEASE), None)
            if rel is None:
                continue
            placed = decode.placements.get(rel.key.id)
            if placed is not None:
                cond = any(decode.placements[p.key.id].conditional for p in phases if p.key.id in decode.placements)
                releases[job_id] = JobRelease(job_id, placed.end, cond, "released")
            elif rel.key.id in decode.satisfied:
                releases[job_id] = JobRelease(job_id, decode.satisfied[rel.key.id], False, "verified complete")
            else:
                reasons = [decode.deferred[p.key.id] for p in phases if p.key.id in decode.deferred]
                releases[job_id] = JobRelease(job_id, None, False, reasons[0] if reasons else "not released")
        completions = aircraft_completions(ctx.jobs.values(), ctx.dispositions, releases, self.lines)
        costs = line_costs(self.registry, completions, ctx.cutoff_minute)
        summary = summarize(costs)
        travel = wait = ot = 0
        for item in decode.ledger.all_items():
            window = ctx.windows.get((item.person, item.shift_id))
            if item.kind is DutyKind.TRAVEL:
                travel += item.minutes
            elif item.kind is DutyKind.WAIT:
                wait += item.minutes
            if window is not None:
                ot += sum(max(0, min(item.end, b) - max(item.start, a)) for a, b in window.ot)
        defended = sum(1 for r in decode.commitment_results if r.outcome in DEFENDED)
        exact = sum(1 for r in decode.commitment_results if r.outcome == "KEPT")
        breaks = sum(continuity_breaks(decode.placements).values())
        signature = digest_id("SIG", sorted((p.phase_id, p.start, p.crew) for p in decode.placements.values()))
        ev = Evaluation(decode, releases, completions, costs, summary, defended, exact, travel, wait, ot, breaks, signature)
        ev.key = (-defended, -exact, len(summary.unknown_lines), len(decode.deferred), summary.controllable_known_cents, travel + wait, ot,
                  breaks, signature)
        return ev

    @staticmethod
    def improved_level(candidate: Evaluation, best: Evaluation) -> str | None:
        """The first key level where ``candidate`` is strictly better than ``best`` (None when it is not better)."""
        for name, a, b in zip(KEY_LEVELS, candidate.key, best.key):
            if a != b:
                return name if a < b else None
        return None

    @staticmethod
    def better(candidate: Evaluation, best: Evaluation) -> bool:
        """Strictly better under the exact key and no commitment kept by ``best`` is lost or downgraded."""
        if not candidate.key < best.key:
            return False
        outcome = {r.incumbent_assignment_id: r.outcome for r in candidate.decode.commitment_results}
        for r in best.decode.commitment_results:
            if r.outcome == "KEPT" and outcome.get(r.incumbent_assignment_id) != "KEPT":
                return False
            if r.outcome == "CREW_CHANGED" and outcome.get(r.incumbent_assignment_id) not in DEFENDED:
                return False
        return True

    # ------------------------------------------------------------------ pipeline
    def run(self) -> MaxRun:
        start = self.clock()

        def stage(name: str, began: float, candidates: int, accepted: int, note: str) -> StageMetric:
            return StageMetric(name, int((self.clock() - began) * 1000), candidates, accepted, note)

        stages: list[StageMetric] = []
        t = self.clock()
        neutral = PriorityState()
        baseline = self.evaluate(self.decode(neutral, None))
        seed_valid = self._valid(baseline, "neutral_seed")
        stages.append(stage("neutral_seed", t, 1, 1, f"{len(baseline.decode.placements)} placed, {len(baseline.decode.deferred)} deferred; "
                                                    f"independent validation {'passed' if seed_valid else 'FAILED'}"))
        best, best_state, best_valid = baseline, neutral, seed_valid
        t = self.clock()
        weight_records = self.registry.derive_priority_weights(completion_minutes(baseline.completions))
        weights = {line: w.normalized for line, w in weight_records.items()}
        econ = self.evaluate(self.decode(neutral, weights))
        accepted = self._admit(econ, best, best_valid, "economic_redispatch")
        if accepted:
            best, best_valid = econ, True
        stages.append(stage("economic_redispatch", t, 1, int(accepted), "registry marginal weights; exact complete-candidate comparison"))
        run = MaxRun(baseline, best, {line: w for line, w in weight_records.items()}, stages, seed_valid=seed_valid)
        t = self.clock()
        phase_job = {pid: p.job for pid, p in self.ctx.phases.items()}
        repaired = 0
        for job in repairable_jobs(best.decode.deferred, phase_job)[:20]:
            if self._out_of_time(start):
                run.deadline_reached = True
                break
            state = best_state.apply(Move("boost", (job,), "repair"), self.peers)
            cand = self.evaluate(self.decode(state, weights))
            if self._admit(cand, best, best_valid, "delay_repair"):
                best, best_state, best_valid = cand, state, True
                repaired += 1
                run.accepted_moves.append(f"repair boost {job}")
        stages.append(stage("delay_repair", t, len(repairable_jobs(baseline.decode.deferred, phase_job)), repaired, "bounded boosts"))
        t = self.clock()
        rng = random.Random(self.seed)
        moves = accepted_n = 0
        while moves < self.max_moves:
            if self._out_of_time(start):
                run.deadline_reached = True
                break
            focus = repairable_jobs(best.decode.deferred, phase_job) + sorted(j for j, n in continuity_breaks(best.decode.placements).items() if n)
            move = generate(rng, team_jobs=self.team_jobs, line_jobs=self.line_jobs, focus_jobs=focus)
            if move is None:
                break
            moves += 1
            state = best_state.apply(move, self.peers)
            cand = self.evaluate(self.decode(state, weights))
            if self._admit(cand, best, best_valid, f"neighborhoods:{move.kind}"):
                best, best_state, best_valid = cand, state, True
                accepted_n += 1
                run.accepted_moves.append(f"{move.kind} {','.join(move.jobs)}")
        stages.append(stage("neighborhoods", t, moves, accepted_n, f"seed {self.seed}; bound {self.max_moves} moves, {self.budget}s; "
                                                                  f"{self._gate_count} candidates independently validated, "
                                                                  f"{len(self._gate_log)} rolled back"))
        run.best, run.moves, run.stages, run.best_valid = best, moves, stages, best_valid
        run.validated, run.validator_rejections = self._gate_count, list(self._gate_log)
        return run

    # ------------------------------------------------------------------ independent validation gate
    def _admit(self, cand: Evaluation, best: Evaluation, best_valid: bool, path: str) -> bool:
        """Validity first (S12 level 0), then the exact key: an invalid candidate is rolled back on every path."""
        if best_valid and not self.better(cand, best):
            return False
        return self._valid(cand, path)

    def _valid(self, ev: Evaluation, path: str) -> bool:
        if not self.gate:
            return True
        from ....validation.input import ValidationInput      # the validator never imports the engine; the engine calls it
        from ....validation.plan import GATE_CHECKS, validate

        if self._vinp is None:
            self._vinp = ValidationInput.build(self.ctx.snapshot, self.ctx.policy, self.ctx.purpose)
        plan = build_candidate(self.ctx, MaxRun(ev, ev, {}), created_at=self.ctx.snapshot.cutoff, incumbent_id=self.incumbent_id)
        receipt = validate(self.ctx.snapshot, self.ctx.policy, plan, required=GATE_CHECKS, inp=self._vinp)
        self._gate_count += 1
        if receipt.violations:
            codes = sorted({f"{v.check}:{v.code}" for v in receipt.violations})
            self._gate_log.append(f"{path}: {', '.join(codes[:6])}")
            return False
        return True

    def _out_of_time(self, start: float) -> bool:
        return self.cancel() or (self.clock() - start) >= self.budget


# ---------------------------------------------------------------------- candidate assembly

def _segments(segs) -> tuple[Interval, ...]:
    merged: list[list[int]] = []
    for a, b, _ in segs:
        if merged and merged[-1][1] == a:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return tuple(Interval.of_minutes(a, b) for a, b in merged)


def build_candidate(ctx: EngineContext, run: MaxRun, *, created_at: datetime, incumbent_id: str | None = None,
                    extra_commitments: tuple = (), incumbent: Mapping[str, str] | None = None) -> CandidatePlan:
    best = run.best
    decode = best.decode
    snapshot = ctx.snapshot
    availability = {(a.person, a.pool): a.id for a in snapshot.availability}
    holdings: dict[str, list] = defaultdict(list)
    for h in snapshot.holdings:
        holdings[h.person].append(h)
    assignments: list[Assignment] = []
    for p in sorted(decode.placements.values(), key=lambda x: (x.start, x.phase_id)):
        if not p.crew or p.assignment_id is None:
            continue
        phase = ctx.phases[p.phase_id]
        demand = phase.role_demands[0]
        codes = set(demand.bundle.all_codes)
        crew = tuple(CrewRole(person, demand.bundle.role,
                              tuple(sorted({e.row_id for h in holdings.get(person, []) if h.cert in codes for e in h.evidence})),
                              availability.get((person, p.pool_id), "")) for person in p.crew)
        releases = tuple(sorted(e.pred_phase for e in ctx.preds.get(p.phase_id, ()) if e.pred_phase in ctx.releases))
        disp = ctx.dispositions.get(p.job)
        assignments.append(Assignment(
            id=p.assignment_id, phase=p.phase_id, job=p.job, line=p.line, kind=p.kind, start=from_minute(p.start), end=from_minute(p.end),
            segments=_segments(p.segments), work_minutes=phase.work_minutes, shift_id=p.shift_id, pool_key=p.pool_id, crew=crew,
            purpose=ctx.purpose, predecessor_release_ids=releases,
            duration_estimate_id=ctx.durations[p.job].id if (p.kind is PhaseKind.WORK and p.job in ctx.durations) else None,
            conditional_reasons=tuple(disp.executable_reasons) if (p.conditional and disp) else (),
            committed=p.committed, commitment_outcome=p.commitment_outcome))
    duties: list[Duty] = []
    for item in decode.ledger.all_items():
        window = ctx.windows.get((item.person, item.shift_id))
        ot = sum(max(0, min(item.end, b) - max(item.start, a)) for a, b in window.ot) if window else 0
        duties.append(Duty(item.id, item.person, from_minute(item.start), from_minute(item.end), item.kind,
                           item.origin_line if item.kind is DutyKind.TRAVEL else item.line, item.line, item.assignment_id, item.shift_id,
                           item.minutes - ot, ot))
    outcomes: list[JobOutcome] = []
    for job_id in sorted(ctx.jobs):
        disp = ctx.dispositions[job_id]
        phases = ctx.job_phases.get(job_id, [])
        placed = [decode.placements[p.key.id] for p in phases if p.key.id in decode.placements]
        if disp.disposition is Disposition.ADMITTED:
            if phases and len(placed) == len(phases):
                cond = any(p.conditional for p in placed)
                state = Disposition.CONDITIONAL if cond else Disposition.SCHEDULED
                reasons = tuple(disp.executable_reasons) if cond else tuple(disp.reasons)
            else:
                state = Disposition.DEFERRED
                reasons = tuple(dict.fromkeys(decode.deferred[p.key.id] for p in phases if p.key.id in decode.deferred))
        else:
            state, reasons = disp.disposition, tuple(disp.reasons)
        starts = [p.start for p in placed if p.crew]
        ends = [p.end for p in placed]
        outcomes.append(JobOutcome(job_id, state, reasons, from_minute(min(starts)) if starts else None,
                                   from_minute(max(ends)) if ends and state in (Disposition.SCHEDULED, Disposition.CONDITIONAL) else None,
                                   state is Disposition.CONDITIONAL))
    base, fin = run.baseline.summary, best.summary
    status = AlgorithmStatus(
        name=ALGORITHM, version=VERSION, seed=ctx.policy.int("optimizer.seed"), budget_seconds=float(ctx.policy.int("optimizer.budget_seconds")),
        deadline_reached=run.deadline_reached, proof_status="HEURISTIC_NO_PROOF", stages=tuple(run.stages),
        weights={str(line): str(w.normalized) for line, w in sorted(run.weights.items())},
        baseline_comparison=to_jsonable({
            "baseline_known_cents": base.known_total_cents, "baseline_conditional_cents": base.conditional_total_cents,
            "baseline_unknown_lines": list(base.unknown_lines), "final_known_cents": fin.known_total_cents,
            "final_conditional_cents": fin.conditional_total_cents, "final_unknown_lines": list(fin.unknown_lines),
            "baseline_deferred_phases": len(run.baseline.decode.deferred), "final_deferred_phases": len(decode.deferred),
            "final_travel_minutes": best.travel_minutes, "final_wait_minutes": best.wait_minutes, "final_ot_minutes": best.ot_minutes,
            "moves": run.moves, "accepted_moves": run.accepted_moves, "complete_fleet_cost": fin.complete,
            "solver": run.solver or None,
        }),
        paths_used=tuple(sorted(decode.paths_used | {"economics_registry", "exact_evaluator", "neighborhoods", "delay_repair"}
                                | ({"cpsat_relaxation_order"} if run.solver.get("model") else set()))),
        incumbent=dict(incumbent or {"id": incumbent_id or "", "status": "NOT_LOOKED_UP", "detail": "no incumbent lookup was requested",
                                     "defended": "0"}))
    plan = CandidatePlan(
        id="", snapshot_id=snapshot.id,
        snapshot_hash=snapshot.hash, policy_hash=ctx.policy.hash, source_run_id=snapshot.source_run_id, scope=snapshot.scope_key,
        purpose=ctx.purpose, created_at=created_at, incumbent_id=incumbent_id, assignments=tuple(assignments), duties=tuple(duties),
        job_dispositions=tuple(outcomes), aircraft_completion=tuple(best.completions[k] for k in sorted(best.completions)),
        costs=fin, commitment_results=tuple(decode.commitment_results) + tuple(extra_commitments), algorithm_status=status,
        global_optimality_proven=False, hash="")
    # substance-addressed: same snapshot, purpose, policy, placements, commitment outcomes and incumbent receipt give the
    # same id; wall-clock stage timings are not substance. Any substantive difference is a new id (no write-once collision).
    plan = replace(plan, id=digest_id("CAND", snapshot.id, ctx.purpose.value, ctx.policy.hash, best.signature,
                                      to_jsonable(list(plan.commitment_results)), to_jsonable(status.incumbent)))
    return seal_candidate(plan)


def receipt(plan: CandidatePlan) -> dict[str, Any]:
    census: dict[str, int] = defaultdict(int)
    for o in plan.job_dispositions:
        census[o.disposition.value] += 1
    return to_jsonable({
        "candidate_id": plan.id, "candidate_hash": plan.hash, "snapshot_id": plan.snapshot_id, "source_run_id": plan.source_run_id,
        "policy_hash": plan.policy_hash, "purpose": plan.purpose.value, "assignments": len(plan.assignments), "duties": len(plan.duties),
        "disposition_census": dict(sorted(census.items())), "jobs_total": len(plan.job_dispositions),
        "costs": {"known_cents": plan.costs.known_total_cents, "conditional_cents": plan.costs.conditional_total_cents,
                  "controllable_known_cents": plan.costs.controllable_known_cents, "unknown_lines": list(plan.costs.unknown_lines),
                  "complete": plan.costs.complete, "currency": plan.costs.currency},
        "incumbent": plan.algorithm_status.incumbent,
        "commitments": [{"job": c.job, "day_offset": c.day_offset, "outcome": c.outcome, "attempt1": c.attempt1, "attempt2": c.attempt2,
                         "reason": c.reason} for c in plan.commitment_results],
        "algorithm": {"name": plan.algorithm_status.name, "version": plan.algorithm_status.version, "proof": plan.algorithm_status.proof_status,
                      "stages": [{"stage": s.stage, "ms": s.runtime_ms, "candidates": s.candidates, "accepted": s.accepted} for s in plan.algorithm_status.stages],
                      "weights": plan.algorithm_status.weights, "baseline_comparison": plan.algorithm_status.baseline_comparison},
        "global_optimality_proven": plan.global_optimality_proven,
        "deferred": [{"job": o.job, "reasons": list(o.reasons)[:3]} for o in plan.job_dispositions if o.disposition is Disposition.DEFERRED],
    })


CANDIDATES = "candidates"


def schedule(services: Any, *, source_run: str, purpose: Purpose, budget_seconds: float | None = None,
             max_moves: int | None = None, solver: bool | None = None) -> tuple[CandidatePlan, MaxRun]:
    from ....data.acquisition import snapshot_for_run

    snapshot = snapshot_for_run(services, source_run, services.policy)
    return schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=budget_seconds, max_moves=max_moves, solver=solver)


def schedule_snapshot(services: Any, snapshot: Any, *, purpose: Purpose, policy: Any = None, budget_seconds: float | None = None,
                      max_moves: int | None = None, fixed_ranks: Mapping[str, int] | None = None,
                      cancel: Callable[[], bool] | None = None, solver: bool | None = None,
                      solver_budget_seconds: float | None = None) -> tuple[CandidatePlan, MaxRun]:
    """Rich MAX over one sealed snapshot (admitted or an isolated scenario copy), then FINAL validation; persists the candidate.

    ``fixed_ranks`` (scenario planned task priorities) and ``cancel`` (a cooperative stop) are for SCENARIO runs only.
    ``solver`` forces the optional CP-SAT stage on or off; None follows policy ``solver.enabled``, then FOCUS_SOLVER_ENABLED.
    """
    from ...commitments import defended_tasks
    from ....domain import DomainError

    if fixed_ranks and purpose is not Purpose.SCENARIO:
        raise DomainError("PRIORITY_SCENARIO_ONLY", "planned task priorities are scenario inputs, never executable or forecast settings")
    policy = policy or services.policy
    from ...native import status as native_status

    requested = bool(getattr(services.settings, "native_core", False))
    ctx = EngineContext(snapshot, policy, purpose, native=requested)
    defense = defended_tasks(services, ctx)
    optimizer = MaxOptimizer(ctx, defended=defense.defended, budget_seconds=budget_seconds, max_moves=max_moves, incumbent_id=defense.incumbent_id,
                             fixed_ranks=fixed_ranks, cancel=cancel)
    run = optimizer.run()
    from .solver import budgets, enabled_by, improve

    switch = enabled_by(policy, services.settings, solver)
    began = time.monotonic()
    outcome = improve(optimizer, run, enabled=switch, bounds=budgets(policy, solver_budget_seconds) if switch else None,
                      cancel=cancel or (lambda: False))
    run.solver = outcome.receipt()
    run.native = native_status(requested)
    run.validated, run.validator_rejections = optimizer._gate_count, list(optimizer._gate_log)
    if switch:
        run.stages.append(StageMetric("solver", int((time.monotonic() - began) * 1000), len(outcome.probes),
                                      int(outcome.status == "IMPROVED"), f"{outcome.status}: {outcome.reason}"))
    plan = build_candidate(ctx, run, created_at=services.clock.now(), incumbent_id=defense.incumbent_id, extra_commitments=defense.other_results,
                           incumbent=defense.receipt())
    stored = services.repositories.get_typed(CANDIDATES, plan.id, "CandidatePlan", CandidatePlan)
    if stored is not None:
        plan = stored                          # the same substance was already scheduled: the persisted candidate is the receipt
    else:
        services.repositories.put_record(CANDIDATES, "CandidatePlan", plan.id, plan, source_run_id=plan.source_run_id,
                                         policy_hash=plan.policy_hash, purpose=plan.purpose.value)
    from ....validation.plan import load_incumbent, persist_receipt, validate

    run.validation = validate(snapshot, policy, plan, incumbent=load_incumbent(services.repositories, plan.incumbent_id), kind="FINAL",
                              inp=optimizer._vinp, created_at=services.clock.now())
    persist_receipt(services.repositories, run.validation, plan)
    return plan, run


def worker_schedule(services: Any, run: Any) -> dict[str, Any]:
    """POST /schedule-runs handler: a named rich-MAX run plus FINAL validation; never signs, accepts or promotes."""
    from ....domain import DomainError
    from ....validation.plan import receipt_summary

    req = run.request
    if req.get("policy_id") != services.policy.id:
        raise DomainError("POLICY_MISMATCH", f"active policy is {services.policy.id}, not {req.get('policy_id')}")
    budget = min(float(req.get("budget_seconds", run.budget_seconds)), float(run.budget_seconds))
    plan, mrun = schedule(services, source_run=str(req.get("source_run_id", "")), purpose=Purpose(req.get("purpose", "EXECUTABLE")),
                          budget_seconds=budget)
    return {"status": "scheduled", **receipt(plan), "validation": receipt_summary(mrun.validation, limit=20),
            "publishable": mrun.validation.valid, "published": False, "solver": mrun.solver, "engine": mrun.native,
            "note": "a candidate is never executable; signing and promotion stay in the trusted publication worker or CLI"}


def cli_schedule(services: Any, args: Any) -> dict[str, Any]:
    if args.policy != services.policy.id:
        return {"status": "invalid", "error": {"code": "POLICY_MISMATCH", "message": f"active policy is {services.policy.id}, not {args.policy}"}}
    from ....validation.plan import receipt_summary

    plan, run = schedule(services, source_run=args.source_run, purpose=Purpose(args.purpose), budget_seconds=args.budget_seconds)
    return {"status": "ok", **receipt(plan), "validation": receipt_summary(run.validation, limit=20), "publishable": run.validation.valid,
            "search_validation": {"validated": run.validated, "rolled_back": run.validator_rejections, "seed_valid": run.seed_valid},
            "solver": run.solver, "engine": run.native, "published": False}
