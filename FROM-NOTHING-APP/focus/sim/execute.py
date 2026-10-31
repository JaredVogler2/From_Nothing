"""A seeded model of the shop floor working one shift, and each team's canonical score with its excusals.

Synthetic outcomes, repeatable from the seed.

**Goals of record.** As in ``performance.lifecycle.GoalRegistry``, the first accepted (valid) EXECUTABLE plan covering a
production day freezes that day's team-shift goals. Its units are the assignments starting in each shift window, each
worth the phase's standard person minutes at freezing. Later plans the same day are proposals: the floor works the
latest one, but the goals stay frozen.
- A frozen unit finished earlier in the day is credited to its own goal (early completion).
- Work that is no frozen unit of the day is offplan output.
- Completion is tracked per job, kind and frozen ordinal: once progress is written back, a job's remaining phases
  restart their ordinals.

**The floor.** At the start of the shift a few present people call off (``late_absence``), and the calls are written to
attendance. Then every dispatched assignment that starts in the shift is worked in start order:
- **predecessors:**
  - each hard edge is honored by type and lag (FS: the predecessor's end; SS: its start; FF/SF: it must be finished);
  - a job's RELEASE node finishes when all its inputs do, so links between jobs work within the shift;
  - with an unfinished predecessor the crew waits (``predecessor``), or with ``oos_work`` probability works out of
    sequence anyway;
  - every out-of-sequence start is recorded at the actual start against each unfinished predecessor. By choice
    (``oos_discretionary``) it is charged at ``score.oos_rate`` whether or not the task finishes; a directed one cites
    a directive and is not charged;
- **parts:** a task cannot start while its part is still open (``part``):
  - nor when its kit is found short at the point of use (``kit_shortage``, a new dated shortage);
  - half of the part slips (``unannounced_slip``) are entered only once the part is found missing;
- **absence:** a task with a called-off crew member cannot start (``absence``);
- **deviation:** with ``deviation`` probability a team works an unplanned ready task in place of one dispatched task;
- **duration:** the actual length is the planned length times a lognormal factor (``duration_sigma``). Work that would
  run more than the authorized overtime past the shift end is not finished (``overrun``): the crew stops when the
  allowance runs out, and what it performed is kept.

Afterwards a team with at least ``PULL_AHEAD_IDLE_HOURS`` idle hours pulls ready work forward with ``pull_ahead``
probability.

**Progress.** Finished phases, and work an overrun cut short, are written back per job:
- A job with production work left becomes IN WORK with its performed and remaining minutes (``work_progress``), so the
  next admission plans only what is left. A phase cut short keeps the rest of its actual length.
- A job whose production work is all done takes its buyoff at the end of the shift and is completed.

Nothing is credited twice.

**Score.** Each team-shift goal is scored by the canonical formula (``focus.performance.score``) under the active policy:
- attainment = earned frozen effort / D;
- adherence = earned frozen effort / (earned frozen + offplan output), so offplan work is discounted;
- the once-only out-of-sequence charge;
- D = frozen effort - approved relief.

Results are labelled with the policy's status (DEMONSTRATION), or NEUTRAL when D = 0.

**Excusals.** Every missed frozen unit's reason becomes a claim in the S14 taxonomy, attributed like
``disruption.auto_attribute``:
- **blame passes upstream:** a blocking phase that was itself blocked passes the blame to its own unfinished
  prerequisite. A RELEASE node passes it to its unfinished inputs, and an open part makes the claim LATE_PART;
- **capacity:** CAPACITY_SHORTAGE claims cite the attendance call-off;
- **rework:** REWORK_INJECTION claims cite the defect origin of rework the team worked offplan;
- **proposed only:** SAME_TEAM_PREDECESSOR, DURATION_OVERRUN and unevidenced rework claims need a person's notes,
  so they are proposed and never reviewed automatically;
- **deviations** have no claim.

A simulated reviewer in the ``excusal_decider`` role, never the claimant, decides each submitted claim. It approves
only when the cited evidence holds (the part record was open, the cited phase was unfinished and belongs to another
team, the person called off); otherwise it rejects with a reason. Approved missed effort is relief E.

Then the source system moves on: rework found on the floor and QA rejections add rework jobs. Parts whose expected date
has passed arrive (or slip with ``part_slip``), undated shortages get a date, and a few newly found shortages arrive
undated.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any, Mapping

from ..domain import EdgeType, PhaseKind, digest_id, from_minute, to_minute
from ..performance.score import status_and_score
from .state import SourceState

OT_ALLOWANCE = 60
PULL_AHEAD_IDLE_HOURS = 2.0
REVIEWER = ("sim-excusal-decider", "excusal_decider")
ATTRIBUTOR = "focus-attribution"
CAUSE_CLAIM = {"part": "LATE_PART", "absence": "CAPACITY_SHORTAGE", "overrun": "DURATION_OVERRUN"}   # predecessor: by its culprit
PROPOSED_ONLY = frozenset({"SAME_TEAM_PREDECESSOR", "DURATION_OVERRUN"})


def scoring_text(policy: Any) -> str:
    """How a team's shift is scored, for the dashboard (the browser never holds the weights or the bands)."""
    bands = ", ".join(f"{label} {low}+" for label, low in policy.seq("score.bands"))
    wa, wh, rate = policy.str("score.attainment_weight"), policy.str("score.adherence_weight"), policy.str("score.oos_rate")
    return (f"Canonical team-shift score ({policy.str('score.policy_status').lower()} policy {policy.id}): 100 × ({wa} × attainment + {wh} × "
            f"adherence) − 100 × {rate} × out-of-sequence effort ÷ D, graded {bands}, F below. Goals are frozen per day from the first "
            f"accepted executable plan; a shift lead may add emergency rework the plan assigns. Attainment = planned work earned ÷ D. "
            f"Adherence = planned work earned ÷ (planned work earned + offplan work), so work done off the plan is discounted. "
            f"D = frozen effort + approved emergency additions − approved relief: "
            f"a missed task excused by an approved claim (late part, another team's unfinished predecessor, a call-off after "
            f"publication, rework from another team's defect) does not count against the team. Effort is standard person minutes.")


@dataclass(frozen=True)
class FloorSpec:
    duration_sigma: float = 0.15
    late_absence: float = 0.01         # a present person calls off after publication (per person and shift)
    part_slip: float = 0.15
    qa_reject: float = 0.04
    new_undated_per_aircraft_shift: float = 0.02
    undated_dated_per_shift: float = 0.4
    planned_absence: float = 0.03
    oos_work: float = 0.15             # a task whose predecessor is unfinished is worked out of sequence instead of waiting
    oos_discretionary: float = 0.6     # share of out-of-sequence work done by choice (charged), not directed
    deviation: float = 0.05            # a team works an unplanned ready task in place of one dispatched task
    pull_ahead: float = 0.35           # a team with idle hours pulls ready work forward
    unannounced_slip: float = 0.5      # share of part slips nobody enters before the part is needed (work finds it missing)
    kit_shortage: float = 0.003        # a dispatched task's kit is found short at the point of use: a new dated shortage
    upstream_defect: float = 0.35      # rework found on the floor traced to another team's finished work at the station


