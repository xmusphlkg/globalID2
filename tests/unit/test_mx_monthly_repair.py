"""Real female DGE totals and contradictory original ICD labels stay distinct."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from src.data.crawlers import mx_high_frequency as crawler
from src.data.crawlers.mx_high_frequency import parse_monthly_pdf
from src.services.mx_high_frequency_review import mx_source_label_icd_conflict


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/ingestion/MX/high_frequency"
REFERENCE = {
    "year": 2025, "sex": "female", "edition_path": "20250",
    "url": "https://epidemiologia.salud.gob.mx/anuario/20250/morbilidad/nacional/distribucion_casos_nuevos_enfermedad_mes.pdf",
}


@pytest.fixture(scope="module")
def female():
    return parse_monthly_pdf((FIXTURES / "dge-monthly-2025-female.pdf").read_bytes(), reference=REFERENCE)


def test_original_female_unlabelled_row_is_a_verified_total_not_a_missing_disease(female):
    rows, audit = female
    provenance = json.loads((FIXTURES / "monthly-repair-provenance.json").read_text())
    assert hashlib.sha256((FIXTURES / provenance["fixture"]).read_bytes()).hexdigest() == provenance["sha256"]
    assert len(rows) == 2232
    assert len({r["SourceDiseaseCode"] for r in rows}) == 186
    assert audit["status"] == "parsed" and audit["failed_count"] == 0
    summary, = audit["excluded_summary_rows"]
    assert summary["page"] == 4 and summary["verified_columns"] == 13
    assert summary["annual_total"] == 20856352
    assert summary["monthly_totals"] == [2140171, 1902539, 1830727, 1947735,
        1548054, 1522828, 1773142, 1321764, 1491593, 2010075, 1571049, 1796675]
    assert not any(r["Cases"] == "20856352" for r in rows)
    influenza = [r for r in rows if r["RawDiseaseLabel"] == "Influenza"]
    assert len(influenza) == 12
    assert all(r["Sex"] == "female" and r["SourceICDCode"] for r in rows)


def test_unlabelled_final_numeric_row_is_not_silently_ignored_when_totals_disagree(monkeypatch):
    original_number = crawler._number
    def corrupt_total(raw, **options):
        value, missing = original_number(raw, **options)
        return ("20856353", missing) if value == "20856352" else (value, missing)
    monkeypatch.setattr(crawler, "_number", corrupt_total)
    rows, audit = parse_monthly_pdf((FIXTURES / "dge-monthly-2025-female.pdf").read_bytes(), reference=REFERENCE)
    assert len(rows) == 2232
    assert audit["excluded_summary_rows"] == []
    assert audit["status"] == "partial"
    assert any(f["page"] == 4 and "label/ICD missing" in f["error"] for f in audit["failures"])


@pytest.mark.parametrize("label,original,expected", [
    ("Fiebre amarilla", "A92.2", "A95"),
    ("Encefalitis equina venezolana", "A95", "A92.2"),
    (" Fiebre   amarilla ", "A92.2", "A95"),
])
def test_original_icd_conflicts_are_explicit_and_never_rewritten(label, original, expected):
    conflict = mx_source_label_icd_conflict(label, original)
    assert conflict["source_icd_code"] == original
    assert conflict["expected_icd_code"] == expected
    assert conflict["policy"] == "retain_original_source_observations_without_canonical_projection"
    assert mx_source_label_icd_conflict(label, expected) is None


def test_registry_cannot_override_an_unresolved_original_code_conflict(tmp_path, monkeypatch, female):
    spec = importlib.util.spec_from_file_location("mx_registry_repair_test", ROOT / "scripts/prepare_mx_high_frequency_registry.py")
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    config = tmp_path / "configs"
    (config / "mapping/reviewed").mkdir(parents=True)
    (config / "mapping/reviewed/mx.csv").write_text(
        "definition_version,local_code,target_kind,mapping_relation,projection_policy,local_name,target_code\n"
    )
    (config / "mx_high_frequency_concept_review.json").write_text(json.dumps({"rules": [{
        "source_labels": ["Fiebre amarilla"], "source_icd_codes": ["A92.2"],
        "target_code": "D048", "mapping_relation": "exact", "review_basis": "incorrect label-only override",
    }]}))
    base = female[0][0]
    conflict = {**base, "RawDiseaseLabel": "Fiebre amarilla", "SourceICDCode": "A92.2",
        "SourceDiseaseCode": "dge_4168a47860f488ecd00b__all__case_notifications", "Sex": "all",
        "Frequency": "weekly", "DefinitionVersion": "mx_dge_weekly_special_confirmed_surveillance_v1",
        "ReportingBasis": "special_confirmed_surveillance"}
    for scope, row in [("mx_dge_monthly", base), ("mx_dge_weekly", conflict)]:
        directory = tmp_path / "data/current/mx" / scope
        directory.mkdir(parents=True)
        with (directory / "observations.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    registry.main()
    result = json.loads((config / "high_frequency_mexico_reviewed_mappings.json").read_text())
    mapped = next(r for r in result["rows"] if r["source_id"] == "SRC_MX_DGE_WEEKLY")
    assert mapped["target_kind"] == "group" and mapped["projection_policy"] == "no_projection"
    assert mapped["mapping_relation"] == "unmapped"
    assert "Unresolved original source label/ICD conflict" in mapped["notes"]
