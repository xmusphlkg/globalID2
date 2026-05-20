from __future__ import annotations

from datetime import date

from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    scope_display_label,
    scope_from_data_source,
)
from src.data.crawlers.kr import (
    DEFAULT_DPORTAL_STATS_AJAX_URL,
    DEFAULT_PORTAL_SOURCE_NAME,
    aggregate_period_region_rows,
    KoreaKDCAOpenAPICrawler,
    normalize_kdca_download_rows,
    parse_kdca_period_month,
)
from src.data.processors.kr import KRMonthlyUpdater
from src.services.crawl_service import CrawlService


def test_kdca_period_month_parser_accepts_common_formats():
    assert parse_kdca_period_month("2026-01").isoformat() == "2026-01-01"
    assert parse_kdca_period_month("2026.1").isoformat() == "2026-01-01"
    assert parse_kdca_period_month("2026년 12월").isoformat() == "2026-12-01"
    assert parse_kdca_period_month("202601").isoformat() == "2026-01-01"
    assert parse_kdca_period_month("2026") is None


def test_kdca_period_region_aggregation_keeps_domestic_and_imported_counts():
    rows = [
        {
            "period": "2026-01",
            "icdGroupNm": "제2급",
            "icdNm": "뎅기열",
            "resultVal": "5",
            "dmstcVal": "2",
            "outnatnVal": "3",
        },
        {
            "period": "2026-01",
            "icdGroupNm": "제2급",
            "icdNm": "뎅기열",
            "resultVal": "1",
            "dmstcVal": "1",
            "outnatnVal": "0",
        },
        {
            "period": "2026-02",
            "icdGroupNm": "제2급",
            "icdNm": "뎅기열",
            "resultVal": "9",
            "dmstcVal": "9",
            "outnatnVal": "0",
        },
    ]

    aggregated = aggregate_period_region_rows(rows, months={(2026, 1)}, source_url="https://example.test")

    assert aggregated == [
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "뎅기열",
            "DiseaseCode": "",
            "DiseaseGroup": "제2급",
            "Year": "2026",
            "Month": "1",
            "Cases": "6",
            "LocalCases": "3",
            "ImportedCases": "3",
            "Source": "Korea KDCA EID Open API",
            "SourceURL": "https://example.test",
        }
    ]


def test_kr_monthly_updater_loads_crawler_csv_shape(tmp_path):
    csv_path = tmp_path / "korea_national_monthly.csv"
    csv_path.write_text(
        "\n".join(
            [
                ",Disease,DiseaseCode,DiseaseGroup,Year,Month,Date,Cases,LocalCases,ImportedCases,Source,SourceURL",
                "1,뎅기열,,Class 3,2026,1,2026-01-01,6,3,3,Korea KDCA EID Open API,https://example.test",
            ]
        ),
        encoding="utf-8",
    )

    rows = KRMonthlyUpdater(output_csv=csv_path)._load_rows(csv_path)

    assert rows == [
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "뎅기열",
            "DiseaseCode": "",
            "DiseaseGroup": "Class 3",
            "Year": "2026",
            "Month": "1",
            "Cases": "6",
            "LocalCases": "3",
            "ImportedCases": "3",
            "Source": "Korea KDCA EID Open API",
            "SourceURL": "https://example.test",
        }
    ]


def test_kdca_portal_download_wide_rows_normalize_to_monthly_rows():
    rows = [
        {
            "TITLE": "제3급",
            "SUBTITLE": "뎅기열",
            "COLUMN1": "6",
            "COLUMN2": "0",
            "COLUMN13": "6",
        },
        {
            "감염병급": "제2급",
            "감염병명": "홍역",
            "1월": "2",
            "2월": "-",
            "누계": "2",
        },
    ]

    normalized = normalize_kdca_download_rows(rows, fallback_year=2025)

    assert normalized == [
        {
            "Date": "2025-01-01",
            "RawDiseaseLabel": "뎅기열",
            "DiseaseCode": "",
            "DiseaseGroup": "제3급",
            "Year": "2025",
            "Month": "1",
            "Cases": "6",
            "LocalCases": "0",
            "ImportedCases": "0",
            "Source": DEFAULT_PORTAL_SOURCE_NAME,
            "SourceURL": "https://dportal.kdca.go.kr/pot/is/inftnsds.do",
        },
        {
            "Date": "2025-01-01",
            "RawDiseaseLabel": "홍역",
            "DiseaseCode": "",
            "DiseaseGroup": "제2급",
            "Year": "2025",
            "Month": "1",
            "Cases": "2",
            "LocalCases": "0",
            "ImportedCases": "0",
            "Source": DEFAULT_PORTAL_SOURCE_NAME,
            "SourceURL": "https://dportal.kdca.go.kr/pot/is/inftnsds.do",
        },
        {
            "Date": "2025-02-01",
            "RawDiseaseLabel": "뎅기열",
            "DiseaseCode": "",
            "DiseaseGroup": "제3급",
            "Year": "2025",
            "Month": "2",
            "Cases": "0",
            "LocalCases": "0",
            "ImportedCases": "0",
            "Source": DEFAULT_PORTAL_SOURCE_NAME,
            "SourceURL": "https://dportal.kdca.go.kr/pot/is/inftnsds.do",
        },
    ]


