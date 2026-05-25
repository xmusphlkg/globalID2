from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    get_known_task_sources,
    scope_display_label,
    scope_from_data_source,
)
from src.data.crawlers.ch import choose_national_series_config, parse_idd_period
from src.data.processors.ch import CHMonthlyUpdater
from src.services.crawl_service import CrawlService


def test_idd_period_parser_accepts_month_week_and_year_values():
    assert parse_idd_period(201301, "month") == date(2013, 1, 1)
    assert parse_idd_period("202009", "iso_week") == date(2020, 2, 24)
    assert parse_idd_period("2025", "year") == date(2025, 1, 1)
    assert parse_idd_period("202554", "iso_week") is None
    assert parse_idd_period("202500", "month") is None


def test_idd_choose_national_config_prefers_ch_total_when_available():
    config, geography = choose_national_series_config(
        [
            {
                "agegroup": "agegroup_food",
                "agegroup_food": "all",
                "CHFL": "CHFL",
                "georegion": "CHFL",
                "sex": "all",
            },
            {
                "agegroup": "agegroup_food",
                "agegroup_food": "all",
                "country": "CH",
                "georegion": "country",
                "sex": "all",
            },
        ]
    )

    assert geography == "CH"
    assert config["country"] == "CH"


def test_idd_choose_national_config_rejects_age_specific_series():
    config, geography = choose_national_series_config(
        [
            {
                "agegroup": "agegroup_sti",
                "agegroup_sti": "0 - 14",
                "CHFL": "CHFL",
                "georegion": "CHFL",
                "sex": "all",
            },
            {
                "agegroup": "agegroup_sti",
                "agegroup_sti": "all",
                "CHFL": "CHFL",
                "georegion": "CHFL",
                "sex": "all",
            },
        ]
    )

    assert geography == "CHFL"
    assert config["agegroup_sti"] == "all"


def test_ch_idd_source_scope_aliases():
    assert canonicalize_task_source("foph", country_code="CH") == "foph_idd"
    assert canonicalize_task_source("ch", country_code="CH") == "foph_idd"
    assert canonicalize_task_source("all", country_code="CH") == "foph_idd"
    assert "foph_idd" in get_known_task_sources("CH")
    assert scope_from_data_source("Switzerland FOPH IDD Mandatory Reporting System") == "foph_idd"
    assert canonical_data_source_label("Switzerland FOPH IDD Mandatory Reporting System") == "Switzerland FOPH IDD"
    assert scope_display_label("foph_idd", country_code="CH") == "Switzerland FOPH IDD"


def test_ch_task_scope_is_recognized_from_dynamic_source_registry():
    from dashboard.api.routers.sources import _scope_from_task

    task = SimpleNamespace(
        input_data={"country_code": "CH", "source": "foph_idd"},
        task_name="Crawl CH Data (foph_idd)",
    )

    assert _scope_from_task(task) == "foph_idd"


def test_crawl_service_supported_country_registry_includes_ch():
    assert "CH" in CrawlService.supported_country_codes()
    assert "CH" in CrawlService.supported_country_text()


@pytest.mark.asyncio
async def test_crawl_service_unsupported_country_error_uses_registry_list():
    task = SimpleNamespace(input_data={})

    with pytest.raises(ValueError) as exc_info:
        await CrawlService().execute(
            task=task,
            country_code="XX",
            source="all",
            force=False,
            process=False,
            save_raw=False,
            fill_missing=False,
        )

    assert "Unsupported country: XX" in str(exc_info.value)
    assert f"Available: {CrawlService.supported_country_text()}" in str(exc_info.value)
    assert "CH" in str(exc_info.value)


def test_ch_monthly_updater_loads_idd_csv_shape(tmp_path):
    csv_path = tmp_path / "switzerland_idd_cases.csv"
    csv_path.write_text(
        "\n".join(
            [
                ",Disease,DiseaseCode,Year,Month,ISOWeek,Date,PeriodType,PeriodValue,Cases,Geography,Group,DataComplete,Trend,SourceDate,Version,Source,SourceURL",
                "1,Campylobacteriosis,campylobacteriosis,2026,3,,2026-03-01,month,202603,810,CHFL,,TRUE,rising,2026-05-12,20260520,Switzerland FOPH IDD Mandatory Reporting System,https://example.test/api",
            ]
        ),
        encoding="utf-8",
    )

    rows = CHMonthlyUpdater(output_csv=csv_path)._load_rows(csv_path)

    assert rows == [
        {
            "Date": "2026-03-01",
            "RawDiseaseLabel": "Campylobacteriosis",
            "DiseaseCode": "campylobacteriosis",
            "Year": "2026",
            "Month": "3",
            "ISOWeek": "",
            "PeriodType": "month",
            "PeriodValue": "202603",
            "Cases": "810",
            "Geography": "CHFL",
            "Group": "",
            "DataComplete": "TRUE",
            "Trend": "rising",
            "SourceDate": "2026-05-12",
            "Version": "20260520",
            "Source": "Switzerland FOPH IDD Mandatory Reporting System",
            "SourceURL": "https://example.test/api",
        }
    ]
