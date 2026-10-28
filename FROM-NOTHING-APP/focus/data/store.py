"""Storage backends behind the repository interfaces (S02, S06, D-11).

``Store`` is the minimal persistence protocol every repository uses:

* immutable write-once records (canonical envelope bytes) per collection;
* versioned mutable state with optimistic compare-and-set;
* versioned heads updated with compare-and-set (always written last);
* idempotency index (kind, key) -> record id;
* scoped locks.

``FixtureStore`` persists under ``fixtures/runtime/<scenario>`` and survives
independent CLI processes and server restarts. Every write is atomic (temp file,
fsync, link/replace) so a crash leaves either the previous file or the new one.
``UnavailableStore`` is the honest no-data backend: reads report unavailable,
writes are refused. Production never falls back to the fixture store.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Protocol

from ..codec import CodecError, format_utc, parse_utc, sha256_hex, strict_loads

UTC = timezone.utc
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class StoreUnavailable(StoreError):
    pass


class ImmutableConflict(StoreError):
    pass


class VersionConflict(StoreError):
    pass


class LockTimeout(StoreError):
    pass


@dataclass(frozen=True, slots=True)
class Head:
    name: str
    record_id: str
    version: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StateRecord:
    collection: str
    record_id: str
    version: int
    data: bytes


class Store(Protocol):
    kind: str

    def available(self) -> bool: ...
    def put_immutable(self, collection: str, record_id: str, data: bytes) -> bool: ...
    def get(self, collection: str, record_id: str) -> bytes | None: ...
    def list_ids(self, collection: str) -> list[str]: ...
    def get_state(self, collection: str, record_id: str) -> StateRecord | None: ...
    def list_state_ids(self, collection: str) -> list[str]: ...
    def put_state(self, collection: str, record_id: str, data: bytes, expected_version: int) -> StateRecord: ...
    def get_head(self, name: str) -> Head | None: ...
    def cas_head(self, name: str, record_id: str, expected_version: int, at: datetime) -> Head: ...
    def idempotency_get(self, kind: str, key: str) -> str | None: ...
    def idempotency_put(self, kind: str, key: str, record_id: str) -> str: ...
    def lock(self, name: str, ttl_seconds: float = 60.0, wait_seconds: float = 10.0) -> contextlib.AbstractContextManager: ...


def _check_collection(name: str) -> None:
    if not _SAFE_NAME.match(name):
        raise StoreError("BAD_COLLECTION", f"collection name {name!r} is not allowlisted-safe")


def _file_token(record_id: str) -> str:
    if not isinstance(record_id, str) or not record_id:
        raise StoreError("BAD_ID", "record id must be a non-empty string")
    if _SAFE_ID.match(record_id):
        return record_id
    return "h-" + sha256_hex(record_id)


def _head_token(name: str) -> str:
    return "h-" + sha256_hex(name)[:40]


class FixtureStore:
    """Persistent file store for fixture mode (never used by live production)."""

    kind = "fixture"

    def __init__(self, root: Path, fault: Callable[[str], None] | None = None):
        self.root = Path(root)
        self._fault = fault or (lambda point: None)

    # --- helpers -----------------------------------------------------------
    def available(self) -> bool:
        return True

    def _dir(self, *parts: str, create: bool = False) -> Path:
        path = self.root.joinpath(*parts)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _atomic_write(self, target: Path, data: bytes, *, exclusive: bool) -> bool:
        """Write ``data`` atomically. With ``exclusive`` the target must not exist.

        Returns True when written, False when an identical exclusive record exists.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".tmp-{uuid.uuid4().hex}"
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            if exclusive:
                try:
                    os.link(tmp, target)
                except FileExistsError:
                    existing = target.read_bytes()
                    if existing == data:
                        return False
                    raise ImmutableConflict("IMMUTABLE_CONFLICT", f"{target.name} already holds different content")
                except (OSError, NotImplementedError):
                    # Filesystems without hard links: exclusive create fallback.
                    if target.exists():
                        existing = target.read_bytes()
                        if existing == data:
                            return False
                        raise ImmutableConflict("IMMUTABLE_CONFLICT", f"{target.name} already holds different content")
                    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                return True
            os.replace(tmp, target)
            tmp = None  # type: ignore[assignment]
            return True
        finally:
            if tmp is not None and Path(tmp).exists():
                with contextlib.suppress(OSError):
                    os.unlink(tmp)

    # --- immutable records ---------------------------------------------------
    def put_immutable(self, collection: str, record_id: str, data: bytes) -> bool:
        _check_collection(collection)
        target = self._dir("objects", collection) / f"{_file_token(record_id)}.json"
        self._fault(f"before_put:{collection}")
        written = self._atomic_write(target, data, exclusive=True)
        self._fault(f"after_put:{collection}")
        return written

    def get(self, collection: str, record_id: str) -> bytes | None:
        _check_collection(collection)
        target = self._dir("objects", collection) / f"{_file_token(record_id)}.json"
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None

    def list_ids(self, collection: str) -> list[str]:
        _check_collection(collection)
        directory = self._dir("objects", collection)
        if not directory.is_dir():
            return []
        ids = []
        for path in directory.glob("*.json"):
            try:
                data = strict_loads(path.read_bytes())
            except CodecError:
                continue
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                ids.append(data["id"])
        return sorted(ids)

    # --- mutable versioned state --------------------------------------------
    def get_state(self, collection: str, record_id: str) -> StateRecord | None:
        _check_collection(collection)
        target = self._dir("state", collection) / f"{_file_token(record_id)}.json"
        try:
            wrapper = json.loads(target.read_bytes())
        except FileNotFoundError:
            return None
        return StateRecord(collection, record_id, int(wrapper["version"]), wrapper["data"].encode("utf-8"))

    def list_state_ids(self, collection: str) -> list[str]:
        _check_collection(collection)
        directory = self._dir("state", collection)
        if not directory.is_dir():
            return []
        ids = []
        for path in directory.glob("*.json"):
            try:
                ids.append(str(json.loads(path.read_bytes())["id"]))
            except (ValueError, KeyError, FileNotFoundError):
                continue
        return sorted(ids)

    def put_state(self, collection: str, record_id: str, data: bytes, expected_version: int) -> StateRecord:
        _check_collection(collection)
        with self.lock(f"state-{collection}-{_file_token(record_id)}"):
            current = self.get_state(collection, record_id)
            current_version = current.version if current else 0
            if current_version != expected_version:
                raise VersionConflict("VERSION_CONFLICT", f"{collection}/{record_id} is at version {current_version}, expected {expected_version}")
            new_version = current_version + 1
            wrapper = json.dumps({"id": record_id, "version": new_version, "data": data.decode("utf-8")}, sort_keys=True).encode("utf-8")
            self._fault(f"before_state:{collection}")
            self._atomic_write(self._dir("state", collection) / f"{_file_token(record_id)}.json", wrapper, exclusive=False)
            return StateRecord(collection, record_id, new_version, data)

    # --- heads ---------------------------------------------------------------
    def get_head(self, name: str) -> Head | None:
        target = self._dir("heads") / f"{_head_token(name)}.json"
        try:
            data = json.loads(target.read_bytes())
        except FileNotFoundError:
            return None
        if data.get("name") != name:
            raise StoreError("HEAD_CORRUPT", f"head file for {name!r} names {data.get('name')!r}")
        return Head(name, data["record_id"], int(data["version"]), parse_utc(data["updated_at"]))

    def cas_head(self, name: str, record_id: str, expected_version: int, at: datetime) -> Head:
        with self.lock(f"head-{_head_token(name)}"):
            current = self.get_head(name)
            current_version = current.version if current else 0
            if current_version != expected_version:
                raise VersionConflict("HEAD_VERSION_CONFLICT", f"head {name!r} is at version {current_version}, expected {expected_version}")
            head = Head(name, record_id, current_version + 1, at.astimezone(UTC))
            payload = json.dumps({"name": name, "record_id": record_id, "version": head.version, "updated_at": format_utc(head.updated_at)}, sort_keys=True).encode("utf-8")
            self._fault(f"before_head:{name}")
            self._atomic_write(self._dir("heads") / f"{_head_token(name)}.json", payload, exclusive=False)
            self._fault(f"after_head:{name}")
            return head

    # --- idempotency -----------------------------------------------------------
    def idempotency_get(self, kind: str, key: str) -> str | None:
        _check_collection(kind)
        target = self._dir("idem", kind) / f"{sha256_hex(key)}.json"
        try:
            data = json.loads(target.read_bytes())
        except FileNotFoundError:
            return None
        if data.get("key") != key:
            raise StoreError("IDEMPOTENCY_CORRUPT", "idempotency record key mismatch")
        return str(data["record_id"])

    def idempotency_put(self, kind: str, key: str, record_id: str) -> str:
        """Bind ``key`` to ``record_id`` once; returns the bound record id."""
        _check_collection(kind)
        target = self._dir("idem", kind) / f"{sha256_hex(key)}.json"
        payload = json.dumps({"key": key, "record_id": record_id}, sort_keys=True).encode("utf-8")
        try:
            self._atomic_write(target, payload, exclusive=True)
        except ImmutableConflict:
            existing = self.idempotency_get(kind, key)
            assert existing is not None
            return existing
        return record_id

    # --- locks -----------------------------------------------------------------
    @contextlib.contextmanager
    def lock(self, name: str, ttl_seconds: float = 60.0, wait_seconds: float = 10.0) -> Iterator[None]:
        token = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]
        lock_dir = self._dir("locks", create=True) / f"{token}.lock"
        owner = uuid.uuid4().hex
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                os.mkdir(lock_dir)
                (lock_dir / "owner").write_text(json.dumps({"owner": owner, "at": time.time(), "pid": os.getpid()}))
                break
            except FileExistsError:
                try:
                    info = json.loads((lock_dir / "owner").read_text())
                    stale = time.time() - float(info.get("at", 0)) > ttl_seconds
                except (FileNotFoundError, ValueError):
                    stale = False
                if stale:
                    with contextlib.suppress(OSError):
                        (lock_dir / "owner").unlink()
                        os.rmdir(lock_dir)
                    continue
                if time.monotonic() > deadline:
                    raise LockTimeout("LOCK_TIMEOUT", f"lock {name!r} is held by another worker")
                time.sleep(0.02)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                info = json.loads((lock_dir / "owner").read_text())
                if info.get("owner") == owner:
                    (lock_dir / "owner").unlink()
                    os.rmdir(lock_dir)


