"""The web's canonical heads, read from display parts only (never a snapshot or a plan artifact).

``DisplayCanon.load`` verifies the current source run's snapshot manifest and each plan head's signed display receipt
(``store``), and loads only their summaries. A projection then asks for the slice it needs:

* ``lines([n])``: the aircraft's jobs, dispositions and shortages, and each plan's job outcomes for them;
* ``jobs([job])``: the job buckets' phases, graph neighborhood, releases, durations, requirements and shortages, and
  each plan's assignments and outcomes of those jobs;
* ``pools(teams, days)``: the pools and availability of those team-days;
* ``work(teams, lines)``: those teams' phases on those aircraft with the records the dispatch rule reads;
* ``holds()``: every inspection-hold phase, and each plan's assignments of them;
* ``team_days(purpose, teams, days)``: a plan's assignments and duties of those team-days, with the plan snapshot's
  phases and jobs for them.

A slice is another ``DisplayCanon`` over the same verified heads. Its ``snapshot`` and ``plan(purpose)`` expose the
summary fields and exactly the collections loaded; reading any other collection raises ``NotInSlice``, so a projection
can never mistake a partial collection for the whole fleet.

**Labels from the plan's own snapshot.** Phases and jobs that come with a plan part (``team_days``, the commitments)
are the plan snapshot's records. When the source head is the plan's snapshot, which is the case after every pipeline,
they are the current records. When a newer source run is admitted before the next plan, the plan's rows keep the
labels and teams they were planned with; current facts (dispositions, pools, dispatch reasons) still come from the
current snapshot's parts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from .records import (DisplayManifest, PlanCommitments, PlanGoals, PlanHolds, PlanJobs, PlanLine, PlanSummary, PlanTeamDay, SnapshotHolds,
                      SnapshotJobs, SnapshotLabels, SnapshotLine, SnapshotPools, SnapshotReleases, SnapshotStandards, SnapshotSummary,
                      SnapshotWork, LineGraph, bucket_of, job_of_phase, unreceived_ancestors)
from .store import VerifiedPlan, load_part

PURPOSES = ("EXECUTABLE", "FORECAST")


class NotInSlice(AttributeError):
    """A projection read a collection its slice did not load."""


# ---------------------------------------------------------------------- slices

_SNAPSHOT_SUMMARY = {"source_run_id", "scope_key", "site", "program", "cutoff", "created_at", "policy_id", "policy_hash", "manifest_hash",
                     "census", "cs_facts", "calendars", "economics", "gaps", "people", "holdings", "inference", "carry_in", "shift_ids"}
_SNAPSHOT_SLICED = ("jobs", "dispositions", "phases", "edges", "releases", "durations", "requirements", "shortages", "pools", "availability")


class SnapshotSlice:
    """The admitted snapshot's summary fields and the collections one projection loaded (in snapshot order)."""

    def __init__(self, summary: SnapshotSummary, collections: dict[str, tuple[Any, ...]] | None = None):
        self.summary = summary
        self._c = collections or {}

    @property
    def id(self) -> str:
        return self.summary.snapshot_id

    @property
    def hash(self) -> str:
        return self.summary.snapshot_hash

    def loaded(self, name: str) -> bool:
        return name in self._c

    def __getattr__(self, name: str) -> Any:
        if name in _SNAPSHOT_SUMMARY:
            return getattr(self.summary, name)
        if name in _SNAPSHOT_SLICED:
            found = self._c.get(name)
            if found is None:
                raise NotInSlice(f"this projection's snapshot slice did not load {name}")
            return found
        raise AttributeError(name)


_PLAN_SUMMARY = {"snapshot_id": "snapshot_id", "snapshot_hash": "snapshot_hash", "policy_hash": "policy_hash", "source_run_id": "source_run_id",
                 "scope": "scope", "purpose": "purpose", "created_at": "created_at", "incumbent_id": "incumbent_id", "hash": "candidate_hash",
                 "costs": "costs", "algorithm_status": "algorithm_status", "global_optimality_proven": "global_optimality_proven",
                 "completion": "completion"}
_PLAN_SLICED = ("assignments", "duties", "job_dispositions", "commitment_results")


