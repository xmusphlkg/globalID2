"""Deterministic CSV, JSON, and XLSX serialization for public downloads."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from io import BytesIO, StringIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook

DOWNLOAD_FIELDS = [
    "dataset_kind",
    "dataset_id",
    "dataset_slug",
    "dataset_name",
    "record_kind",
    "country_code",
    "country_name",
    "disease_id",
    "disease_name_en",
    "disease_name_zh",
    "category",
    "date",
    "year_month",
    "value",
    "cases",
    "weekly_equiv_cases",
    "deaths",
    "incidence_rate_per_100k",
    "incidence_rate_source",
    "mortality_rate",
    "series_code",
    "source_series_code",
    "source_system",
    "source_label",
    "metric_type",
    "reporting_basis",
    "temporal_granularity",
    "unit",
    "geography_key",
    "dimension_key",
    "mapping_relation",
    "comparability",
    "aggregation_policy",
    "availability_status",
    "missing_value_policy",
    "definition_version",
    "case_definition",
    "case_definition_uri",
    "quality_status",
    "is_selected_series",
    "data_layer",
    "projection_policy",
    "coverage_status",
    "loss_risk",
    "selected_series_codes",
    "available_series_count",
    "provenance_note",
    "primary_source_scope",
    "primary_source_label",
    "primary_source_url",
    "primary_source_type",
    "source_scopes",
    "source_labels",
    "source_urls",
    "source_types",
]


_XLSX_TIMESTAMP = (2000, 1, 1, 0, 0, 0)


def _csv_bytes(rows: list[dict]) -> bytes:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=DOWNLOAD_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(
        {field: row.get(field) for field in DOWNLOAD_FIELDS} for row in rows
    )
    return stream.getvalue().encode("utf-8")


def _json_bytes(metadata: dict, rows: list[dict]) -> bytes:
    return json.dumps(
        {"metadata": metadata, "records": rows},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _xlsx_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return value


def _xlsx_bytes(metadata: dict, rows: list[dict]) -> bytes:
    workbook = Workbook(write_only=True)
    fixed = datetime(*_XLSX_TIMESTAMP)  # noqa: DTZ001 - XLSX output uses a fixed naive timestamp.
    workbook.properties.created = fixed
    workbook.properties.modified = fixed
    records_sheet = workbook.create_sheet("records")
    records_sheet.append(DOWNLOAD_FIELDS)
    for row in rows:
        records_sheet.append([_xlsx_cell(row.get(field)) for field in DOWNLOAD_FIELDS])
    metadata_sheet = workbook.create_sheet("metadata")
    metadata_sheet.append(["field", "value"])
    for key in sorted(metadata):
        metadata_sheet.append([key, _xlsx_cell(metadata[key])])

    original = BytesIO()
    workbook.save(original)
    original.seek(0)
    canonical = BytesIO()
    with ZipFile(original, "r") as source_zip, ZipFile(
        canonical, "w", compression=ZIP_DEFLATED, compresslevel=9
    ) as target_zip:
        for name in sorted(source_zip.namelist()):
            source_info = source_zip.getinfo(name)
            target_info = ZipInfo(name, date_time=_XLSX_TIMESTAMP[:6])
            target_info.compress_type = ZIP_DEFLATED
            target_info.external_attr = source_info.external_attr
            target_info.create_system = source_info.create_system
            payload = source_zip.read(name)
            if name == "docProps/core.xml":
                # openpyxl.save_workbook overwrites properties.modified with
                # wall-clock time. Normalize after save, not only before it.
                payload, replacements = re.subn(
                    rb"(<dcterms:modified\b[^>]*>)[^<]*(</dcterms:modified>)",
                    lambda match: match[1] + fixed.isoformat().encode() + b"Z" + match[2],
                    payload,
                )
                if replacements != 1:
                    raise ValueError("XLSX core properties lack a unique modified timestamp")
            target_zip.writestr(target_info, payload)
    return canonical.getvalue()
