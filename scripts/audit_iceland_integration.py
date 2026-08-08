#!/usr/bin/env python3
"""Read-only final audit for the Iceland surveillance integration.

The command deliberately performs only ``SELECT`` statements against the
configured database and reads checked/local artifacts from disk.  It does not
initialize schemas, commit a session, regenerate data, or publish anything.

Run from the repository root::

    venv/bin/python scripts/audit_iceland_integration.py

Exit status is zero only when every Iceland integration invariant matches the
reviewed snapshot recorded below.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from sqlalchemy import bindparam, text


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.configure_iceland_automation import JOB_VALUES  # noqa: E402
from src.core.database import get_engine  # noqa: E402


EXPECTED_SOURCE_SUMMARIES: dict[str, dict[str, Any]] = {
    "SRC_IS_DOH_ANNUAL": {
        "series_count": 14,
        "observation_count": 209,
        "coverage_start": "2010-01-01",
        "coverage_end": "2025-01-01",
    },
    "SRC_IS_DOH_HISTORY": {
        "series_count": 73,
        "observation_count": 4369,
        "coverage_start": "1997-01-01",
        "coverage_end": "2021-12-01",
    },
    "SRC_IS_DOH_LEGACY_ICD": {
        "series_count": 30,
        "observation_count": 2865,
        "coverage_start": "1997-01-01",
        "coverage_end": "2020-12-01",
    },
    "SRC_IS_DOH_RESPIRATORY": {
        "series_count": 5,
        "observation_count": 1581,
        "coverage_start": "2019-07-01",
        "coverage_end": "2026-05-04",
    },
    "SRC_IS_DOH_STI": {
        "series_count": 3,
        "observation_count": 372,
        "coverage_start": "2016-01-01",
        "coverage_end": "2026-06-01",
    },
}

EXPECTED_SERIES_COUNT = 125
EXPECTED_CONCEPT_COUNT = 68
EXPECTED_OBSERVATION_COUNT = 9396
EXPECTED_PROJECTION_COUNT = 3015
EXPECTED_COMPATIBILITY_PROJECTION = {
    "total_count": 3791,
    "current_count": 103,
    "history_annual_count": 763,
    "history_monthly_count": 2925,
}
EXPECTED_RAW_FILE_COUNT = 22
EXPECTED_HISTORY_SERIES_ROWS = 7234
EXPECTED_QUARANTINE_BY_REASON = {
    "unreviewed_annual_disease": 50,
    "unreviewed_legacy_icd_series": 5290,
}
EXPECTED_QUARANTINE_IDENTITIES = {
    "unreviewed_annual_disease": 4,
    "unreviewed_legacy_icd_series": 98,
}
EXPECTED_AUTOMATION_JOBS = {
    job_id: dict(values) for job_id, values in JOB_VALUES.items()
}

EXPECTED_MAPPING_SEMANTICS = {
    "SER_IS_HISTORY_PANDEMIC_INFLUENZA_A_H1N1_2009_ANNUAL": (
        "D038", "narrower", "conditional", "non_additive"
    ),
    "SER_IS_HISTORY_INFLUENZA_A_H3_ANNUAL": (
        "D038", "narrower", "conditional", "non_additive"
    ),
    "SER_IS_HISTORY_HIB_ANNUAL": (
        "D100", "narrower", "conditional", "non_additive"
    ),
    "SER_IS_HISTORY_INVASIVE_HAEMOPHILUS_INFLUENZAE_ANNUAL": (
        "D100", "narrower", "conditional", "non_additive"
    ),
    "SER_IS_HISTORY_ESBL_AMPC_MONTHLY": (
        "D237", "narrower", "not_comparable", "non_additive"
    ),
    "SER_IS_HISTORY_ESBL_AMPC_ANNUAL": (
        "D237", "exact", "not_comparable", "reported_aggregate"
    ),
    "SER_IS_HISTORY_HEPATITIS_B_COMBINED_ANNUAL": (
        "D008", "related", "not_comparable", "reported_aggregate"
    ),
    "SER_IS_HISTORY_HEPATITIS_B_COMBINED_MONTHLY": (
        "D008", "related", "not_comparable", "non_additive"
    ),
    "SER_IS_HISTORY_CHOLERA_ANNUAL": (
        "D002", "broader", "not_comparable", "reported_aggregate"
    ),
    "SER_IS_LEGACY_ICD_J09_J10_J10_8_U05_9_STADFEST_INFLUENSA_MONTHLY": (
        "D038", "narrower", "not_comparable", "non_additive"
    ),
    "SER_IS_LEGACY_ICD_J02_0_J03_0_STREP_PHARYNGITIS_LIST_MONTHLY": (
        "D224", "related", "not_comparable", "non_additive"
    ),
    "SER_IS_LEGACY_ICD_J02_0_J03_0_STREP_PHARYNGITIS_RANGE_MONTHLY": (
        "D224", "related", "not_comparable", "non_additive"
    ),
}

EXPECTED_ZERO_QUALITY_FIELDS = (
    "negative_count",
    "fractional_count",
    "null_value_count",
    "unit_mismatch_count",
    "bad_grain_count",
)


SOURCE_SUMMARY_SQL = text(
    """
    SELECT
        series.source_system,
        COUNT(DISTINCT observation.series_code) AS series_count,
        COUNT(observation.id) AS observation_count,
        MIN(observation.time) AS coverage_start,
        MAX(observation.time) AS coverage_end,
        SUM(CASE WHEN observation.value < 0 THEN 1 ELSE 0 END) AS negative_count,
        SUM(CASE WHEN observation.value <> FLOOR(observation.value) THEN 1 ELSE 0 END)
            AS fractional_count,
        SUM(CASE WHEN observation.value IS NULL THEN 1 ELSE 0 END) AS null_value_count,
        SUM(CASE WHEN observation.unit <> series.unit THEN 1 ELSE 0 END)
            AS unit_mismatch_count,
        SUM(
            CASE
                WHEN series.temporal_granularity = 'annual'
                     AND (EXTRACT(MONTH FROM observation.time) <> 1
                          OR EXTRACT(DAY FROM observation.time) <> 1) THEN 1
                WHEN series.temporal_granularity = 'monthly'
                     AND EXTRACT(DAY FROM observation.time) <> 1 THEN 1
                WHEN series.temporal_granularity = 'weekly'
                     AND EXTRACT(ISODOW FROM observation.time) <> 1 THEN 1
                ELSE 0
            END
        ) AS bad_grain_count
    FROM disease_series_observations AS observation
    JOIN disease_surveillance_series AS series
      ON series.series_code = observation.series_code
    WHERE series.country_code = 'IS'
    GROUP BY series.source_system
    ORDER BY series.source_system
    """
)

SERIES_REGISTRY_SQL = text(
    """
    SELECT
        series_code, disease_id, source_system, source_series_code,
        source_label, metric_type, reporting_basis, temporal_granularity,
        unit, mapping_relation, comparability, aggregation_policy,
        availability_status, missing_value_policy, is_active
    FROM disease_surveillance_series
    WHERE country_code = 'IS'
    ORDER BY series_code
    """
)

COMPATIBILITY_PROJECTION_SQL = text(
    """
    SELECT
        COUNT(*) AS total_count,
        COUNT(*) FILTER (
            WHERE record.metadata::jsonb ->> 'legacy_projection' =
                  'current_annual_dashboard_only'
        ) AS current_count,
        COUNT(*) FILTER (
            WHERE record.metadata::jsonb ->> 'source_kind' = 'registry_annual'
        ) AS history_annual_count,
        COUNT(*) FILTER (
            WHERE record.metadata::jsonb ->> 'source_kind' =
                  'registry_disease_monthly'
        ) AS history_monthly_count,
        COUNT(*) FILTER (WHERE series.series_code IS NULL)
            AS unregistered_series_count,
        COUNT(*) FILTER (WHERE series.disease_id <> disease.name)
            AS concept_mismatch_count,
        COUNT(*) FILTER (
            WHERE series.mapping_relation IN (
                'broader', 'related', 'aggregate', 'ambiguous', 'unmapped'
            )
        ) AS unsafe_relation_count,
        COUNT(*) FILTER (WHERE series.metric_type = 'registered_diagnoses')
            AS registered_diagnosis_count,
        COUNT(*) FILTER (
            WHERE series.series_code IN (
                'SER_IS_HISTORY_HIB_ANNUAL',
                'SER_IS_HISTORY_INVASIVE_HAEMOPHILUS_INFLUENZAE_ANNUAL',
                'SER_IS_HISTORY_PANDEMIC_INFLUENZA_A_H1N1_2009_ANNUAL',
                'SER_IS_HISTORY_INFLUENZA_A_H3_ANNUAL'
            )
        ) AS competing_narrower_count,
        COUNT(*) FILTER (WHERE record.cases IS NULL) AS null_cases_count,
        COUNT(*) FILTER (WHERE record.cases < 0) AS negative_cases_count
    FROM disease_records AS record
    JOIN countries AS country ON country.id = record.country_id
    JOIN diseases AS disease ON disease.id = record.disease_id
    LEFT JOIN disease_surveillance_series AS series
      ON series.series_code = record.metadata::jsonb ->> 'source_series_code'
    WHERE country.code = 'IS'
    """
)

AUTOMATION_JOBS_SQL = text(
    """
    SELECT
        job_id, name, country_code, source, enabled, priority, process,
        save_raw, fill_missing, force, retry_threshold, interval_minutes,
        daily_time, timezone, notes
    FROM automation_jobs
    WHERE country_code = :country_code OR job_id IN :job_ids
    ORDER BY job_id
    """
).bindparams(bindparam("job_ids", expanding=True))


@dataclass(frozen=True)
class AuditCheck:
    """One independently reportable audit result."""

    name: str
    detail: str
    errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "errors": list(self.errors),
        }


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    value_text = str(value).strip()
    if not value_text:
        return None
    return value_text[:10]


def _int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mapping_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def validate_database_sources(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected: Mapping[str, Mapping[str, Any]] = EXPECTED_SOURCE_SUMMARIES,
) -> AuditCheck:
    """Validate the five source-system aggregates returned by the SELECT."""

    errors: list[str] = []
    actual: dict[str, dict[str, Any]] = {}
    for row in _mapping_rows(rows):
        source_system = str(row.get("source_system") or "")
        if not source_system:
            errors.append("database returned a row without source_system")
            continue
        if source_system in actual:
            errors.append(f"database returned duplicate source_system {source_system}")
            continue
        actual[source_system] = {
            "series_count": _int_value(row.get("series_count")),
            "observation_count": _int_value(row.get("observation_count")),
            "coverage_start": _date_text(row.get("coverage_start")),
            "coverage_end": _date_text(row.get("coverage_end")),
            **{
                field: _int_value(row.get(field))
                for field in EXPECTED_ZERO_QUALITY_FIELDS
            },
        }

    expected_sources = set(expected)
    actual_sources = set(actual)
    if actual_sources != expected_sources:
        missing = sorted(expected_sources - actual_sources)
        unexpected = sorted(actual_sources - expected_sources)
        if missing:
            errors.append(f"missing source systems: {', '.join(missing)}")
        if unexpected:
            errors.append(f"unexpected source systems: {', '.join(unexpected)}")

    for source_system in sorted(expected_sources & actual_sources):
        for field, expected_value in expected[source_system].items():
            actual_value = actual[source_system].get(field)
            if actual_value != expected_value:
                errors.append(
                    f"{source_system}.{field}: expected {expected_value!r}, "
                    f"got {actual_value!r}"
                )
        for field in EXPECTED_ZERO_QUALITY_FIELDS:
            actual_value = actual[source_system].get(field)
            if actual_value != 0:
                errors.append(
                    f"{source_system}.{field}: expected 0, got {actual_value!r}"
                )

    total_series = sum(
        value.get("series_count") or 0 for value in actual.values()
    )
    total_observations = sum(
        value.get("observation_count") or 0 for value in actual.values()
    )
    if total_series != EXPECTED_SERIES_COUNT:
        errors.append(
            f"database total series: expected {EXPECTED_SERIES_COUNT}, "
            f"got {total_series}"
        )
    if total_observations != EXPECTED_OBSERVATION_COUNT:
        errors.append(
            "database total observations: expected "
            f"{EXPECTED_OBSERVATION_COUNT}, got {total_observations}"
        )

    return AuditCheck(
        name="database source observations",
        detail=(
            f"{total_series} series / {total_observations} observations / "
            f"{len(actual)} source systems"
        ),
        errors=tuple(errors),
    )


def validate_compatibility_projection(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected: Mapping[str, int] = EXPECTED_COMPATIBILITY_PROJECTION,
) -> AuditCheck:
    """Ensure the lossy compatibility table contains only safe projections."""

    materialized = _mapping_rows(rows)
    if len(materialized) != 1:
        return AuditCheck(
            name="safe compatibility projection",
            detail=f"{len(materialized)} aggregate rows returned",
            errors=(f"expected one aggregate row, got {len(materialized)}",),
        )

    row = materialized[0]
    errors: list[str] = []
    for field, expected_value in expected.items():
        actual_value = _int_value(row.get(field))
        if actual_value != expected_value:
            errors.append(
                f"{field}: expected {expected_value}, got {actual_value!r}"
            )
    zero_fields = (
        "unregistered_series_count",
        "concept_mismatch_count",
        "unsafe_relation_count",
        "registered_diagnosis_count",
        "competing_narrower_count",
        "null_cases_count",
        "negative_cases_count",
    )
    for field in zero_fields:
        actual_value = _int_value(row.get(field))
        if actual_value != 0:
            errors.append(f"{field}: expected 0, got {actual_value!r}")

    return AuditCheck(
        name="safe compatibility projection",
        detail=(
            f"{_int_value(row.get('total_count'))} rows; "
            f"current={_int_value(row.get('current_count'))}, "
            f"history annual={_int_value(row.get('history_annual_count'))}, "
            f"history monthly={_int_value(row.get('history_monthly_count'))}"
        ),
        errors=tuple(errors),
    )


def validate_automation_jobs(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected: Mapping[str, Mapping[str, Any]] = EXPECTED_AUTOMATION_JOBS,
) -> AuditCheck:
    """Validate the exact persistent configuration for all Iceland jobs."""

    errors: list[str] = []
    actual: dict[str, dict[str, Any]] = {}
    for row in _mapping_rows(rows):
        job_id = str(row.get("job_id") or "")
        if not job_id:
            errors.append("database returned an automation job without job_id")
            continue
        if job_id in actual:
            errors.append(f"database returned duplicate automation job {job_id}")
            continue
        actual[job_id] = row

    expected_ids = set(expected)
    actual_ids = set(actual)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        if missing:
            errors.append(f"missing automation jobs: {', '.join(missing)}")
        if unexpected:
            errors.append(f"unexpected Iceland automation jobs: {', '.join(unexpected)}")

    for job_id in sorted(expected_ids & actual_ids):
        for field, expected_value in expected[job_id].items():
            actual_value = actual[job_id].get(field)
            if actual_value != expected_value:
                errors.append(
                    f"{job_id}.{field}: expected {expected_value!r}, "
                    f"got {actual_value!r}"
                )

    return AuditCheck(
        name="automation jobs",
        detail=f"{len(actual)} exact Iceland job configurations",
        errors=tuple(errors),
    )


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_manifest_path(manifest_path: Path, raw_path: Any) -> Path:
    candidate = Path(str(raw_path or ""))
    if not str(candidate):
        return manifest_path.parent
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_path.parent / candidate).resolve()


def validate_raw_manifest(manifest_path: Path) -> AuditCheck:
    """Check every archived workbook against the raw manifest."""

    manifest_path = Path(manifest_path)
    manifest = _load_json(manifest_path)
    errors: list[str] = []
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        return AuditCheck(
            name="historical raw archive",
            detail="invalid manifest",
            errors=(f"{manifest_path}: files must be a list",),
        )

    declared_count = _int_value(manifest.get("catalogue_file_count"))
    if declared_count != EXPECTED_RAW_FILE_COUNT:
        errors.append(
            f"catalogue_file_count: expected {EXPECTED_RAW_FILE_COUNT}, "
            f"got {declared_count!r}"
        )
    if len(files) != EXPECTED_RAW_FILE_COUNT:
        errors.append(
            f"manifest file entries: expected {EXPECTED_RAW_FILE_COUNT}, "
            f"got {len(files)}"
        )

    seen_paths: set[Path] = set()
    verified = 0
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            errors.append(f"files[{index}] is not an object")
            continue
        label = str(entry.get("filename") or entry.get("key") or f"files[{index}]")
        raw_path = entry.get("path")
        if not raw_path:
            errors.append(f"{label}: missing path")
            continue
        resolved = _resolve_manifest_path(manifest_path, raw_path)
        if resolved in seen_paths:
            errors.append(f"{label}: duplicate resolved path {resolved}")
            continue
        seen_paths.add(resolved)
        if not resolved.is_file():
            errors.append(f"{label}: file not found at {resolved}")
            continue

        expected_size = _int_value(entry.get("size_bytes"))
        actual_size = resolved.stat().st_size
        if expected_size != actual_size:
            errors.append(
                f"{label}: size expected {expected_size!r}, got {actual_size}"
            )
        expected_hash = str(entry.get("sha256") or "").lower()
        actual_hash = _sha256(resolved)
        if expected_hash != actual_hash:
            errors.append(
                f"{label}: SHA256 expected {expected_hash or '<missing>'}, "
                f"got {actual_hash}"
            )
        if expected_size == actual_size and expected_hash == actual_hash:
            verified += 1

    return AuditCheck(
        name="historical raw archive",
        detail=f"{verified}/{len(files)} workbooks match path, size, and SHA256",
        errors=tuple(errors),
    )


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_disease_mappings(
    ontology_path: Path,
    mapping_path: Path,
    series_rows_path: Path,
    database_rows: Iterable[Mapping[str, Any]],
) -> AuditCheck:
    """Cross-check reviewed source semantics from config through the database."""

    document = _load_json(Path(ontology_path))
    errors: list[str] = []
    sources = {
        str(item.get("id") or ""): item
        for item in document.get("sources", [])
        if isinstance(item, dict)
    }
    ontology_rows = [
        item
        for item in document.get("source_series", [])
        if isinstance(item, dict)
        and sources.get(str(item.get("source_id") or ""), {}).get("country_code")
        == "IS"
    ]
    ontology_by_code = {
        str(item.get("id") or ""): item for item in ontology_rows
    }
    if len(ontology_rows) != EXPECTED_SERIES_COUNT:
        errors.append(
            f"ontology Iceland series: expected {EXPECTED_SERIES_COUNT}, "
            f"got {len(ontology_rows)}"
        )
    if len(ontology_by_code) != len(ontology_rows):
        errors.append("ontology contains duplicate or empty Iceland series IDs")
    concept_ids = {
        str(item.get("concept_id") or "")
        for item in ontology_rows
        if item.get("concept_id")
    }
    if len(concept_ids) != EXPECTED_CONCEPT_COUNT:
        errors.append(
            f"ontology Iceland disease targets: expected {EXPECTED_CONCEPT_COUNT}, "
            f"got {len(concept_ids)}"
        )

    for series_code, expected in EXPECTED_MAPPING_SEMANTICS.items():
        definition = ontology_by_code.get(series_code)
        if definition is None:
            errors.append(f"reviewed semantic series missing: {series_code}")
            continue
        actual = (
            str(definition.get("concept_id") or ""),
            str(definition.get("mapping_relation") or ""),
            str(definition.get("comparability") or ""),
            str(definition.get("aggregation_policy") or ""),
        )
        if actual != expected:
            errors.append(
                f"{series_code} semantics: expected {expected!r}, got {actual!r}"
            )

    current_rows = _csv_rows(Path(mapping_path))
    if len(current_rows) != 22:
        errors.append(f"current mapping rows: expected 22, got {len(current_rows)}")
    current_series_ids: set[str] = set()
    for row in current_rows:
        series_code = str(row.get("series_id") or "")
        if series_code in current_series_ids:
            errors.append(f"duplicate current mapping series_id: {series_code}")
            continue
        current_series_ids.add(series_code)
        definition = ontology_by_code.get(series_code)
        if definition is None:
            errors.append(f"current mapping references unknown series: {series_code}")
            continue
        expected_values = {
            "disease_id": str(definition.get("concept_id") or ""),
            "source_id": str(definition.get("source_id") or ""),
        }
        for field, expected_value in expected_values.items():
            if str(row.get(field) or "") != expected_value:
                errors.append(
                    f"{series_code}.{field}: expected {expected_value!r}, "
                    f"got {row.get(field)!r}"
                )
        if str(row.get("local_code") or "") not in {
            str(value) for value in definition.get("local_codes", [])
        }:
            errors.append(f"{series_code}: current local_code is not registered")
        if str(row.get("local_name") or "") not in {
            str(value) for value in definition.get("local_labels", [])
        }:
            errors.append(f"{series_code}: current local_name is not registered")

    normalized_rows = _csv_rows(Path(series_rows_path))
    if len(normalized_rows) != EXPECTED_HISTORY_SERIES_ROWS:
        errors.append(
            f"reviewed historical mapping rows: expected {EXPECTED_HISTORY_SERIES_ROWS}, "
            f"got {len(normalized_rows)}"
        )
    normalized_identities: set[str] = set()
    for row in normalized_rows:
        series_code = str(row.get("SourceSeriesCode") or "")
        normalized_identities.add(series_code)
        definition = ontology_by_code.get(series_code)
        if definition is None:
            errors.append(f"normalized row references unknown series: {series_code}")
            continue
        expected_fields = {
            "DiseaseFull": definition.get("concept_id"),
            "SourceId": definition.get("source_id"),
            "Frequency": definition.get("frequency"),
            "Measure": definition.get("measure"),
            "Unit": definition.get("unit", "count"),
        }
        for field, expected_value in expected_fields.items():
            if str(row.get(field) or "") != str(expected_value or ""):
                errors.append(
                    f"{series_code}.{field}: expected {expected_value!r}, "
                    f"got {row.get(field)!r}"
                )
                break
        if str(row.get("DiseaseCode") or "") not in {
            str(value) for value in definition.get("local_codes", [])
        }:
            errors.append(f"{series_code}: normalized DiseaseCode is not registered")

    database_by_code: dict[str, dict[str, Any]] = {}
    for raw in database_rows:
        row = dict(raw)
        series_code = str(row.get("series_code") or "")
        if series_code in database_by_code:
            errors.append(f"database returned duplicate series: {series_code}")
        database_by_code[series_code] = row
    expected_codes = set(ontology_by_code)
    actual_codes = set(database_by_code)
    if actual_codes != expected_codes:
        missing = sorted(expected_codes - actual_codes)
        unexpected = sorted(actual_codes - expected_codes)
        if missing:
            errors.append(f"database missing registry series: {', '.join(missing)}")
        if unexpected:
            errors.append(f"database has unexpected registry series: {', '.join(unexpected)}")

    field_map = {
        "disease_id": "concept_id",
        "source_system": "source_id",
        "metric_type": "measure",
        "reporting_basis": "reporting_basis",
        "temporal_granularity": "frequency",
        "unit": "unit",
        "mapping_relation": "mapping_relation",
        "comparability": "comparability",
        "aggregation_policy": "aggregation_policy",
        "missing_value_policy": "missing_value_policy",
    }
    for series_code in sorted(expected_codes & actual_codes):
        definition = ontology_by_code[series_code]
        row = database_by_code[series_code]
        for database_field, ontology_field in field_map.items():
            expected_value = definition.get(ontology_field)
            if expected_value is None and ontology_field == "unit":
                expected_value = "count"
            actual_value = row.get(database_field)
            if str(actual_value or "") != str(expected_value or ""):
                errors.append(
                    f"database {series_code}.{database_field}: expected "
                    f"{expected_value!r}, got {actual_value!r}"
                )
        if str(row.get("source_series_code") or "") != series_code:
            errors.append(f"database {series_code}: unstable source_series_code")
        expected_label = next(
            iter(definition.get("local_labels") or definition.get("local_codes") or []),
            series_code,
        )
        if str(row.get("source_label") or "") != str(expected_label):
            errors.append(
                f"database {series_code}.source_label: expected {expected_label!r}, "
                f"got {row.get('source_label')!r}"
            )
        expected_active = definition.get("status") == "active"
        if bool(row.get("is_active")) != expected_active:
            errors.append(
                f"database {series_code}.is_active: expected {expected_active}, "
                f"got {row.get('is_active')!r}"
            )

    return AuditCheck(
        name="disease mapping registry",
        detail=(
            f"{len(ontology_by_code)} series / {len(concept_ids)} disease targets / "
            f"{len(current_rows)} current mappings / "
            f"{len(normalized_rows)} reviewed historical observations"
        ),
        errors=tuple(errors),
    )


def _identity_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    identities: dict[str, set[tuple[str, str, str]]] = {}
    for row in rows:
        reason = str(row.get("reason") or "")
        identities.setdefault(reason, set()).add(
            (
                str(row.get("raw_label_is") or row.get("RawDiseaseLabel") or ""),
                str(row.get("raw_label_en") or ""),
                str(row.get("icd10") or row.get("ICD10") or ""),
            )
        )
    return {reason: len(values) for reason, values in identities.items()}


def validate_history_artifacts(
    manifest_path: Path,
    series_rows_path: Path,
    quarantine_path: Path,
) -> AuditCheck:
    """Cross-check historical manifest summaries against both CSV artifacts."""

    manifest = _load_json(Path(manifest_path))
    series_rows = _csv_rows(Path(series_rows_path))
    quarantine_rows = _csv_rows(Path(quarantine_path))
    errors: list[str] = []

    counts = manifest.get("counts") if isinstance(manifest, dict) else {}
    counts = counts if isinstance(counts, dict) else {}
    manifest_series_count = _int_value(counts.get("series_rows"))
    manifest_quarantine_count = _int_value(counts.get("quarantine_rows"))
    if manifest_series_count != EXPECTED_HISTORY_SERIES_ROWS:
        errors.append(
            "history manifest series_rows: expected "
            f"{EXPECTED_HISTORY_SERIES_ROWS}, got {manifest_series_count!r}"
        )
    if len(series_rows) != EXPECTED_HISTORY_SERIES_ROWS:
        errors.append(
            f"series_rows.csv rows: expected {EXPECTED_HISTORY_SERIES_ROWS}, "
            f"got {len(series_rows)}"
        )

    manifest_files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(manifest_files, list):
        manifest_files = []
        errors.append("history manifest files must be a list")
    if len(manifest_files) != EXPECTED_RAW_FILE_COUNT:
        errors.append(
            f"history manifest file entries: expected {EXPECTED_RAW_FILE_COUNT}, "
            f"got {len(manifest_files)}"
        )
    portable_verified = 0
    for index, entry in enumerate(manifest_files):
        if not isinstance(entry, dict):
            errors.append(f"history manifest files[{index}] is not an object")
            continue
        label = str(entry.get("filename") or entry.get("key") or f"files[{index}]")
        raw_path = Path(str(entry.get("path") or ""))
        if not str(entry.get("path") or ""):
            errors.append(f"{label}: parsed manifest path is missing")
            continue
        if raw_path.is_absolute():
            errors.append(f"{label}: parsed manifest path must be relative")
            continue
        resolved = (Path(manifest_path).parent / raw_path).resolve()
        if not resolved.is_file():
            errors.append(f"{label}: parsed manifest workbook not found at {resolved}")
            continue
        expected_size = _int_value(entry.get("size_bytes"))
        expected_hash = str(entry.get("sha256") or "").lower()
        actual_size = resolved.stat().st_size
        actual_hash = _sha256(resolved)
        if expected_size != actual_size:
            errors.append(
                f"{label}: parsed manifest size expected {expected_size!r}, "
                f"got {actual_size}"
            )
        if expected_hash != actual_hash:
            errors.append(
                f"{label}: parsed manifest SHA256 expected "
                f"{expected_hash or '<missing>'}, got {actual_hash}"
            )
        if expected_size == actual_size and expected_hash == actual_hash:
            portable_verified += 1

    expected_quarantine_total = sum(EXPECTED_QUARANTINE_BY_REASON.values())
    if manifest_quarantine_count != expected_quarantine_total:
        errors.append(
            "history manifest quarantine_rows: expected "
            f"{expected_quarantine_total}, got {manifest_quarantine_count!r}"
        )
    if len(quarantine_rows) != expected_quarantine_total:
        errors.append(
            f"quarantine.csv rows: expected {expected_quarantine_total}, "
            f"got {len(quarantine_rows)}"
        )

    quarantine = manifest.get("quarantine") if isinstance(manifest, dict) else {}
    quarantine = quarantine if isinstance(quarantine, dict) else {}
    manifest_by_reason = {
        str(key): _int_value(value)
        for key, value in (quarantine.get("by_reason") or {}).items()
    }
    csv_by_reason = dict(Counter(str(row.get("reason") or "") for row in quarantine_rows))
    if manifest_by_reason != EXPECTED_QUARANTINE_BY_REASON:
        errors.append(
            "history manifest quarantine by_reason: expected "
            f"{EXPECTED_QUARANTINE_BY_REASON!r}, got {manifest_by_reason!r}"
        )
    if csv_by_reason != EXPECTED_QUARANTINE_BY_REASON:
        errors.append(
            "quarantine.csv by_reason: expected "
            f"{EXPECTED_QUARANTINE_BY_REASON!r}, got {csv_by_reason!r}"
        )

    identity_rows = quarantine.get("identities") or []
    if not isinstance(identity_rows, list):
        identity_rows = []
        errors.append("history manifest quarantine.identities must be a list")
    manifest_identity_counts = dict(
        Counter(
            str(row.get("reason") or "")
            for row in identity_rows
            if isinstance(row, dict)
        )
    )
    csv_identity_counts = _identity_counts(quarantine_rows)
    if manifest_identity_counts != EXPECTED_QUARANTINE_IDENTITIES:
        errors.append(
            "history manifest raw identities: expected "
            f"{EXPECTED_QUARANTINE_IDENTITIES!r}, got {manifest_identity_counts!r}"
        )
    if csv_identity_counts != EXPECTED_QUARANTINE_IDENTITIES:
        errors.append(
            "quarantine.csv raw identities: expected "
            f"{EXPECTED_QUARANTINE_IDENTITIES!r}, got {csv_identity_counts!r}"
        )

    manifest_identity_row_counts = {
        reason: sum(
            _int_value(row.get("row_count")) or 0
            for row in identity_rows
            if isinstance(row, dict) and str(row.get("reason") or "") == reason
        )
        for reason in EXPECTED_QUARANTINE_BY_REASON
    }
    if manifest_identity_row_counts != EXPECTED_QUARANTINE_BY_REASON:
        errors.append(
            "history manifest identity row totals: expected "
            f"{EXPECTED_QUARANTINE_BY_REASON!r}, "
            f"got {manifest_identity_row_counts!r}"
        )

    return AuditCheck(
        name="historical normalized artifacts",
        detail=(
            f"{len(series_rows)} series rows; {len(quarantine_rows)} quarantined "
            f"rows; {portable_verified}/{len(manifest_files)} portable raw paths; "
            f"identities {csv_identity_counts}"
        ),
        errors=tuple(errors),
    )


def validate_download_manifest(manifest_path: Path) -> AuditCheck:
    """Validate the generated, local-only Iceland download-package summary."""

    manifest = _load_json(Path(manifest_path))
    errors: list[str] = []
    schema_version = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if schema_version != 4:
        errors.append(f"schema_version: expected 4, got {schema_version!r}")

    countries = manifest.get("countries") if isinstance(manifest, dict) else None
    countries = countries if isinstance(countries, list) else []
    entries = [
        entry
        for entry in countries
        if isinstance(entry, dict) and entry.get("code") == "IS"
    ]
    if len(entries) != 1:
        return AuditCheck(
            name="local download manifest",
            detail=f"schema {schema_version!r}; {len(entries)} Iceland entries",
            errors=tuple(errors + [f"expected one IS country entry, got {len(entries)}"]),
        )

    entry = entries[0]
    expected_counts = {
        "source_series_count": EXPECTED_SERIES_COUNT,
        "source_observation_count": EXPECTED_OBSERVATION_COUNT,
        "projection_record_count": EXPECTED_PROJECTION_COUNT,
    }
    for field, expected_value in expected_counts.items():
        actual_value = _int_value(entry.get(field))
        if actual_value != expected_value:
            errors.append(
                f"IS.{field}: expected {expected_value}, got {actual_value!r}"
            )

    expected_record_count = EXPECTED_OBSERVATION_COUNT + EXPECTED_PROJECTION_COUNT
    if _int_value(entry.get("record_count")) != expected_record_count:
        errors.append(
            f"IS.record_count: expected {expected_record_count}, "
            f"got {_int_value(entry.get('record_count'))!r}"
        )

    parts = entry.get("parts") if isinstance(entry.get("parts"), list) else []
    for field, expected_value in {
        "source_observation_count": EXPECTED_OBSERVATION_COUNT,
        "projection_record_count": EXPECTED_PROJECTION_COUNT,
        "record_count": expected_record_count,
    }.items():
        part_total = sum(
            _int_value(part.get(field)) or 0
            for part in parts
            if isinstance(part, dict)
        )
        if part_total != expected_value:
            errors.append(
                f"IS.parts {field} total: expected {expected_value}, got {part_total}"
            )

    return AuditCheck(
        name="local download manifest",
        detail=(
            f"schema {schema_version}; {entry.get('source_series_count')} series / "
            f"{entry.get('source_observation_count')} source observations / "
            f"{entry.get('projection_record_count')} projections"
        ),
        errors=tuple(errors),
    )


def _disease_series_values(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def validate_country_json(country_path: Path) -> AuditCheck:
    """Recount source provenance embedded in the Iceland country payload."""

    payload = _load_json(Path(country_path))
    errors: list[str] = []
    if not isinstance(payload, dict):
        return AuditCheck(
            name="Iceland country JSON",
            detail="invalid JSON root",
            errors=("country JSON root must be an object",),
        )
    if payload.get("country_code") != "IS":
        errors.append(
            f"country_code: expected 'IS', got {payload.get('country_code')!r}"
        )

    source_rows: list[Mapping[str, Any]] = []
    for disease in _disease_series_values(payload.get("disease_series")):
        series = disease.get("source_series")
        if isinstance(series, list):
            source_rows.extend(row for row in series if isinstance(row, dict))

    codes = [str(row.get("series_code") or "") for row in source_rows]
    nonempty_codes = [code for code in codes if code]
    unique_codes = set(nonempty_codes)
    observation_count = sum(
        _int_value(row.get("observation_count")) or 0 for row in source_rows
    )
    if len(source_rows) != EXPECTED_SERIES_COUNT:
        errors.append(
            f"source_series entries: expected {EXPECTED_SERIES_COUNT}, got {len(source_rows)}"
        )
    if len(unique_codes) != EXPECTED_SERIES_COUNT:
        errors.append(
            f"distinct series_code values: expected {EXPECTED_SERIES_COUNT}, "
            f"got {len(unique_codes)}"
        )
    if len(nonempty_codes) != len(source_rows):
        errors.append("one or more source_series entries has an empty series_code")
    if observation_count != EXPECTED_OBSERVATION_COUNT:
        errors.append(
            f"source observation_count sum: expected {EXPECTED_OBSERVATION_COUNT}, "
            f"got {observation_count}"
        )

    for code, row in zip(codes, source_rows):
        stated_count = _int_value(row.get("observation_count"))
        dates = row.get("dates")
        values = row.get("values")
        if isinstance(dates, list) and stated_count != len(dates):
            errors.append(
                f"{code or '<missing>'}.dates: observation_count {stated_count!r} "
                f"but array length is {len(dates)}"
            )
        if isinstance(values, list) and stated_count != len(values):
            errors.append(
                f"{code or '<missing>'}.values: observation_count {stated_count!r} "
                f"but array length is {len(values)}"
            )

    return AuditCheck(
        name="Iceland country JSON",
        detail=f"{len(unique_codes)} source series / {observation_count} observations",
        errors=tuple(errors),
    )


async def collect_database_snapshot() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Execute the audit SELECTs without creating or committing a session."""

    engine = get_engine()
    async with engine.connect() as connection:
        source_rows = _mapping_rows(
            (await connection.execute(SOURCE_SUMMARY_SQL)).mappings().all()
        )
        job_rows = _mapping_rows(
            (
                await connection.execute(
                    AUTOMATION_JOBS_SQL,
                    {
                        "country_code": "IS",
                        "job_ids": tuple(sorted(EXPECTED_AUTOMATION_JOBS)),
                    },
                )
            )
            .mappings()
            .all()
        )
        registry_rows = _mapping_rows(
            (await connection.execute(SERIES_REGISTRY_SQL)).mappings().all()
        )
        compatibility_rows = _mapping_rows(
            (await connection.execute(COMPATIBILITY_PROJECTION_SQL)).mappings().all()
        )
    return source_rows, job_rows, registry_rows, compatibility_rows


