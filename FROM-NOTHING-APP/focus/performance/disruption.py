"""Disruption claims, automatic attribution and reviewed excusal decisions (S14 DISRUPTION TAXONOMY, P17).

**Taxonomy.** Exactly eight causes. Exactly five are nominally excusable: LATE_PART,
CROSS_TEAM_PREDECESSOR, CAPACITY_SHORTAGE, SKILL_SHORTAGE and REWORK_INJECTION. The policy must
carry the same lists as the contract taxonomy, or nothing is admitted.

**Claims are evidence, never relief.**

* A claim names frozen earning units of one goal, a cause, origin and executing teams, evidence
  references and notes.
* SAME_TEAM_PREDECESSOR and DURATION_OVERRUN need notes. Automatic attribution of those causes
  only *proposes* a claim (status PROPOSED), which a person must submit with notes before review.
* There is no automatic 3x overrun allowance at any burn ratio.
* A REWORK_INJECTION origin team is admitted only from explicit defect-origin evidence: the
  admitted rework classification, or a ``defect_evidence`` row. It never comes from the DAG
  direction or the executing team.
* Duplicate and overlapping claims are kept as separate records.

**Decisions are reviewed.**

* Only an ``excusal_decider`` authority decides, and never on its own claim.
* PLAN_CHURN is reviewed on its own track and never mixed with other causes.
* Approving a cause outside the five is a recorded exception with a substantive reason.
* An appeal appends a superseding decision; the earlier decision stays in history.

**Relief is a union.** Relief (E) is the union of approved units that are still missed at the
evaluation cutoff. An earned unit is never relief. Overlapping approvals and multiple causes
cannot multiply relief beyond the frozen missed effort.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from typing import Any, Mapping

from ..contracts.events import (DISRUPTION_CAUSES, NOMINALLY_EXCUSABLE, DisruptionClaim, ExcusalDecision)
from ..data.duration_gauge import minutes_of, to_micros, union_length
from ..domain import DomainError, PhaseKind, aware_utc, digest_id

SYSTEM_ATTRIBUTOR = "focus-attribution"
EVIDENCE_REQUIRED = frozenset({"LATE_PART", "CROSS_TEAM_PREDECESSOR", "CAPACITY_SHORTAGE", "SKILL_SHORTAGE", "REWORK_INJECTION", "PLAN_CHURN"})


def taxonomy_errors(policy: Any) -> list[str]:
    """The policy may configure review, never redefine the user's exact taxonomy."""
    out = []
    if tuple(policy.seq("disruption.causes")) != DISRUPTION_CAUSES:
        out.append("POLICY_TAXONOMY_MISMATCH: disruption.causes must be exactly the eight S14 causes")
    if frozenset(policy.seq("disruption.nominally_excusable")) != NOMINALLY_EXCUSABLE or len(policy.seq("disruption.nominally_excusable")) != 5:
        out.append("POLICY_TAXONOMY_MISMATCH: disruption.nominally_excusable must be exactly the five S14 categories")
    if policy.str("disruption.overrun_auto_excuse") != "NEVER":
        out.append("POLICY_TAXONOMY_MISMATCH: no automatic overrun excuse may be configured")
    return out


