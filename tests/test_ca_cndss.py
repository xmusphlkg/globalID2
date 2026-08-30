from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path

import pytest

from src.core.country_library import get_country_bootstrap_config
from src.core.source_scopes import (
    canonicalize_task_source,
    get_expected_scopes_for_country,
)
from src.data.crawlers.ca_national import (
    ATTRIBUTION,
    CNDSSContractError,
    CNDSS_DISEASE_CONTRACTS,
    CONTRACT_VERSION,
    MANITOBA_2023_UNAVAILABLE_CODES,
    NATIONAL_COMPLETENESS_NOTICE,
    REUSE_TERMS_URL,
    CanadaCNDSSNationalCrawler,
)
from src.data.processors.ca_national import CanadaCNDSSAnnualUpdater
from src.generation.site_data_about import build_country_source_info
from src.services.crawl_service import CrawlService


def _describe(*, last_year: int = 2023) -> dict:
    return {
        "year_min": 1924,
        "year_max": last_year,
        "descriptions": [
            {
                "code": item.code,
                "name": item.label,
                "limitation": (
                    "The 2023 data from MB were not available at time of data "
                    "preparation."
                    if item.code in MANITOBA_2023_UNAVAILABLE_CODES
                    else "Official disease-specific reporting coverage."
                ),
                "tableyears": [1924, last_year],
                "table": {
                    "MB": (
                        "2" if item.code in MANITOBA_2023_UNAVAILABLE_CODES else "1"
                    )
                },
            }
            for item in CNDSS_DISEASE_CONTRACTS
        ],
    }


def _grid(*, first_year: int = 2023, last_year: int = 2023) -> dict:
    return {
        "status": "ok",
        "records": [
            {"y": year, "d": item.code, "g": 1, "t": None, "r": None}
            for year in range(first_year, last_year + 1)
            for item in CNDSS_DISEASE_CONTRACTS
        ],
    }


def test_cndss_reviewed_contract_is_complete_and_projection_safe() -> None:
    assert len(CNDSS_DISEASE_CONTRACTS) == 70
    assert len({item.code for item in CNDSS_DISEASE_CONTRACTS}) == 70
    assert sum(item.projection_policy == "canonical" for item in CNDSS_DISEASE_CONTRACTS) == 62
    assert sum(item.projection_policy == "no_projection" for item in CNDSS_DISEASE_CONTRACTS) == 8
    by_code = {item.code: item for item in CNDSS_DISEASE_CONTRACTS}
    assert (by_code[51].concept_id, by_code[51].mapping_relation) == (
        "D071", "related"
    )
    assert by_code[51].projection_policy == "no_projection"
    assert (by_code[73].concept_id, by_code[73].mapping_relation) == (
        "D134", "exact"
    )
    assert by_code[73].projection_policy == "canonical"


def test_canada_national_pipeline_is_registered_separately_from_ontario() -> None:
    config = get_country_bootstrap_config("CA")
    assert config["crawler_config"]["sources"] == ["phac_cndss_annual"]
    assert config["crawler_config"]["public_release_enabled"] is True
    assert get_expected_scopes_for_country("CA") == ["phac_cndss_annual"]
    assert canonicalize_task_source("all", country_code="CA") == "phac_cndss_annual"
    assert CrawlService._SERIES_SOURCE_IDS["CA"] == "SRC_CA_PHAC_CNDSS"
    assert CrawlService._SERIES_SOURCE_IDS["CA-ON"] == "SRC_CA_ON_PHO_IDTO"


def test_public_source_metadata_discloses_national_coverage_limitations() -> None:
    source = build_country_source_info("ca")["sources"][0]
    assert "does not mean every jurisdiction is complete" in source["description"]
    assert "Manitoba 2023 data were unavailable for 44" in source["description"]


def test_describe_contract_accepts_reviewed_grid_and_rejects_drift() -> None:
    assert CanadaCNDSSNationalCrawler._validate_describe(_describe()) == (1924, 2023)
    changed = _describe()
    changed["descriptions"][0]["name"] = "Changed identity"
    with pytest.raises(CNDSSContractError, match="disease contract changed"):
        CanadaCNDSSNationalCrawler._validate_describe(changed)
    with pytest.raises(CNDSSContractError, match="last year changed"):
        CanadaCNDSSNationalCrawler._validate_describe(_describe(last_year=2024))


def test_describe_contract_requires_reviewed_manitoba_limitations() -> None:
    changed = _describe()
    affected = next(
        item
        for item in changed["descriptions"]
        if item["code"] in MANITOBA_2023_UNAVAILABLE_CODES
    )
    affected["limitation"] = "Changed upstream coverage note"
    with pytest.raises(CNDSSContractError, match="Manitoba 2023 limitation"):
        CanadaCNDSSNationalCrawler._validate_describe(changed)


def test_raw_archive_retains_disease_reporting_tables(tmp_path: Path) -> None:
    crawler = CanadaCNDSSNationalCrawler(save_raw=True, raw_dir=tmp_path)
    crawler._archive(_describe(), _grid())
    archived = list(tmp_path.glob("*/*/*/cndss_national_annual_*.json"))
    assert len(archived) == 1
    payload = json.loads(archived[0].read_text(encoding="utf-8"))
    assert payload["national_aggregate_is_all_jurisdiction_complete"] is False
    assert payload["national_completeness_notice"] == NATIONAL_COMPLETENESS_NOTICE
    assert payload["manitoba_2023_unavailable_disease_count"] == 44
    assert len(payload["describe"]["descriptions"]) == 70
    assert all(
        item["limitation"] and item["table"]
        for item in payload["describe"]["descriptions"]
    )


