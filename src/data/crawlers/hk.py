"""Hong Kong, China CHP notifiable infectious diseases crawler.

The Centre for Health Protection publishes one annual CSV per year under
``https://www.chp.gov.hk/files/misc/nidYYYYen.csv``.  Each CSV is already at
the Hong Kong, China monthly total grain used by GlobalID, so this crawler downloads
the annual CSV files and normalizes them to one row per disease/month.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Hong Kong, China CHP Notifiable Infectious Diseases"
DEFAULT_INDEX_URL = "https://www.chp.gov.hk/en/static/24012.html"
DEFAULT_ANNUAL_CSV_TEMPLATE = "https://www.chp.gov.hk/files/misc/nid{year}en.csv"
DEFAULT_HISTORY_START_YEAR = 1997
DEFAULT_REFRESH_RECENT_MONTHS = 3

MONTH_COLUMNS: Tuple[Tuple[str, int], ...] = (
    ("Jan", 1),
    ("Feb", 2),
    ("Mar", 3),
    ("Apr", 4),
    ("May", 5),
    ("Jun", 6),
    ("Jul", 7),
    ("Aug", 8),
    ("Sep", 9),
    ("Oct", 10),
    ("Nov", 11),
    ("Dec", 12),
)

SKIPPED_LABELS = {
    "food poisoning - persons affected",
    "food poisoning- persons affected",
}


@dataclass
class HKFetchSummary:
    row_count: int
    latest_date: Optional[date]
    years_fetched: int
    source_url: str


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


def _decode_csv_response(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _month_set(months: Optional[Iterable[Tuple[int, int]]]) -> Optional[Set[Tuple[int, int]]]:
    if months is None:
        return None
    return {(int(year), int(month)) for year, month in months if 1 <= int(month) <= 12}


def _last_n_available_months(
    rows: Sequence[Dict[str, str]],
    count: int = DEFAULT_REFRESH_RECENT_MONTHS,
) -> Set[Tuple[int, int]]:
    dates: Set[Tuple[int, int]] = set()
    for row in rows:
        parsed = _parse_row_date(row)
        if parsed is not None:
            dates.add((parsed.year, parsed.month))
    return set(sorted(dates)[-max(1, count):])


def _parse_row_date(row: Dict[str, str]) -> Optional[date]:
    text = _norm_text(row.get("Date"))
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_total_label(label: str) -> bool:
    return _norm_text(label).lower().startswith("total")


def _is_skipped_label(label: str) -> bool:
    normalized = _norm_text(label).lower()
    return normalized in SKIPPED_LABELS or _is_total_label(normalized)


def _record_type(label: str) -> str:
    normalized = _norm_text(label).lower()
    if "food poisoning" in normalized and "outbreak" in normalized:
        return "outbreaks"
    return "cases"


def aggregate_annual_csv_rows(
    year: int,
    rows: Iterable[Dict[str, str]],
    *,
    months: Optional[Set[Tuple[int, int]]] = None,
    source_url: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Normalize one CHP annual CSV to national monthly rows."""
    output_rows: List[Dict[str, str]] = []
    for row in rows:
        disease = _norm_text(row.get("Disease"))
        if not disease or _is_skipped_label(disease):
            continue

        total = _parse_int(row.get("Total"))
        for month_name, month_num in MONTH_COLUMNS:
            if months is not None and (year, month_num) not in months:
                continue
            cases = _parse_int(row.get(month_name))
            if cases is None:
                continue
            output_rows.append(
                {
                    "Date": date(year, month_num, 1).isoformat(),
                    "RawDiseaseLabel": disease,
                    "DiseaseCode": "",
                    "Year": str(year),
                    "Month": str(month_num),
                    "Cases": str(max(0, cases)),
                    "AnnualTotal": "" if total is None else str(max(0, total)),
                    "RecordType": _record_type(disease),
                    "Source": DEFAULT_SOURCE_NAME,
                    "SourceURL": source_url or DEFAULT_ANNUAL_CSV_TEMPLATE.format(year=year),
                }
            )
    return output_rows


