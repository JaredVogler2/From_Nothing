"""Performance over any period for a team, a superintendent or the business unit (D-25).

**One light row per team-shift.** Whenever a team-shift's evaluation gets a revision (``score.evaluate_shift``), its row
in ``team_shift_performance`` (one versioned document per production day) takes that revision's seven canonical
components, in standard person-minutes:

* B0, the frozen obligation, and B, with approved additions;
* E, approved relief;
* Cp, verified planned completion;
* U, unplanned output, and Uc, the part of it that earns credit as extra work (D-27);
* O, the out-of-sequence charge.

It also keeps the status, the evaluation it came from, the team and the superintendent the team reported to at the
shift start (``data.org``). That attribution is fixed when the row is first written; a later correction changes the
numbers, never the attribution. A row is about 200 bytes, and it needs none of the snapshot, the plan or the per-unit
evidence.

**Any group, any period.** A team, a superintendent (the teams that reported to them in the period) or the business
unit (every team), over a shift, a day, a week, rolling 30 days, a month, a quarter, a year or any dates:

* add the components of the group's team-shifts in the period;
* apply the one canonical formula (``score.status_and_score``) once to the sums.

Sums come before division, so a team counts in proportion to its planned work: 400 planned and 200 earned plus 100 and
100 scores 60, not the 75 average of the two scores. Credited extra work is summed too, so extra work beyond one shift's
expected amount counts toward the period. This is exactly the pooled evaluation of the same members
(``score.aggregate``).

**Shifts that cannot be scored.** The official result is unknown when any team-shift in the period has no score yet
(an open credit window, missing data, an unknown classification). That is the canonical pooling rule, and it never
invents a score. Next to it, ``partial`` is the score of the scored team-shifts, labeled with how many are missing.
Leaving missing shifts out can flatter a group, so ``partial`` is never the official number.

**How the teams compare.** For a superintendent or the business unit, the result also gives each team's own score over
the period: how many are at or above a target (80 by default), the lowest and highest team, the range, and the count
per grade. The official score weights teams by planned work. These show whether one large team carries the rest.

**The jobs behind the score.** Beside each row, ``job_outcomes`` keeps the team-shift's jobs compactly, about 45 bytes
each. Every planned unit records its job's shop order id (shared across aircraft), its aircraft line, phase, planned
minutes and outcome:

* done, missed or excused (with the claim causes);
* still open;
* done out of sequence.

Every unplanned completion records the same identity and its minutes. A job the current snapshot does not know yet (an
emergency addition after the cutoff) keeps its job id; ``performance-ledger`` fills in its shop order id once a later
snapshot carries it. That answers "adherence was 60%: which jobs were
done as planned and which were not" for one team-shift, and "which jobs miss, or get done out of plan, aircraft after
aircraft" for any group and period (``job_patterns``). It is kept for good, while the bulky evidence behind it goes after
the owner's period (``performance.details``). At 50 aircraft that is about 107 jobs per team-shift, under 0.2 GB a year.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

from ..data.store import VersionConflict

LEDGER = "team_shift_performance"
LEDGER_SCHEMA = 1
JOBS = "job_outcomes"                      # versioned state, one document per production day: each team-shift's per-job outcomes
JOB_COLUMNS = ("soi", "line", "kind", "ordinal", "minutes", "outcome", "out_of_sequence", "causes", "origin", "job")
UNPLANNED_COLUMNS = ("soi", "line", "kind", "ordinal", "minutes", "job")
OUTCOMES = ("done", "missed", "excused", "open", "unplanned", "out_of_sequence", "no_plan")
NO_PLAN = "NO_OBLIGATION_PLAN"             # the reason of a team-shift no accepted executable plan covers (score.evaluate_shift)
GROUPINGS = ("job", "job_aircraft")
LEVELS = ("team", "superintendent", "business_unit")
PERIODS = ("shift", "day", "week", "rolling30", "month", "quarter", "year", "custom")
BREAKDOWNS = ("none", "shift", "day", "week", "month", "quarter")
UNASSIGNED = "UNASSIGNED"                  # the superintendent node of team-shifts whose team names none
MAX_DAYS = 400                             # the longest custom period one request reads
DEFAULT_TARGET = Decimal(80)


class RollupError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _text(x: Fraction | None) -> str | None:
    return None if x is None else (str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}")


def _frac(s: str | None) -> Fraction | None:
    return None if s is None else Fraction(s)


# ---------------------------------------------------------------------- writing

def latest_org_chart(services: Any) -> Any:
    """The org chart admitted with the current source run (it carries the effective history the source keeps)."""
    from ..data.acquisition import source_head_name
    from ..data.org import ORG_CHARTS, OrgChart

    repos = services.repositories
    head = repos.head(source_head_name(services.settings.scope_key))
    return None if head is None else repos.get_typed(ORG_CHARTS, head.record_id, "OrgChart", OrgChart)


def record(services: Any, evaluation: Any, *, team: str, shift_id: str, window_start: datetime, window_end: datetime,
           chart: Any = "latest") -> dict[str, Any]:
    """Make the team-shift's row carry ``evaluation`` (idempotent; the first write fixes the superintendent)."""
    from ..calendar import parse_shift_id
    from ..data.org import superintendent_at

    _, day, number = parse_shift_id(shift_id)
    repos = services.repositories
    key = f"{team}|{shift_id}"
    for _ in range(8):
        found = repos.get_state(LEDGER, day.isoformat())
        doc, version = found if found is not None else ({"schema": LEDGER_SCHEMA, "day": day.isoformat(), "rows": {}}, 0)
        old = doc["rows"].get(key)
        if old is not None and old["evaluation_id"] == evaluation.id:
            return old
        if old is not None:
            superintendent, note = old["superintendent"], old["superintendent_note"]
        else:
            superintendent, note = superintendent_at(latest_org_chart(services) if chart == "latest" else chart, team, window_start)
        row = {"team": team, "shift_id": shift_id, "day": day.isoformat(), "shift_number": number, "window_start": window_start.isoformat(),
               "window_end": window_end.isoformat(), "superintendent": superintendent, "superintendent_note": note,
               "business_unit": services.settings.scope_key, "B0": _text(evaluation.B0), "B": _text(evaluation.B), "E": _text(evaluation.E),
               "Cp": _text(evaluation.Cp), "U": _text(evaluation.U), "Uc": _text(evaluation.Uc), "O": _text(evaluation.O),
               "status": evaluation.status.value,
               "reasons": [str(r).split(":", 1)[0] for r in evaluation.reasons], "evaluation_id": evaluation.id,
               "revision": evaluation.revision, "goal_id": evaluation.goal_ids[0] if evaluation.goal_ids else None,
               "policy_hash": evaluation.policy_hash, "evaluated_at": evaluation.cutoff.isoformat()}
        doc = {**doc, "rows": {**doc["rows"], key: row}}
        try:
            repos.put_state(LEDGER, day.isoformat(), doc, version)
            return row
        except VersionConflict:
            continue
    raise RollupError("LEDGER_CONTENDED", f"the performance row of {key} could not be written; the next evaluation retries")


