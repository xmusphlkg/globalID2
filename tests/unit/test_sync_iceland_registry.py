from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from scripts.sync_iceland_registry import (
    CURRENT_SOURCE_IDS,
    _iceland_mapping_rows,
    _insert_or_merge_diseases,
    _insert_or_merge_standard_diseases,
    _validate_current_mapping_coverage,
)


ROOT = Path(__file__).resolve().parents[2]


class _CaptureDB:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):
        self.statement = statement


def _compiled(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_scoped_sync_preserves_shared_disease_enrichment() -> None:
    db = _CaptureDB()
    await _insert_or_merge_diseases(
        db,
        [
            {
                "name": "D236",
                "name_en": "MRSA surveillance",
                "category": "Antimicrobial resistance",
                "icd_10": None,
                "icd_11": None,
                "aliases": [],
                "keywords": [],
                "description": "Scoped bootstrap description",
                "metadata": {"ontology_status": "active"},
                "is_active": True,
            }
        ],
    )

    sql = _compiled(db.statement)
    assert "aliases = diseases.aliases" in sql
    assert "keywords = diseases.keywords" in sql
    assert "is_active = diseases.is_active" in sql
    # Incoming metadata is on the left of jsonb concatenation, so an existing
    # shared key on the right wins while missing scoped keys are added.
    assert "excluded.metadata" in sql
    assert "diseases.metadata" in sql
    assert sql.index("excluded.metadata") < sql.index("diseases.metadata")


@pytest.mark.asyncio
async def test_scoped_sync_preserves_existing_standard_definition() -> None:
    db = _CaptureDB()
    await _insert_or_merge_standard_diseases(
        db,
        [
            {
                "disease_id": "D236",
                "standard_name_en": "MRSA surveillance",
                "standard_name_zh": None,
                "category": "Antimicrobial resistance",
                "icd_10": None,
                "icd_11": None,
                "description": "Scoped bootstrap description",
                "source": "Manual",
                "metadata": {"ontology_status": "active"},
                "is_active": True,
            }
        ],
    )

    sql = _compiled(db.statement)
    assert "standard_name_en = standard_diseases.standard_name_en" in sql
    assert "source = standard_diseases.source" in sql
    assert "is_active = standard_diseases.is_active" in sql


def _current_series_rows() -> list[dict[str, str]]:
    ontology = json.loads(
        (ROOT / "configs" / "disease_ontology.json").read_text(encoding="utf-8")
    )
    return [
        {
            "series_code": row["id"],
            "source_system": row["source_id"],
            "source_series_code": row["id"],
            "disease_id": row["concept_id"],
            "metadata": {"local_codes": row["local_codes"]},
        }
        for row in ontology["source_series"]
        if row.get("source_id") in CURRENT_SOURCE_IDS
    ]


def test_scoped_sync_contains_every_fresh_db_current_compatibility_mapping() -> None:
    series_rows = _current_series_rows()
    mappings = _iceland_mapping_rows()

    assert len(series_rows) == 22
    _validate_current_mapping_coverage(series_rows, mappings)

    removed = [
        row
        for row in mappings
        if row["series_id"] != series_rows[0]["series_code"]
    ]
    with pytest.raises(ValueError, match=series_rows[0]["series_code"]):
        _validate_current_mapping_coverage(series_rows, removed)
