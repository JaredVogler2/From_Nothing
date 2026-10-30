"""Non-destructive retention: plan, archive and restore (S17 RECOVERY/MONITORING, P23).

Nothing here deletes a record, and no retention period is assumed. ``retain-history --keep-days N`` takes the
operator's own period and works from durable references.

**Always preserved:**

- accepted plans, with their stages, payloads, acceptance receipts and frozen goals;
- every candidate and validation receipt that an accepted plan, a stage or a pipeline step names;
- source runs, datasets, snapshots and manifests;
- observations, evaluations, recognition, lifecycle and commitment history;
- anything on a legal hold (record ids or source run ids).

**Eligible:**

- superseded candidates that were never staged or accepted, and their validation receipts;
- isolated scenario snapshots and results;
- in both cases only when older than N days and not held.

``--apply`` (fixture only) moves eligible records to ``<runtime>/archive/<receipt>/`` and writes a receipt listing every
moved record with its SHA-256. ``restore_archive`` puts them back byte for byte. Live retention stays with the installed,
lock-holding retention procedures (``FOCUS_FN_PurgeTeamInference``, ``FOCUS_FN_PurgeDurationEvidence``), run by the
retention role with the operator's period. This tool only reports for live.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from .codec import canonical_dumps, parse_utc, sha256_hex, strict_loads
from .domain import digest_id

ELIGIBLE_COLLECTIONS = ("candidates", "validations", "scenario_snapshots", "scenario_results")
PRESERVED_COLLECTIONS = ("accepted_plans", "plan_stages", "published_plans", "published_payloads", "frozen_goals", "acceptance_receipts",
                         "source_runs", "datasets", "snapshots", "manifests", "observation_runs", "lifecycle_events", "actual_intervals",
                         "defect_evidence", "evaluations", "delivery_commitments", "delivery_events", "delivery_receipts")


def _envelope(store: Any, collection: str, record_id: str) -> dict[str, Any] | None:
    raw = store.get(collection, record_id)
    return None if raw is None else strict_loads(raw)


def _referenced(store: Any) -> tuple[set[str], set[str]]:
    """Candidate ids and validation ids that accepted plans, stages and pipeline steps depend on."""
    candidates: set[str] = set()
    validations: set[str] = set()
    for pid in store.list_ids("published_plans"):
        env = _envelope(store, "published_plans", pid)
        payload = env["payload"] if env else {}
        candidates.add(str(payload.get("candidate_id", "")))
        receipt = payload.get("acceptance_receipt") or {}
        for key in ("readback_validation_id", "final_validation_id", "detached_validation_id"):
            if receipt.get(key):
                validations.add(str(receipt[key]))
    for sid in store.list_ids("plan_stages"):
        env = _envelope(store, "plan_stages", sid)
        if env:
            candidates.add(str(env["payload"].get("candidate_id", "")))
    for rid in store.list_state_ids("pipelines"):
        rec = store.get_state("pipelines", rid)
        if rec is None:
            continue
        for step in strict_loads(rec.data).get("steps", {}).values():
            for item in (step.get("output") or {}).values() if isinstance(step.get("output"), dict) else ():
                if isinstance(item, dict):
                    candidates.add(str(item.get("candidate_id", "")))
                    for key in ("validation_id", "readback_validation_id"):
                        if item.get(key):
                            validations.add(str(item[key]))
    candidates.discard("")
    return candidates, validations


def _holds(path: str | None) -> tuple[set[str], set[str]]:
    if not path:
        return set(), set()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return set(map(str, data.get("records", []))), set(map(str, data.get("source_runs", [])))


def plan(services: Any, *, keep_days: int, legal_hold: str | None = None) -> dict[str, Any]:
    if keep_days is None or keep_days < 1:
        return {"status": "invalid", "error": {"code": "KEEP_DAYS_REQUIRED", "message": "pass the operator's retention period in days (>= 1)"}}
    store = services.repositories.store
    now = services.clock.now()
    cutoff = now - timedelta(days=keep_days)
    held_records, held_runs = _holds(legal_hold)
    used_candidates, used_validations = _referenced(store)
    eligible: list[dict[str, Any]] = []
    preserved: dict[str, int] = {}
    reasons: dict[str, int] = {}

    def keep(why: str) -> None:
        reasons[why] = reasons.get(why, 0) + 1

    candidate_of: dict[str, str] = {}
    for collection in ELIGIBLE_COLLECTIONS:
        for record_id in store.list_ids(collection):
            env = _envelope(store, collection, record_id)
            if env is None:
                continue
            created = parse_utc(env["created_at_utc"])
            payload = env.get("payload") or {}
            if collection == "validations":
                candidate_of[record_id] = str(payload.get("candidate_id", ""))
            if record_id in held_records or (env.get("source_run_id") and env["source_run_id"] in held_runs):
                keep("legal hold")
                continue
            if created > cutoff:
                keep(f"younger than {keep_days} days")
                continue
            if collection == "candidates" and record_id in used_candidates:
                keep("candidate of an accepted plan, a stage or a pipeline step")
                continue
            if collection == "validations" and (record_id in used_validations or candidate_of[record_id] in used_candidates):
                keep("validation an accepted plan or a pipeline step depends on")
                continue
            eligible.append({"collection": collection, "id": record_id, "created_at": env["created_at_utc"],
                             "sha256": sha256_hex(store.get(collection, record_id))})
    for collection in PRESERVED_COLLECTIONS:
        n = len(store.list_ids(collection))
        if n:
            preserved[collection] = n
    return {"status": "ok", "keep_days": keep_days, "cutoff": cutoff, "legal_hold": {"records": len(held_records), "source_runs": len(held_runs)},
            "eligible": eligible, "kept_in_eligible_collections": reasons, "preserved_collections": preserved,
            "note": "nothing is deleted; --apply archives eligible fixture records with a restorable receipt"}


def apply(services: Any, retention_plan: dict[str, Any]) -> dict[str, Any]:
    """Archive the plan's eligible records (fixture store only) and write the receipt; idempotent."""
    store = services.repositories.store
    root = getattr(store, "root", None)
    if services.settings.mode != "fixture" or root is None:
        return {"status": "refused", "error": {"code": "LIVE_RETENTION_BY_PROCEDURE",
                "message": "live retention runs only through the installed retention procedures by the retention role"}}
    items = retention_plan["eligible"]
    receipt_id = digest_id("RET", retention_plan["keep_days"], [(i["collection"], i["id"], i["sha256"]) for i in items])
    archive = Path(root) / "archive" / receipt_id
    moved = []
    for item in items:
        source = _object_path(store, item["collection"], item["id"])
        if not source.exists():
            continue                                             # already archived by an earlier apply
        if sha256_hex(source.read_bytes()) != item["sha256"]:
            return {"status": "error", "error": {"code": "CHANGED_SINCE_PLAN", "message": f"{item['collection']}/{item['id']} changed"}}
        target = archive / item["collection"] / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        moved.append(item)
    receipt = {"schema_version": 1, "receipt_id": receipt_id, "keep_days": retention_plan["keep_days"], "cutoff": retention_plan["cutoff"],
               "archived_at": services.clock.now(), "items": items}
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "RECEIPT.json").write_bytes(canonical_dumps(receipt))
    return {"status": "ok", "receipt_id": receipt_id, "archive_dir": str(archive), "moved": len(moved), "total": len(items)}


