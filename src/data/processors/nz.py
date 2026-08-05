"""NZ monthly updater.

New Zealand monthly notifiable disease data from PHF Science (formerly ESR).
Data is published monthly as ZIP files containing Excel workbooks.

The Rolling workbook provides 12 months of historical data in each release,
so we can back-fill missing months from recent reports. Like AU, NZ data is
provisional and may be revised, so we upsert unconditionally.
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
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)
from src.data.crawlers.nz import NewZealandPHFCrawler

logger = get_logger(__name__)

MAPPING_SOURCE_ID = "SRC_NZ_PHS"

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/nz/new_zealand_national_data.csv"
DEFAULT_SOURCE_NAME = "NZ PHF Science Monthly Notifiable Disease Surveillance"


@dataclass
class NZUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass
class NZUpdateImportResult:
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


class NZMonthlyUpdater:
    """Read NZ national monthly rows from PHF Science crawler output and import."""

    def __init__(
        self,
        *,
        country_code: str = "NZ",
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
        self, months: Optional[List[Tuple[int, int]]]
    ) -> List[Tuple[int, int]]:
        return sorted(set(months)) if months is not None else self._default_recent_months()

    def refresh_source(
        self,
        *,
        source: str = "nz",
        run_external: bool = False,
        force: bool = False,
        months: Optional[List[Tuple[int, int]]] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> NZUpdateFetchResult:
        """Fetch NZ data from PHF Science Digital Library.

        Args:
            source:       Ignored (kept for interface parity).
            run_external: Ignored.
            force:        Re-fetch even if data appears up-to-date.
            months:       Explicit (year, month) pairs to request.
                          When None, defaults to the most recent 3 months.
            save_raw:     Whether to archive raw ZIP files.
            raw_dir:      Directory for raw archives.
        """
        logs: List[str] = []
        requested_months = self._resolve_requested_months(months)

        actual_raw_dir = Path(raw_dir) if raw_dir is not None else ROOT / "data/raw" / self.country_code.lower()

        # Try to load existing CSV data first
        prior_rows: List[Dict[str, str]] = []
        if self.output_csv.exists():
            try:
                prior_rows = self._load_rows(self.output_csv)
                logs.append(f"[cache] loaded {len(prior_rows)} rows from existing CSV")
            except Exception as exc:
                logs.append(f"[cache] unable to read existing CSV: {type(exc).__name__}: {exc}")

        # Attempt live fetch
        crawler = NewZealandPHFCrawler(save_raw=save_raw, raw_dir=actual_raw_dir)
        live_rows: List[Dict[str, str]] = []
        live_error: Optional[Exception] = None

        try:
            fetch_summary = crawler.crawl_monthly_national(
                self.output_csv,
                months=requested_months,
                max_pages=10,
            )
            logs.append(
                f"[crawler] fetched {fetch_summary.row_count} rows; "
                f"months={fetch_summary.months_fetched}; "
                f"latest={fetch_summary.latest_date}"
            )
            live_rows = self._load_rows(self.output_csv)
        except Exception as exc:
            live_error = exc
            logs.append(f"[crawler] live fetch failed: {type(exc).__name__}: {exc}")

        # Select best available data
        candidates: List[Tuple[str, List[Dict[str, str]], int]] = []
        if live_rows:
            candidates.append(("live fetch", live_rows, 2))
        if prior_rows:
            candidates.append(("previous CSV snapshot", prior_rows, 0))

        if not candidates:
            if live_error is not None:
                raise live_error
            raise RuntimeError("NZ crawler produced no usable rows")

        selected_label, rows, _ = max(candidates, key=lambda item: (len(item[1]), item[2]))

        if selected_label != "live fetch":
            logs.append(
                f"[recovery] using {selected_label} with {len(rows)} rows"
            )

        latest = self._latest_row_date(rows)

        return NZUpdateFetchResult(
            rows=rows,
            source_latest_date=latest,
            source_csv=self.output_csv,
            script_logs=logs,
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(
                f"NZ crawler output not found: {csv_path}. "
                "Please run the NZ crawler first."
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

                rows.append({
                    "Date": report_date.isoformat(),
                    "RawDiseaseLabel": disease,
                    "Cases": str(max(0, cases)),
                    "CumulativeTotal": _norm_text(row.get("CumulativeTotal")),
                    "Rate": _norm_text(row.get("Rate")),
                    "Year": str(report_date.year),
                    "Month": str(report_date.month),
                    "Source": _norm_text(row.get("Source")) or self.source_name,
                })

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
        """Return the set of (year, month) pairs already in disease_records for NZ."""
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
    ) -> NZUpdateImportResult:
        """
        Upsert NZ disease rows into the database.

        NZ data is provisional and may be revised, so ALL rows are always
        upserted (ON CONFLICT DO UPDATE) regardless of date.
        """
        if not rows:
            return NZUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)

        upsert_rows: List[Dict[str, object]] = []
        skipped_unmapped = 0
        seen_keys: set = set()

        for row in rows:
            try:
                day = datetime.strptime(row.get("Date", ""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            label = _norm_text(row.get("RawDiseaseLabel", ""))
            disease_id = mapping_dict.get(normalize_mapping_key(label))
            if disease_id is None:
                skipped_unmapped += 1
                continue

            cases = _parse_int(row.get("Cases", ""))
            incidence = None
            try:
                rate_str = row.get("Rate", "").strip()
                incidence = float(rate_str) if rate_str else None
            except ValueError:
                incidence = None

            metadata_obj = {
                "raw_disease_label": label,
                "cumulative_total": row.get("CumulativeTotal", ""),
                "source": row.get("Source", ""),
                "death_reporting": "not_provided_by_source",
                "death_reporting_note": "New Zealand notifiable disease feed used here reports cases, not death counts.",
            }

            key = (day, disease_id, country_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            upsert_rows.append({
                "time": day,
                "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": cases if cases is not None else 0,
                    "deaths": None,
                "data_source": row.get("Source", self.source_name),
                "incidence_rate": incidence,
                "metadata": json.dumps(metadata_obj),
                "raw_data": json.dumps(row),
            })

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
            "NZ monthly import complete: upserted {} rows, skipped_unmapped {}",
            imported,
            skipped_unmapped,
        )

        return NZUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )
