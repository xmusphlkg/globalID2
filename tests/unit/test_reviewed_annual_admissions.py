"""Publication scope and visible provenance for reviewed annual admissions."""

import json

import pytest

from src.core import country_library
from src.core.high_frequency_sources import sources_for_country
from src.core.reviewed_annual_admissions import (
    REVIEWED_ANNUAL_COUNTRIES, WHO_TERMS_URL, reviewed_annual_admission_config,
)
from src.generation.site_data_about import build_country_source_info


@pytest.mark.parametrize("code", sorted(REVIEWED_ANNUAL_COUNTRIES))
def test_runtime_and_json_admission_preserve_reviewed_annual_sources(code):
    registry = json.loads(country_library.COUNTRY_BOOTSTRAP_FILE.read_text())
    config = country_library.get_country_bootstrap_config(code)
    expected = reviewed_annual_admission_config(code)
    assert len(REVIEWED_ANNUAL_COUNTRIES) == 20
    for key in ("public_release_enabled", "public_legacy_enabled", "public_source_systems", "public_source_scopes"):
        assert config[key] == registry[code][key]
        if key in ("public_source_systems", "public_source_scopes"):
            supplements = sources_for_country(code)
            added = {source.source_id if key == "public_source_systems" else source.scope
                     for source in supplements}
            assert set(config[key]) == set(expected[key]) | added
        else:
            assert config[key] == expected[key]
    assert config["public_legacy_enabled"] is False
    policies = config["crawler_config"]["source_policies"]
    for scope in expected["public_source_scopes"]:
        assert policies[scope]["public_release_enabled"] is True
        assert policies[scope]["license_url"]
        assert policies[scope]["required_attribution"]


@pytest.mark.parametrize("code,scope", [
    ("CR", "cr_moh_notifications"), ("LA", "who_wpro_dengue"),
    ("VN", "who_wpro_dengue"), ("MY", "my_idengue_daily"),
])
def test_independent_unreviewed_feed_stays_closed(code, scope):
    config = country_library.get_country_bootstrap_config(code)
    assert scope in config["crawler_config"]["sources"]
    assert scope not in config["public_source_scopes"]
    assert config["crawler_config"]["source_policies"][scope]["public_release_enabled"] is False


@pytest.mark.parametrize("code", sorted(REVIEWED_ANNUAL_COUNTRIES))
def test_reviewed_provenance_survives_stale_database_crawler(code):
    result = build_country_source_info(code, database_config={
        "data_source_url": "https://stale.test/private-source", "notes": "unreviewed ingestion",
        "crawler_config": {"sources": ["private-feed"], "cadence": "unknown", "source_policies": {}},
    })
    expected = reviewed_annual_admission_config(code)
    sources = {source["scope"]: source for source in result["sources"]}
    assert set(expected["public_source_scopes"]) <= sources.keys()
    assert "unreviewed ingestion" not in result["notes"]
    for scope in expected["public_source_scopes"]:
        source = sources[scope]
        assert source["source_id"] in expected["public_source_systems"]
        assert source["url"] != "https://stale.test/private-source"
        assert source["publisher"] in source["required_attribution"]
        assert source["license"] and source["license_url"] and source["review_status"]
        assert source["cadence"] == "annual"
        assert "2026-11-16" in source["description"]
        assert source["description_zh"]


def test_who_terms_and_original_data_limits_are_visible():
    source = next(s for s in build_country_source_info("KE")["sources"] if s["scope"] == "who_gho_annual")
    assert source["license_url"] == WHO_TERMS_URL
    assert "CC BY" not in source["license"]
    assert source["original_dataset_modification_authorized"] is False
    assert "Original indicator titles" in source["description"]
    assert "does not imply WHO endorsement" in source["description"]
    assert "complete national surveillance coverage" in source["description"]


def test_spf_tb_geography_break_and_licence_are_visible():
    source = next(s for s in build_country_source_info("GF")["sources"] if s["scope"] == "spf_odisse_annual")
    assert source["license"] == "Licence ouverte 2.0"
    assert "through 2018" in source["description"]
    assert "from 2019" in source["description"]
    assert "older data retained in source details" in source["description"]
    assert "报告地" in source["description_zh"] and "居住地" in source["description_zh"]


def test_chile_current_csv_discrepancy_and_aggregate_only_release_are_visible():
    source = next(s for s in build_country_source_info("CL")["sources"] if s["scope"] == "cl_eno_historical")
    assert source["attribution_required"] is False
    assert source["license"].startswith("CC0")
    assert "individual case rows are not released" in source["description"]
    assert "2007–2024" in source["description"] and "2007–2022" in source["description"]
    assert "reviewed confirmed national annual" in source["description"]


def test_reviewed_admission_fallback_works_without_bootstrap_registry(monkeypatch):
    monkeypatch.setattr(country_library, "_load_country_bootstrap_registry", lambda: {})
    for code in REVIEWED_ANNUAL_COUNTRIES:
        config = country_library.get_country_bootstrap_config(code)
        assert config["public_release_enabled"] is True
        assert config["public_source_systems"]
        assert config["public_source_details"]
    assert reviewed_annual_admission_config("AR") == {}
