"""Named ready-frontier list scheduling over the complete WIP (S12 MAX steps 4-6, P13).

``decode`` places every placeable phase of every aircraft in one shared duty ledger, in
topological order with a priority key supplied by the orchestrator (neutral seed,
economic redispatch or a neighbourhood perturbation). For each phase it does the following.

* **Floors.** The earliest start is the maximum of the cutoff, the job's sourced earliest,
  the same-run CS earliest, every hard typed predecessor (FS/SS on the start, FF/SF on the
  end, signed lag kept) and every part-release floor allowed for the purpose.
* **Shift scan.** Only the line's eligible shifts within admitted calendar bounds are
  scanned (no extension). Candidate starts are the floor and the instants when pool members
  finish a duty, plus or minus a transfer. Each start must be a valid start (no-start cutoff
  with equality). Regular-only placement is tried before overtime.
* **Crew.** Exactly the demanded full crew is booked through the ledger (transfers, budget,
  windows, activation, tools). A phase that cannot be placed waits with a verified cause;
  its successors wait too ("PREDECESSOR_UNPLACED"). There is no fallback crew, cap
  relaxation or precedence voiding.
* **Waiting crew.** With ``qa.crew_during_ip = WAIT``, the production crew of the segment
  before an in-process inspection holds WAIT duty until the inspection ends. This is booked
  atomically with the inspector.
* **Defended commitments.** Ready commitments are dispatched first and the phases that must
  precede a commitment come second, ahead of all other work. Each defended phase is tried in
  two attempts: first the exact start and full crew, then the same start with an alternate
  full crew. A commitment that fails both, or that a safety fact invalidated, drops to the
  ordinary class, so feasible commitments go first. It then gets a minimal audited move or is
  deferred. Every outcome is recorded.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field, replace
from typing import Callable, Mapping

from ....contracts.planning import CommitmentResult, Phase
from ....domain import DutyKind, EdgeType, PhaseKind, digest_id, from_minute, minute_ceil
from ...crew_selection import choose_crew
from ...ledger import DutyLedger, LedgerError
from ...model import EngineContext

MAX_STARTS_PER_SHIFT = 48
LEAD_DEFENDED, LEAD_CONE, LEAD_OTHER = 0, 1, 2       # dispatch classes ahead of the caller's priority key
REQUEUE = "DEFENSE_FAILED_REQUEUED"


@dataclass(frozen=True)
class DefendedTask:
    """A committed incumbent phase inside the commitment horizon (day offsets 0..horizon-1)."""

    phase_id: str
    incumbent_assignment_id: str
    shift_id: str
    start: int
    crew: tuple[str, ...]
    day_offset: int


@dataclass(frozen=True)
class PhasePlacement:
    phase_id: str
    job: str
    line: int
    kind: PhaseKind
    start: int
    end: int
    segments: tuple[tuple[int, int, bool], ...]
    shift_id: str | None
    pool_id: str | None
    crew: tuple[str, ...]
    assignment_id: str | None
    conditional: bool
    committed: bool
    commitment_outcome: str | None
    notes: tuple[str, ...] = ()


@dataclass
class DecodeResult:
    placements: dict[str, PhasePlacement] = field(default_factory=dict)
    deferred: dict[str, str] = field(default_factory=dict)
    satisfied: dict[str, int] = field(default_factory=dict)          # verified-complete phase -> minute
    milestones: dict[str, int] = field(default_factory=dict)         # release milestone -> floor minute
    commitment_results: list[CommitmentResult] = field(default_factory=list)
    ledger: DutyLedger | None = None
    paths_used: set[str] = field(default_factory=set)
    attempts: int = 0


def _times(result: DecodeResult, node: str) -> tuple[int, int] | None:
    p = result.placements.get(node)
    if p is not None:
        return p.start, p.end
    if node in result.satisfied:
        t = result.satisfied[node]
        return t, t
    if node in result.milestones:
        t = result.milestones[node]
        return t, t
    return None


def _floors(ctx: EngineContext, result: DecodeResult, phase: Phase) -> tuple[int, int, list[str], bool]:
    start_floor, notes = ctx.job_floor(phase.job)
    end_floor = 0
    conditional = False
    for e in ctx.preds.get(phase.key.id, ()):
        times = _times(result, e.pred_phase)
        if times is None:          # unresolved predecessors are handled by the caller (never satisfied by absence)
            continue
        ps, pe = times
        if e.type is EdgeType.FS:
            start_floor = max(start_floor, pe + e.lag_minutes)
        elif e.type is EdgeType.SS:
            start_floor = max(start_floor, ps + e.lag_minutes)
        elif e.type is EdgeType.FF:
            end_floor = max(end_floor, pe + e.lag_minutes)
        elif e.type is EdgeType.SF:
            end_floor = max(end_floor, ps + e.lag_minutes)
        if e.pred_phase in ctx.releases and e.pred_phase in result.milestones:
            _, cond, _ = ctx.release_floor(e.pred_phase)
            conditional = conditional or cond
        pred = result.placements.get(e.pred_phase)
        if pred is not None and pred.conditional:
            conditional = True
    return start_floor, end_floor, notes, conditional


def _candidate_starts(ctx: EngineContext, ledger: DutyLedger, shift, pool_id: str, first: int) -> list[int]:
    travel = ctx.travel.changed_aircraft_minutes
    points = {first, shift.cutoff_instant}
    for m in ctx.pool_members(pool_id):
        for x in ledger.free_points(m, shift.id):
            points.add(x)
            points.add(x + travel)
    out = sorted(t for t in points if first <= t <= shift.cutoff_instant)
    return out[:MAX_STARTS_PER_SHIFT]


class Decoder:
    def __init__(self, ctx: EngineContext, priority: Callable[[str], tuple], *, defended: Mapping[str, DefendedTask] | None = None):
        self.ctx = ctx
        self.priority = priority
        self.defended = dict(defended or {})
        self._cone = self._commitment_cone()
        self._requeued: set[str] = set()

    def _commitment_cone(self) -> frozenset[str]:
        """Every hard-edge ancestor of a defended phase: it must be dispatched before the commitment can be ready."""
        seen: set[str] = set()
        stack = list(self.defended)
        while stack:
            for e in self.ctx.preds.get(stack.pop(), ()):
                if e.pred_phase not in seen:
                    seen.add(e.pred_phase)
                    stack.append(e.pred_phase)
        return frozenset(seen)

    def _key(self, pid: str) -> tuple:
        if pid in self.defended and pid not in self._requeued:
            lead = LEAD_DEFENDED
        elif pid in self._cone:
            lead = LEAD_CONE
        else:
            lead = LEAD_OTHER
        return (lead, *self.priority(pid))

    # ---------------------------------------------------------------- driver
    def run(self) -> DecodeResult:
        ctx = self.ctx
        result = DecodeResult(ledger=ctx.new_ledger())
        result.paths_used.update({"graph", "calendar", "crew_selection", "duty_ledger", "cs_facts", "part_releases"})
        unplaceable: dict[str, str] = {}
        conditional_job: dict[str, bool] = {}
        for job_id, phases in ctx.job_phases.items():
            info = ctx.placeability(job_id)
            conditional_job[job_id] = info.conditional
            for p in phases:
                if info.satisfied:
                    result.satisfied[p.key.id] = min(minute_ceil(p.actual_end), ctx.cutoff_minute) if p.actual_end else ctx.cutoff_minute
                elif not info.placeable:
                    unplaceable[p.key.id] = "; ".join(info.reasons) or "NOT_PLACEABLE"
        for rid in ctx.releases:
            floor, _, reason = ctx.release_floor(rid)
            if floor is None:
                unplaceable[rid] = reason
            else:
                result.milestones[rid] = floor
        result.deferred.update({k: v for k, v in unplaceable.items() if k in ctx.phases})
        remaining: dict[str, int] = {}
        heap: list[tuple[tuple, str]] = []
        resolved = set(result.satisfied) | set(result.milestones) | set(unplaceable)
        for pid, phase in ctx.phases.items():
            if pid in resolved:
                continue
            remaining[pid] = sum(1 for e in ctx.preds.get(pid, ()) if e.pred_phase not in resolved)
        blocked_by: dict[str, str] = {}
        for pid, n in remaining.items():
            for e in ctx.preds.get(pid, ()):
                if e.pred_phase in unplaceable:
                    blocked_by.setdefault(pid, e.pred_phase)
            if n == 0:
                heapq.heappush(heap, (self._key(pid), pid))

        def release(done: str) -> None:
            for e in ctx.succs.get(done, ()):
                s = e.succ_phase
                if s not in remaining:
                    continue
                remaining[s] -= 1
                if remaining[s] == 0:
                    heapq.heappush(heap, (self._key(s), s))

        while heap:
            _, pid = heapq.heappop(heap)
            if pid in result.placements or pid in result.deferred:
                continue
            phase = ctx.phases[pid]
            blocker = blocked_by.get(pid)
            if blocker is not None:
                cause = unplaceable.get(blocker) or result.deferred.get(blocker) or ""
                result.deferred[pid] = f"PREDECESSOR_UNPLACED: {blocker} ({cause[:160]})"
                self._defer_successors(pid, result, blocked_by)
                release(pid)
                continue
            placement, reason = self._place(phase, result, conditional_job.get(phase.job, False))
            if reason == REQUEUE:
                self._requeued.add(pid)
                heapq.heappush(heap, (self._key(pid), pid))
                continue
            if placement is None:
                result.deferred[pid] = reason
                self._defer_successors(pid, result, blocked_by)
            else:
                result.placements[pid] = placement
            release(pid)
        for pid in remaining:
            if pid not in result.placements and pid not in result.deferred:
                result.deferred[pid] = "PREDECESSOR_CYCLE_OR_UNRESOLVED: never became ready"
        self._unreached_commitments(result)
        return result

    def _defer_successors(self, pid: str, result: DecodeResult, blocked_by: dict[str, str]) -> None:
        for e in self.ctx.succs.get(pid, ()):
            blocked_by.setdefault(e.succ_phase, pid)

    # ---------------------------------------------------------------- placement
    def _place(self, phase: Phase, result: DecodeResult, job_conditional: bool) -> tuple[PhasePlacement | None, str]:
        ctx = self.ctx
        result.attempts += 1
        start_floor, end_floor, notes, cond = _floors(ctx, result, phase)
        conditional = cond or job_conditional
        job = ctx.jobs[phase.job]
        if phase.kind is PhaseKind.RELEASE:
            t = max(start_floor, end_floor)
            return PhasePlacement(phase.key.id, phase.job, job.line, phase.kind, t, t, (), None, None, (), None, conditional, False, None,
                                  tuple(notes)), "RELEASE"
        if not phase.role_demands:
            return None, "ROLE_DEMAND_MISSING"
        defended = self.defended.get(phase.key.id)
        if defended is None:
            return self._search(phase, start_floor, end_floor, conditional, result)
        if phase.key.id not in self._requeued:
            placement = self._defend(phase, defended, start_floor, end_floor, conditional, result)
            if placement is not None:
                return placement, "DEFENDED"
            return None, REQUEUE                 # feasible commitments first; the move happens at ordinary priority
        # minimal audited move: the nearest later slot first, then the earliest safe slot; else defer
        pending = result.commitment_results[self._result_index(defended, result)]
        not_before = defended.start if pending.outcome == "PENDING_MOVE" else None
        placement, reason = self._search(phase, start_floor, end_floor, conditional, result, not_before=not_before)
        if placement is None and not_before is not None:
            placement, reason = self._search(phase, start_floor, end_floor, conditional, result)
        self._finish_defense(defended, placement, reason, result)
        return result.placements.get(phase.key.id, placement), reason

    def _search(self, phase: Phase, start_floor: int, end_floor: int, conditional: bool, result: DecodeResult,
                not_before: int | None = None) -> tuple[PhasePlacement | None, str]:
        ctx = self.ctx
        ledger = result.ledger
        job = ctx.jobs[phase.job]
        demand = phase.role_demands[0]
        floor = max(start_floor, not_before or start_floor)
        last_reason = "NO_ELIGIBLE_SHIFT: no eligible shift within admitted calendar bounds after the floor"
        for shift in ctx.line_shifts(job.line):
            if shift.end <= floor or shift.cutoff_instant < floor:
                continue
            pool = ctx.pool_for(demand.team, shift)
            if pool is None:
                last_reason = f"POOL_MISSING: no {demand.team} pool for {shift.label}"
                continue
            first = shift.next_open(max(floor, shift.start))
            if first is None or not shift.valid_start(first):
                continue
            for t in _candidate_starts(ctx, ledger, shift, pool.id, first):
                t2 = shift.next_open(t)
                if t2 is None or not shift.valid_start(t2):
                    continue
                for ot_allowed in (0, ctx.shift_policy.ot_minutes):
                    fit = shift.advance(t2, phase.work_minutes, ot_allowed)
                    if fit is None:
                        continue
                    end, segments = fit
                    if end < end_floor:
                        break
                    placement, reason = self._book(phase, shift, pool.id, segments, conditional, result)
                    if placement is not None:
                        return placement, reason
                    last_reason = reason
                    break
        return None, f"NO_FEASIBLE_PLACEMENT: {last_reason}"

    def _book(self, phase: Phase, shift, pool_id: str, segments, conditional: bool, result: DecodeResult,
              exact_crew=None, committed: bool = False, outcome: str | None = None) -> tuple[PhasePlacement | None, str]:
        ctx = self.ctx
        ledger = result.ledger
        job = ctx.jobs[phase.job]
        demand = phase.role_demands[0]
        aid = digest_id("ASG", phase.key.id, segments[0][0], ctx.purpose.value)
        kind = DutyKind.WORK
        tools = ctx.tool_requirements.get(phase.key.id, ())
        wait_crew = self._wait_crew(phase, result) if phase.kind is PhaseKind.IP_QA and ctx.crew_during_ip == "WAIT" else None
        choice, reason = choose_crew(ctx, ledger, phase_id=phase.key.id, assignment_id=aid, line=job.line, team=demand.team,
                                     shift_id=shift.id, pool_id=pool_id, count=demand.bundle.count, bundle=demand.bundle,
                                     segments=segments, kind=kind, exact_crew=exact_crew, tools=tools,
                                     window=(shift.start, max(shift.end, shift.ot_window[1] if shift.ot_window else shift.end)))
        if choice is None:
            return None, reason
        try:
            with ledger.transaction():
                ledger.commit(choice.booking)
                if wait_crew is not None:
                    prev, crew = wait_crew
                    ledger.book(f"{aid}:WAIT", crew, job.line, prev.shift_id, prev.pool_id, ((prev.end, segments[-1][1], False),),
                                DutyKind.WAIT)
        except LedgerError as exc:
            return None, f"WAIT_DUTY_INFEASIBLE: {exc.code}: {exc.detail}"
        result.paths_used.add("wait_duty" if wait_crew is not None else "release_crew")
        return PhasePlacement(phase.key.id, phase.job, job.line, phase.kind, segments[0][0], segments[-1][1], tuple(segments), shift.id,
                              pool_id, choice.crew, aid, conditional, committed, outcome), reason

    def _wait_crew(self, phase: Phase, result: DecodeResult):
        for e in self.ctx.preds.get(phase.key.id, ()):
            prev = result.placements.get(e.pred_phase)
            if prev is not None and prev.kind is PhaseKind.WORK and prev.job == phase.job and prev.crew:
                return prev, prev.crew
        return None

    # ---------------------------------------------------------------- defense (P14)
    def _defend(self, phase: Phase, d: DefendedTask, start_floor: int, end_floor: int, conditional: bool,
                result: DecodeResult) -> PhasePlacement | None:
        ctx = self.ctx
        result.paths_used.add("commitment_defense")
        shift = next((s for s in ctx.line_shifts(ctx.jobs[phase.job].line) if s.id == d.shift_id), None)
        if shift is None:
            self._record(d, phase, "UNSAFE", "CALENDAR: committed shift is no longer eligible", "not attempted", "calendar changed", None, result)
            return None
        if start_floor > d.start:
            self._record(d, phase, "UNSAFE", f"FLOOR_AFTER_COMMITTED_START: earliest {from_minute(start_floor).isoformat()} > committed "
                         f"{from_minute(d.start).isoformat()}", "not attempted",
                         "a predecessor, part or CS floor moved past the committed start", None, result)
            return None
        if not shift.valid_start(d.start):
            self._record(d, phase, "UNSAFE", "NO_START_RULE: committed start is no longer a valid start", "not attempted", "invalid start", None, result)
            return None
        fit = shift.advance(d.start, phase.work_minutes, 0) or shift.advance(d.start, phase.work_minutes, ctx.shift_policy.ot_minutes)
        if fit is None or fit[0] < end_floor:
            self._record(d, phase, "UNSAFE", "FINISH_FIT: committed slot can no longer hold the work", "not attempted", "finish fit", None, result)
            return None
        end, segments = fit
        pool = ctx.pool_for(phase.role_demands[0].team, shift)
        if pool is None:
            self._record(d, phase, "UNSAFE", "POOL_MISSING", "not attempted", "no pool", None, result)
            return None
        placement, reason1 = self._book(phase, shift, pool.id, segments, conditional, result, exact_crew=d.crew, committed=True, outcome="KEPT")
        if placement is not None:
            self._record(d, phase, "KEPT", "OK", "not needed", "exact incumbent slot and full crew", placement.assignment_id, result)
            return placement
        placement, reason2 = self._book(phase, shift, pool.id, segments, conditional, result, committed=True, outcome="CREW_CHANGED")
        if placement is not None:
            self._record(d, phase, "CREW_CHANGED", reason1, "OK", f"same slot, alternate full crew {list(placement.crew)}", placement.assignment_id, result)
            return placement
        self._record(d, phase, "PENDING_MOVE", reason1, reason2, "both attempts failed; minimal audited move next", None, result)
        return None

    def _unreached_commitments(self, result: DecodeResult) -> None:
        """A defended phase that never reached placement (late part, blocked predecessor, not placeable) is UNSAFE with its cause."""
        recorded = {r.incumbent_assignment_id for r in result.commitment_results}
        for pid, d in sorted(self.defended.items()):
            if d.incumbent_assignment_id in recorded or pid in result.placements:
                continue
            phase = self.ctx.phases.get(pid)
            cause = result.deferred.get(pid, "NOT_PLACED: the phase never became placeable")
            result.commitment_results.append(CommitmentResult(pid, phase.job if phase else pid, d.incumbent_assignment_id, d.day_offset,
                                                              "UNSAFE", "not attempted", "not attempted", f"SAFETY_OVERRIDE: {cause}"[:480],
                                                              None))

    @staticmethod
    def _result_index(d: DefendedTask, result: DecodeResult) -> int:
        return next(i for i in range(len(result.commitment_results) - 1, -1, -1)
                    if result.commitment_results[i].incumbent_assignment_id == d.incumbent_assignment_id)

    def _finish_defense(self, d: DefendedTask, placement: PhasePlacement | None, reason: str, result: DecodeResult) -> None:
        i = self._result_index(d, result)
        last = result.commitment_results[i]
        if last.outcome not in ("PENDING_MOVE", "UNSAFE"):
            return
        if last.outcome == "UNSAFE":
            outcome = "UNSAFE"
            text = last.reason + ("; moved to the earliest safe slot" if placement else f"; {reason}")
        elif placement is not None:
            outcome, text = "MOVED", f"minimal audited move {placement.start - d.start:+d} minutes to {placement.shift_id}"
        else:
            outcome, text = "DEFERRED", reason
        result.commitment_results[i] = CommitmentResult(last.phase, last.job, last.incumbent_assignment_id, last.day_offset, outcome,
                                                        last.attempt1, last.attempt2, text, placement.assignment_id if placement else None)
        if placement is not None:
            result.placements[placement.phase_id] = replace(placement, committed=True, commitment_outcome=outcome)

    def _record(self, d: DefendedTask, phase: Phase, outcome: str, attempt1: str, attempt2: str, reason: str, new_id: str | None,
                result: DecodeResult) -> None:
        result.commitment_results.append(CommitmentResult(phase.key.id, phase.job, d.incumbent_assignment_id, d.day_offset, outcome,
                                                          attempt1, attempt2, reason, new_id))
