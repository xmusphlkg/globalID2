"""Publication validates source meaning and preserves overlapping lab strata."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import pytest

from src.services.europe_high_frequency_review import _contracts, reviewed_europe_high_frequency_record


def record(series):
    monthly = series["frequency"] == "monthly"
    return {"date": "2026-10-01" if monthly else "2026-11-02", "period_start": "2026-10-01" if monthly else "2026-11-02",
        "period_end_exclusive": "2026-11-01" if monthly else "2026-11-09", "source_series_code": series["id"],
        "source_system": series["source_id"], "source_label": series["local_labels"][0], "disease_id": series.get("concept_id"),
        "definition_version": series["definition_version"], "metric_type": series["measure"], "reporting_basis": series["reporting_basis"],
        "temporal_granularity": series["frequency"], "series_unit": series["unit"], "observation_unit": series["unit"],
        "mapping_relation": series["mapping_relation"], "comparability": series["comparability"], "aggregation_policy": series["aggregation_policy"],
        "geography_key": "country:" + series["source_id"].split("_")[1] + ":national", "dimensions": {}}


def test_monthly_direct_total_requires_exact_contract_and_interval():
    series = next(s for s in _contracts().values() if s["source_id"] == "SRC_CZ_SZU_MONTHLY" and s.get("concept_id") == "D114")
    row = record(series)
    accepted = reviewed_europe_high_frequency_record(row, "CZ")
    assert accepted["projection_dimension_key"] == "all" and accepted["dimensions"] == {}
    for change in [{"metric_type": "cumulative_cases"}, {"observation_unit": "percent"}, {"dimensions": {"sex": "male"}},
                   {"period_end_exclusive": "2026-10-31"}, {"definition_version": "unreviewed"}, {"geography_key": "country:CZ:Prague"},
                   {"reviewed_source_label": "Other disease"}]:
        assert reviewed_europe_high_frequency_record(dict(row, **change), "CZ") is None
    unmapped = next(s for s in _contracts().values() if s["source_id"] == "SRC_CZ_SZU_MONTHLY" and "group_id" in s)
    assert reviewed_europe_high_frequency_record(record(unmapped), "CZ") is None


def test_lab_observations_never_gain_patient_projection_or_lose_dimensions():
    from src.data.crawlers.ecdc_respiratory import parse_csv
    path = Path(__file__).parents[1] / "fixtures/ingestion/EU_HIGH_FREQUENCY/nonSentinelTestsDetections.csv"
    parsed, _ = parse_csv(path.read_bytes(), country_code="AT", dataset="data/nonSentinelTestsDetections.csv", as_of=date(2026, 11, 17))
    sample = parsed[0]
    series = next(s for s in _contracts().values() if s["source_id"] == "SRC_AT_ECDC_RESPIRATORY" and s["local_codes"] == [sample["SourceDiseaseCode"]])
    row = record(series); row["dimensions"] = json.loads(sample["Dimensions"])
    row.update(projection_dimension_key="all", projection_metric_type="case_notifications")
    accepted = reviewed_europe_high_frequency_record(row, "AT")
    assert accepted["dimensions"] == row["dimensions"]
    assert accepted["projection_policy"] == "source_observations_only"
    assert "projection_dimension_key" not in accepted and "projection_metric_type" not in accepted
    changed = deepcopy(row); changed["dimensions"]["age"] = "new age"
    assert reviewed_europe_high_frequency_record(changed, "AT") is None
    assert reviewed_europe_high_frequency_record(dict(row, metric_type="case_notifications"), "AT") is None


def test_independent_patch_loads_with_existing_ontology_and_mapping_csvs(tmp_path):
    import csv
    from src.ontology.disease import DiseaseOntology
    from src.services.disease_mapping_config import load_reviewed_source_category_mappings
    root = Path(__file__).resolve().parents[2]
    from src.ontology import read_ontology_document
    original = read_ontology_document(root / "configs/disease_ontology.json")
    patch = json.loads((root / "configs/high_frequency_europe_ontology.json").read_text())
    for key, items in patch.items():
        index = {r["id"]: r for r in original[key]}
        index.update({r["id"]: r for r in items})
        original[key] = list(index.values())
    registry = DiseaseOntology.from_dict(original)
    active = {c["id"] for c in original["concepts"] if c["status"] == "active"}
    assert all(s["concept_id"] in active for s in patch["source_series"] if "concept_id" in s)
    rubella = next(s for s in patch["source_series"] if s["source_id"] == "SRC_EE_TERVISEAMET_MONTHLY" and s["local_labels"] == ["Punetised"])
    assert rubella.get("group_id") == "G_RUBELLA_SPECTRUM" and rubella["mapping_relation"] == "aggregate"
    mappings = json.loads((root / "configs/high_frequency_europe_reviewed_mappings.json").read_text())
    for path, rows in mappings.items():
        target = tmp_path / Path(path).name
        with target.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    reviewed = load_reviewed_source_category_mappings(mapping_dir=tmp_path, ontology=registry)
    assert len(reviewed) == len(patch["source_series"]) == 1498
    assert all(m.projection_policy == "canonical" for m in reviewed if m.source_id.endswith("_ECDC_RESPIRATORY"))
    assert all(s["projection_policy"] == "source_observations_only" for s in patch["source_series"] if s["source_id"].endswith("_ECDC_RESPIRATORY"))
