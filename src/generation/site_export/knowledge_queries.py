"""Published knowledge and country briefs from an explicitly supplied session."""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import text


async def fetch_disease_knowledge_briefs(session, *, has_table, safe_int, iso_or_none, enrich_source_attribution) -> dict[str, dict[str, dict]]:
    """Load reviewed or published knowledge briefs keyed by disease and language."""
    if not await has_table(session, "disease_knowledge_briefs"):
        return {}
    sources_by_disease_id: dict[str, dict[int, dict]] = defaultdict(dict)
    if await has_table(session, "disease_knowledge_sources"):
        source_rows = await session.execute(text("""
                SELECT id, disease_id, source_type, source_name, url, resolved_url,
                       title, license, status, review_status, fetched_at, metadata
                FROM disease_knowledge_sources
                """))
        for row in source_rows:
            source = dict(row._mapping)
            source_id = safe_int(source.get("id"))
            disease_id = source.get("disease_id")
            if source_id is None or not disease_id:
                continue
            source["fetched_at"] = iso_or_none(source.get("fetched_at"))
            if not source.get("resolved_url"):
                metadata = (
                    source.get("metadata")
                    if isinstance(source.get("metadata"), dict)
                    else {}
                )
                source["resolved_url"] = metadata.get("resolved_url") or source.get(
                    "url"
                )
            sources_by_disease_id[str(disease_id)][source_id] = source

    rows = await session.execute(text("""
            SELECT disease_id, language, brief, definition, clinical_features, epidemiology,
                   clinical_summary, transmission, prevention, surveillance_note, risk_groups,
                   source_attribution, disclaimer, status, source_confidence, updated_at, metadata
            FROM disease_knowledge_briefs
            WHERE status IN ('published', 'requires_review')
            """))
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        item = dict(row._mapping)
        if item.get("updated_at"):
            item["updated_at"] = item["updated_at"].isoformat()
        item["source_attribution"] = enrich_source_attribution(
            item.get("source_attribution") or [],
            sources_by_disease_id.get(str(item.get("disease_id")), {}),
        )
        result[item["disease_id"]][item["language"]] = item
    return result


async def fetch_country_briefs(session, *, has_table) -> dict[str, dict[str, dict]]:
    """Load published country briefs keyed by country code and language."""
    if not await has_table(session, "country_briefs"):
        return {}
    rows = await session.execute(text("""
            SELECT country_code, language, brief, surveillance_system,
                   coverage_interpretation, reporting_cadence, data_limitations,
                   source_summary, status, updated_at
            FROM country_briefs
            WHERE status = 'published'
            """))
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        item = dict(row._mapping)
        if item.get("updated_at"):
            item["updated_at"] = item["updated_at"].isoformat()
        result[(item["country_code"] or "").upper()][item["language"]] = item
    return result
