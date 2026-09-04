"""Normalize provider payloads without coupling the rest of the pipeline to them."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import unescape
import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from .types import ArticleCandidate


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_OPENALEX_ID_RE = re.compile(r"(?:^|/)(W\d+)$", re.IGNORECASE)
_DOI_IN_TEXT_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_OPEN_LICENSE_PATHS = (
    "creativecommons.org/licenses/",
    "creativecommons.org/publicdomain/",
    "nationalarchives.gov.uk/doc/open-government-licence/",
)
_OPENALEX_AUTHORSHIP_LIMIT = 50
_OPENALEX_INSTITUTION_LIMIT = 50
_OPENALEX_SUBJECT_LIMIT = 50
_OPENALEX_WORK_LINK_LIMIT = 50


def compact_text(value: Any) -> str:
    text = unescape(_TAG_RE.sub(" ", str(value or "")))
    return _SPACE_RE.sub(" ", text).strip()


def normalize_doi(value: Any) -> str | None:
    doi = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def normalize_openalex_id(value: Any) -> str | None:
    match = _OPENALEX_ID_RE.search(str(value or "").strip().rstrip("/"))
    return match.group(1).upper() if match else None


def normalize_oa_url(value: Any) -> str | None:
    """Accept only public HTTP(S) links suitable for an OA outbound link."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    if parsed.username or parsed.password or hostname.lower() == "localhost" or hostname.lower().endswith(".local"):
        return None
    try:
        if not ipaddress.ip_address(hostname).is_global:
            return None
    except ValueError:
        pass
    return text


def is_open_license_url(value: Any) -> bool:
    """Recognize explicit open-reuse licenses, excluding publisher/TDM terms."""
    normalized = normalize_oa_url(value)
    if not normalized:
        return False
    lowered = normalized.lower()
    return any(marker in lowered for marker in _OPEN_LICENSE_PATHS)


