"""Duration gauge: raw-clock islands, episode metrics and descriptive statistics (S08, P07).

Island method (running MAX, not comparison with the previous raw end):
nested clocks [0,60], [10,20], [60,90] form one 90-minute island; adding
[100,110] gives active elapsed 100 with the 10-minute gap preserved. Short gaps
are never bridged, long clocks are never capped and short clocks are never
dropped. Job union gives active elapsed; each owner's union summed gives person
effort; first-on to last-off gives cycle. Effort is attributed to the clock's
owner (PERSON_KEY); an actor who entered or audited a clock is not its owner.

Raw clocks are admitted by the latest SOURCE_REVISION of each SOURCE_EVENT_KEY:
a correction replaces the original and a voided correction removes the event,
so nothing is counted twice. Metrics are exact microseconds; minutes are exact
fractions, and quantization (display 6 dp, scheduling ceiling) is labelled.

Continuous percentiles use h=(n-1)p, lo=floor(h), hi=ceil(h),
x_lo + (h-lo)(x_hi-x_lo), computed exactly with Fractions. P50, P55, mean, P75
and P90 are persisted with n, window, group, models, crews, source/query/method
hashes, policy provenance and a distribution-free order-statistic confidence
interval for P75. The mean need not lie between P55 and P75.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

from ..codec import content_hash, parse_utc
from ..domain import digest_id

UTC = timezone.utc
STAT_KEYS = ("P50", "P55", "MEAN", "P75", "P90")
_Q = Decimal("0.000001")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_BIGINT = re.compile(r"^ *[+-]?[0-9]+ *$")
_US_PER_MINUTE = 60_000_000


def revision_rank(revision: str) -> tuple[int, int, str]:
    """Order of ``ORDER BY TRY_CONVERT(bigint, SourceRevision) DESC, SourceRevision DESC``: numeric outranks text."""
    if _BIGINT.match(revision) and -(2 ** 63) <= int(revision) < 2 ** 63:
        return (1, int(revision), revision)
    return (0, 0, revision)


def admit_latest_revisions(rows: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], int]:
    """Latest revision of each SOURCE_EVENT_KEY (voided rows are kept here; callers drop them). Returns (rows, superseded)."""
    latest: dict[str, Mapping[str, Any]] = {}
    total = 0
    for row in rows:
        total += 1
        key = str(row["SOURCE_EVENT_KEY"])
        held = latest.get(key)
        if held is None or revision_rank(str(row["SOURCE_REVISION"])) > revision_rank(str(held["SOURCE_REVISION"])):
            latest[key] = row
    return list(latest.values()), total - len(latest)


def to_micros(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(microseconds=1)


def minutes_of(us: int) -> Fraction:
    """Exact minutes for a microsecond count (no quantization)."""
    return Fraction(us, _US_PER_MINUTE)


def display_minutes(us: int) -> Decimal:
    """Minutes rounded half away from zero to 6 dp (decimal(19,6) display/persistence quantization)."""
    return (Decimal(us) / Decimal(_US_PER_MINUTE)).quantize(_Q, rounding=ROUND_HALF_UP)


def interval_union(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Running-max union of half-open intervals; touching intervals merge, gaps stay."""
    ordered = sorted((s, e) for s, e in intervals if e > s)
    islands: list[tuple[int, int]] = []
    cur_s: int | None = None
    cur_e: int | None = None
    for s, e in ordered:
        if cur_s is None:
            cur_s, cur_e = s, e
        elif s <= cur_e:  # type: ignore[operator]
            cur_e = max(cur_e, e)  # type: ignore[type-var]
        else:
            islands.append((cur_s, cur_e))  # type: ignore[arg-type]
            cur_s, cur_e = s, e
    if cur_s is not None:
        islands.append((cur_s, cur_e))  # type: ignore[arg-type]
    return islands


def union_length(intervals: Iterable[tuple[int, int]]) -> int:
    return sum(e - s for s, e in interval_union(intervals))


@dataclass(frozen=True)
class EpisodeMetrics:
    """Exact microsecond metrics for one episode; ``*_minutes`` are exact fractions."""

    episode: str
    active_elapsed: int                 # microseconds
    person_effort: int                  # microseconds
    cycle: int                          # microseconds
    islands: tuple[tuple[int, int], ...]
    gaps: tuple[int, ...]
    max_concurrent_people: int
    crew_profile: dict[int, int]        # concurrent owners -> microseconds
    excluded_voided: int
    superseded: int = 0
    invalid: int = 0
    unit: str = "microseconds"

    @property
    def active_elapsed_minutes(self) -> Fraction:
        return minutes_of(self.active_elapsed)

    @property
    def person_effort_minutes(self) -> Fraction:
        return minutes_of(self.person_effort)

    @property
    def cycle_minutes(self) -> Fraction:
        return minutes_of(self.cycle)


