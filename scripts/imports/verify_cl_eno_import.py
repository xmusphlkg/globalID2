"""Read-only reconciliation of Chile ENO source rows, artifacts and database."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.core.database import dispose_database, get_db
from src.services.ingestion.artifacts import read_artifact


def identity(row):
    return (row["Date"], row["SourceDiseaseCode"], row["DefinitionVersion"],
            row["GeographyKey"], json.dumps(json.loads(row["Dimensions"]), ensure_ascii=False, sort_keys=True))


async def verify():
    csv_path = ROOT / "data/current/cl/chile_eno_historical.csv"
    receipt = json.loads((ROOT / "data/current/cl/database_import_receipt.json").read_text())
    manifest_path = Path(receipt["source_manifest"])
    manifest = json.loads(manifest_path.read_text())
    expected = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    expected_by_key = {identity(row): row for row in expected}
    assert len(expected) == len(expected_by_key) == receipt["records"] == 442
    assert sum(int(row["Cases"]) for row in expected) == receipt["source_audit"]["source_case_rows"] == 333300
    assert len(manifest["artifacts"]) == receipt["artifacts"] == 5
    for artifact in manifest["artifacts"]:
        read_artifact(SimpleNamespace(**artifact))
    raw_hash = receipt["source_audit"]["raw_sha256"]
    assert all(row["RawSHA256"] == raw_hash and row["PublicReleaseEnabled"] == "false" for row in expected)

    try:
        async with get_db() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            task = (await db.execute(text("SELECT status, progress FROM tasks WHERE task_uuid=:task"),
                                     {"task": receipt["task_uuid"]})).mappings().one()
            assert task["status"].lower() == "completed" and task["progress"] == 100
            actual = (await db.execute(text("""SELECT o.time, o.value, o.geography_key, o.dimensions,
                       o.raw_data, o.metadata, o.unit, o.quality_status,
                       s.source_series_code, s.definition_version, s.metric_type, s.reporting_basis,
                       s.temporal_granularity, s.disease_id, s.mapping_relation, s.aggregation_policy,
                       s.metadata AS series_metadata, c.source_code AS category_code
                       FROM disease_series_observations o
                       JOIN disease_surveillance_series s ON s.series_code=o.series_code
                       JOIN source_disease_categories c ON c.id=(s.metadata->>'source_category_id')::bigint
                       WHERE s.country_code='CL' AND s.source_system='SRC_CL_ENO'"""))).mappings().all()
            assert len(actual) == len(expected)
            seen = Counter()
            mismatches = []
            for observed in actual:
                native = observed["raw_data"]
                key = identity(native)
                seen[key] += 1
                source = expected_by_key.get(key)
                if source is None:
                    mismatches.append({"key": key[:4], "error": "unmatched identity"})
                    continue
                checks = {
                    "full_raw_row": native == source,
                    "year": observed["time"].date().isoformat() == source["Date"],
                    "count": observed["value"] == float(source["Cases"]),
                    "geography": observed["geography_key"] == source["GeographyKey"],
                    "dimensions": observed["dimensions"] == json.loads(source["Dimensions"]),
                    "source_category": observed["source_series_code"] == observed["category_code"] == source["SourceDiseaseCode"],
                    "definition": observed["definition_version"] == source["DefinitionVersion"],
                    "metric": observed["metric_type"] == source["Measure"],
                    "basis": observed["reporting_basis"] == source["ReportingBasis"],
                    "frequency": observed["temporal_granularity"] == source["Frequency"],
                    "holding": observed["series_metadata"].get("dynamic_holding") is True,
                    "unmapped": observed["disease_id"] is None and observed["mapping_relation"] == "unmapped",
                    "non_additive": observed["aggregation_policy"] == "non_additive",
                }
                failures = [name for name, passed in checks.items() if not passed]
                if failures:
                    mismatches.append({"key": key[:4], "failures": failures})
            assert not mismatches, mismatches[:10]
            assert seen == Counter(expected_by_key.keys())
            registered = (await db.execute(text("""SELECT relative_path, sha256, byte_size,
                       source_url, kind, metadata AS metadata_ FROM source_artifacts
                       WHERE task_uuid=:task"""), {"task": receipt["task_uuid"]})).mappings().all()
            assert len(registered) == len(manifest["artifacts"])
            artifact_key = lambda item: (item["sha256"], item["byte_size"], item["source_url"], item["kind"], item["metadata_"].get("endpoint"))
            assert Counter(map(artifact_key, registered)) == Counter(map(artifact_key, manifest["artifacts"]))
            for artifact in registered:
                read_artifact(SimpleNamespace(**dict(artifact)))
    finally:
        await dispose_database()

    result = {"status": "accepted", "verified_at": datetime.now(timezone.utc).isoformat(),
              "task_uuid": receipt["task_uuid"], "database_rows": len(actual),
              "database_case_sum": int(sum(row["value"] for row in actual)),
              "row_by_row_mismatches": 0, "registered_raw_artifacts": len(registered),
              "source_years": sorted(receipt["source_audit"]["years_in_csv"]),
              "source_eno_categories": receipt["source_audit"]["source_eno_categories"],
              "raw_csv_sha256": raw_hash,
              "review_csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
              "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "public_release_enabled": False, "database_read_only_transaction": True}
    target = ROOT / "data/current/cl/database_acceptance.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(verify())
