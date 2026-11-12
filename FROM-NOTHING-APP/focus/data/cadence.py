"""How often each dataset is gathered from its source and staged in SQL Server.

The owner, 2026-11-12 (D-34): "For all data for the scheduler and dashboard we need to determine cadence for all data
gathering and processing and staging to sql server." Their example: who holds which certificate, which job needs which,
and so who may do which job "doesn't need ran shiftly it may only need to be weekly".

This registry is that determination, one entry per planning dataset. The staging loader is company code and reads
nothing from here; this module is what the documents, the pipeline draft and the freshness limits are held to
(``docs/integration/STAGING_PLAN.md`` section 3, ``.gitlab-ci.yml``, policy ``admission.max_source_age_hours``).

Four passes keep the copy in SQL Server complete and current. Three rules place a dataset:

1. **How often its content changes in a way that changes a plan.** Floor state changes all day. The standing facts of a
   job, the certificates people hold and the lookup tables change with engineering releases and training, not with
   the floor.
2. **What a stale copy does.** Where a missing row blocks a job with a named gap, a slower pass is safe. Where a stale
   copy makes the plan wrong and nothing flags it (template links, calendars and due dates, rates), the check stays
   daily even though the data seldom changes.
3. **Size.** A large table is compared in full seldom, and what changed is pulled in between: rows for new jobs come
   with the jobs, at every run.

They are starting values, set without a single timed source query. The loader's receipts move a dataset between
passes by the rules of ``STAGING_PLAN.md`` section 12.
"""
from __future__ import annotations

from dataclasses import dataclass

from .datasets import DATASETS

# The passes, slowest first, with the longest time between two runs of each in hours. Shifts start 8.5, 8.5 and 7 hours
# apart, and the pass before a shift and the pass at the cut both run once per shift.
PASS_INTERVAL_HOURS: dict[str, float] = {"weekly": 168.0, "nightly": 24.0, "preshift": 8.5, "cut": 8.5}
PASSES: tuple[str, ...] = tuple(PASS_INTERVAL_HOURS)
GROUPS: tuple[str, ...] = ("Floor state", "Job facts", "Calendar", "People", "Reference", "History", "Economics")
MISSED_PASSES_TOLERATED = 1     # one missed pass never stops a plan; the second one does


@dataclass(frozen=True, slots=True)
class Step:
    """What one pass does with a dataset. ``whole``: the pass reads a complete set again (the table, or a complete
    current set such as all open shortages or the coming shift's attendance), so the dataset is known current as of
    the pass and its row clock moves. A pull of changed or new rows proves nothing when it returns no row."""
    text: str
    whole: bool = False


def whole(text: str) -> Step:
    return Step(text, True)


def part(text: str) -> Step:
    return Step(text, False)


@dataclass(frozen=True, slots=True)
class Cadence:
    group: str
    weekly: Step | None = None
    nightly: Step | None = None
    preshift: Step | None = None
    cut: Step | None = None

    def step(self, name: str) -> Step | None:
        return getattr(self, name)

    @property
    def passes(self) -> tuple[str, ...]:
        """The passes that touch the dataset, slowest first."""
        return tuple(name for name in PASSES if self.step(name) is not None)

    @property
    def verified_by(self) -> str | None:
        """The most frequent pass that reads the dataset whole: after it, the copy is known current."""
        found = [name for name in PASSES if (step := self.step(name)) is not None and step.whole]
        return min(found, key=PASS_INTERVAL_HOURS.__getitem__) if found else None


_FULL_COMPARE = whole("full compare")
_FULL_LOAD = whole("full load")
_CHECK = whole("check; reload on change")
_CHANGED = part("changed rows")
_NEW_JOBS = part("rows for new jobs")
_NEW_REWORK = part("rows for new rework jobs")
_NEW_LINES = part("rows for new lines")
_OWNER = part("on owner change")
_ACTIVE_LOAD = part("check the active load per line; reload a changed line")
_ACTIVE_LOAD_AGAIN = part("check the active load again")


def _job_fact(new: Step = _NEW_JOBS) -> Cadence:
    """A standing fact of each job: compared in full once a week, corrected nightly, and brought in with every new job."""
    return Cadence("Job facts", weekly=_FULL_COMPARE, nightly=_CHANGED, preshift=new, cut=new)


