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


@pytest.mark.asyncio
@pytest.mark.parametrize("country,expression", [("JP-25", "mapping.target_code"), ("CA", "dss.disease_id")])
async def test_disease_scoped_series_query_filters_effective_reviewed_mapping(country, expression):
    session = FakeSession(FakeRows([]))
    assert await site_data_queries.fetch_disease_series_records(session, country, False, disease_ids={"D038", "D001"}) == []
    query, params = session.calls[0]
    assert f"{expression} IN (__[POSTCOMPILE_disease_ids])" in query
    assert params["disease_ids"] == ["D001", "D038"]
    assert "D038" not in query


@pytest.mark.asyncio
@pytest.mark.parametrize("count,identity", [(15, False), (16, True), (32, True)])
async def test_dense_reviewed_query_routes_at_threshold_without_widening_targets(count, identity):
    requested = {f"D{number:03d}" for number in range(count)}
    session = FakeSession(FakeRows([]))
    assert await site_data_queries.fetch_disease_series_records(session, "JP-25", True, disease_ids=requested) == []
    query, params = session.calls[0]
    assert ("export_identities AS MATERIALIZED" in query) is identity
    assert ("requested_categories AS MATERIALIZED" in query) is not identity
    assert "AND mapping.target_code IN (__[POSTCOMPILE_disease_ids])" in query
    assert params["disease_ids"] == sorted(requested)
    assert params["population_code"] == "JP-25"
    assert "mapping_release.status = 'active'" in query


@pytest.mark.asyncio
async def test_empty_disease_scope_returns_no_facts_without_issuing_sql():
    session = FakeSession()
    assert await site_data_queries.fetch_disease_series_records(session, "JP-25", True, disease_ids=set()) == []
    assert await site_data_queries.fetch_disease_records_direct(session, "JP-25", True, disease_ids=set()) == []
    assert session.calls == []


@pytest.mark.asyncio
async def test_dense_unreviewed_country_retains_its_registered_disease_filter():
    requested = {f"D{number:03d}" for number in range(32)}
    session = FakeSession(FakeRows([]))
    await site_data_queries.fetch_disease_series_records(session, "CA", False, disease_ids=requested)
    query, params = session.calls[0]
    assert "export_identities" not in query and "requested_categories" not in query
    assert "AND dss.disease_id IN (__[POSTCOMPILE_disease_ids])" in query
    assert params["disease_ids"] == sorted(requested)


@pytest.mark.asyncio
async def test_disease_scoped_legacy_query_is_parameterized():
    session = FakeSession(FakeRows([]))
    assert await site_data_queries.fetch_disease_records_direct(session, "US", False, disease_ids={"D038"}) == []
    query, params = session.calls[0]
    assert "d.name IN (__[POSTCOMPILE_disease_ids])" in query
    assert params["disease_ids"] == ["D038"]


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
    assert "source_category.source_code = identity.effective_source_code" in sql
    assert "export_identities AS MATERIALIZED" in sql
    assert "source_category.definition_version = 'source-current'" in sql
    assert "source_category.canonical_source_label" in sql
    assert "dso.dimensions->>'source_disease_code'" in sql
    assert "mapping.projection_policy = 'canonical'" in sql
    assert "mapping_release.status = 'active'" in sql
    assert "mapping.mapping_relation AS mapping_relation" in sql
    assert "dss.disease_id IS NOT NULL" not in sql


