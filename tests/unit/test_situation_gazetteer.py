"""Offline tests for resolving an alert's admin area to map coordinates."""

from __future__ import annotations

import pytest

from src.services.situation_notices.export import map_marker
from src.services.situation_notices.gazetteer import locate, normalize


def test_normalize_drops_accents_and_generic_words():
    assert normalize("Équateur Province") == "equateur"
    assert normalize("Guangxi Zhuang Autonomous Region") == "guangxi zhuang"


@pytest.mark.parametrize(
    ("country", "area", "precision"),
    [
        ("CD", "Ituri", "admin1"),
        ("CD", "Ituri Province", "admin1"),
        ("CD", "Haut-Uélé / Dungu Health Zone", "admin1"),
        ("CN", "Henan Province", "admin1"),
        ("US", "Washington State", "admin1"),
        ("ZA", "Johannesburg", "place"),
        ("IN", "Kerala State, Kozhikode district", "place"),
    ],
)
def test_single_area_resolves(country, area, precision):
    location = locate(country, area)
    assert location is not None and location.precision == precision


@pytest.mark.parametrize(
    ("country", "area"),
    [
        ("CD", "Ituri, North Kivu, South Kivu, Tshopo, Haut-Uele, Bas Uele"),
        ("AU", "New South Wales, South Australia, Tasmania, Victoria"),
        ("CN", "Guangdong and Hunan provinces"),
        ("CD", None),
        (None, "Ituri"),
        ("CD", "Nowhereville"),
    ],
)
def test_several_unknown_or_missing_areas_resolve_to_nothing(country, area):
    assert locate(country, area) is None


def test_the_same_name_in_another_country_does_not_match():
    assert locate("KE", "Ituri") is None


def test_map_marker_carries_coordinates_only_when_the_area_resolves():
    base = {"alert_id": "a", "country_code": "CD", "disease_label": "Ebola", "status": "new", "published_date": "2026-10-23"}
    hit = map_marker({**base, "admin_area": "Ituri"})
    assert hit["geo_precision"] == "admin1" and -90 < hit["lat"] < 90 and -180 < hit["lng"] < 180
    miss = map_marker({**base, "admin_area": "Ituri, North Kivu"})
    assert {"lat", "lng", "geo_precision"}.isdisjoint(miss)
