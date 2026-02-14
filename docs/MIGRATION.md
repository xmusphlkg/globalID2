# Migration Guide (Updated)

## Overview

This document describes how to migrate historical data from the legacy ID_CN dataset into GlobalID V2. The recommended migration tool is `full_migration_v2.py` (an async script) which replaces older, deprecated scripts.

---

## Data source

Default data location (legacy system):

/home/likangguo/globalID/ID_CN/Data/AllData/CN/

Example CSV columns (ID_CN format):

Date,YearMonthDay,YearMonth,Diseases,DiseasesCN,Cases,Deaths,Incidence,Mortality,Province,Source,URL

Sample row:

2025-06-01,2025/06/01,2025 June,Hepatitis B,乙型肝炎,105033,38,-10,-10,China,GOV Data,http://example.com/record

Notes:
- The migration scripts expect ID_CN-style CSVs. Extra columns are allowed, but some older column names (e.g. `fatality_rate`) are no longer recognized by the current model.

---

## Quick start ✅

1. Initialize the database schema and baseline data:

```bash
cd /home/likangguo/globalID/globalID2
python main.py init-database
```

2. Run the preferred migration script:

```bash
python full_migration_v2.py
```

Notes:
- `full_migration_v2.py` hard-codes the `data_dir` variable at the top; edit that path if your CSVs live elsewhere.
- Alternatively, you can run `python auto_run.py` to execute a full automated setup (init -> migrate -> export). `auto_run.py` currently calls `scripts.migrate_data.DataMigration` — if that module is absent, prefer invoking `full_migration_v2.py` directly or update `auto_run.py` to call the new script.

---

## What the migration does (details)

- Reads and concatenates all CSVs found in `data_dir`.
- Performs deduplication (drop duplicates on `Date`, `Diseases`, `Province`).
- **By default the script imports *national* rows only** — rows with `Province` values of `China`, `National`, or `全国`. This is intentional because the current `DiseaseRecord` primary key is (time, disease_id, country_id) and does not support storing multiple province-level records for the same disease/time.
- Creates missing `Disease` entries and sets:
  - `name` and `name_en` to the CSV disease name
  - `category` to `Uncategorized` (default)
  - `aliases` contains `DiseasesCN` when available
  - `metadata_` includes `name_cn` if present
- Creates or upserts `DiseaseRecord` entries with fields mapped as:
  - `cases`, `deaths` (treat `-10` as missing / 0 where appropriate)
  - `incidence_rate`, `mortality_rate` mapped from CSV columns (or None)
  - `data_source` and `metadata_['url']` preserved when present
  - `region` set to `None` for national records
- Uses batching for performance (default `batch_size = 1000` in `full_migration_v2.py`).

---

## Breaking changes & common errors ⚠️

1. Country `name_en` NOT NULL constraint
   - The schema enforces `Country.name_en` as NOT NULL. Run `python main.py init-database` before migrating so the bootstrap step creates the `China` country with `name_en='China'`.
   - If you see an IntegrityError mentioning `name_en`, initialize the DB or patch the country row to include `name_en`.

2. `fatality_rate` / old column names
   - Older CSVs or older scripts may reference `fatality_rate`. The current model expects `mortality_rate` (or computes mortality from deaths/cases). If migration fails with: `"'fatality_rate' is an invalid keyword argument for DiseaseRecord"`, either:
     - remove/rename that column in the CSV, or
     - update `full_migration_v2.py` to map `fatality_rate` → `mortality_rate` before creating `DiseaseRecord` objects.

3. Provincial data not imported
   - Current `full_migration_v2.py` filters to national rows. To import province-level data you must:
     - decide whether `DiseaseRecord` should include `region` in its primary key, or
     - store provincial rows as separate records under a different table/strategy. This requires schema changes.

4. Memory / batch tuning
   - If memory usage is high, reduce `batch_size` in `full_migration_v2.py` (e.g. 500).

5. Logs
   - Migration errors and warnings are logged to `logs/` (e.g., `logs/error_YYYY-MM-DD.log`). Check that file for row-level errors during debugging.

---

## Verifying results ✅

Quick checks after migration:

- Use the Python snippet to count DiseaseRecord entries:

```bash
python - <<'PY'
import asyncio
from src.core import init_app, get_database
from src.domain import DiseaseRecord
from sqlalchemy import select, func

async def check():
    await init_app()
    db = get_database()
    total = await db.scalar(select(func.count(DiseaseRecord.id)))
    print('Total DiseaseRecord rows:', total)

asyncio.run(check())
PY
```

- Run integration tests:

```bash
python main.py test
```

- Inspect top diseases via a SQL query or use the `DataExporter` to produce CSV/JSON for spot checks.

---

## How to adapt the script (tips) 💡

- To point `full_migration_v2.py` at a different dataset, edit the `data_dir` variable near the top of the file.
- To change deduplication or filtering behavior, modify the DataFrame preprocessing steps — e.g., remove the `national_df` filter to include provinces (but update schema or aggregation logic accordingly).
- To map extra CSV columns into `metadata_`, add mapping logic when building `DiseaseRecord` objects.

---

## Expected CLI output (example)

Migration will end with a summary similar to:

Migration completed!
  Processed: 84,523 records
  New diseases added: 6
  Failed rows: 0 (see logs/ for details)

---

last updated: February 11, 2026