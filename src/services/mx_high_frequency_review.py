"""Second-stage publication checks after exact DGE category mapping review."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re


def mx_source_label_icd_conflict(label: str, icd_code: str) -> dict | None:
    """Describe an unresolved original-code conflict without correcting it."""
    expected = {
        "fiebre amarilla": "A95",
        "encefalitis equina venezolana": "A92.2",
    }.get(re.sub(r"\s+", " ", label.strip("§*• ")).casefold())
    original = re.sub(r"\s+", "", icd_code).upper()
    if expected is None or original == expected:
        return None
    return {
        "source_label": label, "source_icd_code": icd_code,
        "expected_icd_code": expected, "reason": "source_label_icd_conflict",
        "policy": "retain_original_source_observations_without_canonical_projection",
    }


def reviewed_mx_high_frequency_record(record: dict, country_code: str) -> dict | None:
    """Admit only explicit national, all-sex, single-period case counts.

    Calling code must already have verified the category's canonical concept
    mapping. Cumulative, estimated, sex-specific and unknown-calendar measures
    remain in the source ledger and cannot supply a patient-count curve.
    """
    if country_code != "MX" or record.get("geography_key") != "country:MX:national":return None
    source=record.get("source_system");dims=record.get("dimensions")
    if not isinstance(dims,dict):return None
    if (record.get("metric_type")!="case_notifications"
        or record.get("series_unit")!="count" or record.get("observation_unit")!="count"
        or dims.get("sex")!="all" or dims.get("age_group")!="all"
        or dims.get("geography_level")!="national"):return None
    label=str(record.get("reviewed_source_label") or record.get("source_label") or "")
    if not label or dims.get("source_disease_label")!=label or not dims.get("source_icd_code"):return None
    # Some original weekly tables swap these two ICD codes. Preserve their
    # raw cells, but never trust a label-only mapping for contradictory codes.
    if mx_source_label_icd_conflict(label, str(dims["source_icd_code"])):return None
    if not re.fullmatch(r"dge_[a-f0-9]{20}__all__case_notifications",str(record.get("reviewed_source_code") or "")):return None
    when=record.get("date")
    try:
        when=when.date() if isinstance(when,datetime) else when if isinstance(when,date) else date.fromisoformat(str(when)[:10])
    except (ValueError,TypeError):return None
    if source=="SRC_MX_DGE_MONTHLY":
        if (record.get("definition_version")!="mx_dge_monthly_monthly_new_case_notifications_v1"
            or record.get("reporting_basis")!="monthly_new_case_notifications"
            or record.get("temporal_granularity")!="monthly"
            or dims.get("source_case_definition")!="new_case_notifications" or when.day!=1):return None
    elif source=="SRC_MX_DGE_WEEKLY":
        basis=record.get("reporting_basis")
        if (basis not in {"special_confirmed_surveillance","conventional_including_probable"}
            or record.get("definition_version")!=f"mx_dge_weekly_{basis}_v1"
            or record.get("temporal_granularity")!="weekly"
            or dims.get("calendar")!="source_verified_sunday_saturday"
            or dims.get("table_family") not in {"national_summary","national_total_from_state_table"}
            or when.weekday()!=6):return None
        year=dims.get("source_epi_year");week=dims.get("source_epi_week")
        if type(year) is not int or type(week) is not int or not 1<=week<=53:return None
        # The parser derived this table's date from an explicitly dated cover,
        # never from the cover week label alone. The resulting period year must
        # be compatible; do not relabel a conventional W42 row as cover W43.
        if when.year not in {year,year-1} or (when+timedelta(days=6)).year not in {year,year+1}:return None
    else:return None
    result=dict(record);result["projection_dimension_key"]="all"
    return result


__all__=["reviewed_mx_high_frequency_record", "mx_source_label_icd_conflict"]
