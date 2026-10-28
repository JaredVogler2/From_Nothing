"""Same-run control-station facts (S09, P08).

``FOCUS_CS_WINDOWS`` rows come only from the SAME source run as the snapshot;
the optimizer never queries a second CS head. Raw MBU, station, line,
reference positions, signed offsets, CS_OP_LOAD/UNLOAD, LOAD_ID, LOAD_TS,
IS_ACTIVE and modified dates are preserved. Policy declares row grain, active
generation, whether dates are already resolved or offsets apply, the
workday/calendar-day basis and the date-to-UTC convention. Ambiguity blocks:
conflicting active loads, missing conversion policy, or missing required
mapping (``CS_WINDOW_GAP``). There is no MAX(load), no widening across
stations and no line-calendar substitute.

The earliest permitted start is feasibility; the station gate is recognition
pressure; commercial due is economics. A CS744 date can serve as an explicit
planning delivery proxy (baseline proxy adds the 2-day grace once).
"""
from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from ..contracts.planning import CSFacts, LineCalendar, RawCSRow
from ..domain import EvidenceRef
from ..policy import Policy
from .sourcetime import local_midnight_utc

UTC = timezone.utc


def raw_rows(rows: Iterable[Mapping[str, Any]]) -> list[RawCSRow]:
    out = []
    for ordinal, r in enumerate(rows):
        out.append(RawCSRow(
            row_id=f"cs_windows#{ordinal}",
            mbu=r.get("MBU"),
            control_station=int(r["CONTROL_STATION"]),
            line=int(r["LINE_NUMBER"]),
            load_ref_pos=r.get("LOAD_REF_POS"),
            load_offset=int(r["LOAD_OFFSET"]) if r.get("LOAD_OFFSET") is not None else None,
            cs_op_load=date.fromisoformat(str(r["CS_OP_LOAD"])) if r.get("CS_OP_LOAD") else None,
            unload_ref_pos=r.get("UNLOAD_REF_POS"),
            unload_offset=int(r["UNLOAD_OFFSET"]) if r.get("UNLOAD_OFFSET") is not None else None,
            cs_op_unload=date.fromisoformat(str(r["CS_OP_UNLOAD"])) if r.get("CS_OP_UNLOAD") else None,
            last_modified=date.fromisoformat(str(r["LAST_MODIFIED_DT"])),
            load_id=int(r["LOAD_ID"]),
            load_ts=str(r["LOAD_TS"]),
            is_active=bool(r["IS_ACTIVE"]),
        ))
    return out


def _shift_days(anchor: date, offset: int, basis: str, calendar: LineCalendar | None) -> date | None:
    if basis == "CALENDAR_DAY":
        return anchor + timedelta(days=offset)
    if basis == "WORKDAY":
        if calendar is None or calendar.status != "ADMITTED":
            return None
        working = sorted(d.day for d in calendar.days if d.status == "WORKING")
        if anchor not in working:
            return None
        idx = working.index(anchor) + offset
        if idx < 0 or idx >= len(working):
            return None
        return working[idx]
    return None


def _anchor(calendar: LineCalendar | None, position: str | None) -> date | None:
    if calendar is None or position is None:
        return None
    for day in calendar.days:
        if day.position == position:
            return day.day
    return None


SUPPORTED_DATE_MODES = ("RESOLVED", "OFFSET_FROM_REFERENCE")
SUPPORTED_DATE_INSTANTS = ("LOCAL_MIDNIGHT",)


