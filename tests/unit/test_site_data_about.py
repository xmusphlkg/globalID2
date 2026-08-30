from __future__ import annotations

from datetime import datetime, timezone

from scripts import generate_site_data as legacy_api
from src.generation import site_data_about as about


def test_generate_script_reexports_about_builders_and_constants() -> None:
    names = (
        "ABOUT_COUNTRY_NAMES_ZH",
        "ABOUT_SOURCE_DESCRIPTIONS_ZH",
        "ABOUT_SOURCE_LABELS_ZH",
        "CADENCE_LABELS_ZH",
        "SOURCE_DETAILS_BY_SCOPE",
        "build_about_snapshot",
        "build_country_source_info",
        "normalize_cadence_label",
        "normalize_cadence_label_zh",
        "parse_iso_timestamp",
        "resolve_snapshot_version",
    )

    for name in names:
        assert getattr(legacy_api, name) is getattr(about, name)


def test_country_source_info_preserves_source_order_and_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(
        about,
        "get_country_bootstrap_config",
        lambda _code: {
            "data_source_url": "https://example.test/source",
            "data_source_type": "api",
            "notes": "Fallback notes",
            "crawler_config": {
                "sources": ["second", "first"],
                "dashboard_url": "https://example.test/dashboard",
            },
            "parser_config": {"primary": "ExampleParser"},
        },
    )
    monkeypatch.setattr(
        about,
        "scope_display_label",
        lambda scope, *, country_code: f"{country_code}:{scope}",
    )

    result = about.build_country_source_info(
        "XX", {"source_frequency": "Quarterly"}
    )

    assert result == {
        "country_code": "XX",
        "primary_scope": "second",
        "primary_label": "XX:second",
        "primary_url": "https://example.test/source",
        "primary_type": "api",
        "parser_primary": "ExampleParser",
        "notes": "Fallback notes",
        "sources": [
            {
                "scope": "second",
                "label": "XX:second",
                "url": "https://example.test/source",
                "machine_url": None,
                "type": "api",
                "cadence": "Quarterly",
                "description": "Fallback notes",
            },
            {
                "scope": "first",
                "label": "XX:first",
                "url": "https://example.test/source",
                "machine_url": None,
                "type": "api",
                "cadence": "Quarterly",
                "description": "Fallback notes",
            },
        ],
    }


def test_province_source_info_uses_database_adapter_metadata() -> None:
    result = about.build_country_source_info(
        "CN-SH",
        {"source_frequency": "MONTHLY"},
        database_config={
            "data_source_url": "https://wsjkw.sh.gov.cn/yqxx/index.html",
            "data_source_type": "mixed",
            "crawler_config": {
                "sources": [
                    "cn_province_datacenter",
                    "cn_province_monthly_report",
                ],
                "cadence": "mixed_annual_monthly",
            },
            "parser_config": {"primary": "html_table"},
            "metadata": {
                "location_type": "subdivision",
                "parent_country_code": "CN",
                "iso_subdivision_code": "CN-SH",
            },
        },
    )

    assert result["location_type"] == "subdivision"
    assert result["parent_country_code"] == "CN"
    assert result["parser_primary"] == "html_table"
    assert [source["cadence"] for source in result["sources"]] == [
        "annual",
        "monthly",
    ]
    assert result["sources"][1]["url"] == "https://wsjkw.sh.gov.cn/yqxx/index.html"


def test_cadence_and_iso_helpers_keep_labels_and_utc_semantics() -> None:
    assert about.normalize_cadence_label(None) == "Variable"
    assert about.normalize_cadence_label("every_two_weeks") == "Every Two Weeks"
    assert about.normalize_cadence_label_zh(None) == "按来源更新"
    assert about.normalize_cadence_label_zh("monthly") == "每月"
    assert about.normalize_cadence_label_zh("custom_value") == "custom value"

    assert about.parse_iso_timestamp("not-a-date") is None
    assert about.parse_iso_timestamp("2026-08-05") == datetime(
        2026, 8, 5, tzinfo=timezone.utc
    )
    assert about.parse_iso_timestamp("2026-08-05T10:00:00+08:00") == datetime(
        2026, 8, 5, 2, tzinfo=timezone.utc
    )
    assert about.resolve_snapshot_version(
        [{"date_range": {"end": "2026-08-04"}}],
        [{"period_start": "2026-08-05T10:00:00+08:00", "period_end": None}],
    ) == "2026-08-05T02:00:00+00:00"


def test_about_snapshot_preserves_schema_country_sorting_and_source_order() -> None:
    countries = [
        {
            "code": "US",
            "name_en": "United States",
            "name_zh": "美国",
            "disease_count": 2,
            "total_cases": 20,
            "total_deaths": 1,
            "date_range": {"start": "2024-02-01", "end": "2026-07-01"},
            "source_info": {
                "sources": [
                    {
                        "scope": "nndss_api",
                        "label": "US CDC NNDSS",
                        "description": "Provisional data",
                        "url": "https://example.test/us",
                        "machine_url": "https://example.test/us.csv",
                        "type": "api",
                        "cadence": "weekly",
                    },
                    {
                        "scope": "secondary",
                        "label": "Secondary",
                        "type": "web",
                        "cadence": "monthly",
                    },
                ]
            },
        },
        {
            "code": "AU",
            "name_en": "Australia",
            "name_zh": "澳大利亚",
            "disease_count": 1,
            "total_cases": 10,
            "total_deaths": 2,
            "date_range": {"start": "2023-01-01", "end": "2025-12-31"},
            "source_info": {
                "sources": [
                    {
                        "scope": "all",
                        "label": "Australia NINDSS",
                        "type": "microsoft_bi",
                        "cadence": "monthly",
                    }
                ]
            },
        },
    ]

    result = about.build_about_snapshot(
        countries,
        [{"disease_id": "D001"}, {"disease_id": "D002"}],
        [{"report_id": "R1"}],
        "2026-08-05T02:00:00+00:00",
    )

    assert list(result) == [
        "generated_at",
        "summary",
        "metrics",
        "pipeline_steps",
        "architecture",
        "features",
        "data_sources",
        "country_coverage",
    ]
    assert result["summary"] == {
        "total_countries": 2,
        "total_diseases": 2,
        "total_reports": 1,
        "total_cases": 30,
        "total_deaths": 3,
        "coverage_start": "2023-01-01",
        "coverage_end": "2026-07-01",
        "source_count": 3,
        "cadence_en": "Monthly / Weekly",
        "cadence_zh": "每月 / 每周",
        "source_type_summary": "MICROSOFT_BI / API / WEB",
    }
    assert [row["code"] for row in result["country_coverage"]] == ["AU", "US"]
    assert [row["label_en"] for row in result["data_sources"]] == [
        "Australia NINDSS",
        "US CDC NNDSS",
        "Secondary",
    ]
    assert result["data_sources"][0]["label_zh"] == "澳大利亚 NINDSS"
    assert result["data_sources"][1]["description_zh"] == (
        "美国 CDC 国家法定传染病监测系统的临时数据。"
    )
    assert result["pipeline_steps"][2]["description_en"].endswith(
        "1 published report entry in the current release."
    )
    assert len(result["metrics"]) == 4
    assert len(result["pipeline_steps"]) == 4
    assert len(result["features"]) == 6
