"""AU monthly updater.

This updater only handles incremental updates from crawler outputs that already
exist inside globalID2 (data/raw/au/australia_national_data.csv).
It does not run external scripts or external data pipelines.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers import AustraliaNINDSSCrawler

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/raw/au/australia_national_data.csv"
DEFAULT_SOURCE_NAME = "Australia NINDSS (location aggregated)"


@dataclass
class AUUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass
class AUUpdateImportResult:
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
    txt = _norm_text(value)
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


class AUMonthlyUpdater:
    """Read AU national monthly rows from local crawler output and import."""

    def __init__(
        self,
        *,
        country_code: str = "AU",
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
    ) -> None:
        self.country_code = country_code.upper()
        self.source_name = source_name
        self.output_csv = output_csv

    def refresh_source(self, *, source: str = "au", run_external: bool = False) -> AUUpdateFetchResult:
        logs: List[str] = []
        crawler = AustraliaNINDSSCrawler()
        fetch_summary = crawler.crawl_monthly_national_csv(self.output_csv)
        logs.append(
            f"[crawler] fetched/aggregated {fetch_summary.row_count} rows from {fetch_summary.csv_url}; latest={fetch_summary.latest_date}"
        )

        rows = self._load_rows(self.output_csv)
        latest = self._latest_row_date(rows)

        return AUUpdateFetchResult(
            rows=rows,
            source_latest_date=latest,
            source_csv=self.output_csv,
            script_logs=logs,
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(
                f"AU crawler output not found: {csv_path}. "
                "Please run the AU crawler in globalID2 first."
            )
        rows: List[Dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                disease = _norm_text(row.get("Disease"))
                report_date = _parse_date(row)
                cases = _parse_int(row.get("Cases"))
                if not disease or report_date is None or cases is None:
                    continue

                rows.append(
                    {
                        "Date": report_date.isoformat(),
                        "RawDiseaseLabel": disease,
                        "DiseaseFull": _norm_text(row.get("DiseaseFull")) or disease,
                        "Cases": str(max(0, cases)),
                        "Group": _norm_text(row.get("Group")),
                        "Incidence": _norm_text(row.get("Incidence")),
                        "Population": _norm_text(row.get("Population")),
                        "Source": self.source_name,
                        "__source_file": csv_path.name,
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

    async def _get_country_id(self, db: AsyncSession) -> int:
        result = await db.execute(text("SELECT id FROM countries WHERE code = :code"), {"code": self.country_code})
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
    ) -> AUUpdateImportResult:
        if not rows:
            return AUUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        if not force and db_latest_date is not None and source_latest_date is not None and source_latest_date <= db_latest_date:
            return AUUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)

        upsert_rows: List[Dict[str, object]] = []
        skipped_unmapped = 0
        seen_keys: set[Tuple[datetime, int, int]] = set()

        for row in rows:
            try:
                day = datetime.strptime(row.get("Date", ""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if not force and db_latest_date is not None and day.date() <= db_latest_date:
                continue

            label = _norm_text(row.get("RawDiseaseLabel", ""))
            disease_id = mapping_dict.get(label.lower())
            if disease_id is None:
                skipped_unmapped += 1
                continue

            cases = _parse_int(row.get("Cases", ""))
            incidence = None
            try:
                incidence = float(row.get("Incidence", "")) if row.get("Incidence", "").strip() else None
            except ValueError:
                incidence = None

            metadata_obj = {
                "raw_disease_label": label,
                "disease_full": row.get("DiseaseFull", ""),
                "group": row.get("Group", ""),
                "population": row.get("Population", ""),
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
                    "incidence_rate": incidence,
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
                        data_source, incidence_rate, metadata, raw_data,
                        new_cases, new_deaths, recoveries, active_cases, new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, :deaths,
                        :data_source, :incidence_rate, :metadata, :raw_data,
                        0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        data_source = EXCLUDED.data_source,
                        incidence_rate = EXCLUDED.incidence_rate,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    """
                ),
                upsert_rows,
            )

        imported = len(upsert_rows)
        logger.info(
            "AU monthly import complete: upserted {} rows, skipped_unmapped {}",
            imported,
            skipped_unmapped,
        )

        return AUUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )


# Backward compatibility for older imports.
AUWeeklyUpdater = AUMonthlyUpdater
