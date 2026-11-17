#!/usr/bin/env python3
"""Independently reconcile Argentina HIV CSV/XLSX and stored annual facts.

This verifier parses publisher bytes directly without the ingestion parser.
It reads PostgreSQL in a repeatable-read, read-only transaction and never
changes observations, mappings, release gates or publication pointers.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIELDS = ("id_sexo", "sexo", "id_jurisdiccion", "jurisdiccion", "anio", "casos_vih")
SOURCE_ID = "SRC_AR_MOH_HIV_ANNUAL"
SOURCE_CODE = "hiv"
DEFINITION = "ar_moh_hiv_annual_notifications_v1"
DIMENSIONS = {
    "dataset": "casos_prov_vih", "sex": "all", "source_sex_id": "3",
    "source_sex_label": "Ambos sexos", "source_jurisdiction_label": "ARG",
    "source_jurisdiction_code": "200", "date_basis": "calendar_year",
}
INITIAL_RESOURCE_VALUES = dict(zip(range(2014, 2024), (6654, 6747, 6375, 5218, 6027, 6311, 3991, 5772, 6168, 6588)))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def inspect_source(csv_path: Path, xlsx_path: Path) -> tuple[dict, dict[int, dict]]:
    from openpyxl import load_workbook

    raw = csv_path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("cp850")), delimiter=";")
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError("Unexpected HIV CSV columns")
    csv_rows = [{key: cell(row[key]) for key in FIELDS} for row in reader]
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) != 1:
            raise ValueError("Expected a single HIV XLSX data sheet")
        rows = iter(workbook.worksheets[0].values)
        if tuple(cell(value) for value in next(rows)) != FIELDS:
            raise ValueError("Unexpected HIV XLSX columns")
        xlsx_rows = [dict(zip(FIELDS, map(cell, row))) for row in rows]
    finally:
        workbook.close()
    if csv_rows != xlsx_rows:
        raise ValueError("CSV and XLSX source rows differ")
    identities = [tuple(row[key] for key in FIELDS[:-1]) for row in csv_rows]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate source identities in HIV files")
    selected = {}
    for number, row in enumerate(csv_rows, 2):
        if tuple(row[key] for key in FIELDS[:4]) != ("3", "Ambos sexos", "ARG", "200"):
            continue
        year, value = int(row["anio"]), int(row["casos_vih"])
        if value < 0 or year in selected:
            raise ValueError("Invalid or duplicate national annual value")
        selected[year] = {"value": value, "csv_row": number, "source_fields": row}
    values = {year: row["value"] for year, row in selected.items()}
    if values != INITIAL_RESOURCE_VALUES:
        raise ValueError("Current 2014–2023 resource differs from independently reviewed values; review the new source version")
    return {
        "csv": {"path": str(csv_path), "sha256": sha(csv_path), "bytes": len(raw), "encoding": "cp850", "delimiter": ";"},
        "xlsx": {"path": str(xlsx_path), "sha256": sha(xlsx_path), "bytes": xlsx_path.stat().st_size},
        "all_source_rows": len(csv_rows), "csv_xlsx_all_rows_equal": True,
        "source_duplicate_identities": 0, "national_selection": dict(zip(FIELDS[:4], ("3", "Ambos sexos", "ARG", "200"))),
        "national_values": [{"year": year, **row} for year, row in sorted(selected.items())],
        "national_values_aggregated": False,
    }, selected


async def inspect_database(selected: dict[int, dict], source: dict, task_uuid: str | None) -> dict:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from src.core.config import get_config

    engine = create_async_engine(get_config().database.url)
    try:
        async with AsyncSession(engine) as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '90s'"))
            rows = (await db.execute(text("""
                SELECT obs.time, obs.value, obs.geography_key, obs.dimension_key,
                       obs.dimensions, obs.raw_data, obs.suppressed, obs.unit,
                       obs.series_code, series.source_series_code, series.definition_version,
                       series.metric_type, series.reporting_basis, series.temporal_granularity
                FROM disease_series_observations obs
                JOIN disease_surveillance_series series USING (series_code)
                WHERE series.country_code='AR' AND series.source_system=:source
                ORDER BY obs.time, obs.series_code, obs.dimension_key
            """), {"source": SOURCE_ID})).mappings().all()
            issues, actual, identities = [], {}, set()
            for record in rows:
                row = dict(record)
                raw = row["raw_data"] or {}
                year = row["time"].year
                identity = (row["time"].isoformat(), row["series_code"], row["geography_key"], row["dimension_key"])
                if identity in identities or year in actual:
                    issues.append({"year": year, "issue": "duplicate annual observation"})
                identities.add(identity)
                actual[year] = row
                expected = selected.get(year)
                expected_fields = {
                    "source_series_code": SOURCE_CODE, "definition_version": DEFINITION,
                    "metric_type": "case_notifications", "reporting_basis": "annual_hiv_notifications",
                    "temporal_granularity": "annual", "geography_key": "country:AR:national",
                    "dimensions": DIMENSIONS, "suppressed": False,
                }
                mismatches = [key for key, value in expected_fields.items() if row[key] != value]
                if not expected or row["value"] != expected["value"] or row["time"].date().isoformat() != f"{year}-01-01":
                    mismatches.append("source year/value/date")
                expected_raw = {
                    "Date": f"{year}-01-01", "PeriodStart": f"{year}-01-01", "PeriodEnd": f"{year}-12-31",
                    "SourceDiseaseCode": SOURCE_CODE, "DefinitionVersion": DEFINITION,
                    "GeographyKey": "country:AR:national", "Measure": "case_notifications",
                    "ReportingBasis": "annual_hiv_notifications", "Frequency": "annual",
                    "RevisionSemantics": "authoritative_revision", "RawSHA256": source["csv"]["sha256"],
                    "CrosscheckXLSXSHA256": source["xlsx"]["sha256"],
                    "DatasetStatus": "closed_revisable", "AuthoritativeRevision": "true",
                }
                mismatches += ["raw." + key for key, value in expected_raw.items() if raw.get(key) != value]
                if expected and int(raw.get("Cases", -1)) != expected["value"]:
                    mismatches.append("raw.Cases")
                if expected:
                    if raw.get("SourceRowNumber") != str(expected["csv_row"]):
                        mismatches.append("raw.SourceRowNumber")
                    try:
                        original_fields = json.loads(raw.get("SourceRawRecord", "{}"))
                    except (TypeError, ValueError):
                        original_fields = None
                    if original_fields != expected["source_fields"]:
                        mismatches.append("raw.SourceRawRecord")
                if mismatches:
                    issues.append({"year": year, "mismatched_fields": mismatches})
            revisions = (await db.execute(text("""
                SELECT DISTINCT ON (rev.time, rev.series_code, rev.geography_key, rev.dimension_key)
                       rev.time, rev.series_code, rev.geography_key, rev.dimension_key,
                       rev.value, rev.dimensions, rev.raw_data, rev.recorded_at
                FROM disease_series_observation_revisions rev
                JOIN disease_surveillance_series series USING (series_code)
                WHERE series.country_code='AR' AND series.source_system=:source
                ORDER BY rev.time, rev.series_code, rev.geography_key, rev.dimension_key, rev.recorded_at DESC
            """), {"source": SOURCE_ID})).mappings().all()
            revision_issues = []
            for rev in revisions:
                current = actual.get(rev["time"].year)
                if current is None or any(rev[key] != current[key] for key in ("time", "series_code", "geography_key", "dimension_key", "value", "dimensions", "raw_data")):
                    revision_issues.append(rev["time"].date().isoformat())
            artifacts = []
            if task_uuid:
                artifacts = [dict(row) for row in (await db.execute(text("""
                    SELECT kind, sha256, relative_path, byte_size FROM source_artifacts
                    WHERE task_uuid=:task ORDER BY id
                """), {"task": task_uuid})).mappings().all()]
                hashes = {row["sha256"] for row in artifacts}
                for kind in ("csv", "xlsx"):
                    if source[kind]["sha256"] not in hashes:
                        issues.append({"issue": f"task lacks matching original {kind} artifact"})
                for item in artifacts:
                    path = ROOT / item["relative_path"]
                    if not path.is_file() or path.stat().st_size != item["byte_size"] or sha(path) != item["sha256"]:
                        issues.append({"issue": "raw task artifact does not match stored size/hash", "path": item["relative_path"]})
            missing = sorted(set(selected) - set(actual))
            extra = sorted(set(actual) - set(selected))
            passed = len(rows) == len(selected) and not (issues or missing or extra or revision_issues) and len(revisions) == len(rows)
            await db.rollback()
            return {
                "mode": "repeatable_read_read_only", "task_uuid": task_uuid,
                "source_id": SOURCE_ID, "stored_observations": len(rows),
                "stored_series": sorted({row["series_code"] for row in rows}),
                "missing_years": missing, "unexpected_years": extra, "issues": issues,
                "current_revision_ledger_entries": len(revisions), "revision_ledger_matches_current_rows": not revision_issues and len(revisions) == len(rows),
                "revision_issues": revision_issues, "captured_task_artifacts": artifacts,
                "passed": passed,
            }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default = ROOT / "data/current/ar/alternative_source_discovery"
    parser.add_argument("--csv", type=Path, default=default / "hiv2023.csv")
    parser.add_argument("--xlsx", type=Path, default=default / "hiv2023.xlsx")
    parser.add_argument("--task-uuid")
    parser.add_argument("--source-only", action="store_true", help="Verify original files without accessing PostgreSQL")
    parser.add_argument("--output", type=Path, default=ROOT / "data/current/ar/hiv_annual_database_acceptance.json")
    args = parser.parse_args()
    source, selected = inspect_source(args.csv.resolve(), args.xlsx.resolve())
    report = {
        "schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "mode": "source_only" if args.source_only else "source_database_reconciliation",
        "database_verified": not args.source_only,
        "verifier_sha256": sha(Path(__file__)), "published": False,
        "source": source,
    }
    if not args.source_only:
        report["database"] = asyncio.run(inspect_database(selected, source, args.task_uuid))
    report["passed"] = report.get("database", {"passed": True})["passed"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "national_rows": len(selected), "all_source_rows": source["all_source_rows"], "database_rows": report.get("database", {}).get("stored_observations"), "published": False}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