def job_rows(evidence: Any, view: Any, goal_id: str | None) -> tuple[list[list[Any]], list[list[Any]], dict[str, str]]:
    """The compact per-job outcomes of one team-shift's evidence, its unplanned work, and the jobs' descriptions."""
    from .disruption import effective_claims

    graph = None if view is None else view.graph
    # every cause on record, proposed or submitted (``disruption.explained_units``): a missed job with none has no reason
    # on record
    causes: dict[str, set[str]] = defaultdict(set)
    if view is not None and goal_id:
        for claim in effective_claims(view, goal_id).values():
            if claim.status != "WITHDRAWN":
                for unit in claim.units:
                    causes[unit].add(claim.cause)
    names: dict[str, str] = {}

    def identity(job: str, phase: str) -> tuple[str | None, int | None, str | None, int | None, str | None]:
        info = None if graph is None else graph.job_names.get(job)
        p = None if graph is None else graph.phases.get(phase)
        parts = phase.split(".")
        kind = p.kind.value if p is not None else (parts[-2] if len(parts) >= 3 else None)
        ordinal = p.ordinal if p is not None else (int(parts[-1]) if len(parts) >= 3 and parts[-1].isdigit() else None)
        if info is None:
            return None, None if p is None else p.line, kind, ordinal, job       # the shop order id is not known: keep the job id
        if info[2]:
            names[info[0]] = info[2]
        return info[0], info[1], kind, ordinal, None

    jobs = []
    for f in evidence.unit_facts:
        soi, line, kind, ordinal, job = identity(f.job, f.phase)
        # work the previous shift finished before this shift began is out of D with the relief (score.components, D-26)
        before = f.status == "DONE_BEFORE_SHIFT"
        outcome = "excused" if (f.status == "MISSED" and f.relief) or before else {"EARNED": "done", "MISSED": "missed"}.get(f.status, "open")
        why = set(causes.get(f.unit_id, ())) | ({"DONE_BEFORE_SHIFT"} if before else set())
        jobs.append([soi, line, kind, ordinal, f.effort, outcome, int(bool(f.oos_charged)), "|".join(sorted(why)) or None,
                     "added" if f.origin == "AMENDMENT" else "planned", job])
    unplanned = []
    for o in evidence.offplan:
        soi, line, kind, ordinal, job = identity(o.job, o.phase)
        unplanned.append([soi, line, kind, ordinal, o.effort, job])
    return jobs, unplanned, names


