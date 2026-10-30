"""P24: the acceptance register covers all 72 cases and 39 requirements, matches the manual, and every receipt resolves.

The register (tests/acceptance/map.json) is what the acceptance run judges. These checks keep it honest: no case or
requirement is missing, every named test is really collected, every rule authority imports, every caller path is a
real call to the named object, every source file exists, and the stages and cases are the manual's own.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from focus.release.acceptance import CASES, CONSTRAINTS, check_path, load_map, map_problems, resolve
from focus.release.hashes import find_manual, recorded_manual_sha256


@pytest.fixture(scope="module")
def register(app_root: Path):
    return load_map(app_root)


@pytest.fixture(scope="module")
def collected(app_root: Path) -> set[str]:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests"],
                         cwd=app_root, capture_output=True, text=True, timeout=600, check=False)
    nodes = {line.strip() for line in out.stdout.splitlines() if "::" in line}
    assert nodes, out.stdout[-2000:] + out.stderr[-2000:]
    return nodes


def _refs(register) -> set[str]:
    return {ref for group in ("cases", "constraints", "counterexamples", "verification") for entry in register[group].values()
            for ref in entry.get("tests", [])}


def test_register_covers_every_case_and_requirement(register, collected, app_root) -> None:
    assert map_problems(register) == []
    assert tuple(register["cases"]) == CASES and tuple(register["constraints"]) == CONSTRAINTS
    missing = sorted(r for r in _refs(register) if r not in collected and not any(n.startswith(r + "[") for n in collected))
    assert missing == [], "named tests that pytest does not collect"
    unresolved = []
    for cid, entry in register["constraints"].items():
        for rule in entry["rule"]:
            try:
                resolve(rule)
            except LookupError as exc:
                unresolved.append((cid, str(exc)))
        unresolved += [(cid, problem) for problem in (check_path(caller, callee) for caller, callee in entry["paths"]) if problem]
        unresolved += [(cid, f"missing file {f}") for f in entry["files"] if not (app_root / f).is_file()]
    assert unresolved == []


def test_path_receipts_reject_a_wrong_or_missing_call() -> None:
    """Negative counterexamples for the receipt checker itself."""
    assert check_path("focus.validation.plan:validate", "focus.validation.plan:_v2") is None
    assert "does not call" in check_path("focus.validation.plan:_v2", "focus.validation.plan:_v9")
    # the validator recomputes the activation cap itself; it does not call the workforce rule (independence)
    assert "is not" in check_path("focus.validation.duty:reconstruct", "focus.data.workforce:activation_cap")
    assert check_path("focus.validation.duty:reconstruct", "focus.validation.input:ValidationInput.activation_cap") is None
    assert "no " in check_path("focus.engine.model:EngineContext.qualifies", "focus.eligibility:Eligibility.check_bundle")
    assert check_path("focus.nowhere:f", "focus.validation.plan:_v2").startswith("caller focus.nowhere:f")


def test_register_matches_the_manual(register, app_root) -> None:
    manual = find_manual(app_root)
    if manual is None:
        pytest.skip("the build manual is not next to the app root; a skipped A72 check blocks release")
    assert hashlib.sha256(manual.read_bytes()).hexdigest() == recorded_manual_sha256(app_root)
    text = manual.read_text(encoding="utf-8")
    s19 = dict(re.findall(r"^(A\d\d) \| primary stage (P\d\d)$", text, re.M))
    assert s19 == {cid: entry["stage"] for cid, entry in register["cases"].items()}
    s20 = {cid: (req.strip(), stages.split(","), cases.split(","))
           for cid, req, stages, cases in re.findall(r"^(C\d\d) \| (.+?) \| ([P0-9,]+) \| ([A0-9,]+)$", text, re.M)}
    assert s20 == {cid: (e["requirement"], e["stages"], e["cases"]) for cid, e in register["constraints"].items()}
    named = re.search(r"Include known (.+?) counterexamples", text.replace("\n", " ")).group(1)
    for words, key in (("in-progress edge", "in_progress_edge"), ("activation repair", "activation_repair"), ("rework P75", "rework_p75"),
                       ("same-run CS serialization/readback", "same_run_cs_readback"), ("OOS double-charge", "oos_double_charge")):
        assert words in named and register["counterexamples"][key]["tests"]
