"""P23: versioned migrations, host preflight, non-destructive retention and the role-separated pipeline.

Acceptance: A05, A07, A50, A51, A71. Every rule has a positive boundary and a known negative counterexample. Outcomes
are read back from durable records (catalog ledger, heads, published plans, commitment state, outbox), not from the
helper under test.
"""
from __future__ import annotations

import json
import shutil
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus import migrations as M
from focus import orchestration as O
from focus.clock import FIXTURE_CUTOFF
from focus.config import Settings
from focus.data.acquisition import source_head_name
from focus.data.fixture_reader import CaptureError, FixtureSourceReader
from focus.preflight import run as preflight

AT = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
INPUTS = Path(__file__).resolve().parents[2] / "fixtures" / "inputs" / "manufacturing_demo"


def _shipped(app_root: Path) -> list[M.Migration]:
    return M.discover(M.migrations_root(app_root))


def _copy_migrations(app_root: Path, tmp_path: Path) -> Path:
    root = tmp_path / "migrations"
    shutil.copytree(M.migrations_root(app_root), root, ignore=shutil.ignore_patterns("gridiron_*"))
    return root


# ---------------------------------------------------------------------- shipped migrations


def test_shipped_migrations_are_versioned_lint_clean_and_generated_files_fresh(app_root) -> None:
    shipped = _shipped(app_root)
    assert [m.version for m in shipped] == list(range(1, 11))
    assert all(m.rollback_path is not None for m in shipped)
    assert M.all_findings(shipped, app_root) == []                                   # forward, rollback, grants, freshness
    assert (app_root / "sql" / "grants" / "focus_fn_roles.sql").read_text(encoding="utf-8") == M.grants_script()
    assert not any("draft" in m.path.name for m in shipped)                          # other modules' design drafts are never applied
    assert not list(M.migrations_root(app_root).glob("*_draft.sql")) or all(
        p.name.startswith("gridiron_") for p in M.migrations_root(app_root).glob("*_draft.sql"))
    first = (M.migrations_root(app_root) / "001_focus_fn_core.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE WEB_APP.FOCUS_FN_SchemaVersion" in first                    # S06 001 stays the verbatim core


def test_a71_dry_run_apply_and_replay_are_idempotent(tmp_path, app_root) -> None:
    shipped = _shipped(app_root)
    catalog = M.FixtureCatalog(tmp_path / "catalog.json")
    assert {s.state for s in M.plan(shipped, catalog.state())} == {"PENDING"}
    first = M.apply_pending(shipped, catalog, installer="dba-test", at=AT)
    assert first["status"] == "ok" and [a["version"] for a in first["applied"]] == list(range(1, 11))
    state = catalog.state()
    assert {v: r["sha256"] for v, r in state.ledger.items()} == {m.version: m.sha256 for m in shipped}
    assert all(k in state.objects for m in shipped for k in m.object_keys)            # every declared object verified
    again = M.apply_pending(shipped, catalog, installer="dba-test", at=AT)
    assert again["status"] == "ok" and again["applied"] == [] and {s["state"] for s in again["steps"]} == {"APPLIED"}
    attempts = json.loads((tmp_path / "catalog.json").read_text())["attempts"]
    assert [a["outcome"] for a in attempts] == ["APPLIED"] * 10                        # a replay applies nothing twice


def test_a71_checksum_mismatch_blocks_every_later_version(tmp_path, app_root) -> None:
    root = _copy_migrations(app_root, tmp_path)
    catalog = M.FixtureCatalog(tmp_path / "catalog.json")
    assert M.apply_pending(M.discover(root), catalog, installer="dba-test", at=AT)["status"] == "ok"
    tampered = root / "005_duration_statistics.sql"
    tampered.write_text(tampered.read_text(encoding="utf-8") + "\n-- edited after install\n", encoding="utf-8")
    extra = root / "011_extra.sql"
    extra.write_text("CREATE TABLE WEB_APP.FOCUS_FN_Extra(ExtraId bigint NOT NULL PRIMARY KEY, CreatedAtUtc datetime2(6) NOT NULL);\n", encoding="utf-8")
    migrations = M.discover(root)
    (root / "rollback" / "011_extra.sql").write_text(M.rollback_script(migrations[-1]), encoding="utf-8")
    migrations = M.discover(root)
    states = {s.version: s.state for s in M.plan(migrations, catalog.state())}
    assert states[5] == "CHECKSUM_MISMATCH" and states[11] == "BLOCKED" and states[4] == "APPLIED"
    refused = M.apply_pending(migrations, catalog, installer="dba-test", at=AT)
    assert refused["status"] == "refused" and refused["reason"] == "PLAN_BLOCKED"
    assert 11 not in catalog.state().ledger


def test_a71_unrecorded_object_is_a_collision(tmp_path, app_root) -> None:
    shipped = _shipped(app_root)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"schema_version": 1, "objects": ["TABLE:WEB_APP.FOCUS_FN_SourceRun"], "ledger": {}, "rows": {}, "attempts": []}))
    catalog = M.FixtureCatalog(path)
    states = [s.state for s in M.plan(shipped, catalog.state())]
    assert states[0] == "COLLISION" and set(states[1:]) == {"BLOCKED"}
    assert M.apply_pending(shipped, catalog, installer="dba-test", at=AT)["status"] == "refused"
    assert catalog.state().ledger == {}                                               # nothing recorded
    assert M.unaccounted(shipped, catalog.state()) == []                              # it is a shipped name, so a collision


def test_failure_mid_version_records_nothing_and_resumes_forward(tmp_path, app_root) -> None:
    shipped = _shipped(app_root)
    path = tmp_path / "catalog.json"
    failing = M.FixtureCatalog(path, fail_at=(3, 2))
    result = M.apply_pending(shipped, failing, installer="dba-test", at=AT)
    assert result["status"] == "error" and result["failed"]["version"] == 3 and [a["version"] for a in result["applied"]] == [1, 2]
    state = failing.state()
    assert sorted(state.ledger) == [1, 2]
    assert not any(k in state.objects for k in shipped[2].object_keys)                # the failed version left nothing behind
    attempts = json.loads(path.read_text())["attempts"]
    assert attempts[-1]["version"] == 3 and attempts[-1]["outcome"] == "FAILED"       # the attempt receipt is durable
    resumed = M.apply_pending(shipped, M.FixtureCatalog(path), installer="dba-test", at=AT)
    assert resumed["status"] == "ok" and [a["version"] for a in resumed["applied"]] == list(range(3, 11))


def test_rollback_only_the_latest_version_and_only_without_data(tmp_path, app_root) -> None:
    shipped = _shipped(app_root)
    catalog = M.FixtureCatalog(tmp_path / "catalog.json")
    M.apply_pending(shipped, catalog, installer="dba-test", at=AT)
    assert M.rollback_latest(shipped, catalog, 9, installer="dba-test", at=AT)["reason"] == "NOT_LATEST"
    catalog.set_rows("WEB_APP.FOCUS_FN_TeamShiftPerformance", 3)
    refused = M.rollback_latest(shipped, catalog, 10, installer="dba-test", at=AT)
    assert refused["reason"] == "ROLLBACK_REFUSED_DATA" and 10 in catalog.state().ledger
    catalog.set_rows("WEB_APP.FOCUS_FN_TeamShiftPerformance", 0)
    done = M.rollback_latest(shipped, catalog, 10, installer="dba-test", at=AT)
    assert done["status"] == "ok" and sorted(catalog.state().ledger) == list(range(1, 10))
    assert not any(k in catalog.state().objects for k in shipped[9].object_keys)
    assert M.apply_pending(shipped, catalog, installer="dba-test", at=AT)["applied"] == [{"version": 10, "name": "performance_rollup",
                                                                                          "sha256": shipped[9].sha256}]


