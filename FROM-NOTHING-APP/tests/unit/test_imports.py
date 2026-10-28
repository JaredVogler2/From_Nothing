"""A02: optional dependencies are lazy; only the local focus package is imported."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker(app_root: Path):
    spec = importlib.util.spec_from_file_location("check_imports", app_root / "tools" / "check_imports.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_a02_all_modules_import_with_optional_packages_blocked(app_root: Path) -> None:
    report = _load_checker(app_root).run()
    assert report["ok"], report
    assert report["loaded_optional"] == []
    assert report["local_package"] is True
    assert report["runtime_written"] is False
    assert "focus.web.app" in report["modules"]


def test_a02_sql_driver_absence_is_typed(app_root: Path, monkeypatch) -> None:
    import builtins

    from focus.config import Settings
    from focus.data.sql import SqlUnavailable, connect

    settings = Settings.from_env({"FOCUS_SQL_USER": "u", "FOCUS_SQL_PASSWORD": "p", "FOCUS_APP_ROOT": str(app_root)})
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pymssql":
            raise ImportError("pymssql removed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        connect(settings)
    except SqlUnavailable as exc:
        assert exc.code == "DRIVER_MISSING"
    else:  # pragma: no cover
        raise AssertionError("expected DRIVER_MISSING")


def test_reader_refuses_non_select_and_unlisted_objects() -> None:
    import pytest

    from focus.data.sql import SqlReader, SqlUnavailable

    SqlReader.check_read_only("SELECT LINE_NUMBER FROM WEB_APP.FOCUS_CDAY_OP_PLAN WHERE IS_ACTIVE=1")
    for bad in ("DELETE FROM WEB_APP.FOCUS_CDAY_OP_PLAN", "SELECT * FROM WEB_APP.SECRET_TABLE",
                "WITH x AS (SELECT 1) SELECT 1; DROP TABLE WEB_APP.FOCUS_FN_Policy"):
        with pytest.raises(SqlUnavailable):
            SqlReader.check_read_only(bad)
