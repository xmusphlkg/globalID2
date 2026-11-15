"""Merge literature candidates while retaining priority, preprints and discovery provenance."""
from __future__ import annotations
from typing import Any
from .types import ArticleCandidate


def _candidate_key(candidate: ArticleCandidate) -> str:
    if candidate.doi:
        return f"doi:{candidate.doi.strip().lower()}"
    if candidate.pmid:
        return f"pmid:{candidate.pmid.strip()}"
    if candidate.pmcid:
        return f"pmcid:{candidate.pmcid.strip().upper()}"
    if candidate.openalex_id:
        return f"openalex:{candidate.openalex_id.strip().upper()}"
    return f"article:{candidate.article_id}"



def _merge_candidate(primary: ArticleCandidate, incoming: ArticleCandidate) -> None:
    """Fill source gaps without downgrading the earlier, higher-priority record."""
    for attribute in (
        "doi", "pmid", "pmcid", "openalex_id", "journal", "publisher",
        "study_type", "published_at", "indexed_at", "abstract_text",
        "abstract_license", "open_access_url", "license_url",
    ):
        if not getattr(primary, attribute) and getattr(incoming, attribute):
            setattr(primary, attribute, getattr(incoming, attribute))
    if not primary.issn and incoming.issn:
        primary.issn = list(incoming.issn)
    if not primary.authors and incoming.authors:
        primary.authors = list(incoming.authors)
    if primary.open_access_status == "unknown" and incoming.open_access_status != "unknown":
        primary.open_access_status = incoming.open_access_status
    # A dedicated preprint registry is authoritative for the review status of
    # its own DOI. Never let provider priority erase that safety signal.
    if (
        incoming.peer_review_status == "preprint"
        and primary.doi
        and primary.doi == incoming.doi
    ):
        primary.peer_review_status = "preprint"
        primary.article_type = "preprint"
        primary.study_type = "Preprint"
    existing_relations = {
        (
            relation.get("preprint_doi"),
            relation.get("peer_reviewed_doi"),
        )
        for relation in primary.version_relations
    }
    primary.version_relations.extend(
        relation
        for relation in incoming.version_relations
        if (relation.get("preprint_doi"), relation.get("peer_reviewed_doi"))
        not in existing_relations
    )
    discovery_origins: list[dict[str, Any]] = []
    for candidate in (primary, incoming):
        direct = candidate.source_payload.get("_research_radar_discovery")
        if isinstance(direct, dict):
            discovery_origins.append(direct)
        europe_pmc = candidate.source_payload.get("europe_pmc")
        if isinstance(europe_pmc, dict):
            nested = europe_pmc.get("_research_radar_discovery")
            if isinstance(nested, dict):
                discovery_origins.append(nested)
        discovery_origins.extend(
            origin
            for origin in candidate.source_payload.get("controlled_discovery_origins") or []
            if isinstance(origin, dict)
        )
    primary.source_urls = {**incoming.source_urls, **primary.source_urls}
    primary.source_payload = {**incoming.source_payload, **primary.source_payload}
    if discovery_origins:
        by_key = {
            (str(origin.get("provider") or ""), str(origin.get("query_id") or "")): origin
            for origin in discovery_origins
        }
        primary.source_payload["controlled_discovery_origins"] = [
            by_key[key] for key in sorted(by_key)
        ]



def _deduplicate_candidates(candidates: list[ArticleCandidate]) -> tuple[list[ArticleCandidate], int]:
    deduplicated: dict[str, ArticleCandidate] = {}
    duplicates = 0
    for candidate in candidates:
        key = _candidate_key(candidate)
        primary = deduplicated.get(key)
        if primary is None:
            deduplicated[key] = candidate
            continue
        duplicates += 1
        _merge_candidate(primary, candidate)
    return list(deduplicated.values()), duplicates
