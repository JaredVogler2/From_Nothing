"""Build display parts from an admitted snapshot, and from an accepted plan with the snapshot it was planned on.

Both builders yield ``(name, record)`` pairs one part at a time, in a deterministic order, so the writer encodes and
stores each part and keeps nothing but its reference. Every record is the snapshot's or plan's own record in the same
order, with its evidence references dropped.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import fields, replace
from datetime import date
from typing import Any, Iterable, Iterator

from ..calendar import parse_shift_id
from ..domain import Disposition, PhaseKind, ReleaseKind, ReleaseStatus
from .records import (BUCKETS, BurndownPoint, CompletionRow, LineCount, LineGraph, PlanCommitments, PlanGoals, PlanHolds, PlanJobs, PlanLine,
                      PlanSummary, PlanTeamDay,
                      PoolIndex, SnapshotHolds, SnapshotJobs, SnapshotLabels, SnapshotLine, SnapshotPools, SnapshotReleases, SnapshotStandards,
                      SnapshotSummary,
                      SnapshotWork, StatusCount, UnitFact, bucket_of, edge_end_label)

HOLD_KINDS = (PhaseKind.IP_QA, PhaseKind.FINAL_QA, PhaseKind.OTHER_BUYOFF)
NO_TEAM = "~"                                       # team-day part of duties no pool of that day lists


def strip(record: Any) -> Any:
    """The record without its evidence references (display parts never carry evidence)."""
    if getattr(record, "evidence", ()) and "evidence" in {f.name for f in fields(record)}:
        return replace(record, evidence=())
    return record


def phase_team(phase: Any) -> str | None:
    return phase.role_demands[0].team if phase.role_demands else phase.team


def shift_day(shift_id: str | None) -> date | None:
    return None if not shift_id else parse_shift_id(shift_id)[1]


def _ordered(records: Iterable[Any], keep: set[int]) -> tuple[Any, ...]:
    return tuple(r for i, r in enumerate(records) if i in keep)


# ---------------------------------------------------------------------- dispatch graph

def line_graphs(snapshot: Any) -> Iterator[LineGraph]:
    """Each aircraft's hard dependency graph by index: its phases and their whole upstream closure, the verified-complete
    phases, and the unconfirmed PART releases with their records (``records.unreceived_ancestors`` walks it)."""
    from ..validation.input import CONCURRENCY_EDGES

    disp = {d.job: d for d in snapshot.dispositions}
    satisfied = set()
    for p in snapshot.phases:
        d = disp.get(p.job)
        if (d is not None and d.disposition is Disposition.VERIFIED_COMPLETE) or p.status == "COMPLETE":
            satisfied.add(p.key.id)
    preds_of: dict[str, list[str]] = defaultdict(list)
    for e in snapshot.edges:
        if e.hard and e.type not in CONCURRENCY_EDGES:
            preds_of[e.succ_phase].append(e.pred_phase)
    phases_of_line: dict[int, list[str]] = defaultdict(list)
    for p in snapshot.phases:
        phases_of_line[p.line].append(p.key.id)
    unreceived = {r.id: r for r in snapshot.releases if r.kind is ReleaseKind.PART and r.status is not ReleaseStatus.CONFIRMED}
    for line in sorted(phases_of_line):
        order: list[str] = []
        index: dict[str, int] = {}
        for n in phases_of_line[line]:
            if n not in index:
                index[n] = len(order)
                order.append(n)
        k = 0
        while k < len(order):                                       # the upstream closure, breadth first
            for pred in preds_of.get(order[k], ()):
                if pred not in index:
                    index[pred] = len(order)
                    order.append(pred)
            k += 1
        yield LineGraph(line=line, nodes=tuple(order), preds=tuple(tuple(index[x] for x in preds_of.get(n, ())) for n in order),
                        satisfied=tuple(i for i, n in enumerate(order) if n in satisfied),
                        unreceived=tuple(i for i, n in enumerate(order) if n in unreceived),
                        releases=tuple(strip(unreceived[n]) for n in order if n in unreceived))


def ready_reach(node: str, trail: frozenset[str], reach: set[str], prefix: dict[str, int], *, releases: dict[str, Any], phases: dict[str, Any],
                done: set[str], inputs: dict[str, list[int]], edges: Any) -> bool:
    """Mark what ``performance.amendments._ready`` can read from ``node`` before an answer no request time can change, and
    return True when it stops there.

    A release that is not confirmed, an unknown node, an open phase that is not an AND release, and a release phase
    without inputs each stop it. A confirmed release may still wait on ``now``, so the walk goes on past it. An open AND
    release goes on through its inputs in order (``inputs``: ``ValidationInput.hard_in`` as edge indexes), and ``prefix``
    keeps how many of each one's inputs are read. ``done`` holds the verified-complete phases."""
    rel = releases.get(node)
    if rel is not None:
        reach.add(node)
        return not (rel.status is ReleaseStatus.CONFIRMED and rel.ready_at is not None)
    phase = phases.get(node)
    if phase is None:
        return True
    reach.add(node)
    if node in done:
        return False
    if phase.kind is PhaseKind.RELEASE and node not in trail:
        mine = inputs.get(node, [])
        for k, i in enumerate(mine):
            prefix[node] = max(prefix.get(node, 0), k + 1)
            if ready_reach(edges[i].pred_phase, trail | {node}, reach, prefix, releases=releases, phases=phases, done=done, inputs=inputs,
                           edges=edges):
                return True
        return not mine
    return True


