from types import SimpleNamespace

from dashboard.api.location_codes import (
    is_subdivision_code,
    jurisdiction_geography_key,
    registry_country_code,
)


def test_child_jurisdiction_uses_parent_registry_and_own_geography() -> None:
    country = SimpleNamespace(
        code="CN-SH",
        metadata_={"parent_country_code": "CN", "location_type": "subdivision"},
    )

    assert registry_country_code(country) == "CN"
    assert jurisdiction_geography_key(country.code) == "country:CN-SH:national"
    assert is_subdivision_code(country.code)


def test_plain_country_owns_its_registry() -> None:
    country = {"code": "US", "metadata": {"location_type": "country"}}

    assert registry_country_code(country) == "US"
    assert jurisdiction_geography_key(country["code"]) == "country:US:national"
    assert not is_subdivision_code(country["code"])
