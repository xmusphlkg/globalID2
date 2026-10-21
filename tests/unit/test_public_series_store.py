"""Row shape for the public disease-series read model."""

from datetime import datetime, timezone

import pytest

from src.generation.public_series_store import (
    PROJECTION_VERSION,
    _EMPTY_COUNTRY_HASH,
    build_public_series_row,
    country_hash_from_disease_series,
    country_publish_hash,
    series_content_hash,
)
from src.generation.site_data_views import build_country_data_from_published_series


def _series() -> dict:
    return {
        "dates": ["2024-01-01", "2024-01-08"],
        "cases": [1.0, None],
        "deaths": [None, 2],
        "incidence_rates": [0.5, None],
        "period_granularity": "weekly",
        "data_layer": "series_registry",
        "selected_series_codes": ["flu:weekly", "flu:sentinel"],
        "point_series_codes": ["flu:weekly", "flu:weekly"],
    }


def test_build_public_series_row_stores_parallel_arrays() -> None:
    high_water = datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc)
    row = build_public_series_row("JP-13", "D038", _series(), source_high_water=high_water)

    assert row is not None
    assert row["period_starts"][0].isoformat() == "2024-01-01"
    assert row["cases"] == [1, None]
    assert row["deaths"] == [None, 2]
    assert row["incidence"] == [0.5, None]
    assert row["source_series_code"] == "flu:weekly"
    assert row["point_count"] == 2
    assert row["total_cases"] == 1
    assert row["total_deaths"] == 2
    assert row["projection_version"] == PROJECTION_VERSION
    assert row["source_high_water"] == high_water
    assert len(row["content_hash"]) == 64


def test_series_hash_ignores_display_fields_and_stays_stable() -> None:
    first = build_public_series_row("CN", "D001", _series(), source_high_water=None)
    second = build_public_series_row("CN", "D001", _series(), source_high_water=None)
    assert first is not None and second is not None
    assert first["content_hash"] == second["content_hash"]
    assert series_content_hash({"disease_id": "D001"}) == series_content_hash(
        {"disease_id": "D001"}
    )


def test_country_hash_changes_when_one_series_changes() -> None:
    original = build_public_series_row("CN", "D001", _series(), source_high_water=None)
    changed_series = _series()
    changed_series["cases"] = [9, None]
    changed = build_public_series_row("CN", "D001", changed_series, source_high_water=None)
    assert original is not None and changed is not None
    assert country_publish_hash([original["content_hash"]]) != country_publish_hash(
        [changed["content_hash"]]
    )


def test_empty_curve_is_omitted() -> None:
    assert build_public_series_row("CN", "D001", {"dates": []}, source_high_water=None) is None


def test_published_country_curve_hashes_back_to_the_same_country() -> None:
    row = build_public_series_row("SG", "D001", _series(), source_high_water=None)
    assert row is not None
    country = build_country_data_from_published_series(
        "SG",
        "Singapore",
        [row],
        {
            "D001": {
                "disease_id": "D001",
                "name_en": "Plague",
                "name_zh": "鼠疫",
                "category": "Bacterial",
                "slug": "plague",
            }
        },
    )

    series = country["disease_series"]["D001"]
    assert series["dates"] == ["2024-01-01", "2024-01-08"]
    assert series["cases"] == [1, None]
    assert series["deaths"] == [None, 2]
    assert series["incidence_rates"] == [0.5, None]
    assert series["weekly_equiv_cases"] == [1.0, None]
    assert series["period_granularity"] == "weekly"
    assert series["data_layer"] == "series_registry"
    assert series["selected_series_codes"] == ["flu:weekly"]
    assert country["disease_count"] == 1
    assert country["total_cases"] == 1
    assert country["frequency_meta"]["source_frequency"] == "WEEKLY"
    assert country["heatmap"]["months"] == ["2024-01"]
    assert country_hash_from_disease_series("SG", country["disease_series"]) == (
        country_publish_hash([row["content_hash"]])
    )


def test_empty_published_country_uses_the_empty_curve_hash() -> None:
    country = build_country_data_from_published_series("CN", "China", [], {})
    assert country["disease_series"] == {}
    assert (
        country_hash_from_disease_series("CN", country["disease_series"])
        == _EMPTY_COUNTRY_HASH
    )


def test_mismatched_arrays_are_rejected() -> None:
    series = _series()
    series["deaths"] = [1]
    with pytest.raises(ValueError, match="differ in length"):
        build_public_series_row("CN", "D001", series, source_high_water=None)
