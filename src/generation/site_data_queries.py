"""Read-only database queries used by the static site data export.

This module owns SQL execution and the normalization of database rows into the
plain dictionaries consumed by :mod:`scripts.generate_site_data`.  Keeping
these functions independent from file generation makes the database boundary
testable without changing the historical script API.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import bindparam, text

from src.generation.site_export import knowledge_queries as _knowledge_queries
from src.generation.site_export import report_queries as _report_queries

from src.core.country_library import get_country_bootstrap_config
from src.generation.site_series_projection import (
    _normalise_count,
    apply_disease_cutover_projection,
)


def safe_float(value) -> float | None:
    """Return a finite float, or ``None`` for invalid values."""
    if value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def iso_or_none(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


# These NESID diagnoses are reported through Japan's sentinel surveillance,
# including legacy national rows that predate the registered source series.
_JP_SENTINEL_DISEASE_IDS = ("D136", "D138", "D139", "D141", "D143")

# Broad precise updates otherwise repeat category resolution for nearly every
# historical observation. Keep small target sets on the selective query path.
_DENSE_REVIEWED_DISEASE_QUERY_THRESHOLD = 16


def compact_report_metadata(metadata: dict | None, *, include_figures: bool = False) -> dict:
    return _report_queries.compact_report_metadata(metadata, include_figures=include_figures)


def source_metadata_field(source: dict, key: str):
    metadata = source.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def enrich_source_attribution(
    attribution: list, sources_by_id: dict[int, dict]
) -> list[dict]:
    enriched: list[dict] = []
    metadata_keys = (
        "pmid",
        "doi",
        "first_author",
        "journal",
        "pub_date",
        "container_title",
        "publisher",
        "year",
        "provider",
        "content_kind",
    )
    direct_keys = (
        "source_name",
        "source_type",
        "title",
        "url",
        "resolved_url",
        "license",
        "fetched_at",
    )

    for item in attribution or []:
        if not isinstance(item, dict):
            continue
        source_id = safe_int(item.get("source_id") or item.get("id"))
        source = sources_by_id.get(source_id) if source_id is not None else None
        if not source:
            enriched.append(dict(item))
            continue
        source_metadata = (
            source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        )
        item_metadata = (
            item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        )
        merged = {
            **item,
            "id": item.get("id") or source.get("id"),
            "source_id": item.get("source_id") or source.get("id"),
            "metadata": {**source_metadata, **item_metadata},
        }
        for key in direct_keys:
            if not merged.get(key):
                merged[key] = source.get(key)
        for key in metadata_keys:
            if not merged.get(key):
                merged[key] = source_metadata_field(source, key)
        enriched.append(merged)
    return enriched


async def fetch_countries(session) -> list[dict]:
    rows = await session.execute(text("""
            SELECT code, name, name_en, name_local, language, timezone,
                   data_source_url, data_source_type, crawler_config,
                   parser_config, metadata, notes
            FROM countries
            WHERE is_active = true
            ORDER BY code
            """))
    countries = [dict(row._mapping) for row in rows]
    return [
        country
        for country in countries
        if get_country_bootstrap_config(country.get("code", "")).get(
            "public_release_enabled", True
        )
        is not False
    ]


async def has_population_table(session) -> bool:
    return await has_table(session, "population_records")


async def has_table(session, table_name: str) -> bool:
    row = await session.execute(
        text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = :table_name
            ) AS has_table
            """),
        {"table_name": table_name},
    )
    result = row.fetchone()
    return bool(result[0]) if result else False


async def _iter_query_rows(session, statement, params, *, batch_size: int = 10_000):
    """Stream large export queries without buffering a second full row copy."""
    stream = getattr(session, "stream", None)
    if stream is None:
        rows = await session.execute(statement, params)
        for row in rows:
            yield row
        return

    rows = await stream(
        statement,
        params,
        execution_options={"yield_per": batch_size},
    )
    try:
        # Cross SQLAlchemy's async/greenlet bridge once per bounded batch,
        # rather than once per observation in these multi-million-row exports.
        async for batch in rows.partitions(batch_size):
            for row in batch:
                yield row
    finally:
        await rows.close()


async def fetch_disease_records(
    session, country_code: str, use_population_table: bool
) -> list[dict]:
    """Return a loss-aware Series-first projection for one country."""

    projection_records, _source_records = await fetch_disease_export_layers(
        session, country_code, use_population_table
    )
    return projection_records


