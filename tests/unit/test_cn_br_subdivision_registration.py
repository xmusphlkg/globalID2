from __future__ import annotations

from src.core.country_library import (
    get_country_bootstrap_config,
    get_country_display_name,
    get_country_profile,
    get_standard_country_codes,
)
from src.core.source_scopes import get_expected_scopes_for_country


def test_china_province_is_a_registered_subdivision_jurisdiction() -> None:
    profile = get_country_profile("cn-bj")
    config = get_country_bootstrap_config("CN-BJ")

    assert profile.code == "CN-BJ"
    assert profile.name_en == "Beijing, China"
    assert profile.language == "zh-CN"
    assert profile.timezone == "Asia/Shanghai"
    assert get_country_display_name("CN-BJ", "zh") == "北京市"
    assert config["parent_country_code"] == "CN"
    assert config["location_type"] == "subdivision"
    assert config["iso_subdivision_code"] == "CN-BJ"
    assert config["crawler_config"]["geography_key"] == "country:CN-BJ:national"
    assert config["crawler_config"]["sources"] == [
        "cn_province_datacenter",
        "cn_province_monthly_report",
    ]
    assert "CN-BJ" in get_standard_country_codes()


def test_brazil_state_is_a_registered_subdivision_jurisdiction() -> None:
    profile = get_country_profile("br-sp")
    config = get_country_bootstrap_config("BR-SP")

    assert profile.code == "BR-SP"
    assert profile.name_en == "São Paulo, Brazil"
    assert profile.language == "pt-BR"
    assert profile.timezone == "America/Sao_Paulo"
    assert get_country_display_name("BR-SP", "zh") == "巴西圣保罗州"
    assert config["parent_country_code"] == "BR"
    assert config["location_type"] == "subdivision"
    assert config["iso_subdivision_code"] == "BR-SP"
    assert config["crawler_config"]["geography_key"] == "country:BR-SP:national"
    assert config["crawler_config"]["sources"] == ["sinan_datasus"]
    assert "BR-SP" in get_standard_country_codes()


def test_all_china_provinces_and_brazil_states_are_registered() -> None:
    codes = set(get_standard_country_codes())
    cn = sorted(c for c in codes if c.startswith("CN-"))
    br = sorted(c for c in codes if c.startswith("BR-"))
    assert len(cn) == 31
    assert len(br) == 27
    for code in cn:
        assert get_country_bootstrap_config(code)["parent_country_code"] == "CN"
        assert get_expected_scopes_for_country(code) == [
            "cn_province_datacenter",
            "cn_province_monthly_report",
        ]
    for code in br:
        assert get_country_bootstrap_config(code)["parent_country_code"] == "BR"
        assert get_expected_scopes_for_country(code) == ["sinan_datasus"]
