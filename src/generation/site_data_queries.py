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

from sqlalchemy import text

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


def compact_report_metadata(
    metadata: dict | None, *, include_figures: bool = False
) -> dict:
    """Return site-facing report metadata without heavyweight agent internals."""
    if not isinstance(metadata, dict):
        return {}

    document = metadata.get("report_document_v4")
    if isinstance(document, dict):
        compact = {
            "report_layout": "report_v4",
            "schema_version": metadata.get("schema_version")
            or document.get("schema_version"),
            "method_version": metadata.get("method_version")
            or document.get("schema_version"),
            "default_locale": document.get("default_locale"),
            "locales": document.get("locales") or ["zh", "en"],
            "quality_gate": metadata.get("quality_gate") or {},
            "data_quality": document.get("data_quality")
            or metadata.get("data_quality")
            or {},
            "summary_metrics": {
                **(document.get("metrics") or {}),
                "death_reporting": document.get("death_reporting") or {},
            },
            "death_reporting": document.get("death_reporting") or {},
            "disease_directory": document.get("disease_directory") or [],
            "risk_ranking": document.get("risk_ranking") or [],
            "references": document.get("references") or [],
        }
        if include_figures:
            compact["figures"] = document.get("figures") or []
            compact["figure_data"] = metadata.get("figure_data") or {}
            compact["report_document_v4"] = document
        return compact

    return {}


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


async def fetch_disease_records(
    session, country_code: str, use_population_table: bool
) -> list[dict]:
    """Return a loss-aware Series-first projection for one country."""

    projection_records, _source_records = await fetch_disease_export_layers(
        session, country_code, use_population_table
    )
    return projection_records


async def fetch_disease_export_layers(
    session, country_code: str, use_population_table: bool
) -> tuple[list[dict], list[dict]]:
    """Return public projections and lossless source observations separately."""

    public_config = get_country_bootstrap_config(country_code)
    legacy_records = (
        await fetch_disease_records_direct(
            session, country_code, use_population_table
        )
        if public_config.get("public_legacy_enabled", True) is not False
        else []
    )
    registry_tables_exist = await has_table(
        session, "disease_surveillance_series"
    ) and await has_table(session, "disease_series_observations")
    if not registry_tables_exist:
        return (
            apply_disease_cutover_projection(
                legacy_records, [], country_code=country_code
            ),
            [],
        )

    series_records = await fetch_disease_series_records(
        session, country_code, use_population_table
    )
    return (
        apply_disease_cutover_projection(
            legacy_records, series_records, country_code=country_code
        ),
        series_records,
    )


