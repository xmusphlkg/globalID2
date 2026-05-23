from types import SimpleNamespace

from dashboard.api.routers.countries import _country_name_zh


def test_country_name_zh_prefers_chinese_local_name_when_mapped_name_lacks_chinese() -> None:
    country = SimpleNamespace(code="HK", name_local="中国香港")
    assert _country_name_zh(country) == "中国香港"


def test_country_name_zh_uses_library_mapping_when_local_is_not_chinese() -> None:
    country = SimpleNamespace(code="US", name_local="United States")
    assert _country_name_zh(country) == "美国"
