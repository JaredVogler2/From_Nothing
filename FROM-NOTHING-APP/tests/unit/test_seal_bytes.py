"""``codec.seal_bytes`` writes exactly ``seal(...).to_bytes()`` from a payload already encoded by ``canonical_dumps``."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from fractions import Fraction

import pytest

import focus.codec as codec
from focus.codec import canonical_dumps, open_envelope, seal, seal_bytes


@dataclass(frozen=True)
class _Row:
    name: str
    at: datetime
    share: Fraction
    tags: tuple[str, ...]
    note: str | None = None


PAYLOADS = [
    {"plain": 1, "nested": {"z": [1, 2, {"é": "ü"}], "a": None}},
    _Row("Línea 1203 — ✓", datetime(2026, 11, 1, 9, 30, tzinfo=timezone(timedelta(hours=-5))), Fraction(7, 3), ("b", "a")),
    [1, "two", None, True, 3.5],
    'a string with "quotes", a \\ backslash and a\ttab',
]


@pytest.fixture(params=["native", "python"])
def encoder(request):
    saved = codec.native_codec()
    if request.param == "native":
        if not codec.enable_native_codec(True):
            pytest.skip("native encoder not installed")
    else:
        codec.enable_native_codec(False)
    yield request.param
    codec._CANON = saved


@pytest.mark.parametrize("payload", PAYLOADS, ids=["dict", "dataclass", "list", "string"])
@pytest.mark.parametrize("meta", [{}, {"source_run_id": "SR-1", "policy_hash": "a" * 64, "purpose": "FORECAST"}], ids=["bare", "bound"])
def test_seal_bytes_equals_seal(encoder, payload, meta) -> None:
    created = datetime(2026, 11, 1, 20, 0, 1, 250000, tzinfo=UTC)
    expected = seal("Typed", "ID-1", payload, created_at=created, **meta).to_bytes()
    got = seal_bytes("Typed", "ID-1", canonical_dumps(payload), created_at=created, **meta)
    assert got == expected
    assert open_envelope(got, "Typed").payload == open_envelope(expected, "Typed").payload       # and it opens with its hash checked
