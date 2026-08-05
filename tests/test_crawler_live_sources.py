"""Read-only smoke tests for the live surveillance sources.

These tests deliberately stop before database writes and raw-data publication.
Run them explicitly with ``pytest --run-network``.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

import pytest

from src.data.crawlers.au import AustraliaNINDSSCrawler
from src.data.crawlers.br import BrazilSINANCrawler
from src.data.crawlers.ch import SwitzerlandIDDCrawler
from src.data.crawlers.cn import ChinaCDCCrawler
from src.data.crawlers.hk import HongKongCHPCrawler
from src.data.crawlers.jp import JapanIDWRCrawler
from src.data.crawlers.kr import KoreaKDCAOpenAPICrawler
from src.data.crawlers.nz import NewZealandPHFCrawler
from src.data.crawlers.tw import TaiwanNIDSSCrawler
from src.data.crawlers.us import DEFAULT_CSV_API_URL, USNNDSSCrawler


pytestmark = pytest.mark.network


@pytest.mark.asyncio
async def test_cn_live_index_discovers_weekly_reports() -> None:
    reports = await ChinaCDCCrawler().fetch_list(source="cdc_weekly")
    assert reports
    assert any(report.url for report in reports)


def test_jp_live_index_discovers_week_pages() -> None:
    crawler = JapanIDWRCrawler()
    year_pages = crawler._discover_year_index_urls()
    assert year_pages
    week_pages = crawler._discover_week_index_urls(year_pages[0])
    assert week_pages


def test_au_live_dashboard_exposes_runtime_configuration(tmp_path) -> None:
    crawler = AustraliaNINDSSCrawler(save_raw=False, raw_dir=tmp_path)
    assert crawler._load_config()
    assert crawler._config
    assert crawler._config.get("apiUrl")


def test_us_live_api_returns_parseable_rows() -> None:
    crawler = USNNDSSCrawler()
    response = crawler.get(f"{DEFAULT_CSV_API_URL}&$limit=5")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows
    assert {"states", "year", "week", "label"}.issubset(rows[0])


def test_nz_live_sitemap_discovers_reports(tmp_path) -> None:
    crawler = NewZealandPHFCrawler(save_raw=False, raw_dir=tmp_path)
    reports = crawler.fetch_report_index(max_pages=1)
    assert reports
    assert any(report.get("url") for report in reports)


def test_tw_live_index_and_one_csv_are_parseable(tmp_path) -> None:
    crawler = TaiwanNIDSSCrawler(save_raw=False, raw_dir=tmp_path)
    crawler.timeout = 10
    diseases = crawler.fetch_disease_index()
    assert diseases
    candidates = sorted(diseases, key=lambda disease: disease.code != "061")
    csv_text = next((crawler._download_csv_text(disease) for disease in candidates[:10]), None)
    assert csv_text
    assert crawler._parse_csv_rows(csv_text)


def test_hk_live_current_or_previous_year_csv_is_parseable(tmp_path) -> None:
    crawler = HongKongCHPCrawler(save_raw=False, raw_dir=tmp_path)
    current_year = datetime.now().year
    parsed_rows = []
    for year in (current_year, current_year - 1):
        try:
            parsed_rows = crawler._parse_csv_rows(crawler._download_year_csv(year))
        except Exception:
            continue
        if parsed_rows:
            break
    assert parsed_rows


def test_ch_live_api_exposes_version_and_sets(tmp_path) -> None:
    crawler = SwitzerlandIDDCrawler(save_raw=False, raw_dir=tmp_path)
    assert crawler.fetch_version()
    assert crawler.fetch_sets()


def test_kr_live_portal_returns_rows_without_credentials(tmp_path) -> None:
    crawler = KoreaKDCAOpenAPICrawler(
        service_key="",
        save_raw=False,
        raw_dir=tmp_path,
    )
    rows = crawler._fetch_bass_stats_list(datetime.now().year - 1)
    assert rows


def test_br_live_index_discovers_dbc_files(tmp_path) -> None:
    crawler = BrazilSINANCrawler(
        save_raw=False,
        raw_dir=tmp_path / "raw",
        cache_dir=tmp_path / "cache",
    )
    files = crawler.fetch_file_index()
    assert files
    assert any(item.filename.lower().endswith(".dbc") for item in files)
