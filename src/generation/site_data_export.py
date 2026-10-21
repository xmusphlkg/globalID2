"""Database-to-filesystem orchestration for the static site data export."""

import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

from src.core.config import get_config
from src.core.country_library import expand_country_scopes
from src.core.data_share import (
    derive_github_raw_base_url,
    get_data_share_repo_branch,
    get_data_share_repo_url,
)
from src.core.database import get_db
from src.generation.direct_download_files import (
    DEFAULT_TARGET_FILE_BYTES,
    build_direct_download_files,
)
from src.generation.site_data_about import (
    build_about_snapshot,
    build_country_source_info,
    resolve_snapshot_version,
)
from src.generation.site_data_catalogue import (
    enrich_diseases_with_ontology,
    load_standard_diseases,
    validate_record_catalogue_coverage,
)
from src.generation.site_data_database import (
    ensure_site_export_database_ready as _ensure_site_export_database_ready,
)
from src.generation.site_data_knowledge import (
    apply_country_brief_fields,
    build_disease_knowledge_fields,
)
from src.generation.site_data_literature import (
    attach_surveillance_evidence,
    collect_literature_export,
    write_literature_artifacts,
)
from src.generation.site_data_queries import (
    fetch_countries,
    fetch_country_briefs,
    fetch_country_frequency_meta,
    fetch_disease_export_layers,
    fetch_disease_knowledge_briefs,
    fetch_report_detail,
    fetch_reports,
    has_population_table,
    has_table,
)
from src.generation.site_data_views import (
    _country_series_data_layer_summary,
    _data_layer_summary,
    build_country_data,
    build_country_data_from_published_series,
    build_country_site_data,
    build_country_source_series_data,
    build_disease_data,
    build_disease_data_from_country_series,
    build_disease_site_data,
    build_national_monthly_summary,
    frequency_meta_from_granularities,
    resolve_country_display_names,
)
from src.generation.site_data_writer import (
    existing_site_export_has_content,
    prepare_site_output_dirs,
    remove_stale_json_files,
    write_compact_json,
    write_pretty_json,
)
from src.knowledge.catalogue import should_generate_public_disease_page
from src.ontology import load_disease_ontology
from src.services.situation_v3.persistence import latest_report_v3, reports_v3

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "astro-site" / "src" / "data"
DEFAULT_PUBLIC_SITE_DATA_OUTPUT = ROOT / "astro-site" / "public" / "site-data"
DEFAULT_DIRECT_DOWNLOAD_OUTPUT = ROOT / "exports" / "site-downloads"
DEFAULT_DOWNLOAD_MANIFEST = ROOT / "astro-site" / "src" / "data" / "downloads.json"
DEFAULT_DOWNLOAD_REPO_URL = get_data_share_repo_url()
PUBLISH_HASH_MANIFEST = "publish-hashes.json"
DEFAULT_DIRECT_DOWNLOAD_BRANCH = get_data_share_repo_branch()
DEFAULT_DIRECT_DOWNLOAD_URL_BASE = derive_github_raw_base_url(
    DEFAULT_DOWNLOAD_REPO_URL,
    DEFAULT_DIRECT_DOWNLOAD_BRANCH,
)

_ICELAND_SCOPE_MARKERS = {
    "is_doh_annual": ("annual:", "ser_is_doh_annual"),
    "is_doh_sti": ("sti:", "ser_is_doh_sti"),
    "is_doh_respiratory": ("respiratory:", "ser_is_doh_respiratory"),
    "is_doh_history": ("ser_is_history", "is_history_"),
    "is_doh_legacy_icd": ("ser_is_legacy_icd", "is_legacy_icd_"),
}


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


