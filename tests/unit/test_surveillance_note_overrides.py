import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.surveillance_note_overrides import apply_surveillance_note_override
from src.knowledge.citations import validate_knowledge_citations
from src.knowledge.quality import apply_knowledge_quality_gate


def test_reviewed_source_note_is_appended_and_idempotent() -> None:
    payload = {
        "disease_id": "D017",
        "language": "en",
        "surveillance_note": "Existing evidence-backed surveillance guidance [1].",
        "metadata": {"generator": "test"},
    }

    first = apply_surveillance_note_override(payload)
    second = apply_surveillance_note_override(first)

    assert first["surveillance_note"].startswith(payload["surveillance_note"])
    assert "Cinderella disease" in first["surveillance_note"]
    assert first["surveillance_note"] == second["surveillance_note"]
    assert first["metadata"]["source_data_note_review_version"]


def test_reviewed_source_note_deduplicates_whitespace_normalized_legacy_blocks() -> None:
    payload = {
        "disease_id": "D026",
        "language": "en",
        "surveillance_note": "Existing source-grounded interpretation [1].",
        "metadata": {},
    }
    overlaid = apply_surveillance_note_override(payload)
    flattened_legacy = {
        **overlaid,
        "surveillance_note": " ".join([overlaid["surveillance_note"]] * 4),
    }

    repaired = apply_surveillance_note_override(flattened_legacy)

    assert repaired["surveillance_note"].count("GlobalID source-data note:") == 1
    assert repaired["surveillance_note"].startswith(
        "Existing source-grounded interpretation [1]."
    )


def test_reviewed_source_note_uses_bilingual_text() -> None:
    result = apply_surveillance_note_override(
        {
            "disease_id": "D039",
            "language": "zh",
            "surveillance_note": "原有监测说明[1]。",
            "metadata": {},
        }
    )

    assert "GlobalID 来源数据说明" in result["surveillance_note"]
    assert "Sikotauti" in result["surveillance_note"]
    assert "猪瘟" in result["surveillance_note"]


def test_disease_without_override_is_unchanged() -> None:
    payload = {
        "disease_id": "D001",
        "language": "en",
        "surveillance_note": "Original note.",
        "metadata": {},
    }

    assert apply_surveillance_note_override(payload) == payload


def test_reviewed_source_note_refreshes_quality_and_citation_validation() -> None:
    payload = {
        "disease_id": "D017",
        "language": "en",
        "status": "published",
        "source_confidence": "high",
        "brief": "Cinderella disease is represented in public source data [1].",
        "definition": "The source defines Cinderella disease as a reportable entity [1].",
        "clinical_features": "Clinical features are described by the supporting source [1].",
        "epidemiology": "The disease has reportable source-data context [1].",
        "transmission": "The source describes transmission context for surveillance interpretation [1].",
        "prevention": "Prevention context is available in the supporting source [1].",
        "surveillance_note": None,
        "risk_groups": "The source identifies affected groups for interpretation [1].",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "citation_index": 1, "url": "https://example.org"}],
        "review_notes": (
            "AI-generated partial brief; unsupported fields were omitted and remain queued for enrichment.; "
            "missing required sections: surveillance_note"
        ),
        "metadata": {},
    }

    overlaid = apply_surveillance_note_override(payload)
    cleaned, assessment = apply_knowledge_quality_gate(overlaid)

    assert assessment.display_mode == "full"
    assert "surveillance_note" not in assessment.missing_required_fields
    assert "missing required sections: surveillance_note" not in cleaned["review_notes"]
    assert cleaned["review_notes"] == "AI-generated, source-grounded brief; ready for human spot review."
    assert validate_knowledge_citations(cleaned, fields=["surveillance_note"]) == []
