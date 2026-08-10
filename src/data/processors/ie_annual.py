"""Ireland HPSC reviewed annual-history updater (2004–2020)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.ie import stable_disease_code
from src.data.crawlers.ie_annual import (
    DEFAULT_ANNUAL_END_YEAR,
    DEFAULT_ANNUAL_SOURCE_NAME,
    DEFAULT_ANNUAL_SOURCE_SCOPE,
    DEFAULT_ANNUAL_START_YEAR,
    IrelandHPSCAnnualCrawler,
    validate_annual_rows,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ANNUAL_OUTPUT_CSV = ROOT / "data/current/ie/ireland_hpsc_annual.csv"
ANNUAL_ONTOLOGY_SOURCE_ID = "SRC_IE_HPSC_ANNUAL"
DEFAULT_REVISION_YEARS = frozenset({2018, 2019, 2020})


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").split()).strip()


@dataclass(frozen=True)
class IEAnnualUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]
    years_fetched: Tuple[int, ...]


@dataclass(frozen=True)
class IEAnnualUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


class IEAnnualUpdater:
    """Plan, fetch, and persist the non-overlapping modern annual history."""

    country_code = "IE"
    source_scope = DEFAULT_ANNUAL_SOURCE_SCOPE
    ontology_source_id = ANNUAL_ONTOLOGY_SOURCE_ID
    series_geography_key = "country:IE:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    license_review_status = "written_permission_required"
    full_history_start_year = DEFAULT_ANNUAL_START_YEAR
    full_history_end_year = DEFAULT_ANNUAL_END_YEAR

    def __init__(
        self,
        *,
        source_name: str = DEFAULT_ANNUAL_SOURCE_NAME,
        output_csv: Path = DEFAULT_ANNUAL_OUTPUT_CSV,
    ) -> None:
        self.source_name = source_name
        self.output_csv = Path(output_csv)

    def _load_rows(self) -> List[Dict[str, str]]:
        if not self.output_csv.exists():
            raise FileNotFoundError(
                f"IE annual crawler output not found: {self.output_csv}"
            )
        rows: List[Dict[str, str]] = []
        with self.output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                label = _text(raw.get("RawDiseaseLabel"))
                code = _text(raw.get("DiseaseCode"))
                year_text = _text(raw.get("Year"))
                if not label or code != stable_disease_code(label) or not year_text.isdigit():
                    continue
                normalized = {key: _text(value) for key, value in raw.items()}
                normalized.update(
                    {
                        "RawDiseaseLabel": label,
                        "DiseaseCode": code,
                        "Source": _text(raw.get("Source")) or self.source_name,
                        "SourceScope": DEFAULT_ANNUAL_SOURCE_SCOPE,
                        "GeographyKey": "country:IE:national",
                        "PublicReleaseEnabled": "false",
                        "LicenseReviewStatus": self.license_review_status,
                    }
                )
                rows.append(normalized)
        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        validate_annual_rows(rows)
        return rows

    def refresh_source(
        self,
        *,
        source: str = DEFAULT_ANNUAL_SOURCE_SCOPE,
        run_external: bool = False,
        force: bool = False,
        fill_missing: bool = False,
        existing_years: Optional[Set[int]] = None,
        start_year: Optional[int] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> IEAnnualUpdateFetchResult:
        del run_external
        if _text(source).casefold() not in {
            DEFAULT_ANNUAL_SOURCE_SCOPE,
            "annual",
            "hpsc_annual_history",
        }:
            raise ValueError(f"Unsupported Ireland annual source: {source}")

        selected_start = max(
            DEFAULT_ANNUAL_START_YEAR,
            min(int(start_year or DEFAULT_ANNUAL_START_YEAR), DEFAULT_ANNUAL_END_YEAR),
        )
        catalogue = set(range(selected_start, DEFAULT_ANNUAL_END_YEAR + 1))
        if force:
            selected = catalogue
            mode = f"full reviewed annual history {selected_start}–2020"
        elif fill_missing:
            selected = catalogue - set(existing_years or set())
            selected.update(DEFAULT_REVISION_YEARS.intersection(catalogue))
            mode = "missing annual years plus reviewed overlap revisions"
        else:
            selected = DEFAULT_REVISION_YEARS.intersection(catalogue)
            mode = "latest reviewed annual overlap revisions"
        if not selected:
            selected = DEFAULT_REVISION_YEARS.intersection(catalogue)

        actual_raw_dir = Path(raw_dir) if raw_dir else ROOT / "data/raw/ie/annual"
        crawler = IrelandHPSCAnnualCrawler(
            save_raw=save_raw,
            raw_dir=actual_raw_dir,
        )
        try:
            summary = crawler.crawl_annual_national(
                self.output_csv,
                years=sorted(selected),
            )
        finally:
            crawler.session.close()

        rows = [
            row for row in self._load_rows() if int(row["Year"]) in selected
        ]
        validate_annual_rows(rows, requested_years=selected)
        logs = [
            "[gate] public release disabled; HPSC written permission required",
            f"[planner] {mode}",
            (
                f"[crawler] prepared {summary.row_count} annual rows across "
                f"{len(summary.years_fetched)} years; "
                f"diseases={summary.diseases_catalogued}"
            ),
        ]
        if save_raw:
            logs.append(f"[crawler] raw HPSC PDFs archived under {actual_raw_dir}")
        return IEAnnualUpdateFetchResult(
            rows=rows,
            source_latest_date=summary.latest_date,
            source_csv=self.output_csv,
            script_logs=logs,
            years_fetched=summary.years_fetched,
        )

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        result = await db.execute(
            text(
                """
                SELECT MAX(o.time)
                FROM disease_series_observations o
                JOIN disease_surveillance_series s ON s.series_code = o.series_code
                WHERE s.country_code = :country_code
                  AND s.source_system = :source_system
                """
            ),
            {
                "country_code": self.country_code,
                "source_system": self.ontology_source_id,
            },
        )
        value = result.scalar()
        if value is None:
            return None
        return value.date() if isinstance(value, datetime) else value

    async def get_db_years(self, db: AsyncSession) -> Set[int]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT EXTRACT(YEAR FROM o.time)::int
                FROM disease_series_observations o
                JOIN disease_surveillance_series s ON s.series_code = o.series_code
                WHERE s.country_code = :country_code
                  AND s.source_system = :source_system
                """
            ),
            {
                "country_code": self.country_code,
                "source_system": self.ontology_source_id,
            },
        )
        return {int(row[0]) for row in result.fetchall()}

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> IEAnnualUpdateImportResult:
        """Leave this distinct grain out of the legacy weekly fact table."""

        del db, force
        if not rows:
            return IEAnnualUpdateImportResult(
                0, 0, db_latest_date, source_latest_date, False
            )
        validate_annual_rows(rows)
        non_missing = sum(bool(_text(row.get("Cases"))) for row in rows)
        return IEAnnualUpdateImportResult(
            inserted_or_updated=non_missing,
            skipped_unmapped=0,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=non_missing > 0,
        )


__all__ = [
    "ANNUAL_ONTOLOGY_SOURCE_ID",
    "DEFAULT_ANNUAL_OUTPUT_CSV",
    "IEAnnualUpdateFetchResult",
    "IEAnnualUpdateImportResult",
    "IEAnnualUpdater",
]