def build_cs_facts(*, rows: list[RawCSRow], run_id: str, needed: Iterable[tuple[int, str | None]], calendars: Mapping[int, LineCalendar],
                   policy: Policy, tz: ZoneInfo | None, dataset_id: str) -> dict[tuple[int, str | None], CSFacts]:
    """Typed CS facts per needed (line, station). ``tz`` is the cs_windows dataset's declared source timezone."""
    mode = policy.optional("cs.date_mode")
    instant = policy.optional("cs.date_instant")
    day_basis = policy.optional("cs.offset_day_basis")
    proxy_station = str(policy.optional("due.proxy_station", ""))
    proxy_basis = str(policy.optional("due.proxy_date_basis", "ACTUAL_CS_GATE"))
    grace = policy.int("gate.proxy_grace_days")
    hard_latest_policy = policy.str("cs.hard_latest")
    index: dict[tuple[int, int], list[RawCSRow]] = {}
    for r in rows:
        if r.is_active:
            index.setdefault((r.line, r.control_station), []).append(r)
    out: dict[tuple[int, str | None], CSFacts] = {}
    conversion = f"{mode or '-'}/{instant or '-'}/{day_basis or '-'}"
    policy_problem = None
    if mode not in SUPPORTED_DATE_MODES:
        policy_problem = f"CS_CONVERSION_POLICY_MISSING: date mode {mode!r} is not an admitted conversion"
    elif instant not in SUPPORTED_DATE_INSTANTS:
        policy_problem = f"CS_CONVERSION_POLICY_MISSING: date-to-UTC convention {instant!r} is not admitted (no guessed convention)"
    elif tz is None:
        policy_problem = "CS_SOURCE_TIMEZONE_MISSING: no declared source timezone for cs_windows dates"
    for line, station in sorted(set(needed), key=lambda k: (k[0], k[1] or "")):
        def gap(code: str, detail: str, status: str = "GAP", load: int | None = None, offsets: dict | None = None) -> CSFacts:
            return CSFacts(line, station or "", None, status, None, None, None, None, load, offsets or {}, conversion, run_id, (f"{code}: {detail}",), ())
        if policy_problem:
            code, detail = policy_problem.split(": ", 1)
            out[(line, station)] = gap(code, detail, status="POLICY_MISSING")
            continue
        if station is None or not station.isdigit():
            out[(line, station)] = gap("CS_STATION_UNPARSEABLE", f"control station text {station!r} is not an exact integer station")
            continue
        candidates = index.get((line, int(station)), [])
        if not candidates:
            out[(line, station)] = gap("CS_WINDOW_GAP", f"no active same-run CS row for line {line} station {station}")
            continue
        loads = sorted({r.load_id for r in candidates})
        offsets = {
            "load_ref_pos": candidates[0].load_ref_pos, "load_offset": candidates[0].load_offset,
            "unload_ref_pos": candidates[0].unload_ref_pos, "unload_offset": candidates[0].unload_offset,
        }
        evidence = tuple(EvidenceRef(dataset_id, r.row_id, revision=str(r.load_id), source_run_id=run_id) for r in candidates)
        if len(loads) > 1:
            out[(line, station)] = CSFacts(line, station, candidates[0].mbu, "CONFLICT", None, None, None, None, None, offsets, conversion, run_id,
                                           (f"CS_LOAD_CONFLICT: active loads {loads}; no approved generation selector",), evidence)
            continue
        signatures = {(r.cs_op_load, r.cs_op_unload, r.load_offset, r.unload_offset, r.load_ref_pos, r.unload_ref_pos, r.mbu) for r in candidates}
        if len(signatures) > 1:
            out[(line, station)] = CSFacts(line, station, candidates[0].mbu, "CONFLICT", None, None, None, None, loads[0], offsets, conversion, run_id,
                                           ("CS_ROW_CONFLICT: duplicate active rows disagree for the same line/station/load",), evidence)
            continue
        row = candidates[0]
        reasons: list[str] = []
        if mode == "RESOLVED":
            earliest_day, gate_day = row.cs_op_load, row.cs_op_unload
        else:  # OFFSET_FROM_REFERENCE
            if day_basis not in ("WORKDAY", "CALENDAR_DAY"):
                out[(line, station)] = CSFacts(line, station, row.mbu, "POLICY_MISSING", None, None, None, None, row.load_id, offsets, conversion, run_id,
                                               ("CS_CONVERSION_POLICY_MISSING: offset day basis not admitted",), evidence)
                continue
            cal = calendars.get(line)
            la, ua = _anchor(cal, row.load_ref_pos), _anchor(cal, row.unload_ref_pos)
            earliest_day = _shift_days(la, row.load_offset or 0, day_basis, cal) if la else None
            gate_day = _shift_days(ua, row.unload_offset or 0, day_basis, cal) if ua else None
            if earliest_day is None:
                reasons.append("CS_ANCHOR_UNRESOLVED: load reference position/offset cannot be resolved on the line calendar")
        if earliest_day is None:
            out[(line, station)] = CSFacts(line, station, row.mbu, "GAP", None, gate_day, None, None, row.load_id, offsets, conversion, run_id,
                                           tuple(reasons or ["CS_EARLIEST_MISSING: no earliest date"]), evidence)
            continue
        earliest_at = local_midnight_utc(earliest_day, tz)
        due_proxy = None
        if station == proxy_station and gate_day is not None:
            due_proxy = gate_day + timedelta(days=grace) if proxy_basis == "BASELINE_PROXY" else gate_day
        hard_latest = None if hard_latest_policy == "NONE" else (local_midnight_utc(gate_day + timedelta(days=1), tz) if gate_day else None)
        out[(line, station)] = CSFacts(line, station, row.mbu, "ADMITTED", earliest_at, gate_day, hard_latest, due_proxy, row.load_id, offsets,
                                       conversion, run_id, tuple(reasons), evidence)
    return out