def test_lint_rejects_destructive_misnamed_or_unbound_sql(tmp_path) -> None:
    root = tmp_path / "bad"
    root.mkdir()
    (root / "001_bad.sql").write_text("""
CREATE TABLE WEB_APP.Other(Id int NOT NULL);
CREATE TABLE WEB_APP.FOCUS_FN_Good(Id bigint NOT NULL PRIMARY KEY, SeenUtc datetime NOT NULL, Payload varchar(100) NOT NULL);
TRUNCATE TABLE WEB_APP.FOCUS_FN_Good;
GO
CREATE OR ALTER VIEW WEB_APP.FOCUS_FN_Early AS SELECT Id FROM WEB_APP.FOCUS_FN_Later;
GO
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_Wipe AS BEGIN DELETE FROM WEB_APP.FOCUS_FN_Good; END;
GO
CREATE OR ALTER PROCEDURE WEB_APP.FOCUS_FN_PurgeOld AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @lock int; EXEC @lock = sp_getapplock @Resource = N'FOCUS_FN_X', @LockMode = 'Exclusive', @LockOwner = 'Transaction';
    EXEC sp_set_session_context @key = N'focus_retention', @value = 1;
    DELETE g FROM WEB_APP.FOCUS_FN_Good g WHERE g.Id < 0;
    COMMIT TRANSACTION;
END;
GO
CREATE OR ALTER TRIGGER WEB_APP.TR_FN_Good_Immutable ON WEB_APP.FOCUS_FN_Good AFTER UPDATE, DELETE AS BEGIN THROW 51000, 'x', 1; END;
""", encoding="utf-8")
    codes = {(f.code, f.subject) for f in M.lint(M.discover(root))}
    assert ("NAMING", "TABLE:WEB_APP.Other") in codes and ("PRIMARY_KEY", "WEB_APP.Other") in codes
    assert ("INSTANT_PRECISION", "WEB_APP.FOCUS_FN_Good.SeenUtc") in codes and ("LEGACY_DATETIME", "WEB_APP.FOCUS_FN_Good.SeenUtc") in codes
    assert ("PAYLOAD_TYPE", "WEB_APP.FOCUS_FN_Good.Payload") in codes
    assert ("DESTRUCTIVE", "TRUNCATE TABLE") in codes
    assert ("BINDING_ORDER", "WEB_APP.FOCUS_FN_Early") in codes
    assert ("DELETE_OUTSIDE_RETENTION", "WEB_APP.FOCUS_FN_Wipe") in codes
    assert not any(c[1] == "WEB_APP.FOCUS_FN_PurgeOld" for c in codes)                # a locked retention purge is allowed
    assert not any(c[1] == "WEB_APP.TR_FN_Good_Immutable" for c in codes)             # "AFTER UPDATE, DELETE" is an event list
    assert ("ROLLBACK_MISSING", "001_bad.sql") in codes


def test_versions_are_contiguous_and_unique(tmp_path) -> None:
    gap = tmp_path / "gap"
    gap.mkdir()
    for name in ("001_a.sql", "003_c.sql"):
        (gap / name).write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(M.MigrationError) as err:
        M.discover(gap)
    assert err.value.code == "VERSION_GAP"
    dup = tmp_path / "dup"
    dup.mkdir()
    for name in ("001_a.sql", "001_b.sql"):
        (dup / name).write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(M.MigrationError) as err:
        M.discover(dup)
    assert err.value.code == "DUPLICATE_VERSION"


def test_grants_keep_separation_of_duties(app_root, monkeypatch) -> None:
    shipped = _shipped(app_root)
    assert M.lint_grants(shipped) == []
    bad = {**M.ROLE_GRANTS, "FOCUS_FN_Stager": {"EXECUTE": ("StagePlan", "ReadPlanStage", "RecordValidation", "AcceptPlan")},
           "FOCUS_FN_AppReader": {**M.ROLE_GRANTS["FOCUS_FN_AppReader"], "EXECUTE": ("BeginSourceRun",)},
           "FOCUS_FN_Ghost": {"SELECT": ("NoSuchView",)}}
    monkeypatch.setattr(M, "ROLE_GRANTS", bad)
    codes = {(f.code, f.subject) for f in M.lint_grants(shipped)}
    assert ("SEPARATION_OF_DUTIES", "AcceptPlan") in codes                            # only the signer may accept
    assert ("SEPARATION_OF_DUTIES", "FOCUS_FN_AppReader") in codes                    # the app reader executes nothing
    assert ("GRANT_UNKNOWN_OBJECT", "FOCUS_FN_Ghost:NoSuchView") in codes


def test_cli_bundle_and_live_install_needs_a_confirmed_installer(services_factory, tmp_path) -> None:
    services = services_factory()
    args = Namespace(dry_run=True, apply=False, rollback=None, catalog=str(tmp_path / "c.json"), installer=None, emit_dir=str(tmp_path / "bundle"),
                     write_generated=False, confirm_manifest=None)
    result = M.cli_migrate(services, args)
    assert result["status"] == "ok" and len(result["migrations"]) == 10 and len(result["manifest_sha256"]) == 64
    manifest = json.loads((tmp_path / "bundle" / "MANIFEST.json").read_text())
    assert [m["version"] for m in manifest["migrations"]] == list(range(1, 11)) and manifest["grants"]["file"] == "focus_fn_roles.sql"
    assert all((tmp_path / "bundle" / "rollback" / m["rollback"]).exists() for m in manifest["migrations"])
    live = services_factory(mode="live", identity_provider="trusted_header", trusted_user_header="X-Forwarded-User")
    dry = M.cli_migrate(live, Namespace(**{**vars(args), "catalog": "live", "emit_dir": None}))
    assert dry["status"] == "not_probed"                                              # never "ok" without probing the target
    apply = M.cli_migrate(live, Namespace(**{**vars(args), "catalog": "live", "emit_dir": None, "apply": True}))
    assert apply["status"] == "refused" and apply["error"]["code"] == "INSTALLER_SESSION_REQUIRED"


# ---------------------------------------------------------------------- host preflight


def _live_env(app_root: Path, **extra: str) -> dict[str, str]:
    return {"FOCUS_MODE": "live", "FOCUS_IDENTITY_PROVIDER": "trusted_header", "FOCUS_TRUSTED_USER_HEADER": "X-Forwarded-User",
            "VCAP_APPLICATION": "{}", "FOCUS_APP_ROOT": str(app_root), **extra}


def test_preflight_fixture_passes_and_live_gaps_fail(app_root) -> None:
    ok = preflight(Settings.from_env({}, app_root=app_root))
    assert ok["status"] == "ok" and {c["name"] for c in ok["checks"]} >= {"interpreter", "policy", "storage", "app", "migrations"}
    gaps = preflight(env=_live_env(app_root))
    assert gaps["status"] == "error" and gaps["platform"] == "tanzu" and {"session_secret", "sql"} <= set(gaps["failed"])
    ready = preflight(env=_live_env(app_root, FOCUS_SESSION_SECRET="x" * 48, FOCUS_SQL_USER="reader", FOCUS_SQL_PASSWORD="not-a-real-secret",
                                    FOCUS_SECURE_COOKIES="true"),
                      probe=lambda: {m.version: m.sha256 for m in _shipped(app_root)})
    assert ready["status"] == "ok", ready["failed"]
    assert next(c for c in ready["checks"] if c["name"] == "schema")["status"] == "PASS"
    assert "not-a-real-secret" not in json.dumps(ready)                               # presence only, never a value
    behind = preflight(env=_live_env(app_root, FOCUS_SESSION_SECRET="x" * 48, FOCUS_SQL_USER="reader", FOCUS_SQL_PASSWORD="p"),
                       probe=lambda: {1: _shipped(app_root)[0].sha256})
    assert "schema" in behind["failed"]                                               # versions 2..9 not installed


