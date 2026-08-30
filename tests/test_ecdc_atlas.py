from __future__ import annotations

from datetime import date
import csv
from pathlib import Path
import pytest

from src.core.country_library import get_country_bootstrap_config
from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRY_CODES
from src.data.crawlers.ecdc import (
    ATTRIBUTION,
    ECDCAtlasCrawler,
    ECDCTopicContract,
    TOPIC_CONTRACTS,
    _annual_count_measure,
)
from src.data.processors.ecdc import ECDCAnnualUpdater
from src.services.crawl_service import CrawlService


def test_ecdc_contract_is_unique_and_fr_is_public_with_attribution() -> None:
    assert len(TOPIC_CONTRACTS) == 55
    assert len({item.source_code for item in TOPIC_CONTRACTS}) == 55

    config = get_country_bootstrap_config("FR")
    assert config["public_release_enabled"] is True
    assert config["crawler_config"]["required_attribution"] == ATTRIBUTION
    assert config["crawler_config"]["series_quality_guard"]["registry_coverage"] == "required"


def test_annual_count_measure_requires_country_year_count() -> None:
    measures = [
        {
            "Id": 4,
            "Index": 2,
            "Code": "TEST.RATE",
            "Unit": "R",
            "Label": "Rate",
            "ResolutionList": {"Resolutions": [{"GeoLevelNumber": 2, "TimeUnitCode": "Y"}]},
        },
        {
            "Id": 9,
            "Index": 1,
            "Code": "TEST.COUNT",
            "Unit": "N",
            "Label": "Number of cases",
            "IsDefault": True,
            "ResolutionList": {"Resolutions": [{"GeoLevelNumber": 2, "TimeUnitCode": "Y"}]},
        },
    ]
    assert _annual_count_measure(measures)["Id"] == 9


def test_greece_uses_ecdc_el_code_without_changing_public_identity() -> None:
    crawler = ECDCAtlasCrawler("GR")
    assert crawler.country_code == "GR"
    assert crawler.source_geo_code == "EL"


def test_shared_ecdc_adapter_covers_all_eu_eea_country_codes() -> None:
    assert len(ECDC_BASELINE_COUNTRY_CODES) == 31
    assert ECDCAtlasCrawler("BE").source_geo_code == "BE"
    assert ECDCAtlasCrawler("GB").source_geo_code == "UK"
    assert CrawlService._PIPELINES["BE"].handler_name == "_execute_ecdc_annual"
    # A national high-frequency pipeline remains the default; callers select
    # the independent ECDC fallback with source=ecdc_atlas_annual.
    assert CrawlService._PIPELINES["FI"].handler_name == "_execute_fi_monthly"


def test_permission_gated_national_sources_do_not_enter_public_ecdc_export() -> None:
    austria = get_country_bootstrap_config("AT")
    ireland = get_country_bootstrap_config("IE")
    assert austria["crawler_config"]["sources"] == [
        "ages_radar",
        "ecdc_atlas_annual",
    ]
    assert ireland["crawler_config"]["sources"][-1] == "ecdc_atlas_annual"
    assert austria["public_source_systems"] == ["SRC_AT_ECDC_ATLAS"]
    assert ireland["public_source_systems"] == ["SRC_IE_ECDC_ATLAS"]
    assert austria["public_legacy_enabled"] is False
    assert ireland["public_legacy_enabled"] is False


class _FixtureCrawler(ECDCAtlasCrawler):
    def __init__(self, payloads: dict[tuple[str, tuple[tuple[str, object], ...]], dict]) -> None:
        super().__init__("FR")
        self.payloads = payloads

    def _json(self, path: str, **params) -> dict:
        return self.payloads[(path, tuple(sorted(params.items())))]


def _measure(measure_id: int) -> dict:
    return {
        "Measures": [{
            "Id": measure_id,
            "Index": 1,
            "Code": "FIXTURE.COUNT",
            "Unit": "N",
            "Label": "Number of cases",
            "IsDefault": True,
            "ResolutionList": {"Resolutions": [{"GeoLevelNumber": 2, "TimeUnitCode": "Y"}]},
        }]
    }


