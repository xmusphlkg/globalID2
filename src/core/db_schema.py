"""Database schema self-healing helpers."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.task import Task, TaskType


async def _ensure_country_row(db: AsyncSession, country_code: str) -> None:
    """Create a minimal country row when scope migration finds missing parent country."""
    await db.execute(text("""
        INSERT INTO countries (
            code, name, name_en, name_local, language, timezone,
            crawler_config, parser_config, disease_mapping_rules, report_config,
            is_active, metadata, created_at, updated_at
        ) VALUES (
            :code, :name, :name_en, :name_local, :language, :timezone,
            '{}'::json, '{}'::json, '{}'::json, '{}'::json,
            true, '{}'::json, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (code) DO NOTHING
    """), {
        "code": country_code,
        "name": country_code,
        "name_en": country_code,
        "name_local": country_code,
        "language": "en",
        "timezone": "UTC",
    })


async def ensure_country_scope(
    db: AsyncSession,
    *,
    scope_code: str,
    country_code: str,
    scope_type: str,
    language_code: str | None,
    display_name: str | None,
    is_default: bool,
    is_active: bool,
    metadata: dict | None = None,
) -> None:
    """Upsert a country scope row."""
    await db.execute(text("""
        INSERT INTO country_scopes (
            scope_code, country_code, scope_type, language_code,
            display_name, is_default, is_active, metadata,
            created_at, updated_at
        ) VALUES (
            :scope_code, :country_code, :scope_type, :language_code,
            :display_name, :is_default, :is_active, CAST(:metadata AS json),
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (scope_code) DO UPDATE SET
            country_code = EXCLUDED.country_code,
            scope_type = EXCLUDED.scope_type,
            language_code = EXCLUDED.language_code,
            display_name = EXCLUDED.display_name,
            is_default = EXCLUDED.is_default,
            is_active = EXCLUDED.is_active,
            metadata = EXCLUDED.metadata,
            updated_at = CURRENT_TIMESTAMP
    """), {
        "scope_code": scope_code,
        "country_code": country_code,
        "scope_type": scope_type,
        "language_code": language_code,
        "display_name": display_name,
        "is_default": is_default,
        "is_active": is_active,
        "metadata": json.dumps(metadata or {}),
    })


async def ensure_country_scope_for_code(db: AsyncSession, scope_code: str) -> None:
    """Ensure one scope code exists, creating minimal canonical parent if needed."""
    normalized = (scope_code or "").strip().upper()
    if not normalized:
        return

    base_code = normalized.split("_", 1)[0]
    if len(normalized) == 2 and normalized.isalpha():
        base_code = normalized

    await _ensure_country_row(db, base_code)
    await ensure_country_scope(
        db,
        scope_code=normalized,
        country_code=base_code,
        scope_type="canonical" if normalized == base_code else "language_variant",
        language_code=base_code.lower() if normalized == base_code else normalized.split("_", 1)[1].lower(),
        display_name=normalized,
        is_default=normalized == base_code,
        is_active=True,
        metadata={"origin": "runtime_scope_guard"},
    )


async def ensure_disease_mapping_source_schema(db: AsyncSession) -> None:
    """Add source/series mapping identity without rewriting existing rows.

    Existing mappings become wildcard rows.  The old unique index must be
    recreated because PostgreSQL cannot alter an index's key columns in place.
    This helper is intentionally independent from ontology table evolution so
    preflight and ingestion entry points can call it first when appropriate.
    """

    await db.execute(
        text(
            """
            ALTER TABLE disease_mappings
                ADD COLUMN IF NOT EXISTS source_id VARCHAR(120)
                    NOT NULL DEFAULT '*'
            """
        )
    )
    await db.execute(
        text(
            """
            ALTER TABLE disease_mappings
                ADD COLUMN IF NOT EXISTS series_id VARCHAR(160)
            """
        )
    )
    await db.execute(
        text(
            """
            ALTER TABLE disease_mappings
                ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP WITH TIME ZONE
            """
        )
    )
    await db.execute(text("DROP INDEX IF EXISTS idx_mapping_unique"))
    await db.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mapping_unique
            ON disease_mappings (
                disease_id, country_code, source_id, local_name
            )
            """
        )
    )
    await db.execute(text("DROP INDEX IF EXISTS idx_mapping_lookup"))
    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_mapping_lookup
            ON disease_mappings (country_code, source_id, local_name)
            """
        )
    )
    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_mapping_source
            ON disease_mappings (source_id)
            """
        )
    )
    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_mapping_series
            ON disease_mappings (series_id)
            """
        )
    )


async def ensure_country_scope_schema(db: AsyncSession) -> None:
    """Ensure country_scopes exists and migrate disease_mappings.country_code to scope FK."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS country_scopes (
            id SERIAL PRIMARY KEY,
            scope_code VARCHAR(20) NOT NULL UNIQUE,
            country_code VARCHAR(10) NOT NULL,
            scope_type VARCHAR(30) NOT NULL DEFAULT 'canonical',
            language_code VARCHAR(20),
            display_name VARCHAR(120),
            is_default BOOLEAN NOT NULL DEFAULT false,
            is_active BOOLEAN NOT NULL DEFAULT true,
            metadata JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_country_scopes_country
                FOREIGN KEY (country_code)
                REFERENCES countries (code)
                ON DELETE CASCADE
        )
    """))

    alter_statements = [
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS scope_type VARCHAR(30) DEFAULT 'canonical'",
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS language_code VARCHAR(20)",
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS display_name VARCHAR(120)",
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT false",
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true",
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS metadata JSON DEFAULT '{}'::json",
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE country_scopes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE disease_mappings ALTER COLUMN country_code TYPE VARCHAR(20)",
    ]

    for statement in alter_statements:
        await db.execute(text(statement))

    await db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_country_scope_code ON country_scopes (scope_code)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_country_scope_country ON country_scopes (country_code)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_country_scope_type ON country_scopes (scope_type)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_country_scope_active ON country_scopes (is_active)"
    ))

    # Variant-like rows in countries are internal; keep but hide from active list.
    await db.execute(text("""
        UPDATE countries
        SET is_active = false,
            updated_at = CURRENT_TIMESTAMP
        WHERE code LIKE '%\\_%' ESCAPE '\\'
    """))

    # Canonical scopes from countries (ISO-like 2 letters).
    canonical_rows = await db.execute(text("""
        SELECT code, name, language, is_active
        FROM countries
        WHERE code ~ '^[A-Z]{2}$'
    """))
    for code, name, language, is_active in canonical_rows.fetchall():
        await ensure_country_scope(
            db,
            scope_code=code,
            country_code=code,
            scope_type="canonical",
            language_code=language,
            display_name=name,
            is_default=True,
            is_active=bool(is_active),
            metadata={"origin": "countries"},
        )

    # Legacy variant country rows become scope rows (e.g. CN_EN).
    variant_rows = await db.execute(text("""
        SELECT code, name, language
        FROM countries
        WHERE code LIKE '%\\_%' ESCAPE '\\'
    """))
    for code, name, language in variant_rows.fetchall():
        base_code = code.split("_", 1)[0].upper()
        await _ensure_country_row(db, base_code)
        await ensure_country_scope(
            db,
            scope_code=code,
            country_code=base_code,
            scope_type="language_variant",
            language_code=language,
            display_name=name,
            is_default=False,
            is_active=True,
            metadata={"origin": "legacy_country_variant"},
        )

    # Move legacy variant code references to canonical code where possible.
    await db.execute(text("""
        UPDATE crawl_runs cr
        SET country_code = split_part(cr.country_code, '_', 1),
            updated_at = CURRENT_TIMESTAMP
        WHERE cr.country_code LIKE '%\\_%' ESCAPE '\\'
          AND EXISTS (
              SELECT 1
              FROM countries c
              WHERE c.code = split_part(cr.country_code, '_', 1)
          )
    """))
    await db.execute(text("""
        UPDATE disease_learning_suggestions dls
        SET country_code = split_part(dls.country_code, '_', 1),
            updated_at = CURRENT_TIMESTAMP
        WHERE dls.country_code LIKE '%\\_%' ESCAPE '\\'
          AND EXISTS (
              SELECT 1
              FROM countries c
              WHERE c.code = split_part(dls.country_code, '_', 1)
          )
    """))

    # Remove obsolete variant rows from countries once no table references them.
    await db.execute(text("""
        DELETE FROM countries c
        WHERE c.code LIKE '%\\_%' ESCAPE '\\'
          AND NOT EXISTS (
              SELECT 1 FROM crawl_runs cr WHERE cr.country_code = c.code
          )
          AND NOT EXISTS (
              SELECT 1 FROM disease_learning_suggestions dls WHERE dls.country_code = c.code
          )
          AND NOT EXISTS (
              SELECT 1 FROM disease_records dr WHERE dr.country_id = c.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM reports r WHERE r.country_id = c.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM tasks t WHERE t.country_id = c.id
          )
    """))

    # Make sure every used mapping scope exists.
    mapping_table_exists = await db.execute(text("SELECT to_regclass('public.disease_mappings')"))
    if mapping_table_exists.scalar() is not None:
        scope_rows = await db.execute(text("SELECT DISTINCT country_code FROM disease_mappings"))
        for (scope_code,) in scope_rows.fetchall():
            if not scope_code:
                continue
            base_code = scope_code.split("_", 1)[0].upper()
            if len(scope_code) == 2 and scope_code.isalpha():
                base_code = scope_code.upper()

            await _ensure_country_row(db, base_code)
            await ensure_country_scope(
                db,
                scope_code=scope_code,
                country_code=base_code,
                scope_type="canonical" if scope_code == base_code else "language_variant",
                language_code=base_code.lower() if scope_code == base_code else scope_code.split("_", 1)[1].lower(),
                display_name=scope_code,
                is_default=scope_code == base_code,
                is_active=True,
                metadata={"origin": "disease_mappings"},
            )

        await db.execute(text("""
            DO $$
            DECLARE r RECORD;
            BEGIN
                FOR r IN
                    SELECT c.conname
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = 'public'
                      AND t.relname = 'disease_mappings'
                      AND c.contype = 'f'
                      AND pg_get_constraintdef(c.oid) ILIKE '%(country_code)%'
                LOOP
                    IF r.conname <> 'fk_disease_mappings_scope' THEN
                        EXECUTE format('ALTER TABLE disease_mappings DROP CONSTRAINT %I', r.conname);
                    END IF;
                END LOOP;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'fk_disease_mappings_scope'
                ) THEN
                    ALTER TABLE disease_mappings
                    ADD CONSTRAINT fk_disease_mappings_scope
                    FOREIGN KEY (country_code)
                    REFERENCES country_scopes (scope_code)
                    ON DELETE CASCADE;
                END IF;
            END $$;
        """))


async def ensure_disease_learning_suggestions_schema(db: AsyncSession) -> None:
    """Ensure disease_learning_suggestions table and required columns/indexes exist."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS disease_learning_suggestions (
            id SERIAL PRIMARY KEY,
            country_code VARCHAR(10) NOT NULL,
            local_name VARCHAR(500) NOT NULL,
            source_url TEXT,
            context TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            suggested_disease_id VARCHAR(100),
            suggested_standard_name VARCHAR(200),
            ai_confidence FLOAT,
            ai_reasoning TEXT,
            status VARCHAR(20) DEFAULT 'pending',
            reviewed_by VARCHAR(100),
            reviewed_at TIMESTAMP WITH TIME ZONE,
            review_notes TEXT,
            final_disease_id VARCHAR(100),
            final_mapping_id INTEGER,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))

    alter_statements = [
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS occurrence_count INTEGER DEFAULT 1",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS source_url TEXT",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS context TEXT",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS suggested_disease_id VARCHAR(100)",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS suggested_standard_name VARCHAR(200)",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS ai_confidence FLOAT",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS ai_reasoning TEXT",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending'",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(100)",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS review_notes TEXT",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS final_disease_id VARCHAR(100)",
        "ALTER TABLE disease_learning_suggestions ADD COLUMN IF NOT EXISTS final_mapping_id INTEGER",
    ]

    for statement in alter_statements:
        await db.execute(text(statement))

    # Add FK only when countries table is present (helps fresh/partial databases).
    await db.execute(text("""
        DO $$
        BEGIN
            IF to_regclass('public.countries') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                   FROM pg_constraint
                   WHERE conname = 'fk_learning_suggestions_country'
               ) THEN
                ALTER TABLE disease_learning_suggestions
                ADD CONSTRAINT fk_learning_suggestions_country
                FOREIGN KEY (country_code)
                REFERENCES countries (code)
                ON DELETE CASCADE;
            END IF;
        END $$;
    """))

    # De-duplicate old rows first so unique index can be created safely.
    await db.execute(text("""
        DELETE FROM disease_learning_suggestions a
        USING disease_learning_suggestions b
        WHERE a.id < b.id
          AND a.country_code = b.country_code
          AND a.local_name = b.local_name
    """))

    await db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_unique_country_local "
        "ON disease_learning_suggestions (country_code, local_name)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_learning_country "
        "ON disease_learning_suggestions (country_code)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_learning_status "
        "ON disease_learning_suggestions (status)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_learning_occurrence "
        "ON disease_learning_suggestions (occurrence_count DESC)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_learning_confidence "
        "ON disease_learning_suggestions (ai_confidence DESC)"
    ))


