# Add New Country Guide

This guide covers onboarding into the staged crawl pipeline, Mapping Registry v3,
and dashboard. A configured adapter is not evidence of a verified database import
or public release.

## 1. Decide The Contract First

Before writing code, lock down three things:

- the country code and display names
- the canonical source keys the system will use end to end
- the output cadence and reporting grain

Record the official endpoint, reuse terms, geographic coverage, case definition,
revision policy and zero/missing/suppression rules. Keep cumulative counts,
period counts, laboratory detections and sentinel observations separate. Verify
the source calendar and week boundaries before deriving dates.

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
Include display names, language and timezone in the country library. Set
`public_release_enabled: false` explicitly while source rights or publication
validation remain unresolved; an absent flag defaults to enabled. Configure
`source_policies` when national and baseline sources have different contracts.

## 3. Build The Crawler

Create `src/data/crawlers/<country>.py`.

The crawler should be responsible for:

- discovering available source artifacts
- downloading raw source content
- exposing raw response bytes for durable collection
- returning enough metadata for downstream processing

Practical checklist:

- expose one clear entrypoint for incremental runs
- preserve source dates exactly
- keep raw source metadata for traceability
- distinguish explicit zero values from missing data
- emit stable source labels that map cleanly to canonical scopes

The task pipeline persists source artifacts before business parsing, even when
optional legacy raw-file saving is disabled. Do not make a successful prior
cache hide a failure to persist the new source artifacts.

## 4. Build The Processor / Updater

Create `src/data/processors/<country>.py`.

The processor should:

- normalize source rows into one stable tabular shape
- preserve source codes, labels, dates, measures and geographic dimensions
- define incremental refresh and revision behavior
- expose `get_db_latest_date(...)` and `import_rows(...)` for the store strategy

New integrations can store source-series facts without a legacy
`disease_records` projection; `CanadaCNDSSAnnualUpdater` is an example. Do not
force an unreviewed source category into a canonical disease ID.

Recommended outputs:

- `data/current/<country>/...`
- optional `data/processed/<country>/...`

Use one stable persisted `data_source` label per logical source whenever possible.

## 5. Add The Staged Connector And Dispatch

Create `src/services/ingestion/countries/<country>.py` with:

- `collect(writer, ...)`: fetch the required raw responses, persist them with
  `writer.write(...)`, and return the artifacts. Preserve the source URL and
  request/coverage metadata; omit credentials.
- `organize(artifacts, updater=..., ...)`: read verified artifacts with
  `read_artifact(...)` and return normalized rows and source coverage metadata.
  Organization must work offline without source requests or database writes.

`src/services/ingestion/countries/ca_national.py` demonstrates explicit raw-byte
collection and pure parsing. Existing monthly connectors also use captured
transport replay. Persist a collection plan when parsing depends on the run's
date or requested window so replay does not depend on today's date.

In `src/services/crawl_service.py`, add the `_PIPELINES` entry, updater factory
and execution handler. The handler calls
`src/services/ingestion/runner.py:run_surveillance(...)` with the connector;
the shared runner owns `CrawlRun`, stage metrics, cancellation and storage.
Export the updater from `src/data/processors/__init__.py` if the factory imports
it there. Assign an `ontology_source_id` on the updater or add its mapping to
`CrawlService._SERIES_SOURCE_IDS`.

Use authoritative window replacement only after validating the complete source
window. A partial response or failed disease fetch must never withdraw valid
stored observations.

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
- source-series source identities
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
- `src/control_plane/operations.py`

Add the source ID to `_SERIES_SOURCE_SCOPE_OVERRIDES` in the sources router so
series-only imports appear under their canonical scope. Task creation uses
`src/services/crawl_task_service.py`; avoid country-specific validation unless
the new source contract requires it. Update `main.py` source help as needed.

Make sure:

- the country shows the correct source options
- `/sources/flow` shows task-only rows before data exists
- `/quality/sources` groups historical aliases into one label
- automation jobs round-trip canonical source keys

## 8. Add Disease Mapping Support

