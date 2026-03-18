"""US NNDSS crawler.

Fetches US CDC NNDSS weekly disease data from the Socrata CSV API.
Paginated fetch is handled here; row normalisation and DB import live in processors/us.py.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Tuple

from src.core import get_logger
from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_CSV_API_URL = (
    "https://data.cdc.gov/resource/x9gk-5huc.csv"
    "?$select=states,year,week,label,m1,m1_flag,m2,m2_flag,m3,m3_flag,m4,m4_flag,"
    "location1,location2,sort_order,geocode"
    "&$where=upper(states)='TOTAL'"
    "&$order=sort_order"
)
SOCRATA_PAGE_SIZE = 50_000


class USNNDSSCrawler(BaseCrawler):
    """Fetch US CDC NNDSS weekly data from the Socrata Open Data API (CSV endpoint).

    Usage::

        crawler = USNNDSSCrawler()
        raw_rows, source_url = crawler.fetch_raw_pages()
        # raw_rows: List[Dict[str, str]] — one dict per CSV row, unfiltered
    """

    def fetch_raw_pages(self) -> Tuple[List[Dict[str, object]], str]:
        """Fetch all paginated rows from the CDC Socrata CSV endpoint.

        Returns:
            (rows, source_url) — rows is the raw DictReader output, source_url is
            the base API URL for provenance tracking.

        Raises:
            RuntimeError: if the API returns no rows at all.
        """
        all_rows: List[Dict[str, object]] = []
        offset = 0

        while True:
            page_url = f"{DEFAULT_CSV_API_URL}&$limit={SOCRATA_PAGE_SIZE}&$offset={offset}"
            response = self.get(page_url)
            page_rows = list(csv.DictReader(io.StringIO(response.text)))
            if not page_rows:
                break
            all_rows.extend(page_rows)
            if len(page_rows) < SOCRATA_PAGE_SIZE:
                break
            offset += SOCRATA_PAGE_SIZE

        if not all_rows:
            raise RuntimeError("[US-NNDSS] Socrata API returned no rows")

        logger.info(f"[US-NNDSS] Fetch complete | rows={len(all_rows)} source={DEFAULT_CSV_API_URL}")
        return all_rows, DEFAULT_CSV_API_URL

    # ── BaseCrawler abstract method stubs ─────────────────────────────────────
    # US data flows through fetch_raw_pages() → processors/us.py, not the
    # generic crawl() / parse() interface used by CN.

    def crawl(self) -> List[CrawlerResult]:
        """Not used for US; call fetch_raw_pages() directly."""
        return []

    def parse(self, response) -> List[CrawlerResult]:
        """Not used for US."""
        return []