class Intake:
    """Validated admission of claims and decisions (feed rows, commands and automatic attribution share these rules)."""

    def __init__(self, services: Any, as_of: datetime, store: Any, *, registry: Any = None, view: Any = None, graph: Any = "current"):
        from .lifecycle import GoalRegistry, ObservationView, current_graph

        self.services = services
        self.policy = services.policy
        self.as_of = aware_utc(as_of, "as_of")
        self.store = store
        self.scope = services.settings.scope_key
        self.site = services.settings.site
        self.registry = registry or GoalRegistry.load(services)
        self.graph = current_graph(services) if graph == "current" else graph
        self._view = view
        self._view_cls = ObservationView

    @property
    def view(self) -> Any:
        if self._view is None:
            self._view = self._view_cls.build(self.services, self.as_of, store=self.store, registry=self.registry, graph=self.graph)
        return self._view

    def _fail(self, code: str, detail: str) -> None:
        from .actuals import RowError

        raise RowError(code, detail)

    # ------------------------------------------------------------------ claims
    def admit_claim(self, *, claim_id: str, goal_id: str, units: tuple[str, ...], cause: str, origin_team: str | None, executing_team: str | None,
                    evidence: tuple[str, ...], notes: str, status: str, attribution: str, reported_by: str, reported_at: datetime,
                    source_key: str, supersedes: str | None) -> DisruptionClaim:
        from .actuals import CLAIMS

        for err in taxonomy_errors(self.policy):
            self._fail("POLICY_TAXONOMY_MISMATCH", err)
        if cause not in DISRUPTION_CAUSES:
            self._fail("BAD_CAUSE", f"unknown disruption cause {cause!r}")
        record = self.view.record_by_id(goal_id)
        if record is None:
            self._fail("GOAL_NOT_FOUND", f"{goal_id} is not a goal of record")
        known = {u.id: u for u in record.units}
        missing = [u for u in units if u not in known]
        if missing:
            self._fail("UNIT_NOT_IN_GOAL", f"{len(missing)} unit(s) are not frozen units of {goal_id}")
        earned = [u for u in units if u in self.view.credits]
        if earned and status != "WITHDRAWN":
            self._fail("UNIT_ALREADY_EARNED", f"{len(earned)} unit(s) are already verified complete; earned work is never relief")
        if not reported_by:
            self._fail("REPORTER_REQUIRED", "a claim names who reported it")
        if status == "SUBMITTED":
            if cause in self.policy.seq("disruption.notes_required") and len(notes.strip()) < self.policy.int("disruption.notes_min_chars"):
                self._fail("NOTES_REQUIRED", f"{cause} claims need substantive notes for review")
            if cause in EVIDENCE_REQUIRED and not evidence:
                self._fail("EVIDENCE_REQUIRED", f"{cause} claims cite explicit source evidence")
        team = record.team
        if cause == "CROSS_TEAM_PREDECESSOR" and (not origin_team or origin_team == team):
            self._fail("CROSS_TEAM_SAME_TEAM", "a cross-team predecessor claim names a predecessor team different from the goal's team")
        if cause == "SAME_TEAM_PREDECESSOR" and origin_team not in (None, team):
            self._fail("SAME_TEAM_CROSS_TEAM", "a same-team predecessor claim cannot name another team as origin")
        if cause == "REWORK_INJECTION" and origin_team is not None and not self._origin_evidenced(origin_team, evidence):
            self._fail("ORIGIN_TEAM_UNEVIDENCED", "a rework origin team must come from explicit defect-origin evidence cited by the claim")
        if supersedes is not None:
            prior = self.store.get(CLAIMS, supersedes)
            if prior is None or prior.goal_id != goal_id:
                self._fail("SUPERSEDED_CLAIM_NOT_FOUND", "a revised or withdrawn claim supersedes a claim of the same goal")
        try:
            return DisruptionClaim(claim_id, self.scope, goal_id, tuple(units), cause, origin_team, executing_team, tuple(evidence), notes[:2000], status,
                                   attribution, reported_by, aware_utc(reported_at, "reported_at"), self.as_of, source_key, supersedes)
        except DomainError as exc:
            self._fail(exc.code, exc.message)
            raise  # pragma: no cover

    def _origin_evidenced(self, origin_team: str, evidence: tuple[str, ...]) -> bool:
        cited = set(evidence)
        for d in self.view.defects:
            if d.origin_team == origin_team and ({d.id, d.evidence_ref, f"DEFECT:{d.evidence_ref}"} & cited):
                return True
        graph = self.graph
        if graph is not None:
            for child, (team, _) in graph.rework_origin.items():
                if team == origin_team and ({f"REWORK_LINK:{child}", child} & cited):
                    return True
        return False

    def claim_from_row(self, row: Mapping[str, Any], ident: Any) -> DisruptionClaim:
        from .actuals import _date, _int, _json_list, _opt_text, _text, _utc, parse_work_refs, shift_id_of

        key = _text(row, "CLAIM_KEY")
        team = _text(row, "TEAM")
        shift_id = shift_id_of(ident.site, _date(row, "SHIFT_DATE"), _int(row, "SHIFT_NUMBER", 1))
        goal_id = self.registry.goal_id_for(team, shift_id)
        if goal_id is None:
            self._fail("GOAL_NOT_FOUND", f"no accepted obligation plan covers {team} {shift_id}")
        units = self._units_for(goal_id, parse_work_refs(_json_list(row, "WORK_REFS")), ident)
        status = _text(row, "STATUS")
        if status not in ("SUBMITTED", "WITHDRAWN"):
            self._fail("BAD_CLAIM_STATUS", "a source claim is SUBMITTED or WITHDRAWN")
        evidence = tuple(str(x) for x in _json_list(row, "EVIDENCE_REFS"))
        target = _opt_text(row, "SUPERSEDES_CLAIM_KEY")
        return self.admit_claim(
            claim_id=digest_id("DCL", self.scope, "feed", key), goal_id=goal_id, units=units, cause=_text(row, "CAUSE"),
            origin_team=_opt_text(row, "ORIGIN_TEAM"), executing_team=_opt_text(row, "EXECUTING_TEAM"), evidence=evidence,
            notes=_opt_text(row, "NOTES") or "", status=status, attribution="MANUAL", reported_by=_text(row, "REPORTED_BY"),
            reported_at=_utc(row, "REPORTED_AT_UTC"), source_key=f"disruption_claims:{key}",
            supersedes=None if target is None else digest_id("DCL", self.scope, "feed", target))

    def _units_for(self, goal_id: str, refs: tuple[Any, ...], ident: Any) -> tuple[str, ...]:
        record = self.view.record_by_id(goal_id)
        if record is None:
            self._fail("GOAL_NOT_FOUND", f"{goal_id} is not a goal of record")
        if not refs:
            self._fail("NO_UNITS", "name at least one missed frozen unit")
        out: list[str] = []
        for ref in refs:
            phase = ident.ref_phase(ref)
            matched = [u.id for u in record.units if u.phase == phase and (ref.segment is None or u.segment == ref.segment)]
            if not matched:
                self._fail("UNIT_NOT_IN_GOAL", f"{ref.line}/{ref.soi}/{ref.kind.value}/{ref.ordinal} is not frozen in this goal")
            out.extend(matched)
        return tuple(dict.fromkeys(out))

    # ------------------------------------------------------------------ decisions
    def admit_decision(self, *, decision_id: str, claim_ids: tuple[str, ...], decision: str, approved_units: tuple[str, ...], authority: str,
                       authority_role: str, decision_at: datetime, reason: str, review_track: str | None, supersedes: str | None,
                       source_key: str, goal_hint: str | None = None, expected_version: int | None = None) -> ExcusalDecision:
        from .actuals import CLAIMS, DECISIONS

        for err in taxonomy_errors(self.policy):
            self._fail("POLICY_TAXONOMY_MISMATCH", err)
        claims = [self.store.get(CLAIMS, cid) for cid in claim_ids]
        if not claim_ids or any(c is None or c.ingested_at > self.as_of for c in claims):
            self._fail("CLAIM_NOT_FOUND", "every reviewed claim must already be recorded")
        goals = {c.goal_id for c in claims}
        if len(goals) != 1 or (goal_hint is not None and goal_hint not in goals):
            self._fail("CLAIMS_ACROSS_GOALS", "one decision reviews claims of exactly one goal")
        goal_id = next(iter(goals))
        effective = self.effective_claims(goal_id)
        if any(effective.get(c.id) is None or effective[c.id].status != "SUBMITTED" for c in claims):
            self._fail("CLAIM_NOT_SUBMITTED", "only submitted (not proposed, withdrawn or superseded) claims are reviewed")
        causes = {c.cause for c in claims}
        churn = "PLAN_CHURN" in causes
        if churn and len(causes) > 1:
            self._fail("PLAN_CHURN_SEPARATE_REVIEW", "PLAN_CHURN claims are reviewed separately from other causes")
        track = review_track or ("PLAN_CHURN" if churn else "STANDARD")
        if (track == "PLAN_CHURN") != churn:
            self._fail("PLAN_CHURN_SEPARATE_REVIEW", "the PLAN_CHURN review track holds exactly the PLAN_CHURN claims")
        if authority_role not in self.policy.seq("disruption.reviewer_roles"):
            self._fail("AUTHORITY_NOT_REVIEWER", f"role {authority_role!r} cannot decide excusals")
        if not self.policy.bool("disruption.self_review_allowed") and any(c.reported_by == authority for c in claims):
            self._fail("SELF_REVIEW", "a claimant cannot decide their own claim")
        if decision not in ("APPROVED", "REJECTED", "PENDING"):
            self._fail("BAD_DECISION", f"unknown decision {decision!r}")
        claimed = {u for c in claims for u in c.units}
        if decision == "APPROVED":
            if not approved_units:
                self._fail("APPROVAL_WITHOUT_UNITS", "an approval names the missed units it relieves")
            if set(approved_units) - claimed:
                self._fail("APPROVED_UNIT_NOT_CLAIMED", "approved units must come from the reviewed claims")
            if set(approved_units) & set(self.view.credits):
                self._fail("UNIT_ALREADY_EARNED", "earned units are never relieved")
        elif approved_units:
            self._fail("UNITS_WITHOUT_APPROVAL", "only an approval names approved units")
        exception = decision == "APPROVED" and not causes <= NOMINALLY_EXCUSABLE
        minimum = self.policy.int("disruption.notes_min_chars")
        if len(reason.strip()) < (minimum if exception or decision == "REJECTED" else 1):
            self._fail("REASON_REQUIRED", "a decision records its reason (substantive for rejections and exception approvals)")
        known = [d for d in self.store.all(DECISIONS) if d.goal_id == goal_id and d.ingested_at <= self.as_of]
        if expected_version is not None and expected_version != len(known):
            self._fail("STALE_VERSION", f"decision state of {goal_id} is at version {len(known)}, not {expected_version}")
        if supersedes is not None:
            prior = self.store.get(DECISIONS, supersedes)
            if prior is None or prior.goal_id != goal_id:
                self._fail("SUPERSEDED_DECISION_NOT_FOUND", "an appeal supersedes a decision of the same goal")
            if any(d.supersedes == supersedes and d.id != decision_id for d in known):
                self._fail("DECISION_ALREADY_SUPERSEDED", "that decision was already superseded; appeal the current one")
        try:
            return ExcusalDecision(decision_id, self.scope, goal_id, tuple(claim_ids), tuple(approved_units), decision, authority, authority_role,
                                   aware_utc(decision_at, "decision_at"), self.policy.id, reason[:2000], track, exception, supersedes, self.as_of,
                                   source_key)
        except DomainError as exc:
            self._fail(exc.code, exc.message)
            raise  # pragma: no cover

    def decision_from_row(self, row: Mapping[str, Any], ident: Any) -> ExcusalDecision:
        from .actuals import CLAIMS, _date, _int, _json_list, _opt_text, _text, _utc, parse_work_refs, shift_id_of

        key = _text(row, "DECISION_KEY")
        team = _text(row, "TEAM")
        shift_id = shift_id_of(ident.site, _date(row, "SHIFT_DATE"), _int(row, "SHIFT_NUMBER", 1))
        goal_id = self.registry.goal_id_for(team, shift_id)
        if goal_id is None:
            self._fail("GOAL_NOT_FOUND", f"no accepted obligation plan covers {team} {shift_id}")
        claim_ids = []
        for ref in _json_list(row, "CLAIM_KEYS"):
            if not isinstance(ref, str) or not ref:
                self._fail("BAD_CLAIM_REF", "CLAIM_KEYS holds claim keys or claim ids")
            claim_ids.append(ref if self.store.get(CLAIMS, ref) is not None else digest_id("DCL", self.scope, "feed", ref))
        decision = _text(row, "DECISION")
        refs = parse_work_refs(_json_list(row, "APPROVED_WORK_REFS"))
        approved = self._units_for(goal_id, refs, ident) if refs else ()
        target = _opt_text(row, "SUPERSEDES_DECISION_KEY")
        return self.admit_decision(
            decision_id=digest_id("DXD", self.scope, "feed", key), claim_ids=tuple(claim_ids), decision=decision, approved_units=approved,
            authority=_text(row, "AUTHORITY"), authority_role=_text(row, "AUTHORITY_ROLE"), decision_at=_utc(row, "DECISION_AT_UTC"),
            reason=_opt_text(row, "REASON") or "", review_track=_opt_text(row, "REVIEW_TRACK"),
            supersedes=None if target is None else digest_id("DXD", self.scope, "feed", target), source_key=f"excusal_decisions:{key}", goal_hint=goal_id)

    def effective_claims(self, goal_id: str) -> dict[str, DisruptionClaim]:
        """Each claim id -> the latest claim of its supersession chain (a withdrawal or revision replaces the claim it supersedes)."""
        return effective_claims(self.view, goal_id, store_claims=[c for c in _all_claims(self.store) if c.ingested_at <= self.as_of])


