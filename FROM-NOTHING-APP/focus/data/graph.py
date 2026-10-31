"""Typed precedence, rework, parts and QA graph (S10, P09, P10).

Nodes are exact phase keys (WORK segments, IP_QA, FINAL_QA, OTHER_BUYOFF, RELEASE)
and zero-work release milestones (``R:PART:*``). Job endpoints are:

* start  = the first WORK phase; for a verified-complete anchor, its COMPLETE
  RELEASE node;
* finish = the job's RELEASE (AND) milestone.

Relationships (signed lag is preserved, never clamped to 0):

* FS  pred.finish + lag <= succ.start
* SS  pred.start  + lag <= succ.start
* FF  pred.finish + lag <= succ.finish
* SF  pred.start  + lag <= succ.finish

IS_HARD=Y enforces the edge. N is advisory unless policy designates it mandatory.
Unknown hardness or type quarantines the successor. CONCURRENT/NON-CONCURRENT
use only explicit policy modes. Template edges are instantiated only on lines
where both endpoints exist with matching model effectivity; there is never a
cross join. Cycles are detected (Tarjan SCC) and quarantined with exact
evidence; no edge is deleted. An in-progress successor keeps every incoming
incomplete predecessor edge. Rework children inject child-release -> root-start
(or a continuation hold for a started root). Part releases gate the used-on job
and its root.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from ..contracts.planning import Edge, JobRequirement, Phase, ReworkLink, RoleDemand
from ..domain import EdgeType, EvidenceRef, PhaseKey, PhaseKind, ReleaseStatus, RequirementBundle, RequirementStatus, digest_id
from ..policy import Policy
from .durations import DurationResolution
from .inspections import PhaseSpec, phase_plan
from .parts import PartsBuild, root_chain
from .scope import CensusBuild

KIND = {"WORK": PhaseKind.WORK, "IP_QA": PhaseKind.IP_QA, "FINAL_QA": PhaseKind.FINAL_QA, "OTHER_BUYOFF": PhaseKind.OTHER_BUYOFF}
BRIDGE_SIDES = {"FS": ("F", "S"), "SS": ("S", "S"), "FF": ("F", "F"), "SF": ("S", "F")}      # (predecessor side, successor side)
BRIDGE_TYPES = {sides: etype for etype, sides in BRIDGE_SIDES.items()}


@dataclass
class _PhaseDraft:
    key: PhaseKey
    job: str
    line: int
    kind: PhaseKind
    ordinal: int
    work: int
    effort: int
    parent_elapsed: int
    demands: tuple[RoleDemand, ...]
    team: str | None
    status: str
    actual_start: datetime | None
    actual_end: datetime | None
    label: str


@dataclass
class GraphBuild:
    phases: list[Phase] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    rework_links: list[ReworkLink] = field(default_factory=list)
    start_phase: dict[str, str] = field(default_factory=dict)
    finish_phase: dict[str, str] = field(default_factory=dict)
    job_preds: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    cycles: list[list[str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    diagnostics: list[tuple[str, str, str, str]] = field(default_factory=list)   # (code, scope, detail, effect)
    template_absent: int = 0
    template_pred_absent: int = 0
    template_bridged: int = 0             # edges bridged through template tasks an aircraft does not carry


def _tarjan(nodes: Iterable[str], adj: Mapping[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        # iterative Tarjan to avoid recursion limits on large graphs
        work = [(v, iter(sorted(adj.get(v, ()))))]
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        while work:
            node, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(adj.get(w, ())))))
                    advanced = True
                    break
                if w in on_stack:
                    low[node] = min(low[node], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1 or node in adj.get(node, ()):
                    out.append(sorted(comp))

    for n in sorted(nodes):
        if n not in index:
            strong(n)
    return out


def build_graph(*, census: CensusBuild, durations: Mapping[str, DurationResolution], requirements: Mapping[str, JobRequirement],
                policy: Policy, mode: str, pnet_rows: Iterable[Mapping[str, Any]], neo_rows: Iterable[Mapping[str, Any]],
                classification_rows: Iterable[Mapping[str, Any]], parts: PartsBuild, other_minutes: Mapping[str, int],
                dataset_ids: Mapping[str, str], run_id: str, cutoff: datetime) -> GraphBuild:
    out = GraphBuild()
    drafts: dict[str, _PhaseDraft] = {}
    chain_edges: list[Edge] = []
    inspector_bundle = RequirementBundle(tuple(tuple(g) for g in policy.seq("qa.inspector_cert_groups")), "inspector", 1, "QA policy inspector role")
    inspector_team = policy.str("qa.inspector_team")

    # ------------------------------------------------------------------ phases
    for job_id, job in census.jobs.items():
        label = job.key.label()
        if job_id in census.verified_complete:
            key = PhaseKey(job.key, PhaseKind.RELEASE, 0)
            drafts[key.id] = _PhaseDraft(key, job_id, job.line, PhaseKind.RELEASE, 0, 0, 0, 0, (), job.team, "COMPLETE",
                                         job.actual_start, job.actual_complete or cutoff, f"{label} RELEASE (verified complete)")
            out.start_phase[job_id] = out.finish_phase[job_id] = key.id
            continue
        res = durations.get(job_id)
        est = res.estimate if res else None
        req = requirements.get(job_id)
        # blocked jobs keep their sourced TOTAL_MECHANICS when valid; otherwise 0 = demand unavailable (never 1)
        crew = est.crew_size if est else (job.raw_budget.total_mechanics if job.raw_budget and (job.raw_budget.total_mechanics or 0) > 0 else 0)
        groups = req.bundle.groups if (req and req.bundle) else ()
        bundle = RequirementBundle(groups, "mechanic", crew, "exact SOI/line requirement bundle; every member holds every AND group")
        work_demand = RoleDemand(bundle, job.team or "UNASSIGNED", req.status if req else RequirementStatus.UNKNOWN,
                                 "FOCUS_FLOW_BUDGET.TOTAL_MECHANICS simultaneous crew")
        qa_demand = RoleDemand(inspector_bundle, inspector_team, RequirementStatus.REQUIRED, "qualified inspector (QA team pool)")
        performed = job.performed_elapsed or 0 if census.status_class.get(job_id) == "IN_PROGRESS" else 0
        specs: list[PhaseSpec]
        if est is not None:
            specs, problems = phase_plan(work_minutes=est.elapsed_minutes, performed=performed, trigger=job.inspection_trigger,
                                         policy=policy, other_minutes=other_minutes)
            for p in problems:
                if p.startswith("IP_HOLD_STATUS_UNKNOWN"):
                    census.executable_only[job_id].append(p)
                else:
                    census.blocked[job_id].append(p)
        else:
            specs = [PhaseSpec("WORK", 0, "work (duration unavailable)")]
        trig = job.inspection_trigger
        if trig and trig.ip_required and trig.ip_hold_status != "SOURCED":
            census.blocked[job_id].append(f"IP_HOLD_{trig.ip_hold_status}: in-process QA required without a sourced hold location/allocation")
        total_work = sum(s.minutes for s in specs if s.kind == "WORK")
        effort_basis = policy.str("duration.standard_effort_basis")
        work_specs = [s for s in specs if s.kind == "WORK"]
        efforts: list[int] = []
        if est is not None and work_specs:
            if effort_basis == "ELAPSED_TIMES_CREW":
                efforts = [s.minutes * crew for s in work_specs]
            else:
                total_effort = est.standard_person_minutes
                raw = [total_effort * s.minutes / total_work for s in work_specs]
                floors = [int(x) for x in raw]
                rem = total_effort - sum(floors)
                order = sorted(range(len(raw)), key=lambda i: (raw[i] - floors[i]), reverse=True)
                for i in order[:rem]:
                    floors[i] += 1
                efforts = floors
        counters: dict[PhaseKind, int] = defaultdict(int)
        main: list[str] = []
        parallel: list[str] = []
        widx = 0
        for spec in specs:
            kind = KIND[spec.kind]
            ordinal = counters[kind]
            counters[kind] += 1
            key = PhaseKey(job.key, kind, ordinal)
            if kind is PhaseKind.WORK:
                effort = efforts[widx] if efforts else 0
                widx += 1
                demands = (work_demand,)
                team = job.team
            else:
                effort = spec.minutes
                demands = (qa_demand,)
                team = inspector_team
            drafts[key.id] = _PhaseDraft(key, job_id, job.line, kind, ordinal, spec.minutes, effort, total_work, demands, team, "OPEN",
                                         None, None, f"{label} {spec.label}")
            (parallel if spec.parallel else main).append(key.id)
        rel_key = PhaseKey(job.key, PhaseKind.RELEASE, 0)
        drafts[rel_key.id] = _PhaseDraft(rel_key, job_id, job.line, PhaseKind.RELEASE, 0, 0, 0, total_work, (), job.team, "OPEN", None, None,
                                         f"{label} RELEASE (AND)")
        out.start_phase[job_id] = main[0]
        out.finish_phase[job_id] = rel_key.id
        for a, b in zip(main, main[1:]):
            chain_edges.append(_edge(a, b, EdgeType.FS, 0, True, "PHASE_CHAIN"))
        last_work = [pid for pid in main if drafts[pid].kind is PhaseKind.WORK][-1]
        for pid in parallel:
            chain_edges.append(_edge(last_work, pid, EdgeType.FS, 0, True, "QA_PARALLEL_BRANCH"))
            chain_edges.append(_edge(pid, rel_key.id, EdgeType.FS, 0, True, "AND_RELEASE"))
        chain_edges.append(_edge(main[-1], rel_key.id, EdgeType.FS, 0, True, "AND_RELEASE"))

    # --------------------------------------------------------- template edges
    rel_map: Mapping[str, str] = policy.map("edges.relationship_map")
    hard_map: Mapping[str, str] = policy.map("edges.hard_flag_map")
    advisory_mandatory = policy.bool("edges.advisory_mandatory")
    by_soi: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (line, native), job_id in census.job_index.items():
        by_soi[native].append((line, job_id))
    job_edges: list[Edge] = []
    job_adj: dict[str, set[str]] = defaultdict(set)
    job_edge_types: dict[tuple[str, str], set[EdgeType]] = defaultdict(set)
    seen_template: set[tuple[str, str, str, int]] = set()
    lines_of: dict[str, set[int]] = {}                  # SOI -> lines carrying it (one set per SOI, not per template row)
    carriers: dict[str, list[tuple[int, str, Any]]] = {}  # SOI -> (line, job id, census job) in census order
    job_index, jobs = census.job_index, census.jobs

    def carried(soi: str) -> set[int]:
        found = lines_of.get(soi)
        if found is None:
            found = lines_of[soi] = {line for line, _ in by_soi.get(soi, ())}
        return found

    # Each minor model's network spans every customer's aircraft, and customer options mean an aircraft may not carry a
    # task: A>B>C on one customer's line is A>C on another's without B. A missing task is bridged, never taken as done and
    # never dropped: its nearest carried ancestors connect to its successors. A missing task takes no time, so the
    # composed relation joins the ancestor's side of the first edge to the successor's side of the last, with the lags
    # summed; a bridge is hard only when every edge on its path is.
    template_in: dict[str, list[tuple[int, str, str | None, str | None, int, str | None, str | None]]] = defaultdict(list)
    for ordinal, row in enumerate(pnet_rows):
        raw_type, raw_hard = row.get("RELATIONSHIP_TYPE"), row.get("IS_HARD")
        pm, sm = row.get("PRED_MINOR_MODEL"), row.get("SUC_MINOR_MODEL")
        template_in[str(row["SUCCESSOR_SOI"])].append((
            ordinal, str(row["PREDECESSOR_SOI"]), rel_map.get(str(raw_type)) if raw_type is not None else None,
            hard_map.get(str(raw_hard)) if raw_hard is not None else None,
            int(row["OFFSET_MINUTES"]) if row.get("OFFSET_MINUTES") is not None else 0, None if pm is None else str(pm), None if sm is None else str(sm)))
    frontiers: dict[tuple[int, str], list[tuple[str, str, int, bool, tuple[int, ...]]]] = {}

    def frontier(line: int, model: str | None, soi: str) -> list[tuple[str, str, int, bool, tuple[int, ...]]]:
        """The carried ancestors reached through tasks ``line`` does not carry, up to ``soi`` (itself not carried): each with
        the ancestor's side (F/S), the summed lag to soi, whether every edge is hard, and the template rows on the path."""
        key = (line, soi)
        if key in frontiers:
            return frontiers[key]
        frontiers[key] = []                                    # a cycle through missing tasks contributes nothing
        best: dict[tuple[str, str, bool], tuple[int, tuple[int, ...]]] = {}
        for ordinal, pred, x_type, x_hardness, x_lag, xpm, xsm in template_in.get(soi, ()):
            if model is not None and ((xpm is not None and xpm != model) or (xsm is not None and xsm != model)):
                continue                                       # the edge's effectivity excludes this aircraft
            if x_type not in BRIDGE_SIDES or x_hardness is None:
                out.problems.append(f"line {line}: template edge {pred}->{soi} ({x_type}, IS_HARD {x_hardness}) cannot be bridged; not instantiated")
                continue
            x_hard = x_hardness == "HARD" or (x_hardness == "ADVISORY" and advisory_mandatory)
            reached = ([(pred, BRIDGE_SIDES[x_type][0], 0, True, ())] if line in carried(pred)
                       else frontier(line, model, pred))
            for anc, side, lag, hard, path in reached:
                k = (anc, side, hard and x_hard)
                if k not in best or lag + x_lag > best[k][0]:      # the binding path: the largest summed lag
                    best[k] = (lag + x_lag, path + (ordinal,))
        frontiers[key] = [(anc, side, lag, hard, path) for (anc, side, hard), (lag, path) in sorted(best.items())]
        return frontiers[key]

    for ordinal, row in enumerate(pnet_rows):
        pred_soi, succ_soi = str(row["PREDECESSOR_SOI"]), str(row["SUCCESSOR_SOI"])
        pred_lines = lines_of.get(pred_soi)
        if pred_lines is None:
            pred_lines = lines_of[pred_soi] = {line for line, _ in by_soi.get(pred_soi, ())}
        raw_type, raw_hard = row.get("RELATIONSHIP_TYPE"), row.get("IS_HARD")
        lag = int(row["OFFSET_MINUTES"]) if row.get("OFFSET_MINUTES") is not None else 0
        etype = rel_map.get(str(raw_type)) if raw_type is not None else None
        hardness = hard_map.get(str(raw_hard)) if raw_hard is not None else None
        evidence = (EvidenceRef(dataset_ids.get("pnet_dag", ""), f"pnet_dag#{ordinal}", source_run_id=run_id),)
        pm, sm = row.get("PRED_MINOR_MODEL"), row.get("SUC_MINOR_MODEL")
        pm, sm = (None if pm is None else str(pm)), (None if sm is None else str(sm))
        # the successor is on a line that does not carry the predecessor (a customer option): bridge to its carried ancestors
        for line, succ_job in by_soi.get(succ_soi, ()):
            if line in pred_lines:
                continue
            out.template_pred_absent += 1
            sjob = jobs.get(succ_job)
            model = sjob.model if sjob is not None else None
            if sjob is None or (model is not None and ((pm is not None and pm != model) or (sm is not None and sm != model))):
                continue                                       # the template's effectivity excludes this aircraft
            if etype not in BRIDGE_SIDES or hardness is None:
                out.problems.append(f"line {line}: template edge {pred_soi}->{succ_soi} ({raw_type}, IS_HARD {raw_hard!r}) cannot be bridged")
                continue
            y_hard = hardness == "HARD" or (hardness == "ADVISORY" and advisory_mandatory)
            for anc, side, anc_lag, anc_hard, path in frontier(line, model, pred_soi):
                anc_job = job_index.get((line, anc))
                if anc_job is None or anc_job == succ_job:
                    continue
                et = EdgeType(BRIDGE_TYPES[(side, BRIDGE_SIDES[etype][1])])
                b_lag, b_hard = anc_lag + lag, anc_hard and y_hard
                dedupe = (anc_job, succ_job, et.value, b_lag)
                if dedupe in seen_template:
                    continue
                seen_template.add(dedupe)
                a = out.finish_phase[anc_job] if side == "F" else out.start_phase[anc_job]
                b = out.start_phase[succ_job] if BRIDGE_SIDES[etype][1] == "S" else out.finish_phase[succ_job]
                rows = path + (ordinal,)
                job_edges.append(Edge(digest_id("E", a, b, et.value, b_lag, "BRIDGED_PNET"), a, b, et, b_lag, b_hard, "BRIDGED_PNET",
                                      f"bridged through {len(rows) - 1} task(s) line {line} does not carry", "HARD" if b_hard else "ADVISORY",
                                      None, None, tuple(EvidenceRef(dataset_ids.get("pnet_dag", ""), f"pnet_dag#{o}", source_run_id=run_id)
                                                        for o in rows)))
                out.template_bridged += 1
                if b_hard:
                    job_adj[anc_job].add(succ_job)
                    out.job_preds[succ_job].add(anc_job)
                    job_edge_types[(anc_job, succ_job)].add(et)
        preds = carriers.get(pred_soi)
        if preds is None:
            preds = carriers[pred_soi] = [(line, job_id, jobs.get(job_id)) for line, job_id in by_soi.get(pred_soi, ())]
        for line, pred_job, pjob in preds:
            succ_job = job_index.get((line, succ_soi))
            if succ_job is None:
                out.template_absent += 1
                continue
            sjob = jobs.get(succ_job)
            if pjob is None or sjob is None:
                continue
            if (pm is not None and pjob.model is not None and pm != pjob.model) or (sm is not None and sjob.model is not None and sm != sjob.model):
                continue  # template effectivity does not match this aircraft
            if pred_job == succ_job:
                census.quarantined[succ_job].append(f"CYCLE_SELF_EDGE: template edge {pred_soi}->{succ_soi} on line {line}")
                continue
            if hardness is None:
                census.quarantined[succ_job].append(f"EDGE_HARDNESS_UNKNOWN: IS_HARD={raw_hard!r} for {pred_soi}->{succ_soi}")
                continue
            hard = hardness == "HARD" or (hardness == "ADVISORY" and advisory_mandatory)
            if etype is None:
                if hard:
                    census.quarantined[succ_job].append(f"EDGE_TYPE_UNSUPPORTED: {raw_type!r} for {pred_soi}->{succ_soi}")
                else:
                    out.problems.append(f"advisory edge {pred_soi}->{succ_soi} line {line} has unsupported type {raw_type!r} (recorded, not enforced)")
                continue
            dedupe = (pred_job, succ_job, etype, lag)
            if dedupe in seen_template:
                continue
            seen_template.add(dedupe)
            et = EdgeType(etype)
            conc_mode = None
            if et in (EdgeType.CONCURRENT, EdgeType.NONCONCURRENT):
                key = "edges.concurrent_mode" if et is EdgeType.CONCURRENT else "edges.nonconcurrent_mode"
                conc_mode = policy.str(key)
                if hard and mode == "live" and policy.authority_of(key) == "open_decision":
                    for j in (pred_job, succ_job):
                        census.quarantined[j].append(f"CONCURRENCY_SEMANTICS_UNRESOLVED: hard {raw_type} meaning not admitted (OD-07)")
                    continue
                a, b = out.start_phase[pred_job], out.start_phase[succ_job]
            elif et is EdgeType.FS:
                a, b = out.finish_phase[pred_job], out.start_phase[succ_job]
            elif et is EdgeType.SS:
                a, b = out.start_phase[pred_job], out.start_phase[succ_job]
            elif et is EdgeType.FF:
                a, b = out.finish_phase[pred_job], out.finish_phase[succ_job]
            else:
                a, b = out.start_phase[pred_job], out.finish_phase[succ_job]
            edge = Edge(digest_id("E", a, b, et.value, lag, "SOURCE_PNET"), a, b, et, lag, hard, "SOURCE_PNET", str(raw_type), str(raw_hard),
                        conc_mode, None, evidence)
            job_edges.append(edge)
            if hard and et not in (EdgeType.CONCURRENT, EdgeType.NONCONCURRENT):
                job_adj[pred_job].add(succ_job)
                out.job_preds[succ_job].add(pred_job)
                job_edge_types[(pred_job, succ_job)].add(et)

    # -------------------------------------------------------------- rework
    started_root_hold = policy.str("rework.started_root_hold")
    if started_root_hold not in ("CONTINUATION", "CLOSURE"):
        raise ValueError(f"unsupported rework.started_root_hold {started_root_hold!r}")
    status_classes: Mapping[str, str] = policy.map("scope.status_classes")
    classification = {(int(r["LINE_NUMBER"]), str(r["CHILD_SOI"])): r for r in classification_rows}
    neo = {(int(r["LINE_NUMBER"]), str(r["CHILD_SOI"])): r for r in neo_rows}
    for (line, child_soi) in sorted(set(classification) | set(neo)):
        child = census.job_index.get((line, child_soi))
        cls_row, neo_row = classification.get((line, child_soi)), neo.get((line, child_soi))
        if child is None:
            out.problems.append(f"rework child {child_soi} on line {line} is not in the census")
            continue
        root_soi = str(cls_row["ROOT_SOI"]) if cls_row and cls_row.get("ROOT_SOI") else (str(neo_row["PARENT_SOI"]) if neo_row else None)
        parent_soi = str(neo_row["PARENT_SOI"]) if neo_row else None
        root = census.job_index.get((line, root_soi)) if root_soi else None
        parent = census.job_index.get((line, parent_soi)) if parent_soi else None
        reasons: list[str] = []
        status = "LINKED"
        if cls_row and neo_row and root_soi != parent_soi:
            chain, _ = root_chain(parent, census.parent_of) if parent else ([], False)
            if root not in chain:
                status = "QUARANTINED"
                reasons.append(f"REWORK_LINK_CONFLICT: classification root {root_soi} vs rework parent {parent_soi}")
        if root is None:
            status = "UNRESOLVED"
            reasons.append(f"REWORK_ROOT_UNRESOLVED: root {root_soi!r} not found on line {line}")
        else:
            chain, cyclic = root_chain(root, census.parent_of)
            if child in chain or cyclic:
                status = "QUARANTINED"
                reasons.append("REWORK_CYCLE: child is an ancestor of its root (reversed/cyclic linkage)")
        if status in ("QUARANTINED", "UNRESOLVED"):
            for j in filter(None, (child, root)):
                census.quarantined[j].append(reasons[-1])
        else:
            # ordered verified lifecycle facts decide the child release: NEO and the census must agree
            neo_status = str(neo_row["CHILD_STATUS"]) if neo_row and neo_row.get("CHILD_STATUS") is not None else None
            if neo_status is not None and root is not None:
                neo_complete = status_classes.get(neo_status) == "TERMINAL_COMPLETE"
                if neo_complete != (child in census.verified_complete):
                    reasons.append(f"REWORK_STATUS_CONFLICT: NEO child status {neo_status} disagrees with census "
                                   f"{census.status_class.get(child)}; release requires reconciled lifecycle evidence")
                    census.executable_only[root].append(reasons[-1])
        if status not in ("QUARANTINED", "UNRESOLVED") and child not in census.verified_complete and root is not None:
            evidence = (EvidenceRef(dataset_ids.get("rework_classification", ""), f"{line}/{child_soi}", source_run_id=run_id),)
            if root in census.verified_complete:
                # the root closed while rework is open (reopened/voided completion): its closure must be re-evidenced,
                # so every hard successor of the root waits for the rework release
                reasons.append("ROOT_CLOSURE_REOPENED: root closure must be re-evidenced; its successors wait for the rework release")
                out.diagnostics.append(("ROOT_CLOSURE_REOPENED", census.label(root), f"open rework {census.label(child)}", "INFO"))
                for succ in sorted(job_adj.get(root, ())):
                    a, b = out.finish_phase[child], out.start_phase[succ]
                    job_edges.append(Edge(digest_id("E", a, b, "FS", 0, "REWORK_REOPENED_ROOT"), a, b, EdgeType.FS, 0, True,
                                          "REWORK_REOPENED_ROOT", None, None, None, None, evidence))
                    job_adj[child].add(succ)
                    out.job_preds[succ].add(child)
            else:
                started = census.status_class.get(root) == "IN_PROGRESS"
                if started and started_root_hold == "CLOSURE":
                    basis, a, b = "REWORK_CLOSURE_HOLD", out.finish_phase[child], out.finish_phase[root]
                elif started:
                    basis, a, b = "REWORK_CONTINUATION_HOLD", out.finish_phase[child], out.start_phase[root]
                else:
                    basis, a, b = "REWORK_INJECTED", out.finish_phase[child], out.start_phase[root]
                job_edges.append(Edge(digest_id("E", a, b, "FS", 0, basis), a, b, EdgeType.FS, 0, True, basis, None, None, None, None, evidence))
                job_adj[child].add(root)
                out.job_preds[root].add(child)
                job_edge_types[(child, root)].add(EdgeType.FS if basis != "REWORK_CLOSURE_HOLD" else EdgeType.FF)
        out.rework_links.append(ReworkLink(line, child, root, parent, census.jobs[child].rework_class,
                                           str(neo_row["CHILD_STATUS"]) if neo_row and neo_row.get("CHILD_STATUS") else None,
                                           status, census.jobs[child].defect_origin_team, tuple(reasons)))

    # --------------------------------------------- in-progress successors keep incomplete predecessors
    for job_id in sorted(out.job_preds):
        if census.status_class.get(job_id) != "IN_PROGRESS":
            continue
        job = census.jobs[job_id]
        for pred in sorted(out.job_preds[job_id]):
            if pred in census.verified_complete:
                continue
            types = job_edge_types.get((pred, job_id), set())
            started_at = job.actual_start.isoformat() if job.actual_start else "an unrecorded time"
            if types & {EdgeType.FS, EdgeType.SS}:
                out.diagnostics.append(("SEQUENCE_VIOLATION", census.label(job_id),
                                        f"started at {started_at} while hard predecessor {census.label(pred)} was incomplete "
                                        f"({'/'.join(sorted(t.value for t in types))}); recorded, never legalized", "INFO"))
                out.diagnostics.append(("IN_PROGRESS_CONTINUATION_HOLD", census.label(job_id),
                                        f"remaining work waits for {census.label(pred)}; actual history kept; no sourced safety disposition", "INFO"))
            elif types & {EdgeType.FF, EdgeType.SF}:
                out.diagnostics.append(("IN_PROGRESS_CLOSURE_HOLD", census.label(job_id),
                                        f"closure waits for {census.label(pred)}; continuation of unrelated safe work is not prohibited", "INFO"))

    # ---------------------------------------------------------------- parts
    for rel in parts.releases:
        for job_id in rel.scope_keys:
            if job_id in census.verified_complete or job_id not in out.start_phase:
                continue
            b = out.start_phase[job_id]
            job_edges.append(Edge(digest_id("E", rel.id, b, "FS", 0, "PART_RELEASE"), rel.id, b, EdgeType.FS, 0, True, "PART_RELEASE",
                                  None, None, None, None, rel.evidence))
            if rel.status is ReleaseStatus.UNKNOWN:
                census.blocked[job_id].append(f"PART_RELEASE_UNKNOWN: {rel.label} has no ECD and no receipt")
            elif rel.status is not ReleaseStatus.CONFIRMED:
                census.executable_only[job_id].append(f"PART_NOT_RECEIVED: {rel.label} (forecast floor {rel.forecast_at.isoformat() if rel.forecast_at else 'unknown'})")
                census.conditional[job_id].append(f"conditional part ETA {rel.label}")
    for line, reasons in parts.line_unresolved.items():
        for job_id, job in census.jobs.items():
            if job.line == line and job_id not in census.verified_complete:
                census.executable_only[job_id].append("MATERIAL_SCOPE_UNRESOLVED: " + "; ".join(reasons))
                census.conditional[job_id].append("line has a shortage with unresolved used-on scope")

    # ---------------------------------------------------------------- cycles
    for comp in _tarjan(census.jobs.keys(), job_adj):
        out.cycles.append(comp)
        labels = ", ".join(census.label(j) for j in comp)
        for j in comp:
            census.quarantined[j].append(f"CYCLE: hard precedence cycle among {labels} (no edge deleted)")

    # ------------------------------------------------------------ propagation
    order = _topological(census.jobs.keys(), job_adj)
    for job_id in order:
        for pred in sorted(out.job_preds.get(job_id, ())):
            if pred in census.verified_complete:
                continue
            plabel = census.label(pred)
            if census.quarantined.get(pred) or census.excluded.get(pred) or census.blocked.get(pred):
                census.blocked[job_id].append(f"PREDECESSOR_NOT_SCHEDULABLE: {plabel}")
            elif census.executable_only.get(pred):
                census.executable_only[job_id].append(f"PREDECESSOR_NOT_EXECUTABLE: {plabel}")
            if census.conditional.get(pred):
                census.conditional[job_id].append(f"predecessor {plabel} is conditional")

    # ------------------------------------------------------ materialize phases
    for pid, d in drafts.items():
        blocked = tuple(dict.fromkeys(census.quarantined.get(d.job, []) + census.excluded.get(d.job, []) + census.blocked.get(d.job, [])))
        cond = tuple(dict.fromkeys(census.conditional.get(d.job, [])))
        out.phases.append(Phase(d.key, d.job, d.line, d.kind, d.ordinal, d.work, d.effort, d.parent_elapsed, d.demands, d.team, d.status,
                                d.actual_start, d.actual_end, cond, blocked, d.label))
    out.edges = chain_edges + job_edges
    return out


