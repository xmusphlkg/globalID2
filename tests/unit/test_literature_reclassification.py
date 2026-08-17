from datetime import datetime
from types import SimpleNamespace

from src.literature.reclassification import _automatic_relation, candidate_from_stored_article


def test_automatic_signal_relation_upgrades_when_reclassification_adds_signal_country():
    relation, confidence, reasons = _automatic_relation(
        disease_confidence=0.98,
        country_confidences={"CD": 0.94},
        signal_country_codes=["CD", "UG"],
        disease_min_confidence=0.82,
        exact_min_confidence=0.78,
        context_min_confidence=0.82,
    )

    assert relation == "exact_disease_geography"
    assert confidence == 0.94
    assert any("country:CD" in reason for reason in reasons)


def test_stored_article_candidate_preserves_source_evidence_and_normalizes_utc():
    article = SimpleNamespace(
        article_id="lit-1", slug="article", title="Ebola in DRC", doi="10.1/x",
        pmid="1", pmcid="PMC1", openalex_id="W1", journal="Journal", issn=["1234-5678"],
        publisher="Publisher", authors=[{"name": "A"}], article_type="journal-article",
        study_type="Commentary", published_at=datetime(2026, 8, 1), indexed_at=datetime(2026, 8, 2),
        abstract_text="Democratic Republic of the Congo", abstract_license=None,
        source_urls={"doi": "https://doi.org/10.1/x"}, open_access_status="open",
        open_access_url="https://example.org/open", license_url=None,
        peer_review_status="peer_reviewed", integrity_status="current",
        source_payload={"openalex": {"id": "W1"}},
    )

    candidate = candidate_from_stored_article(article)

    assert candidate.published_at.tzinfo is not None
    assert candidate.indexed_at.tzinfo is not None
    assert candidate.source_payload == {"openalex": {"id": "W1"}}
