from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_iceland_integration import (
    AUTOMATION_JOBS_SQL,
    COMPATIBILITY_PROJECTION_SQL,
    EXPECTED_AUTOMATION_JOBS,
    EXPECTED_COMPATIBILITY_PROJECTION,
    EXPECTED_HISTORY_SERIES_ROWS,
    EXPECTED_MAPPING_SEMANTICS,
    EXPECTED_OBSERVATION_COUNT,
    EXPECTED_PROJECTION_COUNT,
    EXPECTED_QUARANTINE_BY_REASON,
    EXPECTED_QUARANTINE_IDENTITIES,
    EXPECTED_RAW_FILE_COUNT,
    EXPECTED_SERIES_COUNT,
    EXPECTED_SOURCE_SUMMARIES,
    EXPECTED_ZERO_QUALITY_FIELDS,
    SERIES_REGISTRY_SQL,
    SOURCE_SUMMARY_SQL,
    AuditCheck,
    audit_local_artifacts,
    render_report,
    run_audit,
    validate_automation_jobs,
    validate_compatibility_projection,
    validate_country_json,
    validate_database_sources,
    validate_download_manifest,
    validate_disease_mappings,
    validate_history_artifacts,
    validate_raw_manifest,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _database_source_rows() -> list[dict]:
    return [
        {
            "source_system": source_system,
            "series_count": values["series_count"],
            "observation_count": values["observation_count"],
            "coverage_start": date.fromisoformat(values["coverage_start"]),
            "coverage_end": date.fromisoformat(values["coverage_end"]),
            **{field: 0 for field in EXPECTED_ZERO_QUALITY_FIELDS},
        }
        for source_system, values in EXPECTED_SOURCE_SUMMARIES.items()
    ]


def _automation_rows() -> list[dict]:
    return [
        {"job_id": job_id, **values}
        for job_id, values in EXPECTED_AUTOMATION_JOBS.items()
    ]


def _database_registry_rows() -> list[dict]:
    return []


def _compatibility_projection_rows() -> list[dict]:
    return [
        {
            **EXPECTED_COMPATIBILITY_PROJECTION,
            "unregistered_series_count": 0,
            "concept_mismatch_count": 0,
            "unsafe_relation_count": 0,
            "registered_diagnosis_count": 0,
            "competing_narrower_count": 0,
            "null_cases_count": 0,
            "negative_cases_count": 0,
        }
    ]


def _write_raw_archive(root: Path) -> Path:
    raw_dir = root / "data/raw/is/history"
    raw_dir.mkdir(parents=True)
    entries = []
    for index in range(EXPECTED_RAW_FILE_COUNT):
        content = f"official workbook {index}".encode()
        filename = f"history-{index}.xlsx"
        path = raw_dir / filename
        path.write_bytes(content)
        entries.append(
            {
                "key": f"history_{index}",
                "filename": filename,
                "path": filename,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest_path = raw_dir / "raw_manifest.json"
    _write_json(
        manifest_path,
        {"catalogue_file_count": EXPECTED_RAW_FILE_COUNT, "files": entries},
    )
    return manifest_path


def _quarantine_identity_specs() -> list[tuple[str, str, int]]:
    annual_counts = [13, 13, 12, 12]
    legacy_counts = [53, 53] + [54] * 96
    return [
        ("unreviewed_annual_disease", f"annual-{index}", count)
        for index, count in enumerate(annual_counts)
    ] + [
        ("unreviewed_legacy_icd_series", f"legacy-{index}", count)
        for index, count in enumerate(legacy_counts)
    ]


def _write_history_artifacts(root: Path) -> tuple[Path, Path, Path]:
    history_dir = root / "data/current/is/history"
    history_dir.mkdir(parents=True)
    raw_dir = root / "data/raw/is/history"
    raw_dir.mkdir(parents=True, exist_ok=True)
    file_entries = []
    for index in range(EXPECTED_RAW_FILE_COUNT):
        raw_path = raw_dir / f"history-{index}.xlsx"
        content = f"official workbook {index}".encode()
        raw_path.write_bytes(content)
        file_entries.append(
            {
                "filename": raw_path.name,
                "path": f"../../../raw/is/history/{raw_path.name}",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    series_path = history_dir / "series_rows.csv"
    with series_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["SourceSeriesCode"])
        writer.writeheader()
        writer.writerows(
            {"SourceSeriesCode": f"SER-{index % EXPECTED_SERIES_COUNT}"}
            for index in range(EXPECTED_HISTORY_SERIES_ROWS)
        )

    quarantine_path = history_dir / "quarantine.csv"
    identity_rows = []
    with quarantine_path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["reason", "raw_label_is", "raw_label_en", "icd10"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for reason, label, row_count in _quarantine_identity_specs():
            identity = {
                "reason": reason,
                "raw_label_is": label,
                "raw_label_en": f"en-{label}",
                "icd10": f"icd-{label}",
            }
            identity_rows.append({**identity, "row_count": row_count})
            writer.writerows(identity for _ in range(row_count))

    manifest_path = history_dir / "manifest.json"
    _write_json(
        manifest_path,
        {
            "files": file_entries,
            "counts": {
                "series_rows": EXPECTED_HISTORY_SERIES_ROWS,
                "quarantine_rows": sum(EXPECTED_QUARANTINE_BY_REASON.values()),
            },
            "quarantine": {
                "row_count": sum(EXPECTED_QUARANTINE_BY_REASON.values()),
                "by_reason": EXPECTED_QUARANTINE_BY_REASON,
                "identities": identity_rows,
            },
        },
    )
    return manifest_path, series_path, quarantine_path


def _write_download_manifest(root: Path) -> Path:
    path = root / "exports/site-downloads/manifest.json"
    source_parts = [2000, 2000, 2000, 2000, 1396]
    projection_parts = [600, 600, 600, 600, 615]
    parts = [
        {
            "source_observation_count": source_count,
            "projection_record_count": projection_count,
            "record_count": source_count + projection_count,
        }
        for source_count, projection_count in zip(source_parts, projection_parts)
    ]
    _write_json(
        path,
        {
            "schema_version": 4,
            "countries": [
                {
                    "code": "IS",
                    "source_series_count": EXPECTED_SERIES_COUNT,
                    "source_observation_count": EXPECTED_OBSERVATION_COUNT,
                    "projection_record_count": EXPECTED_PROJECTION_COUNT,
                    "record_count": EXPECTED_OBSERVATION_COUNT
                    + EXPECTED_PROJECTION_COUNT,
                    "parts": parts,
                }
            ],
        },
    )
    return path


def _write_country_json(root: Path) -> Path:
    path = root / "astro-site/src/data/countries/is.json"
    counts = [75] * (EXPECTED_SERIES_COUNT - 1) + [96]
    source_series = []
    for index, count in enumerate(counts):
        source_series.append(
            {
                "series_code": f"SER-{index}",
                "observation_count": count,
                "dates": ["2020-01-01"] * count,
                "values": [0] * count,
            }
        )
    assert sum(counts) == EXPECTED_OBSERVATION_COUNT
    _write_json(
        path,
        {
            "country_code": "IS",
            "disease_series": {"D001": {"source_series": source_series}},
        },
    )
    return path


def _write_all_local_artifacts(root: Path) -> None:
    _write_raw_archive(root)
    _write_history_artifacts(root)
    _write_download_manifest(root)
    _write_country_json(root)


def test_database_queries_are_select_only() -> None:
    for statement in (
        SOURCE_SUMMARY_SQL,
        COMPATIBILITY_PROJECTION_SQL,
        AUTOMATION_JOBS_SQL,
        SERIES_REGISTRY_SQL,
    ):
        sql = str(statement).strip().upper()
        assert sql.startswith("SELECT")
        assert all(
            word not in sql
            for word in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ")
        )


def test_database_source_and_automation_validators_accept_exact_snapshot() -> None:
    source_check = validate_database_sources(_database_source_rows())
    compatibility_check = validate_compatibility_projection(
        _compatibility_projection_rows()
    )
    jobs_check = validate_automation_jobs(_automation_rows())

    assert source_check.passed
    assert source_check.detail == "125 series / 9396 observations / 5 source systems"
    assert compatibility_check.passed
    assert compatibility_check.detail.startswith("3791 rows")
    assert jobs_check.passed
    assert jobs_check.detail == "3 exact Iceland job configurations"


def test_database_validators_report_count_and_job_drift() -> None:
    source_rows = _database_source_rows()
    source_rows[0]["observation_count"] -= 1
    compatibility_rows = _compatibility_projection_rows()
    compatibility_rows[0]["competing_narrower_count"] = 1
    job_rows = _automation_rows()
    job_rows[0]["fill_missing"] = True

    source_check = validate_database_sources(source_rows)
    compatibility_check = validate_compatibility_projection(compatibility_rows)
    jobs_check = validate_automation_jobs(job_rows)

    assert not source_check.passed
    assert any("observation_count" in error for error in source_check.errors)
    assert not compatibility_check.passed
    assert any("competing_narrower_count" in error for error in compatibility_check.errors)
    assert not jobs_check.passed
    assert any("fill_missing" in error for error in jobs_check.errors)


def test_raw_manifest_verifies_path_size_and_sha256(tmp_path: Path) -> None:
    manifest_path = _write_raw_archive(tmp_path)

    assert validate_raw_manifest(manifest_path).passed

    first_file = manifest_path.parent / "history-0.xlsx"
    first_file.write_bytes(b"tampered")
    failed = validate_raw_manifest(manifest_path)
    assert not failed.passed
    assert any("history-0.xlsx: size" in error for error in failed.errors)
    assert any("history-0.xlsx: SHA256" in error for error in failed.errors)


def test_history_artifacts_validate_rows_reasons_and_identities(tmp_path: Path) -> None:
    manifest_path, series_path, quarantine_path = _write_history_artifacts(tmp_path)

    check = validate_history_artifacts(manifest_path, series_path, quarantine_path)

    assert check.passed
    assert "7234 series rows" in check.detail
    assert "5340 quarantined rows" in check.detail
    assert "22/22 portable raw paths" in check.detail
    assert str(EXPECTED_QUARANTINE_IDENTITIES) in check.detail

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "/machine-specific/history-0.xlsx"
    _write_json(manifest_path, manifest)
    failed = validate_history_artifacts(manifest_path, series_path, quarantine_path)
    assert not failed.passed
    assert any("must be relative" in error for error in failed.errors)


def test_generated_download_and_country_counts(tmp_path: Path) -> None:
    download_path = _write_download_manifest(tmp_path)
    country_path = _write_country_json(tmp_path)

    assert validate_download_manifest(download_path).passed
    assert validate_country_json(country_path).passed

    payload = json.loads(country_path.read_text(encoding="utf-8"))
    payload["disease_series"]["D001"]["source_series"][0]["observation_count"] = 74
    _write_json(country_path, payload)
    failed = validate_country_json(country_path)
    assert not failed.passed
    assert any("source observation_count sum" in error for error in failed.errors)


def test_disease_mapping_audit_covers_config_normalized_rows_and_database(
    tmp_path: Path,
) -> None:
    ontology_path = tmp_path / "configs/disease_ontology.json"
    mapping_path = tmp_path / "configs/mapping/is.csv"
    history_path = tmp_path / "data/current/is/history/series_rows.csv"
    reviewed_ids = list(EXPECTED_MAPPING_SEMANTICS)
    concept_ids = [
        "D002", "D008", "D038", "D100", "D224", "D237",
        *[f"DX{index:03d}" for index in range(62)],
    ]
    series = []
    for index, series_code in enumerate(reviewed_ids):
        concept_id, relation, comparability, aggregation = (
            EXPECTED_MAPPING_SEMANTICS[series_code]
        )
        series.append(
            {
                "id": series_code,
                "source_id": "SRC_TEST_IS",
                "concept_id": concept_id,
                "local_codes": [f"CODE_{index}"],
                "local_labels": [f"Label {index}"],
                "frequency": "monthly",
                "measure": "case_notifications",
                "reporting_basis": "notification",
                "unit": "count",
                "mapping_relation": relation,
                "comparability": comparability,
                "aggregation_policy": aggregation,
                "missing_value_policy": "missing_is_unknown",
                "status": "historical",
            }
        )
    for index in range(EXPECTED_SERIES_COUNT - len(series)):
        position = len(series)
        series.append(
            {
                "id": f"SER_SYN_{index:03d}",
                "source_id": "SRC_TEST_IS",
                "concept_id": concept_ids[index % len(concept_ids)],
                "local_codes": [f"CODE_{position}"],
                "local_labels": [f"Label {position}"],
                "frequency": "monthly",
                "measure": "case_notifications",
                "reporting_basis": "notification",
                "unit": "count",
                "mapping_relation": "exact",
                "comparability": "conditional",
                "aggregation_policy": "non_additive",
                "missing_value_policy": "missing_is_unknown",
                "status": "historical",
            }
        )
    _write_json(
        ontology_path,
        {
            "sources": [{"id": "SRC_TEST_IS", "country_code": "IS"}],
            "source_series": series,
        },
    )

    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["disease_id", "local_name", "local_code", "source_id", "series_id"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for definition in series[:22]:
            writer.writerow(
                {
                    "disease_id": definition["concept_id"],
                    "local_name": definition["local_labels"][0],
                    "local_code": definition["local_codes"][0],
                    "source_id": definition["source_id"],
                    "series_id": definition["id"],
                }
            )

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "SourceSeriesCode", "DiseaseFull", "SourceId", "Frequency",
            "Measure", "Unit", "DiseaseCode",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(EXPECTED_HISTORY_SERIES_ROWS):
            definition = series[index % len(series)]
            writer.writerow(
                {
                    "SourceSeriesCode": definition["id"],
                    "DiseaseFull": definition["concept_id"],
                    "SourceId": definition["source_id"],
                    "Frequency": definition["frequency"],
                    "Measure": definition["measure"],
                    "Unit": definition["unit"],
                    "DiseaseCode": definition["local_codes"][0],
                }
            )

    database_rows = [
        {
            "series_code": definition["id"],
            "disease_id": definition["concept_id"],
            "source_system": definition["source_id"],
            "source_series_code": definition["id"],
            "source_label": definition["local_labels"][0],
            "metric_type": definition["measure"],
            "reporting_basis": definition["reporting_basis"],
            "temporal_granularity": definition["frequency"],
            "unit": definition["unit"],
            "mapping_relation": definition["mapping_relation"],
            "comparability": definition["comparability"],
            "aggregation_policy": definition["aggregation_policy"],
            "availability_status": "available",
            "missing_value_policy": definition["missing_value_policy"],
            "is_active": False,
        }
        for definition in series
    ]

    check = validate_disease_mappings(
        ontology_path, mapping_path, history_path, database_rows
    )

    assert check.passed, check.errors
    assert "125 series / 68 disease targets" in check.detail
    database_rows[0]["mapping_relation"] = "exact"
    failed = validate_disease_mappings(
        ontology_path, mapping_path, history_path, database_rows
    )
    assert not failed.passed
    assert any("mapping_relation" in error for error in failed.errors)


@pytest.mark.asyncio
async def test_full_audit_uses_injected_database_loader_and_temp_files(
    tmp_path: Path,
) -> None:
    _write_all_local_artifacts(tmp_path)
    calls = 0

    async def database_loader():
        nonlocal calls
        calls += 1
        return (
            _database_source_rows(),
            _automation_rows(),
            _database_registry_rows(),
            _compatibility_projection_rows(),
        )

    def mapping_validator(*_args):
        return AuditCheck(
            name="disease mapping registry",
            detail="125 synthetic series",
        )

    checks = await run_audit(
        root=tmp_path,
        database_loader=database_loader,
        mapping_validator=mapping_validator,
    )

    assert calls == 1
    assert len(checks) == 8
    assert all(check.passed for check in checks)
    report = render_report(checks)
    assert "Summary: PASS (8/8 checks passed)" in report
    assert "no database rows or files were changed" in report


def test_local_audit_turns_missing_files_into_clear_failures(tmp_path: Path) -> None:
    checks = audit_local_artifacts(tmp_path)

    assert len(checks) == 4
    assert not any(check.passed for check in checks)
    assert all(check.errors for check in checks)
