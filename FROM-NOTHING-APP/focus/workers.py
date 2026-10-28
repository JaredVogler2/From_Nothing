"""Background worker runs (S17 WORKERS).

Commands enqueue durable :class:`WorkerRun` records. A worker leases the
oldest queued run with optimistic versions, executes its handler within the
run's budget and stores a durable result receipt (or a typed failure). Web
GETs never execute work. The in-process worker (fixture default, or
``FOCUS_INPROCESS_WORKER=true``) starts lazily on the first command, never
at import. Hosted deployments can instead run ``python -m focus.cli worker``
as a separate process/task (Tanzu task, Posit Connect scheduled job).
"""
from __future__ import annotations

import importlib
import os
import threading
import time
import traceback
import uuid
from typing import Any, Callable

from .contracts.runs import WorkerRun
from .data.store import StoreError, VersionConflict

# kind -> "module:function"; handler(services, run) -> receipt dict
HANDLERS: dict[str, str] = {
    "schedule": "focus.engine.max.optimizer.orchestrator:worker_schedule",
    "scenario": "focus.analytics.scenarios:worker_scenario",
    "amendment": "focus.performance.amendments:worker_amendment",
    "ie_review": "focus.performance.history:worker_ie_review",
    "disruption_claim": "focus.performance.disruption:worker_claim",
    "excusal_decision": "focus.performance.disruption:worker_decision",
    "explanation": "focus.explanations.pipeline:worker_explanation",
    "reported_observation": "focus.performance.actuals:worker_reported_observation",
}


class HandlerUnavailable(RuntimeError):
    code = "HANDLER_UNAVAILABLE"


class RunCancelled(RuntimeError):
    """Raised by a handler that saw a cancel request; the run ends ``cancelled`` and stores no result."""

    code = "CANCELLED"


CANCEL_REQUESTS = "run_cancel_requests"


def request_cancel(services: Any, run: WorkerRun, *, by: str, reason: str, at: Any) -> bool:
    """Record a durable cancel request for a running run (idempotent). Returns False when one already existed.

    The handler polls :func:`cancel_requested` and stops cooperatively; nothing it already wrote is changed, and a
    handler that never polls simply finishes within its budget.
    """
    repos = services.repositories
    if repos.get_raw(CANCEL_REQUESTS, run.id) is not None:
        return False
    repos.put_record(CANCEL_REQUESTS, "CancelRequest", run.id, {"run_id": run.id, "requested_by": by, "reason": reason[:200], "at": at},
                     created_at=at)
    return True


def cancel_requested(services: Any, run_id: str) -> bool:
    return services.repositories.get_raw(CANCEL_REQUESTS, run_id) is not None


def _resolve(kind: str) -> Callable[[Any, WorkerRun], dict[str, Any]]:
    target = HANDLERS.get(kind)
    if target is None:
        raise HandlerUnavailable(f"no handler registered for {kind}")
    module_name, func_name = target.split(":")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, func_name)
    except (ImportError, AttributeError) as exc:
        raise HandlerUnavailable(f"handler for {kind} is not available: {exc}") from exc


class WorkerService:
    def __init__(self, services: Any, owner: str | None = None, lease_seconds: float = 900.0):
        self.services = services
        self.owner = owner or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.lease_seconds = lease_seconds

    def run_once(self) -> WorkerRun | None:
        run = self.services.repositories.claim_worker_run(self.owner, self.lease_seconds)
        return None if run is None else self.execute(run)

    def execute(self, run: WorkerRun) -> WorkerRun | None:
        """Run the handler of a run this worker has claimed and store its terminal state."""
        repos = self.services.repositories
        version = repos.worker_run_version(run.id)
        try:
            handler = _resolve(run.kind)
            started = time.monotonic()
            receipt = handler(self.services, run)
            receipt = dict(receipt)
            receipt.setdefault("runtime_seconds", round(time.monotonic() - started, 3))
            return repos.update_worker_run(run, version, status="completed", result_receipt=receipt, error=None,
                                           lease_owner=None, lease_until=None)
        except RunCancelled as exc:
            try:
                return repos.update_worker_run(run, version, status="cancelled", error={"code": "CANCELLED", "message": str(exc)[:2000]},
                                               lease_owner=None, lease_until=None)
            except (VersionConflict, StoreError):
                return None
        except Exception as exc:  # noqa: BLE001 - every failure is recorded durably
            error = {
                "code": getattr(exc, "code", type(exc).__name__),
                "message": getattr(exc, "message", str(exc))[:2000],
                "trace_tail": traceback.format_exc().splitlines()[-3:],
            }
            try:
                return repos.update_worker_run(run, version, status="failed", error=error, lease_owner=None, lease_until=None)
            except (VersionConflict, StoreError):
                return None

    def run_until_idle(self, max_runs: int = 100) -> list[WorkerRun]:
        done = []
        for _ in range(max_runs):
            run = self.run_once()
            if run is None:
                break
            done.append(run)
        return done


class InprocessWorker(threading.Thread):
    def __init__(self, services: Any):
        super().__init__(name="focus-inprocess-worker", daemon=True)
        self.service = WorkerService(services)
        self._wake = threading.Event()

    def notify(self) -> None:
        self._wake.set()

    def run(self) -> None:  # pragma: no cover - exercised through integration tests
        while True:
            self._wake.wait(timeout=30.0)
            self._wake.clear()
            try:
                self.service.run_until_idle()
            except Exception:  # noqa: BLE001 - keep the daemon alive; failures are recorded per run
                time.sleep(1.0)


_START_LOCK = threading.Lock()


def maybe_start_inprocess_worker(services: Any) -> InprocessWorker | None:
    if not services.settings.inprocess_worker or not services.repositories.available:
        return None
    with _START_LOCK:
        worker = services.extras.get("inprocess_worker")
        if worker is None:
            worker = InprocessWorker(services)
            worker.start()
            services.extras["inprocess_worker"] = worker
        return worker


def cli_worker(services: Any, args: Any) -> dict[str, Any]:
    service = WorkerService(services)
    processed: list[WorkerRun] = []
    if getattr(args, "loop", False):
        interval = max(0.5, float(getattr(args, "interval_seconds", 5.0)))
        try:
            while True:  # pragma: no cover - long-running process type
                processed.extend(service.run_until_idle(args.max_runs))
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
    else:
        processed = service.run_until_idle(1 if args.once else args.max_runs)
    return {
        "status": "ok",
        "processed": [{"run_id": r.id, "kind": r.kind, "status": r.status, "error": r.error} for r in processed],
    }
