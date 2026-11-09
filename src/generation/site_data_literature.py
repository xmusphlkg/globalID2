"""Public Research Radar projection and artifact writer.

Editorially published records are the only full evidence records exported.
Review-state records may appear as compact, title-estimated associations; raw
abstracts and source payloads never cross this boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import (
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureSignalArticleLink,
    LiteratureStatusEvent,
    LiteratureSummary,
    LiteratureTopicLink,
)

# Private helpers remain importable for existing integrations.
from src.generation.literature_integrity import (
    _PUBLIC_INTEGRITY_STATUSES,
    _public_event_datetime,  # noqa: F401 - legacy import
    _public_integrity_source_url,  # noqa: F401 - legacy import
    _record_value,  # noqa: F401 - legacy import
    project_public_integrity_alerts,
)
from src.generation.literature_visualizations import (
    _add_months,  # noqa: F401
    _article_diseases,  # noqa: F401
    _article_topics,  # noqa: F401
    _facet_slug,
    _hotspot_article_reference,  # noqa: F401
    _iso_week,  # noqa: F401
    _month_key,  # noqa: F401
    _month_periods,  # noqa: F401
    _parse_public_datetime,
    _quarter_key,  # noqa: F401
    _quarter_periods,  # noqa: F401
    _quarter_start,  # noqa: F401
    build_emerging_topics,
    build_hotspot_visualizations,
    build_pipeline_funnel,
    build_publication_pulse,
    build_related_surveillance,
    build_surveillance_coverage_matrix,
)
from src.generation.research_shards import (
    PUBLIC_RESEARCH_SHARD_MAX_BYTES,
    _chunk_records,  # noqa: F401 - legacy import
    _compact_json_bytes,  # noqa: F401 - legacy import
    publish_browser_research_index,
)
from src.generation.site_data_queries import has_table
from src.generation.site_data_writer import (
    remove_stale_json_files,
    write_compact_json,
    write_pretty_json,
)
from src.literature.content_policy import content_policy
from src.literature.knowledge_graph import build_knowledge_graph
from src.literature.recommendations import attach_related_research
from src.literature.weekly_ai_review import load_weekly_ai_review_registry
from src.literature.weekly_briefs import (
    enrich_weekly_briefs,
    load_weekly_review_registry,
)

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_LITERATURE_SEED_PATH = ROOT / "configs" / "literature" / "historical_seed.json"
LITERATURE_DISEASE_ALIASES_PATH = ROOT / "configs" / "literature" / "disease_aliases.json"


def _title_estimated_literature_links(
    title: str,
    diseases_by_id: dict[str, dict[str, Any]],
    aliases_by_id: dict[str, list[str]] | None = None,
    prepared_aliases: list[tuple[str, str, str, re.Pattern[str]]] | None = None,
) -> list[dict[str, Any]]:
    """Make conservative disease associations from canonical names and aliases."""
    normalized = _normalize_title_match_text(title)
    candidates: list[tuple[str, str, str, int, int]] = []
    for disease_id, raw_term, term, pattern in prepared_aliases or _prepare_title_disease_aliases(
        diseases_by_id, aliases_by_id or {}
    ):
        for match in pattern.finditer(normalized):
            candidates.append((disease_id, raw_term, term, match.start(), match.end()))

    # Prefer the longest concept mention when one disease name is embedded in
    # a more specific name (for example, SARS inside SARS-CoV-2). A separate
    # mention elsewhere in the title remains eligible.
    specific_spans = [candidate[3:] for candidate in candidates]
    best_by_disease: dict[str, tuple[str, str, int]] = {}
    for disease_id, raw_term, term, start, end in candidates:
        if _title_term_is_negated(normalized, start, end):
            continue
        if any(
            other_start <= start and end <= other_end
            and (other_start, other_end) != (start, end)
            for other_start, other_end in specific_spans
        ):
            continue
        current = best_by_disease.get(disease_id)
        if current is None or len(term) > current[2]:
            best_by_disease[disease_id] = (raw_term, term, len(term))

    matches: list[dict[str, Any]] = []
    for disease_id, (hit, _, _) in sorted(best_by_disease.items(), key=lambda item: -item[1][2]):
        # A subtype mention is more informative than the generic influenza
        # concept for title-only estimates; avoid adding a second broad link.
        if disease_id == "D038" and {"D014", "D015", "D016"}.intersection(best_by_disease):
            continue
        disease = diseases_by_id[disease_id]
        matches.append({
            "disease_id": str(disease_id),
            "slug": disease.get("slug"),
            "name_en": disease.get("name_en") or disease.get("standard_name_en") or str(disease_id),
            "name_zh": disease.get("name_zh") or disease.get("standard_name_zh"),
            "source": "title_estimate",
            "matched_term": hit,
        })
    return matches[:5]


def _title_term_is_negated(title: str, start: int, end: int) -> bool:
    left_context = title[max(0, start - 24):start]
    right_context = title[end:min(len(title), end + 20)].lstrip()
    return bool(
        re.search(r"(?:^|\s)(?:non|without|no|absence of|free of|negative for)\s+$", left_context)
        or re.match(r"(?:negative|neg|uninfected|free)\b", right_context)
    )


def _prepare_title_disease_aliases(
    diseases_by_id: dict[str, dict[str, Any]], aliases_by_id: dict[str, list[str]]
) -> list[tuple[str, str, str, re.Pattern[str]]]:
    prepared: list[tuple[str, str, str, re.Pattern[str]]] = []
    for disease_id, disease in diseases_by_id.items():
        terms = {
            str(disease.get(key) or "").strip()
            for key in ("name_en", "standard_name_en", "name_zh", "standard_name_zh", "name_fr", "standard_name_fr")
        }
        terms.update(str(term).strip() for term in aliases_by_id.get(str(disease_id), []))
        for raw_term in terms:
            term = _normalize_title_match_text(raw_term)
            if not _usable_title_alias(term):
                continue
            if re.search(r"[\u3400-\u9fff]", term) and not re.search(r"[a-z0-9]", term):
                pattern = re.compile(re.escape(term))
            else:
                pattern = re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])", flags=re.UNICODE)
            prepared.append((str(disease_id), raw_term, term, pattern))
    return prepared


def _normalize_title_match_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("_", " ")
    return re.sub(r"[^\w\u3400-\u9fff]+", " ", value, flags=re.UNICODE).strip()


def _usable_title_alias(term: str) -> bool:
    if not term:
        return False
    # Two-character Chinese disease names are useful; short Latin abbreviations
    # are too collision-prone unless expanded to a longer phrase in the config.
    if re.search(r"[\u3400-\u9fff]", term):
        return len(term.replace(" ", "")) >= 2
    return len(term.replace(" ", "")) >= 3


def _title_term_spans(title: str, term: str) -> list[tuple[int, int]]:
    if re.search(r"[\u3400-\u9fff]", term) and not re.search(r"[a-z0-9]", term):
        return [(match.start(), match.end()) for match in re.finditer(re.escape(term), title)]
    pattern = rf"(?<![\w]){re.escape(term)}(?![\w])"
    return [(match.start(), match.end()) for match in re.finditer(pattern, title, flags=re.UNICODE)]


def _load_title_disease_aliases() -> dict[str, list[str]]:
    merged: dict[str, set[str]] = defaultdict(set)
    try:
        payload = json.loads(LITERATURE_DISEASE_ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if isinstance(aliases, dict):
        for disease_id, terms in aliases.items():
            if isinstance(terms, list):
                merged[str(disease_id)].update(str(term).strip() for term in terms if str(term).strip())
    try:
        ontology = json.loads((ROOT / "configs" / "disease_ontology.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ontology = {}
    for concept in ontology.get("concepts", []) if isinstance(ontology, dict) else []:
        if not isinstance(concept, dict):
            continue
        labels = concept.get("legacy_labels") or {}
        if not isinstance(labels, dict):
            continue
        for values in labels.values():
            if isinstance(values, list):
                merged[str(concept.get("id") or "")].update(
                    str(term).strip() for term in values if str(term).strip()
                )
    return {disease_id: sorted(terms) for disease_id, terms in merged.items() if disease_id}


def _title_contains_term(title: str, term: str) -> bool:
    normalized_title = _normalize_title_match_text(title)
    normalized_term = _normalize_title_match_text(term)
    return bool(_usable_title_alias(normalized_term) and _title_term_spans(normalized_title, normalized_term))


def _title_estimated_topics(title: str) -> list[dict[str, str]]:
    vocabulary = {
        "vaccination": ("vaccin", "immunization", "immunisation"),
        "antimicrobial resistance": ("antimicrobial resistance", "antibiotic resistance"),
        "outbreak response": ("outbreak", "epidemic", "cluster"),
        "surveillance": ("surveillance", "monitoring"),
        "genomic surveillance": ("genomic surveillance", "genomic epidemiology", "whole genome"),
        "diagnostics": ("diagnostic", "detection assay", "testing"),
        "epidemiology": ("epidemiology", "incidence", "prevalence", "transmission"),
        "One Health": ("one health", "zoonotic", "animal reservoir"),
    }
    normalized = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", title.casefold())
    results = []
    for topic, terms in vocabulary.items():
        matched = next((term for term in terms if _title_contains_term(normalized, term)), None)
        if matched:
            results.append({"name": topic, "source": "title_estimate", "matched_term": matched})
    return results


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


def load_historical_seed_articles(seed_path: Path = HISTORICAL_LITERATURE_SEED_PATH) -> list[dict[str, Any]]:
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


def append_historical_seed_articles(
    projected: list[dict[str, Any]],
    *,
    diseases_by_id: dict[str, dict[str, Any]],
    surveillance_coverage: dict[str, set[str]] | None = None,
    seed_path: Path = HISTORICAL_LITERATURE_SEED_PATH,
) -> int:
    """Append curated historical records that are not already in the export."""
    existing_article_ids = {str(item.get("article_id") or "") for item in projected}
    existing_dois = {_normalize_seed_doi(item.get("doi")) for item in projected if item.get("doi")}
    added = 0
    for item in load_historical_seed_articles(seed_path):
        article_id, _ = _seed_identity(item)
        doi = _normalize_seed_doi(item.get("doi"))
        if article_id in existing_article_ids or (doi and doi in existing_dois):
            continue
        projected_article = _project_historical_seed_article(
            item,
            diseases_by_id=diseases_by_id,
            surveillance_coverage=surveillance_coverage or {},
        )
        if projected_article is None:
            continue
        projected.append(projected_article)
        existing_article_ids.add(projected_article["article_id"])
        if projected_article.get("doi"):
            existing_dois.add(_normalize_seed_doi(projected_article["doi"]))
        added += 1
    return added


def build_publication_timeline(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate public publication volume by month without implying causality."""
    counts = Counter(
        _parse_public_datetime(item["published_at"]).strftime("%Y-%m")
        for item in articles
        if item.get("published_at")
    )
    return [
        {"month": month, "publication_count": count}
        for month, count in sorted(counts.items())
    ]