class PlanSlice:
    """An accepted plan's summary fields and the collections one projection loaded."""

    def __init__(self, summary: PlanSummary, collections: dict[str, tuple[Any, ...]] | None = None):
        self.summary = summary
        self._c = collections or {}

    @property
    def id(self) -> str:
        return self.summary.candidate_id

    def loaded(self, name: str) -> bool:
        return name in self._c

    def __getattr__(self, name: str) -> Any:
        if name in _PLAN_SUMMARY:
            return getattr(self.summary, _PLAN_SUMMARY[name])
        if name in _PLAN_SLICED:
            found = self._c.get(name)
            if found is None:
                raise NotInSlice(f"this projection's plan slice did not load {name}")
            return found
        raise AttributeError(name)


@dataclass(frozen=True)
class ArtifactView:
    """The parts of an accepted artifact a projection reads, from its display summary."""

    candidate: PlanSlice
    summary: PlanSummary

    @property
    def obligation_dates(self) -> tuple[date, ...]:
        return self.summary.obligation_dates

    @property
    def capability(self) -> str:
        return self.summary.capability

    @property
    def policy_id(self) -> str:
        return self.summary.policy_id

    @property
    def snapshot_id(self) -> str:
        return self.summary.snapshot_id

    @property
    def final_validation_id(self) -> str:
        return self.summary.final_validation_id


@dataclass(frozen=True)
class PlanRef:
    """A verified current plan as a projection sees it (``publication.service.CurrentPlan`` without the artifact)."""

    verified: VerifiedPlan
    artifact: ArtifactView

    @property
    def published(self) -> Any:
        return self.verified.published

    @property
    def head_version(self) -> int:
        return self.verified.head_version


def _merge(into: dict[str, list[Any]], seen: dict[str, set[str]], name: str, records: Iterable[Any], key: Any) -> None:
    bucket = into.setdefault(name, [])
    ids = seen.setdefault(name, set())
    for r in records:
        k = key(r)
        if k not in ids:
            ids.add(k)
            bucket.append(r)


def _phase_key(p: Any) -> str:
    return p.key.id


def _job_key(j: Any) -> str:
    return j.key.id


_KEYS = {"jobs": _job_key, "phases": _phase_key, "dispositions": lambda d: d.job, "edges": lambda e: e.id, "releases": lambda r: r.id,
         "durations": lambda d: d.id, "requirements": lambda r: r.job, "shortages": lambda s: str(s.record_id), "pools": lambda p: p.id,
         "availability": lambda a: a.id, "assignments": lambda a: a.id, "duties": lambda d: d.id, "job_dispositions": lambda o: o.job,
         "commitment_results": lambda r: (r.phase, r.incumbent_assignment_id)}


# ---------------------------------------------------------------------- the canon

