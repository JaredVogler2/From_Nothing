"""Versioned FOCUS_FN migrations: checksums, static lint, dry-run, authorized apply and recovery (S06, S17, P23).

Nothing here runs at import, at app start or on a source SELECT. Nothing is applied without ``migrate --apply``.

* **Discovery.** ``sql/migrations/NNN_name.sql`` files, with contiguous versions starting at 001, each paired with
  ``sql/migrations/rollback/NNN_name.sql``. Any other file, such as another module's design draft, is never picked up.
* **Checksums.** Each script's checksum is the SHA-256 of its exact bytes. If a recorded version's checksum differs,
  every later step is blocked.
* **Static lint** (no server needed):
  - created objects follow the FOCUS_FN naming: tables, views and procedures ``WEB_APP.FOCUS_FN_*``, triggers
    ``WEB_APP.TR_FN_*``, indexes ``IX_FN_`` or ``UX_FN_``, constraints ``CK_FN_``, ``UX_FN_``, ``FK_FN_``, ``PK_FN_``
    or ``DF_FN_``;
  - every table has a primary key and references only tables created earlier;
  - instants (``*Utc``) are ``datetime2(6)`` and payloads are ``varbinary(max)``;
  - identity columns are ``bigint``;
  - there is no ``TRUNCATE``, ``DROP`` or ``ALTER TABLE ... DROP``;
  - ``DELETE`` appears only inside a retention procedure that takes an application lock and sets the
    ``focus_retention`` session flag that the immutability triggers honor.
* **Plan** against a catalog (installed objects plus the version ledger). Each version is one of:
  - ``APPLIED`` or ``PENDING``;
  - ``COLLISION``: an installed object the ledger does not account for;
  - ``CHECKSUM_MISMATCH``, ``DRIFT`` (an applied object is missing) or ``UNKNOWN_RECORDED_VERSION``;
  - ``BLOCKED``: after an earlier blocking step.
* **Apply.** Each pending version runs in order, in one transaction, under ``sp_getapplock('FOCUS_FN_MIGRATIONS')``.
  Every declared object is verified before the checksum is recorded. A failure records nothing, leaves nothing
  behind and stops the run. Reruns check the ledger instead of re-creating anything.
* **Recovery** is forward by default. A rollback script removes only its own version's objects, and only for the
  latest applied version. It refuses to drop a table that holds rows.

``FixtureCatalog`` rehearses the protocol in a JSON file, including injected failures. ``SqlCatalog`` is the live
adapter for an installer principal. This build never connected it to a server.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

SCHEMA = "WEB_APP"
LOCK = "FOCUS_FN_MIGRATIONS"
VERSION_FILE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
MANIFEST_SCHEMA = 1
BLOCKING = frozenset({"COLLISION", "CHECKSUM_MISMATCH", "DRIFT", "UNKNOWN_RECORDED_VERSION", "BLOCKED"})

_NAME = r"(?:\[?\w+\]?\s*\.\s*)?\[?\w+\]?"
_CREATE = re.compile(rf"\bCREATE\s+(OR\s+ALTER\s+)?(TABLE|VIEW|PROCEDURE|PROC|FUNCTION|TRIGGER)\s+({_NAME})", re.I)
_INDEX = re.compile(rf"\bCREATE\s+(?:UNIQUE\s+)?(?:CLUSTERED\s+|NONCLUSTERED\s+)?INDEX\s+(\[?\w+\]?)\s+ON\s+({_NAME})", re.I)
_TABLE_BODY = re.compile(rf"\bCREATE\s+TABLE\s+({_NAME})\s*\(", re.I)
_ALTER_ADD = re.compile(rf"\bALTER\s+TABLE\s+({_NAME})\s+(?:WITH\s+(?:NO)?CHECK\s+)?ADD\b", re.I)
_CONSTRAINT = re.compile(r"\bCONSTRAINT\s+(\[?\w+\]?)", re.I)
_REFERENCES = re.compile(rf"\bREFERENCES\s+({_NAME})", re.I)
_DESTRUCTIVE = re.compile(r"\b(TRUNCATE\s+TABLE|DROP\s+(?:TABLE|INDEX|VIEW|PROCEDURE|PROC|TRIGGER|FUNCTION|SCHEMA|DATABASE|CONSTRAINT|COLUMN))\b", re.I)
_ALTER_DROP = re.compile(rf"\bALTER\s+TABLE\s+{_NAME}\s+DROP\b", re.I)
_TRIGGER_EVENTS = re.compile(r"\b(?:AFTER|FOR|INSTEAD\s+OF)\s+(?:(?:INSERT|UPDATE|DELETE)\s*,?\s*)+", re.I)
_FK_ACTIONS = re.compile(r"\bON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|NO\s+ACTION|SET\s+NULL|SET\s+DEFAULT)\b", re.I)
_DELETE = re.compile(r"\bDELETE\b", re.I)
_GO = re.compile(r"^\s*GO\s*;?\s*$", re.I)


class MigrationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------- parsing


def strip_comments(text: str) -> str:
    """Remove ``--`` and ``/* */`` comments, keeping string literals intact."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                in_str = False
            i += 1
        elif c == "'":
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def batches(text: str) -> list[str]:
    """T-SQL batches split at ``GO`` lines (procedures, views and triggers must start their own batch)."""
    parts: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _GO.match(line):
            parts.append("\n".join(current))
            current = []
        else:
            current.append(line)
    parts.append("\n".join(current))
    return [p for p in parts if strip_comments(p).strip()]


def _qualify(raw: str) -> str:
    parts = [p.strip().strip("[]") for p in raw.split(".")]
    return ".".join(parts)


def _balanced(text: str, open_at: int) -> int:
    depth, i, in_str = 0, open_at, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "'":
                in_str = False
        elif c == "'":
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise MigrationError("UNBALANCED", f"no closing parenthesis for the one at offset {open_at}")


def _split_top(body: str) -> list[str]:
    items, depth, start, in_str = [], 0, 0, False
    for i, c in enumerate(body):
        if in_str:
            if c == "'":
                in_str = False
        elif c == "'":
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            items.append(body[start:i].strip())
            start = i + 1
    items.append(body[start:].strip())
    return [x for x in items if x]


