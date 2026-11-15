"""Country projection and published baseline assembly without session ownership."""
from collections import defaultdict
from pathlib import Path


def _group_records_by_disease(
    records_by_country: dict[str, list],
) -> dict[str, dict[str, list]]:
    grouped_by_country: dict[str, dict[str, list]] = {}
    for country_code, records in records_by_country.items():
        grouped: dict[str, list] = defaultdict(list)
        for record in records:
            disease_id = record.get("disease_id")
            if disease_id:
                grouped[str(disease_id)].append(record)
        grouped_by_country[country_code] = dict(grouped)
    return grouped_by_country


def _records_for_disease(
    grouped_by_country: dict[str, dict[str, list]],
    disease_id: str,
) -> dict[str, list]:
    return {
        country_code: records
        for country_code, records_by_disease in grouped_by_country.items()
        if (records := records_by_disease.get(disease_id))
    }


def _release_projected_country_rows(result: dict) -> dict:
    """Data-only projections retain lossless source series, not duplicate rows."""
    result["input_record_count"] = len(result["records"]) + len(result["source_records"])
    result["source_record_count"] = len(result["source_records"])
    result["record_count"] = sum(len(series.get("dates") or []) for series in (result["country_data"].get("disease_series") or {}).values())
    result["records"] = []
    result["source_records"] = []
    return result


def _retain_observed_iceland_sources(
    source_info: dict,
    country_data: dict,
    scope_markers,
) -> dict:
    """Do not advertise configured Iceland feeds until facts are exported."""
    searchable_values: list[str] = []
    for series in (country_data.get("disease_series") or {}).values():
        for source_series in series.get("source_series") or []:
            searchable_values.extend(
                str(source_series.get(field) or "").lower()
                for field in (
                    "series_code",
                    "source_series_code",
                    "source_system",
                    "source_label",
                )
            )
    observed_scopes = {
        scope
        for scope, markers in scope_markers.items()
        if any(
            marker in value
            for marker in markers
            for value in searchable_values
        )
    }
    if not observed_scopes:
        return source_info

    retained = [
        source
        for source in source_info.get("sources") or []
        if source.get("scope") in observed_scopes
    ]
    if not retained:
        return source_info
    result = {**source_info, "sources": retained}
    primary = retained[0]
    result.update(
        {
            "primary_scope": primary.get("scope"),
            "primary_label": primary.get("label"),
            "primary_url": primary.get("url"),
            "primary_type": primary.get("type"),
        }
    )
    return result


def _unchanged_published_country_result(
    country: dict,
    *,
    summary: dict | None,
    publish_hash: str | None,
    country_name_by_code: dict[str, str],
    country_name_zh_by_code: dict[str, str],
    empty_layer_summary,
) -> dict:
    code = country["code"]
    summary = summary or {}
    country_name = (
        summary.get("name")
        or country_name_by_code.get(code)
        or country.get("name")
        or code
    )
    source_info = summary.get("source_info") or {}
    country_data = {
        "country_code": code,
        "country_name": country_name,
        "country_name_zh": summary.get("name_zh") or country_name_zh_by_code.get(code),
        "disease_series": {},
        "data_layer_summary": summary.get("data_layer_summary") or empty_layer_summary(),
        "total_cases": summary.get("total_cases") or 0,
        "total_deaths": summary.get("total_deaths") or 0,
        "disease_count": summary.get("disease_count") or 0,
        "date_range": summary.get("date_range") or {"start": None, "end": None},
        "source_info": source_info,
        "publish_content_hash": publish_hash,
        "unchanged_curve": True,
    }
    return {
        "code": code,
        "country_name": country_name,
        "country_name_zh": country_data["country_name_zh"],
        "records": [],
        "source_records": [],
        "country_data": country_data,
        "source_info": source_info,
        "unchanged_curve": True,
        "record_count": summary.get("record_count") or 0,
    }