def test_kdca_portal_download_source_file_refresh_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    source_file = tmp_path / "kdca_2025.json"
    source_file.write_text(
        """
        {
          "value": [
            {"TITLE": "제3급", "SUBTITLE": "뎅기열", "COLUMN1": "6", "COLUMN2": "0"}
          ]
        }
        """,
        encoding="utf-8",
    )
    updater = KRMonthlyUpdater(output_csv=tmp_path / "korea_national_monthly.csv")

    result = updater.refresh_source(
        months=[(2025, 1), (2025, 2)],
        source_file=source_file,
    )

    assert [row["Date"] for row in result.rows] == ["2025-01-01", "2025-02-01"]
    assert [row["Cases"] for row in result.rows] == ["6", "0"]
    assert any("kind=download" in line for line in result.script_logs)


def test_kr_crawl_monthly_uses_dportal_ajax_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    fetched_years = []

    def fake_fetch_bass_stats_list(self, year: int, **kwargs):
        fetched_years.append(year)
        return [
            {
                "TITLE": "제3급",
                "SUBTITLE": "쯔쯔가무시",
                "COLUMN1": "6",
                "COLUMN2": "0",
                "COLUMN3": "1",
            }
        ]

    monkeypatch.setattr(
        KoreaKDCAOpenAPICrawler,
        "_fetch_bass_stats_list",
        fake_fetch_bass_stats_list,
    )

    crawler = KoreaKDCAOpenAPICrawler()
    output_csv = tmp_path / "korea_national_monthly.csv"
    summary = crawler.crawl_monthly_national(
        output_csv,
        months=[(2024, 1), (2025, 2)],
    )

    assert fetched_years == [2024, 2025]
    assert summary.source_kind == "portal"
    assert summary.source_url == DEFAULT_DPORTAL_STATS_AJAX_URL
    assert summary.row_count == 2
    assert output_csv.read_text(encoding="utf-8").count("\n") >= 3
    assert output_csv.exists()


def test_kr_history_months_uses_configured_start_year(tmp_path):
    updater = KRMonthlyUpdater(output_csv=tmp_path / "korea_national_monthly.csv")

    assert updater.history_months(start_year=2025, end_date=date(2026, 2, 1)) == [
        (2025, 1),
        (2025, 2),
        (2025, 3),
        (2025, 4),
        (2025, 5),
        (2025, 6),
        (2025, 7),
        (2025, 8),
        (2025, 9),
        (2025, 10),
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]


def test_kr_crawl_service_accepts_task_history_start_year(tmp_path):
    class DummyTask:
        input_data = {"start_year": "2005"}

    updater = KRMonthlyUpdater(output_csv=tmp_path / "korea_national_monthly.csv")

    assert CrawlService._kr_history_start_year(DummyTask(), updater) == 2005


def test_kr_download_source_from_parent_directory_year_is_respected(tmp_path):
    year_dir = tmp_path / "2026"
    year_dir.mkdir(parents=True)
    source_file = year_dir / "kdca_export.csv"
    source_file.write_text(
        "TITLE,SUBTITLE,COLUMN1,COLUMN2\n제3급,뎅기열,6,0\n",
        encoding="utf-8",
    )

    result = KRMonthlyUpdater(output_csv=tmp_path / "korea_national_monthly.csv").refresh_source(
        months=[(2026, 1), (2026, 2)],
        source_file=source_file,
    )

    assert [row["Date"] for row in result.rows] == ["2026-01-01", "2026-02-01"]
    assert result.rows[0]["RawDiseaseLabel"] == "뎅기열"


def test_kdca_source_scope_aliases():
    assert canonicalize_task_source("kdca", country_code="KR") == "kdca_open_api"
    assert canonicalize_task_source("kr", country_code="KR") == "kdca_open_api"
    assert canonicalize_task_source("kosis", country_code="KR") == "kdca_open_api"
    assert canonicalize_task_source("all", country_code="KR") == "kdca_open_api"
    assert scope_from_data_source("Korea KDCA EID Open API") == "kdca_open_api"
    assert scope_from_data_source("Korea KDCA EID Portal Download") == "kdca_open_api"
    assert scope_from_data_source("Korea KOSIS Download") == "kdca_open_api"
    assert canonical_data_source_label("Korea KDCA EID Open API") == "Korea KDCA EID"
    assert canonical_data_source_label("Korea KOSIS Download") == "Korea KDCA EID"
    assert scope_display_label("kdca_open_api", country_code="KR") == "Korea KDCA EID"
