"""P07: running-max clock unions, exact continuous percentiles and persisted statistics (A16, A17)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from focus.data.duration_gauge import (compute_statistics, continuous_percentile, display_minutes, episode_metrics, interval_union,
                                       quantile_interval, scheduled_ticks)

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
MIN = 60_000_000          # microseconds per minute


def _clock(key: str, owner: str, start: int, end: int, revision: str = "1", voided: bool = False, **extra) -> dict:
    row = {"SOURCE_EVENT_KEY": key, "SOURCE_REVISION": revision, "PERSON_KEY": owner, "IS_VOIDED": voided,
           "START_UTC": (T0 + timedelta(minutes=start)).isoformat().replace("+00:00", "Z"),
           "END_UTC": (T0 + timedelta(minutes=end)).isoformat().replace("+00:00", "Z")}
    row.update(extra)
    return row


A16 = [_clock("K1", "X", 0, 60), _clock("K2", "Y", 10, 20), _clock("K3", "X", 60, 90), _clock("K4", "Y", 100, 110)]


def test_a16_running_max_union_preserves_gap_and_counts_nested_once() -> None:
    m = episode_metrics("EP", A16)
    assert m.active_elapsed == 100 * MIN and m.active_elapsed_minutes == 100          # [0,90] U [100,110]
    assert m.gaps == (10 * MIN,)                                                     # the 10-minute gap is not bridged
    assert len(m.islands) == 2
    nested = episode_metrics("EP", A16[:3])
    assert nested.active_elapsed_minutes == 90 and len(nested.islands) == 1           # touching [60,90] merges, nested once


def test_a16_owner_unions_give_effort_independently_of_job_union() -> None:
    m = episode_metrics("EP", A16)
    assert m.person_effort_minutes == 110                                            # X: [0,90]=90, Y: 10+10=20
    assert m.cycle_minutes == 110                                                    # first-on 0 to last-off 110
    assert m.max_concurrent_people == 2
    assert {k: Fraction(v, MIN) for k, v in m.crew_profile.items()} == {1: 90, 2: 10}
    audited = [dict(r, ACTOR_KEY="AUDITOR-9") for r in A16]                           # an auditing actor is not the owner
    assert episode_metrics("EP", audited).person_effort == m.person_effort


def test_union_uses_running_max_not_previous_raw_end() -> None:
    # comparing [30,40] with the previous raw end (20) would split the island and double count
    assert interval_union([(0, 100), (10, 20), (30, 40)]) == [(0, 100)]
    assert interval_union([(0, 60), (60, 90)]) == [(0, 90)]                          # touching merges
    assert interval_union([(0, 60), (61, 90)]) == [(0, 60), (61, 90)]                # a 1-unit gap stays


def test_no_cap_no_short_clock_removal_and_corrections_count_once() -> None:
    long = episode_metrics("L", [_clock("L1", "X", 0, 1000)])
    assert long.active_elapsed_minutes == 1000                                       # no 600-minute cap
    short = episode_metrics("S", [_clock("S1", "X", 0, 1), _clock("S2", "X", 5, 6)])
    assert short.active_elapsed_minutes == 2                                         # short clocks kept, gap kept
    corrected = episode_metrics("C", [_clock("E", "X", 0, 60, "1"), _clock("E", "X", 0, 30, "2")])
    assert corrected.active_elapsed_minutes == 30 and corrected.superseded == 1       # the correction replaces, never adds
    voided = episode_metrics("V", [_clock("E", "X", 0, 60, "1"), _clock("E", "X", 0, 60, "2", voided=True), _clock("F", "X", 70, 80)])
    assert voided.active_elapsed_minutes == 10 and voided.excluded_voided == 1        # the voided correction removes the event
    numeric = episode_metrics("N", [_clock("E", "X", 0, 60, "9"), _clock("E", "X", 0, 15, "10")])
    assert numeric.active_elapsed_minutes == 15                                      # revision 10 outranks 9 numerically
    backwards = episode_metrics("B", [_clock("E", "X", 30, 30)])
    assert backwards.active_elapsed == 0 and backwards.invalid == 1


def test_exact_microsecond_minutes_and_labelled_display_quantization() -> None:
    row = {"SOURCE_EVENT_KEY": "Q", "SOURCE_REVISION": "1", "PERSON_KEY": "X", "IS_VOIDED": False,
           "START_UTC": "2026-10-01T12:00:00Z", "END_UTC": "2026-10-01T12:00:10Z"}
    m = episode_metrics("Q", [row])
    assert m.active_elapsed_minutes == Fraction(1, 6)                                # exact, not floored to 0
    assert display_minutes(m.active_elapsed) == Decimal("0.166667")


S18 = [Fraction(v) for v in range(30, 60)]


def test_a17_s18_analytic_percentiles_and_tick() -> None:
    assert sum(S18) / len(S18) == Fraction(89, 2)                                    # mean 44.5
    expected = {Fraction(50, 100): Fraction("44.5"), Fraction(55, 100): Fraction("45.95"),
                Fraction(75, 100): Fraction("51.75"), Fraction(90, 100): Fraction("56.1")}
    for p, value in expected.items():
        assert continuous_percentile(S18, p) == value
    assert scheduled_ticks(Decimal("51.75")) == 52 and scheduled_ticks(Decimal("52")) == 52 and scheduled_ticks(Decimal("52.000001")) == 53
    with pytest.raises(ValueError):
        continuous_percentile([], Fraction(1, 2))


def test_skewed_sample_mean_above_p75_is_not_reordered() -> None:
    skew = [Fraction(v) for v in (10, 10, 10, 10, 10, 10, 10, 400)]
    mean = sum(skew) / len(skew)
    p55, p75 = continuous_percentile(skew, Fraction(55, 100)), continuous_percentile(skew, Fraction(3, 4))
    assert mean == Fraction(470, 8) and p55 == 10 and p75 == 10 and mean > p75     # mean need not sit between P55 and P75


def _episodes(values, cls="NC", team="PROD-B", status="verified_complete", start_day=0, effort=lambda v: 3 * v + 7, crew=2, prefix="E"):
    base = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    return [{"EPISODE_KEY": f"{prefix}{i}", "REWORK_CLASS": cls, "TEAM": team, "MODEL": "9", "ELAPSED_MINUTES": str(v),
             "PERSON_EFFORT_MINUTES": str(effort(v)), "CYCLE_MINUTES": str(v + 10), "STATUS": status, "CREW": crew,
             "COMPLETED_AT_UTC": (base + timedelta(days=start_day + i % 20)).isoformat().replace("+00:00", "Z")}
            for i, v in enumerate(values)]


def test_a17_statistics_persist_all_fields_on_elapsed_basis() -> None:
    rows = _episodes(range(30, 60)) + _episodes([400], status="reopened", prefix="R") + _episodes([400], status="unverified", prefix="U") \
        + _episodes([0], prefix="Z") + [dict(_episodes([45], prefix="M")[0], COMPLETED_AT_UTC=None)]
    stats = {s.group: s for s in compute_statistics(rows, run_id="RUN", policy_id="POL", minimum_sample=30,
                                                     as_of=datetime(2026, 10, 26, 10, tzinfo=UTC), stale_after_days=365,
                                                     query_sha="Q" * 64, confidence_level=Fraction(9, 10))}
    team = stats[("NC", "PROD-B")]
    assert (team.mean, team.p50, team.p55, team.p75, team.p90) == (Decimal("44.500000"), Decimal("44.500000"), Decimal("45.950000"),
                                                                    Decimal("51.750000"), Decimal("56.100000"))
    assert team.measurement_basis == "ELAPSED_ACTIVE" and team.unit == "minutes"
    assert (team.raw_count, team.admitted_count, team.excluded_count) == (34, 30, 4)
    assert team.excluded_reasons == {"COMPLETED_AT_MISSING": 1, "ELAPSED_NONPOSITIVE": 1, "STATUS_REOPENED": 1, "STATUS_UNVERIFIED": 1}
    assert team.sufficient and not team.stale and team.usable
    assert team.models == ("9",) and team.crew_values == (2,) and team.query_sha == "Q" * 64 and len(team.source_sha) == 64
    assert team.sample_start == datetime(2026, 9, 1, 14, tzinfo=UTC) and team.sample_end == datetime(2026, 9, 20, 14, tzinfo=UTC)
    assert (team.p75_ci_low, team.p75_ci_high, team.confidence_level) == (Decimal("48.000000"), Decimal("56.000000"), Decimal("0.900000"))
    # distinct person-effort values are kept descriptively and never become the elapsed statistic
    assert team.effort_mean == Decimal("140.500000") and team.effort_mean / 2 != team.p75
    assert ("NC",) in stats and stats[("NC",)].admitted_count == 30


def test_exact_context_groups_split_by_model_and_crew_and_skip_unknown_context() -> None:
    rows = _episodes(range(30, 60)) + _episodes(range(100, 130), crew=3, prefix="C") + \
        [dict(r, MODEL=None) for r in _episodes(range(30, 40), prefix="N")]
    stats = {s.group: s for s in compute_statistics(rows, run_id="RUN", policy_id="POL", minimum_sample=30)}
    assert set(stats) == {("NC",), ("NC", "PROD-B"), ("NC", "PROD-B", "MODEL=9", "CREW=2"), ("NC", "PROD-B", "MODEL=9", "CREW=3")}
    assert stats[("NC", "PROD-B", "MODEL=9", "CREW=2")].admitted_count == 30 and stats[("NC", "PROD-B", "MODEL=9", "CREW=3")].admitted_count == 30
    assert stats[("NC", "PROD-B")].admitted_count == 70                       # a broader group keeps every episode, unknown model included
    assert stats[("NC", "PROD-B", "MODEL=9", "CREW=3")].p50 == Decimal("114.500000")


def test_statistics_flag_insufficient_and_stale_cohorts() -> None:
    small = compute_statistics(_episodes(range(30, 59)), run_id="RUN", policy_id="POL", minimum_sample=30)
    assert all(not s.sufficient and not s.usable for s in small)                      # 29 < 30
    old = compute_statistics(_episodes(range(30, 60)), run_id="RUN", policy_id="POL", minimum_sample=30,
                             as_of=datetime(2027, 12, 1, tzinfo=UTC), stale_after_days=365)
    assert all(s.sufficient and s.stale and not s.usable for s in old)


def test_source_hash_tracks_admitted_values_and_ids_bind_policy() -> None:
    one = compute_statistics(_episodes(range(30, 60)), run_id="RUN", policy_id="POL", minimum_sample=30)
    changed = _episodes(range(30, 60))
    changed[0]["ELAPSED_MINUTES"] = "31"
    two = compute_statistics(changed, run_id="RUN", policy_id="POL", minimum_sample=30)
    assert one[0].source_sha != two[0].source_sha
    other_policy = compute_statistics(_episodes(range(30, 60)), run_id="RUN", policy_id="POL-P90", minimum_sample=30)
    assert one[0].id != other_policy[0].id and one[0].source_sha == other_policy[0].source_sha


def test_quantile_interval_coverage_and_small_samples() -> None:
    low, high, coverage = quantile_interval(S18, Fraction(3, 4), Fraction(9, 10))
    assert (low, high) == (48, 56) and coverage >= Fraction(9, 10) and low <= continuous_percentile(S18, Fraction(3, 4)) <= high
    assert quantile_interval(S18[:3], Fraction(3, 4), Fraction(9, 10)) is None       # cannot reach 90% with three values
