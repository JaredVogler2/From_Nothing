"""Time the pre-shift flow action by action on a synthetic fleet (D-28: data cut to accepted plan within 20 minutes).

    python tools/time_pre_shift.py run WORKDIR --aircraft 50 [--planning full] [--repair] [--views] [--no-forecast]

``run`` does, in order, each in a fresh process the way the pipeline's jobs run:

1. ``prepare``: writes the synthetic fleet's source rows as fixture input files and as a local SQLite database. This is
   the stand-in for the company's staged source tables and is outside the window.
2. ``pull``: reads every source table back from the local SQLite database into rows. A floor for the real pull: no
   network and no SQL Server, only the client's cost of receiving that many rows.
3. the pipeline roles of one pipeline key: ``source`` (acquire, admit), ``app`` (schedule, validate), ``stager``
   (stage_readback), ``signer`` (sign_accept), then the forecast's ``app``, ``stager`` and ``signer``.
4. ``views`` (with ``--views``): a web process opens the dashboard's read operations against the store.
5. with ``--repair``: the same admitted data scheduled again in ``repair`` mode, which keeps the plan just accepted
   wherever it still holds, with its independent validation. Nothing changed between the two, so this is a repair's
   best case; ``docs/execution/PLAN_REPAIR.md`` has it on a shift's changed data. Staging and signing a repaired plan
   cost what they cost for the first one.

Every role runs the product's own ``orchestration.run_role``; only the source reader is replaced, by a fixture reader
over the files of step 1. Each job ends the way the pipeline draft's live jobs do (``FOCUS_FAST_EXIT``: without freeing
its objects one by one); ``--slow-exit`` measures the ordinary exit. Named functions are wrapped with a wall-clock timer (inclusive times: a function's seconds
include the functions it calls). The fixture clock is fixed, so the pipeline's own ``window`` report reads zero here;
on live data it is the report to keep (``python -m focus.cli pipeline --key K --status``).

Not measured here, and to be measured on the company's systems: the pull from SQL Server over the network, and writes
into SQL Server (the fixture store writes files).
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import resource
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

SCENARIO = "fullscale"
EXECUTABLE_ROLES = ("source", "app", "stager", "signer")          # the window: acquire .. sign_accept
FORECAST_ROLES = ("app", "stager", "signer")                      # after acceptance, outside the window


class Timers:
    """Inclusive wall-clock seconds and call counts by label."""

    def __init__(self) -> None:
        self.rows: dict[str, list[float]] = {}

    def add(self, label: str, seconds: float) -> None:
        row = self.rows.setdefault(label, [0, 0.0])
        row[0] += 1
        row[1] += seconds

    def wrap(self, label: str, fn: Callable[..., Any], key: Callable[..., str] | None = None) -> Callable[..., Any]:
        @functools.wraps(fn)
        def timed(*args: Any, **kwargs: Any) -> Any:
            began = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                self.add(label if key is None else f"{label}:{key(*args, **kwargs)}", time.perf_counter() - began)
        return timed

    def function(self, module: Any, name: str, label: str | None = None, key: Callable[..., str] | None = None) -> None:
        """Time ``module.name`` wherever a loaded focus module holds it (``from x import name`` binds a copy)."""
        fn = getattr(module, name)
        timed = self.wrap(label or name, fn, key)
        for mod in list(sys.modules.values()):
            if mod is None or not getattr(mod, "__name__", "").startswith("focus"):
                continue
            for attr, value in list(vars(mod).items()):
                if value is fn:
                    setattr(mod, attr, timed)

    def method(self, cls: type, name: str, label: str | None = None, key: Callable[..., str] | None = None) -> None:
        setattr(cls, name, self.wrap(label or f"{cls.__name__}.{name}", cls.__dict__[name], key))

    def table(self) -> dict[str, dict[str, float]]:
        return {label: {"calls": int(calls), "seconds": round(seconds, 2)} for label, (calls, seconds) in sorted(self.rows.items())}


def tree_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def peak_mb() -> int:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


def services_for(work: Path) -> Any:
    from focus.config import Settings
    from focus.runtime import build_services

    env = {"FOCUS_MODE": "fixture", "FOCUS_NATIVE_CORE": "true", "FOCUS_RUNTIME_DIR": str(work / "store"), "FOCUS_SCENARIO_ID": SCENARIO}
    return build_services(Settings.from_env(env, app_root=APP_ROOT, inprocess_worker=False))


# ---------------------------------------------------------------------------------------------- the stand-in source

def prepare(work: Path, aircraft: int, fleet_seed: int | None) -> dict[str, Any]:
    from focus.data.datasets import DATASETS
    from focus.data.fullscale_fixture import FleetSpec, build_fullscale_fixture

    began = time.perf_counter()
    spec = FleetSpec(n_aircraft=aircraft, **({} if fleet_seed is None else {"seed": fleet_seed}))
    datasets = build_fullscale_fixture(spec)
    generated = time.perf_counter() - began
    inputs = work / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    db = work / "source.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    tables: dict[str, dict[str, Any]] = {}
    for name, ds in datasets.items():
        doc = ds.to_json()
        path = inputs / f"{name}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        columns = list(DATASETS[name].columns)
        con.execute(f'CREATE TABLE "{name}" ({", ".join(chr(34) + c + chr(34) for c in columns)})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(columns))})',
                        ([json.dumps(v) if isinstance(v, (list, dict)) else v for v in (row[c] for c in columns)] for row in ds.rows))
        tables[name] = {"rows": len(ds.rows), "columns": len(columns), "file_mb": round(path.stat().st_size / 1e6, 1)}
    con.commit()
    con.close()
    out = {"aircraft": aircraft, "fleet_seed": fleet_seed, "generate_seconds": round(generated, 1), "tables": tables,
           "rows": sum(t["rows"] for t in tables.values()), "files_mb": round(sum(t["file_mb"] for t in tables.values()), 1),
           "sqlite_mb": round(db.stat().st_size / 1e6, 1)}
    (work / "timing").mkdir(exist_ok=True)
    (work / "timing" / "prepare.json").write_text(json.dumps(out, indent=1))
    return out


def pull(work: Path) -> dict[str, Any]:
    """Every source table read from the local SQLite database into one dict per row."""
    con = sqlite3.connect(f"file:{work / 'source.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables: dict[str, dict[str, Any]] = {}
    total = time.perf_counter()
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
        began = time.perf_counter()
        rows = [dict(r) for r in con.execute(f'SELECT * FROM "{name}"')]
        tables[name] = {"rows": len(rows), "seconds": round(time.perf_counter() - began, 2)}
        del rows
    out = {"seconds": round(time.perf_counter() - total, 1), "rows": sum(t["rows"] for t in tables.values()), "tables": tables,
           "peak_memory_mb": peak_mb(), "note": "local SQLite, no network: a floor for the pull from SQL Server"}
    (work / "timing" / "pull.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------------------------------------------------------------------------------------- one role, one process

def install_timers(timers: Timers, work: Path) -> dict[str, Any]:
    """Wrap the functions each step spends its time in. Returns a dict the wrappers fill with run facts."""
    import focus.data.acquisition as acquisition
    import focus.data.repositories as repositories
    import focus.data.snapshot_builder as builder
    import focus.display.store as display
    import focus.engine.max.optimizer.orchestrator as optimizer
    import focus.orchestration as orchestration
    import focus.publication.repository as publication_repository
    import focus.publication.serializer as serializer
    import focus.publication.service as publication
    import focus.validation.plan as validation
    import focus.validation.publication as validation_publication
    from focus.data.fixture_reader import FixtureSourceReader

    facts: dict[str, Any] = {"stages": {}, "validations": []}

    # ---- the pull (here: fixture files) and the raw datasets
    reader = FixtureSourceReader(work / "inputs")
    reader.capture = timers.wrap("pull.capture", reader.capture, key=lambda spec, *a, **k: spec.name)       # type: ignore[method-assign]
    acquisition.make_reader = lambda services, scenario=None: reader
    timers.function(acquisition, "acquire", "acquire")
    timers.function(acquisition, "_readback", "acquire.readback_of_stored_rows")
    timers.function(acquisition, "_promote", "acquire.move_source_head")
    timers.function(acquisition, "load_snapshot", "load_snapshot")
    # ---- digest: the snapshot builder, stage by stage
    timers.function(builder, "build_snapshot", "digest.build_snapshot")
    for name in ("index_master", "flow_index", "build_triggers", "build_census", "admit_line_calendars", "build_cs_facts", "compute_statistics",
                 "resolve", "build_requirements", "build_workforce", "build_holdings", "matrix_label_notes", "build_virtual_support",
                 "merge_virtual_pools", "build_parts", "build_graph", "build_economics", "coverage_gaps", "freshness_gaps", "seal_snapshot",
                 "scope_config", "horizon_dates"):
        if hasattr(builder, name):
            timers.function(builder, name, f"digest.{name}")
    # ---- storing: every record written to and read from the store, by collection
    timers.method(repositories.Repositories, "put_record", "store.write", key=lambda self, collection, *a, **k: collection)
    timers.method(repositories.Repositories, "put_envelope_bytes", "store.write", key=lambda self, collection, *a, **k: collection)
    timers.method(repositories.Repositories, "get_typed", "store.read", key=lambda self, collection, *a, **k: collection)
    timers.method(repositories.Repositories, "get_raw", "store.read_raw", key=lambda self, collection, *a, **k: collection)
    # ---- the dashboard's data
    timers.function(display, "write_snapshot_display", "display.snapshot_parts")
    timers.function(display, "write_plan_display", "display.plan_parts")
    timers.function(display, "publish_plan_display", "display.plan_parts_signed")
    timers.function(display, "write_candidate_digest", "display.candidate_digest")
    # ---- scheduling
    timers.function(optimizer, "schedule", "schedule")
    timers.function(optimizer, "schedule_snapshot", "schedule.schedule_snapshot")
    timers.function(optimizer, "build_candidate", "schedule.build_candidate")
    timers.method(optimizer.EngineContext, "__init__", "schedule.engine_context")
    if hasattr(optimizer, "defended_tasks"):
        timers.function(optimizer, "defended_tasks", "schedule.defended_commitments")
    run = optimizer.MaxOptimizer.run

    def timed_run(self: Any) -> Any:
        began = time.perf_counter()
        result = run(self)
        timers.add("schedule.optimizer_run", time.perf_counter() - began)
        purpose = getattr(getattr(self, "ctx", None), "purpose", None)
        label = f"{getattr(purpose, 'value', purpose)}{'.repair' if getattr(self, 'repair', False) else ''}"
        facts["stages"].setdefault(label, []).append(
            {"stop_reason": result.stop_reason, "moves": result.moves,
             "stages": [{"stage": s.stage, "seconds": round(s.runtime_ms / 1000, 1), "candidates": s.candidates, "accepted": s.accepted}
                        for s in result.stages]})
        return result
    optimizer.MaxOptimizer.run = timed_run                                                                    # type: ignore[method-assign]
    # ---- validation: every independent check of a plan
    validate = validation.validate

    def timed_validate(*args: Any, **kwargs: Any) -> Any:
        began = time.perf_counter()
        receipt = validate(*args, **kwargs)
        seconds = time.perf_counter() - began
        kind = kwargs.get("kind", "FINAL")
        timers.add(f"validate:{kind}", seconds)
        facts["validations"].append({"kind": kind, "seconds": round(seconds, 1), "valid": receipt.valid, "violations": len(receipt.violations)})
        return receipt
    for mod in list(sys.modules.values()):
        if mod is not None and getattr(mod, "__name__", "").startswith("focus"):
            for attr, value in list(vars(mod).items()):
                if value is validate:
                    setattr(mod, attr, timed_validate)
    timers.function(validation_publication, "validate_artifact", "validate_artifact", key=lambda *a, **k: k.get("kind", "?"))
    timers.function(validation, "persist_receipt", "store.validation_receipt")
    # ---- publication: serialize, stage, read back, sign, accept
    timers.function(publication, "publish", "publish")
    timers.function(serializer, "build_artifact", "publish.build_artifact")
    timers.function(serializer, "artifact_bytes", "publish.serialize")
    timers.function(publication, "_post_acceptance", "publish.post_acceptance")
    repo_class = publication_repository.FixturePublicationRepository
    for name in ("stage", "read_stage", "accept"):
        if name in repo_class.__dict__:
            timers.method(repo_class, name, f"publish.{name}")
    # ---- the steps themselves
    step = orchestration.run_step
    orchestration.run_step = timers.wrap("step", step, key=lambda services, pid, name, **k: name)
    return facts


def role(work: Path, key: str, role_name: str, planning: str, label: str) -> dict[str, Any]:
    started = time.perf_counter()
    import focus.orchestration as orchestration

    services = services_for(work)
    timers = Timers()
    facts = install_timers(timers, work)
    store = work / "store"
    before = tree_bytes(store)
    imported = time.perf_counter() - started
    doc = orchestration.create(services, key=key, scenario=SCENARIO, planning=planning)
    began = time.perf_counter()
    results = orchestration.run_role(services, doc["pipeline_id"], role_name)
    seconds = time.perf_counter() - began
    state = orchestration.load(services, doc["pipeline_id"])[0]
    outputs = {}
    for result in results:
        output = state["steps"][result["step"]].get("output") or {}
        slim = json.loads(json.dumps(output, default=str))
        for purpose in ("EXECUTABLE", "FORECAST"):                       # keep the facts, drop the long per-aircraft lists
            if isinstance(slim.get(purpose), dict):
                slim[purpose].pop("criticality", None)
                objective = slim[purpose].get("objective")
                if isinstance(objective, dict):
                    slim[purpose]["objective"] = {k: v for k, v in objective.items() if not isinstance(v, (list, dict)) or len(json.dumps(v)) < 400}
        if "datasets" in slim:
            slim["datasets"] = len(slim["datasets"])
        outputs[result["step"]] = {"outcome": result["outcome"], "detail": result.get("detail"), "error": state["steps"][result["step"]].get("error"),
                                   "output": slim}
    out = {"label": label, "key": key, "role": role_name, "planning": planning, "startup_seconds": round(imported, 1),
           "role_seconds": round(seconds, 1), "process_seconds": round(time.perf_counter() - started, 1), "peak_memory_mb": peak_mb(),
           "store_written_mb": round((tree_bytes(store) - before) / 1e6, 1), "steps": outputs, "timers": timers.table(), **facts}
    (work / "timing" / f"{label}.json").write_text(json.dumps(out, indent=1))
    return out


def repair(work: Path, key: str, purpose: str, label: str) -> dict[str, Any]:
    """The pipeline's schedule and validate steps for ``purpose`` in repair mode, on the source run ``key`` admitted."""
    started = time.perf_counter()
    import focus.orchestration as orchestration

    services = services_for(work)
    timers = Timers()
    facts = install_timers(timers, work)
    doc = orchestration.create(services, key=key, scenario=SCENARIO)
    inputs = {"admit": doc["steps"]["admit"]["output"]}
    began = time.perf_counter()
    scheduled = orchestration._schedule_one(services, {"request": {**doc["request"], "planning": "repair"}}, inputs, purpose)
    schedule_seconds = time.perf_counter() - began
    began = time.perf_counter()
    validated = orchestration._validate_one(services, inputs, purpose, scheduled)
    out = {"label": label, "key": key, "purpose": purpose, "planning": scheduled.get("planning"), "schedule_seconds": round(schedule_seconds, 1),
           "validate_seconds": round(time.perf_counter() - began, 1), "valid": validated["valid"], "violations": validated["violations"],
           "process_seconds": round(time.perf_counter() - started, 1), "peak_memory_mb": peak_mb(), "timers": timers.table(), **facts}
    (work / "timing" / f"{label}.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def views(work: Path, persona: str) -> dict[str, Any]:
    """A web process opens every parameterless read operation twice: the first request after a start, then again."""
    started = time.perf_counter()
    from focus.web.app import create_app

    services = services_for(work)
    app = create_app(services=services)
    app.config["TESTING"] = True
    client = app.test_client()
    startup = time.perf_counter() - started
    login = client.post("/auth/demo-login", json={"persona": persona})
    rules = sorted({r.rule for r in app.url_map.iter_rules() if "GET" in r.methods and r.rule.startswith("/api/v1/") and "<" not in r.rule})
    rows = []
    for rule in rules:
        row: dict[str, Any] = {"operation": rule}
        for attempt in ("first", "again"):
            began = time.perf_counter()
            response = client.get(rule)
            row[f"{attempt}_seconds"] = round(time.perf_counter() - began, 2)
            row["status"], row["kb"] = response.status_code, round(len(response.get_data()) / 1024, 1)
        rows.append(row)
    out = {"persona": persona, "login_status": login.status_code, "startup_seconds": round(startup, 1), "peak_memory_mb": peak_mb(),
           "operations": rows}
    (work / "timing" / "views.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------------------------------------------------------------------------------------- the whole flow

FAST_EXIT = True                                                  # jobs end as the pipeline draft's live jobs do


def child(work: Path, *args: str) -> float:
    began = time.perf_counter()
    env = {**os.environ, "FOCUS_FAST_EXIT": "1" if FAST_EXIT else ""}
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), *args], cwd=APP_ROOT, capture_output=True, text=True, env=env)
    seconds = time.perf_counter() - began
    with (work / "timing" / "log.txt").open("a", encoding="utf-8") as log:
        log.write(f"--- {' '.join(args)}: exit {done.returncode}, {seconds:.1f}s\n{done.stdout[-1500:]}\n{done.stderr[-3000:]}\n")
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed ({done.returncode}): see {work / 'timing' / 'log.txt'}\n{done.stderr[-2000:]}")
    return round(seconds, 1)