# ---------------------------------------------------------------------- snapshot

def snapshot_parts(snapshot: Any, buckets: int = BUCKETS) -> Iterator[tuple[str, Any]]:
    """The parts of one admitted snapshot: summary, lines, job buckets, team pools by day, team work by line, holds."""
    snap = snapshot
    jobs = {j.id: j for j in snap.jobs}
    job_index = {j.id: i for i, j in enumerate(snap.jobs)}
    disp = {d.job: d for d in snap.dispositions}
    phase_by_id = {p.key.id: p for p in snap.phases}
    phase_index = {p.key.id: i for i, p in enumerate(snap.phases)}
    phases_of_job: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(snap.phases):
        phases_of_job[p.job].append(i)
    releases = {r.id: r for r in snap.releases}
    release_index = {r.id: i for i, r in enumerate(snap.releases)}
    touching: dict[str, list[int]] = defaultdict(list)            # node -> indexes of hard edges with it at either end
    into: dict[str, list[int]] = defaultdict(list)                # node -> indexes of hard edges ending at it
    for i, e in enumerate(snap.edges):
        if e.hard:
            touching[e.pred_phase].append(i)
            if e.succ_phase != e.pred_phase:
                touching[e.succ_phase].append(i)
            into[e.succ_phase].append(i)
    scoped: dict[str, list[int]] = defaultdict(list)              # job -> releases scoped to it
    for i, r in enumerate(snap.releases):
        for key in r.scope_keys:
            scoped[key].append(i)
    shortages_of_job: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(snap.shortages):
        for j in dict.fromkeys((*s.affected_jobs, s.root_job, s.resolved_job)):
            if j:
                shortages_of_job[j].append(i)
    durations = {d.job: d for d in snap.durations}
    requirements = {r.job: r for r in snap.requirements}

    # summary
    person_teams: dict[str, set[str]] = defaultdict(set)
    person_pool_teams: dict[str, set[str]] = defaultdict(set)
    for pool in snap.pools:
        for m in pool.members:
            person_teams[m].add(pool.key.team)
        for m in (*pool.members, *pool.executable_members, *pool.forecast_members):
            person_pool_teams[m].add(pool.key.team)
    counts = Counter((j.key.line, j.team, disp[j.id].disposition.value if j.id in disp else None) for j in snap.jobs)
    teams = ({p.key.team for p in snap.pools} | {t for t in (phase_team(p) for p in snap.phases) if t} | {j.team for j in snap.jobs if j.team})
    yield "summary", SnapshotSummary(
        snapshot_id=snap.id, snapshot_hash=snap.hash, source_run_id=snap.source_run_id, scope_key=snap.scope_key, site=snap.site,
        program=snap.program, cutoff=snap.cutoff, created_at=snap.created_at, policy_id=snap.policy_id, policy_hash=snap.policy_hash,
        manifest_hash=snap.manifest_hash, census=snap.census, cs_facts=tuple(strip(f) for f in snap.cs_facts), calendars=snap.calendars,
        economics=snap.economics, gaps=snap.gaps, people=tuple(strip(p) for p in snap.people), holdings=tuple(strip(h) for h in snap.holdings),
        inference=snap.inference, carry_in=tuple(strip(c) for c in snap.carry_in), shift_ids=snap.shift_ids,
        pools=tuple(PoolIndex(p.id, p.key.team, p.key.start_date, p.key.shift_number, p.synthetic, p.n_executable, p.cap_executable,
                              p.n_forecast, p.cap_forecast, len(p.members), len(p.executable_members)) for p in snap.pools),
        person_teams={m: tuple(sorted(ts)) for m, ts in sorted(person_teams.items())},
        person_pool_teams={m: tuple(sorted(ts)) for m, ts in sorted(person_pool_teams.items())},
        job_counts=tuple(LineCount(line, team, d, n) for (line, team, d), n in sorted(counts.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), str(kv[0][2])))),
        shortage_counts=tuple(StatusCount(line, status, n) for (line, status), n in sorted(Counter((s.line, s.status) for s in snap.shortages).items())),
        teams=tuple(sorted(teams)))

    # lines: jobs, dispositions and shortages of each aircraft
    lines = sorted({c.line for c in snap.census} | {j.key.line for j in snap.jobs})
    jobs_of_line: dict[int, list[int]] = defaultdict(list)
    for i, j in enumerate(snap.jobs):
        jobs_of_line[j.key.line].append(i)
    shortages_of_line: dict[int, list[int]] = defaultdict(list)
    for i, s in enumerate(snap.shortages):
        shortages_of_line[s.line].append(i)
    for line in lines:
        mine = [snap.jobs[i] for i in jobs_of_line.get(line, ())]
        own = {j.id for j in mine}
        shorts = [snap.shortages[i] for i in shortages_of_line.get(line, ())]
        named = {j for s in shorts for j in (*s.affected_jobs, s.root_job, s.resolved_job) if j and j not in own and j in jobs}
        rels = {s.release_id for s in shorts if s.release_id in releases}
        yield f"line:{line}", SnapshotLine(
            line=line, jobs=tuple(strip(j) for j in mine), dispositions=tuple(disp[j.id] for j in mine if j.id in disp),
            shortages=tuple(strip(s) for s in shorts),
            releases=tuple(strip(releases[r]) for r in sorted(rels, key=release_index.__getitem__)),
            other_jobs=tuple(strip(jobs[j]) for j in sorted(named, key=job_index.__getitem__)))

    # standards and requirements of each aircraft (snapshot order within each)
    line_of_job = {j.id: j.key.line for j in snap.jobs}
    durations_of_line: dict[int, list[Any]] = defaultdict(list)
    for d in snap.durations:
        if d.job in line_of_job:
            durations_of_line[line_of_job[d.job]].append(d)
    requirements_of_line: dict[int, list[Any]] = defaultdict(list)
    for r in snap.requirements:
        if r.job in line_of_job:
            requirements_of_line[line_of_job[r.job]].append(strip(r))
    for line in lines:
        yield f"standards:{line}", SnapshotStandards(line=line, durations=tuple(durations_of_line.get(line, ())),
                                                     requirements=tuple(requirements_of_line.get(line, ())))
    del durations_of_line, requirements_of_line

    # job buckets
    jobs_of_bucket: dict[int, list[int]] = defaultdict(list)
    for i, j in enumerate(snap.jobs):
        jobs_of_bucket[bucket_of(j.id, buckets)].append(i)
    for b in range(buckets):
        mine = [snap.jobs[i] for i in jobs_of_bucket.get(b, ())]
        if not mine:
            continue
        own = {j.id for j in mine}
        phase_idx = sorted(i for j in mine for i in phases_of_job.get(j.id, ()))
        own_phases = {snap.phases[i].key.id for i in phase_idx}
        edge_idx = sorted({i for pid in own_phases for i in touching.get(pid, ())})
        ends = {n for i in edge_idx for n in (snap.edges[i].pred_phase, snap.edges[i].succ_phase)}
        other_phase_ids = sorted((n for n in ends if n not in own_phases and n in phase_by_id))
        shorts = sorted({i for j in own for i in shortages_of_job.get(j, ())})
        named = {j for i in shorts for j in (*snap.shortages[i].affected_jobs, snap.shortages[i].root_job, snap.shortages[i].resolved_job) if j}
        named = {j for j in named if j not in own and j in jobs}
        rel_idx = sorted({i for j in own for i in scoped.get(j, ())} | {release_index[n] for n in ends if n in releases}
                         | {release_index[snap.shortages[i].release_id] for i in shorts if snap.shortages[i].release_id in releases})
        yield f"jobs:{b}", SnapshotJobs(
            bucket=b, jobs=tuple(strip(j) for j in mine), dispositions=tuple(disp[j.id] for j in mine if j.id in disp),
            phases=tuple(snap.phases[i] for i in phase_idx), edges=tuple(strip(snap.edges[i]) for i in edge_idx),
            edge_labels={n: edge_end_label(n, phase_by_id[n], jobs.get(phase_by_id[n].job)) for n in other_phase_ids},
            other_jobs=tuple(strip(jobs[j]) for j in sorted(named, key=job_index.__getitem__)),
            releases=tuple(strip(snap.releases[i]) for i in rel_idx),
            durations=tuple(durations[j.id] for j in mine if j.id in durations),
            requirements=tuple(strip(requirements[j.id]) for j in mine if j.id in requirements),
            shortages=tuple(strip(snap.shortages[i]) for i in shorts))

    # phase labels by job bucket, and every release
    for b in range(buckets):
        mine = [snap.jobs[i] for i in jobs_of_bucket.get(b, ())]
        if mine:
            yield f"labels:{b}", SnapshotLabels(bucket=b, labels={snap.phases[i].key.id: snap.phases[i].label
                                                                  for j in mine for i in phases_of_job.get(j.id, ())})
    yield "releases", SnapshotReleases(releases=tuple(strip(r) for r in snap.releases))

    # pools by team and shift start date
    pools_of: dict[tuple[str, date], list[Any]] = defaultdict(list)
    for p in snap.pools:
        pools_of[(p.key.team, p.key.start_date)].append(p)
    availability_of: dict[str, list[Any]] = defaultdict(list)
    for a in snap.availability:
        availability_of[a.pool].append(a)
    for (team, day), pools in sorted(pools_of.items()):
        yield f"pools:{team}:{day.isoformat()}", SnapshotPools(
            team=team, start_date=day, pools=tuple(pools), availability=tuple(strip(a) for p in pools for a in availability_of.get(p.id, ())))

    # each aircraft's dependency graph, then team work by aircraft
    for graph in line_graphs(snap):
        yield f"graph:{graph.line}", graph
    from ..validation.input import CONCURRENCY_EDGES

    ready_in: dict[str, list[int]] = defaultdict(list)                       # ValidationInput.hard_in, as edge indexes in order
    for i, e in enumerate(snap.edges):
        if e.hard and e.type not in CONCURRENCY_EDGES:
            ready_in[e.succ_phase].append(i)
    done = {p.key.id for p in snap.phases
            if (disp.get(p.job) is not None and disp[p.job].disposition is Disposition.VERIFIED_COMPLETE) or p.status == "COMPLETE"}

    work: dict[tuple[str, int], list[int]] = defaultdict(list)
    for i, p in enumerate(snap.phases):
        team = phase_team(p)
        if team:
            work[(team, p.line)].append(i)
    for (team, line), idx in sorted(work.items()):
        mine = [snap.phases[i] for i in idx]
        own = {p.key.id for p in mine}
        job_ids = sorted({p.job for p in mine if p.job in jobs}, key=job_index.__getitem__)
        edge_set = {i for pid in own for i in into.get(pid, ())}             # assess_action reads every edge into the phase
        preds = {snap.edges[i].pred_phase for i in edge_set}
        reach: set[str] = set()
        prefix: dict[str, int] = {}
        for pid in own:                                                       # what assess_addition's readiness walk can reach
            for i in ready_in.get(pid, ()):
                ready_reach(snap.edges[i].pred_phase, frozenset(), reach, prefix, releases=releases, phases=phase_by_id, done=done,
                            inputs=ready_in, edges=snap.edges)
        for node, k in prefix.items():
            edge_set.update(ready_in[node][:k])
        preds |= reach
        edge_idx = sorted(edge_set)
        pred_phases = sorted((n for n in preds if n not in own and n in phase_by_id), key=phase_index.__getitem__)
        own_jobs = set(job_ids)
        pred_jobs = sorted({phase_by_id[n].job for n in pred_phases if phase_by_id[n].job in disp and phase_by_id[n].job not in own_jobs},
                           key=lambda j: job_index.get(j, -1))
        rel_ids = {n for n in preds if n in releases}
        shorts = sorted({i for j in job_ids for i in shortages_of_job.get(j, ())})
        yield f"work:{team}:{line}", SnapshotWork(
            team=team, line=line, phases=tuple(mine), jobs=tuple(strip(jobs[j]) for j in job_ids),
            dispositions=tuple(disp[j] for j in job_ids if j in disp), edges=tuple(strip(snap.edges[i]) for i in edge_idx),
            predecessors=tuple(phase_by_id[n] for n in pred_phases), predecessor_dispositions=tuple(disp[j] for j in pred_jobs),
            releases=tuple(strip(releases[r]) for r in sorted(rel_ids, key=release_index.__getitem__)),
            shortages=tuple(strip(snap.shortages[i]) for i in shorts))

    # every inspection hold
    holds = [p for p in snap.phases if p.kind in HOLD_KINDS]
    hold_jobs = sorted({p.job for p in holds if p.job in jobs}, key=job_index.__getitem__)
    yield "holds", SnapshotHolds(phases=tuple(holds), jobs=tuple(strip(jobs[j]) for j in hold_jobs))


