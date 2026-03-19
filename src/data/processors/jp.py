"""JP weekly updater.

This updater only handles incremental updates from the crawler's current-output
CSV. Historical backfill files live under ``data/history`` and are imported
only by the dedicated history scripts.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers import JapanIDWRCrawler

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/jp/weekly_cases_standardized.csv"
DEFAULT_REPORTING_AREA = "総数"
DEFAULT_SOURCE_NAME = "Japan NIID Weekly Sentinel"


@dataclass
class JPUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass
class JPUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def _parse_int(value: object) -> Optional[int]:
    txt = _norm_text(value)
    if not txt:
        return None
    try:
        return int(float(txt))
    except ValueError:
        return None


def _mmwr_week_end_date(year: int, week: int) -> date:
    jan_4 = date(year, 1, 4)
    week_1_start = jan_4 - timedelta(days=(jan_4.weekday() + 1) % 7)
    return week_1_start + timedelta(weeks=week - 1, days=6)


class JPWeeklyUpdater:
    """Read JP current weekly rows from crawler output and import."""

    def __init__(
        self,
        *,
        country_code: str = "JP",
        reporting_area: str = DEFAULT_REPORTING_AREA,
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
    ) -> None:
        self.country_code = country_code.upper()
        self.reporting_area = reporting_area
        self.source_name = source_name
        self.output_csv = output_csv
    def refresh_source(self, *, source: str = "jp_weekly", run_external: bool = False, force: bool = False) -> JPUpdateFetchResult:
        logs: List[str] = []
        source_key = (source or "jp_weekly").strip().lower()
        crawler = JapanIDWRCrawler()

        if source_key == "local":
            logs.append(f"[local] using existing standardized CSV: {self.output_csv}")
        else:
            try:
                fetch_summary = crawler.crawl_standardized_csv(
                    self.output_csv,
                    reporting_area=self.reporting_area,
                    force=force,
                )
                logs.append(
                    f"[crawler] fetched {fetch_summary.row_count} rows from {fetch_summary.csv_url}; latest={fetch_summary.latest_date}"
                )
                logs.extend(fetch_summary.debug_logs)
            except Exception as exc:
                raise RuntimeError(
                    "JP source refresh failed. "
                    f"Entry page: {crawler.page_url}. "
                    f"Reason: {exc}"
                ) from exc

        rows = self._load_standardized_rows(self.output_csv)
        latest = self._latest_row_date(rows)

        return JPUpdateFetchResult(
            rows=rows,
            source_latest_date=latest,
            source_csv=self.output_csv,
            script_logs=logs,
        )

    def _load_standardized_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(
                f"JP crawler output not found: {csv_path}. "
                "Please run the JP crawler in globalID2 first."
            )
        rows: List[Dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                area = _norm_text(row.get("Reporting Area", ""))
                if area != self.reporting_area:
                    continue

                disease = _norm_text(row.get("Disease", ""))
                year = _parse_int(row.get("Current MMWR Year", ""))
                week = _parse_int(row.get("MMWR WEEK", ""))
                cases = _parse_int(row.get("Current week", ""))

                if not disease or year is None or week is None or cases is None:
                    continue
                if week <= 0 or week > 53:
                    continue

                rows.append(
                    {
                        "ReportingArea": area,
                        "MMWRYear": str(year),
                        "MMWRWeek": str(week),
                        "RawDiseaseLabel": disease,
                        "Cases": str(max(0, cases)),
                        "CurrentWeekFlag": _norm_text(row.get("Current week, flag", "")),
                        "Source": self.source_name,
                        "__source_file": csv_path.name,
                    }
                )

        rows.sort(key=lambda r: (r["MMWRYear"], r["MMWRWeek"], r["RawDiseaseLabel"]))
        return rows

    @staticmethod
    def _latest_row_date(rows: List[Dict[str, str]]) -> Optional[date]:
        latest: Optional[date] = None
        for row in rows:
            year = _parse_int(row.get("MMWRYear"))
            week = _parse_int(row.get("MMWRWeek"))
            if year is None or week is None:
                continue
            day = _mmwr_week_end_date(year, week)
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
        result = await db.execute(
            text(
                """
                SELECT dm.local_name, d.id
                FROM disease_mappings dm
                JOIN diseases d ON dm.disease_id = d.name
                WHERE dm.country_code = :code AND dm.is_active = true
                """
            ),
            {"code": self.country_code},
        )

        mapping: Dict[str, int] = {}
        for local_name, disease_db_id in result:
            key = _norm_text(local_name).lower()
            if key:
                mapping[key] = int(disease_db_id)
        return mapping

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> JPUpdateImportResult:
        if not rows:
            return JPUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        if not force and db_latest_date is not None and source_latest_date is not None and source_latest_date <= db_latest_date:
            return JPUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)

        upsert_rows: List[Dict[str, object]] = []
        skipped_unmapped = 0
        seen_keys: set[Tuple[datetime, int, int]] = set()

        for row in rows:
            year = _parse_int(row.get("MMWRYear"))
            week = _parse_int(row.get("MMWRWeek"))
            if year is None or week is None:
                continue

            day = datetime.combine(_mmwr_week_end_date(year, week), time.min).replace(tzinfo=timezone.utc)
            if not force and db_latest_date is not None and day.date() <= db_latest_date:
                continue

            label = _norm_text(row.get("RawDiseaseLabel", ""))
            disease_id = mapping_dict.get(label.lower())
            if disease_id is None:
                skipped_unmapped += 1
                continue

            cases = _parse_int(row.get("Cases", ""))
            metadata_obj = {
                "raw_disease_label": label,
                "reporting_area": row.get("ReportingArea", ""),
                "mmwr_year": row.get("MMWRYear", ""),
                "mmwr_week": row.get("MMWRWeek", ""),
                "current_week_flag": row.get("CurrentWeekFlag", ""),
                "source_file": row.get("__source_file", ""),
            }

            key = (day, disease_id, country_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            upsert_rows.append(
                {
                    "time": day,
                    "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": cases if cases is not None else 0,
                    "deaths": 0,
                    "data_source": row.get("Source", self.source_name),
                    "metadata": json.dumps(metadata_obj),
                    "raw_data": json.dumps(row),
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
            "JP weekly import complete: upserted {} rows, skipped_unmapped {}",
            imported,
            skipped_unmapped,
        )

        return JPUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )
