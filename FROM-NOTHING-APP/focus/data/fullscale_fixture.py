"""Full-scale synthetic fleet: 50 aircraft, three 787 minor models, 30 teams on three shifts (labeled synthetic).

The program's stated shape (October 2026), every assumption an explicit field of ``FleetSpec``:

* **Aircraft.** ``n_aircraft`` lines over five control stations (740-744; 744 is roll out and the due proxy), each held for
  ``stay_days``. Aircraft i sits at station i % 5 and entered it 0-6 days before the cutoff. Work can be planned any
  distance ahead, but no task may start before its own station's window opens on its own aircraft (the window start is
  a start floor; the planner applies it and the independent validator re-checks it).
* **Customers and minor models.** The open-order mix of ``fixtures/economics/penalty_proxy.csv`` (firm 787 orders,
  30 September 2026), split over the fleet by largest remainder. Lateness is priced per customer and minor model with
  the proxy's daily penalty (ticket revenue a day; see that folder's README).
* **Tasks.** ``baseline`` tasks per minor-model network with ``edges`` model-level dependencies (predecessors always
  earlier: 60 % from a 150-task window of the same station, 25 % from anywhere earlier in it, 15 % from the previous
  station; 98 % finish to start). Rework: ``rework_total`` per aircraft found over the build in proportion to progress,
  ``open_rework_share`` of what has been found still open, each rework job gating an open task of its station.
* **Workforce.** ``teams`` teams with ``team_size`` mechanics each per shift on three shifts, and a QA team sized to the
  inspection load. Tasks go to teams in proportion to team size; task lengths are scaled so the fleet's work fills
  ``utilization`` of the mechanics' regular hours over the build.
* **Late parts.** ``late_part_share`` of each aircraft's baseline tasks get a part shortage. On open tasks the part is late
  relative to the task's need date: when the line-flow plan reaches it (its station window start plus its place in the
  station's sequence), or the cutoff when that has passed. ``late_delays`` gives (share, first day late, last day late)
  bands, by default 70 % one to three days, 25 % four to seven and 5 % eight to fourteen. An aircraft's lateness is set
  by its worst late parts (the maximum over hundreds of draws), so the tail of this mix decides how late the fleet runs.
  ``undated_share`` of shortages have no date yet: an undated shortage blocks its task and everything after it (unknown is
  never zero), which in a network this dense is most of the aircraft, so the starting state has none by default; the
  simulation introduces them on discovery and dates them within a few shifts. Completed tasks keep theirs as history.
* **Calendar.** Three shifts every day (seven-day operation) from the cutoff for ``horizon_days``.

The result is the same dataset mapping the fixture reader takes (``FixtureSourceReader(overrides=...)``), so acquisition,
admission and every engine path are the production ones.
"""
from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import fixture_builder as fb

PENALTY_PROXY = Path(__file__).resolve().parents[2] / "fixtures" / "economics" / "penalty_proxy.csv"
ICAO = {"UA": "UAL", "AA": "AAL", "QR": "QTR", "BA": "BAW", "AC": "ACA", "NH": "ANA", "JL": "JAL", "EY": "ETD", "SQ": "SIA",
        "BR": "EVA", "KE": "KAL", "TK": "THY"}
SHIFT_REGULAR_MINUTES = {1: 430, 2: 430, 3: 340}          # shift_fixture.json regular minutes
POSITIONS = 5
FIRST_STATION = 740


@dataclass(frozen=True)
class FleetSpec:
    n_aircraft: int = 50
    first_line: int = 1201
    baseline: int = 4500
    edges: int = 22000
    rework_total: int = 3000
    open_rework_share: float = 0.3
    stay_days: int = 7
    teams: int = 30
    team_size: tuple[int, int] = (10, 30)
    qa_per_shift: int | None = None
    utilization: float = 0.90
    late_part_share: float = 0.20
    late_delays: tuple[tuple[float, int, int], ...] = ((0.70, 1, 3), (0.25, 4, 7), (0.05, 8, 14))
    undated_share: float = 0.0
    cutoff_day: date = date(2026, 10, 26)
    horizon_days: int = 120
    seed: int = fb.SEED
    customer_models: tuple[tuple[str, str, int, int, int], ...] | None = None   # (code, name, minor model, open orders, daily USD)
    model_networks: tuple[str, ...] = field(default=("8", "9", "10"))


