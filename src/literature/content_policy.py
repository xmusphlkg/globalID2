"""Publication content tiers for literature records.

Records that cannot support a detailed, source-grounded introduction remain
eligible for catalogue and surveillance relationships, but are explicitly
marked as related-only. This keeps metadata useful without implying that a
full-text review was performed.
"""

from __future__ import annotations

from typing import Any


_RELATED_ONLY_STUDY_MARKERS = (
    "commentary",
    "editorial",
    "perspective",
    "opinion",
    "systematic review",
    "meta-analysis",
    "meta analysis",
    "review",
)


def detail_restrictions(article: Any) -> list[str]:
    """Return reasons a record must not receive a detailed introduction."""

    study_type = str(getattr(article, "study_type", None) or "").strip().casefold()
    article_type = str(getattr(article, "article_type", None) or "").strip().casefold()
    restrictions: list[str] = []
    if any(marker in study_type for marker in _RELATED_ONLY_STUDY_MARKERS) or any(
        marker in article_type for marker in ("commentary", "editorial", "opinion", "review")
    ):
        restrictions.append("opinion_or_review")

    has_access_metadata = any(
        hasattr(article, field)
        for field in ("open_access_status", "open_access_url", "pmcid")
    )
    open_access_status = str(getattr(article, "open_access_status", None) or "").casefold()
    open_access_url = str(getattr(article, "open_access_url", None) or "").strip()
    pmcid = str(getattr(article, "pmcid", None) or "").strip()
    if has_access_metadata and open_access_status != "open" and not open_access_url and not pmcid:
        restrictions.append("full_text_unavailable")
    return restrictions


def content_policy(article: Any) -> dict[str, Any]:
    restrictions = detail_restrictions(article)
    return {
        "detail_available": not restrictions,
        "related_only": bool(restrictions),
        "detail_restrictions": restrictions,
    }


__all__ = ["content_policy", "detail_restrictions"]