async def ensure_disease_knowledge_source_schema(db: AsyncSession) -> None:
    """Ensure the disease knowledge source table can store resolved URLs and parsed page text."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS disease_knowledge_sources (
            id SERIAL PRIMARY KEY,
            disease_id VARCHAR(100) NOT NULL,
            source_type VARCHAR(40) NOT NULL,
            source_name VARCHAR(120) NOT NULL,
            url VARCHAR(1000) NOT NULL,
            resolved_url VARCHAR(1000),
            title VARCHAR(500),
            license VARCHAR(200),
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            language VARCHAR(20) NOT NULL DEFAULT 'en',
            raw_excerpt TEXT,
            content_text TEXT,
            content_sections JSON NOT NULL DEFAULT '[]'::json,
            raw_excerpt_hash VARCHAR(64),
            fetched_at TIMESTAMP WITH TIME ZONE,
            review_status VARCHAR(30) NOT NULL DEFAULT 'pending',
            metadata JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_disease_knowledge_source UNIQUE (disease_id, source_type, url)
        )
    """))

    alter_statements = [
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS resolved_url VARCHAR(1000)",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS content_text TEXT",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS content_sections JSON DEFAULT '[]'::json",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'active'",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS language VARCHAR(20) DEFAULT 'en'",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS raw_excerpt_hash VARCHAR(64)",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS review_status VARCHAR(30) DEFAULT 'pending'",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS metadata JSON DEFAULT '{}'::json",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE disease_knowledge_sources ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
    ]

    for statement in alter_statements:
        await db.execute(text(statement))

    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_source_disease ON disease_knowledge_sources (disease_id)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_source_type ON disease_knowledge_sources (source_type)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_source_review ON disease_knowledge_sources (review_status)"
    ))