def _failed_check(name: str, exc: Exception) -> AuditCheck:
    return AuditCheck(
        name=name,
        detail="could not complete check",
        errors=(f"{type(exc).__name__}: {exc}",),
    )


def audit_local_artifacts(root: Path) -> list[AuditCheck]:
    root = Path(root).resolve()
    checks: list[AuditCheck] = []
    validators: Sequence[tuple[str, Callable[[], AuditCheck]]] = (
        (
            "historical raw archive",
            lambda: validate_raw_manifest(
                root / "data/raw/is/history/raw_manifest.json"
            ),
        ),
        (
            "historical normalized artifacts",
            lambda: validate_history_artifacts(
                root / "data/current/is/history/manifest.json",
                root / "data/current/is/history/series_rows.csv",
                root / "data/current/is/history/quarantine.csv",
            ),
        ),
        (
            "local download manifest",
            lambda: validate_download_manifest(
                root / "exports/site-downloads/manifest.json"
            ),
        ),
        (
            "Iceland country JSON",
            lambda: validate_country_json(
                root / "astro-site/src/data/countries/is.json"
            ),
        ),
    )
    for name, validator in validators:
        try:
            checks.append(validator())
        except Exception as exc:  # keep the full final audit report actionable
            checks.append(_failed_check(name, exc))
    return checks


