from datetime import datetime, timezone
import json

import pytest

from src.literature.weekly_briefs import enrich_weekly_briefs, load_weekly_review_registry


def test_weekly_brief_contains_only_cited_bilingual_findings_and_public_signal_context():
    briefs = [{
        "week": "2026-W33",
        "articles": [
            {
                "article_id": "lit-1",
                "slug": "lit-1",
                "title": "Dengue study",
                "doi": "10.1000/dengue",
                "diseases": [{"disease_id": "D021"}],
                "summary": {
                    "en": {"main_findings": "The study reported a source-level finding."},
                    "zh": {"main_findings": "该研究报告了一项来源层面的发现。"},
                },
                "related_signals": [{
                    "signal_id": "signal-1", "disease_id": "D021", "visibility": "public",
                    "relation_level": "exact_disease_geography", "situation_url": "/situation/",
                }],
            },
            {
                "article_id": "lit-2", "slug": "lit-2", "title": "Metadata only",
                "diseases": [{"disease_id": "D021"}], "summary": {},
                "related_signals": [{"signal_id": "shadow", "visibility": "shadow"}],
            },
        ],
    }]
    result = enrich_weekly_briefs(briefs, surveillance_evidence={
        "evidence_gaps": [{
            "gap_id": "gap-1", "signal_id": "signal-2", "disease_id": "D021",
            "gap_type": "geography_coverage_gap", "note_en": "No exact match.", "note_zh": "没有精确匹配。",
        }],
    })[0]
    assert [item["article_id"] for item in result["cited_findings"]] == ["lit-1"]
    assert [item["signal_id"] for item in result["monitoring_context"]] == ["signal-1"]
    assert [item["gap_id"] for item in result["evidence_gaps"]] == ["gap-1"]
    assert result["brief_status"] == "automatically_compiled_not_editorially_reviewed"
    assert result["byline"]["reviewer"] is None


def test_weekly_brief_never_claims_a_gap_for_an_unrelated_disease():
    result = enrich_weekly_briefs([{
        "week": "2026-W33",
        "articles": [{"article_id": "lit-1", "diseases": [{"disease_id": "D021"}]}],
    }], surveillance_evidence={
        "evidence_gaps": [{"gap_id": "gap-ebola", "disease_id": "D050"}],
    })[0]
    assert result["evidence_gaps"] == []


def test_complete_explicit_human_review_is_allowlisted_and_publicly_attributed():
    result = enrich_weekly_briefs([{
        "week": "2026-W33",
        "articles": [],
        "brief_status": "caller_claimed_reviewed",
        "byline": {"reviewer": {"name": "Untrusted caller"}},
        "_editorial_review": {
            "name": "Dr Jane Q. Public",
            "role": "Infectious disease editor",
            "reviewed_at": "2026-08-16T10:30:00+08:00",
            "institution": "Example School of Public Health",
            "note_en": "Checked against the cited source records.",
            "note_zh": "已依据所引来源记录完成核对。",
            "internal_reviewer_id": "operator-17",
            "reviewer_email": "private@example.test",
            "private_note": "Never publish this.",
        },
    }], surveillance_evidence=None, now=datetime(2026, 8, 17, tzinfo=timezone.utc))[0]

    assert result["brief_status"] == "editorially_reviewed"
    assert result["byline"]["reviewer"] == {
        "name": "Dr Jane Q. Public",
        "role": "Infectious disease editor",
        "reviewed_at": "2026-08-16T02:30:00+00:00",
        "institution": "Example School of Public Health",
        "note_en": "Checked against the cited source records.",
        "note_zh": "已依据所引来源记录完成核对。",
    }
    serialized = json.dumps(result)
    assert "operator-17" not in serialized
    assert "private@example.test" not in serialized
    assert "Never publish this" not in serialized
    assert "_editorial_review" not in result


@pytest.mark.parametrize("review", [
    {"name": "Dr Jane Q. Public", "role": "Editor"},
    {"name": "Automated compiler", "role": "Editor", "reviewed_at": "2026-08-16T10:00:00Z"},
    {"name": "Dr Jane Q. Public", "role": "Editor", "reviewed_at": "2026-08-16T10:00:00"},
    {"name": "Dr Jane Q. Public", "role": "Editor", "reviewed_at": "2027-08-16T10:00:00Z"},
    {"name": "private@example.test", "role": "Editor", "reviewed_at": "2026-08-16T10:00:00Z"},
])
def test_partial_synthetic_unsafe_or_invalid_review_fails_closed(review):
    result = enrich_weekly_briefs([{
        "week": "2026-W33",
        "articles": [],
        "_editorial_review": review,
    }], surveillance_evidence=None, now=datetime(2026, 8, 17, tzinfo=timezone.utc))[0]

    assert result["brief_status"] == "automatically_compiled_not_editorially_reviewed"
    assert result["byline"]["reviewer"] is None
    assert "_editorial_review" not in result


def test_partial_or_unsafe_optional_note_fails_the_review_closed_without_leaking():
    result = enrich_weekly_briefs([{
        "week": "2026-W33",
        "articles": [],
        "_editorial_review": {
            "name": "Dr Jane Q. Public",
            "role": "Infectious disease editor",
            "reviewed_at": "2026-08-16T10:00:00Z",
            "note_en": "Contact private@example.test for internal detail.",
        },
    }], surveillance_evidence=None, now=datetime(2026, 8, 17, tzinfo=timezone.utc))[0]

    assert result["brief_status"] == "automatically_compiled_not_editorially_reviewed"
    assert result["byline"]["reviewer"] is None
    assert "private@example.test" not in json.dumps(result)


def test_review_registry_omits_duplicate_weeks_and_keeps_only_explicit_records(tmp_path):
    path = tmp_path / "weekly_reviews.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "reviews": [
            {"week": "2026-W32", "review": {"name": "First"}},
            {"week": "2026-W32", "review": {"name": "Second"}},
            {"week": "2026-W33", "review": {"name": "Dr Jane Q. Public"}},
            {"week": "not-a-week", "review": {"name": "Ignored"}},
        ],
    }), encoding="utf-8")

    assert load_weekly_review_registry(path) == {
        "2026-W33": {"name": "Dr Jane Q. Public"},
    }