def test_live_host_with_sql_credentials_starts_with_typed_unavailability(app_root) -> None:
    """Before the FOCUS_FN schema is installed a live host starts and says why nothing is stored (no crash, no connection)."""
    from focus.data.sql_store import SqlStore
    from focus.runtime import default_store
    from focus.web.app import create_app

    settings = Settings.from_env(_live_env(app_root, FOCUS_SESSION_SECRET="x" * 48, FOCUS_SQL_USER="reader", FOCUS_SQL_PASSWORD="p"),
                                 app_root=app_root)
    store = default_store(settings)
    assert isinstance(store, SqlStore) and not store.available() and "migrations 001-010" in store.reason
    client = create_app(settings=settings).test_client()
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and "REPOSITORY_UNAVAILABLE" in {r["code"] for r in ready.get_json()["reasons"]}


def test_preflight_python_role_and_platform_rules(app_root) -> None:
    old = preflight(Settings.from_env({}, app_root=app_root), interpreter=((3, 11), "CPython"))
    assert old["status"] == "error" and "interpreter" in old["failed"]
    connect = preflight(env={"RSTUDIO_PRODUCT": "CONNECT", "FOCUS_APP_ROOT": str(app_root)})
    assert connect["platform"] == "posit_connect"
    signer = preflight(env=_live_env(app_root, FOCUS_SESSION_SECRET="x" * 48, FOCUS_SQL_USER="u", FOCUS_SQL_PASSWORD="p"), role="signer")
    assert "signing" in signer["failed"]                                              # the signer role needs a referenced key
    web = preflight(env=_live_env(app_root, FOCUS_SESSION_SECRET="x" * 48, FOCUS_SQL_USER="u", FOCUS_SQL_PASSWORD="p",
                                  FOCUS_SIGN_KEY_ID="KEY-1", FOCUS_SIGN_KEY_REF="env:SIGN_KEY"))
    assert next(c for c in web["checks"] if c["name"] == "signing")["status"] == "WARN"   # signing material off the web role


# ---------------------------------------------------------------------- the role-separated pipeline


@pytest.fixture()
def pipeline_services(services_factory):
    from focus.pipeline import cli_demo_bootstrap

    services = services_factory()
    assert cli_demo_bootstrap(services, SimpleNamespace(scenario="manufacturing_demo", reset=False))["status"] == "ok"
    return services


def _pid(services, key="p23"):
    return O.create(services, key=key, budget_seconds=10, max_moves=10)["pipeline_id"]


def _published(services) -> list[str]:
    return services.repositories.list_ids("published_plans")


ROLE_SEQUENCE = ("source", "app", "stager", "signer", "app", "stager", "signer", "evaluator")   # executable first, then the forecast


def test_a71_roles_order_and_idempotent_replay(pipeline_services) -> None:
    services = pipeline_services
    pid = _pid(services)
    assert O.run_role(services, pid, "signer")[0]["outcome"] == "BLOCKED"            # no signing before a clean readback
    assert O.run_step(services, pid, "acquire", role="app")["outcome"] == "ROLE_MISMATCH"
    for role in ROLE_SEQUENCE:
        ran = [r for r in O.run_role(services, pid, role) if r["outcome"] != "ALREADY_COMPLETED"]
        assert ran and all(r["outcome"] in ("COMPLETED", "BLOCKED") for r in ran) and ran[0]["outcome"] == "COMPLETED", (role, ran)
    status = O.status(services, pid)
    assert status["state"] == "completed" and len(status["outbox"]) == len(O.STEPS)
    stage = O.load(services, pid)[0]["steps"]["stage_readback"]["output"]["EXECUTABLE"]
    assert stage["status"] == "STAGED" and stage["readback_validation_id"]            # the stager stopped before signing
    plans = _published(services)
    replay = O.run_role(services, pid, "all")
    assert {r["outcome"] for r in replay} == {"ALREADY_COMPLETED"}
    assert _published(services) == plans and O.status(services, pid)["outbox"] == status["outbox"]


def test_role_lists_name_roles_once_each() -> None:
    assert O.roles_of("all") == O.ROLES and O.roles_of("app") == ("app",)
    assert O.roles_of("source,app,stager") == ("source", "app", "stager") and O.roles_of("app, stager") == ("app", "stager")
    for bad in ("", "planner", "app,", "app,app", "source,bogus", "all,app", ","):
        assert O.roles_of(bad) is None, bad


JOB_SEQUENCE = ("source,app,stager", "signer", "app,stager", "signer", "evaluator")     # four plan jobs where the roles alone take seven


def test_a_job_that_runs_several_roles_parses_the_snapshot_once_and_reads_no_candidate_back(pipeline_services, monkeypatch) -> None:
    """DEC-36 (g): one process runs the source, app and stager steps in a row. It holds the snapshot it has just
    admitted and the candidate it has just stored, so it builds neither again; the signer, a job of its own, reads
    everything from the store. Step by step the pipeline is the one the roles alone would have run."""
    import focus.data.acquisition as acquisition
    from focus.contracts.planning import CandidatePlan, PlanningSnapshot

    services = pipeline_services
    pid = _pid(services, "combined")
    built: list[str] = []
    real = acquisition.from_jsonable_releasing

    def counted(tp, data, path="$"):
        if tp is PlanningSnapshot:
            built.append("snapshot")
        return real(tp, data, path)
    monkeypatch.setattr(acquisition, "from_jsonable_releasing", counted)
    read: list[str] = []
    get_typed = services.repositories.get_typed

    def spy(collection, record_id, type_name, cls, *args, **kwargs):
        found = get_typed(collection, record_id, type_name, cls, *args, **kwargs)
        if cls is CandidatePlan and found is not None:      # a stored candidate read back and decoded
            read.append(record_id)
        return found
    monkeypatch.setattr(services.repositories, "get_typed", spy)

    first = O.run_role(services, pid, JOB_SEQUENCE[0])
    assert [(r["step"], r["outcome"]) for r in first] == [("acquire", "COMPLETED"), ("admit", "COMPLETED"), ("schedule", "COMPLETED"),
                                                          ("validate", "COMPLETED"), ("stage_readback", "COMPLETED"), ("forecast", "BLOCKED")]
    steps = O.load(services, pid)[0]["steps"]
    candidate = steps["schedule"]["output"]["EXECUTABLE"]["candidate_id"]
    assert built == []                                    # the snapshot this process admitted was never parsed again
    assert candidate not in read                          # nor was the candidate it stored read back for staging
    assert steps["stage_readback"]["output"]["EXECUTABLE"]["status"] == "STAGED" and not _published_since(services, steps)

    monkeypatch.setattr(acquisition, "_VERIFIED", {})     # the signer is another process: it holds nothing
    from focus.engine.max.optimizer import orchestrator as scheduling

    monkeypatch.setattr(scheduling, "_SCHEDULED", {})
    signed = O.run_role(services, pid, JOB_SEQUENCE[1])
    assert [(r["step"], r["outcome"]) for r in signed] == [("sign_accept", "COMPLETED"), ("forecast_sign_accept", "BLOCKED")]
    assert built == ["snapshot"] and candidate in read    # it loaded the stored snapshot and the stored candidate itself

    for job in JOB_SEQUENCE[2:]:
        ran = [r for r in O.run_role(services, pid, job) if r["outcome"] != "ALREADY_COMPLETED"]
        assert ran and ran[0]["outcome"] == "COMPLETED", (job, ran)
    status = O.status(services, pid)
    assert status["state"] == "completed" and len(status["outbox"]) == len(O.STEPS)
    steps = O.load(services, pid)[0]["steps"]
    assert {steps["sign_accept"]["output"]["EXECUTABLE"]["status"], steps["forecast_sign_accept"]["output"]["FORECAST"]["status"]} == {"ACCEPTED"}
    validations = {v for name in ("stage_readback", "forecast_stage_readback") for v in [next(iter(steps[name]["output"].values()))["readback_validation_id"]]}
    assert len(validations) == 2 and all(validations)


