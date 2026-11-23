"""D-34 (owner, 2026-11-12): every dataset has a cadence, and the documents, the pipeline draft and the freshness limits
follow one registry (focus/data/cadence.py)."""
from __future__ import annotations

from pathlib import Path

from focus.data.cadence import (CADENCE, GROUPS, MISSED_PASSES_TOLERATED, PASS_INTERVAL_HOURS, PASSES, in_group, in_pass, least_age_limit_hours,
                                plan_rests_on)
from focus.data.datasets import DATASETS
from focus.data.freshness import source_clock

APP = Path(__file__).resolve().parents[2]
CUT = {"master_task_list", "neo_graph_rework", "work_progress", "etracs_active", "part_events", "flow_budget", "soi_cert_requirements",
       "buyoff_plan", "inspection_holds", "rework_classification", "requirement_census", "cday_op_plan", "cs_windows", "holding_events",
       "attendance_shift", "availability_exceptions", "carry_in", "econ_aircraft_mapping"}


def _rows(text: str, columns: int) -> dict[str, list[str]]:
    """Table rows that start with a dataset name and have this many columns."""
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if line.startswith("| `") and len(cells) == columns + 2:
            assert cells[1].strip("`") not in found, cells[1]                    # one row per dataset
            found[cells[1].strip("`")] = cells[1:-1]
    return found


def test_every_dataset_has_a_cadence_in_one_of_four_passes() -> None:
    assert set(CADENCE) == set(DATASETS) and PASSES == ("weekly", "nightly", "preshift", "cut")
    assert all(c.group in GROUPS and c.passes for c in CADENCE.values())           # no dataset is left to chance
    assert sum(len(in_group(group)) for group in GROUPS) == len(DATASETS) == 44
    assert {name: len(in_pass(name)) for name in PASSES} == {"weekly": 18, "nightly": 28, "preshift": 21, "cut": 18}
    assert set(in_pass("cut")) == CUT and CUT <= set(in_pass("preshift"))          # the pull at the cut continues the one before it
    # a pass's steps keep their slowest-first order, and the fastest pass that reads a dataset whole verifies it
    assert CADENCE["flow_budget"].passes == PASSES and CADENCE["flow_budget"].verified_by == "weekly"
    assert CADENCE["master_task_list"].verified_by == "nightly" and CADENCE["etracs_active"].verified_by in ("preshift", "cut")
    assert CADENCE["part_events"].verified_by is None and least_age_limit_hours("part_events") is None   # events only: no age limit can hold


def test_floor_state_reaches_every_cut_and_slow_data_is_read_whole_once_a_week() -> None:
    # what the coming shift's plan depends on right now is brought up to every cut
    assert set(in_group("Floor state")) <= CUT
    for name in ("attendance_shift", "availability_exceptions", "holding_events", "carry_in"):
        assert CADENCE[name].cut is not None, name
    # the owner's example: who holds which certificate and which job needs which are gathered in full weekly, not every
    # shift. In between, only rows that changed or belong to new jobs are pulled
    for name in ("mechanic_certs", "soi_cert_requirements", "cert_course_map", "cert_eligibility_matrix"):
        cadence = CADENCE[name]
        assert cadence.weekly is not None and cadence.weekly.whole and cadence.verified_by == "weekly", name
        assert all(not step.whole for step in (cadence.nightly, cadence.preshift, cadence.cut) if step is not None), name
    assert CADENCE["soi_cert_requirements"].cut.text == "rows for new jobs" and CADENCE["holding_events"].cut is not None
    # the standing facts of jobs: the full compare is weekly for every one of them
    assert all(CADENCE[name].verified_by == "weekly" for name in in_group("Job facts"))
    # where a stale copy makes a plan wrong and nothing flags it, the whole table is still checked every day
    for name in ("pnet_dag", "cday_op_plan", "cs_windows", "econ_mapping_revision", "econ_aircraft_mapping"):
        assert CADENCE[name].verified_by == "nightly", name
    assert PASS_INTERVAL_HOURS["weekly"] == 7 * PASS_INTERVAL_HOURS["nightly"] and MISSED_PASSES_TOLERATED == 1