@dataclass(frozen=True)
class SqlObject:
    kind: str                     # TABLE | VIEW | PROCEDURE | FUNCTION | TRIGGER | INDEX | CONSTRAINT | COLUMN
    name: str                     # schema-qualified for schema objects; bare for indexes, constraints and columns
    parent: str = ""              # the table an index, constraint or added column belongs to
    replaceable: bool = False     # CREATE OR ALTER
    definition: str = ""          # an added column's definition (lint and rollback)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.parent}.{self.name}" if self.kind in ("INDEX", "COLUMN") else f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    definition: str


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    has_primary_key: bool
    references: tuple[str, ...]
    constraints: tuple[str, ...]


def parse_tables(text: str) -> list[Table]:
    out = []
    for m in _TABLE_BODY.finditer(text):
        name = _qualify(m.group(1))
        close = _balanced(text, m.end() - 1)
        cols, refs, cons, pk = [], [], [], False
        for item in _split_top(text[m.end():close]):
            upper = item.upper()
            refs += [_qualify(r) for r in _REFERENCES.findall(item)]
            cons += [c.strip("[]") for c in _CONSTRAINT.findall(item)]
            if "PRIMARY KEY" in upper:
                pk = True
            if re.match(r"^(CONSTRAINT|PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|INDEX)\b", upper):
                continue
            parts = item.split(None, 2)
            if len(parts) >= 2:
                type_ = re.match(r"[A-Za-z0-9_]+(?:\s*\([^)]*\))?", parts[1] + (" " + parts[2] if len(parts) > 2 else ""))
                cols.append(Column(parts[0].strip("[]"), re.sub(r"\s+", "", type_.group(0)).lower() if type_ else parts[1].lower(), item))
        out.append(Table(name, tuple(cols), pk, tuple(refs), tuple(cons)))
    return out


def created_objects(text: str) -> list[SqlObject]:
    """Objects a script creates, in order: schema objects, indexes and named constraints (inline or ALTER TABLE ADD)."""
    clean = strip_comments(text)
    found: list[tuple[int, SqlObject]] = []
    for m in _CREATE.finditer(clean):
        kind = {"PROC": "PROCEDURE"}.get(m.group(2).upper(), m.group(2).upper())
        found.append((m.start(), SqlObject(kind, _qualify(m.group(3)), replaceable=bool(m.group(1)))))
    for m in _INDEX.finditer(clean):
        found.append((m.start(), SqlObject("INDEX", m.group(1).strip("[]"), _qualify(m.group(2)))))
    for t in parse_tables(clean):
        at = clean.find(t.name.split(".")[-1])
        found += [(at, SqlObject("CONSTRAINT", c, t.name)) for c in t.constraints]
    for m in _ALTER_ADD.finditer(clean):
        end = clean.find(";", m.end())
        parent = _qualify(m.group(1))
        for offset, item in enumerate(_split_top(clean[m.end(): end if end >= 0 else len(clean)])):
            at = m.start() + offset / 1000                       # keep declaration order inside one statement
            if not re.match(r"^CONSTRAINT\b", item, re.I):
                found.append((at, SqlObject("COLUMN", item.split(None, 1)[0].strip("[]"), parent, definition=item)))
            found += [(at + 0.0001, SqlObject("CONSTRAINT", c.strip("[]"), parent)) for c in _CONSTRAINT.findall(item)]
    return [o for _, o in sorted(found, key=lambda x: x[0])]


# ---------------------------------------------------------------------- migrations on disk


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sha256: str
    text: str
    objects: tuple[SqlObject, ...]
    rollback_path: Path | None
    rollback_sha256: str | None

    @property
    def object_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(o.key for o in self.objects))

    @property
    def batches(self) -> list[str]:
        return batches(self.text)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover(root: Path) -> list[Migration]:
    """Versioned migrations under ``root`` (``sql/migrations``); versions must run 1..N without gaps or duplicates."""
    found: dict[int, Migration] = {}
    for path in sorted(root.glob("*.sql")):
        m = VERSION_FILE.match(path.name)
        if m is None:
            continue                                  # drafts and other modules' designs are never applied
        version = int(m.group(1))
        if version in found:
            raise MigrationError("DUPLICATE_VERSION", f"{path.name} and {found[version].path.name} share version {version:03d}")
        data = path.read_bytes()
        text = data.decode("utf-8")
        rollback = root / "rollback" / path.name
        found[version] = Migration(version, m.group(2), path, sha256_bytes(data), text, tuple(created_objects(text)),
                                   rollback if rollback.exists() else None,
                                   sha256_bytes(rollback.read_bytes()) if rollback.exists() else None)
    versions = sorted(found)
    if versions and versions != list(range(1, len(versions) + 1)):
        raise MigrationError("VERSION_GAP", f"versions must run 001..{len(versions):03d} without gaps, found {versions}")
    return [found[v] for v in versions]


# ---------------------------------------------------------------------- static lint


@dataclass(frozen=True)
class Finding:
    code: str
    version: int
    subject: str
    detail: str


_NAMING = {
    "TABLE": re.compile(r"^WEB_APP\.FOCUS_FN_\w+$"),
    "VIEW": re.compile(r"^WEB_APP\.FOCUS_FN_\w+$"),
    "PROCEDURE": re.compile(r"^WEB_APP\.FOCUS_FN_\w+$"),
    "FUNCTION": re.compile(r"^WEB_APP\.FOCUS_FN_\w+$"),
    "TRIGGER": re.compile(r"^WEB_APP\.TR_FN_\w+$"),
    "INDEX": re.compile(r"^(IX|UX)_FN_\w+$"),
    "CONSTRAINT": re.compile(r"^(CK|UX|FK|PK|DF)_FN_\w+$"),
    "COLUMN": re.compile(r"^[A-Za-z]\w*$"),
}


def _column_type(definition: str) -> str:
    parts = definition.split(None, 2)
    if len(parts) < 2:
        return ""
    match = re.match(r"[A-Za-z0-9_]+(?:\s*\([^)]*\))?", parts[1] + (" " + parts[2] if len(parts) > 2 else ""))
    return re.sub(r"\s+", "", match.group(0)).lower() if match else parts[1].lower()


def _delete_allowed(batch: str) -> bool:
    head = _CREATE.search(batch)
    return (head is not None and head.group(2).upper() in ("PROCEDURE", "PROC") and re.search(r"(Purge|Retain)", head.group(3)) is not None
            and "sp_getapplock" in batch and "focus_retention" in batch)


