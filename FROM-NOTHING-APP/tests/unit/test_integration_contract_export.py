"""The confidential workplace handoff must remain complete and data-free."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _exporter():
    path = ROOT / "scripts" / "export_integration_contract.py"
    spec = importlib.util.spec_from_file_location("focus_export_integration_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_manifest_lists_every_source_without_live_inputs():
    from focus.data.datasets import DATASETS
    from focus.data.discovery import CONTRACTS

    doc = _exporter().catalog()
    assert doc["counts"]["datasets"] == len(DATASETS)
    assert len(doc["datasets"]) == len(DATASETS)
    assert doc["counts"]["reference_objects"] == len(CONTRACTS)
    assert {d["canonical_dataset"] for d in doc["datasets"]} == set(DATASETS)
    assert {d["kind"] for d in doc["datasets"]} == {"SOURCE", "SUPPLEMENTAL"}
    assert all(d["required_columns_in_order"] == list(DATASETS[d["canonical_dataset"]].columns)
               for d in doc["datasets"])
    assert all(d["COMPANY_PRIVATE_MAPPING_TEMPLATE_DO_NOT_FILL_IN_PUBLIC_REPO"]["availability"] == "NOT_MAPPED"
               for d in doc["datasets"])


def test_every_sql_projection_is_cataloged_by_hash_and_path():
    doc = _exporter().catalog()
    queries = {q["path"]: q for q in doc["checked_in_query_catalog"]}
    for d in doc["datasets"]:
        query = d["checked_in_reference_query"]
        if d["kind"] == "SOURCE":
            assert query == "queries/source_inputs/" + d["canonical_dataset"] + ".sql"
            assert query in queries
            assert d["checked_in_query_sha256"] == queries[query]["sha256"]
        if query:
            assert query in queries
    assert doc["counts"]["sql_query_files"] == len(queries)


def test_machine_manifest_is_reference_only_json(tmp_path):
    doc = _exporter().catalog()
    dumped = json.dumps(doc)
    assert "COMPANY_PRIVATE_MAPPING_TEMPLATE_DO_NOT_FILL_IN_PUBLIC_REPO" in dumped
    assert "FOCUS_SQL_PASSWORD" not in dumped
    assert "NOT_MAPPED" in dumped
    p = tmp_path / "contract.json"
    p.write_text(dumped, encoding="utf-8")
    assert json.loads(p.read_text(encoding="utf-8"))["counts"] == doc["counts"]
