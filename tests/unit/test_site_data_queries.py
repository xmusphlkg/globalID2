from __future__ import annotations

from datetime import date, datetime, timezone
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


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_export_stream_batches_preserve_order_and_close_on_failure(fail):
    class Stream:
        closed = False

        async def partitions(self, size):
            assert size == 2
            yield [1, 2]
            if fail:
                raise RuntimeError("stream failed")
            yield [3]

        async def close(self):
            self.closed = True

    stream = Stream()

    class Session:
        async def stream(self, statement, params, *, execution_options):
            assert execution_options == {"yield_per": 2}
            return stream

    result = []
    try:
        async for row in site_data_queries._iter_query_rows(Session(), "query", {}, batch_size=2):
            result.append(row)
    except RuntimeError as exc:
        assert fail and str(exc) == "stream failed"
    else:
        assert not fail
    assert result == ([1, 2] if fail else [1, 2, 3])
    assert stream.closed


@pytest.mark.asyncio
async def test_export_stream_execute_fallback_keeps_order():
    assert [row async for row in site_data_queries._iter_query_rows(
        FakeSession(FakeRows([1, 2, 3])), "query", {}, batch_size=2,
    )] == [1, 2, 3]


def test_generate_script_reexports_query_api_without_wrappers() -> None:
    """Keep historical script imports and monkeypatch targets stable."""
    for name in QUERY_EXPORTS:
        assert getattr(generate_site_data, name) is getattr(site_data_queries, name)


@pytest.mark.asyncio
async def test_report_export_exposes_update_marker_and_peer_review_provenance() -> None:
    created = datetime(2026, 5, 31, tzinfo=timezone.utc)
    updated = datetime(2026, 6, 1, tzinfo=timezone.utc)
    document = {
        "schema_version": "report_v4.1",
        "default_locale": "zh",
        "locales": ["zh", "en", "fr"],
        "title": {"zh": "报告"},
        "summary": {"zh": "摘要"},
        "key_findings": {"zh": ["发现"]},
        "metrics": {"total_cases": 12},
        "peer_review": {"status": "published", "review_mode": "cross_model"},
    }
    row = mapped_row(
        id=5,
        title="Report",
        report_type="weekly",
        status="approved",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 7),
        created_at=created,
        updated_at=updated,
        summary="summary",
        quality_score=0.9,
        metadata={"report_layout": "report_v4", "report_document_v4": document},
        generation_config={},
        country_code="JP",
        country_name="Japan",
        country_name_en="Japan",
        country_name_local="日本",
    )
    session = FakeSession(FakeRows([row]))

    reports = await site_data_queries.fetch_reports(session)
    assert "AND c.code = 'JP'" in session.calls[0][0]

    assert reports[0]["updated_at"] == updated.isoformat()
    assert reports[0]["metadata"]["peer_review"]["review_mode"] == "cross_model"
    assert "r.updated_at" in session.calls[0][0]


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
async def test_fetch_countries_includes_sweden_after_public_release_approval() -> None:
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

    assert [country["code"] for country in countries] == ["FI", "SE"]


@pytest.mark.asyncio
async def test_ecdc_source_does_not_reopen_ireland_country_release_gate() -> None:
    session = FakeSession(FakeRows([
        mapped_row(code="IE", name="Ireland", name_en="Ireland"),
        mapped_row(code="AT", name="Austria", name_en="Austria"),
        mapped_row(code="US", name="United States", name_en="United States"),
    ]))
    countries = await site_data_queries.fetch_countries(session)
    assert [country["code"] for country in countries] == ["AT", "US"]


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
    assert params == {
        "code": "US",
        "geography_key": "country:US:national",
    }


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
async def test_direct_disease_rows_preserve_missing_counts() -> None:
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
            "cases": None,
            "deaths": None,
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
    assert "dr.cases::bigint AS cases" in sql
    assert "COALESCE(dr.cases, 0)" not in sql
    assert "metadata::jsonb ->> 'time_basis'" in sql
    assert "national_registry_notifications" in sql
    assert params == {"code": "CN"}


@pytest.mark.asyncio
async def test_swiss_legacy_export_excludes_chfl_combined_geography() -> None:
    session = FakeSession(FakeRows([]))

    await site_data_queries.fetch_disease_records_direct(session, "CH", False)

    sql, params = session.calls[0]
    assert "dr.raw_data::jsonb->0->>'Geography'" in sql
    assert ") = 'CH'" in sql
    assert params == {"code": "CH"}


@pytest.mark.asyncio
async def test_japanese_legacy_sentinel_history_keeps_its_reporting_basis() -> None:
    session = FakeSession(FakeRows([]))

    await site_data_queries.fetch_disease_records_direct(session, "JP", False)

    sql, _params = session.calls[0]
    assert "d.name IN ('D136', 'D138', 'D139', 'D141', 'D143')" in sql
    assert "THEN 'sentinel_case_notifications'" in sql
    assert "THEN 'sentinel_surveillance'" in sql
    assert "THEN 'weekly'" in sql


