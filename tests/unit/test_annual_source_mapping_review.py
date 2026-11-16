"""Guard source meaning and population totals at annual-source onboarding."""
from __future__ import annotations

import hashlib
import json
import csv
from datetime import date
from pathlib import Path

import pytest

from src.data.crawlers.my_who_annual import INDICATORS
from src.data.crawlers.spf_overseas import DATASETS
from src.data.crawlers.who_annual import COUNTRY_CONTRACTS
from src.services.annual_source_mapping_review import WHO_TARGETS, spf_mapping, who_mapping
from src.services.disease_mapping_config import load_reviewed_source_category_mappings
from scripts.verify_annual_source_mappings import verify_country


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("code", INDICATORS)
def test_who_indicator_retains_metric_and_definition(code):
    label, metric = INDICATORS[code]
    review = who_mapping(code, label, "who_gho_country_reported_annual_v1")
    assert review["metric_type"] == metric
    assert review["total_dimensions"] == {"who_indicator": code}
    assert review["aggregation_policy"] == "non_additive"
    assert review["temporal_granularity"] == "annual"
    assert review["reporting_basis"] == "who_country_reported_notifications"


def test_neonatal_and_congenital_scopes_are_separate_from_general_disease():
    assert WHO_TARGETS["WHS3_56"] != WHO_TARGETS["WHS3_46"]
    assert WHO_TARGETS["WHS3_55"] != WHO_TARGETS["WHS3_57"]
    assert WHO_TARGETS["WHS3_47"] == "D235"  # not bacterial/meningococcal-only
    assert WHO_TARGETS["WHS3_49"] == "D013"  # polio is not AFP syndrome


@pytest.mark.parametrize("code", ["WHS3_45", "TB_c_newinc", "MALARIA_CONF_CASES"])
def test_qualified_who_counts_do_not_claim_unqualified_scope(code):
    assert who_mapping(code, INDICATORS[code][0], "who_gho_country_reported_annual_v1")["mapping_relation"] == "narrower"


@pytest.mark.parametrize("code,label,version", [
    ("UNKNOWN", "Measles - number of reported cases", "who_gho_country_reported_annual_v1"),
    ("WHS3_62", "Measles - modelled incidence", "who_gho_country_reported_annual_v1"),
    ("WHS3_62", "Measles - number of reported cases", "future-definition-v2"),
])
def test_who_drift_requires_review(code, label, version):
    with pytest.raises(ValueError):
        who_mapping(code, label, version)


