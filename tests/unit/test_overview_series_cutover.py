from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.api.deps import get_db
from dashboard.api.routers import overview
from dashboard.api.services.disease_series_projection import SeriesFirstResult


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(overview.router)

    async def _db_override():
        yield object()

    app.dependency_overrides[get_db] = _db_override
    return app


def _result() -> SeriesFirstResult:
    return SeriesFirstResult(
        disease_code="D001",
        disease_name="Example",
        disease_numeric_id=7,
        country_id=11,
        records=[
            {
                "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "cases": 2,
                "deaths": 1,
                "incidence_rate": 1.0,
                "mortality_rate": None,
            },
            {
                "time": datetime(2024, 1, 8, tzinfo=timezone.utc),
                "cases": 3,
                "deaths": 0,
                "incidence_rate": 2.0,
                "mortality_rate": 0.1,
            },
        ],
        metadata={"data_layer": "series_registry"},
    )


def test_disease_trend_uses_series_first_curve(monkeypatch) -> None:
    calls = []

    async def fake_load(db, *, disease_code, country_id, **kwargs):
        calls.append((db, disease_code, country_id, kwargs))
        return _result()

    monkeypatch.setattr(overview, "load_series_first_records", fake_load)
    async def fake_country_id(_country_code, _db):
        return 11
    monkeypatch.setattr(overview, "_resolve_country_id", fake_country_id)

    response = TestClient(_app()).get("/analytics/trends?country_code=XX&disease_code=D001")

    assert response.status_code == 200
    assert [point["cases"] for point in response.json()] == [2, 3]
    assert calls and calls[0][1:3] == ("D001", 11)


def test_monthly_comparison_aggregates_projected_curve(monkeypatch) -> None:
    async def fake_load(*_args, **_kwargs):
        return _result()

    monkeypatch.setattr(overview, "load_series_first_records", fake_load)
    async def fake_country_id(_country_code, _db):
        return 11
    monkeypatch.setattr(overview, "_resolve_country_id", fake_country_id)

    response = TestClient(_app()).get(
        "/analytics/monthly-comparison?country_code=XX&disease_code=D001"
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "year": 2024,
            "month": 1,
            "cases": 5,
            "deaths": 1,
            "incidence_rate": 1.5,
            "mortality_rate": 0.1,
        }
    ]
