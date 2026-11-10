"""Performance over any period (D-25), on synthetic team-shift rows: the arithmetic, the periods, the groups and the jobs.

* a group's score is the one canonical formula applied to its summed components (teams weigh by planned work);
* a team-shift with no score makes the official result unknown, beside a labeled partial score;
* periods (ISO week, rolling 30 days, month, quarter, year, custom) and breakdowns cover the right days;
* superintendent attribution follows the org chart over time, and a group outside the caller's scope is refused;
* the job patterns add each shop order id's outcomes across aircraft.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

from focus.data.org import build_org_chart, superintendent_at
from focus.performance import rollup as R
from focus.performance.score import formula


@pytest.fixture(scope="module")
def services(tmp_path_factory, app_root):
    from focus.clock import FIXTURE_CUTOFF, FixedClock
    from focus.config import Settings
    from focus.data.repositories import Repositories
    from focus.data.store import FixtureStore
    from focus.runtime import build_services

    settings = Settings.from_env({}, app_root=app_root, inprocess_worker=False)
    clock = FixedClock(FIXTURE_CUTOFF)
    root = tmp_path_factory.mktemp("rollup_unit") / "runtime"
    return build_services(settings, repositories=Repositories(FixtureStore(root), mode="fixture", scope_key=settings.scope_key, clock=clock),
                          clock=clock)


def row(services, team, day, number, *, B, Cp, E=0, U=0, Uc=None, O=0, status="demonstration", supt="S1", policy_hash=None) -> dict:
    """``Uc`` is the credited part of the unplanned minutes ``U`` (all of it unless given)."""
    Uc = U if Uc is None else Uc
    return {"team": team, "shift_id": f"DEMO:{day}:S{number}", "day": day, "shift_number": number, "superintendent": supt,
            "superintendent_note": None, "business_unit": services.settings.scope_key, "B0": str(B), "B": str(B), "E": str(E), "Cp": str(Cp),
            "U": None if U is None else str(U), "Uc": None if Uc is None else str(Uc), "O": None if O is None else str(O), "status": status,
            "reasons": [], "evaluation_id": f"EVL-{team}-{day}-{number}",
            "revision": 1, "goal_id": None, "policy_hash": policy_hash or services.policy.hash, "evaluated_at": f"{day}T12:00:00+00:00"}


def expected(services, rows) -> str:
    B, E, Cp = (sum(Fraction(r[k]) for r in rows) for k in ("B", "E", "Cp"))
    Uc, O = (sum(Fraction(r[k]) for r in rows) for k in ("Uc", "O"))
    return str(formula(B, E, Cp, Uc, O, services.policy)["score"])


# ---------------------------------------------------------------------- arithmetic

def test_a_group_sums_components_before_the_one_formula(services) -> None:
    rows = [row(services, "A", "2026-10-26", 1, B=400, Cp=200), row(services, "B", "2026-10-26", 1, B=100, Cp=100)]
    result = R.summarize(rows, services.policy)
    assert result["official"]["score"] == expected(services, rows) == "60.0"                 # not the 75 average of 50 and 100
    one, two = (R.summarize([r], services.policy)["official"]["score"] for r in rows)
    assert (one, two) == ("50.0", "100.0")
    assert (result["official"]["B"], result["official"]["Cp"], result["official"]["D"]) == ("500", "300", "500")


@pytest.mark.parametrize("seed", range(20))
def test_any_group_equals_the_formula_on_its_sums(services, seed) -> None:
    import random

    rng = random.Random(seed)
    rows = []
    for i in range(rng.randint(1, 12)):
        B = rng.randint(1, 900)
        E = rng.randint(0, B // 3)
        Cp = rng.randint(0, B - E)
        U = rng.choice([0, 0, rng.randint(1, 300)])
        rows.append(row(services, f"T{i % 4}", f"2026-10-{20 + i % 7:02d}", 1 + i % 3, B=B, E=E, Cp=Cp, U=U, Uc=rng.randint(0, U),
                        O=rng.choice([0, Fraction(rng.randint(1, 40), 10)])))
    assert R.summarize(rows, services.policy)["official"]["score"] == expected(services, rows)


def test_an_unscored_shift_makes_the_official_result_unknown_beside_a_labeled_partial(services) -> None:
    rows = [row(services, "A", "2026-10-26", 1, B=400, Cp=200), row(services, "B", "2026-10-26", 1, B=100, Cp=100),
            row(services, "C", "2026-10-26", 1, B=300, Cp=0, U=None, status="unknown")]
    result = R.summarize(rows, services.policy)
    assert result["official"]["status"] == "unknown" and result["official"]["score"] is None
    assert any(r.startswith("SHIFTS_NOT_SCORED: 1 of 3") for r in result["official"]["reasons"])
    assert result["partial"]["score"] == "60.0" and "2 of 3 team-shifts scored; 1 missing" in result["partial"]["label"]
    assert R.summarize([rows[2]], services.policy)["partial"] is None                         # nothing scored: no partial either


def test_mixed_policies_and_all_excused_follow_the_canonical_rules(services) -> None:
    mixed = [row(services, "A", "2026-10-26", 1, B=100, Cp=50), row(services, "B", "2026-10-26", 1, B=100, Cp=50, policy_hash="0" * 64)]
    assert R.summarize(mixed, services.policy)["official"]["status"] == "unknown"
    assert "MIXED_POLICY" in " ".join(R.summarize(mixed, services.policy)["official"]["reasons"])
    excused = [row(services, "A", "2026-10-26", 1, B=100, E=100, Cp=0)]
    assert R.summarize(excused, services.policy)["official"]["status"] == "neutral"


def test_the_spread_shows_each_team_on_its_own(services) -> None:
    rows = [row(services, "A", "2026-10-26", 1, B=400, Cp=200), row(services, "B", "2026-10-26", 1, B=100, Cp=100),
            row(services, "C", "2026-10-26", 1, B=100, Cp=90), row(services, "D", "2026-10-26", 1, B=100, Cp=0, U=None, status="unknown")]
    s = R.spread(rows, services.policy, Decimal(80))
    assert (s["team_count"], s["scored"], s["not_scored"], s["on_target"]) == (4, 3, 1, 2)
    assert s["lowest"] == {"team": "A", "score": "50.0"} and s["highest"] == {"team": "B", "score": "100.0"} and s["range"] == "50.0"
    assert s["grades"] == {"A": 2, "F": 1}


# ---------------------------------------------------------------------- periods and breakdowns

@pytest.mark.parametrize("period,anchor,first,last", [
    ("day", "2026-10-28", "2026-10-28", "2026-10-28"),
    ("week", "2026-10-28", "2026-10-26", "2026-11-01"),                 # ISO week, Monday to Sunday
    ("rolling30", "2026-10-28", "2026-09-29", "2026-10-28"),
    ("month", "2026-12-15", "2026-12-01", "2026-12-31"),
    ("month", "2028-02-10", "2028-02-01", "2028-02-29"),
    ("quarter", "2026-11-02", "2026-10-01", "2026-12-31"),
    ("quarter", "2026-02-02", "2026-01-01", "2026-03-31"),
    ("year", "2026-11-02", "2026-01-01", "2026-12-31"),
])
def test_periods_cover_the_right_days(period, anchor, first, last) -> None:
    assert R.period_range(period, date.fromisoformat(anchor)) == (date.fromisoformat(first), date.fromisoformat(last))


def test_a_custom_period_is_validated() -> None:
    assert R.period_range("custom", date(2026, 1, 1), date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 3, 1), date(2026, 3, 31))
    for start, end in ((None, date(2026, 3, 31)), (date(2026, 3, 31), date(2026, 3, 1)), (date(2025, 1, 1), date(2026, 6, 1))):
        with pytest.raises(R.RollupError):
            R.period_range("custom", date(2026, 1, 1), start, end)
    with pytest.raises(R.RollupError):
        R.period_range("fortnight", date(2026, 1, 1))


def test_breakdown_buckets(services) -> None:
    r = row(services, "A", "2026-12-30", 2, B=10, Cp=5)
    assert [R.bucket_of(r, by) for by in ("shift", "day", "week", "month", "quarter")] == [
        "DEMO:2026-12-30:S2", "2026-12-30", "2026-W53", "2026-12", "2026-Q4"]


# ---------------------------------------------------------------------- groups, children and scope

def test_levels_children_and_the_unassigned_superintendent(services) -> None:
    rows = [row(services, "A", "2026-10-26", 1, B=400, Cp=200, supt="S1"), row(services, "B", "2026-10-26", 1, B=100, Cp=100, supt="S1"),
            row(services, "C", "2026-10-27", 2, B=100, Cp=90, supt="S2"), row(services, "D", "2026-10-27", 1, B=100, Cp=10, supt=None)]
    window = {"start": date(2026, 10, 26), "end": date(2026, 11, 1), "all_rows": rows}
    bu = R.rollup(services, level="business_unit", node=None, by="day", **window)
    assert bu["official"]["score"] == expected(services, rows)
    assert [(c["node"], c["teams"]) for c in bu["children"]] == [("S1", ["A", "B"]), ("S2", ["C"]), (R.UNASSIGNED, ["D"])]
    assert [s["bucket"] for s in bu["series"]] == ["2026-10-26", "2026-10-27"]
    s1 = R.rollup(services, level="superintendent", node="S1", **window)
    assert s1["teams"] == ["A", "B"] and s1["official"]["score"] == "60.0" and [c["node"] for c in s1["children"]] == ["A", "B"]
    team = R.rollup(services, level="team", node="C", **window)
    assert team["spread"] is None and [c["node"] for c in team["children"]] == ["DEMO:2026-10-27:S2"]
    assert R.rollup(services, level="team", node="C", shift_number=1, **window)["team_shifts"] == 0
    with pytest.raises(R.RollupError, match="OUT_OF_SCOPE"):
        R.rollup(services, level="superintendent", node="S1", allowed=lambda t: t == "A", **window)
    choices = R.choices_of(services, rows, lambda t: t in ("A", "B", "C"))
    assert choices == {"teams": ["A", "B", "C"], "superintendents": ["S1", "S2"], "business_unit": None}


# ---------------------------------------------------------------------- the org chart

def at(day: int, hour: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def test_the_org_chart_follows_moves_and_flags_mixed_teams() -> None:
    rows = [("p1", "A", "S1", None, at(15)), ("p1", "A", "S2", at(15), None),                    # team A moves to S2 on the 15th
            ("p2", "A", "S1", None, at(15)), ("p2", "A", "S2", at(15), None),
            ("p3", "B", "S1", at(1), None), ("p4", "B", "S3", at(1), None), ("p5", "B", "S3", at(1), None),
            ("p6", "C", "", at(1), None), ("p7", "D", "S1", at(1), at(10))]
    chart = build_org_chart(rows, source_run_id="SR", scope="X")
    assert chart.people == 7
    assert superintendent_at(chart, "A", at(14)) == ("S1", None) and superintendent_at(chart, "A", at(15)) == ("S2", None)
    supt, note = superintendent_at(chart, "B", at(20))
    assert supt == "S3" and note.startswith("MIXED_SUPERINTENDENT: members name S3 (2), S1 (1)")
    assert superintendent_at(chart, "C", at(20))[0] is None and superintendent_at(chart, "C", at(20))[1].startswith("NO_SUPERINTENDENT")
    assert superintendent_at(chart, "D", at(20))[1].startswith("TEAM_NOT_IN_ORG_CHART")         # no member assigned any more
    assert superintendent_at(None, "A", at(20))[1].startswith("NO_ORG_CHART")
    assert len([i for i in chart.intervals if i.team == "A"]) == 2                                # merged where nothing changes


# ---------------------------------------------------------------------- the jobs behind the score

def job_record(team, day, supt, jobs, unplanned=(), no_plan=False) -> dict:
    return {"team": team, "shift_id": f"DEMO:{day}:S1", "shift_number": 1, "superintendent": supt, "evaluation_id": f"E-{team}-{day}",
            "revision": 1, "no_plan": no_plan, "day": day, "jobs": [list(j) for j in jobs], "unplanned": [list(u) for u in unplanned]}


def test_job_patterns_add_a_shop_order_across_aircraft(services) -> None:
    records = [
        job_record("A", "2026-10-26", "S1", [("X1", 1301, "WORK", 0, 120, "missed", 0, "LATE_PART", "planned", None),
                                             ("X2", 1301, "WORK", 0, 60, "done", 1, None, "planned", None)],
                   [("Y9", 1301, "WORK", 0, 30, None)]),
        job_record("A", "2026-10-27", "S1", [("X1", 1302, "WORK", 0, 120, "missed", 0, None, "planned", None),
                                             ("X2", 1302, "WORK", 0, 60, "done", 0, None, "planned", None)]),
        job_record("B", "2026-10-27", "S2", [("X1", 1303, "WORK", 0, 120, "excused", 0, "LATE_PART|PLAN_CHURN", "planned", None),
                                             (None, None, "WORK", 0, 50, "done", 0, None, "added", "Jabc")]),
    ]
    names = {"X1": "Install galley", "X2": "Route harness"}
    out = R.job_patterns(services, level="business_unit", node=None, start=date(2026, 10, 26), end=date(2026, 10, 27), records_names=(records, names))
    first = out["jobs"][0]
    assert (first["soi"], first["name"], first["aircraft"], first["lines"]) == ("X1", "Install galley", 3, [1301, 1302, 1303])
    assert first["count"]["missed"] == 2 and first["count"]["excused"] == 1 and first["missed_on"] == [1301, 1302]
    assert first["causes"] == {"LATE_PART": 2, "PLAN_CHURN": 1}
    by_soi = {j["soi"] or j["job"]: j for j in out["jobs"]}
    assert by_soi["X2"]["count"]["out_of_sequence"] == 1 and by_soi["Y9"]["count"]["unplanned"] == 1 and by_soi["Jabc"]["soi"] is None
    done, missed = out["totals"]["minutes"]["done"], out["totals"]["minutes"]["missed"]
    assert (done, missed, out["totals"]["minutes"]["unplanned"]) == (170, 240, 30)
    assert out["totals"]["adherence_from_jobs"] == str(Fraction(done, done + missed)) == "17/41"    # excused and unplanned work stay out
    per_aircraft = R.job_patterns(services, level="team", node="A", start=date(2026, 10, 26), end=date(2026, 10, 27), group="job_aircraft",
                                  records_names=(records, names))
    assert sorted((j["soi"], j["line"]) for j in per_aircraft["jobs"]) == [("X1", 1301), ("X1", 1302), ("X2", 1301), ("X2", 1302), ("Y9", 1301)]
    missed = R.job_patterns(services, level="superintendent", node="S1", start=date(2026, 10, 26), end=date(2026, 10, 27), outcome="missed",
                            records_names=(records, names))
    assert [j["soi"] for j in missed["jobs"]] == ["X1"]
    with pytest.raises(R.RollupError, match="OUT_OF_SCOPE"):
        R.job_patterns(services, level="business_unit", node=None, start=date(2026, 10, 26), end=date(2026, 10, 27), allowed=lambda t: t == "A",
                       records_names=(records, names))


def test_work_with_no_plan_and_unknown_minutes_follow_the_canonical_score(services) -> None:
    span = {"level": "business_unit", "node": None, "start": date(2026, 10, 26), "end": date(2026, 10, 27)}
    planned = job_record("A", "2026-10-26", "S1", [("X1", 1301, "WORK", 0, 60, "done", 0, None, "planned", None)], [("Y9", 1301, "WORK", 0, 20, None)])
    idle = job_record("B", "2026-10-27", "S1", [], [("Y9", 1302, "WORK", 0, 40, None)], no_plan=True)
    out = R.job_patterns(services, **span, records_names=([planned, idle], {}))
    # a team-shift no plan covered scores neutral and its work counts in no U: listed as no_plan; unplanned work is never in adherence
    assert out["totals"]["minutes"]["no_plan"] == 40 and out["totals"]["minutes"]["unplanned"] == 20
    assert out["totals"]["adherence_from_jobs"] == "1"
    y9 = next(j for j in out["jobs"] if j["soi"] == "Y9")
    assert y9["count"]["no_plan"] == 1 and y9["unplanned_on"] == [1301]
    unknown = job_record("C", "2026-10-27", "S1", [("X1", 1303, "WORK", 0, 60, "done", 0, None, "planned", None)], [("Y8", 1303, "WORK", 0, None, None)])
    out = R.job_patterns(services, **span, records_names=([planned, unknown], {}))
    assert out["totals"]["adherence_from_jobs"] == "1" and out["totals"]["unplanned_minutes_unknown"] == 1   # only the extra-work credit is unknown
    still_open = job_record("D", "2026-10-27", "S1", [("X1", 1304, "WORK", 0, 60, "open", 0, None, "planned", None)])
    assert R.job_patterns(services, **span, records_names=([planned, still_open], {}))["totals"]["adherence_from_jobs"] is None


def test_job_rows_name_jobs_by_shop_order_and_keep_unknown_ones_by_id() -> None:
    from focus.contracts.evaluation import EvaluationEvidence, OffplanFact, UnitFact

    phase = SimpleNamespace(kind=SimpleNamespace(value="WORK"), ordinal=0, line=1301)
    graph = SimpleNamespace(phases={"J1.WORK.0": phase}, job_names={"J1": ("X1", 1301, "Install galley")})
    # every cause on record names the job, proposed or submitted; a withdrawn claim does not
    claims = [SimpleNamespace(status="SUBMITTED", units=("U2",), cause="LATE_PART"), SimpleNamespace(status="PROPOSED", units=("U2",), cause="X"),
              SimpleNamespace(status="WITHDRAWN", units=("U2",), cause="GONE")]
    view = SimpleNamespace(graph=graph)
    facts = (UnitFact("U1", "G", "J1", "J1.WORK.0", 0, "A", 60, "ORIGINAL", "EARNED", at(26), "EV", False, True, ()),
             UnitFact("U2", "G", "J1", "J1.WORK.0", 1, "A", 40, "ORIGINAL", "MISSED", None, None, True, False, ()),
             UnitFact("U3", "G", "J9", "J9.FINAL_QA.0", 0, "A", 45, "AMENDMENT", "PENDING", None, None, False, False, ()))
    offplan = (OffplanFact("O1", "A", "DEMO:2026-10-26:S1", "J1", "J1.WORK.0", 30, at(26), "EV2", ()),)
    evidence = EvaluationEvidence("H", facts, offplan, (), (), {}, (), None)
    from focus.performance import disruption

    original = disruption.effective_claims
    disruption.effective_claims = lambda v, goal: {str(i): c for i, c in enumerate(claims)}
    try:
        jobs, unplanned, names = R.job_rows(evidence, view, "G")
    finally:
        disruption.effective_claims = original
    assert jobs == [["X1", 1301, "WORK", 0, 60, "done", 1, None, "planned", None],
                    ["X1", 1301, "WORK", 0, 40, "excused", 0, "LATE_PART|X", "planned", None],
                    [None, None, "FINAL_QA", 0, 45, "open", 0, None, "added", "J9"]]
    assert unplanned == [["X1", 1301, "WORK", 0, 30, None]] and names == {"X1": "Install galley"}