def _published_since(services, steps) -> list[str]:
    """Published plans of this pipeline's shift-plan candidate (none before its signer has run)."""
    candidate = steps["schedule"]["output"]["EXECUTABLE"]["candidate_id"]
    from focus.publication.repository import repository_for

    repo = repository_for(services)
    return [pid for pid in repo.accepted_ids() if repo.published(pid).candidate_id == candidate]


def test_jobs_of_several_roles_accept_the_same_plans_as_the_roles_alone(services_factory) -> None:
    from focus.pipeline import cli_demo_bootstrap

    accepted = {}
    for name, sequence in (("roles", ROLE_SEQUENCE), ("jobs", JOB_SEQUENCE)):
        services = services_factory()
        assert cli_demo_bootstrap(services, SimpleNamespace(scenario="manufacturing_demo", reset=False))["status"] == "ok"
        pid = _pid(services, "same")
        for role in sequence:
            O.run_role(services, pid, role)
        steps = O.load(services, pid)[0]["steps"]
        assert O.status(services, pid)["state"] == "completed", name
        accepted[name] = (steps["schedule"]["output"]["EXECUTABLE"]["candidate_id"], steps["sign_accept"]["output"]["EXECUTABLE"]["plan_id"],
                          steps["forecast"]["output"]["FORECAST"]["candidate_id"], steps["forecast_sign_accept"]["output"]["FORECAST"]["plan_id"])
    assert accepted["roles"] == accepted["jobs"] and all(accepted["jobs"])        # candidates and plans, id for id


def test_cli_refuses_a_role_list_with_an_unknown_or_repeated_role(pipeline_services) -> None:
    for bad in ("source,planner", "app,app", ""):
        out = O.cli_pipeline(pipeline_services, Namespace(key="bad-role", role=bad, status=False, step=None))
        if bad == "":
            assert out["status"] != "invalid"              # no role given means all
            continue
        assert out["status"] == "invalid" and out["error"]["code"] == "BAD_ROLE", bad
    out = O.cli_pipeline(pipeline_services, Namespace(key="list-role", role="source,app,stager", status=False, step=None))
    assert out["status"] == "ok" and [r["step"] for r in out["ran"] if r["outcome"] == "COMPLETED"] == [
        "acquire", "admit", "schedule", "validate", "stage_readback"]
    one = O.cli_pipeline(pipeline_services, Namespace(key="list-role", role="app,stager", status=False, step="sign_accept"))
    assert one["ran"][0]["outcome"] == "ROLE_MISMATCH"     # a step outside the job's roles is never run by it


def test_executable_plan_is_accepted_before_the_forecast_is_scheduled(pipeline_services) -> None:
    """The shift's dispatch never waits for the forecast; the forecast still runs and publishes on the same snapshot."""
    from focus.publication.service import current_plan

    services = pipeline_services
    pid = _pid(services, "exec-first")
    scope = services.settings.scope_key
    O.run_role(services, pid, "source")
    app = {r["step"]: r["outcome"] for r in O.run_role(services, pid, "app")}
    assert app == {"schedule": "COMPLETED", "validate": "COMPLETED", "forecast": "BLOCKED"}      # no forecast before acceptance
    O.run_role(services, pid, "stager")
    O.run_role(services, pid, "signer")
    executable, _ = current_plan(services, scope, "EXECUTABLE")
    assert executable is not None and O.load(services, pid)[0]["steps"]["forecast"]["status"] == "pending"
    O.run_role(services, pid, "app")
    O.run_role(services, pid, "stager")
    O.run_role(services, pid, "signer")
    steps = O.load(services, pid)[0]["steps"]
    forecast, _ = current_plan(services, scope, "FORECAST")
    assert forecast is not None and steps["forecast_sign_accept"]["output"]["FORECAST"]["plan_id"] == forecast.published.id
    assert forecast.published.source_run_id == executable.published.source_run_id                   # the same admitted snapshot


def test_the_window_report_measures_the_data_cut_to_the_accepted_plan_against_the_owners_budget(pipeline_services) -> None:
    """D-28 and D-33: gathering, digesting and scheduling within 20 minutes. The clock runs from the data cut. The shift
    plan's acceptance writes the dashboard's data (the task lists); the delivery plan's acceptance completes the schedule,
    and the budget is about the whole schedule."""
    from focus.clock import FIXTURE_CUTOFF
    from focus.display.store import has_plan_display

    services = pipeline_services
    assert services.policy.int("pipeline.pre_shift_budget_minutes") == 20
    assert services.policy.authority_of("pipeline.pre_shift_budget_minutes") == "owner_decision"
    pid = _pid(services, "window")
    before = O.status(services, pid)["window"]
    assert (before["budget_minutes"], before["data_cut"], before["minutes"], before["within_budget"]) == (20, None, None, None)
    for role, minutes in (("source", 6), ("app", 3), ("stager", 2)):
        O.run_role(services, pid, role)
        services.clock.advance(minutes=minutes)
    waiting = O.status(services, pid)["window"]
    assert waiting["data_cut"] == FIXTURE_CUTOFF and waiting["plan_and_dashboard_ready_at"] is None and waiting["within_budget"] is None
    O.run_role(services, pid, "signer")
    w = O.status(services, pid)["window"]
    plan_id = O.load(services, pid)[0]["steps"]["sign_accept"]["output"]["EXECUTABLE"]["plan_id"]
    assert has_plan_display(services.repositories, plan_id)                             # acceptance wrote the dashboard's data
    assert (w["minutes"], w["shift_plan_within_budget"]) == (11.0, True)                 # 6 + 3 + 2 minutes after the cut
    assert (w["schedule_minutes"], w["delivery_plan_ready_at"], w["within_budget"]) == (None, None, None)   # the schedule is not whole yet
    assert w["plans_shift"] == "DEMO:2026-10-26:S1" and w["late"] is True                # the fixture cuts at the shift's start
    assert set(w["step_seconds"]) == set(O.WINDOW_STEPS + O.DELIVERY_STEPS)
    assert all(w["step_seconds"][name] == 0.0 for name in O.WINDOW_STEPS) and all(w["step_seconds"][name] is None for name in O.DELIVERY_STEPS)
    services.clock.advance(minutes=12)
    for role in ("app", "stager", "signer"):                                             # the delivery plan follows acceptance
        O.run_role(services, pid, role)
    over = O.status(services, pid)["window"]                                             # the task lists in time, the whole schedule not
    assert (over["minutes"], over["shift_plan_within_budget"], over["schedule_minutes"], over["within_budget"]) == (11.0, True, 23.0, False)
    assert over["delivery_plan_ready_at"] == FIXTURE_CUTOFF + timedelta(minutes=23)
    assert all(over["step_seconds"][name] == 0.0 for name in O.DELIVERY_STEPS)
    slow = _pid(services, "window-slow")
    O.run_role(services, slow, "source")
    services.clock.advance(minutes=25)
    for role in ("app", "stager", "signer"):
        O.run_role(services, slow, role)
    over = O.status(services, slow)["window"]
    assert over["minutes"] > 20 and over["shift_plan_within_budget"] is False and over["within_budget"] is False   # known at once


