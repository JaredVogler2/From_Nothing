"""Canonical plan artifacts and frozen goals (S13 CANONICAL ACCEPTANCE ORDER and GOAL FREEZE, P16).

``build_artifact`` binds one validated candidate into an immutable ``PlanArtifact``:

* **Bindings.** Scope, purpose and capability, the source run, snapshot and manifest hashes,
  the policy and the timezone.
* **Graph lineage.** Every hard edge of the snapshot's graph.
* **CS lineage.** The same-run CS station facts for every placed aircraft: raw row, load,
  offsets, gate and bounds.
* **The candidate itself.** Crews, duties, economics and the complete inventory.
* **Frozen goals.** Today's obligation set, one goal per team-shift, with standard person
  effort split into segment earning units, the admitted membership and the S14 gate
  recognition context. The full-horizon plan stays in the candidate, separate from the
  obligation set.

``artifact_bytes`` is strict canonical JSON. ``decode_artifact`` rejects anything that does not
re-encode to the identical bytes, so a stored, staged or transported artifact can be compared
byte for byte. Nothing here signs; the service signs only bytes it has read back and validated.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from ..calendar import parse_shift_id
from ..codec import CodecError, canonical_dumps, from_jsonable, sha256_hex, strict_loads
from ..contracts.planning import (CandidatePlan, CSLineage, EarningUnit, EdgeLineage, FrozenGoal, GateContext, PlanArtifact,
                                  PlanningSnapshot, record_hash)
from ..domain import PhaseKind, Purpose, digest_id, to_minute
from ..policy import Policy

ARTIFACT_TYPE = "FOCUS_PLAN_V1"
ARTIFACT_SCHEMA = 1
CAPABILITY = {Purpose.EXECUTABLE: "EXECUTABLE_DISPATCH", Purpose.FORECAST: "FORECAST_ONLY"}


class ArtifactError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------- lineage

def graph_lineage(snapshot: PlanningSnapshot) -> tuple[EdgeLineage, ...]:
    return tuple(sorted((EdgeLineage(e.id, e.pred_phase, e.succ_phase, e.type, e.lag_minutes, e.hard, e.basis) for e in snapshot.edges if e.hard),
                        key=lambda e: e.id))


def cs_lineage(snapshot: PlanningSnapshot, plan: CandidatePlan) -> tuple[CSLineage, ...]:
    jobs = {j.id: j for j in snapshot.jobs}
    facts = {(f.line, f.station): f for f in snapshot.cs_facts}
    raw = {(r.line, str(r.control_station), r.load_id): r for r in snapshot.raw_cs_rows if r.is_active}
    out = []
    for key in sorted({(jobs[a.job].line, jobs[a.job].station) for a in plan.assignments if a.job in jobs}, key=lambda k: (k[0], str(k[1]))):
        f = facts.get(key)
        if f is None:
            continue
        r = raw.get((f.line, f.station, f.raw_load_id))
        out.append(CSLineage(f.line, f.station, f.source_run_id, r.row_id if r else "", f.raw_load_id, r.load_ref_pos if r else None,
                             r.load_offset if r else None, r.unload_ref_pos if r else None, r.unload_offset if r else None, f.gate_date,
                             f.earliest_at, f.hard_latest_at, f.conversion_policy))
    return tuple(out)


# ---------------------------------------------------------------------- goal freeze

def obligation_dates(snapshot: PlanningSnapshot, policy: Policy) -> tuple[date, ...]:
    from zoneinfo import ZoneInfo

    first = snapshot.cutoff.astimezone(ZoneInfo(policy.str("shift.timezone"))).date()
    return tuple(first + timedelta(days=i) for i in range(policy.int("goals.obligation_days")))


def gate_context(policy: Policy, gate_date: date | None, valuation: date, effort: int) -> GateContext:
    """S14 gate recognition frozen at publication: base = effort/divisor; bonus = base * weight * min(max(age,0)/cap, 1)."""
    base = Fraction(effort, policy.int("recognition.base_divisor"))
    basis = policy.str("gate.date_basis")
    if gate_date is None:
        return GateContext("UNAVAILABLE", None, None, valuation, None, None, base, None)
    effective = gate_date + timedelta(days=policy.int("gate.proxy_grace_days")) if basis == "BASELINE_PROXY" else gate_date
    behind = max((valuation - effective).days, 0)
    raw = min(Fraction(behind, policy.int("gate.cap_days")), Fraction(1))
    return GateContext(basis, gate_date, effective, valuation, behind, raw, base, base * Fraction(policy.int("gate.weight"), 100) * raw)


def _split(total: int, parts: list[int]) -> list[int]:
    """Exact integer allocation of standard effort over segment minutes (largest remainder, deterministic)."""
    whole = sum(parts)
    if whole <= 0:
        return [0] * len(parts)
    raw = [Fraction(total * p, whole) for p in parts]
    out = [int(r) for r in raw]
    order = sorted(range(len(parts)), key=lambda i: (-(raw[i] - out[i]), i))
    for i in order[: total - sum(out)]:
        out[i] += 1
    return out


def freeze_goals(snapshot: PlanningSnapshot, policy: Policy, plan: CandidatePlan) -> tuple[FrozenGoal, ...]:
    phases = {p.key.id: p for p in snapshot.phases}
    jobs = {j.id: j for j in snapshot.jobs}
    facts = {(f.line, f.station): f for f in snapshot.cs_facts}
    pools = {p.id: p for p in snapshot.pools}
    people = {p.id: p for p in snapshot.people}
    days = set(obligation_dates(snapshot, policy))
    units: dict[tuple[str, str], list[EarningUnit]] = defaultdict(list)
    effort_by_job: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    pool_of: dict[tuple[str, str], str] = {}
    for a in sorted(plan.assignments, key=lambda x: (x.start, x.id)):
        phase = phases.get(a.phase)
        if phase is None or not a.shift_id or not phase.role_demands or phase.kind is PhaseKind.RELEASE:
            continue
        _, day, _ = parse_shift_id(a.shift_id)
        if day not in days:
            continue                                         # the full horizon stays in the plan, not in today's obligations
        team = phase.role_demands[0].team
        minutes = [to_minute(s.end) - to_minute(s.start) for s in a.segments]
        for i, effort in enumerate(_split(phase.standard_person_minutes, minutes)):
            units[(team, a.shift_id)].append(EarningUnit(digest_id("EU", plan.id, a.id, i), a.id, a.job, a.phase, i, team, a.shift_id, effort,
                                                         team, "PROPORTIONAL_SEGMENT_MINUTES"))
            effort_by_job[(team, a.shift_id)][a.job] += effort
        if a.pool_key:
            pool_of[(team, a.shift_id)] = a.pool_key
    goals = []
    for (team, shift_id), items in sorted(units.items()):
        _, day, number = parse_shift_id(shift_id)
        contexts = {}
        for job_id, effort in sorted(effort_by_job[(team, shift_id)].items()):
            job = jobs[job_id]
            f = facts.get((job.line, job.station))
            contexts[job_id] = gate_context(policy, f.gate_date if f is not None and f.status == "ADMITTED" else None, day, effort)
        pool = pools.get(pool_of.get((team, shift_id), ""))
        if pool is None:
            members: tuple[str, ...] = ()
        elif plan.purpose is Purpose.EXECUTABLE:
            members = tuple(sorted(m for m in pool.executable_members if m in people and not people[m].synthetic))
        else:
            members = tuple(sorted(pool.forecast_members))
        goal = FrozenGoal(digest_id("GOAL", plan.id, team, shift_id), plan.id, shift_id, team, day, number, tuple(items),
                          sum(u.standard_person_minutes for u in items), contexts, members, "")
        goals.append(replace(goal, hash=record_hash(goal)))
    return tuple(goals)


# ---------------------------------------------------------------------- artifact

def build_artifact(snapshot: PlanningSnapshot, policy: Policy, plan: CandidatePlan, final_validation_id: str) -> PlanArtifact:
    if plan.purpose not in CAPABILITY:
        raise ArtifactError("PURPOSE_NOT_PUBLISHABLE", f"{plan.purpose.value} plans are never published")
    return PlanArtifact(
        schema_version=ARTIFACT_SCHEMA, artifact_type=ARTIFACT_TYPE, scope=plan.scope, purpose=plan.purpose, capability=CAPABILITY[plan.purpose],
        source_run_id=snapshot.source_run_id, snapshot_id=snapshot.id, snapshot_hash=snapshot.hash, manifest_hash=snapshot.manifest_hash,
        policy_id=policy.id, policy_hash=policy.hash, timezone=policy.str("shift.timezone"), candidate=plan,
        final_validation_id=final_validation_id, graph=graph_lineage(snapshot), cs_lineage=cs_lineage(snapshot, plan),
        obligation_dates=obligation_dates(snapshot, policy), frozen_goals=freeze_goals(snapshot, policy, plan))


def artifact_bytes(artifact: PlanArtifact) -> bytes:
    return canonical_dumps(artifact)


def payload_sha256(data: bytes) -> str:
    return sha256_hex(data)


def decode_artifact(data: bytes) -> PlanArtifact:
    """Strict decode: canonical bytes only, known schema/type, typed fields, identical re-encoding."""
    try:
        raw: Any = strict_loads(data)
    except (CodecError, ValueError) as exc:
        raise ArtifactError("ARTIFACT_UNDECODABLE", str(exc)) from exc
    if not isinstance(raw, dict) or raw.get("artifact_type") != ARTIFACT_TYPE or raw.get("schema_version") != ARTIFACT_SCHEMA:
        raise ArtifactError("ARTIFACT_TYPE", "not a FOCUS_PLAN_V1 schema 1 artifact")
    try:
        artifact = from_jsonable(PlanArtifact, raw)
    except (CodecError, ValueError, TypeError) as exc:
        raise ArtifactError("ARTIFACT_UNDECODABLE", str(exc)) from exc
    if artifact_bytes(artifact) != bytes(data):
        raise ArtifactError("NON_CANONICAL", "the bytes are not the canonical encoding of their own content")
    return artifact