CADENCE: dict[str, Cadence] = {
    # ---- floor state: what is open, in work, done and short. Changes all day; brought up to every cut
    "master_task_list": Cadence("Floor state", nightly=whole("reconcile keys and counts"), preshift=part("changes since the last pass"),
                                cut=part("changes since the last pass")),
    "neo_graph_rework": Cadence("Floor state", nightly=whole("reconcile"), preshift=part("changes"), cut=part("changes")),
    "work_progress": Cadence("Floor state", preshift=whole("every job in work"), cut=whole("every job in work, as of the cut")),
    "etracs_active": Cadence("Floor state", nightly=whole("reconcile"), preshift=whole("all open shortages"), cut=whole("all open shortages")),
    "part_events": Cadence("Floor state", preshift=part("new events"), cut=part("new events up to the cut")),
    # ---- job facts: duration, crew, certificates required, inspections, rework class. Standing; new jobs bring new rows
    "flow_budget": _job_fact(),
    "soi_cert_requirements": _job_fact(),
    "buyoff_plan": _job_fact(),
    "inspection_holds": _job_fact(),
    "rework_classification": _job_fact(_NEW_REWORK),
    "requirement_census": _job_fact(),
    "requirement_alternatives": Cadence("Job facts", weekly=_FULL_COMPARE, nightly=_CHANGED),
    "inspection_counts": Cadence("Job facts", weekly=_FULL_LOAD),
    # ---- calendar: small, seldom changed, and a stale due date misprices lateness with nothing to flag it
    "cday_op_plan": Cadence("Calendar", nightly=_FULL_LOAD, preshift=_ACTIVE_LOAD, cut=_ACTIVE_LOAD_AGAIN),
    "cs_windows": Cadence("Calendar", nightly=_FULL_LOAD, preshift=_ACTIVE_LOAD, cut=_ACTIVE_LOAD_AGAIN),
    # ---- people: certificates weekly (the owner's example); who is on which team and shift daily; who is there at the cut
    "mechanic_certs": Cadence("People", weekly=_FULL_LOAD, nightly=_CHANGED),
    "holding_events": Cadence("People", preshift=part("new events"), cut=part("new events up to the cut")),
    "person_team_map": Cadence("People", nightly=_FULL_LOAD, preshift=part("changes")),
    "shift_roster": Cadence("People", nightly=_FULL_LOAD, preshift=_FULL_LOAD),
    "team_inference": Cadence("People", weekly=whole("computed in SQL Server")),
    "attendance_shift": Cadence("People", preshift=whole("the coming production day and shift"), cut=whole("again, as known at the cut")),
    "availability_exceptions": Cadence("People", preshift=part("new rows"), cut=part("new rows up to the cut")),
    "ot_authorizations": Cadence("People", nightly=_FULL_LOAD, preshift=_FULL_LOAD),
    "carry_in": Cadence("People", preshift=whole("full load for the horizon"), cut=whole("again, as of the cut")),
    # ---- reference: changes with a release. A missing template link is the one gap nothing flags, so its check is daily
    "pnet_dag": Cadence("Reference", nightly=_CHECK),
    "inspection_standards": Cadence("Reference", weekly=_CHECK),
    "cert_course_map": Cadence("Reference", weekly=_CHECK),
    "dept_team_skill_ref": Cadence("Reference", weekly=_CHECK),
    "scope_receipt": Cadence("Reference", nightly=part("on change")),
    "people_directory": Cadence("Reference", nightly=_FULL_LOAD),
    "cert_eligibility_matrix": Cadence("Reference", weekly=whole("full load, or not at all")),
    "mech_team_inferred": Cadence("Reference", weekly=_FULL_LOAD),
    # ---- history: what the duration statistics learn from is appended daily; what no plan reads, weekly
    "duration_episodes": Cadence("History", nightly=part("append completed episodes")),
    "clock_observations": Cadence("History", nightly=part("append")),
    "etracs_history": Cadence("History", weekly=part("append closed shortages")),
    "actual_labor": Cadence("History", weekly=_FULL_LOAD),
    "labor_shift": Cadence("History", weekly=part("append")),
    # ---- economics: tiny, and they price every late day, so the check is daily; an owner's change loads the next night
    "econ_mapping_revision": Cadence("Economics", nightly=whole("check")),
    "econ_aircraft_mapping": Cadence("Economics", nightly=whole("check"), preshift=_NEW_LINES, cut=_NEW_LINES),
    "econ_scenario": Cadence("Economics", nightly=_OWNER),
    "econ_contract_assignment": Cadence("Economics", nightly=_OWNER),
    "econ_term": Cadence("Economics", nightly=_OWNER),
    "econ_original_sale_basis": Cadence("Economics", nightly=_OWNER),
    "econ_liability_checkpoint": Cadence("Economics", nightly=_OWNER),
}


def in_pass(name: str) -> tuple[str, ...]:
    """The datasets a pass touches, in registry order."""
    return tuple(dataset for dataset, cadence in CADENCE.items() if cadence.step(name) is not None)


def in_group(group: str) -> tuple[str, ...]:
    return tuple(dataset for dataset, cadence in CADENCE.items() if cadence.group == group)


def least_age_limit_hours(dataset: str) -> float | None:
    """The smallest freshness limit the dataset's cadence allows, in hours: the copy may go unverified for one pass
    interval, plus one more for each missed pass that must not stop a plan. ``None``: no pass reads the dataset whole,
    so its row clock moves only when rows arrive and no age limit can hold."""
    by = CADENCE[dataset].verified_by
    return None if by is None else (1 + MISSED_PASSES_TOLERATED) * PASS_INTERVAL_HOURS[by]


def plan_rests_on(dataset: str) -> bool:
    """Whether an executable plan is built from the dataset's rows: the census of jobs, or a fact a crew or a slot
    depends on. Only such a dataset may refuse a plan for its age."""
    return bool({"inventory", "executable"} & set(DATASETS[dataset].capabilities))