def test_the_window_is_met_only_when_the_delivery_plan_is_accepted_inside_it_too(pipeline_services) -> None:
    """D-33: one schedule through delivery, made within the 20 minutes."""
    services = pipeline_services
    pid = _pid(services, "window-whole")
    for role, minutes in (("source", 6), ("app", 3), ("stager", 2), ("signer", 4)):
        O.run_role(services, pid, role)
        services.clock.advance(minutes=minutes)
    for role in ("app", "stager", "signer"):
        O.run_role(services, pid, role)
    w = O.status(services, pid)["window"]
    assert (w["minutes"], w["shift_plan_within_budget"], w["schedule_minutes"], w["within_budget"]) == (11.0, True, 15.0, True)


def test_the_pipeline_reports_whether_the_cost_objective_steered_each_plan(pipeline_services) -> None:
    """A plan is returned whether or not its dollars compared anything, so each run says how many aircraft had a known
    rate, due date and finish date, and why open jobs were not planned (OBJECTIVE_EXPLAINED.md, section 4)."""
    services = pipeline_services
    pid = _pid(services, "objective")
    assert O.status(services, pid)["objective"] == {}                                   # nothing scheduled yet
    assert O.status(services, pid)["readiness_gaps"] is None                            # nothing admitted yet
    O.run_role(services, pid, "source")
    # the admitted data's gaps by code: the demo has two, and no calendar that stops short of a due date (DEC-28)
    assert O.status(services, pid)["readiness_gaps"] == {"IN_PROGRESS_CONTINUATION_HOLD": 1, "SEQUENCE_VIOLATION": 1}
    O.run_role(services, pid, "app")
    executable = O.status(services, pid)["objective"]
    assert set(executable) == {"EXECUTABLE"}                                            # the forecast waits for acceptance
    e = executable["EXECUTABLE"]
    assert (e["aircraft"], e["cost_known"], e["rate_known"], e["due_date_known"]) == (6, 4, 5, 6) and e["cost_steering"] == "partial"
    assert e["aircraft_without_rate"] == [1306] and e["aircraft_without_due_date"] == []   # the demo's one aircraft with no term
    assert e["aircraft_without_finish"] == [1305, 1306] and e["finish_unknown"] == 2
    assert e["cost_unknown_by_reason"] == {"AIRCRAFT_NOT_MAPPED": 1, "COMPLETION_UNKNOWN": 1}   # the first cause on each aircraft
    assert e["unplanned_open_jobs"] == sum(e["unplanned_open_jobs_by_reason"].values()) == 7
    assert e["unplanned_open_jobs_by_reason"]["PART_NOT_RECEIVED"] == 2                 # confirmed facts only: no part, no plan
    for role in ("stager", "signer", "app", "stager", "signer"):
        O.run_role(services, pid, role)
    both = O.status(services, pid)["objective"]
    assert both["EXECUTABLE"] == e
    f = both["FORECAST"]
    assert (f["cost_known"], f["finish_conditional"], f["finish_unknown"]) == (5, 1, 1)  # an expected part gives a conditional finish
    assert f["aircraft_without_finish"] == [1306] and f["unplanned_open_jobs_by_reason"] == {"FLOW_CREW_INVALID": 1}
    assert f["aircraft_without_rate"] == [1306] and "5 of 6 aircraft" in f["message"]
    # D-29: the same status reports each aircraft's criticality (the cost of one more day late). Nothing is scored with it.
    crit = O.status(services, pid)["criticality"]
    assert set(crit) == {"EXECUTABLE", "FORECAST"}
    fc = crit["FORECAST"]
    assert (fc["aircraft"], fc["known"], fc["unknown"]) == (6, 5, 1) and fc["states"] == {"ON_TIME": 5, "NO_RATE_OR_DUE_DATE": 1}
    assert fc["highest_cost_per_day_usd"] == 0                                           # the demo fleet finishes ahead of its due dates
    assert {x["line"]: x["weight"] for x in fc["lines"]}[1306] is None                   # no rate: unknown, never zero
    assert crit["EXECUTABLE"]["states"] == {"ON_TIME": 4, "FINISH_UNKNOWN": 1, "NO_RATE_OR_DUE_DATE": 1}


def test_pipeline_plans_with_the_requested_planning_mode(pipeline_services) -> None:
    """As shipped a pipeline repairs the accepted plan, whichever shift it plans (``auto``, D-33). A pipeline asked for the
    full solve (the nightly one) runs it and records how far a repair would have landed. A ``repair`` pipeline repairs."""
    services = pipeline_services
    first = _pid(services, "plan-auto")
    assert O.load(services, first)[0]["request"]["planning"] == services.policy.str("planning.default_mode") == "auto"
    for role in ROLE_SEQUENCE:
        O.run_role(services, first, role)
    steps = O.load(services, first)[0]["steps"]
    auto = steps["schedule"]["output"]["EXECUTABLE"]["planning"]
    assert (auto["requested"], auto["ran"]) == ("auto", "repair") and "the incumbent's cutoff is" in auto["reason"]
    assert auto["kept"] + auto["rehomed"] <= auto["hints"] and "drift" not in auto
    delivery = steps["forecast"]["output"]["FORECAST"]["planning"]
    assert delivery["ran"] == "repair" and delivery["follows"]["purpose"] == "EXECUTABLE"     # it follows the shift plan just accepted
    assert delivery["follows"]["plan"] != auto.get("plan") and delivery["rehomed"] <= delivery["hints"]
    nightly = O.create(services, key="plan-nightly", budget_seconds=10, max_moves=10, planning="full")["pipeline_id"]
    for role in ROLE_SEQUENCE:
        O.run_role(services, nightly, role)
    assert O.status(services, nightly)["state"] == "completed"
    steps = O.load(services, nightly)[0]["steps"]
    for step, purpose in (("schedule", "EXECUTABLE"), ("forecast", "FORECAST")):
        record = steps[step]["output"][purpose]["planning"]
        assert (record["requested"], record["ran"], record["reason"]) == ("full", "full", "full requested")
        assert record["drift"]["repair_seconds"] >= 0                              # the accepted plan was repaired for comparison
    second = O.create(services, key="plan-repair", budget_seconds=10, max_moves=10, planning="repair")["pipeline_id"]
    for role in ROLE_SEQUENCE:
        O.run_role(services, second, role)
    assert O.status(services, second)["state"] == "completed"
    steps = O.load(services, second)[0]["steps"]
    for step, purpose in (("schedule", "EXECUTABLE"), ("forecast", "FORECAST")):
        record = steps[step]["output"][purpose]["planning"]
        assert (record["requested"], record["ran"]) == ("repair", "repair") and record["kept"] + record["rehomed"] <= record["hints"]
    assert steps["forecast"]["output"]["FORECAST"]["planning"]["follows"]["purpose"] == "EXECUTABLE"


