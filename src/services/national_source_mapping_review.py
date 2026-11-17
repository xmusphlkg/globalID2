"""Publication contracts for a conservative subset of national observations.

Only source-reported totals are admitted. The original dimensions remain on
the observation; a projection key is a display decision, never a rollup.
"""
from __future__ import annotations

import re
from typing import Any


_DIMENSION_KEYS = {
    "SRC_CL_ENO": {"note", "source_case_classification", "source_eno", "source_notification_year", "source_system_period"},
    "SRC_KH_CAMEWARN": {"chart_only", "extraction", "population_scope", "source_calendar", "source_case_definition", "source_reporting_week", "source_reporting_year"},
    "SRC_MX_DGE_ANNUAL": {"age_group", "edition_path", "geography_level", "rate_denominator", "sex", "source_case_definition", "source_disease_label", "source_footnote_markers", "source_footnotes_sha256", "source_icd_code", "source_icd_revision", "source_page", "table"},
    "SRC_ID_KEMKES_PROFILE": {"count_kind", "evidence", "extraction", "source_calendar", "source_case_label", "source_page", "source_profile_year", "source_report_sha256", "source_report_url", "source_statistic_year"},
    "SRC_PE_CDC": {"case_classification", "comparison_values", "comparison_year", "count_kind", "evidence", "extraction", "incidence_denominator", "incidence_rate", "source_calendar", "source_case_label", "source_current_year_values", "source_page", "source_report_sha256", "source_report_url", "source_reporting_week", "source_reporting_year", "table_schema"},
    "SRC_ZA_NICD_NMC": {"case_classification", "cohort", "count_period", "date_role", "nmc_category", "source_disease_code", "source_window_detail", "source_window_label", "time_basis"},
}


