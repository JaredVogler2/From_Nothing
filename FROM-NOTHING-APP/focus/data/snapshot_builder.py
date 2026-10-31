"""Admission: build one coherent immutable PlanningSnapshot from one source run (S03, S05, P04).

The snapshot is a pure function of (source run datasets, policy, cutoff).
Every engine path, validator, serializer and UI view reads only these bound
facts. CS windows come from the same run; no second head is queried. The
census always reconciles: admitted + blocked + quarantined + excluded +
verified complete = observed census, and every non-admitted job carries its
reasons.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from fractions import Fraction
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..calendar import ShiftPolicy, admit_line_calendars, horizon_dates, shifts_between
from ..codec import content_hash
from ..contracts.planning import (
    AircraftCensus,
    JobDisposition,
    PlanningSnapshot,
    ReadinessGap,
    seal_snapshot,
)
from ..domain import Disposition, RequirementStatus, digest_id, minute_floor
from ..eligibility import Eligibility
from ..economics.repository import build_economics
from ..policy import Policy
from ..simulation.pools import build_virtual_support, merge_virtual_pools
from .cs_windows import build_cs_facts, raw_rows
from .duration_gauge import DurationStatistics, compute_statistics
from .durations import DurationResolution, flow_index, resolve
from .freshness import freshness_gaps
from .graph import build_graph
from .inspections import build_triggers
from .parts import build_parts
from .qualifications import build_holdings, build_requirements, matrix_label_notes
from .scope import build_census, index_master, scope_config
from .sourcetime import SourceTimeError, TimezonePolicy
from .workforce import build_workforce

UTC = timezone.utc


class AdmissionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class SnapshotBuild:
    snapshot: PlanningSnapshot
    statistics: list[DurationStatistics]
    notes: list[str] = field(default_factory=list)


def _zone(tzp: TimezonePolicy, dataset: str) -> ZoneInfo | None:
    """The dataset's declared source timezone; None (never a guessed zone) when the policy is missing."""
    return tzp.zone(dataset) if tzp.has(dataset) else None


