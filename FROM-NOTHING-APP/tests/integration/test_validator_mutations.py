"""P15: the independent validator against a mutation corpus of every required check (A47, A26, A38, A64; A48 detached part).

Each mutation corrupts the real final candidate once. Attackers re-seal the content hash, except
in the hash-tamper case. The validator must name the expected check and code, reading only the
original source snapshot. The unmutated candidates validate clean with nonzero coverage on
every required check.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest

from focus.calendar import ShiftPolicy, build_shift
from focus.clock import FIXTURE_CUTOFF, FixedClock
from focus.config import Settings
from focus.contracts.planning import seal_candidate
from focus.data.acquisition import acquire, make_reader, snapshot_for_run
from focus.data.repositories import Repositories
from focus.data.store import FixtureStore
from focus.domain import Interval, PersonKey, PhaseKind, Purpose, from_minute, to_minute
from focus.engine.commitments import import_fixture_incumbent
from focus.engine.max.optimizer.greedy import PhasePlacement
from focus.engine.max.optimizer.orchestrator import MaxOptimizer, schedule
from focus.engine.model import EngineContext
from focus.publication.serializer import artifact_bytes, build_artifact
from focus.runtime import build_services
from focus.validation.input import ValidationInput
from focus.validation.plan import REQUIRED_CHECKS, load_incumbent, validate
from focus.validation.publication import publication_capability, validate_artifact
from tests.conftest import job_id


@pytest.fixture(scope="module")
def base(tmp_path_factory, app_root):
    root = tmp_path_factory.mktemp("mutations")
    settings = Settings.from_env({}, app_root=app_root)
    clock = FixedClock(FIXTURE_CUTOFF)
    repos = Repositories(FixtureStore(root / "rt"), mode="fixture", scope_key=settings.scope_key, clock=clock)
    services = build_services(settings, repositories=repos, clock=clock)
    outcome = acquire(services, make_reader(services, "manufacturing_demo"), cutoff=FIXTURE_CUTOFF, idempotency_key="mutations")
    plans, runs = {}, {}
    for purpose in (Purpose.EXECUTABLE, Purpose.FORECAST):
        import_fixture_incumbent(services, purpose=purpose.value)
        plans[purpose], runs[purpose] = schedule(services, source_run=outcome.run_id, purpose=purpose, budget_seconds=10, max_moves=20)
    snapshot = snapshot_for_run(services, outcome.run_id)
    return SimpleNamespace(services=services, outcome=outcome, snapshot=snapshot, policy=services.policy, plans=plans, runs=runs,
                           incumbents={p: load_incumbent(repos, plans[p].incumbent_id) for p in plans},
                           inputs={p: ValidationInput.build(snapshot, services.policy, p) for p in plans},
                           sp=ShiftPolicy.from_policy(services.policy))


def P(native: str) -> str:
    return PersonKey(native, "fixture-demo").id


def check(base, plan, purpose=None, incumbent="default", **kw):
    purpose = purpose or plan.purpose
    inc = base.incumbents[plan.purpose] if incumbent == "default" else incumbent
    return validate(base.snapshot, base.policy, plan, purpose=purpose, incumbent=inc, inp=base.inputs.get(purpose), **kw)


def codes(receipt) -> set[tuple[str, str]]:
    return {(v.check, v.code) for v in receipt.violations}


def find(base, plan, line, soi, kind=PhaseKind.WORK, ordinal=0):
    jid = job_id(base.snapshot, line, soi)
    return [a for a in plan.assignments if a.job == jid and a.kind is kind][ordinal]


def swap(plan, old, new, reseal=True):
    out = replace(plan, assignments=tuple(new if a.id == old.id else a for a in plan.assignments))
    return seal_candidate(out) if reseal else out


def add(plan, *extra):
    return seal_candidate(replace(plan, assignments=plan.assignments + tuple(extra)))


def at(base, day: date, hhmm: str) -> int:
    return to_minute(datetime.combine(day, time.fromisoformat(hhmm), tzinfo=base.sp.tz))


def segments(a, segs):
    return replace(a, segments=tuple(Interval.of_minutes(s, e) for s, e in segs), start=from_minute(segs[0][0]), end=from_minute(segs[-1][1]))


def place(base, a, day: date, number: int, hhmm: str, minutes: int | None = None, retarget=True):
    """Move an assignment to a new shift/start through the calendar authority, keeping the work minutes."""
    shift = build_shift(day, number, base.sp, "DEMO")
    fit = shift.advance(at(base, day, hhmm), minutes or a.work_minutes, 60)
    assert fit is not None, (day, number, hhmm)
    merged: list[list[int]] = []
    for s, e, _ in fit[1]:
        if merged and merged[-1][1] == s:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    moved = replace(segments(a, [tuple(x) for x in merged]), shift_id=shift.id)
    if not retarget:
        return moved
    inp = base.inputs[a.purpose]
    phase = inp.phases[a.phase]
    pool = inp.pool_by_key.get((phase.role_demands[0].team, day, number))
    crew = tuple(replace(r, availability_id=inp.availability[(r.person, pool.id)].id) if pool and (r.person, pool.id) in inp.availability else r
                 for r in a.crew)
    return replace(moved, pool_key=pool.id if pool else a.pool_key, crew=crew)


def with_person(base, a, old_native: str, new_person: str):
    inp = base.inputs[a.purpose]
    av = inp.availability.get((new_person, a.pool_key))
    crew = tuple(replace(r, person=new_person, availability_id=av.id if av else "", holding_evidence_ids=())
                 if r.person == P(old_native) else r for r in a.crew)
    return replace(a, crew=crew)


# ---------------------------------------------------------------------- baseline


@pytest.mark.parametrize("purpose", [Purpose.EXECUTABLE, Purpose.FORECAST])
def test_final_candidates_validate_clean_with_full_coverage(base, purpose) -> None:
    receipt = check(base, base.plans[purpose])
    assert receipt.valid and not receipt.violations and not receipt.required_skips
    assert all(receipt.check_counts[c] > 0 for c in REQUIRED_CHECKS), receipt.check_counts      # coverage nonzero, nothing vacuous
    assert receipt.coverage["V2"]["status"] == "CHECKED" and receipt.validator_version
    assert base.runs[purpose].validation.valid and base.runs[purpose].validated >= 1               # schedule validated it too
    assert publication_capability(base.plans[purpose], base.runs[purpose].validation) == []


def test_zero_violations_with_a_skipped_required_check_is_invalid(base) -> None:
    receipt = check(base, base.plans[Purpose.EXECUTABLE], incumbent=None)
    assert not receipt.violations and receipt.required_skips == ("E_COMMITMENT",) and not receipt.valid
    assert any(r.startswith("REQUIRED_CHECKS_SKIPPED") for r in publication_capability(base.plans[Purpose.EXECUTABLE], receipt))


# ---------------------------------------------------------------------- the corpus

def m_work(base, plan):                                     # V1: placed active minutes != remaining work
    a = find(base, plan, 1301, "A1")
    s, e = to_minute(a.segments[-1].start), to_minute(a.segments[-1].end)
    return swap(plan, a, segments(a, [(s, e - 10)]))


def m_restart(base, plan):                                  # V1 legacy: in-progress job re-planned at full duration (restart fiction)
    a = find(base, plan, 1304, "ROOT4")
    return swap(plan, a, place(base, a, date(2026, 10, 28), 1, "07:15", minutes=100))


def m_unknown_phase(base, plan):
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, phase="J0000000000000000000.WORK.0"))


def m_identity(base, plan):                                 # V1: forged line on an otherwise real assignment
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, line=1302))


def m_precedence(base, plan):                               # V2 legacy: in-progress successor starts before its incomplete FS predecessor
    a = find(base, plan, 1304, "ROOT4")
    return swap(plan, a, place(base, a, date(2026, 10, 28), 1, "06:15"))


def m_omitted_predecessor(base, plan):                      # V2: the predecessor vanishes; omission never satisfies it
    pre = find(base, plan, 1304, "PRE4")
    outcomes = tuple(replace(o, disposition=o.disposition.DEFERRED, reasons=("forged deferral",), start=None, end=None)
                     if o.job == pre.job else o for o in plan.job_dispositions)
    return seal_candidate(replace(plan, assignments=tuple(a for a in plan.assignments if a.id != pre.id), job_dispositions=outcomes))


def m_and_release(base, plan):                              # V2: successor before the AND release (final QA) of its predecessor
    a = find(base, plan, 1302, "FGI")
    return swap(plan, a, place(base, a, date(2026, 10, 27), 1, "10:05", retarget=False))


def m_weekend(base, plan):                                  # V3: S1 on a WEEKEND day of the line's own calendar
    a = find(base, plan, 1301, "FGI")
    return swap(plan, a, place(base, a, date(2026, 10, 31), 1, "08:30"))


def m_s3_rule(base, plan):                                  # V3: S3 on a weekend whose next day is not WORKING
    a = find(base, plan, 1301, "FGI")
    return swap(plan, a, place(base, a, date(2026, 10, 31), 3, "23:15"))


def m_bounds(base, plan):                                   # V3: outside the bounded calendar generation
    a = find(base, plan, 1301, "FGI")
    return swap(plan, a, place(base, a, date(2026, 11, 16), 1, "08:30"))


def m_break(base, plan):                                    # V4: active work inside the 09:00 break, minutes still conserved
    a = find(base, plan, 1302, "B2")
    (s1, e1), (s2, e2) = [(to_minute(s.start), to_minute(s.end)) for s in a.segments]
    return swap(plan, a, segments(a, [(s1, e1 + 10), (s2, e2 - 10)]))


def m_no_start(base, plan):                                 # V4: start one minute after the no-start cutoff
    a = find(base, plan, 1301, "FGI")
    d = date(2026, 10, 28)
    return swap(plan, a, segments(a, [(at(base, d, "13:46"), at(base, d, "14:15")), (at(base, d, "14:30"), at(base, d, "14:31"))]))


def m_backdate(base, plan):                                 # V4: start before the snapshot cutoff
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, place(base, a, date(2026, 10, 23), 1, "06:15"))


def m_ot_misreported(base, plan):                           # V4: truthful OT amount
    d = next(x for x in plan.duties if x.kind.value == "WORK" and x.regular_minutes >= 5)
    duties = tuple(replace(x, regular_minutes=x.regular_minutes - 5, ot_minutes=x.ot_minutes + 5) if x.id == d.id else x for x in plan.duties)
    return seal_candidate(replace(plan, duties=duties))


def m_short_crew(base, plan):                               # V5: one person short (no clamp)
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, crew=a.crew[:1]))


def m_duplicate(base, plan):
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, crew=(a.crew[0], a.crew[0])))


def m_unqualified(base, plan):                              # V5: a same-pool person without the bundle, carrying forged evidence
    inp = base.inputs[plan.purpose]
    for a in plan.assignments:
        demand = inp.phases[a.phase].role_demands[0]
        _, day, label = a.shift_id.rsplit(":", 2)
        pool = inp.pool_by_key[(demand.team, date.fromisoformat(day), int(label[1:]))]
        for person in sorted(inp.admitted_members(pool) - {r.person for r in a.crew}):
            if not inp.bundle_held(person, demand.bundle, to_minute(a.start), to_minute(a.end))[0]:
                first = a.crew[0]
                forged = replace(first, person=person, availability_id=inp.availability[(person, pool.id)].id)   # keeps first's evidence ids
                return swap(plan, a, replace(a, crew=(forged,) + a.crew[1:]))
    raise AssertionError("fixture has no unqualified same-pool person")


def m_loan(base, plan):                                     # V5: cross-team loan
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, with_person(base, a, "DEMO-A1-02", P("DEMO-B1-02")))


def m_synthetic(base, plan):                                # V5/A64: virtual support person in an executable crew
    a = find(base, plan, 1301, "A1")
    virtual = next(p.id for p in base.snapshot.people if p.key.native_id == "VIRTUAL-MHE-S1-01")
    return swap(plan, a, with_person(base, a, "DEMO-A1-02", virtual))


def m_role(base, plan):
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, crew=tuple(replace(r, role="INSPECTOR") for r in a.crew)))


def m_roster(base, plan):                                   # V5: candidate-only roster declaration
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, crew=tuple(replace(r, availability_id="AV-FORGED") for r in a.crew)))


def m_pool_key(base, plan):
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, pool_key="Q-FORGED-POOL"))


def m_evidence(base, plan):
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, crew=tuple(replace(r, holding_evidence_ids=r.holding_evidence_ids + ("mechanic_certs#9999",)) for r in a.crew)))


def m_qa_pool(base, plan):                                  # V5 legacy: production person in a QA inspection (no unlimited QA pool)
    a = find(base, plan, 1303, "NC3", PhaseKind.FINAL_QA)
    return swap(plan, a, replace(a, crew=(replace(a.crew[0], person=P("DEMO-B1-02")),)))


def m_carry_in(base, plan):                                 # V6: work during actual carry-in duty (training 06:15-07:15)
    a = find(base, plan, 1303, "NC3")
    return swap(plan, a, with_person(base, a, "DEMO-B1-04", P("DEMO-B1-02")))


def m_overlap(base, plan):                                  # V6: same crew in two places at once
    a = find(base, plan, 1303, "PU3")
    return swap(plan, a, place(base, a, date(2026, 10, 26), 1, "06:30", retarget=False))


def m_transfer(base, plan):                                 # V7: back-to-back aircraft change with no transfer
    a1 = find(base, plan, 1301, "A1")
    b1 = find(base, plan, 1302, "B1")
    forged = place(base, replace(b1, id="FORGED-B1", crew=(a1.crew[0], b1.crew[1])), date(2026, 10, 26), 1, "08:15", retarget=False)
    return add(plan, forged)


def m_cap(base, plan):                                      # V8: activate every admitted member of a pool
    inp = base.inputs[plan.purpose]
    a = find(base, plan, 1301, "A1")
    pool = inp.pools[a.pool_key]
    members = sorted(inp.admitted_members(pool))
    crew = tuple(replace(a.crew[0], person=m, availability_id=inp.availability[(m, pool.id)].id) for m in members)
    return add(plan, place(base, replace(a, id="FORGED-CAP", crew=crew), date(2026, 10, 26), 1, "12:00", retarget=False))


def m_part_exec(base, plan):                                # V9: executable work behind an unreceived part
    f = find(base, base.plans[Purpose.FORECAST], 1305, "CHILD5")
    return add(plan, replace(f, id="FORGED-CHILD5", purpose=Purpose.EXECUTABLE, conditional_reasons=()))


def m_inventory(base, plan):                                # E_INVENTORY: a job silently dropped
    return seal_candidate(replace(plan, job_dispositions=plan.job_dispositions[1:]))


def m_blocked(base, plan):                                  # E_INVENTORY: blocked job reported scheduled
    tst = job_id(base.snapshot, 1306, "TST6B")
    return seal_candidate(replace(plan, job_dispositions=tuple(replace(o, disposition=o.disposition.SCHEDULED) if o.job == tst else o
                                                               for o in plan.job_dispositions)))


def m_hash(base, plan):                                     # E_LINEAGE: content changed without the sealed hash
    return replace(plan, global_optimality_proven=False, algorithm_status=replace(plan.algorithm_status, seed=1))


def m_snapshot(base, plan):
    return seal_candidate(replace(plan, snapshot_hash="0" * 64))


def m_policy(base, plan):
    return seal_candidate(replace(plan, policy_hash="f" * 64))


def m_proof(base, plan):
    return seal_candidate(replace(plan, global_optimality_proven=True))


def m_commit_kept(base, plan):                              # E_COMMITMENT: a crew change reported as KEPT
    results = tuple(replace(r, outcome="KEPT") if r.outcome == "CREW_CHANGED" else r for r in plan.commitment_results)
    return seal_candidate(replace(plan, commitment_results=results))


def m_commit_missing(base, plan):
    return seal_candidate(replace(plan, commitment_results=tuple(r for r in plan.commitment_results if r.outcome != "KEPT")[:1]))


def m_cost(base, plan):                                     # E_ECONOMICS: forged cheaper cost
    return seal_candidate(replace(plan, costs=replace(plan.costs, controllable_known_cents=plan.costs.controllable_known_cents - Fraction(1))))


def m_completion(base, plan):
    first = plan.aircraft_completion[1]
    forged = replace(first, completion_at=(first.completion_at or base.snapshot.cutoff) - timedelta(days=1))
    return seal_candidate(replace(plan, aircraft_completion=(plan.aircraft_completion[0], forged) + plan.aircraft_completion[2:]))


def m_conditional(base, plan):                              # E_CAPABILITY: forecast-only evidence labelled into an executable plan
    a = find(base, plan, 1301, "A1")
    return swap(plan, a, replace(a, conditional_reasons=("conditional part ETA",)))


CORPUS = [
    ("V1-work", m_work, ("V1", "WORK_NOT_CONSERVED")),
    ("V1-restart-fiction", m_restart, ("V1", "WORK_NOT_CONSERVED")),
    ("V1-unknown-phase", m_unknown_phase, ("V1", "UNKNOWN_PHASE")),
    ("V1-identity", m_identity, ("V1", "IDENTITY_MISMATCH")),
    ("V2-in-progress-before-predecessor", m_precedence, ("V2", "PRECEDENCE_VIOLATION")),
    ("V2-omitted-predecessor", m_omitted_predecessor, ("V2", "PREDECESSOR_UNRESOLVED")),
    ("V2-and-release", m_and_release, ("V2", "PRECEDENCE_VIOLATION")),
    ("V3-weekend", m_weekend, ("V3", "NONWORKING_DAY")),
    ("V3-s3-rule", m_s3_rule, ("V3", "S3_NONWORKING")),
    ("V3-bounds", m_bounds, ("V3", "OUTSIDE_CALENDAR_BOUNDS")),
    ("V4-break", m_break, ("V4", "SEGMENT_OUTSIDE_SHIFT_WINDOWS")),
    ("V4-no-start", m_no_start, ("V4", "NO_START_CUTOFF")),
    ("V4-backdate", m_backdate, ("V4", "BEFORE_CUTOFF")),
    ("V4-ot-misreported", m_ot_misreported, ("V4", "OT_MISREPORTED")),
    ("V5-short", m_short_crew, ("V5", "CREW_SIZE_MISMATCH")),
    ("V5-duplicate", m_duplicate, ("V5", "CREW_DUPLICATE_PERSON")),
    ("V5-unqualified-forged-evidence", m_unqualified, ("V5", "NOT_QUALIFIED")),
    ("V5-loan", m_loan, ("V5", "NOT_POOL_MEMBER")),
    ("V5-synthetic", m_synthetic, ("V5", "SYNTHETIC_IN_EXECUTABLE")),
    ("V5-role", m_role, ("V5", "ROLE_MISMATCH")),
    ("V5-roster", m_roster, ("V5", "CANDIDATE_ONLY_ROSTER")),
    ("V5-pool-key", m_pool_key, ("V5", "POOL_KEY_MISMATCH")),
    ("V5-evidence", m_evidence, ("V5", "CERT_EVIDENCE_FORGED")),
    ("V5-qa-pool", m_qa_pool, ("V5", "NOT_POOL_MEMBER")),
    ("V6-carry-in", m_carry_in, ("V6", "PERSON_OVERLAP")),
    ("V6-overlap", m_overlap, ("V6", "PERSON_OVERLAP")),
    ("V7-transfer", m_transfer, ("V7", "TRANSFER_MISSING")),
    ("V8-cap", m_cap, ("V8", "ACTIVATION_CAP")),
    ("V9-part-executable", m_part_exec, ("V9", "PART_NOT_RECEIVED")),
    ("E-inventory-missing", m_inventory, ("E_INVENTORY", "INVENTORY_MISSING")),
    ("E-inventory-blocked", m_blocked, ("E_INVENTORY", "DISPOSITION_CONFLICT")),
    ("E-lineage-hash", m_hash, ("E_LINEAGE", "PLAN_HASH_MISMATCH")),
    ("E-lineage-snapshot", m_snapshot, ("E_LINEAGE", "SNAPSHOT_MISMATCH")),
    ("E-lineage-policy", m_policy, ("E_LINEAGE", "POLICY_MISMATCH")),
    ("E-lineage-proof", m_proof, ("E_LINEAGE", "UNTRUTHFUL_PROOF")),
    ("E-commitment-misreported", m_commit_kept, ("E_COMMITMENT", "COMMITMENT_MISREPORTED")),
    ("E-commitment-missing", m_commit_missing, ("E_COMMITMENT", "COMMITMENT_RESULT_MISSING")),
    ("E-economics-cost", m_cost, ("E_ECONOMICS", "ECONOMICS_MISMATCH")),
    ("E-economics-completion", m_completion, ("E_ECONOMICS", "COMPLETION_MISMATCH")),
    ("E-capability-conditional", m_conditional, ("E_CAPABILITY", "CONDITIONAL_IN_EXECUTABLE")),
]


@pytest.mark.parametrize(("name", "mutate", "expected"), CORPUS, ids=[c[0] for c in CORPUS])
def test_a47_each_corruption_is_caught_and_cannot_publish(base, name, mutate, expected) -> None:
    plan = base.plans[Purpose.EXECUTABLE]
    bad = mutate(base, plan)
    receipt = check(base, bad)
    assert expected in codes(receipt), sorted(codes(receipt))
    assert not receipt.valid and receipt.check_counts["V1"] > 0                       # coverage stays nonzero after the mutation
    assert publication_capability(bad, receipt)                                     # a corrupted plan can never publish
    assert check(base, plan).valid                                                  # the original is untouched


def test_a47_every_required_check_has_a_catching_mutation() -> None:
    covered = {expected[0] for _, _, expected in CORPUS}
    assert set(REQUIRED_CHECKS) - {"E_CS"} <= covered                               # E_CS has its forged-source test below


def test_v4_no_start_boundary_equality_is_allowed(base) -> None:
    plan = base.plans[Purpose.EXECUTABLE]
    a = find(base, plan, 1301, "FGI")
    d = date(2026, 10, 28)
    ok = swap(plan, a, segments(a, [(at(base, d, "13:45"), at(base, d, "14:15"))]))
    assert ("V4", "NO_START_CUTOFF") not in codes(check(base, ok))                  # coordinate 400 == cutoff: allowed


def test_v9_forecast_part_floor_and_label(base) -> None:
    plan = base.plans[Purpose.FORECAST]
    child = find(base, plan, 1305, "CHILD5")
    assert to_minute(child.start) == to_minute(datetime(2026, 10, 28, 4, tzinfo=child.start.tzinfo))   # placed exactly at the floor
    early = swap(plan, child, place(base, child, date(2026, 10, 27), 3, "23:15"))
    assert ("V9", "BEFORE_PART_FLOOR") in codes(check(base, early))
    unlabelled = swap(plan, child, replace(child, conditional_reasons=()))
    assert {("V9", "PART_CONDITIONAL_UNLABELLED"), ("E_CAPABILITY", "CONDITIONAL_UNLABELLED")} <= codes(check(base, unlabelled))


def test_a64_virtual_support_is_forecast_only_by_origin_evidence(base) -> None:
    fplan = base.plans[Purpose.FORECAST]
    mhe = find(base, fplan, 1306, "MHE6")
    assert check(base, fplan).valid and all(base.inputs[Purpose.FORECAST].synthetic(r.person) for r in mhe.crew)
    as_exec = seal_candidate(replace(fplan, purpose=Purpose.EXECUTABLE,
                                     assignments=tuple(replace(a, purpose=Purpose.EXECUTABLE) for a in fplan.assignments)))
    found = codes(check(base, as_exec, purpose=Purpose.EXECUTABLE, incumbent=base.incumbents[Purpose.EXECUTABLE]))
    assert {("V5", "SYNTHETIC_IN_EXECUTABLE"), ("E_CAPABILITY", "NOT_PLACEABLE_FOR_PURPOSE")} <= found


def test_e_cs_forged_source_station_offset_is_caught_against_raw_rows(base) -> None:
    snap = base.snapshot
    facts = list(snap.cs_facts)
    facts[0] = replace(facts[0], raw_offset_fields={**facts[0].raw_offset_fields, "LOAD_OFFSET": 3})
    facts[1] = replace(facts[1], source_run_id="SR-FOREIGN")
    forged = replace(snap, cs_facts=tuple(facts))
    forged = replace(forged, hash=forged.computed_hash())                           # a self-consistent forged source
    receipt = validate(forged, base.policy, base.plans[Purpose.EXECUTABLE], incumbent=base.incumbents[Purpose.EXECUTABLE])
    found = codes(receipt)
    assert {("E_CS", "CS_RAW_FIELD_MISMATCH"), ("E_CS", "CS_FACT_FOREIGN_RUN"), ("E_LINEAGE", "SNAPSHOT_MISMATCH")} <= found
    tampered = replace(snap, cs_facts=tuple(facts))                                 # forged without re-hashing: integrity fails first
    bad = validate(tampered, base.policy, base.plans[Purpose.EXECUTABLE], incumbent=base.incumbents[Purpose.EXECUTABLE])
    assert ("E_LINEAGE", "SNAPSHOT_HASH_MISMATCH") in codes(bad) and len(bad.required_skips) == len(REQUIRED_CHECKS) - 1


def test_a48_detached_bytes_with_forged_fields_are_rejected(base) -> None:
    plan = base.plans[Purpose.EXECUTABLE]
    art = build_artifact(base.snapshot, base.policy, plan, base.runs[Purpose.EXECUTABLE].validation.id)
    data = artifact_bytes(art)
    ok_art, ok = validate_artifact(data, base.snapshot, base.policy, incumbent=base.incumbents[Purpose.EXECUTABLE])
    assert ok.valid and ok_art == art and ok.check_counts["E_ARTIFACT"] > 0

    def forge(edit):
        obj = json.loads(data)
        edit(obj)
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def first_root_edge(o):
        e = next(x for x in o["graph"] if x["basis"] != "PART_RELEASE")
        e["succ_phase"], e["pred_phase"] = e["pred_phase"], e["succ_phase"]

    cases = {
        "crew": (lambda o: o["candidate"]["assignments"][0]["crew"][0].update(person=P("DEMO-B1-02")), ("E_LINEAGE", "PLAN_HASH_MISMATCH")),
        "root-edge": (first_root_edge, ("E_ARTIFACT", "GRAPH_LINEAGE_MISMATCH")),
        "timezone": (lambda o: o.update(timezone="America/Chicago"), ("E_ARTIFACT", "TIMEZONE_MISMATCH")),
        "station-load": (lambda o: o["cs_lineage"][0].update(raw_load_id=7002), ("E_ARTIFACT", "CS_LINEAGE_MISMATCH")),
        "offset": (lambda o: o["cs_lineage"][0].update(load_offset=-2), ("E_ARTIFACT", "CS_LINEAGE_MISMATCH")),
        "source-hash": (lambda o: o.update(snapshot_hash="0" * 64), ("E_ARTIFACT", "SOURCE_HASH_MISMATCH")),
    }
    for name, (edit, expected) in cases.items():
        _, receipt = validate_artifact(forge(edit), base.snapshot, base.policy, incumbent=base.incumbents[Purpose.EXECUTABLE])
        assert not receipt.valid and expected in codes(receipt), (name, sorted(codes(receipt)))
    _, receipt = validate_artifact(data[:-1] + b" }", base.snapshot, base.policy)
    assert not receipt.valid and codes(receipt) & {("E_ARTIFACT", "NON_CANONICAL"), ("E_ARTIFACT", "ARTIFACT_UNDECODABLE")}


def test_every_search_path_rolls_back_an_invalid_candidate(base, monkeypatch) -> None:
    """Seed, economic redispatch, repair and neighbourhood candidates all pass the same independent gate."""
    ctx = EngineContext(base.snapshot, base.policy, Purpose.EXECUTABLE)
    calls = {"n": 0}
    real = MaxOptimizer.decode
    deferred_phase = next(p for p in ctx.phases.values() if ctx.jobs[p.job].key.native_id == "MHE6" and p.kind is PhaseKind.WORK)

    def corrupting(self, state, weights):
        result = real(self, state, weights)
        calls["n"] += 1
        if calls["n"] in (2, 4):                              # economic redispatch and the first neighbourhood decode
            t = ctx.shift(date(2026, 10, 26), 1).regular_windows[0][0]
            forged = PhasePlacement(deferred_phase.key.id, deferred_phase.job, deferred_phase.line, PhaseKind.WORK, t, t + deferred_phase.work_minutes,
                                    ((t, t + deferred_phase.work_minutes, False),), "DEMO:2026-10-26:S1", None,
                                    (P("DEMO-A1-07"), P("DEMO-A1-08")), "FORGED", False, False, None)
            result.placements[deferred_phase.key.id] = forged
            result.deferred.pop(deferred_phase.key.id, None)    # one fewer deferred phase: strictly better on the key
        return result

    monkeypatch.setattr(MaxOptimizer, "decode", corrupting)
    run = MaxOptimizer(ctx, budget_seconds=30, max_moves=3).run()
    assert run.seed_valid and run.best_valid and len(run.validator_rejections) >= 1
    assert all("V5" in r or "E_CAPABILITY" in r for r in run.validator_rejections)
    assert deferred_phase.key.id not in run.best.decode.placements                  # rolled back, never retained


def test_cli_validate_in_fresh_processes(tmp_path, app_root) -> None:
    import subprocess
    import sys

    env = {"FOCUS_RUNTIME_DIR": str(tmp_path / "rt"), "PATH": "/usr/bin:/bin"}

    def cli(*args: str) -> tuple[int, dict]:
        out = subprocess.run([sys.executable, "-m", "focus.cli", *args], cwd=app_root, env=env, capture_output=True, text=True, timeout=300)
        return out.returncode, json.loads(out.stdout)

    run_id = cli("acquire", "--mode", "fixture", "--scenario", "manufacturing_demo")[1]["source_run_id"]
    cli("demo-bootstrap", "--scenario", "manufacturing_demo")
    code, sched = cli("schedule", "--source-run", run_id, "--policy", "rebuild-v1", "--purpose", "EXECUTABLE", "--budget-seconds", "3")
    assert code == 0 and sched["publishable"] is True and sched["validation"]["valid"] is True
    code, result = cli("validate", "--candidate", sched["candidate_id"], "--all-required")
    assert code == 0 and result["valid"] is True and result["publishable"] is True and not result["required_skips"]
    assert all(n > 0 for n in result["check_counts"].values())                        # the stored incumbent was reloaded: no skips
    code, missing = cli("validate", "--candidate", "CAND-does-not-exist", "--all-required")
    assert code == 2 and missing["error"]["code"] == "CANDIDATE_NOT_FOUND"