DatabaseLoader = Callable[
    [],
    Awaitable[
        tuple[
            list[dict[str, Any]],
            list[dict[str, Any]],
            list[dict[str, Any]],
            list[dict[str, Any]],
        ]
    ],
]


async def run_audit(
    *,
    root: Path = ROOT,
    database_loader: DatabaseLoader = collect_database_snapshot,
    mapping_validator: Callable[
        [Path, Path, Path, Iterable[Mapping[str, Any]]], AuditCheck
    ] = validate_disease_mappings,
) -> list[AuditCheck]:
    """Run every check while isolating database and artifact failures."""

    try:
        source_rows, job_rows, registry_rows, compatibility_rows = (
            await database_loader()
        )
        database_checks = [
            validate_database_sources(source_rows),
            validate_compatibility_projection(compatibility_rows),
            validate_automation_jobs(job_rows),
        ]
    except Exception as exc:
        database_checks = [
            _failed_check("database source observations", exc),
            _failed_check("safe compatibility projection", exc),
            _failed_check("automation jobs", exc),
        ]
        registry_rows = []
    try:
        mapping_check = mapping_validator(
            Path(root) / "configs/disease_ontology.json",
            Path(root) / "configs/mapping/is.csv",
            Path(root) / "data/current/is/history/series_rows.csv",
            registry_rows,
        )
    except Exception as exc:
        mapping_check = _failed_check("disease mapping registry", exc)
    return database_checks + [mapping_check] + audit_local_artifacts(root)


def render_report(checks: Sequence[AuditCheck]) -> str:
    lines = ["Iceland integration final audit (read-only)"]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"[{status}] {check.name}: {check.detail}")
        lines.extend(f"  - {error}" for error in check.errors)
    passed = sum(check.passed for check in checks)
    status = "PASS" if passed == len(checks) else "FAIL"
    lines.append(f"Summary: {status} ({passed}/{len(checks)} checks passed)")
    lines.append("Read-only audit: no database rows or files were changed.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only final audit of the Iceland integration snapshot."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root containing data/, exports/, and astro-site/.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the audit report as JSON on stdout.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    checks = asyncio.run(run_audit(root=args.root))
    if args.json:
        print(
            json.dumps(
                {
                    "passed": all(check.passed for check in checks),
                    "checks": [check.as_dict() for check in checks],
                    "read_only": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_report(checks))
    raise SystemExit(0 if all(check.passed for check in checks) else 1)


if __name__ == "__main__":
    main()
