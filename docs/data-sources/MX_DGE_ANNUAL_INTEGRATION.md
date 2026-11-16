# Mexico — DGE annual morbidity (private holding)

## Stable contract and continuing official channel

- Country `MX`; canonical scope `mx_dge_annual`; ontology source `SRC_MX_DGE_ANNUAL`.
- Persisted source label: **Mexico DGE Annual Morbidity**.
- Official landing/archive: <https://epidemiologia.salud.gob.mx/anuario/html/index.html>.
- Live national discovery channel: <https://epidemiologia.salud.gob.mx/anuario/html/morbilidad_nacional.html>.
- Live incidence discovery: <https://epidemiologia.salud.gob.mx/anuario/html/incidencia_casos.html>.
- Discovery entrypoint: `src.data.crawlers.mx.discover_national_tables`; crawler `MexicoDGEAnnualCrawler`; updater `MexicoDGEAnnualUpdater`; staged connector `src.services.ingestion.countries.mx`.

Every collect fetches both live official menus again; there is no frozen 2025 upper bound. The year and sex come from the human-readable source edition option, while the option value supplies the URL path. For example `5000/5001` means 2003 female/male; `1400/1401` means 2012 female/male. Newly linked years are automatically discovered. Root manages source schedules and shared registry/configuration.

Routine staged collect defaults to a **one-day** byte-cache TTL so daily publication checks re-fetch same-URL revisions after one day. Its `cache_ttl_days` parameter can configure this boundary. The standalone historical collector and crawler retain their seven-day default; explicit offline organization replays frozen, checksum-verified artifacts without source requests and does not establish current source freshness. Every cache reuse rereads and verifies length/SHA256. Expired bytes require a successful official response; refresh errors do not silently fall back to stale data. Each staged response, including verified cached bytes, is still passed to `ArtifactWriter.write()` before parsing. Source-content blobs are immutable; fresh response metadata is replaced atomically. `force=True` bypasses freshness reuse.

## Observation grain and boundaries

National annual new-case notifications and source incidence rates remain distinct. Dates are explicit calendar-year anchors (`Date=YYYY-01-01`, `PeriodEnd=YYYY-12-31`), not January incidence. Age cells, all-age totals, female/male editions and both-sex editions are separate observations. They must never be added together. The 2002 national menu provides only the institution distribution for count totals; only its explicit national total is normalized, not institutional strata.

All published source disease categories are retained, including noninfectious morbidity. Source ICD-9/ICD-10 labels, source labels, applicable sex, age, denominator notes and per-page traceability remain auditable. ICD codes are not unique disease identities in all historical editions: label plus source code, sex, age and measure determine the holding category. Codes are `dge_<source label+ICD hash>__<sex>__<age>__cases|rate`; `DefinitionVersion` preserves the source classification contract. Source labels are never forced into reviewed canonical disease mappings.

- Counts: `Measure=case_notifications`, `Unit=count`, `ReportingBasis=notification`.
- Rates: `Measure=incidence_rate`, `Unit=per_100000`. Source footnotes can restrict the denominator to a sex or age population; rates are not recomputed against a uniform national denominator. Decimal commas and thousands separators are interpreted according to the captured cell format.
- `GeographyKey=country:MX:national`; state tables are outside this contract. Institution and monthly distributions are not mixed with annual facts.
- Explicit `0` is a reported zero. `N.A.`/`N.A` is `not_applicable`; a blank/dash is `source_missing`. Missing cells are not converted to zero.
- Source age/sex/rate dimensions are in both source category identity and `Dimensions`. `RawSHA256`, exact PDF `SourceURL`, source page and edition path identify the original cell.
- `RevisionSemantics=authoritative_revision` applies to the same source fact when it is reported again. Partial downloads/parsing never authorize withdrawing an entire stored window.
- `PublicReleaseEnabled=false`. The source is official public material; reuse/publication rights and a country public snapshot require separate review.

## Capture and offline organization

```bash
venv/bin/python scripts/imports/collect_mx_dge_annual.py --workers 4
venv/bin/python scripts/imports/collect_mx_dge_annual.py --workers 4 --offline-organize
venv/bin/python -m pytest -q tests/unit/test_mx_dge_annual.py
```

Artifacts: `data/raw/mx/dge_annual_full_history/artifacts_manifest.json`; immutable PDFs/HTML/plan files live beside it. Each manifest item includes exact source URL, byte length, SHA256 and frozen edition metadata. The CLI makes no database writes. CSV: `data/current/mx/mexico_dge_annual.csv`; complete structured coverage receipt: `data/current/mx/source_audit.json`.

