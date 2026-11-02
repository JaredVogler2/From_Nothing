"""Display retention: delete the display parts nothing reads any more (D-24).

Display parts are a cache. Each one is rebuilt from records kept forever (the admitted snapshot, or the accepted plan's
payload) and must hash to its manifest, which for a plan is signed. Deleting one loses no history, so this is the one
place records are deleted. The store refuses to delete outside ``display_parts`` (``data.store.DERIVED_COLLECTIONS``),
and manifests, display receipts and candidate digests are kept forever.

Every display (one manifest) gets one verdict:

* **Kept whole** (every part):
  - the display of a current head: the source head's snapshot and each accepted plan head;
  - a display that can still become current. Heads move only forward, so these are exactly:
    - a snapshot whose run is not rejected and whose cutoff is not behind the source head's (a replay promotes it,
      ``acquisition._promote``);
    - an accepted plan whose acceptance names a head version the head has not reached (``publication.service._reconcile``
      recovers it);
    - a plan not yet accepted whose cutoff is not behind the head plan's (``publish`` can still accept it: no
      STALE_REPLAY);
  - every display of a scope and kind whose head moved less than the grace period ago, since a request may still be
    reading the display that head replaced.
* **History:** every other accepted plan keeps its ``summary`` and ``goals:<date>`` parts forever. The accepted plan
  list, the completion history and the goals of record read exactly these (``view.accepted_display``,
  ``GoalRegistry.from_display``).
* **Released:** everything else.

Every stored part that no kept display and no history names is then deleted:

- older snapshots' displays;
- the rest of older plans' displays (lines, job buckets, team-days, holds, commitments);
- the displays of plans that can no longer be accepted;
- parts no manifest names (a write that never finished).

A released display can never become current again. So it goes into the ledger (``display_retention/ledger``) with the
history parts it keeps, and is never read again. A run reads only the displays not yet released, a handful per shift,
never the whole history. A released display's parts can still be rebuilt with ``python -m focus.cli display`` for a look;
the next run removes them again.

**Safety.**

- Package writers hold the display parts lock while they write (``store.writing``), and retention holds it while it
  decides and deletes. So it never deletes a part that a writer found stored and is about to name.
- Writers put back any missing part of their manifest. The replay and recovery paths check every part, not just the
  manifest (``store.missing_parts``).
- The ledger is written before anything is deleted. A crash in between only leaves parts for the next run.
- The current heads of every scope seen, and of the configured scope, are kept whole whatever the ledger says.

``display-retention`` reports by default; ``--apply`` deletes and writes a receipt (``display_retention_runs``). The
pipeline runs it before acquisition, so the store holds at most the current package plus the one being built.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any

from ..codec import CodecError, canonical_dumps, from_jsonable, sha256_hex
from ..data.store import LockTimeout
from ..domain import digest_id
from .records import DisplayManifest
from .store import LOCK, MANIFESTS, PARTS

LEDGER = "display_retention"          # versioned state: {"schema", "released": {manifest id: [kept part hashes]}}
LEDGER_ID = "ledger"
RUNS = "display_retention_runs"       # one immutable receipt per applied run
LEDGER_SCHEMA = 1
RETAIN_TTL = 900.0                    # retention holds the lock for seconds (decide, then delete); a crash frees it after this
RETAIN_WAIT = 120.0
DEFAULT_GRACE_MINUTES = 60
KEEP, HISTORY, RELEASE = "KEEP", "HISTORY", "RELEASE"
PURPOSES = ("EXECUTABLE", "FORECAST")


def history_part(name: str) -> bool:
    """The parts an accepted plan keeps forever: exactly what the history views and the goals of record read."""
    return name == "summary" or name.startswith("goals:")


class Heads:
    """The heads, source runs and acceptances a run consults, each read once."""

    def __init__(self, services: Any, now: datetime, grace: timedelta):
        from ..publication.repository import repository_for

        self.repos = services.repositories
        self.repo = repository_for(services)
        self.now = now
        self.grace = grace
        self._runs: dict[str | None, Any] = {}
        self._source: dict[str, tuple[Any, Any]] = {}
        self._plans: dict[tuple[str, str], tuple[Any, datetime | None]] = {}

    def run(self, run_id: str | None) -> Any:
        from ..data.acquisition import get_run

        if run_id not in self._runs:
            found = get_run(self.repos, run_id) if run_id else None
            self._runs[run_id] = None if found is None else found[0]
        return self._runs[run_id]

    def source(self, scope: str) -> tuple[Any, Any]:
        """The source head of a scope and the run it names."""
        from ..data.acquisition import source_head_name

        if scope not in self._source:
            head = self.repos.head(source_head_name(scope))
            self._source[scope] = (head, None if head is None else self.run(head.record_id))
        return self._source[scope]

    def plan(self, scope: str, purpose: str) -> tuple[Any, datetime | None]:
        """The plan head of a scope and purpose, and the cutoff of the source run its plan rests on."""
        key = (scope, purpose)
        if key not in self._plans:
            head = self.repo.head(scope, purpose)
            cutoff = None
            if head is not None:
                published = self.repo.published(head.record_id)
                run = None if published is None else self.run(published.source_run_id)
                cutoff = None if run is None else run.cutoff
            self._plans[key] = (head, cutoff)
        return self._plans[key]

    def recent(self, head: Any) -> bool:
        return head is not None and self.now - head.updated_at < self.grace

    def current_ids(self, scope: str) -> list[str]:
        """The displays the heads of a scope name now."""
        _, head_run = self.source(scope)
        ids = [head_run.snapshot_id] if head_run is not None and head_run.snapshot_id else []
        for purpose in PURPOSES:
            head, _ = self.plan(scope, purpose)
            if head is not None:
                ids.append(head.record_id)
        return ids


def classify(manifest: DisplayManifest, heads: Heads) -> tuple[str, str]:
    """One display's verdict and its reason (see the module docstring)."""
    run = heads.run(manifest.source_run_id)
    if run is None:
        return KEEP, "its source run is not stored"
    if manifest.subject == "SNAPSHOT":
        head, head_run = heads.source(run.scope)
        if head_run is not None and head_run.snapshot_id == manifest.subject_id:
            return KEEP, "current source head"
        if run.status != "REJECTED" and (head_run is None or run.cutoff >= head_run.cutoff):
            return KEEP, "can still become the source head"
        if heads.recent(head):
            return KEEP, "the source head moved within the grace period"
        return RELEASE, "superseded snapshot"
    if manifest.subject != "PLAN" or manifest.purpose not in PURPOSES:
        return KEEP, f"unknown subject {manifest.subject}/{manifest.purpose}"
    plan_id = manifest.subject_id
    head, head_cutoff = heads.plan(run.scope, manifest.purpose)
    if head is not None and head.record_id == plan_id:
        return KEEP, "current plan head"
    receipt = heads.repo.receipt(plan_id)
    if receipt is not None:
        if head is None or receipt.head_version > head.version:
            return KEEP, "accepted; recovery can still move the head to it"
    elif head is None or head_cutoff is None or head_cutoff <= run.cutoff:
        return KEEP, "not accepted yet; it can still be accepted"
    if heads.recent(head):
        return KEEP, "the plan head moved within the grace period"
    if heads.repo.published(plan_id) is not None:
        return HISTORY, "superseded accepted plan; its summary and goals stay"
    return RELEASE, "can no longer be accepted"


