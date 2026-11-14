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
from src.data.iceland_audit_contracts import (
    EXPECTED_SOURCE_SUMMARIES as EXPECTED_SOURCE_SUMMARIES,
    EXPECTED_SERIES_COUNT as EXPECTED_SERIES_COUNT,
    EXPECTED_CONCEPT_COUNT as EXPECTED_CONCEPT_COUNT,
    EXPECTED_OBSERVATION_COUNT as EXPECTED_OBSERVATION_COUNT,
    EXPECTED_PROJECTION_COUNT as EXPECTED_PROJECTION_COUNT,
    EXPECTED_COMPATIBILITY_PROJECTION as EXPECTED_COMPATIBILITY_PROJECTION,
    EXPECTED_RAW_FILE_COUNT as EXPECTED_RAW_FILE_COUNT,
    EXPECTED_HISTORY_SERIES_ROWS as EXPECTED_HISTORY_SERIES_ROWS,
    EXPECTED_QUARANTINE_BY_REASON as EXPECTED_QUARANTINE_BY_REASON,
    EXPECTED_QUARANTINE_IDENTITIES as EXPECTED_QUARANTINE_IDENTITIES,
    EXPECTED_MAPPING_SEMANTICS as EXPECTED_MAPPING_SEMANTICS,
    EXPECTED_ZERO_QUALITY_FIELDS as EXPECTED_ZERO_QUALITY_FIELDS,
    AuditCheck as AuditCheck,
    _date_text as _date_text,
    _int_value as _int_value,
    _mapping_rows as _mapping_rows,
    validate_database_sources as validate_database_sources,
    validate_compatibility_projection as validate_compatibility_projection,
    _load_json as _load_json,
    _sha256 as _sha256,
    _resolve_manifest_path as _resolve_manifest_path,
    validate_raw_manifest as validate_raw_manifest,
    _csv_rows as _csv_rows,
    validate_disease_mappings as validate_disease_mappings,
    _identity_counts as _identity_counts,
    validate_history_artifacts as validate_history_artifacts,
    validate_download_manifest as validate_download_manifest,
    _disease_series_values as _disease_series_values,
    validate_country_json as validate_country_json,
)

from src.core.database import get_engine  # noqa: E402


EXPECTED_AUTOMATION_JOBS = {
    job_id: dict(values) for job_id, values in JOB_VALUES.items()
}


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
