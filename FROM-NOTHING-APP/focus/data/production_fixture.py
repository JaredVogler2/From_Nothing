"""Production-shaped synthetic fixture (labeled synthetic; real readiness not established).

The shape follows the program's stated numbers (2026-10-30): about 4,500 baseline tasks and 3,000 rework tasks per
aircraft over positions 0-3 and roll out (5 positions, each owning its tasks), and about 25,000 dependencies per aircraft,
interwoven across teams. It exists to measure the engine at that shape (``tools/benchmark_production.py``); it is not a
model of any real aircraft.

Assumptions, all explicit parameters:

* the baseline network is model-level (shared by every aircraft of the model): predecessors are always earlier tasks
  (acyclic), 60 % from a 150-task window of the same position, 25 % from anywhere earlier in the position and 15 %
  from the previous position; 98 % finish-to-start, 1 % start-to-start, 1 % finish-to-finish (the S04 mix);
* aircraft ``i`` sits at position ``i % 5``; earlier positions are complete, 40 % of the current one is complete and
  later positions cannot start before their station's load date (stations 740-744, 7-day stays, roll out = 744, the
  due proxy station);
* open rework at a point in time is 30 % of the aircraft's 3,000, linked as gates of open tasks of its position;
* 10 production teams and QA on 2 shifts, shared by every aircraft; ``max(12, 6 x aircraft)`` mechanics per team and
  shift; 50 % hold one certificate, 35 % two, 15 % three; 15 % of tasks of crew 1-2 require two certificates;
* every part shortage has an expected date unless ``no_ecd_share`` says otherwise (an undated shortage blocks its root
  and, in a network this dense, much of the aircraft).
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta

from . import fixture_builder as fb

CAL_TO = date(2026, 12, 31)
STAY_DAYS = 7


def _status_for(d: date) -> str:
    if d <= fb.CAL_TO:
        return fb._calendar_status(d)
    if d == date(2026, 11, 26):
        return "NONWORKING"
    return "WEEKEND" if d.weekday() >= 5 else "WORKING"


def build_production_fixture(n_aircraft: int, *, baseline: int = 4500, rework_total: int = 3000, open_rework_share: float = 0.3,
                       edges: int = 22000, positions: int = 5, teams: int = 10, per_team_shift: int | None = None,
                       qa_per_shift: int | None = None, position_of=None, no_ecd_share: float = 0.0, seed: int = fb.SEED) -> dict:
    rng = random.Random(seed * 7919 + n_aircraft * 31 + baseline)
    lines = list(range(3001, 3001 + n_aircraft))
    ds: dict[str, list] = {name: [] for name in fb.DATASETS}
    team_names = [f"T{t:02d}" for t in range(1, teams + 1)]
    cert = {t: f"C{i + 1:02d}" for i, t in enumerate(team_names)}
    second = {t: cert[team_names[(i + 1) % teams]] for i, t in enumerate(team_names)}
    third = {t: cert[team_names[(i + 2) % teams]] for i, t in enumerate(team_names)}
    per_pos = math.ceil(baseline / positions)
    tasks = []
    for i in range(baseline):
        pos = min(positions - 1, i // per_pos)
        team = rng.choice(team_names)
        flow = rng.choice((30, 45, 60, 60, 90, 120, 180, 240))
        crew = rng.choice((1, 1, 2, 2, 2, 3))
        certs = (cert[team], second[team]) if crew <= 2 and rng.random() < 0.15 else (cert[team],)
        tasks.append((f"B{i:05d}", pos, team, flow, crew, certs, rng.random() < 0.08))
    # model-level network: predecessors always earlier (acyclic); window, same-position and cross-position links
    edge_set: dict[tuple[str, str], str] = {}
    k_avg = edges / baseline
    for i in range(1, baseline):
        pos = tasks[i][1]
        start = pos * per_pos
        k = max(0, min(i, int(rng.expovariate(1 / k_avg) + 0.5)))
        for _ in range(k):
            r = rng.random()
            if r < 0.60 and i - start > 1:
                j = rng.randint(max(start, i - 150), i - 1)
            elif r < 0.85 and i - start > 1:
                j = rng.randint(start, i - 1)
            else:
                j = rng.randint(max(0, start - per_pos), max(0, start - 1)) if start > 0 else rng.randint(0, i - 1)
            if j == i:
                continue
            t = rng.random()
            edge_set.setdefault((tasks[j][0], tasks[i][0]), "FINISH <= START" if t < 0.98 else ("START <= START" if t < 0.99 else "FINISH <= FINISH"))
    for (pred, succ), rel in edge_set.items():
        ds["pnet_dag"].append({"PREDECESSOR_SOI": pred, "SUCCESSOR_SOI": succ, "RELATIONSHIP_TYPE": rel, "IS_HARD": "Y",
                               "PRED_MINOR_MODEL": fb.MODEL, "SUC_MINOR_MODEL": fb.MODEL, "ORIGINAL_PRED_JOBNUMBER": pred,
                               "ORIGINAL_SUC_JOBNUMBER": succ, "OFFSET_MINUTES": 0, "LOAD_TIMESTAMP": fb.LOAD_TS})
    per_team = per_team_shift or max(12, math.ceil(6 * n_aircraft))
    qa_size = qa_per_shift or max(6, math.ceil(2 * n_aircraft))
    record_key, rec_id = 1, 90000
    cutoff_day = date(2026, 10, 26)
    for idx, line in enumerate(lines):
        p = position_of(idx) if position_of else idx % positions
        entered = cutoff_day - timedelta(days=rng.randint(0, STAY_DAYS - 1))
        windows = {}
        for pos in range(positions):
            load = entered + timedelta(days=STAY_DAYS * (pos - p))
            windows[pos] = (load, load + timedelta(days=STAY_DAYS))
        done_in_current = int(per_pos * 0.4)
        open_parents = []
        for i, (soi, pos, team, flow, crew, certs, final) in enumerate(tasks):
            if pos < p or (pos == p and i - pos * per_pos < done_in_current):
                status = "COMPLETE"
            elif pos == p:
                status = rng.choice(("AVAILABLE", "AVAILABLE", "PLANNED", "IDLE"))
            else:
                status = "PLANNED"
            act_start = act_comp = None
            if status == "COMPLETE":
                d = min(windows[pos][0] + timedelta(days=rng.randint(0, 3)), cutoff_day - timedelta(days=1))
                act_start, act_comp = fb._local_naive(d, 7), fb._local_naive(d, 11)
            else:
                open_parents.append((soi, pos, team))
            ds["master_task_list"].append({
                "SHOP_ORDER_IDENTIFIER": soi, "SHOP_ORDER_DESCRIPTION": f"Production task {soi}", "LINE_NUMBER": line,
                "SHOP_ORDER_STATUS": status, "TEAM": team, "CURRENT_CONTROL_STATION": str(740 + pos), "MINOR_MODEL_IDENT": fb.MODEL,
                "CUSTOMER_CODE": fb.CUSTOMER, "PARENT_SOI": None, "SUPERINTENDENT": "DEMO-SUPT",
                "FIRST_AVAILABLE": fb._local_naive(date(2026, 10, 25), 6), "ACT_START": act_start, "ACT_COMP": act_comp,
                "LOAD_TIMESTAMP": fb.LOAD_TS, "PLANNING_SYSTEM_NAME": "DEMO-PS", "IS_REWORK": False, "RECORD_KEY": record_key})
            record_key += 1
            ds["flow_budget"].append({"JOB_NUMBER": soi, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": flow, "TOTAL_MECHANICS": crew,
                                      "PRIMARY_MECHANIC_COUNT": crew, "PRIMARY_MECHANIC_DURATION": flow, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                                      "TOTAL_LABOR_MINUTES": flow * crew, "DURATION_SOURCE": "FIXTURE_STANDARD", "LOAD_TIMESTAMP": fb.LOAD_TS})
            for code in certs:
                ds["soi_cert_requirements"].append({"SOI_ID": soi, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": f"PROC {code}", "CERT_CODE": code,
                                                    "CERT_TYPE": "CERT", "REQ_CERT": "Y", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            if final:
                ds["buyoff_plan"].append({"SHOP_ORDER_IDENTIFIER": soi, "LINE_NUMBER": line, "SHOP_ORDER_STATUS": status, "MANUFACTURING_INSP": 0,
                                          "QUALITY_INSP": 0, "CC_INSP": 0, "ODAUNITMEMBER_INSP": 0, "PARTNER_QT_INSP": 0, "FINAL_QUALITY_INSP": 1,
                                          "IN_PROCESS_QUALITY_INSP": 0, "LOAD_TIMESTAMP": fb.LOAD_TS})
        n_open_rework = int(rework_total * open_rework_share)
        candidates = [x for x in open_parents if x[1] == p] or open_parents
        for r in range(n_open_rework if candidates else 0):
            parent, pos, team = candidates[rng.randrange(len(candidates))]
            child = f"R{r:05d}"
            cls = rng.choice(("NC", "PU", "CI"))
            ds["master_task_list"].append({
                "SHOP_ORDER_IDENTIFIER": child, "SHOP_ORDER_DESCRIPTION": f"Rework on {parent}", "LINE_NUMBER": line, "SHOP_ORDER_STATUS": "AVAILABLE",
                "TEAM": team, "CURRENT_CONTROL_STATION": str(740 + pos), "MINOR_MODEL_IDENT": fb.MODEL, "CUSTOMER_CODE": fb.CUSTOMER,
                "PARENT_SOI": parent, "SUPERINTENDENT": "DEMO-SUPT", "FIRST_AVAILABLE": fb._local_naive(date(2026, 10, 25), 6), "ACT_START": None,
                "ACT_COMP": None, "LOAD_TIMESTAMP": fb.LOAD_TS, "PLANNING_SYSTEM_NAME": "DEMO-PS", "IS_REWORK": True, "RECORD_KEY": record_key})
            record_key += 1
            ds["flow_budget"].append({"JOB_NUMBER": child, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": None, "TOTAL_MECHANICS": 2,
                                      "PRIMARY_MECHANIC_COUNT": 2, "PRIMARY_MECHANIC_DURATION": None, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                                      "TOTAL_LABOR_MINUTES": None, "DURATION_SOURCE": "REWORK_STATISTIC", "LOAD_TIMESTAMP": fb.LOAD_TS})
            ds["soi_cert_requirements"].append({"SOI_ID": child, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": "PROC REWORK", "CERT_CODE": cert[team],
                                                "CERT_TYPE": "CERT", "REQ_CERT": "Y", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            ds["neo_graph_rework"].append({"LINE_NUMBER": line, "PARENT_SOI": parent, "CHILD_SOI": child, "CHILD_STATUS": "OPEN", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            ds["rework_classification"].append({"LINE_NUMBER": line, "CHILD_SOI": child, "REWORK_CLASS": cls, "ROOT_SOI": parent,
                                                "DEFECT_ORIGIN_TEAM": team, "EVIDENCE_REF": f"PROD-NCR-{line}-{r}"})
        for _ in range(max(1, len(open_parents) // 100)):
            used_on = open_parents[rng.randrange(len(open_parents))][0]
            ecd = None if rng.random() < no_ecd_share else (cutoff_day + timedelta(days=rng.randint(0, 10))).isoformat()
            rec_id += 1
            ds["etracs_active"].append({"RECORD_ID": rec_id, "LINE_NUMBER": line, "MINOR_MODEL_IDENT": fb.MODEL, "USED_ON": used_on, "ETRAC_ECD": ecd,
                                        "TEAM": team_names[0], "REASON_CODE": "LP", "ETRAC_CREATED_DT": "2026-10-20", "ITEM_NUMBER": f"P-{rec_id}",
                                        "LOAD_TIMESTAMP": fb.LOAD_TS})
        d = fb.CAL_FROM
        while d <= CAL_TO:
            ds["cday_op_plan"].append({"LINE_NUMBER": line, "LINE": f"L{line}", "CALENDAR_DATE": d.isoformat(), "POSITION": "P744",
                                       "DAY_STATUS": _status_for(d), "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001,
                                       "LOAD_TS": "2026-10-20T04:00:00", "IS_ACTIVE": True})
            d += timedelta(days=1)
        for pos in range(positions):
            load, unload = windows[pos]
            ds["cs_windows"].append({"MBU": "DEMO", "CONTROL_STATION": 740 + pos, "LINE_NUMBER": line, "LOAD_REF_POS": f"P{740 + pos}",
                                     "LOAD_OFFSET": 0, "CS_OP_LOAD": load.isoformat(), "UNLOAD_REF_POS": f"P{740 + pos}", "UNLOAD_OFFSET": 0,
                                     "CS_OP_UNLOAD": unload.isoformat(), "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001,
                                     "LOAD_TS": "2026-10-20T04:00:00", "IS_ACTIVE": True})
    for t, minutes, rel, desc in (("FINAL_QUALITY", 45, "AFTER_WORK_FS", "Final quality"), ("IN_PROCESS_QUALITY", 45, "TOTAL_ACROSS_HOLDS", "In-process quality")):
        ds["inspection_standards"].append({"INSPECTION_TYPE": t, "DURATION_MINUTES": minutes, "SCHEDULING_RELATIONSHIP": rel, "DESCRIPTION": desc})
    people = []
    for team in team_names:
        for shift in (1, 2):
            for n in range(1, per_team + 1):
                r = n / per_team
                certs = (cert[team],) if r <= 0.5 else ((cert[team], second[team]) if r <= 0.85 else (cert[team], second[team], third[team]))
                people.append(fb.FixturePerson(f"M{team[1:]}{shift}-{n:04d}", team, shift, n, "MECH", tuple(sorted(set(certs)))))
    for shift in (1, 2):
        for n in range(1, qa_size + 1):
            people.append(fb.FixturePerson(f"Q{shift}-{n:04d}", "QA", shift, n, "QA", ("QA",)))
    for p_ in people:
        for code in p_.certs:
            ds["mechanic_certs"].append({"BEMS_ID": p_.native, "CERT_CODE": code, "STATUS": "ACTIVE", "LAST_CERT_DATE": "2026/01/01",
                                         "CERT_EXP_DATE": "2027/12/31", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
        ds["person_team_map"].append({"BEMS_ID": p_.native, "ROLE": p_.role, "TEAM": p_.team, "IN_SCOPE": True, "BASIS": "ASSIGNED",
                                      "EFFECTIVE_FROM": "2026-01-01T00:00:00", "EFFECTIVE_TO": None, "RUN_ID": "FIXTURE-ASSIGN-01",
                                      "EVIDENCE_COUNT": 0, "LOOKBACK_DAYS": 30, "LOAD_TIMESTAMP": fb.LOAD_TS})
        ds["shift_roster"].append({"PERSON_KEY": p_.native, "TEAM": p_.team, "ROLE": p_.role, "SITE": fb.SITE, "SHIFT_NUMBER": p_.shift,
                                   "EFFECTIVE_FROM": "2026-01-01", "EFFECTIVE_TO": None, "BASIS": "SCHEDULED_ROSTER", "EVIDENCE_REF": "FIXTURE-ROSTER-2026"})
        ds["people_directory"].append({"PERSON_KEY": p_.native, "DISPLAY_NAME": p_.display, "ORIGIN": "FIXTURE_DEMO"})
        ds["attendance_shift"].append({"PRODUCTION_DAY": "2026-10-26", "SHIFT_NUMBER": p_.shift, "BEMS_ID": p_.native, "PRESENT": True,
                                       "HOURS": "8.50", "SOURCE": "FIXTURE_BADGE", "LOAD_TIMESTAMP": "2026-10-26T05:45:00.000"})
    for team in team_names + ["QA"]:
        for shift in (1, 2):
            ds["ot_authorizations"].append({"TEAM": team, "SHIFT_NUMBER": shift, "DATE_FROM": "2026-10-26", "DATE_TO": CAL_TO.isoformat(),
                                            "MINUTES": 60, "AUTHORITY": "FIXTURE-OT-APPROVAL", "EVIDENCE_REF": "FIXTURE-OT-2026"})
    base = datetime(2026, 9, 1, 14, 0, tzinfo=fb.UTC)
    for cls in ("NC", "PU", "CI"):
        for team in team_names:
            for i in range(30):
                ds["duration_episodes"].append({"EPISODE_KEY": f"{cls}-{team}-{i:03d}", "REWORK_CLASS": cls, "TEAM": team, "MODEL": fb.MODEL,
                                                "ELAPSED_MINUTES": str(30 + 2 * i), "PERSON_EFFORT_MINUTES": str(2 * (30 + 2 * i)),
                                                "CYCLE_MINUTES": str(40 + 2 * i), "STATUS": "verified_complete", "CREW": 2,
                                                "COMPLETED_AT_UTC": fb._utc(base + timedelta(days=i % 45)), "SOURCE": "fixture"})
    ds["scope_receipt"].append({"SITE": fb.SITE, "PROGRAM": fb.PROGRAM, "SCOPE_BASIS": "ALL_WIP", "LINE_NUMBER": None, "ADMISSION": "ADMIT",
                                "REASON": "production-shaped synthetic fixture", "EVIDENCE_REF": f"PROD-SHAPE-{n_aircraft}"})
    mapping_id, scenario_id = fb._uuid(f"prod-mapping-{n_aircraft}"), fb._uuid(f"prod-scenario-{n_aircraft}")
    ds["econ_mapping_revision"].append({"MappingRevisionId": mapping_id, "SourceName": "prod-shape-mapping", "SourcePredicate": "SITE='DEMO'",
                                        "QuerySha256": "0" * 64, "ContentSha256": "1" * 64, "RowCount": len(lines),
                                        "ImportedAtUtc": "2026-10-20T12:00:00.000", "CoverageJson": json.dumps({"lines": len(lines)}), "IsCurrent": True})
    ds["econ_scenario"].append({"ScenarioRevisionId": scenario_id, "ScenarioKey": "DEMO-PROD", "ScenarioRevision": 1, "Provenance": "ASSUMED",
                                "Methodology": "Synthetic planning assumption", "OwnerSubject": "fixture-economics", "EffectiveFromUtc": "2026-01-01T00:00:00.000",
                                "EffectiveToUtc": None, "Currency": "USD", "CapFraction": "0.2500", "PriorityWeight": "1.00000",
                                "CreatedAtUtc": "2026-10-20T12:00:00.000", "IsActive": True})
    for line in lines:
        ds["econ_aircraft_mapping"].append({"MappingRevisionId": mapping_id, "LineNumber": line, "CustomerCode": fb.CUSTOMER, "MinorModel": int(fb.MODEL),
                                            "SourceStart": None, "SourceEnd": None, "RevisionDate": "2026-10-20", "ShopCompleteDate": None,
                                            "AssemblyLocation": "D", "SourceContentSha256": "2" * 64, "ImportedAtUtc": "2026-10-20T12:00:00.000"})
        ds["econ_term"].append({"TermId": fb._uuid(f"prod-term-{n_aircraft}-{line}"), "ScenarioRevisionId": scenario_id, "SelectorType": "AIRCRAFT_OVERRIDE",
                                "SelectorLength": len(str(line)), "CustomerCode": fb.CUSTOMER, "MinorModel": int(fb.MODEL), "LineNumber": line,
                                "ContractKey": None, "OriginalSalePrice": "0.0000", "DailyRate": f"{rng.choice((10, 25, 40, 60, 100, 150, 200))}.0000",
                                "Currency": "USD", "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None, "Provenance": "ASSUMED",
                                "Methodology": "Synthetic linear daily planning rate", "SourceReference": f"PROD-TERM-{line}",
                                "OwnerSubject": "fixture-economics", "ApprovedBy": None, "TermVersion": 1, "CreatedAtUtc": "2026-10-20T12:00:00.000"})
    certified = {"mech_team_inferred", "cert_eligibility_matrix", "econ_original_sale_basis", "econ_liability_checkpoint", "team_inference", "clock_observations",
                 "requirement_census", "requirement_alternatives", "holding_events", "inspection_holds", "work_progress", "part_events", "carry_in",
                 "availability_exceptions", "econ_contract_assignment", "etracs_history", "actual_labor", "labor_shift", "cert_course_map",
                 "dept_team_skill_ref", "inspection_counts", "rework_classification", "neo_graph_rework", "etracs_active"}
    scenario = f"production_{n_aircraft}"
    return {name: fb.Dataset(name, ds[name], fb._receipt(name, scenario, ds[name], certified_empty=name in certified,
                                                           note="production-shaped synthetic fixture" if not ds[name] and name in certified else None))
            for name in fb.DATASETS}
