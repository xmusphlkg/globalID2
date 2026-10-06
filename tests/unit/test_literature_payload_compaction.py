from types import SimpleNamespace

from scripts.backfill_pubmed_abstracts import _apply_pubmed_payload
from src.domain import LiteratureArticle
from src.literature.payloads import compact_source_payload
from src.literature.repository import _classification_metadata
from src.literature.types import Classification, Match
from src.services.literature_automation_service import _has_explicit_correction_parent


def test_source_payload_compaction_is_bounded_idempotent_and_preserves_evidence():
    payload = {
        "DOI": "10.1000/correction",
        "abstract": "large raw abstract " * 10_000,
        "title": ["large provider title"],
        "update-to": [
            {
                "type": "correction",
                "DOI": "10.1000/parent",
                "unused": "must not persist",
            }
        ],
        "europe_pmc": {
            "pmid": "123",
            "abstractText": "duplicate abstract " * 5_000,
            "meshHeadingList": {
                "meshHeading": [
                    {"descriptorName": "Dengue", "majorTopic_YN": "Y"}
                ]
            },
            "keywordList": {"keyword": ["surveillance"]},
            "pubTypeList": {"pubType": ["Journal Article"]},
        },
        "openalex": {
            "id": "W123",
            "topics": [{"display_name": "Dengue", "score": 0.99}],
            "institutions": [{"id": "I123", "country_code": "BR"}],
        },
        "unpaywall": {
            "doi": "10.1000/correction",
            "is_oa": True,
            "best_oa_location": {"url": "https://example.org/article"},
        },
    }

    compacted = compact_source_payload(payload)

    assert compacted == compact_source_payload(compacted)
    assert "abstract" not in compacted
    assert "title" not in compacted
    assert "abstractText" not in compacted["europe_pmc"]
    assert compacted["europe_pmc"]["meshHeadingList"] == {
        "meshHeading": [{"descriptorName": "Dengue", "majorTopic_YN": "Y"}]
    }
    assert compacted["openalex"]["topics"][0]["display_name"] == "Dengue"
    assert compacted["unpaywall"]["is_oa"] is True

    article = SimpleNamespace(
        title="Correction to: dengue surveillance evidence",
        doi="10.1000/correction",
        source_payload=compacted,
    )
    assert _has_explicit_correction_parent(article) is True


def test_classification_fingerprint_avoids_churning_unchanged_metadata():
    classification = Classification(
        diseases=[Match("DENG", "Dengue", 0.95, ["dengue"])],
        discovery_score=0.8,
    )

    initial = _classification_metadata({}, classification)
    unchanged = _classification_metadata(initial, classification)
    changed = _classification_metadata(
        initial,
        Classification(
            diseases=[Match("DENG", "Dengue", 0.96, ["dengue"])],
            discovery_score=0.8,
        ),
    )

    assert unchanged is initial
    assert changed["classification_fingerprint"] != initial["classification_fingerprint"]


def test_pubmed_abstract_backfill_does_not_reintroduce_raw_payload():
    article = LiteratureArticle(
        article_id="lit-pubmed",
        slug="pubmed-article",
        title="PubMed article",
        pmid="123",
        abstract_text="short",
        source_payload={},
        source_payload_version=0,
    )

    assert _apply_pubmed_payload(article, {
        "pmid": "123",
        "abstractText": "A substantially longer abstract used by the article.",
        "raw_xml": "must not persist" * 1_000,
    }) is True
    assert article.abstract_text.startswith("A substantially longer")
    assert article.source_payload == {"pubmed_efetch": {"pmid": "123"}}
    assert article.source_payload_version == 1
    assert article.source_payload_compacted_at is not None
