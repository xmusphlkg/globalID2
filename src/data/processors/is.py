"""Updater for Iceland's mixed-frequency current surveillance dashboards.

All three public dashboard feeds are retained in the source-series store.  The
legacy ``disease_records`` projection intentionally receives only the annual
dashboard rows: a table key there does not include source or frequency, so
writing January monthly/weekly rows beside annual values could overwrite facts.
"""

from __future__ import annotations

import csv
import importlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)


_crawler_module = importlib.import_module("src.data.crawlers.is")
ANNUAL_SOURCE_NAME = _crawler_module.ANNUAL_SOURCE_NAME
IcelandDOHCrawler = _crawler_module.IcelandDOHCrawler
SERIES_DEFINITIONS = _crawler_module.SERIES_DEFINITIONS
SOURCE_IDS = _crawler_module.SOURCE_IDS
SOURCE_NAMES = _crawler_module.SOURCE_NAMES
SOURCE_SCOPE_ANNUAL = _crawler_module.SOURCE_SCOPE_ANNUAL
SOURCE_SCOPE_RESPIRATORY = _crawler_module.SOURCE_SCOPE_RESPIRATORY
SOURCE_SCOPE_STI = _crawler_module.SOURCE_SCOPE_STI
SUPPORTED_SOURCE_SCOPES = _crawler_module.SUPPORTED_SOURCE_SCOPES

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/is/iceland_doh_current.csv"


@dataclass
class ISUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]
    source_row_counts: Dict[str, int]
    source_last_refresh: Dict[str, Optional[str]]
    schema_fingerprints: Dict[str, str]


@dataclass
class ISUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool
    skipped_incompatible_projection: int = 0


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    text_value = _norm_text(value).replace(",", "")
    if not text_value:
        return None
    try:
        numeric = float(text_value)
    except ValueError:
        return None
    if numeric < 0 or not numeric.is_integer():
        return None
    return int(numeric)


def _parse_date(row: Mapping[str, object]) -> Optional[date]:
    value = _norm_text(row.get("Date"))
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass

    year = _parse_int(row.get("Year"))
    month = _parse_int(row.get("Month")) or 1
    if year is None:
        return None
    try:
        return date(year, month, 1)
    except ValueError:
        return None


def _source_scopes(source: str) -> Tuple[str, ...]:
    normalized = _norm_text(source).lower()
    aliases = {
        "": "all",
        "all": "all",
        "is": "all",
        "doh": "all",
        "is_doh": "all",
        "current": "all",
        "annual": SOURCE_SCOPE_ANNUAL,
        "sti": SOURCE_SCOPE_STI,
        "respiratory": SOURCE_SCOPE_RESPIRATORY,
        SOURCE_SCOPE_ANNUAL: SOURCE_SCOPE_ANNUAL,
        SOURCE_SCOPE_STI: SOURCE_SCOPE_STI,
        SOURCE_SCOPE_RESPIRATORY: SOURCE_SCOPE_RESPIRATORY,
    }
    resolved = aliases.get(normalized)
    if resolved is None:
        raise ValueError(f"Unsupported Iceland current source: {source}")
    return SUPPORTED_SOURCE_SCOPES if resolved == "all" else (resolved,)