def no_plan(evaluation: Any) -> bool:
    """True when no accepted executable plan covered the team-shift: its score is neutral and its work counts in no U."""
    return any(str(r).split(":", 1)[0] == NO_PLAN for r in evaluation.reasons)


def record_jobs(services: Any, evaluation: Any, evidence: Any, view: Any, *, team: str, shift_id: str,
                superintendent: str | None) -> dict[str, Any] | None:
    """The team-shift's per-job outcomes for ``evaluation`` (idempotent).

    The record adds up to the score: planned minutes to B, done to Cp, excused to E, unplanned to U. The credited part
    of the unplanned work (Uc) is kept in the row. A team-shift no plan covered (``no_plan``) records the work it did,
    which its neutral score leaves out of U. ``superintendent`` is the
    row's (``record``), which fixes the attribution; the job record follows it.
    """
    from ..calendar import parse_shift_id

    if evidence is None:
        return None
    _, day, number = parse_shift_id(shift_id)
    repos = services.repositories
    key = f"{team}|{shift_id}"
    jobs, unplanned, names = job_rows(evidence, view, evaluation.goal_ids[0] if evaluation.goal_ids else None)
    for _ in range(8):
        found = repos.get_state(JOBS, day.isoformat())
        doc, version = found if found is not None else ({"schema": LEDGER_SCHEMA, "day": day.isoformat(),
                                                        "columns": {"jobs": list(JOB_COLUMNS), "unplanned": list(UNPLANNED_COLUMNS)},
                                                        "names": {}, "rows": {}}, 0)
        old = doc["rows"].get(key)
        if (old is not None and old["evaluation_id"] == evaluation.id and (old["jobs"], old["unplanned"]) == (jobs, unplanned)
                and old["superintendent"] == superintendent):
            return old                       # the same score and identities (a rebuild with a newer graph can resolve more jobs)
        row = {"team": team, "shift_id": shift_id, "shift_number": number,
               "superintendent": superintendent, "evaluation_id": evaluation.id,
               "revision": evaluation.revision, "no_plan": no_plan(evaluation), "jobs": jobs, "unplanned": unplanned}
        doc = {**doc, "names": {**doc.get("names", {}), **names}, "rows": {**doc["rows"], key: row}}
        try:
            repos.put_state(JOBS, day.isoformat(), doc, version)
            return row
        except VersionConflict:
            continue
    raise RollupError("LEDGER_CONTENDED", f"the job outcomes of {key} could not be written; the next evaluation retries")


def _unresolved_known(record: dict[str, Any], graph: Any) -> bool:
    """True when a job the record keeps by job id only (no shop order id yet) is now in the current graph."""
    known = {} if graph is None else graph.job_names
    return any(j[0] is None and j[9] in known for j in record["jobs"]) or any(u[0] is None and u[5] in known for u in record["unplanned"])


