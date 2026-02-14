# Database Design

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

- The project contains a generated `schema.sql` at the repository root which contains the deterministic DDL produced from the SQLAlchemy metadata (types, CREATE TABLE, CREATE INDEX).
- Important enum types (PostgreSQL):
  - `reporttype`: DAILY, WEEKLY, MONTHLY, SPECIAL
  - `reportstatus`: PENDING, GENERATING, COMPLETED, FAILED, REVIEWING, PUBLISHED

A short excerpt of the generated DDL (see full `schema.sql` for complete output):

```sql
CREATE TYPE reporttype AS ENUM ('DAILY', 'WEEKLY', 'MONTHLY', 'SPECIAL');
CREATE TYPE reportstatus AS ENUM ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED', 'REVIEWING', 'PUBLISHED');

CREATE TABLE countries (
  id SERIAL PRIMARY KEY,
  code VARCHAR(10) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  language VARCHAR(10) NOT NULL,
  crawler_config JSON NOT NULL,
  metadata JSON NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

CREATE TABLE disease_records (
  time TIMESTAMP NOT NULL,
  disease_id INTEGER NOT NULL REFERENCES diseases(id) ON DELETE CASCADE,
  country_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
  PRIMARY KEY (time, disease_id, country_id)
);
```

Notes and recommendations

- `schema.sql` is cleaned to remove internal catalog-check queries (so it is suitable for review and execution).
- Default handling: **timestamps and boolean defaults are application-level** (set by the ORM / model defaults). The generated DDL does not add `server_default` for `created_at/updated_at` or booleans — this keeps behavior consistent across environments.
- TimescaleDB & hypertable: recommended for `disease_records` in production. Example (commented in `schema.sql`):

```sql
-- CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
-- SELECT create_hypertable('disease_records', 'time', chunk_time_interval => INTERVAL '1 month');
```

- `pgvector` (optional) for native vector columns: the code currently uses JSON `embedding` as a fallback. If you enable `pgvector`, add:

```sql
-- CREATE EXTENSION IF NOT EXISTS pgvector;
-- ALTER TABLE diseases ADD COLUMN embedding vector(<dim>);
```

- JSON => JSONB: if you prefer query performance on JSON columns, consider migrating important JSON fields to `JSONB` and create GIN indexes, e.g.:

```sql
-- ALTER TABLE countries ALTER COLUMN crawler_config TYPE jsonb USING crawler_config::jsonb;
-- CREATE INDEX idx_countries_crawler_config_gin ON countries USING gin (crawler_config jsonb_path_ops);
```

Reproduce / regenerate

- Regenerate the `schema.sql` from the current models using:

```bash
python scripts/generate_schema.py
```

- The script connects using `config.database.url_sync`, runs `Base.metadata.create_all()` (to ensure types/tables exist if DB is reachable) and writes the deterministic DDL to `schema.sql`.

## Database Dashboard

The project includes an interactive web-based dashboard for viewing and monitoring the database, built with Streamlit.

### Features

The dashboard provides three main pages:

1. **Overview**: 
   - Global KPIs (monitored diseases, total records, last update, recent cases)
   - Monthly case trend visualization (epidemic curve)
   - Country-specific aggregated statistics

2. **Disease Analysis**: 
   - Deep dive into individual disease metrics
   - Cumulative cases, deaths, and case fatality rate (CFR)
   - Dual-axis time-series charts (cases + incidence rate)
   - Raw data table export

3. **Data Explorer**: 
   - Direct table browsing (disease_records, diseases, countries)
   - Configurable row limits and real-time query execution

### Prerequisites

Install the required dependencies:

```bash
./venv/bin/pip install streamlit plotly
```

Note: `plotly` is already in `requirements.txt`, but ensure `streamlit` is added if not present.

### Running the Dashboard

1. Ensure the database is running and populated with data:

```bash
docker-compose up -d  # Start PostgreSQL/TimescaleDB
```

2. Start the dashboard:

```bash
./venv/bin/streamlit run src/dashboard/app.py
```

3. Access the dashboard in your browser (typically opens automatically at `http://localhost:8501`)

### Configuration

The dashboard connects to the database using environment variables:

- `DATABASE_URL`: PostgreSQL connection string (defaults to `postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid`)

Set custom connection in `.env`:

```bash
DATABASE_URL=postgresql+asyncpg://user:password@host:port/dbname
```

### Usage Notes

- The dashboard uses async SQLAlchemy with `asyncpg` driver for efficient query execution
- Country and disease filters are dynamically populated from the database
- 'Total' disease records are used for aggregate country-level statistics
- All visualizations are interactive using Plotly (zoom, pan, hover details)

---

Last updated: February 11, 2026
