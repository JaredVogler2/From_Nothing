"""MaxOptimizer: the one real scheduling entrypoint (S12 MAX PIPELINE, P13).

1. Verify the immutable snapshot and build one purpose context: inventory, graph, calendars,
   pools, eligibility, CS facts and releases.
2. Load the defended commitments from the accepted incumbent (P14); physical carry-in is
   locked in the ledger.
3. Build a named neutral seed over all aircraft sharing pool capacity (``greedy.Decoder``,
   CPM tails as urgency).
4. Redispatch economically by the cost of delaying each phase (``urgency.cost_of_delay``: the
   aircraft's daily penalty, decayed by the phase's slack in the seed). The marginal weights of
   ``EconomicsRegistry`` are still recorded in the receipt. Defended commitments and their
   prerequisites, which the decoder dispatches first, keep the seed's order in every candidate
   (redispatch, repairs, search moves), so a candidate keeps the seed's commitment outcomes and is
   judged on the uncommitted work it reorders. A commitment that loses its slot is requeued with
   the candidate's own priority, so its minimal move still follows the cost of delay.
5. Run bounded delay repair for resource-deferred jobs.
6. Search seeded neighbourhoods (swap, boost, aircraft advance; half of the moves boost the
   jobs with the longest waits when there are any), stopping at the move bound, the wall-clock
   budget or cancellation. Only the move bound keeps a run repeatable: the receipt's
   ``stop_reason`` says which one ended the search. Moves are drawn and judged in exactly the
   sequential order, but up to ``SPECULATION`` of them are decoded at once from the same best (on
   threads with the native core, whose search releases the GIL): the draws a sequential run would
   make while no move is accepted. After an accepted move, the rest of the batch is discarded
   and drawing resumes from the random state that followed the accepted move.
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

**Repair** (``mode="repair"``) replaces steps 3-6 when an accepted incumbent exists: one decode keeps
every incumbent placement whose slot and crew still hold (``commitments.repair_hints``), after the
defended commitments, and fills new and changed work around them in neutral critical-path order; a
second decode redispatches that repair by the cost of delay (step 4), and the exact comparison keeps
the better. It re-plans the whole horizon, so a shift's shortfall still moves delivery dates and
penalties, but it keeps the accepted plan wherever it still holds. The FINAL validation gates it like any plan; a repair that finds no
incumbent, or whose plan fails validation, falls back to the full run. ``mode="auto"`` (the pipeline
default) repairs between daily full solves (``planning_mode``), and each scheduled full solve also
decodes the repair and records how far it landed from the full plan (``repair_drift``). The receipt's
``planning`` entry says what was asked, what ran and why.
"""
from __future__ import annotations

import random
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Mapping

from ....codec import native_codec, to_jsonable
from ....contracts.planning import (AlgorithmStatus, Assignment, CandidatePlan, CostSummary, CrewRole, Duty, JobOutcome, StageMetric,
                                    seal_candidate)
from ....domain import Disposition, DutyKind, Interval, PhaseKind, Purpose, digest_id, from_minute
from ....economics.evaluator import JobRelease, aircraft_completions, line_costs, summarize
from ....economics.registry import EconomicsRegistry
from ...model import EngineContext
from ...projections import completion_minutes
from .continuity import continuity_breaks
from .greedy import DecodeResult, Decoder, DefendedTask
from .neighborhoods import Competition, Move, PriorityState, generate
from .repair import repairable_jobs
from .urgency import cost_of_delay, long_waits, urgency_summary

SPECULATION = 3              # search moves decoded at once from the same best (identical results to one at a time)

ALGORITHM = "FOCUS-rich-MAX"
VERSION = "1.0"
DEFENDED = frozenset({"KEPT", "CREW_CHANGED"})
# The exact comparison key follows the policy's lexicographic order (``objective.order``, S12): each level contributes
# these fields. FEASIBILITY is the independent validation every accepted candidate passes, so it adds none; raw job
# counts never outrank money (the deferred-phase count is a last backlog tie); the signature only breaks ties.
OBJECTIVE_FIELDS = {
    "FEASIBILITY": (),
    "COMMITMENT_DEFENSE": ("commitments_defended", "commitments_exact"),
    "CONTROLLABLE_COST": ("unknown_completion_aircraft", "controllable_known_cents"),
    "NVA_MINUTES": ("travel_plus_wait_minutes",),
    "OT_AND_CHURN": ("ot_minutes", "continuity_breaks"),
    "SCARCITY_AFFINITY_GATE": ("deferred_phases",),
}

