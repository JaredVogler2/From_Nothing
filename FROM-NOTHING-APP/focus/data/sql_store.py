"""Live repository store over SQL Server (S06, P23): honest unavailability until the FOCUS_FN schema is installed.

The live write path stores the S06 records in the proposed ``WEB_APP.FOCUS_FN_*`` tables (migrations 001-009). This
build installed none of them, and the per-collection mapping of the repository protocol onto those tables is the
pending live adapter (STATUS gaps).

So ``SqlStore`` refuses every write and reads nothing, with one precise, typed reason. It never connects at import
or construction: the web app starts, ``/health/ready`` and every view show typed blocked states, and nothing pretends
to be stored. Whether the schema is installed is checked separately and read-only, by
``python -m focus.cli preflight --probe-sql`` (the version ledger against the shipped checksums). The source reader
(``focus.data.sql.SqlReader``) is unaffected: acquisition still reads the verified S04 tables.
"""
from __future__ import annotations

from typing import Any

from .store import UnavailableStore

REASON = ("Live FOCUS_FN storage is not available: migrations 001-009 must be applied by an authorized installer "
          "(python -m focus.cli migrate --emit-dir, then the confirmed installer session in docs/execution/INSTALL_AND_PIPELINE.md), "
          "and the live repository adapter over those tables is pending. Nothing is stored until both exist.")


class SqlStore(UnavailableStore):
    kind = "sql_pending"

    def __init__(self, settings: Any):
        super().__init__(REASON)
        self.host = settings.sql_host                     # identifies the target in diagnostics; never the credentials
        self.database = settings.sql_database