def build_disease_evidence_events(
    articles: list[dict[str, Any]],
    *,
    disease_id: str | None = None,
    title_aliases: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Project dated guidance and policy evidence with a title-confirmed disease link."""

    events: list[dict[str, Any]] = []
    aliases = title_aliases if title_aliases is not None else (_load_title_disease_aliases() if disease_id else {})
    for item in articles:
        if not item.get("published_at"):
            continue
        study_type = str(item.get("study_type") or "")
        title = str(item.get("title") or "")
        if disease_id:
            linked_disease = next(
                (disease for disease in item.get("diseases") or []
                 if str(disease.get("disease_id") or "").upper() == disease_id.upper()),
                None,
            )
            if not linked_disease or not _title_estimated_literature_links(
                title, {disease_id.upper(): linked_disease}, aliases
            ):
                continue
        title_text = title.casefold()
        topics = {str(topic.get("name") or "").casefold() for topic in item.get("topics") or []}
        event_type: str | None = None
        if study_type == "Guideline" or any(term in title_text for term in ("guideline", "guidance", "consensus statement")):
            event_type = "guideline_publication"
        elif (
            "vaccination" in topics
            and ("health policy" in topics or any(term in title_text for term in ("vaccine policy", "vaccination policy", "recommendation")))
        ):
            event_type = "vaccine_policy_evidence"
        if event_type is None:
            continue
        events.append({
            "event_type": event_type,
            "date": _parse_public_datetime(item["published_at"]).date().isoformat(),
            "title": title,
            "slug": item.get("slug"),
            "study_type": study_type,
            "publisher": item.get("publisher"),
            "source_kind": item.get("source_kind"),
        })
    return sorted(events, key=lambda event: (event["date"], event["title"]), reverse=True)


def _article_reference(
    article: dict[str, Any],
    relation_level: str,
    *,
    evidence_age_days: int | None = None,
    recency_status: str = "current_window",
) -> dict[str, Any]:
    return {
        "article_id": article.get("article_id"),
        "slug": article.get("slug"),
        "title": article.get("title"),
        "journal": article.get("journal"),
        "study_type": article.get("study_type"),
        "published_at": article.get("published_at"),
        "relation_level": relation_level,
        "recency_status": recency_status,
        "evidence_age_days": evidence_age_days,
        "detail_available": not bool(article.get("related_only")),
        "related_only": bool(article.get("related_only")),
        "content_tier": article.get("content_tier"),
    }


def _optional_public_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text}-01" if re.fullmatch(r"\d{4}-\d{2}", text) else f"{text}-01-01")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _signal_evidence_anchor(
    item: dict[str, Any],
    snapshot: dict[str, Any],
) -> datetime | None:
    """Return the release-time anchor used for the exact-evidence recency gate."""

    candidates = (
        snapshot.get("generated_at"),
        (snapshot.get("report") or {}).get("as_of"),
        item.get("data_through"),
        item.get("published_at"),
        snapshot.get("data_through"),
    )
    return next((parsed for value in candidates if (parsed := _optional_public_datetime(value))), None)


def _signal_geographies(item: dict[str, Any]) -> list[dict[str, str]]:
    geographies: list[dict[str, str]] = []
    if item.get("country_code"):
        geography = {
            "code": str(item["country_code"]),
            "name_en": str(item.get("country_name") or item["country_code"]),
        }
        if item.get("country_name_fr"):
            geography["name_fr"] = str(item["country_name_fr"])
        geographies.append(geography)
    for raw_geography in item.get("geographies") or []:
        code = str(raw_geography.get("code") or "").strip()
        if code and all(existing["code"] != code for existing in geographies):
            geography = {
                "code": code,
                "name_en": str(raw_geography.get("name") or code),
            }
            if raw_geography.get("name_fr"):
                geography["name_fr"] = str(raw_geography["name_fr"])
            geographies.append(geography)
    return geographies


def _situation_evidence_items(
    snapshot: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Project the notice-based Situation snapshot into the literature-linking shape."""

    return [
        (section, item)
        for section in ("increasing", "emerging", "unusual")
        for item in snapshot.get(section) or []
    ]


def build_surveillance_evidence(
    articles: list[dict[str, Any]],
    situation_snapshot: dict[str, Any] | None,
    *,
    diseases_by_id: dict[str, dict[str, Any]] | None = None,
    relation_decisions: list[dict[str, Any]] | None = None,
    max_exact_evidence_age_days: int = 730,
) -> dict[str, Any]:
    """Connect a Situation Room snapshot to published literature conservatively.

    Exact evidence requires disease and geography classifier confidence of at
    least 0.78 and a publication date within the configured recency window.
    Older records remain historical context and never close a current evidence
    gap. Disease-only matches are also contextual. The projection does not
    invoke a model.
    """
    diseases_by_id = diseases_by_id or {}
    visibility = (
        "public"
        if situation_snapshot and situation_snapshot.get("public_enabled")
        else "shadow"
        if situation_snapshot
        else "unavailable"
    )
    empty = {
        "schema_version": "research_surveillance.v1",
        "available": False,
        "visibility": visibility,
        "snapshot_id": None,
        "generated_at": None,
        "data_through": None,
        "method_version": None,
        "metrics": {
            "active_signals": 0,
            "signals_with_exact_evidence": 0,
            "exact_evidence_links": 0,
            "signals_with_disease_context": 0,
            "contextual_evidence_links": 0,
            "evidence_gaps": 0,
        },
        "signals": [],
        "evidence_gaps": [],
        "methodology": {
            "en": "No eligible Situation Room snapshot is available.",
            "zh": "当前没有可用的全球态势室快照。",
        },
        "exact_evidence_max_age_days": max_exact_evidence_age_days,
    }
    if not situation_snapshot:
        return empty

    article_disease_ids: dict[str, set[str]] = {}
    article_country_codes: dict[str, set[str]] = {}
    for article in articles:
        article_id = str(article.get("article_id") or "")
        article_disease_ids[article_id] = {
            str(disease.get("disease_id"))
            for disease in article.get("diseases") or []
            if disease.get("disease_id") and float(disease.get("confidence") or 0) >= 0.78
        }
        article_country_codes[article_id] = {
            str(country.get("code") or country.get("country_code"))
            for country in article.get("countries") or []
            if (country.get("code") or country.get("country_code"))
            and float(country.get("confidence") or 0) >= 0.78
        }
    decisions = {
        (str(item.get("signal_id") or ""), str(item.get("article_id") or "")): item
        for item in relation_decisions or []
        if item.get("signal_id") and item.get("article_id")
    }

    signals: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    related_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_signal_ids: set[str] = set()
    for section, item in _situation_evidence_items(situation_snapshot):
            signal_id = str(item.get("id") or "")
            disease_id = str(item.get("disease_id") or "")
            if not signal_id or not disease_id or signal_id in seen_signal_ids:
                continue
            seen_signal_ids.add(signal_id)
            geographies = _signal_geographies(item)
            signal_country_codes = {geography["code"] for geography in geographies}
            disease_meta = diseases_by_id.get(disease_id) or {}
            disease_name_en = str(
                disease_meta.get("name_en")
                or disease_meta.get("standard_name_en")
                or item.get("disease_name")
                or disease_id
            )
            disease_name_zh = (
                disease_meta.get("name_zh")
                or disease_meta.get("standard_name_zh")
                or disease_name_en
            )
            disease_name_fr = (
                disease_meta.get("name_fr")
                or disease_meta.get("standard_name_fr")
                or item.get("disease_name_fr")
            )
            exact_articles: list[dict[str, Any]] = []
            context_articles: list[dict[str, Any]] = []
            evidence_anchor = _signal_evidence_anchor(item, situation_snapshot)
            for article in articles:
                article_id = str(article.get("article_id") or "")
                decision = decisions.get((signal_id, article_id))
                if decision and decision.get("status") == "rejected":
                    continue
                article_published = _optional_public_datetime(article.get("published_at"))
                related_only = bool(article.get("related_only"))
                evidence_age_days = (
                    max(0, (evidence_anchor.date() - article_published.date()).days)
                    if evidence_anchor and article_published
                    else None
                )
                outside_exact_window = bool(
                    evidence_age_days is not None
                    and evidence_age_days > max_exact_evidence_age_days
                )
                date_unverifiable = evidence_anchor is not None and article_published is None
                if decision and decision.get("status") == "confirmed":
                    relation_level = str(decision.get("relation_level") or "disease_context")
                    requested_exact = relation_level == "exact_disease_geography"
                    is_exact = (
                        requested_exact
                        and not related_only
                        and not outside_exact_window
                        and not date_unverifiable
                    )
                    if requested_exact and outside_exact_window:
                        relation_level = "historical_disease_geography_context"
                    elif requested_exact and date_unverifiable:
                        relation_level = "disease_context"
                    elif requested_exact and related_only:
                        relation_level = "disease_context"
                else:
                    if disease_id not in article_disease_ids.get(article_id, set()):
                        continue
                    geographic_match = bool(
                        signal_country_codes
                        and signal_country_codes.intersection(article_country_codes.get(article_id, set()))
                    )
                    # A classifier-only record without a publication date cannot
                    # demonstrate that it is current enough to close a signal gap.
                    is_exact = (
                        geographic_match
                        and not related_only
                        and not outside_exact_window
                        and not date_unverifiable
                    )
                    relation_level = (
                        "exact_disease_geography"
                        if is_exact
                        else "historical_disease_geography_context"
                        if geographic_match and outside_exact_window
                        else "disease_context"
                    )
                reference = _article_reference(
                    article,
                    relation_level,
                    evidence_age_days=evidence_age_days,
                    recency_status=(
                        "outside_exact_window"
                        if outside_exact_window
                        else "date_unverifiable"
                        if evidence_anchor is not None and article_published is None
                        else "current_window"
                    ),
                )
                (exact_articles if is_exact else context_articles).append(reference)
            exact_articles.sort(key=lambda article: article.get("published_at") or "", reverse=True)
            context_articles.sort(key=lambda article: article.get("published_at") or "", reverse=True)

            window = item.get("window") or {}
            risk = item.get("risk") or {}
            signal = {
                "signal_id": signal_id,
                "section": section,
                "kind": item.get("kind") or ("official_event" if section == "emerging" else "statistical_signal"),
                "title": item.get("title"),
                "disease_id": disease_id,
                "disease_name_en": disease_name_en,
                "disease_name_zh": disease_name_zh,
                **({"disease_name_fr": disease_name_fr} if disease_name_fr else {}),
                "disease_slug": disease_meta.get("slug"),
                "geographies": geographies,
                "data_through": item.get("data_through") or item.get("published_at"),
                "source_label": item.get("source_label") or item.get("source"),
                "window": {
                    "label": window.get("label"),
                    "current": window.get("current"),
                    "previous": window.get("previous"),
                    "absolute_change": window.get("absolute_change"),
                    "change_pct": window.get("change_pct"),
                },
                "risk": {
                    "score": risk.get("score"),
                    "level": risk.get("level"),
                    "confidence": risk.get("confidence") or item.get("confidence"),
                },
                "signal_level": item.get("signal_level"),
                "situation_url": "/situation/",
                "visibility": visibility,
                "coverage_status": (
                    "exact_evidence"
                    if exact_articles
                    else "disease_context_only"
                    if context_articles
                    else "coverage_gap"
                ),
                "exact_article_count": len(exact_articles),
                "context_article_count": len(context_articles),
                "historical_context_article_count": sum(
                    article.get("recency_status") == "outside_exact_window"
                    for article in context_articles
                ),
                "exact_articles": exact_articles[:4],
                "context_articles": context_articles[:4],
            }
            signals.append(signal)
            if not exact_articles:
                gaps.append({
                    "signal_id": signal_id,
                    "disease_id": disease_id,
                    "disease_name_en": disease_name_en,
                    "disease_name_zh": disease_name_zh,
                    **({"disease_name_fr": disease_name_fr} if disease_name_fr else {}),
                    "geographies": geographies,
                    "gap_type": "geography_coverage_gap" if context_articles else "catalogue_coverage_gap",
                    "section": section,
                    "kind": signal["kind"],
                    "data_through": signal["data_through"],
                    "risk": signal["risk"],
                    "context_article_count": len(context_articles),
                    "note_en": (
                        "Disease-level literature is available, but no recent published article has a high-confidence match to the signal geography."
                        if context_articles
                        else "No high-confidence disease-and-geography match exists in the current published Research Radar catalogue."
                    ),
                    "note_zh": (
                        "已有疾病层面的文献背景，但尚无近期已发布文献与该信号地区形成高置信度匹配。"
                        if context_articles
                        else "当前研究雷达公开目录中没有与该疾病及地区形成高置信度匹配的文献。"
                    ),
                })
            for reference in [*exact_articles, *context_articles]:
                article_id = str(reference.get("article_id") or "")
                related_by_article[article_id].append({
                    "signal_id": signal_id,
                    "section": section,
                    "kind": signal["kind"],
                    "title": signal["title"],
                    "disease_id": disease_id,
                    "disease_name_en": disease_name_en,
                    "disease_name_zh": disease_name_zh,
                    **({"disease_name_fr": disease_name_fr} if disease_name_fr else {}),
                    "geographies": geographies,
                    "data_through": signal["data_through"],
                    "window": signal["window"],
                    "risk": signal["risk"],
                    "relation_level": reference["relation_level"],
                    "situation_url": signal["situation_url"],
                    "visibility": visibility,
                })

    exact_links = sum(signal["exact_article_count"] for signal in signals)
    context_links = sum(signal["context_article_count"] for signal in signals)
    return {
        **empty,
        "available": True,
        "snapshot_id": situation_snapshot.get("snapshot_id")
        or (situation_snapshot.get("report") or {}).get("report_id"),
        "generated_at": situation_snapshot.get("generated_at")
        or (situation_snapshot.get("report") or {}).get("as_of"),
        "data_through": situation_snapshot.get("data_through")
        or (situation_snapshot.get("data_currency") or {}).get("latest_data_through"),
        "method_version": situation_snapshot.get("method_version")
        or (situation_snapshot.get("method") or {}).get("version"),
        "metrics": {
            "active_signals": len(signals),
            "signals_with_exact_evidence": sum(bool(signal["exact_article_count"]) for signal in signals),
            "exact_evidence_links": exact_links,
            "signals_with_disease_context": sum(bool(signal["context_article_count"]) for signal in signals),
            "contextual_evidence_links": context_links,
            "evidence_gaps": len(gaps),
        },
        "signals": signals,
        "evidence_gaps": gaps,
        "related_by_article": dict(related_by_article),
        "methodology": {
            "en": f"Signals come unchanged from the Situation Room snapshot. Exact links require classifier confidence of at least 0.78 for both disease and geography and publication within {max_exact_evidence_age_days} days of the signal release anchor; older or disease-only links are contextual. Gaps describe this catalogue's coverage, not the absence of research.",
            "zh": f"信号原样来自全球态势室快照。精确关联要求疾病和地区分类置信度均不低于 0.78，且论文发表于信号发布锚点前 {max_exact_evidence_age_days} 天内；更早或仅疾病匹配的文献只作为背景。缺口描述的是本目录覆盖情况，并不表示相关研究不存在。",
            "fr": f"Les signaux sont repris tels quels du snapshot de la Situation Room. Les liens exacts exigent une confiance d’au moins 0,78 pour la maladie et la géographie, ainsi qu’une publication dans les {max_exact_evidence_age_days} jours précédant l’ancrage de diffusion du signal ; les liens plus anciens ou limités à la maladie restent contextuels. Les lacunes décrivent la couverture de ce catalogue, et non l’absence de recherche.",
        },
    }


def attach_surveillance_evidence(
    payload: dict[str, Any],
    situation_snapshot: dict[str, Any] | None,
    *,
    diseases_by_id: dict[str, dict[str, Any]] | None = None,
    related_cache_path: Path | None = None,
) -> dict[str, Any]:
    """Attach signal evidence and article backlinks without mutating input."""
    evidence_articles = [
        *(payload.get("articles") or []),
        *(payload.get("related_only_articles") or []),
    ]
    projection = build_surveillance_evidence(
        evidence_articles,
        situation_snapshot,
        diseases_by_id=diseases_by_id,
        relation_decisions=payload.get("_signal_article_links") or [],
    )
    related_by_article = projection.pop("related_by_article", {})
    projected_articles = attach_related_research([
        {
            **article,
            "related_signals": related_by_article.get(str(article.get("article_id") or ""), []),
        }
        for article in payload.get("articles") or []
    ], cache_path=related_cache_path)
    by_id = {article["article_id"]: article for article in projected_articles}
    public_payload = {key: value for key, value in payload.items() if not key.startswith("_")}
    visualizations = {
        **(public_payload.get("visualizations") or {}),
        "coverage_matrix": build_surveillance_coverage_matrix(projection),
    }
    projected_weekly_briefs = [
        {
            **brief,
            "articles": [by_id.get(article.get("article_id"), article) for article in brief.get("articles") or []],
        }
        for brief in payload.get("weekly_briefs") or []
    ]
    return {
        **public_payload,
        "articles": projected_articles,
        "related_only_articles": [
            {
                **article,
                "related_signals": related_by_article.get(str(article.get("article_id") or ""), []),
            }
            for article in payload.get("related_only_articles") or []
        ],
        "featured": [by_id.get(article.get("article_id"), article) for article in payload.get("featured") or []],
        "reviews_and_guidelines": [
            by_id.get(article.get("article_id"), article)
            for article in payload.get("reviews_and_guidelines") or []
        ],
        "disease_articles": {
            disease_id: [by_id.get(article.get("article_id"), article) for article in articles]
            for disease_id, articles in (payload.get("disease_articles") or {}).items()
        },
        "country_articles": {
            country_code: [by_id.get(article.get("article_id"), article) for article in articles]
            for country_code, articles in (payload.get("country_articles") or {}).items()
        },
        "topic_articles": {
            topic_slug: [by_id.get(article.get("article_id"), article) for article in articles]
            for topic_slug, articles in (payload.get("topic_articles") or {}).items()
        },
        "weekly_briefs": enrich_weekly_briefs(
            projected_weekly_briefs,
            surveillance_evidence=projection,
        ),
        "surveillance_evidence": projection,
        "visualizations": visualizations,
    }


def empty_literature_export() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "last_updated": None,
        "metrics": {
            "total_public_articles": 0,
            "title_estimated_related_records": 0,
            "title_estimated_disease_links": 0,
            "title_estimated_topic_links": 0,
            "preprints_total": 0,
            "integrity_alerts_total": 0,
            "withheld_metadata_only": 0,
            "public_article_limit": None,
            "historical_baseline_articles": 0,
            "diseases_total": 0,
            "countries_total": 0,
            "papers_last_7_days": 0,
            "diseases_last_7_days": 0,
            "countries_last_7_days": 0,
            "reviews_and_guidelines_last_7_days": 0,
        },
        "featured": [],
        "articles": [],
        "bibliographic_articles": [],
        "title_estimated_related_records": [],
        "preprints": [],
        "related_only_articles": [],
        "integrity_alerts": [],
        "historical_baseline": [],
        "reviews_and_guidelines": [],
        "emerging_topics": [],
        "knowledge_graph": build_knowledge_graph([]),
        "visualizations": {
            "publication_pulse": [],
            "pipeline_funnel": build_pipeline_funnel(
                total=0, review=0, published=0, excluded=0, summarized=0, exact_linked=0
            ),
            "completeness": [],
            "coverage_matrix": build_surveillance_coverage_matrix({}),
            "hotspots": build_hotspot_visualizations([]),
        },
        "disease_articles": {},
        "country_articles": {},
        "topic_articles": {},
        "facets": {"diseases": [], "countries": [], "topics": [], "weeks": []},
        "publication_timeline": [],
        "pipeline_funnel": [],
        "completeness": [],
        "weekly_briefs": [],
        "surveillance_evidence": build_surveillance_evidence([], None),
        "_signal_article_links": [],
    }


