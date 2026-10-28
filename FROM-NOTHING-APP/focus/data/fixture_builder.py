"""Deterministic synthetic fixture construction (S18).

``build_manufacturing_demo()`` returns every dataset of the S18
``manufacturing_demo`` scenario as raw source rows. The rows use the same raw
encodings the S04 tables produce: naive local ``datetime2`` text, varchar cert
dates in slash/ISO/compact forms, decimals as strings and deterministic UUIDs.
``build_scale_fixture(n)`` generates the 51/100/150/300-aircraft
shared-capacity scale fixtures (P22) with the same schemas.

Every person, order, part and contract here is invented demonstration data
(keys ``DEMO-*``). None of it is a real-world identity or commercial term.
"""
from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .datasets import DATASETS

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
SEED = 2601027
SITE, PROGRAM, MODEL, CUSTOMER = "DEMO", "787", "9", "DEMO"
CUTOFF_UTC = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
PREVIOUS_CUTOFF_UTC = datetime(2026, 10, 25, 22, 0, tzinfo=UTC)
LOAD_TS = "2026-10-25T17:30:00.000"          # local; before the previous cutoff
CAL_FROM, CAL_TO = date(2026, 10, 24), date(2026, 11, 15)
NAMESPACE = uuid.UUID("5c9a7a7e-2b1e-4d6c-9a40-26101027f0c5")


def _uuid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_naive(d: date, hh: int, mm: int = 0) -> str:
    return datetime.combine(d, time(hh, mm)).strftime("%Y-%m-%dT%H:%M:%S")


def _local_to_utc(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=NY).astimezone(UTC)


@dataclass
class Dataset:
    name: str
    rows: list[dict[str, Any]]
    receipt: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        spec = DATASETS[self.name]
        for row in self.rows:
            extra = set(row) - set(spec.columns)
            missing = set(spec.columns) - set(row)
            if extra or missing:
                raise ValueError(f"{self.name}: row keys mismatch extra={sorted(extra)} missing={sorted(missing)}")
        return {"schema_version": 1, "dataset": self.name, "columns": list(spec.columns), "rows": self.rows, "receipt": self.receipt}


def _receipt(name: str, scenario: str, rows: list[dict[str, Any]], *, certified_empty: bool = False, note: str | None = None) -> dict[str, Any]:
    receipt = {
        "complete": True,
        "certified_empty": bool(certified_empty and not rows),
        "producer": f"fixture:{scenario}",
        "upstream_revision": "fixture-v1",
        "source_timezone": "America/New_York",
        "source_clock": "2026-10-26T05:55:00",
    }
    if note:
        receipt["note"] = note
    return receipt


# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FixturePerson:
    native: str
    team: str
    shift: int
    index: int
    role: str
    certs: tuple[str, ...]

    @property
    def display(self) -> str:
        return f"{self.team} S{self.shift} #{self.index:02d}"


def demo_people() -> list[FixturePerson]:
    people: list[FixturePerson] = []

    def prod_a(n: int, roster: int) -> tuple[str, ...]:
        if roster == 3:
            return {1: ("A",), 2: ("A",), 3: ("A", "B"), 4: ("A", "B")}[n]
        if n <= 4:
            return ("A",)
        return {5: ("A", "B"), 6: ("A", "B"), 7: ("A", "B", "C"), 8: ("A", "C")}[n]

    for shift, size in ((1, 8), (2, 8), (3, 4)):
        for n in range(1, size + 1):
            people.append(FixturePerson(f"DEMO-A{shift}-{n:02d}", "PROD-A", shift, n, "MECH", prod_a(n, shift)))
    for shift in (1, 2, 3):
        for n in range(1, 5):
            people.append(FixturePerson(f"DEMO-B{shift}-{n:02d}", "PROD-B", shift, n, "MECH", ("B",) if n <= 3 else ("B", "C")))
    for shift, size in ((1, 4), (2, 4), (3, 2)):
        for n in range(1, size + 1):
            people.append(FixturePerson(f"DEMO-Q{shift}-{n:02d}", "QA", shift, n, "QA", ("QA",) if n <= 2 else ("Z9",)))
    return people


# Cert-row variants exercised by A13 (date encodings, expiry, revocation, future, blank, malformed, sentinel).
HOLDING_VARIANTS: dict[tuple[str, str], dict[str, Any]] = {
    ("DEMO-A1-04", "A"): {"LAST_CERT_DATE": "20260101", "CERT_EXP_DATE": "2027-12-31"},
    ("DEMO-A2-08", "C"): {"CERT_EXP_DATE": "2026/10/27"},
    ("DEMO-B1-04", "C"): {"STATUS": "REVOKED"},
    ("DEMO-Q1-04", "Z9"): {"CERT_EXP_DATE": ""},
    ("DEMO-B2-03", "B"): {"LAST_CERT_DATE": "2026/11/02"},
    ("DEMO-A3-02", "A"): {"CERT_EXP_DATE": "2027/13/45"},
    ("DEMO-A1-07", "C"): {"CERT_EXP_DATE": "9999/12/31"},
}


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FixtureJob:
    line: int
    soi: str
    description: str
    team: str
    status: str
    flow: int | None
    crew: int | None
    certs: tuple[str, ...] = ()
    parent: str | None = None
    is_rework: bool = False
    first_available: str | None = None
    act_start: str | None = None
    act_comp: str | None = None
    primary: int | None = None
    assist: int | None = None
    final_qa: int = 0
    ip_qa: int = 0
    oda: int = 0


def demo_jobs() -> list[FixtureJob]:
    d24, d25, d22, d23, d28 = date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 28)
    return [
        # 1301: A0 (verified complete anchor) -> A1 -> A2 -> FGI
        FixtureJob(1301, "A0", "Fit wing harness brackets", "PROD-A", "COMPLETE", 60, 2, ("A",), act_start=_local_naive(d22, 7), act_comp=_local_naive(d22, 9)),
        FixtureJob(1301, "A1", "Install wing harness", "PROD-A", "AVAILABLE", 120, 2, ("A",), first_available=_local_naive(d25, 6)),
        FixtureJob(1301, "A2", "Join harness panels", "PROD-A", "AVAILABLE", 90, 2, ("A", "B"), first_available=_local_naive(d25, 6), final_qa=1),
        FixtureJob(1301, "FGI", "Final gate inspection", "QA", "PLANNED", 30, 1, ("QA",)),
        # 1302: B1 -> B2 -> FGI (same SOI "FGI" on another line; flow differs)
        FixtureJob(1302, "B1", "Install aft harness", "PROD-A", "AVAILABLE", 120, 2, ("A",), first_available=_local_naive(d24, 6)),
        FixtureJob(1302, "B2", "Join aft panels", "PROD-A", "AVAILABLE", 90, 2, ("A", "B"), first_available=_local_naive(d24, 6), final_qa=1),
        FixtureJob(1302, "FGI", "Final gate inspection", "QA", "PLANNED", 40, 1, ("QA",)),
        # 1303: ROOT3 gated by unfinished rework children NC3, PU3; IP holds + final + parallel ODA buyoff
        FixtureJob(1303, "ROOT3", "Install floor beam fittings", "PROD-B", "AVAILABLE", 100, 2, ("B",), first_available=_local_naive(d25, 6), final_qa=1, ip_qa=2, oda=1),
        FixtureJob(1303, "NC3", "Rework: nonconforming fastener", "PROD-B", "AVAILABLE", None, 2, ("B",), parent="ROOT3", is_rework=True, first_available=_local_naive(d25, 6), final_qa=1),
        FixtureJob(1303, "PU3", "Rework: pickup item", "PROD-B", "AVAILABLE", None, 2, ("B",), parent="ROOT3", is_rework=True, first_available=_local_naive(d25, 6), final_qa=1),
        # 1304: ROOT4 in progress (performed 40 / remaining 60) with unfinished FS predecessor PRE4 (earliest future date)
        FixtureJob(1304, "ROOT4", "Seal center tank access", "PROD-B", "IN WORK", 100, 2, ("B",), act_start=_local_naive(d23, 8), final_qa=1),
        FixtureJob(1304, "PRE4", "Clean and prime tank bay", "PROD-B", "PLANNED", 60, 2, ("B",), first_available=_local_naive(d28, 6)),
        # 1305: CHILD5 -> ROOT5 -> NEXT5; shortage E5 used on CHILD5 (date-only ECD); ROOT5 crew 3 = TOTAL (primary 2 + assist 1)
        FixtureJob(1305, "CHILD5", "Install bracket subassembly", "PROD-A", "AVAILABLE", 60, 2, ("A",), parent="ROOT5", first_available=_local_naive(d25, 6)),
        FixtureJob(1305, "ROOT5", "Install door actuator", "PROD-A", "AVAILABLE", 90, 3, ("A",), first_available=_local_naive(d25, 6), primary=2, assist=1, final_qa=1),
        FixtureJob(1305, "NEXT5", "Rig door actuator", "PROD-A", "PLANNED", 60, 2, ("A",)),
        # 1306: support tasks for the virtual MHE/PSC/TST forecast
        FixtureJob(1306, "MHE6", "Material handling: engine stand move", "MHE", "AVAILABLE", 60, 2, ("MHE-OP",), first_available=_local_naive(d25, 6)),
        FixtureJob(1306, "PSC6", "Parts staging and kitting", "PSC", "AVAILABLE", 45, 1, (), first_available=_local_naive(d25, 6)),
        FixtureJob(1306, "TST6", "Functional test: hydraulics", "TST", "AVAILABLE", 90, 2, ("TST-1",), first_available=_local_naive(d25, 6)),
        FixtureJob(1306, "TST6B", "Functional test: avionics (no requirement census)", "TST", "AVAILABLE", 30, 0, (), first_available=_local_naive(d25, 6)),
    ]