def _follow_row(services: Any, day: str, key: str, superintendent: str | None) -> int:
    """Make a job record name its row's superintendent (no evidence needed: only the attribution changes)."""
    repos = services.repositories
    for _ in range(8):
        found = repos.get_state(JOBS, day)
        old = found[0]["rows"][key]
        if old["superintendent"] == superintendent:
            return 0
        try:
            repos.put_state(JOBS, day, {**found[0], "rows": {**found[0]["rows"], key: {**old, "superintendent": superintendent}}}, found[1])
            return 1
        except VersionConflict:
            continue
    raise RollupError("LEDGER_CONTENDED", f"the job outcomes of {key} could not be written; the next rebuild retries")


def rebuild(services: Any) -> dict[str, Any]:
    """Write every current team-shift evaluation into its row and its job record.

    Rows and job records already carrying the current evaluation are left as they are. A job record is (re)written when
    it is missing, carries an older evaluation, or keeps a job by id that the current graph now names. It is computed
    exactly as the evaluation computed it: from the stored evidence, with the claims known at the evaluation's cutoff
    and job names from the current graph. A team-shift whose evidence is already gone keeps its row and gets no job
    record. A row written anew (a lost ledger) takes the org chart's superintendent as of the shift start, and its job
    record follows it.
    """
    from ..contracts.evaluation import Evaluation, EvaluationEvidence
    from . import history
    from .lifecycle import GoalRegistry, ObservationView, current_graph, shift_window

    repos = services.repositories
    scope = services.settings.scope_key
    chart = latest_org_chart(services)
    graph = current_graph(services)
    seen: set[str] = set()
    pending: list[tuple[Any, str, str, str | None, dict[str, Any] | None]] = []
    written = followed = 0
    for eid in repos.list_ids(history.EVALUATIONS):
        e = repos.get_typed(history.EVALUATIONS, eid, "Evaluation", Evaluation)
        if e is None or e.kind != "GOAL" or e.scope != scope or e.series_key in seen:
            continue
        seen.add(e.series_key)
        current = history.current(services, scope, e.series_key)
        if current is None:
            continue
        team, _, shift_id = current.label.rpartition(" ")
        window = shift_window(services.policy, shift_id)
        key, day = f"{team}|{shift_id}", window.start_date.isoformat()
        before = repos.get_state(LEDGER, day)
        row = record(services, current, team=team, shift_id=shift_id, window_start=window.start, window_end=window.end, chart=chart)
        written += int(before is None or before[0]["rows"].get(key) != row)
        found = repos.get_state(JOBS, day)
        old = None if found is None else found[0]["rows"].get(key)
        if old is None or old["evaluation_id"] != current.id or _unresolved_known(old, graph):
            pending.append((current, team, shift_id, row["superintendent"], old))
        elif old["superintendent"] != row["superintendent"]:
            followed += _follow_row(services, day, key, row["superintendent"])
    jobs_written = gone = 0
    view, registry = None, None
    for current, team, shift_id, superintendent, old in sorted(pending, key=lambda p: (p[0].cutoff, p[0].id)):
        evidence = None if no_plan(current) else history.evidence(services, current.evidence_hash)
        if evidence is None and not no_plan(current):
            gone += 1                                           # the per-job evidence is gone: the row stays, no job record
            continue
        if view is None or view.as_of != current.cutoff:        # one view per cutoff, oldest first, one in memory at a time
            registry = registry or GoalRegistry.load(services)
            view = ObservationView.build(services, current.cutoff, registry=registry, graph=graph)
        if evidence is None:                                    # no plan: the work it did, as evaluate_shift records it
            evidence = EvaluationEvidence(current.evidence_hash, (), tuple(view.offplan_for(team, shift_id)), (), (), {}, (), None)
        jobs = record_jobs(services, current, evidence, view, team=team, shift_id=shift_id, superintendent=superintendent)
        jobs_written += int(jobs is not None and jobs != old)
    return {"status": "ok", "team_shifts": len(seen), "written": written, "job_records_written": jobs_written + followed,
            "job_records_without_evidence": gone,
            "note": "a team-shift whose per-job evidence is already gone keeps its row but gets no job record"}


# ---------------------------------------------------------------------- reading