def pipeline(work: Path, key: str, planning: str, prefix: str, forecast: bool) -> dict[str, Any]:
    wall: dict[str, float] = {}
    for name in EXECUTABLE_ROLES:
        wall[f"{prefix}.{name}"] = child(work, "role", str(work), key, name, "--planning", planning, "--label", f"{prefix}.{name}")
    if forecast:
        for name in FORECAST_ROLES:
            wall[f"{prefix}.forecast_{name}"] = child(work, "role", str(work), key, name, "--planning", planning, "--label", f"{prefix}.forecast_{name}")
    return wall


def run(work: Path, aircraft: int, fleet_seed: int | None, planning: str, with_repair: bool, with_views: bool, forecast: bool) -> dict[str, Any]:
    (work / "timing").mkdir(parents=True, exist_ok=True)
    wall: dict[str, float] = {"prepare": child(work, "prepare", str(work), "--aircraft", str(aircraft),
                                               *(() if fleet_seed is None else ("--fleet-seed", str(fleet_seed))))}
    wall["pull"] = child(work, "pull", str(work))
    wall.update(pipeline(work, "shift-1", planning, "first", forecast))
    if with_views:
        wall["views"] = child(work, "views", str(work))
    if with_repair:
        wall["repair.executable"] = child(work, "repair", str(work), "shift-1", "EXECUTABLE", "--label", "repair.executable")
        if forecast:
            wall["repair.forecast"] = child(work, "repair", str(work), "shift-1", "FORECAST", "--label", "repair.forecast")
    out = {"aircraft": aircraft, "fleet_seed": fleet_seed, "planning": planning, "cores": os.cpu_count(), "process_wall_seconds": wall}
    (work / "timing" / "run.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------------------------------------------------------------------------------------- the report

def _seconds(doc: dict[str, Any], *labels: str, prefix: str | None = None) -> float:
    timers = doc.get("timers", {})
    total = sum(timers.get(label, {}).get("seconds", 0.0) for label in labels)
    if prefix is not None:
        total += sum(t["seconds"] for label, t in timers.items() if label.startswith(prefix))
    return round(total, 1)


def _job(doc: dict[str, Any], wall: float | None, rows: list[tuple[str, float]]) -> dict[str, Any]:
    """One job's actions; what the named actions leave of the job's own seconds is ``everything else``."""
    total = wall if wall is not None else doc["process_seconds"]
    named = round(sum(seconds for _, seconds in rows), 1)
    return {"seconds": round(total, 1), "peak_memory_mb": doc.get("peak_memory_mb"), "written_mb": doc.get("store_written_mb"),
            "actions": [{"action": a, "seconds": s} for a, s in rows] + [{"action": "everything else in the job", "seconds": round(total - named, 1)}]}


def _publish_rows(doc: dict[str, Any]) -> list[tuple[str, float]]:
    final = sum(v["seconds"] for v in doc.get("validations", []) if v["kind"] == "FINAL")
    timers = doc.get("timers", {})
    once = timers.get("publish.serialize", {"calls": 1, "seconds": 0.0})          # the validations serialize again to compare
    return [("start the process", doc.get("startup_seconds", 0.0)),
            ("load the admitted snapshot from the store", _seconds(doc, "load_snapshot")),
            ("read the candidate plan from the store", _seconds(doc, "store.read:candidates")),
            ("independent validation of the candidate (FINAL), when the scheduling run's receipt cannot be taken", round(final, 1)),
            ("serialize the plan", round(_seconds(doc, "publish.build_artifact") + once["seconds"] / max(1, once["calls"]), 1)),
            ("decode the serialized plan and validate it (DETACHED): the stager; the signer takes its receipt", _seconds(doc, "validate_artifact:DETACHED")),
            ("write the unsigned stage", _seconds(doc, "publish.stage")),
            ("read the stage back; validate those bytes (READBACK): always in the signer, in the stager only if they differ",
             round(_seconds(doc, "publish.read_stage") + _seconds(doc, "validate_artifact:READBACK"), 1)),
            ("write the plan's dashboard data and sign it", _seconds(doc, "display.plan_parts_signed")),
            ("accept: plan record, payload, frozen goals, head", round(_seconds(doc, "publish.accept") + _seconds(doc, "publish.post_acceptance"), 1))]


STAGE_NAMES = {"neutral_seed": "first pass, part 1: neutral order, with its validation", "economic_redispatch": "first pass, part 2: ordered by cost of delay",
               "delay_repair": "delay repairs", "neighborhoods": "improvement moves", "repair": "repair: keep the accepted plan where it holds",
               "repair_redispatch": "repair: re-dispatch what moved by cost of delay", "solver": "optional solver"}


def _stages(doc: dict[str, Any], purpose: str) -> dict[str, Any]:
    """The optimizer's own stage records. Stages overlap (the seed is validated while the redispatch decodes), so their
    seconds do not add up to the run's."""
    runs = [r for label, items in doc.get("stages", {}).items() if label.startswith(purpose) for r in items]
    return {"stop_reason": [r["stop_reason"] for r in runs], "moves": sum(r["moves"] for r in runs),
            "stages": [{"stage": STAGE_NAMES.get(st["stage"], st["stage"]), "seconds": st["seconds"], "candidates": st["candidates"],
                        "accepted": st["accepted"]} for r in runs for st in r["stages"]]}


def _schedule_rows(doc: dict[str, Any], schedule_seconds: float, validate_seconds: float) -> list[tuple[str, float]]:
    """``schedule_seconds`` is the schedule step (or call) and ``validate_seconds`` the validate step after it."""
    finals = [v for v in doc.get("validations", []) if v["kind"] == "FINAL"]
    after_solve = finals[-1]["seconds"] if finals else 0.0           # the last one computed: the validate step takes its receipt
    solve, context, run = _seconds(doc, "schedule.schedule_snapshot"), _seconds(doc, "schedule.engine_context"), _seconds(doc, "schedule.optimizer_run")
    return [("start the process", doc.get("startup_seconds", 0.0)),
            ("load the admitted snapshot from the store", round(schedule_seconds - solve, 1)),
            ("build the scheduler's working data (who is qualified for what, calendars, the task network)", context),
            ("the scheduler: first pass, then repairs and improvement moves while the budget lasts", run),
            ("build the candidate plan, store it, its dashboard digest and receipts", round(solve - context - run - after_solve, 1)),
            ("independent validation after the solve (FINAL)", round(after_solve, 1)),
            ("the validate step: check the candidate's source binding and its FINAL receipt", round(validate_seconds, 1))]


def report(work: Path, receipt: Path | None = None) -> dict[str, Any]:
    """Print the run action by action; with ``receipt``, also write the report and every job's own timers to that file."""
    timing = work / "timing"
    load = lambda name: json.loads((timing / f"{name}.json").read_text()) if (timing / f"{name}.json").exists() else None   # noqa: E731
    run_doc, prepared, pulled = load("run") or {}, load("prepare"), load("pull")
    wall = run_doc.get("process_wall_seconds", {})
    out: dict[str, Any] = {"aircraft": prepared and prepared["aircraft"], "cores": os.cpu_count(), "source_rows": prepared and prepared["rows"],
                           "source_tables": prepared and {n: t["rows"] for n, t in sorted(prepared["tables"].items(), key=lambda kv: -kv[1]["rows"]) if t["rows"]},
                           "local_pull_seconds": pulled and pulled["seconds"], "jobs": {}}
    source = load("first.source")
    if source:
        digest = ["build_graph", "build_census", "index_master", "flow_index", "build_workforce", "build_requirements", "build_holdings", "resolve",
                  "build_parts", "build_cs_facts", "admit_line_calendars", "build_economics", "seal_snapshot"]
        out["jobs"]["source: acquire, admit"] = _job(source, wall.get("first.source"), [
            ("read the source rows (fixture files here; the SQL pull live)", _seconds(source, prefix="pull.capture:")),
            ("keep the raw rows with their receipts, and read them back",
             round(_seconds(source, "store.write:datasets") + _seconds(source, "acquire.readback_of_stored_rows"), 1)),
            ("digest: build the planning snapshot", _seconds(source, "digest.build_snapshot")),
            ("store the snapshot", _seconds(source, "store.write:snapshots")),
            ("write the snapshot's dashboard data", _seconds(source, "display.snapshot_parts")),
            ("the admit step: verify the stored snapshot", _seconds(source, "step:admit"))])
        out["digest_stages_seconds"] = {name: _seconds(source, f"digest.{name}") for name in digest if _seconds(source, f"digest.{name}") >= 0.05}
    for label, title, purpose in (("first.app", "app: schedule, validate (executable plan)", "EXECUTABLE"),
                                  ("first.forecast_app", "app: forecast (schedule and validate)", "FORECAST")):
        doc = load(label)
        if doc:
            step = "schedule" if purpose == "EXECUTABLE" else "forecast"
            total = _seconds(doc, f"step:{step}")
            # the forecast step schedules and validates in one step, and its validate part takes the solve's FINAL receipt
            check = _seconds(doc, "step:validate") if purpose == "EXECUTABLE" else 0.0
            out["jobs"][title] = _job(doc, wall.get(label), _schedule_rows(doc, total, check))
            out["jobs"][title]["scheduler"] = _stages(doc, purpose)
    for label, title in (("first.stager", "stager: stage_readback (executable plan)"), ("first.signer", "signer: sign_accept (executable plan)"),
                         ("first.forecast_stager", "stager: forecast_stage_readback"), ("first.forecast_signer", "signer: forecast_sign_accept")):
        doc = load(label)
        if doc:
            out["jobs"][title] = _job(doc, wall.get(label), [r for r in _publish_rows(doc) if r[1] or "sign" not in r[0]])
    window = [j["seconds"] for title, j in out["jobs"].items() if "forecast" not in title]
    out["window_seconds"] = round(sum(window), 1) if len(window) == 4 else None
    out["forecast_seconds"] = round(sum(j["seconds"] for title, j in out["jobs"].items() if "forecast" in title), 1)
    for label, purpose in (("repair.executable", "EXECUTABLE"), ("repair.forecast", "FORECAST")):
        doc = load(label)
        if doc:
            rows = _schedule_rows(doc, doc["schedule_seconds"], doc["validate_seconds"])
            out.setdefault("repair", {})[purpose] = {"planning": doc["planning"], "seconds": doc["process_seconds"], "peak_memory_mb": doc["peak_memory_mb"],
                                                     "actions": [{"action": a, "seconds": s} for a, s in rows], "scheduler": _stages(doc, purpose)}
    seen = load("views")
    if seen:
        out["dashboard"] = {"web_start_seconds": seen["startup_seconds"], "peak_memory_mb": seen["peak_memory_mb"],
                            "operations": [o for o in seen["operations"] if o["status"] == 200]}
    (timing / "report.json").write_text(json.dumps(out, indent=1))
    if receipt is not None:
        keep = ("role", "planning", "purpose", "startup_seconds", "role_seconds", "schedule_seconds", "validate_seconds", "process_seconds",
                "peak_memory_mb", "store_written_mb", "timers", "stages", "validations")
        raw = {f.stem: {k: v for k, v in json.loads(f.read_text()).items() if k in keep}
               for f in sorted(timing.glob("*.json")) if f.stem.split(".")[0] in ("first", "repair")}
        receipt.write_text(json.dumps({"tool": "tools/time_pre_shift.py", "label": "SYNTHETIC_FULLSCALE_FIXTURE (real readiness not established)",
                                       "process_wall_seconds": wall, **({"notes": run_doc["notes"]} if run_doc.get("notes") else {}),
                                       "report": out, "jobs": raw}, indent=1) + "\n")
    for title, job in out["jobs"].items():
        print(f"\n{title}: {job['seconds']:.0f} s ({job['seconds'] / 60:.1f} min), peak {job['peak_memory_mb']} MB, wrote {job['written_mb']} MB")
        for a in job["actions"]:
            print(f"  {a['seconds']:8.1f}  {a['action']}")
        for st in job.get("scheduler", {}).get("stages", []):
            print(f"            ({st['seconds']:.1f} s {st['stage']}; {st['candidates']} tried, {st['accepted']} kept)")
        if job.get("scheduler"):
            print(f"            (stopped by: {job['scheduler']['stop_reason']})")
    for purpose, item in out.get("repair", {}).items():
        print(f"\nrepair {purpose}: {item['seconds']:.0f} s; {item['planning']}")
        for a in item["actions"]:
            print(f"  {a['seconds']:8.1f}  {a['action']}")
    print(f"\nwindow (the four executable-plan jobs): {out['window_seconds']} s; forecast jobs: {out['forecast_seconds']} s; "
          f"local pull of {out['source_rows']} rows: {out['local_pull_seconds']} s")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("work")
    p.add_argument("--aircraft", type=int, default=50)
    p.add_argument("--fleet-seed", type=int, default=None)
    p.add_argument("--planning", default="full", choices=("full", "repair", "auto"))
    p.add_argument("--repair", action="store_true", help="then schedule the same admitted data again in repair mode")
    p.add_argument("--views", action="store_true", help="time the dashboard's read operations after the first pipeline")
    p.add_argument("--no-forecast", action="store_true", help="stop after the executable plan is accepted")
    p.add_argument("--slow-exit", action="store_true", help="let every job tear its interpreter down (FOCUS_FAST_EXIT off)")
    p = sub.add_parser("prepare")
    p.add_argument("work")
    p.add_argument("--aircraft", type=int, default=50)
    p.add_argument("--fleet-seed", type=int, default=None)
    p = sub.add_parser("pull")
    p.add_argument("work")
    p = sub.add_parser("role")
    p.add_argument("work")
    p.add_argument("key")
    p.add_argument("role", choices=("source", "app", "stager", "signer"))
    p.add_argument("--planning", default="full", choices=("full", "repair", "auto"))
    p.add_argument("--label", default=None)
    p = sub.add_parser("repair")
    p.add_argument("work")
    p.add_argument("key")
    p.add_argument("purpose", choices=("EXECUTABLE", "FORECAST"))
    p.add_argument("--label", default=None)
    p = sub.add_parser("views")
    p.add_argument("work")
    p.add_argument("--persona", default="manager")
    p = sub.add_parser("report")
    p.add_argument("work")
    p.add_argument("--out", default=None, help="also write the report and the jobs' timers to this file")
    args = parser.parse_args(argv)
    work = Path(args.work).resolve()
    if args.command == "run":
        global FAST_EXIT
        FAST_EXIT = not args.slow_exit
        out = run(work, args.aircraft, args.fleet_seed, args.planning, args.repair, args.views, not args.no_forecast)
        out["fast_exit"] = FAST_EXIT
    elif args.command == "prepare":
        out = prepare(work, args.aircraft, args.fleet_seed)
    elif args.command == "pull":
        out = pull(work)
    elif args.command == "role":
        out = role(work, args.key, args.role, args.planning, args.label or f"{args.key}.{args.role}")
    elif args.command == "repair":
        out = repair(work, args.key, args.purpose, args.label or f"repair.{args.purpose.lower()}")
    elif args.command == "report":
        report(work, Path(args.out).resolve() if args.out else None)
        return 0
    else:
        out = views(work, args.persona)
    print(json.dumps({k: v for k, v in out.items() if k not in ("timers", "tables", "operations", "steps", "stages", "validations")}))
    return 0


if __name__ == "__main__":
    from focus.cli import leave

    leave(main())