def test_component_series_intersection_preserves_zero_and_missing_unknown(tmp_path: Path) -> None:
    contract = ECDCTopicContract(
        "DIPH", "Diphtheria", ("first", "second"), "D029"
    )
    measure_path = "GetIndicatorMeasuresForHealthTopicDatasetAndPopulation"
    result_path = "GetMeasureResultsForTimeUnitAndGeoRegion"
    payloads = {
        (measure_path, (("datasetId", 27), ("healthtopicId", 10), ("measurePopulation", "first"))): _measure(101),
        (measure_path, (("datasetId", 27), ("healthtopicId", 10), ("measurePopulation", "second"))): _measure(102),
        (result_path, (("geoCode", "FR"), ("measureId", 101), ("timeUnit", "Y"))): {
            "MeasureResults": [
                {"GeoCountry": "FR", "TimeCode": "2020", "YValue": 0},
                {"GeoCountry": "FR", "TimeCode": "2021", "YValue": 2},
            ]
        },
        (result_path, (("geoCode", "FR"), ("measureId", 102), ("timeUnit", "Y"))): {
            "MeasureResults": [{"GeoCountry": "FR", "TimeCode": "2020", "YValue": 0}]
        },
    }

    rows, _ = _FixtureCrawler(payloads)._topic_rows(contract, 10, start_year=1990)

    assert [(row["Date"], row["Cases"]) for row in rows] == [(date(2020, 1, 1).isoformat(), "0")]
    assert rows[0]["MissingValuePolicy"] == "missing_is_unknown"
    assert rows[0]["SourceAttribution"] == ATTRIBUTION
    assert rows[0]["Dimensions"] == "{}"
    assert '"topic_code": "DIPH"' in rows[0]["SourceDimensions"]


@pytest.mark.asyncio
async def test_latest_date_query_uses_registry_source_system_column() -> None:
    class _Result:
        def scalar(self):
            return date(2025, 1, 1)

    class _Database:
        statement = ""
        params: dict[str, str] = {}

        async def execute(self, statement, params):
            self.statement = str(statement)
            self.params = params
            return _Result()

    database = _Database()
    latest = await ECDCAnnualUpdater().get_db_latest_date(database)

    assert latest == date(2025, 1, 1)
    assert "series.source_system=:source_id" in database.statement
    assert database.params == {"code": "FR", "source_id": "SRC_FR_ECDC_ATLAS"}


@pytest.mark.asyncio
async def test_authoritative_database_delete_is_source_and_window_scoped() -> None:
    class _Result:
        rowcount = 7

    class _Database:
        statement = ""
        params = {}

        async def execute(self, statement, params):
            self.statement = str(statement)
            self.params = params
            return _Result()

    database = _Database()
    deleted = await ECDCAnnualUpdater("ES").delete_authoritative_window(
        database, start_year=1990
    )

    assert deleted == 7
    assert "series.country_code=:code" in database.statement
    assert "series.source_system=:source_id" in database.statement
    assert "obs.time >= :window_start" in database.statement
    assert database.params == {
        "code": "ES",
        "source_id": "SRC_ES_ECDC_ATLAS",
        "window_start": date(1990, 1, 1),
    }


def test_authoritative_window_refresh_removes_withdrawn_cells(tmp_path: Path) -> None:
    output = tmp_path / "ecdc.csv"
    fields = ["Date", "SourceDiseaseCode", "Cases"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([
            {"Date": "1989-01-01", "SourceDiseaseCode": "meas", "Cases": "1"},
            {"Date": "2020-01-01", "SourceDiseaseCode": "meas", "Cases": "2"},
            {"Date": "2021-01-01", "SourceDiseaseCode": "meas", "Cases": "3"},
        ])

    rows = ECDCAtlasCrawler._write_merged(
        output,
        [{"Date": "2021-01-01", "SourceDiseaseCode": "meas", "Cases": "4"}],
        replace_from_year=2020,
    )

    assert [(row["Date"], row["Cases"]) for row in rows] == [
        ("1989-01-01", "1"),
        ("2021-01-01", "4"),
    ]
