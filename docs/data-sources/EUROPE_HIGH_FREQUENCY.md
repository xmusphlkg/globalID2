# European high-frequency sources

The November 2026 audit confirmed that annual-only country pages were not evidence that weekly/monthly upstream data was unavailable. This implementation adds national monthly PDF tables for Czechia and Estonia and typed weekly ECDC respiratory laboratory observations for 30 EU/EEA jurisdictions.

## Source contracts

| Connector | Source identity | Period | Numeric source |
| --- | --- | --- | --- |
| `ecdc_respiratory` | `SRC_{CC}_ECDC_RESPIRATORY` | Explicit ISO `YYYY-Www`, Monday through Sunday | ECDC non-sentinel, primary-care sentinel and SARI laboratory CSV files |
| `eu_monthly`, CZ | `SRC_CZ_SZU_MONTHLY` | Explicit closed calendar month | SZU national notification PDF, edition-year column only |
| `eu_monthly`, EE | `SRC_EE_TERVISEAMET_MONTHLY` | Explicit closed calendar month | Terviseamet national monthly total column, not YTD, rate or county columns |

ECDC countries: AT, BE, BG, HR, CY, CZ, DK, EE, FI, FR, DE, GR, HU, IS, IE, IT, LV, LI, LT, LU, MT, NL, NO, PL, PT, RO, SK, SI, ES, SE. The feed is respiratory laboratory surveillance; it is not a replacement for each country's multi-disease national notification system.

The laboratory indicators remain separate measures: `laboratory_tests` (count), `laboratory_detections` (count), and `test_positivity` (percent). Each dataset, surveillance origin, virus type/subtype, indicator and age stratum has an independent source code. Positive detections are not assumed to be unique patient cases. Overlapping strata must not be summed. Typed concept mappings allow public discovery under influenza, COVID-19 or RSV, while the static registry and publication validator enforce `source_observations_only` and retain the dimensions. The website's typed laboratory path must not fill ordinary case charts or use population denominators.

Monthly source codes hash the exact ICD string plus source label. Aggregate and subset rows remain separate even when they share an ICD code. Reviewed concept mappings use an explicit source ICD/label dictionary; uncertain categories remain `G_UNMAPPED_SOURCE_CATEGORIES`. The Estonian `B06,P35.0` rubella total includes congenital cases and therefore maps to the reviewed `G_RUBELLA_SPECTRUM` aggregate group; the separate congenital row maps to D168. New hepatitis A mappings use active D007 rather than its deprecated D067 duplicate. Congenital/severity subsets which compete with an existing source total remain discovery-only. Missing/dash/`nd1`/`nd2` cells are unknown; explicit numeric zero remains zero. No cumulative value is differenced into a monthly count.

## Capture and replay

`src/services/ingestion/countries/ecdc_respiratory.py` captures the official recursive Git tree plus README and three immutable Git blobs. Replay checks complete path membership, original SHA-256, Git blob SHA-1, Git tree identity, byte length and official immutable API URLs. The ECDC collector captures all countries once; the same artifacts can be organized separately for each country, avoiding repeated network requests.

Scheduled country tasks also share `data/raw/ecdc_respiratory_cache/`. A cross-process file lock coordinates captures. The official tree has a 15-minute TTL; immutable blob bytes are keyed by Git SHA-1 and checked against the fresh tree's size plus the cache receipt's SHA-256. Original retrieval timestamps are retained when bytes are reused. Each task still writes all five complete source artifacts through its own writer. Expired tree refresh failures fail the task rather than returning an old tree as a fresh capture. Four simultaneous processes are tested to require only five API requests in total. Actual warm-cache replay with network access deliberately forbidden produced all five artifacts and 3,837 AT rows; see `data/current/europe_high_frequency/shared_cache_acceptance.json`.

`src/services/ingestion/countries/eu_monthly.py` captures the real source index, actual year indexes where applicable, the exact report discovery plan and PDFs. Offline replay rediscovers links from those frozen indexes and verifies the complete captured PDF manifest. Czech January (`leden_2026`) is a single month; only January-to-later-month ranges and regional files are excluded. Partial source/parser failures are reported in the audit and never confer withdrawal authority.

Current standalone commands write local source CSVs and audit artifacts; they do not mutate a database:

```sh
venv/bin/python scripts/import_europe_high_frequency.py --country CZ --source cz_szu_monthly --start-year 2026 --end-year 2026
venv/bin/python scripts/import_europe_high_frequency.py --country EE --source ee_terviseamet_monthly --start-year 2026 --end-year 2026
venv/bin/python scripts/import_europe_high_frequency.py --country AT --source ecdc_respiratory_weekly
```