_FN_REF = re.compile(r"\bWEB_APP\s*\.\s*\[?(FOCUS_FN_\w+)\]?", re.I)


def lint(migrations: Iterable[Migration]) -> list[Finding]:
    findings: list[Finding] = []
    tables_so_far: set[str] = set()
    relations_so_far: set[str] = set()          # tables and views, for views and triggers that bind at creation
    seen: dict[str, int] = {}
    for m in migrations:
        clean = strip_comments(m.text)
        for batch in batches(m.text):
            body = strip_comments(batch)
            head = _CREATE.search(body)
            if head is not None and head.group(2).upper() in ("VIEW", "TRIGGER"):
                own = _qualify(head.group(3))
                for ref in sorted({f"WEB_APP.{r}" for r in _FN_REF.findall(body)} - {own}):
                    if ref not in relations_so_far:
                        findings.append(Finding("BINDING_ORDER", m.version, own, f"{head.group(2).lower()} references {ref}, "
                                                                               "which no earlier statement creates"))
                if head.group(2).upper() == "VIEW":
                    relations_so_far.add(own)
            relations_so_far.update(t.name for t in parse_tables(body))
        for o in m.objects:
            rule = _NAMING[o.kind]
            if not rule.match(o.name):
                findings.append(Finding("NAMING", m.version, o.key, f"{o.kind.lower()} {o.name} breaks the FOCUS_FN naming rule"))
            if o.parent and not _NAMING["TABLE"].match(o.parent):
                findings.append(Finding("NAMING", m.version, o.key, f"parent {o.parent} is not a WEB_APP.FOCUS_FN_ table"))
            if not o.replaceable and o.key in seen:
                findings.append(Finding("DUPLICATE_OBJECT", m.version, o.key, f"already created by migration {seen[o.key]:03d}"))
            seen.setdefault(o.key, m.version)
        for t in parse_tables(clean):
            if not t.has_primary_key:
                findings.append(Finding("PRIMARY_KEY", m.version, t.name, "every FOCUS_FN table needs a primary key"))
            for ref in t.references:
                if ref not in tables_so_far and ref != t.name:
                    findings.append(Finding("REFERENCE", m.version, t.name, f"references {ref}, which no earlier statement creates"))
            for c in t.columns:
                if c.name.endswith("Utc") and c.type != "datetime2(6)":
                    findings.append(Finding("INSTANT_PRECISION", m.version, f"{t.name}.{c.name}", f"instants are datetime2(6), not {c.type}"))
                if c.type in ("datetime", "smalldatetime"):
                    findings.append(Finding("LEGACY_DATETIME", m.version, f"{t.name}.{c.name}", f"{c.type} loses precision; use datetime2(6)"))
                if c.name.endswith("Payload") and c.type != "varbinary(max)":
                    findings.append(Finding("PAYLOAD_TYPE", m.version, f"{t.name}.{c.name}", f"canonical payloads are varbinary(max), not {c.type}"))
                if "IDENTITY" in c.definition.upper() and c.type != "bigint":
                    findings.append(Finding("IDENTITY_WIDTH", m.version, f"{t.name}.{c.name}", f"identity columns are bigint, not {c.type}"))
            tables_so_far.add(t.name)
        for o in m.objects:
            if o.kind != "COLUMN":
                continue
            type_, upper = _column_type(o.definition), o.definition.upper()
            if o.name.endswith("Utc") and type_ != "datetime2(6)":
                findings.append(Finding("INSTANT_PRECISION", m.version, o.key, f"instants are datetime2(6), not {type_}"))
            if o.name.endswith("Payload") and type_ != "varbinary(max)":
                findings.append(Finding("PAYLOAD_TYPE", m.version, o.key, f"canonical payloads are varbinary(max), not {type_}"))
            if "NOT NULL" in upper and "DEFAULT" not in upper:
                findings.append(Finding("ADDED_COLUMN_NOT_NULL", m.version, o.key, "an added column is NULL or has a default, so existing rows stay valid"))
        for hit in _DESTRUCTIVE.finditer(clean):
            findings.append(Finding("DESTRUCTIVE", m.version, hit.group(1).upper(), "forward migrations never drop or truncate"))
        for hit in _ALTER_DROP.finditer(clean):
            findings.append(Finding("DESTRUCTIVE", m.version, hit.group(0)[:60], "forward migrations never drop columns or constraints"))
        for batch in batches(m.text):
            body = _FK_ACTIONS.sub(" ", _TRIGGER_EVENTS.sub(" ", strip_comments(batch)))
            if _DELETE.search(body) and not _delete_allowed(body):
                head = _CREATE.search(body)
                findings.append(Finding("DELETE_OUTSIDE_RETENTION", m.version, head.group(3) if head else "batch",
                                        "DELETE only in a locked retention procedure that sets focus_retention"))
        if m.rollback_path is None:
            findings.append(Finding("ROLLBACK_MISSING", m.version, m.path.name, "every version ships sql/migrations/rollback/<same name>"))
    return findings


