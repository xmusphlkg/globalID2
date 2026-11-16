"""Protect source totals from being confused with rates, strata or snapshots."""
from copy import deepcopy

import pytest

from src.services.national_mapping_review import projection_decision, review_target
from src.services.national_source_mapping_review import national_mapping, reviewed_national_total


def _std_record():
    return {
        "source_system": "SRC_MY_MOH_STD", "reviewed_source_code": "hiv", "reviewed_source_label": "HIV",
        "definition_version": "my_moh_notified_annual_report_date_v1", "geography_key": "country:MY:national",
        "metric_type": "cases", "reporting_basis": "notification", "temporal_granularity": "annual",
        "dimensions": {"dataset": "std_state", "date_basis": "report_date", "source_disease_code": "hiv", "source_disease_label": "hiv"},
    }


def test_explicit_total_is_accepted_without_mutating_its_source_dimensions():
    record = _std_record()
    original = deepcopy(record)
    assert reviewed_national_total(record, "MY")
    assert record == original


@pytest.mark.parametrize("field,value", [
    ("metric_type", "incidence_rate"), ("metric_type", "cumulative_cases"),
    ("reporting_basis", "laboratory_detections"), ("temporal_granularity", "monthly"),
    ("geography_key", "country:MY:state:Selangor"),
])
def test_measure_period_basis_and_geography_changes_require_new_review(field, value):
    record = _std_record()
    record[field] = value
    assert not reviewed_national_total(record, "MY")


def test_population_stratum_cannot_be_normalized_to_a_total():
    record = _std_record()
    record["dimensions"]["sex"] = "female"
    assert not reviewed_national_total(record, "MY")


@pytest.mark.parametrize("source,code,definition", [
    ("SRC_AR_BEN", "eno_x", "ar_ben_eno_confirmed_cumulative_v1"),
    ("SRC_CO_INS_SIVIGILA", "sivigila_210", "co-ins-sivigila-municipality-week-v1"),
    ("SRC_TH_ID_TH_ARCHIVE", "Dengue_fever", "th_id_th_archive_cases_basis_unverified_year_anchor_v1"),
    ("SRC_MY_WHO_FLUNET", "AH3", "my_who_flunet_detections_v1"),
])
def test_incomparable_sources_never_become_national_incident_curves(source, code, definition):
    with pytest.raises(ValueError):
        national_mapping(source, code, "Dengue", definition)


@pytest.mark.parametrize("code", ["dge_x__male__all__cases", "dge_x__all__1to4__cases", "dge_x__all__all__rate"])
def test_mexico_count_contract_rejects_subgroups_and_rates(code):
    with pytest.raises(ValueError):
        national_mapping("SRC_MX_DGE_ANNUAL", code, "Cólera", "mx_dge_annual_icd10_v1")


def test_classified_and_cumulative_peru_cells_cannot_be_added_to_a_weekly_total():
    for code, definition in [
        ("colera__cumulative_cases", "pe_cdc_national_summary_unclassified_comparison_v1_cumulative_cases_v1"),
        ("colera__weekly_cases__confirmed", "pe_cdc_national_summary_classified_comparison_v1_weekly_cases__confirmed_v1"),
    ]:
        with pytest.raises(ValueError):
            national_mapping("SRC_PE_CDC", code, "Cólera", definition)


def test_infant_botulism_is_a_subset_and_ocr_combined_labels_are_held():
    assert review_target({"canonical_source_label": "Botulismo Infantil"}, []) == ("D091", "narrower")
    assert review_target({"canonical_source_label": "Cólera Defectos del tubo neural"}, []) is None


def test_colombia_disease_identity_does_not_authorize_geographic_rollup():
    assert projection_decision({"source_id": "SRC_CO_INS_SIVIGILA", "source_code": "sivigila_210", "definition_version": "v1"})[0] == "no_projection"


def test_south_africa_vital_status_and_quarters_require_distinct_public_contracts():
    for code in [
        "nmc1:measles:patient_status_deceased:reported_month:deaths:notification_window:all_notifications",
        "nmc1:measles:all_notified:reported_quarter:cases:notification_window:all_notifications",
    ]:
        with pytest.raises(ValueError):
            national_mapping("SRC_ZA_NICD_NMC", code, "Measles", "za_nicd_nmc_all_notified_reported_month_cases_notification_window_all_notifications_v1")
