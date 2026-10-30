"""Hosting files stay consistent (Posit Connect and Tanzu, Python 3.12): the default deploy is solver-free (P22)."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _pins(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            assert version, f"{path.name}: unpinned requirement {line!r}"
            out[name.lower()] = version
    return out


def test_default_requirements_never_pull_the_optional_solver(app_root: Path) -> None:
    base = _pins(app_root / "requirements.txt")
    assert "ortools" not in base and {"flask", "waitress", "pymssql", "tzdata"} <= set(base)


def test_solver_requirements_extend_the_base_with_identical_pins(app_root: Path) -> None:
    base, solver, lock = (_pins(app_root / name) for name in ("requirements.txt", "requirements-solver.txt", "requirements.lock"))
    assert {k: solver[k] for k in base} == base                                         # same base, nothing drifts
    assert "ortools" in solver and all(lock.get(k) == v for k, v in solver.items())    # every hosted pin was tested together
    spec = tomllib.loads((app_root / "pyproject.toml").read_text(encoding="utf-8"))
    extra = next(r for r in spec["project"]["optional-dependencies"]["solver"] if r.startswith("ortools"))
    low, high = re.findall(r"(\d+(?:\.\d+)*)", extra)[:2]
    version = tuple(int(x) for x in solver["ortools"].split("."))
    assert tuple(int(x) for x in low.split(".")) <= version < tuple(int(x) for x in high.split("."))


def test_python_312_everywhere(app_root: Path) -> None:
    assert (app_root / "runtime.txt").read_text(encoding="utf-8").strip() == "python-3.12.x"           # Tanzu buildpack
    spec = tomllib.loads((app_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert spec["project"]["requires-python"] == ">=3.12"
    assert (app_root / "requirements.lock").read_text(encoding="utf-8").startswith("# Python 3.12")
