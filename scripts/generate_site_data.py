#!/usr/bin/env python3
"""
Generate static JSON data files for the Astro-based report site.

Usage:
    python scripts/generate_site_data.py
    python scripts/generate_site_data.py --direct-download-output exports/site-downloads

Reads from the PostgreSQL database and writes structured JSON files that
the Astro build process consumes at build time.
"""

import argparse
import asyncio
import os
import json
from datetime import datetime, timezone
import sys
from pathlib import Path

# Make sure project root is on PYTHONPATH
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.generation.site_data_about import (  # noqa: E402
    ABOUT_COUNTRY_NAMES_ZH,
    ABOUT_SOURCE_DESCRIPTIONS_ZH,
    ABOUT_SOURCE_LABELS_ZH,
    CADENCE_LABELS_ZH,
    SOURCE_DETAILS_BY_SCOPE,
    build_about_snapshot,
    build_country_source_info,
    normalize_cadence_label,
    normalize_cadence_label_zh,
    parse_iso_timestamp,
    resolve_snapshot_version,
)
from src.generation.site_data_database import ensure_standard_country_rows  # noqa: E402
from src.generation.direct_download_files import DEFAULT_TARGET_FILE_BYTES  # noqa: E402
from src.generation.site_data_queries import (  # noqa: E402
    compact_report_metadata,
    enrich_source_attribution,
    fetch_countries,
    fetch_country_briefs,
    fetch_country_frequency_meta,
    fetch_disease_export_layers,
    fetch_disease_knowledge_briefs,
    fetch_disease_records,
    fetch_disease_records_direct,
    fetch_disease_series_records,
    fetch_report_detail,
    fetch_reports,
    has_population_table,
    has_table,
    iso_or_none,
    safe_float,
    safe_int,
    source_metadata_field,
)
from src.generation.site_data_views import (  # noqa: E402
    _compact_source_series_metadata,
    _country_series_data_layer_summary,
    _data_layer_summary,
    _series_context_for_records,
    _series_provenance_fields,
    avg_or_none,
    build_country_data,
    build_country_site_data,
    build_disease_data,
    build_disease_site_data,
    calculate_weekly_equivalent,
    dominant_value,
    resolve_country_display_names,
)
from src.generation.site_data_writer import (  # noqa: E402
    clean_generated_dir,
    existing_site_export_has_content,
    prepare_site_output_dirs,
    reset_public_data_dir,
    write_compact_json,
    write_pretty_json,
)
from src.generation.site_series_projection import (  # noqa: E402
    LEGACY_DATA_LAYER,
    LEGACY_GAP_FILL_DATA_LAYER,
    MIXED_DATA_LAYER,
    SERIES_DATA_LAYER,
    _attach_legacy_supplemental_metrics,
    _collapse_selected_series_records,
    _legacy_projection_context,
    _normalise_count,
    _overlay_legacy_coverage_gaps,
    _projection_context,
    _representative_series_code,
    _series_is_case_count,
    _source_series_details,
    apply_disease_cutover_projection,
    apply_series_first_projection,
    validate_series_first_projection,
)
from src.ontology import DiseaseOntology, load_disease_ontology  # noqa: E402
from src.generation.site_data_catalogue import (  # noqa: E402
    enrich_diseases_with_ontology,
    load_standard_diseases,
    validate_record_catalogue_coverage,
)


# ─────────────────────────────────────────────────────────────
from src.generation.site_data_knowledge import (  # noqa: E402
    AUTHORITATIVE_KNOWLEDGE_SOURCE_TYPES,
    AUTHORITATIVE_KNOWLEDGE_URL_MARKERS,
    _is_authoritative_knowledge_source,
    apply_country_brief_fields,
    apply_disease_knowledge_fields,
    build_disease_knowledge_fields,
)


