"""Database-to-filesystem orchestration for the static site data export."""

from collections import defaultdict
from pathlib import Path

from src.core.data_share import (
    derive_github_raw_base_url,
    get_data_share_repo_branch,
    get_data_share_repo_url,
)
from src.core.database import get_db
from src.core.config import get_config
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
from src.generation.site_data_queries import (
    fetch_countries,
    fetch_country_briefs,
    fetch_country_frequency_meta,
    fetch_disease_export_layers,
    fetch_disease_knowledge_briefs,
    fetch_report_detail,
    fetch_reports,
    has_population_table,
)
from src.generation.site_data_views import (
    build_country_data,
    build_country_site_data,
    build_country_source_series_data,
    build_disease_data,
    build_disease_site_data,
    resolve_country_display_names,
)
from src.generation.site_data_writer import (
    existing_site_export_has_content,
    prepare_site_output_dirs,
    remove_stale_json_files,
    write_compact_json,
    write_pretty_json,
)
from src.generation.site_data_literature import (
    attach_surveillance_evidence,
    collect_literature_export,
    write_literature_artifacts,
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
) -> dict:
    """Read and project all database-backed data without writing artifacts."""
    await ensure_site_export_database_ready()
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
                "  Population table detected: incidence will use WPP-based computation"
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
                }
            )

        all_records_by_country: dict[str, list] = {}
        all_source_records_by_country: dict[str, list] = {}
        country_sources_by_code: dict[str, dict] = {}
        country_name_by_code = {c["code"]: c["name"] for c in countries_simple}
        country_name_zh_by_code = {c["code"]: c["name_zh"] for c in countries_simple}
        country_download_entries: list[dict] = []
        disease_download_entries: list[dict] = []
        for country in countries:
            code = country["code"]
            country_name_en = country_name_by_code.get(code) or country["name"]
            print(f"  Fetching records for {code}…")
            frequency_meta = await fetch_country_frequency_meta(session, code)
            country_source_info = build_country_source_info(code, frequency_meta)
            records, source_records = await fetch_disease_export_layers(
                session, code, population_enabled
            )
            validate_record_catalogue_coverage(
                [*records, *source_records],
                catalogue_ids,
                set(diseases_by_id),
            )

            all_records_by_country[code] = records
            all_source_records_by_country[code] = source_records
            country_sources_by_code[code] = country_source_info
            country_data = build_country_data(
                code,
                country_name_en,
                records,
                diseases_by_id,
                frequency_meta,
                source_records,
            )
            if code.upper() == "IS":
                country_source_info = _retain_observed_iceland_sources(
                    country_source_info,
                    country_data,
                )
                country_sources_by_code[code] = country_source_info
            layer_summary = country_data["data_layer_summary"]
            print(
                "    Series-first: "
                f"registry={layer_summary['series_registry_disease_count']}, "
                f"mixed_gap_fill={layer_summary['mixed_disease_count']}, "
                f"legacy_fallback={layer_summary['legacy_fallback_disease_count']}, "
                f"risk_labelled={layer_summary['loss_risk_disease_count']}"
            )
            if layer_summary["non_additive_series_disease_ids"]:
                print(
                    "    Non-additive series kept separate: "
                    + ", ".join(layer_summary["non_additive_series_disease_ids"])
                )
            country_data["source_info"] = country_source_info
            country_data = apply_country_brief_fields(
                country_data, country_briefs.get(code.upper())
            )
            # Augment countries_simple with stats
            for c in countries_simple:
                if c["code"] == code:
                    record_count = sum(
                        len(series.get("dates") or [])
                        for series in (
                            country_data.get("disease_series") or {}
                        ).values()
                    )
                    c["total_cases"] = country_data["total_cases"]
                    c["total_deaths"] = country_data["total_deaths"]
                    c["disease_count"] = country_data["disease_count"]
                    c["date_range"] = country_data["date_range"]
                    c["record_count"] = record_count
                    c["data_available"] = bool(
                        record_count
                        and country_data["disease_count"]
                        and (
                            country_data["date_range"].get("start")
                            or country_data["date_range"].get("end")
                        )
                    )
                    c["source_info"] = country_source_info
                    c["data_layer_summary"] = country_data["data_layer_summary"]

            country_exports.append(
                {
                    "code": code,
                    "country_name": country_name_en,
                    "country_name_zh": country_name_zh_by_code.get(code),
                    "country_data": country_data,
                    "source_info": country_source_info,
                }
            )

        reports = await fetch_reports(session)
        total_record_count = sum(
            len(records) for records in all_records_by_country.values()
        ) + sum(
            len(records) for records in all_source_records_by_country.values()
        )
        if total_record_count == 0 and not allow_empty_export:
            message = (
                "Refusing to overwrite site data with an empty export because no disease "
                f"records were found in the database across {len(countries)} countries."
            )
            if existing_site_export_has_content(output_dir):
                message += f" Existing files in {output_dir} were left untouched."
            message += " Import data first, or pass --allow-empty-export if this is intentional."
            raise RuntimeError(message)

        generated_at = resolve_snapshot_version(countries_simple, reports)

        for country_export in country_exports:
            code = country_export["code"]
            country_name = country_export["country_name"]
            country_name_zh = country_export.get(
                "country_name_zh"
            ) or country_name_zh_by_code.get(code)
            country_data = country_export["country_data"]
            country_source_info = country_export["source_info"]
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
            disease_data = build_disease_data(
                did,
                disease,
                all_records_by_country,
                all_source_records_by_country,
            )
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

        for rep in reports:
            detail = await fetch_report_detail(session, rep["id"])
            if detail:
                report_details[rep["id"]] = detail

        literature_export = await collect_literature_export(
            session,
            diseases_by_id=diseases_by_id,
            surveillance_coverage={
                item["disease_id"]: set((item["disease_data"].get("country_series") or {}).keys())
                for item in disease_exports
            },
            limit=get_config().literature.public_article_limit,
        )

    situation_latest = await latest_report_v3()
    situation_weekly = await reports_v3("weekly")
    situation_monthly = await reports_v3("monthly")
    literature_export = attach_surveillance_evidence(
        literature_export,
        situation_latest,
        diseases_by_id=diseases_by_id,
    )

    return {
        "all_records_by_country": all_records_by_country,
        "all_source_records_by_country": all_source_records_by_country,
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
    }