def national_mapping(source_id: str, code: str, label: str, definition_version: str) -> dict[str, Any]:
    """Return the expected source contract, or reject an unreviewed scope."""
    metric, basis, frequency, dimensions = "cases", "notification", "weekly", None
    if source_id == "SRC_AR_MOH_HIV_ANNUAL":
        from src.core.ar_hiv_source import (
            DEFINITION_VERSION, METRIC_TYPE, NATIONAL_DIMENSIONS, REPORTING_BASIS,
            SOURCE_DISEASE_CODE, SOURCE_DISEASE_LABEL,
        )
        if definition_version != DEFINITION_VERSION or code != SOURCE_DISEASE_CODE or label != SOURCE_DISEASE_LABEL:
            raise ValueError("Only reviewed direct national annual HIV notification totals selected")
        metric, basis, frequency = METRIC_TYPE, REPORTING_BASIS, "annual"
        dimensions = dict(NATIONAL_DIMENSIONS)
    elif source_id == "SRC_CL_ENO":
        if not re.fullmatch(r"cl_eno_(filemaker_2007_2018|epivigila_2019_onward)_confirmada_v1", definition_version) or not code.endswith("__confirmada"):
            raise ValueError("Only explicitly confirmed annual Chile notifications selected")
        metric, basis, frequency = "confirmed_case_notifications", "validated_notification_by_notification_year", "annual"
    elif source_id == "SRC_KH_CAMEWARN":
        if definition_version != "kh_camewarn_source_calendar_syndromic_v1":
            raise ValueError("Unreviewed CamEWARN definition")
        basis = "syndromic_notification"
    elif source_id == "SRC_MW_PHIM_IDSR":
        if definition_version != "mw_phim_idsr_table1_suspected_v1" or not code.endswith(":suspected:weekly:cases"):
            raise ValueError("Only Table 1 single-week suspected notification counts selected")
    elif source_id == "SRC_MX_DGE_ANNUAL":
        if definition_version not in {"mx_dge_annual_icd9_v1", "mx_dge_annual_icd10_v1"} or not code.endswith("__all__all__cases"):
            raise ValueError("Only all-age all-sex national notification counts selected")
        metric, frequency = "case_notifications", "annual"
    elif source_id == "SRC_MY_MOH_STD":
        if definition_version != "my_moh_notified_annual_report_date_v1" or code not in {"aids", "hiv", "chancroid", "gonorrhea", "syphillis"}:
            raise ValueError("Unreviewed MOH STD category")
        frequency = "annual"
        dimensions = {"dataset": "std_state", "date_basis": "report_date", "source_disease_code": code, "source_disease_label": code}
    elif source_id == "SRC_MY_IDENGUE_DAILY":
        if code != "dengue" or definition_version != "my_idengue_explicit_daily_count_v1":
            raise ValueError("Unreviewed iDengue category")
        basis, frequency = "published_daily_count_event_date_unspecified", "daily"
        dimensions = {"date_basis": "source_daily_label", "source_disease_code": "dengue", "source_geography_code": "malaysia", "source_metric": "daily_cases"}
    elif source_id == "SRC_MY_WHO_GHO":
        if definition_version != "my_who_gho_country_reported_annual_v1":
            raise ValueError("Unreviewed Malaysia WHO definition")
        from src.services.annual_source_mapping_review import who_mapping
        # Same reviewed WHO indicator dictionary, with MY's source identity.
        contract = who_mapping(code, label, definition_version)
        return contract
    elif source_id == "SRC_NG_NCDC":
        # WER and situation reports overlap, as do confirmed/suspected columns.
        # Choose a single source/classification contract in this first release.
        if not code.startswith("wer:") or not code.endswith(":suspected:weekly:cases") or definition_version != "ng_ncdc_wer_suspected_weekly_cases_v1":
            raise ValueError("Only WER suspected single-week notifications selected")
    elif source_id == "SRC_PE_CDC":
        if not code.endswith("__weekly_cases") or definition_version not in {
            "pe_cdc_national_summary_unclassified_comparison_v1_weekly_cases_v1",
            "pe_cdc_national_summary_classified_comparison_v1_weekly_cases_v1",
        }:
            raise ValueError("Only unclassified single-week case totals selected; no sum of classifications")
    elif source_id == "SRC_NL_RIVM_NOTIFIABLE":
        if not definition_version.startswith("nl_"):
            raise ValueError("Unreviewed Dutch definition")
        metric, basis, frequency = "case_notifications", "mandatory_notification", "annual"
        dimensions = {"date_basis": "first_onset_else_diagnosis_else_notification", "population_scope": "source_reporting_population", "reporting_basis": "mandatory_notification"}
    elif source_id == "SRC_TH_DOE506":
        if definition_version != "th-doe506-national-monthly-rtf-v1":
            raise ValueError("Unreviewed Report 506 definition")
        metric, basis, frequency, dimensions = "case_notifications", "report_506_mostly_suspected_notifications", "monthly", {}
    elif source_id == "SRC_TH_DDS_WEEKLY":
        if definition_version != "th_dds_situation_single_week_onset_v1":
            raise ValueError("Unreviewed DDS weekly definition")
        metric, basis, dimensions = "case_notifications", "dds_notification_onset_date_and_place", {"date_place_basis": "onset"}
    elif source_id == "SRC_TH_DDS_TABLEAU":
        if definition_version != "th_dds_tableau_single_week_report_v1":
            raise ValueError("Unreviewed DDS Tableau definition")
        metric, basis, dimensions = "case_notifications", "dds_notification_report_date_and_place", {"date_place_basis": "report"}
    elif source_id == "SRC_ID_KEMKES_PROFILE":
        if "appendix" in code or "appendix" in definition_version or not code.endswith("__cases") or definition_version != "id_kemkes_profile_national_narrative_cases_v1":
            raise ValueError("Only explicitly national narrative case totals selected")
        metric, basis, frequency = "case_notifications", "national_health_profile", "annual"
    elif source_id == "SRC_ZA_NICD_NMC":
        if not re.fullmatch(r"nmc[123]:[^:]+:all_notified:reported_month:cases:notification_window:all_notifications", code) or definition_version != "za_nicd_nmc_all_notified_reported_month_cases_notification_window_all_notifications_v1":
            raise ValueError("Only current all-notified monthly national notifications selected")
        frequency = "monthly"
    else:
        raise ValueError("Source has no reviewed national total publication contract")
    return {"metric_type": metric, "reporting_basis": basis, "temporal_granularity": frequency, "total_dimensions": dimensions}


