"""D-26: each shift is scored against the plan published for it, and credit is never taken early."""
from __future__ import annotations

from datetime import date, datetime, timezone

from focus.performance.lifecycle import GoalRecord, GoalRegistry, select_goal, shift_window

UTC = timezone.utc
DAY = date(2026, 10, 26)
S1, S2, S3 = (f"DEMO:{DAY.isoformat()}:S{n}" for n in (1, 2, 3))   # S1 10:00-18:30, S2 18:30-03:00, S3 03:00-10:00 UTC


def at(hhmm: str, day: int = 26) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def registry(policy, plans: list[tuple[str, datetime]]) -> GoalRegistry:
    reg = GoalRegistry("DEMO-787", "DEMO", policy)
    for plan_id, accepted in plans:
        reg._covers(plan_id, accepted, (DAY,))
    return reg


def test_the_plan_accepted_before_each_shift_is_its_plan_of_record(policy) -> None:
    reg = registry(policy, [("P-S1", at("09:50")), ("P-S2", at("18:20")), ("P-S3", at("02:50", 27))])
    assert [reg.plan_of_record(s) for s in (S1, S2, S3)] == ["P-S1", "P-S2", "P-S3"]


def test_a_plan_accepted_after_the_start_is_a_proposal_until_the_next_shift(policy) -> None:
    reg = registry(policy, [("P-S1", at("09:50")), ("LATE", at("10:30"))])
    assert reg.plan_of_record(S1) == "P-S1"                                     # promoted after S1 started
    assert reg.plan_of_record(S2) == reg.plan_of_record(S3) == "LATE"            # the latest plan before S2 and S3
    first_late = registry(policy, [("LATE", at("10:30")), ("LATER", at("11:00"))])
    assert first_late.plan_of_record(S1) == "LATE"                              # nothing before the start: the first stands in
    assert registry(policy, []).plan_of_record(S1) is None


def record(policy, goal: str, shift_id: str) -> GoalRecord:
    return GoalRecord(goal, "P", "PROD-A", shift_id, shift_window(policy, shift_id), None)


def test_credit_is_the_shift_window_then_its_overtime_never_early(policy) -> None:
    r1, r2 = record(policy, "G1", S1), record(policy, "G2", S2)
    assert select_goal(at("12:00"), [r1, r2]) is r1
    assert select_goal(at("19:00"), [r1]) is r1                                 # in S1's authorized overtime
    assert select_goal(at("19:00"), [r1, r2]) is r2                             # the next plan put the job in S2: S2's own window first
    assert select_goal(at("12:00"), [r2]) is None                               # before S2: never credited early, offplan where it happened