def lint_rollback(m: Migration) -> list[Finding]:
    """A rollback script removes only its own version's objects and guards every table drop on emptiness."""
    if m.rollback_path is None:
        return [Finding("ROLLBACK_MISSING", m.version, m.path.name, "no rollback script")]
    text = strip_comments(m.rollback_path.read_text(encoding="utf-8"))
    findings = []
    own = {o.name for o in m.objects}
    for hit in re.finditer(rf"\bDROP\s+(TABLE|VIEW|PROCEDURE|PROC|TRIGGER|FUNCTION|INDEX|CONSTRAINT)\s+(?:IF\s+EXISTS\s+)?({_NAME})", text, re.I):
        name = _qualify(hit.group(2))
        if name not in own and name.split(".")[-1] not in own:
            findings.append(Finding("ROLLBACK_FOREIGN_OBJECT", m.version, name, "a rollback drops only objects its own version created"))
    for t in (o.name for o in m.objects if o.kind == "TABLE"):
        guard = re.search(rf"IF\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+{re.escape(t)}\b[^)]*\)\s*THROW", text, re.I)
        if re.search(rf"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?{re.escape(t)}\b", text, re.I) and guard is None:
            findings.append(Finding("ROLLBACK_UNGUARDED_DROP", m.version, t, "a table is dropped only when it holds no rows"))
    own_columns = {(o.parent, o.name) for o in m.objects if o.kind == "COLUMN"}
    for hit in re.finditer(rf"\bALTER\s+TABLE\s+({_NAME})\s+DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?(\[?\w+\]?)", text, re.I):
        table, column = _qualify(hit.group(1)), hit.group(2).strip("[]")
        if (table, column) not in own_columns:
            findings.append(Finding("ROLLBACK_FOREIGN_OBJECT", m.version, f"{table}.{column}", "a rollback drops only columns its own version added"))
        elif re.search(rf"IF\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+{re.escape(table)}\s+WHERE\s+{re.escape(column)}\s+IS\s+NOT\s+NULL\s*\)\s*THROW", text, re.I) is None:
            findings.append(Finding("ROLLBACK_UNGUARDED_DROP", m.version, f"{table}.{column}", "a column is dropped only when it holds no value"))
    if re.search(r"\bTRUNCATE\b|\bDELETE\b", text, re.I):
        findings.append(Finding("ROLLBACK_DESTROYS_DATA", m.version, m.rollback_path.name, "rollback never deletes rows"))
    return findings