def run_retention(services: Any, *, grace_minutes: int = DEFAULT_GRACE_MINUTES, apply: bool = False) -> dict[str, Any]:
    """Report (default) or delete the display parts nothing reads any more."""
    repos = services.repositories
    if not repos.available:
        return {"status": "unavailable", "error": {"code": "REPOSITORY_UNAVAILABLE", "message": "no repository is configured; nothing to retain"}}
    if grace_minutes is None or grace_minutes < 1:
        return {"status": "invalid", "error": {"code": "GRACE_TOO_SHORT", "message": "the grace period must be at least one minute"}}
    guard = repos.store.lock(LOCK, ttl_seconds=RETAIN_TTL, wait_seconds=RETAIN_WAIT) if apply else nullcontext()
    try:
        with guard:
            return _retain(services, timedelta(minutes=grace_minutes), apply)
    except LockTimeout:
        return {"status": "busy", "error": {"code": "DISPLAY_RETENTION_BUSY",
                                            "message": "a display package is being written; nothing was deleted (the next run retries)"}}


def _retain(services: Any, grace: timedelta, apply: bool) -> dict[str, Any]:
    repos = services.repositories
    store = repos.store
    now = services.clock.now()
    ledger, version = _load_ledger(repos)
    released: dict[str, list[str]] = ledger["released"]
    protected = {sha for kept in released.values() for sha in kept}
    heads = Heads(services, now, grace)
    scopes = {services.settings.scope_key}
    kept_whole: list[dict[str, Any]] = []
    released_now: list[dict[str, Any]] = []
    newly: dict[str, list[str]] = {}
    for mid in store.stored_ids(MANIFESTS):
        if mid in released:
            continue
        manifest = _manifest(repos, mid)
        if manifest is None:
            return {"status": "error", "error": {"code": "DISPLAY_MANIFEST_UNREADABLE",
                                                 "message": f"display manifest {mid} is unreadable; nothing was deleted"}}
        run = heads.run(manifest.source_run_id)
        if run is not None:
            scopes.add(run.scope)
        verdict, why = classify(manifest, heads)
        entry = {"id": mid, "subject": manifest.subject, "purpose": manifest.purpose, "why": why}
        if verdict == KEEP:
            protected.update(ref.sha256 for ref in manifest.parts)
            kept_whole.append(entry)
            continue
        kept = sorted({ref.sha256 for ref in manifest.parts if history_part(ref.name)}) if verdict == HISTORY else []
        protected.update(kept)
        newly[mid] = kept
        released_now.append({**entry, "kept_parts": len(kept)})
    for mid, manifest in _current(repos, heads, sorted(scopes)):          # whatever the ledger says
        protected.update(ref.sha256 for ref in manifest.parts)
    stored = store.stored_ids(PARTS)
    doomed = [sha for sha in stored if sha not in protected]
    report: dict[str, Any] = {
        "status": "ok", "applied": apply, "at": now.isoformat(), "grace_minutes": int(grace.total_seconds() // 60),
        "parts_stored": len(stored), "parts_kept": len(stored) - len(doomed), "parts_to_delete": len(doomed),
        "displays_kept_whole": kept_whole, "displays_released": released_now, "displays_released_before": len(released),
        "note": "derived display parts only; manifests, receipts, snapshots, payloads and history are never deleted"}
    if not apply:
        return report
    new_version = repos.put_state(LEDGER, LEDGER_ID, {**ledger, "released": {**released, **newly}}, version)
    freed = deleted = 0
    for sha in doomed:
        size = store.delete_immutable(PARTS, sha)
        if size is not None:
            freed += size
            deleted += 1
    fingerprint = sha256_hex(canonical_dumps(sorted(doomed)))
    receipt = {"schema_version": 1, "at": now.isoformat(), "grace_minutes": report["grace_minutes"], "ledger_version": new_version,
               "displays_kept_whole": kept_whole, "displays_released": released_now, "parts_stored": len(stored),
               "deleted_parts": deleted, "deleted_bytes": freed, "deleted_sha256": fingerprint}
    receipt_id = digest_id("DRET", now.isoformat(), new_version, fingerprint)
    repos.put_record(RUNS, "DisplayRetentionReceipt", receipt_id, receipt, created_at=now)
    return {**report, "deleted_parts": deleted, "deleted_bytes": freed, "receipt_id": receipt_id}


def _load_ledger(repos: Any) -> tuple[dict[str, Any], int]:
    found = repos.get_state(LEDGER, LEDGER_ID)
    if found is None:
        return {"schema": LEDGER_SCHEMA, "released": {}}, 0
    ledger, version = found
    if not isinstance(ledger, dict) or ledger.get("schema") != LEDGER_SCHEMA or not isinstance(ledger.get("released"), dict):
        raise CodecError("LEDGER_UNREADABLE", "the display retention ledger has an unknown shape")
    return ledger, version


def _manifest(repos: Any, mid: str) -> DisplayManifest | None:
    try:
        env = repos.get_envelope(MANIFESTS, mid, "DisplayManifest")
        return None if env is None else from_jsonable(DisplayManifest, env.payload)
    except (CodecError, ValueError, TypeError):
        return None


def _current(repos: Any, heads: Heads, scopes: list[str]) -> Iterator[tuple[str, DisplayManifest]]:
    for scope in scopes:
        for mid in heads.current_ids(scope):
            manifest = _manifest(repos, mid)
            if manifest is not None:
                yield mid, manifest


def cli_display_retention(services: Any, args: Any) -> dict[str, Any]:
    return run_retention(services, grace_minutes=args.grace_minutes, apply=bool(args.apply))