@pytest.fixture
async def mapping_query_database(monkeypatch):
    """Opt-in disposable PostgreSQL fixture; never uses application config."""
    import os
    from uuid import uuid4
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ.get("TEST_QUERY_DATABASE_URL")
    if not url:
        pytest.skip("TEST_QUERY_DATABASE_URL is required for PostgreSQL query semantics")
    schema = "query_semantics_" + uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    statements = [
        f"CREATE SCHEMA {schema}",
        """CREATE TABLE disease_surveillance_series (
            series_code text PRIMARY KEY, country_code text, disease_id text,
            source_system text, source_series_code text, source_label text,
            definition_version text, case_definition text, case_definition_uri text,
            metric_type text DEFAULT 'case_notifications', reporting_basis text DEFAULT 'notification',
            temporal_granularity text DEFAULT 'daily', unit text DEFAULT 'count',
            mapping_relation text, comparability text, aggregation_policy text,
            availability_status text DEFAULT 'active', missing_value_policy text,
            valid_from date, valid_to date, metadata jsonb DEFAULT '{}', is_active boolean DEFAULT true)""",
        """CREATE TABLE disease_series_observations (
            time timestamptz, series_code text, geography_key text, dimension_key text,
            dimensions jsonb DEFAULT '{}', metadata jsonb DEFAULT '{}', raw_data jsonb DEFAULT '{}',
            value double precision, unit text DEFAULT 'count', suppressed boolean DEFAULT false,
            suppression_reason text, quality_status text DEFAULT 'validated',
            updated_at timestamptz DEFAULT '2026-01-01')""",
        """CREATE TABLE disease_series_observation_revisions (
            time timestamptz, series_code text, geography_key text, dimension_key text,
            dimensions jsonb DEFAULT '{}', metadata jsonb DEFAULT '{}', raw_data jsonb DEFAULT '{}',
            value double precision, unit text DEFAULT 'count', suppressed boolean DEFAULT false,
            suppression_reason text, quality_status text DEFAULT 'validated', recorded_at timestamptz)""",
        """CREATE TABLE source_disease_categories (
            id bigint PRIMARY KEY, source_id text, source_code text, definition_version text,
            canonical_source_label text, is_active boolean DEFAULT true)""",
        """CREATE TABLE disease_mapping_assertions_v3 (
            id bigint PRIMARY KEY, category_id bigint, assertion_status text DEFAULT 'approved',
            target_kind text DEFAULT 'concept', projection_policy text DEFAULT 'canonical', target_code text,
            mapping_relation text DEFAULT 'exact', comparability text DEFAULT 'comparable',
            aggregation_policy text DEFAULT 'additive', valid_from date, valid_to date)""",
        "CREATE TABLE disease_mapping_releases_v3 (id bigint PRIMARY KEY, status text)",
        "CREATE TABLE disease_mapping_release_items_v3 (release_id bigint, assertion_id bigint)",
        "INSERT INTO disease_mapping_releases_v3 VALUES (1, 'active'), (2, 'retired')",
    ]
    monkeypatch.setattr(site_data_queries, "get_country_bootstrap_config", lambda *_: {})
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))
            await connection.execute(text("""INSERT INTO disease_surveillance_series
                (series_code,country_code,source_system,definition_version,source_series_code,source_label,is_active)
                VALUES ('SHARED','JP','TEST','v1','fallback','Fallback Label',true),
                       ('NULL_LABEL','JP','TEST','v1',NULL,NULL,true),
                       ('LABEL_ONLY','JP','TEST','v1',NULL,' Label Only ',false)"""))
            categories = [
                (1, "A", "v1", "Alias Label", True),
                (2, "label:old", "source-current", "Alias Label", True),
                (3, "A", "v1", "Alias Label", True),
                (4, "B", "v1", "Other Label", True),
                (5, "label:fallback", "source-current", "Fallback Label", True),
                (6, "label:new", "source-current", " Alias Label ", True),
                (7, "X", "v1", "Unknown", True),
                (8, "label:only", "source-current", "Label Only", True),
                (9, "V", "v1", "Validity", True),
                (10, "A", "v1", "Alias Label", False),
                (11, "DRAFT", "v1", "Alias Label", True),
                (12, "RETIRED", "v1", "Alias Label", True),
            ]
            for identifier, code, definition, label, active in categories:
                await connection.execute(text("""INSERT INTO source_disease_categories
                    VALUES (:id,'TEST',:code,:definition,:label,:active)"""),
                    {"id": identifier, "code": code, "definition": definition, "label": label, "active": active})
                await connection.execute(text("""INSERT INTO disease_mapping_assertions_v3
                    (id,category_id,target_code,assertion_status,valid_from,valid_to)
                    VALUES (:id,:id,:target,:status,:start,:end)"""),
                    {"id": identifier, "target": f"D{identifier:03d}",
                     "status": "draft" if identifier == 11 else "approved",
                     "start": date(2025, 1, 2) if identifier == 9 else None,
                     "end": date(2025, 1, 3) if identifier == 9 else None})
                await connection.execute(text("INSERT INTO disease_mapping_release_items_v3 VALUES (:release,:id)"),
                    {"release": 2 if identifier == 12 else 1, "id": identifier})
        yield engine, async_sessionmaker(engine)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()


