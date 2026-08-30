"""Aggregate, read-only coverage audit for publisher discovery sources.

The audit deliberately measures provenance in the stored Research Radar corpus.
It does not claim to measure a publisher's complete catalogue: records absent
from every configured discovery source are, by definition, not observable
without a publisher catalogue or API.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text


REPORT_SCHEMA_VERSION = 1
DEFAULT_RECENT_DAYS = 365
DEFAULT_TOP_JOURNALS = 20

_AGGREGATE_SQL = text(
    """
    WITH base AS MATERIALIZED (
        SELECT
            CASE
                WHEN lower(coalesce(publisher, '')) LIKE '%elsevier%'
                    THEN 'elsevier'
                WHEN lower(coalesce(publisher, '')) LIKE '%springer%'
                  OR lower(coalesce(publisher, '')) LIKE '%nature portfolio%'
                  OR lower(coalesce(publisher, '')) LIKE '%biomed central%'
                    THEN 'springer_nature'
                ELSE 'other'
            END AS publisher_family,
            coalesce(nullif(btrim(journal), ''), '(unknown)') AS journal,
            published_at,
            doi IS NOT NULL AS has_doi,
            pmid IS NOT NULL AS has_pmid,
            source_payload->'europe_pmc' IS NOT NULL AS has_europe_pmc,
            source_payload->>'DOI' IS NOT NULL AS has_crossref,
            (
                openalex_id IS NOT NULL
                OR source_payload->'openalex' IS NOT NULL
            ) AS has_openalex
        FROM literature_articles
    ), scoped AS (
        SELECT 'overall'::text AS scope, * FROM base
        UNION ALL
        SELECT publisher_family AS scope, *
        FROM base
        WHERE publisher_family IN ('springer_nature', 'elsevier')
    ), windowed AS (
        SELECT 'all_time'::text AS period, * FROM scoped
        UNION ALL
        SELECT 'recent'::text AS period, *
        FROM scoped
        WHERE published_at >= :recent_start
          AND published_at < :as_of_exclusive
    )
    SELECT
        scope,
        period,
        CASE WHEN GROUPING(journal) = 1 THEN NULL ELSE journal END AS journal,
        COUNT(*)::bigint AS total,
        COUNT(*) FILTER (WHERE has_doi)::bigint AS doi,
        COUNT(*) FILTER (WHERE has_pmid)::bigint AS pmid,
        COUNT(*) FILTER (WHERE has_europe_pmc)::bigint AS europe_pmc,
        COUNT(*) FILTER (WHERE has_crossref)::bigint AS crossref,
        COUNT(*) FILTER (WHERE has_openalex)::bigint AS openalex,
        COUNT(*) FILTER (WHERE has_crossref AND NOT has_pmid)::bigint
            AS crossref_without_pmid,
        COUNT(*) FILTER (WHERE has_crossref AND NOT has_europe_pmc)::bigint
            AS crossref_without_europe_pmc,
        COUNT(*) FILTER (
            WHERE NOT has_crossref AND NOT has_europe_pmc AND NOT has_openalex
        )::bigint AS core_provenance_gap
    FROM windowed
    GROUP BY GROUPING SETS ((scope, period), (scope, period, journal))
    ORDER BY scope, period, journal NULLS FIRST
    """
)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _metric_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    total = int(row.get("total") or 0)
    counts = {
        key: int(row.get(key) or 0)
        for key in (
            "doi",
            "pmid",
            "europe_pmc",
            "crossref",
            "openalex",
            "crossref_without_pmid",
            "crossref_without_europe_pmc",
            "core_provenance_gap",
        )
    }
    return {
        "total": total,
        "counts": counts,
        "coverage": {
            key: _ratio(counts[key], total)
            for key in ("doi", "pmid", "europe_pmc", "crossref", "openalex")
        },
    }


def build_publisher_coverage_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    recent_days: int = DEFAULT_RECENT_DAYS,
    top_journals: int = DEFAULT_TOP_JOURNALS,
    source_configuration: Mapping[str, Mapping[str, bool]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Reduce aggregate query rows into a privacy-safe health report."""

    if recent_days < 1:
        raise ValueError("recent_days must be positive")
    if top_journals < 1:
        raise ValueError("top_journals must be positive")

    totals: dict[tuple[str, str], dict[str, Any]] = {}
    journals: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in rows:
        scope = str(raw.get("scope") or "")
        period = str(raw.get("period") or "")
        if scope not in {"overall", "springer_nature", "elsevier"}:
            continue
        if period not in {"all_time", "recent"}:
            continue
        projected = _metric_projection(raw)
        journal = raw.get("journal")
        if journal is None:
            totals[(scope, period)] = projected
        else:
            journals.setdefault((scope, period), []).append(
                {"journal": str(journal), **projected}
            )

    def period_projection(scope: str, period: str) -> dict[str, Any]:
        result = dict(totals.get((scope, period), _metric_projection({})))
        ranked = sorted(
            journals.get((scope, period), []),
            key=lambda item: (-int(item["total"]), str(item["journal"]).casefold()),
        )[:top_journals]
        result["top_journals"] = ranked
        return result

    publisher_blocks = {
        scope: {
            "all_time": period_projection(scope, "all_time"),
            "recent": period_projection(scope, "recent"),
        }
        for scope in ("springer_nature", "elsevier")
    }

    recent_crossref_total = sum(
        int(block["recent"]["counts"]["crossref"])
        for block in publisher_blocks.values()
    )
    recent_total = sum(int(block["recent"]["total"]) for block in publisher_blocks.values())
    recent_core_gaps = sum(
        int(block["recent"]["counts"]["core_provenance_gap"])
        for block in publisher_blocks.values()
    )
    crossref_coverage = _ratio(recent_crossref_total, recent_total)
    core_sufficient = (
        recent_total > 0
        and crossref_coverage is not None
        and crossref_coverage >= 0.99
        and recent_core_gaps == 0
    )

    configured = source_configuration or {}
    publisher_api_state = {
        provider: {
            "enabled": bool(configured.get(provider, {}).get("enabled")),
            "credential_configured": bool(
                configured.get(provider, {}).get("credential_configured")
            ),
        }
        for provider in ("springer_nature", "elsevier")
    }
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "service": "research-radar-publisher-coverage",
        "mode": "read_only",
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "recent_window": {
            "days": recent_days,
            "start": (as_of - timedelta(days=recent_days - 1)).isoformat(),
            "end_inclusive": as_of.isoformat(),
        },
        "definitions": {
            "pubmed_coverage": "stored records with a PMID",
            "europe_pmc_coverage": "stored records with a Europe PMC metadata payload",
            "crossref_coverage": "stored records with Crossref provenance",
            "core_provenance_gap": (
                "stored records with none of Crossref, Europe PMC, or OpenAlex provenance"
            ),
            "unobservable_gap": (
                "articles absent from every configured core source cannot be measured "
                "without an external publisher catalogue"
            ),
        },
        "coverage": {
            "overall": {
                "all_time": period_projection("overall", "all_time"),
                "recent": period_projection("overall", "recent"),
            },
            "publishers": publisher_blocks,
        },
        "source_configuration": publisher_api_state,
        "source_strategy": {
            "policy_code": (
                "core_sources_sufficient_for_curated_scope"
                if core_sufficient
                else "review_core_discovery_gap"
            ),
            "pubmed_is_complete_replacement": False,
            "publisher_apis_required_now": not core_sufficient,
            "recommendation": (
                "Keep Springer Nature and Elsevier APIs disabled without credentials; "
                "retain Crossref as discovery and Europe PMC/PubMed as biomedical enrichment."
                if core_sufficient
                else "Investigate recent core provenance gaps before deciding source activation."
            ),
            "publisher_catalogue_gap_is_measurable": False,
        },
    }


def collect_publisher_coverage_rows(
    connection: Any,
    *,
    as_of: date,
    recent_days: int = DEFAULT_RECENT_DAYS,
) -> list[Mapping[str, Any]]:
    """Execute only the aggregate SELECT used by the coverage report."""

    if recent_days < 1:
        raise ValueError("recent_days must be positive")
    recent_start = as_of - timedelta(days=recent_days - 1)
    as_of_exclusive = as_of + timedelta(days=1)
    return list(
        connection.execute(
            _AGGREGATE_SQL,
            {"recent_start": recent_start, "as_of_exclusive": as_of_exclusive},
        ).mappings()
    )


__all__ = [
    "DEFAULT_RECENT_DAYS",
    "DEFAULT_TOP_JOURNALS",
    "REPORT_SCHEMA_VERSION",
    "build_publisher_coverage_report",
    "collect_publisher_coverage_rows",
]
