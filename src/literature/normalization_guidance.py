"""Normalize official institutional guidance repository records."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from .normalization_common import (
    _DOI_IN_TEXT_RE,
    _flexible_date,
    _stable_identity,
    compact_text,
    is_open_license_url,
    normalize_doi,
    normalize_oa_url,
)
from .types import ArticleCandidate


def normalize_official_guidance(payload: dict[str, Any]) -> ArticleCandidate | None:
    """Normalize bounded WHO IRIS Dublin Core metadata, never linked files."""

    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

    def values(name: str, *, max_items: int = 40, max_length: int = 4_000) -> list[str]:
        return [
            text[:max_length]
            for value in (fields.get(name) or [])[:max_items]
            if (text := compact_text(value))
        ]

    titles = values("title", max_items=4, max_length=500)
    if not titles:
        return None
    title = titles[0]
    identifiers = values("identifier", max_items=20, max_length=1_000)
    doi = next(
        (
            normalize_doi(match.group(0).rstrip(".,;:)]}"))
            for value in identifiers
            if (match := _DOI_IN_TEXT_RE.search(value))
        ),
        None,
    )
    dates = [parsed for value in values("date", max_items=12, max_length=80) if (parsed := _flexible_date(value))]
    published_at = min(dates) if dates else None
    indexed_at = _flexible_date(payload.get("datestamp")) or published_at
    article_id, slug = _stable_identity(doi, title, published_at)
    creators = values("creator", max_items=50, max_length=300)
    subjects = values("subject", max_items=60, max_length=300)
    descriptions = values("description", max_items=10, max_length=12_000)
    abstract_text = max(descriptions, key=len, default="")
    rights = values("rights", max_items=12, max_length=1_000)
    license_url = next((url for value in rights if (url := normalize_oa_url(value)) and is_open_license_url(url)), None)
    landing_url = next(
        (
            url
            for value in identifiers
            if (url := normalize_oa_url(value))
            and urlsplit(url).hostname == "iris.who.int"
            and "/handle/" in urlsplit(url).path
        ),
        None,
    )
    type_evidence = " ".join([title, *subjects, *values("type", max_items=10, max_length=200)]).casefold()
    is_guideline = any(term in type_evidence for term in (
        "guideline", "guidance", "recommendation", "consensus statement",
        "technical guidance", "vaccine policy",
    ))
    sanitized_fields = {
        name: values(name)
        for name in (
            "title", "creator", "subject", "description", "date", "type",
            "identifier", "language", "relation", "rights", "publisher", "coverage",
        )
        if values(name)
    }
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        title=title,
        journal="WHO Institutional Repository (IRIS)",
        publisher=(values("publisher", max_items=2, max_length=300) or ["World Health Organization"])[0],
        authors=[{"name": name} for name in creators],
        article_type="guideline" if is_guideline else "technical-report",
        study_type="Guideline" if is_guideline else "Technical report",
        published_at=published_at,
        indexed_at=indexed_at,
        abstract_text=abstract_text or None,
        abstract_license=license_url,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"official_guidance": landing_url} if landing_url else {}),
        },
        open_access_status="open" if license_url and landing_url else "unknown",
        open_access_url=landing_url if license_url else None,
        license_url=license_url,
        peer_review_status="peer_reviewed",
        source_payload={
            "official_guidance": {
                "oai_identifier": compact_text(payload.get("oai_identifier")),
                "datestamp": indexed_at.isoformat() if indexed_at else None,
                "sets": [compact_text(value) for value in payload.get("sets") or [] if compact_text(value)][:20],
                "fields": sanitized_fields,
            },
        },
    )