async def _fixture_observation(db, *, key, series="SHARED", code="A", label="Alias Label", day=1,
                               metadata=None, suppressed=False, quality="validated", unit="count",
                               value=11, updated_at="2026-01-01", geography="country:JP-25:national",
                               extra_dimensions=None):
    import json
    from sqlalchemy import text
    dimensions = {}
    if code is not None:
        dimensions["source_disease_code"] = code
    if label is not None:
        dimensions["source_disease_label"] = label
    dimensions.update(extra_dimensions or {})
    await db.execute(text("""INSERT INTO disease_series_observations
        (time,series_code,geography_key,dimension_key,dimensions,metadata,value,suppressed,quality_status,unit,updated_at)
        VALUES (:time,:series,:geography,:key,CAST(:dimensions AS jsonb),CAST(:metadata AS jsonb),
                :value,:suppressed,:quality,:unit,:updated)"""),
        {"time": datetime(2025, 1, day, tzinfo=timezone.utc), "series": series,
         "geography": geography, "key": key, "dimensions": json.dumps(dimensions),
         "metadata": json.dumps(metadata or {}), "value": value, "suppressed": suppressed,
         "quality": quality, "unit": unit, "updated": datetime.fromisoformat(updated_at).replace(tzinfo=timezone.utc)})


@pytest.mark.parametrize("target_count", [None, 16, 32])
async def test_identity_resolution_matches_original_per_observation_precedence_and_cardinality(mapping_query_database, target_count):
    _, sessions = mapping_query_database
    async with sessions() as db:
        await _fixture_observation(db, key="exact-code-wins")
        await _fixture_observation(db, key="second-code", code="B", day=2)
        await _fixture_observation(db, key="newest-label", code="C", day=3)
        await _fixture_observation(db, key="metadata-code", code="", label="", day=4,
                                   metadata={"local_code": "A", "local_label": " Alias Label "})
        await _fixture_observation(db, key="series-fallback", code=None, label=None, day=5)
        await _fixture_observation(db, key="null-label", series="NULL_LABEL", code="X", label=None)
        await _fixture_observation(db, key="null-both-excluded", series="NULL_LABEL", code=None, label=None, day=2)
        await _fixture_observation(db, key="null-code", series="LABEL_ONLY", code=None, label=None)
        for day in (1, 2, 3, 4):
            await _fixture_observation(db, key=f"validity-{day}", code="V", day=day)
        for key, overrides in [
            ("suppressed", {"suppressed": True}), ("rejected", {"quality": "rejected"}),
            ("wrong-unit", {"unit": "ratio"}), ("missing-value", {"value": None}),
            ("other-geography", {"geography": "country:JP-26:national"}),
            ("draft-mapping", {"code": "DRAFT"}), ("retired-release", {"code": "RETIRED"}),
        ]:
            await _fixture_observation(db, key=key, **overrides)
        requested = {f"D{number:03d}" for number in range(1, target_count + 1)} if target_count else None
        optimized = await site_data_queries.fetch_disease_series_records(db, "JP-25", False, disease_ids=requested)
        original = await site_data_queries.fetch_disease_series_records(db, "JP-25", False,
            disease_ids={f"D{number:03d}" for number in range(1, 13)})
        # The public ORDER BY does not order equal-time dimension siblings.
        # Compare every field and duplicate row after resolving only those ties.
        order = lambda row: (row["series_code"], row["date"], row["dimension_key"], row["disease_id"])
        assert sorted(optimized, key=order) == sorted(original, key=order)
        actual = {row["dimension_key"]: row["disease_id"] for row in optimized}
        assert actual == {
            "exact-code-wins": "D003", "second-code": "D004", "newest-label": "D006",
            "metadata-code": "D003", "series-fallback": "D005", "null-label": "D007",
            "null-code": "D008", "validity-2": "D009", "validity-3": "D009",
        }
        assert next(row for row in optimized if row["dimension_key"] == "null-code")["series_is_active"] is False