# ─────────────────────────────────────────────────────────────
# Main export
# ─────────────────────────────────────────────────────────────
from src.generation.site_data_export import (  # noqa: E402
    DEFAULT_DIRECT_DOWNLOAD_OUTPUT,
    DEFAULT_DIRECT_DOWNLOAD_URL_BASE,
    DEFAULT_DOWNLOAD_MANIFEST,
    DEFAULT_DOWNLOAD_REPO_URL,
    DEFAULT_OUTPUT,
    DEFAULT_PUBLIC_SITE_DATA_OUTPUT,
    ensure_site_export_database_ready,
    export,
    export_reports_only,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export site data to JSON")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--public-site-data-output",
        type=Path,
        default=DEFAULT_PUBLIC_SITE_DATA_OUTPUT,
        help=(
            "Compact browser data output directory "
            f"(default: {DEFAULT_PUBLIC_SITE_DATA_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--direct-download-output",
        type=Path,
        default=DEFAULT_DIRECT_DOWNLOAD_OUTPUT,
        help=(
            "Partitioned CSV/JSON/XLSX output directory "
            f"(default: {DEFAULT_DIRECT_DOWNLOAD_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--direct-download-url-base",
        default=DEFAULT_DIRECT_DOWNLOAD_URL_BASE,
        help=(
            "Public GitHub Raw base for partitioned downloads "
            f"(default: {DEFAULT_DIRECT_DOWNLOAD_URL_BASE})"
        ),
    )
    parser.add_argument(
        "--direct-download-max-file-bytes",
        type=int,
        default=DEFAULT_TARGET_FILE_BYTES,
        help=(
            "Target maximum bytes per generated file; oversized calendar windows "
            f"are split automatically (default: {DEFAULT_TARGET_FILE_BYTES})"
        ),
    )
    parser.add_argument(
        "--direct-download-workers",
        type=int,
        default=None,
        help=(
            "Maximum concurrent workers for partitioned download generation "
            "(default: CPU-based exporter default)"
        ),
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        default=DEFAULT_DOWNLOAD_MANIFEST,
        help=f"Frontend manifest output path (default: {DEFAULT_DOWNLOAD_MANIFEST})",
    )
    parser.add_argument(
        "--allow-empty-export",
        action="store_true",
        help="Allow overwriting site data even when the database currently exports zero disease records",
    )
    parser.add_argument(
        "--incremental-country",
        dest="incremental_country_codes",
        action="append",
        default=[],
        help=(
            "Refresh only the listed country scopes and merge them into the existing "
            "site snapshot; repeat for multiple countries"
        ),
    )
    parser.add_argument(
        "--incremental-site",
        action="store_true",
        help="Reuse existing surveillance snapshots while refreshing literature and Situation data",
    )
    parser.add_argument(
        "--recorded-at-as-of",
        help=(
            "Build a complete surveillance site snapshot using the latest "
            "recorded observation states at this timezone-aware ISO datetime"
        ),
    )
    parser.add_argument("--reports-only", action="store_true", help="Refresh report index and changed details only; reuse all other data")
    parser.add_argument("--data-only", action="store_true", help="Export versioned surveillance browser JSON without HTML, reports, literature or download formats")
    parser.add_argument("--content-only", action="store_true", help="Refresh static content using the frozen local surveillance baseline; never read live observations or generate downloads")
    parser.add_argument("--reuse-published-research", action="store_true", help="Reuse the last published research snapshot instead of regenerating literature")
    parser.add_argument("--surveillance-state", type=Path, default=ROOT / "exports/surveillance-state")
    parser.add_argument("--surveillance-output", type=Path, default=ROOT / "exports/surveillance-data")
    parser.add_argument("--data-release-id", help="Stable data release identity (queued jobs use their task UUID)")
    parser.add_argument("--surveillance-changes-file", type=Path, help="Exact selected versioned change sets for scoped JSON projection")
    parser.add_argument("--surveillance-workers", type=int, default=None,
        help="Independent country projection processes, 1–8 (default: CPU/cgroup memory budget)")
    parser.add_argument("--exact-country-scopes", action="store_true", help="Country selection is already expanded by the release planner")
    args = parser.parse_args()
    if args.data_only:
        if args.reports_only or args.content_only or args.incremental_site or args.recorded_at_as_of:
            parser.error("--data-only cannot be combined with reports, literature or historical site export modes")
        from src.generation.surveillance_data_export import export_surveillance_data
        from src.generation.country_projection_workers import default_country_workers
        if args.surveillance_workers is not None and not 1 <= args.surveillance_workers <= 8:
            parser.error("--surveillance-workers must be 1–8")
        async def export_json():
            from src.core.database_snapshot import SNAPSHOT_ENV, export_read_snapshot
            async def generate():
                return await export_surveillance_data(
                    args.surveillance_state, args.surveillance_output,
                    release_id=args.data_release_id or datetime.now(timezone.utc).strftime("data-%Y%m%dT%H%M%S.%fZ"),
                    incremental_country_codes=args.incremental_country_codes or None,
                    incremental=bool(args.incremental_country_codes),
                    country_scopes_exact=args.exact_country_scopes,
                    publication_changes=(json.loads(args.surveillance_changes_file.read_text())
                        if args.surveillance_changes_file else None),
                    country_workers=args.surveillance_workers or default_country_workers(),
                )
            if os.environ.get(SNAPSHOT_ENV):
                return await generate()
            # Standalone CLI runs own the same consistent read boundary as
            # queued data updates. The environment is scoped to this CLI only.
            async with export_read_snapshot() as snapshot_env:
                os.environ[SNAPSHOT_ENV] = snapshot_env[SNAPSHOT_ENV]
                try:
                    return await generate()
                finally:
                    os.environ.pop(SNAPSHOT_ENV, None)
        asyncio.run(export_json())
        return
    if args.content_only and (args.reports_only or args.incremental_country_codes or args.recorded_at_as_of):
        parser.error("--content-only cannot select surveillance or historical export scopes")
    if args.content_only and not args.reuse_published_research:
        from src.core.config import get_config
        args.reuse_published_research = bool(get_config().data_release.literature_json_enabled)
    if args.reuse_published_research:
        if not args.content_only:
            parser.error("--reuse-published-research requires --content-only")
        from src.generation.published_research_reuse import install_published_research
        install_published_research(ROOT, args.output, args.public_site_data_output)
    if args.reports_only:
        if args.incremental_site or args.incremental_country_codes or args.recorded_at_as_of:
            parser.error("--reports-only cannot be combined with other export scopes")
        asyncio.run(export_reports_only(args.output, args.public_site_data_output))
        return
    recorded_at_as_of = None
    if args.recorded_at_as_of:
        try:
            recorded_at_as_of = datetime.fromisoformat(
                args.recorded_at_as_of.replace("Z", "+00:00")
            )
        except ValueError as exc:
            parser.error(f"invalid --recorded-at-as-of value: {exc}")
        if recorded_at_as_of.tzinfo is None:
            parser.error("--recorded-at-as-of must include a timezone")
        recorded_at_as_of = recorded_at_as_of.astimezone(timezone.utc)
    print(f"Exporting site data to {args.output} …")
    print(f"Writing partitioned CSV/JSON/XLSX files to {args.direct_download_output} …")
    print(f"Writing download manifest to {args.manifest_output} …\n")
    asyncio.run(
        export(
            args.output,
            args.manifest_output,
            args.allow_empty_export,
            public_site_data_dir=args.public_site_data_output,
            direct_download_output_dir=args.direct_download_output,
            direct_download_url_base=args.direct_download_url_base,
            direct_download_max_file_bytes=args.direct_download_max_file_bytes,
            direct_download_workers=args.direct_download_workers,
            incremental_country_codes=args.incremental_country_codes,
            recorded_at_as_of=recorded_at_as_of,
            incremental_site=args.incremental_site,
            country_scopes_exact=args.exact_country_scopes,
            content_only=args.content_only,
            reuse_published_research=args.reuse_published_research,
        )
    )


if __name__ == "__main__":
    main()