def rows_between(services: Any, start: date, end: date) -> list[dict[str, Any]]:
    repos = services.repositories
    out: list[dict[str, Any]] = []
    day = start
    while day <= end:
        found = repos.get_state(LEDGER, day.isoformat())
        if found is not None:
            out.extend(found[0]["rows"].values())
        day += timedelta(days=1)
    return sorted(out, key=lambda r: (r["day"], r["shift_number"], r["team"]))


def latest_day(services: Any) -> date | None:
    days = services.repositories.store.list_state_ids(LEDGER)
    return date.fromisoformat(max(days)) if days else None


def period_range(period: str, anchor: date, start: date | None = None, end: date | None = None) -> tuple[date, date]:
    if period in ("shift", "day"):
        return anchor, anchor
    if period == "week":                                       # ISO weeks, Monday to Sunday
        monday = anchor - timedelta(days=anchor.weekday())
        return monday, monday + timedelta(days=6)
    if period == "rolling30":
        return anchor - timedelta(days=29), anchor
    if period == "month":
        nxt = date(anchor.year + (anchor.month == 12), anchor.month % 12 + 1, 1)
        return anchor.replace(day=1), nxt - timedelta(days=1)
    if period == "quarter":
        first = (anchor.month - 1) // 3 * 3 + 1
        nxt = date(anchor.year + (first == 10), (first + 2) % 12 + 1, 1)
        return date(anchor.year, first, 1), nxt - timedelta(days=1)
    if period == "year":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31)
    if period == "custom":
        if start is None or end is None:
            raise RollupError("BAD_PERIOD", "a custom period needs from and to")
        if end < start:
            raise RollupError("BAD_PERIOD", "to is before from")
        if (end - start).days + 1 > MAX_DAYS:
            raise RollupError("BAD_PERIOD", f"a custom period covers at most {MAX_DAYS} days")
        return start, end
    raise RollupError("BAD_PERIOD", f"period must be one of {', '.join(PERIODS)}")


def bucket_of(row: dict[str, Any], by: str) -> str:
    day = date.fromisoformat(row["day"])
    if by == "shift":
        return row["shift_id"]
    if by == "day":
        return row["day"]
    if by == "week":
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02d}"
    if by == "month":
        return f"{day.year}-{day.month:02d}"
    if by == "quarter":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    raise RollupError("BAD_BREAKDOWN", f"by must be one of {', '.join(BREAKDOWNS)}")


def _parts(row: dict[str, Any]) -> dict[str, Any]:
    # a row written before the extra-work credit (D-27) has no Uc: its credit is unknown until it is evaluated again
    return {"B0": Fraction(row["B0"]), "B": Fraction(row["B"]), "E": Fraction(row["E"]), "Cp": Fraction(row["Cp"]), "U": _frac(row["U"]),
            "Uc": _frac(row.get("Uc")), "O": _frac(row["O"]), "reasons": []}


def _score(rows: list[dict[str, Any]], policy: Any, reasons: list[str]) -> dict[str, Any]:
    """The canonical formula applied once to the summed components of ``rows``."""
    from .score import pooled, score_policy_errors, status_and_score

    parts = pooled(_parts(r) for r in rows)
    extra = list(reasons) + score_policy_errors(policy)
    if parts["U"] is None or parts["Uc"] is None or parts["O"] is None:
        extra.append("COMPONENT_UNKNOWN: an offplan or out-of-sequence component is unknown")
    if any(r["policy_hash"] != policy.hash for r in rows):
        extra.append("MIXED_POLICY: some team-shifts were evaluated under another policy")
    status, r, why = status_and_score(parts, policy, extra)
    return {"status": status.value, "score": None if r["score"] is None else str(r["score"]), "grade": r["grade"],
            "attainment": _text(r["attainment"]), "adherence": _text(r["adherence"]), "base": _text(r["base"]), "final": _text(r["final"]),
            "B0": _text(parts["B0"]), "B": _text(parts["B"]), "E": _text(parts["E"]), "D": _text(parts["B"] - parts["E"]),
            "Cp": _text(parts["Cp"]), "U": _text(parts["U"]), "Uc": _text(parts["Uc"]), "O": _text(parts["O"]),
            "reasons": list(dict.fromkeys(why)),
            "policy_status": policy.str("score.policy_status")}