@pytest.mark.parametrize("target_count", [None, 16, 32])
async def test_identity_resolution_preserves_as_of_revision_and_current_fallback(mapping_query_database, target_count):
    from sqlalchemy import text
    _, sessions = mapping_query_database
    async with sessions() as db:
        await _fixture_observation(db, key="revised", updated_at="2026-02-01")
        await _fixture_observation(db, key="fallback", code="B", day=2)
        await _fixture_observation(db, key="not-recorded-at-cutoff", day=3, updated_at="2026-02-01")
        for recorded, value in [("2026-01-01", 3), ("2026-02-01", 8)]:
            await db.execute(text("""INSERT INTO disease_series_observation_revisions
                SELECT time,series_code,geography_key,dimension_key,dimensions,metadata,raw_data,
                       :value,unit,suppressed,suppression_reason,quality_status,:recorded
                FROM disease_series_observations WHERE dimension_key='revised'"""),
                {"value": value, "recorded": datetime.fromisoformat(recorded).replace(tzinfo=timezone.utc)})
        cutoff = datetime(2026, 1, 15, tzinfo=timezone.utc)
        requested = {f"D{number:03d}" for number in range(1, target_count + 1)} if target_count else None
        optimized = await site_data_queries.fetch_disease_series_records(db, "JP-25", False,
            recorded_at_as_of=cutoff, disease_ids=requested)
        original = await site_data_queries.fetch_disease_series_records(db, "JP-25", False,
            recorded_at_as_of=cutoff, disease_ids={f"D{number:03d}" for number in range(1, 13)})
        assert optimized == original
        assert {row["dimension_key"]: row["cases"] for row in optimized} == {"revised": 3, "fallback": 11}
        assert all(row["recorded_history_complete"] is False for row in optimized)


