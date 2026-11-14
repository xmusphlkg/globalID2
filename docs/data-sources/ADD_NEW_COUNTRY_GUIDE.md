# Add New Country Guide

This guide covers the full path for onboarding a new country into the GlobalID2 crawl pipeline and dashboard.

## 1. Decide The Contract First

Before writing code, lock down three things:

- the country code and display names
- the canonical source keys the system will use end to end
- the output cadence and reporting grain

Canonical source keys matter. Pick one stable key per logical source and reuse it everywhere:

- bootstrap config
- crawl task input
- automation jobs
- dashboard filters
- source-flow expected scopes

If you need to support old names or external aliases, add them in `src/core/source_scopes.py` instead of letting multiple spellings leak into storage and UI.

## 2. Register Country Metadata

Update both bootstrap registries:

- `src/core/country_library.py`
- `configs/country_bootstrap.json`

Add:

- `data_source_url`
- `data_source_type`
- `crawler_config.sources`
- `parser_config.primary`
- `disease_mapping_rules`
- `report_config`

Keep `crawler_config.sources` aligned with the canonical keys from `src/core/source_scopes.py`.

## 3. Build The Crawler

Create `src/data/crawlers/<country>.py`.

The crawler should be responsible for:

- discovering available source artifacts
- downloading raw source content
- writing optional raw archives under `data/raw/<country>/`
- returning enough metadata for downstream processing

Practical checklist:

- expose one clear entrypoint for incremental runs
- preserve source dates exactly
- keep raw source metadata for traceability
- distinguish explicit zero values from missing data
- emit stable source labels that map cleanly to canonical scopes

## 4. Build The Processor / Updater

Create `src/data/processors/<country>.py`.

The processor should:

- normalize source rows into one stable tabular shape
- map local disease names to internal diseases
- gate incremental imports
- upsert into `disease_records`

Recommended outputs:

- `data/current/<country>/...`
- optional `data/processed/<country>/...`

Use one stable persisted `data_source` label per logical source whenever possible.

## 5. Wire The Crawl Service

Add the country branch in `src/services/crawl_service.py`.

You usually need:

- a country-specific execution method
- workbook logging for phase 1 / 2 / 3
- `CrawlRun` creation
- raw archive hints
- import summary bookkeeping

If the country has a single real source, keep the source handling simple, but still use the canonical key in task payloads and dashboard labels.

## 6. Wire Task + Automation Source Taxonomy

Update `src/core/source_scopes.py`:

- `EXPECTED_SCOPES_BY_COUNTRY`
- `scope_from_data_source(...)`
- `canonicalize_task_source(...)`
- `scope_display_label(...)`
- `canonical_data_source_label(...)`

This file is the shared contract between:

- stored `disease_records.data_source`
- task input payloads
- automation jobs
- `/sources/flow`
- `/quality/sources`

If you skip this step, the dashboard will drift into duplicate source labels or missing expected rows.

## 7. Wire Dashboard Labels And Filters

Update frontend labels in:

- `dashboard/src/lib/source-labels.ts`

Check backend routes that expose source information:

- `dashboard/api/routers/sources.py`
- `dashboard/api/routers/quality.py`
- `dashboard/api/routers/crawl.py`

Make sure:

- the country shows the correct source options
- `/sources/flow` shows task-only rows before data exists
- `/quality/sources` groups historical aliases into one label
- automation jobs round-trip canonical source keys

## 8. Add Disease Mapping Support

At minimum, verify:

- disease names from the new source can map to internal diseases
- unmapped values surface clearly for follow-up

Relevant places:

- `configs/mapping/`
- `disease_mappings`
- country-specific import scripts if you have historical backfills

## 9. Add Tests

Add both unit and integration coverage.

Recommended minimum:

- source alias canonicalization
- crawler parsing edge cases
- zero-vs-missing handling
- task-only source-flow visibility
- source distribution canonicalization
- a smoke test that the country dispatch path can instantiate and run

`tests/test_pipeline.py` is the current home for cross-country crawl and dashboard regressions.

## 10. Verify End To End

Use this checklist before calling the country complete:

1. `python main.py crawl --country <CC> --source <canonical-source> --no-process --no-save-raw`
2. `python main.py crawl --country <CC> --source <canonical-source> --process --save-raw`
3. confirm raw artifacts landed under `data/raw/<cc>/`
4. confirm current output landed under `data/current/<cc>/`
5. confirm `disease_records` contains the expected `data_source`
6. open dashboard pages:
7. `/sources/flow`
8. `/sources/automation`
9. `/quality`
10. confirm labels, counts, latest task, and expected empty states all look right

## 11. Common Pitfalls

- Mixing canonical scope keys with raw source labels.
- Treating explicit zero counts as missing data.
- Updating the crawler but forgetting dashboard source labels.
- Updating frontend source options but not backend scope normalization.
- Leaving legacy aliases in bootstrap config after introducing a canonical name.
- Returning early from source-flow queries when tasks exist but records do not.

## 12. Suggested File Touch List

For most new countries, expect to touch at least:

- `src/core/country_library.py`
- `configs/country_bootstrap.json`
- `src/core/source_scopes.py`
- `src/data/crawlers/<country>.py`
- `src/data/processors/<country>.py`
- `src/services/crawl_service.py`
- `dashboard/api/routers/crawl.py`
- `dashboard/api/routers/sources.py`
- `dashboard/api/routers/quality.py`
- `dashboard/src/lib/source-labels.ts`
- `tests/test_pipeline.py`

If the country needs history import or special maintenance flows, also add:

- a dedicated script under `scripts/`
- a short operational note in `README.md` or `QUICKSTART.md`