def _edge(a: str, b: str, etype: EdgeType, lag: int, hard: bool, basis: str) -> Edge:
    return Edge(digest_id("E", a, b, etype.value, lag, basis), a, b, etype, lag, hard, basis, None, None, None, None, ())


def _topological(nodes: Iterable[str], adj: Mapping[str, set[str]]) -> list[str]:
    indeg: dict[str, int] = defaultdict(int)
    nodes = list(nodes)
    for n in nodes:
        indeg.setdefault(n, 0)
    for a, succs in adj.items():
        for b in succs:
            indeg[b] += 1
    ready = sorted(n for n in nodes if indeg[n] == 0)
    out: list[str] = []
    import heapq

    heapq.heapify(ready)
    while ready:
        n = heapq.heappop(ready)
        out.append(n)
        for b in sorted(adj.get(n, ())):
            indeg[b] -= 1
            if indeg[b] == 0:
                heapq.heappush(ready, b)
    # cycle members never reach indegree 0; append them for completeness (already quarantined)
    placed = set(out)
    remaining = sorted(n for n in nodes if n not in placed)
    return out + remaining


def precedence_violations(edges: Iterable[Edge], placed: Mapping[str, tuple[int, int]], satisfied: Iterable[str]) -> list[tuple[str, str]]:
    """Check every hard typed edge against a candidate placement (integer UTC minutes).

    ``placed`` maps phase/milestone ids to (start, end); ``satisfied`` holds verified-complete
    anchors. A placed successor whose incomplete predecessor is missing from the candidate is a
    violation: absence never satisfies a prerequisite. Equations keep the signed lag:
    FS pred.end+lag<=succ.start, SS pred.start+lag<=succ.start, FF pred.end+lag<=succ.end,
    SF pred.start+lag<=succ.end. CONCURRENT/NONCONCURRENT modes are checked by their policy owner.
    """
    done = set(satisfied)
    out: list[tuple[str, str]] = []
    for e in edges:
        if not e.hard or e.type in (EdgeType.CONCURRENT, EdgeType.NONCONCURRENT):
            continue
        succ = placed.get(e.succ_phase)
        if succ is None or e.pred_phase in done:
            continue
        pred = placed.get(e.pred_phase)
        if pred is None:
            out.append((e.id, f"PREDECESSOR_MISSING_FROM_CANDIDATE: {e.pred_phase} -> {e.succ_phase} ({e.basis})"))
            continue
        left = (pred[1] if e.type in (EdgeType.FS, EdgeType.FF) else pred[0]) + e.lag_minutes
        right = succ[0] if e.type in (EdgeType.FS, EdgeType.SS) else succ[1]
        if left > right:
            out.append((e.id, f"PRECEDENCE_{e.type.value}: {e.pred_phase} -> {e.succ_phase} needs {left} <= {right} ({e.basis})"))
    return out