@pytest.mark.asyncio
async def test_japanese_reviewed_sentinel_series_uses_legacy_semantics() -> None:
    session = FakeSession(FakeRows([]))

    await site_data_queries.fetch_disease_series_records(session, "JP", False)

    sql, _params = session.calls[0]
    assert "dss.country_code = 'JP' AND mapping.target_code IN ('D136', 'D138', 'D139', 'D141', 'D143')" in sql
    assert "THEN 'sentinel_case_notifications'" in sql
    assert "THEN 'sentinel_surveillance'" in sql
    assert "THEN 'weekly'" in sql


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
    assert "metadata::jsonb ->> 'time_basis'" in sql
    assert "dso.dimensions" in sql
    assert "AND dso.dimension_key = 'all'" not in sql
    assert "dss.is_active IS TRUE" not in sql
    assert params == {
        "code": "IS",
        "geography_key": "country:IS:national",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["JP-01", "SE-AB", "DE-BE", "CH-ZH", "US-CA", "NZ-AKL"])
async def test_new_subnational_sources_use_active_reviewed_mapping(code):
    session = FakeSession(FakeRows([]))

    await site_data_queries.fetch_disease_series_records(session, code, False)

    sql, _params = session.calls[0]
    assert "mapping.target_code AS disease_id" in sql
    assert "JOIN LATERAL" in sql
    assert "source_category.source_code = COALESCE(" in sql
    assert "source_category.definition_version = 'source-current'" in sql
    assert "source_category.canonical_source_label" in sql
    assert "dso.dimensions->>'source_disease_code'" in sql
    assert "mapping.projection_policy = 'canonical'" in sql
    assert "mapping_release.status = 'active'" in sql
    assert "mapping.mapping_relation AS mapping_relation" in sql
    assert "dss.disease_id IS NOT NULL" not in sql


@pytest.mark.asyncio
async def test_series_query_can_reconstruct_latest_recorded_state_as_of() -> None:
    cutoff = datetime(2025, 6, 1, tzinfo=timezone.utc)
    session = FakeSession(
        FakeRows(
            [
                mapped_row(
                    date=date(2025, 1, 1),
                    year_month="2025-01",
                    disease_id="D001",
                    cases=8,
                    deaths=None,
                    recoveries=None,
                    incidence_rate=None,
                    incidence_rate_source="missing_population",
                    mortality_rate=None,
                    recorded_at=cutoff,
                    recorded_history_complete=False,
                )
            ]
        )
    )
    records = await site_data_queries.fetch_disease_series_records(
        session, "ZZ", False, recorded_at_as_of=cutoff
    )
    assert records[0]["recorded_at"] == cutoff.isoformat()
    assert records[0]["recorded_history_complete"] is False
    sql, params = session.calls[0]
    assert "FROM disease_series_observation_revisions" in sql
    assert "recorded_at <= :recorded_at_as_of" in sql
    assert "DISTINCT ON" in sql
    assert params["recorded_at_as_of"] == cutoff


@pytest.mark.asyncio
async def test_reviewed_mapping_also_gates_historical_revisions() -> None:
    cutoff = datetime(2025, 6, 1, tzinfo=timezone.utc)
    session = FakeSession(FakeRows([]))

    await site_data_queries.fetch_disease_series_records(
        session, "US-CA", False, recorded_at_as_of=cutoff
    )

    sql, _params = session.calls[0]
    assert "FROM disease_series_observation_revisions" in sql
    assert "mapping.target_code AS disease_id" in sql
    assert "mapping_release.status = 'active'" in sql


@pytest.mark.asyncio
async def test_as_of_query_requires_timezone_aware_recorded_time() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        await site_data_queries.fetch_disease_series_records(
            FakeSession(FakeRows([])),
            "ZZ",
            False,
            recorded_at_as_of=datetime(2025, 6, 1),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("code,parent", [
    ("CN-GD", "CN"), ("AU-NSW", "AU"), ("BR-SP", "BR"),
    ("JP-01", "JP"), ("SE-AB", "SE"), ("DE-BE", "DE"),
    ("CH-ZH", "CH"), ("US-CA", "US"), ("NZ-AKL", "NZ"),
])
async def test_subdivision_registry_uses_parent_but_population_uses_local_id(code, parent):
    session = FakeSession(FakeRows([]))
    await site_data_queries.fetch_disease_series_records(session, code, True)
    sql, params = session.calls[0]
    assert params == {"code": parent, "geography_key": f"country:{code}:national", "population_code": code}
    assert "pr.country_id = registry_country.id" in sql
    assert "pr.year = EXTRACT(YEAR FROM dso.time)::int" in sql
    assert "lower(pr.source) || '_computed'" in sql
    assert "CASE WHEN dso.dimension_key = 'all'" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("code,parent", [
    ("CN-GD", "CN"), ("AU-NSW", "AU"), ("BR-SP", "BR"),
    ("JP-01", "JP"), ("SE-AB", "SE"), ("DE-BE", "DE"),
    ("CH-ZH", "CH"), ("US-CA", "US"), ("NZ-AKL", "NZ"),
])
async def test_subdivision_frequency_uses_parent_registry(code, parent):
    session = FakeSession(
        FakeRows([(True,)]), FakeRows([(True,)]),
        FakeRows([]), FakeRows([]),
    )
    await site_data_queries.fetch_country_frequency_meta(session, code)
    series_calls = [params for sql, params in session.calls if "disease_surveillance_series dss" in sql]
    assert series_calls == [{"code": parent, "geography_key": f"country:{code}:national"}]
