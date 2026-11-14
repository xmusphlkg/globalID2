"""Transactional helpers used by the database rebuild importers."""

from __future__ import annotations

import json
import pandas as pd

from src.core.missing_values import normalize_rate_value
from .database_rebuild_plan import US_NNDSS_SOURCE_NAME

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BatchInsertResult:
    attempted: int
    inserted: int
    failed: int
    batch_error: str | None = None


async def insert_with_savepoint_fallback(
    db,
    statement,
    rows: Sequence[Mapping[str, object]],
) -> BatchInsertResult:
    """Insert a batch, isolating both batch and row failures with savepoints.

    A session-wide rollback here would discard successful work performed since
    the previous stage checkpoint.  Nested transactions keep the outer stage
    transaction intact while still allowing invalid rows to be skipped.
    """
    if not rows:
        return BatchInsertResult(attempted=0, inserted=0, failed=0)

    try:
        async with db.begin_nested():
            await db.execute(statement, rows)
    except Exception as batch_error:
        inserted = 0
        for row in rows:
            try:
                async with db.begin_nested():
                    await db.execute(statement, row)
            except Exception:
                continue
            inserted += 1
        return BatchInsertResult(
            attempted=len(rows),
            inserted=inserted,
            failed=len(rows) - inserted,
            batch_error=str(batch_error)[:500],
        )

    return BatchInsertResult(attempted=len(rows), inserted=len(rows), failed=0)


def normalize_history_label(value):
    """Normalize the legacy history lookup without coercing missing labels."""
    try:
        return value.strip().lower()
    except Exception:
        return None


def prepare_history_row(row, idx, *, columns, field_columns, country_code,
                        country_id, mapping_dict, source_filename, normalize_report_time):
    """Prepare one history observation; never write or commit a database session.

    The optional second return value names an unmapped disease for reporting.
    Missing observations remain skipped; zero and unreported deaths differ.
    """
    date_col, disease_cn_col, disease_en_col, cases_col, deaths_col = field_columns
    disease_cn = str(row[disease_cn_col]) if pd.notna(row[disease_cn_col]) else None
    if not disease_cn or disease_cn == 'nan':
        return None, None

    disease_en = str(row[disease_en_col]) if disease_en_col and pd.notna(row[disease_en_col]) else None

    # Find mapping (using normalization)
    db_disease_id = None
    if disease_en:
        db_disease_id = mapping_dict.get(normalize_history_label(disease_en))
    if not db_disease_id:
        db_disease_id = mapping_dict.get(normalize_history_label(disease_cn))

    if not db_disease_id:
        return None, disease_cn

    # Parse date
    date_str = str(row[date_col])
    date_obj = normalize_report_time(date_str)
    if date_obj is None:
        return None, None

    # Real data source from CSV. Resolve it before parsing counts so
    # source-specific missing-value contracts can fail closed.
    data_source = 'Historical Data Import'
    if 'Source' in columns and pd.notna(row['Source']):
        data_source = str(row['Source'])

    # A blank NNDSS cell is missing/unknown, never a reported zero.
    cases_missing = not pd.notna(row[cases_col]) or str(row[cases_col]) in ['', '-10', 'nan']
    if cases_missing and country_code == "US" and data_source == US_NNDSS_SOURCE_NAME:
        return None, None
    cases = int(row[cases_col]) if not cases_missing else 0
    # A blank death field means the source does not report deaths;
    # preserving NULL prevents diagnosis-only feeds such as NNDSS
    # and NHSS from being misrepresented as zero mortality.
    deaths = int(row[deaths_col]) if pd.notna(row[deaths_col]) and str(row[deaths_col]) not in ['', '-10', 'nan'] else None

    # Extract additional fields
    incidence = None
    if 'Incidence' in columns and pd.notna(row['Incidence']):
        val = float(row['Incidence'])
        incidence = val if val >= 0 else None

    mortality = None
    if 'Mortality' in columns and pd.notna(row['Mortality']):
        val = float(row['Mortality'])
        mortality = val if val >= 0 else None

    region = None
    if 'ProvinceCN' in columns and pd.notna(row['ProvinceCN']) and str(row['ProvinceCN']) not in ['China', 'National', 'Nationwide']:
        region = str(row['ProvinceCN'])
    elif 'Province' in columns and pd.notna(row['Province']) and str(row['Province']) not in ['China', 'National', 'Nationwide']:
        region = str(row['Province'])

    # Build metadata object
    metadata_obj = {
        'source_csv': source_filename,
        'row_index': int(idx)
    }
    if data_source == 'US CDC NHSS':
        metadata_obj.update({
            'frequency': 'annual',
            'measure': 'hiv_diagnoses_or_aids_classifications',
            'death_reporting': 'not_provided_by_source',
        })
    elif data_source == US_NNDSS_SOURCE_NAME:
        metadata_obj.update({
            'reporting_area': str(row.get('ReportingArea', '')),
            'population_scope': 'us_residents_excluding_territories',
            'death_reporting': 'not_provided_by_source',
        })

    if '__source_file' in columns and pd.notna(row.get('__source_file')):
        metadata_obj['source_file'] = str(row['__source_file'])
    if 'DOI' in columns and pd.notna(row['DOI']):
        metadata_obj['doi'] = str(row['DOI'])
    if 'URL' in columns and pd.notna(row['URL']):
        metadata_obj['url'] = str(row['URL'])
    if 'ADCode' in columns and pd.notna(row['ADCode']):
        metadata_obj['adcode'] = str(int(row['ADCode']))

    # Prepare raw data for traceability
    raw_obj = None
    try:
        raw_obj = {k: (None if pd.isna(v) else v) for k, v in row.items()}
    except Exception:
        pass

    return {
        'time': date_obj,
        'disease_id': db_disease_id,
        'country_id': country_id,
        'cases': max(0, cases),
        'deaths': max(0, deaths) if deaths is not None else None,
        'incidence_rate': normalize_rate_value(incidence),
        'mortality_rate': normalize_rate_value(mortality),
        'region': region,
        'data_source': data_source,
        'metadata': json.dumps(metadata_obj),
        'raw_data': json.dumps(raw_obj) if raw_obj else None
    }, None
