from __future__ import annotations

from datetime import date

from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    scope_display_label,
    scope_from_data_source,
)
from src.data.crawlers.hk import aggregate_annual_csv_rows


def test_hk_chp_annual_csv_rows_skip_totals_and_persons_affected():
    rows = [
        {"Disease": "Cholera", "Jan": "1", "Feb": "0", "Mar": "", "Total": "1"},
        {"Disease": "Food poisoning - Outbreaks", "Jan": "2", "Feb": "", "Mar": "", "Total": "2"},
        {"Disease": "Food poisoning - Persons affected", "Jan": "99", "Feb": "", "Mar": "", "Total": "99"},
        {
            "Disease": "Total (persons affected in Food poisoning outbreaks were excluded in the monthly total and grand total)",
            "Jan": "3",
            "Feb": "0",
            "Mar": "",
            "Total": "3",
        },
    ]

    aggregated = aggregate_annual_csv_rows(
        2026,
        rows,
        months={(2026, 1), (2026, 2), (2026, 3)},
        source_url="https://www.chp.gov.hk/files/misc/nid2026en.csv",
    )

    assert aggregated == [
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "Cholera",
            "DiseaseCode": "",
            "Year": "2026",
            "Month": "1",
            "Cases": "1",
            "AnnualTotal": "1",
            "RecordType": "cases",
            "Source": "Hong Kong, China CHP Notifiable Infectious Diseases",
            "SourceURL": "https://www.chp.gov.hk/files/misc/nid2026en.csv",
        },
        {
            "Date": "2026-02-01",
            "RawDiseaseLabel": "Cholera",
            "DiseaseCode": "",
            "Year": "2026",
            "Month": "2",
            "Cases": "0",
            "AnnualTotal": "1",
            "RecordType": "cases",
            "Source": "Hong Kong, China CHP Notifiable Infectious Diseases",
            "SourceURL": "https://www.chp.gov.hk/files/misc/nid2026en.csv",
        },
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "Food poisoning - Outbreaks",
            "DiseaseCode": "",
            "Year": "2026",
            "Month": "1",
            "Cases": "2",
            "AnnualTotal": "2",
            "RecordType": "outbreaks",
            "Source": "Hong Kong, China CHP Notifiable Infectious Diseases",
            "SourceURL": "https://www.chp.gov.hk/files/misc/nid2026en.csv",
        },
    ]


def test_hk_chp_source_scope_aliases():
    assert canonicalize_task_source("chp", country_code="HK") == "chp_notifiable"
    assert canonicalize_task_source("hk", country_code="HK") == "chp_notifiable"
    assert canonicalize_task_source("all", country_code="HK") == "chp_notifiable"
    assert scope_from_data_source("Hong Kong, China CHP Notifiable Infectious Diseases") == "chp_notifiable"
    assert canonical_data_source_label("Hong Kong, China CHP Notifiable Infectious Diseases") == "Hong Kong, China CHP Notifiable Diseases"
    assert scope_display_label("chp_notifiable", country_code="HK") == "Hong Kong, China CHP Notifiable Diseases"


def test_hk_refresh_source_falls_back_to_previous_csv_snapshot(tmp_path, monkeypatch):
    from src.data.processors.hk import HKMonthlyUpdater

    output_csv = tmp_path / "hk.csv"
    output_csv.write_text(
        "\n".join(
            [
                ",Disease,DiseaseCode,Year,Month,Date,Cases,AnnualTotal,RecordType,Source,SourceURL",
                "1,Cholera,,2026,3,2026-03-01,1,1,cases,Hong Kong, China CHP Notifiable Infectious Diseases,https://example.test/nid2026en.csv",
            ]
        ),
        encoding="utf-8",
    )

    def fake_crawl_monthly_national(self, output_csv, months=None):
        raise RuntimeError("temporary upstream outage")

    monkeypatch.setattr(
        "src.data.processors.hk.HongKongCHPCrawler.crawl_monthly_national",
        fake_crawl_monthly_national,
    )

    updater = HKMonthlyUpdater(output_csv=output_csv)
    result = updater.refresh_source(months=[(2026, 3)], raw_dir=tmp_path / "raw")

    assert result.source_latest_date == date(2026, 3, 1)
    assert result.rows[0]["RawDiseaseLabel"] == "Cholera"
    assert any("previous CSV snapshot" in line for line in result.script_logs)
