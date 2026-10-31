"""A13: the facts a simulation run stores for dependency_unlocked, Firebreak and SkillSteward, on hand-built graphs and holdings.

``released_by`` must match ``focus.analytics.progression``'s rule: a completion releases another job downstream (through
RELEASE nodes) when it is the last of that job's hard prerequisites. ``scarce_role`` must match its SkillSteward rule: a
certificate group of the role held over the shift by at least one and at most ``badges.skill_steward_max_holders`` members.
"""
from __future__ import annotations

from types import SimpleNamespace

from focus.domain import PhaseKind
from focus.sim.execute import released_by, scarce_role


def _ctx(edges: list[tuple[str, str]], kinds: dict[str, PhaseKind]) -> SimpleNamespace:
    phases = {p: SimpleNamespace(kind=kind, job=p.split(".")[0]) for p, kind in kinds.items()}
    preds: dict[str, list] = {}
    succs: dict[str, list] = {}
    for a, b in edges:
        e = SimpleNamespace(pred_phase=a, succ_phase=b)
        preds.setdefault(b, []).append(e)
        succs.setdefault(a, []).append(e)
    jobs = {p.job: SimpleNamespace(line=1, key=SimpleNamespace(native_id=p.job)) for p in phases.values()}
    return SimpleNamespace(phases=phases, preds=preds, succs=succs, releases={"PART-1"}, jobs=jobs)


W, R = PhaseKind.WORK, PhaseKind.RELEASE
KINDS = {"JA.W.0": W, "JA.W.1": W, "JA.R.0": R, "JB.W.0": W, "JC.W.0": W, "JD.W.0": W, "JE.W.0": W, "JG.W.0": W, "JH.W.0": W,
         "JX.W.0": W, "JY.W.0": W, "JZ.W.0": W, "JQ.W.0": W, "JT.W.0": W}
EDGES = [
    ("JA.W.0", "JA.R.0"), ("JA.R.0", "JB.W.0"), ("JA.R.0", "JC.W.0"),   # through a RELEASE node to B and C
    ("JZ.W.0", "JB.W.0"), ("PART-1", "JB.W.0"),                         # B: a prerequisite done before the shift, and a part
    ("JX.W.0", "JC.W.0"),                                               # C: X finished earlier, so A is the last
    ("JA.W.0", "JD.W.0"), ("JY.W.0", "JD.W.0"),                         # D: Y finishes after A, so Y releases D
    ("JA.W.0", "JE.W.0"),                                               # E: its part is still short
    ("JA.W.0", "JA.W.1"),                                               # the same job's next phase is not another job
    ("JA.W.0", "JG.W.0"), ("JQ.W.0", "JG.W.0"),                         # G: Q is still open
    ("JA.W.0", "JH.W.0"), ("JT.W.0", "JH.W.0"),                         # H: A and T finish together, a tie releases for both
]


def test_a_completion_releases_the_jobs_it_was_the_last_prerequisite_of() -> None:
    ctx = _ctx(EDGES, KINDS)
    done = {"JA.W.0": 100, "JA.R.0": 100, "JX.W.0": 50, "JY.W.0": 150, "JT.W.0": 100}
    short = lambda line, soi, at: 7 if soi == "JE" else None                       # noqa: E731 - JE's part has an open record
    assert released_by(ctx, done, {"JZ.W.0"}, short) == {"JA.W.0": ("JB", "JC", "JH"), "JY.W.0": ("JD",), "JT.W.0": ("JH",)}


def test_without_its_other_prerequisites_nothing_is_released() -> None:
    ctx = _ctx(EDGES, KINDS)
    clear = lambda line, soi, at: None                                             # noqa: E731
    out = released_by(ctx, {"JA.W.0": 100, "JA.R.0": 100}, set(), clear)          # Z, X, Y, Q and T unfinished
    assert out == {"JA.W.0": ("JE",)}                                              # only E, whose sole prerequisite is A


class _Index:
    def __init__(self, held: dict[tuple[str, str], tuple[int, int]]):
        self.held = held

    def holds(self, person: str, cert: str, start: int, end: int) -> tuple[bool, str]:
        span = self.held.get((person, cert))
        return (span is not None and span[0] <= start and end <= span[1], "")


def _phase(*groups: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(role_demands=[SimpleNamespace(bundle=SimpleNamespace(groups=list(groups)))])


def test_a_role_is_scarce_when_one_or_two_members_hold_a_group() -> None:
    always = (0, 10_000)
    index = _Index({("M1", "X"): always, ("M1", "Y"): always, ("M2", "Y"): always, ("M3", "Y"): always, ("M2", "W"): always,
                    ("M4", "V"): (0, 50)})                                          # M4 holds V only before the shift
    ctx = SimpleNamespace(policy=SimpleNamespace(int=lambda key: {"badges.skill_steward_max_holders": 2}[key]),
                          eligibility=SimpleNamespace(index=index))
    members = ("M1", "M2", "M3", "M4")
    held: dict = {}
    assert scarce_role(ctx, _phase(("X",)), members, 100, 200, held)                # one holder
    assert scarce_role(ctx, _phase(("Y",), ("X", "W")), members, 100, 200, held)    # X or W: two holders, at the limit
    assert not scarce_role(ctx, _phase(("Y",)), members, 100, 200, held)            # three holders
    assert not scarce_role(ctx, _phase(("V",), ("Z",)), members, 100, 200, held)    # nobody holds either over the shift
    assert not scarce_role(ctx, _phase(("X",)), ("M2", "M3"), 100, 200, held)       # only membership counts
