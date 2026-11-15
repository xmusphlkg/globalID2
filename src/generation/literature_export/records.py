"""Project curated historical records with stable identity and public provenance."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from src.generation.literature_visualizations import _facet_slug, build_related_surveillance


def _normalize_seed_doi(value: Any) -> str | None:
    doi = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def _seed_identity(item: dict[str, Any]) -> tuple[str, str]:
    doi = _normalize_seed_doi(item.get("doi"))
    title = str(item.get("title") or "historical literature record")
    source = doi or f"{title.lower()}|{item.get('published_at') or 'unknown'}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    article_id = str(item.get("article_id") or f"lit_hist_{digest[:24]}")
    slug_base = _facet_slug(doi or title)[:260] or "historical-literature"
    return article_id, str(item.get("slug") or f"{slug_base}-{digest[:8]}")


def _seed_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        text = f"{text}-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        text = f"{text}-01"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _date_label(value: datetime | None, precision: str, *, lang: str) -> str | None:
    if value is None:
        return None
    if precision == "year":
        return f"{value.year}年" if lang == "zh" else str(value.year)
    if lang == "fr":
        months = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
        month = months[value.month - 1]
        return f"{month} {value.year}" if precision == "month" else f"{value.day} {month} {value.year}"
    if precision == "month":
        return value.strftime("%Y年%-m月") if lang == "zh" else value.strftime("%B %Y")
    return value.strftime("%Y年%-m月%-d日") if lang == "zh" else value.strftime("%B %-d, %Y")


def load_historical_seed_articles(seed_path: Path) -> list[dict[str, Any]]:
    """Load curated historical public-literature records from a static seed."""
    if not seed_path.exists():
        return []
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    articles = payload.get("articles") if isinstance(payload, dict) else None
    return [item for item in articles or [] if isinstance(item, dict)]


def _project_historical_seed_article(
    item: dict[str, Any],
    *,
    diseases_by_id: dict[str, dict[str, Any]],
    surveillance_coverage: dict[str, set[str]],
) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    published = _seed_datetime(item.get("published_at"))
    if not title or published is None:
        return None
    article_id, slug = _seed_identity(item)
    doi = _normalize_seed_doi(item.get("doi"))
    pmid = str(item.get("pmid") or "").strip() or None
    pmcid = str(item.get("pmcid") or "").strip() or None
    precision = str(item.get("publication_date_precision") or "day")
    source_urls = {
        **({"doi": f"https://doi.org/{doi}"} if doi else {}),
        **({"pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"} if pmid else {}),
        **({"pmc": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"} if pmcid else {}),
        **{str(key): str(value) for key, value in (item.get("source_urls") or {}).items() if value},
    }
    open_access_url = item.get("open_access_url") or source_urls.get("pmc")
    disease_rows: list[dict[str, Any]] = []
    for link in item.get("diseases") or []:
        disease_id = str(link.get("disease_id") or "").strip()
        if not disease_id:
            continue
        disease = diseases_by_id.get(disease_id) or {}
        disease_rows.append({
            "disease_id": disease_id,
            "slug": link.get("slug") or disease.get("slug"),
            "name_en": link.get("name_en") or disease.get("name_en") or disease.get("standard_name_en") or disease_id,
            "name_zh": link.get("name_zh") or disease.get("name_zh") or disease.get("standard_name_zh"),
            "name_fr": link.get("name_fr") or disease.get("name_fr"),
            "confidence": float(link.get("confidence") or 0.94),
        })
    country_rows = [
        {
            "code": str(link.get("code") or link.get("country_code") or "").upper(),
            "name_en": str(link.get("name_en") or link.get("country_name") or link.get("code") or ""),
            "name_zh": link.get("name_zh"),
            "confidence": float(link.get("confidence") or 0.82),
        }
        for link in item.get("countries") or []
        if link.get("code") or link.get("country_code")
    ]
    topic_rows = [
        {"name": str(link.get("name") or link.get("topic")), "confidence": float(link.get("confidence") or 0.76)}
        for link in item.get("topics") or []
        if link.get("name") or link.get("topic")
    ]
    raw_summary = item.get("summary") or {}
    summary: dict[str, dict[str, Any]] = {}
    for language, values in raw_summary.items():
        if not isinstance(values, dict):
            continue
        summary[str(language)] = {
            **values,
            "provenance": {
                "generated_by": "historical-literature-seed",
                "model": None,
                "provider": "curated",
                "quality_score": 1.0,
                "generated_at": item.get("curated_at"),
                "editorially_approved": True,
                "automatically_approved": False,
                "automation_policy_version": None,
                "publication_gate": "curated-historical-baseline",
            },
        }
    authors = [str(author) for author in item.get("authors") or [] if str(author).strip()]
    study_type = str(item.get("study_type") or "Journal article")
    article_diseases = sorted(disease_rows, key=lambda row: row["confidence"], reverse=True)
    article_countries = sorted(country_rows, key=lambda row: row["confidence"], reverse=True)
    return {
        "article_id": article_id,
        "slug": slug,
        "title": title,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "openalex_id": item.get("openalex_id"),
        "journal": item.get("journal"),
        "publisher": item.get("publisher"),
        "authors": authors,
        "article_type": str(item.get("article_type") or "journal-article"),
        "study_type": study_type,
        "published_at": published.isoformat(),
        "indexed_at": _seed_datetime(item.get("indexed_at") or item.get("curated_at")).isoformat()
        if _seed_datetime(item.get("indexed_at") or item.get("curated_at"))
        else published.isoformat(),
        "publication_date_precision": precision,
        "publication_date_label_en": item.get("publication_date_label_en") or _date_label(published, precision, lang="en"),
        "publication_date_label_zh": item.get("publication_date_label_zh") or _date_label(published, precision, lang="zh"),
        "publication_date_label_fr": item.get("publication_date_label_fr") or _date_label(published, precision, lang="fr"),
        "open_access_status": str(item.get("open_access_status") or ("open" if open_access_url else "unknown")),
        "open_access_url": open_access_url,
        "license_url": item.get("license_url"),
        "peer_review_status": str(item.get("peer_review_status") or "peer_reviewed"),
        "integrity_status": str(item.get("integrity_status") or "current"),
        "discovery_score": float(item.get("discovery_score") or 0.82),
        "is_featured": bool(item.get("is_featured") or False),
        "diseases": article_diseases,
        "countries": article_countries,
        "topics": sorted(topic_rows, key=lambda row: row["confidence"], reverse=True),
        "pathogens": [item for item in item.get("pathogens") or [] if isinstance(item, dict)],
        "pathogen_types": [item for item in item.get("pathogen_types") or [] if isinstance(item, dict)],
        "populations": [item for item in item.get("populations") or [] if isinstance(item, dict)],
        "research_domain": "curated_historical",
        "related_surveillance": build_related_surveillance(
            article_diseases,
            article_countries,
            surveillance_coverage,
        ),
        "summary": summary,
        "why_it_matters_en": item.get("why_it_matters_en")
        or (summary.get("en") or {}).get("public_health_relevance")
        or "Curated as historical baseline literature for interpreting long-running infectious-disease surveillance.",
        "why_it_matters_zh": item.get("why_it_matters_zh")
        or (summary.get("zh") or {}).get("public_health_relevance")
        or "该文献作为历史基线纳入，用于理解长期传染病监测背景。",
        "why_it_matters_fr": item.get("why_it_matters_fr")
        or (summary.get("fr") or {}).get("public_health_relevance")
        or "Cette étude est conservée comme référence historique pour interpréter le contexte de la surveillance des maladies infectieuses.",
        "why_it_matters_source": "historical_seed",
        "source_kind": "historical_seed",
        "classification_version": "curated-historical-v1",
        "historical_baseline": True,
        "content_tier": "curated_multilingual_evidence" if summary.get("fr") else "curated_bilingual_evidence",
        "indexable": True,
        "source_urls": {key: value for key, value in source_urls.items() if key in {"doi", "publisher", "pubmed", "pmc"}},
        "updated_at": _seed_datetime(item.get("curated_at")).isoformat()
        if _seed_datetime(item.get("curated_at"))
        else published.isoformat(),
    }
