"""Static checks over queries/: reader queries stay read-only, placeholders are never hidden in comments (P12 and earlier)."""
from __future__ import annotations

import re

import pytest

from focus.data.sql import SqlReader, SqlUnavailable

WORKER_WRITES = {"durations/build_observations.sql", "durations/percentiles.sql", "workforce/infer_mechanic_teams.sql"}


def _queries(app_root):
    root = app_root / "queries"
    return [(p.relative_to(root).as_posix(), p.read_text(encoding="utf-8")) for p in sorted(root.rglob("*.sql"))]


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_reader_queries_are_read_only_and_writers_are_declared(app_root) -> None:
    seen = set()
    for rel, sql in _queries(app_root):
        if rel in WORKER_WRITES:                                                   # worker-owned writes never run through the reader
            with pytest.raises(SqlUnavailable):
                SqlReader.check_read_only(sql)
            seen.add(rel)
        else:
            SqlReader.check_read_only(sql)
    assert seen == WORKER_WRITES


def test_no_bound_placeholder_hides_in_a_comment(app_root) -> None:
    for rel, sql in _queries(app_root):
        comments = [line.split("--", 1)[1] for line in sql.splitlines() if "--" in line]
        assert not any("%s" in c for c in comments), rel                           # pymssql would bind it client-side


def test_economics_query_mirrors_the_policy_precedence(app_root, policy) -> None:
    sql = dict(_queries(app_root))["economics/effective_terms.sql"]
    ranks = {m.group(1): int(m.group(2)) for m in re.finditer(r"WHEN '([A-Z_]+)' THEN (\d)", sql)}
    assert [s for s, _ in sorted(ranks.items(), key=lambda kv: kv[1])] == list(policy.seq("economics.selector_precedence"))
    assert _code(sql).count("%s") == 6 and "CONTRACT_ASSIGNMENT_CONFLICT" in sql and "CapFraction" in sql
    assert _code(dict(_queries(app_root))["economics/liability_floor.sql"]).count("%s") == 1