def _all_claims(store: Any) -> list[DisruptionClaim]:
    from .actuals import CLAIMS

    return store.all(CLAIMS)


def effective_claims(view: Any, goal_id: str, *, store_claims: list[DisruptionClaim] | None = None) -> dict[str, DisruptionClaim]:
    claims = [c for c in (store_claims if store_claims is not None else view.claims) if c.goal_id == goal_id]
    successor: dict[str, DisruptionClaim] = {}
    for c in sorted(claims, key=lambda x: (x.reported_at, x.ingested_at, x.id)):
        if c.supersedes:
            successor[c.supersedes] = c
    out: dict[str, DisruptionClaim] = {}
    for c in claims:
        cur, seen = c, {c.id}
        while cur.id in successor and successor[cur.id].id not in seen:
            cur = successor[cur.id]
            seen.add(cur.id)
        out[c.id] = cur
    return out


def effective_decisions(view: Any, goal_id: str) -> list[ExcusalDecision]:
    decisions = [d for d in view.decisions if d.goal_id == goal_id]
    superseded = {d.supersedes for d in decisions if d.supersedes}
    return sorted((d for d in decisions if d.id not in superseded), key=lambda d: (d.decision_at, d.id))


def approved_units(view: Any, goal_id: str) -> set[str]:
    """Union of units approved by effective decisions whose reviewed claims are still submitted (withdrawn claims lapse)."""
    claims = effective_claims(view, goal_id)
    out: set[str] = set()
    for d in effective_decisions(view, goal_id):
        if d.decision != "APPROVED":
            continue
        live = [claims.get(cid) for cid in d.claim_ids]
        if any(c is not None and c.status == "SUBMITTED" for c in live):
            out.update(d.approved_units)
    return out