def _calendar_status(d: date) -> str:
    if d == date(2026, 11, 2):
        return "NONWORKING"          # holiday Monday before working Tuesday
    if d.weekday() == 6:
        return "WEEKEND"
    if d.weekday() == 5:
        return "WORKING" if d == date(2026, 10, 24) else "WEEKEND"
    return "WORKING"


def build_manufacturing_demo(scenario: str = "manufacturing_demo") -> dict[str, Dataset]:
    people = demo_people()
    jobs = demo_jobs()
    lines = sorted({j.line for j in jobs})
    ds: dict[str, list[dict[str, Any]]] = {name: [] for name in DATASETS}

    for i, j in enumerate(jobs, start=1):
        ds["master_task_list"].append({
            "SHOP_ORDER_IDENTIFIER": j.soi, "SHOP_ORDER_DESCRIPTION": j.description, "LINE_NUMBER": j.line,
            "SHOP_ORDER_STATUS": j.status, "TEAM": j.team, "CURRENT_CONTROL_STATION": "744", "MINOR_MODEL_IDENT": MODEL,
            "CUSTOMER_CODE": CUSTOMER, "PARENT_SOI": j.parent, "SUPERINTENDENT": "DEMO-SUPT", "FIRST_AVAILABLE": j.first_available,
            "ACT_START": j.act_start, "ACT_COMP": j.act_comp, "LOAD_TIMESTAMP": LOAD_TS, "PLANNING_SYSTEM_NAME": "DEMO-PS",
            "IS_REWORK": j.is_rework, "RECORD_KEY": 9000 + i,
        })
        ds["flow_budget"].append({
            "JOB_NUMBER": j.soi, "LINE_NUMBER": j.line, "CRITICAL_FLOW_MINUTES": j.flow, "TOTAL_MECHANICS": j.crew,
            "PRIMARY_MECHANIC_COUNT": j.primary if j.primary is not None else j.crew,
            "PRIMARY_MECHANIC_DURATION": j.flow, "ASSIST_COUNT": j.assist if j.assist is not None else 0,
            "ASSIST_LABOR_MINUTES": (j.assist or 0) * (j.flow or 0),
            "TOTAL_LABOR_MINUTES": (j.flow or 0) * (j.crew or 0) if j.flow and j.crew else None,
            "DURATION_SOURCE": "REWORK_STATISTIC" if j.is_rework else "FIXTURE_STANDARD", "LOAD_TIMESTAMP": LOAD_TS,
        })
        for code in j.certs:
            ds["soi_cert_requirements"].append({
                "SOI_ID": j.soi, "LINE_NUMBER": j.line, "CRITICAL_PROCESS_NAME": f"DEMO PROCESS {code}", "CERT_CODE": code,
                "CERT_TYPE": "CERT", "REQ_CERT": "Y", "SNAPSHOT_TS": "2026-10-25T05:00:00",
            })
        if j.final_qa or j.ip_qa or j.oda:
            ds["buyoff_plan"].append({
                "SHOP_ORDER_IDENTIFIER": j.soi, "LINE_NUMBER": j.line, "SHOP_ORDER_STATUS": j.status, "MANUFACTURING_INSP": 0,
                "QUALITY_INSP": 0, "CC_INSP": 0, "ODAUNITMEMBER_INSP": j.oda, "PARTNER_QT_INSP": 0, "FINAL_QUALITY_INSP": j.final_qa,
                "IN_PROCESS_QUALITY_INSP": j.ip_qa, "LOAD_TIMESTAMP": LOAD_TS,
            })
            ds["inspection_counts"].append({
                "SHOP_ORDER_IDENTIFIER": j.soi, "LINE_NUMBER": j.line, "MANUFACTURING_INSPECTIONS": 0, "QUALITY_INSPECTIONS": 0,
                "FINAL_QUALITY_INSPECTIONS": j.final_qa, "IN_PROCESS_QUALITY_INSPECTIONS": j.ip_qa, "CC_INSPECTIONS": 0,
                "ODAUNITMEMBER_INSPECTIONS": j.oda, "PARTNER_QT_INSPECTIONS": 0, "LOAD_TIMESTAMP": LOAD_TS,
            })

    for t, minutes, rel, desc in (
        ("FINAL_QUALITY", 45, "AFTER_WORK_FS", "Final quality inspection before successors"),
        ("IN_PROCESS_QUALITY", 45, "TOTAL_ACROSS_HOLDS", "Total in-process inspection across sourced holds"),
        ("ODAUNITMEMBER", 20, "PARALLEL_BRANCH", "ODA unit member buyoff"),
        ("MANUFACTURING", 15, "PARALLEL_BRANCH", "Manufacturing buyoff"),
        ("QUALITY", 30, "PARALLEL_BRANCH", "Quality buyoff"),
        ("CC", 20, "PARALLEL_BRANCH", "Customer contract buyoff"),
        ("PARTNER_QT", 20, "PARALLEL_BRANCH", "Partner QT buyoff"),
    ):
        ds["inspection_standards"].append({"INSPECTION_TYPE": t, "DURATION_MINUTES": minutes, "SCHEDULING_RELATIONSHIP": rel, "DESCRIPTION": desc})

    for pred, succ, rel, hard, offset, note in (
        ("A0", "A1", "FINISH <= START", "Y", 0, None),
        ("A1", "A2", "FINISH <= START", "Y", 0, None),
        ("A2", "FGI", "FINISH <= START", "Y", 0, None),
        ("B1", "B2", "FINISH <= START", "Y", 0, None),
        ("B2", "FGI", "FINISH <= START", "Y", 0, None),
        ("PRE4", "ROOT4", "FINISH <= START", "Y", 0, None),
        ("CHILD5", "ROOT5", "FINISH <= START", "Y", 0, None),
        ("ROOT5", "NEXT5", "FINISH <= START", "Y", 0, None),
        ("MHE6", "TST6", "FINISH <= START", "N", 0, "advisory sequence"),
        ("PSC6", "TST6", "START <= START", "Y", 15, "test may start 15 min after staging starts"),
    ):
        ds["pnet_dag"].append({
            "PREDECESSOR_SOI": pred, "SUCCESSOR_SOI": succ, "RELATIONSHIP_TYPE": rel, "IS_HARD": hard, "PRED_MINOR_MODEL": MODEL,
            "SUC_MINOR_MODEL": MODEL, "ORIGINAL_PRED_JOBNUMBER": pred, "ORIGINAL_SUC_JOBNUMBER": succ, "OFFSET_MINUTES": offset,
            "LOAD_TIMESTAMP": LOAD_TS,
        })

    for child in ("NC3", "PU3"):
        ds["neo_graph_rework"].append({"LINE_NUMBER": 1303, "PARENT_SOI": "ROOT3", "CHILD_SOI": child, "CHILD_STATUS": "OPEN", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
    ds["rework_classification"] += [
        {"LINE_NUMBER": 1303, "CHILD_SOI": "NC3", "REWORK_CLASS": "NC", "ROOT_SOI": "ROOT3", "DEFECT_ORIGIN_TEAM": "PROD-A", "EVIDENCE_REF": "FIXTURE-NCR-301"},
        {"LINE_NUMBER": 1303, "CHILD_SOI": "PU3", "REWORK_CLASS": "PU", "ROOT_SOI": "ROOT3", "DEFECT_ORIGIN_TEAM": "SUPPLIER-DEMO", "EVIDENCE_REF": "FIXTURE-PU-302"},
    ]
    ds["inspection_holds"] += [
        {"LINE_NUMBER": 1303, "SOI": "ROOT3", "SEQUENCE": 1, "AFTER_WORK_MINUTES": 40, "INSPECTOR_MINUTES": 20, "EVIDENCE_REF": "FIXTURE-OP-0040"},
        {"LINE_NUMBER": 1303, "SOI": "ROOT3", "SEQUENCE": 2, "AFTER_WORK_MINUTES": 80, "INSPECTOR_MINUTES": 25, "EVIDENCE_REF": "FIXTURE-OP-0080"},
    ]
    ds["work_progress"].append({"LINE_NUMBER": 1304, "SOI": "ROOT4", "PERFORMED_ELAPSED_MINUTES": 40, "REMAINING_ELAPSED_MINUTES": 60,
                                "AS_OF_UTC": "2026-10-26T09:30:00Z", "EVIDENCE_REF": "FIXTURE-PROGRESS-ROOT4"})

    # calendars and CS windows (same load 7001, active)
    for line in lines:
        d = CAL_FROM
        while d <= CAL_TO:
            ds["cday_op_plan"].append({
                "LINE_NUMBER": line, "LINE": f"L{line}", "CALENDAR_DATE": d.isoformat(), "POSITION": "P744",
                "DAY_STATUS": _calendar_status(d), "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001,
                "LOAD_TS": "2026-10-20T04:00:00", "IS_ACTIVE": True,
            })
            d += timedelta(days=1)
        ds["cs_windows"].append({
            "MBU": "DEMO", "CONTROL_STATION": 744, "LINE_NUMBER": line, "LOAD_REF_POS": "P744", "LOAD_OFFSET": 0,
            "CS_OP_LOAD": "2026-10-26", "UNLOAD_REF_POS": "P744", "UNLOAD_OFFSET": 0, "CS_OP_UNLOAD": "2026-10-28",
            "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001, "LOAD_TS": "2026-10-20T04:00:00", "IS_ACTIVE": True,
        })

    # parts
    ds["etracs_active"] += [
        {"RECORD_ID": 50501, "LINE_NUMBER": 1305, "MINOR_MODEL_IDENT": MODEL, "USED_ON": "CHILD5", "ETRAC_ECD": "2026-10-27",
         "TEAM": "PROD-A", "REASON_CODE": "LP", "ETRAC_CREATED_DT": "2026-10-20", "ITEM_NUMBER": "DEMO-P-0001", "LOAD_TIMESTAMP": LOAD_TS},
        {"RECORD_ID": 50502, "LINE_NUMBER": 1306, "MINOR_MODEL_IDENT": MODEL, "USED_ON": "", "ETRAC_ECD": None,
         "TEAM": "TST", "REASON_CODE": "LP", "ETRAC_CREATED_DT": "2026-10-21", "ITEM_NUMBER": "DEMO-P-0002", "LOAD_TIMESTAMP": LOAD_TS},
    ]
    ds["etracs_history"].append({"RECORD_ID": 40001, "LINE_NUMBER": 1301, "USED_ON": "A1", "PARENT_SOI": None, "PRIMARY_JOB": "A1",
                                 "ETRAC_ECD": "2026-10-08", "ACTUAL_CLOSURE_DATE": "2026-10-10", "LOAD_TIMESTAMP": LOAD_TS})
    ds["part_events"].append({"RECORD_ID": 50501, "EVENT": "RECEIVED", "EFFECTIVE_AT_UTC": "2026-10-27T18:15:00Z",
                              "OBSERVED_AT_UTC": "2026-10-27T18:20:00Z", "EVIDENCE_REF": "FIXTURE-RCV-50501"})

    ds["actual_labor"] += [
        {"SHOP_ORDER_IDENTIFIER": "A0", "LINE_NUMBER": 1301, "SHOP_ORDER_STATUS": "COMPLETE", "LABOR_RECORDS": 2,
         "ACTUAL_MINUTES_BURNED": "120.00", "ACTUAL_HOURS_BURNED": "2.00", "LOAD_TIMESTAMP": LOAD_TS},
        {"SHOP_ORDER_IDENTIFIER": "ROOT4", "LINE_NUMBER": 1304, "SHOP_ORDER_STATUS": "IN WORK", "LABOR_RECORDS": 2,
         "ACTUAL_MINUTES_BURNED": "80.00", "ACTUAL_HOURS_BURNED": "1.33", "LOAD_TIMESTAMP": LOAD_TS},
    ]
    ds["labor_shift"] += [
        {"PRODUCTION_DAY": "2026-10-23", "SHIFT_NUMBER": 1, "LINE_NUMBER": 1304, "SOI": "ROOT4", "BEMS_ID": b, "MINUTES": "40.00",
         "FIRST_ON_ET": "2026-10-23T08:00:00", "LAST_OFF_ET": "2026-10-23T08:40:00", "LOAD_TIMESTAMP": LOAD_TS}
        for b in ("DEMO-B1-01", "DEMO-B1-02")
    ]
    for code, process in (("A", "DEMO PROCESS A"), ("B", "DEMO PROCESS B"), ("C", "DEMO PROCESS C"), ("QA", "DEMO PROCESS QA"),
                          ("MHE-OP", "DEMO PROCESS MHE-OP"), ("TST-1", "DEMO PROCESS TST-1")):
        ds["cert_course_map"].append({"CRITICAL_PROCESS_NAME": process, "CERT_CODE": code, "CERT_TYPE": "CERT", "BAC_SPEC": "DEMO-SPEC",
                                      "BAC_VERB": "PERFORM", "SNAPSHOT_TS": "2026-10-25T05:00:00"})

    ds["requirement_census"].append({"SOI_ID": "PSC6", "LINE_NUMBER": 1306, "STATUS": "AFFIRMATIVELY_NONE", "APPLICABILITY": "JOB",
                                     "EVIDENCE_REF": "FIXTURE-CENSUS-PSC6"})
    for code in ("B", "C"):
        ds["requirement_alternatives"].append({"SOI_ID": "ROOT5", "LINE_NUMBER": 1305, "GROUP_ID": "G1", "CERT_CODE": code,
                                               "APPROVAL_REF": "FIXTURE-ALT-ROOT5"})

    # workforce
    for p in people:
        for code in p.certs:
            row = {"BEMS_ID": p.native, "CERT_CODE": code, "STATUS": "ACTIVE", "LAST_CERT_DATE": "2026/01/01",
                   "CERT_EXP_DATE": "2027/12/31", "SNAPSHOT_TS": "2026-10-25T05:00:00"}
            row.update(HOLDING_VARIANTS.get((p.native, code), {}))
            ds["mechanic_certs"].append(row)
        ds["person_team_map"].append({
            "BEMS_ID": p.native, "ROLE": p.role, "TEAM": p.team, "IN_SCOPE": True, "BASIS": "ASSIGNED",
            "EFFECTIVE_FROM": "2026-01-01T00:00:00", "EFFECTIVE_TO": None, "RUN_ID": "FIXTURE-ASSIGN-01",
            "EVIDENCE_COUNT": 0, "LOOKBACK_DAYS": 30, "LOAD_TIMESTAMP": LOAD_TS,
        })
        ds["shift_roster"].append({"PERSON_KEY": p.native, "TEAM": p.team, "ROLE": p.role, "SITE": SITE, "SHIFT_NUMBER": p.shift,
                                   "EFFECTIVE_FROM": "2026-01-01", "EFFECTIVE_TO": None, "BASIS": "SCHEDULED_ROSTER",
                                   "EVIDENCE_REF": "FIXTURE-ROSTER-2026"})
        ds["people_directory"].append({"PERSON_KEY": p.native, "DISPLAY_NAME": p.display, "ORIGIN": "FIXTURE_DEMO"})
        ds["attendance_shift"].append({
            "PRODUCTION_DAY": "2026-10-26", "SHIFT_NUMBER": p.shift, "BEMS_ID": p.native,
            "PRESENT": p.native != "DEMO-A1-03", "HOURS": "8.50" if p.native != "DEMO-A1-03" else "0.00",
            "SOURCE": "FIXTURE_BADGE", "LOAD_TIMESTAMP": "2026-10-26T05:45:00.000",
        })
        if p.role == "MECH":
            inferred, basis, tie, runner, runner_n, n = p.team, "LABOR_PLURALITY", False, None, None, 24 + p.index
            if p.native == "DEMO-A1-08":
                inferred, runner, runner_n = "PROD-B", "PROD-A", 12        # assignment PROD-A overrides (A09)
            if p.native == "DEMO-B2-04":
                tie, runner, runner_n, n = True, "PROD-C", 18, 18           # tie broken by team name (A08)
            ds["team_inference"].append({
                "RUN_ID": "FIXTURE-INFER-01", "PERSON_KEY": p.native, "INFERRED_TEAM": inferred,
                "WINDOW_START_UTC": "2026-09-25T10:00:00Z", "WINDOW_END_UTC": "2026-10-25T10:00:00Z",
                "EVIDENCE_COUNT": n, "EFFORT_MINUTES": str(n * 45), "TOP_SHARE": "0.50000000" if tie else "0.90000000",
                "RUNNER_UP_TEAM": runner, "RUNNER_UP_COUNT": runner_n, "IS_TIE": tie, "BASIS": basis,
                "ALGORITHM_VERSION": "S07-plurality-v1", "FIRST_EVIDENCE_UTC": "2026-09-26T11:00:00Z", "LAST_EVIDENCE_UTC": "2026-10-24T20:00:00Z",
            })
    ds["team_inference"].append({
        "RUN_ID": "FIXTURE-INFER-01", "PERSON_KEY": "DEMO-Q1-04", "INFERRED_TEAM": "QA",
        "WINDOW_START_UTC": "2026-09-25T10:00:00Z", "WINDOW_END_UTC": "2026-10-25T10:00:00Z", "EVIDENCE_COUNT": 0,
        "EFFORT_MINUTES": "0", "TOP_SHARE": None, "RUNNER_UP_TEAM": None, "RUNNER_UP_COUNT": None, "IS_TIE": False,
        "BASIS": "DEPT_FALLBACK", "ALGORITHM_VERSION": "S07-plurality-v1", "FIRST_EVIDENCE_UTC": None, "LAST_EVIDENCE_UTC": None,
    })
    ds["dept_team_skill_ref"] += [
        {"ACCTG_DEPT_CD": dept, "PRIMARY_TEAM": team, "DOMINANT_SKILL": skill, "LABOR_RECORDS": n, "LOAD_TIMESTAMP": LOAD_TS}
        for dept, team, skill, n in (("D100", "PROD-A", "MECH", 1200), ("D200", "PROD-B", "MECH", 640), ("D900", "QA", "QA", 300))
    ]
    ds["availability_exceptions"].append({
        "PERSON_KEY": "DEMO-A1-06", "PRODUCTION_DAY": "2026-10-28", "SHIFT_NUMBER": 1, "KIND": "LEAVE",
        "START_LOCAL": None, "END_LOCAL": None, "OBSERVED_AT_UTC": "2026-10-26T08:00:00Z", "EVIDENCE_REF": "FIXTURE-LEAVE-A106",
    })
    ds["carry_in"].append({"PERSON_KEY": "DEMO-B1-02", "KIND": "OTHER", "START_UTC": "2026-10-26T10:15:00Z", "END_UTC": "2026-10-26T11:15:00Z",
                           "LINE_NUMBER": None, "SOI": None, "EVIDENCE_REF": "FIXTURE-TRAINING-B102"})
    for team in ("PROD-A", "PROD-B", "QA"):
        for shift in (1, 2):
            ds["ot_authorizations"].append({"TEAM": team, "SHIFT_NUMBER": shift, "DATE_FROM": "2026-10-26", "DATE_TO": "2026-11-13",
                                            "MINUTES": 60, "AUTHORITY": "FIXTURE-OT-APPROVAL", "EVIDENCE_REF": "FIXTURE-OT-2026-10"})
    ds["holding_events"].append({"BEMS_ID": "DEMO-A1-08", "CERT_CODE": "C", "EVENT": "REVOKED", "EFFECTIVE_AT_UTC": "2026-10-29T12:00:00Z",
                                 "OBSERVED_AT_UTC": "2026-10-25T12:00:00Z", "EVIDENCE_REF": "FIXTURE-REVOKE-A108-C"})

    # duration evidence: 30 completed comparable elapsed episodes per class (S18 reference generator)
    class_team = {"NC": "PROD-B", "PU": "PROD-B", "CI": "PROD-A"}
    base = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    for cls, team in class_team.items():
        for i in range(30):
            ds["duration_episodes"].append({
                "EPISODE_KEY": f"{cls}-{i:03d}", "REWORK_CLASS": cls, "TEAM": team, "MODEL": MODEL,
                "ELAPSED_MINUTES": str(30 + i), "PERSON_EFFORT_MINUTES": str(2 * (30 + i)), "CYCLE_MINUTES": str(40 + i),
                "STATUS": "verified_complete", "CREW": 2, "COMPLETED_AT_UTC": _utc(base + timedelta(days=i % 45, hours=i % 7)),
                "SOURCE": "fixture",
            })
        for i, status in ((90, "reopened"), (91, "unverified")):
            ds["duration_episodes"].append({
                "EPISODE_KEY": f"{cls}-{i:03d}", "REWORK_CLASS": cls, "TEAM": team, "MODEL": MODEL, "ELAPSED_MINUTES": "400",
                "PERSON_EFFORT_MINUTES": "800", "CYCLE_MINUTES": "460", "STATUS": status, "CREW": 2,
                "COMPLETED_AT_UTC": _utc(base + timedelta(days=50)), "SOURCE": "fixture",
            })
    union_base = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    for key, owner, start, end, voided in (
        ("CLK-1", "DEMO-B1-01", 0, 60, False), ("CLK-2", "DEMO-B1-02", 10, 20, False), ("CLK-3", "DEMO-B1-01", 60, 90, False),
        ("CLK-4", "DEMO-B1-02", 100, 110, False), ("CLK-5", "DEMO-B1-02", 200, 260, True),
    ):
        ds["clock_observations"].append({
            "SOURCE_EVENT_KEY": key, "LINE_NUMBER": 1303, "NATIVE_SOI": "NC3-HIST", "PERSON_KEY": owner, "EPISODE_KEY": "UNION-DEMO",
            "TEAM": "PROD-B", "REWORK_CLASS": "NC", "START_UTC": _utc(union_base + timedelta(minutes=start)),
            "END_UTC": _utc(union_base + timedelta(minutes=end)), "SOURCE_REVISION": "1", "IS_VOIDED": voided,
        })

    ds["scope_receipt"].append({"SITE": SITE, "PROGRAM": PROGRAM, "SCOPE_BASIS": "ALL_WIP", "LINE_NUMBER": None, "ADMISSION": "ADMIT",
                                "REASON": "fixture scope admits every observed non-COMPLETE line", "EVIDENCE_REF": "FIXTURE-SCOPE-01"})

    _economics(ds, lines)

    out: dict[str, Dataset] = {}
    empties = {"mech_team_inferred": "fixture people use non-numeric DEMO keys; legacy bigint inference evidence is certified empty",
               "cert_eligibility_matrix": "fixture people use non-numeric DEMO keys; the bigint-keyed convenience matrix is certified empty",
               "econ_original_sale_basis": "no actual sale basis in the fixture (assumed planning terms only)",
               "econ_liability_checkpoint": "no accrued liability checkpoints in the fixture"}
    for name in DATASETS:
        rows = ds[name]
        out[name] = Dataset(name, rows, _receipt(name, scenario, rows, certified_empty=name in empties, note=empties.get(name)))
    return out


def _economics(ds: dict[str, list[dict[str, Any]]], lines: list[int]) -> None:
    mapping_id = _uuid("mapping-rev-1")
    scenario_id = _uuid("scenario-assumed-1")
    ds["econ_mapping_revision"].append({
        "MappingRevisionId": mapping_id, "SourceName": "fixture-aircraft-mapping", "SourcePredicate": "SITE='DEMO'",
        "QuerySha256": "0" * 64, "ContentSha256": "1" * 64, "RowCount": len(lines), "ImportedAtUtc": "2026-10-20T12:00:00.000",
        "CoverageJson": json.dumps({"lines": lines}), "IsCurrent": True,
    })
    for line in lines:
        if line == 1306:
            continue   # no mapping: economics unknown, never zero
        ds["econ_aircraft_mapping"].append({
            "MappingRevisionId": mapping_id, "LineNumber": line, "CustomerCode": CUSTOMER, "MinorModel": int(MODEL),
            "SourceStart": None, "SourceEnd": None, "RevisionDate": "2026-10-20", "ShopCompleteDate": None, "AssemblyLocation": "D",
            "SourceContentSha256": "2" * 64, "ImportedAtUtc": "2026-10-20T12:00:00.000",
        })
    ds["econ_scenario"].append({
        "ScenarioRevisionId": scenario_id, "ScenarioKey": "DEMO-PLANNING", "ScenarioRevision": 1, "Provenance": "ASSUMED",
        "Methodology": "Fixture planning assumption; not contractual liability.", "OwnerSubject": "fixture-economics",
        "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None, "Currency": "USD", "CapFraction": "0.2500",
        "PriorityWeight": "1.00000", "CreatedAtUtc": "2026-10-20T12:00:00.000", "IsActive": True,
    })
    contracts = {1303: "DEMO-K1", 1304: "DEMO-K1", 1305: "DEMO-K2"}
    for line, key in contracts.items():
        ds["econ_contract_assignment"].append({
            "AssignmentId": _uuid(f"assign-{line}"), "LineNumber": line, "MappingRevisionId": mapping_id, "ScenarioRevisionId": scenario_id,
            "ContractKey": key, "Provenance": "ASSUMED", "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None,
            "OwnerSubject": "fixture-economics", "ApprovedBy": None, "SourceReference": f"FIXTURE-CONTRACT-{key}",
            "CreatedAtUtc": "2026-10-20T12:00:00.000", "IsActive": True,
        })
    terms = (
        ("AIRCRAFT_OVERRIDE", 1301, None, "100.0000"),
        ("AIRCRAFT_OVERRIDE", 1302, None, "10.0000"),
        ("CONTRACT", None, "DEMO-K1", "50.0000"),
        ("CUSTOMER_MODEL", None, None, "40.0000"),
        ("AIRCRAFT_OVERRIDE", 1304, None, "25.0000"),
    )
    for i, (selector, line, contract, rate) in enumerate(terms, start=1):
        selector_key = str(line) if selector == "AIRCRAFT_OVERRIDE" else (contract or f"{CUSTOMER}/{MODEL}")
        ds["econ_term"].append({
            "TermId": _uuid(f"term-{i}"), "ScenarioRevisionId": scenario_id, "SelectorType": selector,
            "SelectorLength": len(selector_key.encode("utf-8")), "CustomerCode": CUSTOMER, "MinorModel": int(MODEL),
            "LineNumber": line, "ContractKey": contract, "OriginalSalePrice": "0.0000", "DailyRate": rate, "Currency": "USD",
            "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None, "Provenance": "ASSUMED",
            "Methodology": "Fixture linear daily planning rate", "SourceReference": f"FIXTURE-TERM-{i}",
            "OwnerSubject": "fixture-economics", "ApprovedBy": None, "TermVersion": 1, "CreatedAtUtc": "2026-10-20T12:00:00.000",
        })


# ---------------------------------------------------------------------------
# incumbent (previous accepted plan) specification
# ---------------------------------------------------------------------------

DEMO_INCUMBENT: dict[str, Any] = {
    "schema_version": 1,
    "description": "Previous accepted fixture plan with jobs at day offsets 0/1/2 (defended) and 3 (free).",
    "previous_cutoff_utc": _utc(PREVIOUS_CUTOFF_UTC),
    "accepted_at_utc": "2026-10-25T22:30:00Z",
    "assignments": [
        {"line": 1301, "soi": "A1", "phase": "WORK", "ordinal": 0, "date": "2026-10-26", "shift": 1, "start_local": "06:15", "crew": ["DEMO-A1-01", "DEMO-A1-02"]},
        {"line": 1303, "soi": "NC3", "phase": "WORK", "ordinal": 0, "date": "2026-10-26", "shift": 1, "start_local": "06:15", "crew": ["DEMO-B1-03", "DEMO-B1-04"]},
        {"line": 1302, "soi": "B1", "phase": "WORK", "ordinal": 0, "date": "2026-10-27", "shift": 1, "start_local": "06:15", "crew": ["DEMO-A1-03", "DEMO-A1-04"]},
        {"line": 1301, "soi": "A2", "phase": "WORK", "ordinal": 0, "date": "2026-10-28", "shift": 1, "start_local": "06:15", "crew": ["DEMO-A1-05", "DEMO-A1-06"]},
        {"line": 1302, "soi": "B2", "phase": "WORK", "ordinal": 0, "date": "2026-10-29", "shift": 1, "start_local": "06:15", "crew": ["DEMO-A1-05", "DEMO-A1-07"]},
    ],
}


# ---------------------------------------------------------------------------
# observations (S14/S18: actual lifecycle, intervals, claims, decisions; P17)
# ---------------------------------------------------------------------------

OBSERVATION_POINTS_UTC = ("2026-10-26T19:30:00Z", "2026-10-27T16:00:00Z", "2026-10-28T16:00:00Z")
OBSERVATION_COVERED_THROUGH = "2026-10-28T23:59:59Z"


def _w(line: int, soi: str, kind: str = "WORK", ordinal: int = 0, segment: int | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"line": line, "soi": soi, "kind": kind, "ordinal": ordinal}
    if segment is not None:
        ref["segment"] = segment
    return ref


def _event(key: str, seq: int, line: int, soi: str, kind: str, effective: str, *, phase: str | None = "WORK", ordinal: int | None = 0,
           person: str | None = None, verified: bool = False, verifier: str | None = None, recorded: str | None = None,
           observed: str | None = None, segments: list[int] | None = None, revision: str = "1", supersedes: str | None = None,
           directive: str | None = None, detail: str = "") -> dict[str, Any]:
    recorded = recorded or effective
    return {"SOURCE_EVENT_KEY": key, "SOURCE_SEQUENCE": seq, "SOURCE_REVISION": revision, "LINE_NUMBER": line, "SOI": soi,
            "PHASE_KIND": phase, "PHASE_ORDINAL": ordinal if phase is not None else None, "SEGMENTS": segments, "KIND": kind,
            "PERSON_KEY": person, "EFFECTIVE_AT_UTC": effective, "RECORDED_AT_UTC": recorded, "OBSERVED_AT_UTC": observed or recorded,
            "VERIFIED": verified, "VERIFIED_BY": verifier, "DIRECTIVE_REF": directive, "SUPERSEDES_EVENT_KEY": supersedes,
            "EVIDENCE_REF": f"FIXTURE-{key}", "DETAIL": detail}


def _interval(key: str, line: int, soi: str, person: str, start: str, end: str, *, phase: str = "WORK", ordinal: int = 0,
              observed: str | None = None, voided: bool = False, revision: str = "1") -> dict[str, Any]:
    return {"SOURCE_EVENT_KEY": key, "SOURCE_REVISION": revision, "LINE_NUMBER": line, "SOI": soi, "PHASE_KIND": phase, "PHASE_ORDINAL": ordinal,
            "PERSON_KEY": person, "START_UTC": start, "END_UTC": end, "IS_VOIDED": voided, "OBSERVED_AT_UTC": observed or end,
            "EVIDENCE_REF": f"FIXTURE-CLOCK-{key}"}


def build_observations(scenario: str = "manufacturing_demo") -> dict[str, dict[str, Any]]:
    """What actually happened on 2026-10-26 S1 (and the late corrections/reviews after it), as raw observation feeds.

    Expected demonstration outcomes (the integration tests derive them again from source facts):

    * **PROD-A.** It completes A1 (planned) and works B1 and B2 offplan, starting B2 before B1
      finished. That gives U = 240 + 180, and the offplan OOS has no second charge.
    * **PROD-B.**
      * It completes NC3 and PU3. PU3's actual burn is 5x its standard; NC3 burns 1.06x.
      * It works the approved injected rework NC3B, whose defect origin PROD-A is explicitly
        evidenced.
      * It starts ROOT3 before PU3's final QA (a planned, discretionary OOS) and verifies segment 0.
      * Overlapping and duplicate claims are reviewed: one exception approval, one rejection and
        its appeal.
    * **QA.**
      * NC3 final QA is verified before NC3 is administratively cancelled (prior work earns).
      * ROOT3's ODA buyoff is cancelled before completion (earns nothing; PLAN_CHURN).
      * PU3 final QA is verified, then reopened; the reopen is voided late the next morning.
    * **ROOT4.** It was started on 10-23 with PRE4 incomplete. That out-of-sequence record survives
      PRE4's later completion.
    """
    d26 = "2026-10-26"
    T = lambda hhmm, day=d26: f"{day}T{hhmm}:00Z"  # noqa: E731 - compact fixture clock helper
    life = [
        # PROD-A: planned A1, offplan B1 then B2 (B2 starts before B1 is complete)
        _event("EV-A1-START", 1, 1301, "A1", "STARTED", T("10:20"), person="DEMO-A1-01", recorded=T("10:21"), observed=T("10:25")),
        _event("EV-A1-DONE", 2, 1301, "A1", "COMPLETED", T("12:20"), person="DEMO-A1-01", verified=True, verifier="DEMO-Q1-01", recorded=T("12:30"),
               observed=T("12:35")),
        _event("EV-B1-START", 3, 1302, "B1", "STARTED", T("12:40"), person="DEMO-A1-01", observed=T("12:45")),
        _event("EV-B2-START", 4, 1302, "B2", "STARTED", T("13:10"), person="DEMO-A1-05", observed=T("13:15")),
        _event("EV-B1-DONE", 5, 1302, "B1", "COMPLETED", T("14:45"), person="DEMO-A1-01", verified=True, verifier="DEMO-Q1-01", observed=T("14:50")),
        _event("EV-B2-DONE", 6, 1302, "B2", "COMPLETED", T("17:20"), person="DEMO-A1-05", verified=True, verifier="DEMO-Q1-01", observed=T("17:25")),
        # PROD-B: NC3, PU3 (5x burn), injected NC3B, ROOT3 started out of sequence, segment 0 verified
        _event("EV-NC3-START", 10, 1303, "NC3", "STARTED", T("10:18"), person="DEMO-B1-03", observed=T("10:20")),
        _event("EV-NC3-DONE", 11, 1303, "NC3", "COMPLETED", T("11:13"), person="DEMO-B1-03", verified=True, verifier="DEMO-Q1-01", observed=T("11:15")),
        _event("EV-PU3-START", 12, 1303, "PU3", "STARTED", T("11:20"), person="DEMO-B1-03", observed=T("11:22")),
        _event("EV-NC3B-START", 13, 1303, "NC3B", "STARTED", T("12:40"), person="DEMO-B1-01", observed=T("12:42")),
        _event("EV-NC3B-DONE", 14, 1303, "NC3B", "COMPLETED", T("14:20"), person="DEMO-B1-01", verified=True, verifier="DEMO-Q1-02",
               observed=T("14:25")),
        _event("EV-ROOT3-START", 15, 1303, "ROOT3", "STARTED", T("15:00"), person="DEMO-B1-01", observed=T("15:02")),
        _event("EV-PU3-DONE", 16, 1303, "PU3", "COMPLETED", T("15:40"), person="DEMO-B1-03", verified=True, verifier="DEMO-Q1-01", observed=T("15:45")),
        _event("EV-ROOT3-SEG0", 17, 1303, "ROOT3", "PROGRESS", T("17:30"), segments=[0], person="DEMO-B1-01", verified=True, verifier="DEMO-Q1-02",
               observed=T("17:35")),
        _event("EV-ROOT3-W1-REPORTED", 18, 1303, "ROOT3", "REPORTED_COMPLETION", T("18:00"), ordinal=1, person="DEMO-B1-02", observed=T("18:05"),
               detail="mechanic reported WORK 1 complete; never verified"),
        # QA: NC3 final QA verified, then NC3 administratively cancelled; ODA buyoff cancelled before completion;
        # PU3 final QA verified, reopened, and the reopen voided late
        _event("EV-NC3QA-DONE", 20, 1303, "NC3", "COMPLETED", T("11:58"), phase="FINAL_QA", person="DEMO-Q1-01", verified=True, verifier="DEMO-Q1-02",
               observed=T("12:00")),
        _event("EV-ODA-CANCEL", 21, 1303, "ROOT3", "CANCELLED", T("14:00"), phase="OTHER_BUYOFF", observed=T("14:05"),
               detail="engineering withdrew the ODA buyoff requirement"),
        _event("EV-PU3QA-DONE", 22, 1303, "PU3", "COMPLETED", T("16:25"), phase="FINAL_QA", person="DEMO-Q1-01", verified=True, verifier="DEMO-Q1-02",
               recorded=T("16:30"), observed=T("16:32")),
        _event("EV-NC3-CANCEL", 23, 1303, "NC3", "CANCELLED", T("17:00"), phase=None, ordinal=None, observed=T("17:05"),
               detail="NCR dispositioned use-as-is: job administratively cancelled after verified completion"),
        _event("EV-PU3QA-REOPEN", 24, 1303, "PU3", "REOPENED", T("17:10"), phase="FINAL_QA", observed=T("17:12"),
               detail="reopened for an inspection finding"),
        _event("EV-PU3QA-REOPEN-VOID", 25, 1303, "PU3", "VOIDED", "2026-10-27T09:00:00Z", phase="FINAL_QA", supersedes="EV-PU3QA-REOPEN",
               observed="2026-10-27T09:05:00Z", detail="late correction: the reopen was entered against the wrong order"),
        # ROOT4 started 2026-10-23 with PRE4 incomplete (A26); PRE4 completes later and never erases that record
        _event("EV-ROOT4-START", 30, 1304, "ROOT4", "STARTED", "2026-10-23T12:00:00Z", person="DEMO-B1-01", recorded="2026-10-23T12:05:00Z",
               observed="2026-10-23T12:10:00Z"),
        _event("EV-PRE4-DONE", 31, 1304, "PRE4", "COMPLETED", "2026-10-28T11:20:00Z", person="DEMO-B1-02", verified=True, verifier="DEMO-Q1-01",
               observed="2026-10-28T11:30:00Z"),
        # replay and malformed rows: a duplicate, a conflicting revision and three unsupported facts (quarantined with reasons)
        _event("EV-A1-DONE", 2, 1301, "A1", "COMPLETED", T("12:20"), person="DEMO-A1-01", verified=True, verifier="DEMO-Q1-01", recorded=T("12:30"),
               observed=T("12:35")),
        _event("EV-A1-DONE", 2, 1301, "A1", "COMPLETED", T("12:05"), person="DEMO-A1-01", verified=True, verifier="DEMO-Q1-01", recorded=T("12:30"),
               observed=T("12:36")),
        _event("EV-BAD-KIND", 40, 1301, "A2", "FINISHED", T("13:00"), observed=T("13:01")),
        _event("EV-BAD-VERIFIED-REPORT", 41, 1301, "A2", "REPORTED_COMPLETION", T("13:00"), verified=True, verifier="DEMO-A1-02", observed=T("13:01")),
        _event("EV-BAD-DISAPPEARED", 42, 1301, "A2", "DISAPPEARED", T("13:00"), phase=None, ordinal=None, observed=T("13:01")),
    ]
    intervals = [
        _interval("CLK-NC3-1", 1303, "NC3", "DEMO-B1-03", T("10:18"), T("11:13")),
        _interval("CLK-NC3-2", 1303, "NC3", "DEMO-B1-04", T("10:18"), T("11:13")),
        _interval("CLK-PU3-1", 1303, "PU3", "DEMO-B1-03", T("11:20"), T("15:40")),
        _interval("CLK-PU3-2", 1303, "PU3", "DEMO-B1-04", T("11:20"), T("15:40")),
        _interval("CLK-NC3B-1", 1303, "NC3B", "DEMO-B1-01", T("12:40"), T("14:20")),
        _interval("CLK-NC3B-2", 1303, "NC3B", "DEMO-B1-02", T("12:40"), T("14:20")),
        _interval("CLK-ROOT3-1", 1303, "ROOT3", "DEMO-B1-01", T("15:00"), T("17:30")),
        _interval("CLK-ROOT3-2", 1303, "ROOT3", "DEMO-B1-02", T("15:00"), T("17:30")),
        _interval("CLK-ROOT3-X", 1303, "ROOT3", "DEMO-B1-02", T("17:40"), T("18:10"), voided=True),
    ]
    defects = [{"SOURCE_EVENT_KEY": "DEF-NC3B", "LINE_NUMBER": 1303, "SOI": "NC3B", "ROOT_SOI": "ROOT3", "REWORK_CLASS": "NC",
                "DEFECT_ORIGIN_TEAM": "PROD-A", "EVIDENCE_REF": "FIXTURE-NCR-303", "OBSERVED_AT_UTC": T("12:35")}]
    amendments = [{"AMENDMENT_KEY": "AMD-NC3B", "TEAM": "PROD-B", "SHIFT_DATE": d26, "SHIFT_NUMBER": 1, "WORK_REF": _w(1303, "NC3B"),
                   "STANDARD_PERSON_MINUTES": 104, "EFFORT_BASIS": "NC class P75 52 elapsed min x simultaneous crew 2 (S08 sourced rework statistic)",
                   "FEASIBILITY_REF": "FIXTURE-AMEND-FEAS-01: validated emergency addition (full crew, duty and graph feasible)",
                   "AUTHORITY": "demo:supervisor-prod-b", "DECISION_AT_UTC": T("12:30"),
                   "REASON": "Injected NCR-303 fastener rework on 1303 must clear before ROOT3", "OBSERVED_AT_UTC": T("12:31")}]
    root3_missed = [_w(1303, "ROOT3", "WORK", 0, 1), _w(1303, "ROOT3", "WORK", 1), _w(1303, "ROOT3", "WORK", 2)]

    def claim(key: str, refs: list[dict[str, Any]], cause: str, *, origin: str | None, notes: str, reporter: str, evidence: list[str],
              reported: str = "2026-10-26T19:00:00Z", status: str = "SUBMITTED", supersedes: str | None = None) -> dict[str, Any]:
        return {"CLAIM_KEY": key, "TEAM": "PROD-B", "SHIFT_DATE": d26, "SHIFT_NUMBER": 1, "WORK_REFS": refs, "CAUSE": cause, "ORIGIN_TEAM": origin,
                "EXECUTING_TEAM": "PROD-B", "EVIDENCE_REFS": evidence, "NOTES": notes, "STATUS": status, "REPORTED_BY": reporter,
                "REPORTED_AT_UTC": reported, "OBSERVED_AT_UTC": "2026-10-26T19:05:00Z", "SUPERSEDES_CLAIM_KEY": supersedes}

    claims = [
        claim("CLM-B-01", root3_missed, "REWORK_INJECTION", origin="PROD-A", reporter="demo:lead-prod-b", evidence=["DEFECT:FIXTURE-NCR-303"],
              notes="Injected NC3B rework (NCR-303) took the ROOT3 crew from 12:40"),
        claim("CLM-B-02", root3_missed[1:], "SAME_TEAM_PREDECESSOR", origin=None, reporter="demo:lead-prod-b", evidence=["PHASE:1303/PU3/WORK/0"],
              notes="PU3 supplier pickup overran about four hours; ROOT3 is gated by PU3 final QA"),
        claim("CLM-B-03", root3_missed, "REWORK_INJECTION", origin="PROD-A", reporter="demo:mechanic-b1-01", evidence=["DEFECT:FIXTURE-NCR-303"],
              notes="duplicate claim for the same missed ROOT3 work"),
        claim("CLM-B-04", root3_missed[1:2], "DURATION_OVERRUN", origin=None, reporter="demo:lead-prod-b", evidence=[], notes=""),
        claim("CLM-B-05", root3_missed, "REWORK_INJECTION", origin="PROD-B", reporter="demo:lead-prod-b", evidence=["DEFECT:FIXTURE-NCR-303"],
              notes="origin guessed from the executing team / DAG direction"),
        claim("CLM-B-06", root3_missed[1:2], "CROSS_TEAM_PREDECESSOR", origin="QA", reporter="demo:lead-prod-b", evidence=["PHASE:1303/ROOT3/IP_QA/0"],
              notes="IP QA hold 0 was not bought off in time"),
    ]

    def decision(key: str, claim_keys: list[str], verdict: str, refs: list[dict[str, Any]], at: str, reason: str,
                 supersedes: str | None = None) -> dict[str, Any]:
        return {"DECISION_KEY": key, "TEAM": "PROD-B", "SHIFT_DATE": d26, "SHIFT_NUMBER": 1, "CLAIM_KEYS": claim_keys, "APPROVED_WORK_REFS": refs,
                "DECISION": verdict, "AUTHORITY": "demo:excusal", "AUTHORITY_ROLE": "excusal_decider", "DECISION_AT_UTC": at, "REASON": reason,
                "REVIEW_TRACK": None, "SUPERSEDES_DECISION_KEY": supersedes, "OBSERVED_AT_UTC": at}

    decisions = [
        decision("DEC-B-01", ["CLM-B-01", "CLM-B-03"], "APPROVED", root3_missed, "2026-10-27T15:00:00Z",
                 "NC3B injected rework evidenced by NCR-303 with defect origin PROD-A"),
        decision("DEC-B-02", ["CLM-B-02"], "APPROVED", root3_missed[1:], "2026-10-27T15:05:00Z",
                 "Reviewed exception: supplier pickup PU3 overran and gates ROOT3 through its final QA"),
        decision("DEC-B-03", ["CLM-B-06"], "REJECTED", [], "2026-10-27T15:10:00Z",
                 "IP QA hold 0 was blocked by PROD-B's own unfinished ROOT3 WORK 0"),
        decision("DEC-B-04", ["CLM-B-06"], "REJECTED", [], "2026-10-28T14:00:00Z",
                 "Appeal reviewed: QA was not culpable; DAG direction alone does not prove culpability", supersedes="DEC-B-03"),
    ]
    columns = {
        "lifecycle_events": ("SOURCE_EVENT_KEY,SOURCE_SEQUENCE,SOURCE_REVISION,LINE_NUMBER,SOI,PHASE_KIND,PHASE_ORDINAL,SEGMENTS,KIND,PERSON_KEY,"
                             "EFFECTIVE_AT_UTC,RECORDED_AT_UTC,OBSERVED_AT_UTC,VERIFIED,VERIFIED_BY,DIRECTIVE_REF,SUPERSEDES_EVENT_KEY,EVIDENCE_REF,DETAIL"),
        "actual_intervals": "SOURCE_EVENT_KEY,SOURCE_REVISION,LINE_NUMBER,SOI,PHASE_KIND,PHASE_ORDINAL,PERSON_KEY,START_UTC,END_UTC,IS_VOIDED,OBSERVED_AT_UTC,EVIDENCE_REF",
        "defect_evidence": "SOURCE_EVENT_KEY,LINE_NUMBER,SOI,ROOT_SOI,REWORK_CLASS,DEFECT_ORIGIN_TEAM,EVIDENCE_REF,OBSERVED_AT_UTC",
        "goal_amendments": ("AMENDMENT_KEY,TEAM,SHIFT_DATE,SHIFT_NUMBER,WORK_REF,STANDARD_PERSON_MINUTES,EFFORT_BASIS,FEASIBILITY_REF,AUTHORITY,"
                            "DECISION_AT_UTC,REASON,OBSERVED_AT_UTC"),
        "disruption_claims": ("CLAIM_KEY,TEAM,SHIFT_DATE,SHIFT_NUMBER,WORK_REFS,CAUSE,ORIGIN_TEAM,EXECUTING_TEAM,EVIDENCE_REFS,NOTES,STATUS,REPORTED_BY,"
                              "REPORTED_AT_UTC,OBSERVED_AT_UTC,SUPERSEDES_CLAIM_KEY"),
        "excusal_decisions": ("DECISION_KEY,TEAM,SHIFT_DATE,SHIFT_NUMBER,CLAIM_KEYS,APPROVED_WORK_REFS,DECISION,AUTHORITY,AUTHORITY_ROLE,DECISION_AT_UTC,"
                              "REASON,REVIEW_TRACK,SUPERSEDES_DECISION_KEY,OBSERVED_AT_UTC"),
    }
    rows = {"lifecycle_events": life, "actual_intervals": intervals, "defect_evidence": defects, "goal_amendments": amendments,
            "disruption_claims": claims, "excusal_decisions": decisions}
    out: dict[str, dict[str, Any]] = {}
    for name, cols in columns.items():
        col_tuple = [c for c in cols.split(",") if c]
        for row in rows[name]:
            if list(row) != col_tuple and set(row) != set(col_tuple):
                raise ValueError(f"{name}: observation row keys differ from the feed contract")
        out[name] = {"schema_version": 1, "dataset": name, "columns": col_tuple, "rows": rows[name],
                     "receipt": {"complete": True, "certified_empty": False, "producer": f"fixture:{scenario}", "upstream_revision": "fixture-v1",
                                 "covered_through_utc": OBSERVATION_COVERED_THROUGH}}
    out["manifest"] = {"schema_version": 1, "scenario": scenario, "collection_points_utc": list(OBSERVATION_POINTS_UTC),
                       "note": "Deterministic collection points for the S14 observation replay (fixture only)."}
    return out


def write_inputs(target: Path, datasets: dict[str, Dataset], extra: dict[str, Any] | None = None) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, dataset in sorted(datasets.items()):
        path = target / f"{name}.json"
        path.write_text(json.dumps(dataset.to_json(), indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    for name, payload in (extra or {}).items():
        path = target / f"{name}.json"
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


# ---------------------------------------------------------------------------
# scale fixtures (51+/100/150/300 shared-capacity aircraft; P22, A03, A70)
# ---------------------------------------------------------------------------

TEMPLATE: tuple[tuple[str, str, int, int, tuple[str, ...], bool], ...] = (
    # soi, team, base flow, crew, certs, final QA
    ("J01", "PROD-A", 120, 2, ("A",), False),
    ("J02", "PROD-B", 90, 2, ("B",), False),
    ("J03", "PROD-C", 60, 1, ("C",), False),
    ("J04", "PROD-D", 150, 2, ("D",), False),
    ("J05", "PROD-A", 90, 2, ("A", "B"), False),
    ("J06", "PROD-B", 120, 3, ("B",), False),
    ("J07", "PROD-C", 180, 2, ("C",), True),
    ("J08", "PROD-D", 60, 1, ("D", "A"), False),
    ("J09", "PROD-A", 240, 2, ("A",), False),
    ("J10", "PROD-B", 60, 2, ("B", "C"), False),
    ("J11", "PROD-C", 90, 2, ("C",), False),
    ("J12", "PROD-D", 120, 2, ("D",), False),
    ("J13", "PROD-A", 60, 2, ("A",), True),
    ("J14", "PROD-B", 45, 1, ("B",), True),
)
TEMPLATE_EDGES: tuple[tuple[str, str], ...] = (
    ("J01", "J02"), ("J02", "J03"), ("J03", "J05"), ("J05", "J07"), ("J07", "J09"), ("J09", "J11"), ("J11", "J13"), ("J13", "J14"),
    ("J02", "J04"), ("J04", "J06"), ("J06", "J08"), ("J08", "J10"), ("J10", "J12"), ("J12", "J13"), ("J03", "J04"), ("J06", "J07"),
)
SCALE_TEAM_CERT = {"PROD-A": "A", "PROD-B": "B", "PROD-C": "C", "PROD-D": "D"}
SCALE_SECOND_CERT = {"PROD-A": "B", "PROD-B": "C", "PROD-C": "D", "PROD-D": "A"}


def _topo_template() -> list[str]:
    order: list[str] = []
    indeg = {soi: 0 for soi, *_ in TEMPLATE}
    for _, succ in TEMPLATE_EDGES:
        indeg[succ] += 1
    ready = sorted(s for s, d in indeg.items() if d == 0)
    while ready:
        node = ready.pop(0)
        order.append(node)
        for pred, succ in TEMPLATE_EDGES:
            if pred == node:
                indeg[succ] -= 1
                if indeg[succ] == 0:
                    ready.append(succ)
                    ready.sort()
    return order


def build_scale_fixture(n_aircraft: int, scenario: str | None = None, seed: int = SEED) -> dict[str, Dataset]:
    """Deterministic shared-capacity scale fixture (labeled synthetic; real readiness not established)."""
    if n_aircraft < 2:
        raise ValueError("scale fixtures need at least two aircraft (one blocked, one missing standards)")
    scenario = scenario or f"scale_{n_aircraft}"
    rng = random.Random(seed * 1000 + n_aircraft)
    lines = list(range(2001, 2001 + n_aircraft))
    ds: dict[str, list[dict[str, Any]]] = {name: [] for name in DATASETS}
    order = _topo_template()
    per_team = max(8, -(-n_aircraft // 8))
    qa_size = max(4, -(-n_aircraft // 15))
    record_key = 1
    rec_id = 70000
    for line in lines:
        progress = rng.randint(0, 8)
        complete = set(order[:progress])
        blocked_line = line == lines[0]
        missing_standards = line == lines[1]
        gate_day = date(2026, 10, 27) + timedelta(days=rng.randint(0, 16))
        for soi, team, base_flow, crew, certs, final in TEMPLATE:
            status = "COMPLETE" if soi in complete else ("ON HOLD" if blocked_line else rng.choice(("AVAILABLE", "AVAILABLE", "PLANNED", "IDLE")))
            flow = max(15, base_flow + rng.choice((-15, 0, 0, 15, 30)))
            act_start = act_comp = None
            if status == "COMPLETE":
                d = date(2026, 10, 1) + timedelta(days=order.index(soi))
                act_start, act_comp = _local_naive(d, 7), _local_naive(d, 11)
            ds["master_task_list"].append({
                "SHOP_ORDER_IDENTIFIER": soi, "SHOP_ORDER_DESCRIPTION": f"Template operation {soi}", "LINE_NUMBER": line,
                "SHOP_ORDER_STATUS": status, "TEAM": team, "CURRENT_CONTROL_STATION": "744", "MINOR_MODEL_IDENT": MODEL,
                "CUSTOMER_CODE": CUSTOMER, "PARENT_SOI": None, "SUPERINTENDENT": "DEMO-SUPT",
                "FIRST_AVAILABLE": _local_naive(date(2026, 10, 25), 6), "ACT_START": act_start, "ACT_COMP": act_comp,
                "LOAD_TIMESTAMP": LOAD_TS, "PLANNING_SYSTEM_NAME": "DEMO-PS", "IS_REWORK": False, "RECORD_KEY": record_key,
            })
            record_key += 1
            if not missing_standards:
                ds["flow_budget"].append({
                    "JOB_NUMBER": soi, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": flow, "TOTAL_MECHANICS": crew,
                    "PRIMARY_MECHANIC_COUNT": crew, "PRIMARY_MECHANIC_DURATION": flow, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                    "TOTAL_LABOR_MINUTES": flow * crew, "DURATION_SOURCE": "FIXTURE_STANDARD", "LOAD_TIMESTAMP": LOAD_TS,
                })
            for code in certs:
                ds["soi_cert_requirements"].append({"SOI_ID": soi, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": f"DEMO PROCESS {code}",
                                                    "CERT_CODE": code, "CERT_TYPE": "CERT", "REQ_CERT": "Y", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            if final:
                ds["buyoff_plan"].append({"SHOP_ORDER_IDENTIFIER": soi, "LINE_NUMBER": line, "SHOP_ORDER_STATUS": status, "MANUFACTURING_INSP": 0,
                                          "QUALITY_INSP": 0, "CC_INSP": 0, "ODAUNITMEMBER_INSP": 0, "PARTNER_QT_INSP": 0,
                                          "FINAL_QUALITY_INSP": 1, "IN_PROCESS_QUALITY_INSP": 0, "LOAD_TIMESTAMP": LOAD_TS})
        if not blocked_line and rng.random() < 0.12:
            open_jobs = [s for s in order if s not in complete]
            parent = rng.choice(open_jobs)
            child = f"NC{line % 1000:03d}"
            team = next(t for s, t, *_ in TEMPLATE if s == parent)
            ds["master_task_list"].append({
                "SHOP_ORDER_IDENTIFIER": child, "SHOP_ORDER_DESCRIPTION": f"Rework on {parent}", "LINE_NUMBER": line,
                "SHOP_ORDER_STATUS": "AVAILABLE", "TEAM": team, "CURRENT_CONTROL_STATION": "744", "MINOR_MODEL_IDENT": MODEL,
                "CUSTOMER_CODE": CUSTOMER, "PARENT_SOI": parent, "SUPERINTENDENT": "DEMO-SUPT",
                "FIRST_AVAILABLE": _local_naive(date(2026, 10, 25), 6), "ACT_START": None, "ACT_COMP": None,
                "LOAD_TIMESTAMP": LOAD_TS, "PLANNING_SYSTEM_NAME": "DEMO-PS", "IS_REWORK": True, "RECORD_KEY": record_key,
            })
            record_key += 1
            ds["flow_budget"].append({"JOB_NUMBER": child, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": None, "TOTAL_MECHANICS": 2,
                                      "PRIMARY_MECHANIC_COUNT": 2, "PRIMARY_MECHANIC_DURATION": None, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                                      "TOTAL_LABOR_MINUTES": None, "DURATION_SOURCE": "REWORK_STATISTIC", "LOAD_TIMESTAMP": LOAD_TS})
            ds["soi_cert_requirements"].append({"SOI_ID": child, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": "DEMO PROCESS REWORK",
                                                "CERT_CODE": SCALE_TEAM_CERT[team], "CERT_TYPE": "CERT", "REQ_CERT": "Y", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            ds["neo_graph_rework"].append({"LINE_NUMBER": line, "PARENT_SOI": parent, "CHILD_SOI": child, "CHILD_STATUS": "OPEN", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            ds["rework_classification"].append({"LINE_NUMBER": line, "CHILD_SOI": child, "REWORK_CLASS": "NC", "ROOT_SOI": parent,
                                                "DEFECT_ORIGIN_TEAM": team, "EVIDENCE_REF": f"FIXTURE-NCR-{line}"})
        if not blocked_line and rng.random() < 0.12:
            open_jobs = [s for s in order if s not in complete]
            used_on = rng.choice(open_jobs)
            ecd = None if rng.random() < 0.15 else (date(2026, 10, 26) + timedelta(days=rng.randint(0, 6))).isoformat()
            rec_id += 1
            ds["etracs_active"].append({"RECORD_ID": rec_id, "LINE_NUMBER": line, "MINOR_MODEL_IDENT": MODEL, "USED_ON": used_on,
                                        "ETRAC_ECD": ecd, "TEAM": "PROD-A", "REASON_CODE": "LP", "ETRAC_CREATED_DT": "2026-10-20",
                                        "ITEM_NUMBER": f"DEMO-P-{rec_id}", "LOAD_TIMESTAMP": LOAD_TS})
        d = CAL_FROM
        while d <= CAL_TO:
            ds["cday_op_plan"].append({"LINE_NUMBER": line, "LINE": f"L{line}", "CALENDAR_DATE": d.isoformat(), "POSITION": "P744",
                                       "DAY_STATUS": _calendar_status(d), "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001,
                                       "LOAD_TS": "2026-10-20T04:00:00", "IS_ACTIVE": True})
            d += timedelta(days=1)
        ds["cs_windows"].append({"MBU": "DEMO", "CONTROL_STATION": 744, "LINE_NUMBER": line, "LOAD_REF_POS": "P744", "LOAD_OFFSET": 0,
                                 "CS_OP_LOAD": "2026-10-26", "UNLOAD_REF_POS": "P744", "UNLOAD_OFFSET": 0, "CS_OP_UNLOAD": gate_day.isoformat(),
                                 "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001, "LOAD_TS": "2026-10-20T04:00:00", "IS_ACTIVE": True})
    for pred, succ in TEMPLATE_EDGES:
        ds["pnet_dag"].append({"PREDECESSOR_SOI": pred, "SUCCESSOR_SOI": succ, "RELATIONSHIP_TYPE": "FINISH <= START", "IS_HARD": "Y",
                               "PRED_MINOR_MODEL": MODEL, "SUC_MINOR_MODEL": MODEL, "ORIGINAL_PRED_JOBNUMBER": pred,
                               "ORIGINAL_SUC_JOBNUMBER": succ, "OFFSET_MINUTES": 0, "LOAD_TIMESTAMP": LOAD_TS})
    for t, minutes, rel, desc in (("FINAL_QUALITY", 45, "AFTER_WORK_FS", "Final quality"), ("IN_PROCESS_QUALITY", 45, "TOTAL_ACROSS_HOLDS", "In-process quality")):
        ds["inspection_standards"].append({"INSPECTION_TYPE": t, "DURATION_MINUTES": minutes, "SCHEDULING_RELATIONSHIP": rel, "DESCRIPTION": desc})
    # shared pools: no private crews per aircraft
    people: list[FixturePerson] = []
    for team, cert in SCALE_TEAM_CERT.items():
        letter = team[-1]
        for shift in (1, 2):
            for n in range(1, per_team + 1):
                if n <= int(per_team * 0.6):
                    certs = (cert,)
                elif n <= int(per_team * 0.85):
                    certs = (cert, SCALE_SECOND_CERT[team])
                else:
                    certs = tuple(sorted({cert, SCALE_SECOND_CERT[team], SCALE_SECOND_CERT[SCALE_TEAM_CERT_INV[SCALE_SECOND_CERT[team]]]}))
                people.append(FixturePerson(f"S{letter}{shift}-{n:03d}", team, shift, n, "MECH", certs))
    for shift in (1, 2):
        for n in range(1, qa_size + 1):
            people.append(FixturePerson(f"SQ{shift}-{n:03d}", "QA", shift, n, "QA", ("QA",) if n <= max(2, int(qa_size * 0.75)) else ("Z9",)))
    for p in people:
        for code in p.certs:
            ds["mechanic_certs"].append({"BEMS_ID": p.native, "CERT_CODE": code, "STATUS": "ACTIVE", "LAST_CERT_DATE": "2026/01/01",
                                         "CERT_EXP_DATE": "2027/12/31", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
        ds["person_team_map"].append({"BEMS_ID": p.native, "ROLE": p.role, "TEAM": p.team, "IN_SCOPE": True, "BASIS": "ASSIGNED",
                                      "EFFECTIVE_FROM": "2026-01-01T00:00:00", "EFFECTIVE_TO": None, "RUN_ID": "FIXTURE-ASSIGN-01",
                                      "EVIDENCE_COUNT": 0, "LOOKBACK_DAYS": 30, "LOAD_TIMESTAMP": LOAD_TS})
        ds["shift_roster"].append({"PERSON_KEY": p.native, "TEAM": p.team, "ROLE": p.role, "SITE": SITE, "SHIFT_NUMBER": p.shift,
                                   "EFFECTIVE_FROM": "2026-01-01", "EFFECTIVE_TO": None, "BASIS": "SCHEDULED_ROSTER", "EVIDENCE_REF": "FIXTURE-ROSTER-2026"})
        ds["people_directory"].append({"PERSON_KEY": p.native, "DISPLAY_NAME": p.display, "ORIGIN": "FIXTURE_DEMO"})
        ds["attendance_shift"].append({"PRODUCTION_DAY": "2026-10-26", "SHIFT_NUMBER": p.shift, "BEMS_ID": p.native, "PRESENT": True,
                                       "HOURS": "8.50", "SOURCE": "FIXTURE_BADGE", "LOAD_TIMESTAMP": "2026-10-26T05:45:00.000"})
    for team in list(SCALE_TEAM_CERT) + ["QA"]:
        for shift in (1, 2):
            ds["ot_authorizations"].append({"TEAM": team, "SHIFT_NUMBER": shift, "DATE_FROM": "2026-10-26", "DATE_TO": "2026-11-13",
                                            "MINUTES": 60, "AUTHORITY": "FIXTURE-OT-APPROVAL", "EVIDENCE_REF": "FIXTURE-OT-2026-10"})
    base = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    for cls, team in (("NC", "PROD-A"), ("PU", "PROD-B"), ("CI", "PROD-C")):
        for i in range(30):
            ds["duration_episodes"].append({"EPISODE_KEY": f"{cls}-{i:03d}", "REWORK_CLASS": cls, "TEAM": team, "MODEL": MODEL,
                                            "ELAPSED_MINUTES": str(30 + i), "PERSON_EFFORT_MINUTES": str(2 * (30 + i)), "CYCLE_MINUTES": str(40 + i),
                                            "STATUS": "verified_complete", "CREW": 2, "COMPLETED_AT_UTC": _utc(base + timedelta(days=i % 45)),
                                            "SOURCE": "fixture"})
    for team in SCALE_TEAM_CERT:
        for i in range(30):
            ds["duration_episodes"].append({"EPISODE_KEY": f"NC-{team}-{i:03d}", "REWORK_CLASS": "NC", "TEAM": team, "MODEL": MODEL,
                                            "ELAPSED_MINUTES": str(30 + i), "PERSON_EFFORT_MINUTES": str(2 * (30 + i)), "CYCLE_MINUTES": str(40 + i),
                                            "STATUS": "verified_complete", "CREW": 2, "COMPLETED_AT_UTC": _utc(base + timedelta(days=i % 45)),
                                            "SOURCE": "fixture"})
    ds["scope_receipt"].append({"SITE": SITE, "PROGRAM": PROGRAM, "SCOPE_BASIS": "ALL_WIP", "LINE_NUMBER": None, "ADMISSION": "ADMIT",
                                "REASON": "synthetic scale fixture: every non-COMPLETE line admitted", "EVIDENCE_REF": f"FIXTURE-SCALE-{n_aircraft}"})
    mapping_id = _uuid(f"scale-mapping-{n_aircraft}")
    scenario_id = _uuid(f"scale-scenario-{n_aircraft}")
    ds["econ_mapping_revision"].append({"MappingRevisionId": mapping_id, "SourceName": "fixture-scale-mapping", "SourcePredicate": "SITE='DEMO'",
                                        "QuerySha256": "0" * 64, "ContentSha256": "1" * 64, "RowCount": len(lines),
                                        "ImportedAtUtc": "2026-10-20T12:00:00.000", "CoverageJson": json.dumps({"lines": len(lines)}), "IsCurrent": True})
    ds["econ_scenario"].append({"ScenarioRevisionId": scenario_id, "ScenarioKey": "DEMO-SCALE", "ScenarioRevision": 1, "Provenance": "ASSUMED",
                                "Methodology": "Synthetic scale planning assumption", "OwnerSubject": "fixture-economics",
                                "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None, "Currency": "USD", "CapFraction": "0.2500",
                                "PriorityWeight": "1.00000", "CreatedAtUtc": "2026-10-20T12:00:00.000", "IsActive": True})
    for i, line in enumerate(lines, start=1):
        ds["econ_aircraft_mapping"].append({"MappingRevisionId": mapping_id, "LineNumber": line, "CustomerCode": CUSTOMER, "MinorModel": int(MODEL),
                                            "SourceStart": None, "SourceEnd": None, "RevisionDate": "2026-10-20", "ShopCompleteDate": None,
                                            "AssemblyLocation": "D", "SourceContentSha256": "2" * 64, "ImportedAtUtc": "2026-10-20T12:00:00.000"})
        rate = f"{rng.choice((10, 25, 40, 60, 100, 150, 200))}.0000"
        ds["econ_term"].append({"TermId": _uuid(f"scale-term-{n_aircraft}-{line}"), "ScenarioRevisionId": scenario_id, "SelectorType": "AIRCRAFT_OVERRIDE",
                                "SelectorLength": len(str(line)), "CustomerCode": CUSTOMER, "MinorModel": int(MODEL), "LineNumber": line,
                                "ContractKey": None, "OriginalSalePrice": "0.0000", "DailyRate": rate, "Currency": "USD",
                                "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None, "Provenance": "ASSUMED",
                                "Methodology": "Synthetic linear daily planning rate", "SourceReference": f"FIXTURE-SCALE-TERM-{line}",
                                "OwnerSubject": "fixture-economics", "ApprovedBy": None, "TermVersion": 1, "CreatedAtUtc": "2026-10-20T12:00:00.000"})
    out: dict[str, Dataset] = {}
    certified = {"mech_team_inferred", "cert_eligibility_matrix", "econ_original_sale_basis", "econ_liability_checkpoint", "team_inference", "clock_observations",
                 "requirement_census", "requirement_alternatives", "holding_events", "inspection_holds", "work_progress",
                 "part_events", "carry_in", "availability_exceptions", "econ_contract_assignment", "etracs_history", "actual_labor",
                 "labor_shift", "cert_course_map", "dept_team_skill_ref", "inspection_counts", "rework_classification", "neo_graph_rework",
                 "etracs_active"}
    for name in DATASETS:
        rows = ds[name]
        out[name] = Dataset(name, rows, _receipt(name, scenario, rows, certified_empty=name in certified,
                                                 note="synthetic scale fixture" if not rows and name in certified else None))
    return out


SCALE_TEAM_CERT_INV = {v: k for k, v in SCALE_TEAM_CERT.items()}
