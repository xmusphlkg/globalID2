from src.literature.recommendations import attach_related_research


def _article(article_id, *, disease="D021", country="JP", topic="Surveillance", study="Cohort study", date="2026-01-01"):
    return {
        "article_id": article_id,
        "slug": article_id,
        "title": article_id,
        "journal": "Test Journal",
        "published_at": f"{date}T00:00:00+00:00",
        "study_type": study,
        "peer_review_status": "peer_reviewed",
        "diseases": [{"disease_id": disease, "name_en": disease}] if disease else [],
        "countries": [{"code": country}] if country else [],
        "topics": [{"name": topic}] if topic else [],
    }


def test_related_research_is_ranked_stably_and_excludes_self():
    result = attach_related_research([
        _article("source"),
        _article("strong", date="2026-02-01"),
        _article("topic-only", disease="D999", country="US", study="Guideline"),
        _article("unrelated", disease="D050", country="CD", topic="Treatment"),
    ])
    source = result[0]
    assert [item["article_id"] for item in source["related_articles"]] == ["strong", "topic-only"]
    assert source["related_articles"][0]["similarity_score"] > source["related_articles"][1]["similarity_score"]
    assert source["related_articles"][0]["reasons_en"] == [
        "Shared disease: D021", "Shared geography: JP", "Shared topic: Surveillance", "Same study type: Cohort study",
    ]
    assert all(item["article_id"] != "source" for item in source["related_articles"])


def test_geography_or_study_design_alone_never_recommends_an_unrelated_article():
    result = attach_related_research([
        _article("source", disease="D021", topic="Surveillance"),
        _article("same-place-only", disease="D050", topic="Treatment"),
    ])
    assert result[0]["related_articles"] == []


def test_related_research_projection_never_exports_private_source_fields():
    private = _article("private")
    private["abstract_text"] = "not public"
    private["source_payload"] = {"secret": True}
    result = attach_related_research([_article("source"), private])
    projection = result[0]["related_articles"][0]
    assert "abstract_text" not in projection
    assert "source_payload" not in projection
