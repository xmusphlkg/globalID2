"""Shared text, identity, metadata, and access-evidence normalization."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from html import unescape
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
        doi = doi.removeprefix(prefix)
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
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
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
