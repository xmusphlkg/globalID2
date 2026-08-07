from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    get_expected_scopes_for_country,
    scope_from_data_source,
    source_options_for_country,
)


def test_europe_monthly_sources_use_stable_canonical_scopes() -> None:
    expected = {
        "FI": ("thl_ttr", "Finland THL Infectious Diseases Register"),
        "NO": ("fhi_msis", "Norway FHI MSIS Statistics Bank"),
        "SE": ("fohm_sminet", "Sweden Public Health Agency SmiNet"),
    }

    for country_code, (scope, label) in expected.items():
        assert canonicalize_task_source("all", country_code=country_code) == scope
        assert get_expected_scopes_for_country(country_code) == [scope]
        assert source_options_for_country(country_code)[0]["value"] == scope
        assert scope_from_data_source(label) == scope
        assert canonical_data_source_label(label, country_code=country_code) == label