def penalty_terms(path: Path = PENALTY_PROXY) -> list[tuple[str, str, int, int, int]]:
    """(ICAO code, customer, minor model, open orders, daily penalty USD) for every row of the proxy table."""
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out.append((ICAO[row["customer_code"]], row["customer"], int(row["minor_model"]), int(row["open_orders"]),
                        int(row["daily_penalty_usd"])))
    return out


def fleet_mix(n: int, terms: list[tuple[str, str, int, int, int]]) -> list[tuple[str, str, int, int]]:
    """n aircraft split over the customer-model pairs with open orders, by largest remainder (ties by code, model)."""
    ordered = sorted((t for t in terms if t[3] > 0), key=lambda t: (t[0], t[2]))
    total = sum(t[3] for t in ordered)
    shares = [(n * t[3] / total, t) for t in ordered]
    counts = {(t[0], t[2]): int(s) for s, t in shares}
    rest = n - sum(counts.values())
    for _, t in sorted(shares, key=lambda st: (-(st[0] - int(st[0])), st[1][0], st[1][2]))[:rest]:
        counts[(t[0], t[2])] += 1
    out = []
    for code, name, model, _, rate in ordered:
        out.extend([(code, name, model, rate)] * counts[(code, model)])
    return out


def _round5(minutes: float, floor: int = 15) -> int:
    return max(floor, 5 * round(minutes / 5))