def restore_archive(services: Any, receipt_id: str) -> dict[str, Any]:
    """Move an archive's records back, byte for byte (refusing any record whose bytes no longer match the receipt)."""
    store = services.repositories.store
    archive = Path(store.root) / "archive" / receipt_id
    receipt = strict_loads((archive / "RECEIPT.json").read_bytes())
    restored = 0
    for item in receipt["items"]:
        target = _object_path(store, item["collection"], item["id"])
        source = archive / item["collection"] / target.name
        if not source.exists():
            continue
        if sha256_hex(source.read_bytes()) != item["sha256"]:
            return {"status": "error", "error": {"code": "ARCHIVE_TAMPERED", "message": f"{item['collection']}/{item['id']}"}}
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        restored += 1
    return {"status": "ok", "receipt_id": receipt_id, "restored": restored}


def _object_path(store: Any, collection: str, record_id: str) -> Path:
    from .data.store import _check_collection, _file_token

    _check_collection(collection)
    return store._dir("objects", collection) / f"{_file_token(record_id)}.json"


def cli_retain_history(services: Any, args: Any) -> dict[str, Any]:
    result = plan(services, keep_days=args.keep_days, legal_hold=getattr(args, "legal_hold", None))
    if result["status"] != "ok" or not getattr(args, "apply", False):
        return result
    return {**result, "applied": apply(services, result)}