@dataclass(frozen=True)
class GoalUnit:
    """A frozen earning unit of a team-shift goal of record."""
    assignment_id: str
    phase: str
    job: str
    kind: str
    ordinal: int
    line: int
    team: str
    effort: int                        # frozen standard person minutes
    start: int                         # frozen planned start
    crew: tuple[str, ...]
    shift_number: int
    origin: str = "ORIGINAL"           # ORIGINAL (frozen) | AMENDMENT (approved emergency addition)
    scarce: bool = False               # its role needs a certificate group few of the frozen membership hold (SkillSteward)


@dataclass
class DayGoals:
    """One production day's goals of record and its progress so far (frozen keys are (job, kind, frozen ordinal))."""
    day: date
    plan_id: str
    units: dict[tuple[str, int], list[GoalUnit]]
    jobs: frozenset[str] = frozenset()                                         # jobs admitted when the goals froze
    keys: set[tuple[str, str, int]] = field(default_factory=set)
    done: dict[tuple[str, str, int], int] = field(default_factory=dict)        # frozen key -> completion minute
    progress: dict[tuple[str, str], int] = field(default_factory=dict)         # (job, kind) -> phases finished since freezing
    unlocked: dict[tuple[str, str, int], tuple[str, ...]] = field(default_factory=dict)  # frozen key -> jobs its completion released


def scarce_role(ctx: Any, phase: Any, members: tuple[str, ...], start: int, end: int, held: dict[tuple, int]) -> bool:
    """SkillSteward's scarce role (S14), as ``focus.analytics.progression`` judges it: a certificate group of the phase's role
    held over the shift window by at least one and at most ``badges.skill_steward_max_holders`` of the frozen membership."""
    limit = ctx.policy.int("badges.skill_steward_max_holders")
    for group in phase.role_demands[0].bundle.groups:
        key = (members, start, end, tuple(group))
        if key not in held:
            held[key] = sum(1 for m in members if any(ctx.eligibility.index.holds(m, cert, start, end)[0] for cert in group))
        if group and 0 < held[key] <= limit:
            return True
    return False


def frozen_members(ctx: Any, team: str, shift: Any) -> tuple[str, ...]:
    """The admitted membership a goal freezes with: the team's executable pool members of that shift."""
    pool = ctx.pool_for(team, shift)
    return tuple(sorted(ctx.pool_members(pool.id))) if pool is not None else ()


def freeze_day(plan: Any, ctx: Any, day: date, windows: Mapping[int, tuple[int, int]]) -> DayGoals:
    """The day's goals of record from an accepted plan: its assignments in each remaining shift window of the day."""
    units: dict[tuple[str, int], list[GoalUnit]] = defaultdict(list)
    members: dict[tuple[str, int], tuple[str, ...]] = {}
    held: dict[tuple, int] = {}
    for a in sorted(plan.assignments, key=lambda a: (to_minute(a.start), a.id)):
        phase = ctx.phases.get(a.phase)
        if phase is None or not phase.role_demands:
            continue
        start = to_minute(a.start)
        number = next((n for n, (w0, w1) in sorted(windows.items()) if w0 <= start < w1), None)
        if number is None:
            continue
        team = phase.role_demands[0].team
        if (team, number) not in members:
            members[(team, number)] = frozen_members(ctx, team, ctx.shift(day, number))
        w0, w1 = windows[number]
        units[(team, number)].append(GoalUnit(a.id, a.phase, a.job, phase.kind.value, phase.key.ordinal, a.line, team,
                                              phase.standard_person_minutes, start, tuple(c.person for c in a.crew), number,
                                              scarce=scarce_role(ctx, phase, members[(team, number)], w0, w1, held)))
    goals = DayGoals(day, plan.id, dict(units), frozenset(ctx.jobs))
    goals.keys = {(u.job, u.kind, u.ordinal) for us in goals.units.values() for u in us}
    return goals


@dataclass
class Outcome:
    rows: list[tuple]
    events: list[tuple]
    perf: list[tuple]
    units: list[tuple]
    claims: list[tuple]
    offplan: list[tuple]
    oos: list[tuple]
    completed_jobs: int
    amendments: list[tuple] = field(default_factory=list)
    unlocks: list[tuple] = field(default_factory=list)


def _local(dt_utc: datetime, tz) -> str:
    return dt_utc.astimezone(tz).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def attendance_rows(state: SourceState, day: date, rng: random.Random, absence: float, load_ts: str) -> list[dict]:
    """Every rostered person's attendance for the day's three shifts (absent with ``absence`` probability)."""
    rows = []
    for r in sorted(state.all("shift_roster"), key=lambda r: (r["SHIFT_NUMBER"], r["PERSON_KEY"])):
        present = rng.random() >= absence
        rows.append({"PRODUCTION_DAY": day.isoformat(), "SHIFT_NUMBER": int(r["SHIFT_NUMBER"]), "BEMS_ID": r["PERSON_KEY"], "PRESENT": present,
                     "HOURS": ("8.50" if int(r["SHIFT_NUMBER"]) != 3 else "7.00") if present else "0.00", "SOURCE": "SIM_BADGE",
                     "LOAD_TIMESTAMP": load_ts})
    return rows


