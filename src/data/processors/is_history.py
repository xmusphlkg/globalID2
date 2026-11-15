"""Parse Iceland's heterogeneous historical Excel surveillance releases.

This module deliberately preserves three source-series families instead of
pretending that they are one continuous metric:

``registry_annual``
    Annual case notifications from the historical national registry table.
``registry_disease_monthly``
    Disease-specific monthly case-notification workbooks (and published
    incidence where present).
``legacy_icd_monthly``
    Monthly *registered diagnoses* from primary-care ICD tables.  These are a
    different reporting basis and are never projected into legacy
    ``disease_records`` rows.

Only explicitly reviewed disease identities are candidates, and candidates
are emitted to ``series_rows`` only when the checked-in ontology contains the
exact source-series code, source, local code, label, concept, frequency, and
measure.  Everything else is counted and retained in quarantine metadata.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import openpyxl
import xlrd
from openpyxl.utils.cell import column_index_from_string
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.is_history import (
    IcelandHistoryRawFile,
    IcelandHistoryWorkbookSpec,
    OFFICIAL_WORKBOOKS,
)
from src.services.disease_series_policy import (
    SOURCE_OBSERVATIONS_ONLY_POLICY,
    is_case_count_series,
    select_series_projection,
)


from .iceland_history.contracts import (
    IcelandWorkbookLayoutError,
    ReviewedSeries,
    ReviewedLegacySeries,
    IcelandHistoryPreparedResult,
    IcelandHistoryImportResult,
    _ParseContext,
)
from .iceland_history.catalogue import (
    _text,
    _label_key,
    _explanation_key,
    _normalize_icd,
    _as_year,
    _month_number,
    _build_annual_aliases,
    HISTORY_SOURCE_ID,
    LEGACY_SOURCE_ID,
    HISTORY_SOURCE_NAME,
    LEGACY_SOURCE_NAME,
    MONTHS,
    ANNUAL_SERIES,
    MONTHLY_SERIES,
    LEGACY_SERIES,
    ANNUAL_ALIAS_LOOKUP,
    LEGACY_PAIR_LOOKUP,
)
from .iceland_history.workbook import (
    _Workbook,
    _numeric_cell,
    _safe_arithmetic,
    _evaluate_formula,
    _count_value,
    _rate_value,
    _CELL_RE,
    _RANGE_RE,
)
from .iceland_history.parsers import (
    _base_row,
    _find_year_header,
    _parse_annual,
    _find_rate_column,
    _parse_disease_monthly,
    _month_header,
    _explanation_codes,
    _sheet_year,
    _parse_legacy_icd,
)
from .iceland_history.audit import (
    _quarantine_summary,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ONTOLOGY_PATH = ROOT / "configs" / "disease_ontology.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "current" / "is" / "history"
PARSED_MANIFEST_SCHEMA = "globalid.iceland-history-parsed-manifest.v1"
PARSER_VERSION = "1.0.0"


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# The annual list contains only mappings whose source wording and target
# concept have been manually checked.  Five published categories remain
# intentionally absent: Anisakiasis, acute toxin/radiological symptoms,
# influenza-like illness, hepatitis non-A--E, and unexpected health events.


class _OntologySeriesGate:
    """Require exact checked-in Registry coverage before rows can escape."""

    def __init__(self, ontology_path: Path) -> None:
        document = json.loads(ontology_path.read_text(encoding="utf-8"))
        self.series = {str(item.get("id")): item for item in document.get("source_series", [])}

    def validate(self, row: Mapping[str, Any]) -> str | None:
        code = _text(row.get("SourceSeriesCode"))
        definition = self.series.get(code)
        if definition is None:
            return "source_series_not_registered"
        expected = {
            "source_id": _text(row.get("SourceId")),
            "concept_id": _text(row.get("DiseaseFull")),
            "frequency": _text(row.get("Frequency")),
            "measure": _text(row.get("Measure")),
        }
        for field_name, expected_value in expected.items():
            if _text(definition.get(field_name)) != expected_value:
                return f"source_series_{field_name}_mismatch"
        if _text(definition.get("status")).casefold() in {"deprecated", "inactive"}:
            return "source_series_inactive"
        local_codes = {_text(value) for value in definition.get("local_codes", [])}
        if _text(row.get("DiseaseCode")) not in local_codes:
            return "source_series_local_code_mismatch"
        local_labels = {_label_key(value) for value in definition.get("local_labels", [])}
        if _label_key(row.get("RawDiseaseLabel")) not in local_labels:
            return "source_series_local_label_mismatch"
        return None


def _raw_file_from_manifest(
    item: Mapping[str, Any], manifest_path: Path
) -> IcelandHistoryRawFile:
    raw_path = Path(_text(item.get("path")))
    filename = _text(item.get("filename")) or raw_path.name
    # v1 manifests originally recorded machine-specific absolute paths.  A
    # relocated archive must still be replayable, so a missing old path falls
    # back to the workbook beside the manifest.  New manifests use relative
    # paths and take the same deterministic resolution route.
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    elif _text(item.get("path")):
        candidates.append(manifest_path.parent / raw_path)
    if filename:
        candidates.append(manifest_path.parent / filename)
    if not candidates:
        candidates.append(manifest_path.parent / filename)
    path = next(
        (candidate for candidate in candidates if candidate.exists()), candidates[0]
    )
    size_bytes = int(
        item.get("size_bytes") or (path.stat().st_size if path.exists() else 0)
    )
    return IcelandHistoryRawFile(
        key=_text(item.get("key")),
        source_kind=_text(item.get("source_kind")),
        filename=filename or path.name,
        path=str(path.resolve()),
        source_url=_text(item.get("source_url")),
        sha256=_text(item.get("sha256")),
        size_bytes=size_bytes,
        media_type=_text(item.get("media_type")),
        disease_key=_text(item.get("disease_key")),
        validation_only=bool(item.get("validation_only", False)),
    )


class IcelandHistoryProcessor:
    """Prepare auditable Iceland history rows and import the safe projection."""

    country_code = "IS"
    series_registered_rows_only = True

    def __init__(
        self,
        *,
        ontology_path: Path | str = DEFAULT_ONTOLOGY_PATH,
        require_registered_series: bool = True,
    ) -> None:
        self.ontology_path = Path(ontology_path)
        self.require_registered_series = require_registered_series

    def prepare_manifest(self, manifest_path: Path | str) -> IcelandHistoryPreparedResult:
        path = Path(manifest_path)
        document = json.loads(path.read_text(encoding="utf-8"))
        files = [_raw_file_from_manifest(item, path) for item in document.get("files", [])]
        if not files:
            raise ValueError(f"Iceland raw manifest contains no files: {path}")
        return self.prepare_files(files, raw_manifest=document)

    def prepare_files(
        self,
        raw_files: Iterable[IcelandHistoryRawFile],
        *,
        raw_manifest: Mapping[str, Any] | None = None,
    ) -> IcelandHistoryPreparedResult:
        files = list(raw_files)
        context = _ParseContext()
        candidates: list[dict[str, Any]] = []
        raw_hashes: dict[str, str] = {}
        file_manifest: list[dict[str, Any]] = []

        for raw_file in files:
            path = Path(raw_file.path)
            if not path.exists():
                raise FileNotFoundError(f"Iceland raw workbook not found: {path}")
            digest = _sha256_path(path)
            if raw_file.sha256 and raw_file.sha256 != digest:
                raise ValueError(
                    f"Iceland raw hash mismatch for {raw_file.filename}: "
                    f"manifest={raw_file.sha256} actual={digest}"
                )
            raw_hashes[raw_file.filename] = digest
            file_manifest.append(
                {
                    **asdict(raw_file),
                    "path": str(path.resolve()),
                    "sha256": digest,
                }
            )
            if raw_file.validation_only or raw_file.source_kind == "validation_annual":
                context.stats["files.validation_only_skipped"] += 1
                continue
            book = _Workbook(path)
            try:
                if raw_file.source_kind == "registry_annual":
                    candidates.extend(_parse_annual(book, raw_file, context))
                elif raw_file.source_kind == "registry_disease_monthly":
                    candidates.extend(_parse_disease_monthly(book, raw_file, context))
                elif raw_file.source_kind == "legacy_icd_monthly":
                    candidates.extend(_parse_legacy_icd(book, raw_file, context))
                else:
                    raise ValueError(
                        f"Unsupported Iceland source kind {raw_file.source_kind!r}"
                    )
            finally:
                book.close()
            context.stats[f"files.{raw_file.source_kind}"] += 1

        gate = _OntologySeriesGate(self.ontology_path) if self.require_registered_series else None
        series_rows: list[dict[str, Any]] = []
        validation_cache: dict[tuple[str, str, str], str | None] = {}
        for row in candidates:
            cache_key = (
                _text(row.get("SourceSeriesCode")),
                _text(row.get("DiseaseCode")),
                _text(row.get("RawDiseaseLabel")),
            )
            reason = validation_cache.get(cache_key)
            if cache_key not in validation_cache:
                reason = gate.validate(row) if gate is not None else None
                validation_cache[cache_key] = reason
            if reason:
                context.reject(reason, **row)
                continue
            series_rows.append(row)

        # The legacy disease_records projection cannot represent frequency or
        # reporting basis.  Prefer disease-specific monthly notification rows;
        # retain annual rows only for disease-years absent from those workbooks.
        monthly_years = {
            (_text(row.get("DiseaseFull")), int(row["Year"]))
            for row in series_rows
            if row.get("SourceKind") == "registry_disease_monthly"
        }
        projection_candidates = [
            row
            for row in series_rows
            if row.get("SourceKind") == "registry_disease_monthly"
            or (
                row.get("SourceKind") == "registry_annual"
                and (_text(row.get("DiseaseFull")), int(row["Year"])) not in monthly_years
            )
        ]
        context.stats["projection.annual_rows_superseded_by_monthly"] = sum(
            1
            for row in series_rows
            if row.get("SourceKind") == "registry_annual"
            and (_text(row.get("DiseaseFull")), int(row["Year"])) in monthly_years
        )
        if gate is not None:
            semantically_projectable: list[dict[str, Any]] = []
            unsafe_mapping_rows = 0
            for row in projection_candidates:
                definition = gate.series[_text(row.get("SourceSeriesCode"))]
                if is_case_count_series(
                    {
                        "metric_type": definition.get("measure"),
                        "series_unit": definition.get("unit", "count"),
                        "observation_unit": row.get("Unit"),
                        "mapping_relation": definition.get("mapping_relation"),
                    }
                ):
                    semantically_projectable.append(row)
                else:
                    unsafe_mapping_rows += 1
            context.stats["projection.unsafe_mapping_rows_excluded"] = (
                unsafe_mapping_rows
            )

            definitions_by_concept: dict[str, dict[str, Mapping[str, Any]]] = (
                defaultdict(dict)
            )
            for row in series_rows:
                series_code = _text(row.get("SourceSeriesCode"))
                definition = gate.series[series_code]
                if is_case_count_series(
                    {
                        "metric_type": definition.get("measure"),
                        "series_unit": definition.get("unit", "count"),
                        "observation_unit": row.get("Unit"),
                        "mapping_relation": definition.get("mapping_relation"),
                    }
                ):
                    definitions_by_concept[_text(row.get("DiseaseFull"))][
                        series_code
                    ] = definition
            source_only_concepts: set[str] = set()
            for concept_id, definitions in definitions_by_concept.items():
                if len(definitions) < 2:
                    continue
                selection = select_series_projection(
                    [
                        {
                            "series_code": series_code,
                            "mapping_relation": definition.get("mapping_relation"),
                            "aggregation_policy": definition.get(
                                "aggregation_policy", "non_additive"
                            ),
                        }
                        for series_code, definition in definitions.items()
                    ]
                )
                if selection.projection_policy == SOURCE_OBSERVATIONS_ONLY_POLICY:
                    source_only_concepts.add(concept_id)
            before_narrower_filter = len(semantically_projectable)
            projection_candidates = [
                row
                for row in semantically_projectable
                if _text(row.get("DiseaseFull")) not in source_only_concepts
            ]
            context.stats["projection.narrower_multi_series_rows_excluded"] = (
                before_narrower_filter - len(projection_candidates)
            )
        # ``disease_records`` has no source-series key. Distinct reviewed
        # categories can intentionally map to one ontology concept (for
        # example historical Hib and all-invasive H. influenzae series), so
        # projecting either one would silently overwrite the other. Preserve
        # every fact in ``series_rows`` and omit all non-representable legacy
        # identities instead of choosing or aggregating a category.
        projection_identity_counts = Counter(
            (
                _text(row.get("DiseaseFull")),
                _text(row.get("Date")),
            )
            for row in projection_candidates
        )
        rows = [
            row
            for row in projection_candidates
            if projection_identity_counts[
                (_text(row.get("DiseaseFull")), _text(row.get("Date")))
            ]
            == 1
        ]
        context.stats["projection.multi_series_identity_rows_excluded"] = (
            len(projection_candidates) - len(rows)
        )

        sort_key = lambda row: (
            _text(row.get("Date")),
            _text(row.get("SourceSeriesCode")),
            _text(row.get("DiseaseCode")),
        )
        rows.sort(key=sort_key)
        series_rows.sort(key=sort_key)
        manifest = {
            "schema": PARSED_MANIFEST_SCHEMA,
            "parser_version": PARSER_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "country_code": "IS",
            "source_families": {
                "registry_annual": {
                    "source_id": HISTORY_SOURCE_ID,
                    "frequency": "annual",
                    "measure": "case_notifications",
                    "dash_semantics": "not_applicable_skip",
                    "blank_semantics": "unknown_skip",
                },
                "registry_disease_monthly": {
                    "source_id": HISTORY_SOURCE_ID,
                    "frequency": "monthly",
                    "measure": "case_notifications",
                    "dash_semantics": "published_zero",
                    "blank_semantics": "unknown_skip",
                },
                "legacy_icd_monthly": {
                    "source_id": LEGACY_SOURCE_ID,
                    "frequency": "monthly",
                    "measure": "registered_diagnoses",
                    "dash_semantics": "published_zero",
                    "blank_semantics": "unknown_skip",
                    "legacy_projection": "excluded_reporting_basis_not_comparable",
                },
            },
            "raw_manifest_schema": _text((raw_manifest or {}).get("schema")),
            "raw_landing_sha256": _text((raw_manifest or {}).get("landing_sha256")),
            "files": file_manifest,
            "raw_hashes": dict(sorted(raw_hashes.items())),
            "counts": {
                "projection_rows": len(rows),
                "series_rows": len(series_rows),
                "quarantine_rows": len(context.quarantine),
                **dict(sorted(context.stats.items())),
            },
            "series_counts": dict(
                sorted(Counter(_text(row.get("SourceSeriesCode")) for row in series_rows).items())
            ),
            "quarantine": _quarantine_summary(context.quarantine),
        }
        return IcelandHistoryPreparedResult(
            rows=rows,
            series_rows=series_rows,
            quarantine=context.quarantine,
            manifest=manifest,
            raw_hashes=raw_hashes,
        )

    @staticmethod
    def write_outputs(
        result: IcelandHistoryPreparedResult,
        output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    ) -> dict[str, Path]:
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, Path] = {}
        for filename, rows in (
            ("rows.csv", result.rows),
            ("series_rows.csv", result.series_rows),
            ("quarantine.csv", result.quarantine),
        ):
            path = target / filename
            fieldnames = sorted({key for row in rows for key in row})
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                if fieldnames:
                    writer = csv.DictWriter(
                        handle, fieldnames=fieldnames, extrasaction="ignore"
                    )
                    writer.writeheader()
                    writer.writerows(rows)
            outputs[filename] = path.resolve()
        manifest_path = target / "manifest.json"
        # ``prepare_files`` resolves workbooks to absolute paths while it is
        # parsing them.  Those paths are useful in-memory, but persisting them
        # would bind the reviewed artifact to one checkout and disclose the
        # local machine layout.  Store paths relative to the parsed manifest so
        # the raw archive and normalized audit bundle remain movable together.
        manifest = {**result.manifest}
        manifest_files: list[dict[str, Any]] = []
        for raw_entry in result.manifest.get("files", []):
            entry = dict(raw_entry)
            raw_path = Path(_text(entry.get("path")))
            if raw_path:
                entry["path"] = Path(
                    os.path.relpath(
                        raw_path.resolve(),
                        start=manifest_path.parent.resolve(),
                    )
                ).as_posix()
            manifest_files.append(entry)
        manifest["files"] = manifest_files
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        outputs["manifest.json"] = manifest_path.resolve()
        return outputs

    async def import_rows(
        self,
        db: AsyncSession,
        rows: list[dict[str, Any]],
        *,
        db_latest_date: date | None = None,
        source_latest_date: date | None = None,
        force: bool = False,
    ) -> IcelandHistoryImportResult:
        """Upsert the safe annual/monthly case-notification projection.

        ``legacy_icd_monthly`` is rejected even if a caller bypasses
        :meth:`prepare_files`, because ``disease_records`` cannot encode its
        registered-diagnosis reporting basis.
        """

        del db_latest_date, force
        safe_rows = [
            row
            for row in rows
            if _text(row.get("SourceKind")) in {
                "registry_annual",
                "registry_disease_monthly",
            }
            and _text(row.get("Measure")) == "case_notifications"
        ]
        if not safe_rows:
            return IcelandHistoryImportResult(0, 0, source_latest_date)

        country_result = await db.execute(
            text("SELECT id FROM countries WHERE code = 'IS'")
        )
        country_row = country_result.fetchone()
        if country_row is None:
            raise ValueError("Country not found in database: IS")
        country_id = int(country_row[0])
        concept_ids = sorted({_text(row.get("DiseaseFull")) for row in safe_rows})
        disease_result = await db.execute(
            text(
                "SELECT name, id FROM diseases "
                "WHERE name = ANY(:concept_ids) AND is_active = true"
            ),
            {"concept_ids": concept_ids},
        )
        disease_ids = {str(row[0]): int(row[1]) for row in disease_result.fetchall()}
        current_result = await db.execute(
            text(
                """
                SELECT timezone('UTC', time)::date, disease_id
                FROM disease_records
                WHERE country_id = :country_id
                  AND COALESCE(metadata::jsonb ->> 'legacy_projection', '') =
                      'current_annual_dashboard_only'
                """
            ),
            {"country_id": country_id},
        )
        current_identities = {
            (row[0], int(row[1])) for row in current_result.fetchall()
        }

        upserts: list[dict[str, Any]] = []
        skipped = 0
        skipped_current_precedence = 0
        seen: set[tuple[str, int]] = set()
        for row in safe_rows:
            concept_id = _text(row.get("DiseaseFull"))
            disease_id = disease_ids.get(concept_id)
            if disease_id is None:
                skipped += 1
                continue
            report_date = datetime.strptime(_text(row.get("Date")), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if (report_date.date(), disease_id) in current_identities:
                # The current dashboard is authoritative for overlapping
                # annual identities (for example D236/MRSA in 2019).  History
                # remains losslessly available in the source-series table.
                skipped_current_precedence += 1
                continue
            identity = (report_date.isoformat(), disease_id)
            if identity in seen:
                raise ValueError(
                    "Iceland projection contains duplicate disease_records identity: "
                    f"{identity}"
                )
            seen.add(identity)
            upserts.append(
                {
                    "time": report_date,
                    "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": int(row["Cases"]),
                    "data_source": _text(row.get("Source")),
                    "metadata": json.dumps(
                        {
                            "source_kind": row.get("SourceKind"),
                            "source_series_code": row.get("SourceSeriesCode"),
                            "raw_disease_label": row.get("SourceRawDiseaseLabel"),
                            "incidence": row.get("Incidence"),
                            "source_url": row.get("SourceURL"),
                            "source_file": row.get("__source_file"),
                            "source_sha256": row.get("__source_sha256"),
                        },
                        ensure_ascii=False,
                    ),
                    "raw_data": json.dumps(row, ensure_ascii=False),
                }
            )
        if upserts:
            await db.execute(
                text(
                    """
                    INSERT INTO disease_records (
                        time, disease_id, country_id, cases, deaths, data_source,
                        metadata, raw_data, new_cases, new_deaths, recoveries,
                        active_cases, new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, NULL, :data_source,
                        :metadata, :raw_data, 0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        data_source = EXCLUDED.data_source,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    WHERE COALESCE(
                        disease_records.metadata::jsonb ->> 'legacy_projection',
                        ''
                    ) <> 'current_annual_dashboard_only'
                    """
                ),
                upserts,
            )
        latest = source_latest_date or max(
            (datetime.strptime(_text(row["Date"]), "%Y-%m-%d").date() for row in safe_rows),
            default=None,
        )
        return IcelandHistoryImportResult(
            len(upserts),
            skipped,
            latest,
            skipped_current_precedence=skipped_current_precedence,
        )


def raw_files_for_paths(paths: Iterable[Path | str]) -> list[IcelandHistoryRawFile]:
    """Build verified raw-file descriptors for already-downloaded catalogue files."""

    specs_by_filename = {spec.filename.casefold(): spec for spec in OFFICIAL_WORKBOOKS}
    raw_files: list[IcelandHistoryRawFile] = []
    for path_value in paths:
        path = Path(path_value).resolve()
        spec = specs_by_filename.get(path.name.casefold())
        if spec is None:
            raise ValueError(f"Path is not a canonical Iceland catalogue filename: {path.name}")
        raw_files.append(
            IcelandHistoryRawFile(
                key=spec.key,
                source_kind=spec.source_kind,
                filename=spec.filename,
                path=str(path),
                source_url=spec.url,
                sha256=_sha256_path(path),
                size_bytes=path.stat().st_size,
                media_type="application/vnd.ms-excel" if path.suffix.casefold() == ".xls" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                disease_key=spec.disease_key,
                validation_only=spec.validation_only,
            )
        )
    return raw_files


__all__ = [
    "ANNUAL_SERIES",
    "DEFAULT_ONTOLOGY_PATH",
    "DEFAULT_OUTPUT_DIR",
    "HISTORY_SOURCE_ID",
    "HISTORY_SOURCE_NAME",
    "IcelandHistoryImportResult",
    "IcelandHistoryPreparedResult",
    "IcelandHistoryProcessor",
    "IcelandWorkbookLayoutError",
    "LEGACY_SERIES",
    "LEGACY_SOURCE_ID",
    "LEGACY_SOURCE_NAME",
    "MONTHLY_SERIES",
    "PARSER_VERSION",
    "raw_files_for_paths",
]
