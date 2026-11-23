"""PS: how similar is a shift's task list from one pre-shift run to the next?

Usage: plan_stability.py SIM_DB OUT.json            (read-only on a stored simulation database)

The owner's requirement (D-33): the run before a shift plans the whole flow, so it also plans the shifts after it;
when the next run comes, its task list for a shift should be similar to the one the run before made for that shift.

For every pair of runs ``lead`` shifts apart, this compares what each planned for the same shift:

* the **earlier** list: what the run ``lead`` shifts before the shift planned for it;
* the **final** list: what the run just before the shift planned for it, which is the plan crews are dispatched from.

A line of a list is one piece of one job: its work (a long job is planned as several pieces) or its final inspection.
Two lists agree on a line when both hold that piece in that shift; "same team" and "same start" narrow that further.

A line the final list dropped is classed, first match wins: the job was completed before the shift started; the final
run planned it in a later shift; the final run planned it earlier than the shift (work that ran late); the final run
did not plan it at all. A line the final list added is classed the same way from the earlier plan's side.

Each run makes two plans: the shift plan (EXECUTABLE: only work that can really start, the plan crews are dispatched
from) and the delivery plan (FORECAST: all work through delivery, where dates and late cost are worked out). Two more
comparisons say whether they are one schedule:

* **within a run**: the shift plan's list for the shift about to start against the delivery plan's list for that shift;
* **across runs**: the list the delivery plan made for a shift ``lead`` shifts ahead against the list the shift plan
  issued for it when that shift came.

Aircraft finish dates are compared between consecutive FORECAST plans: how far the planned finish of the same
aircraft moved from one run to the next.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

db, out_path = sys.argv[1], sys.argv[2]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
LEADS = (1, 2, 3, 6, 9)                                           # shifts between the two runs; 3 is one day

shifts = [(n, sid) for n, sid in con.execute("select shift_no, shift_id from sim_shift order by shift_no")]
order = {sid: n for n, sid in shifts}                             # a simulated shift's place in the run order


def place(shift_id: str) -> tuple[str, int]:
    """Sort key of any shift id, simulated or beyond the simulated days: its production day and shift number."""
    _, day, number = shift_id.rsplit(":", 2)
    return day, int(number[1:])


def plan(run: int, purpose: str) -> dict[tuple[str, str, int], tuple[str, str, str]]:
    """(job, phase kind, pieces after it) -> (shift id, team, start) for one run's plan. A long job is planned as several
    pieces; a piece is named by how many pieces of the job follow it, which stays the same when the first pieces get done."""
    out: dict[tuple[str, str, int], tuple[str, str, str]] = {}
    seen: Counter[tuple[str, str]] = Counter()
    for job, kind, sid, team, start in con.execute(
            "select job_id, kind, shift_id, team, start_utc from plan_assignment where shift_no = ? and purpose = ? "
            "order by job_id, kind, start_utc desc", (run, purpose)):
        out[(job, kind, seen[(job, kind)])] = (sid, team, start)
        seen[(job, kind)] += 1
    return out


done_by: dict[str, int] = {}                                      # job -> the shift it was completed in
for n, job in con.execute("select shift_no, job_id from exec_assignment where outcome = 'completed'"):
    done_by.setdefault(job, n)
for n, job in con.execute("select shift_no, job_id from exec_offplan"):
    done_by[job] = min(n, done_by.get(job, n))


def pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def spread(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "median": None, "lowest": None, "highest": None}
    return {"n": len(values), "median": round(statistics.median(values), 1), "lowest": round(min(values), 1),
            "highest": round(max(values), 1)}


PLANS: dict[str, dict] = {}


def plans_of(purpose: str) -> dict:
    if purpose not in PLANS:
        PLANS[purpose] = {n: plan(n, purpose) for n, _ in shifts}
    return PLANS[purpose]


def compare(earlier_purpose: str, final_purpose: str, leads: tuple[int, ...] = LEADS) -> dict[str, object]:
    """The list a run's ``earlier_purpose`` plan made for a shift against the list the ``final_purpose`` plan of the run
    just before that shift made for it. A lead of 0 compares the two plans of one run for the shift about to start."""
    earlier_plans, final_plans = plans_of(earlier_purpose), plans_of(final_purpose)
    result: dict[str, object] = {}
    for lead in leads:
        kept_of_final: list[float] = []
        kept_of_earlier: list[float] = []
        same_team: list[float] = []
        same_start: list[float] = []
        total: Counter[str] = Counter()
        dropped: Counter[str] = Counter()
        added: Counter[str] = Counter()
        for target, target_id in shifts:
            earlier_run = target - lead
            if earlier_run < 0:
                continue
            earlier, final = earlier_plans[earlier_run], final_plans[target]
            want = place(target_id)
            earlier_list = {k for k, v in earlier.items() if v[0] == target_id}
            final_list = {k for k, v in final.items() if v[0] == target_id}
            if not earlier_list or not final_list:
                continue
            both = earlier_list & final_list
            kept_of_final.append(100.0 * len(both) / len(final_list))
            kept_of_earlier.append(100.0 * len(both) / len(earlier_list))
            same_team.append(100.0 * sum(1 for k in both if earlier[k][1] == final[k][1]) / len(both) if both else 0.0)
            same_start.append(100.0 * sum(1 for k in both if earlier[k][2] == final[k][2]) / len(both) if both else 0.0)
            total["pairs"] += 1
            total["earlier"] += len(earlier_list)
            total["final"] += len(final_list)
            total["both"] += len(both)
            for key in earlier_list - final_list:
                done = done_by.get(key[0])
                if done is not None and done < target:
                    dropped["completed_before_the_shift"] += 1
                elif key not in final:
                    dropped["not_planned_by_the_final_run"] += 1
                elif place(final[key][0]) > want:
                    dropped["planned_in_a_later_shift"] += 1
                else:
                    dropped["planned_earlier_than_the_shift"] += 1
            for key in final_list - earlier_list:
                if key not in earlier:
                    added["not_planned_by_the_earlier_run"] += 1
                elif place(earlier[key][0]) < want:
                    added["carried_over_from_an_earlier_shift"] += 1
                else:
                    added["pulled_in_from_a_later_shift"] += 1
        result[f"lead_{lead}_shifts"] = {
            "pairs": total["pairs"],
            "lines_on_the_earlier_lists": total["earlier"],
            "lines_on_the_final_lists": total["final"],
            "lines_on_both": total["both"],
            "share_of_the_final_list_already_on_the_earlier_list_pct": spread(kept_of_final),
            "share_of_the_earlier_list_still_on_the_final_list_pct": spread(kept_of_earlier),
            "of_the_lines_on_both_same_team_pct": spread(same_team),
            "of_the_lines_on_both_same_start_time_pct": spread(same_start),
            "dropped_from_the_earlier_list": dict(sorted(dropped.items())),
            "added_to_the_final_list": dict(sorted(added.items())),
        }
    return result


def finish_moves() -> dict[str, object]:
    finish: dict[tuple[int, int], tuple[str, str | None]] = {
        (n, line): (status, completion) for n, line, status, completion in con.execute(
            "select shift_no, line, status, completion_utc from plan_aircraft where purpose = 'FORECAST'")}
    moved_hours: list[float] = []
    states: Counter[str] = Counter()
    for (n, line), (status, completion) in finish.items():
        before = finish.get((n - 1, line))
        if before is None:
            continue
        if not completion or not before[1]:
            states["finish_unknown_in_one_of_the_two"] += 1
            continue
        delta = (datetime.fromisoformat(completion) - datetime.fromisoformat(before[1])).total_seconds() / 3600.0
        moved_hours.append(delta)
        states["compared"] += 1
    absolute = sorted(abs(h) for h in moved_hours)

    def at(q: float) -> float | None:
        return round(absolute[min(len(absolute) - 1, int(q * len(absolute)))], 1) if absolute else None

    return {
        "aircraft_run_pairs": dict(sorted(states.items())),
        "finish_unchanged": sum(1 for h in moved_hours if h == 0.0),
        "finish_later": sum(1 for h in moved_hours if h > 0.0),
        "finish_earlier": sum(1 for h in moved_hours if h < 0.0),
        "moved_by_hours_absolute": {"median": at(0.5), "p90": at(0.9), "highest": at(1.0)},
        "moved_within_one_shift_pct": pct(sum(1 for h in absolute if h <= 8.5), len(absolute)),
        "moved_within_one_day_pct": pct(sum(1 for h in absolute if h <= 24.0), len(absolute)),
    }


def commitments() -> dict[str, object]:
    out: dict[str, object] = {}
    for purpose in ("EXECUTABLE", "FORECAST"):
        per_run: dict[int, Counter[str]] = defaultdict(Counter)
        for n, outcome, count in con.execute(
                "select shift_no, outcome, sum(n) from method_commitment where purpose = ? group by 1, 2", (purpose,)):
            per_run[n][outcome] += count
        kept: list[float] = []
        totals: Counter[str] = Counter()
        for n, counts in sorted(per_run.items()):
            totals.update(counts)
            defended = counts["KEPT"] + counts["MOVED"] + counts["CREW_CHANGED"] + counts["UNSAFE"]
            if defended:
                kept.append(100.0 * counts["KEPT"] / defended)
        out[purpose] = {"outcomes": dict(sorted(totals.items())),
                        "kept_at_the_same_slot_and_crew_pct_of_open_defended": spread(kept)}
    return out


spec = json.loads(con.execute("select spec from sim_run").fetchone()[0])
stages = Counter(stage for (stage,) in con.execute("select stage from method_stage"))
report = {
    "what": "How similar a shift's task list is from one pre-shift run to the next, in a stored simulation",
    "simulation": {"aircraft": spec["fleet"]["n_aircraft"], "days": spec["days"], "shifts": len(shifts),
                   "max_moves": spec.get("max_moves"), "max_repairs": spec.get("max_repairs"),
                   "every_run_is_a_full_solve": not {"repair", "repair_redispatch"} & set(stages),
                   "delivery_plan_follows_the_shift_plan": bool(json.loads(con.execute("select facts from sim_run").fetchone()[0])
                                                                .get("delivery_plan_follows_the_shift_plan", False)),
                   "stages_recorded": dict(sorted(stages.items()))},
    "leads": {"0": "the same run", "1": "the run before the shift before", "3": "one day earlier", "6": "two days",
              "9": "three days"},
    "reading": {"executable": "earlier and final lists both from shift plans",
                "forecast": "earlier and final lists both from delivery plans",
                "delivery_plan_against_...": "the earlier list is the delivery plan's, the final list is the shift plan's"},
    "executable": compare("EXECUTABLE", "EXECUTABLE"),
    "forecast": compare("FORECAST", "FORECAST"),
    "delivery_plan_against_the_shift_plan_of_the_same_run": compare("FORECAST", "EXECUTABLE", (0,))["lead_0_shifts"],
    "delivery_plan_against_the_shift_plan_issued_later": compare("FORECAST", "EXECUTABLE", (1, 2, 3)),
    "aircraft_finish_between_consecutive_forecasts": finish_moves(),
    "commitments": commitments(),
}
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=1)
    handle.write("\n")
head = report["executable"]["lead_1_shifts"]
same = report["delivery_plan_against_the_shift_plan_of_the_same_run"]
print(json.dumps({"shift_plan_run_to_run": head["share_of_the_final_list_already_on_the_earlier_list_pct"],
                  "shift_plan_lines_the_delivery_plan_of_the_same_run_has_in_that_shift":
                      same["share_of_the_final_list_already_on_the_earlier_list_pct"]}))
