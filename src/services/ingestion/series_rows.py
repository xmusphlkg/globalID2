"""Source-series ingestion within the caller's existing mutation transaction."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.storage import (
    SeriesObservationQualityError, SeriesObservationQualityPolicy,
    SeriesObservationSaveResult,
)


def series_quality_policy(updater, *, source_ids, country_config) -> SeriesObservationQualityPolicy:
    """Resolve per-country guard settings with an operational env override."""

    country_code = str(getattr(updater, "country_code", "") or "").upper()
    series_country_code = str(
        getattr(updater, "series_country_code", "") or country_code
    ).upper()
    config = country_config(country_code) if country_code else {}
    crawler_config = (
        config.get("crawler_config", {}) if isinstance(config, dict) else {}
    )
    raw_policy = crawler_config.get("series_quality_guard", {})
    if not isinstance(raw_policy, dict):
        raw_policy = {}
    raw_policy = dict(raw_policy)

    updater_policy = getattr(updater, "series_quality_guard", None)
    if isinstance(updater_policy, dict):
        raw_policy.update(updater_policy)

    registry_coverage = getattr(updater, "series_registry_coverage", None)
    if registry_coverage:
        raw_policy["registry_coverage"] = registry_coverage
    elif (
        country_code in source_ids
        or series_country_code in source_ids
    ):
        raw_policy.setdefault("registry_coverage", "required")

    env_country_code = country_code.replace("-", "_")
    country_env = (
        os.getenv(f"GLOBALID_{env_country_code}_SERIES_QUALITY_MODE")
        if country_code
        else None
    )
    global_env = os.getenv("GLOBALID_SERIES_QUALITY_MODE")
    if country_env or global_env:
        raw_policy["mode"] = country_env or global_env
    return SeriesObservationQualityPolicy.from_mapping(raw_policy)


async def save_series_rows(
    db,
    updater,
    rows,
    *,
    series_rows=None,
    _bounded_batch=False,
    _jp_holding_profiles=None,
    store_type,
    save_batch,
    quality_policy_for,
    source_ids,
    mutation_lock,
    logger,
):
    """Persist lossless source-series facts without a legacy projection."""

    # Discovery and dynamic holding-series creation also write registry
    # tables. Protect them before the first such write, including callers
    # that intentionally bypass the legacy projection.
    await mutation_lock(
        db, str(getattr(updater, "series_country_code", "") or updater.country_code)
    )

    # The IDWR cache contains millions of rows. Building normalized
    # observations and holding-series payloads for the entire history at
    # once can exhaust the worker's memory. Keep the enclosing transaction
    # and mutation lock, but bound the intermediate objects for each batch.
    source_rows = rows if series_rows is None else series_rows
    if (
        not _bounded_batch
        and str(getattr(updater, "series_country_code", "")).upper() == "JP"
        and len(source_rows) > 10000
    ):
        source_id = getattr(updater, "ontology_source_id", None) or "SRC_JP_NIID"
        profiles = await asyncio.to_thread(
            store_type()._jp_mixed_holding_profiles,
            source_rows, "JP", source_id, "Cases",
        )
        totals = None
        fields = (
            "upserted", "skipped_unmatched", "skipped_ambiguous",
            "skipped_invalid", "skipped_registry_not_synced",
            "skipped_unregistered", "skipped_missing", "holding_observations",
        )
        offset = 0
        while offset < len(source_rows):
            end = min(offset + 10000, len(source_rows))
            # JP rows are sorted by reporting week. Keep a week together
            # for cross-series quality checks; bound unusually large weeks.
            if end < len(source_rows):
                boundary = (source_rows[end - 1].get("MMWRYear"), source_rows[end - 1].get("MMWRWeek"))
                if all(boundary):
                    while end < min(offset + 20000, len(source_rows)) and (source_rows[end].get("MMWRYear"), source_rows[end].get("MMWRWeek")) == boundary:
                        end += 1
            batch = source_rows[offset:end]
            result = await save_batch(
                db, updater, batch, _bounded_batch=True,
                _jp_holding_profiles=profiles,
            )
            if totals is None:
                totals = result
            else:
                totals = replace(totals, **{
                    field: getattr(totals, field, 0) + getattr(result, field, 0)
                    for field in fields
                }, publication_changes=(
                    totals.publication_changes + result.publication_changes
                    if totals.publication_changes is not None
                    and result.publication_changes is not None else None
                ))
            offset = end
        return totals

    quality_policy = quality_policy_for(updater)
    country_code = str(getattr(updater, "country_code", "") or "").upper()
    series_country_code = str(
        getattr(updater, "series_country_code", "") or country_code
    ).upper()
    source_id = getattr(updater, "ontology_source_id", None)
    if source_id is None:
        source_id = source_ids.get(
            country_code,
            source_ids.get(series_country_code),
        )
    geography_key = getattr(updater, "series_geography_key", None)
    geography_from_rows = bool(
        getattr(updater, "series_geography_from_rows", False)
    )
    if (
        geography_key is None
        and not geography_from_rows
        and country_code in source_ids
    ):
        # Updaters without an explicit row-grain contract emit a national
        # aggregate.  Scope-aware updaters opt out and derive the key from
        # each source row.
        geography_key = f"country:{country_code}:national"
    series_store = store_type()
    source_series_rows = rows if series_rows is None else series_rows
    # Mapping Registry v3 discovers source categories for every country at
    # the same transactional boundary as the source facts.  Unknown
    # categories are retained and queued for AI review; they are never
    # silently coerced to a legacy disease ID.
    if isinstance(db, AsyncSession) and source_series_rows:
        from src.services.disease_mapping_registry_service import (
            disease_mapping_registry_service,
        )

        source_series_rows = await asyncio.to_thread(
            series_store.enrich_registry_identities,
            source_series_rows,
            series_country_code,
            source_id=source_id,
        )
        discovery = await disease_mapping_registry_service.discover_rows(
            db,
            country_code=series_country_code,
            source_id=source_id,
            rows=source_series_rows,
            notify=True,
        )
        logger.info(
            "Disease Mapping Registry discovery | country={} categories={} new={}",
            series_country_code,
            discovery["source_category_count"],
            discovery["new_category_count"],
        )
    skipped_unregistered = 0
    skipped_missing = 0
    if bool(getattr(updater, "series_registered_rows_only", False)):
        selection = await asyncio.to_thread(
            series_store.select_registry_rows,
            source_series_rows,
            series_country_code,
            source_id=source_id,
        )
        holding_rows = list(getattr(selection, "unregistered_rows", ()) or ())
        if source_series_rows and not selection.rows and not holding_rows:
            raise ValueError(
                "Registry-scoped dual write selected no non-missing source rows"
            )
        source_series_rows = [*selection.rows, *holding_rows]
        # Older/test selection adapters do not expose the retained rows. In
        # that compatibility case the count still means rows were omitted;
        # the production selector forwards them into dynamic holding series.
        skipped_unregistered = (
            0 if holding_rows else selection.skipped_unregistered
        )
        skipped_missing = selection.skipped_missing
        logger.info(
            "Disease source-series Registry selection | country={} "
            "selected={} retained_unmapped={} omitted_unregistered={} "
            "omitted_missing={}",
            series_country_code,
            len(selection.rows),
            len(holding_rows),
            skipped_unregistered,
            selection.skipped_missing,
        )

        if country_code == "US" and any(
            str(row.get("Source") or "").strip().casefold()
            == "us cdc nndss"
            for row in series_rows or rows
        ):
            nndss_areas = {
                str(row.get("ReportingArea") or "").strip().casefold()
                for row in source_series_rows
                if str(row.get("Source") or "").strip().casefold()
                == "us cdc nndss"
            }
            resident_aliases = {
                "us residents",
                "u.s. residents",
                "united states residents",
            }
            missing_scopes = []
            if not nndss_areas.intersection(resident_aliases):
                missing_scopes.append("US_RESIDENTS")
            if "total" not in nndss_areas:
                missing_scopes.append("TOTAL")
            if missing_scopes:
                raise ValueError(
                    "NNDSS Registry dual write is missing required reporting "
                    "scope(s): " + ", ".join(missing_scopes)
                )
    try:
        series_result = await series_store.save_rows(
            db,
            source_series_rows,
            series_country_code,
            source_id=source_id,
            geography_key=geography_key,
            quality_policy=quality_policy,
            **({"jp_holding_profiles": _jp_holding_profiles} if _jp_holding_profiles is not None else {}),
        )
    except SeriesObservationQualityError as exc:
        logger.error(
            "Disease source-series batch rejected before transaction commit | "
            "country={} mode={} report={}",
            series_country_code,
            quality_policy.mode,
            json.dumps(exc.report.to_dict(), ensure_ascii=False, sort_keys=True),
        )
        raise
    if isinstance(series_result, SeriesObservationSaveResult):
        series_result = replace(
            series_result,
            skipped_unregistered=skipped_unregistered,
            skipped_missing=skipped_missing,
            publication_changes=(
                series_result.publication_changes
                if not (skipped_unregistered or skipped_missing) else None
            ),
        )
    else:
        # Test doubles and compatibility stores may return a namespace.
        # Preserve that contract while surfacing Registry pre-filter counts.
        setattr(series_result, "skipped_unregistered", skipped_unregistered)
        setattr(series_result, "skipped_missing", skipped_missing)
    quality_report = series_result.quality_report
    logger.info(
        "Disease source-series dual write complete | country={} upserted={} "
        "unregistered={} missing={} unmatched={} ambiguous={} invalid={} "
        "registry_not_synced={} holding={} "
        "quality_mode={} quality_issues={} quality_highest={}",
        series_country_code,
        series_result.upserted,
        series_result.skipped_unregistered,
        series_result.skipped_missing,
        series_result.skipped_unmatched,
        series_result.skipped_ambiguous,
        series_result.skipped_invalid,
        series_result.skipped_registry_not_synced,
        getattr(series_result, "holding_observations", 0),
        quality_policy.mode,
        len(quality_report.issues),
        quality_report.highest_severity or "none",
    )
    if quality_report.issues:
        logger.warning(
            "Disease source-series quality anomalies | country={} report={}",
            series_country_code,
            json.dumps(
                quality_report.to_dict(), ensure_ascii=False, sort_keys=True
            ),
        )
    return series_result
