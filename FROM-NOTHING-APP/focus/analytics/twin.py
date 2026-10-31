"""Digital twin: one read-only, reconciled view of observed, planned, forecast and unknown state (S16 TWIN, P21).

**Keys.** Every cell is keyed by the canonical revision it was built from and by its coordinates:

* revision: the source run, the accepted EXECUTABLE and FORECAST plans, the policy, and the current evaluation
  revision of the cell's team-shift;
* coordinates: team, shift start date, shift number and aircraft line.

A new source run, plan head, policy or evaluation revision is therefore a new key. A correction appends an
evaluation revision; it never rewrites the earlier one, which stays readable in history.

**Layers.** They are never mixed:

* ``observed`` holds measured facts only. Effort comes from the stored canonical evaluation evidence of each
  team-shift: earned, missed, relieved, pending, unknown, offplan and charged out-of-sequence effort. Job progress
  comes from verified lifecycle facts at the observation cutoff. Nothing synthetic or forecast is ever counted as
  measured.
* ``planned`` is the accepted EXECUTABLE plan: standard person minutes under the frozen-goal rule
  (``publication.serializer.earning_phase``), duty minutes by kind from the duty ledger, and OT.
* ``forecast`` is the accepted FORECAST plan, labeled CONDITIONAL_FORECAST. It carries the plan's conditional
  reasons and the duty of virtual support people.
* ``unknown`` is what no layer can state: jobs with no placement in either plan, lifecycles that are unknown,
  goals not evaluated yet and people whose attendance is unknown.

**Behind-station value (S01).** Each cell's ``recognition`` is the gate recognition frozen in its goal of record: base
value and behind-station bonus per job (weight, cap and grace from policy, explicit date basis), labeled
FROZEN_AT_PUBLICATION and never revalued. ``behind_station`` values each in-scope line and station gate on one display
date, the selected shift date or else the local knowledge date, with its basis, ages, grace and factor.

**Reconciliation.** These checks are returned with the projection. A failing check is a typed reason, never hidden.

* Each layer's cells sum exactly to its team, shift, line and grand totals (integers, never floats).
* Each layer's job states partition the census, per line, per team and in total.
* On an obligation day of the plan of record, the planned standard effort equals the frozen B0.
* Each evaluated team-shift's observed effort equals its stored evaluation's components.
* Each goal of record's frozen recognition base equals its frozen effort over ``recognition.base_divisor``.
* The planned duty minutes equal the duty ledger.

Nothing here schedules, evaluates, signs or writes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from ..calendar import parse_shift_id
from ..domain import Disposition, DutyKind, PhaseKind

TWIN_VERSION = "focus-twin-v1"
DUTY_KINDS = tuple(k.value for k in DutyKind)
FORECAST_BASIS = "CONDITIONAL_FORECAST"
MEASURED_BASIS = "CANONICAL_EVALUATION_EVIDENCE"
FROZEN_BASIS = "FROZEN_AT_PUBLICATION"
PLACED = {Disposition.SCHEDULED.value, Disposition.CONDITIONAL.value, Disposition.VERIFIED_COMPLETE.value}

Coord = tuple[str, date, int, "int | None"]          # team, shift start date, shift number, aircraft line


# ---------------------------------------------------------------------- measures

def blank_plan() -> dict[str, Any]:
    return {"standard_minutes": 0, "assignments": 0, "conditional_assignments": 0, "duty_minutes": {k: 0 for k in DUTY_KINDS},
            "ot_minutes": 0, "virtual_duty_minutes": 0}


def blank_recognition() -> dict[str, Any]:
    return {"base_value": Fraction(0), "bonus_value": Fraction(0), "jobs": 0, "behind_jobs": 0, "unavailable_jobs": 0, "date_bases": {}}


def blank_observed() -> dict[str, Any]:
    return {"goal_minutes": 0, "goal_original_minutes": 0, "earned_minutes": 0, "missed_minutes": 0, "relieved_minutes": 0,
            "pending_minutes": 0, "unknown_minutes": 0, "offplan_minutes": 0, "offplan_effort_unknown": 0, "oos_charged_minutes": 0,
            "units": 0, "earned_units": 0}


def add(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Exact sum of two measure dicts (nested dicts of integers)."""
    out: dict[str, Any] = {}
    for k in sorted(a.keys() | b.keys()):
        x, y = a.get(k), b.get(k)
        out[k] = add(x or {}, y or {}) if isinstance(x, dict) or isinstance(y, dict) else (x or 0) + (y or 0)
    return out


