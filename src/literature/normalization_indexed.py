"""Normalize and enrich records from Crossref, Europe PMC, and PubMed."""

from __future__ import annotations

from typing import Any

from .normalization_common import (
    _apply_oa_evidence,
    _boolean_signal,
    _date_from_parts,
    _first,
    _flexible_date,
    _integrity_status,
    _iso_datetime,
    _pubmed_date,
    _stable_identity,
    compact_text,
    is_open_license_url,
    normalize_doi,
    normalize_oa_url,
    normalize_openalex_id,
    sanitize_openalex_metadata,
    sanitize_unpaywall_metadata,
)
from .types import ArticleCandidate


def crossref_version_relations(message: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize Crossref preprint relations into one DOI-to-DOI direction."""

    current_doi = normalize_doi(message.get("DOI"))
    relation_payload = message.get("relation") or {}
    if not current_doi or not isinstance(relation_payload, dict):
        return []
    mappings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for relation_name, current_is_preprint in (
        ("is-preprint-of", True),
        ("has-preprint", False),
    ):
        entries = relation_payload.get(relation_name) or []
        entries = entries if isinstance(entries, list) else [entries]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id-type") or "").strip().casefold() != "doi":
                continue
            related_doi = normalize_doi(entry.get("id"))
            if not related_doi or related_doi == current_doi:
                continue
            preprint_doi, peer_reviewed_doi = (
                (current_doi, related_doi)
                if current_is_preprint
                else (related_doi, current_doi)
            )
            key = (preprint_doi, peer_reviewed_doi)
            if key in seen:
                continue
            seen.add(key)
            mappings.append({
                "relation_type": "preprint_to_peer_reviewed",
                "preprint_doi": preprint_doi,
                "peer_reviewed_doi": peer_reviewed_doi,
                "source": "crossref",
                **(
                    {"asserted_by": str(entry.get("asserted-by"))}
                    if entry.get("asserted-by")
                    else {}
                ),
            })
    return mappings


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
    primary_url = normalize_oa_url((resources.get("primary") or {}).get("URL") or message.get("URL"))
    licenses = message.get("license") or []
    license_url = next(
        (
            value
            for item in licenses
            if isinstance(item, dict) and (value := normalize_oa_url(item.get("URL")))
        ),
        None,
    )
    has_open_license = is_open_license_url(license_url)
    version_relations = crossref_version_relations(message)
    record_type = str(message.get("type") or "journal-article")
    is_preprint = (
        record_type.casefold() in {"posted-content", "preprint"}
        or str(message.get("subtype") or "").casefold() == "preprint"
        or any(relation.get("preprint_doi") == doi for relation in version_relations)
    )
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        title=title,
        journal=_first(message.get("container-title")),
        issn=sorted({str(value).upper() for value in (message.get("ISSN") or []) if value}),
        publisher=_first(message.get("publisher")),
        authors=authors,
        article_type="preprint" if is_preprint else record_type,
        published_at=published_at,
        indexed_at=indexed_at,
        abstract_text=compact_text(message.get("abstract")) or None,
        abstract_license=license_url,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"publisher": str(primary_url)} if primary_url else {}),
        },
        open_access_status="open" if has_open_license else "unknown",
        open_access_url=str(primary_url) if has_open_license and primary_url else None,
        license_url=str(license_url) if license_url else None,
        peer_review_status="preprint" if is_preprint else "peer_reviewed",
        integrity_status=_integrity_status(message),
        version_relations=version_relations,
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
            candidate.source_urls["pmc"] = f"https://pmc.ncbi.nlm.nih.gov/articles/{candidate.pmcid}/"
    if candidate.pmid:
        candidate.source_urls["pubmed"] = f"https://pubmed.ncbi.nlm.nih.gov/{candidate.pmid}/"
    candidate.source_payload = {**candidate.source_payload, "europe_pmc": payload}
    return candidate


def apply_pubmed_abstract(candidate: ArticleCandidate, payload: dict[str, Any]) -> ArticleCandidate:
    abstract = compact_text(payload.get("abstractText"))
    if abstract and len(abstract) > len(candidate.abstract_text or ""):
        candidate.abstract_text = abstract
        candidate.abstract_license = candidate.abstract_license or "PubMed abstract metadata"
    pmid = compact_text(payload.get("pmid"))
    if pmid:
        candidate.pmid = candidate.pmid or pmid
        candidate.source_urls["pubmed"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    candidate.source_payload = {**candidate.source_payload, "pubmed_efetch": payload}
    return candidate


def apply_unpaywall(candidate: ArticleCandidate, payload: dict[str, Any]) -> ArticleCandidate:
    location = payload.get("best_oa_location")
    if not isinstance(location, dict):
        location = {}
    is_open = _boolean_signal(payload.get("is_oa"))
    open_url = next(
        (
            url
            for value in (
                location.get("url_for_pdf"),
                location.get("url"),
                location.get("url_for_landing_page"),
            )
            if (url := normalize_oa_url(value))
        ),
        None,
    )
    _apply_oa_evidence(candidate, is_open=is_open, open_url=open_url)
    candidate.source_payload = {
        **candidate.source_payload,
        "unpaywall": sanitize_unpaywall_metadata(payload),
    }
    return candidate


def apply_openalex(candidate: ArticleCandidate, payload: dict[str, Any]) -> ArticleCandidate:
    openalex_id = normalize_openalex_id(payload.get("id"))
    if openalex_id:
        candidate.openalex_id = candidate.openalex_id or openalex_id
        candidate.source_urls.setdefault("openalex", f"https://openalex.org/{openalex_id}")

    open_access = payload.get("open_access")
    if not isinstance(open_access, dict):
        open_access = {}
    location = payload.get("best_oa_location")
    if not isinstance(location, dict):
        location = {}
    is_open = _boolean_signal(open_access.get("is_oa"))
    open_url = next(
        (
            url
            for value in (
                open_access.get("oa_url"),
                location.get("pdf_url"),
                location.get("landing_page_url"),
            )
            if (url := normalize_oa_url(value))
        ),
        None,
    )
    _apply_oa_evidence(candidate, is_open=is_open, open_url=open_url)
    candidate.source_payload = {
        **candidate.source_payload,
        "openalex": sanitize_openalex_metadata(payload),
    }
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


def normalize_pubmed(payload: dict[str, Any]) -> ArticleCandidate | None:
    """Normalize PubMed ESummary metadata into the shared candidate shape."""

    title = compact_text(payload.get("title"))
    if not title:
        return None
    article_ids = [item for item in payload.get("articleids") or [] if isinstance(item, dict)]
    ids_by_type = {
        str(item.get("idtype") or "").casefold(): compact_text(item.get("value"))
        for item in article_ids
        if compact_text(item.get("value"))
    }
    pmid = compact_text(payload.get("uid")) or ids_by_type.get("pubmed") or ids_by_type.get("pmid") or None
    doi = normalize_doi(ids_by_type.get("doi"))
    pmcid = ids_by_type.get("pmc") or ids_by_type.get("pmcid") or None
    published_at = next(
        (
            value
            for value in (
                _pubmed_date(payload.get("epubdate")),
                _pubmed_date(payload.get("pubdate")),
                _pubmed_date(payload.get("sortpubdate")),
            )
            if value is not None
        ),
        None,
    )
    article_id, slug = _stable_identity(doi, title, published_at)
    authors = [
        {"name": name}
        for author in payload.get("authors") or []
        if isinstance(author, dict) and (name := compact_text(author.get("name")))
    ]
    publication_types = [compact_text(value).casefold() for value in payload.get("pubtype") or []]
    is_preprint = any("preprint" in value for value in publication_types)
    issn = sorted({
        compact_text(value).upper()
        for value in (payload.get("issn"), payload.get("essn"))
        if compact_text(value)
    })
    return ArticleCandidate(
        article_id=article_id,
        slug=slug,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        title=title,
        journal=compact_text(payload.get("fulljournalname") or payload.get("source")) or None,
        issn=issn,
        publisher=compact_text(payload.get("publisher")) or None,
        authors=authors,
        article_type="preprint" if is_preprint else "journal-article",
        published_at=published_at,
        indexed_at=published_at,
        source_urls={
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
            **({"pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"} if pmid else {}),
            **({"pmc": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"} if pmcid else {}),
        },
        open_access_status="unknown",
        open_access_url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else None,
        peer_review_status="preprint" if is_preprint else "peer_reviewed",
        source_payload={"pubmed": payload},
    )