def build_snapshot(*, run_id: str, scope_key: str, site: str, program: str, mode: str, cutoff: datetime, created_at: datetime,
                   datasets: Mapping[str, tuple[Mapping[str, Any], ...]], dataset_status: Mapping[str, str],
                   dataset_ids: Mapping[str, str], dataset_lineage: Mapping[str, str], manifest_hash: str,
                   tzp: TimezonePolicy, policy: Policy, incumbent_id: str | None = None,
                   dataset_queries: Mapping[str, str] | None = None, dataset_receipts: Mapping[str, Mapping[str, Any]] | None = None,
                   dataset_captured: Mapping[str, datetime] | None = None) -> SnapshotBuild:
    notes: list[str] = []
    gaps: list[ReadinessGap] = []
    shift_policy = ShiftPolicy.from_policy(policy)
    tz = ZoneInfo(shift_policy.tz_name)
    master = list(datasets.get("master_task_list", ()))
    if not master and dataset_status.get("master_task_list") != "CERTIFIED_EMPTY":
        raise AdmissionError("CENSUS_UNAVAILABLE", "the WIP census returned no rows without a certified-empty receipt")
    for name, status in sorted(dataset_status.items()):
        if status in ("NOT_PROBED", "EMPTY_UNCERTIFIED", "FAILED"):
            effect = "BLOCKS_EXECUTABLE" if status != "FAILED" else "BLOCKS_EXECUTABLE"
            gaps.append(ReadinessGap(f"DATASET_{status}", name, f"dataset {name} is {status}", effect))
    for name in ("master_task_list", "cday_op_plan", "cs_windows"):
        if not tzp.has(name):
            gaps.append(ReadinessGap("SOURCE_TIMEZONE_POLICY_MISSING", name, "no explicit source timezone policy", "BLOCKS_ALL"))
    gaps.extend(freshness_gaps(datasets=datasets, receipts=dataset_receipts, captured=dataset_captured, tzp=tzp, policy=policy, cutoff=cutoff))

    scope = scope_config(datasets.get("scope_receipt", ()), default_site=site, default_program=program, policy=policy)
    job_index, keys, duplicates = index_master(master, scope)
    flows = flow_index(datasets.get("flow_budget", ()))
    rework = {(int(r["LINE_NUMBER"]), str(r["CHILD_SOI"])): r for r in datasets.get("rework_classification", ())}
    progress = {(int(r["LINE_NUMBER"]), str(r["SOI"])): r for r in datasets.get("work_progress", ())}
    triggers, trigger_notes = build_triggers(buyoff_rows=datasets.get("buyoff_plan", ()), hold_rows=datasets.get("inspection_holds", ()),
                                             count_rows=datasets.get("inspection_counts", ()), job_index=job_index, policy=policy,
                                             dataset_ids=dataset_ids, run_id=run_id)
    notes.extend(trigger_notes)
    try:
        census = build_census(master_rows=master, scope=scope, policy=policy, tzp=tzp, flow=flows, rework=rework, progress=progress,
                              triggers=triggers, job_index=job_index, keys=keys, duplicates=duplicates,
                              dataset_id=dataset_ids.get("master_task_list", ""), run_id=run_id, cutoff=cutoff)
    except SourceTimeError as exc:
        raise AdmissionError(exc.code, exc.message) from None
    notes.extend(census.problems)
    lines = sorted({job.line for job in census.jobs.values()})

    # ---- calendars, shifts, CS (same run) ----
    calendars = admit_line_calendars(datasets.get("cday_op_plan", ()), policy, lines)
    for line, cal in calendars.items():
        for reason in cal.reasons:
            gaps.append(ReadinessGap(reason.split(":")[0], f"line {line}", reason, "BLOCKS_ALL" if cal.status != "ADMITTED" else "INFO"))
    horizon = horizon_dates(calendars)
    cs_rows = raw_rows(datasets.get("cs_windows", ()))
    needed = {(job.line, job.station) for jid, job in census.jobs.items() if jid not in census.verified_complete}
    proxy_station = str(policy.optional("due.proxy_station", ""))
    needed |= {(line, proxy_station) for line in lines if proxy_station}
    cs_facts = build_cs_facts(rows=cs_rows, run_id=run_id, needed=needed, calendars=calendars, policy=policy, tz=_zone(tzp, "cs_windows"),
                              dataset_id=dataset_ids.get("cs_windows", ""))

    # ---- requirements, holdings, durations ----
    stats = compute_statistics(datasets.get("duration_episodes", ()), run_id=run_id, policy_id=policy.id,
                               minimum_sample=policy.int("duration.minimum_fixture_sample"), as_of=cutoff,
                               stale_after_days=policy.int("duration.stale_after_days"),
                               query_sha=(dataset_queries or {}).get("duration_episodes", ""),
                               confidence_level=Fraction(policy.str("duration.confidence_level")))
    stats_by_group = {s.group: s for s in stats}
    resolutions: dict[str, DurationResolution] = {}
    for jid, job in census.jobs.items():
        if jid in census.verified_complete:
            continue
        resolutions[jid] = resolve(job, status_class=census.status_class.get(jid, "UNKNOWN"), policy=policy, mode=mode,
                                   stats=stats_by_group, snapshot_key=run_id, flow_problem=flows.conflicts.get((job.line, job.key.native_id)))
    crew_sizes = {jid: (r.estimate.crew_size if r.estimate else None) for jid, r in resolutions.items()}
    requirements = build_requirements(job_index, datasets.get("soi_cert_requirements", ()), datasets.get("requirement_alternatives", ()),
                                      datasets.get("requirement_census", ()), crew_sizes, {}, dataset_ids.get("soi_cert_requirements", ""), run_id)

    # ---- workforce, virtual support ----
    wf = build_workforce(datasets=datasets, mode=mode, policy=policy, shift_policy=shift_policy, tzp=tzp, site=scope.site, run_id=run_id,
                         cutoff=cutoff, horizon=horizon, dataset_ids=dataset_ids)
    for code, scope_name, detail, effect in wf.gaps:
        gaps.append(ReadinessGap(code, scope_name, detail, effect))
    holdings_build = build_holdings(datasets.get("mechanic_certs", ()), datasets.get("holding_events", ()), wf.person_by_native, policy,
                                    _zone(tzp, "mechanic_certs"), dataset_ids.get("mechanic_certs", ""), run_id)
    notes.extend(holdings_build.problems)
    matrix_rows = datasets.get("cert_eligibility_matrix", ())
    if matrix_rows:
        authority = Eligibility(holdings_build.holdings, requirements, source_run_id=run_id, policy_hash=policy.hash)
        for code, scope_name, detail, effect in matrix_label_notes(matrix_rows, authority, wf.person_by_native, job_index, minute_floor(cutoff)):
            gaps.append(ReadinessGap(code, scope_name, detail, effect))
    virtual = build_virtual_support(policy=policy, job_teams={jid: j.team for jid, j in census.jobs.items()}, requirements=requirements,
                                    horizon=horizon, shift_policy=shift_policy, site=scope.site, run_id=run_id)
    pools = merge_virtual_pools(wf.pools, virtual, policy)
    real_exec_teams = {p.key.team for p in wf.pools if p.n_executable > 0}
    virtual_teams = set(policy.seq("support.virtual_teams"))

    # ---- job-level readiness reasons (before graph propagation) ----
    for jid, job in census.jobs.items():
        if jid in census.verified_complete:
            continue
        res = resolutions.get(jid)
        if res is not None:
            census.blocked[jid].extend(res.blocked)
            census.executable_only[jid].extend(res.executable_only)
        req = requirements.get(jid)
        if req is None or req.status is RequirementStatus.UNKNOWN:
            census.blocked[jid].append("REQUIREMENTS_UNKNOWN: no positive requirement rows and no affirmative census (never cert-free)")
        if job.team is None:
            census.blocked[jid].append("TEAM_UNKNOWN: job has no source team; no pool can be selected")
        elif job.team in virtual_teams and job.team not in real_exec_teams:
            census.executable_only[jid].append(f"SUPPORT_RESOURCE_UNAVAILABLE: no real verified {job.team} resource source; virtual support is forecast-only")
            census.conditional[jid].append(f"virtual {job.team} support people (synthetic)")
        cal = calendars.get(job.line)
        if cal is None or cal.status != "ADMITTED":
            census.blocked[jid].append(f"{(cal.reasons[0] if cal and cal.reasons else 'CALENDAR_MISSING')}")
        facts = cs_facts.get((job.line, job.station))
        if facts is None or facts.status != "ADMITTED":
            reason = facts.reasons[0] if facts and facts.reasons else "CS_WINDOW_GAP: no CS facts"
            if policy.bool("cs.required_for_scheduling"):
                census.blocked[jid].append(reason)
        if mode == "live" and policy.authority_of("cs.date_mode") == "open_decision":
            census.executable_only[jid].append("CS_DATE_SEMANTICS_UNCONFIRMED: CS offsets/date basis not admitted (OD-05)")

    # ---- parts and graph ----
    labels = {jid: job.key.label() for jid, job in census.jobs.items()}
    parts = build_parts(rows=datasets.get("etracs_active", ()), events=datasets.get("part_events", ()), job_index=job_index,
                        parent_of=census.parent_of, labels=labels, tz=_zone(tzp, "etracs_active"), dataset_ids=dataset_ids, run_id=run_id,
                        cutoff=cutoff, date_floor=policy.str("parts.date_only_floor"), timestamp_floor=policy.str("parts.timestamp_floor"))
    other_minutes = {str(r["INSPECTION_TYPE"]): int(r["DURATION_MINUTES"]) for r in datasets.get("inspection_standards", ())
                     if str(r.get("SCHEDULING_RELATIONSHIP")) == "PARALLEL_BRANCH"}
    graph = build_graph(census=census, durations=resolutions, requirements=requirements, policy=policy, mode=mode,
                        pnet_rows=datasets.get("pnet_dag", ()), neo_rows=datasets.get("neo_graph_rework", ()),
                        classification_rows=datasets.get("rework_classification", ()), parts=parts, other_minutes=other_minutes,
                        dataset_ids=dataset_ids, run_id=run_id, cutoff=cutoff)
    notes.extend(graph.problems)
    if graph.template_absent or graph.template_pred_absent:
        notes.append(f"template edges not instantiated: {graph.template_absent} with the successor and {graph.template_pred_absent} with the "
                     f"predecessor SOI absent from that line's full census (a customer option, never treated as done); {graph.template_bridged} "
                     "edges bridge the missing tasks to their nearest carried ancestors")
    for code, scope_name, detail, effect in graph.diagnostics:
        gaps.append(ReadinessGap(code, scope_name, detail, effect))

    # ---- economics ----
    cs_by_line = {line: cs_facts.get((line, proxy_station)) for line in lines}
    economics = build_economics(datasets=datasets, lines=lines, cs_facts_by_line=cs_by_line, policy=policy, tzp=tzp, tz=tz, cutoff=cutoff)

    # ---- dispositions and census ----
    dispositions: list[JobDisposition] = []
    per_line: dict[int, Counter] = defaultdict(Counter)
    for jid, job in census.jobs.items():
        if jid in census.verified_complete:
            disp, reasons = Disposition.VERIFIED_COMPLETE, ("verified complete anchor",)
        elif census.excluded.get(jid):
            disp, reasons = Disposition.EXCLUDED, tuple(dict.fromkeys(census.excluded[jid]))
        elif census.quarantined.get(jid):
            disp, reasons = Disposition.QUARANTINED, tuple(dict.fromkeys(census.quarantined[jid]))
        elif census.blocked.get(jid):
            disp, reasons = Disposition.BLOCKED, tuple(dict.fromkeys(census.blocked[jid]))
        else:
            disp, reasons = Disposition.ADMITTED, tuple(dict.fromkeys(census.conditional.get(jid, [])))
        exec_reasons = tuple(dict.fromkeys(census.executable_only.get(jid, [])))
        dispositions.append(JobDisposition(jid, disp, reasons, exec_reasons))
        per_line[job.line][disp.value] += 1
    census_records = []
    for line in lines:
        counts = per_line[line]
        meta = census.line_meta.get(line, {})
        line_reasons = []
        cal = calendars.get(line)
        if cal is not None and cal.status != "ADMITTED":
            line_reasons.append(cal.reasons[0])
        if line in parts.line_unresolved:
            line_reasons.extend(parts.line_unresolved[line])
        census_records.append(AircraftCensus(
            line=line, site=scope.site, program=scope.program, model=meta.get("model"), customer=meta.get("customer"),
            status_counts=dict(sorted(census.status_counts[line].items())),
            jobs_total=sum(counts.values()), admitted=counts.get("admitted", 0), blocked=counts.get("blocked", 0),
            quarantined=counts.get("quarantined", 0), excluded=counts.get("excluded", 0), verified_complete=counts.get("verified_complete", 0),
            scope_basis=scope.basis, reasons=tuple(line_reasons),
        ))
    observed = len(master)
    reconciled = sum(c.jobs_total for c in census_records) + sum(n - 1 for n in Counter((int(r["LINE_NUMBER"]), str(r["SHOP_ORDER_IDENTIFIER"])) for r in master).values() if n > 1)
    if reconciled != observed:
        raise AdmissionError("CENSUS_NOT_RECONCILED", f"observed {observed} source rows but reconciled {reconciled}")
    if mode == "live":
        for item in policy.open_decisions:
            gaps.append(ReadinessGap(str(item["id"]), "policy", str(item["topic"]), "BLOCKS_EXECUTABLE"))
    shift_ids: tuple[str, ...] = ()
    if horizon is not None:
        shift_ids = tuple(s.id for s in shifts_between(horizon[0], horizon[1], shift_policy, scope.site))
    snapshot_id = digest_id("SNAP", run_id, policy.hash, content_hash([s.id for s in stats]), length=24)
    snapshot = PlanningSnapshot(
        id=snapshot_id,
        source_run_id=run_id,
        scope_key=scope_key,
        site=scope.site,
        program=scope.program,
        cutoff=cutoff,
        created_at=created_at,
        policy_id=policy.id,
        policy_hash=policy.hash,
        manifest_hash=manifest_hash,
        census=tuple(census_records),
        jobs=tuple(census.jobs.values()),
        dispositions=tuple(dispositions),
        phases=tuple(graph.phases),
        edges=tuple(graph.edges),
        releases=tuple(parts.releases),
        durations=tuple(r.estimate for r in resolutions.values() if r.estimate is not None),
        requirements=tuple(requirements[j] for j in census.jobs if j in requirements),
        people=tuple(wf.people + virtual.people),
        holdings=tuple(holdings_build.holdings + tuple(virtual.holdings)),
        pools=tuple(pools),
        availability=tuple(wf.availability + virtual.availability),
        calendars=tuple(calendars[line] for line in lines),
        raw_cs_rows=tuple(cs_rows),
        cs_facts=tuple(cs_facts[k] for k in sorted(cs_facts, key=lambda k: (k[0], k[1] or ""))),
        shortages=tuple(parts.shortages),
        rework_links=tuple(graph.rework_links),
        economics=tuple(economics[line] for line in lines),
        carry_in=tuple(wf.carry_in),
        inference=tuple(wf.inference),
        shift_ids=shift_ids,
        gaps=tuple(gaps),
        statistics_ids=tuple(s.id for s in stats),
        dataset_lineage=dict(sorted(dataset_lineage.items())),
        incumbent_id=incumbent_id,
        hash="",
    )
    return SnapshotBuild(seal_snapshot(snapshot), stats, notes)
