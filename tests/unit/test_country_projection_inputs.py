import copy

import pytest

from src.generation.country_projection_inputs import INPUT_KEY, attach_inputs, eligible_inputs, merge_country_diseases, precise_disease_scope
from src.generation.site_data_views import build_country_data, build_country_site_data, build_country_source_series_data


def _record(did, when, cases, *, source="wpp_computed", cadence="monthly"):
    return {"disease_id": did, "date": when, "year_month": when[:7], "cases": cases, "deaths": 1,
            "incidence_rate": float(cases), "incidence_rate_source": source, "mortality_rate": 0.5,
            "quality_status": "validated", "metric_type": "case_notifications",
            "_series_context": {"data_layer": "series_registry", "projection_policy": "single_series",
                                "selected_series_codes": ["SER_" + did], "available_series_count": 1,
                                "period_granularity": cadence,
                                "source_series": [{"series_code": "SER_" + did, "dates": [when], "values": [cases], "quality_statuses": ["validated"]}]}}


@pytest.mark.parametrize("operation", ["replace", "delete", "insert", "source-only"])
def test_scoped_disease_merge_matches_full_country_projection(operation):
    catalogue = {did: {"name_en": did, "name_zh": did, "category": "Test"} for did in ("D001", "D002", "D003")}
    before = [_record("D001", "2025-01-01", 2), _record("D001", "2025-02-01", 3),
              _record("D002", "2025-01-01", 7, cadence="annual"), _record("D002", "2026-01-01", 9, cadence="annual")]
    old = build_country_data("ZZ", "Test", before, catalogue)
    attach_inputs(old, before)
    changed_ids = {"D003"} if operation == "insert" else {"D001"}
    changed = [] if operation == "delete" else [_record(next(iter(changed_ids)), "2024-12-01", 12, source="original_db"), _record(next(iter(changed_ids)), "2025-01-01", 13, source="original_db")]
    source_records = None
    if operation == "source-only":
        changed = []
        source_records = [{**_record("D001", "2025-03-01", 14), "series_code": "SER_D001", "unit": "test", "value": 14}]
    all_rows = sorted([row for row in before if row["disease_id"] not in changed_ids] + changed, key=lambda row: (row["date"], row["disease_id"]))
    complete = build_country_data("ZZ", "Test", all_rows, catalogue, source_records=source_records)
    attach_inputs(complete, all_rows)
    partial = build_country_data("ZZ", "Test", changed, catalogue, source_records=source_records)
    merged = merge_country_diseases(old, partial, changed_ids, changed)
    assert merged == complete
    assert build_country_site_data(merged) == build_country_site_data(complete)
    assert build_country_source_series_data(merged) == build_country_source_series_data(complete)


@pytest.mark.parametrize("operation", ["upsert", "delete"])
def test_precise_scopes_require_all_marker_coverage_and_known_source_dependencies(operation):
    country = {"disease_series": {"D001": {"dates": ["2025-01-01"], "source_series": [{"series_code": "KNOWN"}]}}}
    attach_inputs(country, [_record("D001", "2025-01-01", 1)])
    marker = {"scope": "JP-25", "change_set": {"version": 1, "precise": True, "changes": [{"series_code": "KNOWN", "geography_key": "country:JP-25:national", "operation": "upsert"}]}}
    marker["change_set"]["changes"][0]["operation"] = operation
    assert precise_disease_scope("JP-25", [marker], country, {"D001"}) == {"D001"}
    assert precise_disease_scope("JP-25", [marker, {"scope": "JP", "change_set": None}], country, {"D001"}) is None
    unknown = copy.deepcopy(marker); unknown["change_set"]["changes"][0]["series_code"] = "NEW_HOLDING"
    assert precise_disease_scope("JP-25", [unknown], country, {"D001"}) is None
    assert precise_disease_scope("JP-25", [marker, {"scope": "JP-25", "change_set": None}], country, {"D001"}) is None
    country[INPUT_KEY]["diseases"] = {}
    assert precise_disease_scope("JP-25", [marker], country, {"D001"}) is None


def test_scoped_merge_preserves_unreported_months_and_matches_full_projection():
    catalogue = {did: {"name_en": did, "name_zh": did} for did in ("D001", "D002")}
    before = [
        _record("D001", "2025-01-01", 0),
        {**_record("D001", "2025-02-01", 0), "cases": None},
        _record("D001", "2025-04-01", 4),
        {**_record("D001", "2025-04-08", 0), "cases": None},
        _record("D002", "2025-03-01", 5),
    ]
    changed = [
        _record("D002", "2025-03-01", 9),
        {**_record("D002", "2025-05-01", 0), "cases": None},
        _record("D002", "2025-06-01", 0),
        {**_record("D002", "2025-06-08", 0), "cases": None},
    ]
    previous = build_country_data("ZZ", "Test", before, catalogue)
    attach_inputs(previous, before)
    current = build_country_data("ZZ", "Test", changed, catalogue)
    all_rows = sorted([row for row in before if row["disease_id"] != "D002"] + changed,
                      key=lambda row: (row["date"], row["disease_id"]))
    complete = build_country_data("ZZ", "Test", all_rows, catalogue)
    attach_inputs(complete, all_rows)

    merged = merge_country_diseases(previous, current, {"D002"}, changed)

    assert merged == complete
    assert build_country_site_data(merged) == build_country_site_data(complete)
    summaries = merged[INPUT_KEY]["diseases"]
    assert summaries["D001"]["monthly_cases"] == {"2025-01": 0, "2025-02": None, "2025-04": 4}
    assert summaries["D002"]["monthly_cases"] == {"2025-03": 9, "2025-05": None, "2025-06": 0}
    assert merged["heatmap"]["months"] == ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"]


def test_legacy_zero_filled_incremental_baseline_requires_full_projection():
    records = [_record("D001", "2025-01-01", 0)]
    country = build_country_data("ZZ", "Test", records, {"D001": {"name_en": "Test"}})
    attach_inputs(country, records)
    marker = {"scope": "ZZ", "change_set": {"version": 1, "precise": True, "changes": [
        {"series_code": "SER_D001", "geography_key": "country:ZZ:national", "operation": "upsert"},
    ]}}
    assert eligible_inputs(country)
    assert precise_disease_scope("ZZ", [marker], country, {"D001"}) == {"D001"}

    country[INPUT_KEY]["schema_version"] = 1

    assert not eligible_inputs(country)
    assert precise_disease_scope("ZZ", [marker], country, {"D001"}) is None
    with pytest.raises(ValueError, match="complete input-summary baseline"):
        merge_country_diseases(country, {}, {"D001"}, records)
