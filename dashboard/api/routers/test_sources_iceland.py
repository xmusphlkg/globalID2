from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dashboard.api.routers.crawl import _validate_iceland_crawl_options
from dashboard.api.routers.sources import (
    _country_source_config,
    _pick_latest_crawl_task,
    _series_source_scope,
    _validate_country_automation_options,
    get_sources_flow,
)


def _country() -> SimpleNamespace:
    return SimpleNamespace(
        id=20191,
        code="IS",
        name="Iceland",
        name_en="Iceland",
        name_local="Ísland",
        language="is-IS",
        timezone="Atlantic/Reykjavik",
    )


def test_iceland_source_config_is_scope_aware() -> None:
    config = _country_source_config(_country(), lang="en")

    assert config.supports_crawl is True
    assert config.supports_fill_missing is False
    assert config.default_fill_missing is False
    assert config.default_source == "all"
    assert [option.value for option in config.source_options] == [
        "all",
        "is_doh_annual",
        "is_doh_sti",
        "is_doh_respiratory",
        "is_doh_history",
        "is_doh_legacy_icd",
    ]
    assert config.source_options[0].label_en.endswith("All Current Dashboards")
    assert {
        option.value: (option.source_kind, option.supports_start_year)
        for option in config.source_options
    } == {
        "all": ("current", True),
        "is_doh_annual": ("current", True),
        "is_doh_sti": ("current", True),
        "is_doh_respiratory": ("current", True),
        "is_doh_history": ("history", False),
        "is_doh_legacy_icd": ("history", False),
    }


def test_iceland_registry_source_ids_map_to_five_distinct_scopes() -> None:
    assert {
        _series_source_scope(source_id, country_code="IS")
        for source_id in (
            "SRC_IS_DOH_ANNUAL",
            "SRC_IS_DOH_STI",
            "SRC_IS_DOH_RESPIRATORY",
            "SRC_IS_DOH_HISTORY",
            "SRC_IS_DOH_LEGACY_ICD",
        )
    } == {
        "is_doh_annual",
        "is_doh_sti",
        "is_doh_respiratory",
        "is_doh_history",
        "is_doh_legacy_icd",
    }


def test_iceland_current_all_task_is_not_history_freshness() -> None:
    live_task = object()
    latest = {"20191:all": live_task}

    assert _pick_latest_crawl_task(latest, scope="20191:is_doh_annual") is live_task
    assert _pick_latest_crawl_task(latest, scope="20191:is_doh_history") is None
    assert _pick_latest_crawl_task(latest, scope="20191:is_doh_legacy_icd") is None


def test_iceland_automation_rejects_synthetic_missing_periods() -> None:
    with pytest.raises(HTTPException, match="fill_missing is not supported"):
        _validate_country_automation_options("IS", fill_missing=True)

    _validate_country_automation_options("IS", fill_missing=False)


def test_iceland_crawl_options_are_source_aware() -> None:
    _validate_iceland_crawl_options(
        country_code="IS",
        source="all",
        fill_missing=False,
        start_year=1997,
    )
    with pytest.raises(HTTPException, match="complete workbook catalogue"):
        _validate_iceland_crawl_options(
            country_code="IS",
            source="is_history",
            fill_missing=False,
            start_year=1997,
        )
    with pytest.raises(HTTPException, match="fill_missing is not supported"):
        _validate_iceland_crawl_options(
            country_code="IS",
            source="is_doh_sti",
            fill_missing=True,
            start_year=None,
        )


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return list(self.rows)

    def scalars(self):
        return self


class _DB:
    def __init__(self, results):
        self.results = [_Result(rows) for rows in results]

    async def execute(self, _statement):
        assert self.results, "unexpected dashboard query"
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_iceland_flow_uses_lossless_series_counts_and_semantics() -> None:
    specs = [
        ("SRC_IS_DOH_ANNUAL", 14, 209, "active", "revised", "case_notifications", "conditional", 2010, 2025),
        ("SRC_IS_DOH_HISTORY", 73, 4369, "historical", "raw", "case_notifications", "conditional", 1997, 2021),
        ("SRC_IS_DOH_LEGACY_ICD", 30, 2865, "historical", "raw", "registered_diagnoses", "not_comparable", 1997, 2020),
        ("SRC_IS_DOH_RESPIRATORY", 5, 1581, "active", "revised", "case_notifications", "conditional", 2019, 2026),
        ("SRC_IS_DOH_STI", 3, 372, "active", "revised", "case_notifications", "conditional", 2016, 2026),
    ]
    series_rows = [
        SimpleNamespace(
            country_id=20191,
            country_code="IS",
            country_name="Iceland",
            source_system=source,
            series_count=series_count,
            observation_count=observation_count,
            earliest_date=datetime(start_year, 1, 1, tzinfo=timezone.utc),
            latest_date=datetime(end_year, 1, 1, tzinfo=timezone.utc),
        )
        for (
            source,
            series_count,
            observation_count,
            _availability,
            _quality,
            _metric,
            _comparability,
            start_year,
            end_year,
        ) in specs
    ]
    definition_rows = [
        SimpleNamespace(
            country_id=20191,
            country_code="IS",
            source_system=source,
            availability_status=availability,
            metric_type=metric,
            mapping_relation="exact",
            comparability=comparability,
            count=series_count,
        )
        for source, series_count, _observations, availability, _quality, metric, comparability, _start, _end in specs
    ]
    quality_rows = [
        SimpleNamespace(
            country_id=20191,
            country_code="IS",
            source_system=source,
            quality_status=quality,
            count=observations,
        )
        for source, _series, observations, _availability, quality, _metric, _comparability, _start, _end in specs
    ]
    availability_rows = [
        SimpleNamespace(
            country_id=20191,
            country_code="IS",
            source_system=source,
            status="available",
            count=series_count,
        )
        for source, series_count, _observations, _availability, _quality, _metric, _comparability, _start, _end in specs
    ]
    db = _DB(
        [
            [],
            series_rows,
            definition_rows,
            quality_rows,
            availability_rows,
            [],
            [_country()],
        ]
    )

    flows = await get_sources_flow(country_id=20191, db=db)

    assert len(flows) == 5
    assert sum(row.source_series_count for row in flows) == 125
    assert sum(row.source_observation_count for row in flows) == 9396
    assert sum(row.source_availability.get("available", 0) for row in flows) == 125
    assert sum(row.mapping_relations.get("exact", 0) for row in flows) == 125
    legacy = next(row for row in flows if row.source_scope == "is_doh_legacy_icd")
    assert legacy.metric_types == {"registered_diagnoses": 30}
    assert legacy.comparability == {"not_comparable": 30}
    assert legacy.record_count == 0
    assert legacy.latest_task_uuid is None
    assert not db.results