Use `--offline-organize --capture-directory data/raw/<captured-run>` to replay a saved `artifacts_manifest.json` without network access. Staged interfaces are `collect(writer, *, country_code, source, start_year, end_year, **options)` and `organize(artifacts, *, updater, source, **options)`. Updaters are `ECDCRespiratoryUpdater(country_code=...)` and `EUMonthlyUpdater(country_code=...)`. Both require registered static series and fail closed on registry coverage.

## Actual November 17 acceptance

| Capture | Real parsed observations | Verified coverage |
| --- | ---: | --- |
| CZ national monthly | 970 | January–October 2026, 97 categories per month, 10 PDFs, no parsing/download errors |
| EE national monthly | 740 | January–October 2026, 74 categories per month, 10 PDFs, no parsing/download errors |
| ECDC non-sentinel | 104,734 | Actual closed weeks across the 30 countries |
| ECDC primary-care sentinel | 47,599 | Actual closed weeks across reporting countries |
| ECDC SARI virological | 25,726 | Actual closed weeks across reporting countries |

CZ actual October Salmonella `A02` = 1,198. EE October Salmonella `A02` = 9; the source's YTD 115 and Tallinn 5 were independently checked and are not imported as the national monthly total.

Monthly receipts: `data/current/cz/cz_szu_monthly.audit.json`, `data/current/ee/ee_terviseamet_monthly.audit.json`. Raw capture manifests are under `data/raw/cz_szu_monthly/cz-20261117T013250Z/` and `data/raw/ee_terviseamet_monthly/ee-20261117T012742Z/`. Full ECDC acceptance is `data/current/europe_high_frequency/ecdc_real_parse_acceptance.json`; downloaded research CSVs and README are under `data/raw/europe_frequency_research/ecdc/`.

The final immutable ECDC staged capture is `data/raw/ecdc_respiratory_weekly/at-20261117T013547Z/artifacts_manifest.json`; although the local run is named AT, its five artifacts contain the official complete 30-country datasets. `scripts/verify_europe_high_frequency.py` replays all three final source manifests, resolves every real row through the static observation store, and checks exported source contracts. Its receipt is `data/current/europe_high_frequency/staged_registry_publication_acceptance.json`. This acceptance does not write to the database.

Real files are included as small regression fixtures under `tests/fixtures/ingestion/EU_HIGH_FREQUENCY/`. Run:

```sh
venv/bin/python -m pytest -q tests/unit/test_ecdc_respiratory.py tests/unit/test_ecdc_respiratory_cache.py tests/unit/test_eu_monthly.py tests/unit/test_europe_high_frequency_review.py
```

## Registry and publication integration

`scripts/prepare_europe_high_frequency_registry.py` generates independent additions in `configs/high_frequency_europe_ontology.json` and `configs/high_frequency_europe_reviewed_mappings.json`. The latter maps each reviewed CSV path to added rows, preserving existing mappings. The patch contains 32 sources, 1,498 static series and availability assertions: 1,327 ECDC strata and 171 national monthly categories. Initial availability says parsed upstream data awaits ingestion; the release owner should change it to available only after successful database import.

`src/services/europe_high_frequency_review.py` validates exact source, registry series/category, definition version, metric, reporting basis, unit, geography, mapping semantics and actual period bounds. National monthly totals require empty population dimensions and get `projection_dimension_key=all`; laboratory rows preserve all seven source dimensions and cannot get a case projection override. Existing reviewed-country mapping release requirements remain the release owner's responsibility.

## Coverage limits

* Acceptance establishes the actual 2026 monthly files. Historical compiled PDFs are discovered but need the same explicit month heading/table contracts; unsupported historical layouts fail closed with an audit entry. Historical Estonian XLS files are explicitly reported as unsupported, not counted as covered.
* ECDC datasets have different reporting countries and latest weeks. File push time is not every country's latest observation. The acceptance receipt records each country/dataset's actual latest source week.
* The captured ECDC README has a migration/download-pause note. Preserve that source document alongside the numeric files; it does not override the actual values and dates present in the captured files.
* This work does not enable UK England-only feeds as GB national data, convert Polish YTD bulletins into monthly incidence, or relabel Spain's currently quarterly epidemiology journal as weekly numeric surveillance. See the regional audit for those findings.
