from src.generation.site_data_literature import bibliographic_catalogue_records
from src.literature.release_validation import validate_public_research_payload


def test_bibliographic_tier_retains_closed_access_without_private_content():
    article = {
        "article_id": "a", "title": "Disease study", "doi": "10.1000/example",
        "editorial_status": "published", "peer_review_status": "peer_reviewed",
        "integrity_status": "current", "open_access_status": "closed",
        "indexable": False, "research_domain": "human_health",
        "abstract_text": "private", "source_payload": {"private": True},
    }
    records = bibliographic_catalogue_records([article])
    assert len(records) == 1
    assert records[0]["detail_available"] is False
    assert records[0]["open_access_status"] == "closed"
    assert "abstract_text" not in records[0] and "source_payload" not in records[0]
    assert validate_public_research_payload({"bibliographic_articles": records}) == []
    for update in ({"integrity_status": "retracted"}, {"editorial_status": "review"},
                   {"peer_review_status": "preprint"}, {"indexable": True}):
        assert bibliographic_catalogue_records([{**article, **update}]) == []


def test_validator_rejects_private_or_unapproved_bibliographic_records():
    blockers = validate_public_research_payload({"bibliographic_articles": [{
        "article_id": "a", "abstract_text": "private", "editorial_status": "review",
        "peer_review_status": "peer_reviewed", "integrity_status": "retracted",
    }]})
    assert len(blockers) == 4
