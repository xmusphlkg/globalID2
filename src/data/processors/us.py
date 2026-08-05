"""US processor — normalize and import NNDSS and NHSS surveillance data.

HTTP fetching is delegated to crawlers/us.py. This module keeps the weekly
NNDSS and annual HIV NHSS series distinct while sharing mapping and upsert code.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)
from src.data.crawlers.us import (
    NHSS_SOURCE_NAME,
    USNHSSHIVCrawler,
    USNNDSSCrawler,
)
logger = get_logger(__name__)

NNDSS_TOTAL_REPORTING_AREA = "TOTAL"
NNDSS_US_RESIDENTS_REPORTING_AREA = "US RESIDENTS"
DEFAULT_REPORTING_AREA = NNDSS_US_RESIDENTS_REPORTING_AREA
DEFAULT_SOURCE_NAME = "US CDC NNDSS"
REPORT_TIME_UTC = time(hour=12)
NNDSS_REVISION_LOOKBACK_WEEKS = 8
NNDSS_MAPPING_SOURCE_ID = "SRC_US_NNDSS"
NHSS_MAPPING_SOURCE_ID = "SRC_US_NHSS"



@dataclass
class USUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_ref: str
    update_mode: str
    latest_date: Optional[date]
    latest_by_source: Dict[str, Optional[date]] = field(default_factory=dict)
    series_rows: List[Dict[str, str]] = field(default_factory=list)


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


def _canonical_nndss_reporting_area(value: object) -> Optional[str]:
    normalized = _normalize_text(value).casefold()
    if normalized == NNDSS_TOTAL_REPORTING_AREA.casefold():
        return NNDSS_TOTAL_REPORTING_AREA
    if normalized in {
        "us residents",
        "u.s. residents",
        "united states residents",
    }:
        return NNDSS_US_RESIDENTS_REPORTING_AREA
    return None


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
    if not math.isfinite(numeric):
        return None
    return int(numeric)


def _parse_float(value: str) -> Optional[float]:
    text = _normalize_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


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
    canonical_filter = _canonical_nndss_reporting_area(reporting_area_filter)
    if canonical_filter is None:
        raise ValueError(
            f"Unsupported NNDSS reporting-area filter: {reporting_area_filter!r}"
        )
    canonical_reporting_area = _canonical_nndss_reporting_area(reporting_area)
    if canonical_reporting_area is None:
        raise ValueError(
            "Unsupported NNDSS ReportingArea; expected TOTAL or a known "
            f"US-resident alias, received {reporting_area!r}"
        )
    if canonical_reporting_area != canonical_filter:
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

    population_scope = (
        "us_residents_excluding_territories"
        if canonical_reporting_area == NNDSS_US_RESIDENTS_REPORTING_AREA
        else (
            "nndss_total_including_us_residents_territories_"
            "and_non_us_residents"
        )
    )
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
        "Frequency": "weekly",
        "Measure": "case_notifications",
        "PopulationScope": population_scope,
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
    """Fetch, normalize, gate, and import US NNDSS plus NHSS data."""

    # NNDSS publishes both an all-reporting-area TOTAL and the narrower
    # US RESIDENTS population.  The series store must derive geography from
    # each row so these two statistical scopes never share a natural key.
    series_geography_from_rows = True
    # The upstream NNDSS table contains many conditions whose series semantics
    # have not yet been reviewed into the Registry. Keep the lossless dual
    # write explicitly limited to declared series while legacy mappings remain
    # available for the rest of the feed.
    series_registered_rows_only = True

    def __init__(
        self,
        *,
        country_code: str = "US",
        reporting_area: str = DEFAULT_REPORTING_AREA,
        source_name: str = DEFAULT_SOURCE_NAME,
    ) -> None:
        self.country_code = country_code.upper()
        if (
            _canonical_nndss_reporting_area(reporting_area)
            != NNDSS_US_RESIDENTS_REPORTING_AREA
        ):
            raise ValueError(
                "US legacy disease_records projection must use US RESIDENTS; "
                "NNDSS TOTAL is a broader source aggregate"
            )
        self.reporting_area = NNDSS_US_RESIDENTS_REPORTING_AREA
        self.source_name = source_name

    def fetch_latest(self, source: str = "all") -> USUpdateFetchResult:
        normalized_source = _normalize_text(source or "all").lower()
        source_aliases = {
            "nndss": "nndss_api",
            "nhss": "nhss_hiv",
            "hiv": "nhss_hiv",
            "hiv_nhss": "nhss_hiv",
        }
        normalized_source = source_aliases.get(normalized_source, normalized_source)
        if normalized_source not in {"all", "nndss_api", "nhss_hiv"}:
            raise ValueError(
                f"Unsupported US source: {source}. Available: all, nndss_api, nhss_hiv"
            )

        normalized_rows: List[Dict[str, str]] = []
        series_rows: List[Dict[str, str]] = []
        source_refs: List[str] = []
        latest_by_source: Dict[str, Optional[date]] = {}

        if normalized_source in {"all", "nndss_api"}:
            raw_rows, nndss_ref = USNNDSSCrawler().fetch_raw_pages()
            # Both the legacy national projection and the national source
            # series use US RESIDENTS.  TOTAL remains available only as a
            # distinct source aggregate in the lossless series projection.
            resident_rows = _normalize_rows(
                raw_rows,
                reporting_area=self.reporting_area,
                source_name=self.source_name,
                update_mode="api_sync",
                source_file=nndss_ref,
            )
            total_rows = _normalize_rows(
                raw_rows,
                reporting_area=NNDSS_TOTAL_REPORTING_AREA,
                source_name=self.source_name,
                update_mode="api_sync",
                source_file=nndss_ref,
            )
            if not resident_rows:
                raise RuntimeError(
                    "[US-NNDSS] US RESIDENTS rows are missing; refusing to "
                    "substitute the broader TOTAL scope"
                )
            if not total_rows:
                raise RuntimeError(
                    "[US-NNDSS] TOTAL rows are missing from the requested "
                    "dual-scope series feed"
                )
            normalized_rows.extend(resident_rows)
            series_rows.extend(resident_rows)
            series_rows.extend(total_rows)
            source_refs.append(nndss_ref)
            latest_by_source[self.source_name] = _latest_row_date(resident_rows)

        if normalized_source in {"all", "nhss_hiv"}:
            nhss_rows, nhss_ref = USNHSSHIVCrawler().fetch_national_annual_rows()
            typed_nhss_rows = [dict(row) for row in nhss_rows]
            normalized_rows.extend(typed_nhss_rows)
            series_rows.extend(typed_nhss_rows)
            source_refs.append(nhss_ref)
            latest_by_source[NHSS_SOURCE_NAME] = _latest_row_date(typed_nhss_rows)

        def sort_key(item: Dict[str, str]) -> tuple[str, str, str, str]:
            return (
                item.get("Date", ""),
                item.get("Diseases", ""),
                item.get("ReportingArea", ""),
                item.get("SortOrder", ""),
            )

        normalized_rows.sort(key=sort_key)
        series_rows.sort(key=sort_key)
        latest_date = _latest_row_date(normalized_rows)
        return USUpdateFetchResult(
            rows=normalized_rows,
            source_ref=" | ".join(source_refs),
            update_mode="multi_source_sync" if normalized_source == "all" else "api_sync",
            latest_date=latest_date,
            latest_by_source=latest_by_source,
            series_rows=series_rows,
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

    async def _load_mapping_dict(
        self, db: AsyncSession, *, source_id: str = NNDSS_MAPPING_SOURCE_ID
    ) -> Dict[str, int]:
        return await load_country_mapping_dict(
            db, self.country_code, source_id=source_id
        )

    async def _get_source_latest_dates(
        self,
        db: AsyncSession,
        source_names: Iterable[str],
    ) -> Dict[str, date]:
        names = sorted({_normalize_text(name) for name in source_names if _normalize_text(name)})
        if not names:
            return {}
        result = await db.execute(
            text(
                """
                SELECT dr.data_source, MAX(dr.time)
                FROM disease_records dr
                JOIN countries c ON c.id = dr.country_id
                WHERE c.code = :code
                  AND dr.data_source = ANY(:source_names)
                GROUP BY dr.data_source
                """
            ),
            {"code": self.country_code, "source_names": names},
        )
        latest: Dict[str, date] = {}
        for source_name, max_time in result.fetchall():
            if max_time is not None:
                latest[_normalize_text(source_name)] = max_time.date()
        return latest

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

        country_id = await self._get_country_id(db)
        mapping_by_source = {
            NNDSS_MAPPING_SOURCE_ID: await self._load_mapping_dict(
                db, source_id=NNDSS_MAPPING_SOURCE_ID
            ),
            NHSS_MAPPING_SOURCE_ID: await self._load_mapping_dict(
                db, source_id=NHSS_MAPPING_SOURCE_ID
            ),
        }
        source_latest_dates = await self._get_source_latest_dates(
            db,
            (row.get("Source", self.source_name) for row in rows),
        )

        upsert_rows: List[Dict[str, object]] = []
        skipped_unmapped = 0
        skipped_missing_cases = 0
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

            row_source = _normalize_text(row.get("Source", self.source_name)) or self.source_name
            mapping_source_id = (
                NHSS_MAPPING_SOURCE_ID
                if row_source == NHSS_SOURCE_NAME
                else NNDSS_MAPPING_SOURCE_ID
            )
            mapping_dict = mapping_by_source[mapping_source_id]
            default_frequency = "annual" if row_source == NHSS_SOURCE_NAME else "weekly"
            frequency = (
                _normalize_text(row.get("Frequency", default_frequency)).lower()
                or default_frequency
            )
            db_source_latest = source_latest_dates.get(row_source)
            if not force and db_source_latest is not None and frequency == "weekly":
                revision_start = db_source_latest - timedelta(
                    weeks=NNDSS_REVISION_LOOKBACK_WEEKS
                )
                if day.date() < revision_start:
                    continue

            label = _normalize_text(row.get("RawDiseaseLabel", "") or row.get("Diseases", ""))
            disease_id = mapping_dict.get(normalize_mapping_key(label))
            if disease_id is None:
                skipped_unmapped += 1
                continue

            cases = _parse_int(row.get("Cases", ""))
            if cases is None:
                skipped_missing_cases += 1
                continue
            metadata_obj = {
                "raw_disease_label": label,
                "reporting_area": row.get("ReportingArea", ""),
                "mmwr_year": row.get("MMWRYear", ""),
                "mmwr_week": row.get("MMWRWeek", ""),
                "source_file": row.get("__source_file", ""),
                "update_mode": row.get("UpdateMode", ""),
                "is_provisional": row.get("IsProvisional", ""),
                "frequency": frequency,
                "measure": row.get("Measure", "case_notifications"),
                "population_scope": row.get("PopulationScope", "all"),
                "surveillance_year": row.get("SurveillanceYear", ""),
                "release_year": row.get("ReleaseYear", ""),
                "death_reporting": "not_provided_by_source",
                "death_reporting_note": (
                    "The selected CDC surveillance series does not provide a death count "
                    "paired with this diagnosis/case observation."
                ),
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
                    "cases": cases,
                    "deaths": None,
                    "incidence_rate": _parse_float(row.get("Incidence", "")),
                    "data_source": row_source,
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
                        incidence_rate, data_source, metadata, raw_data,
                        new_cases, new_deaths, recoveries, active_cases, new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, :deaths,
                        :incidence_rate, :data_source, :metadata, :raw_data,
                        0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        incidence_rate = EXCLUDED.incidence_rate,
                        data_source = EXCLUDED.data_source,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    """
                ),
                upsert_rows,
            )

        imported = len(upsert_rows)
        logger.info(
            "US surveillance import complete: upserted {} rows, "
            "skipped_unmapped {}, skipped_missing_cases {}",
            imported,
            skipped_unmapped,
            skipped_missing_cases,
        )

        return USUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )
