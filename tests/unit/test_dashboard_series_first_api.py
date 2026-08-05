from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from dashboard.api.deps import get_db
from dashboard.api.routers import diseases
from dashboard.api.services.disease_series_projection import (
    LEGACY_DATA_LAYER,
    LEGACY_GAP_FILL_DATA_LAYER,
    SERIES_DATA_LAYER,
    SeriesFirstResult,
    load_series_first_records,
    project_series_first_records,
)
from src.core.disease_cutover import DiseaseReadPolicy


def _time(month: int, day: int = 1) -> datetime:
    return datetime(2024, month, day, tzinfo=timezone.utc)


def _legacy(month: int, cases: int, *, deaths: int = 0) -> dict:
    return {
        "time": _time(month),
        "disease_id": 7,
        "country_id": 11,
        "cases": cases,
        "deaths": deaths,
        "recoveries": 0,
        "data_source": "legacy",
        "data_quality": "validated",
    }


def _series(
    code: str,
    month: int,
    value: int,
    *,
    aggregation_policy: str = "non_additive",
    metric_type: str = "case_notifications",
) -> dict:
    return {
        "time": _time(month),
        "series_code": code,
        "value": value,
        "observation_unit": "count",
        "series_unit": "count",
        "quality_status": "validated",
        "source_system": "TEST",
        "source_series_code": code.lower(),
        "source_label": code,
        "definition_version": "1",
        "case_definition": f"Definition {code}",
        "case_definition_uri": None,
        "metric_type": metric_type,
        "reporting_basis": "notification",
        "temporal_granularity": "monthly",
        "mapping_relation": "exact",
        "comparability": "direct",
        "aggregation_policy": aggregation_policy,
        "availability_status": "active",
        "missing_value_policy": "missing_is_unknown",
    }


def test_partial_registry_history_overlays_matching_period_only() -> None:
    records, metadata = project_series_first_records(
        [_legacy(1, 90, deaths=2), _legacy(2, 91, deaths=3)],
        [_series("SER_A", 2, 5)],
        disease_numeric_id=7,
        country_id=11,
    )

    assert [(row["cases"], row["data_layer"]) for row in records] == [
        (90, LEGACY_GAP_FILL_DATA_LAYER),
        (5, SERIES_DATA_LAYER),
    ]
    assert records[1]["deaths"] == 3
    assert records[0]["gap_fill_reason"] == "registry_period_missing"
    assert metadata["coverage"] == {
        "status": "legacy_gap_fill",
        "legacy_period_count": 2,
        "registry_period_count": 1,
        "overlap_period_count": 1,
        "legacy_gap_fill_count": 1,
        "registry_only_period_count": 0,
        "coverage_ratio_against_legacy": 0.5,
    }


def test_non_additive_series_are_never_summed_and_remain_in_provenance() -> None:
    records, metadata = project_series_first_records(
        [_legacy(1, 999, deaths=8)],
        [
            _series("SER_A", 1, 5),
            _series("SER_A", 2, 6),
            _series("SER_B", 1, 100),
        ],
        disease_numeric_id=7,
        country_id=11,
    )

    assert [row["cases"] for row in records] == [5, 6]
    assert [row["deaths"] for row in records] == [None, None]
    assert metadata["projection_policy"] == "representative_series"
    assert metadata["selected_series_codes"] == ["SER_A"]
    assert metadata["loss_risk"] == "non_additive_series_not_rolled_up"
    assert {item["series_code"] for item in metadata["source_series"]} == {
        "SER_A",
        "SER_B",
    }


def test_sum_disjoint_requires_every_component_in_each_period() -> None:
    records, metadata = project_series_first_records(
        [_legacy(1, 90), _legacy(2, 91)],
        [
            _series("SER_CHILD", 1, 5, aggregation_policy="sum_disjoint"),
            _series("SER_ADULT", 1, 7, aggregation_policy="sum_disjoint"),
            _series("SER_CHILD", 2, 6, aggregation_policy="sum_disjoint"),
        ],
        disease_numeric_id=7,
        country_id=11,
    )

    assert [(row["cases"], row["data_layer"]) for row in records] == [
        (12, SERIES_DATA_LAYER),
        (91, LEGACY_GAP_FILL_DATA_LAYER),
    ]
    assert metadata["projection_policy"] == "sum_disjoint"


def test_sum_disjoint_counts_registered_component_with_no_observations() -> None:
    missing_component = {
        **_series("SER_ADULT", 1, 0, aggregation_policy="sum_disjoint"),
        "time": None,
        "value": None,
        "observation_unit": None,
    }
    records, metadata = project_series_first_records(
        [_legacy(1, 90)],
        [
            _series("SER_CHILD", 1, 5, aggregation_policy="sum_disjoint"),
            missing_component,
        ],
        disease_numeric_id=7,
        country_id=11,
    )

    assert [(row["cases"], row["data_layer"]) for row in records] == [
        (90, LEGACY_DATA_LAYER)
    ]
    assert metadata["registry_projection_policy"] == "sum_disjoint"
    assert metadata["fallback_reason"] == "incomplete_registered_rollup_periods"
    details = {item["series_code"]: item for item in metadata["source_series"]}
    assert details["SER_ADULT"]["observation_count"] == 0


def test_non_case_registry_metric_falls_back_without_truncating_legacy() -> None:
    records, metadata = project_series_first_records(
        [_legacy(1, 12), _legacy(2, 13)],
        [_series("SER_DETECTIONS", 2, 500, metric_type="organism_detections")],
        disease_numeric_id=7,
        country_id=11,
    )

    assert [row["cases"] for row in records] == [12, 13]
    assert all(row["data_layer"] == LEGACY_DATA_LAYER for row in records)
    assert metadata["fallback_reason"] == ("registered_facts_not_case_count_compatible")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(diseases.router)

    async def _db_override():
        yield object()

    app.dependency_overrides[get_db] = _db_override
    return app


def test_records_endpoint_preserves_shape_and_adds_semantic_metadata(
    monkeypatch,
) -> None:
    async def fake_load(db, *, disease_code, country_id, limit=None):
        assert limit == 500
        return SeriesFirstResult(
            disease_code=disease_code,
            disease_name="Example",
            disease_numeric_id=7,
            country_id=country_id,
            records=[
                {
                    **_legacy(1, 5),
                    "data_layer": SERIES_DATA_LAYER,
                    "projection_policy": "representative_series",
                    "series_codes": ["SER_A"],
                    "loss_risk": "non_additive_series_not_rolled_up",
                    "coverage": {"status": "parity"},
                    "provenance": {"source_series": [{"series_code": "SER_A"}]},
                }
            ],
            metadata={},
        )

    monkeypatch.setattr(diseases, "load_series_first_records", fake_load)
    response = TestClient(_app()).get("/diseases/D001/records?country_id=11")

    assert response.status_code == 200
    record = response.json()[0]
    assert record["cases"] == 5
    assert record["disease_id"] == 7
    assert record["data_layer"] == SERIES_DATA_LAYER
    assert record["series_codes"] == ["SER_A"]
    assert record["provenance"]["source_series"][0]["series_code"] == "SER_A"


def test_compare_endpoint_aggregates_only_the_safe_projected_curve(monkeypatch) -> None:
    async def fake_load(db, *, disease_code, country_id, limit=None):
        return SeriesFirstResult(
            disease_code=disease_code,
            disease_name="Example",
            disease_numeric_id=7,
            country_id=country_id,
            records=[
                {**_legacy(1, 5), "time": _time(1, 1)},
                {**_legacy(1, 6), "time": _time(1, 8)},
            ],
            metadata={
                "data_layer": SERIES_DATA_LAYER,
                "projection_policy": "representative_series",
                "loss_risk": "non_additive_series_not_rolled_up",
                "selected_series_codes": ["SER_A"],
                "source_series": [
                    {"series_code": "SER_A"},
                    {"series_code": "SER_UNSELECTED", "total_value": 9999},
                ],
                "available_series_count": 2,
                "coverage": {"status": "parity"},
            },
        )

    monkeypatch.setattr(diseases, "load_series_first_records", fake_load)
    response = TestClient(_app()).get("/analysis/compare?country_id=11&diseases=D001")

    assert response.status_code == 200
    result = response.json()["diseases"][0]
    assert result["data"] == [{"time_period": "2024-01-01", "cases": 11, "deaths": 0}]
    assert result["projection_policy"] == "representative_series"
    assert result["provenance"]["available_series_count"] == 2
    assert len(result["provenance"]["source_series"]) == 2


@pytest.mark.asyncio
async def test_series_only_loader_never_executes_legacy_query(monkeypatch) -> None:
    disease = SimpleNamespace(id=7, name="D001", name_en="Example")
    country = SimpleNamespace(id=11, code="XX")
    series_row = SimpleNamespace(
        _mapping={
            **_series("SER_A", 1, 5),
            "value": 5,
        }
    )

    class Result:
        def __init__(self, *, scalar=None, rows=None):
            self._scalar = scalar
            self._rows = rows or []

        def scalar_one_or_none(self):
            return self._scalar

        def all(self):
            return self._rows

    class DB:
        def __init__(self):
            self.statements = []
            self.results = [
                Result(scalar=disease),
                Result(scalar=country),
                Result(rows=[series_row]),
            ]

        async def execute(self, statement):
            self.statements.append(statement)
            return self.results.pop(0)

    class Config:
        release_version = "test-cutover"

        @staticmethod
        def resolve_read_policy(country_code, concept_id):
            return DiseaseReadPolicy(
                country_code=country_code,
                concept_id=concept_id,
                read_mode="series_only",
                shadow_compare=False,
                required_series=("SER_A",),
                allowed_projection_policy="single_series",
                target_override=True,
            )

    monkeypatch.setattr(
        "dashboard.api.services.disease_series_projection.get_disease_cutover_config",
        lambda: Config(),
    )
    db = DB()

    result = await load_series_first_records(
        db,
        disease_code="D001",
        country_id=11,
    )

    assert [record["cases"] for record in result.records] == [5]
    assert len(db.statements) == 3
    assert all("disease_records" not in str(statement) for statement in db.statements)
    assert result.metadata["cutover"]["blocked_reasons"] == []
