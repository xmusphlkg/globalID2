"""Separate approved bibliography, peer-reviewed evidence and preprint records."""
from __future__ import annotations

from typing import Any


def partition_public_literature_articles(
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep peer-reviewed research and preprints in separate public streams.

    ``publication_status`` is the persisted editorial gate.  The public
    projection exposes it as ``editorial_status`` so downstream consumers can
    enforce the same boundary without depending on the database model name.
    """

    published = [
        article
        for article in articles
        if str(article.get("editorial_status") or "").lower() == "published"
    ]
    peer_reviewed = [
        article
        for article in published
        if str(article.get("peer_review_status") or "").lower() == "peer_reviewed"
    ]
    preprints = [
        article
        for article in published
        if str(article.get("peer_review_status") or "").lower() == "preprint"
    ]
    return peer_reviewed, preprints


def apply_public_summary_gate(
    peer_reviewed: list[dict[str, Any]],
    preprints: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Withhold records until both published language summaries are present."""
    withheld = sum(
        article.get("indexable") is not True
        for article in [*peer_reviewed, *preprints]
    )
    return (
        [article for article in peer_reviewed if article.get("indexable") is True],
        [article for article in preprints if article.get("indexable") is True],
        withheld,
    )


def bibliographic_catalogue_records(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project approved records without evidence pages to a source-linked tier."""
    fields = (
        "article_id", "slug", "title", "doi", "pmid", "journal", "publisher",
        "authors", "study_type", "published_at", "publication_week", "open_access_status",
        "open_access_url", "peer_review_status", "discovery_score", "diseases", "countries",
        "topics", "pathogens", "pathogen_types", "populations", "publication_date_label_en",
        "publication_date_label_zh", "publication_date_label_fr", "integrity_status", "editorial_status",
    )
    return [
        {**{key: article.get(key) for key in fields},
         "content_tier": "bibliographic", "detail_available": False}
        for article in articles
        if article.get("editorial_status") == "published"
        and article.get("peer_review_status") == "peer_reviewed"
        and article.get("integrity_status") not in {"retracted", "expression_of_concern"}
        and article.get("indexable") is not True
        and (article.get("doi") or article.get("pmid"))
        and article.get("research_domain") != "plant_only"
    ]