def _boolean_signal(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _bounded_text(value: Any, limit: int) -> str | None:
    text = compact_text(value)
    return text[:limit] if text else None


def _bounded_int(value: Any) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _openalex_entity_id(value: Any, prefix: str) -> str | None:
    match = re.search(rf"(?:^|/)({re.escape(prefix)}\d+)$", str(value or "").strip().rstrip("/"), re.IGNORECASE)
    return match.group(1).upper() if match else None


def _openalex_subject(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    display_name = _bounded_text(value.get("display_name"), 240)
    if not display_name:
        return None
    output: dict[str, Any] = {"display_name": display_name}
    entity_id = _openalex_entity_id(value.get("id"), "T") or _openalex_entity_id(value.get("id"), "C")
    if entity_id:
        output["id"] = entity_id
    try:
        score = max(0.0, min(1.0, float(value.get("score"))))
    except (TypeError, ValueError):
        score = None
    if score is not None:
        output["score"] = round(score, 6)
    return output


def sanitize_openalex_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only bounded, documented fields needed for OA, search, and graph audit."""
    output: dict[str, Any] = {}
    work_id = normalize_openalex_id(payload.get("id"))
    if work_id:
        output["id"] = work_id
    ids = payload.get("ids") if isinstance(payload.get("ids"), dict) else {}
    doi = normalize_doi(payload.get("doi") or ids.get("doi"))
    if doi:
        output["doi"] = doi

    open_access = payload.get("open_access") if isinstance(payload.get("open_access"), dict) else {}
    oa_url = normalize_oa_url(open_access.get("oa_url"))
    output["open_access"] = {
        **({"is_oa": bool(open_access["is_oa"])} if isinstance(open_access.get("is_oa"), bool) else {}),
        **({"oa_status": status} if (status := _bounded_text(open_access.get("oa_status"), 40)) else {}),
        **({"oa_url": oa_url} if oa_url else {}),
    }
    location = payload.get("best_oa_location") if isinstance(payload.get("best_oa_location"), dict) else {}
    sanitized_location = {
        key: normalized
        for key, raw in (
            ("pdf_url", location.get("pdf_url")),
            ("landing_page_url", location.get("landing_page_url")),
        )
        if (normalized := normalize_oa_url(raw))
    }
    for key, limit in (("license", 80), ("version", 40), ("source_type", 40)):
        if value := _bounded_text(location.get(key), limit):
            sanitized_location[key] = value
    if sanitized_location:
        output["best_oa_location"] = sanitized_location

    if primary_topic := _openalex_subject(payload.get("primary_topic")):
        output["primary_topic"] = primary_topic
    for field in ("topics", "keywords", "concepts"):
        rows = []
        seen = set()
        for value in payload.get(field) or []:
            item = _openalex_subject(value)
            if not item:
                continue
            key = (item.get("id"), item["display_name"].casefold())
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
            if len(rows) >= _OPENALEX_SUBJECT_LIMIT:
                break
        if rows:
            output[field] = rows

    institutions: list[dict[str, Any]] = []
    institution_ids: set[str] = set()
    authorships: list[dict[str, Any]] = []
    aggregate_countries: set[str] = set()
    for raw_authorship in payload.get("authorships") or []:
        if not isinstance(raw_authorship, dict):
            continue
        raw_author = raw_authorship.get("author") if isinstance(raw_authorship.get("author"), dict) else {}
        author_id = _openalex_entity_id(raw_author.get("id"), "A")
        countries = {
            str(value).strip().upper()
            for value in raw_authorship.get("countries") or []
            if re.fullmatch(r"[A-Za-z]{2}", str(value).strip())
        }
        linked_institutions: list[str] = []
        for raw_institution in raw_authorship.get("institutions") or []:
            if not isinstance(raw_institution, dict):
                continue
            institution_id = _openalex_entity_id(raw_institution.get("id"), "I")
            country_code = str(raw_institution.get("country_code") or "").strip().upper()
            if re.fullmatch(r"[A-Z]{2}", country_code):
                countries.add(country_code)
            if institution_id:
                linked_institutions.append(institution_id)
            if not institution_id or institution_id in institution_ids or len(institutions) >= _OPENALEX_INSTITUTION_LIMIT:
                continue
            institution = {"id": institution_id}
            if display_name := _bounded_text(raw_institution.get("display_name"), 240):
                institution["display_name"] = display_name
            if re.fullmatch(r"[A-Z]{2}", country_code):
                institution["country_code"] = country_code
            if institution_type := _bounded_text(raw_institution.get("type"), 60):
                institution["type"] = institution_type
            institutions.append(institution)
            institution_ids.add(institution_id)
        aggregate_countries.update(countries)
        if len(authorships) < _OPENALEX_AUTHORSHIP_LIMIT and (author_id or countries or linked_institutions):
            authorships.append({
                **({"author_id": author_id} if author_id else {}),
                "country_codes": sorted(countries),
                "institution_ids": list(dict.fromkeys(linked_institutions))[:_OPENALEX_INSTITUTION_LIMIT],
            })
    if institutions:
        output["institutions"] = institutions
    if authorships:
        output["authorships"] = authorships
    if aggregate_countries:
        output["author_countries"] = sorted(aggregate_countries)

    if (cited_by_count := _bounded_int(payload.get("cited_by_count"))) is not None:
        output["cited_by_count"] = cited_by_count
    for field in ("referenced_works", "related_works"):
        work_ids = list(dict.fromkeys(
            work_id
            for value in payload.get(field) or []
            if (work_id := normalize_openalex_id(value))
        ))[:_OPENALEX_WORK_LINK_LIMIT]
        if work_ids:
            output[field] = work_ids
        if field == "referenced_works":
            count = _bounded_int(payload.get("referenced_works_count"))
            output["referenced_works_count"] = count if count is not None else len(work_ids)
    return output


def sanitize_unpaywall_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain only bounded legal-OA evidence used by the application."""
    output: dict[str, Any] = {}
    if doi := normalize_doi(payload.get("doi")):
        output["doi"] = doi
    if isinstance(payload.get("is_oa"), bool):
        output["is_oa"] = payload["is_oa"]
    if status := _bounded_text(payload.get("oa_status"), 40):
        output["oa_status"] = status
    location = payload.get("best_oa_location") if isinstance(payload.get("best_oa_location"), dict) else {}
    sanitized_location: dict[str, Any] = {}
    for key in ("url_for_pdf", "url", "url_for_landing_page"):
        if url := normalize_oa_url(location.get(key)):
            sanitized_location[key] = url
    for key, limit in (("license", 80), ("version", 40), ("host_type", 40)):
        if value := _bounded_text(location.get(key), limit):
            sanitized_location[key] = value
    if sanitized_location:
        output["best_oa_location"] = sanitized_location
    if updated := _bounded_text(payload.get("updated"), 80):
        output["updated"] = updated
    return output


def _apply_oa_evidence(
    candidate: ArticleCandidate,
    *,
    is_open: bool | None,
    open_url: str | None,
) -> None:
    # Source application order carries the confidence hierarchy. Once a
    # higher-priority source has made a determination, lower-priority sources
    # can fill a missing URL but cannot reverse that determination.
    if candidate.open_access_status == "unknown" and is_open is not None:
        candidate.open_access_status = "open" if is_open else "closed"
    if (
        is_open
        and candidate.open_access_status != "closed"
        and candidate.open_access_url is None
        and open_url is not None
    ):
        candidate.open_access_url = open_url


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


def _pubmed_date(value: Any) -> datetime | None:
    text = compact_text(value)
    if not text:
        return None
    normalized = text.replace("/", "-")
    if parsed := _flexible_date(normalized.split()[0]):
        return parsed
    for fmt in ("%Y %b %d", "%Y %B %d", "%Y %b", "%Y %B", "%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
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


__all__ = [
    "apply_europe_pmc",
    "apply_openalex",
    "apply_unpaywall",
    "compact_text",
    "crossref_version_relations",
    "normalize_crossref",
    "normalize_doi",
    "normalize_biorxiv",
    "normalize_elsevier",
    "normalize_europe_pmc",
    "normalize_official_guidance",
    "normalize_oa_url",
    "normalize_openalex_id",
    "normalize_pubmed",
    "normalize_publisher_rss",
    "normalize_springer_nature",
    "sanitize_openalex_metadata",
    "sanitize_unpaywall_metadata",
]