class ISMultiFrequencyUpdater:
    """Refresh and import annual, monthly and weekly Iceland source facts."""

    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_geography_key = "country:IS:national"
    ontology_source_id = {
        SOURCE_NAMES[scope]: source_id for scope, source_id in SOURCE_IDS.items()
    }
    # Current Power BI snapshots are small and retrospectively revised.  A
    # complete refresh is both simpler and safer than a date-gated request.
    refresh_recent_months = 12
    full_history_start_year = 2010

    def __init__(
        self,
        *,
        country_code: str = "IS",
        source_name: str = ANNUAL_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
    ) -> None:
        self.country_code = country_code.upper()
        self.source_name = source_name
        self.output_csv = Path(output_csv)
        cfg = get_country_bootstrap_config(self.country_code)
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        self.full_history_start_year = int(
            crawler_cfg.get("full_history_start_year") or self.full_history_start_year
        )
        self.refresh_recent_months = int(
            crawler_cfg.get("refresh_recent_months") or self.refresh_recent_months
        )
        self.refresh_recent_weeks = int(
            crawler_cfg.get("refresh_recent_weeks") or 52
        )

    @staticmethod
    def _scope_filter(
        rows: Iterable[Dict[str, str]], scopes: Sequence[str]
    ) -> List[Dict[str, str]]:
        selected = set(scopes)
        return [row for row in rows if _norm_text(row.get("SourceScope")) in selected]

    @staticmethod
    def _filter_start_year(
        rows: Iterable[Dict[str, str]], start_year: Optional[int]
    ) -> List[Dict[str, str]]:
        if start_year is None:
            return list(rows)
        return [
            row
            for row in rows
            if (parsed := _parse_date(row)) is not None and parsed.year >= int(start_year)
        ]

    def refresh_source(
        self,
        *,
        source: str = "all",
        run_external: bool = False,
        force: bool = False,
        months: Optional[List[Tuple[int, int]]] = None,
        history: bool = False,
        start_year: Optional[int] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> ISUpdateFetchResult:
        """Refresh selected public reports and return their normalized rows.

        ``months`` is accepted for the shared pipeline contract but deliberately
        does not constrain the source query.  These reports are compact and can
        revise old periods; every run obtains the complete selected source and
        lets idempotent upserts apply corrections.
        """

        del run_external, force, months, history
        scopes = _source_scopes(source)
        logs = [
            f"[plan] selected Iceland source scopes: {', '.join(scopes)}",
            "[plan] full selected-source refresh (authoritative revisions enabled)",
        ]

        prior_rows: List[Dict[str, str]] = []
        if self.output_csv.exists():
            try:
                prior_rows = self._load_rows(self.output_csv)
                logs.append(f"[cache] loaded {len(prior_rows)} rows from current snapshot")
            except Exception as exc:
                logs.append(
                    f"[cache] unable to read current snapshot: {type(exc).__name__}: {exc}"
                )

        actual_raw_dir = (
            Path(raw_dir)
            if raw_dir is not None
            else ROOT / "data/raw" / self.country_code.lower()
        )
        crawler = IcelandDOHCrawler(save_raw=save_raw, raw_dir=actual_raw_dir)
        live_error: Optional[Exception] = None
        live_rows: List[Dict[str, str]] = []
        source_row_counts: Dict[str, int] = {}
        source_last_refresh: Dict[str, Optional[str]] = {}
        schema_fingerprints: Dict[str, str] = {}
        try:
            summary = crawler.crawl_national(
                self.output_csv,
                source_scopes=scopes,
            )
            live_rows = self._load_rows(self.output_csv)
            source_row_counts = dict(summary.source_row_counts)
            source_last_refresh = dict(summary.source_last_refresh)
            schema_fingerprints = dict(summary.schema_fingerprints)
            logs.append(
                f"[crawler] prepared {len(live_rows)} rows; latest={summary.latest_date}"
            )
            if save_raw:
                logs.append(f"[crawler] raw artifacts archived under {actual_raw_dir}")

            if set(scopes) != set(SUPPORTED_SOURCE_SCOPES):
                untouched = [
                    row
                    for row in prior_rows
                    if _norm_text(row.get("SourceScope")) not in set(scopes)
                ]
                crawler.write_rows(self.output_csv, [*untouched, *live_rows])
                logs.append(
                    f"[snapshot] preserved {len(untouched)} rows from unrequested scopes"
                )
        except Exception as exc:
            live_error = exc
            logs.append(f"[crawler] live fetch failed: {type(exc).__name__}: {exc}")

        selected_rows = self._scope_filter(live_rows, scopes)
        if not selected_rows and prior_rows:
            selected_rows = self._scope_filter(prior_rows, scopes)
            if selected_rows:
                logs.append(
                    f"[recovery] using {len(selected_rows)} cached rows for selected scopes"
                )
        if not selected_rows:
            if live_error is not None:
                raise live_error
            raise RuntimeError("Iceland current crawler produced no usable rows")

        selected_rows = self._filter_start_year(selected_rows, start_year)
        if not selected_rows:
            raise RuntimeError("No Iceland current rows remained after start-year filtering")

        if not source_row_counts:
            for scope in scopes:
                source_row_counts[scope] = sum(
                    1 for row in selected_rows if row.get("SourceScope") == scope
                )
        return ISUpdateFetchResult(
            rows=selected_rows,
            source_latest_date=self._latest_row_date(selected_rows),
            source_csv=self.output_csv,
            script_logs=logs,
            source_row_counts=source_row_counts,
            source_last_refresh=source_last_refresh,
            schema_fingerprints=schema_fingerprints,
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(f"Iceland crawler output not found: {csv_path}")
        rows: List[Dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for source_row in csv.DictReader(handle):
                report_date = _parse_date(source_row)
                cases = _parse_int(source_row.get("Cases"))
                label = _norm_text(
                    source_row.get("Disease") or source_row.get("RawDiseaseLabel")
                )
                disease_code = _norm_text(
                    source_row.get("DiseaseCode")
                    or source_row.get("SourceSeriesCode")
                )
                scope = _norm_text(source_row.get("SourceScope"))
                if (
                    report_date is None
                    or cases is None
                    or not label
                    or not disease_code
                    or scope not in SUPPORTED_SOURCE_SCOPES
                ):
                    continue
                row = {
                    key: _norm_text(value)
                    for key, value in source_row.items()
                    if key not in {None, ""}
                }
                row.update(
                    {
                        "Date": report_date.isoformat(),
                        "RawDiseaseLabel": label,
                        "DiseaseCode": disease_code,
                        "SourceSeriesCode": _norm_text(
                            source_row.get("SourceSeriesCode")
                        )
                        or disease_code,
                        "Cases": str(cases),
                        "SourceScope": scope,
                        "SourceId": _norm_text(source_row.get("SourceId"))
                        or SOURCE_IDS[scope],
                        "Source": _norm_text(source_row.get("Source"))
                        or SOURCE_NAMES[scope],
                        "GeographyKey": _norm_text(source_row.get("GeographyKey"))
                        or "country:IS:national",
                        "Dimensions": _norm_text(source_row.get("Dimensions")) or "{}",
                        "Measure": _norm_text(source_row.get("Measure"))
                        or "case_notifications",
                        "Unit": _norm_text(source_row.get("Unit")) or "count",
                        "AuthoritativeRevision": _norm_text(
                            source_row.get("AuthoritativeRevision")
                        )
                        or "true",
                    }
                )
                rows.append(row)
        rows.sort(
            key=lambda row: (
                row["Date"], row["SourceScope"], row["DiseaseCode"]
            )
        )
        return rows

    @staticmethod
    def _latest_row_date(rows: Iterable[Mapping[str, object]]) -> Optional[date]:
        return max(
            (parsed for row in rows if (parsed := _parse_date(row)) is not None),
            default=None,
        )

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        result = await db.execute(
            text(
                """
                SELECT MAX(dr.time)
                FROM disease_records dr
                JOIN countries c ON c.id = dr.country_id
                WHERE c.code = :code
                """
            ),
            {"code": self.country_code},
        )
        value = result.scalar()
        return value.date() if value is not None else None

    async def get_db_months(self, db: AsyncSession) -> Set[Tuple[int, int]]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT
                    EXTRACT(YEAR FROM dr.time)::int,
                    EXTRACT(MONTH FROM dr.time)::int
                FROM disease_records dr
                JOIN countries c ON c.id = dr.country_id
                WHERE c.code = :code
                """
            ),
            {"code": self.country_code},
        )
        return {(int(row[0]), int(row[1])) for row in result.fetchall()}

    async def _get_country_id(self, db: AsyncSession) -> int:
        result = await db.execute(
            text("SELECT id FROM countries WHERE code = :code"),
            {"code": self.country_code},
        )
        row = result.fetchone()
        if row is None:
            raise ValueError(f"Country not found in database: {self.country_code}")
        return int(row[0])

    async def _get_stable_series_mapping(
        self, db: AsyncSession, *, source_id: str
    ) -> Dict[str, str]:
        """Resolve source labels/codes to stable registry series identifiers."""

        result = await db.execute(
            text(
                """
                SELECT dm.local_name, dm.series_id
                FROM disease_mappings AS dm
                WHERE dm.country_code = :country_code
                  AND dm.source_id = :source_id
                  AND dm.is_active = true
                  AND dm.series_id IS NOT NULL
                ORDER BY dm.local_name, dm.series_id
                """
            ),
            {"country_code": self.country_code, "source_id": source_id},
        )
        mapping: Dict[str, str] = {}
        for local_name, series_id in result:
            key = normalize_mapping_key(local_name)
            stable_id = _norm_text(series_id)
            if not key or not stable_id:
                continue
            existing = mapping.get(key)
            if existing is not None and existing != stable_id:
                raise ValueError(
                    "Ambiguous Iceland source-series mapping for "
                    f"{local_name!r}: {existing} vs {stable_id}"
                )
            mapping[key] = stable_id
        return mapping

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> ISUpdateImportResult:
        """Upsert the explicit annual-only legacy projection.

        The source-series dual write, invoked by ``CrawlService``, persists the
        complete input list after this method returns.
        """

        del force
        annual_rows = [
            row for row in rows if row.get("SourceScope") == SOURCE_SCOPE_ANNUAL
        ]
        if not annual_rows:
            return ISUpdateImportResult(
                inserted_or_updated=0,
                skipped_unmapped=0,
                db_latest_date=db_latest_date,
                source_latest_date=source_latest_date,
                imported_new_data=False,
            )

        country_id = await self._get_country_id(db)
        mapping = await load_country_mapping_dict(
            db,
            self.country_code,
            source_id=SOURCE_IDS[SOURCE_SCOPE_ANNUAL],
        )
        stable_series_mapping = await self._get_stable_series_mapping(
            db, source_id=SOURCE_IDS[SOURCE_SCOPE_ANNUAL]
        )
        preserved_monthly_result = await db.execute(
            text(
                """
                SELECT timezone('UTC', time)::date, disease_id
                FROM disease_records
                WHERE country_id = :country_id
                  AND COALESCE(metadata::jsonb ->> 'source_kind', '') =
                      'registry_disease_monthly'
                """
            ),
            {"country_id": country_id},
        )
        preserved_monthly_identities = {
            (row[0], int(row[1])) for row in preserved_monthly_result.fetchall()
        }
        upserts: List[Dict[str, object]] = []
        skipped_unmapped = 0
        skipped_incompatible_projection = 0
        seen: Set[Tuple[datetime, int, int]] = set()

        for row in annual_rows:
            report_date = _parse_date(row)
            cases = _parse_int(row.get("Cases"))
            if report_date is None or cases is None:
                continue
            label = _norm_text(row.get("RawDiseaseLabel"))
            code = _norm_text(row.get("DiseaseCode"))
            disease_id = mapping.get(normalize_mapping_key(code)) or mapping.get(
                normalize_mapping_key(label)
            )
            if disease_id is None:
                skipped_unmapped += 1
                continue
            stable_series_code = stable_series_mapping.get(
                normalize_mapping_key(code)
            ) or stable_series_mapping.get(normalize_mapping_key(label))
            if stable_series_code is None:
                raise ValueError(
                    "No stable Iceland source-series mapping for annual row "
                    f"code={code!r} label={label!r}"
                )
            if (report_date, disease_id) in preserved_monthly_identities:
                # ``disease_records`` cannot store both a January monthly fact
                # and an annual total at the same concept/date identity. Keep
                # the more granular historical notification in compatibility
                # storage; the complete annual dashboard fact remains in the
                # source-series observation table.
                skipped_incompatible_projection += 1
                continue
            report_time = datetime.combine(
                report_date, datetime.min.time(), tzinfo=timezone.utc
            )
            key = (report_time, disease_id, country_id)
            if key in seen:
                raise ValueError(f"Duplicate Iceland annual legacy identity: {key}")
            seen.add(key)

            metadata = {
                "legacy_projection": "current_annual_dashboard_only",
                "source_scope": row.get("SourceScope"),
                "source_id": row.get("SourceId"),
                "source_series_code": stable_series_code,
                "source_native_series_code": row.get("SourceSeriesCode") or code,
                "raw_disease_label": label,
                "frequency": row.get("Frequency"),
                "measure": row.get("Measure"),
                "reporting_basis": row.get("ReportingBasis"),
                "period_type": row.get("PeriodType"),
                "period_value": row.get("PeriodValue"),
                "source_last_refresh": row.get("SourceLastRefresh"),
                "retrieved_at": row.get("RetrievedAt"),
                "schema_fingerprint": row.get("SchemaFingerprint"),
                "resource_key": row.get("ResourceKey"),
                "model_id": row.get("ModelId"),
                "dataset_id": row.get("DatasetId"),
                "report_id": row.get("ReportId"),
                "source_url": row.get("SourceURL"),
                "raw_artifact": row.get("RawArtifact"),
                "authoritative_revision": True,
                "death_reporting": "not_provided_by_source",
            }
            upserts.append(
                {
                    "time": report_time,
                    "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": cases,
                    "deaths": None,
                    "data_source": row.get("Source") or ANNUAL_SOURCE_NAME,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "raw_data": json.dumps(row, ensure_ascii=False),
                }
            )

        if upserts:
            await db.execute(
                text(
                    """
                    INSERT INTO disease_records (
                        time, disease_id, country_id, cases, deaths,
                        data_source, metadata, raw_data,
                        new_cases, new_deaths, recoveries, active_cases, new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, :deaths,
                        :data_source, :metadata, :raw_data,
                        0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        data_source = EXCLUDED.data_source,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    """
                ),
                upserts,
            )

        return ISUpdateImportResult(
            inserted_or_updated=len(upserts),
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=bool(upserts),
            skipped_incompatible_projection=skipped_incompatible_projection,
        )


# The aliases make the mixed-grain nature explicit for new code while keeping
# compatibility with shared updater factories that conventionally use a
# ``MonthlyUpdater`` suffix.
ISDataUpdater = ISMultiFrequencyUpdater
ISMonthlyUpdater = ISMultiFrequencyUpdater


__all__ = [
    "DEFAULT_OUTPUT_CSV",
    "ISDataUpdater",
    "ISMonthlyUpdater",
    "ISMultiFrequencyUpdater",
    "ISUpdateFetchResult",
    "ISUpdateImportResult",
    "SERIES_DEFINITIONS",
]
