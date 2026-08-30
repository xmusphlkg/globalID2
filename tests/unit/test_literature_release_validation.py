import pytest

from src.literature.release_validation import (
    assert_public_research_payload,
    validate_public_research_payload,
)


def _article(article_id="lit-1", *, peer_review_status="peer_reviewed"):
    return {
        "article_id": article_id,
        "slug": article_id,
        "title": article_id,
        "doi": f"10.1000/{article_id}",
        "published_at": "2026-08-01T00:00:00+00:00",
        "peer_review_status": peer_review_status,
        "editorial_status": "published",
        "integrity_status": "current",
        "indexable": True,
        "classification_version": 5,
        "research_domain": "human_health",
        "summary": {"en": {"main_findings": "A"}, "zh": {"main_findings": "甲"}},
        "related_articles": [],
    }


def test_valid_public_research_payload_passes_fail_closed_validator():
    payload = {
        "articles": [_article()],
        "preprints": [_article("pre-1", peer_review_status="preprint")],
        "weekly_briefs": [{
            "week": "2026-W31", "article_count": 1, "articles": [_article()],
            "cited_findings": [{"provenance": "published_bilingual_structured_summary"}],
        }],
        "integrity_alerts": [],
        "surveillance_evidence": {
            "signals": [{"signal_id": "signal-1", "exact_articles": []}],
            "evidence_gaps": [{"signal_id": "signal-1"}],
        },
    }
    assert validate_public_research_payload(payload) == []
    assert_public_research_payload(payload)


def test_release_validator_blocks_thin_pages_preprint_leaks_and_stale_exact_links():
    thin = _article()
    thin["summary"] = {"en": {"main_findings": "A"}}
    thin["indexable"] = False
    thin["abstract_text"] = "private"
    thin["peer_review_status"] = "preprint"
    thin["related_articles"] = [{"article_id": "lit-1"}]
    payload = {
        "articles": [thin],
        "preprints": [],
        "weekly_briefs": [],
        "integrity_alerts": [],
        "surveillance_evidence": {
            "signals": [{
                "signal_id": "signal-1",
                "exact_articles": [{
                    "article_id": "historical", "recency_status": "outside_exact_window", "evidence_age_days": 10000,
                }],
            }],
            "evidence_gaps": [],
        },
    }
    blockers = validate_public_research_payload(payload)
    assert any("missing a bilingual" in blocker for blocker in blockers)
    assert any("private fields" in blocker for blocker in blockers)
    assert any("non-peer-reviewed" in blocker for blocker in blockers)
    assert any("recommends itself" in blocker for blocker in blockers)
    assert any("out-of-window" in blocker for blocker in blockers)
    with pytest.raises(ValueError, match="release validation failed"):
        assert_public_research_payload(payload)


def test_release_validator_accepts_redacted_weekly_brief_index_projection():
    payload = {
        "articles": [_article()],
        "preprints": [],
        "weekly_briefs": [{
            "week": "2026-W31",
            "article_count": 1,
            "cited_findings": [{"provenance": "published_bilingual_structured_summary"}],
        }],
        "integrity_alerts": [],
        "surveillance_evidence": {"signals": [], "evidence_gaps": []},
    }

    assert validate_public_research_payload(payload) == []


def test_release_validator_blocks_stale_classification_versions():
    article = _article()
    article["classification_version"] = 4
    payload = {
        "articles": [article],
        "preprints": [],
        "weekly_briefs": [],
        "integrity_alerts": [],
        "surveillance_evidence": {"signals": [], "evidence_gaps": []},
    }

    assert any(
        "stale or missing classification" in blocker
        for blocker in validate_public_research_payload(payload)
    )


def test_release_validator_blocks_non_public_research_domains():
    article = _article()
    article["research_domain"] = "animal_only"
    payload = {
        "articles": [article],
        "preprints": [],
        "weekly_briefs": [],
        "integrity_alerts": [],
        "surveillance_evidence": {"signals": [], "evidence_gaps": []},
    }

    assert any(
        "non-public research domain" in blocker
        for blocker in validate_public_research_payload(payload)
    )


def test_release_validator_accepts_complete_allowlisted_weekly_review():
    payload = {
        "articles": [_article()],
        "preprints": [],
        "weekly_briefs": [{
            "week": "2026-W31",
            "article_count": 1,
            "articles": [_article()],
            "brief_status": "editorially_reviewed",
            "byline": {"reviewer": {
                "name": "Dr Jane Q. Public",
                "role": "Infectious disease editor",
                "reviewed_at": "2020-08-16T10:00:00+00:00",
                "institution": "Example School of Public Health",
            }},
        }],
        "integrity_alerts": [],
        "surveillance_evidence": {"signals": [], "evidence_gaps": []},
    }

    assert validate_public_research_payload(payload) == []


def test_release_validator_accepts_ai_review_only_as_distinct_non_editorial_evidence():
    payload = {
        "articles": [_article()], "preprints": [], "integrity_alerts": [],
        "weekly_briefs": [{
            "week": "2026-W31", "article_count": 1, "articles": [_article()],
            "brief_status": "ai_reviewed",
            "byline": {"reviewer": None, "ai_review": {
                "verdict": "pass", "issue_codes": [], "reviewed_at": "2020-08-16T10:00:00Z",
                "protocol_version": "research-weekly-ai-review.v1",
                "model": "bounded-review-model", "provider": "model-center",
            }},
        }],
        "surveillance_evidence": {"signals": [], "evidence_gaps": []},
    }

    assert validate_public_research_payload(payload) == []

    payload["weekly_briefs"][0]["byline"]["ai_review"]["private_reasoning"] = "must not leak"
    assert any("AI review exports non-public fields" in item for item in validate_public_research_payload(payload))


@pytest.mark.parametrize("brief, expected", [
    (
        {
            "brief_status": "editorially_reviewed",
            "byline": {"reviewer": {"name": "Dr Jane Q. Public", "role": "Editor"}},
        },
        "claims review without valid reviewer evidence",
    ),
    (
        {
            "brief_status": "automatically_compiled_not_editorially_reviewed",
            "byline": {"reviewer": {
                "name": "Dr Jane Q. Public", "role": "Editor",
                "reviewed_at": "2020-08-16T10:00:00Z",
            }},
        },
        "exposes a reviewer without reviewed status",
    ),
    (
        {
            "brief_status": "editorially_reviewed",
            "byline": {"reviewer": {
                "name": "Dr Jane Q. Public", "role": "Editor",
                "reviewed_at": "2020-08-16T10:00:00Z",
                "internal_reviewer_id": "operator-17",
            }},
        },
        "exports non-public fields",
    ),
])
def test_release_validator_blocks_incomplete_inconsistent_or_leaky_weekly_review(brief, expected):
    payload = {
        "articles": [_article()],
        "preprints": [],
        "weekly_briefs": [{"week": "2026-W31", "article_count": 1, "articles": [_article()], **brief}],
        "integrity_alerts": [],
        "surveillance_evidence": {"signals": [], "evidence_gaps": []},
    }

    assert any(expected in blocker for blocker in validate_public_research_payload(payload))
