from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from scripts import generate_site_data
from src.generation import site_data_queries


QUERY_EXPORTS = (
    "compact_report_metadata",
    "enrich_source_attribution",
    "fetch_countries",
    "fetch_country_briefs",
    "fetch_country_frequency_meta",
    "fetch_disease_export_layers",
    "fetch_disease_knowledge_briefs",
    "fetch_disease_records",
    "fetch_disease_records_direct",
    "fetch_disease_series_records",
    "fetch_report_detail",
    "fetch_reports",
    "has_population_table",
    "has_table",
    "iso_or_none",
    "safe_float",
    "safe_int",
    "source_metadata_field",
)


class FakeRows(list):
    def fetchone(self):
        return self[0] if self else None


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return self.results.pop(0)


def mapped_row(**values):
    return SimpleNamespace(_mapping=values)


def test_generate_script_reexports_query_api_without_wrappers() -> None:
    """Keep historical script imports and monkeypatch targets stable."""
    for name in QUERY_EXPORTS:
        assert getattr(generate_site_data, name) is getattr(site_data_queries, name)


@pytest.mark.asyncio
async def test_fetch_countries_preserves_ordering_query_and_plain_dict_rows() -> None:
    session = FakeSession(
        FakeRows(
            [
                mapped_row(
                    code="AU",
                    name="Australia",
                    name_en="Australia",
                    name_local="Australia",
                    language="en",
                    timezone="Australia/Sydney",
                )
            ]
        )
    )

    countries = await site_data_queries.fetch_countries(session)

    assert countries == [
        {
            "code": "AU",
            "name": "Australia",
            "name_en": "Australia",
            "name_local": "Australia",
            "language": "en",
            "timezone": "Australia/Sydney",
        }
    ]
    sql, params = session.calls[0]
    assert "FROM countries" in sql
    assert "ORDER BY code" in sql
    assert params is None


@pytest.mark.asyncio
async def test_fetch_countries_excludes_public_release_gated_country() -> None:
    session = FakeSession(
        FakeRows(
            [
                mapped_row(
                    code="FI",
                    name="Finland",
                    name_en="Finland",
                    name_local="Suomi",
                    language="fi-FI",
                    timezone="Europe/Helsinki",
                ),
                mapped_row(
                    code="SE",
                    name="Sweden",
                    name_en="Sweden",
                    name_local="Sverige",
                    language="sv-SE",
                    timezone="Europe/Stockholm",
                ),
            ]
        )
    )

    countries = await site_data_queries.fetch_countries(session)

    assert [country["code"] for country in countries] == ["FI"]


@pytest.mark.asyncio
async def test_has_population_table_delegates_to_parameterized_table_check() -> None:
    session = FakeSession(FakeRows([(True,)]))

    assert await site_data_queries.has_population_table(session) is True

    sql, params = session.calls[0]
    assert "information_schema.tables" in sql
    assert "table_name = :table_name" in sql
    assert params == {"table_name": "population_records"}


@pytest.mark.asyncio
async def test_frequency_metadata_keeps_weekly_classification_and_query_params() -> None:
    session = FakeSession(
        FakeRows([(True,)]),
        FakeRows([(True,)]),
        FakeRows(
            [
                mapped_row(temporal_granularity="weekly"),
            ]
        )
    )

    metadata = await site_data_queries.fetch_country_frequency_meta(session, "US")

    assert metadata == {
        "source_frequency": "WEEKLY",
        "source_frequencies": ["WEEKLY"],
        "canonical_frequency": "SOURCE_REPORTED_PERIODS",
        "aggregation_rule": "preserve_source_period_counts",
    }
    sql, params = session.calls[2]
    assert "disease_surveillance_series" in sql
    assert "disease_series_observations" in sql
    assert params == {"code": "US"}


@pytest.mark.asyncio
async def test_frequency_metadata_does_not_misclassify_annual_january_rows() -> None:
    session = FakeSession(
        FakeRows([(False,)]),
        FakeRows(
            [
                mapped_row(report_date=date(2022, 1, 1)),
                mapped_row(report_date=date(2023, 1, 1)),
                mapped_row(report_date=date(2024, 1, 1)),
            ]
        ),
    )

    metadata = await site_data_queries.fetch_country_frequency_meta(session, "IS")

    assert metadata["source_frequency"] == "ANNUAL"
    assert metadata["source_frequencies"] == ["ANNUAL"]
    assert metadata["canonical_frequency"] == "SOURCE_REPORTED_PERIODS"


@pytest.mark.asyncio
async def test_direct_disease_rows_keep_site_facing_normalization() -> None:
    session = FakeSession(
        FakeRows(
            [
                mapped_row(
                    date=date(2026, 2, 1),
                    year_month="2026-02",
                    disease_id="D001",
                    cases=None,
                    deaths=None,
                    recoveries=0,
                    incidence_rate=float("nan"),
                    incidence_rate_source=None,
                    mortality_rate="1.25",
                    data_quality="validated",
                )
            ]
        )
    )

    records = await site_data_queries.fetch_disease_records_direct(
        session, "CN", False
    )

    assert records == [
        {
            "date": "2026-02-01",
            "year_month": "2026-02",
            "disease_id": "D001",
            "cases": 0,
            "deaths": 0,
            "recoveries": 0,
            "incidence_rate": None,
            "incidence_rate_source": "missing_population",
            "mortality_rate": 1.25,
            "data_quality": "validated",
        }
    ]
    sql, params = session.calls[0]
    assert "FROM disease_records dr" in sql
    assert "ORDER BY timezone('UTC', dr.time)::date ASC, d.name" in sql
    assert "population_records" not in sql
    assert "raw_data::jsonb ->> 'Frequency'" in sql
    assert "national_registry_notifications" in sql
    assert params == {"code": "CN"}


@pytest.mark.asyncio
async def test_series_query_exports_inactive_history_with_active_selection_flag() -> None:
    session = FakeSession(
        FakeRows(
            [
                mapped_row(
                    date=date(2020, 1, 1),
                    year_month="2020-01",
                    disease_id="D028",
                    cases=3,
                    deaths=0,
                    recoveries=0,
                    incidence_rate=None,
                    incidence_rate_source="missing_population",
                    mortality_rate=None,
                    series_code="SER_IS_HISTORY_PERTUSSIS_MONTHLY",
                    series_is_active=False,
                    valid_from=None,
                    valid_to=None,
                )
            ]
        )
    )

    records = await site_data_queries.fetch_disease_series_records(
        session, "IS", False
    )

    assert records[0]["series_is_active"] is False
    assert records[0]["date"] == "2020-01-01"
    sql, params = session.calls[0]
    assert "dss.is_active AS series_is_active" in sql
    assert "dss.is_active IS TRUE" not in sql
    assert params == {
        "code": "IS",
        "geography_key": "country:IS:national",
    }
