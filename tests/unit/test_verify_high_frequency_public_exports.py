"""Pure acceptance checks for the read-only high-frequency export verifier."""
import json
from pathlib import Path

from scripts.verify_high_frequency_public_exports import (
    compare_points, coverage, expected_points, load_contracts, public_source_points,
)


def test_point_comparison_is_dimension_and_metric_sensitive():
    expected = [{"source_system": "SRC_AT_AGES_RADAR", "series_code": "SER_AT_AGES_RADAR_SOURCE_NATIVE",
                 "disease_id": "D017", "metric_type": "case_notifications", "temporal_granularity": "monthly",
                 "geography_key": "country:AT:national", "unit": "count", "date": "2026-09-01",
                 "dimensions": {"source_disease_code": "masern"}, "cases": 3}]
    assert compare_points(expected, [dict(expected[0])])["pass"]
    changed = dict(expected[0], dimensions={"source_disease_code": "measles"})
    assert compare_points(expected, [changed])["missing_count"] == 1
    changed = dict(expected[0], cases=4)
    assert compare_points(expected, [changed])["mismatch_count"] == 1


def test_contracts_load_static_and_review_mapping_patches():
    series, mappings, hashes = load_contracts()
    assert series["SER_AT_AGES_RADAR_SOURCE_NATIVE"]["frequency"] == "monthly"
    assert mappings["AT", "SRC_AT_AGES_RADAR", "masern", "AT_AGES_RADAR_source_native"]["target_code"] == "D017"
    assert "configs/ontology/sources/mx_dge_high_frequency.json" in hashes


def test_expected_ages_points_use_native_static_identity_and_keep_coverage_gaps():
    rows = [{"source_system": "SRC_AT_AGES_RADAR", "series_code": "SER_AT_AGES_RADAR_SOURCE_NATIVE",
             "definition_version": "AT_AGES_RADAR_source_native", "metric_type": "case_notifications",
             "reporting_basis": "national_notifiable_disease_radar", "temporal_granularity": "monthly",
             "unit": "count", "observation_unit": "count", "geography_key": "country:AT:national",
             "time": "2026-09-01", "value": 3, "dimensions": {"source_disease_code": "masern",
             "source_disease_label": "Masern"}, "raw_data": {"SourceDiseaseCode": "__source_native__",
             "Dimensions": json.dumps({"source_disease_code": "masern", "source_disease_label": "Masern"})},
             "suppressed": False, "quality_status": "final"}]
    series, mappings, _ = load_contracts()
    expected, excluded, errors = expected_points("AT", rows, series, mappings)
    assert len(expected) == 1 and not errors
    assert not excluded
    report = coverage(rows)
    assert report["numeric_observations"] == 1
    assert "Only observed source periods" in report["coverage_note"]


def test_public_source_points_preserves_point_dimensions():
    payload = {"series": {"D017": [{"source_system": "SRC_AT_AGES_RADAR", "series_code": "SER_AT_AGES_RADAR_SOURCE_NATIVE",
                                      "dates": ["2026-09-01"], "values": [3],
                                      "point_source_dimensions": [{"source_disease_code": "masern"}]}]}}
    points = public_source_points(payload, {"SRC_AT_AGES_RADAR"})
    assert points[0]["dimensions"] == {"source_disease_code": "masern"}