def test_a71_cli_role_that_only_waited_is_not_a_success(pipeline_services) -> None:
    services = pipeline_services
    args = Namespace(key="cli", role="signer", step=None, status=False, scenario=None, budget_seconds=10, max_moves=10, lease_seconds=900)
    early = O.cli_pipeline(services, args)
    assert early["status"] == "blocked" and early["ran"][0]["outcome"] == "BLOCKED"   # exits non-zero in CI
    bad_role = O.cli_pipeline(services, Namespace(**{**vars(args), "role": "root"}))
    assert bad_role["status"] == "invalid"
    assert O.cli_pipeline(services, Namespace(**{**vars(args), "role": "source"}))["status"] == "ok"
    assert O.cli_pipeline(services, Namespace(**{**vars(args), "status": True}))["steps"][1]["status"] == "completed"


def test_a71_live_lease_blocks_a_competitor_and_an_expired_one_is_taken_over(pipeline_services) -> None:
    services = pipeline_services
    pid = _pid(services)
    O.run_role(services, pid, "source")
    first = O.claim(services, pid, "schedule", owner="worker-a", role="app", lease_seconds=60)
    assert first.outcome == "CLAIMED"
    assert O.claim(services, pid, "schedule", owner="worker-b", role="app", lease_seconds=60).outcome == "LEASED"
    services.clock.advance(seconds=61)
    taken = O.run_step(services, pid, "schedule", role="app", owner="worker-b")
    assert taken["outcome"] == "COMPLETED_AFTER_TAKEOVER"
    late, why = O._finish(services, pid, "schedule", "worker-a", status="completed", output={}, output_sha="0" * 64)
    assert late is None and why == "LEASE_LOST"                                       # the stale owner cannot overwrite
    assert O.load(services, pid)[0]["steps"]["schedule"]["attempts"] == 2


def test_a05_failed_source_blocks_everything_downstream_and_keeps_the_old_head(pipeline_services, monkeypatch) -> None:
    services = pipeline_services
    head_before = services.repositories.head(source_head_name(services.settings.scope_key))

    def fail_midway(name: str) -> None:
        if name == "cs_windows":
            raise CaptureError("SOURCE_TIMEOUT", "simulated read timeout midway through acquisition")
    import focus.data.acquisition as acquisition

    monkeypatch.setattr(acquisition, "make_reader", lambda services, scenario=None: FixtureSourceReader(INPUTS, fault=fail_midway))
    pid = _pid(services, "a05")
    results = O.run_role(services, pid, "source")
    assert results[0]["outcome"] == "FAILED" and results[0]["error"]["code"] == "ACQUISITION_REJECTED"
    assert services.repositories.head(source_head_name(services.settings.scope_key)) == head_before
    assert O.run_role(services, pid, "app")[0]["outcome"] == "BLOCKED"                 # nothing schedules on a failed source
    for _ in range(2):
        O.run_role(services, pid, "source")                                           # the replayed key stays rejected
    assert O.run_role(services, pid, "source")[0]["outcome"] == "EXHAUSTED"
    assert O.status(services, pid)["failed"] == ["acquire"]


def test_a07_crash_after_acquisition_replays_one_source_run(pipeline_services) -> None:
    services = pipeline_services
    pid = _pid(services, "a07")

    def crash(step: str) -> None:
        raise RuntimeError(f"process killed after {step}")
    with pytest.raises(RuntimeError):
        O.run_step(services, pid, "acquire", role="source", owner="worker-a", lease_seconds=30, crash=crash)
    runs_after_crash = services.repositories.store.list_state_ids("source_runs")
    assert O.load(services, pid)[0]["steps"]["acquire"]["status"] == "running"          # the crash left only a lease behind
    services.clock.advance(seconds=31)
    replay = O.run_step(services, pid, "acquire", role="source", owner="worker-b")
    assert replay["outcome"] == "COMPLETED_AFTER_TAKEOVER"
    runs = services.repositories.store.list_state_ids("source_runs")
    assert runs == runs_after_crash and len(runs) == 1                                # one run, one receipt, no duplicate facts
    head = services.repositories.head(source_head_name(services.settings.scope_key))
    assert head.record_id == replay["output"]["source_run_id"]


def test_a51_crash_inside_publication_never_reports_signed_but_uncommitted(pipeline_services, monkeypatch) -> None:
    services = pipeline_services
    pid = _pid(services, "a51")
    for role in ("source", "app", "stager"):
        O.run_role(services, pid, role)
    import focus.publication.service as service

    real = service.publish
    calls = {"n": 0}

    def killed_after_sign(step: str) -> None:
        if step == "after_sign":
            raise RuntimeError("signer process killed after signing, before acceptance")

    def crash_after_sign(services_, candidate_id, **kw):
        calls["n"] += 1
        return real(services_, candidate_id, fault=killed_after_sign, **kw) if calls["n"] == 1 else real(services_, candidate_id, **kw)
    monkeypatch.setattr(service, "publish", crash_after_sign)
    first = O.run_step(services, pid, "sign_accept", role="signer")
    assert first["outcome"] == "FAILED" and _published(services) == []                # signed, never accepted: no success
    assert service.current_plan(services, services.settings.scope_key, "EXECUTABLE")[0] is None   # the old head stays
    second = O.run_step(services, pid, "sign_accept", role="signer")
    assert second["outcome"] == "COMPLETED"
    plans = second["output"]
    assert plans["EXECUTABLE"]["status"] == "ACCEPTED" and len(_published(services)) == 1   # the executable plan once, no duplicate


def test_a50_takeover_after_acceptance_does_not_duplicate_commitments(pipeline_services) -> None:
    services = pipeline_services
    pid = _pid(services, "a50")
    for role in ("source", "app", "stager"):
        O.run_role(services, pid, role)

    def crash(step: str) -> None:
        raise RuntimeError("killed after the acceptance, before the step was recorded")
    with pytest.raises(RuntimeError):
        O.run_step(services, pid, "sign_accept", role="signer", owner="signer-a", lease_seconds=30, crash=crash)
    store = services.repositories.store
    before = {k: (store.get_state("delivery_commitments", k).version, store.get_state("delivery_commitments", k).data)
              for k in store.list_state_ids("delivery_commitments")}
    plans = _published(services)
    services.clock.advance(seconds=31)
    replay = O.run_step(services, pid, "sign_accept", role="signer", owner="signer-b")
    assert replay["outcome"] == "COMPLETED_AFTER_TAKEOVER"
    assert {p["status"] for p in replay["output"].values()} <= {"RECOVERED", "ALREADY_ACCEPTED"}
    after = {k: (store.get_state("delivery_commitments", k).version, store.get_state("delivery_commitments", k).data)
             for k in store.list_state_ids("delivery_commitments")}
    assert after == before and _published(services) == plans                          # no extra confirmation or plan


# ---------------------------------------------------------------------- retention


