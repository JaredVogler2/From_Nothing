"""Workforce authority: SQL-owned inference, effective assignment, rosters, attendance, pure pools (S07, P05, P06).

* Team inference is computed in SQL Server (``queries/workforce/infer_mechanic_teams.sql``)
  and persisted as ``FOCUS_FN_TeamInference``. The app consumes those rows; it never runs
  its own production inference. :func:`reference_inference` re-implements the SQL
  semantics only to verify the fixture contract.
* A current effective assignment overrides inference from its exact effective instant.
  Inference alone is nominal forecast evidence, never present executable capacity.
* Pure pools are keyed (site, team, shift start date, shift number, source run).
  Duplicate rows dedupe; overlapping inconsistent memberships are admission conflicts.
  There is no borrowing across teams or shifts.
* The denominator N counts distinct admitted available people of the pool for the
  purpose (executable: roster/attendance; forecast: plus nominal and synthetic).
  The activation cap is exactly floor(85*N/100), with no minimum-one exception.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping

from ..calendar import SHIFT_NUMBERS, ShiftPolicy, build_shift
from ..codec import parse_utc
from ..contracts.planning import Availability, CarryIn, Person, Pool, TeamInferenceRecord
from ..domain import AvailabilityBasis, DutyKind, EvidenceRef, Interval, PersonKey, PersonOrigin, PoolKey, digest_id, from_minute
from ..policy import Policy
from .duration_gauge import admit_latest_revisions, interval_union, to_micros
from .sourcetime import TimezonePolicy

UTC = timezone.utc


@dataclass
class WorkforceBuild:
    people: list[Person] = field(default_factory=list)
    pools: list[Pool] = field(default_factory=list)
    availability: list[Availability] = field(default_factory=list)
    carry_in: list[CarryIn] = field(default_factory=list)
    inference: list[TeamInferenceRecord] = field(default_factory=list)
    person_by_native: dict[str, str] = field(default_factory=dict)
    team_of_person: dict[str, str] = field(default_factory=dict)
    gaps: list[tuple[str, str, str, str]] = field(default_factory=list)   # (code, scope, detail, effect)
    conflicts: dict[str, list[str]] = field(default_factory=dict)


def activation_cap(n: int, numerator: int = 85, denominator: int = 100) -> int:
    """Exact floor(numerator*N/denominator); N 0/1/2/10 -> 0/0/1/8 for 85/100."""
    if n < 0:
        raise ValueError("pool size cannot be negative")
    return (numerator * n) // denominator


INFERENCE_ALGORITHM = "S07-plurality-v1"


def reference_inference(clock_rows: Iterable[Mapping[str, Any]], run_id: str, window_start: datetime, window_end: datetime, *,
                        departments: Mapping[str, str] | None = None,
                        dept_team: Mapping[str, str | None] | None = None) -> dict[str, dict[str, Any]]:
    """Python reference of ``queries/workforce/infer_mechanic_teams.sql`` (fixture contract verification only).

    Mirrors the SQL step for step: the latest revision of each source event is admitted
    (a voided correction removes the event instead of counting it twice); it must be
    unvoided, carry a team and start in [window_start, window_end). Per person the
    transaction count ranks teams (n DESC, team name ascending by code point); effort
    minutes are the per-(person, team) running-max interval union, kept separately and
    rounded half away from zero to 6 dp like decimal(19,6); top share = n/total_n at
    decimal(9,8); tie when the runner-up count equals the top count. ``departments`` is
    the active HR person -> accounting department population and ``dept_team`` the
    FOCUS_DEPT_TEAM_SKILL_REF mapping: a person with no admitted team evidence gets an
    explicit DEPT_FALLBACK row only through a mapped PRIMARY_TEAM; one with team-less
    evidence and no mapping stays UNKNOWN. No team is invented.
    """
    admitted, _ = admit_latest_revisions(clock_rows)
    by_team: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    teamless: set[str] = set()
    span: dict[str, tuple[datetime, datetime]] = {}
    for row in admitted:
        if row.get("IS_VOIDED"):
            continue
        start = parse_utc(str(row["START_UTC"]))
        end = parse_utc(str(row["END_UTC"]))
        if not (window_start <= start < window_end):
            continue
        person = str(row["PERSON_KEY"])
        team = str(row.get("TEAM") or "").strip(" ")
        if not team:
            teamless.add(person)
            continue
        by_team[person][team].append((to_micros(start), to_micros(end)))
        first, last = span.get(person, (start, end))
        span[person] = (min(first, start), max(last, end))
    six, eight = Decimal("0.000001"), Decimal("0.00000001")
    out: dict[str, dict[str, Any]] = {}
    for person, teams in by_team.items():
        ranked = sorted(teams.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        total = sum(len(v) for v in teams.values())
        top_team, intervals = ranked[0]
        n = len(intervals)
        second_n = len(ranked[1][1]) if len(ranked) > 1 else None
        effort_us = sum(e - s for s, e in interval_union(intervals))
        out[person] = {
            "team": top_team,
            "basis": "LABOR_PLURALITY",
            "n": n,
            "total_n": total,
            "effort": (Decimal(effort_us) / Decimal(60_000_000)).quantize(six, rounding=ROUND_HALF_UP),
            "top_share": (Decimal(n) / Decimal(total)).quantize(eight, rounding=ROUND_HALF_UP),
            "is_tie": second_n is not None and second_n == n,
            "runner_up_team": ranked[1][0] if len(ranked) > 1 else None,
            "runner_up_count": second_n,
            "first_evidence_utc": span[person][0],
            "last_evidence_utc": span[person][1],
            "window_start_utc": window_start,
            "window_end_utc": window_end,
            "algorithm_version": INFERENCE_ALGORITHM,
            "run_id": run_id,
        }
    departments = departments or {}
    dept_team = dept_team or {}
    for person in sorted((set(departments) | teamless) - set(out)):
        mapped = dept_team.get(departments.get(person, ""))
        if not mapped and person not in teamless:
            continue
        out[person] = {
            "team": mapped or None,
            "basis": "DEPT_FALLBACK" if mapped else "UNKNOWN",
            "n": 0, "total_n": 0, "effort": Decimal(0).quantize(six), "top_share": None, "is_tie": False,
            "runner_up_team": None, "runner_up_count": None, "first_evidence_utc": None, "last_evidence_utc": None,
            "window_start_utc": window_start, "window_end_utc": window_end,
            "algorithm_version": INFERENCE_ALGORITHM, "run_id": run_id,
        }
    return out


def _local_dt(text: str | None, tzp: TimezonePolicy, dataset: str) -> datetime | None:
    return tzp.parse_local(dataset, text) if text else None


def _within(day: date, start: date | None, end: date | None) -> bool:
    return (start is None or start <= day) and (end is None or day < end)


def _active(instant: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or start <= instant) and (end is None or instant < end)


def build_workforce(*, datasets: Mapping[str, tuple[Mapping[str, Any], ...]], mode: str, policy: Policy, shift_policy: ShiftPolicy,
                    tzp: TimezonePolicy, site: str, run_id: str, cutoff: datetime, horizon: tuple[date, date] | None,
                    dataset_ids: Mapping[str, str]) -> WorkforceBuild:
    out = WorkforceBuild()
    namespace = "fixture-demo" if mode == "fixture" else "BEMS"
    directory = {str(r["PERSON_KEY"]): r for r in datasets.get("people_directory", ())}
    roster_rows = list(datasets.get("shift_roster", ()))
    assign_rows = list(datasets.get("person_team_map", ()))
    inference_rows = list(datasets.get("team_inference", ()))
    natives: set[str] = set()
    for r in roster_rows:
        natives.add(str(r["PERSON_KEY"]))
    for r in assign_rows:
        natives.add(str(r["BEMS_ID"]))
    for r in inference_rows:
        natives.add(str(r["PERSON_KEY"]))
    for r in datasets.get("attendance_shift", ()):
        natives.add(str(r["BEMS_ID"]))

    for native in sorted(natives):
        entry = directory.get(native)
        origin = PersonOrigin.FIXTURE_DEMO if (entry and entry.get("ORIGIN") == "FIXTURE_DEMO") or mode == "fixture" else PersonOrigin.SOURCE
        key = PersonKey(native, namespace)
        person = Person(key=key, display_name=str(entry["DISPLAY_NAME"]) if entry else native, origin=origin, synthetic=False,
                        namespace=namespace, purpose_scope="ANY",
                        evidence=(EvidenceRef(dataset_ids.get("people_directory", ""), native, source_run_id=run_id),) if entry else ())
        out.people.append(person)
        out.person_by_native[native] = person.id

    # ---- effective assignments (exact UTC instants; dedupe true repeats, conflicts on overlap) ----
    assignments: dict[str, list[tuple[datetime | None, datetime | None, str, str]]] = defaultdict(list)
    for r in assign_rows:
        if not r.get("IN_SCOPE"):
            continue
        start_dt = _local_dt(r.get("EFFECTIVE_FROM"), tzp, "person_team_map")
        end_dt = _local_dt(r.get("EFFECTIVE_TO"), tzp, "person_team_map")
        spec = (start_dt, end_dt, str(r.get("TEAM") or ""), str(r.get("ROLE") or ""))
        if spec not in assignments[str(r["BEMS_ID"])]:
            assignments[str(r["BEMS_ID"])].append(spec)
    for native, specs in assignments.items():
        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                a, b = specs[i], specs[j]
                overlap = (a[1] is None or b[0] is None or b[0] < a[1]) and (b[1] is None or a[0] is None or a[0] < b[1])
                if overlap and (a[2], a[3]) != (b[2], b[3]):
                    out.conflicts.setdefault(native, []).append(f"ASSIGNMENT_CONFLICT: overlapping {a[2]}/{a[3]} and {b[2]}/{b[3]}")
    # ---- rosters ----
    rosters: dict[str, list[tuple[date | None, date | None, str, int, str]]] = defaultdict(list)
    for r in roster_rows:
        spec = (date.fromisoformat(str(r["EFFECTIVE_FROM"])) if r.get("EFFECTIVE_FROM") else None,
                date.fromisoformat(str(r["EFFECTIVE_TO"])) if r.get("EFFECTIVE_TO") else None,
                str(r["TEAM"]), int(r["SHIFT_NUMBER"]), str(r.get("ROLE") or ""))
        if spec not in rosters[str(r["PERSON_KEY"])]:
            rosters[str(r["PERSON_KEY"])].append(spec)
    for native, specs in rosters.items():
        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                a, b = specs[i], specs[j]
                overlap = (a[1] is None or b[0] is None or b[0] < a[1]) and (b[1] is None or a[0] is None or a[0] < b[1])
                if overlap and (a[2], a[3]) != (b[2], b[3]):
                    out.conflicts.setdefault(native, []).append(f"ROSTER_CONFLICT: overlapping {a[2]} S{a[3]} and {b[2]} S{b[3]}")

    # ---- roster/assignment agreement at every rostered shift start (before any pool exists) ----
    undefined_shift: set[tuple[str, int]] = set()
    if horizon is not None:
        day = horizon[0]
        while day <= horizon[1]:
            for native, specs in sorted(rosters.items()):
                for spec in specs:
                    if not _within(day, spec[0], spec[1]):
                        continue
                    if spec[3] not in SHIFT_NUMBERS:
                        undefined_shift.add((native, spec[3]))
                        continue
                    start = from_minute(build_shift(day, spec[3], shift_policy, site).start)
                    for a in assignments.get(native, []):
                        if _active(start, a[0], a[1]) and a[2] != spec[2]:
                            out.conflicts.setdefault(native, []).append(
                                f"ROSTER_ASSIGNMENT_MISMATCH: roster {spec[2]} vs assignment {a[2]} at {day.isoformat()} S{spec[3]}")
            day += timedelta(days=1)
    for native, number in sorted(undefined_shift):
        out.gaps.append(("ROSTER_SHIFT_UNDEFINED", native, f"roster shift {number} is not a configured shift; no pool membership", "BLOCKS_EXECUTABLE"))

    # ---- SQL-owned inference records ----
    stale_days = policy.int("workforce.inference_stale_after_days")
    inferred: dict[str, Mapping[str, Any]] = {str(r["PERSON_KEY"]): r for r in inference_rows}
    stale_people: list[str] = []
    for native, r in sorted(inferred.items()):
        person = out.person_by_native.get(native)
        if person is None:
            continue
        window_end = parse_utc(str(r["WINDOW_END_UTC"])) if r.get("WINDOW_END_UTC") else None
        stale = window_end is None or (cutoff - window_end) > timedelta(days=stale_days)
        if stale:
            stale_people.append(native)
        effective = None
        basis = "INFERRED_NOMINAL"
        current = [s for s in assignments.get(native, []) if _active(cutoff, s[0], s[1])]
        if current and native not in out.conflicts:
            effective = current[0][2]
            basis = "EFFECTIVE_ASSIGNMENT"
        elif r.get("INFERRED_TEAM") and not stale:
            effective = str(r["INFERRED_TEAM"])
        out.inference.append(TeamInferenceRecord(
            person=person,
            inferred_team=str(r["INFERRED_TEAM"]) if r.get("INFERRED_TEAM") else None,
            evidence_count=int(r.get("EVIDENCE_COUNT") or 0),
            effort_minutes=Decimal(str(r.get("EFFORT_MINUTES") or "0")),
            top_share=Decimal(str(r["TOP_SHARE"])) if r.get("TOP_SHARE") not in (None, "") else None,
            runner_up_team=str(r["RUNNER_UP_TEAM"]) if r.get("RUNNER_UP_TEAM") else None,
            runner_up_count=int(r["RUNNER_UP_COUNT"]) if r.get("RUNNER_UP_COUNT") is not None else None,
            is_tie=bool(r.get("IS_TIE")),
            basis=str(r.get("BASIS") or "UNKNOWN"),
            window_start=parse_utc(str(r["WINDOW_START_UTC"])) if r.get("WINDOW_START_UTC") else None,
            window_end=parse_utc(str(r["WINDOW_END_UTC"])) if r.get("WINDOW_END_UTC") else None,
            algorithm_version=str(r.get("ALGORITHM_VERSION") or ""),
            effective_team=effective,
            effective_basis=basis,
            stale=stale,
        ))
    if stale_people:
        out.gaps.append(("TEAM_INFERENCE_STALE", "workforce",
                         f"{len(stale_people)} inference rows end more than {stale_days} days before the cutoff; labelled stale, never a home team",
                         "INFO"))

    if not assign_rows:
        out.gaps.append(("ASSIGNMENT_FEED_EMPTY", "workforce", "FOCUS_PERSON_TEAM_MAP returned no rows; inference is nominal forecast evidence only", "BLOCKS_EXECUTABLE"))
    if not datasets.get("attendance_shift"):
        out.gaps.append(("ATTENDANCE_FEED_EMPTY", "workforce", "FOCUS_ATTENDANCE_SHIFT returned no rows for the production day", "BLOCKS_EXECUTABLE"))
    if horizon is None:
        out.gaps.append(("HORIZON_UNKNOWN", "workforce", "no admitted line calendar bounds; pools cannot be dated", "BLOCKS_ALL"))
        return out

    # ---- attendance, leave, other duty, OT ----
    production_day = cutoff.astimezone(shift_policy.tz).date()
    attendance: dict[tuple[date, int, str], bool] = {}
    for r in datasets.get("attendance_shift", ()):
        attendance[(date.fromisoformat(str(r["PRODUCTION_DAY"])), int(r["SHIFT_NUMBER"]), str(r["BEMS_ID"]))] = bool(r["PRESENT"])
    leave: set[tuple[str, date, int]] = set()
    for r in datasets.get("availability_exceptions", ()):
        native = str(r["PERSON_KEY"])
        d = date.fromisoformat(str(r["PRODUCTION_DAY"]))
        n = int(r["SHIFT_NUMBER"])
        if str(r["KIND"]) == "LEAVE":
            leave.add((native, d, n))
        elif r.get("START_LOCAL") and r.get("END_LOCAL") and native in out.person_by_native:
            s = tzp.parse_local("availability_exceptions", str(r["START_LOCAL"]))
            e = tzp.parse_local("availability_exceptions", str(r["END_LOCAL"]))
            if s and e and e > s:
                out.carry_in.append(CarryIn(digest_id("CI", native, str(s), str(e)), out.person_by_native[native], s, e, DutyKind.OTHER, None, None, None,
                                            (EvidenceRef(dataset_ids.get("availability_exceptions", ""), str(r.get("EVIDENCE_REF") or ""), source_run_id=run_id),)))
    for r in datasets.get("carry_in", ()):
        native = str(r["PERSON_KEY"])
        if native not in out.person_by_native:
            out.gaps.append(("CARRY_IN_PERSON_UNKNOWN", native, "carry-in duty for an unknown person", "INFO"))
            continue
        s, e = parse_utc(str(r["START_UTC"])), parse_utc(str(r["END_UTC"]))
        kind = DutyKind(str(r["KIND"])) if str(r["KIND"]) in DutyKind.__members__ else DutyKind.OTHER
        out.carry_in.append(CarryIn(digest_id("CI", native, str(s), str(e), str(r.get("EVIDENCE_REF"))), out.person_by_native[native], s, e, kind,
                                    int(r["LINE_NUMBER"]) if r.get("LINE_NUMBER") is not None else None, None, None,
                                    (EvidenceRef(dataset_ids.get("carry_in", ""), str(r.get("EVIDENCE_REF") or ""), source_run_id=run_id),)))
    max_ot = policy.int("shift.maximum_ot_minutes")
    ot_auth: list[tuple[str, int, date, date, int]] = []
    for r in datasets.get("ot_authorizations", ()):
        ot_auth.append((str(r["TEAM"]), int(r["SHIFT_NUMBER"]), date.fromisoformat(str(r["DATE_FROM"])), date.fromisoformat(str(r["DATE_TO"])),
                        min(int(r["MINUTES"]), max_ot)))

    def ot_for(team: str, n: int, d: date) -> int:
        best = 0
        for t, s_n, lo, hi, minutes in ot_auth:
            if t == team and s_n == n and lo <= d <= hi:
                best = max(best, minutes)
        return best

    executable_bases = set(policy.seq("workforce.executable_availability_bases"))
    forecast_bases = set(policy.seq("workforce.forecast_availability_bases"))
    num = policy.int("activation.fraction_numerator")
    den = policy.int("activation.fraction_denominator")

    # resolve each person's (team, how) for one shift; membership is decided at the shift start instant
    def membership(native: str, d: date, n: int, shift_start: datetime) -> tuple[str, str] | None:
        if native in out.conflicts:
            return None
        roster = [s for s in rosters.get(native, []) if _within(d, s[0], s[1]) and s[3] == n]
        if not roster:
            return None
        team = roster[0][2]
        if any(_active(shift_start, a[0], a[1]) for a in assignments.get(native, [])):
            return team, "ASSIGNED"          # agreement with the roster was verified before pools were built
        record = inferred.get(native)
        if record and record.get("INFERRED_TEAM") == team and native not in stale_people:
            return team, "INFERRED"
        return team, "ROSTER_ONLY"

    teams: set[str] = {s[2] for specs in rosters.values() for s in specs}
    lo, hi = horizon
    d = lo
    while d <= hi:
        for n in SHIFT_NUMBERS:
            shift = build_shift(d, n, shift_policy, site)
            members_by_team: dict[str, list[tuple[str, AvailabilityBasis, bool]]] = defaultdict(list)
            excluded_by_team: dict[str, list[str]] = defaultdict(list)
            shift_start = from_minute(shift.start)
            for native in sorted(rosters):
                m = membership(native, d, n, shift_start)
                if m is None:
                    continue
                team, how = m
                person = out.person_by_native[native]
                if (native, d, n) in leave:
                    excluded_by_team[team].append(f"{person}:LEAVE")
                    continue
                present = attendance.get((d, n, native))
                if d == production_day and present is False:
                    excluded_by_team[team].append(f"{person}:ABSENT")
                    continue
                if how != "ASSIGNED":
                    # roster-only or inference-only membership is nominal: forecast evidence only
                    basis, admitted = AvailabilityBasis.INFERRED_NOMINAL, False
                elif d == production_day and present is True:
                    basis, admitted = AvailabilityBasis.ATTENDANCE_CONFIRMED, True
                else:
                    basis, admitted = AvailabilityBasis.SCHEDULED_ROSTER, False
                members_by_team[team].append((person, basis, admitted))
            for team in sorted(teams | set(members_by_team)):
                entries = members_by_team.get(team, [])
                key = PoolKey(site, team, d, n, run_id)
                ot_minutes = ot_for(team, n, d)
                exec_members, fore_members = [], []
                for person, basis, admitted in entries:
                    intervals = tuple(Interval(from_minute(ws), from_minute(we)) for ws, we in shift.regular_windows)
                    ot_intervals = (Interval(from_minute(shift.ot_window[0]), from_minute(shift.ot_window[0] + ot_minutes)),) if (ot_minutes and shift.ot_window) else ()
                    out.availability.append(Availability(
                        id=digest_id("AV", key.id, person),
                        person=person,
                        pool=key.id,
                        intervals=intervals,
                        other_duty=(),
                        ot_intervals=ot_intervals,
                        regular_cap=shift.regular_cap,
                        ot_cap=ot_minutes,
                        admitted_presence=admitted,
                        basis=basis,
                        evidence=(EvidenceRef(dataset_ids.get("shift_roster", ""), f"{person}@{d.isoformat()}S{n}", source_run_id=run_id),),
                    ))
                    if basis.value in executable_bases:
                        exec_members.append(person)
                    if basis.value in forecast_bases:
                        fore_members.append(person)
                    out.team_of_person.setdefault(person, team)
                out.pools.append(Pool(
                    key=key,
                    members=tuple(sorted({p for p, _, _ in entries})),
                    executable_members=tuple(sorted(set(exec_members))),
                    forecast_members=tuple(sorted(set(fore_members))),
                    synthetic=False,
                    n_executable=len(set(exec_members)),
                    cap_executable=activation_cap(len(set(exec_members)), num, den),
                    n_forecast=len(set(fore_members)),
                    cap_forecast=activation_cap(len(set(fore_members)), num, den),
                    basis="ROSTER+ATTENDANCE" if d == production_day else "ROSTER",
                    excluded=tuple(sorted(excluded_by_team.get(team, []))),
                ))
        d += timedelta(days=1)
    for native, problems in sorted(out.conflicts.items()):
        out.gaps.append(("WORKFORCE_CONFLICT", native, "; ".join(sorted(set(problems))), "INFO"))
    return out
