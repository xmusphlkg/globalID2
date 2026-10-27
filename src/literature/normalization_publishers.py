"""Normalize publisher feeds, commercial indexes, and preprint records."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from .normalization_common import (
    _boolean_signal,
    _bounded_text,
    _flexible_date,
    _stable_identity,
    compact_text,
    normalize_doi,
    normalize_oa_url,
)
from .types import ArticleCandidate


def normalize_publisher_rss(payload: dict[str, Any]) -> ArticleCandidate | None:
    """Normalize discovery-only publisher feed metadata without feed body text."""
    title = compact_text(payload.get("title"))
    if not title:
        return None
    doi = normalize_doi(payload.get("doi"))
    published_at = _flexible_date(payload.get("published_at"))
    indexed_at = _flexible_date(payload.get("retrieved_at"))
    article_id, slug = _stable_identity(doi, title, published_at)
    publisher_url = normalize_oa_url(payload.get("link"))
    feed_url = normalize_oa_url(payload.get("feed_url"))
    issn_value = payload.get("issn") or []
    issn = issn_value if isinstance(issn_value, list) else [issn_value]
    source_metadata = {
        "feed_id": compact_text(payload.get("feed_id")),
        "entry_id": compact_text(payload.get("entry_id")),
        "guid": compact_text(payload.get("guid")) or None,
        "retrieved_at": indexed_at.isoformat() if indexed_at else None,
        "feed_origins": [compact_text(value) for value in payload.get("feed_origins") or [] if compact_text(value)],
    }
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        title=title,
        journal=compact_text(payload.get("journal")) or None,
        issn=sorted({str(value).strip().upper() for value in issn if value}),
        publisher=compact_text(payload.get("publisher")) or None,
        article_type="journal-article",
        published_at=published_at,
        indexed_at=indexed_at or published_at,
        abstract_text=None,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"publisher": publisher_url} if publisher_url else {}),
            **({"rss": feed_url} if feed_url else {}),
        },
        open_access_status="unknown",
        peer_review_status="peer_reviewed",
        source_payload={"rss": source_metadata},
    )


def _publisher_metadata_authors(values: Any) -> list[dict[str, str]]:
    authors: list[dict[str, str]] = []
    rows = values if isinstance(values, list) else [values]
    for raw in rows[:50]:
        if isinstance(raw, dict):
            raw = raw.get("creator") or raw.get("$") or raw.get("name")
        if name := _bounded_text(raw, 300):
            authors.append({"name": name})
    return authors


def normalize_springer_nature(payload: dict[str, Any]) -> ArticleCandidate | None:
    """Normalize a bounded subset of Springer metadata, excluding abstracts/full text."""
    title = _bounded_text(payload.get("title"), 1_000)
    if not title:
        return None
    identifiers = payload.get("identifier") or []
    identifiers = (identifiers if isinstance(identifiers, list) else [identifiers])[:50]
    doi = normalize_doi(payload.get("doi")) or next(
        (
            normalize_doi(str(value).split("doi:", 1)[-1])
            for value in identifiers
            if "doi:" in str(value).casefold()
        ),
        None,
    )
    published_at = _flexible_date(
        payload.get("onlineDate") or payload.get("publicationDate") or payload.get("printDate")
    )
    article_id, slug = _stable_identity(doi, title, published_at)
    urls = payload.get("url") or []
    urls = urls if isinstance(urls, list) else [urls]
    landing_url = next(
        (
            url
            for raw in urls[:20]
            if isinstance(raw, dict)
            and str(raw.get("format") or "").casefold() != "pdf"
            and (url := normalize_oa_url(raw.get("value") or raw.get("url")))
            and not urlsplit(url).path.casefold().endswith(".pdf")
        ),
        None,
    )
    issn = sorted({
        str(value).strip().upper()
        for value in (payload.get("issn"), payload.get("eIssn"), payload.get("pIssn"))
        if value
    })
    sanitized = {
        "doi": doi,
        "title": title,
        "journal": _bounded_text(payload.get("publicationName") or payload.get("journalTitle"), 500),
        "publisher": _bounded_text(payload.get("publisher"), 300),
        "publication_date": published_at.date().isoformat() if published_at else None,
        "content_type": _bounded_text(payload.get("contentType"), 100),
        "landing_url": landing_url,
    }
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        title=title,
        journal=sanitized["journal"],
        issn=issn,
        publisher=sanitized["publisher"] or "Springer Nature",
        authors=_publisher_metadata_authors(payload.get("creators")),
        article_type="journal-article",
        published_at=published_at,
        indexed_at=published_at,
        # Publisher API abstracts are intentionally not retained; a legal OA
        # source can fill an abstract later through the existing enrichers.
        abstract_text=None,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"publisher": landing_url} if landing_url else {}),
        },
        peer_review_status="peer_reviewed",
        source_payload={"springer_nature": {key: value for key, value in sanitized.items() if value is not None}},
    )


def normalize_elsevier(payload: dict[str, Any]) -> ArticleCandidate | None:
    """Normalize Scopus search metadata without requesting proprietary content."""
    title = _bounded_text(payload.get("dc:title"), 1_000)
    if not title:
        return None
    doi = normalize_doi(payload.get("prism:doi"))
    published_at = _flexible_date(payload.get("prism:coverDate") or payload.get("prism:coverDisplayDate"))
    article_id, slug = _stable_identity(doi, title, published_at)
    links = payload.get("link") or []
    links = links if isinstance(links, list) else [links]
    landing_url = next(
        (
            url
            for raw in links[:20]
            if isinstance(raw, dict)
            and str(raw.get("@ref") or "").casefold() in {"scopus", "self"}
            and (url := normalize_oa_url(raw.get("@href")))
        ),
        None,
    )
    is_open = _boolean_signal(payload.get("openaccess")) is True
    issn = sorted({
        str(value).strip().upper()
        for value in (payload.get("prism:issn"), payload.get("prism:eIssn"))
        if value
    })
    sanitized = {
        "eid": _bounded_text(payload.get("eid"), 100),
        "doi": doi,
        "title": title,
        "journal": _bounded_text(payload.get("prism:publicationName"), 500),
        "publication_date": published_at.date().isoformat() if published_at else None,
        "subtype": _bounded_text(payload.get("subtypeDescription"), 100),
        "open_access": is_open,
        "landing_url": landing_url,
    }
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        title=title,
        journal=sanitized["journal"],
        issn=issn,
        publisher="Elsevier",
        authors=_publisher_metadata_authors(payload.get("dc:creator")),
        article_type="journal-article",
        published_at=published_at,
        indexed_at=published_at,
        abstract_text=None,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"scopus": landing_url} if landing_url else {}),
        },
        open_access_status="open" if is_open else "unknown",
        peer_review_status="peer_reviewed",
        source_payload={"elsevier": {key: value for key, value in sanitized.items() if value is not None}},
    )


def _preprint_license_url(value: Any) -> str | None:
    text = compact_text(value).casefold().replace("_", "-")
    mapping = {
        "cc-by": "https://creativecommons.org/licenses/by/4.0/",
        "cc-by-nc": "https://creativecommons.org/licenses/by-nc/4.0/",
        "cc-by-nd": "https://creativecommons.org/licenses/by-nd/4.0/",
        "cc-by-nc-nd": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    }
    return mapping.get(text)


def normalize_biorxiv(payload: dict[str, Any]) -> ArticleCandidate | None:
    """Normalize official bioRxiv/medRxiv API metadata and force preprint status."""
    title = _bounded_text(payload.get("title"), 1_000)
    if not title:
        return None
    doi = normalize_doi(payload.get("doi"))
    published_at = _flexible_date(payload.get("date"))
    article_id, slug = _stable_identity(doi, title, published_at)
    server = compact_text(payload.get("server")).casefold()
    if server not in {"biorxiv", "medrxiv"}:
        return None
    license_url = _preprint_license_url(payload.get("license"))
    peer_reviewed_doi = normalize_doi(payload.get("published"))
    relations = []
    if doi and peer_reviewed_doi and doi != peer_reviewed_doi:
        relations.append({
            "relation_type": "preprint_to_peer_reviewed",
            "preprint_doi": doi,
            "peer_reviewed_doi": peer_reviewed_doi,
            "source": "biorxiv-api",
        })
    authors = [
        {"name": name[:300]}
        for value in str(payload.get("authors") or "").split(";")[:50]
        if (name := compact_text(value))
    ]
    landing_url = f"https://doi.org/{doi}" if doi else None
    sanitized = {
        "server": server,
        "doi": doi,
        "version": _bounded_text(payload.get("version"), 20),
        "date": published_at.date().isoformat() if published_at else None,
        "category": _bounded_text(payload.get("category"), 200),
        "license": _bounded_text(payload.get("license"), 80),
        "published_doi": peer_reviewed_doi,
    }
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        title=title,
        journal="medRxiv" if server == "medrxiv" else "bioRxiv",
        publisher="Cold Spring Harbor Laboratory",
        authors=authors,
        article_type="preprint",
        study_type="Preprint",
        published_at=published_at,
        indexed_at=published_at,
        abstract_text=_bounded_text(payload.get("abstract"), 12_000),
        abstract_license=license_url,
        source_urls={"doi": landing_url} if landing_url else {},
        open_access_status="open",
        open_access_url=landing_url,
        license_url=license_url,
        peer_review_status="preprint",
        version_relations=relations,
        source_payload={"biorxiv": {key: value for key, value in sanitized.items() if value is not None}},
    )
