from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.data.crawlers.jp import (
    CsvCandidate,
    DiscoveredWeekPage,
    JPCrawlerError,
    JapanIDWRCrawler,
)


@dataclass
class _FakeResponse:
    text: str = ""
    url: str = ""
    content: bytes = b""
    headers: dict | None = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}


@pytest.fixture
def crawler() -> JapanIDWRCrawler:
    return JapanIDWRCrawler()


def test_discover_year_pages_from_direct_week_entry(crawler: JapanIDWRCrawler, monkeypatch: pytest.MonkeyPatch) -> None:
    week_url = "https://id-info.jihs.go.jp/surveillance/idwr/provisional/2025/12/index.html"

    def fake_get(url: str):
        return _FakeResponse(
            text='<html><body><a href="../11/index.html">11週</a></body></html>',
            url=week_url,
        )

    monkeypatch.setattr(crawler, "get", fake_get)
    logs: list[str] = []

    assert crawler._discover_year_index_urls(logs) == [
        "https://id-info.jihs.go.jp/provisional/2025/index.html"
    ]


def test_discover_year_pages_from_jihs_rapid_menu(crawler: JapanIDWRCrawler, monkeypatch: pytest.MonkeyPatch) -> None:
    entry_url = "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/index.html"

    def fake_get(url: str):
        return _FakeResponse(
            text="""
            <html><body>
              <ul class="menu menu_list">
                <li><a class="path" href="./2026/index.html">IDWR Surveillance Data Table 2026</a></li>
                <li><a class="path" href="./2025/index.html">IDWR Surveillance Data Table 2025</a></li>
                <li><a class="path" href="./2024/index.html">IDWR Surveillance Data Table 2024</a></li>
                <li><a class="path" href="./2015/index.html">IDWR Surveillance Data Table 2015</a></li>
              </ul>
            </body></html>
            """,
            url=entry_url,
        )

    monkeypatch.setattr(crawler, "get", fake_get)

    assert crawler._discover_year_index_urls() == [
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2026/index.html",
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/index.html",
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2024/index.html",
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2015/index.html",
    ]


def test_discover_week_pages_from_relative_links(crawler: JapanIDWRCrawler, monkeypatch: pytest.MonkeyPatch) -> None:
    year_url = "https://id-info.jihs.go.jp/surveillance/idwr/provisional/2025/index.html"

    def fake_get(url: str):
        return _FakeResponse(
            text="""
            <html><body>
              <a href="./12/index.html">第12週</a>
              <a href="./11/index.html">11th week</a>
            </body></html>
            """,
            url=year_url,
        )

    monkeypatch.setattr(crawler, "get", fake_get)
    logs: list[str] = []
    pages = crawler._discover_week_index_urls(year_url, logs)

    assert [(page.year, page.week) for page in pages] == [(2025, 12), (2025, 11)]


def test_choose_csv_candidates_rejects_ambiguous_best_match(crawler: JapanIDWRCrawler) -> None:
    week_page = DiscoveredWeekPage(year=2025, week=12, url="https://example.com/2025/12/index.html")
    logs: list[str] = []
    candidates = [
        CsvCandidate(
            url="https://example.com/zensu_weekly_202512.csv",
            source_kind="zensu",
            score=100,
            year=2025,
            week=12,
        ),
        CsvCandidate(
            url="https://mirror.example.com/zensu_weekly_202512.csv",
            source_kind="zensu",
            score=100,
            year=2025,
            week=12,
        ),
    ]

    with pytest.raises(JPCrawlerError, match="Ambiguous JP CSV candidate selection"):
        crawler._choose_csv_candidates(candidates, week_page, logs)


def test_decode_csv_text_rejects_html_payload(crawler: JapanIDWRCrawler) -> None:
    response = _FakeResponse(
        url="https://example.com/file.csv",
        content=b"<html><body>oops</body></html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

    with pytest.raises(JPCrawlerError, match="Expected CSV but received HTML"):
        crawler._decode_csv_text("https://example.com/file.csv", response.content, response)


def test_parse_legacy_standardized_rows_clamps_negative_and_filters_area(crawler: JapanIDWRCrawler) -> None:
    rows = [
        ["Reporting Area", "Current MMWR Year", "MMWR WEEK", "Disease", "Current week", "Current week, flag"],
        ["総数", "2025", "12", "Influenza", "-5", ""],
        ["東京都", "2025", "12", "Influenza", "3", ""],
    ]

    parsed, diagnostics = crawler._parse_csv_rows(rows, reporting_area="総数")

    assert diagnostics.source_format == "legacy_standardized"
    assert len(parsed) == 1
    assert parsed[0].cases == 0
    assert parsed[0].reporting_area == "総数"


def test_parse_jihs_matrix_rows_with_shifted_header(crawler: JapanIDWRCrawler) -> None:
    rows = [
        ["Infectious Disease Weekly Report 2025 12th week"],
        ["", "", "Prefecture", "Influenza(excld. avian influenza and pandemic influenza)", "AIDS"],
        ["", "", "総数", "15", "2"],
    ]

    parsed, diagnostics = crawler._parse_csv_rows(rows, reporting_area="総数")

    assert diagnostics.source_format == "jihs_matrix"
    assert [(row.disease, row.cases) for row in parsed] == [("AIDS", 2), ("Influenza", 15)]


def test_parse_unknown_format_returns_diagnostics(crawler: JapanIDWRCrawler) -> None:
    rows = [["foo", "bar"], ["baz", "qux"]]

    with pytest.raises(JPCrawlerError, match="Unknown JP CSV format"):
        crawler._parse_csv_rows(rows, reporting_area="総数")