def execute_shift(state: SourceState, shift_plan: Any, shift: Any, *, shift_no: int, week: int, rng: random.Random, spec: FloorSpec,
                  rework_rate: float, rework_minutes: list[int], cert_of_team: dict[str, str], tz, policy: Any,
                  goals: DayGoals | None) -> Outcome:
    ctx = shift_plan.context
    plan = shift_plan.plan
    s0, s1 = shift.start, shift.end
    phases = ctx.phases
    team_of_phase = {pid: (p.role_demands[0].team if p.role_demands else None) for pid, p in phases.items()}
    native_of = {p.id: p.key.native_id for p in ctx.snapshot.people}
    started: dict[str, int] = {}                                      # phase -> actual start minute (this shift)
    done_phase: dict[str, int] = {}                                   # phase -> actual end minute (this shift)
    satisfied = {p.key.id for job, plist in ctx.job_phases.items() for p in plist
                 if ctx.dispositions.get(job) is not None and ctx.dispositions[job].disposition.value == "verified_complete"}
    open_short: dict[tuple[int, str], dict] = {}
    for r in state.all("etracs_active"):
        open_short[(int(r["LINE_NUMBER"]), str(r["USED_ON"]))] = r
    shift_day = from_minute(s0).astimezone(tz).date()
    load_ts = _local(from_minute(s1 - 20), tz)
    base = dict(goals.progress) if goals else {}                       # (job, kind) -> phases finished earlier today
    events: list[tuple] = []

    def frozen_key(pid: str) -> tuple[str, str, int]:
        p = phases[pid]
        return p.job, p.kind.value, p.key.ordinal + base.get((p.job, p.kind.value), 0)

    # ---- call-offs after publication, written to attendance: the evidence of any capacity claim
    roster_team = {r["PERSON_KEY"]: r["TEAM"] for r in state.all("shift_roster") if int(r["SHIFT_NUMBER"]) == shift.number}
    called_off: set[str] = set()
    present: dict[str, int] = defaultdict(int)
    for r in sorted((r for r in state.all("attendance_shift") if r["PRODUCTION_DAY"] == shift.start_date.isoformat()
                     and int(r["SHIFT_NUMBER"]) == shift.number and r["PRESENT"]), key=lambda r: r["BEMS_ID"]):
        if rng.random() < spec.late_absence:
            called_off.add(r["BEMS_ID"])
            state.upsert("attendance_shift", {**r, "PRESENT": False, "HOURS": "0.00", "SOURCE": "SIM_LATE_CALLOFF", "LOAD_TIMESTAMP": _local(from_minute(s0), tz)})
            events.append((shift_no, "call_off", None, None, r["BEMS_ID"], "called off after publication"))
        elif roster_team.get(r["BEMS_ID"]):
            present[roster_team[r["BEMS_ID"]]] += 1

    found_missing: set[int] = set()
    kit_short: list[tuple[int, Any]] = []                             # shortages found at the point of use this shift

    def open_record(line: int, soi: str, at_minute: int) -> int | None:
        """The shortage record keeping the part off the floor at ``at_minute``, if any (no side effects)."""
        r = open_short.get((line, soi))
        if r is None:
            return None
        if r["RECORD_ID"] in state.missing_parts or not r.get("ETRAC_ECD"):
            return int(r["RECORD_ID"])
        ready_day = date.fromisoformat(str(r["ETRAC_ECD"])) + timedelta(days=1)         # end-of-local-date floor
        return int(r["RECORD_ID"]) if from_minute(at_minute).astimezone(tz).date() < ready_day else None

    def gates(pid: str, at: int | None = None) -> tuple[int, int, list[str], str | None]:
        """Hard edges into ``pid`` by type and lag: (start floor, end floor, unfinished predecessors, the binding one).
        With ``at``, a predecessor counts as finished only when it finished (or started, for SS/SF) by then."""
        start_floor, end_floor, unfinished, binding = s0, s0, [], None
        for e in ctx.preds.get(pid, ()):
            q = e.pred_phase
            if q in satisfied or q in ctx.releases:
                continue
            lag = e.lag_minutes or 0
            t = started.get(q) if e.type in (EdgeType.SS, EdgeType.SF) else done_phase.get(q)
            if t is None or (at is not None and t > at):
                unfinished.append(q)
                continue
            if e.type in (EdgeType.FF, EdgeType.SF):
                end_floor = max(end_floor, t + lag)
            elif t + lag > start_floor:
                start_floor, binding = t + lag, q
        return start_floor, end_floor, unfinished, binding

    def settle(pid: str) -> None:
        """A RELEASE (AND) node finishes when all its inputs have: links between jobs run through it."""
        stack = [pid]
        while stack:
            for e in ctx.succs.get(stack.pop(), ()):
                rel = e.succ_phase
                node = phases.get(rel)
                if node is None or node.kind is not PhaseKind.RELEASE or rel in done_phase:
                    continue
                floor, _, unfinished, _ = gates(rel)
                if not unfinished:
                    started[rel] = done_phase[rel] = floor
                    stack.append(rel)

    todo = sorted((a for a in plan.assignments if s0 <= to_minute(a.start) < s1), key=lambda a: (to_minute(a.start), a.id))
    planned_ids = {a.phase for a in todo}
    upcoming: dict[str, list[Any]] = defaultdict(list)                # team -> its tasks planned in the next ten hours
    for a in sorted((a for a in plan.assignments if s1 <= to_minute(a.start) < s1 + 600 and a.phase not in planned_ids),
                    key=lambda a: (to_minute(a.start), a.id)):
        upcoming[team_of_phase.get(a.phase) or "?"].append(a)
    # emergency additions: the shift lead amends the team's goal with rework found after the goals froze that the accepted
    # plan assigns in this shift (approved feasible additions: B = B0 + additions, the original units unchanged)
    added: dict[str, GoalUnit] = {}
    amendments: list[tuple] = []
    held: dict[tuple, int] = {}
    for a in todo if goals is not None else ():
        p, job = phases.get(a.phase), ctx.jobs.get(a.job)
        if (p is None or job is None or p.kind is PhaseKind.RELEASE or not p.role_demands or not job.is_rework or job.id in goals.jobs
                or frozen_key(a.phase) in goals.keys):
            continue
        team = team_of_phase.get(a.phase) or "?"
        added[a.phase] = GoalUnit(a.id, a.phase, a.job, p.kind.value, frozen_key(a.phase)[2], a.line, team, p.standard_person_minutes,
                                  to_minute(a.start), tuple(c.person for c in a.crew), shift.number, "AMENDMENT",
                                  scarce_role(ctx, p, frozen_members(ctx, team, shift), s0, s1, held))
        amendments.append((shift_no, team, digest_id("SIMAMD", shift_no, team, a.phase), a.id, a.job, a.phase, a.line, p.standard_person_minutes,
                           "ASSIGNED_IN_ACCEPTED_PLAN", f"sim-lead:{team}", f"emergency rework {job.key.native_id} found after the goals froze"))
    deviating = {team for team in sorted({team_of_phase.get(a.phase) or "?" for a in todo}) if rng.random() < spec.deviation}
    taken: set[str] = set()
    rows, oos_rows, offplan_rows = [], [], []
    worked_on: dict[str, str] = {}                                    # phase -> why it was worked outside the dispatch
    by_team: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    job_times: dict[str, list[int]] = {}
    missed: dict[str, tuple[str, str | None, str, int]] = {}          # phase -> (cause, blocking phase, evidence, when)
    stopped: dict[str, tuple[int, int, int]] = {}                     # phase -> (start, stop, true end) of work an overrun cut short
    worked: dict[str, int] = defaultdict(int)                         # team -> crew minutes actually worked

    def ready_at(pid: str, at: int) -> bool:
        p = phases.get(pid)
        job = ctx.jobs.get(p.job) if p is not None else None
        if p is None or job is None or p.kind is not PhaseKind.WORK:
            return False
        floor, _, unfinished, _ = gates(pid)
        job_floor, _ = ctx.job_floor(p.job)
        return not unfinished and floor <= at and (job_floor is None or job_floor <= at) and open_record(job.line, job.key.native_id, at) is None

    def finish(pid: str, job_id: str, start_m: int, end_m: int, crew: int, team: str) -> None:
        started[pid], done_phase[pid] = start_m, end_m
        t = job_times.setdefault(job_id, [start_m, end_m])
        t[0], t[1] = min(t[0], start_m), max(t[1], end_m)
        worked[team] += (end_m - start_m) * crew
        settle(pid)

    def offplan(team: str, a: Any, start_m: int, kind: str) -> int | None:
        """Work an upcoming task offplan from ``start_m``; its end when it finishes inside the overtime allowance."""
        length = max(5, round((a.work_minutes or (to_minute(a.end) - to_minute(a.start))) * math.exp(rng.gauss(0.0, spec.duration_sigma))))
        end_m = start_m + length
        if end_m > s1 + OT_ALLOWANCE:
            return None
        taken.add(a.phase)
        finish(a.phase, a.job, start_m, end_m, len(a.crew), team)
        worked_on[a.phase] = kind
        return end_m

    for a in todo:
        phase, job = phases.get(a.phase), ctx.jobs.get(a.job)
        if phase is None or job is None:
            continue
        team = team_of_phase.get(a.phase) or "?"
        start, end = to_minute(a.start), to_minute(a.end)
        planned = a.work_minutes or (end - start)
        crew_size = len(a.crew)
        stats = by_team[team]
        stats["planned_hours"] += planned * crew_size / 60
        cause, actual_start, actual_end, blocker, evidence = None, None, None, None, ""
        start_floor, end_floor, unfinished, binding = gates(a.phase)
        out_of_sequence: list[str] = []
        discretionary = False
        if unfinished:
            if phase.kind is PhaseKind.WORK and rng.random() < spec.oos_work:
                out_of_sequence, discretionary = unfinished, rng.random() < spec.oos_discretionary
            else:
                cause, blocker = "predecessor", unfinished[0]
        if cause is None and phase.kind is PhaseKind.WORK:
            rec = open_record(job.line, job.key.native_id, start)
            if rec is not None:
                if rec in state.missing_parts:
                    found_missing.add(rec)
                cause, evidence = "part", f"ETRACS:{rec}"
            elif rng.random() < spec.kit_shortage:
                rec = 800000000 + shift_no * 100000 + len(kit_short)
                kit_short.append((rec, job))
                cause, evidence = "part", f"ETRACS:{rec}"
        if cause is None:
            gone = [native_of.get(c.person, c.person) for c in a.crew if native_of.get(c.person, c.person) in called_off]
            if gone:
                cause, evidence = "absence", f"ATTENDANCE:{gone[0]}:{shift.start_date.isoformat()}:S{shift.number}"
        latest = max(start, start_floor)
        if cause is None and team in deviating:
            swap = next((u for u in upcoming.get(team, ()) if u.phase not in taken and ready_at(u.phase, latest)), None)
            if swap is not None and offplan(team, swap, latest, "deviation") is not None:
                cause = "deviation"
                deviating.discard(team)
        if cause is None:
            actual_start = latest
            actual_end = max(actual_start + max(5, round(planned * math.exp(rng.gauss(0.0, spec.duration_sigma)))), end_floor)
            if actual_start >= s1:
                cause, blocker, actual_start = "predecessor", binding, None
            elif actual_end > s1 + OT_ALLOWANCE:
                cause, evidence = "overrun", f"BURN:{a.phase}:{actual_end - actual_start}/{planned}"
        if out_of_sequence and actual_start is not None:
            # the violation is stored at the actual start whether or not the task finishes; by choice it is charged
            for q in out_of_sequence:
                oos_rows.append((shift_no, team, a.id, a.phase, q, int(discretionary), int(discretionary),
                                 phase.standard_person_minutes if discretionary else 0, from_minute(actual_start).isoformat(),
                                 "" if discretionary else f"DIRECTIVE:SIM-ED-{shift_no}-{a.id[-6:]}"))
        if cause is None:
            finish(a.phase, a.job, actual_start, actual_end, crew_size, team)
            if actual_end <= end + 15:
                stats["on_time"] += 1
        else:
            if actual_start is not None:
                started[a.phase] = actual_start
                worked[team] += (min(s1 + OT_ALLOWANCE, actual_end or s1) - actual_start) * crew_size
            if cause == "overrun":
                stopped[a.phase] = (actual_start, s1 + OT_ALLOWANCE, actual_end)   # the crew stops when the overtime allowance runs out
            missed[a.phase] = (cause, blocker if cause == "predecessor" else None, evidence, start)
        rows.append((shift_no, a.id, a.job, job.line, job.key.native_id, team, crew_size, a.start.isoformat(), a.end.isoformat(),
                     from_minute(actual_start).isoformat() if actual_start is not None else None,
                     from_minute(actual_end).isoformat() if actual_end is not None and cause is None else None,
                     "completed" if cause is None else "slipped", cause))

    # ---- idle teams pull ready work forward (offplan unless it is a frozen unit of the day)
    regular = shift.regular_cap
    for team in sorted(set(present) | set(by_team)):
        idle_hours = (present.get(team, 0) * regular - worked.get(team, 0)) / 60
        if idle_hours < PULL_AHEAD_IDLE_HOURS or rng.random() >= spec.pull_ahead:
            continue
        clock = max(s0, s1 - round(idle_hours * 60 / max(1, present.get(team, 1))))
        pulled = 0
        for a in upcoming.get(team, ()):
            if a.phase in taken or not ready_at(a.phase, clock):
                continue
            end_m = offplan(team, a, clock, "pull_ahead")
            if end_m is None:
                break
            clock, pulled = end_m, pulled + 1
            if pulled >= 2:
                break

    # ---- the source system moves on: finished phases are written back per job, so nothing is credited twice, and work an
    # overrun cut short keeps what it performed: the next admission plans only its remaining minutes (S08)
    completed = 0
    finished_by_job: dict[str, list[str]] = defaultdict(list)
    for pid in list(done_phase):
        node = phases.get(pid)
        if node is not None and node.kind is not PhaseKind.RELEASE:
            finished_by_job[node.job].append(pid)
    stopped_by_job: dict[str, list[str]] = defaultdict(list)
    for pid in stopped:
        stopped_by_job[phases[pid].job].append(pid)
    buyoff: set[str] = set()                                          # inspections taken with the job's buyoff at shift end
    for job_id in sorted(set(finished_by_job) | set(stopped_by_job)):
        pids, cut = finished_by_job.get(job_id, []), stopped_by_job.get(job_id, [])
        job = ctx.jobs[job_id]
        row = state.get("master_task_list", job.line, job.key.native_id)
        if row is None:
            continue
        left = [p for p in ctx.job_phases.get(job_id, ()) if p.kind is not PhaseKind.RELEASE and p.key.id not in done_phase
                and p.key.id not in satisfied]
        work_left = sum(stopped[p.key.id][2] - stopped[p.key.id][1] if p.key.id in stopped else p.work_minutes
                        for p in left if p.kind is PhaseKind.WORK)
        t0 = min(([job_times[job_id][0]] if job_id in job_times else []) + [stopped[p][0] for p in cut])
        if work_left <= 0:
            t1 = job_times[job_id][1]
            for p in left:                                            # only inspections left: the buyoff closes the job
                done_phase[p.key.id] = max(t1, min(s1, t1 + p.work_minutes))
                buyoff.add(p.key.id)
            finish_at = max(done_phase[p] for p in pids + [p.key.id for p in left])
            state.upsert("master_task_list", {**row, "SHOP_ORDER_STATUS": "COMPLETE", "ACT_START": row.get("ACT_START") or _local(from_minute(t0), tz),
                                              "ACT_COMP": _local(from_minute(finish_at), tz), "LOAD_TIMESTAMP": load_ts})
            completed += 1
            if row.get("IS_REWORK"):
                link = state.get("neo_graph_rework", job.line, job.key.native_id)
                if link is not None:
                    state.upsert("neo_graph_rework", {**link, "CHILD_STATUS": "COMPLETE", "SNAPSHOT_TS": load_ts})   # the master vocabulary
            if any(p.kind is PhaseKind.FINAL_QA for p in ctx.job_phases.get(job_id, ())) and rng.random() < spec.qa_reject:
                _new_rework(state, job.line, row, rng, rework_minutes, cert_of_team, load_ts, shift_no, "QA reject", events)
        else:
            prog = state.get("work_progress", job.line, job.key.native_id)
            performed = ((int(prog["PERFORMED_ELAPSED_MINUTES"]) if prog else 0) + sum(phases[p].work_minutes for p in pids
                                                                                      if phases[p].kind is PhaseKind.WORK)
                         + sum(stopped[p][1] - stopped[p][0] for p in cut))
            state.upsert("work_progress", {"LINE_NUMBER": job.line, "SOI": job.key.native_id, "PERFORMED_ELAPSED_MINUTES": performed,
                                           "REMAINING_ELAPSED_MINUTES": work_left, "AS_OF_UTC": from_minute(s1 - 20).isoformat(),
                                           "EVIDENCE_REF": f"SIM-PROGRESS:{shift_no}:{job.key.native_id}"})
            state.upsert("master_task_list", {**row, "SHOP_ORDER_STATUS": "IN WORK", "ACT_START": row.get("ACT_START") or _local(from_minute(t0), tz),
                                              "LOAD_TIMESTAMP": load_ts})
    for rec, job in kit_short:                                        # shortages found at the point of use are entered
        row = state.get("master_task_list", job.line, job.key.native_id) or {}
        new = (from_minute(s1).astimezone(tz).date() + timedelta(days=rng.randint(1, 3))).isoformat()
        state.upsert("etracs_active", {"RECORD_ID": rec, "LINE_NUMBER": job.line, "MINOR_MODEL_IDENT": row.get("MINOR_MODEL_IDENT"),
                                       "USED_ON": job.key.native_id, "ETRAC_ECD": new, "TEAM": row.get("TEAM"), "REASON_CODE": "KIT",
                                       "ETRAC_CREATED_DT": shift_day.isoformat(), "ITEM_NUMBER": f"K-{rec}", "LOAD_TIMESTAMP": load_ts})
        events.append((shift_no, "kit_short_at_point_of_use", job.line, job.key.native_id, None, f"expected {new}"))
    # rework found on the floor, against open or just-finished tasks of each aircraft's current station
    by_line_station: dict[int, str] = {}
    for f in ctx.snapshot.cs_facts:
        if f.earliest_at is not None and f.earliest_at <= from_minute(s1) and (f.line not in by_line_station or f.station > by_line_station[f.line]):
            by_line_station[f.line] = f.station
    station_tasks: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for r in state.all("master_task_list"):
        if not r["IS_REWORK"]:
            station_tasks[(r["LINE_NUMBER"], str(r["CURRENT_CONTROL_STATION"]))].append(r)
    for line in sorted(by_line_station):
        candidates = station_tasks.get((line, str(by_line_station[line])), [])
        done_teams = sorted({r["TEAM"] for r in candidates if r["SHOP_ORDER_STATUS"] == "COMPLETE"})
        count = _poisson(rng, rework_rate)
        for _ in range(min(count, len(candidates))):
            parent = candidates[rng.randrange(len(candidates))]
            # the defect may come from another team's finished work at the station (explicit defect-origin fact)
            upstream = [t for t in done_teams if t != parent["TEAM"]]
            origin = upstream[rng.randrange(len(upstream))] if upstream and rng.random() < spec.upstream_defect else parent["TEAM"]
            _new_rework(state, line, parent, rng, rework_minutes, cert_of_team, load_ts, shift_no, "found on the floor", events, origin)
        if rng.random() < spec.new_undated_per_aircraft_shift and candidates:
            target = candidates[rng.randrange(len(candidates))]
            if target["SHOP_ORDER_STATUS"] != "COMPLETE" and (line, target["SHOP_ORDER_IDENTIFIER"]) not in open_short:
                rec = 900000 + shift_no * 1000 + line % 1000
                state.upsert("etracs_active", {"RECORD_ID": rec, "LINE_NUMBER": line, "MINOR_MODEL_IDENT": target["MINOR_MODEL_IDENT"],
                                               "USED_ON": target["SHOP_ORDER_IDENTIFIER"], "ETRAC_ECD": None, "TEAM": target["TEAM"],
                                               "REASON_CODE": "LP", "ETRAC_CREATED_DT": shift_day.isoformat(), "ITEM_NUMBER": f"P-{rec}",
                                               "LOAD_TIMESTAMP": load_ts})
                events.append((shift_no, "shortage_found_undated", line, target["SHOP_ORDER_IDENTIFIER"], None, "no expected date yet"))
    # parts: arrive once their date has passed (or slip), undated ones get a date
    end_day = from_minute(s1).astimezone(tz).date()
    for r in sorted(list(state.all("etracs_active")), key=lambda r: r["RECORD_ID"]):
        ecd = r.get("ETRAC_ECD")
        if not ecd:
            if rng.random() < spec.undated_dated_per_shift:
                new = (end_day + timedelta(days=rng.randint(1, 7))).isoformat()
                state.upsert("etracs_active", {**r, "ETRAC_ECD": new, "LOAD_TIMESTAMP": load_ts})
                events.append((shift_no, "shortage_dated", r["LINE_NUMBER"], r["USED_ON"], None, f"expected {new}"))
            continue
        rec = r["RECORD_ID"]
        if rec in state.missing_parts and (rec in found_missing or date.fromisoformat(str(ecd)) < end_day):
            new = state.missing_parts.pop(rec)                     # the slip is entered once the part is found missing
            state.upsert("etracs_active", {**r, "ETRAC_ECD": new, "LOAD_TIMESTAMP": load_ts})
            events.append((shift_no, "part_slipped", r["LINE_NUMBER"], r["USED_ON"], None, f"{ecd} -> {new} (found missing)"))
            continue
        if date.fromisoformat(str(ecd)) < end_day:
            if rng.random() < spec.part_slip:
                new = (date.fromisoformat(str(ecd)) + timedelta(days=rng.randint(1, 3))).isoformat()
                if rng.random() < spec.unannounced_slip:
                    state.missing_parts[rec] = new                  # the system keeps the old date: planned work will find it missing
                    events.append((shift_no, "part_late_unannounced", r["LINE_NUMBER"], r["USED_ON"], None, f"expected {ecd}, really {new}"))
                    continue
                state.upsert("etracs_active", {**r, "ETRAC_ECD": new, "LOAD_TIMESTAMP": load_ts})
                events.append((shift_no, "part_slipped", r["LINE_NUMBER"], r["USED_ON"], None, f"{ecd} -> {new}"))
            else:
                state.remove("etracs_active", r)
                events.append((shift_no, "part_arrived", r["LINE_NUMBER"], r["USED_ON"], None, f"expected {ecd}"))


    # ---- each team's goal of record: excusal claims, reviewed decisions and the canonical score
    excusable = set(policy.seq("disruption.nominally_excusable"))
    rate = Fraction(policy.str("score.oos_rate"))
    frozen = goals.keys if goals is not None else set()
    output: dict[str, list[tuple[str, int]]] = defaultdict(list)     # team -> offplan output (phase, effort) this shift
    for p in sorted(done_phase):
        node = phases.get(p)
        if node is None or node.kind is PhaseKind.RELEASE or p in buyoff or frozen_key(p) in frozen or p in added:
            continue
        output[team_of_phase.get(p) or "?"].append((p, node.standard_person_minutes))
        job = ctx.jobs[node.job]
        offplan_rows.append((shift_no, team_of_phase.get(p) or "?", node.job, p, job.line, job.key.native_id, node.standard_person_minutes,
                             from_minute(started.get(p, done_phase[p])).isoformat(), from_minute(done_phase[p]).isoformat(),
                             worked_on.get(p, "outside_goals")))
    if goals is not None:
        goals.done.update({frozen_key(p): t for p, t in done_phase.items() if phases.get(p) is not None and phases[p].kind is not PhaseKind.RELEASE})
        goals.unlocked.update({frozen_key(p): jobs for p, jobs in released_by(ctx, done_phase, satisfied, open_record).items()})
    known_records = {int(r["RECORD_ID"]) for r in open_short.values()} | {rec for rec, _ in kit_short}

    def culprit(pid: str, at: int, trail: frozenset[str]) -> tuple[str, str | None, str]:
        """The cause a blocking phase passes on (``disruption.culpable_blockers``): a RELEASE node passes it to its unfinished
        inputs, a phase that was itself blocked passes it upstream, and one that was ready yet unfinished is culpable."""
        node = phases.get(pid)
        if node is None or pid in trail:
            return "phase", team_of_phase.get(pid), f"PHASE:{pid}"
        if node.kind is PhaseKind.RELEASE:
            _, _, unfinished, _ = gates(pid, at=at)
            return culprit(unfinished[0], at, trail | {pid}) if unfinished else ("phase", None, f"PHASE:{pid}")
        info = missed.get(pid)
        if info is not None:
            cause, blocker, evidence, when = info
            if cause == "part":
                return "part", None, evidence
            if cause == "predecessor" and blocker is not None:
                return culprit(blocker, when, trail | {pid})
            return "phase", team_of_phase.get(pid), f"PHASE:{pid}"
        _, _, unfinished, _ = gates(pid, at=at)
        if unfinished:
            return culprit(unfinished[0], at, trail | {pid})
        job = ctx.jobs.get(node.job)
        rec = open_record(job.line, job.key.native_id, at) if job is not None else None
        if rec is not None:
            return "part", None, f"ETRACS:{rec}"
        return "phase", team_of_phase.get(pid), f"PHASE:{pid}"

    def verify(claim: str, origin: str | None, evidence: str, units: list[GoalUnit], team: str) -> tuple[bool, str]:
        """The simulated reviewer checks the cited evidence against what the source records."""
        ref = evidence.partition(":")[2]
        first = min(u.start for u in units)
        if claim == "LATE_PART":
            ok = ref.isdigit() and int(ref) in known_records
            return ok, (f"verified: part record {ref} kept the part off the floor at the planned start" if ok
                        else f"rejected: part record {ref} was not open at the planned start")
        if claim == "CROSS_TEAM_PREDECESSOR":
            t = done_phase.get(ref)
            ok = origin is not None and origin != team and team_of_phase.get(ref) == origin and (t is None or t > first)
            return ok, (f"verified: {ref} of team {origin} was unfinished at the planned start" if ok
                        else f"rejected: {ref} does not show team {origin}'s unfinished work at the planned start")
        if claim == "CAPACITY_SHORTAGE":
            person = ref.split(":")[0]
            ok = person in called_off
            return ok, (f"verified: {person} called off after publication (attendance)" if ok
                        else f"rejected: attendance shows {person} present this shift")
        if claim == "REWORK_INJECTION":
            ok = origin is not None and origin != team
            return ok, (f"verified: the defect behind {ref} originated in team {origin}'s work" if ok
                        else f"rejected: the defect behind {ref} originated in the team's own work")
        return False, f"rejected: {claim} has no automatic verification"

    rework_origin = {(int(r["LINE_NUMBER"]), str(r["CHILD_SOI"])): r.get("DEFECT_ORIGIN_TEAM") for r in state.all("rework_classification")}
    teams_now = (set(by_team) | set(present) | set(output) | {u.team for u in added.values()}
                 | ({t for (t, n) in goals.units if n == shift.number} if goals else set()))
    units_rows, claims, perf, unlock_rows = [], [], [], []
    for team in sorted(teams_now):
        mine = (goals.units.get((team, shift.number), []) if goals is not None else []) + [u for u in added.values() if u.team == team]
        by_key = {(u.job, u.kind, u.ordinal): u for u in mine}
        groups: dict[tuple[str, str | None, str, str], list[tuple[str, str, int]]] = defaultdict(list)
        facts: dict[tuple[str, str, int], tuple[str, str | None, str | None, str | None, str | None]] = {}
        unexplained: list[tuple[str, str, int]] = []
        for key, u in by_key.items():
            if (u.phase in done_phase) if u.origin == "AMENDMENT" else (goals is not None and key in goals.done):
                facts[key] = ("EARNED", None, None, None, None)
                unlock_rows.extend((shift_no, team, u.assignment_id, u.phase, u.line, job) for job in (goals.unlocked.get(key, ()) if goals else ()))
                continue
            now = u.ordinal - base.get((u.job, u.kind), 0)
            cur = f"{u.job}.{u.kind}.{now}"
            info = missed.get(cur) if now >= 0 else None
            if info is None and cur in phases:
                # not worked this shift (a replan moved it): what kept it from its frozen start
                _, _, unfinished, _ = gates(cur, at=u.start)
                job = ctx.jobs.get(u.job)
                rec = open_record(job.line, job.key.native_id, u.start) if job is not None else None
                gone = [p for p in (native_of.get(c, c) for c in u.crew) if p in called_off]
                if unfinished:
                    info = ("predecessor", unfinished[0], "", u.start)
                elif rec is not None:
                    info = ("part", None, f"ETRACS:{rec}", u.start)
                elif gone:
                    info = ("absence", None, f"ATTENDANCE:{gone[0]}:{shift.start_date.isoformat()}:S{shift.number}", u.start)
            if info is None:
                facts[key] = ("MISSED", "replanned", None, None, None)          # no disruption on record: nothing to claim
                unexplained.append(key)
                continue
            cause, blocker, evidence, when = info
            if cause == "deviation":
                facts[key] = ("MISSED", cause, None, None, None)
                continue
            if cause == "predecessor":
                kind, origin, evidence = culprit(blocker, when, frozenset({cur})) if blocker else ("phase", None, "")
                if kind == "part":
                    claim, origin, status = "LATE_PART", None, "SUBMITTED"
                elif origin is not None and origin != team:
                    claim, status = "CROSS_TEAM_PREDECESSOR", "SUBMITTED"
                else:
                    claim, origin, status = "SAME_TEAM_PREDECESSOR", None, "PROPOSED"
            else:
                claim, origin = CAUSE_CLAIM[cause], None
                status = "PROPOSED" if claim in PROPOSED_ONLY else "SUBMITTED"
            groups[(claim, origin, evidence, status)].append(key)
            facts[key] = ("MISSED", cause, claim, origin, status)
        # rework this team-shift took on outside its frozen units (approved additions or offplan work), with the defect origin
        # from the rework classification; one whose defect came from another team is cited first (relief is a union)
        reworked = [u.phase for u in mine if u.origin == "AMENDMENT"] + [p for p, _ in output.get(team, ())
                                                                          if phases[p].job in ctx.jobs and ctx.jobs[phases[p].job].is_rework]
        injected = sorted(((p, rework_origin.get((ctx.jobs[phases[p].job].line, ctx.jobs[phases[p].job].key.native_id))) for p in reworked),
                          key=lambda x: (x[1] in (None, team), x[0]))
        if unexplained and injected:                                   # the rework explains otherwise unexplained misses
            p, origin = injected[0]
            status = "SUBMITTED" if origin else "PROPOSED"             # without defect-origin evidence only proposed
            job = ctx.jobs[phases[p].job]
            groups[("REWORK_INJECTION", origin, f"REWORK:{job.key.native_id}", status)].extend(unexplained)
            for key in unexplained:
                facts[key] = ("MISSED", "rework", "REWORK_INJECTION", origin, status)
        relieved: set[tuple[str, str, int]] = set()
        for (claim, origin, evidence, status), keys in sorted(groups.items(), key=lambda kv: repr(kv[0])):
            cid = digest_id("SIMDCL", shift_no, team, claim, origin, evidence, sorted(keys))
            reporter = f"sim-lead:{team}" if claim == "CAPACITY_SHORTAGE" else ATTRIBUTOR
            decision, decided_by, reason = None, None, "proposed: needs a person's notes before any review"
            if status == "SUBMITTED":
                ok, reason = verify(claim, origin, evidence, [by_key[k] for k in keys], team)
                decided_by = REVIEWER[0]
                if ok and claim in excusable:
                    decision = "APPROVED"
                    relieved.update(keys)
                else:
                    decision = "REJECTED"
                    if ok:
                        reason = f"rejected: {claim} is outside the five nominally excusable causes"
            claims.append((shift_no, team, cid, claim, origin, team, status, "MANUAL" if claim == "CAPACITY_SHORTAGE" else "AUTO", len(keys),
                           sum(by_key[k].effort for k in keys), evidence, decision, decided_by, reason, reporter))
        charged = {frozen_key(r[3]) for r in oos_rows if r[1] == team and r[6] and r[3] in phases and frozen_key(r[3]) in by_key}
        B0 = sum(u.effort for u in mine if u.origin == "ORIGINAL")
        additions = sum(u.effort for u in mine if u.origin == "AMENDMENT")
        Cp = sum(u.effort for k, u in by_key.items() if facts[k][0] == "EARNED")
        E = sum(by_key[k].effort for k in relieved)
        U = sum(e for _, e in output.get(team, ()))
        oos_effort = sum(by_key[k].effort for k in charged)
        parts = {"B0": Fraction(B0), "B": Fraction(B0 + additions), "E": Fraction(E), "Cp": Fraction(Cp), "U": Fraction(U), "O": rate * oos_effort,
                 "reasons": []}
        status, result, why = status_and_score(parts, policy, [])
        for key, u in by_key.items():
            status_, cause, claim, origin, cstatus = facts[key]
            units_rows.append((shift_no, team, u.assignment_id, u.job, u.phase, u.line, u.effort, status_, cause, claim, origin, cstatus,
                               int(key in relieved), int(key in charged), u.origin, int(u.scarce)))
        available = present.get(team, 0) * regular / 60
        score = result["score"]
        counts = {c: sum(1 for f in facts.values() if f[1] == c) for c in ("part", "predecessor", "absence", "overrun", "deviation")}
        perf.append((shift_no, shift.start_date.isoformat(), shift.number, week, team, len(mine), sum(1 for f in facts.values() if f[0] == "EARNED"),
                     int(by_team.get(team, {}).get("on_time", 0)), counts["part"], counts["absence"], counts["predecessor"], counts["overrun"],
                     counts["deviation"], round(by_team.get(team, {}).get("planned_hours", 0.0), 2), round(worked.get(team, 0) / 60, 2),
                     present.get(team, 0), round(available, 2), B0, additions, B0 + additions, E, Cp, U, oos_effort, len(relieved),
                     sum(1 for u in mine if u.origin == "AMENDMENT"), len(output.get(team, ())), len(charged),
                     None if result["attainment"] is None else float(result["attainment"]),
                     None if result["adherence"] is None else float(result["adherence"]),
                     round(worked.get(team, 0) / 60 / available, 4) if available else None,
                     None if score is None else float(score), result["grade"], status.value.upper(),
                     None if result["base"] is None else float(result["base"]), float(parts["O"]),
                     None if result["final"] is None else float(result["final"]), policy.id, policy.hash, "; ".join(why) or None))
    if goals is not None:                                             # remaining phases restart their ordinals after write-back
        for p in done_phase:
            node = phases.get(p)
            if node is not None and node.kind is not PhaseKind.RELEASE:
                k = (node.job, node.kind.value)
                goals.progress[k] = goals.progress.get(k, 0) + 1
    return Outcome(rows, events, perf, units_rows, claims, offplan_rows, oos_rows, completed, amendments, unlock_rows)