KEY_LEVELS = tuple(f for fields in OBJECTIVE_FIELDS.values() for f in fields) + ("signature",)   # every key field; signature last


def key_levels(policy: Any) -> tuple[str, ...]:
    order = tuple(policy.seq("objective.order"))
    if sorted(order) != sorted(OBJECTIVE_FIELDS):
        raise ValueError(f"objective.order must order exactly {sorted(OBJECTIVE_FIELDS)}, got {list(order)}")
    return tuple(f for level in order for f in OBJECTIVE_FIELDS[level]) + ("signature",)


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
    levels: tuple[str, ...] = ()                # the field name of each key element, in order
    unknown: frozenset[int] = frozenset()       # aircraft whose completion (so whose cost) is unknown


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
    urgency: dict[str, int] = field(default_factory=dict)  # phase -> cost of delay used by the economic passes
    stop_reason: str = "move_bound"                        # move_bound | no_moves | repair (repeatable) or deadline | cancelled
    planning: dict[str, Any] = field(default_factory=dict) # requested and resolved planning mode, why, repair counts, drift check


def make_priority(ctx: EngineContext, state: PriorityState, urgency: Mapping[str, int] | None,
                  fixed: Mapping[str, int] | None = None) -> Callable[[str], tuple]:
    """Order within a dispatch class; the decoder puts ready commitments and their prerequisites first.

    ``urgency`` is the cost of delaying each phase (``urgency.cost_of_delay``; None in the neutral seed, which orders by
    CPM tail alone). ``fixed`` holds planned task priorities of an isolated scenario (job -> negative rank). They lead
    the key, so search moves cannot reorder them, and they act only among ready phases: graph, parts, QA, crews and
    defended commitments still decide what may start.
    """
    fixed = fixed or {}
    urgency = urgency or {}

    def key(pid: str) -> tuple:
        phase = ctx.phases[pid]
        return (fixed.get(phase.job, 0), state.ranks.get(phase.job, 0.0), -urgency.get(pid, 0), -ctx.tails.get(pid, 0), pid)
    return key


