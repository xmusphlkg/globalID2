from __future__ import annotations

from types import SimpleNamespace

from dashboard.api.routers.sources import _country_source_config
from src.core.country_library import (
    get_country_bootstrap_config,
    get_country_display_name,
    get_country_profile,
    get_standard_country_codes,
    validate_standard_country_registry,
)
from src.core.source_scopes import (
    canonicalize_task_source,
    default_source_for_country,
    get_expected_scopes_for_country,
    source_scope_label,
)
from src.generation.site_data_about import build_country_source_info


def test_ontario_is_a_registered_subdivision_jurisdiction() -> None:
    profile = get_country_profile("ca-on")
    config = get_country_bootstrap_config("CA-ON")

    assert profile.code == "CA-ON"
    assert profile.name_en == "Ontario, Canada"
    assert get_country_display_name("CA-ON", "en") == "Ontario, Canada"
    assert get_country_display_name("CA-ON", "zh") == "加拿大安大略省"
    assert config["parent_country_code"] == "CA"
    assert config["location_type"] == "subdivision"
    assert config["iso_subdivision_code"] == "CA-ON"
    assert config["crawler_config"]["geography_key"] == "country:CA-ON:national"
    assert config["crawler_config"]["supports_fill_missing"] is False
    assert config["crawler_config"]["default_fill_missing"] is False
    assert "full_history_start_year" not in config["crawler_config"]
    assert "CA-ON" in get_standard_country_codes()
    assert not any(
        "CA-ON" in warning for warning in validate_standard_country_registry()
    )


def test_canada_national_scope_does_not_resolve_to_ontario() -> None:
    assert get_expected_scopes_for_country("CA") == ["all"]
    assert canonicalize_task_source("all", country_code="CA") == "all"
    assert default_source_for_country("CA") == "all"
    assert source_scope_label("all", country_code="CA") == "All Sources"

    assert get_expected_scopes_for_country("CA-ON") == ["pho_idto_monthly"]
    assert canonicalize_task_source("all", country_code="CA-ON") == (
        "pho_idto_monthly"
    )
    assert default_source_for_country("CA-ON") == "pho_idto_monthly"


def test_ontario_source_info_exports_jurisdiction_metadata() -> None:
    result = build_country_source_info("ca-on")

    assert result["country_code"] == "CA-ON"
    assert result["parent_country_code"] == "CA"
    assert result["location_type"] == "subdivision"
    assert result["iso_subdivision_code"] == "CA-ON"
    assert result["primary_scope"] == "pho_idto_monthly"
    assert result["primary_label"] == "Public Health Ontario IDTO Monthly"
    assert result["sources"][0]["cadence"] == "monthly"
    assert result["sources"][0]["type"] == "microsoft_bi"


def test_ontario_control_plane_matches_snapshot_capabilities() -> None:
    country = SimpleNamespace(
        id=17,
        code="CA-ON",
        name="Ontario, Canada",
        name_en="Ontario, Canada",
        name_local="Ontario, Canada",
        language="en-CA",
        timezone="America/Toronto",
    )

    result = _country_source_config(country, lang="en")

    assert result.supports_crawl is True
    assert result.supports_fill_missing is False
    assert result.default_fill_missing is False
    assert result.supports_start_year is False
    assert all(
        option.supports_start_year is False for option in result.source_options
    )
