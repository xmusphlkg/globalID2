# Database Design (Updated)

This document describes the current GlobalID V2 database schema. It reflects the SQLAlchemy domain models in `src/domain/` and the intended runtime types.

## Conventions
- All domain models inherit from `BaseModel` which provides:
  - `id` (Integer, primary key, autoincrement)
  - `created_at`, `updated_at` timestamps
- JSON fields use PostgreSQL JSON/JSONB via SQLAlchemy `Column(JSON)`.

## Core Tables (models)

### `countries` (Country)
- Purpose: store per-country configuration, crawler/parser settings, and metadata.
- Primary key: `id` (Integer)
- Important columns:
  - `code` (String, unique) — country code (e.g. "CN")
  - `name`, `name_en`, `name_local` — display names
  - `language`, `timezone`
  - `crawler_config`, `parser_config` (JSON) — crawler-specific settings
  - `disease_mapping_rules` (JSON) — mapping and normalization rules
  - `report_config` (JSON)
  - `is_active` (Boolean)
  - `metadata` (JSON), `notes` (Text)

Example (SQLAlchemy model snippet):
```
class Country(BaseModel):
    code = Column(String(10), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    crawler_config = Column(JSON, default=dict)
    # ...
```

### `diseases` (Disease)
- Purpose: canonical disease registry, aliases, category and optional embeddings.
- Primary key: `id` (Integer)
- Important columns:
  - `name` (String, unique) — canonical disease name
  - `name_en`, `category`
  - `icd_10`, `icd_11` (String)
  - `aliases`, `keywords` (JSON arrays)
  - `description`, `symptoms`, `transmission` (Text)
  - `embedding` (JSON) — stored embedding vector(s) when `pgvector` is not available
  - `metadata` (JSON), `is_active` (Boolean)

Notes:
- `name` is unique to support lookups by disease label.
- If `pgvector` is enabled in the DB image, a real vector column may be used instead; current code uses JSON fallback.

### `disease_records` (DiseaseRecord)
- Purpose: time-series disease monitoring records (intended to be stored as a timeseries/hypertable).
- Primary key: composite `(time, disease_id, country_id)` via SQLAlchemy mapped columns.
- Important columns:
  - `time` (DateTime) — observation timestamp
  - `disease_id` (FK -> diseases.id)
  - `country_id` (FK -> countries.id)
  - `cases`, `deaths`, `recoveries`, `active_cases` (Integer)
  - `new_cases`, `new_deaths`, `new_recoveries` (Integer)
  - `incidence_rate`, `mortality_rate`, `recovery_rate` (Float)
  - `region`, `city` (String) — optional geographical granularity
  - `data_source`, `data_quality`, `confidence_score` (String/Float)
  - `raw_data`, `metadata` (JSON)

Indexes and performance:
- The model defines indexes on `time`, `disease_id`, `country_id`, `region` and a composite index `(time, disease_id, country_id)`.
- In production, the table is intended to be converted to a TimescaleDB hypertable for chunking/compression.

### `reports` and `report_sections` (Report, ReportSection)
- `reports` stores report metadata and publish information.
  - `title`, `report_type` (enum), `status` (enum), `country_id` (FK), `period_start`, `period_end`
  - generation / AI metadata: `ai_model_used`, `token_usage`, `generation_time`
  - file paths: `html_path`, `pdf_path`, `markdown_path`

- `report_sections` stores ordered sections for a `Report`.
  - `report_id` (FK), `section_type`, `section_order`, `title`, `content` (Markdown)
  - AI generation metadata: `prompt_used`, `ai_model`, `token_count`, `generation_time`
  - `data_sources`, `charts`, `tables` (JSON)

### Relationship summary
- `Country` 1–* `DiseaseRecord`
- `Disease` 1–* `DiseaseRecord`
- `Country` 1–* `Report`
- `Report` 1–* `ReportSection`

## Other supporting tables (present or planned)
- `ai_interactions`: logs AI requests/responses, token usage and latency (hypertable suggested).
- `validation_results`: stores validation outcomes for generated sections.
- `human_review_queue`: tasks for manual human review.

## Migration and Import notes
- The project includes `full_migration_v2.py` (preferred) for importing legacy CSVs. The older `scripts/migrate_data.py` was removed.
- Importers must map legacy country codes to `countries.id` and ensure `diseases` are registered before inserting `disease_records`.

## Recommended SQL snippets

Create `disease_records` hypertable (Timescale):
```sql
-- after creating table with columns matching models
SELECT create_hypertable('disease_records', 'time', chunk_time_interval => INTERVAL '1 month');
```

Query example — latest date per country:
```sql
SELECT country_id, MAX(time) AS latest
FROM disease_records
GROUP BY country_id;
```

Aggregate example — monthly totals for a country (Total row uses disease.name='Total'):
```sql
SELECT date_trunc('month', time) AS month, SUM(cases) AS cases
FROM disease_records r
JOIN diseases d ON r.disease_id = d.id
WHERE r.country_id = :country_id AND d.name = 'Total'
GROUP BY month
ORDER BY month;
```

## Notes and rationale
- Using `id` as integer primary keys simplifies ORM relationship handling and migration.
- JSON columns are used where schema-flexibility is required (crawler configs, embeddings fallback, raw data).
- TimescaleDB integration is recommended for scalable time-series storage and compression policies.

If you want, I can:
- add SQL DDL examples that exactly match current SQLAlchemy `metadata.create_all()` output,
- or generate a `schema.sql` dump from the current models.

---
Last updated: February 11, 2026