async def ensure_disease_knowledge_schema(db: AsyncSession) -> None:
    """Ensure the disease knowledge brief schema supports structured academic fields."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS disease_knowledge_briefs (
            id SERIAL PRIMARY KEY,
            disease_id VARCHAR(100) NOT NULL,
            language VARCHAR(20) NOT NULL,
            brief TEXT NOT NULL,
            definition TEXT,
            clinical_features TEXT,
            epidemiology TEXT,
            transmission TEXT,
            prevention TEXT,
            surveillance_note TEXT,
            clinical_summary TEXT,
            risk_groups TEXT,
            source_ids JSON NOT NULL DEFAULT '[]'::json,
            source_attribution JSON NOT NULL DEFAULT '[]'::json,
            disclaimer TEXT,
            model VARCHAR(120),
            status VARCHAR(30) NOT NULL DEFAULT 'draft',
            source_confidence VARCHAR(30) NOT NULL DEFAULT 'medium',
            quality_score FLOAT,
            review_notes TEXT,
            metadata JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_disease_knowledge_brief UNIQUE (disease_id, language)
        )
    """))

    alter_statements = [
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS definition TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS clinical_features TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS epidemiology TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS surveillance_note TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS clinical_summary TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS risk_groups TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS source_ids JSON NOT NULL DEFAULT '[]'::json",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS source_attribution JSON NOT NULL DEFAULT '[]'::json",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS disclaimer TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS model VARCHAR(120)",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'draft'",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS source_confidence VARCHAR(30) DEFAULT 'medium'",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS quality_score FLOAT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS review_notes TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS metadata JSON DEFAULT '{}'::json",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
    ]

    for statement in alter_statements:
        await db.execute(text(statement))

    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_brief_disease ON disease_knowledge_briefs (disease_id)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_brief_language ON disease_knowledge_briefs (language)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_brief_status ON disease_knowledge_briefs (status)"
    ))


async def ensure_task_type_enum_schema(db: AsyncSession) -> None:
    """Ensure the task_type enum knows about newly added task kinds."""
    enum_type = getattr(getattr(Task.__table__.c.task_type, "type", None), "name", None) or "tasktype"
    bind = getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect_name and dialect_name != "postgresql":
        return

    changed = False
    seen_labels: set[str] = set()
    labels: list[str] = []
    for value in TaskType:
        # SQLAlchemy's default Enum(TaskType) stores enum member names, but this
        # repository has historically mixed name-based and value-based labels in
        # Postgres. Keep both forms in sync so either representation can be
        # written safely during migrations and runtime task creation.
        for label in (value.name, value.value):
            if label and label not in seen_labels:
                seen_labels.add(label)
                labels.append(label)

    for label in labels:
        try:
            escaped_value = label.replace("'", "''")
            await db.execute(
                text(
                    f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{escaped_value}'"
                )
            )
            changed = True
        except Exception:
            # Ignore enum maintenance failures on non-PostgreSQL databases or
            # when the type has not been created yet.
            return

    if changed:
        # PostgreSQL requires new enum values to be committed before they can
        # be referenced in later statements within the same session.
        await db.commit()
