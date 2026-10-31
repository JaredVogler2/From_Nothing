"""Independent nine-check and extended final validation (S13, P15).

``validate(snapshot, policy, plan, ...) -> ValidationReceipt`` reconstructs every constraint from
the sealed source snapshot (``ValidationInput``). It never uses the optimizer's feasibility
helpers or anything the candidate declares about itself (evidence ids, estimate ids, release ids,
caps, conditional or committed flags).

Stable check ids:

* **V1 duration/identity.** Known phase keys; the assignment's identity matches the phase;
  positive ordered segments; active minutes conserve the phase's remaining work; the job's
  phases conserve the estimate or the in-progress remainder; nothing re-plans a completed phase.
* **V2 precedence.** Every hard typed edge (FS/SS/FF/SF with signed lag) into a placed phase,
  with verified-complete anchors, purpose release floors and derived AND releases. An
  unresolved, unknown or cyclic predecessor is never satisfied by omission, and in-progress work
  is never skipped. Hard concurrency edges use the admitted fixture meaning (OD-07).
* **V3 workday.** The line's own admitted, bounded calendar and the S3 exception.
* **V4 shift fit.** Physical regular/OT windows and breaks; a valid start (no-start cutoff,
  equality allowed); no start before the cutoff, the job earliest or the same-run CS earliest;
  no end after the CS hard latest; OT within the maximum, authorized per person and truthfully
  reported in the duties.
* **V5 full crew.** Exact distinct role crews; the pure pool of the role's own team and shift;
  admitted purpose membership and availability; individually held whole-interval certificate
  bundles; no synthetic person for an executable plan; no candidate-only roster or forged
  certificate evidence.
* **V6/V7/V8.** One person one duty across labels and carry-in; transfers and complete duty
  budgets; distinct activation against the recomputed cap (``focus.validation.duty``).
* **V9 parts.** Every own and ancestor part release: confirmed receipt for executable plans;
  precision floor and conditional label for forecasts; unknown, revoked or reopened material
  blocks.

Extended checks:

* **E_INVENTORY**: complete WIP reconciliation.
* **E_LINEAGE**: snapshot, source run, scope, policy, purpose and hashes.
* **E_CS**: the same-run raw CS row, load and offsets behind each placed job's station fact.
* **E_COMMITMENT**: every incumbent task's reported defense outcome.
* **E_ECONOMICS**: exact recomputation of completions and costs.
* **E_CAPABILITY**: the purpose's placeability and conditional labels.

The receipt lists counts, coverage, required skips and violations, sorted deterministically. Zero
violations with a skipped required check is **invalid**: there is no "validator 0" success
without coverage.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Iterable

from ..contracts.planning import CandidatePlan, IncumbentPlan, PlanningSnapshot, ValidationReceipt, Violation
from ..data.sourcetime import local_to_utc
from ..domain import (Disposition, DomainError, EdgeType, PersonKey, PhaseKind, Purpose, ReleaseKind, ReleaseStatus, RequirementStatus, digest_id,
                      minute_ceil, to_minute)
from ..economics.evaluator import JobRelease, aircraft_completions, line_costs, summarize
from ..economics.registry import EconomicsRegistry
from ..policy import Policy
from .duty import reconstruct
from .input import VALIDATOR_VERSION, NodeTime, ValidationInput, overlap_minutes

REQUIRED_CHECKS = ("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9",
                   "E_INVENTORY", "E_LINEAGE", "E_CS", "E_COMMITMENT", "E_ECONOMICS", "E_CAPABILITY")
GATE_CHECKS = tuple(c for c in REQUIRED_CHECKS if c != "E_COMMITMENT")   # in-search gate: the incumbent is checked on the final plan
VALIDATIONS = "validations"
CANDIDATES = "candidates"                 # repository contract shared with the optimizer
ACCEPTED_PLANS = "accepted_plans"         # repository contract shared with commitments (P14)
DEFENSE_OUTCOMES = ("KEPT", "CREW_CHANGED", "MOVED", "DEFERRED", "UNSAFE", "OUT_OF_HORIZON", "COMPLETED")


@dataclass
class _Run:
    inp: ValidationInput
    plan: CandidatePlan

    def __post_init__(self) -> None:
        self.violations: list[Violation] = []
        self.counts: dict[str, int] = {c: 0 for c in REQUIRED_CHECKS}
        self.skipped: dict[str, str] = {}
        self.by_phase: dict[str, list] = defaultdict(list)
        for a in self.plan.assignments:
            self.by_phase[a.phase].append(a)
        self.times: dict[str, NodeTime] = {}
        self.cyclic: set[str] = set()

    def bad(self, check: str, code: str, entity: str, detail: str) -> None:
        self.violations.append(Violation(check, code, entity, detail[:600]))

    def count(self, check: str, n: int = 1) -> None:
        self.counts[check] = self.counts.get(check, 0) + n

    def skip(self, check: str, why: str) -> None:
        self.skipped[check] = why


def _segs(a) -> list[tuple[int, int]]:
    return [(to_minute(s.start), to_minute(s.end)) for s in a.segments]


# ---------------------------------------------------------------------- times and the graph

def _topological(inp: ValidationInput) -> tuple[list[str], set[str]]:
    nodes = set(inp.phases) | set(inp.releases)
    indeg = {n: 0 for n in nodes}
    for n in nodes:
        for e in inp.hard_out.get(n, ()):
            if e.succ_phase in indeg:
                indeg[e.succ_phase] += 1
    ready = sorted(n for n, d in indeg.items() if d == 0)
    order: list[str] = []
    while ready:
        n = ready.pop()
        order.append(n)
        for e in inp.hard_out.get(n, ()):
            if e.succ_phase in indeg:
                indeg[e.succ_phase] -= 1
                if indeg[e.succ_phase] == 0:
                    ready.append(e.succ_phase)
    return order, {n for n, d in indeg.items() if d > 0}


def _job_floor(inp: ValidationInput, job_id: str) -> int:
    job = inp.jobs[job_id]
    floor = inp.cutoff
    if job.earliest is not None:
        floor = max(floor, minute_ceil(job.earliest))
    facts = inp.cs.get((job.line, job.station))
    if facts is not None and facts.earliest_at is not None:
        floor = max(floor, to_minute(facts.earliest_at))
    return floor


def _derive_times(run: _Run) -> None:
    inp = run.inp
    order, cyclic = _topological(inp)
    run.cyclic = cyclic
    for pid, assigns in run.by_phase.items():
        phase = inp.phases.get(pid)
        segs = _segs(assigns[0])
        if phase is None or not segs:
            continue
        run.times[pid] = NodeTime(segs[0][0], segs[-1][1], inp.placeable(phase.job)[1], "PLACED")
    for pid, phase in inp.phases.items():
        if pid not in run.times:
            t = inp.satisfied(phase)
            if t is not None:
                run.times[pid] = NodeTime(t, t, False, "SATISFIED")
    for rid, rel in inp.releases.items():
        nt = inp.release_time(rel)
        if nt is not None:
            run.times[rid] = nt
    for n in order:                                 # conditional evidence flows downstream; AND releases are derived
        phase = inp.phases.get(n)
        if phase is None:
            continue
        current = run.times.get(n)
        if current is not None and current.basis == "PLACED":
            cond = current.conditional or any(run.times[e.pred_phase].conditional for e in inp.hard_in.get(n, ()) if e.pred_phase in run.times)
            if cond != current.conditional:
                run.times[n] = NodeTime(current.start, current.end, cond, "PLACED")
            continue
        if current is not None or phase.kind is not PhaseKind.RELEASE:
            continue
        ok, cond, _ = inp.placeable(phase.job)
        if not ok:
            continue
        t = _job_floor(inp, phase.job)
        resolved = True
        for e in inp.hard_in.get(n, ()):
            pt = run.times.get(e.pred_phase)
            if pt is None:
                resolved = False
                break
            t = max(t, (pt.end if e.type in (EdgeType.FS, EdgeType.FF) else pt.start) + e.lag_minutes)
            cond = cond or pt.conditional
        if resolved:
            run.times[n] = NodeTime(t, t, cond, "DERIVED_RELEASE")


# ---------------------------------------------------------------------- V1-V5, V9

def _v1(run: _Run) -> None:
    inp = run.inp
    for a in run.plan.assignments:
        run.count("V1")
        phase = inp.phases.get(a.phase)
        if phase is None:
            run.bad("V1", "UNKNOWN_PHASE", a.id, f"phase {a.phase} is not in snapshot {inp.snapshot.id}")
            continue
        if phase.kind is PhaseKind.RELEASE:
            run.bad("V1", "RELEASE_HAS_CREW", a.id, "release milestones carry no work or crew")
        if a.job != phase.job or a.line != phase.line or a.kind is not phase.kind:
            run.bad("V1", "IDENTITY_MISMATCH", a.id, f"declared job/line/kind {a.job}/{a.line}/{a.kind.value} vs source "
                                                     f"{phase.job}/{phase.line}/{phase.kind.value}")
        if inp.satisfied(phase) is not None:
            run.bad("V1", "COMPLETE_PHASE_REPLANNED", a.id, f"{phase.label} is verified complete: history is not re-planned")
        segs = _segs(a)
        if not segs or any(e <= s for s, e in segs) or any(b[0] < x[1] for x, b in zip(segs, segs[1:])):
            run.bad("V1", "SEGMENT_INVALID", a.id, f"segments {segs} must be positive, ordered and disjoint")
            continue
        if to_minute(a.start) != segs[0][0] or to_minute(a.end) != segs[-1][1]:
            run.bad("V1", "SEGMENT_ENVELOPE_MISMATCH", a.id, "start/end must be the first segment start and the last segment end")
        work = sum(e - s for s, e in segs)
        if work != phase.work_minutes:
            run.bad("V1", "WORK_NOT_CONSERVED", a.id, f"{work} active minutes placed for {phase.label}; source remaining work is "
                                                      f"{phase.work_minutes}")
        if a.work_minutes != phase.work_minutes:
            run.bad("V1", "WORK_DECLARATION_MISMATCH", a.id, f"declares {a.work_minutes}, source {phase.work_minutes}")
        est = inp.durations.get(phase.job)
        if a.kind is PhaseKind.WORK and a.duration_estimate_id is not None and (est is None or a.duration_estimate_id != est.id):
            run.bad("V1", "ESTIMATE_MISMATCH", a.id, f"declared estimate {a.duration_estimate_id} is not the snapshot estimate")
    for pid, assigns in run.by_phase.items():
        if len(assigns) > 1:
            run.bad("V1", "DUPLICATE_PHASE_ASSIGNMENT", pid, f"{len(assigns)} assignments for one phase")
    for job_id, phases in inp.job_phases.items():
        work = [p for p in phases if p.kind is PhaseKind.WORK and p.status == "OPEN"]
        est = inp.durations.get(job_id)
        if not work or est is None:
            continue
        run.count("V1")
        total = sum(p.work_minutes for p in work)
        parents = {p.parent_elapsed_minutes for p in work}
        job = inp.jobs[job_id]
        expected = job.remaining_elapsed if job.remaining_elapsed is not None else est.elapsed_minutes
        if parents != {total} or total != est.elapsed_minutes or total != expected:
            run.bad("V1", "PARENT_NOT_CONSERVED", job_id, f"work phases {total}, parent {sorted(parents)}, estimate {est.elapsed_minutes}, "
                                                          f"remaining {job.remaining_elapsed}")


def _v2(run: _Run) -> None:
    inp = run.inp
    for succ, edges in sorted(inp.hard_in.items()):
        st = run.times.get(succ)
        if st is None or st.basis != "PLACED":
            continue
        for e in edges:
            run.count("V2")
            if not inp.known_node(e.pred_phase):
                run.bad("V2", "UNKNOWN_ENDPOINT", e.id, f"{e.pred_phase} -> {succ}: unknown predecessor is never satisfied")
                continue
            pt = run.times.get(e.pred_phase)
            if pt is None:
                run.bad("V2", "PREDECESSOR_UNRESOLVED", e.id, f"{e.type.value} {e.pred_phase} -> {succ}: the predecessor is unplaced, "
                                                              "deferred, blocked or unreleased; omission never satisfies it")
                continue
            lag = e.lag_minutes
            ok = {EdgeType.FS: st.start >= pt.end + lag, EdgeType.SS: st.start >= pt.start + lag,
                  EdgeType.FF: st.end >= pt.end + lag, EdgeType.SF: st.end >= pt.start + lag}[e.type]
            if not ok:
                run.bad("V2", "PRECEDENCE_VIOLATION", e.id, f"{e.type.value}{lag:+d} {e.pred_phase} [{pt.start},{pt.end}) -> {succ} "
                                                            f"[{st.start},{st.end})")
    for node in sorted(run.cyclic):
        if node in run.by_phase:
            run.count("V2")
            run.bad("V2", "PRECEDENCE_CYCLE", node, "a placed phase lies on or behind a hard-edge cycle")
    for e in inp.concurrency:
        a, b = run.times.get(e.pred_phase), run.times.get(e.succ_phase)
        if a is None or b is None or "PLACED" not in (a.basis, b.basis):
            continue
        run.count("V2")
        overlaps = a.start < b.end and b.start < a.end
        if (e.type is EdgeType.CONCURRENT) != overlaps:
            run.bad("V2", "CONCURRENCY_VIOLATION", e.id, f"{e.type.value}: overlap={overlaps}")


def _v3(run: _Run) -> None:
    inp = run.inp
    for a in run.plan.assignments:
        run.count("V3")
        shift = inp.shift(a.shift_id)
        if shift is None:
            run.bad("V3", "SHIFT_ID_INVALID", a.id, f"shift id {a.shift_id!r} is not a valid {inp.snapshot.site} shift")
            continue
        ok, code, detail = inp.workday(a.line, shift.start_date, shift.number)
        if not ok:
            run.bad("V3", code, a.id, detail)


def _pool_for(inp: ValidationInput, phase, shift):
    if shift is None or not phase.role_demands:
        return None
    return inp.pool_by_key.get((phase.role_demands[0].team, shift.start_date, shift.number))


def _v4(run: _Run) -> None:
    inp = run.inp
    max_ot = inp.policy.int("shift.maximum_ot_minutes")
    for a in run.plan.assignments:
        phase = inp.phases.get(a.phase)
        shift = inp.shift(a.shift_id)
        segs = _segs(a)
        if phase is None or not segs:
            continue
        run.count("V4")
        if shift is None:
            run.bad("V4", "SHIFT_UNKNOWN", a.id, f"no shift geometry for {a.shift_id!r}")
            continue
        windows = list(shift.regular_windows) + ([shift.ot_window] if shift.ot_window else [])
        for s, e in segs:
            if overlap_minutes(s, e, windows) != e - s:
                run.bad("V4", "SEGMENT_OUTSIDE_SHIFT_WINDOWS", a.id, f"[{s},{e}) leaves the physical windows of {shift.label} "
                                                                     "(break, lunch, pre/post or beyond OT)")
        start, end = segs[0][0], segs[-1][1]
        if not shift.in_regular_window(start):
            run.bad("V4", "START_NOT_IN_REGULAR_WINDOW", a.id, f"start {start} is not inside a regular window of {shift.label}")
        elif not shift.valid_start(start):
            run.bad("V4", "NO_START_CUTOFF", a.id, f"start coordinate {shift.coord(start)} > cutoff {shift.cutoff_coord} in {shift.label}")
        if start < inp.cutoff:
            run.bad("V4", "BEFORE_CUTOFF", a.id, f"start {start} < snapshot cutoff {inp.cutoff}")
        job = inp.jobs.get(a.job)
        if job is not None and job.earliest is not None and start < minute_ceil(job.earliest):
            run.bad("V4", "BEFORE_JOB_EARLIEST", a.id, f"start before the sourced job earliest {job.earliest.isoformat()}")
        facts = inp.cs.get((job.line, job.station)) if job is not None else None
        if facts is not None and facts.earliest_at is not None and start < to_minute(facts.earliest_at):
            run.bad("V4", "BEFORE_CS_EARLIEST", a.id, f"start before CS{facts.station} earliest {facts.earliest_at.isoformat()}")
        if facts is not None and facts.hard_latest_at is not None and end > to_minute(facts.hard_latest_at):
            run.bad("V4", "AFTER_CS_HARD_LATEST", a.id, f"end after CS{facts.station} hard latest {facts.hard_latest_at.isoformat()}")
        ot = sum(overlap_minutes(s, e, [shift.ot_window]) for s, e in segs) if shift.ot_window else 0
        if ot > max_ot:
            run.bad("V4", "OT_EXCEEDS_MAXIMUM", a.id, f"{ot} OT minutes > {max_ot}")
        pool = _pool_for(inp, phase, shift)
        if ot and pool is not None:
            for r in a.crew:
                av = inp.availability.get((r.person, pool.id))
                authorized = sum(overlap_minutes(s, e, [i.as_minutes() for i in av.ot_intervals]) for s, e in segs) if av else 0
                in_ot = sum(overlap_minutes(s, e, [shift.ot_window]) for s, e in segs)
                if authorized < in_ot:
                    run.bad("V4", "OT_NOT_AUTHORIZED", a.id, f"{r.person}: {in_ot} OT minutes, {authorized} authorized")
    # truthful OT and regular minutes on every reported duty
    windows_by_person_shift: dict[tuple[str, str], tuple] = {}
    for (person, pool_id), av in inp.availability.items():
        pool = inp.pools.get(pool_id)
        if pool is not None:
            sid = f"{pool.key.site}:{pool.key.start_date.isoformat()}:S{pool.key.shift_number}"
            windows_by_person_shift[(person, sid)] = tuple(i.as_minutes() for i in av.ot_intervals)
    for d in run.plan.duties:
        ot_w = windows_by_person_shift.get((d.person, d.shift_id or ""))
        if ot_w is None:
            continue
        run.count("V4")
        s, e = to_minute(d.start), to_minute(d.end)
        ot = overlap_minutes(s, e, ot_w)
        if d.ot_minutes != ot or d.regular_minutes != (e - s) - ot:
            run.bad("V4", "OT_MISREPORTED", d.id, f"{d.person} reports {d.regular_minutes} regular + {d.ot_minutes} OT; the windows give "
                                                  f"{(e - s) - ot} + {ot}")


def _v5(run: _Run) -> None:
    inp = run.inp
    for a in run.plan.assignments:
        phase = inp.phases.get(a.phase)
        segs = _segs(a)
        if phase is None or not segs:
            continue
        run.count("V5")
        req = inp.requirements.get(phase.job)
        if req is None or req.status is RequirementStatus.UNKNOWN:
            run.bad("V5", "REQUIREMENTS_UNKNOWN", a.id, "no positive requirement rows and no affirmative census")
        if not phase.role_demands:
            run.bad("V5", "ROLE_DEMAND_MISSING", a.id, f"{phase.label} has no sourced role demand")
            continue
        shift = inp.shift(a.shift_id)
        start, end = segs[0][0], segs[-1][1]
        people = [r.person for r in a.crew]
        if len(set(people)) != len(people):
            run.bad("V5", "CREW_DUPLICATE_PERSON", a.id, "one person cannot fill two roles at once")
        expected = sum(d.bundle.count for d in phase.role_demands)
        if any(d.bundle.count <= 0 for d in phase.role_demands):
            run.bad("V5", "CREW_DEMAND_UNAVAILABLE", a.id, "sourced crew demand is 0 or unavailable (no clamp)")
        if len(people) != expected:
            run.bad("V5", "CREW_SIZE_MISMATCH", a.id, f"{len(people)} people for a demand of {expected}")
        for demand in phase.role_demands:
            pool = inp.pool_by_key.get((demand.team, shift.start_date, shift.number)) if shift else None
            roles = [r for r in a.crew if r.role == demand.bundle.role]
            if len(roles) != demand.bundle.count:
                run.bad("V5", "ROLE_MISMATCH", a.id, f"{len(roles)} crew declared as {demand.bundle.role}, demand {demand.bundle.count}")
            if pool is None:
                run.bad("V5", "POOL_MISSING", a.id, f"no pure {demand.team} pool for {a.shift_id}")
                continue
            if a.pool_key != pool.id:
                run.bad("V5", "POOL_KEY_MISMATCH", a.id, f"declared pool {a.pool_key} is not the {demand.team} pool {pool.id}")
            members = inp.admitted_members(pool)
            codes = set(demand.bundle.all_codes)
            for r in roles:
                if inp.purpose is Purpose.EXECUTABLE and inp.synthetic(r.person):
                    run.bad("V5", "SYNTHETIC_IN_EXECUTABLE", a.id, f"{r.person} has synthetic origin evidence")
                if r.person not in members:
                    run.bad("V5", "NOT_POOL_MEMBER", a.id, f"{r.person} is not an admitted {inp.purpose.value} member of {pool.id}")
                    continue
                av = inp.availability.get((r.person, pool.id))
                if av is None:
                    run.bad("V5", "NO_AVAILABILITY", a.id, f"{r.person} has no admitted availability in {pool.id}")
                elif r.availability_id != av.id:
                    run.bad("V5", "CANDIDATE_ONLY_ROSTER", a.id, f"{r.person}: declared availability {r.availability_id!r} is not the source "
                                                                 f"record {av.id}")
                if demand.bundle.groups:
                    ok, why = inp.bundle_held(r.person, demand.bundle, start, end)
                    if not ok:
                        run.bad("V5", "NOT_QUALIFIED", a.id, f"{r.person} lacks the full bundle over [{start},{end}): {why}")
                allowed = {ev.row_id for cert, items in inp.holdings.get(r.person, {}).items() if cert in codes for h in items for ev in h.evidence}
                forged = sorted(set(r.holding_evidence_ids) - allowed)
                if forged:
                    run.bad("V5", "CERT_EVIDENCE_FORGED", a.id, f"{r.person} declares evidence {forged[:3]} that is not a source holding row")


def _reached(inp: ValidationInput, seeds: Iterable[str]) -> set[str]:
    """Nodes reached from ``seeds`` through one or more hard edges: exactly the nodes having a seed in
    ``inp.ancestors(node)``, cycles included (``hard_out`` is ``hard_in`` reversed)."""
    seen: set[str] = set()
    stack = [e.succ_phase for s in seeds for e in inp.hard_out.get(s, ())]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(e.succ_phase for e in inp.hard_out.get(n, ()))
    return seen


def _v9(run: _Run) -> None:
    """Part release floors and blocking shortages on every placed phase's hard-edge ancestors.

    One forward pass in topological order carries to every node a summary of what lies above it: the latest part
    floor (a receipt, or a forecast on conditional work), whether some part release above it can never be met (no
    receipt on executable work, or a blocked release), whether one is a forecast that needs a conditional label, and
    whether a phase of a job a blocking shortage affects is at or above it. An assignment the summary clears has no
    V9 finding. Every other assignment, and any phase on or below a cycle, gets the exact walk over its own ancestors,
    so the decisions, the violations and their order are those of the per-assignment reference pinned in
    ``tests/unit/test_validator_v9_one_pass.py``. The pass costs one visit per edge instead of one walk per release.
    """
    inp = run.inp
    executable = inp.purpose is Purpose.EXECUTABLE
    blocking = [s for s in inp.snapshot.shortages if s.status in ("UNKNOWN_ECD", "REOPENED")]
    affected_any = {job for s in blocking for job in s.affected_jobs}
    # What each part release imposes on the work below it: (floor minute or None, never met, needs a label).
    own: dict[str, tuple[int | None, bool, bool]] = {}
    for rid, rel in inp.releases.items():
        if rel.kind is not ReleaseKind.PART:
            continue
        if rel.status is ReleaseStatus.CONFIRMED and rel.ready_at is not None:
            own[rid] = (minute_ceil(rel.ready_at), False, False)
        elif executable:
            own[rid] = (None, True, False)
        elif rel.status is ReleaseStatus.CONDITIONAL and rel.forecast_at is not None:
            own[rid] = (minute_ceil(rel.forecast_at), False, True)
        else:
            own[rid] = (None, True, False)
    indeg: dict[str, int] = defaultdict(int)
    for edges in inp.hard_out.values():
        for e in edges:
            indeg[e.succ_phase] += 1
    ready = [n for n in inp.hard_out if not indeg.get(n)]
    floor_in: dict[str, int] = {}
    never_in: set[str] = set()
    label_in: set[str] = set()
    shortage_in: set[str] = set()                       # a phase of an affected job lies strictly above
    while ready:
        n = ready.pop()
        floor, never, label = floor_in.get(n), n in never_in, n in label_in
        mine = own.get(n)
        if mine is not None:
            if mine[0] is not None and (floor is None or mine[0] > floor):
                floor = mine[0]
            never, label = never or mine[1], label or mine[2]
        phase = inp.phases.get(n)
        shortage = n in shortage_in or (phase is not None and phase.job in affected_any)
        for e in inp.hard_out.get(n, ()):
            s = e.succ_phase
            if floor is not None and floor > floor_in.get(s, floor - 1):
                floor_in[s] = floor
            if never:
                never_in.add(s)
            if label:
                label_in.add(s)
            if shortage:
                shortage_in.add(s)
            indeg[s] -= 1
            if not indeg[s]:
                ready.append(s)
    reach_cache: list[tuple[Any, set[str], set[str]]] = []

    def shortage_reach() -> list[tuple[Any, set[str], set[str]]]:
        if blocking and not reach_cache:
            by_job: dict[str, list[str]] = defaultdict(list)
            for pid, ph in inp.phases.items():
                by_job[ph.job].append(pid)
            for s in blocking:
                affected = set(s.affected_jobs)
                reach_cache.append((s, affected, _reached(inp, [pid for job in affected for pid in by_job.get(job, ())])))
        return reach_cache

    for a in run.plan.assignments:
        phase = inp.phases.get(a.phase)
        if phase is None:
            continue
        run.count("V9")
        start = to_minute(a.start)
        n = a.phase
        if not indeg.get(n) and n not in never_in and start >= floor_in.get(n, start) \
                and (a.conditional_reasons or n not in label_in) and n not in shortage_in and phase.job not in affected_any:
            continue                                    # nothing above this assignment can draw a V9 finding
        above: set[str] = set()
        stack = [e.pred_phase for e in inp.hard_in.get(n, ())]
        while stack:
            m = stack.pop()
            if m not in above:
                above.add(m)
                stack.extend(e.pred_phase for e in inp.hard_in.get(m, ()))
        for node in sorted(above & own.keys()):
            rel = inp.releases[node]
            if rel.status is ReleaseStatus.CONFIRMED and rel.ready_at is not None:
                if start < minute_ceil(rel.ready_at):
                    run.bad("V9", "BEFORE_PART_RELEASE", a.id, f"{rel.label} received {rel.ready_at.isoformat()}")
            elif executable:
                run.bad("V9", "PART_NOT_RECEIVED", a.id, f"{rel.label} has no confirmed receipt: not executable")
            elif rel.status is ReleaseStatus.CONDITIONAL and rel.forecast_at is not None:
                if start < minute_ceil(rel.forecast_at):
                    run.bad("V9", "BEFORE_PART_FLOOR", a.id, f"{rel.label} forecast floor {rel.forecast_at.isoformat()} ({rel.precision})")
                if not a.conditional_reasons:
                    run.bad("V9", "PART_CONDITIONAL_UNLABELLED", a.id, f"{rel.label} is a forecast ETA: the assignment must be conditional")
            else:
                run.bad("V9", "PART_RELEASE_BLOCKED", a.id, f"{rel.label} is {rel.status.value}")
        for s, affected, reach in shortage_reach():
            if phase.job in affected or n in reach:
                run.bad("V9", "PART_SHORTAGE_BLOCKED", a.id, f"shortage {s.record_id} is {s.status}")


# ---------------------------------------------------------------------- extended checks

def _e_inventory(run: _Run) -> None:
    inp, plan = run.inp, run.plan
    outcomes: dict[str, Any] = {}
    for o in plan.job_dispositions:
        if o.job in outcomes:
            run.bad("E_INVENTORY", "INVENTORY_DUPLICATE", o.job, "job reported twice")
        outcomes[o.job] = o
    for job_id in sorted(inp.jobs):
        run.count("E_INVENTORY")
        if job_id not in outcomes:
            run.bad("E_INVENTORY", "INVENTORY_MISSING", job_id, "a snapshot job has no disposition: nothing is dropped to look cheap")
    assigned = {a.job for a in plan.assignments}
    for job_id, o in sorted(outcomes.items()):
        disp = inp.dispositions.get(job_id)
        if disp is None:
            run.bad("E_INVENTORY", "INVENTORY_UNKNOWN_JOB", job_id, "job is not in the snapshot")
            continue
        if disp.disposition is not Disposition.ADMITTED:
            if o.disposition is not disp.disposition:
                run.bad("E_INVENTORY", "DISPOSITION_CONFLICT", job_id, f"source {disp.disposition.value}, plan {o.disposition.value}")
            if job_id in assigned:
                run.bad("E_INVENTORY", "SCHEDULED_NOT_ADMITTED", job_id, f"{disp.disposition.value} job has assignments")
            continue
        placeable, cond, _ = inp.placeable(job_id)
        open_phases = [p for p in inp.job_phases.get(job_id, []) if p.kind is not PhaseKind.RELEASE and inp.satisfied(p) is None]
        placed = [p for p in open_phases if p.key.id in run.by_phase]
        if o.disposition in (Disposition.SCHEDULED, Disposition.CONDITIONAL):
            if len(placed) != len(open_phases):
                run.bad("E_INVENTORY", "SCHEDULED_INCOMPLETE", job_id, f"{len(placed)} of {len(open_phases)} open phases placed")
            release = next((p for p in inp.job_phases.get(job_id, []) if p.kind is PhaseKind.RELEASE), None)
            if release is not None and release.key.id not in run.times:
                run.bad("E_INVENTORY", "SCHEDULED_UNRELEASED", job_id, "scheduled job's release does not resolve")
            if (o.disposition is Disposition.CONDITIONAL) != cond:
                run.bad("E_INVENTORY", "CONDITIONAL_LABEL_MISMATCH", job_id, f"plan {o.disposition.value}, source conditional={cond}")
        elif o.disposition is Disposition.DEFERRED:
            if not o.reasons:
                run.bad("E_INVENTORY", "DEFERRED_WITHOUT_REASON", job_id, "every wait needs a verified cause")
        else:
            run.bad("E_INVENTORY", "DISPOSITION_CONFLICT", job_id, f"admitted job reported as {o.disposition.value}")


def _e_lineage(run: _Run, purpose: Purpose) -> bool:
    inp, plan, snap = run.inp, run.plan, run.inp.snapshot
    run.count("E_LINEAGE")
    integrity = inp.snapshot_integrity()
    if integrity != "OK":
        run.bad("E_LINEAGE", "SNAPSHOT_HASH_MISMATCH", snap.id, integrity)
        return False
    checks = ((plan.snapshot_id == snap.id and plan.snapshot_hash == snap.hash, "SNAPSHOT_MISMATCH", "plan is bound to another snapshot"),
              (plan.source_run_id == snap.source_run_id, "SOURCE_RUN_MISMATCH", "plan names another source run"),
              (plan.scope == snap.scope_key, "SCOPE_MISMATCH", f"plan scope {plan.scope} vs {snap.scope_key}"),
              (plan.policy_hash == inp.policy.hash and snap.policy_hash == inp.policy.hash, "POLICY_MISMATCH",
               "plan, snapshot and active policy hashes differ"),
              (plan.purpose is purpose and all(a.purpose is purpose for a in plan.assignments), "PURPOSE_MISMATCH",
               f"validated as {purpose.value}"),
              (plan.hash == plan.computed_hash(), "PLAN_HASH_MISMATCH", "content does not match the sealed hash"),
              (not (plan.global_optimality_proven and plan.algorithm_status.proof_status == "HEURISTIC_NO_PROOF"), "UNTRUTHFUL_PROOF",
               "a heuristic cannot claim global optimality"))
    for ok, code, detail in checks:
        run.count("E_LINEAGE")
        if not ok:
            run.bad("E_LINEAGE", code, plan.id, detail)
    return True


def _e_cs(run: _Run) -> None:
    inp = run.inp
    for job_id in sorted({a.job for a in run.plan.assignments}):
        job = inp.jobs.get(job_id)
        if job is None:
            continue
        run.count("E_CS")
        facts = inp.cs.get((job.line, job.station))
        if facts is None:
            run.bad("E_CS", "CS_FACT_MISSING", job_id, f"no same-run CS fact for line {job.line} station {job.station}")
            continue
        if facts.status != "ADMITTED":
            run.bad("E_CS", "CS_FACT_NOT_ADMITTED", job_id, f"CS{facts.station} is {facts.status}")
        if facts.source_run_id != inp.snapshot.source_run_id:
            run.bad("E_CS", "CS_FACT_FOREIGN_RUN", job_id, f"fact from run {facts.source_run_id}")
        rows = [r for r in inp.raw_cs.get((job.line, facts.station), []) if r.is_active]
        match = [r for r in rows if r.load_id == facts.raw_load_id]
        if not match:
            run.bad("E_CS", "CS_RAW_ROW_MISSING", job_id, f"no active same-run raw row with load {facts.raw_load_id}")
            continue
        raw = match[0]
        for key, value in sorted(facts.raw_offset_fields.items()):
            attr = key.lower()
            if hasattr(raw, attr) and getattr(raw, attr) != value:
                run.bad("E_CS", "CS_RAW_FIELD_MISMATCH", job_id, f"{key}: fact {value!r}, raw row {getattr(raw, attr)!r}")


def _e_commitment(run: _Run, incumbent: IncumbentPlan | None) -> None:
    inp, plan = run.inp, run.plan
    if plan.incumbent_id is None:
        for r in plan.commitment_results:
            run.count("E_COMMITMENT")
            run.bad("E_COMMITMENT", "COMMITMENT_WITHOUT_INCUMBENT", r.job, f"{r.outcome} reported without an accepted incumbent")
        return
    if incumbent is None:
        run.skip("E_COMMITMENT", f"accepted incumbent {plan.incumbent_id} was not supplied")
        return
    if incumbent.id != plan.incumbent_id:
        run.count("E_COMMITMENT")
        run.bad("E_COMMITMENT", "INCUMBENT_MISMATCH", plan.id, f"plan names {plan.incumbent_id}, supplied {incumbent.id}")
        return
    horizon = inp.policy.int("commitment.horizon_calendar_days")
    today = inp.snapshot.cutoff.astimezone(inp.shift_policy.tz).date()
    results = {r.incumbent_assignment_id: r for r in plan.commitment_results}
    by_id = {a.id: a for a in plan.assignments}
    jobs = {(j.line, j.key.native_id): j for j in inp.jobs.values()}
    for n, ia in enumerate(incumbent.assignments):
        run.count("E_COMMITMENT")
        aid = digest_id("INCA", incumbent.id, n)
        r = results.get(aid)
        if r is None:
            run.bad("E_COMMITMENT", "COMMITMENT_RESULT_MISSING", aid, f"{ia.line}/{ia.soi} {ia.day} has no defense outcome")
            continue
        offset = (ia.day - today).days
        a = by_id.get(r.new_assignment_id) if r.new_assignment_id else None
        start = to_minute(local_to_utc(datetime.combine(ia.day, time.fromisoformat(ia.start_local)), inp.shift_policy.tz))
        crew = {PersonKey(c, incumbent.namespace).id for c in ia.crew}
        sid = f"{inp.snapshot.site}:{ia.day.isoformat()}:S{ia.shift}"
        same_slot = a is not None and to_minute(a.start) == start and a.shift_id == sid
        actual = {x.person for x in a.crew} if a is not None else set()
        job = jobs.get((ia.line, ia.soi))
        expected_ok = {
            "KEPT": same_slot and actual == crew and a.committed and 0 <= offset < horizon,
            "CREW_CHANGED": same_slot and actual != crew and a.committed and 0 <= offset < horizon,
            "MOVED": a is not None and not same_slot and a.committed and 0 <= offset < horizon,
            "DEFERRED": a is None and 0 <= offset < horizon,
            "UNSAFE": bool(r.reason),
            "OUT_OF_HORIZON": offset < 0 or offset >= horizon,
            "COMPLETED": job is not None and inp.dispositions[job.id].disposition is Disposition.VERIFIED_COMPLETE,
        }
        if r.outcome not in expected_ok:
            run.bad("E_COMMITMENT", "COMMITMENT_OUTCOME_UNKNOWN", aid, r.outcome)
        elif r.day_offset != offset:
            run.bad("E_COMMITMENT", "COMMITMENT_OFFSET_MISMATCH", aid, f"reported {r.day_offset}, source {offset}")
        elif not expected_ok[r.outcome]:
            run.bad("E_COMMITMENT", "COMMITMENT_MISREPORTED", aid, f"{r.outcome} does not match the placed slot/crew for {ia.line}/{ia.soi}")
        if a is not None and a.commitment_outcome not in (None, r.outcome):
            run.bad("E_COMMITMENT", "COMMITMENT_MISREPORTED", aid, f"assignment says {a.commitment_outcome}, result {r.outcome}")


def _e_economics(run: _Run) -> None:
    inp, plan = run.inp, run.plan
    releases: dict[str, JobRelease] = {}
    for job_id, phases in inp.job_phases.items():
        rel = next((p for p in phases if p.kind is PhaseKind.RELEASE), None)
        if rel is None:
            continue
        t = run.times.get(rel.key.id)
        if t is not None and t.basis in ("DERIVED_RELEASE", "SATISFIED"):
            releases[job_id] = JobRelease(job_id, t.end, t.conditional, "released" if t.basis == "DERIVED_RELEASE" else "verified complete")
        else:
            releases[job_id] = JobRelease(job_id, None, False, "not released")
    lines = sorted({j.line for j in inp.jobs.values()})
    completions = aircraft_completions(inp.jobs.values(), inp.dispositions, releases, lines)
    registry = EconomicsRegistry({e.line: e for e in inp.snapshot.economics}, inp.policy)
    summary = summarize(line_costs(registry, completions, inp.cutoff))
    reported = {c.line: c for c in plan.aircraft_completion}
    for line in lines:
        run.count("E_ECONOMICS")
        mine, theirs = completions[line], reported.get(line)
        if theirs is None or (mine.status, mine.completion_at) != (theirs.status, theirs.completion_at):
            run.bad("E_ECONOMICS", "COMPLETION_MISMATCH", str(line), f"recomputed {mine.status} {mine.completion_at}, reported "
                                                                     f"{theirs.status if theirs else None} {theirs.completion_at if theirs else None}")
    run.count("E_ECONOMICS")
    got = plan.costs
    if (summary.known_total_cents, summary.conditional_total_cents, summary.controllable_known_cents, summary.unknown_lines, summary.complete) != \
            (got.known_total_cents, got.conditional_total_cents, got.controllable_known_cents, got.unknown_lines, got.complete):
        run.bad("E_ECONOMICS", "ECONOMICS_MISMATCH", plan.id, f"recomputed known {summary.known_total_cents} conditional "
                                                              f"{summary.conditional_total_cents} controllable {summary.controllable_known_cents} "
                                                              f"unknown {summary.unknown_lines}; reported known {got.known_total_cents} "
                                                              f"controllable {got.controllable_known_cents} unknown {got.unknown_lines}")


def _e_capability(run: _Run) -> None:
    inp, plan = run.inp, run.plan
    for a in plan.assignments:
        run.count("E_CAPABILITY")
        ok, cond, why = inp.placeable(a.job)
        if not ok:
            run.bad("E_CAPABILITY", "NOT_PLACEABLE_FOR_PURPOSE", a.id, f"{inp.purpose.value}: {why[:300]}")
        if inp.purpose is Purpose.EXECUTABLE and a.conditional_reasons:
            run.bad("E_CAPABILITY", "CONDITIONAL_IN_EXECUTABLE", a.id, "forecast-only evidence cannot be executable")
        if inp.purpose is not Purpose.EXECUTABLE and ok:
            if cond and not a.conditional_reasons:
                run.bad("E_CAPABILITY", "CONDITIONAL_UNLABELLED", a.id, "forecast-only assignment must carry its conditional reasons")
            if not cond and a.conditional_reasons:
                run.bad("E_CAPABILITY", "CONDITIONAL_MISLABELLED", a.id, "confirmed work labelled conditional")
    for o in plan.job_dispositions:
        if inp.purpose is Purpose.EXECUTABLE and o.disposition is Disposition.CONDITIONAL:
            run.count("E_CAPABILITY")
            run.bad("E_CAPABILITY", "CONDITIONAL_IN_EXECUTABLE", o.job, "an executable plan has no conditional jobs")


# ---------------------------------------------------------------------- entrypoint

def validate(snapshot: PlanningSnapshot, policy: Policy, plan: CandidatePlan, *, purpose: Purpose | None = None,
             incumbent: IncumbentPlan | None = None, kind: str = "FINAL", payload_sha256: str | None = None,
             required: Iterable[str] = REQUIRED_CHECKS, inp: ValidationInput | None = None,
             created_at: datetime | None = None) -> ValidationReceipt:
    purpose = purpose or plan.purpose
    inp = inp if inp is not None and inp.purpose is purpose and inp.snapshot is snapshot else ValidationInput.build(snapshot, policy, purpose)
    run = _Run(inp, plan)
    required = tuple(required)
    if _e_lineage(run, purpose):
        _derive_times(run)
        _v1(run)
        _v2(run)
        _v3(run)
        _v4(run)
        _v5(run)
        duty = reconstruct(snapshot, policy, plan, inp)
        run.violations.extend(duty.violations)
        for check in ("V6", "V7", "V8"):
            run.count(check, duty.checks.get(check, 0))
        run.count("V7", duty.checks.get("TRANSFER", 0))
        _v9(run)
        _e_inventory(run)
        _e_cs(run)
        _e_commitment(run, incumbent)
        _e_economics(run)
        _e_capability(run)
    else:
        for check in REQUIRED_CHECKS:
            if check != "E_LINEAGE":
                run.skip(check, "snapshot integrity failed: no trustworthy source view")
    violations = tuple(sorted(run.violations, key=lambda v: (v.check, v.code, v.entity, v.detail)))
    required_skips = tuple(sorted(c for c in run.skipped if c in required))
    coverage: dict[str, Any] = {
        c: {"items": run.counts.get(c, 0), "status": "SKIPPED" if c in run.skipped else ("CHECKED" if run.counts.get(c, 0) else "VACUOUS"),
            "required": c in required, **({"reason": run.skipped[c]} if c in run.skipped else {})}
        for c in REQUIRED_CHECKS}
    coverage["notes"] = {"tool_overlap": "NOT_APPLICABLE: no tool-requirement source is mapped (S04); tool calendars are enforced when present",
                         "frozen_goals": "checked at publication (P16) against the accepted goal set"}
    receipt = ValidationReceipt(
        id=digest_id("VAL", plan.id, plan.hash, snapshot.hash, policy.hash, purpose.value, kind, VALIDATOR_VERSION, payload_sha256 or "",
                     sorted(required)),
        candidate_id=plan.id, kind=kind, snapshot_hash=snapshot.hash, policy_hash=policy.hash, validator_version=VALIDATOR_VERSION,
        check_counts={c: run.counts.get(c, 0) for c in REQUIRED_CHECKS}, coverage=coverage, violations=violations,
        required_skips=required_skips, plan_hash=plan.hash, payload_sha256=payload_sha256,
        valid=not violations and not required_skips, created_at=created_at or plan.created_at)
    return receipt


def receipt_summary(receipt: ValidationReceipt, limit: int = 50) -> dict[str, Any]:
    by_check: dict[str, int] = defaultdict(int)
    for v in receipt.violations:
        by_check[v.check] += 1
    return {"receipt_id": receipt.id, "candidate_id": receipt.candidate_id, "kind": receipt.kind, "valid": receipt.valid,
            "validator_version": receipt.validator_version, "check_counts": receipt.check_counts,
            "violation_counts": dict(sorted(by_check.items())), "required_skips": list(receipt.required_skips),
            "violations": [{"check": v.check, "code": v.code, "entity": v.entity, "detail": v.detail} for v in receipt.violations[:limit]],
            "plan_hash": receipt.plan_hash, "snapshot_hash": receipt.snapshot_hash, "policy_hash": receipt.policy_hash}


def persist_receipt(repositories: Any, receipt: ValidationReceipt, plan: CandidatePlan) -> None:
    if repositories.get_raw(VALIDATIONS, receipt.id) is None:
        repositories.put_record(VALIDATIONS, "ValidationReceipt", receipt.id, receipt, source_run_id=plan.source_run_id,
                                policy_hash=plan.policy_hash, purpose=plan.purpose.value)


def load_incumbent(repositories: Any, incumbent_id: str | None) -> IncumbentPlan | None:
    if not incumbent_id:
        return None
    return repositories.get_typed(ACCEPTED_PLANS, incumbent_id, "IncumbentPlan", IncumbentPlan)


def validate_stored(services: Any, candidate_id: str, *, kind: str = "FINAL", persist: bool = True) -> tuple[CandidatePlan | None, ValidationReceipt | None, str]:
    """Load a persisted candidate, its snapshot and accepted incumbent, validate and persist the receipt."""
    from ..data.acquisition import load_snapshot

    repos = services.repositories
    plan = repos.get_typed(CANDIDATES, candidate_id, "CandidatePlan", CandidatePlan)
    if plan is None:
        return None, None, "CANDIDATE_NOT_FOUND"
    snapshot = load_snapshot(repos, plan.snapshot_id)
    if snapshot is None:
        return plan, None, "SNAPSHOT_NOT_FOUND"
    receipt = validate(snapshot, services.policy, plan, incumbent=load_incumbent(repos, plan.incumbent_id), kind=kind,
                       created_at=services.clock.now())
    if persist:
        persist_receipt(repos, receipt, plan)
    return plan, receipt, "OK"


def cli_validate(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "all_required", True):
        return {"status": "invalid", "error": {"code": "ALL_REQUIRED_MANDATORY",
                                               "message": "every required check always runs; partial validation is not offered"}}
    plan, receipt, code = validate_stored(services, args.candidate)
    if receipt is None:
        return {"status": "invalid" if code == "CANDIDATE_NOT_FOUND" else "unavailable",
                "error": {"code": code, "message": f"cannot validate {args.candidate}: {code}"}}
    return {"status": "ok" if receipt.valid else "invalid", **receipt_summary(receipt), "publishable": receipt.valid}