def summarize(rows: list[dict[str, Any]], policy: Any) -> dict[str, Any]:
    """The official result over ``rows`` and, when some cannot be scored, the labeled partial score of the rest."""
    if not rows:
        return {"team_shifts": 0, "unscored": 0, "official": None, "partial": None}
    unscored = [r for r in rows if r["status"] == "unknown"]
    reasons = [f"SHIFTS_NOT_SCORED: {len(unscored)} of {len(rows)} team-shifts have no score yet"] if unscored else []
    scored = [r for r in rows if r["status"] != "unknown"]
    partial = None
    if unscored and scored:
        partial = {**_score(scored, policy, []), "label": f"partial: {len(scored)} of {len(rows)} team-shifts scored; {len(unscored)} missing"}
    return {"team_shifts": len(rows), "unscored": len(unscored), "official": _score(rows, policy, reasons), "partial": partial}


def spread(rows: list[dict[str, Any]], policy: Any, target: Decimal) -> dict[str, Any]:
    """Each team's own score over the period and how the teams compare (the official score weights them by planned work)."""
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)
    teams = []
    for team, team_rows in sorted(by_team.items()):
        s = summarize(team_rows, policy)
        teams.append({"team": team, "score": s["official"]["score"], "status": s["official"]["status"], "grade": s["official"]["grade"],
                      "planned": s["official"]["D"], "team_shifts": s["team_shifts"], "unscored": s["unscored"],
                      "partial_score": None if s["partial"] is None else s["partial"]["score"]})
    scored = [t for t in teams if t["score"] is not None]
    ranked = sorted(scored, key=lambda t: (Decimal(t["score"]), t["team"]))
    lowest, highest = (ranked[0], ranked[-1]) if ranked else (None, None)
    return {"teams": teams, "team_count": len(teams), "scored": len(scored), "not_scored": len(teams) - len(scored), "target": str(target),
            "on_target": sum(1 for t in scored if Decimal(t["score"]) >= target),
            "lowest": None if lowest is None else {"team": lowest["team"], "score": lowest["score"]},
            "highest": None if highest is None else {"team": highest["team"], "score": highest["score"]},
            "range": None if lowest is None else str(Decimal(highest["score"]) - Decimal(lowest["score"])),
            "grades": dict(sorted(Counter(t["grade"] for t in scored).items()))}


def node_of(row: dict[str, Any], level: str) -> str:
    if level == "team":
        return row["team"]
    if level == "superintendent":
        return row["superintendent"] or UNASSIGNED
    return row["business_unit"]


