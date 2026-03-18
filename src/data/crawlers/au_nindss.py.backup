"""Australia NINDSS crawler.

Fetches AU location-level rows and aggregates monthly national totals into
globalID2-managed CSV format used by updater.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib import request

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config
from .base import BaseCrawler

logger = get_logger(__name__)


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


def _parse_month(month_val: object) -> Optional[int]:
    txt = _norm_text(month_val)
    if not txt:
        return None
    num = _parse_int(txt)
    if num is not None and 1 <= num <= 12:
        return num
    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(txt, fmt).month
        except ValueError:
            continue
    return None


@dataclass
class AUFetchSummary:
    row_count: int
    latest_date: Optional[date]
    csv_url: str


class AustraliaNINDSSCrawler(BaseCrawler):
    """Crawler for AU national monthly rows aggregated from location data."""

    SKIP_GROUPS = {"AUS", "UNKNOWN", "TOTAL", "ALL"}

    def __init__(self) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0)",
            timeout=60,
            max_retries=3,
            delay=0.5,
        )
        cfg = get_country_bootstrap_config("AU")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg.get("crawler_config"), dict) else {}
        self.dashboard_url = _norm_text(crawler_cfg.get("dashboard_url")) or "https://nindss.health.gov.au/pbi-dashboard/"
        self.query_url = _norm_text(crawler_cfg.get("query_url"))
        self.query_payload = crawler_cfg.get("query_payload") if isinstance(crawler_cfg.get("query_payload"), dict) else {}
        self.auth_token = _norm_text(crawler_cfg.get("auth_token"))
        self.extra_headers = crawler_cfg.get("headers") if isinstance(crawler_cfg.get("headers"), dict) else {}

    async def crawl(self, **kwargs):  # pragma: no cover - not used via base crawl path
        raise NotImplementedError("Use crawl_monthly_national_csv()")

    def parse(self, response):  # pragma: no cover - not used via base parse path
        return []

    def crawl_monthly_national_csv(self, output_csv: Path) -> AUFetchSummary:
        if not self.query_url:
            self.query_url = self._discover_query_url(self.dashboard_url)

        if not self.query_url:
            raise RuntimeError(
                "AU crawler_config.query_url is required for Microsoft BI fetch. "
                "Configure query_url directly or provide dashboard_url with discoverable endpoint."
            )

        if not self.query_payload:
            raise RuntimeError(
                "AU crawler_config.query_payload is required for Microsoft BI fetch."
            )

        rows = self._fetch_bi_rows(self.query_url, self.query_payload)
        aggregated = self._aggregate_location_rows(rows)

        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["", "Disease", "Group", "Year", "Month", "Date", "Cases", "DiseaseFull", "Population", "Incidence"],
            )
            writer.writeheader()
            for idx, row in enumerate(aggregated, start=1):
                out_row = dict(row)
                out_row[""] = str(idx)
                writer.writerow(out_row)

        latest = None
        for row in aggregated:
            try:
                day = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            except Exception:
                continue
            if latest is None or day > latest:
                latest = day

        return AUFetchSummary(row_count=len(aggregated), latest_date=latest, csv_url=self.query_url)

    def _discover_query_url(self, dashboard_url: str) -> str:
        response = self.get(dashboard_url)
        text = response.text

        direct = re.search(r"https://[^\"'\s]+/QueryExecutionService", text)
        if direct:
            return direct.group(0)

        return ""

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; GlobalID/2.0)",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        for key, value in self.extra_headers.items():
            k = _norm_text(key)
            v = _norm_text(value)
            if k and v:
                headers[k] = v

        if self.auth_token:
            headers.setdefault("Authorization", f"Bearer {self.auth_token}")

        return headers

    def _fetch_bi_rows(self, query_url: str, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        headers = self._build_headers()
        req = request.Request(
            query_url,
            method="POST",
            headers=headers,
            data=json.dumps(payload).encode("utf-8"),
        )
        with request.urlopen(req, timeout=90) as response:
            body = response.read().decode("utf-8", errors="ignore")

        data = json.loads(body)
        rows = self._extract_candidate_rows(data)
        if not rows:
            raise RuntimeError("AU Microsoft BI response did not contain parseable rows")
        return rows

    def _extract_candidate_rows(self, node: Any) -> List[Dict[str, str]]:
        candidates: List[Dict[str, str]] = []

        def walk(value: Any) -> None:
            if isinstance(value, list):
                if value and all(isinstance(item, dict) for item in value):
                    for item in value:
                        normalized = {str(k): _norm_text(v) for k, v in item.items()}
                        if self._looks_like_data_row(normalized):
                            candidates.append(normalized)
                for item in value:
                    walk(item)
                return

            if isinstance(value, dict):
                for child in value.values():
                    walk(child)

        walk(node)
        return candidates

    @staticmethod
    def _looks_like_data_row(row: Dict[str, str]) -> bool:
        keys = {k.lower() for k in row.keys()}
        has_disease = any(k in keys for k in ("disease", "diseasefull", "label"))
        has_time = any(k in keys for k in ("year", "month", "date"))
        has_cases = any(k in keys for k in ("cases", "value", "count"))
        return has_disease and has_time and has_cases

    def _aggregate_location_rows(self, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        totals: Dict[Tuple[str, int, int], int] = defaultdict(int)

        for src in rows:
            disease = _norm_text(
                src.get("Disease") or src.get("disease") or src.get("label") or src.get("DiseaseFull")
            )
            year = _parse_int(src.get("Year") or src.get("year"))
            month = _parse_month(src.get("Month") or src.get("month"))
            cases = _parse_int(src.get("Cases") or src.get("cases") or src.get("value") or src.get("count"))
            group = _norm_text(src.get("Group") or src.get("group") or src.get("location") or src.get("state")).upper()

            if (year is None or month is None) and _norm_text(src.get("Date") or src.get("date")):
                parsed = _norm_text(src.get("Date") or src.get("date"))
                try:
                    dt = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
                    year = dt.year
                    month = dt.month
                except ValueError:
                    pass

            if not disease or year is None or month is None or cases is None:
                continue
            if group in self.SKIP_GROUPS:
                continue

            totals[(disease, year, month)] += max(0, cases)

        output: List[Dict[str, str]] = []
        for (disease, year, month), cases in sorted(totals.items(), key=lambda x: (x[0][1], x[0][2], x[0][0])):
            output.append(
                {
                    "Disease": disease,
                    "Group": "location_aggregated",
                    "Year": str(year),
                    "Month": str(month),
                    "Date": f"{year:04d}-{month:02d}-01",
                    "Cases": str(cases),
                    "DiseaseFull": disease,
                    "Population": "",
                    "Incidence": "",
                }
            )
        return output