def _retain_observed_iceland_sources(
    source_info: dict,
    country_data: dict,
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
        for scope, markers in _ICELAND_SCOPE_MARKERS.items()
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


def _read_publish_hash_manifest(output_dir: Path) -> dict[str, str] | None:
    payload = _read_json_file(output_dir / PUBLISH_HASH_MANIFEST, None)
    if not isinstance(payload, dict) or not isinstance(payload.get("countries"), dict):
        return None
    from src.generation.public_series_store import PROJECTION_VERSION

    if payload.get("projection_version") != PROJECTION_VERSION:
        return None
    return {
        str(code): str(value)
        for code, value in payload["countries"].items()
        if value
    }


def _country_hash_from_snapshot(output_dir: Path, country_code: str) -> str | None:
    country_data = _read_json_file(
        output_dir / "countries" / f"{country_code.lower()}.json",
        None,
    )
    if not isinstance(country_data, dict):
        return None
    stamped = country_data.get("publish_content_hash")
    if isinstance(stamped, str) and len(stamped) == 64:
        return stamped
    from src.generation.public_series_store import country_hash_from_disease_series

    try:
        return country_hash_from_disease_series(
            country_code,
            country_data.get("disease_series") or {},
        )
    except ValueError:
        return None


def _local_publish_hashes(output_dir: Path, country_codes: list[str]) -> dict[str, str] | None:
    """Return snapshot curve hashes, or None when there is no snapshot to compare."""

    if not (output_dir / "meta.json").is_file():
        return None
    hashes = dict(_read_publish_hash_manifest(output_dir) or {})
    missing = [code for code in country_codes if code not in hashes]
    if not missing:
        return hashes
    print(
        f"  Comparing published curve hashes with {len(missing)} country snapshots…",
        flush=True,
    )
    for index, code in enumerate(missing, start=1):
        hashed = _country_hash_from_snapshot(output_dir, code)
        if hashed is not None:
            hashes[code] = hashed
        if index % 25 == 0 or index == len(missing):
            print(
                f"  Snapshot curve hashes {index}/{len(missing)}",
                flush=True,
            )
    return hashes


def _write_publish_hash_manifest(output_dir: Path, context: dict) -> None:
    hashes = context.get("publish_hashes")
    if not isinstance(hashes, dict):
        return
    from src.generation.public_series_store import PROJECTION_VERSION

    write_pretty_json(
        output_dir / PUBLISH_HASH_MANIFEST,
        {
            "projection_version": PROJECTION_VERSION,
            "countries": dict(sorted((str(code), str(value)) for code, value in hashes.items())),
        },
    )


def _empty_layer_summary() -> dict:
    return _data_layer_summary({})


def _unchanged_published_country_result(
    country: dict,
    *,
    summary: dict | None,
    publish_hash: str | None,
    country_name_by_code: dict[str, str],
    country_name_zh_by_code: dict[str, str],
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
        "data_layer_summary": summary.get("data_layer_summary") or _empty_layer_summary(),
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


async def _fetch_publish_states(session) -> dict[str, dict] | None:
    if not await has_table(session, "country_publish_state"):
        return None
    if not await has_table(session, "public_disease_series"):
        return None
    from sqlalchemy import select

    from src.domain.public_series import CountryPublishState
    from src.generation.public_series_store import PROJECTION_VERSION

    rows = (
        await session.execute(
            select(
                CountryPublishState.country_code,
                CountryPublishState.content_hash,
                CountryPublishState.point_count,
                CountryPublishState.series_count,
            ).where(CountryPublishState.projection_version == PROJECTION_VERSION)
        )
    ).all()
    if not rows:
        return None
    return {
        country_code: {
            "content_hash": content_hash,
            "point_count": int(point_count or 0),
            "series_count": int(series_count or 0),
        }
        for country_code, content_hash, point_count, series_count in rows
    }


async def _fetch_published_rows_by_country(session, country_codes: list[str]) -> dict[str, list[dict]]:
    if not country_codes:
        return {}
    from sqlalchemy import select

    from src.domain.public_series import PublicDiseaseSeries

    rows = (
        await session.execute(
            select(PublicDiseaseSeries).where(
                PublicDiseaseSeries.country_code.in_(tuple(country_codes))
            )
        )
    ).scalars().all()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row.country_code].append(
            {
                "country_code": row.country_code,
                "disease_id": row.disease_id,
                "period_starts": list(row.period_starts or []),
                "cases": list(row.cases or []),
                "deaths": list(row.deaths or []),
                "incidence": list(row.incidence or []),
                "granularity": row.granularity,
                "data_layer": row.data_layer,
                "source_series_code": row.source_series_code,
                "point_count": row.point_count,
                "total_cases": row.total_cases,
                "total_deaths": row.total_deaths,
                "content_hash": row.content_hash,
            }
        )
    return grouped


async def _published_country_codes_by_disease(session) -> dict[str, set[str]]:
    from sqlalchemy import select

    from src.domain.public_series import PublicDiseaseSeries

    rows = (
        await session.execute(
            select(PublicDiseaseSeries.disease_id, PublicDiseaseSeries.country_code)
        )
    ).all()
    grouped: dict[str, set[str]] = defaultdict(set)
    for disease_id, country_code in rows:
        grouped[str(disease_id)].add(str(country_code))
    return grouped


def _published_country_result(
    country: dict,
    rows: list[dict],
    *,
    publish_hash: str,
    country_name_by_code: dict[str, str],
    country_name_zh_by_code: dict[str, str],
    diseases_by_id: dict,
    country_briefs: dict,
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
        source_info = _retain_observed_iceland_sources(source_info, country_data)
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
        local_hashes = _local_publish_hashes(
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
    rows_by_country = await _fetch_published_rows_by_country(session, publish_codes)
    results = []
    for country in refresh_countries:
        code = country["code"]
        if code in publish_states:
            result = _published_country_result(
                country,
                rows_by_country.get(code) or [],
                publish_hash=publish_states[code]["content_hash"],
                country_name_by_code=country_name_by_code,
                country_name_zh_by_code=country_name_zh_by_code,
                diseases_by_id=diseases_by_id,
                country_briefs=country_briefs,
            )
        else:
            result = await _collect_country_export(
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


async def _collect_country_export(
    country: dict,
    *,
    country_name_by_code: dict[str, str],
    country_name_zh_by_code: dict[str, str],
    diseases_by_id: dict[str, dict],
    catalogue_ids: set[str],
    country_briefs: dict[str, dict[str, dict]],
    population_enabled: bool,
    registry_tables_exist: bool | None = None,
    recorded_at_as_of=None,
    session=None,
) -> dict:
    """Fetch and project one country using an isolated database session."""

    code = country["code"]
    country_name_en = country_name_by_code.get(code) or country["name"]
    if session is None:
        async with get_db() as country_session:
            return await _collect_country_export(
                country,
                country_name_by_code=country_name_by_code,
                country_name_zh_by_code=country_name_zh_by_code,
                diseases_by_id=diseases_by_id,
                catalogue_ids=catalogue_ids,
                country_briefs=country_briefs,
                population_enabled=population_enabled,
                registry_tables_exist=registry_tables_exist,
                recorded_at_as_of=recorded_at_as_of,
                session=country_session,
            )

    frequency_meta = await fetch_country_frequency_meta(
        session, code, registry_tables_exist=registry_tables_exist
    )
    country_source_info = build_country_source_info(
        code,
        frequency_meta,
        database_config=country,
    )
    records, source_records = await fetch_disease_export_layers(
        session,
        code,
        population_enabled,
        recorded_at_as_of=recorded_at_as_of,
        registry_tables_exist=registry_tables_exist,
    )

    validate_record_catalogue_coverage(
        [*records, *source_records],
        catalogue_ids,
        set(diseases_by_id),
    )
    country_data = build_country_data(
        code,
        country_name_en,
        records,
        diseases_by_id,
        frequency_meta,
        source_records,
    )
    if recorded_at_as_of is not None:
        country_data["recorded_at_as_of"] = recorded_at_as_of.isoformat()
        country_data["recorded_history_complete"] = bool(source_records) and all(
            record.get("recorded_history_complete") is True
            for record in source_records
        )
        for entry in (country_data.get("disease_series") or {}).values():
            entry["recorded_at_as_of"] = country_data["recorded_at_as_of"]
            entry["recorded_history_complete"] = country_data[
                "recorded_history_complete"
            ]
    country_metadata = (
        country.get("metadata") if isinstance(country.get("metadata"), dict) else {}
    )
    country_data["country_name_zh"] = country_name_zh_by_code.get(code)
    country_data["location_type"] = country_metadata.get(
        "location_type"
    ) or ("subdivision" if "-" in code else "country")
    country_data["parent_code"] = country_metadata.get("parent_country_code")
    if code.upper() == "IS":
        country_source_info = _retain_observed_iceland_sources(
            country_source_info,
            country_data,
        )
    country_data["source_info"] = country_source_info
    country_data = apply_country_brief_fields(
        country_data, country_briefs.get(code.upper())
    )
    return {
        "code": code,
        "country_name": country_name_en,
        "country_name_zh": country_name_zh_by_code.get(code),
        "records": records,
        "source_records": source_records,
        "country_data": country_data,
        "source_info": country_source_info,
    }


def _read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _merge_incremental_disease_snapshot(
    existing: dict,
    *,
    disease_id: str,
    updated_country_data: dict[str, dict],
    jurisdiction_codes: list[str] | None = None,
) -> dict:
    """Replace changed country slices while retaining the existing snapshot."""

    merged = dict(existing)
    country_series = {
        str(code): dict(series)
        for code, series in (existing.get("country_series") or {}).items()
        if isinstance(series, dict)
    }
    for country_code, country_data in updated_country_data.items():
        series = (country_data.get("disease_series") or {}).get(disease_id)
        if series:
            country_series[country_code] = series
        else:
            country_series.pop(country_code, None)

    national_series = {
        code: series for code, series in country_series.items() if "-" not in code
    }
    global_monthly = build_national_monthly_summary(
        national_series, jurisdiction_codes
    )
    merged.update(
        {
            "country_series": country_series,
            "global_monthly": global_monthly,
            "total_cases": sum(
                sum(value for value in (series.get("cases") or []) if value is not None)
                for series in national_series.values()
            ),
            "total_deaths": sum(
                sum(value for value in (series.get("deaths") or []) if value is not None)
                for series in national_series.values()
            ),
            "aggregation_scope": "national_jurisdictions_only",
            "subdivision_country_codes": sorted(
                code for code in country_series if "-" in code
            ),
            "data_layer_summary": _country_series_data_layer_summary(country_series),
        }
    )
    return merged


async def ensure_site_export_database_ready() -> None:
    """Create missing tables, seed countries, and restore WPP denominators."""
    country_count = await _ensure_site_export_database_ready()
    print(f"  ✓ database schema ready ({country_count} countries)")
    # Country rebuilds can recreate country IDs after population was imported,
    # leaving a valid table with no denominator rows for some countries.  Site
    # generation is already a schema-preparation boundary, so repair the
    # idempotent WPP reference data here before calculating crude incidence.
    from scripts.import_wpp_population import ensure_wpp_population

    population_result = await ensure_wpp_population()
    print(
        "  ✓ WPP population ready "
        f"({population_result['mapped_countries']} countries, "
        f"{population_result['mapped_rows']} country-years, "
        f"{population_result['year_min']}-{population_result['year_max']})"
    )


async def collect_site_export_context(
    output_dir: Path,
    allow_empty_export: bool = False,
    incremental_country_codes: list[str] | tuple[str, ...] | None = None,
    *,
    public_site_data_dir: Path = DEFAULT_PUBLIC_SITE_DATA_OUTPUT,
    recorded_at_as_of=None,
    incremental_site: bool = False,
) -> dict:
    """Read and project all database-backed data without writing artifacts."""
    await ensure_site_export_database_ready()
    incremental_codes = set(expand_country_scopes(incremental_country_codes or ()))
    incremental = bool(incremental_codes) or incremental_site
    if incremental and recorded_at_as_of is not None:
        raise ValueError(
            "Historical site exports must be complete builds, not incremental merges."
        )
    if incremental and not (output_dir / "meta.json").is_file():
        raise RuntimeError(
            "Incremental site export requires an existing snapshot; run one full export first."
        )
    generated_at = ""

    # Load the stable catalogue and the independently versioned ontology.
    csv_path = ROOT / "configs" / "standard_diseases.csv"
    catalogue_diseases = load_standard_diseases(csv_path)
    catalogue_ids = {disease["disease_id"] for disease in catalogue_diseases}
    ontology = load_disease_ontology()
    ontology_document = ontology.to_dict()
    diseases = [
        disease
        for disease in catalogue_diseases
        if should_generate_public_disease_page(disease)
    ]
    enrich_diseases_with_ontology(diseases, ontology)
    diseases_by_id = {d["disease_id"]: d for d in diseases}
    disease_knowledge_briefs: dict[str, dict[str, dict]] = {}
    country_briefs: dict[str, dict[str, dict]] = {}
    countries_simple: list[dict] = []
    country_exports: list[dict] = []
    disease_exports: list[dict] = []
    reports: list[dict] = []
    report_details: dict[int, dict] = {}
    literature_export: dict = {}

    async with get_db() as session:
        population_enabled = await has_population_table(session)
        if population_enabled:
            print(
                "  Population table detected: incidence will use jurisdiction-specific annual population"
            )
        else:
            print(
                "  Population table not found: incidence falls back to database values"
            )

        disease_knowledge_briefs = await fetch_disease_knowledge_briefs(session)
        country_briefs = await fetch_country_briefs(session)
        if disease_knowledge_briefs:
            print(
                f"  Knowledge briefs detected: {len(disease_knowledge_briefs)} diseases"
            )
        else:
            print("  Knowledge briefs not found: disease profiles will remain blocked")
        if country_briefs:
            print(f"  Country briefs detected: {len(country_briefs)} countries")
        else:
            print(
                "  Country briefs not found: using generated country context fallback"
            )

        # ── Countries ──
        countries = await fetch_countries(session)
        registry_tables_exist = await has_table(
            session, "disease_surveillance_series"
        ) and await has_table(session, "disease_series_observations")
        countries_simple = []
        for country in countries:
            name_en, name_zh = resolve_country_display_names(country["code"], country)
            countries_simple.append(
                {
                    "code": country["code"],
                    "name": name_en,
                    "name_en": name_en,
                    "name_zh": name_zh,
                    "language": country["language"],
                    "location_type": (
                        (country.get("metadata") or {}).get("location_type")
                        if isinstance(country.get("metadata"), dict)
                        else None
                    ) or ("subdivision" if "-" in country["code"] else "country"),
                    "parent_code": (
                        (country.get("metadata") or {}).get("parent_country_code")
                        if isinstance(country.get("metadata"), dict)
                        else None
                    ),
                }
            )

        all_records_by_country: dict[str, list] = {}
        all_source_records_by_country: dict[str, list] = {}
        country_sources_by_code: dict[str, dict] = {}
        country_name_by_code = {c["code"]: c["name"] for c in countries_simple}
        country_name_zh_by_code = {c["code"]: c["name_zh"] for c in countries_simple}
        country_download_entries: list[dict] = []
        disease_download_entries: list[dict] = []
        incremental_codes = set(expand_country_scopes(
            incremental_codes, available_codes=(country["code"] for country in countries)
        ))
        countries_to_refresh = (
            [country for country in countries if country["code"] in incremental_codes]
            if incremental
            else countries
        )
        missing_incremental_codes = incremental_codes - {
            country["code"] for country in countries_to_refresh
        }
        if missing_incremental_codes:
            raise RuntimeError(
                "Incremental site export requested unknown country scope(s): "
                + ", ".join(sorted(missing_incremental_codes))
            )

        publish_states = None
        publish_coverage = None
        curve_refresh_codes: set[str] | None = None
        if recorded_at_as_of is None:
            publish_states = await _fetch_publish_states(session)
        use_publish_read = publish_states is not None
        if use_publish_read:
            assert publish_states is not None
            publish_coverage = await _published_country_codes_by_disease(session)
            country_results, curve_refresh_codes = await _build_published_country_results(
                session,
                countries=countries,
                countries_to_refresh=countries_to_refresh,
                incremental=incremental,
                publish_states=publish_states,
                output_dir=output_dir,
                country_name_by_code=country_name_by_code,
                country_name_zh_by_code=country_name_zh_by_code,
                diseases_by_id=diseases_by_id,
                catalogue_ids=catalogue_ids,
                country_briefs=country_briefs,
                population_enabled=population_enabled,
                registry_tables_exist=registry_tables_exist,
                recorded_at_as_of=recorded_at_as_of,
            )
        else:
            # Country reads are independent.  Use bounded concurrency so a release
            # does not serialize hundreds of database round trips while still
            # leaving connections available for the worker itself.
            configured_pool_size = max(1, int(get_config().database.pool_size or 1))
            country_worker_count = max(
                1, min(8, configured_pool_size - 1, len(countries_to_refresh))
            )
            if countries_to_refresh:
                print(
                    f"  Fetching records for {len(countries_to_refresh)} "
                    f"{'changed ' if incremental else ''}countries "
                    f"with {country_worker_count} concurrent workers…"
                )
            semaphore = asyncio.Semaphore(country_worker_count)

            async def collect_with_limit(country: dict) -> dict:
                async with semaphore:
                    return await _collect_country_export(
                        country,
                        country_name_by_code=country_name_by_code,
                        country_name_zh_by_code=country_name_zh_by_code,
                        diseases_by_id=diseases_by_id,
                        catalogue_ids=catalogue_ids,
                        country_briefs=country_briefs,
                        population_enabled=population_enabled,
                        registry_tables_exist=registry_tables_exist,
                        recorded_at_as_of=recorded_at_as_of,
                    )

            if configured_pool_size <= 1:
                # The outer metadata session owns the only connection in this
                # configuration, so reuse it instead of waiting for another one.
                country_results = []
                for country in countries_to_refresh:
                    result = await _collect_country_export(
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
                    country_results.append(result)
                    print(
                        f"SITE_EXPORT_COUNTRIES {len(country_results)}/{len(countries_to_refresh)} "
                        f"country={result['code']}",
                        flush=True,
                    )
            else:
                country_tasks = [
                    asyncio.create_task(collect_with_limit(country))
                    for country in countries_to_refresh
                ]
                country_results = []
                try:
                    for completed in asyncio.as_completed(country_tasks):
                        result = await completed
                        country_results.append(result)
                        print(
                            f"SITE_EXPORT_COUNTRIES {len(country_results)}/{len(country_tasks)} "
                            f"country={result['code']}",
                            flush=True,
                        )
                except BaseException:
                    for country_task in country_tasks:
                        country_task.cancel()
                    await asyncio.gather(*country_tasks, return_exceptions=True)
                    raise
        fresh_results_by_code = {result["code"]: result for result in country_results}
        existing_country_summaries: dict[str, dict] = {}
        if curve_refresh_codes is not None:
            existing_meta = _read_json_file(output_dir / "meta.json", {})
            if isinstance(existing_meta, dict):
                existing_country_summaries = {
                    str(item.get("code")): item
                    for item in (existing_meta.get("countries") or [])
                    if isinstance(item, dict) and item.get("code")
                }
        for country in countries:
            code = country["code"]
            result = fresh_results_by_code.get(code)
            if result is None and curve_refresh_codes is not None:
                result = _unchanged_published_country_result(
                    country,
                    summary=existing_country_summaries.get(code),
                    publish_hash=(publish_states or {}).get(code, {}).get("content_hash"),
                    country_name_by_code=country_name_by_code,
                    country_name_zh_by_code=country_name_zh_by_code,
                )
            elif result is None:
                country_data = _read_json_file(
                    output_dir / "countries" / f"{code.lower()}.json",
                    None,
                )
                if not isinstance(country_data, dict):
                    raise RuntimeError(
                        "Incremental site export is missing the existing country "
                        f"snapshot for {code}."
                    )
                country_source_info = country_data.get("source_info") or {}
                result = {
                    "code": code,
                    "country_name": country_data.get("country_name")
                    or country_name_by_code.get(code)
                    or country["name"],
                    "country_name_zh": country_data.get("country_name_zh")
                    or country_name_zh_by_code.get(code),
                    "records": [],
                    "source_records": [],
                    "country_data": country_data,
                    "source_info": country_source_info,
                }
            code = result["code"]
            records = result["records"]
            source_records = result["source_records"]
            country_data = result["country_data"]
            country_source_info = result["source_info"]
            all_records_by_country[code] = records
            all_source_records_by_country[code] = source_records
            country_sources_by_code[code] = country_source_info
            layer_summary = country_data["data_layer_summary"]
            shown_records = result.get("record_count", len(records))
            print(
                f"    {code}: records={shown_records}, source_records={len(source_records)}, "
                f"registry={layer_summary.get('series_registry_disease_count', 0)}, "
                f"mixed_gap_fill={layer_summary.get('mixed_disease_count', 0)}, "
                f"legacy_fallback={layer_summary.get('legacy_fallback_disease_count', 0)}, "
                f"risk_labelled={layer_summary.get('loss_risk_disease_count', 0)}"
            )
            if layer_summary.get("non_additive_series_disease_ids"):
                print(
                    "      Non-additive series kept separate: "
                    + ", ".join(layer_summary["non_additive_series_disease_ids"])
                )

            # Augment countries_simple with the computed data stats.
            record_count = int(result.get("record_count") or 0)
            for country_summary in countries_simple:
                if country_summary["code"] != code:
                    continue
                if result.get("unchanged_curve"):
                    record_count = int(result.get("record_count") or 0)
                else:
                    record_count = sum(
                        len(series.get("dates") or [])
                        for series in (country_data.get("disease_series") or {}).values()
                    )
                country_summary["total_cases"] = country_data["total_cases"]
                country_summary["total_deaths"] = country_data["total_deaths"]
                country_summary["disease_count"] = country_data["disease_count"]
                country_summary["date_range"] = country_data["date_range"]
                country_summary["record_count"] = record_count
                country_summary["data_available"] = bool(
                    record_count
                    and country_data["disease_count"]
                    and (
                        country_data["date_range"].get("start")
                        or country_data["date_range"].get("end")
                    )
                )
                country_summary["source_info"] = country_source_info
                country_summary["data_layer_summary"] = country_data[
                    "data_layer_summary"
                ]
                break

            country_exports.append(
                {
                    "code": code,
                    "country_name": result["country_name"],
                    "country_name_zh": result["country_name_zh"],
                    "country_data": country_data,
                    "source_info": country_source_info,
                    "unchanged_curve": bool(result.get("unchanged_curve")),
                    "record_count": record_count,
                }
            )

        reports = (
            await fetch_reports(session)
            if not incremental
            else _read_json_file(output_dir / "reports" / "index.json", [])
        )
        if not isinstance(reports, list):
            reports = []
        if use_publish_read and publish_states is not None:
            total_record_count = sum(
                int(state["point_count"]) for state in publish_states.values()
            ) + sum(len(records) for records in all_records_by_country.values())
        else:
            total_record_count = sum(
                len(records) for records in all_records_by_country.values()
            ) + sum(
                len(records) for records in all_source_records_by_country.values()
            )
        if total_record_count == 0 and not allow_empty_export and not incremental:
            message = (
                "Refusing to overwrite site data with an empty export because no disease "
                f"records were found in the database across {len(countries)} countries."
            )
            if existing_site_export_has_content(output_dir):
                message += f" Existing files in {output_dir} were left untouched."
            message += " Import data first, or pass --allow-empty-export if this is intentional."
            raise RuntimeError(message)

        generated_at = resolve_snapshot_version(countries_simple, reports)
        records_by_disease_by_country = _group_records_by_disease(
            all_records_by_country
        )
        source_records_by_disease_by_country = _group_records_by_disease(
            all_source_records_by_country
        )
        affected_disease_ids: set[str] = set()
        if curve_refresh_codes:
            for code in curve_refresh_codes:
                fresh = fresh_results_by_code.get(code) or {}
                affected_disease_ids.update(
                    (fresh.get("country_data") or {}).get("disease_series") or {}
                )
                previous = _read_json_file(
                    output_dir / "countries" / f"{code.lower()}.json",
                    None,
                )
                if isinstance(previous, dict):
                    affected_disease_ids.update(previous.get("disease_series") or {})
        changed_disease_ids: set[str] = set(affected_disease_ids)
        refreshed_country_data = {
            code: result["country_data"]
            for code, result in fresh_results_by_code.items()
            if not result.get("unchanged_curve")
        }
        jurisdiction_codes = [str(country.get("code") or "") for country in countries]

        for country_export in country_exports:
            code = country_export["code"]
            country_name = country_export["country_name"]
            country_name_zh = country_export.get(
                "country_name_zh"
            ) or country_name_zh_by_code.get(code)
            country_data = country_export["country_data"]
            country_source_info = country_export["source_info"]
            if country_export.get("unchanged_curve"):
                country_site_data = {}
                country_record_count = int(country_export.get("record_count") or 0)
            else:
                country_data["generated_at"] = generated_at
                country_site_data = build_country_site_data(country_data)
                country_record_count = sum(
                    len(series.get("dates") or [])
                    for series in (country_data.get("disease_series") or {}).values()
                )
            country_export["site_data"] = country_site_data
            country_download_entries.append(
                {
                    "kind": "country",
                    "id": code.lower(),
                    "code": code,
                    "name": country_name,
                    "name_en": country_name,
                    "name_zh": country_name_zh,
                    "generated_at": generated_at,
                    "record_count": country_record_count,
                    "date_range": country_data.get("date_range"),
                    "site_json_path": f"/site-data/countries/{code.lower()}.json",
                }
            )

        # ── Per-disease files ──
        for disease in diseases:
            did = disease["disease_id"]
            if curve_refresh_codes is not None and did not in affected_disease_ids:
                disease_exports.append(
                    {
                        "disease_id": did,
                        "disease_data": {"disease_id": did, "country_series": {}},
                        "site_data": {},
                        "unchanged_curve": True,
                    }
                )
                disease_download_entries.append(
                    {
                        "kind": "disease",
                        "id": did.lower(),
                        "disease_id": did,
                        "slug": disease.get("slug"),
                        "name_en": disease.get("name_en"),
                        "name_zh": disease.get("name_zh"),
                        "generated_at": generated_at,
                        "record_count": 0,
                        "country_count": len(publish_coverage.get(did, ())) if publish_coverage else 0,
                        "site_json_path": f"/site-data/diseases/{did.lower()}.json",
                    }
                )
                continue
            if incremental or curve_refresh_codes is not None:
                existing_disease_data = _read_json_file(
                    output_dir / "diseases" / f"{did.lower()}.json",
                    None,
                )
                if not isinstance(existing_disease_data, dict):
                    raise RuntimeError(
                        "Incremental site export is missing the existing disease "
                        f"snapshot for {did}."
                    )
                affected = any(
                    code in (existing_disease_data.get("country_series") or {})
                    or did in (country_data.get("disease_series") or {})
                    for code, country_data in refreshed_country_data.items()
                )
                if affected:
                    disease_data = _merge_incremental_disease_snapshot(
                        existing_disease_data,
                        disease_id=did,
                        updated_country_data=refreshed_country_data,
                        jurisdiction_codes=jurisdiction_codes,
                    )
                    changed_disease_ids.add(did)
                else:
                    disease_data = existing_disease_data
            elif use_publish_read:
                country_series = {}
                for item in country_exports:
                    entry = (item["country_data"].get("disease_series") or {}).get(did)
                    if entry and (entry.get("dates") or entry.get("source_series")):
                        country_series[item["code"]] = entry
                disease_data = build_disease_data_from_country_series(
                    did,
                    disease,
                    country_series,
                    jurisdiction_codes=jurisdiction_codes,
                )
            else:
                disease_records_by_country = _records_for_disease(
                    records_by_disease_by_country,
                    did,
                )
                disease_source_records_by_country = _records_for_disease(
                    source_records_by_disease_by_country,
                    did,
                )
                disease_data = build_disease_data(
                    did,
                    disease,
                    disease_records_by_country,
                    disease_source_records_by_country,
                    jurisdiction_codes=jurisdiction_codes,
                )
                if recorded_at_as_of is not None:
                    disease_data["recorded_at_as_of"] = recorded_at_as_of.isoformat()
                    disease_data["recorded_history_complete"] = (
                        all(
                            item["country_data"].get(
                                "recorded_history_complete"
                            )
                            is True
                            for item in country_exports
                        )
                    )
            if incremental:
                monthly = build_national_monthly_summary(
                    disease_data.get("country_series") or {},
                    [str(country.get("code") or "") for country in countries],
                )
                if disease_data.get("global_monthly") != monthly:
                    disease_data["global_monthly"] = monthly
                    changed_disease_ids.add(did)
            disease_site_data = build_disease_site_data(
                disease_data,
                country_name_by_code,
                country_name_zh_by_code,
            )
            disease_countries = sorted(
                (disease_data.get("country_series") or {}).keys()
            )
            disease_source_info = []
            for country_code in disease_countries:
                country_source = dict(country_sources_by_code.get(country_code, {}))
                country_source["country_name"] = country_name_by_code.get(country_code)
                country_source["country_name_en"] = country_name_by_code.get(
                    country_code
                )
                country_source["country_name_zh"] = country_name_zh_by_code.get(
                    country_code
                )
                disease_source_info.append(country_source)
            disease_data["generated_at"] = generated_at
            disease_data["source_info"] = disease_source_info
            disease_record_count = sum(
                len(series.get("dates") or [])
                for series in (disease_data.get("country_series") or {}).values()
            )
            disease_exports.append(
                {
                    "disease_id": did,
                    "disease_data": disease_data,
                    "site_data": disease_site_data,
                }
            )
            disease_download_entries.append(
                {
                    "kind": "disease",
                    "id": did.lower(),
                    "disease_id": did,
                    "slug": disease.get("slug"),
                    "name_en": disease.get("name_en"),
                    "name_zh": disease.get("name_zh"),
                    "generated_at": generated_at,
                    "record_count": disease_record_count,
                    "country_count": len(disease_countries),
                    "site_json_path": f"/site-data/diseases/{did.lower()}.json",
                }
            )

        # The projected country and disease snapshots are now complete. Drop
        # the raw row dictionaries before report/literature enrichment and file
        # generation so the export does not keep both representations alive.
        if use_publish_read:
            country_record_counts = {
                item["code"]: int(item.get("record_count") or 0)
                for item in country_exports
            }
        else:
            country_record_counts = {
                code: len(records) for code, records in all_records_by_country.items()
            }
        all_records_by_country.clear()
        all_source_records_by_country.clear()
        records_by_disease_by_country.clear()
        source_records_by_disease_by_country.clear()
        country_results.clear()
        fresh_results_by_code.clear()
        refreshed_country_data.clear()

        if not incremental:
            for rep in reports:
                detail = await fetch_report_detail(session, rep["id"])
                if detail:
                    report_details[rep["id"]] = detail

        literature_export = await collect_literature_export(
            session,
            diseases_by_id=diseases_by_id,
            surveillance_coverage={
                item["disease_id"]: (
                    set((publish_coverage or {}).get(item["disease_id"], ()))
                    if use_publish_read
                    else set((item["disease_data"].get("country_series") or {}).keys())
                )
                for item in disease_exports
            },
            limit=get_config().literature.public_article_limit,
        )

    if incremental:
        # The Situation Room refresh runs immediately before an incremental
        # release and advances the database publication pointer. Reading the
        # previous local snapshot here can therefore publish a stale
        # `latest.json` and fail the release gate. Fetch the pointer-backed
        # report even for incremental country exports; weekly/monthly archives
        # remain unchanged to keep the incremental path bounded.
        situation_latest = await latest_report_v3()
        situation_weekly = []
        situation_monthly = []
    else:
        situation_latest = await latest_report_v3()
        situation_weekly = await reports_v3("weekly")
        situation_monthly = await reports_v3("monthly")
    literature_export = attach_surveillance_evidence(
        literature_export,
        situation_latest,
        diseases_by_id=diseases_by_id,
    )

    return {
        "country_record_counts": country_record_counts,
        "countries_simple": countries_simple,
        "country_download_entries": country_download_entries,
        "country_exports": country_exports,
        "country_sources_by_code": country_sources_by_code,
        "disease_download_entries": disease_download_entries,
        "disease_exports": disease_exports,
        "disease_knowledge_briefs": disease_knowledge_briefs,
        "diseases": diseases,
        "diseases_by_id": diseases_by_id,
        "generated_at": generated_at,
        "ontology": ontology,
        "ontology_document": ontology_document,
        "report_details": report_details,
        "reports": reports,
        "literature_export": literature_export,
        "situation_latest": situation_latest,
        "situation_monthly": situation_monthly,
        "situation_weekly": situation_weekly,
        "incremental": incremental,
        "incremental_country_codes": sorted(incremental_codes),
        "changed_disease_ids": sorted(changed_disease_ids),
        "publish_hashes": (
            {
                code: state["content_hash"]
                for code, state in publish_states.items()
            }
            if publish_states
            else None
        ),
        "publish_curve_refresh_codes": (
            sorted(curve_refresh_codes) if curve_refresh_codes is not None else None
        ),
        "recorded_at_as_of": (
            recorded_at_as_of.isoformat() if recorded_at_as_of else None
        ),
        "recorded_history_complete": (
            all(
                item["country_data"].get("recorded_history_complete") is True
                for item in country_exports
            )
            if recorded_at_as_of is not None
            else None
        ),
    }


def write_site_export_artifacts(
    context: dict,
    output_dir: Path,
    public_site_data_dir: Path,
) -> None:
    """Write build and public artifacts in their established order."""
    country_record_counts = context.get("country_record_counts")
    if not isinstance(country_record_counts, dict):
        country_record_counts = {
            code: len(records)
            for code, records in (context.get("all_records_by_country") or {}).items()
        }
    countries_simple = context["countries_simple"]
    country_download_entries = context["country_download_entries"]
    country_exports = context["country_exports"]
    disease_download_entries = context["disease_download_entries"]
    disease_exports = context["disease_exports"]
    disease_knowledge_briefs = context["disease_knowledge_briefs"]
    diseases = context["diseases"]
    diseases_by_id = context["diseases_by_id"]
    generated_at = context["generated_at"]
    ontology = context["ontology"]
    ontology_document = context["ontology_document"]
    report_details = context["report_details"]
    reports = context["reports"]
    literature_export = context.get("literature_export") or {}
    situation_latest = context.get("situation_latest")
    situation_monthly = context.get("situation_monthly") or []
    situation_weekly = context.get("situation_weekly") or []

    prepare_site_output_dirs(output_dir, public_site_data_dir)
    write_compact_json(public_site_data_dir / "site-status.json", {"generated_at": generated_at})
    curve_refresh_codes = context.get("publish_curve_refresh_codes")
    limit_curves = curve_refresh_codes is not None
    curve_refresh_set = {str(code) for code in curve_refresh_codes or ()}
    changed_disease_set = {str(disease_id) for disease_id in context.get("changed_disease_ids") or ()}

    # Write disease index
    write_pretty_json(output_dir / "diseases" / "index.json", diseases)
    print(f"  ✓ diseases/index.json ({len(diseases)} diseases)")

    write_pretty_json(output_dir / "disease-ontology.json", ontology_document)
    write_pretty_json(
        public_site_data_dir / "disease-ontology.json", ontology_document
    )
    print(
        "  ✓ disease-ontology.json "
        f"({len(ontology.concept_ids)} concepts, {len(ontology.series_ids)} series)"
    )

    for country_export in country_exports:
        code = country_export["code"]
        if limit_curves and code not in curve_refresh_set:
            continue
        country_data = country_export["country_data"]
        site_data = country_export["site_data"]
        write_pretty_json(
            output_dir / "countries" / f"{code.lower()}.json", country_data
        )
        write_compact_json(
            public_site_data_dir / "countries" / f"{code.lower()}.json",
            site_data,
        )
        write_compact_json(
            public_site_data_dir / "countries" / f"{code.lower()}-source-series.json",
            build_country_source_series_data(country_data),
        )
        print(
            f"  ✓ countries/{code.lower()}.json "
            f"({country_record_counts.get(code, 0)} records)"
        )

    knowledge_mode_counts: dict[str, int] = defaultdict(int)
    knowledge_completeness_values: list[float] = []
    for disease_export in disease_exports:
        did = disease_export["disease_id"]
        disease_data = disease_export["disease_data"]
        disease_site_data = disease_export["site_data"]
        disease_knowledge_payload = build_disease_knowledge_fields(
            diseases_by_id[did],
            disease_knowledge_briefs.get(did),
        )
        knowledge_mode_counts[
            str(disease_knowledge_payload.get("knowledge_display_mode") or "blocked")
        ] += 1
        knowledge_completeness_values.append(
            float(disease_knowledge_payload.get("knowledge_completeness") or 0.0)
        )
        if limit_curves and did not in changed_disease_set:
            continue
        write_pretty_json(
            output_dir / "diseases" / f"{did.lower()}.json", disease_data
        )
        write_pretty_json(
            output_dir / "disease-knowledge" / f"{did.lower()}.json",
            disease_knowledge_payload,
        )
        write_compact_json(
            public_site_data_dir / "diseases" / f"{did.lower()}.json",
            disease_site_data,
        )
    rewritten_diseases = len(changed_disease_set) if limit_curves else len(diseases)
    print(
        f"  ✓ diseases/{diseases[0]['disease_id'].lower()}.json … "
        f"({rewritten_diseases} rewritten, {len(diseases)} in catalogue)"
    )
    print(
        f"  ✓ disease-knowledge/{diseases[0]['disease_id'].lower()}.json … ({len(diseases)} files)"
    )
    print(
        "  Knowledge quality: "
        + ", ".join(
            f"{mode}={knowledge_mode_counts.get(mode, 0)}"
            for mode in ("full", "partial", "blocked")
        )
    )
    print(
        "  ✓ download catalogue entries "
        f"({len(country_download_entries)} countries, "
        f"{len(disease_download_entries)} diseases)"
    )

    write_pretty_json(output_dir / "reports" / "index.json", reports)
    print(f"  ✓ reports/index.json ({len(reports)} reports)")

    for report_id, detail in report_details.items():
        write_pretty_json(output_dir / "reports" / f"{report_id}.json", detail)
    print(f"  ✓ reports/<id>.json ({len(report_details)} files)")

    write_literature_artifacts(literature_export, output_dir)
    print(f"  ✓ research/index.json ({len(literature_export.get('articles') or [])} published articles)")

    # ── Meta ──
    meta = {
        "generated_at": generated_at,
        "total_countries": sum(
            1 for country in countries_simple if country.get("data_available")
        ),
        "total_diseases": len(diseases),
        "total_reports": len(reports),
        "countries": countries_simple,
        "knowledge_quality": {
            "display_modes": dict(sorted(knowledge_mode_counts.items())),
            "average_completeness": (
                round(
                    sum(knowledge_completeness_values)
                    / len(knowledge_completeness_values),
                    3,
                )
                if knowledge_completeness_values
                else 0.0
            ),
            "schema_version": 3,
        },
        "disease_ontology": {
            "registry_id": ontology_document["registry_id"],
            "schema_version": ontology_document["schema_version"],
            "default_rollup_policy": ontology_document["default_rollup_policy"],
            "concept_count": len(ontology.concept_ids),
            "source_series_count": len(ontology.series_ids),
        },
    }
    write_pretty_json(output_dir / "meta.json", meta)
    print("  ✓ meta.json")

    about_snapshot = build_about_snapshot(
        countries_simple=[
            country
            for country in countries_simple
            if country.get("data_available")
        ],
        diseases=diseases,
        reports=reports,
        generated_at=generated_at,
    )
    write_pretty_json(output_dir / "about.json", about_snapshot)
    print("  ✓ about.json")

    # Situation Room artifacts are generated from durable snapshots.  The
    # public-site copy is compact; build-time pages retain readable JSON.
    situation_public = bool(situation_latest and situation_latest.get("public_enabled"))
    if situation_public:
        # Canonical v3 paths. The legacy latest.json alias remains for one
        # release cycle but intentionally carries the v3 contract unchanged.
        write_pretty_json(output_dir / "situation" / "v3" / "latest.json", situation_latest)
        write_compact_json(public_site_data_dir / "situation" / "v3" / "latest.json", situation_latest)
        write_pretty_json(output_dir / "situation" / "latest.json", situation_latest)
        write_compact_json(public_site_data_dir / "situation" / "latest.json", situation_latest)
        for snapshot in situation_weekly:
            iso_week = str((snapshot.get("report") or {}).get("period_key") or "")
            if not iso_week:
                continue
            write_pretty_json(output_dir / "situation" / "v3" / "weekly" / f"{iso_week}.json", snapshot)
            write_compact_json(public_site_data_dir / "situation" / "v3" / "weekly" / f"{iso_week}.json", snapshot)
        for snapshot in situation_monthly:
            month = str((snapshot.get("report") or {}).get("period_key") or "")
            if not month:
                continue
            write_pretty_json(output_dir / "situation" / "v3" / "monthly" / f"{month}.json", snapshot)
            write_compact_json(public_site_data_dir / "situation" / "v3" / "monthly" / f"{month}.json", snapshot)
        print(f"  ✓ situation snapshots (latest + {len(situation_weekly)} weekly + {len(situation_monthly)} monthly)")
    elif situation_latest:
        # Dev preview only: this file is consumed at Astro build time and is
        # never copied to public/site-data, indexed, or included in sitemaps.
        write_pretty_json(output_dir / "situation" / "v3" / "shadow-latest.json", situation_latest)
        print("  ✓ situation shadow preview (build-time only)")

    # Reconcile stale artifacts only after every new artifact is safely on disk.
    # This keeps unchanged files intact throughout export and prevents a failed
    # run from leaving an empty site-data directory behind.
    remove_stale_json_files(
        output_dir / "countries",
        {f"{item['code'].lower()}.json" for item in country_exports},
    )
    disease_json_names = {f"{item['disease_id'].lower()}.json" for item in disease_exports}
    remove_stale_json_files(output_dir / "diseases", disease_json_names | {"index.json"})
    remove_stale_json_files(output_dir / "disease-knowledge", disease_json_names)
    remove_stale_json_files(
        output_dir / "reports",
        {"index.json", *[f"{report_id}.json" for report_id in report_details]},
    )
    remove_stale_json_files(
        public_site_data_dir / "countries",
        {
            filename
            for item in country_exports
            for filename in (
                f"{item['code'].lower()}.json",
                f"{item['code'].lower()}-source-series.json",
            )
        },
    )
    remove_stale_json_files(
        public_site_data_dir / "diseases",
        disease_json_names,
    )
    if situation_public:
        remove_stale_json_files(output_dir / "situation", {"latest.json"})
        remove_stale_json_files(public_site_data_dir / "situation", {"latest.json"})
        remove_stale_json_files(output_dir / "situation" / "v3", {"latest.json"})
        remove_stale_json_files(public_site_data_dir / "situation" / "v3", {"latest.json"})
        week_names = {f"{(snapshot.get('report') or {}).get('period_key')}.json" for snapshot in situation_weekly if (snapshot.get("report") or {}).get("period_key")}
        month_names = {f"{(snapshot.get('report') or {}).get('period_key')}.json" for snapshot in situation_monthly if (snapshot.get("report") or {}).get("period_key")}
        remove_stale_json_files(output_dir / "situation" / "v3" / "weekly", week_names)
        remove_stale_json_files(public_site_data_dir / "situation" / "v3" / "weekly", week_names)
        remove_stale_json_files(output_dir / "situation" / "v3" / "monthly", month_names)
        remove_stale_json_files(public_site_data_dir / "situation" / "v3" / "monthly", month_names)
        # v2 period artifacts are no longer canonical.
        remove_stale_json_files(output_dir / "situation" / "weeks", set())
        remove_stale_json_files(public_site_data_dir / "situation" / "weeks", set())
        remove_stale_json_files(output_dir / "situation" / "months", set())
        remove_stale_json_files(public_site_data_dir / "situation" / "months", set())
    else:
        remove_stale_json_files(
            output_dir / "situation",
            set(),
        )
        remove_stale_json_files(
            output_dir / "situation" / "v3",
            {"shadow-latest.json"} if situation_latest else set(),
        )
        remove_stale_json_files(public_site_data_dir / "situation", set())
        remove_stale_json_files(public_site_data_dir / "situation" / "v3", set())
        remove_stale_json_files(output_dir / "situation" / "weeks", set())
        remove_stale_json_files(output_dir / "situation" / "months", set())
        remove_stale_json_files(public_site_data_dir / "situation" / "weeks", set())
        remove_stale_json_files(public_site_data_dir / "situation" / "months", set())
        remove_stale_json_files(output_dir / "situation" / "v3" / "weekly", set())
        remove_stale_json_files(output_dir / "situation" / "v3" / "monthly", set())
        remove_stale_json_files(public_site_data_dir / "situation" / "v3" / "weekly", set())
        remove_stale_json_files(public_site_data_dir / "situation" / "v3" / "monthly", set())
    _write_publish_hash_manifest(output_dir, context)


def write_incremental_site_export_artifacts(
    context: dict,
    output_dir: Path,
    public_site_data_dir: Path,
) -> None:
    """Write changed full snapshots and refresh every compact browser projection."""

    prepare_site_output_dirs(output_dir, public_site_data_dir)
    write_compact_json(public_site_data_dir / "site-status.json", {"generated_at": context["generated_at"]})
    changed_countries = set(context.get("incremental_country_codes") or ())
    changed_diseases = set(context.get("changed_disease_ids") or ())
    countries_by_code = {
        item["code"]: item for item in context["country_exports"]
    }
    diseases_by_id = {
        item["disease_id"]: item for item in context["disease_exports"]
    }

    for code in sorted(changed_countries):
        country_export = countries_by_code.get(code)
        if not country_export:
            continue
        country_data = country_export["country_data"]
        write_pretty_json(
            output_dir / "countries" / f"{code.lower()}.json",
            country_data,
        )

    # Only refresh affected browser projections. Missing files are repaired so
    # an older snapshot can still be used as the base for an incremental run.
    for code, country_export in countries_by_code.items():
        site_path = public_site_data_dir / "countries" / f"{code.lower()}.json"
        source_path = public_site_data_dir / "countries" / f"{code.lower()}-source-series.json"
        if code not in changed_countries and site_path.is_file() and source_path.is_file():
            continue
        country_data = country_export["country_data"]
        write_compact_json(
            site_path,
            country_export["site_data"],
        )
        write_compact_json(
            source_path,
            build_country_source_series_data(country_data),
        )

    for disease_id in sorted(changed_diseases):
        disease_export = diseases_by_id.get(disease_id)
        if not disease_export:
            continue
        disease_data = disease_export["disease_data"]
        write_pretty_json(
            output_dir / "diseases" / f"{disease_id.lower()}.json",
            disease_data,
        )
        write_pretty_json(
            output_dir / "disease-knowledge" / f"{disease_id.lower()}.json",
            build_disease_knowledge_fields(
                context["diseases_by_id"][disease_id],
                context["disease_knowledge_briefs"].get(disease_id),
            ),
        )

    for disease_id, disease_export in diseases_by_id.items():
        site_path = public_site_data_dir / "diseases" / f"{disease_id.lower()}.json"
        if disease_id not in changed_diseases and site_path.is_file():
            continue
        write_compact_json(
            site_path,
            disease_export["site_data"],
        )

    existing_meta = _read_json_file(output_dir / "meta.json", {})
    if not isinstance(existing_meta, dict):
        existing_meta = {}
    existing_meta.update(
        {
            "generated_at": context["generated_at"],
            "total_countries": sum(
                1
                for country in context["countries_simple"]
                if country.get("data_available")
            ),
            "total_diseases": len(context["diseases"]),
            "total_reports": len(context["reports"]),
            "countries": context["countries_simple"],
        }
    )
    write_pretty_json(output_dir / "meta.json", existing_meta)

    about_snapshot = build_about_snapshot(
        countries_simple=[
            country
            for country in context["countries_simple"]
            if country.get("data_available")
        ],
        diseases=context["diseases"],
        reports=context["reports"],
        generated_at=context["generated_at"],
    )
    write_pretty_json(output_dir / "about.json", about_snapshot)

    # An incremental release still runs the Situation Room refresh first.
    # Refresh both build-time and public latest snapshots so the generated
    # artifact stays aligned with the database publication pointer; weekly
    # and monthly archives remain unchanged on this bounded path.
    situation_latest = context.get("situation_latest")
    if situation_latest and situation_latest.get("public_enabled"):
        write_pretty_json(output_dir / "situation" / "v3" / "latest.json", situation_latest)
        write_compact_json(public_site_data_dir / "situation" / "v3" / "latest.json", situation_latest)
        write_pretty_json(output_dir / "situation" / "latest.json", situation_latest)
        write_compact_json(public_site_data_dir / "situation" / "latest.json", situation_latest)
    elif situation_latest:
        write_pretty_json(output_dir / "situation" / "v3" / "shadow-latest.json", situation_latest)

    write_literature_artifacts(context["literature_export"], output_dir)
    _write_publish_hash_manifest(output_dir, context)

    print(
        "  ✓ incremental site artifacts "
        f"({len(changed_countries)} countries, {len(changed_diseases)} diseases)"
    )


async def export(
    output_dir: Path,
    manifest_output: Path,
    allow_empty_export: bool = False,
    *,
    public_site_data_dir: Path = DEFAULT_PUBLIC_SITE_DATA_OUTPUT,
    direct_download_output_dir: Path = DEFAULT_DIRECT_DOWNLOAD_OUTPUT,
    direct_download_url_base: str = DEFAULT_DIRECT_DOWNLOAD_URL_BASE,
    direct_download_max_file_bytes: int = DEFAULT_TARGET_FILE_BYTES,
    direct_download_workers: int | None = None,
    incremental_country_codes: list[str] | tuple[str, ...] | None = None,
    recorded_at_as_of=None,
    incremental_site: bool = False,
) -> None:
    """Package and write one complete or scoped incremental export."""
    collect_started = time.perf_counter()
    try:
        context = await collect_site_export_context(
            output_dir,
            allow_empty_export,
            incremental_country_codes,
            public_site_data_dir=public_site_data_dir,
            recorded_at_as_of=recorded_at_as_of,
            incremental_site=incremental_site,
        )
    except RuntimeError as exc:
        if not (incremental_site or incremental_country_codes) or not str(exc).startswith(
            ("Incremental site export requires", "Incremental site export is missing")
        ):
            raise
        print(f"  Existing incremental snapshot unavailable ({exc}); rebuilding all site data", flush=True)
        context = await collect_site_export_context(
            output_dir,
            allow_empty_export,
            None,
            public_site_data_dir=public_site_data_dir,
            recorded_at_as_of=recorded_at_as_of,
        )
    print(
        "  Phase timing: database reads and projections "
        f"{time.perf_counter() - collect_started:.1f}s",
        flush=True,
    )
    country_download_entries = context["country_download_entries"]
    disease_download_entries = context["disease_download_entries"]

    artifact_started = time.perf_counter()
    if context.get("incremental"):
        write_incremental_site_export_artifacts(
            context,
            output_dir,
            public_site_data_dir,
        )
    else:
        write_site_export_artifacts(context, output_dir, public_site_data_dir)
    print(
        "  Phase timing: site JSON artifacts "
        f"{time.perf_counter() - artifact_started:.1f}s",
        flush=True,
    )

    downloads_started = time.perf_counter()
    curve_refresh_codes = context.get("publish_curve_refresh_codes")
    if context.get("incremental"):
        changed_country_codes = set(context["incremental_country_codes"])
        changed_disease_ids = set(context["changed_disease_ids"])
    elif curve_refresh_codes is not None:
        changed_country_codes = set(curve_refresh_codes)
        changed_disease_ids = set(context.get("changed_disease_ids") or ())
    else:
        changed_country_codes = None
        changed_disease_ids = None
    downloads_manifest = build_direct_download_files(
        context,
        direct_download_output_dir,
        download_url_base=direct_download_url_base,
        max_file_bytes=direct_download_max_file_bytes,
        workers=direct_download_workers,
        changed_country_codes=changed_country_codes,
        changed_disease_ids=changed_disease_ids,
    )
    print(
        "  Phase timing: partitioned download files "
        f"{time.perf_counter() - downloads_started:.1f}s",
        flush=True,
    )
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    write_pretty_json(manifest_output, downloads_manifest)
    print(
        "  ✓ partitioned CSV/JSON/XLSX downloads "
        f"({len(country_download_entries)} countries, "
        f"{len(disease_download_entries)} diseases)"
    )
    print("  ✓ frontend download manifest uses GitHub Raw main-branch files")
    print(f"\nDone. Data written to: {output_dir}")