class UnavailableStore:
    """Backend used when no source/repository is configured: honest unavailability."""

    kind = "unavailable"

    def __init__(self, reason: str = "No repository is configured (no SQL credentials and no fixture runtime)."):
        self.reason = reason

    def available(self) -> bool:
        return False

    def _refuse(self) -> StoreUnavailable:
        return StoreUnavailable("REPOSITORY_UNAVAILABLE", self.reason)

    def put_immutable(self, collection: str, record_id: str, data: bytes) -> bool:
        raise self._refuse()

    def get(self, collection: str, record_id: str) -> bytes | None:
        return None

    def list_ids(self, collection: str) -> list[str]:
        return []

    def get_state(self, collection: str, record_id: str) -> StateRecord | None:
        return None

    def list_state_ids(self, collection: str) -> list[str]:
        return []

    def put_state(self, collection: str, record_id: str, data: bytes, expected_version: int) -> StateRecord:
        raise self._refuse()

    def get_head(self, name: str) -> Head | None:
        return None

    def cas_head(self, name: str, record_id: str, expected_version: int, at: datetime) -> Head:
        raise self._refuse()

    def idempotency_get(self, kind: str, key: str) -> str | None:
        return None

    def idempotency_put(self, kind: str, key: str, record_id: str) -> str:
        raise self._refuse()

    @contextlib.contextmanager
    def lock(self, name: str, ttl_seconds: float = 60.0, wait_seconds: float = 10.0) -> Iterator[None]:
        raise self._refuse()
        yield  # pragma: no cover