class MaxOptimizer:
    def __init__(self, ctx: EngineContext, *, defended: Mapping[str, DefendedTask] | None = None, budget_seconds: float | None = None,
                 seed: int | None = None, max_moves: int | None = None, cancel: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic, gate: bool = True, incumbent_id: str | None = None,
                 fixed_ranks: Mapping[str, int] | None = None, max_repairs: int = 20,
                 hints: Mapping[str, DefendedTask] | None = None, repair: bool = False):
        self.ctx = ctx
        self.hints = dict(hints or {})                 # repair: incumbent slots kept where they still hold
        self.repair = repair
        self.fixed_ranks = dict(fixed_ranks or {})
        self.gate = gate
        self.incumbent_id = incumbent_id
        self._vinp = None
        self._gate_log: list[str] = []
        self._gate_count = 0
        self.registry = EconomicsRegistry(ctx.economics, ctx.policy)
        self.key_levels = key_levels(ctx.policy)
        self.defended = dict(defended or {})
        self.budget = float(budget_seconds if budget_seconds is not None else ctx.policy.int("optimizer.budget_seconds"))
        self.seed = int(seed if seed is not None else ctx.policy.int("optimizer.seed"))
        self.max_moves = int(max_moves if max_moves is not None else ctx.policy.int("optimizer.max_neighborhood_moves"))
        self.cancel = cancel or (lambda: False)
        self.clock = clock
        self.max_repairs = max_repairs                 # delay-repair boosts (each a full re-decode)
        self.lines = sorted({j.line for j in ctx.jobs.values()})
        placeable = [j for j in ctx.jobs if ctx.placeability(j).placeable]
        self.team_jobs: dict[str, list[str]] = defaultdict(list)
        self.line_jobs: dict[int, list[str]] = defaultdict(list)
        listed: set[tuple[str, str]] = set()
        for job in placeable:
            for p in ctx.job_phases[job]:
                for d in p.role_demands:
                    if (d.team, job) not in listed:
                        listed.add((d.team, job))
                        self.team_jobs[d.team].append(job)
            self.line_jobs[ctx.jobs[job].line].append(job)
        self.peers = Competition(self.team_jobs)
        self.workers = SPECULATION if ctx.native else 1    # threads overlap only when the search runs without the GIL
        # only work that is not verified complete gets a release from a candidate or can delay an aircraft
        # (aircraft_completions skips verified-complete jobs), so evaluating a candidate keeps pace with open work
        self.open_jobs = [job for job in ctx.jobs.values() if not ctx.placeability(job.id).satisfied]
        self.open_releases: list[tuple[str, list[Any], Any]] = []
        for job_id, phases in ctx.job_phases.items():
            rel = next((p for p in phases if p.kind is PhaseKind.RELEASE), None)
            if rel is not None and not ctx.placeability(job_id).satisfied:
                self.open_releases.append((job_id, phases, rel))

    # ------------------------------------------------------------------ evaluation
    def decode(self, state: PriorityState, urgency: Mapping[str, int] | None) -> DecodeResult:
        # committed work (defended phases and their prerequisites) is dispatched in the seed's order by every candidate
        return Decoder(self.ctx, make_priority(self.ctx, state, urgency, self.fixed_ranks), defended=self.defended,
                       committed=make_priority(self.ctx, PriorityState(), None, self.fixed_ranks), hints=self.hints).run()

    def _decode_all(self, states: list[PriorityState], urgency: Mapping[str, int] | None) -> list[Evaluation]:
        """Decode and evaluate each state, results in order. With the native core the decodes run on threads: the Rust
        search releases the GIL, each decode has its own ledger, and the shared context is only read."""
        if self.workers <= 1 or len(states) <= 1:
            return [self.evaluate(self.decode(state, urgency)) for state in states]
        with ThreadPoolExecutor(max_workers=min(self.workers, len(states))) as pool:
            return list(pool.map(lambda state: self.evaluate(self.decode(state, urgency)), states))

    def evaluate(self, decode: DecodeResult) -> Evaluation:
        ctx = self.ctx
        releases: dict[str, JobRelease] = {}
        for job_id, phases, rel in self.open_releases:
            placed = decode.placements.get(rel.key.id)
            if placed is not None:
                cond = any(decode.placements[p.key.id].conditional for p in phases if p.key.id in decode.placements)
                releases[job_id] = JobRelease(job_id, placed.end, cond, "released")
            elif rel.key.id in decode.satisfied:
                releases[job_id] = JobRelease(job_id, decode.satisfied[rel.key.id], False, "verified complete")
            else:
                reasons = [decode.deferred[p.key.id] for p in phases if p.key.id in decode.deferred]
                releases[job_id] = JobRelease(job_id, None, False, reasons[0] if reasons else "not released")
        completions = aircraft_completions(self.open_jobs, ctx.dispositions, releases, self.lines)
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
        fields = {"commitments_defended": -defended, "commitments_exact": -exact, "unknown_completion_aircraft": len(summary.unknown_lines),
                  "controllable_known_cents": summary.controllable_known_cents, "travel_plus_wait_minutes": travel + wait, "ot_minutes": ot,
                  "continuity_breaks": breaks, "deferred_phases": len(decode.deferred), "signature": signature}
        ev.key = tuple(fields[f] for f in self.key_levels)
        ev.levels, ev.unknown = self.key_levels, frozenset(summary.unknown_lines)
        return ev

    @staticmethod
    def improved_level(candidate: Evaluation, best: Evaluation) -> str | None:
        """The first key level where ``candidate`` is strictly better than ``best`` (None when it is not better)."""
        for name, a, b in zip(getattr(candidate, "levels", ()) or KEY_LEVELS, candidate.key, best.key):
            if a != b:
                return name if a < b else None
        return None

    @staticmethod
    def better(candidate: Evaluation, best: Evaluation) -> bool:
        """Strictly better under the exact key, no commitment kept by ``best`` is lost or downgraded, and a cost decided at
        or after the cost level compares the same aircraft: a candidate whose unknown aircraft differ is better only by
        making some of best's unknown aircraft known and losing none (S12: never cheaper by ignoring missing lines)."""
        level = MaxOptimizer.improved_level(candidate, best)
        if level is None:
            return False
        levels = getattr(candidate, "levels", ()) or KEY_LEVELS
        mine, theirs = getattr(candidate, "unknown", frozenset()), getattr(best, "unknown", frozenset())
        if level in levels[levels.index("unknown_completion_aircraft"):] and mine != theirs and not mine < theirs:
            return False
        outcome = {r.incumbent_assignment_id: r.outcome for r in candidate.decode.commitment_results}
        for r in best.decode.commitment_results:
            if r.outcome == "KEPT" and outcome.get(r.incumbent_assignment_id) != "KEPT":
                return False
            if r.outcome == "CREW_CHANGED" and outcome.get(r.incumbent_assignment_id) not in DEFENDED:
                return False
        return True

    # ------------------------------------------------------------------ pipeline
    def _run_repair(self) -> MaxRun:
        """Repair the incumbent in two decodes, the better of which is the plan.

        1. Keep each incumbent placement whose slot and crew still hold and place new and changed work around them in
           neutral order.
        2. The full solve's cost-of-delay redispatch on that repair: incumbent slots still lead, and new, changed and
           re-placed work is dispatched by the cost of delaying it (``urgency.cost_of_delay``, from the first decode).

        The exact comparison keeps the second only when it is strictly better. No delay repair or search, and no
        in-search validation: the FINAL validation gates the plan."""
        def summary(d: DecodeResult) -> str:
            return (f"{d.kept} of {len(self.hints)} incumbent placements kept at their slot and crew, {d.rehomed} re-placed; "
                    f"{len(d.placements)} placed, {len(d.deferred)} deferred")

        t = self.clock()
        neutral = PriorityState()
        repaired = self.evaluate(self.decode(neutral, None))
        weight_records = self.registry.derive_priority_weights(completion_minutes(repaired.completions))
        urgency = cost_of_delay(self.ctx, self.registry, repaired.decode, weight_records)
        t_econ = self.clock()
        econ = self.evaluate(self.decode(neutral, urgency))
        accepted = self.better(econ, repaired)
        best = econ if accepted else repaired
        stages = [StageMetric("repair", int((t_econ - t) * 1000), 1, 1, summary(repaired.decode)),
                  StageMetric("repair_redispatch", int((self.clock() - t_econ) * 1000), 1, int(accepted),
                              f"{urgency_summary(urgency)}; {summary(econ.decode)}; exact complete-candidate comparison")]
        run = MaxRun(repaired, best, dict(weight_records), stages, urgency=dict(urgency) if accepted else {})
        run.stop_reason = "repair"
        return run

    def run(self) -> MaxRun:
        if self.repair:
            return self._run_repair()
        start = self.clock()

        def stage(name: str, began: float, candidates: int, accepted: int, note: str) -> StageMetric:
            return StageMetric(name, int((self.clock() - began) * 1000), candidates, accepted, note)

        stages: list[StageMetric] = []
        t = self.clock()
        neutral = PriorityState()
        baseline = self.evaluate(self.decode(neutral, None))
        seed_decoded = self.clock()
        weight_records = self.registry.derive_priority_weights(completion_minutes(baseline.completions))
        urgency = cost_of_delay(self.ctx, self.registry, baseline.decode, weight_records)      # dispatch through the Registry weights
        timed: dict[str, float] = {}

        def validate_seed() -> bool:
            began = self.clock()
            try:
                return self._valid(baseline, "neutral_seed")
            finally:
                timed["seed_validation"] = self.clock() - began

        t_econ = self.clock()
        if self.workers > 1:
            # the seed's independent validation runs while the redispatch decodes (the native search releases the GIL);
            # the redispatch is judged only after the seed's verdict, exactly as in sequence
            with ThreadPoolExecutor(max_workers=1) as pool:
                verdict = pool.submit(validate_seed)
                econ = self.evaluate(self.decode(neutral, urgency))
                seed_valid = verdict.result()
        else:
            seed_valid = validate_seed()
            t_econ = self.clock()
            econ = self.evaluate(self.decode(neutral, urgency))
        econ_decoded = self.clock()
        overlap = "; validated while the redispatch decoded" if self.workers > 1 else ""
        stages.append(StageMetric("neutral_seed", int((seed_decoded - t + timed["seed_validation"]) * 1000), 1, 1,
                                  f"{len(baseline.decode.placements)} placed, {len(baseline.decode.deferred)} deferred; "
                                  f"independent validation {'passed' if seed_valid else 'FAILED'}{overlap}"))
        best, best_state, best_valid = baseline, neutral, seed_valid
        accepted = self._admit(econ, best, best_valid, "economic_redispatch")
        best_urgency: Mapping[str, int] | None = None              # the order the best candidate was decoded with
        if accepted:
            best, best_valid, best_urgency = econ, True, urgency
        stages.append(StageMetric("economic_redispatch", int((econ_decoded - t_econ + self.clock() - econ_decoded) * 1000), 1, int(accepted),
                                  f"{urgency_summary(urgency)}; exact complete-candidate comparison"))
        run = MaxRun(baseline, best, {line: w for line, w in weight_records.items()}, stages, seed_valid=seed_valid,
                     urgency=dict(best_urgency or {}))
        t = self.clock()
        phase_job = {pid: p.job for pid, p in self.ctx.phases.items()}
        repaired = 0
        for job in repairable_jobs(best.decode.deferred, phase_job)[:self.max_repairs]:
            if self._out_of_time(start):
                run.deadline_reached = True
                run.stop_reason = "cancelled" if self.cancel() else "deadline"
                break
            state = best_state.apply(Move("boost", (job,), "repair"), self.peers)
            cand = self.evaluate(self.decode(state, best_urgency))
            if self._admit(cand, best, best_valid, "delay_repair"):
                best, best_state, best_valid = cand, state, True
                repaired += 1
                run.accepted_moves.append(f"repair boost {job}")
        stages.append(stage("delay_repair", t, len(repairable_jobs(baseline.decode.deferred, phase_job)), repaired, "bounded boosts"))
        t = self.clock()
        rng = random.Random(self.seed)
        moves = accepted_n = 0
        waits = long_waits(best.decode)
        while moves < self.max_moves:
            if self._out_of_time(start):
                run.deadline_reached = True
                run.stop_reason = "cancelled" if self.cancel() else "deadline"
                break
            # the next draws of a sequential run while no move is accepted: same best, same focus, the rng in order
            focus = repairable_jobs(best.decode.deferred, phase_job) + sorted(j for j, n in continuity_breaks(best.decode.placements).items() if n)
            batch: list[tuple[Move, Any]] = []
            exhausted = False
            while len(batch) < min(SPECULATION, self.max_moves - moves):
                move = generate(rng, team_jobs=self.team_jobs, line_jobs=self.line_jobs, focus_jobs=focus, wait_jobs=waits)
                if move is None:
                    exhausted = True
                    break
                batch.append((move, rng.getstate()))
            states = [best_state.apply(move, self.peers) for move, _ in batch]
            accepted = False
            for (move, after), state, cand in zip(batch, states, self._decode_all(states, best_urgency)):
                moves += 1
                if self._admit(cand, best, best_valid, f"neighborhoods:{move.kind}"):
                    best, best_state, best_valid = cand, state, True
                    accepted_n += 1
                    run.accepted_moves.append(f"{move.kind} {','.join(move.jobs)}")
                    waits = long_waits(best.decode)
                    rng.setstate(after)                 # later draws in the batch came from the old best: redraw them
                    accepted = True
                    break
            if exhausted and not accepted:
                run.stop_reason = "no_moves"
                break
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
    evidence: dict[tuple[str, frozenset[str]], tuple[str, ...]] = {}      # (person, bundle codes) -> holding rows

    def held(person: str, codes: frozenset[str]) -> tuple[str, ...]:
        found = evidence.get((person, codes))
        if found is None:
            found = evidence[(person, codes)] = tuple(sorted({e.row_id for h in holdings.get(person, []) if h.cert in codes for e in h.evidence}))
        return found

    for p in sorted(decode.placements.values(), key=lambda x: (x.start, x.phase_id)):
        if not p.crew or p.assignment_id is None:
            continue
        phase = ctx.phases[p.phase_id]
        demand = phase.role_demands[0]
        codes = frozenset(demand.bundle.all_codes)
        crew = tuple(CrewRole(person, demand.bundle.role, held(person, codes), availability.get((person, p.pool_id), "")) for person in p.crew)
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
            "stop_reason": run.stop_reason, "repeatable": run.stop_reason in ("move_bound", "no_moves", "repair"),
            **({"planning": run.planning} if run.planning else {}),
            "long_wait_jobs": len(long_waits(best.decode)), "cost_of_delay_phases": len(run.urgency),
            "solver": run.solver or None,
        }),
        paths_used=tuple(sorted(decode.paths_used | {"economics_registry", "exact_evaluator"}
                                | (set() if run.stop_reason == "repair" else {"neighborhoods", "delay_repair"})
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


RUN_REFERENCES = frozenset({"availability_id", "pool_key", "duration_estimate_id", "evidence", "source_run_id", "snapshot_id"})


def decision_digest(plan: CandidatePlan) -> str:
    """Hash of what the plan decides, without references to the source run it was read from.

    Assignments (phase, start, end, segments, shift, people, conditions, commitment outcome), duties, job dispositions
    with reasons, aircraft completions, costs and commitment results. Ids that every acquisition mints afresh
    (availability, pool and duration-estimate references, evidence, source run and snapshot ids) and the run's stage
    timings are left out, so the same constraints and open work give the same digest in every run (repeatability).
    """
    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in RUN_REFERENCES}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    record = {"assignments": plan.assignments, "duties": plan.duties, "dispositions": plan.job_dispositions,
              "completions": plan.aircraft_completion, "costs": plan.costs, "commitments": plan.commitment_results}
    canon = native_codec()
    if canon is not None:
        try:
            return canon.digest_excluding("DEC", record, RUN_REFERENCES, 64)     # the same bytes, keys dropped while writing
        except Exception:  # noqa: BLE001 - the Python path gives the same digest or raises the exact error
            pass
    return digest_id("DEC", strip(to_jsonable(record)), length=64)


def receipt(plan: CandidatePlan) -> dict[str, Any]:
    census: dict[str, int] = defaultdict(int)
    for o in plan.job_dispositions:
        census[o.disposition.value] += 1
    return to_jsonable({
        "candidate_id": plan.id, "candidate_hash": plan.hash, "decision_digest": decision_digest(plan), "snapshot_id": plan.snapshot_id,
        "source_run_id": plan.source_run_id,
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


SCHEDULE_MODES = ("full", "repair", "auto")


def schedule(services: Any, *, source_run: str, purpose: Purpose, budget_seconds: float | None = None,
             max_moves: int | None = None, solver: bool | None = None, mode: str = "full") -> tuple[CandidatePlan, MaxRun]:
    from ....data.acquisition import snapshot_for_run

    snapshot = snapshot_for_run(services, source_run, services.policy)
    return schedule_snapshot(services, snapshot, purpose=purpose, budget_seconds=budget_seconds, max_moves=max_moves, solver=solver,
                             mode=mode)


def planning_mode(ctx: EngineContext, defense: Any, requested: str) -> tuple[str, str]:
    """The mode a run uses, and why. ``auto`` (the pipeline default) repairs the accepted plan between daily full solves.
    A run plans the shift that contains its cutoff plus ``planning.cutoff_lead_minutes`` (a pipeline runs shortly before
    each shift). ``auto`` runs a full solve when that shift is one of ``planning.full_solve_shifts``, when there is no
    accepted incumbent, or when the incumbent's cutoff is more than ``planning.repair_max_incumbent_age_hours`` old."""
    if requested != "auto":
        return requested, f"{requested} requested"
    from ....calendar import shift_containing

    policy = ctx.policy
    shift = shift_containing(ctx.cutoff_minute + policy.int("planning.cutoff_lead_minutes"), ctx.shift_policy, ctx.site)
    full_shifts = sorted(int(n) for n in policy.seq("planning.full_solve_shifts"))
    if shift is None or shift.number in full_shifts:
        where = shift.label if shift is not None else "no shift"
        return "full", f"the run plans {where}: the daily full solve runs for shifts {full_shifts} (planning.full_solve_shifts)"
    if defense.plan is None:
        return "full", f"no accepted incumbent to repair ({defense.status})"
    age = (ctx.snapshot.cutoff - defense.plan.previous_cutoff).total_seconds() / 3600
    limit = policy.int("planning.repair_max_incumbent_age_hours")
    if age > limit:
        return "full", f"the incumbent's cutoff is {age:.1f} h old, over planning.repair_max_incumbent_age_hours ({limit})"
    return "repair", f"the run plans {shift.label} and the incumbent's cutoff is {age:.1f} h old"


def repair_drift(repaired: Evaluation, full: Evaluation) -> dict[str, Any]:
    """How far a repair of the incumbent lands from the full solve on the same snapshot (the daily drift check).
    Differences are repair minus full: a positive cost means the repair would have cost more. Costs compare only when
    both leave the same aircraft with unknown completion."""
    moved, worst = 0, 0.0
    for line in sorted(set(full.completions) | set(repaired.completions)):
        a = full.completions[line].completion_at if line in full.completions else None
        b = repaired.completions[line].completion_at if line in repaired.completions else None
        if a != b:
            moved += 1
            if a is not None and b is not None:
                worst = max(worst, abs((b - a).total_seconds()) / 86400)
    rs, fs = repaired.summary, full.summary
    return {"repair_controllable_known_cents": rs.controllable_known_cents, "full_controllable_known_cents": fs.controllable_known_cents,
            "controllable_delta_cents": rs.controllable_known_cents - fs.controllable_known_cents,
            "costs_comparable": set(rs.unknown_lines) == set(fs.unknown_lines),
            "repair_unknown_aircraft": len(rs.unknown_lines), "full_unknown_aircraft": len(fs.unknown_lines),
            "repair_deferred_phases": len(repaired.decode.deferred), "full_deferred_phases": len(full.decode.deferred),
            "aircraft_completion_moved": moved, "max_completion_days_moved": round(worst, 2)}


def schedule_snapshot(services: Any, snapshot: Any, *, purpose: Purpose, policy: Any = None, budget_seconds: float | None = None,
                      max_moves: int | None = None, fixed_ranks: Mapping[str, int] | None = None,
                      cancel: Callable[[], bool] | None = None, solver: bool | None = None,
                      solver_budget_seconds: float | None = None, mode: str = "full") -> tuple[CandidatePlan, MaxRun]:
    """Rich MAX over one sealed snapshot (admitted or an isolated scenario copy), then FINAL validation; persists the candidate.

    ``fixed_ranks`` (scenario planned task priorities) and ``cancel`` (a cooperative stop) are for SCENARIO runs only.
    ``solver`` forces the optional CP-SAT stage on or off; None follows policy ``solver.enabled``, then FOCUS_SOLVER_ENABLED.
    ``mode="repair"`` keeps the accepted incumbent's placements where they still hold, re-places what changed and
    redispatches by the cost of delay: two decodes, no search. Without an incumbent, or when the repaired plan fails FINAL validation, it runs the full solve.
    ``mode="auto"`` picks one by ``planning_mode``; its scheduled full solve also measures how far a repair would have
    landed (``repair_drift``, policy ``planning.drift_check``). The receipt's ``planning`` entry records what was asked,
    what ran and why (``mode="full"``, the default for direct calls, records nothing, so its candidate is unchanged).
    """
    from ...commitments import defended_tasks
    from ....domain import DomainError

    if mode not in SCHEDULE_MODES:
        raise DomainError("SCHEDULE_MODE_UNKNOWN", f"mode must be one of {list(SCHEDULE_MODES)}, not {mode!r}")
    if fixed_ranks and purpose is not Purpose.SCENARIO:
        raise DomainError("PRIORITY_SCENARIO_ONLY", "planned task priorities are scenario inputs, never executable or forecast settings")
    policy = policy or services.policy
    requested = bool(getattr(services.settings, "native_core", False))
    ctx = EngineContext(snapshot, policy, purpose, native=requested)
    defense = defended_tasks(services, ctx)
    return plan_by_mode(ctx, defense, mode, lambda hints, planning, reference: _solve(
        services, ctx, defense, budget_seconds=budget_seconds, max_moves=max_moves, fixed_ranks=fixed_ranks, cancel=cancel, solver=solver,
        solver_budget_seconds=solver_budget_seconds, requested=requested, planning=planning, hints=hints, reference=reference),
        fixed_ranks=fixed_ranks)


def plan_by_mode(ctx: EngineContext, defense: Any, mode: str, solve: Callable[..., Any], *, fixed_ranks: Mapping[str, int] | None = None,
                 max_repairs: int = 20) -> Any:
    """Run ``solve(hints, planning, reference)`` as the mode decides; its result is ``(candidate, run, ...)``.

    A repair is ``solve`` with the incumbent's hints; when its FINAL validation fails, the full solve replaces it. The full
    solve is ``solve`` without hints. An ``auto`` full solve with an incumbent also gets ``reference``: the repair it
    replaces (its evaluation and seconds), so the receipt can record the drift. ``planning`` says what was asked, what
    ran and why; a plain ``full`` request records nothing."""
    from ...commitments import repair_hints

    ran, why = planning_mode(ctx, defense, mode)
    planning = {"requested": mode, "ran": ran, "reason": why} if mode != "full" else {}
    reference = None
    if ran == "repair":
        if defense.plan is None:
            planning.update(ran="full", reason=f"REPAIR_NO_INCUMBENT: {defense.status}: {defense.detail}")
        else:
            result = solve(repair_hints(ctx, defense.plan, defense.defended), planning, None)
            candidate, validation = result[0], result[1].validation
            if validation.valid:
                return result
            codes = sorted({f"{v.check}:{v.code}" for v in validation.violations})
            planning = {**planning, "ran": "full",
                        "reason": f"REPAIR_INVALID: repaired candidate {candidate.id} failed FINAL validation with "
                                  f"{len(validation.violations)} violations ({', '.join(codes[:4]) or 'required checks skipped'})"}
    elif mode == "auto" and defense.plan is not None and ctx.policy.bool("planning.drift_check"):
        began = time.monotonic()
        repaired = MaxOptimizer(ctx, defended=defense.defended, incumbent_id=defense.incumbent_id, fixed_ranks=fixed_ranks,
                                max_repairs=max_repairs, hints=repair_hints(ctx, defense.plan, defense.defended), repair=True).run().best
        reference = (repaired, time.monotonic() - began)
    return solve(None, planning, reference)


def record_planning(run: MaxRun, planning: Mapping[str, Any], hints: Mapping[str, DefendedTask] | None,
                    reference: tuple[Evaluation, float] | None) -> None:
    """The run's planning receipt: the mode decision, a repair's counts, and the drift check against the repair."""
    run.planning = dict(planning)
    if hints is not None:
        d = run.best.decode
        run.planning.update(hints=len(hints), kept=d.kept, rehomed=d.rehomed, deferred=sum(1 for pid in hints if pid in d.deferred))
    if reference is not None:
        run.planning["drift"] = {**repair_drift(reference[0], run.best), "repair_seconds": round(reference[1], 3)}


def _solve(services: Any, ctx: EngineContext, defense: Any, *, budget_seconds: float | None, max_moves: int | None,
           fixed_ranks: Mapping[str, int] | None, cancel: Callable[[], bool] | None, solver: bool | None,
           solver_budget_seconds: float | None, requested: bool, planning: Mapping[str, Any],
           hints: Mapping[str, DefendedTask] | None = None, reference: tuple[Evaluation, float] | None = None) -> tuple[CandidatePlan, MaxRun]:
    """One planning run (a repair when ``hints`` is given, else the full solve), the candidate persisted, then FINAL
    validation. ``reference`` is the drift check's repair (its evaluation and seconds), compared with the full plan."""
    from ...native import status as native_status

    snapshot, policy, repair = ctx.snapshot, ctx.policy, hints is not None
    optimizer = MaxOptimizer(ctx, defended=defense.defended, budget_seconds=budget_seconds, max_moves=max_moves, incumbent_id=defense.incumbent_id,
                             fixed_ranks=fixed_ranks, cancel=cancel, hints=hints, repair=repair)
    run = optimizer.run()
    record_planning(run, planning, hints, reference)
    from .solver import budgets, enabled_by, improve

    switch = "" if repair else enabled_by(policy, services.settings, solver)
    began = time.monotonic()
    outcome = improve(optimizer, run, enabled=switch, bounds=budgets(policy, solver_budget_seconds) if switch else None,
                      cancel=cancel or (lambda: False),
                      off_reason="a repair keeps the accepted plan where it holds; the optional solver runs only in a full solve" if repair else None)
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
                          budget_seconds=budget, mode=str(req.get("planning", "full")))
    return {"status": "scheduled", **receipt(plan), "validation": receipt_summary(mrun.validation, limit=20),
            "publishable": mrun.validation.valid, "published": False, "solver": mrun.solver, "engine": mrun.native, "planning": mrun.planning,
            "note": "a candidate is never executable; signing and promotion stay in the trusted publication worker or CLI"}


def cli_schedule(services: Any, args: Any) -> dict[str, Any]:
    if args.policy != services.policy.id:
        return {"status": "invalid", "error": {"code": "POLICY_MISMATCH", "message": f"active policy is {services.policy.id}, not {args.policy}"}}
    from ....validation.plan import receipt_summary

    plan, run = schedule(services, source_run=args.source_run, purpose=Purpose(args.purpose), budget_seconds=args.budget_seconds,
                         mode=getattr(args, "planning", None) or "full")
    return {"status": "ok", **receipt(plan), "validation": receipt_summary(run.validation, limit=20), "publishable": run.validation.valid,
            "search_validation": {"validated": run.validated, "rolled_back": run.validator_rejections, "seed_valid": run.seed_valid},
            "solver": run.solver, "engine": run.native, "planning": run.planning, "published": False}