Staged organization reads verified artifacts only, checks the frozen plan against both captured official menus, verifies its exact captured-PDF manifest, and performs no source requests or database writes. `MexicoDGEFetchResult.source_audit` exposes discovered/archived/parsed counts, page/row failures, requested windows, numeric/missing facts and `coverage_complete` for persistent run-stage metrics and control-panel status. Completion of a crawl is not a claim of full numerical extraction.

## Actual source limitations and fail-closed parsing

The complete linked source archive includes 1984–2025 and 176 national count/incidence PDFs across sex editions. Early incidence documents contain scanned pages. Other historical layouts differ in age bins and ICD headings; some contain malformed values or overlapping text. The parser learns numeric columns from actual cells, validates numeric grouping, rejects ambiguous cells and preserves errors per page/row. Source pages that conflict with the edition's geography/year/sex contribute no observations. Other independently verified pages remain usable.

In multiple female incidence editions, page 1 explicitly says `Población Femenina` but later pages say `Población General`. These conflicting later pages are archived and rejected; a footer mentioning a female population denominator cannot establish a female page header. Some PDFs contain footnote-only pages, which contribute no cases. The 2003 count template includes rows with `0` as both disease label and ICD; these are template scaffolding, not disease observations.

Age counts are retained only when their reported sum reconciles with the independently reported national total. A discrepancy preserves the explicit national total and records the unreconciled parsed age cells for review; it never rescales counts, fills missing values or silently repairs source typos. Exact full-archive acceptance figures are recorded below and in `source_audit.json`.

## Full archive organization acceptance (2026-11-16)

- Captured 176/176 linked PDFs, 31,079,296 PDF bytes, plus both official menus and a frozen collection plan. Every artifact length/SHA256 reverified.
- 162 PDFs yield supported facts; 99 have no recorded page/row failures. Other pages/rows remain archived and explicitly audited.
- 224,601 normalized source cells: 212,971 numeric, 459 `source_missing`, and 11,171 `not_applicable`; 43,682 distinct source-code/definition identities (43,647 distinct source codes). All 42 official source years 1984–2025 contribute supported observations.
- `failed_count=866` counts source page/row issues, not whole-country absence. `coverage_complete=false`; this is full linked-PDF capture and partial numerical extraction.
- Duplicate observation keys, invalid/nonnegative count/rate formats, wrong geography, mismatched raw hashes and public-release flags: zero. No database writes were performed by this collector.
- Real source regression: `28 passed`; acceptance: `data/current/mx/organization_acceptance.json`. Root owns actual database import and its independent acceptance.

## Database and daily update acceptance (2026-11-16)

The first database phase completed at `2026-11-16T02:05:01Z` under task `2b3ea0ca-9547-410a-9d3f-c0c247984c31`: **213,430 committed observations**, comprising **212,971 numeric values** and **459 explicit `source_missing` cells**, in **41,670 private holding series/categories**. All **179** frozen original artifacts are registered. The ordinary collect/organize/store stages completed; the store retains source-native annual measures and units without a canonical disease projection or public release.

An independent `REPEATABLE READ, READ ONLY` database transaction compared every committed source-cell identity, value, missingness status, measure/unit, dimensions, geography, original URL/SHA256, period end and private-release flag against the frozen CSV. Missing identities, unexpected identities, value/contract/provenance differences and missingness-metadata differences were all **zero**. Holding-series policy and category counts also matched. Evidence: `data/current/mx/database_numeric_import_receipt.json` and `data/current/mx/database_acceptance_numeric_phase.json` (`status=passed`).

Migration `0033_not_applicable_observations` is now applied. The final verified offline replay, task `1a8e0ae4-806d-439e-aeb8-1c825b2e6053`, completed the normal collect/organize/store stages with **224,601 committed observations**: **212,971 numeric**, **459 `source_missing`** and **11,171 `not_applicable`**, across **43,682 private holding series/categories**. Independent all-cell database acceptance passed at 10:24 CST: identity, value/contract/provenance and missing-status metadata differences are all **zero**; all **179** frozen artifacts match. Final evidence: `data/current/mx/database_import_receipt.json` and `data/current/mx/database_acceptance_independent.json`. Source numerical extraction remains partial independently of database completion (`coverage_complete=false`).

Routine collection now checks its verified byte cache against a one-day TTL. Actual-cache regression tests age a genuine source PDF receipt to 36 hours, confirm a source re-fetch under the daily default, confirm explicit two-day reuse, and verify both paths still persist the original artifact. Configurable TTL forwarding is covered separately. The full MX regression suite passed **32 tests**; this connector-only update changes neither frozen manifest/CSV bytes nor parser interpretations.
