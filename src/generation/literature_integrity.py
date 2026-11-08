"""Metadata-only projection of literature integrity events for public export."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

_PUBLIC_INTEGRITY_STATUSES = {
    "retracted": "retraction",
    "retraction": "retraction",
    "expression_of_concern": "expression_of_concern",
    "corrected": "correction",
    "correction": "correction",
}


def _record_value(record: Any, field: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(field, default)
    return getattr(record, field, default)


def _public_event_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.isoformat()
    text = str(value or "").strip()
    return text or None


def _public_integrity_source_url(article: Any) -> str | None:
    source_urls = _record_value(article, "source_urls", {}) or {}
    candidates = [
        source_urls.get("doi") if isinstance(source_urls, dict) else None,
        source_urls.get("publisher") if isinstance(source_urls, dict) else None,
        source_urls.get("pubmed") if isinstance(source_urls, dict) else None,
    ]
    doi = str(_record_value(article, "doi") or "").strip()
    if doi:
        candidates.append(f"https://doi.org/{doi}")
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value.startswith(("https://", "http://")):
            return value
    return None


def project_public_integrity_alerts(
    events: list[Any],
    articles: list[Any],
    *,
    ever_public_article_ids: set[str] | None = None,
    currently_public_article_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Project a metadata-only integrity history for articles once made public.

    Event ``metadata`` and article abstracts/source payloads are deliberately
    never read here, so they cannot accidentally cross the static-site export
    boundary.
    """

    article_by_id = {
        str(_record_value(article, "article_id") or ""): article
        for article in articles
        if _record_value(article, "article_id")
    }
    ever_public = {str(value) for value in (ever_public_article_ids or set()) if value}
    ever_public.update(
        article_id
        for article_id, article in article_by_id.items()
        if str(_record_value(article, "publication_status") or "").lower() == "published"
    )
    currently_public = {
        str(value) for value in (currently_public_article_ids or set()) if value
    }
    alerts: list[dict[str, Any]] = []
    for event in events:
        article_id = str(_record_value(event, "article_id") or "")
        article = article_by_id.get(article_id)
        normalized_status = re.sub(
            r"[\s-]+", "_", str(_record_value(event, "current_status") or "").strip().lower()
        )
        public_event_type = _PUBLIC_INTEGRITY_STATUSES.get(normalized_status)
        if article is None or article_id not in ever_public or public_event_type is None:
            continue
        effective_at = _public_event_datetime(
            _record_value(event, "effective_at") or _record_value(event, "created_at")
        )
        event_id = _record_value(event, "id")
        if event_id is None:
            fingerprint = "|".join((
                article_id,
                public_event_type,
                effective_at or "unknown",
                str(_record_value(event, "source") or "unknown"),
            ))
            event_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
        is_currently_public = article_id in currently_public
        slug = str(_record_value(article, "slug") or "").strip()
        alerts.append({
            "alert_id": f"integrity-{event_id}",
            "article_id": article_id,
            "article_slug": slug,
            "article_title": str(_record_value(article, "title") or "Literature record"),
            "doi": _record_value(article, "doi"),
            "journal": _record_value(article, "journal"),
            "published_at": _public_event_datetime(_record_value(article, "published_at")),
            "event_type": public_event_type,
            "previous_status": _record_value(event, "previous_status"),
            "current_status": normalized_status,
            "effective_at": effective_at,
            "recorded_at": _public_event_datetime(_record_value(event, "created_at")),
            "source": str(_record_value(event, "source") or "source record"),
            "source_url": _public_integrity_source_url(article),
            "is_currently_public": is_currently_public,
            "article_url": f"/research/articles/{slug}/" if is_currently_public and slug else None,
        })
    return sorted(
        alerts,
        key=lambda item: (item.get("effective_at") or item.get("recorded_at") or "", item["alert_id"]),
        reverse=True,
    )
