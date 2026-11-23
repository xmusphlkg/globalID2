"""Portal spelling changes preserve series; changed case scopes do not merge."""

import csv
from pathlib import Path

import pytest

from src.ontology import load_disease_ontology


@pytest.fixture(scope="module")
def registry():
    return load_disease_ontology()


@pytest.mark.parametrize("label,code,target", [
    ("Systemisk H. influenzae-sykdom", "103", "D100"),
    ("Tuberkulose - aktiv sykdom", "102", "D025"),
    ("Cryptosporidiose", "309", "D096"),
    ("Systemisk gruppe A streptokokksykdom (GAS)", "709", "D157"),
    ("Systemisk meningokokksykdom", "707", "D110"),
    ("Systemisk pneumokokksykdom", "708", "D106"),
    ("Hivinfeksjon", "902", "D162"),
    ("Lymfogranuloma venereum (LGV)", "910", "D242"),
])
def test_new_portal_label_resolves_to_existing_exact_source_series(registry, label, code, target):
    by_label = registry.series_lookup(source_id="SRC_NO_FHI_MSIS", local_label=label)
    by_old_code = registry.series_lookup(source_id="SRC_NO_FHI_MSIS", local_code=code)
    by_new_code = registry.series_lookup(source_id="SRC_NO_FHI_MSIS", local_code=label)
    assert len(by_label) == 1
    assert by_label == by_old_code == by_new_code
    assert by_label[0]["concept_id"] == target
    mapping = Path(__file__).resolve().parents[2] / "configs/mapping/no.csv"
    with mapping.open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["local_code"] == code)
    assert row["disease_id"] == target
    assert label in row["aliases"].split("|")


@pytest.mark.parametrize("label", [
    "Systemisk gruppe B streptokokksykdom (GBS)",
    "Tuberkulose - forebyggende behandling",
])
def test_source_native_aliases_still_have_no_clinical_projection(registry, label):
    matches = registry.series_lookup(source_id="SRC_NO_FHI_MSIS", local_label=label)
    assert len(matches) == 1
    assert matches[0]["mapping_relation"] == "unmapped"
    assert "concept_id" not in matches[0]
    assert matches[0]["rollup_policy"] == "no_auto_rollup"


@pytest.mark.parametrize("label", [
    "E. coli-enteritt (unntatt EHEC-infeksjon og HUS)",
    "EHEC-infeksjon og HUS",
    "Uspesifisert viral CNS-infeksjon",
    "Enterovirus",
])
def test_new_split_categories_never_resolve_to_the_old_aggregate(registry, label):
    assert registry.series_lookup(source_id="SRC_NO_FHI_MSIS", local_label=label) == []
    assert registry.series_lookup(source_id="SRC_NO_FHI_MSIS", local_code=label) == []
