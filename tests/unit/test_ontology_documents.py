"""Source document composition preserves contracts and editor ownership."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.ontology import (
    DiseaseOntology,
    OntologyValidationError,
    ontology_document_paths,
    read_ontology_document,
    write_ontology_document,
)


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def split_document(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "ontology.json"
    source = tmp_path / "sources/mx.json"
    _save(root, {
        "schema_version": 1, "description": "Shared metadata",
        "sources": [{"id": "SRC_CORE"}],
        "source_series": [{"id": "SER_CORE", "source_id": "SRC_CORE"}],
        "availability": [{"id": "AV_CORE", "source_id": "SRC_CORE"}],
        "source_documents": ["sources/mx.json"],
    })
    _save(source, {
        "schema_version": 1, "review_note": "Retain review provenance",
        "sources": [{"id": "SRC_MX"}],
        "source_series": [{"id": "SER_MX", "source_id": "SRC_MX", "local_labels": ["Dengue"]}],
        "availability": [{"id": "AV_MX", "source_id": "SRC_MX"}],
    })
    return root, source


def test_source_documents_load_in_manifest_order(split_document):
    root, source = split_document
    merged = read_ontology_document(root)
    assert "source_documents" not in merged
    assert [row["id"] for row in merged["source_series"]] == ["SER_CORE", "SER_MX"]
    assert merged["description"] == "Shared metadata"
    assert "review_note" not in merged
    assert ontology_document_paths(root) == (root, source)


def test_editor_retains_source_ownership_and_review_metadata(split_document):
    root, source = split_document
    merged = read_ontology_document(root)
    merged["source_series"][1]["local_labels"] = ["Dengue fever"]
    merged["sources"].append({"id": "SRC_NEW"})
    merged["source_series"].append({"id": "SER_NEW", "source_id": "SRC_NEW"})
    write_ontology_document(root, merged)
    saved = read_ontology_document(root)
    for section in ("sources", "source_series", "availability"):
        assert {row["id"]: row for row in saved[section]} == {row["id"]: row for row in merged[section]}
    saved_source = json.loads(source.read_text())
    saved_root = json.loads(root.read_text())
    assert saved_source["review_note"] == "Retain review provenance"
    assert saved_source["source_series"][0]["local_labels"] == ["Dengue fever"]
    assert [row["id"] for row in saved_root["sources"]] == ["SRC_CORE", "SRC_NEW"]
    assert saved_root["source_documents"] == ["sources/mx.json"]


@pytest.mark.parametrize("name", ["../outside.json", "/tmp/outside.json", "ontology.json", "sources/mx.txt"])
def test_source_document_paths_reject_escape_and_recursion(split_document, name):
    root, _ = split_document
    data = json.loads(root.read_text())
    data["source_documents"] = [name]
    _save(root, data)
    with pytest.raises(OntologyValidationError):
        read_ontology_document(root)


def test_duplicate_and_nested_source_documents_fail_closed(split_document):
    root, source = split_document
    data = json.loads(source.read_text())
    data["source_series"][0]["id"] = "SER_CORE"
    _save(source, data)
    with pytest.raises(OntologyValidationError, match="duplicate source_series"):
        read_ontology_document(root)
    data["source_documents"] = []
    _save(source, data)
    with pytest.raises(OntologyValidationError, match="nested"):
        read_ontology_document(root)


def test_shard_cannot_claim_another_documents_source(split_document):
    root, source = split_document
    data = json.loads(source.read_text())
    data["source_series"][0]["source_id"] = "SRC_CORE"
    _save(source, data)
    with pytest.raises(OntologyValidationError, match="owned elsewhere"):
        read_ontology_document(root)


def test_checked_in_shard_roundtrip_preserves_validated_registry(tmp_path):
    root = Path(__file__).resolve().parents[2] / "configs/disease_ontology.json"
    paths = ontology_document_paths(root)
    assert len(paths) > 1
    complete = read_ontology_document(root)
    DiseaseOntology.from_dict(complete)
    assert all(path.stat().st_size < 5 * 1024 * 1024 for path in paths)
    assert len([row for row in complete["source_series"] if row["source_id"].startswith("SRC_MX_DGE_")]) == 1259
    for path in paths:
        destination = tmp_path / path.relative_to(root.parent)
        _save(destination, json.loads(path.read_text()))
    output = tmp_path / root.name
    write_ontology_document(output, deepcopy(complete))
    assert read_ontology_document(output) == complete


def test_preparing_independent_patch_does_not_replace_active_contracts(tmp_path):
    root = Path(__file__).resolve().parents[2] / "configs/disease_ontology.json"
    for path in ontology_document_paths(root):
        _save(tmp_path / path.relative_to(root.parent), json.loads(path.read_text()))
    output = tmp_path / root.name
    before = read_ontology_document(output)
    _save(tmp_path / "high_frequency_mexico_ontology.json", {"source_series": []})
    assert read_ontology_document(output) == before