def reviewed_national_total(record: dict[str, Any], country_code: str) -> bool:
    """Validate source-specific dimensions without replacing the raw strata."""
    source = str(record.get("source_system") or "")
    code = str(record.get("reviewed_source_code") or "")
    label = str(record.get("reviewed_source_label") or "")
    try:
        contract = national_mapping(source, code, label, str(record.get("definition_version") or ""))
    except ValueError:
        return False
    if record.get("geography_key") != f"country:{country_code}:national" or any(record.get(k) != contract[k] for k in ("metric_type", "reporting_basis", "temporal_granularity")):
        return False
    dims = record.get("dimensions")
    if not isinstance(dims, dict):
        return False
    if contract["total_dimensions"] is not None:
        return dims == contract["total_dimensions"]
    if source in _DIMENSION_KEYS and not dims.keys() <= _DIMENSION_KEYS[source]:
        return False
    # Explicit population dimensions must never be silently normalized away.
    if any(dims.get(k) not in (None, "all") for k in ("sex", "age_group", "age", "ethnicity", "province", "district", "residence")):
        return False
    if source == "SRC_CL_ENO":
        return (dims.get("source_case_classification") == "CONFIRMADA" and dims.get("source_eno") == label
                and dims.get("source_system_period") in {"filemaker_2007_2018", "epivigila_2019_onward"}
                and isinstance(dims.get("source_notification_year"), int))
    if source == "SRC_KH_CAMEWARN":
        return (dims.get("chart_only") is False and dims.get("extraction") == "national_weekly_matrix"
                and dims.get("population_scope") == "reporting_health_facilities"
                and dims.get("source_calendar") == "report_explicit_interval"
                and dims.get("source_case_definition") == label)
    if source == "SRC_MW_PHIM_IDSR":
        return dims == {"case_classification": "suspected", "section": "epidemic prone diseases", "source_category": label, "table": "Table 1"}
    if source == "SRC_MX_DGE_ANNUAL":
        return (dims.get("sex") == "all" and dims.get("age_group") == "all" and dims.get("geography_level") == "national"
                and dims.get("source_case_definition") == "source_annual_new_cases" and dims.get("source_disease_label") == label
                and dims.get("table") in {"national_cases_age", "national_cases_sex"}
                and dims.get("source_icd_revision") in {"ICD-9", "ICD-10"})
    if source == "SRC_NG_NCDC":
        disease_code = code.split(":")[1]
        return dims == {"case_classification": "suspected", "count_period": "weekly", "report_family": "wer", "source_disease_code": disease_code, "surveillance_scope": "reported_notifications"}
    if source == "SRC_PE_CDC":
        return (dims.get("case_classification") == "reported_unspecified" and dims.get("count_kind") == "weekly_cases"
                and dims.get("extraction") == "national_summary_table" and dims.get("source_calendar") == "explicit_bulletin_dates"
                and dims.get("table_schema") in {"unclassified_comparison_v1", "classified_comparison_v1"} and dims.get("source_case_label") == label)
    if source == "SRC_ID_KEMKES_PROFILE":
        return (dims.get("count_kind") == "cases" and dims.get("extraction") == "national_narrative"
                and dims.get("source_calendar") == "calendar_year_explicit_source_title" and dims.get("source_case_label") == label
                and isinstance(dims.get("source_statistic_year"), int))
    if source == "SRC_ZA_NICD_NMC":
        return (dims.get("case_classification") == "all_notified" and dims.get("cohort") == "all_notifications"
                and dims.get("count_period") == "reported_month" and dims.get("date_role") == "representative_period_start"
                and dims.get("time_basis") == "notification_window" and dims.get("source_disease_code") == code.split(":")[1])
    return False


__all__ = ["national_mapping", "reviewed_national_total"]