def test_freshness_limits_follow_the_cadence(policy) -> None:
    limits = dict(policy.map("admission.max_source_age_hours"))
    # only a dataset an executable plan is built from may refuse one for its age, and every such dataset with a clock does
    assert set(limits) == {name for name in DATASETS if source_clock(name) and plan_rests_on(name)}
    assert len(limits) == 12 and not {"inspection_counts", "etracs_history", "actual_labor", "labor_shift", "cert_course_map",
                                      "dept_team_skill_ref"} & set(limits)
    # one missed pass never stops a plan: the limit is at least twice the interval of the pass that reads the dataset whole
    for name, limit in limits.items():
        least = least_age_limit_hours(name)
        assert least is not None and limit >= least, (name, limit, least)
    assert {name: limits[name] for name in ("attendance_shift", "master_task_list", "mechanic_certs", "soi_cert_requirements", "flow_budget")} == {
        "attendance_shift": 18, "master_task_list": 48, "mechanic_certs": 336, "soi_cert_requirements": 336, "flow_budget": 336}
    # for what the weekly pass reads whole the limit is exactly that: a second missed pass in a row stops executable plans
    assert {limits[name] for name in limits if CADENCE[name].verified_by == "weekly"} == {2 * PASS_INTERVAL_HOURS["weekly"]}
    assert {limits[name] for name in limits if CADENCE[name].verified_by == "nightly"} == {48, 240}


def test_the_staging_plan_says_what_the_registry_says(policy) -> None:
    plan = (APP / "docs" / "integration" / "STAGING_PLAN.md").read_text(encoding="utf-8")
    rows = _rows(plan, 10)
    assert set(rows) == set(DATASETS)
    limits = policy.map("admission.max_source_age_hours")
    for name, cells in rows.items():
        cadence = CADENCE[name]
        said = [cell.replace("**", "") for cell in cells[4:8]]
        assert said == [step.text if step is not None else "none" for step in (cadence.weekly, cadence.nightly, cadence.preshift, cadence.cut)], name
        assert cells[3] == (f"{limits[name]} h" if name in limits else "none"), name
        assert cells[1] == ("yes" if DATASETS[name].required_for_run else "no"), name
        assert (cells[7].startswith("**") and cells[7].endswith("**")) == (cadence.cut is not None), name   # what the cut pulls stands out
    # every table sits under its group's heading, in the registry's order
    for group in GROUPS:
        block = plan.split(f"**{group}**\n\n", 1)[1].split("\n\n", 1)[0]
        assert [line.split("|")[1].strip().strip("`") for line in block.splitlines()[2:]] == list(in_group(group)), group
    for needle in ("--part weekly", "`weekly_sources`", "FOCUS_PIPELINE_PART=weekly", "focus/data/cadence.py", "DATA_CADENCE.md", "the 18 datasets"):
        assert needle in plan, needle


def test_the_cadence_page_lists_every_dataset_as_the_registry_has_it() -> None:
    page = (APP / "docs" / "integration" / "DATA_CADENCE.md").read_text(encoding="utf-8")
    rows = _rows(page, 6)
    assert set(rows) == set(DATASETS)
    for name, cells in rows.items():
        cadence = CADENCE[name]
        assert cells[2:] == ["–" if step is None else ("whole" if step.whole else "changes")
                             for step in (cadence.weekly, cadence.nightly, cadence.preshift, cadence.cut)], name
    for needle in ("`30 7 * * 0`", "FOCUS_PIPELINE_PART=weekly", "18 datasets, 61% of all rows", "28 datasets", "21 datasets", "D-34",
                   "No person accepts anything"):
        assert needle in page, needle


def test_the_pipeline_draft_runs_each_cadence_as_its_own_schedule() -> None:
    draft = (APP / ".gitlab-ci.yml").read_text(encoding="utf-8")
    for part, job in (("weekly", "weekly_sources"), ("nightly", "nightly_sources"), ("preshift", "prestage_sources")):
        block = draft.split(f"\n{job}:\n", 1)[1].split("\n\n", 1)[0]
        assert f'$FOCUS_PIPELINE_PART == "{part}"' in block and f"--part {part} " in block and "extends: .staging_only" in block, job
        assert '--as-of "$CI_PIPELINE_CREATED_AT"' in block and "focus-staging" in block and "--role" not in block, job
    assert "--part cut " in draft.split("\nstage_sources:\n", 1)[1].split("\n\n", 1)[0]
    # a pass that runs for hours has a limit, and never builds a scheduler wheel it does not use
    assert "timeout: 4h" in draft.split("\nweekly_sources:\n", 1)[1].split("\n\n", 1)[0]
    wheel = draft.split("\nnative_wheel:\n", 1)[1].split("\n\n", 1)[0]
    assert all(f'$FOCUS_PIPELINE_PART == "{part}"' in wheel.split("when: never")[0] for part in ("preshift", "nightly", "weekly"))
    # an unknown part still runs nothing
    guard = draft.split("workflow:\n", 1)[1].split("when: never", 1)[0]
    assert all(f'$FOCUS_PIPELINE_PART != "{part}"' in guard for part in ("evaluate", "preshift", "nightly", "weekly"))