class HongKongCHPCrawler(BaseCrawler):
    """Crawler for Hong Kong, China CHP annual notifiable infectious disease CSVs."""

    SOURCE_URL = DEFAULT_INDEX_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; HK-CHP)",
            timeout=60,
            max_retries=3,
            delay=0.2,
        )
        cfg = get_country_bootstrap_config("HK")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        self.index_url = str(crawler_cfg.get("index_url") or DEFAULT_INDEX_URL)
        self.annual_csv_template = str(
            crawler_cfg.get("annual_csv_url_template") or DEFAULT_ANNUAL_CSV_TEMPLATE
        )
        self.full_history_start_year = int(
            crawler_cfg.get("full_history_start_year") or DEFAULT_HISTORY_START_YEAR
        )
        self.refresh_recent_months = int(
            crawler_cfg.get("refresh_recent_months") or DEFAULT_REFRESH_RECENT_MONTHS
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/hk")

    def _csv_url(self, year: int) -> str:
        return self.annual_csv_template.format(year=year)

    def _download_year_csv(self, year: int) -> str:
        url = self._csv_url(year)
        response = self.get(url)
        text = _decode_csv_response(response.content)
        if self.save_raw:
            path = self.raw_dir / str(year) / f"nid{year}en.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            logger.debug(f"[HK-CHP] Saved raw annual CSV | path={path}")
        return text

    @staticmethod
    def _parse_csv_rows(csv_text: str) -> List[Dict[str, str]]:
        return [dict(row) for row in csv.DictReader(io.StringIO(csv_text))]

    def _default_years(self) -> List[int]:
        current_year = datetime.now().year
        start_year = max(self.full_history_start_year, current_year - 1)
        return list(range(start_year, current_year + 1))

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[List[Tuple[int, int]]] = None,
        years: Optional[List[int]] = None,
    ) -> HKFetchSummary:
        """Fetch annual CHP CSVs and write normalized monthly national rows."""
        requested_months = _month_set(months)
        if requested_months is not None:
            target_years = sorted({year for year, _ in requested_months})
        elif years is not None:
            target_years = sorted(set(int(year) for year in years))
        else:
            target_years = self._default_years()

        all_rows: List[Dict[str, str]] = []
        fetched_years = 0
        for year in target_years:
            csv_text = self._download_year_csv(year)
            annual_rows = aggregate_annual_csv_rows(
                year,
                self._parse_csv_rows(csv_text),
                months=requested_months,
                source_url=self._csv_url(year),
            )
            all_rows.extend(annual_rows)
            fetched_years += 1

        if requested_months is None and years is None:
            recent_months = _last_n_available_months(
                all_rows,
                count=self.refresh_recent_months,
            )
            all_rows = [
                row
                for row in all_rows
                if (parsed := _parse_row_date(row)) is not None
                and (parsed.year, parsed.month) in recent_months
            ]

        if not all_rows:
            raise RuntimeError("[HK-CHP] No national monthly rows parsed from CHP CSV source")

        all_rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "",
            "Disease",
            "DiseaseCode",
            "Year",
            "Month",
            "Date",
            "Cases",
            "AnnualTotal",
            "RecordType",
            "Source",
            "SourceURL",
        ]
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for idx, row in enumerate(all_rows, start=1):
                writer.writerow(
                    {
                        "": str(idx),
                        "Disease": row["RawDiseaseLabel"],
                        "DiseaseCode": row["DiseaseCode"],
                        "Year": row["Year"],
                        "Month": row["Month"],
                        "Date": row["Date"],
                        "Cases": row["Cases"],
                        "AnnualTotal": row["AnnualTotal"],
                        "RecordType": row["RecordType"],
                        "Source": row["Source"],
                        "SourceURL": row["SourceURL"],
                    }
                )

        latest_date = max(
            (_parse_row_date(row) for row in all_rows if _parse_row_date(row) is not None),
            default=None,
        )
        logger.info(
            f"[HK-CHP] CSV written | path={output_csv} rows={len(all_rows)} "
            f"years={fetched_years} latest={latest_date}"
        )
        return HKFetchSummary(
            row_count=len(all_rows),
            latest_date=latest_date,
            years_fetched=fetched_years,
            source_url=self.index_url,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/hk/hong_kong_national_monthly.csv"
        )
        months = kwargs.get("months")
        years = kwargs.get("years")
        summary = self.crawl_monthly_national(output_csv, months=months, years=years)
        return [
            CrawlerResult(
                title="Hong Kong, China CHP notifiable infectious diseases by month",
                url=self.index_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": "chp_notifiable",
                    "country_code": "HK",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "years_fetched": summary.years_fetched,
                },
            )
        ]

    def parse(self, response: Any) -> List[CrawlerResult]:
        """BaseCrawler contract; annual CSV parsing is handled internally."""
        return []