def test_retention_archives_only_unreferenced_candidates_and_restores_them(pipeline_services, tmp_path) -> None:
    from focus import retention as R
    from focus.domain import Purpose
    from focus.engine.max.optimizer.orchestrator import schedule
    from focus.publication.service import current_plan

    services = pipeline_services
    pid = _pid(services, "retention")
    for role in ROLE_SEQUENCE[:-1]:
        O.run_role(services, pid, role)
    source_run = O.load(services, pid)[0]["steps"]["admit"]["output"]["source_run_id"]
    extra, _ = schedule(services, source_run=source_run, purpose=Purpose.EXECUTABLE, budget_seconds=5, max_moves=3)   # superseded what-if
    assert R.plan(services, keep_days=0)["status"] == "invalid"                       # no period is ever assumed
    young = R.plan(services, keep_days=30)
    assert young["eligible"] == []                                                    # everything is younger than 30 days
    services.clock.advance(minutes=60 * 24 * 40)
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({"records": [extra.id]}))
    held = R.plan(services, keep_days=30, legal_hold=str(hold))
    assert all(item["id"] != extra.id for item in held["eligible"])                   # a legal hold always wins
    old = R.plan(services, keep_days=30)
    ids = {(i["collection"], i["id"]) for i in old["eligible"]}
    steps = O.load(services, pid)[0]["steps"]
    used = {steps["schedule"]["output"]["EXECUTABLE"]["candidate_id"], steps["forecast"]["output"]["FORECAST"]["candidate_id"]}
    assert extra.id not in used and ("candidates", extra.id) in ids                   # the superseded candidate is eligible
    assert not any(c == "candidates" and i in used for c, i in ids)                   # accepted plans' candidates are preserved
    assert old["preserved_collections"]["published_plans"] == 2
    raw = {i: services.repositories.store.get(c, i) for c, i in ids}
    applied = R.apply(services, old)
    assert applied["status"] == "ok" and applied["moved"] == len(ids)
    assert all(services.repositories.store.get(c, i) is None for c, i in ids)
    current, why = current_plan(services, services.settings.scope_key, "EXECUTABLE")
    assert current is not None, why                                                   # the accepted plan still verifies end to end
    restored = R.restore_archive(services, applied["receipt_id"])
    assert restored["restored"] == len(ids) and all(services.repositories.store.get(c, i) == raw[i] for c, i in ids)


# ---------------------------------------------------------------------- CI draft


def _ci_jobs(text: str) -> dict[str, str]:
    """The pipeline draft's top-level blocks by name, each with its own text."""
    jobs: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        if line and not line.startswith((" ", "#")) and line.rstrip().endswith(":"):
            current = line.rstrip()[:-1]
            jobs[current] = ""
        elif current:
            jobs[current] += line + "\n"
    return jobs


