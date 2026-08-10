from src.knowledge.surveillance_note_overrides import apply_surveillance_note_override


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
