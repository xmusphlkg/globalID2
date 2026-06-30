"""Taiwan, China CDC NIDSS open-data crawler.

Taiwan, China CDC's NIDSS dashboard exposes per-disease open CSV files under
``https://od.cdc.gov.tw/eic/Age_County_Gender_<code>.csv``.  Each CSV is a
monthly age/county/gender detail table.  This crawler discovers the disease
codes from the public dashboard and aggregates rows to national monthly totals,
which match the existing ``(time, disease_id, country_id)`` storage grain.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Taiwan, China CDC NIDSS Open Data"
DEFAULT_INDEX_URL = "https://nidss.cdc.gov.tw/Home/Index"
DEFAULT_MONTHLY_CSV_TEMPLATE = (
    "https://od.cdc.gov.tw/eic/Age_County_Gender_{disease_code}.csv"
)
DEFAULT_WEEKLY_CSV_TEMPLATE = (
    "https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_{disease_code}.csv"
)


@dataclass(frozen=True)
class TWDiseaseSource:
    code: str
    name: str
    monthly_csv_url: str
    weekly_csv_url: str


@dataclass
class TWFetchSummary:
    row_count: int
    latest_date: Optional[date]
    diseases_fetched: int
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


def _last_n_months(count: int = 3) -> Set[Tuple[int, int]]:
    now = datetime.now()
    months: Set[Tuple[int, int]] = set()
    for delta in range(max(1, count)):
        month = now.month - delta
        year = now.year
        if month <= 0:
            month += 12
            year -= 1
        months.add((year, month))
    return months


def _decode_csv_response(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def aggregate_monthly_csv_rows(
    disease: TWDiseaseSource,
    rows: Iterable[Dict[str, str]],
    *,
    months: Optional[Set[Tuple[int, int]]] = None,
) -> List[Dict[str, str]]:
    """Aggregate Taiwan, China CDC detail rows to national monthly rows."""
    aggregate: Dict[Tuple[int, int], Dict[str, int]] = {}

    for row in rows:
        year = _parse_int(row.get("發病年份"))
        month = _parse_int(row.get("發病月份"))
        cases = _parse_int(row.get("確定病例數"))
        if year is None or month is None or cases is None:
            continue
        if not (1 <= month <= 12):
            continue
        if months is not None and (year, month) not in months:
            continue

        bucket = aggregate.setdefault(
            (year, month),
            {"cases": 0, "local_cases": 0, "imported_cases": 0},
        )
        safe_cases = max(0, cases)
        bucket["cases"] += safe_cases

        imported_flag = _norm_text(row.get("是否為境外移入"))
        if imported_flag == "1":
            bucket["imported_cases"] += safe_cases
        else:
            bucket["local_cases"] += safe_cases

    output_rows: List[Dict[str, str]] = []
    for year, month in sorted(aggregate):
        totals = aggregate[(year, month)]
        output_rows.append(
            {
                "Date": date(year, month, 1).isoformat(),
                "RawDiseaseLabel": disease.name,
                "DiseaseCode": disease.code,
                "Year": str(year),
                "Month": str(month),
                "Cases": str(totals["cases"]),
                "LocalCases": str(totals["local_cases"]),
                "ImportedCases": str(totals["imported_cases"]),
                "Source": DEFAULT_SOURCE_NAME,
                "SourceURL": disease.monthly_csv_url,
            }
        )
    return output_rows


class TaiwanNIDSSCrawler(BaseCrawler):
    """Crawler for Taiwan, China CDC NIDSS monthly open data CSVs."""

    SOURCE_URL = DEFAULT_INDEX_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; TW-NIDSS)",
            timeout=120,
            max_retries=3,
            delay=0.3,
        )
        cfg = get_country_bootstrap_config("TW")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        self.index_url = str(crawler_cfg.get("index_url") or DEFAULT_INDEX_URL)
        self.monthly_csv_template = str(
            crawler_cfg.get("monthly_csv_url_template") or DEFAULT_MONTHLY_CSV_TEMPLATE
        )
        self.weekly_csv_template = str(
            crawler_cfg.get("weekly_csv_url_template") or DEFAULT_WEEKLY_CSV_TEMPLATE
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/tw")

    def fetch_disease_index(self) -> List[TWDiseaseSource]:
        """Discover NIDSS disease codes that are exposed in the dashboard selector."""
        from bs4 import BeautifulSoup

        response = self.get(self.index_url)
        soup = BeautifulSoup(response.text, "html.parser")

        discovered: Dict[str, str] = {}
        selector = soup.find("select", id="DiseaseList")
        if selector is not None:
            for option in selector.find_all("option"):
                value = _norm_text(option.get("value"))
                label = _norm_text(option.get_text(" ", strip=True))
                if not value.startswith("disease?id=") or not label:
                    continue
                code = value.split("=", 1)[1].strip()
                if code and code != "0":
                    discovered.setdefault(code, label)

        if not discovered:
            for link in soup.find_all("a", href=True):
                href = str(link.get("href") or "")
                if "/nndss/disease?id=" not in href.lower():
                    continue
                code = href.split("id=", 1)[1].split("&", 1)[0].strip()
                label = _norm_text(link.get_text(" ", strip=True))
                if code and label:
                    discovered.setdefault(code, label)

        diseases = [
            TWDiseaseSource(
                code=code,
                name=name,
                monthly_csv_url=self.monthly_csv_template.format(disease_code=code),
                weekly_csv_url=self.weekly_csv_template.format(disease_code=code),
            )
            for code, name in discovered.items()
        ]
        logger.info(f"[TW-NIDSS] Index complete | diseases={len(diseases)}")
        return diseases

    def _download_csv_text(self, disease: TWDiseaseSource) -> Optional[str]:
        """Download one disease CSV with a small retry loop for CDN hiccups."""
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                time.sleep(self.delay)
                response = self.session.get(disease.monthly_csv_url, timeout=self.timeout)
                if response.status_code == 404:
                    logger.warning(
                        f"[TW-NIDSS] CSV missing | code={disease.code} name={disease.name}"
                    )
                    return None
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "html" in content_type.lower():
                    logger.warning(
                        f"[TW-NIDSS] CSV returned HTML | code={disease.code} url={disease.monthly_csv_url}"
                    )
                    return None
                return _decode_csv_response(response.content)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    f"[TW-NIDSS] CSV download failed | code={disease.code} "
                    f"attempt={attempt}/3 error={exc}"
                )
                time.sleep(attempt)

        if last_error is not None:
            logger.warning(
                f"[TW-NIDSS] CSV skipped after retries | code={disease.code} "
                f"name={disease.name} error={last_error}"
            )
        return None

    def _save_raw_csv(self, disease: TWDiseaseSource, csv_text: str) -> None:
        if not self.save_raw:
            return
        path = self.raw_dir / "monthly" / f"{disease.code}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(csv_text, encoding="utf-8")
        logger.debug(f"[TW-NIDSS] Saved raw CSV | path={path}")

    @staticmethod
    def _parse_csv_rows(csv_text: str) -> List[Dict[str, str]]:
        reader = csv.DictReader(io.StringIO(csv_text))
        return [dict(row) for row in reader]

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[List[Tuple[int, int]]] = None,
        disease_codes: Optional[List[str]] = None,
    ) -> TWFetchSummary:
        """Fetch and aggregate Taiwan, China NIDSS monthly CSVs to a national CSV."""
        diseases = self.fetch_disease_index()
        if not diseases:
            raise RuntimeError("[TW-NIDSS] No diseases discovered from NIDSS dashboard")

        target_months = set(months) if months is not None else _last_n_months(3)
        requested_codes = {code.strip() for code in disease_codes or [] if code.strip()}

        all_rows: List[Dict[str, str]] = []
        fetched_count = 0
        failed_count = 0
        skipped_count = 0
        for disease in diseases:
            if requested_codes and disease.code not in requested_codes:
                continue

            try:
                csv_text = self._download_csv_text(disease)
            except Exception as exc:
                failed_count += 1
                logger.warning(
                    f"[TW-NIDSS] CSV skipped after unexpected error | "
                    f"code={disease.code} name={disease.name} error={exc}"
                )
                continue
            if not csv_text:
                skipped_count += 1
                continue

            self._save_raw_csv(disease, csv_text)
            detail_rows = self._parse_csv_rows(csv_text)
            national_rows = aggregate_monthly_csv_rows(
                disease,
                detail_rows,
                months=target_months,
            )
            if national_rows:
                all_rows.extend(national_rows)
            fetched_count += 1

        if not all_rows:
            raise RuntimeError(
                "[TW-NIDSS] No national monthly rows parsed from CSV source "
                f"(fetched=0, skipped={skipped_count}, failed={failed_count})"
            )

        if failed_count or skipped_count:
            logger.warning(
                f"[TW-NIDSS] Completed with skipped disease CSVs | "
                f"fetched={fetched_count} skipped={skipped_count} failed={failed_count}"
            )

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
            "LocalCases",
            "ImportedCases",
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
                        "LocalCases": row["LocalCases"],
                        "ImportedCases": row["ImportedCases"],
                        "Source": row["Source"],
                        "SourceURL": row["SourceURL"],
                    }
                )

        latest_date = max(
            (datetime.strptime(row["Date"], "%Y-%m-%d").date() for row in all_rows),
            default=None,
        )
        logger.info(
            f"[TW-NIDSS] CSV written | path={output_csv} "
            f"rows={len(all_rows)} diseases={fetched_count} latest={latest_date}"
        )
        return TWFetchSummary(
            row_count=len(all_rows),
            latest_date=latest_date,
            diseases_fetched=fetched_count,
            source_url=self.index_url,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/tw/taiwan_national_monthly.csv"
        )
        months = kwargs.get("months")
        summary = self.crawl_monthly_national(output_csv, months=months)
        return [
            CrawlerResult(
                title="Taiwan, China CDC NIDSS monthly open data",
                url=self.index_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": "nidss_open_data",
                    "country_code": "TW",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "diseases_fetched": summary.diseases_fetched,
                },
            )
        ]

    def parse(self, response: Any) -> List[CrawlerResult]:
        """BaseCrawler contract; parsing is integrated in ``crawl_monthly_national``."""
        return []
