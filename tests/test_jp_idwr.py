from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime

import pytest

from src.data.crawlers.jp import JapanIDWRCrawler


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

    assert crawler._discover_year_index_urls() == [
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
    pages = crawler._discover_week_index_urls(year_url)

    assert [crawler._parse_year_week_from_url(page) for page in pages] == [(2025, 12), (2025, 11)]


def test_discover_weekly_csv_urls_selects_known_csv_kinds(
    crawler: JapanIDWRCrawler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    year_url = "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/index.html"
    week_url = "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/12/index.html"

    monkeypatch.setattr(crawler, "_discover_year_index_urls", lambda: [year_url])
    monkeypatch.setattr(crawler, "_discover_week_index_urls", lambda _url: [week_url])

    def fake_get(url: str):
        assert url == week_url
        return _FakeResponse(
            text="""
            <html><body>
              <a href="./zensu202512.csv">zensu</a>
              <a href="./teiten202512.csv">teiten</a>
              <a href="./metadata202512.csv">metadata</a>
            </body></html>
            """,
            url=week_url,
        )

    monkeypatch.setattr(crawler, "get", fake_get)

    csv_urls, raw_csv_urls, logs = crawler._discover_weekly_csv_urls(existing_weeks=set())

    assert csv_urls == [
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/12/zensu202512.csv",
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/12/teiten202512.csv",
    ]
    assert set(raw_csv_urls) == {
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/12/zensu202512.csv",
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/12/teiten202512.csv",
        "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2025/12/metadata202512.csv",
    }
    assert any("[week] 2025-W12 csvs:" in line for line in logs)


def test_download_csv_table_decodes_japanese_csv(
    crawler: JapanIDWRCrawler,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    csv_url = "https://example.com/rapid/2025/12/zensu202512.csv"
    crawler.raw_dir = tmp_path
    payload = "疾病,報告\n総数,5\n".encode("cp932")

    monkeypatch.setattr(crawler, "get", lambda _url: _FakeResponse(url=csv_url, content=payload))

    assert crawler._download_csv_table(csv_url) == [["疾病", "報告"], ["総数", "5"]]
    assert (tmp_path / "2025" / "12" / "zensu202512.csv").read_bytes() == payload


def test_incremental_discovery_limits_work_to_recent_weeks(
    crawler: JapanIDWRCrawler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler.max_candidate_csvs = 2
    current_year = datetime.now().year
    current_index = (
        f"https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/{current_year}/index.html"
    )
    old_index = "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/2020/index.html"
    week_pages = [
        current_index.replace("index.html", f"{week}/index.html")
        for week in (30, 29, 28)
    ]
    monkeypatch.setattr(
        crawler,
        "_discover_year_index_urls",
        lambda: [current_index, old_index],
    )

    def discover_weeks(url: str):
        assert url == current_index
        return week_pages

    monkeypatch.setattr(crawler, "_discover_week_index_urls", discover_weeks)
    monkeypatch.setattr(
        crawler,
        "get",
        lambda url: _FakeResponse(
            text='<a href="./zensu.csv">zensu</a>',
            url=url,
        ),
    )

    csv_urls, _raw_urls, _logs = crawler._discover_weekly_csv_urls(
        existing_weeks={(current_year, 29)}
    )

    assert len(csv_urls) == 2
    assert f"/{current_year}/30/" in csv_urls[0]
    assert f"/{current_year}/29/" in csv_urls[1]


def test_incremental_refresh_merges_existing_standardized_rows(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "weekly.csv"
    output.write_text(
        'Reporting Area,Current MMWR Year,MMWR WEEK,Disease,Current week,"Current week, flag"\n'
        "総数,2026,29,AIDS,1,\n",
        encoding="utf-8",
    )
    crawler = JapanIDWRCrawler(save_raw=False)
    monkeypatch.setattr(
        crawler,
        "_discover_weekly_csv_urls",
        lambda **_kwargs: (["https://example/zensu.csv"], [], []),
    )
    monkeypatch.setattr(crawler, "_download_csv_table", lambda _url: [])
    monkeypatch.setattr(
        crawler,
        "_normalize_rows",
        lambda *_args, **_kwargs: [
            {
                "Reporting Area": "総数",
                "Current MMWR Year": "2026",
                "MMWR WEEK": "30",
                "Disease": "AIDS",
                "Current week": "2",
                "Current week, flag": "",
            }
        ],
    )

    summary = crawler.crawl_standardized_csv(output)

    with output.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {(row["MMWR WEEK"], row["Current week"]) for row in rows} == {
        ("29", "1"),
        ("30", "2"),
    }
    assert summary.row_count == 2


def test_parse_legacy_standardized_rows_clamps_negative_and_filters_area(crawler: JapanIDWRCrawler) -> None:
    rows = [
        ["Reporting Area", "Current MMWR Year", "MMWR WEEK", "Disease", "Current week", "Current week, flag"],
        ["総数", "2025", "12", "Influenza", "-5", ""],
        ["東京都", "2025", "12", "Influenza", "3", ""],
    ]

    parsed = crawler._normalize_rows(rows, reporting_area="総数", source_kind="zensu")

    assert len(parsed) == 1
    assert parsed[0]["Current week"] == "0"
    assert parsed[0]["Reporting Area"] == "総数"


def test_parse_jihs_matrix_rows_with_shifted_header(crawler: JapanIDWRCrawler) -> None:
    rows = [
        ["Infectious Disease Weekly Report 2025 12th week"],
        ["", "", "Prefecture", "Influenza(excld. avian influenza and pandemic influenza)", "AIDS"],
        ["", "", "総数", "15", "2"],
    ]

    parsed = crawler._normalize_rows(rows, reporting_area="総数", source_kind="zensu")

    assert [(row["Disease"], row["Current week"]) for row in parsed] == [("AIDS", "2"), ("Influenza", "15")]


def test_parse_unknown_format_returns_diagnostics(crawler: JapanIDWRCrawler) -> None:
    rows = [["foo", "bar"], ["baz", "qux"]]

    assert crawler._normalize_rows(rows, reporting_area="総数", source_kind="zensu") == []