def total(items: Iterable[dict[str, Any]], blank: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    out = blank()
    for m in items:
        out = add(out, m)
    return out


def shift_label(day: date, number: int) -> str:
    return f"{day.isoformat()}:S{number}"


# ---------------------------------------------------------------------- inputs

class Inputs:
    """The immutable facts one twin is built from, plus the indexes every layer shares."""

    def __init__(self, services: Any, canon: Any, registry: Any, load_snapshot: Callable[[str], Any]):
        self.services, self.canon, self.registry = services, canon, registry
        self.site = services.settings.site
        snap = canon.snapshot
        self.snapshots = {snap.id: snap}
        for purpose in ("EXECUTABLE", "FORECAST"):
            cur = canon.current(purpose)
            if cur is not None and cur.artifact.snapshot_id not in self.snapshots:
                other = load_snapshot(cur.artifact.snapshot_id)
                if other is not None:
                    self.snapshots[other.id] = other
        self.job_line: dict[str, int] = {}
        self.job_team: dict[str, str | None] = {}
        for s in self.snapshots.values():
            for j in s.jobs:
                self.job_line.setdefault(j.id, j.key.line)
                self.job_team.setdefault(j.id, j.team)

    def plan_snapshot(self, purpose: str) -> Any:
        cur = self.canon.current(purpose)
        return None if cur is None else self.snapshots.get(cur.artifact.snapshot_id)

    def line_of(self, job: str | None) -> int | None:
        return None if job is None else self.job_line.get(job)


# ---------------------------------------------------------------------- planned and forecast layers

def plan_cells(inp: Inputs, purpose: str) -> tuple[dict[Coord, dict[str, Any]], list[dict[str, Any]]]:
    """Cells of one accepted plan: earning standard effort, assignments and every duty minute, by coordinate."""
    from ..publication.serializer import earning_phase

    plan, snap = inp.canon.plan(purpose), inp.plan_snapshot(purpose)
    cells: dict[Coord, dict[str, Any]] = defaultdict(blank_plan)
    notes: list[dict[str, Any]] = []
    if plan is None or snap is None:
        return {}, notes
    phases = {p.key.id: p for p in snap.phases}
    people = {p.key.id: p for p in snap.people}
    members: dict[tuple[str, date, int], str] = {}
    for pool in snap.pools:
        for m in (pool.executable_members if purpose == "EXECUTABLE" else pool.forecast_members):
            members[(m, pool.key.start_date, pool.key.shift_number)] = pool.key.team
    assignments = {a.id: a for a in plan.assignments}
    for a in plan.assignments:
        p = phases.get(a.phase)
        if not earning_phase(p, a):
            continue
        _, day, number = parse_shift_id(a.shift_id)
        cell = cells[(p.role_demands[0].team, day, number, a.line)]
        cell["standard_minutes"] += p.standard_person_minutes
        cell["assignments"] += 1
        cell["conditional_assignments"] += 1 if a.conditional_reasons else 0
    for d in plan.duties:
        minutes = int((d.end - d.start).total_seconds() // 60)
        if d.shift_id is None:
            notes.append({"code": "DUTY_WITHOUT_SHIFT", "duty_id": d.id, "minutes": minutes})
            continue
        _, day, number = parse_shift_id(d.shift_id)
        team = members.get((d.person, day, number))
        if team is None:
            notes.append({"code": "DUTY_PERSON_NOT_POOLED", "duty_id": d.id, "minutes": minutes})
            continue
        a = assignments.get(d.assignment_id) if d.assignment_id else None
        cell = cells[(team, day, number, a.line if a is not None else d.aircraft_to)]
        cell["duty_minutes"][d.kind.value] += minutes
        cell["ot_minutes"] += d.ot_minutes
        person = people.get(d.person)
        if person is not None and person.synthetic:
            cell["virtual_duty_minutes"] += minutes
    return dict(cells), notes


def recognition_cells(inp: Inputs, visible: Callable[[str | None], bool]) -> tuple[dict[Coord, dict[str, Any]], list[dict[str, Any]]]:
    """Frozen gate recognition of each goal of record, by coordinate, and its check against the goal's frozen effort.

    Base and behind-station bonus value are read from the gate contexts publication froze in the goal
    (``analytics.points.gate_context``), valued at the goal's shift date. They are shown, never revalued.
    """
    divisor = inp.services.policy.int("recognition.base_divisor")
    cells: dict[Coord, dict[str, Any]] = defaultdict(blank_recognition)
    checks = []
    for (team, shift_id), record in sorted(inp.registry.records.items()):
        if record.goal is None or not visible(team):
            continue
        _, day, number = parse_shift_id(shift_id)
        base = Fraction(0)
        for job, c in sorted(record.goal.gate_contexts.items()):
            cell = cells[(team, day, number, inp.line_of(job))]
            cell["base_value"] += c.base_value
            cell["bonus_value"] += c.bonus_value or 0
            cell["jobs"] += 1
            cell["behind_jobs"] += 1 if c.behind_raw else 0
            cell["unavailable_jobs"] += 1 if c.basis == "UNAVAILABLE" else 0
            cell["date_bases"][c.basis] = cell["date_bases"].get(c.basis, 0) + 1
            base += c.base_value
        frozen = Fraction(record.goal.standard_person_minutes, divisor)
        checks.append({"check": "RECOGNITION_EQUALS_FROZEN_EFFORT", "team": team, "shift_id": shift_id, "ok": base == frozen,
                       "mismatch": {} if base == frozen else {"twin": str(base), "frozen": str(frozen)}})
    return dict(cells), checks


def behind_stations(inp: Inputs, visible: Callable[[str | None], bool], states: dict[str, tuple[str, tuple[str, ...]]], valuation: date, *,
                    team: str | None, line: int | None) -> list[dict[str, Any]]:
    """Each aircraft line and station gate in scope, valued on ``valuation`` (S01: weight, cap, grace, explicit date basis)."""
    from .points import behind_station

    facts = {(f.line, f.station): f for f in inp.canon.snapshot.cs_facts}
    jobs: Counter[tuple[int, str | None]] = Counter()
    open_jobs: Counter[tuple[int, str | None]] = Counter()
    for j in inp.canon.snapshot.jobs:
        if visible(j.team) and (team is None or j.team == team) and (line is None or j.key.line == line):
            jobs[(j.key.line, j.station)] += 1
            open_jobs[(j.key.line, j.station)] += 0 if states[j.id][0] == "complete" else 1
    out = []
    for (ln, station), n in sorted(jobs.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        f = facts.get((ln, station))
        gate = f.gate_date if f is not None and f.status == "ADMITTED" else None
        out.append({"line": ln, "station": station, "cs_status": "NO_CS_FACT" if f is None else f.status, "jobs": n,
                    "open_jobs": open_jobs[(ln, station)]} | behind_station(inp.services.policy, gate, valuation))
    return out


def conditional_reasons(plan: Any) -> dict[str, int]:
    """The forecast's conditional reason codes (the text before the first colon), counted per assignment."""
    counts: Counter[str] = Counter()
    for a in (plan.assignments if plan is not None else ()):
        for r in dict.fromkeys(x.split(":", 1)[0] for x in a.conditional_reasons):
            counts[r] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------- observed layer

def team_shifts(inp: Inputs) -> list[tuple[str, str]]:
    """Every team-shift of an obligation day of record: the pools of those days plus the goals of record."""
    days = set(inp.registry.day_plan)
    keys = {(r.team, r.shift_id) for r in inp.registry.records.values()}
    for pool in inp.canon.snapshot.pools:
        if pool.key.start_date in days:
            keys.add((pool.key.team, f"{inp.site}:{pool.key.start_date.isoformat()}:S{pool.key.shift_number}"))
    return sorted(keys, key=lambda k: (parse_shift_id(k[1])[1], parse_shift_id(k[1])[2], k[0]))


def observed_cells(inp: Inputs, visible: Callable[[str | None], bool]) -> tuple[dict[Coord, dict[str, Any]], dict[tuple[str, str], Any],
                                                                                  list[dict[str, Any]], list[dict[str, Any]]]:
    """Observed effort per coordinate from each team-shift's current stored evaluation evidence (never recomputed)."""
    from ..performance import history

    services = inp.services
    scope = services.settings.scope_key
    cells: dict[Coord, dict[str, Any]] = defaultdict(blank_observed)
    evaluations: dict[tuple[str, str], Any] = {}
    unknown: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for team, shift_id in team_shifts(inp):
        if not visible(team):
            continue
        goal_id = inp.registry.goal_id_for(team, shift_id)
        record = inp.registry.records.get((team, shift_id))
        e = None if goal_id is None else history.current(services, scope, history.series_for_goal(services, goal_id))
        if e is None:
            if record is not None and record.B0 > 0:
                unknown.append({"team": team, "shift_id": shift_id, "goal_id": goal_id, "B0": record.B0, "code": "NOT_EVALUATED",
                                "detail": "a goal of record exists but no canonical evaluation has been stored yet"})
            continue
        proof = history.evidence(services, e.evidence_hash)
        if proof is None:
            unknown.append({"team": team, "shift_id": shift_id, "goal_id": goal_id, "evaluation_id": e.id, "code": "EVIDENCE_MISSING",
                            "detail": "the stored evaluation's evidence record is missing"})
            continue
        evaluations[(team, shift_id)] = e
        _, day, number = parse_shift_id(shift_id)
        mine: dict[int | None, dict[str, Any]] = defaultdict(blank_observed)
        for f in proof.unit_facts:
            c = mine[inp.line_of(f.job)]
            c["goal_minutes"] += f.effort
            c["goal_original_minutes"] += f.effort if f.origin == "ORIGINAL" else 0
            c["units"] += 1
            if f.status == "EARNED":
                c["earned_minutes"] += f.effort
                c["earned_units"] += 1
            elif f.status == "MISSED":
                c["missed_minutes"] += f.effort
                c["relieved_minutes"] += f.effort if f.relief else 0
            elif f.status == "PENDING":
                c["pending_minutes"] += f.effort
            else:
                c["unknown_minutes"] += f.effort
            c["oos_charged_minutes"] += f.effort if f.oos_charged else 0
        for o in proof.offplan:
            c = mine[inp.line_of(o.job)]
            if o.effort is None:
                c["offplan_effort_unknown"] += 1
            else:
                c["offplan_minutes"] += o.effort
        for line, measures in mine.items():
            cells[(team, day, number, line)] = add(cells[(team, day, number, line)], measures)
        checks.append(evaluation_check(team, shift_id, e, total(mine.values(), blank_observed), services.policy))
        if e.status.value == "unknown":
            unknown.append({"team": team, "shift_id": shift_id, "goal_id": goal_id, "evaluation_id": e.id, "code": "EVALUATION_UNKNOWN",
                            "detail": "; ".join(e.reasons[:3])})
    return dict(cells), evaluations, unknown, checks


def evaluation_check(team: str, shift_id: str, e: Any, observed: dict[str, Any], policy: Any) -> dict[str, Any]:
    """The twin's observed effort of a team-shift against its stored evaluation's exact components."""
    rate = Fraction(policy.str("score.oos_rate"))
    pairs = {"B0": (observed["goal_original_minutes"], e.B0), "B": (observed["goal_minutes"], e.B),
             "Cp": (observed["earned_minutes"], e.Cp), "E": (observed["relieved_minutes"], e.E),
             "U": (None if observed["offplan_effort_unknown"] else observed["offplan_minutes"], e.U)}
    if e.O is not None:
        pairs["O"] = (rate * observed["oos_charged_minutes"], e.O)
    bad = {k: {"twin": str(a), "evaluation": str(b)} for k, (a, b) in pairs.items() if (a is None) != (b is None) or (a is not None and a != b)}
    return {"check": "OBSERVED_EQUALS_EVALUATION", "team": team, "shift_id": shift_id, "evaluation_id": e.id, "ok": not bad, "mismatch": bad}


# ---------------------------------------------------------------------- inventory

def observed_job_states(inp: Inputs, as_of: datetime | None) -> tuple[dict[str, tuple[str, tuple[str, ...]]], str | None]:
    """Each census job's measured state at the observation cutoff: complete, in_progress, no_start_observed or unknown."""
    from ..performance.lifecycle import GraphView, ObservationView

    snap = inp.canon.snapshot
    source_complete = {d.job for d in snap.dispositions if d.disposition is Disposition.VERIFIED_COMPLETE}
    by_job: dict[str, list[Any]] = defaultdict(list)
    for p in snap.phases:
        if p.kind is not PhaseKind.RELEASE:
            by_job[p.job].append(p)
    cycles: dict[str, Any] = {}
    error = None
    if as_of is not None:
        view = ObservationView.build(inp.services, as_of, registry=inp.registry, graph=GraphView.of(snap))
        cycles = view.lifecycles
        error = "; ".join(view.errors) or None
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for j in snap.jobs:
        if j.id in source_complete:
            out[j.id] = ("complete", ("SOURCE_VERIFIED_COMPLETE",))
            continue
        if as_of is None:
            out[j.id] = ("unknown", ("NO_OBSERVATION_RUN",))
            continue
        cs = [cycles.get(p.key.id) for p in by_job.get(j.id, [])]
        if cs and all(c is not None and c.completed_at is not None for c in cs):
            out[j.id] = ("complete", ("ALL_PHASES_VERIFIED_COMPLETE",))
        elif any(c is not None and c.unknown for c in cs):
            out[j.id] = ("unknown", tuple(sorted({r for c in cs if c is not None and c.unknown for r in c.reasons})) or ("LIFECYCLE_UNKNOWN",))
        elif any(c is not None and (c.started_at is not None or c.completed_at is not None or c.segments) for c in cs):
            out[j.id] = ("in_progress", ())
        else:
            out[j.id] = ("no_start_observed", ())
    return out, error


def inventory(inp: Inputs, visible: Callable[[str | None], bool], observed: dict[str, tuple[str, tuple[str, ...]]], *,
              team: str | None, line: int | None) -> dict[str, Any]:
    """Every census job once per layer, by line and by team; the unknown layer lists jobs no layer can place."""
    outcomes: dict[str, dict[str, Any] | None] = {}
    for purpose in ("EXECUTABLE", "FORECAST"):
        plan = inp.canon.plan(purpose)
        outcomes[purpose] = None if plan is None else {o.job: o for o in plan.job_dispositions}
    rows_line: dict[int, dict[str, Any]] = {}
    rows_team: dict[str, dict[str, Any]] = {}
    unknown_jobs = []

    def bump(row: dict[str, Any], layer: str, state: str) -> None:
        row[layer][state] = row[layer].get(state, 0) + 1

    for j in sorted(inp.canon.snapshot.jobs, key=lambda x: (x.key.line, x.key.native_id, x.id)):
        jteam = j.team or "UNASSIGNED"
        if not visible(j.team) or (team is not None and j.team != team) or (line is not None and j.key.line != line):
            continue
        states = {"observed": observed[j.id][0]}
        for purpose, layer in (("EXECUTABLE", "planned"), ("FORECAST", "forecast")):
            known = outcomes[purpose]
            o = None if known is None else known.get(j.id)
            states[layer] = "no_accepted_plan" if known is None else ("absent" if o is None else o.disposition.value)
        nowhere = states["observed"] != "complete" and states["planned"] not in PLACED and states["forecast"] not in PLACED
        if nowhere:
            reasons = []
            for purpose in ("EXECUTABLE", "FORECAST"):
                o = (outcomes[purpose] or {}).get(j.id)
                reasons += [] if o is None else [r.split(":", 1)[0] for r in o.reasons]
            unknown_jobs.append({"job_id": j.id, "job": f"{j.key.line}/{j.key.native_id}", "team": j.team, "line": j.key.line,
                                 "observed": states["observed"], "planned": states["planned"], "forecast": states["forecast"],
                                 "reasons": sorted(set(reasons)) or ["NO_PLACEMENT_IN_ANY_PLAN"]})
        for key, rows in ((j.key.line, rows_line), (jteam, rows_team)):
            row = rows.setdefault(key, {"jobs": 0, "observed": {}, "planned": {}, "forecast": {}, "unknown": 0})
            row["jobs"] += 1
            for layer, state in states.items():
                bump(row, layer, state)
            row["unknown"] += 1 if nowhere else 0
    grand = {"jobs": 0, "observed": {}, "planned": {}, "forecast": {}, "unknown": 0}
    for row in rows_line.values():
        grand = {"jobs": grand["jobs"] + row["jobs"], "unknown": grand["unknown"] + row["unknown"],
                 **{layer: dict(Counter(grand[layer]) + Counter(row[layer])) for layer in ("observed", "planned", "forecast")}}
    return {"by_line": [{"line": k} | v for k, v in sorted(rows_line.items())],
            "by_team": [{"team": k} | v for k, v in sorted(rows_team.items())],
            "total": grand, "unknown_jobs": unknown_jobs}


# ---------------------------------------------------------------------- reconciliation

def rollup(cells: dict[Coord, dict[str, Any]], blank: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    by_team: dict[str, dict[str, Any]] = defaultdict(blank)
    by_shift: dict[str, dict[str, Any]] = defaultdict(blank)
    by_line: dict[str, dict[str, Any]] = defaultdict(blank)
    for (team, day, number, line), m in cells.items():
        by_team[team] = add(by_team[team], m)
        by_shift[shift_label(day, number)] = add(by_shift[shift_label(day, number)], m)
        by_line["none" if line is None else str(line)] = add(by_line["none" if line is None else str(line)], m)
    return {"by_team": dict(sorted(by_team.items())), "by_shift": dict(sorted(by_shift.items())), "by_line": dict(sorted(by_line.items())),
            "grand": total(cells.values(), blank)}


def rollup_check(layer: str, totals: dict[str, Any], blank: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    grand = totals["grand"]
    sums = {level: total(totals[level].values(), blank) for level in ("by_team", "by_shift", "by_line")}
    bad = [level for level, s in sums.items() if s != grand]
    return {"check": "CELLS_SUM_TO_TOTALS", "layer": layer, "ok": not bad, "mismatch": bad}


def inventory_check(inv: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for level in ("by_line", "by_team"):
        bad = [r.get("line", r.get("team")) for r in inv[level] for layer in ("observed", "planned", "forecast")
               if sum(r[layer].values()) != r["jobs"]]
        out.append({"check": "INVENTORY_PARTITIONS_CENSUS", "level": level, "ok": not bad, "mismatch": bad})
    jobs = inv["total"]["jobs"]
    out.append({"check": "INVENTORY_LEVELS_AGREE", "ok": jobs == sum(r["jobs"] for r in inv["by_team"]) == sum(r["jobs"] for r in inv["by_line"]),
                "mismatch": [] if jobs == sum(r["jobs"] for r in inv["by_team"]) else ["by_team"]})
    absent = sum(r["planned"].get("absent", 0) + r["forecast"].get("absent", 0) for r in inv["by_line"])
    out.append({"check": "EVERY_CENSUS_JOB_HAS_AN_OUTCOME", "ok": absent == 0, "mismatch": [] if absent == 0 else [f"{absent} absent"]})
    return out


def b0_checks(inp: Inputs, planned: dict[Coord, dict[str, Any]], visible: Callable[[str | None], bool]) -> list[dict[str, Any]]:
    """On each obligation day whose plan of record is the current executable plan, planned standard effort == frozen B0."""
    plan_id = inp.canon.plan_id("EXECUTABLE")
    out = []
    if plan_id is None:
        return out
    days = {d for d, pid in inp.registry.day_plan.items() if pid == plan_id}
    for team, shift_id in team_shifts(inp):
        _, day, number = parse_shift_id(shift_id)
        if day not in days or not visible(team):
            continue
        record = inp.registry.records.get((team, shift_id))
        frozen = 0 if record is None or record.goal is None else record.goal.standard_person_minutes
        twin = sum(m["standard_minutes"] for (t, d, n, _), m in planned.items() if (t, d, n) == (team, day, number))
        if frozen or twin:
            out.append({"check": "PLANNED_EQUALS_FROZEN_B0", "team": team, "shift_id": shift_id, "ok": frozen == twin,
                        "mismatch": {} if frozen == twin else {"twin": twin, "frozen_B0": frozen}})
    return out


def ledger_check(inp: Inputs, purpose: str, cells: dict[Coord, dict[str, Any]], notes: list[dict[str, Any]],
                 visible: Callable[[str | None], bool]) -> dict[str, Any]:
    """Every duty minute of the plan's ledger is in exactly one cell, by kind (checked over the whole plan).

    ``cells`` are the unfiltered cells of every team. The check always runs on the complete ledger; the minutes behind
    a mismatch are shown only to a caller who can see every team.
    """
    plan = inp.canon.plan(purpose)
    if plan is None:
        return {"check": "DUTY_EQUALS_LEDGER", "layer": purpose, "ok": True, "mismatch": {}}
    twin = total(cells.values(), blank_plan)["duty_minutes"]
    ledger = {k: 0 for k in DUTY_KINDS}
    for d in plan.duties:
        ledger[d.kind.value] += int((d.end - d.start).total_seconds() // 60)
    unplaced = sum(n["minutes"] for n in notes)
    ok = twin == ledger and unplaced == 0
    full_scope = all(visible(t) for t in {c[0] for c in cells} | {t for t in inp.job_team.values() if t})
    detail = {"twin": twin, "ledger": ledger, "unplaced_minutes": unplaced} if full_scope else {"detail": "withheld outside your scope"}
    return {"check": "DUTY_EQUALS_LEDGER", "layer": purpose, "ok": ok, "mismatch": {} if ok else detail}


# ---------------------------------------------------------------------- the twin

def build_twin(services: Any, canon: Any, registry: Any, *, visible: Callable[[str | None], bool], load_snapshot: Callable[[str], Any],
               team: str | None = None, day: date | None = None, shift: int | None = None, line: int | None = None) -> dict[str, Any]:
    """The reconciled twin of the current canonical revision, restricted to the caller's teams and the drilldown filters."""
    from ..performance.actuals import ObservationStore, knowledge_time

    inp = Inputs(services, canon, registry, load_snapshot)
    policy = services.policy
    scope = services.settings.scope_key
    known = knowledge_time(services, services.clock.now())
    run = ObservationStore(services.repositories, scope).latest_run(known)

    def keep(coord: Coord) -> bool:
        t, d, n, ln = coord
        return visible(t) and (team is None or t == team) and (day is None or d == day) and (shift is None or n == shift) and \
            (line is None or ln == line)

    planned_all, planned_notes = plan_cells(inp, "EXECUTABLE")
    forecast_all, forecast_notes = plan_cells(inp, "FORECAST")
    observed_all, evaluations, unknown_evals, eval_checks = observed_cells(inp, visible)
    recognition_all, recognition_checks = recognition_cells(inp, visible)
    layers = {"observed": {k: v for k, v in observed_all.items() if keep(k)},
              "planned": {k: v for k, v in planned_all.items() if keep(k)},
              "forecast": {k: v for k, v in forecast_all.items() if keep(k)},
              "recognition": {k: v for k, v in recognition_all.items() if keep(k)}}
    blanks = {"observed": blank_observed, "planned": blank_plan, "forecast": blank_plan, "recognition": blank_recognition}
    totals = {layer: rollup(cells, blanks[layer]) for layer, cells in layers.items()}

    revision = {"source_run_id": canon.source_run_id, "snapshot_id": canon.snapshot.id, "plan_id": canon.plan_id("EXECUTABLE"),
                "forecast_plan_id": canon.plan_id("FORECAST"), "policy_id": policy.id, "policy_hash": policy.hash,
                "observation_run_id": None if run is None else run.id}
    cells = []
    for coord in sorted(set().union(*layers.values()), key=lambda c: (c[1], c[2], c[0], -1 if c[3] is None else c[3])):
        t, d, n, ln = coord
        e = evaluations.get((t, f"{inp.site}:{d.isoformat()}:S{n}"))
        key = {"source_run_id": revision["source_run_id"], "plan_id": revision["plan_id"], "forecast_plan_id": revision["forecast_plan_id"],
               "policy_id": policy.id, "evaluation_id": None if e is None else e.id, "evaluation_revision": None if e is None else e.revision,
               "team": t, "shift_start_date": d, "shift_number": n, "line": ln}
        cells.append({"key": key,
                      "observed": None if coord not in layers["observed"] else layers["observed"][coord] | {"basis": MEASURED_BASIS},
                      "planned": layers["planned"].get(coord),
                      "forecast": None if coord not in layers["forecast"] else layers["forecast"][coord] | {"basis": FORECAST_BASIS},
                      "recognition": None if coord not in layers["recognition"] else layers["recognition"][coord] | {"basis": FROZEN_BASIS}})

    states, lifecycle_error = observed_job_states(inp, None if run is None else run.as_of)
    inv = inventory(inp, visible, states, team=team, line=line)
    valuation = day if day is not None else known.astimezone(ZoneInfo(policy.str("shift.timezone"))).date()
    behind = {"valuation_date": valuation, "valuation_basis": "SELECTED_SHIFT_DATE" if day is not None else "KNOWLEDGE_DATE",
              "date_basis": policy.str("gate.date_basis"), "weight": policy.int("gate.weight"), "cap_days": policy.int("gate.cap_days"),
              "proxy_grace_days": policy.int("gate.proxy_grace_days"),
              "stations": behind_stations(inp, visible, states, valuation, team=team, line=line)}
    attendance = []
    avail = {(a.person, a.pool) for a in canon.snapshot.availability}
    for pool in canon.snapshot.pools:
        coord = (pool.key.team, pool.key.start_date, pool.key.shift_number, None)
        if not visible(pool.key.team) or (team is not None and pool.key.team != team) or (day is not None and pool.key.start_date != day) or \
                (shift is not None and pool.key.shift_number != shift):
            continue
        missing = sum(1 for m in pool.members if (m, pool.id) not in avail)
        if missing and coord[1] in {c[1] for c in planned_all} | {c[1] for c in forecast_all}:
            attendance.append({"team": pool.key.team, "shift": shift_label(pool.key.start_date, pool.key.shift_number), "unknown_attendance": missing})

    checks = [rollup_check(layer, totals[layer], blanks[layer]) for layer in layers]
    checks += inventory_check(inv)
    checks += b0_checks(inp, planned_all, visible)
    checks += [c for c in eval_checks if team is None or c["team"] == team]
    checks += [c for c in recognition_checks if team is None or c["team"] == team]
    checks += [ledger_check(inp, "EXECUTABLE", planned_all, planned_notes, visible), ledger_check(inp, "FORECAST", forecast_all, forecast_notes, visible)]

    max_age = timedelta(hours=policy.int("dispatch.max_source_age_hours"))
    cutoffs: dict[str, Any] = {"knowledge_time": known,
                               "source": {"source_run_id": canon.source_run_id, "snapshot_id": canon.snapshot.id, "cutoff": canon.snapshot.cutoff,
                                          "stale": canon.stale, "max_age_hours": policy.int("dispatch.max_source_age_hours")}}
    for purpose, layer in (("EXECUTABLE", "planned"), ("FORECAST", "forecast")):
        cur, snap = canon.current(purpose), inp.plan_snapshot(purpose)
        cutoffs[layer] = None if cur is None else {
            "plan_id": cur.published.id, "accepted_at": cur.published.accepted_at, "snapshot_id": cur.artifact.snapshot_id,
            "source_cutoff": None if snap is None else snap.cutoff, "current_source": cur.artifact.snapshot_id == canon.snapshot.id}
    cutoffs["observed"] = None if run is None else {
        "observation_run_id": run.id, "as_of": run.as_of, "stale": known - run.as_of > max_age,
        "feeds": [{"feed": f.feed, "status": f.status, "complete": f.complete, "covered_through": f.covered_through} for f in run.feeds]}

    reasons = []
    if run is None:
        reasons.append(("NO_OBSERVATIONS", "No observation run has been collected; measured layers are unknown, never zero."))
    if lifecycle_error:
        reasons.append(("LIFECYCLE_UNKNOWN", lifecycle_error))
    for purpose in ("EXECUTABLE", "FORECAST"):
        if canon.current(purpose) is None:
            reasons.append(("NO_ACCEPTED_PLAN", f"No accepted {purpose} plan; its layer is absent, not empty."))
    failed = [c for c in checks if not c["ok"]]
    if failed:
        reasons.append(("RECONCILIATION_FAILED", f"{len(failed)} reconciliation check(s) failed; see reconciliation.checks."))
    return {
        "version": TWIN_VERSION, "revision": revision, "filters": {"team": team, "date": day, "shift": shift, "line": line},
        "cutoffs": cutoffs, "cells": cells, "totals": totals,
        "forecast_conditions": conditional_reasons(canon.plan("FORECAST")),
        "inventory": inv,
        "behind_station": behind,
        "unknown": {"jobs": inv["unknown_jobs"], "evaluations": [u for u in unknown_evals if team is None or u["team"] == team],
                    "attendance": attendance},
        "reconciliation": {"ok": not failed, "checks": checks},
        "reasons": reasons,
        "note": "observed = stored canonical evaluation evidence and verified lifecycle facts; planned = accepted EXECUTABLE plan; "
                "forecast = accepted FORECAST plan (conditional); recognition = gate recognition frozen in each goal of record; "
                "unknown = what no layer can state",
    }