def test_bounded_request_still_rewrites_a_full_authoritative_snapshot(
    tmp_path: Path,
) -> None:
    class _Crawler(CanadaCNDSSNationalCrawler):
        def __init__(self) -> None:
            super().__init__()
            self.raw_params = None

        def _json(self, url: str, **kwargs) -> dict:
            if "describe.json" in url:
                return _describe()
            self.raw_params = kwargs["params"]
            payload = _grid(first_year=1924, last_year=2023)
            next(row for row in payload["records"] if row["y"] == 1924 and row["d"] == 6)["t"] = 1
            next(row for row in payload["records"] if row["y"] == 2023 and row["d"] == 70)["t"] = 2
            return payload

    crawler = _Crawler()
    output = tmp_path / "current.csv"
    summary = crawler.crawl_annual_baseline(output, start_year=2023)
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert summary.row_count == 2
    assert [row["Date"] for row in rows] == ["1924-01-01", "2023-01-01"]
    assert ("f", "y:..:1924,2023") in crawler.raw_params


def test_empty_lab_influenza_contract_is_not_marked_available() -> None:
    ontology = json.loads(
        (Path(__file__).resolve().parents[1] / "configs/disease_ontology.json")
        .read_text(encoding="utf-8")
    )
    assertion = next(
        item
        for item in ontology["availability"]
        if item["id"] == "AV_CA_PHAC_CNDSS_D58_ANNUAL"
    )
    assert assertion["status"] == "upstream_available_ingestion_pending"
    assert assertion["reason_code"] == (
        "cndss_dimension_contract_empty_all_year_cells_null"
    )


def test_raw_grid_preserves_explicit_zero_and_skips_null() -> None:
    payload = _grid()
    anthrax = next(row for row in payload["records"] if row["d"] == 6)
    anthrax.update({"t": 0, "r": 0.0})
    measles = next(row for row in payload["records"] if row["d"] == 70)
    measles.update({"t": 12, "r": 0.03})

    rows = CanadaCNDSSNationalCrawler._rows_from_payload(
        payload, first_year=2023, last_year=2023
    )

    assert [(row["SourceDiseaseCode"], row["Cases"]) for row in rows] == [
        ("6", "0"),
        ("70", "12"),
    ]
    assert all(row["ReportingArea"] == "CA" for row in rows)
    assert all(row["GeographyKey"] == "country:CA:national" for row in rows)
    assert all(row["MissingValuePolicy"] == "missing_is_unknown" for row in rows)
    assert all(row["SourceAttribution"] == ATTRIBUTION for row in rows)
    assert all(row["ReuseTermsURL"] == REUSE_TERMS_URL for row in rows)
    assert all(row["SourceContract"] == CONTRACT_VERSION for row in rows)
    assert json.loads(rows[0]["SourceDimensions"])["national_rate_per_100000"] == 0.0


def test_raw_grid_rejects_a_missing_annual_slot() -> None:
    payload = _grid()
    payload["records"].pop()
    with pytest.raises(CNDSSContractError, match="annual grid changed"):
        CanadaCNDSSNationalCrawler._rows_from_payload(
            payload, first_year=2023, last_year=2023
        )


def test_authoritative_csv_write_removes_withdrawn_cells(tmp_path: Path) -> None:
    output = tmp_path / "cndss.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["Date", "SourceDiseaseCode", "Cases"]
        )
        writer.writeheader()
        writer.writerow({"Date": "2022-01-01", "SourceDiseaseCode": "70", "Cases": "9"})

    written = CanadaCNDSSNationalCrawler._write_authoritative(output, [{
        "Date": "2023-01-01", "SourceDiseaseCode": "70", "Cases": "3"
    }])

    assert written == [{
        "Date": "2023-01-01", "SourceDiseaseCode": "70", "Cases": "3"
    }]
    with output.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == written


def test_updater_loads_national_rows_and_rejects_foreign_source(tmp_path: Path) -> None:
    output = tmp_path / "cndss.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Date", "Cases"])
        writer.writeheader()
        writer.writerow({"Date": "2023-01-01", "Cases": "0"})
    updater = CanadaCNDSSAnnualUpdater(output_csv=output)
    assert updater.country_code == "CA"
    assert updater.series_geography_key == "country:CA:national"
    assert updater._load_rows() == [{"Date": "2023-01-01", "Cases": "0"}]
    with pytest.raises(ValueError, match="Unsupported Canada CNDSS source"):
        updater.refresh_source(source="ontario")


@pytest.mark.asyncio
async def test_updater_database_queries_are_country_and_source_scoped() -> None:
    class _ScalarResult:
        def scalar(self):
            return date(2023, 1, 1)

    class _DeleteResult:
        rowcount = 4

    class _Database:
        calls: list[tuple[str, dict]]

        def __init__(self) -> None:
            self.calls = []

        async def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, params))
            return _DeleteResult() if sql.startswith("DELETE") else _ScalarResult()

    database = _Database()
    updater = CanadaCNDSSAnnualUpdater()
    assert await updater.get_db_latest_date(database) == date(2023, 1, 1)
    assert await updater.delete_authoritative_window(database, start_year=1924) == 4
    assert database.calls[0][1] == {
        "code": "CA", "source_id": "SRC_CA_PHAC_CNDSS"
    }
    assert database.calls[1][1] == {
        "code": "CA",
        "source_id": "SRC_CA_PHAC_CNDSS",
        "window_start": date(1924, 1, 1),
    }
