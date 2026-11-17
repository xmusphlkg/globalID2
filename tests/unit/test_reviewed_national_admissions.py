"""Source boundaries and honest provenance for the nine newly open subjects."""

import json

import pytest

from src.core import country_library
from src.core.high_frequency_sources import sources_for_country
from src.core.reviewed_national_admissions import (
    OPERATOR_AUTHORIZED_NATIONAL_COUNTRIES, REUSE_STATUS,
    reviewed_national_admission_config,
)
from src.generation.site_data_about import build_country_source_info
from src.services.national_source_mapping_review import national_mapping


@pytest.mark.parametrize("code", sorted(OPERATOR_AUTHORIZED_NATIONAL_COUNTRIES))
def test_runtime_registry_and_fallback_admit_only_explicit_sources(code, monkeypatch):
    registry = json.loads(country_library.COUNTRY_BOOTSTRAP_FILE.read_text())
    expected = reviewed_national_admission_config(code)
    config = country_library.get_country_bootstrap_config(code)
    assert len(OPERATOR_AUTHORIZED_NATIONAL_COUNTRIES) == 9
    for key in ("public_release_enabled", "public_legacy_enabled", "public_source_systems", "public_source_scopes", "public_source_details"):
        assert config[key] == registry[code][key]
        if key in ("public_source_systems", "public_source_scopes"):
            supplements = sources_for_country(code)
            added = {source.source_id if key == "public_source_systems" else source.scope
                     for source in supplements}
            assert set(config[key]) == set(expected[key]) | added
        elif key == "public_source_details":
            assert expected[key].items() <= config[key].items()
        else:
            assert config[key] == expected[key]
    monkeypatch.setattr(country_library, "_load_country_bootstrap_registry", lambda: {})
    fallback = country_library.get_country_bootstrap_config(code)
    for key in ("public_release_enabled", "public_legacy_enabled", "public_source_systems", "public_source_scopes", "public_source_details"):
        assert fallback[key] == expected[key]
    assert config["public_release_enabled"] is True
    assert config["public_legacy_enabled"] is False
    assert config["crawler_config"]["reuse_status"] == REUSE_STATUS
    assert config["crawler_config"]["source_license_verified"] is False
    for scope in expected["public_source_scopes"]:
        policy = config["crawler_config"]["source_policies"][scope]
        assert policy["public_release_enabled"] is True
        assert policy["source_license_verified"] is False


@pytest.mark.parametrize("code", sorted(OPERATOR_AUTHORIZED_NATIONAL_COUNTRIES))
def test_public_provenance_survives_stale_database_and_discloses_terms(code):
    expected = reviewed_national_admission_config(code)
    result = build_country_source_info(code, database_config={
        "notes": "unreviewed ingestion text", "data_source_url": "https://stale.test/",
        "crawler_config": {"sources": ["private-feed"]},
    })
    sources = {item["scope"]: item for item in result["sources"]}
    assert set(expected["public_source_scopes"]) <= sources.keys()
    assert "unreviewed ingestion text" not in result["notes"]
    for scope in expected["public_source_scopes"]:
        source = sources[scope]
        assert source["source_id"] in expected["public_source_systems"]
        assert source["publisher"] in source["required_attribution"]
        assert source["url"].startswith("https://") and "stale.test" not in source["url"]
        assert "unverified" in source["license"].lower()
        assert source["license_url"] is None
        assert source["review_status"] == "source_terms_unverified_semantics_reviewed"
        assert "An open license covering these data has not been verified" in source["description"]
        assert "尚未核实覆盖此数据的开放许可" in source["description_zh"]
        assert "Aucune licence ouverte" in source["description_fr"]
        assert "operator" not in source["description"].lower()


def test_thailand_archive_stays_closed_and_source_bases_remain_distinct():
    config = country_library.get_country_bootstrap_config("TH")
    assert set(config["public_source_systems"]) == {"SRC_TH_DOE506", "SRC_TH_DDS_WEEKLY", "SRC_TH_DDS_TABLEAU"}
    assert config["crawler_config"]["source_policies"]["th_id_th_history"]["public_release_enabled"] is False
    assert "SRC_TH_ID_TH_ARCHIVE" not in config["public_source_systems"]
    onset = national_mapping("SRC_TH_DDS_WEEKLY", "dengue", "Dengue", "th_dds_situation_single_week_onset_v1")
    report = national_mapping("SRC_TH_DDS_TABLEAU", "dengue", "Dengue", "th_dds_tableau_single_week_report_v1")
    assert onset["reporting_basis"] != report["reporting_basis"]
    with pytest.raises(ValueError):
        national_mapping("SRC_TH_ID_TH_ARCHIVE", "dengue", "Dengue", "unverified")


def test_bq_shared_collector_does_not_open_netherlands_native_feed():
    config = country_library.get_country_bootstrap_config("BQ")
    assert config["public_source_systems"] == ["SRC_NL_RIVM_NOTIFIABLE"]
    assert config["crawler_config"]["geography_key"] == "country:BQ:national"
    assert config["crawler_config"]["collection_country_code"] == "NL"
    assert config["crawler_config"]["sources"] == []
    assert country_library.get_country_bootstrap_config("NL")["public_source_systems"] == ["SRC_NL_ECDC_ATLAS", "SRC_NL_ECDC_RESPIRATORY"]


@pytest.mark.parametrize("code", ["CO"])
def test_colombia_typed_admission_is_independent_of_national_overlay(code):
    assert reviewed_national_admission_config(code) == {}
    config = country_library.get_country_bootstrap_config(code)
    assert config["public_release_enabled"] is True
    assert config["public_legacy_enabled"] is False
    assert set(config["public_source_systems"]) == {"SRC_CO_INS_BES", "SRC_CO_WHO_FLUNET"}
    policies = config["crawler_config"]["source_policies"]
    assert policies["co_ins_bes_weekly"]["public_release_enabled"] is True
    assert policies["co_ins_sivigila_current"]["public_release_enabled"] is False


def test_admission_overlays_do_not_share_mutable_policy_state():
    first = reviewed_national_admission_config("TH")
    first["crawler_config"]["source_policies"]["th_id_th_history"]["public_release_enabled"] = True
    first["public_source_details"]["th_dds_weekly"]["publisher"] = "changed"
    fresh = reviewed_national_admission_config("TH")
    assert fresh["crawler_config"]["source_policies"]["th_id_th_history"]["public_release_enabled"] is False
    assert fresh["public_source_details"]["th_dds_weekly"]["publisher"] != "changed"