def relief_summary(view: Any, record: Any, facts: list[Any]) -> dict[str, Any]:
    """Claimed effort (sum over claims) versus relief (union of approved missed units), for display and audit."""
    effort = {f.unit_id: f.effort for f in facts}
    claims = [c for c in effective_claims(view, record.goal_id).values() if c.status == "SUBMITTED"]
    unique_claims = {c.id: c for c in claims}
    claimed_sum = sum(effort.get(u, 0) for c in unique_claims.values() for u in c.units)
    relief = {f.unit_id for f in facts if f.relief}
    pending = [c.id for c in unique_claims.values() if not any(c.id in d.claim_ids for d in effective_decisions(view, record.goal_id))]
    proposed = sorted({c.id for c in effective_claims(view, record.goal_id).values() if c.status == "PROPOSED"})
    return {"claimed_effort_sum": claimed_sum, "relief_effort": sum(effort[u] for u in relief), "relief_units": sorted(relief),
            "missed_effort": sum(f.effort for f in facts if f.status == "MISSED"), "pending_claims": sorted(pending), "proposed_claims": proposed,
            "by_cause": _by_cause(unique_claims.values(), effort)}


def _by_cause(claims: Any, effort: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for c in claims:
        out[c.cause] += sum(effort.get(u, 0) for u in c.units)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------- automatic attribution (claims only)

def phase_burn_minutes(intervals: list[Any], phase: str) -> Fraction:
    """Actual person-effort burn of a phase: each owner's running-max union, summed (S08 raw clock island method)."""
    per_owner: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for i in intervals:
        if i.phase == phase:
            per_owner[i.person].append((to_micros(i.start), to_micros(i.end)))
    return sum((minutes_of(union_length(v)) for v in per_owner.values()), Fraction(0))


def _phase_starts(view: Any) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for record in view.records():
        for u in record.units:
            t = view.registry.unit_start.get(u.id)
            if t is not None and (u.phase not in out or t < out[u.phase]):
                out[u.phase] = t
    return out


def culpable_blockers(graph: Any, sat: Any, starts: dict[str, datetime], node: str, by: datetime, trail: frozenset[str] = frozenset()) -> list[str]:
    """The prerequisites that were ready yet still incomplete when needed.

    A predecessor that was itself blocked passes the blame on to its own late prerequisite, so the
    immediate DAG neighbour is never presumed culpable (S14: DAG direction does not prove
    culpability).
    """
    if node in trail:
        return []
    ready = sat.at(node)
    if ready is not None and ready <= by:
        return []
    if node in graph.releases:
        return [node]
    phase = graph.phases.get(node)
    if phase is None:
        return [node]
    preds = graph.hard_in.get(node, [])
    if phase.kind is PhaseKind.RELEASE:
        return [b for e in preds for b in culpable_blockers(graph, sat, starts, e.pred_phase, by, trail | {node})]
    own = starts.get(node, by)
    late = [b for e in preds for b in culpable_blockers(graph, sat, starts, e.pred_phase, own, trail | {node})]
    return late or [node]


def auto_attribute(services: Any, view: Any) -> list[DisruptionClaim]:
    """Claims from explicit evidence for every closed goal with missed units. Never a decision, never relief."""
    from .lifecycle import Satisfaction

    if taxonomy_errors(view.policy):
        return []
    graph = view.graph
    sat = Satisfaction(graph, view.lifecycles) if graph is not None else None
    starts = _phase_starts(view)
    out: list[DisruptionClaim] = []
    for record in view.records():
        if record.window.credit_deadline > view.as_of:
            continue
        facts = view.unit_facts(record)
        missed = [f for f in facts if f.status == "MISSED"]
        if not missed:
            continue
        groups: dict[tuple[str, str | None, str | None, tuple[str, ...], str], list[str]] = defaultdict(list)
        cancel_events = {x.id for x in view.effective if x.kind in ("CANCELLED", "DETABBED")}
        for f in missed:
            start = view.registry.unit_start.get(f.unit_id, record.window.start)
            cycle = view.lifecycles.get(f.phase)
            if _churned(view, f):
                evidence = tuple(sorted(f"EVENT:{e}" for e in cycle.events if e in cancel_events))
                groups[("PLAN_CHURN", None, None, evidence, "SUBMITTED")].append(f.unit_id)
                continue
            if graph is None or sat is None:
                continue
            for edge in graph.hard_in.get(f.phase, []):
                for leaf in culpable_blockers(graph, sat, starts, edge.pred_phase, start):
                    rel = graph.releases.get(leaf)
                    if rel is not None:
                        if rel.kind.value == "PART":
                            groups[("LATE_PART", None, f.team, (f"RELEASE:{rel.id}",), "SUBMITTED")].append(f.unit_id)
                        continue
                    pred_team = graph.team(leaf)
                    if pred_team is None:
                        continue
                    if pred_team != f.team:
                        groups[("CROSS_TEAM_PREDECESSOR", pred_team, pred_team, (f"PHASE:{leaf}",), "SUBMITTED")].append(f.unit_id)
                    else:
                        groups[("SAME_TEAM_PREDECESSOR", None, pred_team, (f"PHASE:{leaf}",), "PROPOSED")].append(f.unit_id)
        missed_ids = [f.unit_id for f in missed if not _churned(view, f)]
        for job, origin, executing, evidence in _injected_rework(view, record, graph):
            if missed_ids:
                groups[("REWORK_INJECTION", origin, executing, evidence, "SUBMITTED" if origin else "PROPOSED")].extend(missed_ids)
        frozen: dict[str, int] = defaultdict(int)
        for f in facts:
            if f.origin == "ORIGINAL":
                frozen[f.phase] += f.effort
        for phase, std in sorted(frozen.items()):
            burn = phase_burn_minutes(view.intervals, phase)
            others = [u for u in missed_ids if u not in {f.unit_id for f in facts if f.phase == phase}]
            if std > 0 and burn > std and others:
                ratio = burn / std
                evidence = (f"BURN:{phase}:{burn}/{std}:ratio={float(ratio):.2f}",)
                groups[("DURATION_OVERRUN", None, record.team, evidence, "PROPOSED")].extend(others)
        out.extend(_auto_revisions(view, record, groups))
    return out


def _auto_revisions(view: Any, record: Any, groups: dict[tuple[str, str | None, str | None, tuple[str, ...], str], list[str]]) -> list[DisruptionClaim]:
    """Automatic claims for one goal, as revisions of earlier automatic attributions with the same cause/origin/evidence.

    A changed unit set supersedes the earlier claim; an attribution whose evidence no longer
    applies is withdrawn by an appended WITHDRAWN revision. A person's revision of an automatic
    claim is never overridden.
    """
    effective = effective_claims(view, record.goal_id)
    current: dict[tuple[str, str | None, tuple[str, ...]], DisruptionClaim] = {}
    for c in view.claims:
        latest = effective.get(c.id)
        if c.goal_id == record.goal_id and c.attribution == "AUTO" and latest is not None and latest.attribution == "AUTO" and latest.status != "WITHDRAWN":
            current[(latest.cause, latest.origin_team, tuple(sorted(latest.evidence)))] = latest
    out: list[DisruptionClaim] = []
    seen: set[tuple[str, str | None, tuple[str, ...]]] = set()
    for (cause, origin, executing, evidence, status), units in sorted(groups.items(), key=lambda kv: repr(kv[0])):
        units = list(dict.fromkeys(units))
        if not units:
            continue
        key = (cause, origin, tuple(sorted(evidence)))
        seen.add(key)
        prior = current.get(key)
        if prior is not None and sorted(prior.units) == sorted(units) and prior.status == status:
            continue
        cid = digest_id("DCL", view.scope, "auto", record.goal_id, cause, origin, sorted(units), sorted(evidence))
        note = "" if status == "SUBMITTED" else "auto-proposed: needs a person's notes (or origin evidence) before review"
        try:
            out.append(DisruptionClaim(cid, view.scope, record.goal_id, tuple(units), cause, origin, executing, tuple(sorted(evidence)), note, status,
                                       "AUTO", SYSTEM_ATTRIBUTOR, record.window.credit_deadline, view.as_of, f"auto:{cid}",
                                       None if prior is None or prior.id == cid else prior.id))
        except DomainError:
            continue
    for key, prior in sorted(current.items(), key=lambda kv: kv[1].id):
        if key in seen:
            continue
        wid = digest_id("DCL", view.scope, "auto-withdraw", prior.id)
        out.append(DisruptionClaim(wid, view.scope, record.goal_id, prior.units, prior.cause, prior.origin_team, prior.executing_team, prior.evidence,
                                   "evidence no longer supports this automatic attribution", "WITHDRAWN", "AUTO", SYSTEM_ATTRIBUTOR,
                                   prior.reported_at, view.as_of, f"auto:{wid}", prior.id))
    return out


def _churned(view: Any, fact: Any) -> bool:
    cycle = view.lifecycles.get(fact.phase)
    return cycle is not None and cycle.disposition in ("CANCELLED", "DETABBED") and cycle.completed_at is None


def _injected_rework(view: Any, record: Any, graph: Any) -> list[tuple[str, str | None, str, tuple[str, ...]]]:
    """Rework worked in this team-shift outside B0 (approved additions or offplan output).

    The origin team is taken only from explicit defect-origin evidence; without it the origin stays
    unknown and the claim is only proposed.
    """
    jobs: dict[str, str] = {}
    for u in record.addition_units:
        jobs[u.job] = u.team
    for o in view.offplan_for(record.team, record.shift_id):
        jobs.setdefault(o.job, o.team)
    defects = {d.job: d for d in view.defects}
    out = []
    for job, executing in sorted(jobs.items()):
        d = defects.get(job)
        if d is not None:
            out.append((job, d.origin_team, executing, (f"DEFECT:{d.evidence_ref}", d.id)))
        elif graph is not None and job in graph.rework_origin:
            out.append((job, graph.rework_origin[job][0], executing, (f"REWORK_LINK:{job}",)))
        elif graph is not None and job in graph.rework_jobs:
            out.append((job, None, executing, (f"REWORK_LINK:{job}",)))
    return out


# ---------------------------------------------------------------------- commands (worker handlers)

def submit_claim(services: Any, *, request_id: str, requested_by: str, goal_id: str, unit_ids: list[str], cause: str, evidence: list[str],
                 notes: str, origin_team: str | None = None, executing_team: str | None = None, supersedes: str | None = None,
                 at: datetime | None = None) -> DisruptionClaim:
    from .actuals import CLAIMS, ObservationStore, RowError

    at = aware_utc(at or services.clock.now(), "at")
    store = ObservationStore(services.repositories, services.settings.scope_key)
    intake = Intake(services, at, store)
    try:
        claim = intake.admit_claim(claim_id=digest_id("DCL", intake.scope, "cmd", request_id), goal_id=goal_id, units=tuple(unit_ids), cause=cause,
                                   origin_team=origin_team, executing_team=executing_team, evidence=tuple(evidence), notes=notes, status="SUBMITTED",
                                   attribution="MANUAL", reported_by=requested_by, reported_at=at, source_key=f"command:{request_id}",
                                   supersedes=supersedes)
    except RowError as exc:
        raise DomainError(exc.code, exc.detail) from None
    if store.put(CLAIMS, claim) == "CONFLICT":
        raise DomainError("REVISION_CONTENT_CONFLICT", "this request id was already recorded with different content")
    return claim


def decide(services: Any, *, request_id: str, authority: str, authority_role: str, claim_ids: list[str], decision: str, unit_ids: list[str],
           reason: str, review_track: str | None = None, supersedes: str | None = None, expected_version: int | None = None,
           at: datetime | None = None) -> ExcusalDecision:
    from .actuals import DECISIONS, ObservationStore, RowError

    at = aware_utc(at or services.clock.now(), "at")
    store = ObservationStore(services.repositories, services.settings.scope_key)
    intake = Intake(services, at, store)
    with services.repositories.store.lock(f"excusal-{intake.scope}"):
        try:
            made = intake.admit_decision(decision_id=digest_id("DXD", intake.scope, "cmd", request_id), claim_ids=tuple(claim_ids), decision=decision,
                                         approved_units=tuple(unit_ids), authority=authority, authority_role=authority_role, decision_at=at,
                                         reason=reason, review_track=review_track, supersedes=supersedes, source_key=f"command:{request_id}",
                                         expected_version=expected_version)
        except RowError as exc:
            raise DomainError(exc.code, exc.detail) from None
        if store.put(DECISIONS, made) == "CONFLICT":
            raise DomainError("REVISION_CONTENT_CONFLICT", "this request id was already recorded with different content")
    return made


def worker_claim(services: Any, run: Any) -> dict[str, Any]:
    req = run.request
    claim = submit_claim(services, request_id=run.id, requested_by=run.requested_by, goal_id=req.get("goal_id", ""), unit_ids=list(req.get("unit_ids", [])),
                         cause=req.get("cause", ""), evidence=[str(x) for x in req.get("evidence_ids", [])], notes=str(req.get("notes", "")),
                         origin_team=req.get("origin_team"), executing_team=req.get("executing_team"), supersedes=req.get("supersedes"),
                         at=run.created_at)
    return {"status": "recorded", "claim_id": claim.id, "claim_status": claim.status, "relief": "none until a reviewed decision approves units"}


def worker_decision(services: Any, run: Any) -> dict[str, Any]:
    req = run.request
    made = decide(services, request_id=run.id, authority=run.requested_by, authority_role=str(req.get("authority_role", "")),
                  claim_ids=list(req.get("claim_ids", [])), decision=str(req.get("decision", "")), unit_ids=list(req.get("unit_ids", [])),
                  reason=str(req.get("notes", "")), review_track=req.get("review_track"), supersedes=req.get("supersedes"),
                  expected_version=req.get("expected_version"), at=run.created_at)
    return {"status": "recorded", "decision_id": made.id, "decision": made.decision, "exception": made.exception, "review_track": made.review_track}
