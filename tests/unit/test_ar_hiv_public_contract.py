"""Keep Argentina's direct HIV totals distinct from BEN cumulative snapshots."""

from copy import deepcopy
import json

import pytest

from src.core import country_library
from src.core.ar_hiv_source import (
    DEFINITION_VERSION, METRIC_TYPE, NATIONAL_DIMENSIONS, REPORTING_BASIS,
    SOURCE_DISEASE_CODE, SOURCE_DISEASE_LABEL, SOURCE_ID, SOURCE_NAME, SOURCE_SCOPE,
)
from src.core.source_scopes import canonicalize_task_source, default_source_for_country, scope_from_data_source
from src.generation.site_data_about import build_country_source_info
from src.services.disease_mapping_config import load_reviewed_source_category_mappings
from src.services.national_source_mapping_review import reviewed_national_total


def national_record():
    return {
        "source_system": SOURCE_ID, "reviewed_source_code": SOURCE_DISEASE_CODE,
        "reviewed_source_label": SOURCE_DISEASE_LABEL,
        "definition_version": DEFINITION_VERSION,
        "geography_key": "country:AR:national", "metric_type": METRIC_TYPE,
        "reporting_basis": REPORTING_BASIS, "temporal_granularity": "annual",
        "dimensions": dict(NATIONAL_DIMENSIONS),
    }


def test_hiv_and_flunet_are_public_while_ben_stays_private(monkeypatch):
    registry = json.loads(country_library.COUNTRY_BOOTSTRAP_FILE.read_text())
    for config in (registry["AR"], country_library.get_country_bootstrap_config("AR")):
        assert config["public_release_enabled"] is True
        assert config["public_legacy_enabled"] is False
        assert config["public_source_systems"] == [SOURCE_ID, "SRC_AR_WHO_FLUNET"]
        assert config["public_source_scopes"] == [SOURCE_SCOPE, "who_flunet_weekly"]
        policies = config["crawler_config"]["source_policies"]
        assert policies[SOURCE_SCOPE]["public_release_enabled"] is True
        assert policies["ar_ben_weekly"]["public_release_enabled"] is False
    monkeypatch.setattr(country_library, "_load_country_bootstrap_registry", lambda: {})
    assert country_library.get_country_bootstrap_config("AR")["public_source_systems"] == [SOURCE_ID]


@pytest.mark.parametrize("value", [None, "all", "AR", "argentina", SOURCE_SCOPE, SOURCE_ID])
def test_default_and_new_source_aliases_route_to_annual_hiv(value):
    assert canonicalize_task_source(value, country_code="AR") == SOURCE_SCOPE


@pytest.mark.parametrize("value", ["ben", "ar_ben_weekly", "SRC_AR_BEN"])
def test_explicit_ben_identity_stays_ben(value):
    assert canonicalize_task_source(value, country_code="AR") == "ar_ben_weekly"


def test_persisted_source_identity_does_not_alias_into_ben():
    assert default_source_for_country("AR") == SOURCE_SCOPE
    for source in (SOURCE_ID, SOURCE_SCOPE, SOURCE_NAME):
        assert scope_from_data_source(source) == SOURCE_SCOPE
    assert scope_from_data_source("SRC_AR_BEN") == "ar_ben_weekly"


def test_new_mapping_is_independent_and_old_ben_contracts_stay_no_projection():
    mappings = load_reviewed_source_category_mappings()
    new = [m for m in mappings if m.source_id == SOURCE_ID]
    assert len(new) == 1
    assert (new[0].country_code, new[0].target_code, new[0].projection_policy) == ("AR", "D162", "canonical")
    assert new[0].source_path == "configs/mapping/ar.csv"
    ben = [m for m in mappings if m.source_id == "SRC_AR_BEN"]
    assert ben and all(m.projection_policy == "no_projection" for m in ben)


def test_review_admits_direct_total_without_discarding_source_dimensions():
    record = national_record()
    original = deepcopy(record)
    assert reviewed_national_total(record, "AR")
    assert record == original


@pytest.mark.parametrize("field,value", [
    ("metric_type", "cumulative_case_notifications"),
    ("metric_type", "hiv_prevalence"),
    ("reporting_basis", "estimated_incident_infections"),
    ("temporal_granularity", "weekly"),
    ("geography_key", "country:AR:province:02"),
    ("definition_version", "ar_ben_eno_confirmed_cumulative_v1"),
    ("reviewed_source_code", "aids"),
    ("reviewed_source_label", "AIDS"),
])
def test_other_measure_geography_and_definition_cannot_enter_hiv_curve(field, value):
    record = national_record()
    record[field] = value
    assert not reviewed_national_total(record, "AR")


@pytest.mark.parametrize("field,value", [
    ("sex", "female"), ("source_sex_id", "1"),
    ("source_sex_label", "Varones"), ("source_jurisdiction_label", "Buenos Aires"),
    ("source_jurisdiction_code", "2"), ("date_basis", "cumulative_cutoff"),
])
def test_subgroups_and_cumulative_dimensions_cannot_be_erased_into_total(field, value):
    record = national_record()
    record["dimensions"][field] = value
    assert not reviewed_national_total(record, "AR")


def test_public_source_metadata_preserves_attribution_and_statistical_limits():
    info = build_country_source_info("AR", database_config={
        "crawler_config": {"sources": ["ar_ben_weekly"]},
        "data_source_url": "https://stale.test/ben", "notes": "stale cumulative feed",
    })
    assert {s["scope"] for s in info["sources"]} == {SOURCE_SCOPE, "who_flunet_weekly"}
    source = next(s for s in info["sources"] if s["scope"] == SOURCE_SCOPE)
    assert source["scope"] == SOURCE_SCOPE and source["source_id"] == SOURCE_ID
    assert "CC BY 4.0" in source["license"]
    assert "Ministerio de Salud" in source["required_attribution"]
    assert "2014–2023" in source["description"]
    assert "not estimates of incident infections" in source["description"]
    assert "不等于新感染估计" in source["description_zh"]
    assert "ne mesurent ni les nouvelles infections" in source["description_fr"]
    assert "stale" not in source["description"]
