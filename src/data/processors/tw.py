"""Taiwan, China monthly updater.

Taiwan, China CDC NIDSS open-data CSV files contain historical monthly detail rows
by disease, county, town, sex, import status, and age group.  The crawler
aggregates those detail rows to national monthly totals.  NIDSS data can be
revised, so imports upsert all fetched rows unconditionally.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers.tw import DEFAULT_SOURCE_NAME, TWFetchSummary, TaiwanNIDSSCrawler
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)

logger = get_logger(__name__)

MAPPING_SOURCE_ID = "SRC_TW_NIDSS"

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/tw/taiwan_national_monthly.csv"


@dataclass
class TWUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass
class TWUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    txt = _norm_text(value).replace(",", "")
    if not txt:
        return None
    try:
        return int(float(txt))
    except ValueError:
        return None


def _parse_date(row: Dict[str, str]) -> Optional[date]:
    date_text = _norm_text(row.get("Date"))
    if date_text:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(date_text, fmt).date()
            except ValueError:
                pass

    year = _parse_int(row.get("Year"))
    month = _parse_int(row.get("Month"))
    if year is not None and month is not None and 1 <= month <= 12:
        return date(year, month, 1)
    return None


class TWMonthlyUpdater:
    """Read Taiwan, China NIDSS national monthly rows and import them."""

    series_registered_rows_only = True

    def __init__(
        self,
        *,
        country_code: str = "TW",
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
    ) -> None:
        self.country_code = country_code.upper()
        self.source_name = source_name
        self.output_csv = output_csv

    @staticmethod
    def _default_recent_months() -> List[Tuple[int, int]]:
        now = datetime.now()
        months_to_fetch: List[Tuple[int, int]] = []
        for delta in range(3):
            month = now.month - delta
            year = now.year
            if month <= 0:
                month += 12
                year -= 1
            months_to_fetch.append((year, month))
        return sorted(set(months_to_fetch))

    def _resolve_requested_months(
        self,
        months: Optional[List[Tuple[int, int]]],
    ) -> List[Tuple[int, int]]:
        return sorted(set(months)) if months is not None else self._default_recent_months()

    def _rows_cover_months(
        self,
        rows: List[Dict[str, str]],
        months: List[Tuple[int, int]],
    ) -> bool:
        requested = set(months)
        present = {
            (parsed.year, parsed.month)
            for row in rows
            if (parsed := _parse_date(row)) is not None
        }
        return requested.issubset(present)

    def _filter_rows_for_months(
        self,
        rows: List[Dict[str, str]],
        months: List[Tuple[int, int]],
    ) -> List[Dict[str, str]]:
        requested = set(months)
        return [
            row
            for row in rows
            if (parsed := _parse_date(row)) is not None
            and (parsed.year, parsed.month) in requested
        ]

    def refresh_source(
        self,
        *,
        source: str = "nidss_open_data",
        run_external: bool = False,
        force: bool = False,
        months: Optional[List[Tuple[int, int]]] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> TWUpdateFetchResult:
        """Fetch Taiwan, China NIDSS open-data CSVs and prepare national monthly rows."""
        logs: List[str] = []
        requested_months = self._resolve_requested_months(months)
        actual_raw_dir = (
            Path(raw_dir) if raw_dir is not None else ROOT / "data/raw" / self.country_code.lower()
        )

        prior_rows: List[Dict[str, str]] = []
        if self.output_csv.exists():
            try:
                prior_rows = self._load_rows(self.output_csv)
                logs.append(f"[cache] loaded {len(prior_rows)} rows from existing CSV")
            except Exception as exc:
                logs.append(
                    f"[cache] unable to read existing CSV: {type(exc).__name__}: {exc}"
                )

        crawler = TaiwanNIDSSCrawler(save_raw=save_raw, raw_dir=actual_raw_dir)
        live_rows: List[Dict[str, str]] = []
        live_error: Optional[Exception] = None
        try:
            fetch_summary: TWFetchSummary = crawler.crawl_monthly_national(
                self.output_csv,
                months=requested_months,
            )
            logs.append(
                f"[crawler] fetched {fetch_summary.row_count} rows; "
                f"diseases={fetch_summary.diseases_fetched}; "
                f"months={len(requested_months)}; latest={fetch_summary.latest_date}"
            )
            if save_raw:
                logs.append(f"[crawler] raw CSVs archived under {actual_raw_dir}")
            live_rows = self._filter_rows_for_months(
                self._load_rows(self.output_csv),
                requested_months,
            )
        except Exception as exc:
            live_error = exc
            logs.append(f"[crawler] live fetch failed: {type(exc).__name__}: {exc}")

        prior_candidate = (
            self._filter_rows_for_months(prior_rows, requested_months)
            if prior_rows and self._rows_cover_months(prior_rows, requested_months)
            else []
        )

        candidates: List[Tuple[str, List[Dict[str, str]], int]] = []
        if live_rows:
            candidates.append(("live fetch", live_rows, 1))
        if prior_candidate:
            candidates.append(("previous CSV snapshot", prior_candidate, 0))

        if not candidates:
            if live_error is not None:
                raise live_error
            raise RuntimeError("TW crawler produced no usable rows")

        selected_label, rows, _ = max(candidates, key=lambda item: (len(item[1]), item[2]))
        if selected_label != "live fetch":
            logs.append(f"[recovery] using {selected_label} with {len(rows)} rows")

        return TWUpdateFetchResult(
            rows=rows,
            source_latest_date=self._latest_row_date(rows),
            source_csv=self.output_csv,
            script_logs=logs,
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(
                f"TW crawler output not found: {csv_path}. Please run the TW crawler first."
            )

        rows: List[Dict[str, str]] = []
        today = datetime.now(timezone.utc).date()
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                disease = _norm_text(row.get("Disease"))
                report_date = _parse_date(row)
                cases = _parse_int(row.get("Cases"))
                if not disease or report_date is None or cases is None:
                    continue

                local_cases = _parse_int(row.get("LocalCases")) or 0
                imported_cases = _parse_int(row.get("ImportedCases")) or 0
                is_open_month = (report_date.year, report_date.month) == (
                    today.year,
                    today.month,
                )
                rows.append(
                    {
                        "Date": report_date.isoformat(),
                        "RawDiseaseLabel": disease,
                        "DiseaseCode": _norm_text(row.get("DiseaseCode")),
                        "Year": str(report_date.year),
                        "Month": str(report_date.month),
                        "Cases": str(max(0, cases)),
                        "LocalCases": str(max(0, local_cases)),
                        "ImportedCases": str(max(0, imported_cases)),
                        "Source": _norm_text(row.get("Source")) or self.source_name,
                        "SourceURL": _norm_text(row.get("SourceURL")),
                        # NIDSS warns that generated results can change after
                        # later corrections.  That makes closed months
                        # revisable; only the open month is also incomplete.
                        "DatasetStatus": (
                            "provisional" if is_open_month else "closed_revisable"
                        ),
                        "IsProvisional": "true" if is_open_month else "false",
                    }
                )

        rows.sort(key=lambda r: (r["Date"], r["RawDiseaseLabel"]))
        return rows

    @staticmethod
    def _latest_row_date(rows: List[Dict[str, str]]) -> Optional[date]:
        latest: Optional[date] = None
        for row in rows:
            try:
                day = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if latest is None or day > latest:
                latest = day
        return latest

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
        max_time = result.scalar()
        if max_time is None:
            return None
        return max_time.date()

    async def get_db_months(self, db: AsyncSession) -> Set[Tuple[int, int]]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT
                    EXTRACT(YEAR  FROM dr.time)::int AS yr,
                    EXTRACT(MONTH FROM dr.time)::int AS mo
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

    async def _load_mapping_dict(self, db: AsyncSession) -> Dict[str, int]:
        return await load_country_mapping_dict(
            db, self.country_code, source_id=MAPPING_SOURCE_ID
        )

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> TWUpdateImportResult:
        """Upsert Taiwan, China national monthly rows into ``disease_records``."""
        if not rows:
            return TWUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)

        grouped: Dict[Tuple[datetime, int, int], Dict[str, object]] = {}
        skipped_unmapped = 0

        for row in rows:
            try:
                day = datetime.strptime(row.get("Date", ""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            label = _norm_text(row.get("RawDiseaseLabel", ""))
            code = _norm_text(row.get("DiseaseCode", ""))
            disease_id = mapping_dict.get(
                normalize_mapping_key(label)
            ) or mapping_dict.get(normalize_mapping_key(code))
            if disease_id is None:
                skipped_unmapped += 1
                continue

            cases = _parse_int(row.get("Cases")) or 0
            local_cases = _parse_int(row.get("LocalCases")) or 0
            imported_cases = _parse_int(row.get("ImportedCases")) or 0
            key = (day, disease_id, country_id)

            bucket = grouped.setdefault(
                key,
                {
                    "time": day,
                    "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": 0,
                    "deaths": None,
                    "data_source": row.get("Source", self.source_name),
                    "local_cases": 0,
                    "imported_cases": 0,
                    "raw_disease_labels": [],
                    "disease_codes": [],
                    "source_urls": [],
                    "raw_rows": [],
                },
            )
            bucket["cases"] = int(bucket["cases"]) + max(0, cases)
            bucket["local_cases"] = int(bucket["local_cases"]) + max(0, local_cases)
            bucket["imported_cases"] = int(bucket["imported_cases"]) + max(0, imported_cases)
            if label and label not in bucket["raw_disease_labels"]:
                bucket["raw_disease_labels"].append(label)
            if code and code not in bucket["disease_codes"]:
                bucket["disease_codes"].append(code)
            source_url = _norm_text(row.get("SourceURL"))
            if source_url and source_url not in bucket["source_urls"]:
                bucket["source_urls"].append(source_url)
            bucket["raw_rows"].append(row)

        upsert_rows: List[Dict[str, object]] = []
        for bucket in grouped.values():
            metadata_obj = {
                "raw_disease_labels": bucket["raw_disease_labels"],
                "disease_codes": bucket["disease_codes"],
                "local_cases": bucket["local_cases"],
                "imported_cases": bucket["imported_cases"],
                "source_urls": bucket["source_urls"],
                "death_reporting": "not_provided_by_source",
                "death_reporting_note": "Taiwan CDC case feed used here reports cases, not death counts.",
            }
            upsert_rows.append(
                {
                    "time": bucket["time"],
                    "disease_id": bucket["disease_id"],
                    "country_id": bucket["country_id"],
                    "cases": bucket["cases"],
                    "deaths": bucket["deaths"],
                    "data_source": bucket["data_source"],
                    "metadata": json.dumps(metadata_obj, ensure_ascii=False),
                    "raw_data": json.dumps(bucket["raw_rows"], ensure_ascii=False),
                }
            )

        if upsert_rows:
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
                upsert_rows,
            )

        imported = len(upsert_rows)
        logger.info(
            "TW monthly import complete: upserted {} rows, skipped_unmapped {}",
            imported,
            skipped_unmapped,
        )
        return TWUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )
