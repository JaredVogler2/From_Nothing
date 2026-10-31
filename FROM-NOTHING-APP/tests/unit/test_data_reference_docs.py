"""The data source reference (docs/data) covers every input FOCUS reads, column for column.

Each dataset in ``DATASETS`` and each observation feed in ``FEEDS`` has exactly one ``## `name``` section, and the first
table under it headed ``Column`` lists exactly its columns in projection order. A new, renamed, reordered or dropped
column fails here until the reference says what it is and what FOCUS does with it.
"""
from __future__ import annotations

import re
from pathlib import Path

from focus.data.datasets import DATASETS
from focus.performance.actuals import FEEDS

DOCS = Path(__file__).resolve().parents[2] / "docs" / "data"
HEADING = re.compile(r"^## `([a-z0-9_]+)`\s*$")
COLUMN_ROW = re.compile(r"^\| `([A-Za-z0-9_]+)` \|")


def _documented() -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {}
    for path in sorted(DOCS.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            heading = HEADING.match(line)
            if not heading:
                continue
            name = heading.group(1)
            assert name not in found, f"{name} is documented twice ({path.name})"
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("#")), len(lines))
            start = next((j for j in range(i + 1, end) if lines[j].startswith("| Column |")), None)
            assert start is not None, f"{path.name}: {name} has no column table"
            columns = []
            for row in lines[start + 2:end]:
                if not row.startswith("|"):
                    break
                match = COLUMN_ROW.match(row)
                assert match, f"{path.name}: {name}: not a column row: {row[:80]!r}"
                columns.append(match.group(1))
            found[name] = tuple(columns)
    return found


def test_every_input_has_exactly_one_section():
    assert set(_documented()) == set(DATASETS) | {feed.name for feed in FEEDS}


def test_each_column_table_matches_the_projection_in_order():
    documented = _documented()
    for spec in (*DATASETS.values(), *FEEDS):
        assert documented[spec.name] == spec.columns, spec.name


def test_the_index_lists_every_input():
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    missing = [name for name in (*DATASETS, *(feed.name for feed in FEEDS)) if f"`{name}`" not in index]
    assert not missing, missing
