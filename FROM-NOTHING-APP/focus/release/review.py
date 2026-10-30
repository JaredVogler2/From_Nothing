"""Versioned, non-overwriting release reviews (S21 REVIEW, P24).

``write_review`` picks the smallest NNN for which both ``review/REVIEW-NNN.md`` and ``review/VERDICT-NNN.json`` are
absent, binds the exact manual, implementation, test, source, policy and acceptance-run hashes, and creates both files
exclusively: an existing review file is never opened for writing. Earlier reviews, including a partial pair, are
listed with their own SHA-256 so the history is bound as well.

The review refuses a stale acceptance run (``ACCEPTANCE_STALE``): the acceptance hashes must equal the files as they
are now. Its verdict is honest about its kind: an implementer self-check is not an independent review, and live
release stays blocked by the gates in ``focus.release.gates``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .acceptance import PASS
from .gates import BLOCKING, GATES
from .hashes import build_hashes, git_state

REVIEW_DIR = "review"
SELF_CHECK = "IMPLEMENTER_SELF_CHECK"
_FILE = re.compile(r"^(REVIEW|VERDICT)-(\d{3})\.(md|json)$")


class ReviewError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def next_number(review_dir: Path) -> int:
    n = 1
    while (review_dir / f"REVIEW-{n:03d}.md").exists() or (review_dir / f"VERDICT-{n:03d}.json").exists():
        n += 1
    if n > 999:
        raise ReviewError("REVIEW_NUMBERS_EXHAUSTED", "REVIEW-001 to REVIEW-999 are all used")
    return n


def history(review_dir: Path) -> list[dict[str, Any]]:
    """Every earlier review file, by number, with its SHA-256 (a partial pair is listed as it is)."""
    found: dict[int, dict[str, Any]] = {}
    for path in sorted(review_dir.glob("*-*.*")) if review_dir.is_dir() else []:
        match = _FILE.match(path.name)
        if match:
            entry = found.setdefault(int(match.group(2)), {"number": int(match.group(2))})
            key = "review" if match.group(1) == "REVIEW" else "verdict"
            entry[key] = {"file": f"{REVIEW_DIR}/{path.name}", "sha256": _sha256(path)}
    return [found[n] for n in sorted(found)]


def _local_verdict(acceptance: dict[str, Any]) -> tuple[str, list[str]]:
    s = acceptance["summary"]
    blockers = [f"{cid} {c['status']}" for cid, c in acceptance["cases"].items() if c["status"] != PASS]
    blockers += [f"{cid} {c['status']}" for cid, c in acceptance["constraints"].items() if c["status"] != PASS]
    blockers += [f"{group}.{key} {item['status']}" for group in ("counterexamples", "verification")
                 for key, item in acceptance.get(group, {}).items() if item["status"] != PASS]
    blockers += [f"unmapped failure {n}" for n in s["unmapped_failures"]] + [f"required skip {n}" for n in s["required_skips"]]
    blockers += [f"map: {p}" for p in s["map_problems"]]
    complete = s["local_acceptance"] == PASS and not blockers
    return ("LOCAL_BUILD_COMPLETE" if complete else "LOCAL_BUILD_INCOMPLETE"), blockers


def _check_fresh(acceptance: dict[str, Any], hashes: dict[str, Any]) -> None:
    for key in ("implementation", "tests"):
        bound = acceptance.get("hashes", {}).get(key, {}).get("sha256")
        if bound != hashes[key]["sha256"]:
            raise ReviewError("ACCEPTANCE_STALE", f"the acceptance run bound {key} {str(bound)[:16]}, the files now hash to "
                                                  f"{hashes[key]['sha256'][:16]}: re-run the acceptance first")


def build_verdict(app_root: Path, number: int, acceptance: dict[str, Any], acceptance_path: Path, review_dir: Path,
                  *, reviewer: str, kind: str, recorded_at: str) -> dict[str, Any]:
    hashes = build_hashes(app_root)
    _check_fresh(acceptance, hashes)
    local, blockers = _local_verdict(acceptance)
    try:
        acceptance_ref = acceptance_path.relative_to(app_root).as_posix()
    except ValueError:
        acceptance_ref = str(acceptance_path)
    return {
        "schema_version": 1,
        "review_id": f"REVIEW-{number:03d}",
        "number": number,
        "recorded_at_utc": recorded_at,
        "kind": kind,
        "independent": kind != SELF_CHECK,
        "reviewer": reviewer,
        "scope": {"manual_sections": "S00-S21, Z01", "stages": "P01-P24", "cases": "A01-A72", "requirements": "C01-C39",
                  "evidence": "fixture only; live release is a separate capability (S21)"},
        "hashes": {**hashes, "acceptance_run": {"file": acceptance_ref, "sha256": _sha256(acceptance_path),
                                                "recorded_at_utc": acceptance.get("run", {}).get("recorded_at_utc"),
                                                "python": acceptance.get("run", {}).get("python")},
                   "git": git_state(app_root)},
        "acceptance": acceptance["summary"],
        "local_verdict": local,
        "local_blockers": blockers,
        "live_release": {"status": "BLOCKED" if any(g.kind == BLOCKING for g in GATES) else "READY_FOR_LIVE_GATES",
                         "gates": [g.as_dict() for g in GATES]},
        "previous_reviews": history(review_dir),
        "statement": ("This verdict is an implementer self-check, not an independent review. It binds the hashes above; "
                      "a changed file needs a new review." if kind == SELF_CHECK else
                      "This verdict binds the hashes above; a changed file needs a new review."),
    }


def render_review(v: dict[str, Any]) -> str:
    h, s = v["hashes"], v["acceptance"]
    gates = [g for g in v["live_release"]["gates"]]
    lines = [
        f"# {v['review_id']}: FOCUS From Nothing release review",
        "",
        f"- Kind: **{v['kind']}** (independent: {str(v['independent']).lower()})",
        f"- Reviewer: {v['reviewer']}",
        f"- Recorded (host clock, UTC): {v['recorded_at_utc']}",
        f"- Local verdict: **{v['local_verdict']}**",
        f"- Live release: **{v['live_release']['status']}** ({sum(g['kind'] == BLOCKING for g in gates)} blocking gates)",
        "",
        "## Bound hashes",
        "",
        "| Item | SHA-256 | Detail |",
        "| --- | --- | --- |",
        f"| Manual | `{h['manual']['sha256']}` | {h['manual']['status']} against STATUS.json |",
        f"| Implementation | `{h['implementation']['sha256']}` | {h['implementation']['files']} files ({h['implementation']['method']}) |",
        f"| Tests | `{h['tests']['sha256']}` | {h['tests']['files']} files |",
        f"| Fixture source | `{h['source']['fixture']['sha256']}` | {h['source']['fixture']['files']} files; live: {h['source']['live']} |",
        f"| Policy `{h['policy']['id']}` | `{h['policy']['sha256']}` | canonical policy hash |",
        f"| Acceptance run | `{h['acceptance_run']['sha256']}` | `{h['acceptance_run']['file']}`, Python {h['acceptance_run']['python']} |",
    ]
    if h.get("git"):
        lines.append(f"| Git HEAD | `{h['git']['head']}` | uncommitted changes in bound files: {str(h['git']['uncommitted_changes']).lower()} |")
    lines += [
        "",
        "## Acceptance",
        "",
        f"- Cases: {s['cases']['passed']}/{s['cases']['total']} pass",
        f"- Requirements: {s['constraints']['passed']}/{s['constraints']['total']} pass, "
        f"{s['constraints']['paths_verified']}/{s['constraints']['paths_total']} caller paths verified",
        f"- Named counterexamples: {', '.join(f'{k} {st}' for k, st in s['counterexamples'].items())}",
        f"- Verification themes: {', '.join(f'{k} {st}' for k, st in s['verification'].items())}",
        f"- Test run: {json.dumps(s['tests'], sort_keys=True)}",
        "",
        "Local blockers: " + ("none." if not v["local_blockers"] else ""),
    ]
    lines += [f"- {b}" for b in v["local_blockers"]]
    lines += ["", "## Live release gates", "", "| Gate | Kind | Area | Effect today | Resolution |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {g['id']} | {g['kind']} | {g['area']} | {g['effect']} | {g['resolve']} |" for g in gates]
    lines += ["", "## Earlier reviews", ""]
    lines += [f"- {e['number']:03d}: " + ", ".join(f"`{x['file']}` `{x['sha256'][:16]}`" for x in (e.get("review"), e.get("verdict")) if x)
              for e in v["previous_reviews"]] or ["None."]
    lines += ["", "## Statement", "", v["statement"], ""]
    return "\n".join(lines)


def write_review(app_root: Path, acceptance_path: Path, *, review_dir: Path | None = None, reviewer: str,
                 kind: str = SELF_CHECK, recorded_at: str | None = None) -> dict[str, Any]:
    review_dir = review_dir or app_root / REVIEW_DIR
    review_dir.mkdir(parents=True, exist_ok=True)
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    number = next_number(review_dir)
    verdict = build_verdict(app_root, number, acceptance, acceptance_path, review_dir, reviewer=reviewer, kind=kind,
                            recorded_at=recorded_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    verdict_path = review_dir / f"VERDICT-{number:03d}.json"
    review_path = review_dir / f"REVIEW-{number:03d}.md"
    try:
        with verdict_path.open("x", encoding="utf-8") as f:       # exclusive create: never overwrite
            f.write(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
        with review_path.open("x", encoding="utf-8") as f:
            f.write(render_review(verdict))
    except FileExistsError as exc:
        raise ReviewError("REVIEW_EXISTS", f"{exc.filename} appeared while writing; nothing was overwritten") from None
    return {"number": number, "review": review_path, "verdict": verdict_path, "local_verdict": verdict["local_verdict"],
            "live_release": verdict["live_release"]["status"]}


def verify_review(app_root: Path, number: int, *, review_dir: Path | None = None) -> dict[str, Any]:
    """Compare a stored verdict's bound hashes with the files as they are now."""
    review_dir = review_dir or app_root / REVIEW_DIR
    path = review_dir / f"VERDICT-{number:03d}.json"
    if not path.is_file():
        raise ReviewError("REVIEW_NOT_FOUND", f"{path.name} does not exist")
    bound = json.loads(path.read_text(encoding="utf-8"))["hashes"]
    now = build_hashes(app_root)
    checks = {
        "manual": (bound["manual"]["sha256"], now["manual"]["sha256"]),
        "implementation": (bound["implementation"]["sha256"], now["implementation"]["sha256"]),
        "tests": (bound["tests"]["sha256"], now["tests"]["sha256"]),
        "fixture_source": (bound["source"]["fixture"]["sha256"], now["source"]["fixture"]["sha256"]),
        "policy": (bound["policy"]["sha256"], now["policy"]["sha256"]),
    }
    result = {k: "MATCH" if a == b else "MISMATCH" for k, (a, b) in checks.items()}
    return {"number": number, "checks": result, "matches": all(v == "MATCH" for v in result.values())}


def cli_review(services: Any, args: argparse.Namespace) -> dict[str, Any]:
    app_root = services.settings.app_root
    try:
        if args.verify is not None:
            out = verify_review(app_root, args.verify)
            return {"status": "ok" if out["matches"] else "error", **out}
        if args.list:
            return {"status": "ok", "reviews": history(app_root / REVIEW_DIR), "next_number": next_number(app_root / REVIEW_DIR)}
        acceptance = Path(args.acceptance) if Path(args.acceptance).is_absolute() else app_root / args.acceptance
        out = write_review(app_root, acceptance, reviewer=args.reviewer, kind=args.kind)
    except ReviewError as exc:
        return {"status": "error", "error": {"code": exc.code, "message": exc.message}}
    return {"status": "ok", "number": out["number"], "review": str(out["review"].relative_to(app_root)),
            "verdict": str(out["verdict"].relative_to(app_root)), "local_verdict": out["local_verdict"], "live_release": out["live_release"]}