def write_site_export_artifacts(
    context: dict,
    output_dir: Path,
    public_site_data_dir: Path,
) -> None:
    """Write build and public artifacts in their established order."""
    all_records_by_country = context["all_records_by_country"]
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
            f"  ✓ countries/{code.lower()}.json ({len(all_records_by_country[code])} records)"
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
    print(
        f"  ✓ diseases/{diseases[0]['disease_id'].lower()}.json … ({len(diseases)} files)"
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


async def export(
    output_dir: Path,
    manifest_output: Path,
    allow_empty_export: bool = False,
    *,
    public_site_data_dir: Path = DEFAULT_PUBLIC_SITE_DATA_OUTPUT,
    direct_download_output_dir: Path = DEFAULT_DIRECT_DOWNLOAD_OUTPUT,
    direct_download_url_base: str = DEFAULT_DIRECT_DOWNLOAD_URL_BASE,
    direct_download_max_file_bytes: int = DEFAULT_TARGET_FILE_BYTES,
) -> None:
    """Package and write one complete export from a collected context."""
    context = await collect_site_export_context(output_dir, allow_empty_export)
    country_download_entries = context["country_download_entries"]
    disease_download_entries = context["disease_download_entries"]

    write_site_export_artifacts(context, output_dir, public_site_data_dir)

    downloads_manifest = build_direct_download_files(
        context,
        direct_download_output_dir,
        download_url_base=direct_download_url_base,
        max_file_bytes=direct_download_max_file_bytes,
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