def rollback_script(m: Migration) -> str:
    """The rollback for one version, generated from its declared objects (one authority), in reverse creation order.

    Code objects are dropped outright. A constraint added to an earlier table is dropped by name. An added (nullable)
    column is dropped only when no row holds a value. A table is dropped only when it holds no rows; its own
    constraints and indexes go with it. Nothing deletes a row. For 001 the version ledger goes last, and only when no
    later version is recorded.
    """
    created_tables = {o.name for o in m.objects if o.kind == "TABLE"}
    lines = [f"-- Rollback of migration {m.version:03d} ({m.name}), generated by focus.migrations.rollback_script from the forward",
             "-- script's declared objects. Recovery is forward by default; this exists for an install that has just failed review.",
             "-- The runner executes it in one transaction under sp_getapplock('FOCUS_FN_MIGRATIONS'); any guard stops it whole.",
             "-- Nothing here deletes a row.",
             "SET XACT_ABORT ON;"]
    code = 51950
    ledger_last = False
    for o in reversed(m.objects):
        if o.kind in ("TRIGGER", "VIEW", "PROCEDURE", "FUNCTION"):
            lines.append(f"DROP {o.kind} IF EXISTS {o.name};")
        elif o.kind == "INDEX" and o.parent not in created_tables:
            lines.append(f"DROP INDEX IF EXISTS {o.name} ON {o.parent};")
        elif o.kind == "CONSTRAINT" and o.parent not in created_tables:
            lines.append(f"ALTER TABLE {o.parent} DROP CONSTRAINT IF EXISTS {o.name};")
        elif o.kind == "COLUMN" and o.parent not in created_tables:
            code += 1
            lines += [f"IF EXISTS (SELECT 1 FROM {o.parent} WHERE {o.name} IS NOT NULL) THROW {code}, "
                      f"'{o.parent}.{o.name} holds values; recover forward with a new migration', 1;",
                      f"ALTER TABLE {o.parent} DROP COLUMN IF EXISTS {o.name};"]
        elif o.kind == "TABLE":
            if o.name == f"{SCHEMA}.FOCUS_FN_SchemaVersion":
                ledger_last = True
                continue
            code += 1
            lines += [f"IF EXISTS (SELECT 1 FROM {o.name}) THROW {code}, '{o.name} holds rows; recover forward with a new migration', 1;",
                      f"DROP TABLE {o.name};"]
    if ledger_last:
        lines += [f"IF EXISTS (SELECT 1 FROM {SCHEMA}.FOCUS_FN_SchemaVersion WHERE Version > {m.version}) THROW 51949, "
                  "'later migrations are installed; roll them back first', 1;",
                  f"DROP TABLE {SCHEMA}.FOCUS_FN_SchemaVersion;"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- roles and grants (S17 TRUSTED IDENTITY)

_FN = "WEB_APP.FOCUS_FN_"
# role -> permission -> objects (bare FOCUS_FN_ suffixes). The installer is a DBA principal with DDL rights on WEB_APP and
# is deliberately absent: no script grants DDL. Members are added by the DBA; the app login is never a member of a
# source, stager, signer, evaluator or retention role.
ROLE_GRANTS: dict[str, dict[str, tuple[str, ...]]] = {
    "FOCUS_FN_AppReader": {"SELECT": (
        "TeamInferenceAtHead", "DurationStatisticsAtHead", "IncumbentAtHead", "DeliveryCommitmentState", "CurrentPlan", "CurrentEvaluation",
        "TeamXp", "SchemaVersion", "Policy", "SourceRun", "SourceHead", "SourceDataset", "SourceRow", "PublishedPlan", "PlanHead", "FrozenGoal",
        "Commitment", "CommitmentResult", "DeliveryReceipt", "Event", "ExcusalDecision", "Evaluation", "EvaluationEvidence", "EvaluationHead",
        "RecognitionEvent", "BadgeAward", "ActualInterval", "DefectEvidence", "GoalAmendment", "DisruptionClaim", "SequenceViolation",
        "ObservationRun", "ObservationFeedReceipt", "ObservationQuarantine", "IEReview", "WorkerRun", "PipelineRun", "PipelineStep", "Outbox")},
    "FOCUS_FN_AppCommand": {"INSERT": ("WorkerRun",), "UPDATE": ("WorkerRun",)},
    "FOCUS_FN_SourceWorker": {"EXECUTE": ("BeginSourceRun", "CompleteSourceRun", "RejectSourceRun", "PromoteSourceHead"),
                              "INSERT": ("SourceDataset", "SourceRow", "ClockObservation", "TeamInference", "DurationObservation", "DurationStatistics")},
    "FOCUS_FN_Stager": {"EXECUTE": ("StagePlan", "ReadPlanStage", "RecordValidation")},
    "FOCUS_FN_Signer": {"EXECUTE": ("AcceptPlan",)},
    "FOCUS_FN_Evaluator": {"EXECUTE": ("AppendEvaluation", "RecordAmendment", "RecordDeliveryObservation"),
                           "INSERT": ("Event", "ExcusalDecision", "ActualInterval", "DefectEvidence", "DisruptionClaim", "SequenceViolation",
                                      "ObservationRun", "ObservationFeedReceipt", "ObservationQuarantine", "IEReview", "RecognitionEvent", "BadgeAward")},
    "FOCUS_FN_ObservationProducer": {"INSERT": ("ObservationProducerReceipt", "LifecycleSource", "ActualIntervalSource", "DefectEvidenceSource",
                                                "GoalAmendmentSource", "DisruptionClaimSource", "ExcusalDecisionSource")},
    "FOCUS_FN_PipelineWorker": {"INSERT": ("PipelineRun", "PipelineStep", "Outbox", "WorkerRun"), "UPDATE": ("PipelineStep", "WorkerRun")},
    "FOCUS_FN_Retention": {"EXECUTE": ("PurgeTeamInference", "PurgeDurationEvidence")},
}
# separation of duties the grants must keep (S13, S17): procedure -> the only role that may execute it
EXCLUSIVE_EXECUTE = {"AcceptPlan": "FOCUS_FN_Signer", "StagePlan": "FOCUS_FN_Stager", "ReadPlanStage": "FOCUS_FN_Stager",
                     "RecordValidation": "FOCUS_FN_Stager", "PromoteSourceHead": "FOCUS_FN_SourceWorker",
                     "PurgeTeamInference": "FOCUS_FN_Retention", "PurgeDurationEvidence": "FOCUS_FN_Retention"}


def grants_script() -> str:
    """The reviewable role and grant script, generated from ROLE_GRANTS (one authority)."""
    lines = ["-- FOCUS_FN database roles and grants (P23), generated by focus.migrations.grants_script from ROLE_GRANTS.",
             "-- PROPOSED, NOT executed by this build. A DBA applies it after migrations 001-009 and then adds members, for example",
             "-- ALTER ROLE FOCUS_FN_AppReader ADD MEMBER [<app user>]. The app login is never a member of a source, stager, signer,",
             "-- evaluator or retention role. The installer is a DBA principal with DDL rights; no script grants DDL. Idempotent.",
             "SET XACT_ABORT ON;"]
    for role in sorted(ROLE_GRANTS):
        lines.append(f"IF DATABASE_PRINCIPAL_ID(N'{role}') IS NULL CREATE ROLE {role};")
    for role in sorted(ROLE_GRANTS):
        for permission in sorted(ROLE_GRANTS[role]):
            for name in sorted(ROLE_GRANTS[role][permission]):
                lines.append(f"GRANT {permission} ON {_FN}{name} TO {role};")
    return "\n".join(lines) + "\n"


def lint_grants(migrations: list[Migration], text: str | None = None) -> list[Finding]:
    """Grants name only shipped objects with fitting permissions; every procedure has an executor; duties stay separate."""
    findings: list[Finding] = []
    objects = {o.name: o.kind for m in migrations for o in m.objects if o.kind in ("TABLE", "VIEW", "PROCEDURE")}
    executors: dict[str, set[str]] = {}
    for role, perms in ROLE_GRANTS.items():
        if not role.startswith("FOCUS_FN_"):
            findings.append(Finding("GRANT_ROLE_NAME", 0, role, "roles carry the FOCUS_FN_ prefix"))
        for permission, names in perms.items():
            for name in names:
                kind = objects.get(_FN + name)
                if kind is None:
                    findings.append(Finding("GRANT_UNKNOWN_OBJECT", 0, f"{role}:{name}", "no shipped migration creates it"))
                elif (permission == "EXECUTE") != (kind == "PROCEDURE") or (permission in ("INSERT", "UPDATE") and kind != "TABLE"):
                    findings.append(Finding("GRANT_PERMISSION_KIND", 0, f"{role}:{name}", f"{permission} does not fit a {kind.lower()}"))
                if permission == "EXECUTE":
                    executors.setdefault(name, set()).add(role)
    for name, kind in objects.items():
        if kind == "PROCEDURE" and name[len(_FN):] not in executors:
            findings.append(Finding("GRANT_NO_EXECUTOR", 0, name, "every shipped procedure is granted to exactly the role that runs it"))
    for proc, role in EXCLUSIVE_EXECUTE.items():
        if executors.get(proc, set()) != {role}:
            findings.append(Finding("SEPARATION_OF_DUTIES", 0, proc, f"only {role} may execute it, granted to {sorted(executors.get(proc, set()))}"))
    if "EXECUTE" in ROLE_GRANTS.get("FOCUS_FN_AppReader", {}):
        findings.append(Finding("SEPARATION_OF_DUTIES", 0, "FOCUS_FN_AppReader", "the app reader executes nothing"))
    if text is not None and text != grants_script():
        findings.append(Finding("GRANTS_STALE", 0, "sql/grants/focus_fn_roles.sql", "regenerate with migrate --write-generated"))
    return findings


# ---------------------------------------------------------------------- catalogs and the plan


@dataclass
class CatalogState:
    objects: set[str] = field(default_factory=set)                      # SqlObject.key values installed
    ledger: dict[int, dict[str, Any]] = field(default_factory=dict)      # version -> {sha256, installed_at, installer}
    rows: dict[str, int] = field(default_factory=dict)                   # table -> row count (rollback guard)
    probed: bool = True


class Catalog(Protocol):
    def state(self) -> CatalogState: ...
    def apply(self, migration: Migration, *, installer: str, at: datetime) -> None: ...
    def rollback(self, migration: Migration, *, installer: str, at: datetime) -> None: ...


@dataclass(frozen=True)
class Step:
    version: int
    name: str
    sha256: str
    state: str
    detail: str = ""


def plan(migrations: list[Migration], state: CatalogState) -> list[Step]:
    steps: list[Step] = []
    blocked = False
    shipped = {m.version for m in migrations}
    for v in sorted(set(state.ledger) - shipped):
        steps.append(Step(v, "?", str(state.ledger[v].get("sha256", "")), "UNKNOWN_RECORDED_VERSION",
                          "the ledger records a version this build does not ship"))
        blocked = True
    owned: set[str] = set()
    for m in migrations:
        record = state.ledger.get(m.version)
        if record is not None:
            if record.get("sha256") != m.sha256:
                steps.append(Step(m.version, m.name, m.sha256, "CHECKSUM_MISMATCH", f"recorded {str(record.get('sha256'))[:12]} != script {m.sha256[:12]}"))
                blocked = True
            else:
                missing = [k for k in m.object_keys if k not in state.objects]
                if missing:
                    steps.append(Step(m.version, m.name, m.sha256, "DRIFT", f"{len(missing)} applied objects are missing: {', '.join(missing[:4])}"))
                    blocked = True
                else:
                    steps.append(Step(m.version, m.name, m.sha256, "APPLIED", str(record.get("installed_at", ""))))
            owned |= set(m.object_keys)
            continue
        if blocked:
            steps.append(Step(m.version, m.name, m.sha256, "BLOCKED", "an earlier step blocks every later version"))
            continue
        clash = sorted(k for k in m.object_keys if k in state.objects and k not in owned)
        if clash:
            steps.append(Step(m.version, m.name, m.sha256, "COLLISION", f"installed but unaccounted for: {', '.join(clash[:4])}"))
            blocked = True
        else:
            steps.append(Step(m.version, m.name, m.sha256, "PENDING", "" if state.probed else "catalog not probed: assumed empty"))
        owned |= set(m.object_keys)
    return steps


def unaccounted(migrations: list[Migration], state: CatalogState) -> list[str]:
    declared = {k for m in migrations for k in m.object_keys}
    tables = {o.name for m in migrations for o in m.objects if o.kind == "TABLE"}
    return sorted(k for k in state.objects if k not in declared
                  and not (k.startswith("COLUMN:") and k[len("COLUMN:"):].rsplit(".", 1)[0] in tables))


class FixtureCatalog:
    """A JSON catalog that rehearses the live protocol: atomic per version, verified, with durable attempt receipts."""

    def __init__(self, path: Path, *, fail_at: tuple[int, int] | None = None):
        self.path = Path(path)
        self.fail_at = fail_at                       # (version, object index): fail before creating that object

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "objects": [], "ledger": {}, "rows": {}, "attempts": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".catalog-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True, indent=1)
        os.replace(tmp, self.path)

    def state(self) -> CatalogState:
        data = self._load()
        return CatalogState(set(data["objects"]), {int(k): v for k, v in data["ledger"].items()}, dict(data.get("rows", {})))

    def set_rows(self, table: str, count: int) -> None:
        data = self._load()
        data.setdefault("rows", {})[table] = count
        self._save(data)

    def apply(self, migration: Migration, *, installer: str, at: datetime) -> None:
        data = self._load()
        working = set(data["objects"])
        attempt = {"version": migration.version, "sha256": migration.sha256, "installer": installer, "at": at.isoformat(), "kind": "APPLY"}
        for index, obj in enumerate(migration.objects):
            if self.fail_at == (migration.version, index):
                data["attempts"].append({**attempt, "outcome": "FAILED", "detail": f"injected failure before {obj.key}"})
                self._save(data)                     # only the receipt is durable: objects and ledger are unchanged
                raise MigrationError("APPLY_FAILED", f"migration {migration.version:03d} failed before {obj.key}; transaction rolled back")
            working.add(obj.key)
        missing = [k for k in migration.object_keys if k not in working]
        if missing:
            raise MigrationError("VERIFY_FAILED", f"declared objects missing after apply: {missing[:4]}")
        data["objects"] = sorted(working)
        data["ledger"][str(migration.version)] = {"sha256": migration.sha256, "installed_at": at.isoformat(), "installer": installer}
        data["attempts"].append({**attempt, "outcome": "APPLIED"})
        self._save(data)

    def rollback(self, migration: Migration, *, installer: str, at: datetime) -> None:
        data = self._load()
        keep = set(data["objects"]) - set(migration.object_keys)
        data["objects"] = sorted(keep)
        data["ledger"].pop(str(migration.version), None)
        data["attempts"].append({"version": migration.version, "sha256": migration.rollback_sha256, "installer": installer,
                                 "at": at.isoformat(), "kind": "ROLLBACK", "outcome": "ROLLED_BACK"})
        self._save(data)


class SqlCatalog:
    """Live catalog for an installer principal over pymssql. Never connected to a server by this build.

    ``state`` reads ``sys.objects``/``sys.indexes`` for the FOCUS_FN names and the ``FOCUS_FN_SchemaVersion`` ledger.
    ``apply`` runs one version in one transaction: application lock, every batch, verification of every declared
    object, then the ledger row; any error rolls the whole version back.
    """

    STATE_SQL = """
SELECT CASE o.type WHEN 'U' THEN 'TABLE' WHEN 'V' THEN 'VIEW' WHEN 'P' THEN 'PROCEDURE' WHEN 'TR' THEN 'TRIGGER'
       WHEN 'FN' THEN 'FUNCTION' WHEN 'IF' THEN 'FUNCTION' WHEN 'TF' THEN 'FUNCTION' ELSE 'CONSTRAINT' END,
       CASE WHEN o.type IN ('U','V','P','TR','FN','IF','TF') THEN s.name + '.' + o.name ELSE o.name END,
       ISNULL(OBJECT_SCHEMA_NAME(o.parent_object_id) + '.' + OBJECT_NAME(o.parent_object_id), '')
FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE s.name = 'WEB_APP' AND (o.name LIKE 'FOCUS[_]FN[_]%' OR o.name LIKE 'TR[_]FN[_]%' OR o.name LIKE 'CK[_]FN[_]%'
       OR o.name LIKE 'UX[_]FN[_]%' OR o.name LIKE 'FK[_]FN[_]%' OR o.name LIKE 'PK[_]FN[_]%' OR o.name LIKE 'DF[_]FN[_]%')
UNION ALL
SELECT 'INDEX', i.name, OBJECT_SCHEMA_NAME(i.object_id) + '.' + OBJECT_NAME(i.object_id)
FROM sys.indexes i WHERE i.name LIKE 'IX[_]FN[_]%' OR (i.name LIKE 'UX[_]FN[_]%' AND i.is_unique_constraint = 0)
UNION ALL
SELECT 'COLUMN', c.name, s.name + '.' + t.name
FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name = 'WEB_APP' AND t.name LIKE 'FOCUS[_]FN[_]%'
"""

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect

    def state(self) -> CatalogState:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(self.STATE_SQL)
            objects = set()
            for kind, name, parent in cur.fetchall():
                objects.add(SqlObject(kind, name, parent or "").key)
            ledger = {}
            if "TABLE:WEB_APP.FOCUS_FN_SchemaVersion" in objects:
                cur.execute("SELECT Version, ScriptSha, InstalledAtUtc, InstallerSubject FROM WEB_APP.FOCUS_FN_SchemaVersion")
                ledger = {int(v): {"sha256": sha, "installed_at": str(at), "installer": who} for v, sha, at, who in cur.fetchall()}
            return CatalogState(objects, ledger, {}, True)
        finally:
            conn.close()

    def apply(self, migration: Migration, *, installer: str, at: datetime) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("DECLARE @r int; EXEC @r = sp_getapplock @Resource = %s, @LockMode = 'Exclusive', @LockOwner = 'Transaction', "
                        "@LockTimeout = 30000; IF @r < 0 THROW 51900, 'FOCUS_FN_MIGRATIONS lock unavailable', 1;", (LOCK,))
            for batch in migration.batches:
                cur.execute(batch)
            cur.execute(self.STATE_SQL)
            present = {SqlObject(k, n, p or "").key for k, n, p in cur.fetchall()}
            missing = [k for k in migration.object_keys if k not in present]
            if missing:
                raise MigrationError("VERIFY_FAILED", f"declared objects missing after apply: {missing[:4]}")
            cur.execute("INSERT WEB_APP.FOCUS_FN_SchemaVersion(Version, ScriptSha, InstalledAtUtc, InstallerSubject) VALUES (%d, %s, %s, %s)",
                        (migration.version, migration.sha256, at.replace(tzinfo=None), installer))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def rollback(self, migration: Migration, *, installer: str, at: datetime) -> None:
        if migration.rollback_path is None:
            raise MigrationError("ROLLBACK_MISSING", f"no rollback script for {migration.version:03d}")
        conn = self._connect()
        try:
            cur = conn.cursor()
            for batch in batches(migration.rollback_path.read_text(encoding="utf-8")):
                cur.execute(batch)
            cur.execute("IF OBJECT_ID(N'WEB_APP.FOCUS_FN_SchemaVersion', N'U') IS NOT NULL "
                        "DELETE FROM WEB_APP.FOCUS_FN_SchemaVersion WHERE Version = %d", (migration.version,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------- protocol


def apply_pending(migrations: list[Migration], catalog: Catalog, *, installer: str, at: datetime) -> dict[str, Any]:
    findings = lint(migrations) + [f for m in migrations for f in lint_rollback(m)]
    if findings:
        return {"status": "refused", "reason": "LINT", "findings": [f.__dict__ for f in findings]}
    steps = plan(migrations, catalog.state())
    if any(s.state in BLOCKING for s in steps):
        return {"status": "refused", "reason": "PLAN_BLOCKED", "steps": [s.__dict__ for s in steps]}
    by_version = {m.version: m for m in migrations}
    applied: list[dict[str, Any]] = []
    for step in steps:
        if step.state != "PENDING":
            continue
        try:
            catalog.apply(by_version[step.version], installer=installer, at=at)
        except MigrationError as exc:
            return {"status": "error", "applied": applied, "failed": {"version": step.version, "code": exc.code, "message": exc.message},
                    "steps": [s.__dict__ for s in plan(migrations, catalog.state())]}
        applied.append({"version": step.version, "name": step.name, "sha256": step.sha256})
    return {"status": "ok", "applied": applied, "steps": [s.__dict__ for s in plan(migrations, catalog.state())]}


def rollback_latest(migrations: list[Migration], catalog: Catalog, version: int, *, installer: str, at: datetime) -> dict[str, Any]:
    state = catalog.state()
    if not state.ledger or max(state.ledger) != version:
        return {"status": "refused", "reason": "NOT_LATEST", "message": f"only the latest applied version ({max(state.ledger) if state.ledger else 'none'}) "
                                                                       "can be rolled back; recover forward instead"}
    m = next((x for x in migrations if x.version == version), None)
    if m is None or m.rollback_path is None:
        return {"status": "refused", "reason": "ROLLBACK_MISSING", "message": f"no rollback script for {version:03d}"}
    if state.ledger[version].get("sha256") != m.sha256:
        return {"status": "refused", "reason": "CHECKSUM_MISMATCH", "message": "the installed version is not this script"}
    holding = sorted(o.name for o in m.objects if o.kind == "TABLE" and state.rows.get(o.name, 0) > 0)
    if holding:
        return {"status": "refused", "reason": "ROLLBACK_REFUSED_DATA",
                "message": f"tables hold rows ({', '.join(holding)}); recover forward with a new migration instead"}
    catalog.rollback(m, installer=installer, at=at)
    return {"status": "ok", "rolled_back": version, "steps": [s.__dict__ for s in plan(migrations, catalog.state())]}


def manifest(migrations: list[Migration]) -> dict[str, Any]:
    return {"schema_version": MANIFEST_SCHEMA, "lock": LOCK, "migrations": [
        {"version": m.version, "name": m.name, "file": m.path.name, "sha256": m.sha256, "batches": len(m.batches),
         "objects": list(m.object_keys), "rollback": m.rollback_path.name if m.rollback_path else None, "rollback_sha256": m.rollback_sha256}
        for m in migrations]}


def emit_bundle(migrations: list[Migration], out: Path, grants: Path | None = None) -> dict[str, Any]:
    """A reviewable install bundle: scripts in order, rollbacks, grants and a checksum manifest (nothing is executed)."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "rollback").mkdir(exist_ok=True)
    for m in migrations:
        (out / m.path.name).write_bytes(m.path.read_bytes())
        if m.rollback_path is not None:
            (out / "rollback" / m.rollback_path.name).write_bytes(m.rollback_path.read_bytes())
    doc = manifest(migrations)
    if grants is not None and grants.exists():
        (out / grants.name).write_bytes(grants.read_bytes())
        doc["grants"] = {"file": grants.name, "sha256": sha256_bytes(grants.read_bytes())}
    (out / "MANIFEST.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def migrations_root(app_root: Path) -> Path:
    return app_root / "sql" / "migrations"


def all_findings(migrations: list[Migration], app_root: Path) -> list[Finding]:
    """Forward lint, rollback lint, generated-file freshness and the grant matrix."""
    findings = lint(migrations)
    for m in migrations:
        findings += lint_rollback(m)
        if m.rollback_path is not None and m.rollback_path.read_text(encoding="utf-8") != rollback_script(m):
            findings.append(Finding("ROLLBACK_STALE", m.version, m.rollback_path.name, "regenerate with migrate --write-generated"))
    grants = app_root / "sql" / "grants" / "focus_fn_roles.sql"
    findings += lint_grants(migrations, grants.read_text(encoding="utf-8") if grants.exists() else "")
    return findings


def manifest_sha256(migrations: list[Migration]) -> str:
    """The checksum an installer confirms: the canonical manifest of the exact scripts and rollbacks reviewed."""
    return sha256_bytes(json.dumps(manifest(migrations), sort_keys=True, separators=(",", ":")).encode("utf-8"))


def sql_connect(settings: Any) -> Callable[[], Any]:
    def connect() -> Any:
        import pymssql  # type: ignore[import-not-found]

        conn = pymssql.connect(server=settings.sql_host, database=settings.sql_database, user=settings.sql_user,
                               password=settings.sql_password.reveal() if settings.sql_password else None,
                               login_timeout=settings.sql_login_timeout_seconds, timeout=max(600, settings.sql_read_timeout_seconds))
        conn.autocommit(False)
        return conn
    return connect


def _live(services: Any, args: Any, migrations: list[Migration], findings: list[Finding]) -> dict[str, Any]:
    """Live catalog: a dry run reports what it can; apply or rollback only in an explicitly confirmed installer session.

    The installer passes its subject and the SHA-256 of the manifest it reviewed (``--confirm-manifest``). The
    principal must hold CREATE TABLE in the database. Nothing here runs implicitly, and this build never ran it against
    a server.
    """
    settings = services.settings
    wants_change = bool(getattr(args, "apply", False) or getattr(args, "rollback", None))
    if not settings.sql_configured:
        steps = plan(migrations, CatalogState(probed=False))
        if wants_change:
            return {"status": "refused", "error": {"code": "INSTALLER_SESSION_REQUIRED", "message": "no SQL credentials in this session"}}
        return {"status": "not_probed" if not findings else "invalid", "steps": [s.__dict__ for s in steps],
                "note": "live catalog not probed: no SQL credentials; every version is assumed pending"}
    catalog = SqlCatalog(sql_connect(settings))
    if not wants_change:
        state = catalog.state()
        steps = plan(migrations, state)
        blocked = findings or any(s.state in BLOCKING for s in steps)
        return {"status": "invalid" if blocked else "ok", "steps": [s.__dict__ for s in steps], "unaccounted": unaccounted(migrations, state)}
    expected = manifest_sha256(migrations)
    if not getattr(args, "installer", None) or getattr(args, "confirm_manifest", None) != expected:
        return {"status": "refused", "error": {"code": "INSTALL_NOT_CONFIRMED",
                "message": "live apply/rollback needs --installer <subject> and --confirm-manifest <sha256 of the reviewed manifest>"},
                "manifest_sha256": expected}
    conn = sql_connect(settings)()
    try:
        cur = conn.cursor()
        cur.execute("SELECT HAS_PERMS_BY_NAME(NULL, NULL, 'CREATE TABLE')")
        allowed = bool(cur.fetchone()[0])
    finally:
        conn.close()
    if not allowed:
        return {"status": "refused", "error": {"code": "INSTALLER_PRIVILEGE_MISSING", "message": "the session principal cannot CREATE TABLE"}}
    at = services.clock.now().astimezone(timezone.utc)
    if getattr(args, "rollback", None):
        return rollback_latest(migrations, catalog, int(args.rollback), installer=args.installer, at=at)
    return apply_pending(migrations, catalog, installer=args.installer, at=at)


def cli_migrate(services: Any, args: Any) -> dict[str, Any]:
    """``migrate --dry-run | --apply | --rollback N | --emit-dir DIR`` (fixture catalog by default; live needs an installer)."""
    app_root = Path(services.settings.app_root)
    try:
        migrations = discover(migrations_root(app_root))
    except MigrationError as exc:
        return {"status": "invalid", "error": {"code": exc.code, "message": exc.message}}
    grants_path = app_root / "sql" / "grants" / "focus_fn_roles.sql"
    result_extra: dict[str, Any] = {}
    if getattr(args, "write_generated", False):
        written = []
        targets = [(migrations_root(app_root) / "rollback" / m.path.name, rollback_script(m)) for m in migrations] + [(grants_path, grants_script())]
        for target_path, text in targets:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not target_path.exists() or target_path.read_text(encoding="utf-8") != text:
                target_path.write_text(text, encoding="utf-8")
                written.append(str(target_path.relative_to(app_root)))
        migrations = discover(migrations_root(app_root))
        result_extra["generated_written"] = written
    findings = all_findings(migrations, app_root)
    target = getattr(args, "catalog", None) or ("live" if services.settings.mode == "live" else "fixture")
    result: dict[str, Any] = {"migrations": manifest(migrations)["migrations"], "manifest_sha256": manifest_sha256(migrations),
                              "findings": [f.__dict__ for f in findings], "catalog": target, **result_extra}
    if getattr(args, "emit_dir", None):
        result["bundle"] = emit_bundle(migrations, Path(args.emit_dir), app_root / "sql" / "grants" / "focus_fn_roles.sql")
        result["bundle_dir"] = str(Path(args.emit_dir))
    if target == "live":
        return {**result, **_live(services, args, migrations, findings)}
    catalog = FixtureCatalog(Path(target) if target not in ("fixture",) else services.settings.runtime_dir() / "sql_catalog.json")
    at = services.clock.now().astimezone(timezone.utc)
    installer = getattr(args, "installer", None) or "fixture-installer"
    if getattr(args, "rollback", None):
        return {**result, **rollback_latest(migrations, catalog, int(args.rollback), installer=installer, at=at)}
    if getattr(args, "apply", False):
        return {**result, **apply_pending(migrations, catalog, installer=installer, at=at)}
    steps = plan(migrations, catalog.state())
    blocked = findings or any(s.state in BLOCKING for s in steps)
    return {**result, "status": "invalid" if blocked else "ok", "steps": [s.__dict__ for s in steps],
            "unaccounted": unaccounted(migrations, catalog.state())}
