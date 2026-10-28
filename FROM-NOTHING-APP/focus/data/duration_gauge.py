"""Duration gauge: raw-clock islands, episode metrics and descriptive statistics (S08, P07).

Island method (running MAX, not comparison with the previous raw end):
nested clocks [0,60], [10,20], [60,90] form one 90-minute island; adding
[100,110] gives active elapsed 100 with the 10-minute gap preserved. Short gaps
are never bridged, long clocks are never capped and short clocks are never
dropped. Job union gives active elapsed; each owner's union summed gives person
effort; first-on to last-off gives cycle. An actor auditing a clock is not
necessarily its owner.

Continuous percentiles use h=(n-1)p, lo=floor(h), hi=ceil(h),
x_lo + (h-lo)(x_hi-x_lo), computed exactly with Fractions. P50, P55, mean, P75
and P90 are persisted with n, window, group, method and policy provenance.
The mean need not lie between P55 and P75.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

from ..codec import content_hash, parse_utc
from ..domain import digest_id

UTC = timezone.utc
STAT_KEYS = ("P50", "P55", "MEAN", "P75", "P90")
_Q = Decimal("0.000001")


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
    episode: str
    active_elapsed: int
    person_effort: int
    cycle: int
    islands: tuple[tuple[int, int], ...]
    gaps: tuple[int, ...]
    max_concurrent_people: int
    crew_profile: dict[int, int]       # concurrent people -> minutes
    excluded_voided: int
    unit: str = "seconds"


def episode_metrics(episode: str, records: Iterable[Mapping[str, Any]]) -> EpisodeMetrics:
    """Metrics from raw clock rows (seconds resolution) for one episode."""
    kept: list[tuple[int, int, str]] = []
    voided = 0
    for row in records:
        if row.get("IS_VOIDED"):
            voided += 1
            continue
        s = int(parse_utc(str(row["START_UTC"])).timestamp())
        e = int(parse_utc(str(row["END_UTC"])).timestamp())
        if e <= s:
            continue
        kept.append((s, e, str(row["PERSON_KEY"])))
    if not kept:
        return EpisodeMetrics(episode, 0, 0, 0, (), (), 0, {}, voided)
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
    return EpisodeMetrics(episode, active, effort, cycle, tuple(islands), gaps, peak, dict(profile), voided)


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


@dataclass(frozen=True)
class DurationStatistics:
    id: str
    run_id: str
    group: tuple[str, ...]              # ("NC", "PROD-B") or ("NC",)
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
    unit: str = "minutes"

    def statistic(self, key: str) -> Decimal:
        return {"P50": self.p50, "P55": self.p55, "MEAN": self.mean, "P75": self.p75, "P90": self.p90}[key]


METHOD = "continuous-percentile h=(n-1)p; exact Fraction; elapsed-active completed comparable episodes; half-even 6dp persistence"
METHOD_SHA = content_hash(METHOD)


def compute_statistics(episodes: Iterable[Mapping[str, Any]], *, run_id: str, policy_id: str, minimum_sample: int) -> list[DurationStatistics]:
    """Group completed episodes by (class, team) and by (class); persist P50/P55/mean/P75/P90."""
    grouped: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in episodes:
        cls = str(row["REWORK_CLASS"])
        grouped[(cls, str(row.get("TEAM") or ""))].append(row)
        grouped[(cls,)].append(row)
    out: list[DurationStatistics] = []
    for group, rows in sorted(grouped.items()):
        admitted: list[Fraction] = []
        efforts: list[Fraction] = []
        crews: list[int] = []
        excluded: dict[str, int] = defaultdict(int)
        times: list[datetime] = []
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
            admitted.append(elapsed)
            if row.get("PERSON_EFFORT_MINUTES") not in (None, ""):
                efforts.append(Fraction(str(row["PERSON_EFFORT_MINUTES"])))
            if row.get("CREW") is not None:
                crews.append(int(row["CREW"]))
            if row.get("COMPLETED_AT_UTC"):
                times.append(parse_utc(str(row["COMPLETED_AT_UTC"])))
        if not admitted:
            continue
        mean = sum(admitted, Fraction(0)) / len(admitted)
        stats = {key: continuous_percentile(admitted, Fraction(int(key[1:]), 100)) for key in ("P50", "P55", "P75", "P90")}
        group_sha = content_hash(list(group))
        out.append(DurationStatistics(
            id=digest_id("STAT", run_id, *group),
            run_id=run_id,
            group=group,
            group_key_sha=group_sha,
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
            sample_start=min(times) if times else datetime(1970, 1, 1, tzinfo=UTC),
            sample_end=max(times) if times else datetime(1970, 1, 1, tzinfo=UTC),
            method_sha=METHOD_SHA,
            policy_id=policy_id,
            minimum_sample=minimum_sample,
            sufficient=len(admitted) >= minimum_sample,
            crew_values=tuple(sorted(set(crews))),
            effort_mean=_dec(sum(efforts, Fraction(0)) / len(efforts)) if efforts else None,
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
    stats = compute_statistics(datasets.get("duration_episodes", ()), run_id=run.id, policy_id=policy.id,
                               minimum_sample=policy.int("duration.minimum_fixture_sample"))
    ids = []
    for stat in stats:
        repos.put_record("statistics", "DurationStatistics", stat.id, stat, source_run_id=run.id, policy_hash=policy.hash)
        ids.append(stat.id)
    union = {}
    for episode in sorted({str(r["EPISODE_KEY"]) for r in datasets.get("clock_observations", ())}):
        rows = [r for r in datasets["clock_observations"] if str(r["EPISODE_KEY"]) == episode]
        m = episode_metrics(episode, rows)
        union[episode] = {"active_elapsed_minutes": m.active_elapsed // 60, "person_effort_minutes": m.person_effort // 60,
                          "cycle_minutes": m.cycle // 60, "gaps_minutes": [g // 60 for g in m.gaps], "voided_excluded": m.excluded_voided}
    return {
        "status": "ok",
        "source_run_id": run.id,
        "measurement": args.measurement,
        "statistics": [to_jsonable({"id": s.id, "group": list(s.group), "n": s.admitted_count, "excluded": s.excluded_count,
                                    "mean": s.mean, "p50": s.p50, "p55": s.p55, "p75": s.p75, "p90": s.p90,
                                    "sufficient": s.sufficient}) for s in stats],
        "statistics_ids": ids,
        "clock_union_examples": union,
    }
