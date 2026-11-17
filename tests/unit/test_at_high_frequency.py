"""AGES actual monthly source facts, holding identity and publication boundary."""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from src.data.crawlers.at import AGESIssue, ATAGESContractError, parse_ages_csv
from src.data.processors.at import ATMonthlyUpdater
from src.services.at_high_frequency_review import reviewed_at_high_frequency_record

FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/AT/ages-live-202609.csv"


def actual_rows():
    meta = json.loads(FIXTURE.with_suffix(".json").read_text())
    payload = FIXTURE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == meta["sha256"]
    return parse_ages_csv(payload, issue=AGESIssue(meta["metadata_"]["detail_url"], meta["source_url"],
                          date(2026, 9, 1), datetime(2026, 11, 17, tzinfo=timezone.utc)))


def record():
    row = next(r for r in actual_rows() if r["SourceCategoryCode"] == "masern")
    return {"date": row["Date"], "period_start": row["PeriodStart"], "period_end_exclusive": "2026-10-01",
            "source_system": "SRC_AT_AGES_RADAR", "series_code": "SER_AT_AGES_RADAR_SOURCE_NATIVE", "reviewed_source_code": "masern",
            "reviewed_source_label": row["RawDiseaseLabel"], "disease_id": "D017",
            "definition_version": row["DefinitionVersion"], "metric_type": row["Measure"],
            "series_unit": row["Unit"], "observation_unit": row["Unit"],
            "reporting_basis": row["ReportingBasis"], "temporal_granularity": row["Frequency"],
            "geography_key": row["GeographyKey"], "mapping_relation": "exact",
            "comparability": "conditional", "aggregation_policy": "direct_only",
            "dimensions": json.loads(row["Dimensions"])}


def test_actual_ages_monthly_contract_preserves_existing_holding_identity():
    rows = actual_rows()
    assert len(rows) == 63
    assert {r["Frequency"] for r in rows} == {"monthly"}
    assert {r["Measure"] for r in rows} == {"case_notifications"}
    assert {r["DefinitionVersion"] for r in rows} == {"AT_AGES_RADAR_source_native"}
    assert {r["SourceDiseaseCode"] for r in rows} == {"__source_native__"}
    assert {r["SourceContractVersion"] for r in rows} == {"at_ages_radar_calendar_month_notifications_v1"}
    assert {r["DiseaseCode"] for r in rows} == {"__source_native__"}
    assert {r["PeriodEnd"] for r in rows} == {"2026-09-30"}
    assert all(r["MissingValueStatus"] == "reported" for r in rows)
    updater = ATMonthlyUpdater()
    assert updater.public_release_enabled and not updater.mapping_notifications_enabled
    source_record = record()
    assert source_record["disease_id"] == "D017"
    result = reviewed_at_high_frequency_record(source_record, "AT")
    assert result is not None
    assert result["dimensions"] == source_record["dimensions"]
    assert result["projection_dimension_key"] == "all"
    assert "projection_dimension_key" not in source_record


def test_ages_explicit_missing_zero_and_cumulative_columns_do_not_mix():
    issue = AGESIssue("https://example.test/issue", "https://example.test/issue.csv",
                      date(2026, 2, 1), datetime(2026, 3, 1, tzinfo=timezone.utc))
    payload = b'Krankheit;Feb 2026;Jan - Feb 2026\nMasern;-;999\nBotulismus;0;7\n'
    rows = {r["SourceCategoryCode"]: r for r in parse_ages_csv(payload, issue=issue)}
    assert rows["masern"]["Cases"] == "" and rows["masern"]["MissingValueStatus"] == "source_missing"
    assert rows["botulismus"]["Cases"] == "0" and rows["botulismus"]["MissingValueStatus"] == "reported"
    with pytest.raises(ATAGESContractError, match="disagrees"):
        parse_ages_csv(payload, issue=AGESIssue(issue.detail_url, issue.csv_url, date(2026, 1, 1), issue.retrieved_at))


@pytest.mark.parametrize("changes", [
    {"geography_key": "country:AT:province:Vienna"}, {"metric_type": "laboratory_detections"},
    {"observation_unit": "percent"}, {"definition_version": "unknown"},
    {"period_end_exclusive": "2026-09-30"}, {"date": "2026-09-02"},
    {"dimensions": {"source_disease_code": "masern", "source_disease_label": "Masern", "age": "0-4"}},
    {"reviewed_source_label": "Masern, 0-4"}, {"disease_id": "D038"},
    {"series_code": "HOLD_AT_anything"},
])
def test_ages_unsafe_grain_unit_calendar_and_category_cannot_project(changes):
    row = record()
    row.update(changes)
    assert reviewed_at_high_frequency_record(row, "AT") is None