def build_fullscale_fixture(spec: FleetSpec = FleetSpec()) -> dict:
    rng = random.Random(spec.seed * 7919 + spec.n_aircraft * 31 + spec.baseline)
    ds: dict[str, list] = {name: [] for name in fb.DATASETS}
    cutoff_day = spec.cutoff_day
    cal_from, cal_to = fb.CAL_FROM, cutoff_day + timedelta(days=spec.horizon_days)
    lines = list(range(spec.first_line, spec.first_line + spec.n_aircraft))
    terms = list(spec.customer_models) if spec.customer_models else penalty_terms()
    mix = fleet_mix(spec.n_aircraft, terms)
    rng.shuffle(mix)                                                    # customers spread over the stations

    # ---- teams, certificates and sizes (mechanics per team per shift)
    team_names = [f"T{t:02d}" for t in range(1, spec.teams + 1)]
    cert = {t: f"C{i + 1:02d}" for i, t in enumerate(team_names)}
    second = {t: cert[team_names[(i + 1) % spec.teams]] for i, t in enumerate(team_names)}
    third = {t: cert[team_names[(i + 2) % spec.teams]] for i, t in enumerate(team_names)}
    size = {t: rng.randint(*spec.team_size) for t in team_names}
    team_weights = [size[t] for t in team_names]

    # ---- one task network per minor model (raw lengths; scaled below)
    per_pos = math.ceil(spec.baseline / POSITIONS)
    networks: dict[str, list[list]] = {}
    for model in spec.model_networks:
        mrng = random.Random(spec.seed * 104729 + int(model) * 7 + spec.baseline)
        tasks = []
        for i in range(spec.baseline):
            pos = min(POSITIONS - 1, i // per_pos)
            team = mrng.choices(team_names, weights=team_weights)[0]
            flow = mrng.choice((30, 45, 60, 60, 90, 120, 180, 240))
            crew = mrng.choice((1, 1, 2, 2, 2, 3))
            certs = (cert[team], second[team]) if crew <= 2 and mrng.random() < 0.15 else (cert[team],)
            tasks.append([f"B{i:05d}", pos, team, flow, crew, certs, mrng.random() < 0.08])
        edge_set: dict[tuple[str, str], str] = {}
        k_avg = spec.edges / spec.baseline
        for i in range(1, spec.baseline):
            pos = tasks[i][1]
            start = pos * per_pos
            k = max(0, min(i, int(mrng.expovariate(1 / k_avg) + 0.5)))
            for _ in range(k):
                r = mrng.random()
                if r < 0.60 and i - start > 1:
                    j = mrng.randint(max(start, i - 150), i - 1)
                elif r < 0.85 and i - start > 1:
                    j = mrng.randint(start, i - 1)
                else:
                    j = mrng.randint(max(0, start - per_pos), max(0, start - 1)) if start > 0 else mrng.randint(0, i - 1)
                if j == i:
                    continue
                t = mrng.random()
                edge_set.setdefault((tasks[j][0], tasks[i][0]), "FINISH <= START" if t < 0.98 else ("START <= START" if t < 0.99 else "FINISH <= FINISH"))
        for (pred, succ), rel in sorted(edge_set.items()):
            ds["pnet_dag"].append({"PREDECESSOR_SOI": pred, "SUCCESSOR_SOI": succ, "RELATIONSHIP_TYPE": rel, "IS_HARD": "Y",
                                   "PRED_MINOR_MODEL": model, "SUC_MINOR_MODEL": model, "ORIGINAL_PRED_JOBNUMBER": pred,
                                   "ORIGINAL_SUC_JOBNUMBER": succ, "OFFSET_MINUTES": 0, "LOAD_TIMESTAMP": fb.LOAD_TS})
        networks[model] = tasks

    # ---- scale task lengths so the build's work fills ``utilization`` of regular mechanic hours
    rework_raw = [30 + 2 * i for i in range(30)]                        # rework statistic episodes (minutes, crew 2)
    rework_labor = spec.rework_total * 2 * (sum(rework_raw) / len(rework_raw))
    demand = 0.0
    for _, _, model, _ in mix:
        demand += sum(t[3] * t[4] for t in networks[str(model)]) + rework_labor
    demand_per_day = demand / (POSITIONS * spec.stay_days)
    capacity_per_day = sum(size.values()) * sum(SHIFT_REGULAR_MINUTES.values())
    scale = spec.utilization * capacity_per_day / demand_per_day
    for tasks in networks.values():
        for t in tasks:
            t[3] = _round5(t[3] * scale)
    rework_minutes = [_round5(m * scale) for m in rework_raw]
    inspections_per_day = sum(sum(1 for t in networks[str(m)] if t[6]) for _, _, m, _ in mix) / (POSITIONS * spec.stay_days)
    qa_size = spec.qa_per_shift or max(4, math.ceil(inspections_per_day * 45 / (spec.utilization * sum(SHIFT_REGULAR_MINUTES.values()))))

    # ---- aircraft: census rows, rework found so far, part shortages, windows, calendars
    record_key, rec_id, hist_id = 1, 90000, 50000
    late_counts: dict[str, int] = {"undated": 0, "history": 0}
    for idx, line in enumerate(lines):
        code, customer, model, _ = mix[idx]
        tasks = networks[str(model)]
        p = idx % POSITIONS
        entered = cutoff_day - timedelta(days=rng.randint(0, spec.stay_days - 1))
        windows = {}
        for pos in range(POSITIONS):
            load = entered + timedelta(days=spec.stay_days * (pos - p))
            windows[pos] = (load, load + timedelta(days=spec.stay_days))
        elapsed = (cutoff_day - entered).days / spec.stay_days
        done_in_current = int(per_pos * min(0.95, 0.95 * elapsed))
        open_parents, done_parents = [], []
        for i, (soi, pos, team, flow, crew, certs, final) in enumerate(tasks):
            if pos < p or (pos == p and i - pos * per_pos < done_in_current):
                status = "COMPLETE"
            elif pos == p:
                status = rng.choice(("AVAILABLE", "AVAILABLE", "PLANNED", "IDLE"))
            else:
                status = "PLANNED"
            act_start = act_comp = None
            if status == "COMPLETE":
                d = min(windows[pos][0] + timedelta(days=rng.randint(0, spec.stay_days - 1)), cutoff_day - timedelta(days=1))
                act_start, act_comp = fb._local_naive(d, 7), fb._local_naive(d, 11)
                done_parents.append((soi, pos, team))
            else:
                open_parents.append((soi, pos, team))
            ds["master_task_list"].append({
                "SHOP_ORDER_IDENTIFIER": soi, "SHOP_ORDER_DESCRIPTION": f"Production task {soi}", "LINE_NUMBER": line,
                "SHOP_ORDER_STATUS": status, "TEAM": team, "CURRENT_CONTROL_STATION": str(FIRST_STATION + pos), "MINOR_MODEL_IDENT": str(model),
                "CUSTOMER_CODE": code, "PARENT_SOI": None, "SUPERINTENDENT": f"SUPT-{FIRST_STATION + pos}",
                "FIRST_AVAILABLE": fb._local_naive(windows[pos][0], 0), "ACT_START": act_start, "ACT_COMP": act_comp,
                "LOAD_TIMESTAMP": fb.LOAD_TS, "PLANNING_SYSTEM_NAME": "SYNTH-PS", "IS_REWORK": False, "RECORD_KEY": record_key})
            record_key += 1
            ds["flow_budget"].append({"JOB_NUMBER": soi, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": flow, "TOTAL_MECHANICS": crew,
                                      "PRIMARY_MECHANIC_COUNT": crew, "PRIMARY_MECHANIC_DURATION": flow, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                                      "TOTAL_LABOR_MINUTES": flow * crew, "DURATION_SOURCE": "FIXTURE_STANDARD", "LOAD_TIMESTAMP": fb.LOAD_TS})
            for c in certs:
                ds["soi_cert_requirements"].append({"SOI_ID": soi, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": f"PROC {c}", "CERT_CODE": c,
                                                    "CERT_TYPE": "CERT", "REQ_CERT": "Y", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            if final:
                ds["buyoff_plan"].append({"SHOP_ORDER_IDENTIFIER": soi, "LINE_NUMBER": line, "SHOP_ORDER_STATUS": status, "MANUFACTURING_INSP": 0,
                                          "QUALITY_INSP": 0, "CC_INSP": 0, "ODAUNITMEMBER_INSP": 0, "PARTNER_QT_INSP": 0, "FINAL_QUALITY_INSP": 1,
                                          "IN_PROCESS_QUALITY_INSP": 0, "LOAD_TIMESTAMP": fb.LOAD_TS})
            # late parts: history on completed tasks, active shortages on open ones
            if rng.random() < spec.late_part_share:
                in_station = (i - pos * per_pos) / per_pos
                need = max(windows[pos][0] + timedelta(days=int(in_station * spec.stay_days)), cutoff_day)
                if status == "COMPLETE":
                    hist_id += 1
                    late_counts["history"] += 1
                    ds["etracs_history"].append({"RECORD_ID": hist_id, "LINE_NUMBER": line, "USED_ON": soi, "PARENT_SOI": None, "PRIMARY_JOB": soi,
                                                 "ETRAC_ECD": (windows[pos][0] + timedelta(days=rng.randint(1, 10))).isoformat(),
                                                 "ACTUAL_CLOSURE_DATE": (windows[pos][0] + timedelta(days=rng.randint(1, 12))).isoformat(),
                                                 "LOAD_TIMESTAMP": fb.LOAD_TS})
                else:
                    if rng.random() < spec.undated_share:
                        ecd, kind = None, "undated"
                    else:
                        r, acc = rng.random() * sum(b[0] for b in spec.late_delays), 0.0
                        band = spec.late_delays[-1]
                        for b in spec.late_delays:
                            acc += b[0]
                            if r < acc:
                                band = b
                                break
                        ecd, kind = (need + timedelta(days=rng.randint(band[1], band[2]))).isoformat(), f"{band[1]}-{band[2]} days"
                    late_counts[kind] = late_counts.get(kind, 0) + 1
                    rec_id += 1
                    ds["etracs_active"].append({"RECORD_ID": rec_id, "LINE_NUMBER": line, "MINOR_MODEL_IDENT": str(model), "USED_ON": soi,
                                                "ETRAC_ECD": ecd, "TEAM": team, "REASON_CODE": "LP", "ETRAC_CREATED_DT": "2026-10-20",
                                                "ITEM_NUMBER": f"P-{rec_id}", "LOAD_TIMESTAMP": fb.LOAD_TS})
        # rework found so far, in proportion to progress through the build; open rework gates open tasks of the station
        progress = (p + min(1.0, elapsed)) / POSITIONS
        found = int(spec.rework_total * progress)
        n_open = int(found * spec.open_rework_share)
        candidates = [x for x in open_parents if x[1] == p] or open_parents
        for r in range(found):
            is_open = r < n_open and bool(candidates)
            pool = candidates if is_open else (done_parents or open_parents)
            parent, pos, team = pool[rng.randrange(len(pool))]
            child = f"R{r:05d}"
            cls = rng.choice(("NC", "PU", "CI"))
            ds["master_task_list"].append({
                "SHOP_ORDER_IDENTIFIER": child, "SHOP_ORDER_DESCRIPTION": f"Rework on {parent}", "LINE_NUMBER": line,
                "SHOP_ORDER_STATUS": "AVAILABLE" if is_open else "COMPLETE", "TEAM": team, "CURRENT_CONTROL_STATION": str(FIRST_STATION + pos),
                "MINOR_MODEL_IDENT": str(model), "CUSTOMER_CODE": code, "PARENT_SOI": parent, "SUPERINTENDENT": f"SUPT-{FIRST_STATION + pos}",
                "FIRST_AVAILABLE": fb._local_naive(cutoff_day - timedelta(days=1), 6), "ACT_START": None if is_open else fb._local_naive(cutoff_day - timedelta(days=1), 8),
                "ACT_COMP": None if is_open else fb._local_naive(cutoff_day - timedelta(days=1), 10), "LOAD_TIMESTAMP": fb.LOAD_TS,
                "PLANNING_SYSTEM_NAME": "SYNTH-PS", "IS_REWORK": True, "RECORD_KEY": record_key})
            record_key += 1
            ds["flow_budget"].append({"JOB_NUMBER": child, "LINE_NUMBER": line, "CRITICAL_FLOW_MINUTES": None, "TOTAL_MECHANICS": 2,
                                      "PRIMARY_MECHANIC_COUNT": 2, "PRIMARY_MECHANIC_DURATION": None, "ASSIST_COUNT": 0, "ASSIST_LABOR_MINUTES": 0,
                                      "TOTAL_LABOR_MINUTES": None, "DURATION_SOURCE": "REWORK_STATISTIC", "LOAD_TIMESTAMP": fb.LOAD_TS})
            ds["soi_cert_requirements"].append({"SOI_ID": child, "LINE_NUMBER": line, "CRITICAL_PROCESS_NAME": "PROC REWORK", "CERT_CODE": cert[team],
                                                "CERT_TYPE": "CERT", "REQ_CERT": "Y", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            ds["neo_graph_rework"].append({"LINE_NUMBER": line, "PARENT_SOI": parent, "CHILD_SOI": child, "CHILD_STATUS": "OPEN" if is_open else "CLOSED",
                                           "SNAPSHOT_TS": "2026-10-25T05:00:00"})
            ds["rework_classification"].append({"LINE_NUMBER": line, "CHILD_SOI": child, "REWORK_CLASS": cls, "ROOT_SOI": parent,
                                                "DEFECT_ORIGIN_TEAM": team, "EVIDENCE_REF": f"SYNTH-NCR-{line}-{r}"})
        d = cal_from
        while d <= cal_to:
            ds["cday_op_plan"].append({"LINE_NUMBER": line, "LINE": f"L{line}", "CALENDAR_DATE": d.isoformat(), "POSITION": "P744",
                                       "DAY_STATUS": "WORKING", "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001, "LOAD_TS": "2026-10-20T04:00:00",
                                       "IS_ACTIVE": True})
            d += timedelta(days=1)
        for pos in range(POSITIONS):
            load, unload = windows[pos]
            ds["cs_windows"].append({"MBU": "SYNTH", "CONTROL_STATION": FIRST_STATION + pos, "LINE_NUMBER": line, "LOAD_REF_POS": f"P{FIRST_STATION + pos}",
                                     "LOAD_OFFSET": 0, "CS_OP_LOAD": load.isoformat(), "UNLOAD_REF_POS": f"P{FIRST_STATION + pos}", "UNLOAD_OFFSET": 0,
                                     "CS_OP_UNLOAD": unload.isoformat(), "LAST_MODIFIED_DT": "2026-10-20", "LOAD_ID": 7001,
                                     "LOAD_TS": "2026-10-20T04:00:00", "IS_ACTIVE": True})

    # ---- standards, workforce, overtime, rework duration statistics
    for t, minutes, rel, desc in (("FINAL_QUALITY", 45, "AFTER_WORK_FS", "Final quality"), ("IN_PROCESS_QUALITY", 45, "TOTAL_ACROSS_HOLDS", "In-process quality")):
        ds["inspection_standards"].append({"INSPECTION_TYPE": t, "DURATION_MINUTES": minutes, "SCHEDULING_RELATIONSHIP": rel, "DESCRIPTION": desc})
    people = []
    for team in team_names:
        for shift in (1, 2, 3):
            n = size[team]
            for k in range(1, n + 1):
                r = k / n
                certs = (cert[team],) if r <= 0.5 else ((cert[team], second[team]) if r <= 0.85 else (cert[team], second[team], third[team]))
                people.append(fb.FixturePerson(f"M{team[1:]}{shift}-{k:04d}", team, shift, k, "MECH", tuple(sorted(set(certs)))))
    for shift in (1, 2, 3):
        for k in range(1, qa_size + 1):
            people.append(fb.FixturePerson(f"Q{shift}-{k:04d}", "QA", shift, k, "QA", ("QA",)))
    for p_ in people:
        for c in p_.certs:
            ds["mechanic_certs"].append({"BEMS_ID": p_.native, "CERT_CODE": c, "STATUS": "ACTIVE", "LAST_CERT_DATE": "2026/01/01",
                                         "CERT_EXP_DATE": "2027/12/31", "SNAPSHOT_TS": "2026-10-25T05:00:00"})
        ds["person_team_map"].append({"BEMS_ID": p_.native, "ROLE": p_.role, "TEAM": p_.team, "IN_SCOPE": True, "BASIS": "ASSIGNED",
                                      "EFFECTIVE_FROM": "2026-01-01T00:00:00", "EFFECTIVE_TO": None, "RUN_ID": "SYNTH-ASSIGN-01",
                                      "EVIDENCE_COUNT": 0, "LOOKBACK_DAYS": 30, "LOAD_TIMESTAMP": fb.LOAD_TS})
        ds["shift_roster"].append({"PERSON_KEY": p_.native, "TEAM": p_.team, "ROLE": p_.role, "SITE": fb.SITE, "SHIFT_NUMBER": p_.shift,
                                   "EFFECTIVE_FROM": "2026-01-01", "EFFECTIVE_TO": None, "BASIS": "SCHEDULED_ROSTER", "EVIDENCE_REF": "SYNTH-ROSTER-2026"})
        ds["people_directory"].append({"PERSON_KEY": p_.native, "DISPLAY_NAME": p_.display, "ORIGIN": "FIXTURE_SYNTHETIC"})
        ds["attendance_shift"].append({"PRODUCTION_DAY": cutoff_day.isoformat(), "SHIFT_NUMBER": p_.shift, "BEMS_ID": p_.native, "PRESENT": True,
                                       "HOURS": "8.50" if p_.shift != 3 else "7.00", "SOURCE": "FIXTURE_BADGE", "LOAD_TIMESTAMP": "2026-10-26T05:45:00.000"})
    for team in team_names + ["QA"]:
        for shift in (1, 2, 3):
            ds["ot_authorizations"].append({"TEAM": team, "SHIFT_NUMBER": shift, "DATE_FROM": cutoff_day.isoformat(), "DATE_TO": cal_to.isoformat(),
                                            "MINUTES": 60, "AUTHORITY": "SYNTH-OT-APPROVAL", "EVIDENCE_REF": "SYNTH-OT-2026"})
    base = datetime(2026, 9, 1, 14, 0, tzinfo=fb.UTC)
    for cls in ("NC", "PU", "CI"):
        for team in team_names:
            for i, minutes in enumerate(rework_minutes):
                ds["duration_episodes"].append({"EPISODE_KEY": f"{cls}-{team}-{i:03d}", "REWORK_CLASS": cls, "TEAM": team, "MODEL": "787",
                                                "ELAPSED_MINUTES": str(minutes), "PERSON_EFFORT_MINUTES": str(2 * minutes),
                                                "CYCLE_MINUTES": str(minutes + 10), "STATUS": "verified_complete", "CREW": 2,
                                                "COMPLETED_AT_UTC": fb._utc(base + timedelta(days=i % 45)), "SOURCE": "fixture"})
    ds["scope_receipt"].append({"SITE": fb.SITE, "PROGRAM": fb.PROGRAM, "SCOPE_BASIS": "ALL_WIP", "LINE_NUMBER": None, "ADMISSION": "ADMIT",
                                "REASON": "full-scale synthetic fleet", "EVIDENCE_REF": f"FULLSCALE-{spec.n_aircraft}"})

    # ---- economics: one penalty term per customer and minor model (CUSTOMER_MODEL selector)
    mapping_id, scenario_id = fb._uuid(f"fullscale-mapping-{spec.n_aircraft}"), fb._uuid(f"fullscale-scenario-{spec.n_aircraft}")
    ds["econ_mapping_revision"].append({"MappingRevisionId": mapping_id, "SourceName": "fullscale-mapping", "SourcePredicate": "SITE='DEMO'",
                                        "QuerySha256": "0" * 64, "ContentSha256": "1" * 64, "RowCount": len(lines),
                                        "ImportedAtUtc": "2026-10-20T12:00:00.000", "CoverageJson": json.dumps({"lines": len(lines)}), "IsCurrent": True})
    ds["econ_scenario"].append({"ScenarioRevisionId": scenario_id, "ScenarioKey": "SYNTH-PENALTY-PROXY", "ScenarioRevision": 1, "Provenance": "ASSUMED",
                                "Methodology": "Ticket-revenue proxy per customer and minor model (fixtures/economics/README.md)",
                                "OwnerSubject": "fixture-economics", "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None,
                                "Currency": "USD", "CapFraction": "0.0000", "PriorityWeight": "1.00000", "CreatedAtUtc": "2026-10-20T12:00:00.000",
                                "IsActive": True})
    for idx, line in enumerate(lines):
        code, _, model, _ = mix[idx]
        ds["econ_aircraft_mapping"].append({"MappingRevisionId": mapping_id, "LineNumber": line, "CustomerCode": code, "MinorModel": model,
                                            "SourceStart": None, "SourceEnd": None, "RevisionDate": "2026-10-20", "ShopCompleteDate": None,
                                            "AssemblyLocation": "D", "SourceContentSha256": "2" * 64, "ImportedAtUtc": "2026-10-20T12:00:00.000"})
    for code, customer, model, _, rate in sorted({(m[0], m[1], m[2], 0, m[3]) for m in mix}):
        ds["econ_term"].append({"TermId": fb._uuid(f"fullscale-term-{code}-{model}"), "ScenarioRevisionId": scenario_id, "SelectorType": "CUSTOMER_MODEL",
                                "SelectorLength": len(code), "CustomerCode": code, "MinorModel": model, "LineNumber": None, "ContractKey": None,
                                "OriginalSalePrice": "0.0000", "DailyRate": f"{rate}.0000", "Currency": "USD",
                                "EffectiveFromUtc": "2026-01-01T00:00:00.000", "EffectiveToUtc": None, "Provenance": "ASSUMED",
                                "Methodology": f"Ticket-revenue proxy: {customer} 787-{model}", "SourceReference": "fixtures/economics/penalty_proxy.csv",
                                "OwnerSubject": "fixture-economics", "ApprovedBy": None, "TermVersion": 1, "CreatedAtUtc": "2026-10-20T12:00:00.000"})
    certified = {"mech_team_inferred", "cert_eligibility_matrix", "econ_original_sale_basis", "econ_liability_checkpoint", "team_inference", "clock_observations",
                 "requirement_census", "requirement_alternatives", "holding_events", "inspection_holds", "work_progress", "part_events", "carry_in",
                 "availability_exceptions", "econ_contract_assignment", "etracs_history", "actual_labor", "labor_shift", "cert_course_map",
                 "dept_team_skill_ref", "inspection_counts", "rework_classification", "neo_graph_rework", "etracs_active"}
    scenario = f"fullscale_{spec.n_aircraft}"
    out = {name: fb.Dataset(name, ds[name], fb._receipt(name, scenario, ds[name], certified_empty=name in certified,
                                                         note="full-scale synthetic fleet" if not ds[name] and name in certified else None))
           for name in fb.DATASETS}
    FLEET_FACTS.clear()
    FLEET_FACTS.update({"scale": round(scale, 4), "qa_per_shift": qa_size, "team_sizes": dict(size), "team_certs": dict(cert),
                        "mechanics_per_shift": sum(size.values()),
                        "late_parts": dict(late_counts), "mix": [(m[0], m[2]) for m in mix], "calendar_to": cal_to.isoformat()})
    return out


FLEET_FACTS: dict = {}                       # the last build's derived facts (length scale, QA size, late-part counts)