@dataclass
class DisplayCanon:
    services: Any
    source_run_id: str | None
    summary: SnapshotSummary | None
    manifest: DisplayManifest | None
    verified: dict[str, VerifiedPlan | None]
    executable_reason: str
    forecast_reason: str
    stale: bool
    reasons: list[dict[str, Any]]
    revision: tuple = ()
    _snap: dict[str, list[Any]] = field(default_factory=dict)       # current snapshot collections loaded by this slice
    _extra: dict[str, dict[str, Any]] = field(default_factory=dict)  # plan-snapshot phases and jobs for lookups
    _plans: dict[str, dict[str, list[Any]]] = field(default_factory=dict)
    _seen: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    edge_labels: dict[str, str] = field(default_factory=dict)       # job buckets: labels of edge ends outside them
    _memo: dict[Any, Any] = field(default_factory=dict, repr=False, compare=False)   # views of this slice, built once

    @classmethod
    def load(cls, services: Any, cache: Any) -> "DisplayCanon":
        """The current source run's verified snapshot summary and each verified plan head's summary (nothing else)."""
        from ..data.acquisition import get_run, source_head_name
        from ..policy import PolicyError
        from ..publication.repository import repository_for
        from ..web.operations import reason
        from .store import DisplayError, snapshot_manifest, verify_plan_display

        repos = services.repositories
        none = {p: None for p in PURPOSES}
        if not repos.available:
            return cls(services, None, None, None, none, "REPOSITORY_UNAVAILABLE", "REPOSITORY_UNAVAILABLE", False,
                       [reason("REPOSITORY_UNAVAILABLE", "No repository is configured; the application is in a typed no-data state.",
                               "Configure FOCUS_SQL_* (live) or use FOCUS_MODE=fixture.")], ("unavailable",))
        try:
            policy = services.policy
        except PolicyError as exc:
            return cls(services, None, None, None, none, "POLICY_UNAVAILABLE", "POLICY_UNAVAILABLE", False,
                       [reason("POLICY_UNAVAILABLE", exc.message, "Fix the configured policy bundle.")], ("no-policy",))
        scope = services.settings.scope_key
        reasons: list[dict[str, Any]] = []
        head = repos.head(source_head_name(scope))
        summary: SnapshotSummary | None = None
        manifest: DisplayManifest | None = None
        if head is None:
            reasons.append(reason("NO_SOURCE_RUN", "No admitted source run exists for this scope.",
                                  "Run the demo pipeline (fixture) or a source acquisition worker (live)."))
        else:
            found = get_run(repos, head.record_id)
            run = None if found is None else found[0]
            if run is None or run.status != "COMPLETE" or not run.snapshot_id:
                reasons.append(reason("SOURCE_RUN_INCOMPLETE", f"The source head names run {head.record_id}, which has no complete admitted snapshot.",
                                      "Investigate the source run receipt; nothing partial is shown as complete."))
            else:
                def verified_snapshot() -> tuple[Any, Any, str]:
                    m, why = snapshot_manifest(repos, run.snapshot_id, run.snapshot_hash)
                    if m is None:
                        return None, None, why
                    ref = m.part("summary")
                    try:
                        s = None if ref is None else load_part(repos, ref)
                    except DisplayError as exc:
                        return None, None, f"{exc.code}: {exc.message}"
                    if s is None or s.snapshot_id != run.snapshot_id or s.snapshot_hash != run.snapshot_hash:
                        return None, None, "DISPLAY_MISMATCH: the snapshot summary does not name the source run's snapshot"
                    return m, s, "OK"

                manifest, summary, why = cache.get_or_load(("display-snapshot", run.snapshot_id, run.snapshot_hash), verified_snapshot,
                                                          keep=lambda v: v[1] is not None)
                if summary is None and why == "DISPLAY_NOT_BUILT":
                    reasons.append(reason("DISPLAY_NOT_BUILT", f"Snapshot {run.snapshot_id} of the current source run has no display package.",
                                          "Build it in the source role: python -m focus.cli display --current-source."))
                elif summary is None and why.startswith("PART_MISSING"):
                    reasons.append(reason("DISPLAY_PARTS_MISSING", f"A part of snapshot {run.snapshot_id}'s display package is not stored: {why}",
                                          "Rebuild it in the source role: python -m focus.cli display --current-source."))
                elif summary is None:
                    reasons.append(reason("DISPLAY_NOT_VERIFIED", f"The display package of snapshot {run.snapshot_id} did not verify: {why}",
                                          "Rebuild it in the source role; nothing unverified is shown."))
                elif summary.policy_hash != policy.hash:
                    reasons.append(reason("POLICY_CHANGED_SINCE_ADMISSION",
                                          "The admitted snapshot was built under another policy revision; facts are shown as admitted.",
                                          "Re-admit or reschedule under the active policy."))
        verified: dict[str, VerifiedPlan | None] = {}
        why_plan: dict[str, str] = {}
        revs: dict[str, tuple] = {}
        repo = repository_for(services)
        for purpose in PURPOSES:
            plan_head = repo.head(scope, purpose)
            if plan_head is None:
                verified[purpose], why_plan[purpose], revs[purpose] = None, "NO_ACCEPTED_PLAN", (purpose, None)
                continue
            vp, why = cache.get_or_load(
                ("display-plan", plan_head.record_id, plan_head.version),
                lambda h=plan_head, p=purpose: verify_plan_display(services, h.record_id, scope=scope, purpose=p, head_version=h.version),
                keep=lambda v: v[0] is not None)
            verified[purpose], why_plan[purpose], revs[purpose] = vp, why, (purpose, plan_head.record_id, plan_head.version)
        stale = False
        if summary is not None:
            stale = services.clock.now() - summary.cutoff > timedelta(hours=policy.int("dispatch.max_source_age_hours"))
            if stale:
                reasons.append(reason("SOURCE_STALE", f"Source cut off at {summary.cutoff.isoformat()} is older than the freshness policy.",
                                      "Shown for history only; acquire a current source run."))
        revision = (None if head is None else head.record_id, None if summary is None else summary.snapshot_id, policy.hash,
                    revs["EXECUTABLE"], revs["FORECAST"], stale)
        return cls(services, None if head is None else head.record_id, summary, manifest, verified, why_plan["EXECUTABLE"],
                   why_plan["FORECAST"], stale, reasons, revision)

    # -- the canonical surface the projections read
    def _once(self, key: Any, build: Any) -> Any:
        """A view of this slice, built on first use (projections read ``snapshot``, ``plan`` and the indexes in loops)."""
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]

    @property
    def snapshot(self) -> SnapshotSlice | None:
        if self.summary is None:
            return None
        return self._once("snapshot", lambda: SnapshotSlice(self.summary, {k: tuple(v) for k, v in self._snap.items()}))

    @property
    def executable(self) -> PlanRef | None:
        return self.current("EXECUTABLE")

    @property
    def forecast(self) -> PlanRef | None:
        return self.current("FORECAST")

    def current(self, purpose: str) -> PlanRef | None:
        vp = self.verified.get(purpose)
        if vp is None:
            return None
        return PlanRef(vp, ArtifactView(self.plan(purpose), vp.summary))

    def plan(self, purpose: str) -> PlanSlice | None:
        vp = self.verified.get(purpose)
        if vp is None:
            return None
        return self._once(("plan", purpose), lambda: PlanSlice(vp.summary, {k: tuple(v) for k, v in self._plans.get(purpose, {}).items()}))

    def plan_id(self, purpose: str) -> str | None:
        vp = self.verified.get(purpose)
        return None if vp is None else vp.published.id

    def jobs(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            out = dict(self._extra.get("jobs", {}))
            out.update({j.key.id: j for j in self._snap.get("jobs", ())})
            return out
        return self._once("jobs", build)

    def phases(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            out = dict(self._extra.get("phases", {}))
            out.update({p.key.id: p for p in self._snap.get("phases", ())})
            return out
        return self._once("phases", build)

    def people(self) -> dict[str, Any]:
        return self._once("people", lambda: {p.key.id: p for p in self.summary.people} if self.summary else {})

    def dispositions(self) -> dict[str, Any]:
        return self._once("dispositions", lambda: {d.job: d for d in self._snap.get("dispositions", ())})

    def base_status(self) -> str:
        if self.summary is None:
            return "unknown" if self.services.repositories.available and self.executable_reason != "POLICY_UNAVAILABLE" else "blocked"
        return "stale" if self.stale else "ready"

    def plan_reason(self, purpose: str) -> dict[str, Any]:
        from ..web.operations import reason

        why = self.executable_reason if purpose == "EXECUTABLE" else self.forecast_reason
        if why in ("NO_ACCEPTED_PLAN", "REPOSITORY_UNAVAILABLE", "POLICY_UNAVAILABLE"):
            return reason("NO_ACCEPTED_PLAN", f"No accepted {purpose} plan exists for this scope.",
                          "Schedule, validate and publish a plan through the trusted publication worker.")
        if why.startswith("DISPLAY_NOT_BUILT"):
            return reason("DISPLAY_NOT_BUILT", f"The accepted {purpose} plan has no display package yet.",
                          "Build it in the signer role: python -m focus.cli display --current-plans.")
        return reason("PLAN_NOT_VERIFIED", f"The accepted {purpose} head did not verify: {why}", "Investigate the publication receipt; nothing is current.")

    # -- parts
    def _part(self, manifest: DisplayManifest | None, name: str) -> Any:
        ref = None if manifest is None else manifest.part(name)
        return None if ref is None else load_part(self.services.repositories, ref)

    def _plan_manifest(self, purpose: str) -> DisplayManifest | None:
        vp = self.verified.get(purpose)
        return None if vp is None else vp.manifest

    def _child(self) -> "DisplayCanon":
        return type(self)(self.services, self.source_run_id, self.summary, self.manifest, self.verified, self.executable_reason,
                            self.forecast_reason, self.stale, self.reasons, self.revision,
                            {k: list(v) for k, v in self._snap.items()}, {k: dict(v) for k, v in self._extra.items()},
                            {p: {k: list(v) for k, v in c.items()} for p, c in self._plans.items()},
                            {k: {n: set(s) for n, s in v.items()} for k, v in self._seen.items()}, dict(self.edge_labels))

    def _add(self, name: str, records: Iterable[Any]) -> None:
        self._memo.clear()
        _merge(self._snap, self._seen.setdefault("snap", {}), name, records, _KEYS[name])

    def _add_plan(self, purpose: str, name: str, records: Iterable[Any]) -> None:
        self._memo.clear()
        _merge(self._plans.setdefault(purpose, {}), self._seen.setdefault(purpose, {}), name, records, _KEYS[name])

    def _add_extra(self, name: str, records: Iterable[Any]) -> None:
        self._memo.clear()
        bucket = self._extra.setdefault(name, {})
        for r in records:
            bucket.setdefault(_KEYS[name](r), r)

    def _ensure(self, name: str) -> None:
        """Declare a collection loaded even when no part added to it (an empty selection is a loaded, empty slice)."""
        self._memo.clear()
        self._snap.setdefault(name, [])

    def _ensure_plan(self, purpose: str, name: str) -> None:
        self._memo.clear()
        if self.verified.get(purpose) is not None:
            self._plans.setdefault(purpose, {}).setdefault(name, [])

    # -- slices
    def lines(self, lines: Iterable[int], *, plans: bool = True) -> "DisplayCanon":
        out = self._child()
        for name in ("jobs", "dispositions", "shortages", "releases"):
            out._ensure(name)
        for line in sorted(set(lines)):
            part: SnapshotLine | None = self._part(self.manifest, f"line:{line}")
            if part is not None:
                out._add("jobs", part.jobs)
                out._add("dispositions", part.dispositions)
                out._add("shortages", part.shortages)
                out._add("releases", part.releases)
                out._add_extra("jobs", part.other_jobs)
            if plans:
                for purpose in PURPOSES:
                    out._ensure_plan(purpose, "job_dispositions")
                    plan_part: PlanLine | None = self._part(self._plan_manifest(purpose), f"line:{line}")
                    if plan_part is not None:
                        out._add_plan(purpose, "job_dispositions", plan_part.outcomes)
        return out

    def jobs_slice(self, job_ids: Iterable[str], *, plans: bool = True) -> "DisplayCanon":
        out = self._child()
        job_ids = list(job_ids)
        for name in ("jobs", "dispositions", "phases", "edges", "releases", "durations", "requirements", "shortages"):
            out._ensure(name)
        if self.manifest is not None:
            for b in sorted({bucket_of(j, self.manifest.buckets) for j in job_ids}):
                part: SnapshotJobs | None = self._part(self.manifest, f"jobs:{b}")
                if part is None:
                    continue
                out._add("jobs", part.jobs)
                out._add("dispositions", part.dispositions)
                out._add("phases", part.phases)
                out._add("edges", part.edges)
                out._add("releases", part.releases)
                out._add("durations", part.durations)
                out._add("requirements", part.requirements)
                out._add("shortages", part.shortages)
                out.edge_labels.update(part.edge_labels)
                out._add_extra("jobs", part.other_jobs)
        if plans:
            for purpose in PURPOSES:
                out._ensure_plan(purpose, "assignments")
                out._ensure_plan(purpose, "job_dispositions")
                manifest = self._plan_manifest(purpose)
                if manifest is None:
                    continue
                for b in sorted({bucket_of(j, manifest.buckets) for j in job_ids}):
                    plan_part: PlanJobs | None = self._part(manifest, f"jobs:{b}")
                    if plan_part is not None:
                        out._add_plan(purpose, "assignments", plan_part.assignments)
                        out._add_plan(purpose, "job_dispositions", plan_part.outcomes)
        return out

    def pools(self, teams: Iterable[str] | None, days: Iterable[date] | None) -> "DisplayCanon":
        """Pools and availability of the team-days named (``None``: every team, or every day, in the pool index)."""
        out = self._child()
        out._ensure("pools")
        out._ensure("availability")
        if self.summary is None:
            return out
        wanted_teams = None if teams is None else set(teams)
        wanted_days = None if days is None else set(days)
        keys = sorted({(p.team, p.start_date) for p in self.summary.pools
                       if (wanted_teams is None or p.team in wanted_teams) and (wanted_days is None or p.start_date in wanted_days)})
        for team, day in keys:
            part: SnapshotPools | None = self._part(self.manifest, f"pools:{team}:{day.isoformat()}")
            if part is not None:
                out._add("pools", part.pools)
                out._add("availability", part.availability)
        return out

    def work(self, teams: Iterable[str], lines: Iterable[int] | None = None) -> "DisplayCanon":
        """Teams' phases (on the aircraft named, or all) with the records ``ValidationInput`` and the dispatch rule read."""
        out = self._child()
        for name in ("jobs", "dispositions", "phases", "edges", "releases", "shortages"):
            out._ensure(name)
        if self.manifest is None:
            return out
        wanted = set(teams)
        line_set = None if lines is None else set(lines)
        for name in self.manifest.names("work:"):
            _, team, line = name.split(":", 2)
            if team not in wanted or (line_set is not None and int(line) not in line_set):
                continue
            part: SnapshotWork = self._part(self.manifest, name)
            out._add("phases", part.phases)
            out._add("phases", part.predecessors)
            out._add("jobs", part.jobs)
            out._add("dispositions", part.dispositions)
            out._add("dispositions", part.predecessor_dispositions)
            out._add("edges", part.edges)
            out._add("releases", part.releases)
            out._add("shortages", part.shortages)
        return out

    def graph(self, line: int) -> LineGraph | None:
        """One aircraft's dependency graph (the dispatch rule's ancestor walk)."""
        return self._part(self.manifest, f"graph:{line}")

    def holds(self, *, plans: bool = True) -> "DisplayCanon":
        out = self._child()
        out._ensure("phases")
        out._ensure("jobs")
        part: SnapshotHolds | None = self._part(self.manifest, "holds")
        if part is not None:
            out._add("phases", part.phases)
            out._add("jobs", part.jobs)
        if plans:
            for purpose in PURPOSES:
                out._ensure_plan(purpose, "assignments")
                plan_part: PlanHolds | None = self._part(self._plan_manifest(purpose), "holds")
                if plan_part is not None:
                    out._add_plan(purpose, "assignments", plan_part.assignments)
        return out

    def team_days(self, purpose: str, teams: Iterable[str] | None, days: Iterable[date | None] | None) -> "DisplayCanon":
        """A plan's assignments and duties of the team-days named (``None``: all), with the plan snapshot's phases and jobs."""
        out = self._child()
        out._ensure_plan(purpose, "assignments")
        out._ensure_plan(purpose, "duties")
        vp = self.verified.get(purpose)
        if vp is None:
            return out
        wanted_teams = None if teams is None else set(teams)
        wanted_days = None if days is None else {"-" if d is None else d.isoformat() for d in days}
        for key in vp.summary.team_days:
            team, day = key.rsplit(":", 1)
            if (wanted_teams is not None and team not in wanted_teams) or (wanted_days is not None and day not in wanted_days):
                continue
            part: PlanTeamDay = self._part(vp.manifest, f"team-day:{key}")
            out._add_plan(purpose, "assignments", part.assignments)
            out._add_plan(purpose, "duties", part.duties)
            out._add_extra("phases", part.phases)
            out._add_extra("jobs", part.jobs)
        return out

    def commitments(self, purpose: str = "EXECUTABLE") -> "DisplayCanon":
        out = self._child()
        out._ensure_plan(purpose, "commitment_results")
        part: PlanCommitments | None = self._part(self._plan_manifest(purpose), "commitments")
        if part is not None:
            out._add_plan(purpose, "commitment_results", part.results)
            out._add_extra("phases", part.phases)
            out._add_extra("jobs", part.jobs)
        return out

    def goal_facts(self, purpose: str, day: date) -> PlanGoals | None:
        return self._part(self._plan_manifest(purpose), f"goals:{day.isoformat()}")

    def labels(self, phase_ids: Iterable[str]) -> dict[str, str]:
        """Current labels of the phases named (their job buckets' labels; a phase not admitted has none)."""
        out: dict[str, str] = {}
        if self.manifest is None:
            return out
        for b in sorted({bucket_of(job_of_phase(p), self.manifest.buckets) for p in phase_ids}):
            part: SnapshotLabels | None = self._part(self.manifest, f"labels:{b}")
            if part is not None:
                out.update(part.labels)
        return out

    def standards(self, lines: Iterable[int]) -> "DisplayCanon":
        """Aircraft's jobs (``lines``) with their duration standards and requirements."""
        out = self.lines(lines, plans=False)
        out._ensure("durations")
        out._ensure("requirements")
        for line in sorted(set(lines)):
            part: SnapshotStandards | None = self._part(self.manifest, f"standards:{line}")
            if part is not None:
                out._add("durations", part.durations)
                out._add("requirements", part.requirements)
        return out

    def releases_slice(self) -> "DisplayCanon":
        out = self._child()
        out._ensure("releases")
        part: SnapshotReleases | None = self._part(self.manifest, "releases")
        if part is not None:
            out._add("releases", part.releases)
        return out


# ---------------------------------------------------------------------- the dispatch rule's input over a slice

_MAY_BE_EMPTY = {"raw_cs_rows", "requirements", "durations"}         # ValidationInput fields the dispatch rule never reads


class _SliceFacts:
    """A slice as ``ValidationInput.build`` reads a snapshot: loaded collections, summary fields, and empty tuples only for
    the three collections the dispatch rule never consults. Any other unloaded collection still raises ``NotInSlice``."""

    def __init__(self, snap: SnapshotSlice):
        self._s = snap

    def __getattr__(self, name: str) -> Any:
        if name in _MAY_BE_EMPTY and not self._s.loaded(name):
            return ()
        return getattr(self._s, name)


def slice_input(canon: DisplayCanon, purpose: Any = None) -> Any:
    """``ValidationInput`` over a work slice, whose ancestor walk runs on the phase's aircraft graph part.

    ``assess_action`` and ``assess_addition`` read only the unconfirmed PART releases among a phase's ancestors. The
    walk returns exactly those (``records.unreceived_ancestors``, the same walk as ``ValidationInput.ancestors``), and
    adds their release records, so both rules decide as they do on the whole graph. Graphs load on first use.
    """
    from dataclasses import dataclass as _dc, field as _field

    from ..domain import Purpose
    from ..validation.input import ValidationInput

    @_dc
    class SliceInput(ValidationInput):
        graph_of: Any = None
        graphs: dict[int, Any] = _field(default_factory=dict)
        walks: dict[int, dict[int, frozenset[int]]] = _field(default_factory=dict)

        def ancestors(self, node: str, memo: dict[str, frozenset[str]]) -> frozenset[str]:
            phase = self.phases.get(node)
            if phase is None or self.graph_of is None:
                return frozenset()
            if phase.line not in self.graphs:
                graph = self.graph_of(phase.line)
                self.graphs[phase.line] = graph
                for r in (graph.releases if graph is not None else ()):
                    self.releases.setdefault(r.id, r)
            graph = self.graphs[phase.line]
            if graph is None:
                return frozenset()
            return unreceived_ancestors(graph, node, self.walks.setdefault(phase.line, {}))

    inp = SliceInput.build(_SliceFacts(canon.snapshot), canon.services.policy, purpose or Purpose.EXECUTABLE)
    inp.graph_of = canon.graph
    return inp


# ---------------------------------------------------------------------- accepted history

def accepted_display(services: Any, purpose: str) -> tuple[list[VerifiedPlan], list[tuple[str, str]]]:
    """Every accepted plan of one purpose verified through its display receipt, in acceptance order (as
    ``publication.service.accepted_plans``, without decoding a single artifact); unverifiable ones are listed, never used."""
    from ..publication.repository import repository_for
    from .store import verify_plan_display

    repo = repository_for(services)
    scope = services.settings.scope_key
    good: list[VerifiedPlan] = []
    bad: list[tuple[str, str]] = []
    for plan_id in repo.accepted_ids():
        try:
            published = repo.published(plan_id)
        except Exception as exc:  # noqa: BLE001 - tampered storage is reported, never used
            bad.append((plan_id, f"SECURITY_FAILURE: unreadable ({type(exc).__name__})"))
            continue
        if published is None or published.scope != scope or published.purpose.value != purpose:
            continue
        vp, why = verify_plan_display(services, plan_id, scope=scope, purpose=purpose)
        if vp is None:
            bad.append((plan_id, why))
        else:
            good.append(vp)
    good.sort(key=lambda v: (v.head_version, v.published.accepted_at, v.published.id))
    return good, bad


def plan_days(vp: VerifiedPlan | None) -> list[date | None]:
    """The shift start dates of a plan's team-day parts (``None`` for unshifted work)."""
    if vp is None:
        return []
    days = {key.rsplit(":", 1)[1] for key in vp.summary.team_days}
    return sorted((None if d == "-" else date.fromisoformat(d) for d in days), key=lambda d: (d is not None, d or date.min))


def next_day(day: date) -> date:
    return day + timedelta(days=1)