def episode_metrics(episode: str, records: Iterable[Mapping[str, Any]]) -> EpisodeMetrics:
    """Metrics from raw clock rows for one episode (latest revision per event, voided removed)."""
    admitted, superseded = admit_latest_revisions(records)
    kept: list[tuple[int, int, str]] = []
    voided = invalid = 0
    for row in admitted:
        if row.get("IS_VOIDED"):
            voided += 1
            continue
        s = to_micros(parse_utc(str(row["START_UTC"])))
        e = to_micros(parse_utc(str(row["END_UTC"])))
        if e <= s:
            invalid += 1                 # zero/negative intervals carry no time; counted, not silently fixed
            continue
        kept.append((s, e, str(row["PERSON_KEY"])))
    if not kept:
        return EpisodeMetrics(episode, 0, 0, 0, (), (), 0, {}, voided, superseded, invalid)
    islands = interval_union((s, e) for s, e, _ in kept)
    active = sum(e - s for s, e in islands)
    owners: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for s, e, o in kept:
        owners[o].append((s, e))
    owner_unions = {o: interval_union(iv) for o, iv in owners.items()}
    effort = sum(e - s for iv in owner_unions.values() for s, e in iv)
    cycle = max(e for _, e, _ in kept) - min(s for s, _, _ in kept)
    events: list[tuple[int, int]] = []
    for iv in owner_unions.values():
        for s, e in iv:
            events.append((s, 1))
            events.append((e, -1))
    events.sort()
    profile: dict[int, int] = defaultdict(int)
    level = 0
    last = events[0][0]
    peak = 0
    for t, delta in events:
        if t > last and level > 0:
            profile[level] += t - last
        level += delta
        peak = max(peak, level)
        last = t
    gaps = tuple(islands[i + 1][0] - islands[i][1] for i in range(len(islands) - 1))
    return EpisodeMetrics(episode, active, effort, cycle, tuple(islands), gaps, peak, dict(profile), voided, superseded, invalid)


def continuous_percentile(values: Sequence[Fraction], p: Fraction) -> Fraction:
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    h = (len(ordered) - 1) * p
    lo = math.floor(h)
    hi = math.ceil(h)
    return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])


def _dec(value: Fraction) -> Decimal:
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(_Q, rounding=ROUND_HALF_EVEN)


