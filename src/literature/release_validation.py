"""Fail-closed validation for a public Research Radar release payload."""

from __future__ import annotations

from typing import Any

from .classification import CLASSIFICATION_VERSION
from .weekly_briefs import project_weekly_editorial_review
from .weekly_ai_review import project_weekly_ai_review

PRIVATE_ARTICLE_FIELDS = {"abstract_text", "abstract", "source_payload", "raw_payload", "full_text"}


def validate_public_research_payload(payload: dict[str, Any]) -> list[str]:
    """Return stable, human-readable release blockers."""
    blockers: list[str] = []
    articles = payload.get("articles") or []
    preprints = payload.get("preprints") or []
    article_ids: set[str] = set()
    dois: set[str] = set()

    for collection_name, collection in (("articles", articles), ("preprints", preprints)):
        for index, article in enumerate(collection):
            identity = str(article.get("article_id") or "")
            label = identity or f"{collection_name}[{index}]"
            if not identity:
                blockers.append(f"{collection_name}[{index}] is missing article_id")
            elif identity in article_ids:
                blockers.append(f"duplicate public article_id: {identity}")
            else:
                article_ids.add(identity)
            doi = str(article.get("doi") or "").strip().lower()
            if doi and doi in dois:
                blockers.append(f"duplicate public DOI: {doi}")
            elif doi:
                dois.add(doi)
            leaked = sorted(PRIVATE_ARTICLE_FIELDS & set(article))
            if leaked:
                blockers.append(f"{label} exports private fields: {', '.join(leaked)}")
            if article.get("editorial_status") != "published" and article.get("source_kind") != "historical_seed":
                blockers.append(f"{label} is not editorially published")
            if article.get("integrity_status") in {"retracted", "expression_of_concern"}:
                blockers.append(f"{label} has blocked integrity status: {article.get('integrity_status')}")
            summary = article.get("summary") or {}
            if not summary.get("en") or not summary.get("zh"):
                blockers.append(f"{label} is missing a bilingual published summary")
            if article.get("indexable") is False:
                blockers.append(f"{label} crossed the release boundary as non-indexable metadata")
            if article.get("source_kind") != "historical_seed":
                try:
                    classification_version = int(article.get("classification_version") or 0)
                except (TypeError, ValueError):
                    classification_version = 0
                if classification_version < CLASSIFICATION_VERSION:
                    blockers.append(f"{label} has stale or missing classification evidence")
                research_domain = str(article.get("research_domain") or "")
                if research_domain not in {"human_health", "one_health", "not_determined"}:
                    blockers.append(f"{label} has a non-public research domain: {research_domain or 'missing'}")
            related_ids = {str(item.get("article_id") or "") for item in article.get("related_articles") or []}
            if identity and identity in related_ids:
                blockers.append(f"{label} recommends itself")
        if collection_name == "articles" and any(
            str(article.get("peer_review_status") or "") != "peer_reviewed"
            for article in collection
        ):
            blockers.append("peer-reviewed catalogue contains a non-peer-reviewed record")
        if collection_name == "preprints" and any(
            str(article.get("peer_review_status") or "") != "preprint"
            for article in collection
        ):
            blockers.append("preprint collection contains a non-preprint record")

    published_dates = [str(article.get("published_at") or "") for article in articles]
    if published_dates != sorted(published_dates, reverse=True):
        blockers.append("main public catalogue is not sorted by published_at descending")

    evidence = payload.get("surveillance_evidence") or {}
    gap_signals = {str(gap.get("signal_id") or "") for gap in evidence.get("evidence_gaps") or []}
    for signal in evidence.get("signals") or []:
        signal_id = str(signal.get("signal_id") or "")
        exact = signal.get("exact_articles") or []
        if not exact and signal_id not in gap_signals:
            blockers.append(f"signal without exact evidence has no explicit gap: {signal_id}")
        for reference in exact:
            if reference.get("recency_status") != "current_window":
                blockers.append(
                    f"historical/out-of-window article marked exact for signal {signal_id}: "
                    f"{reference.get('article_id')}"
                )
            age = reference.get("evidence_age_days")
            if isinstance(age, (int, float)) and age > 730:
                blockers.append(
                    f"article older than 730 days marked exact for signal {signal_id}: "
                    f"{reference.get('article_id')}"
                )

    for brief in payload.get("weekly_briefs") or []:
        # The publication service validates the full in-memory brief, while the
        # generated index intentionally omits the repeated article collection.
        # Only compare the count when that collection is present.
        items = brief.get("articles") or []
        if "articles" in brief and int(brief.get("article_count") or 0) != len(items):
            blockers.append(f"weekly brief count mismatch: {brief.get('week')}")
        for finding in brief.get("cited_findings") or []:
            if finding.get("provenance") != "published_bilingual_structured_summary":
                blockers.append(f"weekly brief contains an ungrounded finding: {brief.get('week')}")
        status = brief.get("brief_status")
        byline = brief.get("byline") if isinstance(brief.get("byline"), dict) else {}
        reviewer = byline.get("reviewer")
        ai_review = byline.get("ai_review")
        valid_reviewer = project_weekly_editorial_review(reviewer)
        if isinstance(reviewer, dict) and set(reviewer) - {
            "name", "role", "reviewed_at", "institution", "note_en", "note_zh",
        }:
            blockers.append(f"weekly brief reviewer exports non-public fields: {brief.get('week')}")
        if status == "editorially_reviewed" and valid_reviewer is None:
            blockers.append(f"weekly brief claims review without valid reviewer evidence: {brief.get('week')}")
        if status != "editorially_reviewed" and reviewer is not None:
            blockers.append(f"weekly brief exposes a reviewer without reviewed status: {brief.get('week')}")
        valid_ai_review = project_weekly_ai_review(ai_review)
        if isinstance(ai_review, dict) and set(ai_review) - {
            "verdict", "issue_codes", "reviewed_at", "protocol_version", "model", "provider",
        }:
            blockers.append(f"weekly brief AI review exports non-public fields: {brief.get('week')}")
        if status == "ai_reviewed" and valid_ai_review is None:
            blockers.append(f"weekly brief claims AI review without valid evidence: {brief.get('week')}")
        if status != "ai_reviewed" and ai_review is not None:
            blockers.append(f"weekly brief exposes AI review without AI-reviewed status: {brief.get('week')}")
        if status not in {
            None, "automatically_compiled_not_editorially_reviewed", "editorially_reviewed", "ai_reviewed",
        }:
            blockers.append(f"weekly brief has unsupported review status: {brief.get('week')}")

    for alert in payload.get("integrity_alerts") or []:
        leaked = sorted(PRIVATE_ARTICLE_FIELDS & set(alert))
        if leaked:
            blockers.append(
                f"integrity alert {alert.get('event_id') or alert.get('article_id')} exports private fields: "
                f"{', '.join(leaked)}"
            )

    return sorted(set(blockers))


def assert_public_research_payload(payload: dict[str, Any]) -> None:
    blockers = validate_public_research_payload(payload)
    if blockers:
        raise ValueError("Research Radar release validation failed:\n- " + "\n- ".join(blockers))


__all__ = ["assert_public_research_payload", "validate_public_research_payload"]
