from pathlib import Path

from src.data.processors.nz import NZMonthlyUpdater, _registry_series_code
from src.ontology import load_disease_ontology


def test_nz_registry_contains_complete_reviewed_series_inventory() -> None:
    registry = load_disease_ontology()
    series = [
        item
        for item in registry.to_dict()["source_series"]
        if item["source_id"] == "SRC_NZ_PHS"
    ]

    assert len(series) == 36
    assert len({item["id"] for item in series}) == 36
    assert len({item["concept_id"] for item in series}) == 36


def test_nz_source_labels_receive_stable_registry_codes(tmp_path: Path) -> None:
    csv_path = tmp_path / "nz.csv"
    csv_path.write_text(
        "Disease,Year,Month,Date,Cases,Source\n"
        "Pertussis,2026,6,2026-06-01,164,NZ PHF Science Monthly Notifiable Disease Surveillance\n",
        encoding="utf-8",
    )

    rows = NZMonthlyUpdater(output_csv=csv_path)._load_rows(csv_path)

    assert rows[0]["SourceDiseaseCode"] == "SER_NZ_PERTUSSIS_MONTHLY"
    assert _registry_series_code("Haemophilus influenzae type b") == (
        "SER_NZ_HAEMOPHILUS_INFLUENZAE_TYPE_B_MONTHLY"
    )
    assert _registry_series_code("not a registered NZ category") == ""
