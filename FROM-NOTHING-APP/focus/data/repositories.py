"""Repository facade over a :class:`Store` (S02, S06).

Every durable record is a canonical envelope (``focus.codec.seal``) stored
immutably by id; mutable state (worker runs, delivery commitments) uses
optimistic versions; heads move with compare-and-set and are written last.
The same facade runs on the persistent fixture store and the live SQL store.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Iterable, TypeVar

from ..clock import Clock
from ..codec import Envelope, canonical_dumps, content_hash, from_jsonable, open_envelope, seal
from ..contracts.runs import WorkerRun
from .store import Head, Store, StoreError, VersionConflict

T = TypeVar("T")


class RepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Repositories:
    """Typed persistence facade. Domain-specific helpers live in their stage modules."""

    def __init__(self, store: Store, *, mode: str, scope_key: str, clock: Clock):
        self.store = store
        self.mode = mode
        self.scope_key = scope_key
        self.clock = clock

    @property
    def available(self) -> bool:
        return self.store.available()

    @property
    def kind(self) -> str:
        return self.store.kind

    # ------------------------------------------------------------------
    # envelopes
    # ------------------------------------------------------------------
    def put_record(self, collection: str, type_name: str, record_id: str, payload: Any, *,
                   source_run_id: str | None = None, policy_hash: str | None = None,
                   purpose: str | None = None, created_at: datetime | None = None) -> Envelope:
        env = seal(
            type_name,
            record_id,
            payload,
            created_at=created_at or self.clock.now(),
            source_run_id=source_run_id,
            policy_hash=policy_hash,
            purpose=purpose,
        )
        self.store.put_immutable(collection, record_id, env.to_bytes())
        return env

    def put_envelope_bytes(self, collection: str, record_id: str, data: bytes) -> bool:
        env = open_envelope(data)
        if env.id != record_id:
            raise RepositoryError("ENVELOPE_ID_MISMATCH", f"envelope id {env.id!r} differs from {record_id!r}")
        return self.store.put_immutable(collection, record_id, data)

    def get_raw(self, collection: str, record_id: str) -> bytes | None:
        return self.store.get(collection, record_id)

    def get_envelope(self, collection: str, record_id: str, type_name: str | None = None) -> Envelope | None:
        raw = self.store.get(collection, record_id)
        if raw is None:
            return None
        return open_envelope(raw, type_name)

    def get_typed(self, collection: str, record_id: str, type_name: str, cls: type[T]) -> T | None:
        env = self.get_envelope(collection, record_id, type_name)
        if env is None:
            return None
        return from_jsonable(cls, env.payload, f"$<{type_name}:{record_id}>")

    def list_ids(self, collection: str) -> list[str]:
        return self.store.list_ids(collection)

    # ------------------------------------------------------------------
    # heads
    # ------------------------------------------------------------------
    def head(self, name: str) -> Head | None:
        return self.store.get_head(name)

    def move_head(self, name: str, record_id: str, expected_version: int) -> Head:
        return self.store.cas_head(name, record_id, expected_version, self.clock.now())

    # ------------------------------------------------------------------
    # mutable versioned state (JSON documents)
    # ------------------------------------------------------------------
    def get_state(self, collection: str, record_id: str) -> tuple[Any, int] | None:
        rec = self.store.get_state(collection, record_id)
        if rec is None:
            return None
        from ..codec import strict_loads

        return strict_loads(rec.data), rec.version

    def put_state(self, collection: str, record_id: str, value: Any, expected_version: int) -> int:
        rec = self.store.put_state(collection, record_id, canonical_dumps(value), expected_version)
        return rec.version

    # ------------------------------------------------------------------
    # worker runs (S06 FOCUS_FN_WorkerRun semantics)
    # ------------------------------------------------------------------
    def create_worker_run(self, *, kind: str, scope: str, idempotency_key: str, requested_by: str,
                          request: dict[str, Any], budget_seconds: float) -> tuple[WorkerRun, bool]:
        """Create a queued run once per (kind, scope, idempotency key).

        Replaying the same key with an identical request returns the existing
        run; replaying it with a *different* request is a conflict.
        """
        request_sha = content_hash(request)
        idem_key = f"{kind}|{scope}|{idempotency_key}"
        existing_id = self.store.idempotency_get("worker_idempotency", idem_key)
        if existing_id:
            run = self.get_worker_run(existing_id)
            if run is None:
                raise RepositoryError("IDEMPOTENCY_ORPHAN", "idempotency key bound to a missing run")
            if run.request_sha256 != request_sha:
                raise RepositoryError("IDEMPOTENCY_CONFLICT", "idempotency key reused with a different request")
            return run, False
        now = self.clock.now()
        run = WorkerRun(
            id="RUN-" + uuid.uuid4().hex[:20],
            kind=kind,
            scope=scope,
            idempotency_key=idempotency_key,
            status="queued",
            requested_by=requested_by,
            request=request,
            request_sha256=request_sha,
            budget_seconds=budget_seconds,
            lease_owner=None,
            lease_until=None,
            attempts=0,
            result_receipt=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        bound = self.store.idempotency_put("worker_idempotency", idem_key, run.id)
        if bound != run.id:
            other = self.get_worker_run(bound)
            if other is None or other.request_sha256 != request_sha:
                raise RepositoryError("IDEMPOTENCY_CONFLICT", "idempotency key reused with a different request")
            return other, False
        self.store.put_state("worker_runs", run.id, canonical_dumps(run), 0)
        return run, True

    def get_worker_run(self, run_id: str) -> WorkerRun | None:
        rec = self.store.get_state("worker_runs", run_id)
        if rec is None:
            return None
        from ..codec import strict_loads

        return from_jsonable(WorkerRun, strict_loads(rec.data))

    def worker_run_version(self, run_id: str) -> int:
        rec = self.store.get_state("worker_runs", run_id)
        return rec.version if rec else 0

    def update_worker_run(self, run: WorkerRun, expected_version: int, **changes: Any) -> WorkerRun:
        updated = replace(run, updated_at=self.clock.now(), **changes)
        self.store.put_state("worker_runs", run.id, canonical_dumps(updated), expected_version)
        return updated

    def list_worker_runs(self, *, scope: str | None = None, statuses: Iterable[str] | None = None) -> list[WorkerRun]:
        wanted = set(statuses) if statuses else None
        runs = []
        for run_id in self.store.list_state_ids("worker_runs"):
            run = self.get_worker_run(run_id)
            if run is None:
                continue
            if scope is not None and run.scope != scope:
                continue
            if wanted is not None and run.status not in wanted:
                continue
            runs.append(run)
        runs.sort(key=lambda r: (r.created_at, r.id))
        return runs

    def claim_worker_run(self, owner: str, lease_seconds: float = 300.0, kinds: Iterable[str] | None = None) -> WorkerRun | None:
        """Lease the oldest queued (or lease-expired running) run using optimistic versions."""
        allowed = set(kinds) if kinds else None
        now = self.clock.now()
        for run in self.list_worker_runs(statuses=("queued", "running")):
            if allowed is not None and run.kind not in allowed:
                continue
            if run.status == "running" and run.lease_until and run.lease_until > now:
                continue
            version = self.worker_run_version(run.id)
            try:
                return self.update_worker_run(
                    run,
                    version,
                    status="running",
                    lease_owner=owner,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    attempts=run.attempts + 1,
                )
            except (VersionConflict, StoreError):
                continue
        return None
