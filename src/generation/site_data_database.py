"""Database readiness steps required before generating site data."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text

from src.core.country_library import (
    get_country_bootstrap_config,
    get_country_profile,
    get_standard_country_codes,
)
from src.core.database import get_db, init_database
from src.core.db_schema import ensure_country_scope, ensure_country_scope_schema


def _build_country_metadata(profile: Any, bootstrap: dict) -> dict:
    """Build location-aware metadata without labelling subdivisions as ISO alpha-2."""

    metadata = {
        "standard_source": profile.source,
        "site_export_bootstrap": True,
    }
    for field in (
        "parent_country_code",
        "location_type",
        "iso_country_code",
        "iso_subdivision_code",
    ):
        if bootstrap.get(field):
            metadata[field] = bootstrap[field]

    iso_alpha2 = bootstrap.get("iso_country_code")
    if not iso_alpha2 and len(profile.code) == 2:
        iso_alpha2 = profile.code
    if iso_alpha2:
        metadata["iso_alpha2"] = iso_alpha2

    flag_country_code = bootstrap.get("parent_country_code") or iso_alpha2
    if flag_country_code:
        metadata["flag_country_code"] = flag_country_code
    return metadata


async def ensure_standard_country_rows(
    session: Any,
    country_codes: Iterable[str] | None = None,
) -> None:
    """Seed canonical country rows required by the public site export.

    ``country_codes`` permits a rollout to initialize only its approved scope;
    the default remains the complete public-site catalogue.
    """
    selected_codes = (
        get_standard_country_codes()
        if country_codes is None
        else sorted({str(code).strip().upper() for code in country_codes})
    )
    for code in selected_codes:
        profile = get_country_profile(code)
        bootstrap = get_country_bootstrap_config(code)
        await session.execute(
            text("""
                INSERT INTO countries (
                    code, name, name_en, name_local, language, timezone,
                    data_source_url, data_source_type,
                    crawler_config, parser_config, disease_mapping_rules, report_config,
                    is_active, metadata, notes, created_at, updated_at
                ) VALUES (
                    :code, :name, :name_en, :name_local, :language, :timezone,
                    :data_source_url, :data_source_type,
                    CAST(:crawler_config AS json), CAST(:parser_config AS json),
                    CAST(:disease_mapping_rules AS json), CAST(:report_config AS json),
                    true, CAST(:metadata AS json), :notes, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    name_en = EXCLUDED.name_en,
                    name_local = EXCLUDED.name_local,
                    language = COALESCE(NULLIF(countries.language, ''), EXCLUDED.language),
                    timezone = COALESCE(NULLIF(countries.timezone, ''), EXCLUDED.timezone),
                    data_source_url = COALESCE(NULLIF(countries.data_source_url, ''), EXCLUDED.data_source_url),
                    data_source_type = COALESCE(NULLIF(countries.data_source_type, ''), EXCLUDED.data_source_type),
                    crawler_config = CASE
                        WHEN countries.crawler_config IS NULL OR countries.crawler_config::text = '{}' THEN EXCLUDED.crawler_config
                        ELSE countries.crawler_config
                    END,
                    parser_config = CASE
                        WHEN countries.parser_config IS NULL OR countries.parser_config::text = '{}' THEN EXCLUDED.parser_config
                        ELSE countries.parser_config
                    END,
                    disease_mapping_rules = CASE
                        WHEN countries.disease_mapping_rules IS NULL OR countries.disease_mapping_rules::text = '{}' THEN EXCLUDED.disease_mapping_rules
                        ELSE countries.disease_mapping_rules
                    END,
                    report_config = CASE
                        WHEN countries.report_config IS NULL OR countries.report_config::text = '{}' THEN EXCLUDED.report_config
                        ELSE countries.report_config
                    END,
                    metadata = CASE
                        WHEN EXCLUDED.metadata::jsonb ? 'iso_subdivision_code' THEN
                            (
                                (COALESCE(countries.metadata::jsonb, '{}'::jsonb) - 'iso_alpha2')
                                || EXCLUDED.metadata::jsonb
                            )::json
                        WHEN countries.metadata IS NULL OR countries.metadata::text = '{}' THEN EXCLUDED.metadata
                        ELSE countries.metadata
                    END,
                    notes = COALESCE(NULLIF(countries.notes, ''), EXCLUDED.notes),
                    is_active = true,
                    updated_at = CURRENT_TIMESTAMP
                """),
            {
                "code": profile.code,
                "name": profile.name,
                "name_en": profile.name_en,
                "name_local": profile.name_local,
                "language": profile.language,
                "timezone": profile.timezone,
                "data_source_url": bootstrap.get("data_source_url"),
                "data_source_type": bootstrap.get("data_source_type"),
                "crawler_config": json.dumps(bootstrap.get("crawler_config", {})),
                "parser_config": json.dumps(bootstrap.get("parser_config", {})),
                "disease_mapping_rules": json.dumps(bootstrap.get("disease_mapping_rules", {})),
                "report_config": json.dumps(bootstrap.get("report_config", {})),
                "metadata": json.dumps(_build_country_metadata(profile, bootstrap)),
                "notes": bootstrap.get("notes"),
            },
        )
        await ensure_country_scope(
            session,
            scope_code=profile.code,
            country_code=profile.code,
            scope_type="canonical",
            language_code=profile.language,
            display_name=profile.name,
            is_default=True,
            is_active=True,
            metadata={"origin": "generate_site_data", "source": profile.source},
        )


async def ensure_site_export_database_ready() -> int:
    """Create missing tables, seed countries, and return the country count."""
    await init_database()
    async with get_db() as session:
        await ensure_country_scope_schema(session)
        await ensure_standard_country_rows(session)
        result = await session.execute(text("SELECT COUNT(*) FROM countries"))
        return int(result.scalar() or 0)
