"""Normalize provider payloads without coupling the rest of the pipeline to them."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import unescape
import re
from typing import Any

from .types import ArticleCandidate


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def compact_text(value: Any) -> str:
    text = unescape(_TAG_RE.sub(" ", str(value or "")))
    return _SPACE_RE.sub(" ", text).strip()


def normalize_doi(value: Any) -> str | None:
    doi = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        return compact_text(value[0]) if value else None
    text = compact_text(value)
    return text or None


def _date_from_parts(value: Any) -> datetime | None:
    try:
        parts = value.get("date-parts", [])[0]
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return datetime(year, month, day, tzinfo=timezone.utc)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _flexible_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, f"{text}-01" if re.fullmatch(r"\d{4}-\d{2}", text) else "", f"{text}-01-01" if re.fullmatch(r"\d{4}", text) else ""):
        parsed = _iso_datetime(candidate)
        if parsed is not None:
            return parsed
    return None


def _stable_identity(doi: str | None, title: str, published_at: datetime | None) -> tuple[str, str]:
    source = doi or f"{title.lower()}|{published_at.year if published_at else 'unknown'}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    article_id = f"lit_{digest[:24]}"
    slug_base = _SLUG_RE.sub("-", doi or title.lower()).strip("-")[:260] or "article"
    return article_id, f"{slug_base}-{digest[:8]}"


def _integrity_status(message: dict[str, Any]) -> str:
    relation_keys = " ".join(str(key).lower() for key in (message.get("relation") or {}))
    update_types = " ".join(
        str(item.get("type") or "").lower()
        for item in (message.get("update-to") or [])
        if isinstance(item, dict)
    )
    evidence = f"{relation_keys} {update_types}"
    if "retract" in evidence:
        return "retracted"
    if "expression-of-concern" in evidence or "expression of concern" in evidence:
        return "expression_of_concern"
    if "correct" in evidence or "errat" in evidence:
        return "corrected"
    return "current"


def normalize_crossref(message: dict[str, Any]) -> ArticleCandidate | None:
    title = _first(message.get("title"))
    if not title:
        return None
    doi = normalize_doi(message.get("DOI"))
    published_at = next(
        (
            value
            for value in (
                _date_from_parts(message.get("published-online")),
                _date_from_parts(message.get("published-print")),
                _date_from_parts(message.get("published")),
                _date_from_parts(message.get("created")),
            )
            if value is not None
        ),
        None,
    )
    indexed_at = _iso_datetime((message.get("indexed") or {}).get("date-time"))
    article_id, slug = _stable_identity(doi, title, published_at)
    authors = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        given = compact_text(author.get("given"))
        family = compact_text(author.get("family"))
        name = " ".join(part for part in (given, family) if part)
        if name:
            authors.append({
                "name": name,
                **({"orcid": str(author["ORCID"]).removeprefix("https://orcid.org/")} if author.get("ORCID") else {}),
            })
    resources = message.get("resource") or {}
    primary_url = (resources.get("primary") or {}).get("URL") or message.get("URL")
    licenses = message.get("license") or []
    license_url = next((item.get("URL") for item in licenses if isinstance(item, dict) and item.get("URL")), None)
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        title=title,
        journal=_first(message.get("container-title")),
        issn=sorted({str(value).upper() for value in (message.get("ISSN") or []) if value}),
        publisher=_first(message.get("publisher")),
        authors=authors,
        article_type=str(message.get("type") or "journal-article"),
        published_at=published_at,
        indexed_at=indexed_at,
        abstract_text=compact_text(message.get("abstract")) or None,
        abstract_license=license_url,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"publisher": str(primary_url)} if primary_url else {}),
        },
        open_access_status="open" if license_url else "unknown",
        open_access_url=str(primary_url) if license_url and primary_url else None,
        license_url=str(license_url) if license_url else None,
        integrity_status=_integrity_status(message),
        source_payload=message,
    )


def apply_europe_pmc(candidate: ArticleCandidate, payload: dict[str, Any]) -> ArticleCandidate:
    candidate.pmid = str(payload.get("pmid") or "") or candidate.pmid
    candidate.pmcid = str(payload.get("pmcid") or "") or candidate.pmcid
    candidate.abstract_text = compact_text(payload.get("abstractText")) or candidate.abstract_text
    is_open = str(payload.get("isOpenAccess") or "").upper() == "Y"
    if is_open:
        candidate.open_access_status = "open"
        if candidate.pmcid:
            candidate.open_access_url = f"https://europepmc.org/articles/{candidate.pmcid}"
    if candidate.pmid:
        candidate.source_urls["pubmed"] = f"https://pubmed.ncbi.nlm.nih.gov/{candidate.pmid}/"
    candidate.source_payload = {**candidate.source_payload, "europe_pmc": payload}
    return candidate


def normalize_europe_pmc(payload: dict[str, Any]) -> ArticleCandidate | None:
    """Normalize a direct Europe PMC search result into the shared candidate."""
    title = compact_text(payload.get("title"))
    if not title:
        return None
    doi = normalize_doi(payload.get("doi"))
    published_at = _flexible_date(
        payload.get("firstPublicationDate")
        or payload.get("electronicPublicationDate")
        or payload.get("journalInfo", {}).get("printPublicationDate")
    )
    indexed_at = _flexible_date(payload.get("dateOfCreation") or payload.get("dateOfRevision"))
    article_id, slug = _stable_identity(doi, title, published_at)
    authors = []
    for author in (payload.get("authorList") or {}).get("author") or []:
        if not isinstance(author, dict):
            continue
        name = compact_text(author.get("fullName") or " ".join(
            part for part in (str(author.get("firstName") or ""), str(author.get("lastName") or "")) if part
        ))
        if name:
            authors.append({"name": name})
    pmid = str(payload.get("pmid") or "").strip() or None
    pmcid = str(payload.get("pmcid") or "").strip() or None
    is_open = str(payload.get("isOpenAccess") or "").upper() == "Y"
    publication_types = [
        str(value).lower()
        for value in (payload.get("pubTypeList") or {}).get("pubType") or []
    ]
    is_preprint = str(payload.get("source") or "").upper() == "PPR" or "preprint" in publication_types
    journal_info = payload.get("journalInfo") or {}
    journal = journal_info.get("journal") or {}
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        title=title,
        journal=compact_text(payload.get("journalTitle") or journal.get("title")) or None,
        issn=sorted({
            str(value).upper()
            for value in (journal.get("issn"), journal.get("essn"))
            if value
        }),
        publisher=compact_text(payload.get("publisher")) or None,
        authors=authors,
        article_type="preprint" if is_preprint else "journal-article",
        published_at=published_at,
        indexed_at=indexed_at or published_at,
        abstract_text=compact_text(payload.get("abstractText")) or None,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"} if pmid else {}),
        },
        open_access_status="open" if is_open else "unknown",
        open_access_url=f"https://europepmc.org/articles/{pmcid}" if is_open and pmcid else None,
        peer_review_status="preprint" if is_preprint else "peer_reviewed",
        source_payload={"europe_pmc": payload},
    )


__all__ = [
    "apply_europe_pmc",
    "compact_text",
    "normalize_crossref",
    "normalize_doi",
    "normalize_europe_pmc",
]