def _published_country_result(
    country: dict,
    rows: list[dict],
    *,
    publish_hash: str,
    country_name_by_code: dict[str, str],
    country_name_zh_by_code: dict[str, str],
    diseases_by_id: dict,
    country_briefs: dict,
    frequency_meta_from_granularities,
    build_country_source_info,
    build_country_data_from_published_series,
    retain_observed_iceland_sources,
    apply_country_brief_fields,
) -> dict:
    code = country["code"]
    country_name = country_name_by_code.get(code) or country["name"]
    frequency_meta = frequency_meta_from_granularities(
        [row.get("granularity") for row in rows]
    )
    source_info = build_country_source_info(
        code,
        frequency_meta,
        database_config=country,
    )
    country_data = build_country_data_from_published_series(
        code,
        country_name,
        rows,
        diseases_by_id,
        frequency_meta,
    )
    metadata = country.get("metadata") if isinstance(country.get("metadata"), dict) else {}
    country_data["country_name_zh"] = country_name_zh_by_code.get(code)
    country_data["location_type"] = metadata.get("location_type") or (
        "subdivision" if "-" in code else "country"
    )
    country_data["parent_code"] = metadata.get("parent_country_code")
    country_data["publish_content_hash"] = publish_hash
    from src.generation.public_series_store import PROJECTION_VERSION

    country_data["publish_projection_version"] = PROJECTION_VERSION
    if code.upper() == "IS":
        source_info = retain_observed_iceland_sources(source_info, country_data)
    country_data["source_info"] = source_info
    country_data = apply_country_brief_fields(
        country_data, country_briefs.get(code.upper())
    )
    return {
        "code": code,
        "country_name": country_name,
        "country_name_zh": country_name_zh_by_code.get(code),
        "records": [],
        "source_records": [],
        "country_data": country_data,
        "source_info": source_info,
        "unchanged_curve": False,
        "record_count": sum(len(row.get("period_starts") or []) for row in rows),
    }


async def _build_published_country_results(
    session,
    *,
    countries: list[dict],
    countries_to_refresh: list[dict],
    incremental: bool,
    publish_states: dict[str, dict],
    output_dir: Path,
    country_name_by_code: dict[str, str],
    country_name_zh_by_code: dict[str, str],
    diseases_by_id: dict,
    catalogue_ids: set[str],
    country_briefs: dict,
    population_enabled: bool,
    registry_tables_exist: bool | None,
    recorded_at_as_of,
    local_publish_hashes,
    fetch_published_rows_by_country,
    published_country_result,
    collect_country_export,
) -> tuple[list[dict], set[str] | None]:
    """Load changed curves from the publish tables.

    A full export with an existing snapshot returns the changed country codes
    so writers can leave matching curves on disk. Historical and explicit
    incremental runs return None and keep the caller's own skip rules.
    """

    if incremental:
        refresh_countries = list(countries_to_refresh)
        curve_refresh_codes = None
    else:
        local_hashes = local_publish_hashes(
            output_dir,
            [country["code"] for country in countries],
        )
        if local_hashes is None:
            refresh_countries = list(countries)
            curve_refresh_codes = None
        else:
            refresh_countries = []
            for country in countries:
                code = country["code"]
                snapshot_path = output_dir / "countries" / f"{code.lower()}.json"
                db_hash = (publish_states.get(code) or {}).get("content_hash")
                if db_hash != local_hashes.get(code) or not snapshot_path.is_file():
                    refresh_countries.append(country)
            curve_refresh_codes = {country["code"] for country in refresh_countries}
            print(
                "  Published curves: "
                f"{len(refresh_countries)} changed, "
                f"{len(countries) - len(refresh_countries)} unchanged",
                flush=True,
            )

    publish_codes = [
        country["code"]
        for country in refresh_countries
        if country["code"] in publish_states
    ]
    if publish_codes:
        print(
            f"  Reading published curves for {len(publish_codes)} countries…",
            flush=True,
        )
    rows_by_country = await fetch_published_rows_by_country(session, publish_codes)
    results = []
    for country in refresh_countries:
        code = country["code"]
        if code in publish_states:
            result = published_country_result(
                country,
                rows_by_country.get(code) or [],
                publish_hash=publish_states[code]["content_hash"],
                country_name_by_code=country_name_by_code,
                country_name_zh_by_code=country_name_zh_by_code,
                diseases_by_id=diseases_by_id,
                country_briefs=country_briefs,
            )
        else:
            result = await collect_country_export(
                country,
                country_name_by_code=country_name_by_code,
                country_name_zh_by_code=country_name_zh_by_code,
                diseases_by_id=diseases_by_id,
                catalogue_ids=catalogue_ids,
                country_briefs=country_briefs,
                population_enabled=population_enabled,
                registry_tables_exist=registry_tables_exist,
                recorded_at_as_of=recorded_at_as_of,
                session=session,
            )
        results.append(result)
        print(
            f"SITE_EXPORT_COUNTRIES {len(results)}/{len(refresh_countries)} "
            f"country={code}",
            flush=True,
        )
    return results, curve_refresh_codes
