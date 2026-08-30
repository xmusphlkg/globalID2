from __future__ import annotations

import json

from scripts import review_research_weekly_brief as workflow
from src.literature.weekly_briefs import enrich_weekly_briefs, load_weekly_review_registry


def _brief() -> dict:
    return {
        "week": "2026-W33",
        "start_date": "2026-08-10",
        "end_date": "2026-08-16",
        "cited_findings": [{
            "article_id": "lit-1", "title": "Study", "finding_en": "Finding.",
            "finding_zh": "发现。", "source_url": "/research/articles/study/",
            "doi": "10.1000/study", "provenance": "published_bilingual_structured_summary",
        }],
        "monitoring_context": [],
        "evidence_gaps": [],
    }


def test_review_workflow_is_dry_run_and_requires_explicit_attestation(tmp_path, capsys):
    brief = tmp_path / "2026-W33.json"
    brief.write_text(json.dumps(_brief()), encoding="utf-8")
    registry = tmp_path / "weekly_reviews.json"
    base = [
        "--week", "2026-W33", "--brief", str(brief), "--registry", str(registry),
        "--reviewer-name", "Dr Jane Public", "--reviewer-role", "Infectious disease editor",
    ]
    assert workflow.main(base) == 2
    assert "human_review_attestation_required" in capsys.readouterr().err
    assert workflow.main([*base, "--attest-reviewed"]) == 0
    assert not registry.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"


def test_applied_review_is_bound_to_the_exact_brief_fingerprint(tmp_path):
    brief = tmp_path / "2026-W33.json"
    brief.write_text(json.dumps(_brief()), encoding="utf-8")
    registry = tmp_path / "weekly_reviews.json"
    args = [
        "--week", "2026-W33", "--brief", str(brief), "--registry", str(registry),
        "--reviewer-name", "Dr Jane Public", "--reviewer-role", "Infectious disease editor",
        "--reviewed-at", "2026-08-17T10:00:00Z", "--attest-reviewed", "--apply",
    ]
    assert workflow.main(args) == 0
    entry = load_weekly_review_registry(registry)["2026-W33"]

    raw = {
        "week": "2026-W33", "start_date": "2026-08-10", "end_date": "2026-08-16",
        "articles": [{
            "article_id": "lit-1", "slug": "study", "title": "Study", "doi": "10.1000/study",
            "diseases": [], "countries": [], "topics": [], "related_signals": [],
            "summary": {"en": {"main_findings": "Finding."}, "zh": {"main_findings": "发现。"}},
        }],
        "_editorial_review": entry,
    }
    reviewed = enrich_weekly_briefs([raw], surveillance_evidence=None)[0]
    assert reviewed["brief_status"] == "editorially_reviewed"

    raw["articles"][0]["summary"]["en"]["main_findings"] = "Changed finding."
    changed = enrich_weekly_briefs([raw], surveillance_evidence=None)[0]
    assert changed["brief_status"] == "automatically_compiled_not_editorially_reviewed"
    assert changed["byline"]["reviewer"] is None