Register the source under `sources` in `configs/disease_ontology.json`, with a
stable `SRC_...` ID, country code, display labels and persisted source aliases.
Add `source_series` only for reviewed categories, with explicit measure,
frequency, reporting basis, case definition, mapping and missing-value policy.
Legacy mapping CSVs under `configs/mapping/` are compatibility inputs, not a
replacement for v3 source identities.

Set updater fields such as `series_country_code`, `series_geography_key`,
`series_registered_rows_only = True` and `series_registry_coverage = "required"`
as appropriate. National geography must only be assigned to verified national
observations; otherwise retain geography from source rows.

The shared store discovers categories transactionally and retains valid
unregistered facts in non-projectable holding series for review. Emit explicit
`SourceDiseaseCode`, `RawDiseaseLabel`, `DefinitionVersion`, `Measure`,
`ReportingBasis`, `Frequency` and `SourceURL`. Holding identities are based on
source category and definition version; distinct measures or reporting grains
must not share an indistinguishable category identity.

Validate the static registry with
`venv/bin/python scripts/sync_disease_ontology.py --offline`. Reviewed declared
series must also be synchronized to the database before collection; use the
script's default database preflight or `--rehearse` before applying a registry
change. Missing registry rows must fail quality checks rather than disappear.

## 9. Add Tests

Add both unit and integration coverage.

Recommended minimum:

- source alias canonicalization
- crawler parsing edge cases
- zero-vs-missing handling
- source calendar, cumulative/period counts and national/subnational boundaries
- offline collect/organize replay with fixed source bytes
- stored observation keys, values, dimensions and quality status
- revisions, duplicate conflicts and incomplete-response failure behavior
- task-only source-flow visibility
- source distribution canonicalization
- a smoke test that the country dispatch path can instantiate and run

Use `tests/unit/` for focused regressions and `tests/contract/` with fixtures in
`tests/fixtures/ingestion/<CC>/` for source observation contracts. Existing
cross-country crawl and dashboard regressions also live in `tests/test_pipeline.py`.

## 10. Verify End To End

Use this checklist before calling the country complete:

1. Run the source's unit and contract tests and inspect the registry plan.
2. Ensure the country exists in the database and the task worker is running.
3. `venv/bin/python main.py crawl --country <CC> --source <canonical-source> --no-process --no-save-raw`
4. Inspect the completed task and captured artifacts under
   `data/raw/<cc>/<task_uuid>/<attempt>/`; the command queues a task, so enqueue
   success alone is not collection success.
5. `venv/bin/python main.py crawl --country <CC> --source <canonical-source> --process --save-raw`
6. Confirm the completed task stored the expected `disease_series_observations`
   and source identities. Inspect `disease_records` only where a compatibility
   projection is implemented, and current CSV output only where produced.
7. Check `/sources/flow`, `/sources/automation` and `/quality` for labels, source
   counts, latest task and expected empty states.
8. Enable public release only after the source contract, rights and real stored
   facts are verified, then generate and inspect a real country snapshot. Add
   source descriptions in `src/generation/site_data_about.py` when publishing.

## 11. Common Pitfalls

- Mixing canonical scope keys with raw source labels.
- Treating explicit zero counts as missing data.
- Updating the crawler but forgetting dashboard source labels.
- Updating frontend source options but not backend scope normalization.
- Leaving legacy aliases in bootstrap config after introducing a canonical name.
- Returning early from source-flow queries when tasks exist but records do not.
- Parsing live source responses during the offline organization stage.
- Treating a configured pipeline or queued task as a verified public dataset.
- Combining distinct measures or definitions into one holding-series identity.

## 12. Suggested File Touch List

For most new countries, expect to touch at least:

- `src/core/country_library.py`
- `configs/country_bootstrap.json`
- `src/core/source_scopes.py`
- `src/data/crawlers/<country>.py`
- `src/data/processors/<country>.py`
- `src/services/ingestion/countries/<country>.py`
- `src/services/crawl_service.py`
- `configs/disease_ontology.json`
- `dashboard/api/routers/sources.py`
- `dashboard/src/lib/source-labels.ts`
- source unit tests and offline ingestion contract fixtures

If the country needs history import or special maintenance flows, also add:

- a dedicated script under `scripts/`
- a short operational note in `README.md` or `QUICKSTART.md`
