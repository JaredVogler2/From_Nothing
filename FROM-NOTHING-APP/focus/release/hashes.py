"""Exact content hashes that bind an acceptance run and a review to what was built (S21 REVIEW, P24).

* ``implementation``: every shipped source file: the ``focus`` package, the optional ``native`` core, SQL, queries, policy,
  fixture inputs, tools
  and the hosting files (``Procfile``, ``manifest.yml``, ``runtime.txt``, requirements, CI draft).
* ``tests``: the test suite, including the acceptance map.
* ``source``: the fixture source inputs the acceptance run used. No live source run exists in this build.
* ``manual``: the build manual, found next to the app root (``docs/execution/ROOTS.md``) and checked against the
  ``manual_sha256`` recorded in ``STATUS.json``.
* ``policy``: the canonical hash the runtime computes for the active policy.

A tree hash is the SHA-256 of sorted ``path NUL sha256(content) LF`` lines. Text is hashed with LF line endings, so
a Windows checkout (``core.autocrlf``) and a Linux checkout bind the same review. Runtime state, caches, virtual
environments, docs and evidence are never part of a hash: they describe the build, they are not the build.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable

MANUAL_NAME = "FOCUS_FROM_NOTHING_MASTER.txt"
IMPLEMENTATION = ("focus", "native", "sql", "queries", "config", "fixtures", "tools", "scripts", "app.py", "build_demo.py",
                  "pyproject.toml", "requirements.txt", "requirements-solver.txt", "requirements.lock", "runtime.txt",
                  ".python-version", "Procfile", "manifest.yml", ".gitlab-ci.yml", ".env.example")
TESTS = ("tests",)
SOURCE = ("fixtures/inputs", "fixtures/source_contracts", "fixtures/workforce")
_SKIP_PARTS = frozenset({"__pycache__", ".pytest_cache", ".venv", ".git", "runtime", "node_modules", "target"})
_SKIP_SUFFIXES = (".pyc", ".pyo")


def file_digest(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" not in data:                                   # text: hash with LF endings on every platform
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _wanted(rel: Path) -> bool:
    if any(part in _SKIP_PARTS or part.endswith(".egg-info") for part in rel.parts[:-1]):
        return False
    return not rel.name.endswith(_SKIP_SUFFIXES)


def _git_files(app_root: Path, roots: Iterable[str]) -> list[str] | None:
    """Tracked plus new, not ignored, files under ``roots``, relative to the app root; None outside a git work tree."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *roots],
                             cwd=app_root, capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted(p for p in out.stdout.decode("utf-8").split("\0") if p)


def select_files(app_root: Path, roots: Iterable[str]) -> tuple[list[str], str]:
    """The files a tree hash covers and how they were chosen (``git`` tracked files, else a filtered walk)."""
    roots = tuple(roots)
    tracked = _git_files(app_root, roots)
    if tracked is not None:
        return sorted({p for p in tracked if (app_root / p).is_file() and _wanted(Path(p))}), "git"
    found: list[str] = []
    for root in roots:
        base = app_root / root
        if base.is_file():
            found.append(root)
        elif base.is_dir():
            found.extend(p.relative_to(app_root).as_posix() for p in base.rglob("*") if p.is_file() and _wanted(p.relative_to(app_root)))
    return sorted(set(found)), "walk"


def tree_hash(app_root: Path, roots: Iterable[str]) -> dict[str, Any]:
    files, method = select_files(app_root, roots)
    lines = "".join(f"{rel}\0{file_digest(app_root / rel)}\n" for rel in files)
    return {"sha256": hashlib.sha256(lines.encode("utf-8")).hexdigest(), "files": len(files), "method": method}


def find_manual(app_root: Path) -> Path | None:
    """The manual next to the app root: the repository layout, the declared layout, or ``FOCUS_MANUAL_PATH``."""
    explicit = os.environ.get("FOCUS_MANUAL_PATH")
    candidates = ([Path(explicit)] if explicit else []) + [app_root.parent / MANUAL_NAME, app_root.parent / "FROM-NOTHING" / MANUAL_NAME]
    return next((p for p in candidates if p.is_file()), None)


def recorded_manual_sha256(app_root: Path) -> str | None:
    status = app_root / "docs" / "execution" / "STATUS.json"
    if not status.is_file():
        return None
    return json.loads(status.read_text(encoding="utf-8")).get("manual_sha256")


def manual_hash(app_root: Path) -> dict[str, Any]:
    expected = recorded_manual_sha256(app_root)
    path = find_manual(app_root)
    if path is None:
        return {"sha256": None, "expected": expected, "status": "NOT_FOUND"}
    actual = hashlib.sha256(path.read_bytes()).hexdigest()       # the manual is bound byte for byte, as recorded at build start
    return {"sha256": actual, "expected": expected, "status": "MATCH" if actual == expected else "MISMATCH"}


def policy_hash(app_root: Path, policy_id: str = "rebuild-v1") -> dict[str, Any]:
    from ..policy import load_policy

    policy = load_policy(app_root / "config" / "policies", policy_id)
    return {"id": policy.id, "sha256": policy.hash}


def git_state(app_root: Path) -> dict[str, Any] | None:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=app_root, capture_output=True, timeout=30, check=True).stdout.decode().strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", *IMPLEMENTATION, *TESTS], cwd=app_root, capture_output=True,
                               timeout=60, check=True).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"head": head, "uncommitted_changes": bool(dirty)}


def build_hashes(app_root: Path) -> dict[str, Any]:
    """Every hash a review binds, computed from the files as they are now."""
    source = tree_hash(app_root, SOURCE)
    return {
        "implementation": tree_hash(app_root, IMPLEMENTATION),
        "tests": tree_hash(app_root, TESTS),
        "source": {"fixture": source, "live": "NOT_ACQUIRED: no live source run exists in this build"},
        "manual": manual_hash(app_root),
        "policy": policy_hash(app_root),
    }