def rollup(services: Any, *, level: str, node: str | None, start: date, end: date, shift_number: int | None = None, by: str = "none",
           target: Decimal = DEFAULT_TARGET, allowed: Callable[[str], bool] = lambda team: True,
           all_rows: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The group's result over [start, end] (inclusive production days), its children, the team spread and a breakdown.

    ``allowed`` is the caller's team scope: a group is shown only when every one of its teams is allowed, and the
    choices list only groups the caller may open. A refusal raises ``RollupError("OUT_OF_SCOPE")``.
    """
    if level not in LEVELS:
        raise RollupError("BAD_LEVEL", f"level must be one of {', '.join(LEVELS)}")
    if by not in BREAKDOWNS:
        raise RollupError("BAD_BREAKDOWN", f"by must be one of {', '.join(BREAKDOWNS)}")
    policy = services.policy
    rows = list(all_rows) if all_rows is not None else rows_between(services, start, end)
    if shift_number is not None:
        rows = [r for r in rows if r["shift_number"] == shift_number]
    node = node if node is not None else (services.settings.scope_key if level == "business_unit" else None)
    if node is None:
        raise RollupError("NODE_REQUIRED", f"name the {level} to roll up")
    mine = [r for r in rows if node_of(r, level) == node]
    teams = sorted({r["team"] for r in mine})
    refused = [t for t in teams if not allowed(t)]
    if refused:
        raise RollupError("OUT_OF_SCOPE", f"{level} {node} includes teams outside your data scope ({len(refused)})")
    result = summarize(mine, policy)
    child_level = {"business_unit": "superintendent", "superintendent": "team", "team": "shift"}[level]
    children = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in mine:
        grouped[r["shift_id"] if child_level == "shift" else node_of(r, child_level)].append(r)
    for key, child_rows in sorted(grouped.items()):
        s = summarize(child_rows, policy)
        children.append({"level": child_level, "node": key, "teams": sorted({r["team"] for r in child_rows}), **s})
    series = []
    if by != "none":
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in mine:
            buckets[bucket_of(r, by)].append(r)
        series = [{"bucket": key, **summarize(bucket_rows, policy)} for key, bucket_rows in sorted(buckets.items())]
    notes = sorted({(r["team"], r["superintendent_note"]) for r in mine if r["superintendent_note"]})
    return {
        "level": level, "node": node, "period": {"start": start.isoformat(), "end": end.isoformat(), "shift_number": shift_number}, "by": by,
        "teams": teams, **result, "children": children, "series": series,
        "spread": spread(mine, policy, target) if level != "team" else None,
        "attribution_notes": [{"team": t, "note": n} for t, n in notes],
        "choices": choices_of(services, rows, allowed),
        "policy_id": policy.id,
    }


def choices_of(services: Any, rows: Iterable[dict[str, Any]], allowed: Callable[[str], bool]) -> dict[str, Any]:
    """The groups the caller may open in the period: allowed teams, superintendents whose every team is allowed."""
    rows = list(rows)
    supts: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        supts[r["superintendent"] or UNASSIGNED].add(r["team"])
    teams = sorted({r["team"] for r in rows})
    return {"teams": [t for t in teams if allowed(t)],
            "superintendents": sorted(s for s, ts in supts.items() if all(allowed(t) for t in ts)),
            "business_unit": services.settings.scope_key if all(allowed(t) for t in teams) else None}


def job_records_between(services: Any, start: date, end: date) -> tuple[list[dict[str, Any]], dict[str, str]]:
    repos = services.repositories
    rows: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    day = start
    while day <= end:
        found = repos.get_state(JOBS, day.isoformat())
        if found is not None:
            names.update(found[0].get("names", {}))
            rows.extend({**r, "day": day.isoformat()} for r in found[0]["rows"].values())
        day += timedelta(days=1)
    return rows, names


def job_patterns(services: Any, *, level: str, node: str | None, start: date, end: date, shift_number: int | None = None, group: str = "job",
                 outcome: str = "all", limit: int = 50, allowed: Callable[[str], bool] = lambda team: True,
                 records_names: tuple[list[dict[str, Any]], dict[str, str]] | None = None) -> dict[str, Any]:
    """Which jobs, by shop order id across aircraft, were done, missed, excused, open, out of sequence or unplanned for a group
    over a period. ``group="job_aircraft"`` keeps one row per job per aircraft (one team-shift's adherence, job by job)."""
    if level not in LEVELS:
        raise RollupError("BAD_LEVEL", f"level must be one of {', '.join(LEVELS)}")
    if group not in GROUPINGS:
        raise RollupError("BAD_GROUP", f"group must be one of {', '.join(GROUPINGS)}")
    if outcome != "all" and outcome not in OUTCOMES:
        raise RollupError("BAD_OUTCOME", f"outcome must be all or one of {', '.join(OUTCOMES)}")
    node = node if node is not None else (services.settings.scope_key if level == "business_unit" else None)
    if node is None:
        raise RollupError("NODE_REQUIRED", f"name the {level}")
    records, names = records_names if records_names is not None else job_records_between(services, start, end)
    if shift_number is not None:
        records = [r for r in records if r["shift_number"] == shift_number]

    def node_of_record(r: dict[str, Any]) -> str:
        return r["team"] if level == "team" else (r["superintendent"] or UNASSIGNED) if level == "superintendent" else services.settings.scope_key

    mine = [r for r in records if node_of_record(r) == node]
    refused = sorted({r["team"] for r in mine if not allowed(r["team"])})
    if refused:
        raise RollupError("OUT_OF_SCOPE", f"{level} {node} includes teams outside your data scope ({len(refused)})")
    table: dict[tuple[Any, ...], dict[str, Any]] = {}

    def entry(soi: Any, kind: Any, line: Any, job: Any) -> dict[str, Any]:
        key = (soi or job, kind) + ((line,) if group == "job_aircraft" else ())
        if key not in table:
            table[key] = {"soi": soi, "job": job if soi is None else None, "kind": kind, "name": names.get(soi) if soi else None,
                          "line": line if group == "job_aircraft" else None, "lines": set(), "teams": set(), "units": 0, "planned_minutes": 0,
                          "count": Counter(), "minutes": Counter(), "causes": Counter(), "missed_lines": set(), "unplanned_lines": set()}
        return table[key]

    unknown_minutes = 0
    for r in mine:
        for soi, line, kind, _ordinal, minutes, result, oos, causes, _origin, job in r["jobs"]:
            e = entry(soi, kind, line, job)
            e["lines"].add(line)
            e["teams"].add(r["team"])
            e["units"] += 1
            e["planned_minutes"] += minutes or 0
            e["count"][result] += 1
            e["minutes"][result] += minutes or 0
            if oos:
                e["count"]["out_of_sequence"] += 1
                e["minutes"]["out_of_sequence"] += minutes or 0
            if result == "missed":
                e["missed_lines"].add(line)
            for cause in (causes.split("|") if causes else ()):
                e["causes"][cause] += 1
        kind_of_work = "no_plan" if r.get("no_plan") else "unplanned"   # no plan: neutral score, the work counts in no U
        for soi, line, kind, _ordinal, minutes, job in r["unplanned"]:
            e = entry(soi, kind, line, job)
            e["lines"].add(line)
            e["teams"].add(r["team"])
            e["count"][kind_of_work] += 1
            e["minutes"][kind_of_work] += minutes or 0
            if minutes is None and kind_of_work == "unplanned":
                unknown_minutes += 1
            if kind_of_work == "unplanned":
                e["unplanned_lines"].add(line)
    totals = {"count": Counter(), "minutes": Counter()}
    for e in table.values():
        totals["count"].update(e["count"])
        totals["minutes"].update(e["minutes"])
    done, missed = totals["minutes"]["done"], totals["minutes"]["missed"]
    # plan adherence is the planned jobs done over the planned jobs expected (done + missed; excused work is not expected).
    # Unplanned work never enters it (D-27); a job still open leaves it unsettled.
    adherence = None if totals["minutes"]["open"] or done + missed == 0 else _text(Fraction(done, done + missed))
    rows = [e for e in table.values() if outcome == "all" or e["count"][outcome] > 0]
    rows.sort(key=lambda e: (-(e["minutes"]["missed"] + e["minutes"]["unplanned"] + e["minutes"]["out_of_sequence"]), str(e["soi"] or e["job"]),
                             str(e["kind"]), e["line"] or 0))
    limit = max(1, min(int(limit), 500))

    def view(e: dict[str, Any]) -> dict[str, Any]:
        return {"soi": e["soi"], "job": e["job"], "kind": e["kind"], "name": e["name"], "line": e["line"],
                "aircraft": len({x for x in e["lines"] if x is not None}), "lines": sorted(x for x in e["lines"] if x is not None),
                "teams": sorted(e["teams"]), "planned_units": e["units"], "planned_minutes": e["planned_minutes"],
                "count": {k: e["count"][k] for k in OUTCOMES}, "minutes": {k: e["minutes"][k] for k in OUTCOMES},
                "causes": dict(e["causes"].most_common()), "missed_on": sorted(x for x in e["missed_lines"] if x is not None),
                "unplanned_on": sorted(x for x in e["unplanned_lines"] if x is not None)}

    return {"level": level, "node": node, "group": group, "outcome": outcome,
            "period": {"start": start.isoformat(), "end": end.isoformat(), "shift_number": shift_number},
            "team_shifts": len(mine), "jobs_total": len(rows), "limit": limit, "jobs": [view(e) for e in rows[:limit]],
            "totals": {"count": {k: totals["count"][k] for k in OUTCOMES}, "minutes": {k: totals["minutes"][k] for k in OUTCOMES},
                       "adherence_from_jobs": adherence, "unplanned_minutes_unknown": unknown_minutes},
            "choices": choices_of(services, ({"team": r["team"], "superintendent": r["superintendent"]} for r in records), allowed)}


def cli_performance_ledger(services: Any, args: Any) -> dict[str, Any]:
    return rebuild(services)
