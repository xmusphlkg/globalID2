"""Transport-neutral literature records used between source clients and storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ArticleCandidate:
    article_id: str
    slug: str
    title: str
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    journal: str | None = None
    issn: list[str] = field(default_factory=list)
    publisher: str | None = None
    authors: list[dict[str, str]] = field(default_factory=list)
    article_type: str = "journal-article"
    study_type: str | None = None
    published_at: datetime | None = None
    indexed_at: datetime | None = None
    abstract_text: str | None = None
    abstract_license: str | None = None
    source_urls: dict[str, str] = field(default_factory=dict)
    open_access_status: str = "unknown"
    open_access_url: str | None = None
    license_url: str | None = None
    peer_review_status: str = "peer_reviewed"
    integrity_status: str = "current"
    source_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Match:
    key: str
    label: str
    confidence: float
    terms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Classification:
    diseases: list[Match] = field(default_factory=list)
    countries: list[Match] = field(default_factory=list)
    topics: list[Match] = field(default_factory=list)
    study_type: str | None = None
    relevance_score: float = 0.0
    public_health_score: float = 0.0
    discovery_score: float = 0.0
    publication_status: str = "review"


__all__ = ["ArticleCandidate", "Classification", "Match"]
