"""Export a SOURCE-CODE-ONLY manifest for mapping FOCUS inputs inside a company.

This script reads checked-in source contract metadata and query FILE TEXT. It
must never connect to live systems or load fixtures, employee records or SQL
credentials. Mapping slots are intentionally BLANK. Completed company mappings
must be kept in company-controlled infrastructure, not committed to this repo.

Run from FROM-NOTHING-APP:
  python scripts/export_integration_contract.py
  python scripts/export_integration_contract.py --output /safe/internal/location/reference.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def catalog() -> dict:
    # Load only schema metadata. No Settings, environment variables, database
    # connections, fixture readers, acquisition or app startup is involved.
    from focus.data.datasets import DATASETS
    from focus.data.discovery import CONTRACTS, CONTRACTS_SHA
    from focus.data.fixture_reader import LIVE_SUPPLEMENTAL_QUERIES

    source_contracts = []
    for name, table in sorted(CONTRACTS.items()):
        source_contracts.append({
            "reference_object": name,
            "reference_primary_key": list(table.primary_key),
            "reference_columns": [col.render() for col in table.columns],
        })

    datasets = []
    for name, spec in sorted(DATASETS.items()):
        sql_path = (
            spec.query_file if spec.kind == "SOURCE"
            else LIVE_SUPPLEMENTAL_QUERIES.get(name)
        )
        text = None
        if sql_path is not None:
            query_file = ROOT / sql_path
            if not query_file.is_file():
                raise FileNotFoundError(f"Missing checked-in query: {sql_path}")
            text = query_file.read_text(encoding="utf-8")
        datasets.append({
            "canonical_dataset": name,
            "kind": spec.kind,
            "reference_source_object": spec.source_object,
            "required_for_source_run": spec.required_for_run,
            "capabilities": list(spec.capabilities),
            "required_columns_in_order": list(spec.columns),
            "reference_column_types": [
                CONTRACTS[spec.source_object].column(col).render()
                for col in spec.columns
            ] if spec.source_object else None,
            "cutoff_column": spec.cutoff_column,
            "description": spec.description,
            "checked_in_reference_query": sql_path,
            "checked_in_query_sha256": sha256_text(text) if text else None,
            "live_reader_support": (
                "REFERENCE_SQL" if spec.kind == "SOURCE"
                else "SUPPLEMENTAL_QUERY_PATH" if sql_path
                else "NOT_PROBED_UNTIL_PRODUCER_IMPLEMENTED"
            ),
            "COMPANY_PRIVATE_MAPPING_TEMPLATE_DO_NOT_FILL_IN_PUBLIC_REPO": {
                "source_owner": None,
                "approved_internal_source": None,
                "approved_internal_query_path": None,
                "approved_query_sha256": None,
                "canonical_field_alias_map": None,
                "source_grain_primary_key": None,
                "version_and_dedupe_policy": None,
                "status_semantics": None,
                "time_zone_and_cutoff_policy": None,
                "completeness_receipt_authority": None,
                "source_access_approval": None,
                "availability": "NOT_MAPPED"
            }
        })

    queries = []
    for file in sorted((ROOT / "queries").rglob("*.sql")):
        relative = file.relative_to(ROOT).as_posix()
        src = file.read_text(encoding="utf-8")
        queries.append({
            "path": relative,
            "sha256": sha256_text(src),
            "top_comment": next(
                (ln.strip() for ln in src.splitlines() if ln.strip()), ""
            )[:320],
            "category": relative.split("/")[1],
            "execution_status": (
                "REFERENCE_PROJECTION" if relative.startswith("queries/source_inputs/")
                else "REVIEW_BEFORE_USE"
            )
        })
    return {
        "schema_version": 1,
        "notice": "PUBLIC REFERENCE-ONLY; NO LIVE DATA. Do not commit completed internal mappings.",
        "generated_from": {
            "dataset_registry": "focus/data/datasets.py",
            "source_contracts": "focus/data/discovery.py",
            "live_reader": "focus/data/fixture_reader.py",
            "sql_queries": "queries/**/*.sql",
            "reference_contracts_sha256": CONTRACTS_SHA
        },
        "counts": {
            "datasets": len(datasets),
            "sql_backed_datasets": sum(d["kind"] == "SOURCE" for d in datasets),
            "supplemental_datasets": sum(d["kind"] == "SUPPLEMENTAL" for d in datasets),
            "reference_objects": len(source_contracts),
            "sql_query_files": len(queries)
        },
        "datasets": datasets,
        "reference_source_contracts": source_contracts,
        "checked_in_query_catalog": queries
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="docs/integration/contract_manifest.generated.json",
        help="Safe reference-only JSON path. Private mapping must be stored separately."
    )
    args = parser.parse_args()
    target = Path(args.output)
    if not target.is_absolute():
        target = ROOT / target
    if target.is_relative_to(ROOT) and target.name.endswith(".private.json"):
        parser.error("Refusing to generate a privately mapped file in this source repository.")
    manifest = catalog()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "reference_only": True,
        "output": str(target),
        "counts": manifest["counts"]
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