def _spf(base, strata, basis):
    spec = next(v for v in DATASETS.values() if v.code == base)
    suffix = "__" + hashlib.sha256(json.dumps(strata, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16] if strata else ""
    label = spec.title + ("; " + "; ".join(f"{k}: {v}" for k, v in strata.items()) if strata else "")
    return base + suffix, label, f"spf_odisse_{base}_{basis}_v1"


@pytest.mark.parametrize("age,serogroup,canonical", [
    ("Tous âges", "Tous types", True),
    ("0 an", "Tous types", False),
    ("Tous âges", "Y", False),
    ("25-64 ans", "C", False),
])
def test_spf_all_age_all_serogroup_total_is_not_added_to_components(age, serogroup, canonical):
    review = spf_mapping(*_spf("invasive_meningococcal", {"classe_age": age, "meningocoque": serogroup}, "residence_or_notifier_region"))
    assert review["projection_policy"] == ("canonical" if canonical else "discovery_only")
    assert (review["total_dimensions"] is not None) is canonical
    assert review["aggregation_policy"] == "non_additive"


@pytest.mark.parametrize("age,canonical", [("Tous âges", True), ("0 an", False), ("30 ans et plus", False)])
def test_spf_rash_year_measles_total_and_age_groups(age, canonical):
    review = spf_mapping(*_spf("measles", {"mdo_cl_age_rougeole": age}, "residence_or_notifier_department_rash_year"))
    assert review["projection_policy"] == ("canonical" if canonical else "discovery_only")
    assert review["reporting_basis"] == "residence_or_notifier_department_rash_year"


def test_spf_tuberculosis_location_definition_change_is_retained():
    reporting = spf_mapping(*_spf("tuberculosis", {}, "notification_department"))
    residence = spf_mapping(*_spf("tuberculosis", {}, "residence_department"))
    assert reporting["reporting_basis"] != residence["reporting_basis"]
    assert reporting["target_code"] == residence["target_code"] == "D025"


def test_spf_code_hash_cannot_be_reused_for_another_population():
    code, label, version = _spf("measles", {"mdo_cl_age_rougeole": "0 an"}, "residence_or_notifier_department_rash_year")
    with pytest.raises(ValueError, match="source code"):
        spf_mapping(code, label.replace("0 an", "Tous âges"), version)


def test_reviewed_manifests_cover_each_annual_contract_and_only_published_spf_totals():
    mappings = load_reviewed_source_category_mappings()
    for country in COUNTRY_CONTRACTS:
        source = f"SRC_{country}_WHO_GHO"
        reviewed = [m for m in mappings if m.source_id == source]
        assert {m.source_code for m in reviewed} == set(INDICATORS)
        assert all(m.definition_version == "who_gho_country_reported_annual_v1" for m in reviewed)
        assert all(m.aggregation_policy == "non_additive" for m in reviewed)
    for country in ("GP", "MQ", "GF"):
        reviewed = [m for m in mappings if m.source_id == f"SRC_{country}_SPF_ODISSE"]
        assert len(reviewed) == 48
        assert sum(m.projection_policy == "canonical" for m in reviewed) == 6
        assert sum(m.projection_policy == "discovery_only" for m in reviewed) == 42
        assert {m.definition_version for m in reviewed if m.source_code == "tuberculosis"} == {
            "spf_odisse_tuberculosis_notification_department_v1",
            "spf_odisse_tuberculosis_residence_department_v1",
        }


def test_machine_manifest_does_not_upgrade_who_to_unrestricted_commercial_licence():
    manifest = json.loads((ROOT / "configs/onboarding/annual_source_mappings.json").read_text())
    assert manifest["licensing"]["WHO"]["public_use_context_required"] == "noncommercial_public_health"
    assert len(manifest["countries"]) == 18
    assert len(manifest["reviewed_categories"]) == 414


def _write_who_csv(root, **overrides):
    row = {
        "SourceDiseaseCode": "WHS3_62", "DefinitionVersion": "who_gho_country_reported_annual_v1",
        "RawDiseaseLabel": "Measles - number of reported cases", "Measure": "case_notifications",
        "Frequency": "annual", "Unit": "count", "ReportingBasis": "who_country_reported_notifications",
        "GeographyKey": "country:ET:national", "Dimensions": '{"who_indicator":"WHS3_62"}',
        "Year": "2025", "PeriodStart": "2025-01-01", "PeriodEnd": "2025-12-31",
        "Cases": "", "MissingValueStatus": "source_missing", **overrides,
    }
    path = root / "et/who_gho_annual.csv"
    path.parent.mkdir()
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


def test_offline_replay_keeps_unknown_observations_and_empty_indicators_distinct(tmp_path):
    path = _write_who_csv(tmp_path)
    before = path.read_bytes()
    report = verify_country("ET", data_root=tmp_path, as_of=date(2026, 11, 16))
    assert report["observations"] == 1
    assert report["source_missing_observations"] == 1
    assert report["configured_without_observations"] == 17
    assert report.get("numeric_observations", 0) == 0
    assert path.read_bytes() == before
    assert report["public_snapshot_verified"] is False


@pytest.mark.parametrize("changes", [
    {"Measure": "death_notifications"},
    {"GeographyKey": "country:KE:national"},
    {"Dimensions": '{"who_indicator":"WHS3_62","age":"under-five"}'},
    {"PeriodEnd": "2025-06-30"},
    {"MissingValueStatus": "reported", "Cases": "12.5"},
])
def test_offline_replay_rejects_unsafe_source_scope_changes(tmp_path, changes):
    _write_who_csv(tmp_path, **changes)
    with pytest.raises(ValueError):
        verify_country("ET", data_root=tmp_path, as_of=date(2026, 11, 16))
