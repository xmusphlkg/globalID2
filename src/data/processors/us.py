"""US processor — normalise and import US NNDSS weekly data.

HTTP fetching is delegated to crawlers/us.py (USNNDSSCrawler).
This module handles row normalisation, gating, and PostgreSQL upsert.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers.us import USNNDSSCrawler, DEFAULT_CSV_API_URL
logger = get_logger(__name__)

DEFAULT_REPORTING_AREA = "TOTAL"
DEFAULT_SOURCE_NAME = "US CDC NNDSS"
REPORT_TIME_UTC = time(hour=12)



@dataclass
class USUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_ref: str
    update_mode: str
    latest_date: Optional[date]


@dataclass
class USUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def _first_value(row: Dict[str, object], *keys: str) -> str:
    for key in keys:
        if key in row:
            value = _normalize_text(row.get(key))
            if value:
                return value
    return ""


def _parse_numeric(value: str) -> str:
    text = _normalize_text(value).replace(",", "")
    if not text:
        return ""
    try:
        numeric = float(text)
    except ValueError:
        return ""
    if numeric.is_integer():
        return str(int(numeric))
    return str(numeric)


def _parse_int(value: str) -> Optional[int]:
    text = _normalize_text(value).replace(",", "")
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        return None
    return int(numeric)


def _mmwr_week_end_date(year: int, week: int) -> date:
    jan_4 = date(year, 1, 4)
    week_1_start = jan_4 - timedelta(days=(jan_4.weekday() + 1) % 7)
    return week_1_start + timedelta(weeks=week - 1, days=6)


def _build_normalized_record(
    row: Dict[str, object],
    *,
    reporting_area_filter: str,
    source_name: str,
    update_mode: str,
    source_file: str,
) -> Optional[Dict[str, str]]:
    reporting_area = _first_value(row, "Reporting Area", "states")
    if reporting_area.upper() != reporting_area_filter.upper():
        return None

    year_text = _first_value(row, "Current MMWR Year", "year")
    week_text = _first_value(row, "MMWR WEEK", "week")
    label = _first_value(row, "Label", "label")
    if not year_text or not week_text or not label:
        return None

    try:
        year = int(float(year_text))
        week = int(float(week_text))
        week_end = _mmwr_week_end_date(year, week)
    except ValueError:
        return None

    return {
        "Date": datetime.combine(week_end, time.min).strftime("%Y-%m-%d"),
        "Diseases": label,
        "DiseasesCN": label,
        "Cases": _parse_numeric(_first_value(row, "Current week", "m1")),
        "Deaths": "",
        "Source": source_name,
        "CountryCode": "US",
        "ReportingArea": reporting_area,
        "MMWRYear": str(year),
        "MMWRWeek": str(week),
        "CurrentWeekFlag": _first_value(row, "Current week, flag", "m1_flag"),
        "Previous52WeekMax": _parse_numeric(_first_value(row, "Previous 52 week Max", "m2")),
        "Previous52WeekMaxFlag": _first_value(row, "Previous 52 weeks Max, flag", "m2_flag"),
        "CumulativeYTDCurrentYear": _parse_numeric(_first_value(row, "Cumulative YTD Current MMWR Year", "m3")),
        "CumulativeYTDCurrentYearFlag": _first_value(row, "Cumulative YTD Current MMWR Year, flag", "m3_flag"),
        "CumulativeYTDPreviousYear": _parse_numeric(_first_value(row, "Cumulative YTD Previous MMWR Year", "m4")),
        "CumulativeYTDPreviousYearFlag": _first_value(row, "Cumulative YTD Previous MMWR Year, flag", "m4_flag"),
        "Location1": _first_value(row, "LOCATION1", "location1"),
        "Location2": _first_value(row, "LOCATION2", "location2"),
        "SortOrder": _first_value(row, "sort_order"),
        "Geocode": _first_value(row, "geocode"),
        "RawDiseaseLabel": label,
        "IsProvisional": "true",
        "UpdateMode": update_mode,
        "__source_file": source_file,
    }


def _normalize_rows(
    rows: Iterable[Dict[str, object]],
    *,
    reporting_area: str,
    source_name: str,
    update_mode: str,
    source_file: str,
) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for row in rows:
        record = _build_normalized_record(
            row,
            reporting_area_filter=reporting_area,
            source_name=source_name,
            update_mode=update_mode,
            source_file=source_file,
        )
        if record is not None:
            normalized.append(record)

    normalized.sort(key=lambda item: (item["Date"], item["Diseases"], item["SortOrder"]))
    return normalized


def _latest_row_date(rows: List[Dict[str, str]]) -> Optional[date]:
    latest: Optional[date] = None
    for row in rows:
        date_text = row.get("Date", "")
        if not date_text:
            continue
        try:
            day = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            continue
        if latest is None or day > latest:
            latest = day
    return latest


class USWeeklyUpdater:
    """Fetch, normalize, gate, and import US NNDSS weekly data."""

    def __init__(
        self,
        *,
        country_code: str = "US",
        reporting_area: str = DEFAULT_REPORTING_AREA,
        source_name: str = DEFAULT_SOURCE_NAME,
    ) -> None:
        self.country_code = country_code.upper()
        self.reporting_area = reporting_area
        self.source_name = source_name

    def fetch_latest(self) -> USUpdateFetchResult:
        raw_rows, source_ref = USNNDSSCrawler().fetch_raw_pages()

        normalized_rows = _normalize_rows(
            raw_rows,
            reporting_area=self.reporting_area,
            source_name=self.source_name,
            update_mode="api_sync",
            source_file=source_ref,
        )

        latest_date = _latest_row_date(normalized_rows)
        return USUpdateFetchResult(
            rows=normalized_rows,
            source_ref=source_ref,
            update_mode="api_sync",
            latest_date=latest_date,
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
            key = _normalize_text(local_name).lower()
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
    ) -> USUpdateImportResult:
        if not rows:
            return USUpdateImportResult(
                inserted_or_updated=0,
                skipped_unmapped=0,
                db_latest_date=db_latest_date,
                source_latest_date=source_latest_date,
                imported_new_data=False,
            )

        if not force and db_latest_date is not None and source_latest_date is not None and source_latest_date <= db_latest_date:
            return USUpdateImportResult(
                inserted_or_updated=0,
                skipped_unmapped=0,
                db_latest_date=db_latest_date,
                source_latest_date=source_latest_date,
                imported_new_data=False,
            )

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)

        upsert_rows: List[Dict[str, object]] = []
        skipped_unmapped = 0
        seen_keys: set[Tuple[datetime, int, int]] = set()

        for row in rows:
            date_text = row.get("Date", "")
            if not date_text:
                continue

            try:
                day = datetime.combine(
                    datetime.strptime(date_text, "%Y-%m-%d").date(),
                    REPORT_TIME_UTC,
                    tzinfo=timezone.utc,
                )
            except ValueError:
                continue

            if not force and db_latest_date is not None and day.date() <= db_latest_date:
                continue

            label = _normalize_text(row.get("RawDiseaseLabel", "") or row.get("Diseases", ""))
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
                "source_file": row.get("__source_file", ""),
                "update_mode": row.get("UpdateMode", ""),
                "is_provisional": row.get("IsProvisional", ""),
                "death_reporting": "not_provided_by_source",
                "death_reporting_note": "NNDSS case notification feed used here does not provide death counts.",
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
                    "deaths": None,
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
            "US weekly import complete: upserted {} rows, skipped_unmapped {}",
            imported,
            skipped_unmapped,
        )

        return USUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )
