"""P24 release acceptance (A72): a fresh checkout runs the documented fixture flow; reviews are versioned and never
overwrite history; the unresolved live gates are listed precisely.

The fresh checkout is a copy of exactly the files a clone holds (tracked plus new, never ignored runtime, caches or
virtual environments), driven only through the documented commands in a minimal environment. Reviews are written
into a temporary review folder that already holds a historical REVIEW-001 pair and a partial 002.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from focus.release import review as review_mod
from focus.release.acceptance import CASES, evaluate, load_map
from focus.release.gates import BLOCKING, GATES, OPTIONAL, blocking
from focus.release.hashes import build_hashes, select_files
from focus.release.review import SELF_CHECK, ReviewError, verify_review, write_review

SHIFT = "DEMO:2026-10-26:S1"
LAST_COLLECTION = "2026-10-28T16:00:00Z"


# ---------------------------------------------------------------------- a fresh checkout, documented commands only

def _fresh_copy(app_root: Path, dst: Path) -> Path:
    files, _ = select_files(app_root, ["."])
    assert "focus/cli.py" in files and not any(f.startswith(("fixtures/runtime/", ".venv/")) or "__pycache__" in f for f in files)
    for rel in files:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(app_root / rel, target)
    return dst


def _env() -> dict[str, str]:
    keep = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "PLAYWRIGHT_BROWSERS_PATH") if k in os.environ}
    return {**keep, "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


def _cli(root: Path, *args: str, ok: bool = True) -> dict:
    proc = subprocess.run([sys.executable, "-m", "focus.cli", *args], cwd=root, env=_env(), capture_output=True, text=True, timeout=900)
    result = json.loads(proc.stdout)
    if ok:
        assert proc.returncode == 0 and result["status"] == "ok", (args, proc.stdout[-3000:], proc.stderr[-3000:])
    return result


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a72_fresh_checkout_runs_the_documented_fixture_flow(app_root: Path, tmp_path: Path) -> None:
    own_runtime = _tree(app_root / "fixtures" / "runtime")
    root = _fresh_copy(app_root, tmp_path / "FROM-NOTHING-APP")
    where = subprocess.run([sys.executable, "-c", "import focus, sys; print(focus.__file__); print(sys.version_info[:2])"],
                           cwd=root, env=_env(), capture_output=True, text=True, check=True).stdout.splitlines()
    assert Path(where[0]).resolve().is_relative_to(root.resolve()) and where[1] == "(3, 12)"   # the copy, on CPython 3.12
    imports = subprocess.run([sys.executable, "tools/check_imports.py"], cwd=root, env=_env(), capture_output=True, text=True, timeout=600)
    assert imports.returncode == 0, imports.stdout[-2000:]

    empty = _cli(root, "status")                                          # import and no data: typed, nothing acquired
    assert set(empty["heads"].values()) == {None} and empty["policy"]["status"] == "ready"
    assert _cli(root, "demo-bootstrap")["status"] == "ok"
    run = _cli(root, "demo-run")
    plan = run["plans"]["EXECUTABLE"]
    assert plan["status"] == "ACCEPTED" and run["plans"]["FORECAST"]["status"] == "ACCEPTED"
    assert [c["as_of"] for c in run["collections"]][-1] == LAST_COLLECTION and run["recognition"]["appended"] > 0

    explained = _cli(root, "explain", "--plan", plan["plan_id"], "--team", "PROD-A", "--shift-id", SHIFT)
    assert explained["items"] and explained["deterministic"]
    action = explained["items"][0]["candidate_id"]
    decision = _cli(root, "dispatch-check", "--plan", plan["plan_id"], "--action", action)
    assert decision["decision"] == "DISPATCHABLE" and decision["reasons"] == []

    heads = _cli(root, "status")["heads"]
    assert all(heads.values())
    what_if = _cli(root, "scenario", "--source-run", run["source_run_id"], "--changes", '[{"type": "duration_statistic", "statistic": "P90"}]',
                   "--budget-seconds", "20")
    assert what_if["capability"] == "SCENARIO_ONLY" and _cli(root, "status")["heads"] == heads   # a scenario never moves a head
    evaluated = _cli(root, "evaluate", "--shift", SHIFT, "--policy", "rebuild-v1", "--as-of", LAST_COLLECTION)
    assert len(evaluated["evaluations"]) == 3 and {e["status"] for e in evaluated["evaluations"]} == {"demonstration"}
    assert _cli(root, "migrate", "--dry-run")["status"] == "ok"
    assert _cli(root, "preflight")["status"] == "ok"

    port = _free_port()
    server = subprocess.Popen([sys.executable, "-m", "focus.cli", "serve", "--port", str(port)], cwd=root, env=_env(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health/live", timeout=2)
                break
            except OSError:
                time.sleep(0.1)
        for path in ("/health/ready", "/api/v1/openapi.json", "/api/v1/context", "/"):
            with urllib.request.urlopen(base + path, timeout=10) as response:
                assert response.status == 200 and response.headers["Cache-Control"] == "no-store", path
    finally:
        server.terminate()
        server.wait(timeout=30)
    assert _tree(app_root / "fixtures" / "runtime") == own_runtime         # the checkout under test wrote only into itself


# ---------------------------------------------------------------------- versioned reviews

def _acceptance(app_root: Path, path: Path, outcome_for=lambda ref: "passed", extra_results=None) -> Path:
    """A judged acceptance report over the real register and the real hashes, from synthetic receipts."""
    m = load_map(app_root)
    refs = {ref for group in ("cases", "constraints", "counterexamples", "verification") for e in m[group].values() for ref in e["tests"]}
    results = {ref: {"outcome": outcome_for(ref), "duration": 0.0, "phases": {"call": outcome_for(ref)}} for ref in refs}
    results.update(extra_results or {})
    report = evaluate(m, {"results": results, "collected": len(results), "exitstatus": 0}, app_root)
    report["hashes"] = build_hashes(app_root)
    report["run"] = {"python": sys.version.split()[0], "recorded_at_utc": "2026-10-30T00:00:00Z"}
    path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return path


def test_a72_new_review_preserves_history_and_binds_exact_hashes(app_root: Path, tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "review"
    folder.mkdir()
    history = {"REVIEW-001.md": b"# REVIEW-001\nhistorical external review\n", "VERDICT-001.json": b'{"historical": true}\n',
               "REVIEW-002.md": b"# REVIEW-002\npartial pair: its verdict was never written\n"}
    for name, data in history.items():
        (folder / name).write_bytes(data)
    accepted = _acceptance(app_root, tmp_path / "acceptance.json")

    out = write_review(app_root, accepted, review_dir=folder, reviewer="test reviewer", recorded_at="2026-10-30T12:00:00Z")
    assert out["number"] == 3                                    # smallest NNN with both files absent: 002 is half used
    assert {name: (folder / name).read_bytes() for name in history} == history
    verdict = json.loads((folder / "VERDICT-003.json").read_text())
    now = build_hashes(app_root)
    assert verdict["kind"] == SELF_CHECK and verdict["independent"] is False
    assert verdict["local_verdict"] == "LOCAL_BUILD_COMPLETE" and verdict["local_blockers"] == []
    for key in ("implementation", "tests", "manual", "policy"):
        assert verdict["hashes"][key] == now[key], key
    assert verdict["hashes"]["source"]["fixture"] == now["source"]["fixture"] and verdict["hashes"]["source"]["live"].startswith("NOT_ACQUIRED")
    assert verdict["hashes"]["acceptance_run"]["sha256"] == review_mod._sha256(accepted)
    assert verdict["previous_reviews"] == [
        {"number": 1, "review": {"file": "review/REVIEW-001.md", "sha256": review_mod._sha256(folder / "REVIEW-001.md")},
         "verdict": {"file": "review/VERDICT-001.json", "sha256": review_mod._sha256(folder / "VERDICT-001.json")}},
        {"number": 2, "review": {"file": "review/REVIEW-002.md", "sha256": review_mod._sha256(folder / "REVIEW-002.md")}}]
    assert verdict["live_release"]["status"] == "BLOCKED" and [g["id"] for g in verdict["live_release"]["gates"]] == [g.id for g in GATES]
    text = (folder / "REVIEW-003.md").read_text()
    assert "IMPLEMENTER_SELF_CHECK" in text and now["implementation"]["sha256"] in text and all(g.id in text for g in GATES)

    third = (folder / "REVIEW-003.md").read_bytes(), (folder / "VERDICT-003.json").read_bytes()
    assert write_review(app_root, accepted, review_dir=folder, reviewer="test reviewer")["number"] == 4
    assert ((folder / "REVIEW-003.md").read_bytes(), (folder / "VERDICT-003.json").read_bytes()) == third
    assert verify_review(app_root, 3, review_dir=folder) == {"number": 3, "matches": True, "checks": dict.fromkeys(
        ("manual", "implementation", "tests", "fixture_source", "policy"), "MATCH")}

    forged = json.loads(third[1])
    forged["hashes"]["implementation"]["sha256"] = "0" * 64
    (folder / "VERDICT-005.json").write_text(json.dumps(forged))
    checked = verify_review(app_root, 5, review_dir=folder)
    assert checked["matches"] is False and checked["checks"]["implementation"] == "MISMATCH"

    monkeypatch.setattr(review_mod, "next_number", lambda _folder: 3)  # a writer racing for a used number
    with pytest.raises(ReviewError) as raced:
        write_review(app_root, accepted, review_dir=folder, reviewer="racer")
    assert raced.value.code == "REVIEW_EXISTS" and (folder / "VERDICT-003.json").read_bytes() == third[1]


def test_a72_review_refuses_stale_acceptance_and_reports_incomplete_builds(app_root: Path, tmp_path: Path) -> None:
    folder = tmp_path / "review"
    stale = _acceptance(app_root, tmp_path / "stale.json")
    report = json.loads(stale.read_text())
    report["hashes"]["implementation"]["sha256"] = "f" * 64
    stale.write_text(json.dumps(report))
    with pytest.raises(ReviewError) as refused:
        write_review(app_root, stale, review_dir=folder, reviewer="test")
    assert refused.value.code == "ACCEPTANCE_STALE" and list(folder.iterdir()) == []

    in_progress = "tests/unit/test_constraint_graph.py::test_a26_in_progress_keeps_incomplete_predecessor_and_actual_history"
    ui = "tests/ui/test_workflows.py::test_a69_every_view_renders_in_both_layouts_at_phone_and_desktop"
    broken = _acceptance(app_root, tmp_path / "broken.json",
                         outcome_for=lambda ref: {in_progress: "failed", ui: "skipped"}.get(ref, "passed"),
                         extra_results={"tests/unit/test_calendar.py::test_unmapped_helper": {"outcome": "error", "duration": 0.0, "phases": {}}})
    verdict = json.loads(write_review(app_root, broken, review_dir=folder, reviewer="test")["verdict"].read_text())
    blockers = verdict["local_blockers"]
    assert verdict["local_verdict"] == "LOCAL_BUILD_INCOMPLETE"
    assert {"A26 FAIL", "C01 FAIL", "C38 FAIL", "A69 BLOCKED_SKIP"} <= set(blockers)
    assert "counterexamples.in_progress_edge FAIL" in blockers and f"required skip {ui}" in blockers
    assert "unmapped failure tests/unit/test_calendar.py::test_unmapped_helper" in blockers
    passed = [c for c in CASES if f"{c} FAIL" not in blockers and f"{c} BLOCKED_SKIP" not in blockers]
    assert len(passed) == 70 and verdict["acceptance"]["cases"]["passed"] == 70


def test_a72_live_gates_are_listed_precisely(app_root: Path) -> None:
    ids = [g.id for g in GATES]
    assert len(ids) == len(set(ids)) and {g.kind for g in GATES} == {BLOCKING, OPTIONAL}
    assert [g.id for g in blocking()] == [f"LG-{i:02d}" for i in range(1, 14)]
    for g in GATES:
        assert g.missing and g.effect and g.resolve and g.area, g.id
        assert (app_root / g.reference).exists(), (g.id, g.reference)
    for doc in ("docs/execution/FINAL_ACCEPTANCE.md", "docs/execution/RELEASE_CHECKLIST.md"):
        text = (app_root / doc).read_text(encoding="utf-8")
        assert all(g.id in text for g in GATES), doc                 # the documents list the same gates as the verdict
