"""Per-job details kept for the owner's period (D-25): the evidence behind each team-shift's score.

A team-shift keeps its score forever: the evaluation record (about 1.2 KB, with the seven components) and its light row
in ``team_shift_performance``. It also keeps its jobs for good, compactly, in ``job_outcomes``: each job's shop order id,
aircraft, phase, minutes and outcome (done, missed, excused with its causes, open, out of sequence, unplanned). That
answers which jobs made a score and which jobs miss across aircraft (``rollup.job_patterns``).

The bulky evidence behind the score is the exact unit facts with their credit events and claim lineage
(``evaluation_evidence``, tens of KB per team-shift). The owner keeps it for 90 days (November 2, 2026), after which it is
deleted.

* Only team-shift (GOAL) evidence goes. The small member lists of pooled evaluations stay.
* A team-shift's evidence goes only once its compact job record carries the current evaluation; otherwise it stays and
  the run says so (a store from before the job records: run ``performance-ledger`` first).
* It goes for every revision of a shift whose production day is more than ``keep_days`` before today (site time).
* Each day is handled once. A watermark (``detail_retention/watermark``) moves past every finished day, so a run reads
  only the days that newly crossed the line. On a store from before the light rows, run ``performance-ledger`` first.
* Whatever reads the details holds rather than guesses. Recognition and badges keep what they awarded
  (``analytics.progression``). The shift performance view, the turnover and the twin say the details are gone.
* The store allows the deletion for this collection only (``data.store.RETAINED_DETAIL_COLLECTIONS``).

``performance-details`` reports by default; ``--apply`` deletes and writes a receipt (``detail_retention_runs``).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..codec import canonical_dumps, sha256_hex
from ..domain import digest_id

STATE = "detail_retention"
WATERMARK = "watermark"
RUNS = "detail_retention_runs"
DEFAULT_KEEP_DAYS = 90
MIN_KEEP_DAYS = 7


def run_detail_retention(services: Any, *, keep_days: int = DEFAULT_KEEP_DAYS, apply: bool = False) -> dict[str, Any]:
    """Report (default) or delete the per-job details of team-shifts older than ``keep_days``."""
    from . import history
    from .rollup import JOBS, LEDGER

    repos = services.repositories
    if not repos.available:
        return {"status": "unavailable", "error": {"code": "REPOSITORY_UNAVAILABLE", "message": "no repository is configured; nothing to retain"}}
    if keep_days is None or keep_days < MIN_KEEP_DAYS:
        return {"status": "invalid", "error": {"code": "KEEP_DAYS_TOO_SHORT", "message": f"keep at least {MIN_KEEP_DAYS} days of per-job details"}}
    today = services.clock.now().astimezone(ZoneInfo(services.policy.str("shift.timezone"))).date()
    first_kept = today - timedelta(days=keep_days)
    found = repos.get_state(STATE, WATERMARK)
    through, version = (found[0].get("through"), found[1]) if found is not None else (None, 0)
    days = sorted(d for d in repos.store.list_state_ids(LEDGER) if d < first_kept.isoformat() and (through is None or d > through))
    hashes: set[str] = set()
    shifts = kept = 0
    blocked: list[str] = []                                     # days holding a score with no compact job record yet
    for day in days:
        doc = repos.get_state(LEDGER, day)
        jobs_doc = repos.get_state(JOBS, day)
        job_rows = {} if jobs_doc is None else jobs_doc[0]["rows"]
        for key, row in (doc[0]["rows"].items() if doc is not None else ()):
            evaluation = history.get(services, row["evaluation_id"])
            if evaluation is None:
                continue
            if job_rows.get(key, {}).get("evaluation_id") != evaluation.id:
                kept += 1                                       # no compact job record of this score yet: keep its evidence
                if not blocked or blocked[-1] != day:
                    blocked.append(day)
                continue
            shifts += 1
            hashes.update(e.evidence_hash for e in history.revisions(services, evaluation.scope, evaluation.series_key))
    stored = sorted(h for h in hashes if repos.exists(history.EVIDENCE, h))
    report: dict[str, Any] = {"status": "ok", "applied": apply, "keep_days": keep_days, "first_day_kept": first_kept.isoformat(),
                              "days": len(days), "from": days[0] if days else None, "through": days[-1] if days else through,
                              "team_shifts": shifts, "details_to_delete": len(stored), "kept_without_job_record": kept,
                              "note": "per-job evidence only; every evaluation, its seven components and the light rows stay"}
    if kept:
        report["note"] += f"; {kept} team-shift(s) keep their evidence until a compact job record carries their score (performance-ledger)"
    if not apply or not days:
        return report
    freed = deleted = 0
    for h in stored:
        size = repos.store.delete_immutable(history.EVIDENCE, h)
        if size is not None:
            freed += size
            deleted += 1
    # the watermark stops before the first day that still holds a score without a job record, so a later run revisits it
    done_days = [d for d in days if not blocked or d < blocked[0]]
    mark = done_days[-1] if done_days else through
    new_version = repos.put_state(STATE, WATERMARK, {"through": mark}, version) if mark != through else version
    fingerprint = sha256_hex(canonical_dumps(stored))
    receipt = {"schema_version": 1, "at": services.clock.now().isoformat(), "keep_days": keep_days, "from": days[0], "through": mark,
               "team_shifts": shifts, "kept_without_job_record": kept, "deleted": deleted, "deleted_bytes": freed, "deleted_sha256": fingerprint,
               "watermark_version": new_version}
    receipt_id = digest_id("DDET", days[0], days[-1], new_version, fingerprint)
    repos.put_record(RUNS, "DetailRetentionReceipt", receipt_id, receipt, created_at=services.clock.now())
    return {**report, "deleted": deleted, "deleted_bytes": freed, "receipt_id": receipt_id}


def cli_performance_details(services: Any, args: Any) -> dict[str, Any]:
    return run_detail_retention(services, keep_days=args.keep_days, apply=bool(args.apply))