@pytest.mark.parametrize("disjoint_sibling", [False, True])
@pytest.mark.parametrize("target_count", [None, 16, 32])
async def test_query_reordering_preserves_complete_country_and_lossless_source_arrays(mapping_query_database, disjoint_sibling, target_count):
    """Do not canonicalize/sort outputs: source arrays and dominant ties matter."""
    from sqlalchemy import text
    from src.generation.site_series_projection import apply_disease_cutover_projection
    from src.generation.site_data_views import build_country_data

    _, sessions = mapping_query_database
    async with sessions() as db:
        await db.execute(text("UPDATE disease_mapping_assertions_v3 SET aggregation_policy='sum_disjoint' WHERE id=3"))
        await db.execute(text("""INSERT INTO disease_surveillance_series
            (series_code,country_code,source_system,definition_version,source_series_code,source_label,metric_type,unit)
            VALUES ('MAIN_A','JP','TEST','v1','A','Primary cases','case_notifications','count'),
                   ('MAIN_B','JP','TEST','v1','A','Disjoint cases','clinical_diagnoses','count'),
                   ('HIDDEN_CASES','JP','TEST','v1','U','Broader definition','case_notifications','count'),
                   ('NONCASE','JP','TEST','v1','N','Positivity measure','positivity_rate','ratio'),
                   ('RATE_ONLY','JP','TEST','v1','O','Source-only rate','positivity_rate','ratio')"""))
        for identifier, code, target, relation in [
            (13, "N", "D003", "exact"), (14, "U", "D003", "broader"), (15, "O", "D015", "exact"),
        ]:
            await db.execute(text("""INSERT INTO source_disease_categories
                VALUES (:id,'TEST',:code,'v1',:code,true)"""), {"id": identifier, "code": code})
            await db.execute(text("""INSERT INTO disease_mapping_assertions_v3
                (id,category_id,target_code,mapping_relation) VALUES (:id,:id,:target,:relation)"""),
                {"id": identifier, "target": target, "relation": relation})
            await db.execute(text("INSERT INTO disease_mapping_release_items_v3 VALUES (1,:id)"), {"id": identifier})
        for day, cases in [(1, 2), (2, 4)]:
            await _fixture_observation(db, key="all", series="MAIN_A", day=day, value=cases)
            await _fixture_observation(db, key="age:child", series="MAIN_A", day=day, value=100 + cases,
                                       extra_dimensions={"age": "child"}, quality="provisional")
            await _fixture_observation(db, key="age:adult", series="MAIN_A", day=day, value=200 + cases,
                                       extra_dimensions={"age": "adult"})
            await _fixture_observation(db, key="all", series="HIDDEN_CASES", code="U", day=day, value=900 + cases)
            await _fixture_observation(db, key="all", series="NONCASE", code="N", day=day, unit="ratio", value=0.25)
            await _fixture_observation(db, key="all", series="RATE_ONLY", code="O", day=day, unit="ratio", value=0.5)
            if disjoint_sibling:
                # A quality/metric tie between selected definitions must retain
                # the same first/dominant choice as the original SQL order.
                await _fixture_observation(db, key="all", series="MAIN_B", day=day,
                                           value=cases + 1, quality="provisional")
        requested = {f"D{number:03d}" for number in range(1, target_count + 1)} if target_count else None
        optimized = await site_data_queries.fetch_disease_series_records(db, "JP-25", False, disease_ids=requested)
        reference = await site_data_queries.fetch_disease_series_records(db, "JP-25", False,
            disease_ids={f"D{number:03d}" for number in range(1, 16)})
        catalogue = {code: {"name_en": code, "name_zh": code, "category": "Test", "slug": code.lower()}
                     for code in ("D003", "D015")}

        def country(source_rows):
            projected = apply_disease_cutover_projection([], source_rows, country_code="JP-25")
            return build_country_data("JP-25", "Fixture country", projected, catalogue, source_records=source_rows)

        actual = country(optimized)
        expected = country(reference)
        assert actual == expected
        cases = actual["disease_series"]["D003"]
        assert cases["dates"] == ["2025-01-01", "2025-01-02"]
        assert cases["cases"] == ([5, 9] if disjoint_sibling else [2, 4])
        assert cases["quality_statuses"] == ["validated", "validated"]
        assert cases["selected_series_codes"] == (["MAIN_A", "MAIN_B"] if disjoint_sibling else ["MAIN_A"])
        sources = {(item["series_code"], item["dimension_key"]): item for item in cases["source_series"]}
        assert sources[("MAIN_A", "age:child")]["values"] == [102, 104]
        assert sources[("MAIN_A", "age:adult")]["values"] == [202, 204]
        assert sources[("HIDDEN_CASES", "all")]["values"] == [902, 904]
        assert sources[("NONCASE", "all")]["values"] == [0.25, 0.25]
        rate = actual["disease_series"]["D015"]
        assert rate["dates"] == []
        assert rate["data_layer"] == "source_observations_only"
        assert rate["source_series"][0]["values"] == [0.5, 0.5]


@pytest.mark.parametrize("target_count", [0, 16, 32])
async def test_dense_final_target_filter_excludes_a_resolved_unrequested_category(mapping_query_database, target_count):
    _, sessions = mapping_query_database
    async with sessions() as db:
        await _fixture_observation(db, key="selected-exact")
        await _fixture_observation(db, key="unrequested-label", code="C", day=2)
        await _fixture_observation(db, key="selected-null-label", series="NULL_LABEL", code="X", label=None)
        selected = {"D003", "D007"} if target_count else set()
        requested = selected | {f"MISSING{number:03d}" for number in range(max(0, target_count - len(selected)))}
        optimized = await site_data_queries.fetch_disease_series_records(db, "JP-25", False, disease_ids=requested)
        reference = await site_data_queries.fetch_disease_series_records(db, "JP-25", False, disease_ids=selected)
        assert optimized == reference
        assert {row["disease_id"] for row in optimized} == selected
        assert all(row["dimension_key"] != "unrequested-label" for row in optimized)


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