# ---------------------------------------------------------------------- plan

def burndown_of(plan: Any) -> tuple[int, tuple[BurndownPoint, ...], int]:
    """``serializers.burndown`` of a whole plan: open jobs, the series of remaining jobs by planned finish day, never finishing."""
    open_jobs = [o for o in plan.job_dispositions if o.disposition.value not in ("verified_complete", "excluded")]
    finishing: dict[date, int] = defaultdict(int)
    for o in open_jobs:
        if o.end is not None and o.disposition.value in ("scheduled", "conditional"):
            finishing[o.end.date()] += 1
    remaining, series = len(open_jobs), []
    for day in sorted(finishing):
        remaining -= finishing[day]
        series.append(BurndownPoint(day, finishing[day], remaining))
    return len(open_jobs), tuple(series), remaining


def plan_parts(snapshot: Any, artifact: Any, plan_id: str, buckets: int = BUCKETS) -> Iterator[tuple[str, Any]]:
    """The parts of one accepted plan, labeled with the snapshot it was planned on (``artifact.snapshot_hash``)."""
    if snapshot.hash != artifact.snapshot_hash:
        raise ValueError(f"plan {plan_id} was planned on snapshot {artifact.snapshot_hash[:12]}, not {snapshot.hash[:12]}")
    plan = artifact.candidate
    jobs = {j.id: j for j in snapshot.jobs}
    job_index = {j.id: i for i, j in enumerate(snapshot.jobs)}
    phases = {p.key.id: p for p in snapshot.phases}
    phase_index = {p.key.id: i for i, p in enumerate(snapshot.phases)}

    # team-day keys: assignments by phase demand team and shift start date; duties by the pools listing their person
    team_of_assignment: dict[str, str] = {}
    by_team_day: dict[tuple[str, date | None], list[int]] = defaultdict(list)
    for i, a in enumerate(plan.assignments):
        p = phases.get(a.phase)
        team = (phase_team(p) if p is not None else None) or NO_TEAM
        team_of_assignment[a.id] = team
        by_team_day[(team, shift_day(a.shift_id))].append(i)
    pool_teams: dict[tuple[str, date], set[str]] = defaultdict(set)
    any_teams: dict[str, set[str]] = defaultdict(set)
    for pool in snapshot.pools:
        for m in (*pool.members, *pool.executable_members, *pool.forecast_members):
            pool_teams[(m, pool.key.start_date)].add(pool.key.team)
            any_teams[m].add(pool.key.team)
    duties_of: dict[tuple[str, date | None], list[int]] = defaultdict(list)
    assignment_index = {a.id: i for i, a in enumerate(plan.assignments)}
    for i, d in enumerate(plan.duties):
        day = shift_day(d.shift_id)
        teams = pool_teams.get((d.person, day), set()) if day is not None else any_teams.get(d.person, set())
        if not teams:
            own = team_of_assignment.get(d.assignment_id) if d.assignment_id else None
            teams = {own} if own else {NO_TEAM}
        for team in teams:
            duties_of[(team, day)].append(i)
    team_days = sorted(set(by_team_day) | set(duties_of), key=lambda k: (k[0], k[1] or date.min))

    holds = tuple(a for a in plan.assignments if a.kind in HOLD_KINDS)
    commitment_phase_ids = {r.phase for r in plan.commitment_results if r.phase in phases}
    commitment_job_ids = {r.job for r in plan.commitment_results if r.job in jobs}
    counts = Counter(o.disposition.value for o in plan.job_dispositions)
    bottlenecks: Counter[str] = Counter()
    for o in plan.job_dispositions:
        if o.disposition.value in ("deferred", "blocked"):
            for r in o.reasons:
                bottlenecks[r.split(":")[0]] += 1
    conditional: Counter[str] = Counter()
    for a in plan.assignments:
        for r in dict.fromkeys(x.split(":", 1)[0] for x in a.conditional_reasons):
            conditional[r] += 1
    open_jobs, series, never = burndown_of(plan)
    line_of = {j.id: j.key.line for j in snapshot.jobs}
    outcomes_of_line: dict[int, list[Any]] = defaultdict(list)
    for o in plan.job_dispositions:
        if o.job in line_of:
            outcomes_of_line[line_of[o.job]].append(o)
    lines = sorted(outcomes_of_line)
    yield "summary", PlanSummary(
        plan_id=plan_id, candidate_id=plan.id, scope=plan.scope, purpose=plan.purpose, capability=artifact.capability,
        source_run_id=artifact.source_run_id, snapshot_id=artifact.snapshot_id, snapshot_hash=artifact.snapshot_hash, policy_id=artifact.policy_id,
        policy_hash=artifact.policy_hash, timezone=artifact.timezone, created_at=plan.created_at, incumbent_id=plan.incumbent_id,
        candidate_hash=plan.hash, final_validation_id=artifact.final_validation_id, obligation_dates=artifact.obligation_dates,
        frozen_goal_ids=tuple(g.id for g in artifact.frozen_goals), algorithm_status=plan.algorithm_status,
        global_optimality_proven=plan.global_optimality_proven, costs=plan.costs,
        completion=tuple(CompletionRow(c.line, c.status, c.completion_at, c.required_jobs, c.scheduled_jobs, len(c.unresolved_jobs), c.reasons)
                         for c in plan.aircraft_completion),
        commitment_count=len(plan.commitment_results),
        commitment_outcomes=dict(sorted(Counter(r.outcome for r in plan.commitment_results).items())), assignment_count=len(plan.assignments), duty_count=len(plan.duties), disposition_counts=dict(sorted(counts.items())),
        open_jobs=open_jobs, burndown=series, never_finishing=never,
        unscheduled_reason_codes=dict(sorted(bottlenecks.items(), key=lambda kv: (-kv[1], kv[0]))),
        conditional_reason_codes=dict(sorted(conditional.items())), lines=tuple(lines),
        team_days=tuple(f"{t}:{d.isoformat() if d else '-'}" for t, d in team_days))

    yield "commitments", PlanCommitments(
        results=plan.commitment_results, phases=tuple(phases[p] for p in sorted(commitment_phase_ids, key=phase_index.__getitem__)),
        jobs=tuple(strip(jobs[j]) for j in sorted(commitment_job_ids, key=job_index.__getitem__)))

    for line in lines:
        yield f"line:{line}", PlanLine(line=line, outcomes=tuple(outcomes_of_line[line]))

    a_bucket: dict[int, list[Any]] = defaultdict(list)
    for a in plan.assignments:
        a_bucket[bucket_of(a.job, buckets)].append(a)
    o_bucket: dict[int, list[Any]] = defaultdict(list)
    for o in plan.job_dispositions:
        o_bucket[bucket_of(o.job, buckets)].append(o)
    for b in sorted(set(a_bucket) | set(o_bucket)):
        yield f"jobs:{b}", PlanJobs(bucket=b, assignments=tuple(a_bucket.get(b, ())), outcomes=tuple(o_bucket.get(b, ())))

    for team, day in team_days:
        own = by_team_day.get((team, day), [])
        duty_idx = duties_of.get((team, day), [])
        served = {plan.duties[i].assignment_id for i in duty_idx if plan.duties[i].assignment_id}
        a_idx = sorted(set(own) | {assignment_index[x] for x in served if x in assignment_index})
        assignments = [plan.assignments[i] for i in a_idx]
        phase_ids = sorted({a.phase for a in assignments if a.phase in phases}, key=phase_index.__getitem__)
        job_ids = sorted({a.job for a in assignments if a.job in jobs}, key=job_index.__getitem__)
        yield f"team-day:{team}:{day.isoformat() if day else '-'}", PlanTeamDay(
            team=team, day=day, assignments=tuple(assignments), duties=tuple(strip(plan.duties[i]) for i in duty_idx),
            phases=tuple(phases[p] for p in phase_ids), jobs=tuple(strip(jobs[j]) for j in job_ids))

    if artifact.frozen_goals:
        by_id = {a.id: a for a in plan.assignments}
        facts: dict[date, dict[str, UnitFact]] = defaultdict(dict)
        for g in artifact.frozen_goals:
            for u in g.earning_units:
                a = by_id.get(u.assignment_id)
                if a is not None:
                    facts[g.shift_start_date][a.id] = UnitFact(a.id, a.start, a.end, tuple(r.person for r in a.crew), a.committed)
        for day in sorted(facts):
            yield f"goals:{day.isoformat()}", PlanGoals(day=day, facts=tuple(facts[day][k] for k in sorted(facts[day])))

    yield "holds", PlanHolds(assignments=holds)