def released_by(ctx: Any, done: Mapping[str, int], satisfied: set[str], open_record: Any) -> dict[str, tuple[str, ...]]:
    """dependency_unlocked (S14), as ``focus.analytics.progression`` derives it: for each phase finished this shift, the other
    jobs downstream (through RELEASE nodes) whose hard prerequisites were all satisfied with this completion the last of them.
    A prerequisite finished before the shift counts as earlier; a part gates through its open shortage record, as on the floor."""
    phases = ctx.phases

    def when(q: str, trail: frozenset[str] = frozenset()) -> int | None:
        if q in satisfied or q in ctx.releases or q not in phases:
            return -1
        if phases[q].kind is PhaseKind.RELEASE:
            times = [when(e.pred_phase, trail | {q}) for e in ctx.preds.get(q, ()) if e.pred_phase not in trail]
            return None if any(t is None for t in times) else max(times, default=-1)
        return done.get(q)

    out: dict[str, tuple[str, ...]] = {}
    for p, t in sorted(done.items()):
        node = phases.get(p)
        if node is None or node.kind is PhaseKind.RELEASE:
            continue
        frontier, seen, released = [e.succ_phase for e in ctx.succs.get(p, ())], set(), set()
        while frontier:
            n = frontier.pop()
            target = phases.get(n)
            if n in seen or target is None:
                continue
            seen.add(n)
            if target.kind is PhaseKind.RELEASE:
                frontier.extend(e.succ_phase for e in ctx.succs.get(n, ()))
                continue
            job = ctx.jobs.get(target.job)
            if job is None or target.job == node.job or target.job in released:
                continue
            times = [when(e.pred_phase) for e in ctx.preds.get(n, ())]
            if times and all(x is not None for x in times) and max(times) == t and open_record(job.line, job.key.native_id, t) is None:
                released.add(target.job)
        if released:
            out[p] = tuple(sorted(released))
    return out


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _new_rework(state: SourceState, line: int, parent: dict, rng: random.Random, rework_minutes: list[int], cert_of_team: dict[str, str],
                load_ts: str, shift_no: int, why: str, events: list, origin: str | None = None) -> None:
    """A rework job on ``parent``, executed by the parent's team; ``origin`` is the team whose work the defect came from
    (the parent's own team when not given)."""
    existing = state.next_rework.get(line, 0)
    child = f"R{existing:05d}"
    while state.get("master_task_list", line, child) is not None:
        existing += 1
        child = f"R{existing:05d}"
    state.next_rework[line] = existing + 1
    team = parent["TEAM"]
    cls = rng.choice(("NC", "PU", "CI"))
    state.upsert("master_task_list", {
        "SHOP_ORDER_IDENTIFIER": child, "SHOP_ORDER_DESCRIPTION": f"Rework on {parent['SHOP_ORDER_IDENTIFIER']} ({why})", "LINE_NUMBER": line,
        "SHOP_ORDER_STATUS": "AVAILABLE", "TEAM": team, "CURRENT_CONTROL_STATION": parent["CURRENT_CONTROL_STATION"],
        "MINOR_MODEL_IDENT": parent["MINOR_MODEL_IDENT"], "CUSTOMER_CODE": parent["CUSTOMER_CODE"], "PARENT_SOI": parent["SHOP_ORDER_IDENTIFIER"],
        "SUPERINTENDENT": parent["SUPERINTENDENT"], "FIRST_AVAILABLE": load_ts, "ACT_START": None, "ACT_COMP": None, "LOAD_TIMESTAMP": load_ts,
        "PLANNING_SYSTEM_NAME": "SYNTH-PS", "IS_REWORK": True, "RECORD_KEY": 10_000_000 + shift_no * 10_000 + existing})
    state.upsert("flow_budget", {"JOB_NUMBER": child, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": None, "TOTAL_MECHANICS": 2,
                                 "PRIMARY_MECHANIC_COUNT": 2, "PRIMARY_MECHANIC_DURATION": None, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                                 "TOTAL_LABOR_MINUTES": None, "DURATION_SOURCE": "REWORK_STATISTIC", "LOAD_TIMESTAMP": load_ts})
    state.upsert("soi_cert_requirements", {"SOI_ID": child, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": "PROC REWORK",
                                           "CERT_CODE": cert_of_team.get(team, "C01"), "CERT_TYPE": "CERT", "REQ_CERT": "Y",
                                           "SNAPSHOT_TS": "2026-10-25T05:00:00"})
    state.upsert("neo_graph_rework", {"LINE_NUMBER": line, "PARENT_SOI": parent["SHOP_ORDER_IDENTIFIER"], "CHILD_SOI": child,
                                      "CHILD_STATUS": "OPEN", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
    state.upsert("rework_classification", {"LINE_NUMBER": line, "CHILD_SOI": child, "REWORK_CLASS": cls, "ROOT_SOI": parent["SHOP_ORDER_IDENTIFIER"],
                                           "DEFECT_ORIGIN_TEAM": origin or team, "EVIDENCE_REF": f"SIM-NCR-{line}-{shift_no}-{existing}"})
    events.append((shift_no, "rework_found", line, child, None, why))