def test_ci_draft_keeps_a_staged_copy_current_in_three_passes(app_root) -> None:
    """SQL Server holds a complete copy of the sources, kept current ahead of time, so the pass inside the owner's 20
    minutes pulls only what changed (docs/integration/STAGING_PLAN.md)."""
    from focus.data.datasets import DATASETS

    jobs = _ci_jobs((app_root / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    cut, before, nightly = jobs["stage_sources"], jobs["prestage_sources"], jobs["nightly_sources"]
    assert "--part cut " in cut and "extends: .live" in cut and "needs: []" in cut      # starts with the pipeline; counts toward D-28
    assert "--part preshift " in before and "--part nightly " in nightly and "--part observations " in jobs["stage_observations"]
    for name, part in (("prestage_sources", "preshift"), ("verify_prestaged_sources", "preshift"), ("nightly_sources", "nightly"),
                       ("verify_nightly_sources", "nightly")):
        job = jobs[name]
        assert f'$FOCUS_PIPELINE_PART == "{part}"' in job and "extends: .staging_only" in job, name
        assert "--role" not in job and "FOCUS_SIGN_KEY_REF" not in job and "focus-signer" not in job, name   # staging never plans or signs
    for name in ("stage_sources", "prestage_sources", "nightly_sources", "stage_observations"):
        assert '--as-of "$CI_PIPELINE_CREATED_AT"' in jobs[name] and "focus-staging" in jobs[name], name       # one instant; staging credentials only
    for name, loaded in (("verify_prestaged_sources", "prestage_sources"), ("verify_nightly_sources", "nightly_sources")):
        assert "source-discover --read-only --mode live" in jobs[name] and f"needs: [{loaded}]" in jobs[name] and "focus-source" in jobs[name]
    # a pass never publishes while acquire_admit reads the copy, and a merge never cancels a live run
    for template in (".live", ".staging_only", ".evaluate_only"):
        assert "resource_group: focus-$FOCUS_SCOPE_KEY" in jobs[template] and "interruptible: false" in jobs[template], template
    # the plan pipeline is the one that names no part, and an unknown part runs nothing
    assert "$FOCUS_PIPELINE_PART == null" in jobs[".live"] and jobs["sign_accept"].count("$FOCUS_PIPELINE_PART == null") == 2
    guard = jobs["workflow"].split("when: never")[0]
    assert all(f'$FOCUS_PIPELINE_PART != "{part}"' in guard for part in ("evaluate", "preshift", "nightly"))
    # the plan names every dataset and says which ones the pass at the cut pulls
    plan = (app_root / "docs" / "integration" / "STAGING_PLAN.md").read_text(encoding="utf-8")
    rows = {cells[1].strip().strip("`"): [c.strip() for c in cells] for cells in (line.split("|") for line in plan.splitlines() if line.startswith("| `"))}
    assert set(DATASETS) <= set(rows), sorted(set(DATASETS) - set(rows))
    at_the_cut = [name for name in DATASETS if rows[name][7] != "none"]
    assert len(at_the_cut) == 18 and "the 18 datasets" in plan and {"master_task_list", "work_progress", "etracs_active"} <= set(at_the_cut)
    assert all(rows[name][2] == ("yes" if DATASETS[name].required_for_run else "no") for name in DATASETS)
    for needle in ("--part cut", "`prestage_sources`", "`nightly_sources`", "`stage_observations`", "FOCUS_PIPELINE_PART=preshift",
                   "FOCUS_PIPELINE_PART=nightly", "DEC-37"):
        assert needle in plan, needle


def test_ci_draft_orders_roles_and_protects_signing(app_root) -> None:
    text = (app_root / ".gitlab-ci.yml").read_text(encoding="utf-8")
    jobs = _ci_jobs(text)
    # the executable plan is scheduled, staged and signed first; the forecast follows its acceptance
    order = [("acquire_admit", "--role source"), ("schedule_validate", "--role app"), ("stage_readback", "--role stager"),
             ("sign_accept", "--role signer"), ("forecast_schedule_validate", "--role app"), ("forecast_stage_readback", "--role stager"),
             ("forecast_sign_accept", "--role signer")]
    for (job, role), previous in zip(order, [None] + [j for j, _ in order]):
        assert role in jobs[job], job
        if previous:                                                              # each role needs the one before it
            assert (f"needs: [{previous}]" in jobs[job] or f"needs: [{previous}," in jobs[job]
                    or f"needs: [{{ job: {previous}, optional: true }}," in jobs[job]), job
    assert "--role evaluator" in jobs["observe_evaluate"] and "needs: [sign_accept]" in jobs["observe_evaluate"]
    # P23: every verification precedes acquisition, and the scheduler runs the native wheel built from the same commit
    needs = jobs["acquire_admit"].split("needs:")[1].splitlines()[0]
    for verify in ("import_and_lint", "unit_and_integration", "ui", "fixture_pipeline_rehearsal", "native_wheel"):
        assert verify in needs, verify
    # D-28: the 20-minute window. The long suites run on merge; a live run needs them only when they are in its pipeline
    # (FOCUS_LIVE_VERIFY=full), and otherwise refuses any ref that is not protected
    for long in ("unit_and_integration", "ui", "fixture_pipeline_rehearsal"):
        assert "extends: .merge_verify" in jobs[long] and f"{{ job: {long}, optional: true }}" in needs, long
    assert "import_and_lint," in needs and "{ job: import_and_lint" not in needs and "{ job: native_wheel" not in needs   # always in the run
    skip, always = jobs[".merge_verify"].split("when: never")[0], jobs[".merge_verify"].split("when: never")[1]
    assert '$FOCUS_MODE == "live"' in skip and '$FOCUS_LIVE_VERIFY != "full"' in skip and "when: on_success" in always
    assert 'FOCUS_LIVE_VERIFY: "merged"' in jobs["variables"]
    guard = 'test "$FOCUS_LIVE_VERIFY" = "full" -o "$CI_COMMIT_REF_PROTECTED" = "true"'
    assert jobs["acquire_admit"].index(guard) < jobs["acquire_admit"].index("--role source")
    assert "{ job: native_wheel, artifacts: true }" in jobs["schedule_validate"] and "dist/focus_native-" in jobs["schedule_validate"]
    assert "focus-native==" not in jobs["schedule_validate"]                     # never an index wheel from another commit
    assert "when: manual" in jobs["sign_accept"] and "focus-signer" in jobs["sign_accept"]
    # DEC-11 is the owner's decision and is not made: the switch for automatic acceptance exists and is off. Turned on, it
    # applies only to a scheduled run on a protected ref; every other run still waits for an operator
    assert 'FOCUS_AUTO_SIGN: "false"' in jobs["variables"]
    auto, manual = jobs["sign_accept"].split("when: on_success")[0], jobs["sign_accept"].split("when: on_success")[1]
    rule = [line for line in auto.splitlines() if line.strip()][-1]              # the condition of the automatic rule
    for condition in ('$FOCUS_AUTO_SIGN == "true"', '$CI_PIPELINE_SOURCE == "schedule"', '$CI_COMMIT_REF_PROTECTED == "true"', '$FOCUS_MODE == "live"'):
        assert condition in rule and " || " not in rule, condition                # every condition must hold: no alternative
    assert "when: manual" in manual and "FOCUS_AUTO_SIGN" not in manual
    # the delivery plan has its own switch, also off: accepted by the run only with both on, on a schedule, on a protected ref
    assert 'FOCUS_AUTO_SIGN_DELIVERY: "false"' in jobs["variables"]
    auto, manual = jobs["forecast_sign_accept"].split("when: on_success")[0], jobs["forecast_sign_accept"].split("when: on_success")[1]
    rule = [line for line in auto.splitlines() if line.strip()][-1]
    for condition in ('$FOCUS_AUTO_SIGN == "true"', '$FOCUS_AUTO_SIGN_DELIVERY == "true"', '$CI_PIPELINE_SOURCE == "schedule"',
                      '$CI_COMMIT_REF_PROTECTED == "true"', '$FOCUS_MODE == "live"'):
        assert condition in rule and " || " not in rule, condition
    assert "when: manual" in manual and "FOCUS_AUTO_SIGN" not in manual and "focus-signer" in jobs["forecast_sign_accept"]
    # D-33: every pre-shift run repairs; the nightly schedule asks for the full solve where the pipeline is created
    create = next(line for line in jobs["acquire_admit"].splitlines() if "--role source" in line)
    for flag in ('${FOCUS_PLANNING:+--planning "$FOCUS_PLANNING"}', '${FOCUS_BUDGET_SECONDS:+--budget-seconds "$FOCUS_BUDGET_SECONDS"}',
                 '${FOCUS_MAX_MOVES:+--max-moves "$FOCUS_MAX_MOVES"}'):
        assert flag in create, flag
    assert "FOCUS_PLANNING" not in jobs["variables"]                              # unset: the policy's mode (auto: repair)
    assert 'FOCUS_FAST_EXIT: "true"' in jobs[".live"]
    assert "dist/focus_native-" in jobs["forecast_schedule_validate"] and "FOCUS_SIGN_KEY_REF" not in jobs["forecast_stage_readback"]
    assert "resource_group: focus-$FOCUS_SCOPE_KEY" in jobs[".live"]
    # D-24: display retention runs before acquisition (the store holds the current package and the one being built), as
    # housekeeping that never holds the shift's plan back, with retention credentials only
    retain = jobs["display_retention"]
    assert "display-retention --apply" in retain and "allow_failure: true" in retain and "focus-retention" in retain
    assert "extends: .live" in retain and "display_retention" in jobs["acquire_admit"].split("needs:")[1].splitlines()[0]
    stages = text.split("stages: [")[1].split("]")[0].split(", ")
    assert stages.index("verify") < stages.index("retain") < stages.index("source")
    assert "display-retention --apply" in jobs["fixture_pipeline_rehearsal"]
    # D-25: per-job score evidence past the owner's 90 days goes in the same retain stage, also never holding the plan back
    details = jobs["detail_retention"]
    assert "performance-details --keep-days 90 --apply" in details and "allow_failure: true" in details and "focus-retention" in details
    assert "performance-details --apply" in jobs["fixture_pipeline_rehearsal"]
    assert "image: python:3.12-slim" in jobs["default"]
    assert "--role all" in jobs["fixture_pipeline_rehearsal"] and "FOCUS_SIGN_KEY_REF" not in jobs["stage_readback"]
    # DEC-36 (g): fewer jobs is a switch, off as shipped. One layout's unsigned jobs or the other's exist in a run, never
    # both; the signer jobs are the same in both and need whichever job staged the plan
    assert 'FOCUS_JOBS: "roles"' in jobs["variables"]
    for job in ("acquire_admit", "schedule_validate", "stage_readback", "forecast_schedule_validate", "forecast_stage_readback"):
        assert "extends: .by_role" in jobs[job], job
    for job in ("plan_unsigned", "delivery_unsigned"):
        assert "extends: .by_planner" in jobs[job] and "FOCUS_SIGN_KEY_REF" not in jobs[job] and "dist/focus_native-" in jobs[job], job
    assert '$FOCUS_JOBS != "planner"' in jobs[".by_role"] and '$FOCUS_JOBS == "planner"' in jobs[".by_planner"]
    for template in (".by_role", ".by_planner"):
        assert "extends: .live" in jobs[template] and '$FOCUS_MODE == "live"' in jobs[template] and "$FOCUS_PIPELINE_PART == null" in jobs[template]
    assert "focus-planner" in jobs[".by_planner"] and "focus-signer" not in jobs[".by_planner"]
    planned = jobs["plan_unsigned"]
    assert planned.index(guard) < planned.index("--role source,app,stager")       # the same protected-ref guard, first
    create = next(line for line in planned.splitlines() if "--role source,app,stager" in line)
    for flag in ('${FOCUS_PLANNING:+--planning "$FOCUS_PLANNING"}', '${FOCUS_BUDGET_SECONDS:+--budget-seconds "$FOCUS_BUDGET_SECONDS"}',
                 '${FOCUS_MAX_MOVES:+--max-moves "$FOCUS_MAX_MOVES"}'):
        assert flag in create, flag
    assert planned.split("needs:")[1].splitlines()[0].replace("{ job: native_wheel, artifacts: true }", "native_wheel") == needs
    assert "--role app,stager" in jobs["delivery_unsigned"] and "needs: [sign_accept," in jobs["delivery_unsigned"]
    assert "signer" not in "".join(line for job in ("plan_unsigned", "delivery_unsigned") for line in jobs[job].splitlines() if "--role" in line)
    assert "needs: [{ job: stage_readback, optional: true }, { job: plan_unsigned, optional: true }]" in jobs["sign_accept"]
    assert "needs: [{ job: forecast_stage_readback, optional: true }, { job: delivery_unsigned, optional: true }]" in jobs["forecast_sign_accept"]
