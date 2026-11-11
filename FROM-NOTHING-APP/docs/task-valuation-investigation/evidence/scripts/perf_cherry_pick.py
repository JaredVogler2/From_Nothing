"""CP analysis: what each measure says about floors that choose their own work, against what happened to the flow.

Usage: perf_cherry_pick.py RUN_DIR OUT.json NAME [NAME ...]      (runs written by cherry_pick.py; the first is the baseline)

For each floor: the outcomes (jobs completed, standard minutes, other teams' work blocked, how long a task left by choice
stayed undone, the delivery forecast at the last shift), the measures a floor could be judged by (job count, throughput,
adherence by count and by minutes, today's score), and the proposed score at several settings: a credit rate ``rho`` for
work done in place of planned work that was left without excuse, and multipliers on planned jobs by tier (MUST: planned
work of this shift or the next waits on it; FILL: nothing depends on it; PLAN: the rest).

    score = 100 x (planned points done + rho x min(off-plan minutes, planned points left without excuse)) / expected points

Then team by team (every production team on every floor is one row): how each measure orders the teams against the work of
other production teams that the team blocked (a rank correlation; a measure that tracks the harm is strongly negative),
each team against itself on the baseline floor, and whether the tiers move a team's score on the floor where nobody chooses.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from collections import defaultdict

RUN_DIR, RESULT, NAMES = sys.argv[1], sys.argv[2], sys.argv[3:]
RHOS = (0.8, 0.5, 0.3, 0.0)
WEIGHTS = {"flat": {"MUST": 1.0, "PLAN": 1.0, "FILL": 1.0}, "tiers_2_1_half": {"MUST": 2.0, "PLAN": 1.0, "FILL": 0.5},
           "tiers_3_1_half": {"MUST": 3.0, "PLAN": 1.0, "FILL": 0.5}}


def floor(name: str) -> dict:
    con = sqlite3.connect(f"file:{RUN_DIR}/{name}.db?mode=ro", uri=True)
    q = lambda sql: con.execute(sql).fetchall()                                        # noqa: E731
    summary = json.load(open(f"{RUN_DIR}/{name}.summary.json"))
    last = q("select max(shift_no) from sim_shift")[0][0]
    tier = {(s, a): (t, g, o) for s, a, t, g, o in q("select shift_no, assignment_id, tier, gated_minutes, gated_other_team_minutes from exec_priority")}
    units = defaultdict(list)                       # (shift, team) -> [(effort, status, relief, tier, cause)]
    for s, team, a, effort, status, relief, cause in q("select shift_no, team, assignment_id, effort, status, relief, cause from perf_unit where team != 'QA'"):
        units[(s, team)].append((effort, status, relief, tier.get((s, a), ("PLAN", 0, 0))[0], cause))
    extra = {(s, team): u or 0 for s, team, u in q("select shift_no, team, U from perf_team_shift where team != 'QA'")}
    oos = {(s, team): o or 0 for s, team, o in q("select shift_no, team, oos_effort from perf_team_shift where team != 'QA'")}
    stored = {(s, team): v for s, team, v in q("select shift_no, team, final from perf_team_shift where team != 'QA'") if v is not None}
    out: dict = {"name": name, "pick": summary["pick"], "share": summary["share"], "shifts": last + 1}
    # ---- outcomes
    out["jobs_completed"] = sum(s["jobs_completed"] for s in summary["per_shift"])
    out["planned_minutes"] = sum(e for rows in units.values() for e, *_ in rows)
    out["planned_done_minutes"] = sum(e for rows in units.values() for e, st, *_ in rows if st == "EARNED")
    out["offplan_minutes"] = sum(extra.values())
    out["throughput_minutes"] = out["planned_done_minutes"] + out["offplan_minutes"]
    out["offplan_jobs"] = q("select count(*) from exec_offplan where team != 'QA'")[0][0]
    out["left_by_choice"] = dict(zip(("jobs", "minutes", "gated_minutes", "gated_other_team_minutes"),
                                     q("select count(*), coalesce(sum(effort),0), coalesce(sum(gated_minutes),0), coalesce(sum(gated_other_team_minutes),0) "
                                       "from exec_priority where left_by_choice = 1")[0]))
    out["blocked_by_another_team_minutes"] = q("select coalesce(sum(effort),0) from perf_unit where claim_cause = 'CROSS_TEAM_PREDECESSOR'")[0][0]
    out["blocked_by_own_team_minutes"] = q("select coalesce(sum(effort),0) from perf_unit where claim_cause = 'SAME_TEAM_PREDECESSOR'")[0][0]
    out["blocked_by_another_team_production_minutes"] = q("select coalesce(sum(effort),0) from perf_unit where claim_cause = 'CROSS_TEAM_PREDECESSOR' and team != 'QA'")[0][0]
    out["blocked_by_another_team_inspection_minutes"] = q("select coalesce(sum(effort),0) from perf_unit where claim_cause = 'CROSS_TEAM_PREDECESSOR' and team = 'QA'")[0][0]
    caused = dict(q("select origin_team, sum(effort) from perf_unit where claim_cause = 'CROSS_TEAM_PREDECESSOR' and team != 'QA' group by 1"))
    offplan_units = {(s, team): n or 0 for s, team, n in q("select shift_no, team, offplan_units from perf_team_shift where team != 'QA'")}
    out["tier_share_of_planned_minutes"] = {t: round(m / max(1, sum(x[1] for x in rows_t)), 4) for rows_t in
                                            [q("select tier, sum(effort) from exec_priority where team != 'QA' group by 1")] for t, m in rows_t}
    fc = {}
    for k in (0, last):
        rows = q(f"select status, late_days, cost_usd from plan_aircraft where purpose = 'FORECAST' and shift_no = {k}")
        known = [(late, cost) for st, late, cost in rows if st != "unknown" and late is not None]
        fc[k] = {"aircraft_known": len(known), "aircraft_unknown": len(rows) - len(known), "late_days": round(sum(max(0.0, l) for l, _ in known), 2),
                 "cost_usd": round(sum(c or 0.0 for _, c in known))}
    out["forecast_first_shift"], out["forecast_last_shift"] = fc[0], fc[last]
    out["_late"] = {(k, line): late for k, line, st, late in q("select shift_no, line, status, late_days from plan_aircraft where purpose = 'FORECAST'")
                    if st != "unknown" and late is not None}
    again = q("select count(*), sum(b.left_by_choice) from exec_priority a join exec_priority b on b.phase = a.phase and b.shift_no = a.shift_no + 1 "
              "where a.left_by_choice = 1")[0]
    out["left_then_dispatched_again_next_shift"] = {"tasks": again[0], "left_again": again[1] or 0}
    # how long a task left by choice stayed undone: tasks first left in the first half of the run, so each had time to be done
    first_left = dict(q("select phase, min(shift_no) from exec_priority where left_by_choice = 1 group by 1"))
    done_in = dict(q("select phase, min(shift_no) from perf_unit where status = 'EARNED' group by 1"))
    for phase, k in q("select phase, min(shift_no) from exec_offplan group by 1"):
        done_in[phase] = min(k, done_in.get(phase, k))
    early = {phase: k for phase, k in first_left.items() if k <= last // 2}
    waits = sorted(done_in[phase] - k for phase, k in early.items() if phase in done_in)
    out["tasks_first_left_in_the_first_half"] = {
        "tasks": len(early), "done_by_the_end": len(waits), "median_shifts_until_done": statistics.median(waits) if waits else None,
        "done_within_2_shifts": sum(w <= 2 for w in waits), "ninth_decile_shifts_until_done": waits[int(0.9 * len(waits))] if waits else None}
    open_rows = q(f"select sum(jobs_open), sum(jobs_done) from calc_aircraft where shift_no = {last}")
    out["jobs_open_at_last_cut"] = open_rows[0][0]
    # ---- measures (per team-shift, then the mean over team-shifts)
    n_units = n_earned = 0
    sums = defaultdict(float)
    count = 0
    teams: dict = defaultdict(lambda: defaultdict(float))
    worst_gap = 0.0
    for key, rows in units.items():
        live = [(e, st, t) for e, st, relief, t, _ in rows if not relief and st != "DONE_BEFORE_SHIFT"]          # expected work
        if not live:
            continue
        count += 1
        U = extra.get(key, 0)
        n_units += len(live)
        n_earned += sum(1 for _, st, _ in live if st == "EARNED")
        sums["adherence_by_count"] += 100 * sum(1 for _, st, _ in live if st == "EARNED") / len(live)
        mine = teams[key[1]]
        mine["team_shifts"] += 1
        mine["jobs_completed"] += sum(1 for _, st, _ in live if st == "EARNED") + offplan_units.get(key, 0)
        mine["throughput_minutes"] += sum(e for e, st, _ in live if st == "EARNED") + U
        charge = 100 * 0.10 * oos.get(key, 0) / sum(e for e, _, _ in live)       # the out-of-sequence charge, against expected minutes
        for wname, mult in WEIGHTS.items():
            D = sum(e * mult[t] for e, _, t in live)
            Cp = sum(e * mult[t] for e, st, t in live if st == "EARNED")
            sums[f"adherence|{wname}"] += 100 * Cp / D
            for rho in RHOS:
                value = max(0.0, 100 * (Cp + rho * min(U, D - Cp)) / D - charge)
                sums[f"score|{wname}|rho{rho}"] += value
                mine[f"score|{wname}|rho{rho}"] += value
                if wname == "flat" and rho == 0.8 and key in stored:             # must be the stored canonical score
                    worst_gap = max(worst_gap, abs(value - stored[key]))
        must = [(e, st) for e, st, t in live if t == "MUST"]
        if must:
            share = 100 * sum(e for e, st in must if st == "EARNED") / sum(e for e, _ in must)
            sums["must_done_share"] += share
            sums["must_team_shifts"] += 1
            mine["must_done_share"] += share
            mine["must_team_shifts"] += 1
    out["measures"] = {k: round(v / count, 2) for k, v in sums.items() if k not in ("must_done_share", "must_team_shifts")}
    out["measures"]["must_jobs_done_share"] = round(sums["must_done_share"] / max(1, sums["must_team_shifts"]), 2)
    out["team_shifts"] = count
    out["largest_gap_to_the_stored_canonical_score"] = round(worst_gap, 6)       # flat multipliers at 0.8 against perf_team_shift.final
    out["_teams"] = {}
    for team, m in teams.items():
        n = m["team_shifts"]
        row = {"jobs_completed": int(m["jobs_completed"]), "throughput_minutes": int(m["throughput_minutes"]),
               "other_production_teams_minutes_blocked": int(caused.get(team, 0)),
               "must_jobs_done_share": m["must_done_share"] / m["must_team_shifts"] if m["must_team_shifts"] else None}
        row.update({k: v / n for k, v in m.items() if k.startswith("score|")})
        out["_teams"][team] = row
    return out


def ranks(values: list) -> list:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out, i = [0.0] * len(values), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1                                              # ties share the mean rank
        i = j + 1
    return out


def rank_correlation(a: list, b: list) -> float | None:
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return round(sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / den, 3) if den else None


floors = [floor(n) for n in NAMES]
base = floors[0]
# delivery, aircraft by aircraft against the baseline floor: the same fleet and seed, so each aircraft is its own control
ref = base["_late"]
for f in floors:
    late = f.pop("_late")
    last = f["shifts"] - 1
    paired = {}
    for label, ks in (("last_shift", [last]), ("last_6_shifts", list(range(max(0, last - 5), last + 1))), ("all_shifts", list(range(1, last + 1)))):
        d = [late[key] - ref[key] for key in late if key in ref and key[0] in ks]
        paired[label] = {"aircraft_shifts": len(d), "mean_days_later_than_baseline": round(sum(d) / len(d), 3) if d else None,
                         "later": sum(x > 0.02 for x in d), "earlier": sum(x < -0.02 for x in d)}
    f["delivery_vs_baseline"] = paired
# team by team: does a measure order the teams by the harm they caused to other production teams?
TEAM_MEASURES = ["jobs_completed", "throughput_minutes", "score|flat|rho0.8", "score|flat|rho0.3", "score|flat|rho0.0",
                 "score|tiers_2_1_half|rho0.8", "score|tiers_2_1_half|rho0.3", "must_jobs_done_share"]
team_rows = [(f["name"], team, row) for f in floors for team, row in f["_teams"].items()]
base_teams = base["_teams"]


def against_harm(rows: list) -> dict:
    out = {"team_periods": len(rows)}
    for k in TEAM_MEASURES:
        kept = [(row[k], row["other_production_teams_minutes_blocked"]) for _, _, row in rows if row[k] is not None]
        out[k] = rank_correlation([a for a, _ in kept], [b for _, b in kept])
    return out


teams_out = {"rank_correlation_with_other_production_teams_minutes_blocked": {
    "all_floors": against_harm(team_rows), "floors_that_choose_only": against_harm([r for r in team_rows if r[0] != base["name"]])},
    "against_the_same_team_on_the_baseline_floor": {}}
for f in floors[1:]:
    both = [(row, base_teams[team]) for team, row in f["_teams"].items() if team in base_teams]
    change = lambda k: round(statistics.median(a[k] - b[k] for a, b in both), 2)                 # noqa: E731
    teams_out["against_the_same_team_on_the_baseline_floor"][f["pick"]] = {
        "teams": len(both), "teams_with_more_jobs_completed": sum(a["jobs_completed"] > b["jobs_completed"] for a, b in both),
        "teams_with_more_standard_minutes": sum(a["throughput_minutes"] > b["throughput_minutes"] for a, b in both),
        "median_change": {k: change(k) for k in ["jobs_completed", "throughput_minutes", "other_production_teams_minutes_blocked",
                                                 "score|flat|rho0.8", "score|flat|rho0.3", "score|tiers_2_1_half|rho0.3"]}}
# on the baseline floor nobody chooses: do the tiers move a team's score there? (they should not: a weighted ratio differs
# from the plain one only when the work left is not a fair sample of the plan)
flat_k, tier_k = "score|flat|rho0.3", "score|tiers_2_1_half|rho0.3"
gaps = [row[tier_k] - row[flat_k] for row in base_teams.values()]
teams_out["tiers_on_the_baseline_floor"] = {
    "teams": len(gaps), "mean_gap_points": round(statistics.fmean(gaps), 3), "largest_gap_points": round(max(gaps, key=abs), 3),
    "rank_agreement_with_the_flat_score": rank_correlation([row[tier_k] for row in base_teams.values()], [row[flat_k] for row in base_teams.values()])}
for f in floors:
    f.pop("_teams")
json.dump({"floors": floors, "teams": teams_out}, open(RESULT, "w"), indent=1)
print(f"{'floor':14s} {'jobs':>6s} {'std min':>8s} {'offplan':>8s} {'left min':>8s} {'gated':>7s} {'blocked':>8s} {'late d':>7s} {'cost $M':>8s} {'unk':>4s} {'open':>7s}")
for f in floors:
    lc, fc = f["left_by_choice"], f["forecast_last_shift"]
    print(f"{f['pick']:14s} {f['jobs_completed']:6d} {f['throughput_minutes']:8.0f} {f['offplan_minutes']:8.0f} {lc['minutes']:8.0f} {lc['gated_minutes']:7.0f} "
          f"{f['blocked_by_another_team_minutes']:8.0f} {fc['late_days']:7.1f} {fc['cost_usd'] / 1e6:8.2f} {fc['aircraft_unknown']:4d} {f['jobs_open_at_last_cut']:7d}")
print("\ndelivery against the baseline floor, aircraft by aircraft (days later; + is worse):")
for f in floors[1:]:
    d = f["delivery_vs_baseline"]
    print(f"  {f['pick']:12s} " + "  ".join(f"{k}: {v['mean_days_later_than_baseline']:+.2f} d ({v['later']} later, {v['earlier']} earlier of {v['aircraft_shifts']})" for k, v in d.items()))
print("\nother teams' work blocked (minutes): " + "; ".join(
    f"{f['pick']} production {f['blocked_by_another_team_production_minutes']}, inspection {f['blocked_by_another_team_inspection_minutes']}, own {f['blocked_by_own_team_minutes']}"
    for f in floors))
print("\ntasks first left by choice in the first half of the run: " + "; ".join(
    f"{f['pick']} {w['tasks']} tasks, {w['done_by_the_end']} done by the end, median {w['median_shifts_until_done']} shifts later, "
    f"{w['done_within_2_shifts']} within 2 shifts, ninth decile {w['ninth_decile_shifts_until_done']}"
    for f in floors[1:] for w in [f["tasks_first_left_in_the_first_half"]]))
print("\nlargest gap between the flat score at 0.8 and the stored canonical score, any team-shift: "
      + ", ".join(f"{f['pick']} {f['largest_gap_to_the_stored_canonical_score']}" for f in floors))
print("\ntier share of planned minutes (baseline):", base["tier_share_of_planned_minutes"], "| forecast at the first shift:", base["forecast_first_shift"])
keys = ["adherence_by_count", "adherence|flat", "adherence|tiers_2_1_half", "adherence|tiers_3_1_half", "must_jobs_done_share",
        "score|flat|rho0.8", "score|flat|rho0.5", "score|flat|rho0.3", "score|flat|rho0.0", "score|tiers_2_1_half|rho0.3", "score|tiers_3_1_half|rho0.3"]
print(f"\n{'measure':30s} " + " ".join(f"{f['pick'][:11]:>11s}" for f in floors))
for k in keys:
    print(f"{k:30s} " + " ".join(f"{f['measures'][k]:11.2f}" for f in floors))
print("\nteam by team, rank correlation with the other production teams' minutes the team blocked (negative: the measure is lower where the harm is higher):")
corr = teams_out["rank_correlation_with_other_production_teams_minutes_blocked"]
print(f"{'measure':30s} {'all floors':>11s} {'choosing':>11s}   (team-periods: {corr['all_floors']['team_periods']} and {corr['floors_that_choose_only']['team_periods']})")
for k in TEAM_MEASURES:
    print(f"{k:30s} {corr['all_floors'][k]:+11.3f} {corr['floors_that_choose_only'][k]:+11.3f}")
print("\neach team against itself on the baseline floor:")
for pick, v in teams_out["against_the_same_team_on_the_baseline_floor"].items():
    m = v["median_change"]
    print(f"  {pick:12s} more jobs: {v['teams_with_more_jobs_completed']} of {v['teams']}; more standard minutes: {v['teams_with_more_standard_minutes']}; median change: "
          f"jobs {m['jobs_completed']:+.0f}, blocked {m['other_production_teams_minutes_blocked']:+.0f} min, today's score {m['score|flat|rho0.8']:+.1f}, "
          f"credit 30% {m['score|flat|rho0.3']:+.1f}, credit 30% with tiers {m['score|tiers_2_1_half|rho0.3']:+.1f}")
t = teams_out["tiers_on_the_baseline_floor"]
print(f"\non the baseline floor, tiers against the flat score per team (credit 30%): mean gap {t['mean_gap_points']:+.3f}, largest {t['largest_gap_points']:+.3f}, "
      f"rank agreement {t['rank_agreement_with_the_flat_score']}")