async def fetch_disease_records_direct(
    session, country_code: str, use_population_table: bool
) -> list[dict]:
    """Query legacy disease records using the standard disease code."""
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
                    THEN 'wpp_computed'
                WHEN dr.incidence_rate IS NOT NULL
                    THEN 'original_db'
                ELSE 'missing_population'
            END
            """
        population_join = (
            "LEFT JOIN population_records pr ON pr.country_id = dr.country_id "
            "AND pr.year = EXTRACT(YEAR FROM dr.time)::int"
        )

    rows = await session.execute(
        text(f"""
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
                COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'frequency', ''),
                    NULLIF(dr.metadata::jsonb ->> 'temporal_granularity', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'Frequency', '')
                ) AS temporal_granularity,
                COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'measure', ''),
                    NULLIF(dr.metadata::jsonb ->> 'metric_type', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'Measure', ''),
                    CASE
                        WHEN COALESCE(dr.metadata::jsonb ->> 'source_kind', '')
                            IN ('registry_annual', 'registry_disease_monthly')
                        THEN 'case_notifications'
                    END
                ) AS metric_type,
                COALESCE(
                    NULLIF(dr.metadata::jsonb ->> 'reporting_basis', ''),
                    NULLIF(dr.raw_data::jsonb ->> 'ReportingBasis', ''),
                    CASE
                        WHEN COALESCE(dr.metadata::jsonb ->> 'source_kind', '')
                            IN ('registry_annual', 'registry_disease_monthly')
                        THEN 'national_registry_notifications'
                    END
                ) AS reporting_basis,
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
            ORDER BY timezone('UTC', dr.time)::date ASC, d.name
            """),
        {"code": country_code},
    )
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
) -> list[dict]:
    """Read national, unstratified registry facts suitable for site export."""
    # Province pages are independent public jurisdictions, but the two source
    # registries are owned by CN. Their geography keys keep province facts
    # isolated from the national China series.
    series_country_code = "CN" if country_code.startswith("CN-") else country_code
    incidence_expr = "NULL::double precision"
    incidence_source_expr = "'missing_population'"
    population_join = ""
    if use_population_table:
        incidence_expr = (
            "CASE WHEN pr.population IS NOT NULL AND pr.population > 0 "
            "THEN (dso.value::double precision / pr.population) * 100000.0 "
            "ELSE NULL END"
        )
        incidence_source_expr = (
            "CASE WHEN pr.population IS NOT NULL AND pr.population > 0 "
            "THEN 'wpp_computed' ELSE 'missing_population' END"
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
    if use_population_table:
        query_params["population_code"] = country_code

    rows = await session.execute(
        text(f"""
            SELECT
                timezone('UTC', dso.time)::date AS "date",
                to_char(timezone('UTC', dso.time), 'YYYY-MM') AS year_month,
                dss.disease_id,
                dso.value::double precision AS cases,
                NULL::bigint AS deaths,
                NULL::bigint AS recoveries,
                {incidence_expr} AS incidence_rate,
                {incidence_source_expr} AS incidence_rate_source,
                NULL::double precision AS mortality_rate,
                dso.quality_status AS data_quality,
                dso.series_code,
                dss.source_system,
                dss.source_series_code,
                dss.source_label,
                dss.definition_version,
                dss.case_definition,
                dss.case_definition_uri,
                dss.metric_type,
                dss.reporting_basis,
                NULLIF(dss.metadata::jsonb ->> 'time_basis', '') AS time_basis,
                dss.temporal_granularity,
                dss.unit AS series_unit,
                dso.unit AS observation_unit,
                dss.mapping_relation,
                dss.comparability,
                dss.aggregation_policy,
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
                NULLIF(dss.metadata::jsonb ->> 'projection_policy', '')
                    AS projection_policy,
                NULLIF(dss.metadata::jsonb ->> 'projection_priority', '')::integer
                    AS projection_priority,
                dss.is_active AS series_is_active,
                dso.quality_status,
                dso.geography_key,
                dso.dimension_key
            FROM disease_series_observations dso
            JOIN disease_surveillance_series dss
              ON dss.series_code = dso.series_code
            {population_join}
            WHERE dss.country_code = :code
              AND dss.disease_id IS NOT NULL
              AND dso.geography_key = :geography_key
              AND dso.dimension_key = 'all'
              AND dso.suppressed IS FALSE
              AND dso.value IS NOT NULL
              AND dso.unit = dss.unit
              AND dso.quality_status <> 'rejected'
            ORDER BY dss.disease_id, dso.series_code,
                     timezone('UTC', dso.time)::date ASC
            """),
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
    for row in rows:
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
        for field in ("valid_from", "valid_to"):
            value = record.get(field)
            record[field] = value.isoformat() if value else None
        result.append(record)
    return result


async def fetch_country_frequency_meta(session, country_code: str) -> dict:
    """Describe source periods without converting period totals into weekly rates."""
    series_country_code = "CN" if country_code.startswith("CN-") else country_code
    source_frequencies: list[str] = []
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
    rows = await session.execute(text("""
            SELECT
                r.id, r.title, r.report_type, r.status,
                r.period_start::date  AS period_start,
                r.period_end::date    AS period_end,
                r.created_at,
                r.summary,
                r.quality_score,
                r.metadata,
                r.generation_config,
                c.code                AS country_code,
                c.name                AS country_name,
                c.name_en             AS country_name_en,
                c.name_local          AS country_name_local
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.status IN ('COMPLETED', 'APPROVED', 'PUBLISHED')
            ORDER BY r.created_at DESC
            """))
    result = []
    for row in rows:
        report = dict(row._mapping)
        metadata = (
            report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
        )
        document = metadata.get("report_document_v4")
        if not isinstance(document, dict):
            continue
        report["period_start"] = (
            report["period_start"].isoformat() if report["period_start"] else None
        )
        report["period_end"] = (
            report["period_end"].isoformat() if report["period_end"] else None
        )
        report["created_at"] = (
            report["created_at"].isoformat() if report["created_at"] else None
        )
        report["quality_score"] = safe_float(report["quality_score"])
        report_language = "zh"
        report["title"] = (document.get("title") or {}).get("zh") or report.get(
            "title"
        )
        report["summary"] = (document.get("summary") or {}).get("zh") or report.get(
            "summary"
        )
        report["key_findings"] = (document.get("key_findings") or {}).get("zh") or []
        report["metadata"] = compact_report_metadata(metadata, include_figures=False)
        report["metadata"]["language"] = report_language
        report["language"] = report_language
        report.pop("generation_config", None)
        report["analysis_summary"] = None
        report["quality_gate"] = report["metadata"].get("quality_gate")
        report["data_quality"] = report["metadata"].get("data_quality")
        report["method_version"] = report["metadata"].get("method_version")
        result.append(report)
    return result


async def fetch_report_detail(session, report_id: int) -> dict | None:
    row = await session.execute(
        text("""
            SELECT
                r.id, r.title, r.report_type,
                r.period_start::date AS period_start,
                r.period_end::date   AS period_end,
                r.created_at, r.ai_model_used, r.quality_score,
                r.summary, r.key_findings,
                r.metadata,
                r.generation_config,
                c.code               AS country_code,
                c.name               AS country_name,
                c.name_en            AS country_name_en,
                c.name_local         AS country_name_local
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.id = :id
            """),
        {"id": report_id},
    )
    result_row = row.fetchone()
    if not result_row:
        return None
    report = dict(result_row._mapping)
    report["period_start"] = (
        report["period_start"].isoformat() if report["period_start"] else None
    )
    report["period_end"] = (
        report["period_end"].isoformat() if report["period_end"] else None
    )
    report["created_at"] = (
        report["created_at"].isoformat() if report["created_at"] else None
    )
    report["quality_score"] = safe_float(report["quality_score"])
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    document = metadata.get("report_document_v4")
    if not isinstance(document, dict):
        return None
    report_language = "zh"
    report["title"] = (document.get("title") or {}).get("zh") or report.get("title")
    report["summary"] = (document.get("summary") or {}).get("zh") or report.get(
        "summary"
    )
    report["key_findings"] = (document.get("key_findings") or {}).get("zh") or []
    report["report_document_v4"] = document
    report["metadata"] = compact_report_metadata(metadata, include_figures=True)
    report["metadata"]["language"] = report_language
    report["language"] = report_language
    report.pop("generation_config", None)
    report["analysis_summary"] = None
    report["quality_gate"] = report["metadata"].get("quality_gate")
    report["data_quality"] = report["metadata"].get("data_quality")
    report["method_version"] = report["metadata"].get("method_version")
    report["sections"] = [
        {
            "section_type": section.get("type"),
            "section_order": section.get("order"),
            "title": (section.get("title") or {}).get("zh"),
            "content": (section.get("body") or {}).get("zh"),
            "charts": section.get("figures") or [],
            "metadata": {
                "locales": {
                    "zh": {
                        "title": (section.get("title") or {}).get("zh"),
                        "content": (section.get("body") or {}).get("zh"),
                    },
                    "en": {
                        "title": (section.get("title") or {}).get("en"),
                        "content": (section.get("body") or {}).get("en"),
                    },
                },
                "evidence_refs": section.get("evidence_refs") or [],
                "quality_flags": section.get("quality_flags") or [],
            },
        }
        for section in document.get("sections") or []
    ]
    return report


async def fetch_disease_knowledge_briefs(session) -> dict[str, dict[str, dict]]:
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


async def fetch_country_briefs(session) -> dict[str, dict[str, dict]]:
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