def quantile_interval(ordered: Sequence[Fraction], p: Fraction, level: Fraction) -> tuple[Fraction, Fraction, Fraction] | None:
    """Distribution-free order-statistic interval for the p-quantile.

    Returns (x_(l), x_(u), coverage) with 1-indexed ranks l < u such that
    P(l <= B <= u-1) >= level for B ~ Binomial(n, p), grown from the modal rank
    toward the heavier neighbour (ties extend downward). Exact integer arithmetic.
    None when the sample cannot reach the level (or n > 5000, not computed).
    """
    n = len(ordered)
    if n < 2 or n > 5000:
        return None
    num, den = p.numerator, p.denominator
    weights = [math.comb(n, k) * num ** k * (den - num) ** (n - k) for k in range(n + 1)]
    total = den ** n
    lo = min(max(1, (n * num) // den), n - 1)
    hi = lo + 1
    covered = weights[lo]
    while covered * level.denominator < level.numerator * total:
        down = weights[lo - 1] if lo - 1 >= 1 else -1
        up = weights[hi] if hi <= n - 1 else -1
        if down < 0 and up < 0:
            return None
        if down >= up:
            lo -= 1
            covered += down
        else:
            covered += up
            hi += 1
    return ordered[lo - 1], ordered[hi - 1], Fraction(covered, total)


@dataclass(frozen=True)
class DurationStatistics:
    id: str
    run_id: str
    group: tuple[str, ...]              # exact ("NC", "PROD-B", "MODEL=787", "CREW=2"), broader ("NC", "PROD-B") or ("NC",)
    group_key_sha: str
    measurement_basis: str              # ELAPSED_ACTIVE (never person effort / crew)
    raw_count: int
    admitted_count: int
    excluded_count: int
    excluded_reasons: dict[str, int]
    mean: Decimal
    p50: Decimal
    p55: Decimal
    p75: Decimal
    p90: Decimal
    sample_start: datetime
    sample_end: datetime
    method_sha: str
    policy_id: str
    minimum_sample: int
    sufficient: bool
    crew_values: tuple[int, ...]
    effort_mean: Decimal | None
    stale: bool = False
    unit: str = "minutes"
    models: tuple[str, ...] = ()
    source_sha: str = ""                # admitted episode keys and values
    query_sha: str = ""                 # producing query / dataset contract
    confidence_level: Decimal | None = None
    p75_ci_low: Decimal | None = None
    p75_ci_high: Decimal | None = None

    @property
    def usable(self) -> bool:
        return self.sufficient and not self.stale

    def statistic(self, key: str) -> Decimal:
        return {"P50": self.p50, "P55": self.p55, "MEAN": self.mean, "P75": self.p75, "P90": self.p90}[key]


METHOD = ("continuous-percentile h=(n-1)p; exact Fraction; elapsed-active completed comparable episodes; half-even 6dp persistence; "
          "order-statistic binomial interval for P75")
METHOD_SHA = content_hash(METHOD)


def exact_group(cls: str, team: str, model: Any, crew: Any) -> tuple[str, ...] | None:
    """The exact comparable context of an episode or a job (S08): class, team, model and crew. None if model or crew is unknown.

    The spec's context also names process and revision. No source contract carries them yet (duration_episodes,
    clock observations and the job census have no such column), so they cannot be matched and are not invented.
    """
    if model in (None, "") or crew in (None, ""):
        return None
    return (cls, team, f"MODEL={model}", f"CREW={int(crew)}")


def compute_statistics(episodes: Iterable[Mapping[str, Any]], *, run_id: str, policy_id: str, minimum_sample: int,
                       as_of: datetime | None = None, stale_after_days: int | None = None, query_sha: str = "",
                       confidence_level: Fraction | None = None) -> list[DurationStatistics]:
    """Group completed episodes by exact context (class, team, model, crew), by (class, team) and by (class); persist P50/P55/mean/P75/P90.

    Admission: STATUS ``verified_complete``, a positive parseable elapsed value and a completion
    instant. Reopened/unverified episodes are censored (excluded with their reason), so a
    current-master survivor view cannot leak in. A cohort is stale when its newest completion
    is more than ``stale_after_days`` before ``as_of``.
    """
    grouped: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in episodes:
        cls, team = str(row["REWORK_CLASS"]), str(row.get("TEAM") or "")
        grouped[(cls, team)].append(row)
        grouped[(cls,)].append(row)
        exact = exact_group(cls, team, row.get("MODEL"), row.get("CREW"))
        if exact is not None:
            grouped[exact].append(row)
    out: list[DurationStatistics] = []
    for group, rows in sorted(grouped.items()):
        admitted: list[Fraction] = []
        efforts: list[Fraction] = []
        crews: list[int] = []
        models: set[str] = set()
        excluded: dict[str, int] = defaultdict(int)
        times: list[datetime] = []
        source: list[list[str]] = []
        for row in rows:
            status = str(row.get("STATUS"))
            try:
                elapsed = Fraction(str(row["ELAPSED_MINUTES"]))
            except (ValueError, ZeroDivisionError):
                excluded["ELAPSED_INVALID"] += 1
                continue
            if status != "verified_complete":
                excluded[f"STATUS_{status.upper()}"] += 1
                continue
            if elapsed <= 0:
                excluded["ELAPSED_NONPOSITIVE"] += 1
                continue
            if not row.get("COMPLETED_AT_UTC"):
                excluded["COMPLETED_AT_MISSING"] += 1
                continue
            admitted.append(elapsed)
            times.append(parse_utc(str(row["COMPLETED_AT_UTC"])))
            source.append([str(row.get("EPISODE_KEY")), str(elapsed), str(row["COMPLETED_AT_UTC"])])
            if row.get("PERSON_EFFORT_MINUTES") not in (None, ""):
                efforts.append(Fraction(str(row["PERSON_EFFORT_MINUTES"])))
            if row.get("CREW") is not None:
                crews.append(int(row["CREW"]))
            if row.get("MODEL"):
                models.add(str(row["MODEL"]))
        if not admitted:
            continue
        ordered = sorted(admitted)
        mean = sum(admitted, Fraction(0)) / len(admitted)
        stats = {key: continuous_percentile(ordered, Fraction(int(key[1:]), 100)) for key in ("P50", "P55", "P75", "P90")}
        interval = quantile_interval(ordered, Fraction(3, 4), confidence_level) if confidence_level else None
        out.append(DurationStatistics(
            id=digest_id("STAT", run_id, policy_id, *group),   # policy-bound: minimum sample, staleness and confidence are policy
            run_id=run_id,
            group=group,
            group_key_sha=content_hash(list(group)),
            measurement_basis="ELAPSED_ACTIVE",
            raw_count=len(rows),
            admitted_count=len(admitted),
            excluded_count=len(rows) - len(admitted),
            excluded_reasons=dict(sorted(excluded.items())),
            mean=_dec(mean),
            p50=_dec(stats["P50"]),
            p55=_dec(stats["P55"]),
            p75=_dec(stats["P75"]),
            p90=_dec(stats["P90"]),
            sample_start=min(times),
            sample_end=max(times),
            method_sha=METHOD_SHA,
            policy_id=policy_id,
            minimum_sample=minimum_sample,
            sufficient=len(admitted) >= minimum_sample,
            crew_values=tuple(sorted(set(crews))),
            effort_mean=_dec(sum(efforts, Fraction(0)) / len(efforts)) if efforts else None,
            stale=bool(as_of is not None and stale_after_days is not None and (as_of - max(times)).days > stale_after_days),
            models=tuple(sorted(models)),
            source_sha=content_hash(sorted(source)),
            query_sha=query_sha,
            confidence_level=_dec(confidence_level) if interval else None,
            p75_ci_low=_dec(interval[0]) if interval else None,
            p75_ci_high=_dec(interval[1]) if interval else None,
        ))
    return out


def scheduled_ticks(value: Decimal) -> int:
    """Quantize an estimated elapsed statistic to scheduling minutes by ceiling (after estimation)."""
    return int(value.to_integral_value(rounding="ROUND_CEILING"))


def cli_duration_stats(services: Any, args: Any) -> dict[str, Any]:
    """Compute and persist duration statistics for an admitted source run (fixture/SQL-compatible)."""
    from ..codec import to_jsonable
    from .acquisition import load_run_datasets

    repos = services.repositories
    policy = services.policy
    datasets, run = load_run_datasets(repos, args.source_run)
    if run is None:
        return {"status": "invalid", "error": {"code": "SOURCE_RUN_NOT_FOUND", "message": args.source_run}}
    queries = {d.name: d.query_sha for d in run.datasets}
    stats = compute_statistics(datasets.get("duration_episodes", ()), run_id=run.id, policy_id=policy.id,
                               minimum_sample=policy.int("duration.minimum_fixture_sample"), as_of=run.cutoff,
                               stale_after_days=policy.int("duration.stale_after_days"),
                               query_sha=queries.get("duration_episodes", ""),
                               confidence_level=Fraction(policy.str("duration.confidence_level")))
    ids = []
    for stat in stats:
        repos.put_record("statistics", "DurationStatistics", stat.id, stat, source_run_id=run.id, policy_hash=policy.hash)
        ids.append(stat.id)
    union = {}
    for episode in sorted({str(r["EPISODE_KEY"]) for r in datasets.get("clock_observations", ())}):
        rows = [r for r in datasets["clock_observations"] if str(r["EPISODE_KEY"]) == episode]
        m = episode_metrics(episode, rows)
        union[episode] = {"active_elapsed_minutes": display_minutes(m.active_elapsed), "person_effort_minutes": display_minutes(m.person_effort),
                          "cycle_minutes": display_minutes(m.cycle), "gaps_minutes": [display_minutes(g) for g in m.gaps],
                          "max_concurrent_people": m.max_concurrent_people, "voided_excluded": m.excluded_voided,
                          "superseded_revisions": m.superseded, "quantization": "6 dp half away from zero (display only)"}
    return {
        "status": "ok",
        "source_run_id": run.id,
        "measurement": args.measurement,
        "statistics": [to_jsonable({"id": s.id, "group": list(s.group), "n": s.admitted_count, "excluded": s.excluded_count,
                                    "excluded_reasons": s.excluded_reasons, "mean": s.mean, "p50": s.p50, "p55": s.p55, "p75": s.p75,
                                    "p90": s.p90, "p75_ci": [s.p75_ci_low, s.p75_ci_high, s.confidence_level],
                                    "window": [s.sample_start, s.sample_end], "models": list(s.models), "crews": list(s.crew_values),
                                    "sufficient": s.sufficient, "stale": s.stale, "unit": s.unit,
                                    "source_sha": s.source_sha, "query_sha": s.query_sha, "method_sha": s.method_sha}) for s in stats],
        "statistics_ids": ids,
        "clock_union_examples": union,
    }