async def fetch_disease_export_layers(
    session,
    country_code: str,
    use_population_table: bool,
    *,
    recorded_at_as_of: datetime | None = None,
    registry_tables_exist: bool | None = None,
    disease_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return public projections and lossless source observations separately.

    Historical builds use the revision ledger only; current-only legacy rows
    are excluded so one export cannot silently mix observation versions.
    """

    public_config = get_country_bootstrap_config(country_code)
    legacy_records = (
        await fetch_disease_records_direct(
            session, country_code, use_population_table,
            **({"disease_ids": disease_ids} if disease_ids is not None else {}),
        )
        if recorded_at_as_of is None
        and public_config.get("public_legacy_enabled", True) is not False
        else []
    )
    if registry_tables_exist is None:
        registry_tables_exist = await has_table(
            session, "disease_surveillance_series"
        ) and await has_table(session, "disease_series_observations")
    if not registry_tables_exist:
        if recorded_at_as_of is not None:
            raise RuntimeError(
                "Historical site export requires the surveillance revision ledger."
            )
        return (
            apply_disease_cutover_projection(
                legacy_records, [], country_code=country_code
            ),
            [],
        )

    if recorded_at_as_of is not None and not await has_table(
        session, "disease_series_observation_revisions"
    ):
        raise RuntimeError(
            "Historical site export requires the surveillance revision ledger."
        )

    series_records = await fetch_disease_series_records(
        session,
        country_code,
        use_population_table,
        recorded_at_as_of=recorded_at_as_of,
        **({"disease_ids": disease_ids} if disease_ids is not None else {}),
    )
    return (
        apply_disease_cutover_projection(
            legacy_records, series_records, country_code=country_code
        ),
        series_records,
    )


async def fetch_disease_records_direct(
    session, country_code: str, use_population_table: bool, *, disease_ids: set[str] | None = None,
) -> list[dict]:
    """Query legacy disease records using the standard disease code."""
    if disease_ids is not None and not disease_ids:
        return []
    # Swiss IDD CHFL is a published Switzerland+Liechtenstein aggregate. The
    # legacy table stored it against the CH country ID, so it must not fill a
    # CH-only public curve when a source-series period is absent.
    geography_filter = ""
    if country_code.upper() == "CH":
        geography_filter = """
              AND COALESCE(
                    dr.raw_data::jsonb->0->>'Geography',
                    dr.raw_data::jsonb->>'Geography'
                  ) = 'CH'
        """
    jp_sentinel_condition = (
        "c.code = 'JP' AND d.name IN ("
        + ", ".join(f"'{disease_id}'" for disease_id in _JP_SENTINEL_DISEASE_IDS)
        + ")"
    )
    incidence_expr = "dr.incidence_rate"
    incidence_source_expr = "CASE WHEN dr.incidence_rate IS NOT NULL THEN 'original_db' ELSE 'missing_population' END"
    population_join = ""
    if use_population_table:
        incidence_expr = """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN (dr.cases::double precision / pr.population) * 100000.0
                ELSE dr.incidence_rate
            END
            """
        incidence_source_expr = """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN CASE WHEN pr.source = 'WPP' THEN 'wpp_computed' ELSE lower(pr.source) || '_computed' END
                WHEN dr.incidence_rate IS NOT NULL
                    THEN 'original_db'
                ELSE 'missing_population'
            END
            """
        population_join = (
            "LEFT JOIN population_records pr ON pr.country_id = dr.country_id "
            "AND pr.year = EXTRACT(YEAR FROM dr.time)::int"
        )

    disease_filter = " AND d.name IN :disease_ids" if disease_ids is not None else ""
    statement = text(f"""
            SELECT
                timezone('UTC', dr.time)::date AS "date",
                to_char(timezone('UTC', dr.time), 'YYYY-MM') AS year_month,
                d.name                 AS disease_id,
                dr.cases::bigint AS cases,
                dr.deaths::bigint AS deaths,
                dr.recoveries::bigint AS recoveries,
                {incidence_expr} AS incidence_rate,
                {incidence_source_expr} AS incidence_rate_source,
                dr.mortality_rate AS mortality_rate,
                dr.data_quality AS data_quality,
                CASE WHEN {jp_sentinel_condition} THEN 'weekly' ELSE COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'frequency', ''),
                    NULLIF(dr.metadata::jsonb ->> 'temporal_granularity', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'Frequency', '')
                ) END AS temporal_granularity,
                CASE WHEN {jp_sentinel_condition} THEN 'sentinel_case_notifications' ELSE COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'measure', ''),
                    NULLIF(dr.metadata::jsonb ->> 'metric_type', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'Measure', ''),
                    CASE
                        WHEN COALESCE(dr.metadata::jsonb ->> 'source_kind', '')
                            IN ('registry_annual', 'registry_disease_monthly')
                        THEN 'case_notifications'
                    END
                ) END AS metric_type,
                CASE WHEN {jp_sentinel_condition} THEN 'sentinel_surveillance' ELSE COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'reporting_basis', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'ReportingBasis', ''),
                    CASE
                        WHEN COALESCE(dr.metadata::jsonb ->> 'source_kind', '')
                            IN ('registry_annual', 'registry_disease_monthly')
                        THEN 'national_registry_notifications'
                    END
                ) END AS reporting_basis,
                COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'time_basis', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'TimeBasis', '')
                ) AS time_basis,
                NULLIF(dr.metadata::jsonb ->> 'definition_version', '') AS definition_version,
                NULLIF(dr.metadata::jsonb ->> 'comparability', '') AS comparability,
                COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'source_series_code', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'SourceSeriesCode', '')
                ) AS legacy_source_series_code
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            JOIN diseases d ON d.id = dr.disease_id
            {population_join}
            WHERE c.code = :code
            {geography_filter}
            {disease_filter}
            ORDER BY timezone('UTC', dr.time)::date ASC, d.name
            """)
    params = {"code": country_code}
    if disease_ids is not None:
        statement = statement.bindparams(bindparam("disease_ids", expanding=True))
        params["disease_ids"] = sorted(disease_ids)
    rows = await session.execute(statement, params)
    result = []
    for row in rows:
        record = dict(row._mapping)
        record["date"] = record["date"].isoformat() if record["date"] else None
        record["cases"] = safe_int(record.get("cases"))
        record["deaths"] = safe_int(record.get("deaths"))
        record["recoveries"] = safe_int(record.get("recoveries"))
        record["incidence_rate"] = safe_float(record["incidence_rate"])
        record["incidence_rate_source"] = (
            record.get("incidence_rate_source") or "missing_population"
        )
        record["mortality_rate"] = safe_float(record["mortality_rate"])
        result.append(record)
    return result


async def fetch_disease_series_records(
    session,
    country_code: str,
    use_population_table: bool,
    *,
    recorded_at_as_of: datetime | None = None,
    disease_ids: set[str] | None = None,
) -> list[dict]:
    """Read registry facts, optionally reconstructing an ingestion-time version.

    The as-of boundary is when this application recorded an observation state;
    upstream publication dates are not inferred from it.
    """
    if disease_ids is not None and not disease_ids:
        return []
    # Subdivision observations use a local geography key, while their source
    # series belongs to the parent country's registry.
    series_country_code = country_code.split("-", 1)[0]
    # These sources retain unmapped facts in holding series. Their legacy
    # series.disease_id is neither complete nor an authoritative review
    # decision; the active v3 release decides which observations may appear
    # on a standard disease page.
    use_reviewed_mapping = series_country_code in {"JP", "SE", "DE", "CH", "US", "NZ"}
    disease_id_expr = "mapping.target_code" if use_reviewed_mapping else "dss.disease_id"
    mapping_relation_expr = "mapping.mapping_relation" if use_reviewed_mapping else "dss.mapping_relation"
    comparability_expr = "mapping.comparability" if use_reviewed_mapping else "dss.comparability"
    aggregation_policy_expr = "mapping.aggregation_policy" if use_reviewed_mapping else "dss.aggregation_policy"
    jp_sentinel_series_condition = (
        "dss.country_code = 'JP' AND "
        + disease_id_expr
        + " IN ("
        + ", ".join(f"'{disease_id}'" for disease_id in _JP_SENTINEL_DISEASE_IDS)
        + ")"
    )
    projection_policy_expr = (
        "mapping.projection_policy" if use_reviewed_mapping
        else "NULLIF(dss.metadata::jsonb ->> 'projection_policy', '')"
    )
    reviewed_mapping_join = ""
    reviewed_assertions_join = ""
    if use_reviewed_mapping:
        reviewed_mapping_join = """
            JOIN LATERAL (
                SELECT source_category.*
                FROM source_disease_categories source_category
                WHERE source_category.source_id = dss.source_system
                  AND source_category.is_active IS TRUE
                  AND (
                        (
                            source_category.definition_version = dss.definition_version
                            AND source_category.source_code = COALESCE(
                                  NULLIF(dso.dimensions->>'source_disease_code', ''),
                                  NULLIF(dso.metadata->>'local_code', ''),
                                  dss.source_series_code
                                )
                        )
                        OR (
                            source_category.definition_version = 'source-current'
                            AND source_category.source_code LIKE 'label:%'
                            AND lower(btrim(source_category.canonical_source_label)) = lower(btrim(
                                  COALESCE(
                                      NULLIF(dso.dimensions->>'source_disease_label', ''),
                                      NULLIF(dso.metadata->>'local_label', ''),
                                      dss.source_label
                                  )
                                ))
                        )
                      )
                ORDER BY CASE
                    WHEN source_category.definition_version = dss.definition_version
                     AND source_category.source_code = COALESCE(
                           NULLIF(dso.dimensions->>'source_disease_code', ''),
                           NULLIF(dso.metadata->>'local_code', ''),
                           dss.source_series_code
                         ) THEN 0
                    ELSE 1
                END,
                source_category.id DESC
                LIMIT 1
            ) category ON TRUE
        """
        reviewed_assertions_join = """
            JOIN disease_mapping_assertions_v3 mapping
              ON mapping.category_id = category.id
             AND mapping.assertion_status = 'approved'
             AND mapping.target_kind = 'concept'
             AND mapping.projection_policy = 'canonical'
             AND (mapping.valid_from IS NULL OR dso.time::date >= mapping.valid_from)
             AND (mapping.valid_to IS NULL OR dso.time::date <= mapping.valid_to)
            JOIN disease_mapping_release_items_v3 release_item
              ON release_item.assertion_id = mapping.id
            JOIN disease_mapping_releases_v3 mapping_release
              ON mapping_release.id = release_item.release_id
             AND mapping_release.status = 'active'
        """
        reviewed_mapping_join += reviewed_assertions_join
    incidence_expr = "NULL::double precision"
    incidence_source_expr = "'missing_population'"
    population_join = ""
    if use_population_table:
        incidence_expr = (
            "CASE WHEN dso.dimension_key = 'all' "
            "AND pr.population IS NOT NULL AND pr.population > 0 "
            "THEN (dso.value::double precision / pr.population) * 100000.0 "
            "ELSE NULL END"
        )
        incidence_source_expr = (
            "CASE WHEN dso.dimension_key = 'all' "
            "AND pr.population IS NOT NULL AND pr.population > 0 "
            "THEN CASE WHEN pr.source = 'WPP' THEN 'wpp_computed' ELSE lower(pr.source) || '_computed' END ELSE 'missing_population' END"
        )
        population_join = (
            "LEFT JOIN countries registry_country "
            "ON registry_country.code = :population_code "
            "LEFT JOIN population_records pr "
            "ON pr.country_id = registry_country.id "
            "AND pr.year = EXTRACT(YEAR FROM dso.time)::int"
        )

    query_params = {
        "code": series_country_code,
        "geography_key": f"country:{country_code}:national",
    }
    if recorded_at_as_of is not None:
        if recorded_at_as_of.tzinfo is None:
            raise ValueError("recorded_at_as_of must include a timezone")
        query_params["recorded_at_as_of"] = recorded_at_as_of.astimezone(
            timezone.utc
        )
        observation_relation = """(
            WITH latest_revisions AS (
                SELECT DISTINCT ON (
                    r.time, r.series_code, r.geography_key, r.dimension_key
                ) r.time, r.series_code, r.geography_key, r.dimension_key,
                  r.value, r.unit, r.suppressed, r.suppression_reason,
                  r.quality_status, r.dimensions, r.raw_data, r.metadata,
                  r.recorded_at
                FROM disease_series_observation_revisions r
                JOIN disease_surveillance_series revision_series
                  ON revision_series.series_code = r.series_code
                WHERE r.recorded_at <= :recorded_at_as_of
                  AND revision_series.country_code = :code
                  AND r.geography_key = :geography_key
                ORDER BY r.time, r.series_code, r.geography_key,
                         r.dimension_key, r.recorded_at DESC
            ),
            current_fallback AS (
                SELECT current.time, current.series_code,
                  current.geography_key, current.dimension_key, current.value,
                  current.unit, current.suppressed, current.suppression_reason,
                  current.quality_status, current.dimensions, current.raw_data,
                  current.metadata, current.updated_at AS recorded_at
                FROM disease_series_observations current
                JOIN disease_surveillance_series current_series
                  ON current_series.series_code = current.series_code
                WHERE current_series.country_code = :code
                  AND current.geography_key = :geography_key
                  AND current.updated_at <= :recorded_at_as_of
                  AND NOT EXISTS (
                    SELECT 1 FROM disease_series_observation_revisions prior
                    WHERE prior.time = current.time
                      AND prior.series_code = current.series_code
                      AND prior.geography_key = current.geography_key
                      AND prior.dimension_key = current.dimension_key
                      AND prior.recorded_at <= :recorded_at_as_of
                  )
            ),
            history_coverage AS (
                SELECT NOT EXISTS (
                    SELECT 1 FROM disease_series_observations current
                    JOIN disease_surveillance_series current_series
                      ON current_series.series_code = current.series_code
                    WHERE current_series.country_code = :code
                      AND current.geography_key = :geography_key
                      AND current.updated_at > :recorded_at_as_of
                      AND NOT EXISTS (
                        SELECT 1
                        FROM disease_series_observation_revisions prior
                        WHERE prior.time = current.time
                          AND prior.series_code = current.series_code
                          AND prior.geography_key = current.geography_key
                          AND prior.dimension_key = current.dimension_key
                          AND prior.recorded_at <= :recorded_at_as_of
                      )
                ) AS complete
            )
            SELECT resolved.*, history_coverage.complete
              AS recorded_history_complete
            FROM (
                SELECT * FROM latest_revisions
                UNION ALL
                SELECT * FROM current_fallback
            ) resolved
            CROSS JOIN history_coverage
        ) AS dso"""
        recorded_at_expression = "dso.recorded_at"
        history_complete_expression = "dso.recorded_history_complete"
    else:
        observation_relation = "disease_series_observations AS dso"
        recorded_at_expression = "NULL::timestamptz"
        history_complete_expression = "NULL::boolean"
    if use_population_table:
        query_params["population_code"] = country_code

    population_expr = (
        "CASE WHEN dso.dimension_key = 'all' THEN pr.population::double precision "
        "ELSE NULL::double precision END"
        if use_population_table
        else "NULL::double precision"
    )
    population_source_expr = (
        "CASE WHEN dso.dimension_key = 'all' THEN pr.source ELSE NULL::text END"
        if use_population_table
        else "NULL::text"
    )

    candidate_prefix = ""
    if use_reviewed_mapping and (
        disease_ids is None or len(disease_ids) >= _DENSE_REVIEWED_DISEASE_QUERY_THRESHOLD
    ):
        # Category identity and precedence depend on source/definition/code/
        # label, not the observation date. Resolve each distinct identity once
        # instead of repeating the same LATERAL lookup for every history row.
        # Mapping validity still applies per observation below. Materialize only
        # export fields, excluding heavyweight raw provenance from this scan.
        # Dense target sets share this path and retain the exact final target
        # predicate; reading a broad scope never widens the returned diseases.
        history_columns = (
            ", dso.recorded_at, dso.recorded_history_complete"
            if recorded_at_as_of is not None else ""
        )
        candidate_prefix = f"""
            WITH export_observations AS MATERIALIZED (
                SELECT dso.time, dso.series_code, dso.geography_key,
                       dso.dimension_key, dso.dimensions, dso.metadata,
                       dso.value, dso.unit, dso.quality_status, dso.suppressed,
                       COALESCE(NULLIF(dso.dimensions->>'source_disease_code', ''),
                                NULLIF(dso.metadata->>'local_code', ''),
                                export_series.source_series_code) AS effective_source_code,
                       lower(btrim(COALESCE(
                           NULLIF(dso.dimensions->>'source_disease_label', ''),
                           NULLIF(dso.metadata->>'local_label', ''),
                           export_series.source_label))) AS effective_source_label
                       {history_columns}
                FROM {observation_relation}
                JOIN disease_surveillance_series export_series
                  ON export_series.series_code = dso.series_code
                WHERE export_series.country_code = :code
                  AND dso.geography_key = :geography_key
                  AND dso.suppressed IS FALSE
                  AND dso.value IS NOT NULL
                  AND dso.unit = export_series.unit
                  AND dso.quality_status <> 'rejected'
            ), export_identities AS MATERIALIZED (
                SELECT DISTINCT dso.series_code, dss.source_system,
                       dss.definition_version, dso.effective_source_code,
                       dso.effective_source_label
                FROM export_observations dso
                JOIN disease_surveillance_series dss ON dss.series_code = dso.series_code
            ), export_categories AS MATERIALIZED (
                SELECT identity.series_code, identity.effective_source_code,
                       identity.effective_source_label, category.id
                FROM export_identities identity
                JOIN LATERAL (
                    SELECT source_category.id
                    FROM source_disease_categories source_category
                    WHERE source_category.source_id = identity.source_system
                      AND source_category.is_active IS TRUE
                      AND (
                        (source_category.definition_version = identity.definition_version
                         AND source_category.source_code = identity.effective_source_code)
                        OR (source_category.definition_version = 'source-current'
                            AND source_category.source_code LIKE 'label:%'
                            AND lower(btrim(source_category.canonical_source_label)) = identity.effective_source_label)
                      )
                    ORDER BY CASE
                        WHEN source_category.definition_version = identity.definition_version
                         AND source_category.source_code = identity.effective_source_code THEN 0
                        ELSE 1
                    END, source_category.id DESC
                    LIMIT 1
                ) category ON TRUE
            )
        """
        observation_relation = "export_observations AS dso"
        reviewed_mapping_join = """
            JOIN export_categories category
              ON category.series_code = dso.series_code
             AND category.effective_source_code IS NOT DISTINCT FROM dso.effective_source_code
             AND category.effective_source_label IS NOT DISTINCT FROM dso.effective_source_label
        """ + reviewed_assertions_join
    elif use_reviewed_mapping and disease_ids is not None:
        # A target filter after the per-observation LATERAL category selection
        # still resolves every unrelated observation first. Preselect a safe
        # superset using active target mappings, then retain the original exact
        # category ordering and target check below. Every original matching row
        # satisfies this EXISTS condition, including newly registered sources.
        candidate_prefix = f"""
            WITH requested_categories AS MATERIALIZED (
                    SELECT candidate_category.source_id, candidate_category.definition_version,
                           candidate_category.source_code, candidate_category.canonical_source_label,
                           candidate_mapping.valid_from AS mapping_valid_from,
                           candidate_mapping.valid_to AS mapping_valid_to
                    FROM source_disease_categories candidate_category
                    JOIN disease_mapping_assertions_v3 candidate_mapping
                      ON candidate_mapping.category_id = candidate_category.id
                     AND candidate_mapping.assertion_status = 'approved'
                     AND candidate_mapping.target_kind = 'concept'
                     AND candidate_mapping.projection_policy = 'canonical'
                     AND candidate_mapping.target_code IN :disease_ids
                    JOIN disease_mapping_release_items_v3 candidate_item
                      ON candidate_item.assertion_id = candidate_mapping.id
                    JOIN disease_mapping_releases_v3 candidate_release
                      ON candidate_release.id = candidate_item.release_id
                     AND candidate_release.status = 'active'
                    WHERE candidate_category.is_active IS TRUE
            ), requested_observations AS MATERIALIZED (
                SELECT dso.* FROM {observation_relation}
                JOIN disease_surveillance_series candidate_series
                  ON candidate_series.series_code = dso.series_code
                WHERE candidate_series.country_code = :code
                  AND dso.geography_key = :geography_key
                  AND EXISTS (
                    SELECT 1 FROM requested_categories candidate_category
                    WHERE candidate_category.source_id = candidate_series.source_system
                      AND (candidate_category.mapping_valid_from IS NULL OR dso.time::date >= candidate_category.mapping_valid_from)
                      AND (candidate_category.mapping_valid_to IS NULL OR dso.time::date <= candidate_category.mapping_valid_to)
                      AND (
                        (candidate_category.definition_version = candidate_series.definition_version
                         AND candidate_category.source_code = COALESCE(
                           NULLIF(dso.dimensions->>'source_disease_code', ''),
                           NULLIF(dso.metadata->>'local_code', ''), candidate_series.source_series_code))
                        OR (candidate_category.definition_version = 'source-current'
                            AND candidate_category.source_code LIKE 'label:%'
                            AND lower(btrim(candidate_category.canonical_source_label)) = lower(btrim(COALESCE(
                              NULLIF(dso.dimensions->>'source_disease_label', ''),
                              NULLIF(dso.metadata->>'local_label', ''), candidate_series.source_label))))
                      )
                  )
            )
        """
        observation_relation = "requested_observations AS dso"

    disease_filter = f" AND {disease_id_expr} IN :disease_ids" if disease_ids is not None else ""
    statement = text(f"""
            {candidate_prefix}
            SELECT
                timezone('UTC', dso.time)::date AS "date",
                to_char(timezone('UTC', dso.time), 'YYYY-MM') AS year_month,
                {disease_id_expr} AS disease_id,
                dso.value::double precision AS cases,
                NULL::bigint AS deaths,
                NULL::bigint AS recoveries,
                {incidence_expr} AS incidence_rate,
                {incidence_source_expr} AS incidence_rate_source,
                {population_expr} AS population,
                {population_source_expr} AS population_source,
                NULLIF(dso.metadata::jsonb ->> 'period_start', '') AS period_start,
                NULLIF(dso.metadata::jsonb ->> 'period_end_exclusive', '') AS period_end_exclusive,
                NULLIF(dss.metadata::jsonb ->> 'calendar', '') AS calendar,
                NULL::double precision AS mortality_rate,
                dso.quality_status AS data_quality,
                dso.series_code,
                dss.source_system,
                dss.source_series_code,
                dss.source_label,
                dss.definition_version,
                dss.case_definition,
                dss.case_definition_uri,
                CASE WHEN {jp_sentinel_series_condition}
                     THEN 'sentinel_case_notifications'
                     ELSE dss.metric_type END AS metric_type,
                CASE WHEN {jp_sentinel_series_condition}
                     THEN 'sentinel_surveillance'
                     ELSE dss.reporting_basis END AS reporting_basis,
                NULLIF(dss.metadata::jsonb ->> 'time_basis', '') AS time_basis,
                CASE WHEN {jp_sentinel_series_condition}
                     THEN 'weekly'
                     ELSE dss.temporal_granularity END AS temporal_granularity,
                dss.unit AS series_unit,
                dso.unit AS observation_unit,
                {mapping_relation_expr} AS mapping_relation,
                {comparability_expr} AS comparability,
                {aggregation_policy_expr} AS aggregation_policy,
                dss.availability_status,
                dss.missing_value_policy,
                dss.valid_from,
                dss.valid_to,
                NULLIF(
                    dss.metadata::jsonb ->> 'definition_effective_from', ''
                ) AS definition_effective_from,
                NULLIF(
                    dss.metadata::jsonb ->> 'definition_effective_to', ''
                ) AS definition_effective_to,
                dss.metadata::jsonb -> 'comparability_break' AS comparability_break,
                NULLIF(dss.metadata::jsonb ->> 'comparability_set', '')
                    AS comparability_set,
                {projection_policy_expr} AS projection_policy,
                NULLIF(dss.metadata::jsonb ->> 'projection_priority', '')::integer
                    AS projection_priority,
                dss.is_active AS series_is_active,
                dso.quality_status,
                dso.geography_key,
                dso.dimension_key,
                dso.dimensions,
                {recorded_at_expression} AS recorded_at,
                {history_complete_expression} AS recorded_history_complete
            FROM {observation_relation}
            JOIN disease_surveillance_series dss
              ON dss.series_code = dso.series_code
            {reviewed_mapping_join}
            {population_join}
            WHERE dss.country_code = :code
              AND {disease_id_expr} IS NOT NULL
              {disease_filter}
              AND dso.geography_key = :geography_key
              AND dso.suppressed IS FALSE
              AND dso.value IS NOT NULL
              AND dso.unit = dss.unit
              AND dso.quality_status <> 'rejected'
            ORDER BY dss.disease_id, dso.series_code,
                     timezone('UTC', dso.time)::date ASC
            """)
    if disease_ids is not None:
        statement = statement.bindparams(bindparam("disease_ids", expanding=True))
        query_params["disease_ids"] = sorted(disease_ids)
    rows = _iter_query_rows(
        session,
        statement,
        query_params,
    )

    public_source_systems = {
        str(value).strip()
        for value in (
            get_country_bootstrap_config(country_code).get(
                "public_source_systems", []
            )
            or []
        )
        if str(value).strip()
    }
    result: list[dict] = []
    async for row in rows:
        record = dict(row._mapping)
        if (
            public_source_systems
            and str(record.get("source_system") or "") not in public_source_systems
        ):
            continue
        record["date"] = record["date"].isoformat() if record.get("date") else None
        record["cases"] = _normalise_count(record.get("cases"))
        record["deaths"] = safe_int(record.get("deaths"))
        record["recoveries"] = safe_int(record.get("recoveries"))
        record["incidence_rate"] = safe_float(record.get("incidence_rate"))
        record["incidence_rate_source"] = (
            record.get("incidence_rate_source") or "missing_population"
        )
        record["mortality_rate"] = safe_float(record.get("mortality_rate"))
        record["recorded_at"] = iso_or_none(record.get("recorded_at"))
        record["recorded_at_as_of"] = (
            query_params["recorded_at_as_of"].isoformat()
            if recorded_at_as_of is not None
            else None
        )
        for field in ("valid_from", "valid_to"):
            value = record.get(field)
            record[field] = value.isoformat() if value else None
        result.append(record)
    return result


async def fetch_country_frequency_meta(
    session,
    country_code: str,
    *,
    registry_tables_exist: bool | None = None,
) -> dict:
    """Describe source periods without converting period totals into weekly rates."""
    series_country_code = country_code.split("-", 1)[0]
    source_frequencies: list[str] = []
    if registry_tables_exist is None:
        registry_tables_exist = await has_table(
            session, "disease_surveillance_series"
        ) and await has_table(session, "disease_series_observations")
    if registry_tables_exist:
        series_rows = await session.execute(
            text("""
                SELECT DISTINCT lower(dss.temporal_granularity)
                    AS temporal_granularity,
                    dss.source_system
                FROM disease_surveillance_series dss
                JOIN disease_series_observations dso
                  ON dso.series_code = dss.series_code
                WHERE dss.country_code = :code
                  AND dso.geography_key = :geography_key
                  AND dso.suppressed IS FALSE
                  AND dso.value IS NOT NULL
                  AND dso.quality_status <> 'rejected'
                ORDER BY temporal_granularity ASC
                """),
            {
                "code": series_country_code,
                "geography_key": f"country:{country_code}:national",
            },
        )
        public_source_systems = {
            str(value).strip()
            for value in (
                get_country_bootstrap_config(country_code).get(
                    "public_source_systems", []
                )
                or []
            )
            if str(value).strip()
        }
        normalized: set[str] = set()
        for row in series_rows:
            mapped = dict(row._mapping)
            if (
                public_source_systems
                and str(mapped.get("source_system") or "")
                not in public_source_systems
            ):
                continue
            value = str(mapped.get("temporal_granularity") or "").upper()
            normalized.add("ANNUAL" if value == "YEARLY" else value)
        normalized.discard("")
        frequency_order = {
            "DAILY": 0,
            "WEEKLY": 1,
            "MONTHLY": 2,
            "QUARTERLY": 3,
            "ANNUAL": 4,
        }
        source_frequencies = sorted(
            normalized,
            key=lambda value: (frequency_order.get(value, 99), value),
        )

    if source_frequencies:
        return {
            "source_frequency": (
                source_frequencies[0]
                if len(source_frequencies) == 1
                else "MIXED"
            ),
            "source_frequencies": source_frequencies,
            "canonical_frequency": "SOURCE_REPORTED_PERIODS",
            "aggregation_rule": "preserve_source_period_counts",
        }

    # Legacy-only countries do not carry explicit series definitions. Infer a
    # display label conservatively, while still preserving the reported count.
    rows = await session.execute(
        text("""
            SELECT DISTINCT timezone('UTC', dr.time)::date AS report_date
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            WHERE c.code = :code
            ORDER BY report_date ASC
            """),
        {"code": country_code},
    )
    report_dates = [
        dict(row._mapping)["report_date"]
        for row in rows
        if dict(row._mapping).get("report_date")
    ]
    if len(report_dates) < 2:
        return {
            "source_frequency": "UNKNOWN",
            "source_frequencies": [],
            "canonical_frequency": "SOURCE_REPORTED_PERIODS",
            "aggregation_rule": "preserve_source_period_counts",
        }

    diffs = []
    for index in range(1, len(report_dates)):
        delta_days = (report_dates[index] - report_dates[index - 1]).days
        if delta_days > 0:
            diffs.append(delta_days)

    if not diffs:
        source_frequency = "UNKNOWN"
    else:
        median_days = statistics.median(diffs)
        if median_days >= 300:
            source_frequency = "ANNUAL"
        elif 75 <= median_days <= 120:
            source_frequency = "QUARTERLY"
        elif 25 <= median_days <= 35:
            source_frequency = "MONTHLY"
        elif 5 <= median_days <= 10:
            source_frequency = "WEEKLY"
        else:
            source_frequency = "DAILY"

    return {
        "source_frequency": source_frequency,
        "source_frequencies": (
            [] if source_frequency == "UNKNOWN" else [source_frequency]
        ),
        "canonical_frequency": "SOURCE_REPORTED_PERIODS",
        "aggregation_rule": "preserve_source_period_counts",
    }


async def fetch_reports(session) -> list[dict]:
    return await _report_queries.fetch_reports(session, safe_float=safe_float,
        compact_report_metadata=compact_report_metadata)


async def fetch_report_detail(session, report_id: int) -> dict | None:
    return await _report_queries.fetch_report_detail(session, report_id, safe_float=safe_float,
        compact_report_metadata=compact_report_metadata)


async def fetch_disease_knowledge_briefs(session) -> dict[str, dict[str, dict]]:
    return await _knowledge_queries.fetch_disease_knowledge_briefs(session,
        has_table=has_table, safe_int=safe_int, iso_or_none=iso_or_none,
        enrich_source_attribution=enrich_source_attribution)


async def fetch_country_briefs(session) -> dict[str, dict[str, dict]]:
    return await _knowledge_queries.fetch_country_briefs(session, has_table=has_table)


__all__ = [
    "compact_report_metadata",
    "enrich_source_attribution",
    "fetch_countries",
    "fetch_country_briefs",
    "fetch_country_frequency_meta",
    "fetch_disease_export_layers",
    "fetch_disease_knowledge_briefs",
    "fetch_disease_records",
    "fetch_disease_records_direct",
    "fetch_disease_series_records",
    "fetch_report_detail",
    "fetch_reports",
    "has_population_table",
    "has_table",
    "iso_or_none",
    "safe_float",
    "safe_int",
    "source_metadata_field",
]