def partition_public_literature_articles(
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep peer-reviewed research and preprints in separate public streams.

    ``publication_status`` is the persisted editorial gate.  The public
    projection exposes it as ``editorial_status`` so downstream consumers can
    enforce the same boundary without depending on the database model name.
    """

    published = [
        article
        for article in articles
        if str(article.get("editorial_status") or "").lower() == "published"
    ]
    peer_reviewed = [
        article
        for article in published
        if str(article.get("peer_review_status") or "").lower() == "peer_reviewed"
    ]
    preprints = [
        article
        for article in published
        if str(article.get("peer_review_status") or "").lower() == "preprint"
    ]
    return peer_reviewed, preprints


def apply_public_summary_gate(
    peer_reviewed: list[dict[str, Any]],
    preprints: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Withhold records until both published language summaries are present."""
    withheld = sum(
        article.get("indexable") is not True
        for article in [*peer_reviewed, *preprints]
    )
    return (
        [article for article in peer_reviewed if article.get("indexable") is True],
        [article for article in preprints if article.get("indexable") is True],
        withheld,
    )


def bibliographic_catalogue_records(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project approved records without evidence pages to a source-linked tier."""
    fields = (
        "article_id", "slug", "title", "doi", "pmid", "journal", "publisher",
        "authors", "study_type", "published_at", "publication_week", "open_access_status",
        "open_access_url", "peer_review_status", "discovery_score", "diseases", "countries",
        "topics", "pathogens", "pathogen_types", "populations", "publication_date_label_en",
        "publication_date_label_zh", "publication_date_label_fr", "integrity_status", "editorial_status",
    )
    return [
        {**{key: article.get(key) for key in fields},
         "content_tier": "bibliographic", "detail_available": False}
        for article in articles
        if article.get("editorial_status") == "published"
        and article.get("peer_review_status") == "peer_reviewed"
        and article.get("integrity_status") not in {"retracted", "expression_of_concern"}
        and article.get("indexable") is not True
        and (article.get("doi") or article.get("pmid"))
        and article.get("research_domain") != "plant_only"
    ]


async def collect_literature_export(
    session: AsyncSession,
    *,
    diseases_by_id: dict[str, dict[str, Any]],
    surveillance_coverage: dict[str, set[str]] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    if not await has_table(session, "literature_articles"):
        return empty_literature_export()
    now = datetime.now(timezone.utc)
    status_counts = (
        await session.execute(
            select(
                func.count(LiteratureArticle.id).label("total"),
                func.count().filter(LiteratureArticle.publication_status == "review").label("review"),
                func.count().filter(LiteratureArticle.publication_status == "excluded").label("excluded"),
                func.count().filter(LiteratureArticle.publication_status == "published").label("published"),
            )
        )
    ).one()
    articles = (
        await session.execute(
            select(LiteratureArticle)
            .where(
                LiteratureArticle.publication_status == "published",
                LiteratureArticle.integrity_status.notin_(("retracted", "expression_of_concern")),
                LiteratureArticle.peer_review_status.in_(("peer_reviewed", "preprint")),
                LiteratureArticle.published_at.is_not(None),
                LiteratureArticle.published_at <= now,
            )
            .order_by(LiteratureArticle.is_featured.desc(), LiteratureArticle.discovery_score.desc(), LiteratureArticle.published_at.desc())
        )
    ).scalars().all()
    review_candidates = (
        await session.execute(
            select(
                LiteratureArticle.article_id,
                LiteratureArticle.title,
                LiteratureArticle.doi,
                LiteratureArticle.pmid,
                LiteratureArticle.journal,
                LiteratureArticle.published_at,
                LiteratureArticle.peer_review_status,
                LiteratureArticle.abstract_text,
                LiteratureArticle.open_access_status,
                LiteratureArticle.open_access_url,
                LiteratureArticle.pmcid,
                LiteratureArticle.study_type,
                LiteratureArticle.article_type,
            )
            .where(
                LiteratureArticle.publication_status == "review",
                LiteratureArticle.peer_review_status.in_(("peer_reviewed", "preprint")),
                LiteratureArticle.integrity_status.notin_(("retracted", "expression_of_concern")),
            )
            .order_by(LiteratureArticle.published_at.desc().nullslast(), LiteratureArticle.article_id)
        )
    ).all()
    # These records cannot support a full AI evidence summary, but their
    # bibliographic identity and conservative title matches remain useful.
    title_disease_aliases = _load_title_disease_aliases()
    prepared_title_disease_aliases = _prepare_title_disease_aliases(
        diseases_by_id, title_disease_aliases
    )
    title_estimate_records = []
    for article in review_candidates:
        policy = content_policy(article)
        access_known = bool(
            article.open_access_status == "open"
            or article.open_access_url
            or article.pmcid
        )
        if len(article.abstract_text or "") >= 180 and access_known and policy["detail_available"]:
            continue
        title_estimate_records.append({
            "article_id": article.article_id,
            "title": article.title,
            "doi": article.doi,
            "pmid": article.pmid,
            "journal": article.journal,
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "peer_review_status": article.peer_review_status,
            "editorial_status": "review",
            "detail_available": False,
            "diseases": [],
            "topics": [],
            "title_estimated_diseases": _title_estimated_literature_links(
                article.title, diseases_by_id, title_disease_aliases, prepared_title_disease_aliases
            ),
            "title_estimated_topics": _title_estimated_topics(article.title),
            "estimate_method": "title_mention_only",
        })
    estimate_ids = [item["article_id"] for item in title_estimate_records]
    estimated_disease_links = (
        (await session.execute(
            select(LiteratureDiseaseLink).where(LiteratureDiseaseLink.article_id.in_(estimate_ids))
        )).scalars().all()
        if estimate_ids else []
    )
    estimated_topic_links = (
        (await session.execute(
            select(LiteratureTopicLink).where(LiteratureTopicLink.article_id.in_(estimate_ids))
        )).scalars().all()
        if estimate_ids else []
    )
    stored_diseases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stored_topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in estimated_disease_links:
        disease = diseases_by_id.get(link.disease_id) or {}
        stored_diseases[link.article_id].append({
            "disease_id": link.disease_id,
            "slug": disease.get("slug"),
            "name_en": disease.get("name_en") or disease.get("standard_name_en") or link.disease_id,
            "name_zh": disease.get("name_zh") or disease.get("standard_name_zh"),
            "confidence": link.confidence,
            "source": "stored_relation",
        })
    for link in estimated_topic_links:
        stored_topics[link.article_id].append({
            "name": link.topic,
            "confidence": link.confidence,
            "source": "stored_relation",
        })
    for item in title_estimate_records:
        known_diseases = stored_diseases.get(item["article_id"], [])
        known_disease_ids = {link["disease_id"] for link in known_diseases}
        item["diseases"] = [
            *known_diseases,
            *(link for link in item["title_estimated_diseases"] if link["disease_id"] not in known_disease_ids),
        ][:5]
        known_topics = stored_topics.get(item["article_id"], [])
        known_topic_names = {link["name"].casefold() for link in known_topics}
        item["topics"] = [
            *known_topics,
            *(link for link in item["title_estimated_topics"] if link["name"].casefold() not in known_topic_names),
        ]
        item.pop("title_estimated_diseases")
        item.pop("title_estimated_topics")
    article_ids = [article.article_id for article in articles]
    peer_reviewed_article_ids = [
        article.article_id for article in articles if article.peer_review_status == "peer_reviewed"
    ]
    integrity_event_rows: list[Any] = []
    integrity_article_rows: list[Any] = []
    ever_public_article_ids: set[str] = set()
    if await has_table(session, "literature_status_events"):
        integrity_event_rows = (
            await session.execute(
                select(LiteratureStatusEvent)
                .where(LiteratureStatusEvent.current_status.in_(tuple(_PUBLIC_INTEGRITY_STATUSES)))
                .order_by(
                    LiteratureStatusEvent.effective_at.desc(),
                    LiteratureStatusEvent.created_at.desc(),
                )
            )
        ).scalars().all()
        integrity_article_ids = {
            event.article_id for event in integrity_event_rows if event.article_id
        }
        if integrity_article_ids:
            ever_public_article_ids = {
                str(article_id)
                for article_id in (
                    await session.execute(
                        select(LiteratureStatusEvent.article_id)
                        .where(
                            LiteratureStatusEvent.article_id.in_(integrity_article_ids),
                            LiteratureStatusEvent.event_type == "publication_status_changed",
                            LiteratureStatusEvent.current_status == "published",
                        )
                        .distinct()
                    )
                ).scalars().all()
            }
            integrity_article_rows = (
                await session.execute(
                    select(LiteratureArticle).where(LiteratureArticle.article_id.in_(integrity_article_ids))
                )
            ).scalars().all()
    disease_links = (
        await session.execute(select(LiteratureDiseaseLink).where(LiteratureDiseaseLink.article_id.in_(article_ids)))
    ).scalars().all() if article_ids else []
    country_links = (
        await session.execute(select(LiteratureCountryLink).where(LiteratureCountryLink.article_id.in_(article_ids)))
    ).scalars().all() if article_ids else []
    topic_links = (
        await session.execute(select(LiteratureTopicLink).where(LiteratureTopicLink.article_id.in_(article_ids)))
    ).scalars().all() if article_ids else []
    summaries = (
        await session.execute(
            select(LiteratureSummary).where(
                LiteratureSummary.article_id.in_(article_ids),
                LiteratureSummary.status == "published",
            )
        )
    ).scalars().all() if article_ids else []
    signal_article_links = (
        (
            await session.execute(
                select(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.article_id.in_(peer_reviewed_article_ids),
                    LiteratureSignalArticleLink.status.in_(("confirmed", "rejected")),
                )
            )
        ).scalars().all()
        if peer_reviewed_article_ids and await has_table(session, "literature_signal_article_links")
        else []
    )
    diseases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    countries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    summaries_by_article: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for link in disease_links:
        disease = diseases_by_id.get(link.disease_id) or {}
        diseases[link.article_id].append({
            "disease_id": link.disease_id,
            "slug": disease.get("slug"),
            "name_en": disease.get("name_en") or disease.get("standard_name_en") or link.disease_id,
            "name_zh": disease.get("name_zh") or disease.get("standard_name_zh"),
            "name_fr": disease.get("name_fr"),
            "confidence": link.confidence,
        })
    for link in country_links:
        countries[link.article_id].append({
            "code": link.country_code,
            "name_en": link.country_name,
            "name_zh": None,
            "confidence": link.confidence,
        })
    for link in topic_links:
        topics[link.article_id].append({"name": link.topic, "confidence": link.confidence})
    summary_fields = (
        "research_question", "study_design", "population_setting", "main_findings",
        "public_health_relevance", "limitations", "gids_interpretation",
    )
    for summary in summaries:
        automation = (summary.generation_metadata or {}).get("autopilot") or {}
        translation = (summary.generation_metadata or {}).get("translation_provenance") or None
        summaries_by_article[summary.article_id][summary.language] = {
            **{field: getattr(summary, field) for field in summary_fields},
            "provenance": {
                "generated_by": summary.generated_by,
                "model": summary.model,
                "provider": summary.provider,
                "quality_score": summary.quality_score,
                "generated_at": summary.generated_at.isoformat() if summary.generated_at else None,
                "editorially_approved": summary.generated_by == "control-plane-editor" or bool(
                    (summary.generation_metadata or {}).get("editorial_reviewed_at")
                ),
                "automatically_approved": automation.get("policy_version") is not None,
                "automation_policy_version": automation.get("policy_version"),
                "publication_gate": (summary.generation_metadata or {}).get("publication_gate"),
                "translation": translation,
            },
        }

    surveillance_coverage = surveillance_coverage or {}
    projected = []
    for article in articles:
        article_diseases = sorted(diseases[article.article_id], key=lambda item: item["confidence"], reverse=True)
        article_topics = sorted(topics[article.article_id], key=lambda item: item["confidence"], reverse=True)
        primary_disease_en = article_diseases[0]["name_en"] if article_diseases else "infectious disease"
        primary_disease_zh = (article_diseases[0].get("name_zh") if article_diseases else None) or "传染病"
        topic_phrase_en = ", ".join(topic["name"] for topic in article_topics[:2])
        country_phrase_en = ", ".join(country["name_en"] for country in countries[article.article_id][:2])
        study_type = article.study_type or "journal article"
        summary = summaries_by_article.get(article.article_id, {})
        policy = content_policy(article)
        summary_relevance_en = (summary.get("en") or {}).get("public_health_relevance")
        summary_relevance_zh = (summary.get("zh") or {}).get("public_health_relevance")
        summary_relevance_fr = (summary.get("fr") or {}).get("public_health_relevance")
        context_en = (
            summary_relevance_en
            or (
                f"Classified as {study_type.lower()} evidence for {primary_disease_en}"
                f"{f' in {country_phrase_en}' if country_phrase_en else ''}"
                f"{f', with signals in {topic_phrase_en}' if topic_phrase_en else ''}. "
                "Treat this as a discovery record until the source findings are reviewed."
            )
        )
        context_zh = (
            summary_relevance_zh
            or f"该记录被分类为与{primary_disease_zh}相关的{study_type}证据。正式引用或应用结论前，请先核对原始文献。"
        )
        primary_disease_fr = (article_diseases[0].get("name_fr") if article_diseases else None) or primary_disease_en
        context_fr = (
            summary_relevance_fr
            or f"Cette étude est classée comme une preuve de type {study_type.lower()} concernant {primary_disease_fr}. Vérifiez la publication originale avant toute citation ou application."
        )
        article_countries = sorted(countries[article.article_id], key=lambda item: item["confidence"], reverse=True)
        related_surveillance = build_related_surveillance(
            article_diseases,
            article_countries,
            surveillance_coverage,
        )
        classification_evidence = (article.metadata_ or {}).get("classification_evidence") or {}

        def controlled_entities(section: str, *, name_key: str = "name") -> list[dict[str, Any]]:
            values = classification_evidence.get(section) or {}
            if not isinstance(values, dict):
                return []
            return sorted(
                [
                    {
                        "id": str(key),
                        name_key: str((evidence or {}).get("label") or key),
                        "confidence": float((evidence or {}).get("confidence") or 0.0),
                    }
                    for key, evidence in values.items()
                    if isinstance(evidence, dict)
                ],
                key=lambda item: item["confidence"],
                reverse=True,
            )

        research_domain_evidence = classification_evidence.get("research_domain") or {}
        version_relations = []
        for relation in (article.metadata_ or {}).get("version_relations") or []:
            if not isinstance(relation, dict):
                continue
            preprint_doi = str(relation.get("preprint_doi") or "").strip().lower()
            peer_reviewed_doi = str(relation.get("peer_reviewed_doi") or "").strip().lower()
            if not preprint_doi or not peer_reviewed_doi:
                continue
            version_relations.append({
                "preprint_doi": preprint_doi,
                "peer_reviewed_doi": peer_reviewed_doi,
                **(
                    {"preprint_article_id": str(relation["preprint_article_id"])}
                    if relation.get("preprint_article_id") else {}
                ),
                **(
                    {"peer_reviewed_article_id": str(relation["peer_reviewed_article_id"])}
                    if relation.get("peer_reviewed_article_id") else {}
                ),
            })
            if len(version_relations) == 10:
                break
        projected.append({
            "article_id": article.article_id,
            "slug": article.slug,
            "title": article.title,
            "doi": article.doi,
            "pmid": article.pmid,
            "pmcid": article.pmcid,
            "openalex_id": article.openalex_id,
            "journal": article.journal,
            "publisher": article.publisher,
            "authors": [str(item.get("name")) for item in (article.authors or []) if item.get("name")],
            "article_type": article.article_type,
            "study_type": article.study_type,
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "indexed_at": article.indexed_at.isoformat() if article.indexed_at else None,
            "publication_date_label_en": _date_label(article.published_at, "day", lang="en"),
            "publication_date_label_zh": _date_label(article.published_at, "day", lang="zh"),
            "publication_date_label_fr": _date_label(article.published_at, "day", lang="fr"),
            "open_access_status": article.open_access_status,
            "open_access_url": article.open_access_url,
            "license_url": article.license_url,
            "peer_review_status": article.peer_review_status,
            "editorial_status": article.publication_status,
            "integrity_status": article.integrity_status,
            "version_relations": version_relations,
            "discovery_score": article.discovery_score,
            "is_featured": article.is_featured,
            "classification_version": int((article.metadata_ or {}).get("classification_version") or 0),
            "research_domain": str(research_domain_evidence.get("value") or "not_determined"),
            "diseases": article_diseases,
            "countries": article_countries,
            "topics": article_topics,
            "pathogens": controlled_entities("pathogens"),
            "pathogen_types": controlled_entities("pathogen_types"),
            "populations": controlled_entities("populations"),
            "related_surveillance": related_surveillance,
            "summary": summary,
            "content_policy": policy,
            "content_tier": (
                "related_only"
                if policy["related_only"]
                else "quality_gated_multilingual_evidence"
                if summary.get("en") and summary.get("zh") and summary.get("fr")
                else "quality_gated_bilingual_evidence"
                if summary.get("en") and summary.get("zh")
                else "metadata_only"
            ),
            "related_only": policy["related_only"],
            "indexable": bool(summary.get("en") and summary.get("zh")) and not policy["related_only"],
            "why_it_matters_en": context_en,
            "why_it_matters_zh": context_zh,
            "why_it_matters_fr": context_fr,
            "why_it_matters_source": "published_summary" if summary_relevance_en or summary_relevance_zh or summary_relevance_fr else "classifier_metadata",
            "source_urls": {
                key: value
                for key, value in (article.source_urls or {}).items()
                if key in {"doi", "publisher", "pubmed"}
            },
            "updated_at": article.updated_at.isoformat() if article.updated_at else None,
        })

    projected, preprints = partition_public_literature_articles(projected)
    # Bibliographic records can be useful even when no detailed GIDS summary
    # is available. They link to their original source, not to an evidence
    # page, and never contain private abstracts or provider payloads.
    bibliographic_articles = bibliographic_catalogue_records(projected)
    related_only_articles = [
        article
        for article in [*projected, *preprints]
        if article.get("related_only") is True
    ]
    # A published editorial state is necessary but not sufficient for a
    # public evidence page. Legacy metadata-only records remain in the control
    # plane until both published language summaries pass their quality gates.
    projected, preprints, withheld_metadata_only = apply_public_summary_gate(projected, preprints)
    quality_gated_db_article_count = len(projected) + len(preprints)
    preprints.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    historical_seed_count = append_historical_seed_articles(
        projected,
        diseases_by_id=diseases_by_id,
        surveillance_coverage=surveillance_coverage,
    )
    currently_public_article_ids = {
        item["article_id"]
        for item in [*projected, *preprints]
        if item.get("indexable") is True
    }
    public_slugs_by_id = {
        item["article_id"]: item["slug"]
        for item in [*projected, *preprints]
        if item.get("indexable") is True
    }
    for item in [*projected, *preprints]:
        for relation in item.get("version_relations") or []:
            preprint_id = relation.get("preprint_article_id")
            peer_reviewed_id = relation.get("peer_reviewed_article_id")
            if preprint_id in public_slugs_by_id:
                relation["preprint_url"] = f"/research/articles/{public_slugs_by_id[preprint_id]}/"
            if peer_reviewed_id in public_slugs_by_id:
                relation["peer_reviewed_url"] = f"/research/articles/{public_slugs_by_id[peer_reviewed_id]}/"
    integrity_alerts = project_public_integrity_alerts(
        integrity_event_rows,
        integrity_article_rows,
        ever_public_article_ids=ever_public_article_ids,
        currently_public_article_ids=currently_public_article_ids,
    )
    integrity_events_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for alert in integrity_alerts:
        integrity_events_by_article[alert["article_id"]].append(alert)
    for item in [*projected, *preprints]:
        item["integrity_events"] = integrity_events_by_article.get(item["article_id"], [])
    # Public streams and every facet labelled "latest" are chronological.
    # Editorial picks remain independently ordered through the featured list.
    projected.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    recent_cutoff = now - timedelta(days=7)
    recent = [
        item
        for item in projected
        if item["published_at"]
        and recent_cutoff <= _parse_public_datetime(item["published_at"]) <= now
    ]
    review_types = {"Systematic review", "Meta-analysis", "Guideline"}
    emerging_topics = build_emerging_topics(projected, now=now)
    disease_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    country_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    topic_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    weekly_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disease_meta: dict[str, dict[str, Any]] = {}
    country_meta: dict[str, dict[str, Any]] = {}
    topic_meta: dict[str, dict[str, Any]] = {}
    for item in projected:
        for disease in item["diseases"]:
            key = disease["disease_id"].lower()
            disease_articles[key].append(item)
            disease_meta[key] = {
                "disease_id": disease["disease_id"],
                "slug": disease.get("slug") or key,
                "name_en": disease["name_en"],
                "name_zh": disease.get("name_zh"),
            }
        for country in item["countries"]:
            key = country["code"].lower()
            country_articles[key].append(item)
            country_meta[key] = {
                "code": country["code"],
                "slug": key,
                "name_en": country["name_en"],
                "name_zh": country.get("name_zh"),
            }
        for topic in item["topics"]:
            key = _facet_slug(topic["name"])
            if not key:
                continue
            topic_articles[key].append(item)
            topic_meta[key] = {"slug": key, "name": topic["name"]}
        if item.get("published_at") and _parse_public_datetime(item["published_at"]) <= now:
            published = _parse_public_datetime(item["published_at"])
            iso_year, iso_week, _ = published.isocalendar()
            publication_week = f"{iso_year}-W{iso_week:02d}"
            item["publication_week"] = publication_week
            weekly_articles[publication_week].append(item)
    disease_facets = [
        {**disease_meta[key], "count": len(items), "url": f"/research/diseases/{disease_meta[key]['slug']}/"}
        for key, items in sorted(disease_articles.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]
    country_facets = [
        {**country_meta[key], "count": len(items), "url": f"/research/countries/{key}/"}
        for key, items in sorted(country_articles.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]
    topic_facets = [
        {**topic_meta[key], "count": len(items), "url": f"/research/topics/{key}/"}
        for key, items in sorted(topic_articles.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]
    weekly_briefs = []
    weekly_reviews = load_weekly_review_registry()
    weekly_ai_reviews = load_weekly_ai_review_registry()
    for week, items in sorted(weekly_articles.items(), reverse=True):
        published_dates = sorted(_parse_public_datetime(item["published_at"]) for item in items)
        weekly_briefs.append({
            "week": week,
            "start_date": published_dates[0].date().isoformat(),
            "end_date": published_dates[-1].date().isoformat(),
            "article_count": len(items),
            "disease_count": len({d["disease_id"] for item in items for d in item["diseases"]}),
            "country_count": len({c["code"] for item in items for c in item["countries"]}),
            "top_topics": [name for name, _ in Counter(t["name"] for item in items for t in item["topics"]).most_common(5)],
            "articles": items,
            "url": f"/research/weekly/{week}/",
            # Registry records remain private generation metadata until the
            # final weekly-brief projector validates and allowlists them.
            "_editorial_review": weekly_reviews.get(week),
            # AI review is a separate content-bound quality-control signal. A
            # valid human editorial review still takes precedence downstream.
            "_ai_review": weekly_ai_reviews.get(week),
        })
    latest_updated_values = [
        article.updated_at
        for article in articles
        if article.updated_at
    ]
    latest_updated_values.extend(
        _parse_public_datetime(item["updated_at"])
        for item in projected
        if item.get("source_kind") == "historical_seed" and item.get("updated_at")
    )
    latest_updated_values.extend(
        _parse_public_datetime(value)
        for alert in integrity_alerts
        if (value := alert.get("effective_at") or alert.get("recorded_at"))
    )
    latest_updated = max(latest_updated_values, default=None)
    public_article_ids = {item["article_id"] for item in projected}
    exact_linked_public_articles = len({
        link.article_id
        for link in signal_article_links
        if link.status == "confirmed"
        and link.relation_level == "exact_disease_geography"
        and link.article_id in public_article_ids
    })
    pipeline_funnel = build_pipeline_funnel(
        total=int(status_counts.total or 0),
        review=int(status_counts.review or 0),
        published=int(status_counts.published or 0),
        public_catalogue=len(projected),
        excluded=int(status_counts.excluded or 0),
        summarized=quality_gated_db_article_count,
        exact_linked=exact_linked_public_articles,
    )
    french_published_summary_count = sum(
        bool((item.get("summary") or {}).get("fr")) for item in projected
    )
    completeness = [
        {"metric": "Disease classified", "count": sum(bool(item["diseases"]) for item in projected), "total": len(projected)},
        {"metric": "Geography classified", "count": sum(bool(item["countries"]) for item in projected), "total": len(projected)},
        {"metric": "Topic classified", "count": sum(bool(item["topics"]) for item in projected), "total": len(projected)},
        {"metric": "Bilingual published summary", "count": len(projected), "total": len(projected)},
        {"metric": "French published summary", "count": french_published_summary_count, "total": len(projected)},
        {"metric": "Open access", "count": sum(item["open_access_status"] == "open" for item in projected), "total": len(projected)},
    ]
    return {
        "schema_version": 1,
        "last_updated": latest_updated.isoformat() if latest_updated else None,
        "metrics": {
            "total_public_articles": len(projected),
            "title_estimated_related_records": len(title_estimate_records),
            "title_estimated_disease_links": sum(
                sum(link.get("source") == "title_estimate" for link in item["diseases"])
                for item in title_estimate_records
            ),
            "title_estimated_topic_links": sum(
                sum(link.get("source") == "title_estimate" for link in item["topics"])
                for item in title_estimate_records
            ),
            "preprints_total": len(preprints),
            "integrity_alerts_total": len(integrity_alerts),
            "withheld_metadata_only": withheld_metadata_only,
            "public_article_limit": None,
            "historical_baseline_articles": historical_seed_count,
            "diseases_total": len({d["disease_id"] for item in projected for d in item["diseases"]}),
            "countries_total": len({c["code"] for item in projected for c in item["countries"]}),
            "papers_last_7_days": len(recent),
            "diseases_last_7_days": len({d["disease_id"] for item in recent for d in item["diseases"]}),
            "countries_last_7_days": len({c["code"] for item in recent for c in item["countries"]}),
            "reviews_and_guidelines_last_7_days": sum(item["study_type"] in review_types for item in recent),
            "french_published_summary_count": french_published_summary_count,
            "french_summary_coverage": round(
                french_published_summary_count / len(projected), 4
            ) if projected else 0.0,
        },
        "featured": sorted(
            [item for item in projected if item["is_featured"]],
            key=lambda item: (float(item.get("discovery_score") or 0), item.get("published_at") or ""),
            reverse=True,
        )[:6],
        "articles": projected,
        "bibliographic_articles": bibliographic_articles,
        "title_estimated_related_records": title_estimate_records,
        "preprints": preprints,
        "related_only_articles": related_only_articles,
        "integrity_alerts": integrity_alerts,
        "historical_baseline": sorted(
            [item for item in projected if item.get("source_kind") == "historical_seed"],
            key=lambda item: item.get("published_at") or "",
        ),
        "reviews_and_guidelines": [item for item in projected if item["study_type"] in review_types][:12],
        "emerging_topics": emerging_topics,
        "knowledge_graph": build_knowledge_graph(projected),
        "disease_articles": dict(disease_articles),
        "country_articles": dict(country_articles),
        "topic_articles": dict(topic_articles),
        "facets": {
            "diseases": disease_facets,
            "countries": country_facets,
            "topics": topic_facets,
            "weeks": [
                {key: value for key, value in brief.items() if key != "articles"}
                for brief in weekly_briefs
            ],
        },
        "publication_timeline": build_publication_timeline(projected),
        "pipeline_funnel": pipeline_funnel,
        "completeness": completeness,
        "visualizations": {
            "publication_pulse": build_publication_pulse(projected, now=now),
            "pipeline_funnel": pipeline_funnel,
            "completeness": completeness,
            "coverage_matrix": build_surveillance_coverage_matrix({}),
            "hotspots": build_hotspot_visualizations(projected, now=now),
        },
        "weekly_briefs": weekly_briefs,
        "_signal_article_links": [
            {
                "signal_id": link.signal_id,
                "article_id": link.article_id,
                "relation_level": link.relation_level,
                "status": link.status,
                "confidence": link.confidence,
                "source": link.source,
            }
            for link in signal_article_links
        ],
    }


_COLLECTION_CARD_FIELDS = (
    "article_id", "slug", "title", "doi", "journal", "publisher", "authors",
    "study_type", "published_at", "publication_date_label_en",
    "publication_date_label_zh", "publication_date_label_fr", "open_access_status",
    "open_access_url", "peer_review_status", "diseases", "countries", "topics",
    "related_surveillance", "source_kind", "historical_baseline",
    "why_it_matters_en", "why_it_matters_zh", "why_it_matters_fr",
)


def _collection_card_article(article: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields used by collection cards and their client filters."""
    card = {key: article[key] for key in _COLLECTION_CARD_FIELDS if key in article}
    publisher_url = (article.get("source_urls") or {}).get("publisher")
    if publisher_url:
        card["source_urls"] = {"publisher": publisher_url}
    if not card.get("why_it_matters_fr"):
        french_relevance = ((article.get("summary") or {}).get("fr") or {}).get(
            "public_health_relevance"
        )
        if french_relevance:
            card["why_it_matters_fr"] = french_relevance
    return card



def write_literature_artifacts(
    payload: dict[str, Any],
    output_dir: Path,
    public_site_data_dir: Path | None = None,
    shard_max_bytes: int = PUBLIC_RESEARCH_SHARD_MAX_BYTES,
) -> None:
    phase_started = time.perf_counter()

    def report_phase(name: str) -> None:
        nonlocal phase_started
        now = time.perf_counter()
        print(f"  Literature timing: {name} {now - phase_started:.1f}s", flush=True)
        phase_started = now

    research_dir = output_dir / "research"
    article_dir = research_dir / "articles"
    disease_dir = research_dir / "diseases"
    country_dir = research_dir / "countries"
    topic_dir = research_dir / "topics"
    weekly_dir = research_dir / "weekly"
    article_dir.mkdir(parents=True, exist_ok=True)
    disease_dir.mkdir(parents=True, exist_ok=True)
    country_dir.mkdir(parents=True, exist_ok=True)
    topic_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)
    public_index = {
        key: value
        for key, value in payload.items()
        if key not in {"disease_articles", "country_articles", "topic_articles"} and not key.startswith("_")
    }
    public_index["weekly_briefs"] = [
        {key: value for key, value in brief.items() if key != "articles"}
        for brief in payload.get("weekly_briefs") or []
    ]
    write_pretty_json(research_dir / "index.json", public_index)
    hotspots = ((payload.get("visualizations") or {}).get("hotspots") or build_hotspot_visualizations(
        payload.get("articles") or []
    ))
    write_pretty_json(research_dir / "hotspots.json", hotspots)
    write_compact_json(
        research_dir / "catalogue.json",
        {
            "schema_version": public_index.get("schema_version"),
            "last_updated": public_index.get("last_updated"),
            "articles": [*(payload.get("articles") or []), *(payload.get("bibliographic_articles") or [])],
        },
    )
    report_phase("build-time indexes")
    if public_site_data_dir is not None:
        publish_browser_research_index(
            public_index,
            public_site_data_dir / "research",
            max_bytes=shard_max_bytes,
        )
    report_phase("browser sharded index")
    public_article_details = [
        *(payload.get("articles") or []),
        *(payload.get("preprints") or []),
    ]
    route_diseases: dict[str, dict[str, Any]] = {}
    for article in public_article_details:
        for disease in article.get("diseases") or []:
            if disease.get("name_en"):
                route_diseases[disease["name_en"]] = {
                    key: disease.get(key)
                    for key in ("name_en", "name_fr", "slug", "disease_id")
                }
    write_compact_json(
        research_dir / "routes.json",
        {
            "articles": [
                {"slug": article["slug"], "indexable": article.get("indexable") is not False}
                for article in public_article_details
            ],
            # Article ranking changes insertion order but not this name lookup.
            # Keep it canonical: it is a shared dependency of every cached page.
            "diseases": [route_diseases[name] for name in sorted(route_diseases)],
            "facets": public_index.get("facets") or {},
        },
    )
    for article in public_article_details:
        write_pretty_json(
            article_dir / f"{article['slug']}.json",
            {**article, "knowledge_graph": build_knowledge_graph([article])},
        )
    report_phase("article details")
    title_aliases = _load_title_disease_aliases()
    for disease_id, articles in (payload.get("disease_articles") or {}).items():
        write_pretty_json(
            disease_dir / f"{disease_id}.json",
            {
                "articles": [_collection_card_article(article) for article in articles],
                "count": len(articles),
                "publication_timeline": build_publication_timeline(articles),
                "evidence_events": build_disease_evidence_events(
                    articles, disease_id=disease_id.upper(), title_aliases=title_aliases
                ),
                "knowledge_graph": build_knowledge_graph(articles),
            },
        )
    report_phase("disease collections")
    for country_code, articles in (payload.get("country_articles") or {}).items():
        write_pretty_json(
            country_dir / f"{country_code}.json",
            {
                "articles": [_collection_card_article(article) for article in articles],
                "count": len(articles),
                "publication_timeline": build_publication_timeline(articles),
                "knowledge_graph": build_knowledge_graph(articles),
            },
        )
    report_phase("country collections")
    for topic_slug, articles in (payload.get("topic_articles") or {}).items():
        write_pretty_json(
            topic_dir / f"{topic_slug}.json",
            {
                "articles": [_collection_card_article(article) for article in articles],
                "count": len(articles),
                "publication_timeline": build_publication_timeline(articles),
                "knowledge_graph": build_knowledge_graph(articles),
            },
        )
    report_phase("topic collections")
    for brief in payload.get("weekly_briefs") or []:
        write_pretty_json(weekly_dir / f"{brief['week']}.json", brief)
    remove_stale_json_files(article_dir, {f"{item['slug']}.json" for item in public_article_details})
    remove_stale_json_files(disease_dir, {f"{key}.json" for key in (payload.get("disease_articles") or {})})
    remove_stale_json_files(country_dir, {f"{key}.json" for key in (payload.get("country_articles") or {})})
    remove_stale_json_files(topic_dir, {f"{key}.json" for key in (payload.get("topic_articles") or {})})
    remove_stale_json_files(weekly_dir, {f"{item['week']}.json" for item in payload.get("weekly_briefs") or []})
    report_phase("weekly briefs and cleanup")


__all__ = [
    "attach_surveillance_evidence",
    "build_emerging_topics",
    "build_disease_evidence_events",
    "build_hotspot_visualizations",
    "build_pipeline_funnel",
    "build_publication_pulse",
    "build_related_surveillance",
    "build_publication_timeline",
    "build_surveillance_coverage_matrix",
    "build_surveillance_evidence",
    "collect_literature_export",
    "empty_literature_export",
    "append_historical_seed_articles",
    "apply_public_summary_gate",
    "load_historical_seed_articles",
    "partition_public_literature_articles",
    "project_public_integrity_alerts",
    "publish_browser_research_index",
    "write_literature_artifacts",
]
