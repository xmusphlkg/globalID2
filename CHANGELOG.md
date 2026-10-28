# Changelog

This file records release-level changes to the GIDS application and its data operations. Public-facing multilingual notes are also available in the website Changelog.

## [0.15.0] - 2026-10-28

### Added

- Rebuilt the public Situation module on official authority notices. `/situation/` shows the current peer-reviewed weekly brief, active events, and a weekly notice timeline. Every brief point links to the notices it cites.
- `/situation/weekly/[period]/` and `/situation/monthly/[period]/` keep their URLs and now hold weekly briefs (monthly pages group the briefs of a month). English, Chinese, and French routes are unchanged.
- Control panel: the Events page reads the new `/api/v1/situation/*` endpoints (overview, notices with AI assessments and review notes, events, digests, source runs). It can queue a full, no-fetch, or metadata-only run and re-queue one notice for AI. The source-flow page shows per-feed run history.

### Changed

- Data releases, the `refresh_situation_sources` task, and the CLI share one runner (`src/services/situation_notices/runner.py`, `scripts/run_situation_notices.py`). The release gate validates the notice export and built pages.
- Research Radar links literature to Situation events from the new tables.
- Rewrote the methodology page, home page strip, header visibility, and sitemap to use the notice export.

### Removed

- Retired the statistical Situation Room (v2/v3 screen, history database, calibration, backtest, review API, alert dispatch, and the manual release workflow). Migration `0020_drop_legacy_situation` drops its 14 tables.

### Validation

- Python suite: 1,456 passed. Dashboard: `tsc` clean, 10 Vitest tests pass. Astro: style and i18n checks pass, 8 TS tests pass, production build 6,355 pages. Release gate passes on the new export.

## [0.14.0] - 2026-10-28

### Added

- Notice database (migration `0019_situation_notices`): notices, mapped entities, AI assessments, events, weekly digests, and per-source run health.
- Africa CDC adapter (official WordPress REST API: outbreak statements and Epidemic Intelligence Weekly Reports; French duplicates dropped) and PAHO adapter (IRIS DSpace search API, because www.paho.org rejects non-browser clients).
- Peer-reviewed AI enrichment through the Model Center: an author model drafts Chinese/French titles, summaries, event type, risk level, and disease/country ids; a reviewer pass accepts, revises (one more round), or rejects. Guardrails restrict ids to the GIDS catalogue and drop digest points without citations. Each export records author, reviewer, and `review_mode` (`single_model` while only one route is configured).
- Disease, country, and country-disease pages show authority notices; country pages show them without requiring a download entry; country-disease pages filter to the page disease.

### Changed

- Chart PNG exports print trilingual disease names and wrap long titles (up to six lines).

### Fixed

- Country abbreviations (DRC, UK, USA) were skipped by a length threshold.
- SARS-CoV-2 no longer matches SARS (D003); hantavirus notices map to D102.

## [0.13.7] - 2026-10-26

### Fixed

- Chart PNG downloads attach the download link to the document before clicking it. Browsers that ignore a detached anchor (Safari and iOS in particular) had saved nothing.
- The surveillance explorer shares a configuration once. It no longer builds and renders the shared link twice for one click.

### Changed

- The QR attribution used by PNG export is imported only when an export starts, so it stays out of the initial chart JavaScript chunk.
- Compare charts no longer offer a per-chart CSV button. Their data action opens the disease page's published-file dialog, so every data download goes through the same download dialog.
- Removed the unused CSV helpers from the shared chart export module.

### Validation

- `astro check`: 0 errors, 0 warnings across 277 files; style boundaries and trilingual i18n checks pass.
- Unit tests: chart, SEO, and performance-budget suites pass (56 + 27 + 10 tests).
- Production build: 6,295 pages; largest JavaScript chunk 108,579 gzip bytes against a 350,000 byte cap; largest chart route 384,255 gzip bytes against a 395,000 byte cap; average page 17,083 gzip bytes.

## [0.13.5] - 2026-10-23

### Added

- Added a Compare page in English, Chinese, and French. It lines up at most two diseases and four countries, keeps each source period and case definition, and can share the current selection or download a PNG or CSV.
- Disease pages now open their reporting footprint on a world map. Sub-national series roll up to the parent country. Hong Kong and Taiwan, which have no polygon in the bundled map, stay visible as markers.
- Disease pages add a year-by-month seasonality view for one national monthly series at a time. Empty cells mean the source did not publish a value.

### Changed

- Stopped publishing a cross-country monthly subtotal on disease snapshots. `global_monthly`, disease-level `total_cases` and `total_deaths`, and `aggregation_scope` are removed from new site exports and stripped from older snapshots during an incremental export. Per-series totals are unchanged.
- Research disease pages show the publication timeline and link to the surveillance page. They no longer place a summed case series beside publication counts.
- The home page reports jurisdictions, diseases, sources, and analyses. It no longer adds case or record totals across sources.

### Validation

- Site data view tests: 16 passed.
- Local Astro dev, checked in Chromium: COVID-19 coverage map with territory markers, zoom, and the case scale; Chinese influenza seasonality with a country change; French dengue map; English, Chinese, and French compare pages, including a shared country query and CSV download; mobile navigation to Compare.
- Production build performance budget, checked against the release dist: the disease route is 381,198 gzip bytes under a 395,000 byte chart-route cap. Compare is classified with the other chart pages. The largest ordinary page is 23,556 gzip bytes.
- Compare pages keep the single `<main>` landmark from the site layout.

## [0.13.6] - 2026-10-24

### Changed

- Refined the comparison, disease, and research interfaces for clearer navigation, chart controls, and mobile layouts.
- Research Radar guidance and policy evidence now requires a title-confirmed match to the disease page; guidance-related records are labeled without implying an official policy date.

### Data

- Published 79 changed files to `globalID2_data_download` `main` for 295 jurisdictions and 239 diseases; GitHub manifest and sample partitions verified at `5d621777a`.

## [0.13.4] - 2026-10-21

### Changed

- The browser copy of the research catalogue is written as compact JSON chunks of at most 8 MiB under `public/site-data/research`. The build input `src/data/research/index.json` stays one document.
- Direct-download export workers now follow available memory. A 16 GiB host uses 6 workers.

### Data

- Published `globalID2_data_download` `main` at `5569a7aa3` via the direct `192.168.30.1` path to GitHub's Asia edge. The catalogue lists 295 jurisdictions and 239 diseases. The publish tables held 15,545 series and 4,463,205 points. GitHub accepted the push and warned that some disease partitions exceed 50 MiB.

## [0.13.3] - 2026-10-21

### Added

- Added `public_disease_series` and `country_publish_state` as the current public curve read model. Raw observations remain the system of record, and a successful crawl refreshes that country's published rows.

### Changed

- Current site exports read the publish tables and rewrite a country curve only when its `content_hash` differs from the local snapshot. Unchanged download partitions are reused by file size. Historical `recorded_at_as_of` exports continue to read observation history.

### Validation

- Backfill refreshed 295 active countries at `public-series-v1`: 15,545 series rows and 4,463,198 points, with no failed countries.
- A current export compared all 295 snapshots, found 0 curve changes, and wrote `publish-hashes.json`. Sampled SG, CN, JP-13, US-NY, and DE hashes matched the publish table.
- Publish-table unit tests: 21 passed.

## [0.13.2] - 2026-10-21

### Fixed

- Corrected Research Radar disease alias IDs for HIV, hepatitis B/C/D/E, and influenza subtypes; removed vaccine names that had been treated as pertussis aliases.
- Expanded title association matching to use curated aliases and ontology legacy labels, normalize accents and punctuation, prefer specific mentions, and ignore common negated mentions.
- Added regression coverage for disease alias IDs, SARS-CoV-2 specificity, influenza subtypes, and negated HIV mentions.

### Validation

- Regenerated Research Radar data: 8,349 title-estimated records, 152 added disease links, and 754 topic links.
- Literature Radar unit tests: 63 passed.

## [0.13.1] - 2026-10-20

### Changed

- Retained review records that are ineligible for detailed AI summaries as compact bibliographic references in the existing Research Radar export. Disease and public-health topic links are estimated from title mentions and explicitly identified as estimates; abstracts and full-text content remain excluded, and no per-record pages are generated.
- Added estimated related-record and title-link counts to the existing Research Radar overview, separate from the verified public catalogue metrics.

## [0.13.0] - 2026-10-18

### Added

- Added request-level AI model call telemetry for production attempts and model health checks, including provider/model identity, agent, outcome, latency, and sanitized error details; prompts and generated responses are not retained.
- Added a paginated and filterable AI model call-log API and a live-updating call-log panel in the AI Model Center.
- Added the `ai_model_call_logs` schema migration and subdivision geography fields for location type and parent jurisdiction.

### Changed

- Improved Chinese provincial report parsing to recognize subtotal labels and additional no-case narrative forms; expanded Hunan archive-index coverage and matching aliases for provincial disease labels.
- Refreshed the 2026-W42 weekly AI review fingerprint and review timestamp to match the reviewed brief.

### Validation

- Added unit coverage for metadata-only AI call logging and secret redaction.

## [0.12.1] - 2026-10-18

### Fixed

- Corrected Germany SurvStat state exports to apply the hierarchical state geography filter before downloading, preventing state requests from silently returning national observations.
- Added geography, dimension, reference, and checksum metadata to archived German state exports so each raw payload can be traced and validated.
- Kept German pathogen category identifiers in source-series identity rather than analytical dimensions, allowing reviewed case-count mappings to reach standard disease projections.

### Data

- Refreshed and normalized the 16 German state series from official SurvStat exports, restoring their standard disease projections for state detail pages and trend charts.

## [0.12.0] - 2026-10-16

### Added

- Added 26 disease and surveillance concepts required to preserve source-specific mappings across Australia, Brazil, China, Hong Kong, South Korea, Taiwan, and Ontario; added separate concepts for community-associated MRSA infection, enterovirus 71 infection, and food-poisoning outbreaks.
- Added reviewed source-category decisions for 256 previously unresolved categories, including explicit no-projection dispositions for aggregate totals, residual or mixed categories, and Candida auris colonization/unspecified reports.

### Changed

- Completed the active source-category mapping review: all 4,802 active registry categories now have an approved mapping assertion. Canonical concepts project only where the source semantics support comparison; reviewed groups remain source-only and do not appear as canonical disease curves.
- Reduced disease-page HTML by previewing up to 12 official source records and linking to the full source register in the dataset JSON; full source provenance remains available for download.
- Expanded the ontology release to `2026.10.16.2` and published full mapping snapshot `DMR-COVERAGE-20261016T154733073582Z`.

### Validation

- The mapping release contains 4,802 approved assertions and has checksum `e84c48bbadd23be48590b1b400613d7dc2a4045edb2fe5ce10a5fb994926819c`.
- Rehearsed the final import with 195 new assertions, 0 unmatched categories, and 0 conflicts; the separate 61-row import also had 0 unmatched categories and 0 conflicts.
- Preserved 30 retired New Zealand synthetic categories outside the active mapping set; all active categories have a reviewed disposition.

## [0.11.0] - 2026-10-16

### Added

- Added source-aware subnational surveillance ingestion for Japan (47 prefectures), Sweden (21 counties), Germany (16 states), Switzerland (26 cantons), the United States (56 states, districts, and territories), and New Zealand (19 district health boards).
- Imported historical regional observations across all six countries, including Japan's prefecture series back to 2015, Germany's state series back to 2001, Switzerland's canton series back to 2013, and New Zealand's annual DHB series for 2006–2025.
- Added CDC NNDSS state and territory data from the current Socrata feed and archived CDC weekly tables from 2014–2021. Explicitly blank current-week cells are retained as unknown (`NULL`) observations with source flags and provenance; reported zeroes remain zero.
- Added a shared subdivision registry that supplies stable jurisdiction codes, parent-country relationships, source labels, geographic identities, and country profiles to ingestion and public data queries.
- Enabled incremental crawl routing for registered subdivisions through the existing country pipelines. The six parent-country schedules remain enabled, so new regional observations flow through the normal crawl, database, and release workflow.
- Added an annual New Zealand DHB pipeline and the database migration that permits explicitly marked source-missing observations while continuing to reject unclassified null values.
- Added 12 Japanese disease concepts for JIHS categories that previously had no exact ontology target, including sentinel-only infections and syndrome-specific hantavirus surveillance.
- Reviewed 168 additional current and historical US NNDSS categories. Each now has an approved disease target or an explicit source-only disposition when the metric or disease identity cannot safely project to a canonical curve.

### Changed

- Expanded Japan, Sweden, Germany, Switzerland, United States, and New Zealand source adapters and processors to retain jurisdiction-specific geography and source provenance without overwriting national series.
- Batched PostgreSQL observation and revision upserts with executemany to keep large historical imports within practical query and memory limits.
- Batched Situation Room source-history queries so releases remain below PostgreSQL's bind-parameter limit as regional series counts grow.
- Preserved unmapped source categories in holding series so source facts remain available for later disease-mapping review rather than being dropped.
- Published versioned source-category mappings in the site export, preserving sentinel versus all-case reporting and keeping non-comparable source categories outside canonical curves.
- Corrected Swiss FOPH CHFL observations and revision identities so Switzerland-and-Liechtenstein aggregates are no longer assigned to Switzerland-only geography; retained the Swiss-only legacy records.
- Inherited mappings for label-only source categories only when same-source, same-label source-series definitions agree on one exact disease target; release-pinned and site-export queries now resolve those categories with exact-code priority, and source-current placeholder reconciliation runs during mapping bootstrap under the release lock.
- Expanded Chinese provincial monthly-report retrieval for Guangxi, Heilongjiang, Jiangxi, and Qinghai, including explicit disease counts in article text and embedded or attached tables; added province-level HIV infection as a distinct category from AIDS.

### Validation

- Imported 2.85 million Japanese prefecture observations, 893,139 German state observations, 147,576 Swedish county observations, 2,574 Swiss canton observations, and 17,100 New Zealand DHB observations.
- Completed the US NNDSS source-missing backfill with 1,523,825 distinct missing observations across 58 reporting scopes; source-label aliases that resolve to an already represented canonical observation are deduplicated, and no non-null count was overwritten by a source blank.
- Confirmed enabled scheduled crawl jobs for all six parent countries and successful read/write access checks for the data repositories and Cloudflare Pages integration.
- Activated a reviewed mapping release with 2,573 assertions; all 5,809,371 stored observations across the six countries now have an explicit mapping disposition, including 5,483,077 canonical projections and 326,294 source-only/no-projection observations.
- Added 70 unambiguous reviewed source-series and source-identity assertions in mapping release `DMR-SOURCE-COVERAGE-20261016T152842267458Z`; preserved the one conflicting US NHSS HIV category for manual review.
- Verified the CHFL repair moved 419 observation identities and 213 revision identities with no key collisions and no changes to reported values; the migration audit supports exact restoration.
- Configured and verified 19 staggered New Zealand DHB refresh jobs in `Pacific/Auckland`.
- Imported 124 explicit September 2026 reports from Guangxi (55), Heilongjiang (48), and Qinghai (21); Jiangxi had no matching September report and no zeroes were synthesized.
- Passed 52 targeted subdivision and series-storage tests plus 19 US NNDSS crawler tests; three environment-dependent crawler tests were skipped.
- Refreshed the Situation Room against the expanded database after splitting qualified source histories into bounded queries.

## [0.10.6] - 2026-10-13

### Changed

- Rebuild only changed Research Radar article pages and social cards during data releases; automatically rebuild all articles when shared templates or assets change.
- Split the browser research catalogue into bounded JSON chunks, so catalogue growth no longer creates one ever-growing static file.
- Read site and database dates from small current release artifacts, allowing unchanged article pages to be reused safely.
- Literature-triggered releases skip raw crawler archive and download repository publication while retaining existing GitHub download links.

### Validation

- Verified full and incremental production builds, catalogue integrity, SEO, localization, and the published release.

## [0.10.5] - 2026-10-12

### Fixed

- Constrained the surveillance data table to a scrollable panel with a sticky header so long time series no longer stretch the page.

## [0.10.4] - 2026-10-12

### Changed

- Rebuilt surveillance curves around one query, chart, and table flow across country, disease, report, and research views.
- Matched the curve controls and full-screen view to the site chart design; compacted the series picker and kept file downloads in the page's data access section.
- Added conservative date-pattern frequency hints for legacy series whose source period is not declared, with the inference marked separately from source metadata.

### Fixed

- Preserved source-specific observations, report snapshots, and selected time windows across the shared curve views.
- Kept sparse and mixed-date series unspecified instead of assigning an unsupported reporting frequency.

### Validation

- Passed 1,484 Python unit tests, 56 chart tests, full/compact surveillance projection checks, desktop and mobile browser regressions, and a 5,680-page production build with style, performance, SEO, and localization checks.

## [0.10.3] - 2026-10-06

### Added

- Added historical Australian state and territory disease series and Brazilian state and Federal District notification series, enabling jurisdiction-level comparisons alongside national curves.
- Added dynamic refresh of official population denominators from China's National Bureau of Statistics, the Australian Bureau of Statistics, and Brazil's IBGE, including subnational populations where available.
- Added monthly population refresh and weekly Brazilian state-case refresh services.

### Changed

- Simplified epidemic-curve geography selection and comparison so users can compare all Chinese provinces for one disease, or compare a Chinese province with other countries and supported subnational jurisdictions.
- Incidence-rate curves now use each jurisdiction's matching official population by year and retain missing rates when a denominator is unavailable.
- Research Radar reconciliation now uses bounded keyset queries and smaller projections; provider evidence payloads are versioned and compacted in resumable batches.
- Literature classification avoids rewriting unchanged evidence, reducing database churn during duplicate-heavy crawls.

### Fixed

- Brazilian state case counts now use residence geography, combine overlapping annual-source contributions without double-counting duplicate files, and retain source provenance.
- Preserved national Brazilian totals while exporting state-level series and local-population rates.
- Prevented concurrent literature maintenance from overwriting newer provider metadata and kept reconciliation memory use bounded while disease crawls run.

## [0.10.2] - 2026-10-05

### Changed

- Shared process locking and database cleanup across literature metadata backfill, PubMed abstract backfill, and payload compaction commands; database pools now close before the command's event loop exits.
- Consolidated metadata-backfill checkpoint serialization and cache command error handling to keep failure behavior consistent across execution paths.
- AI request admission now counts retries and fallback-model attempts individually while cache hits consume no request capacity.
- Static pages keep release identification on localized homepages without repeating release metadata across thousands of generated pages.

### Fixed

- Redis connection failures and command timeouts now degrade to cache misses or unsuccessful writes instead of aborting otherwise valid AI completions. Concurrent initial connections are serialized, unsuccessful clients are closed, and task cancellation remains observable.
- Fixed the default cache TTL configuration lookup and conversion from hours to seconds; reject invalid TTLs before accessing Redis.
- Prevented concurrent rate-limit waiters from exceeding an instance's sliding-window capacity after waking together. Window timing now uses a monotonic clock, expires records at the exact boundary, and prunes stale request history.
- Reject invalid maintenance batch limits, zero concurrency, non-finite request intervals, and malformed checkpoint cursors before processing data.
- Preserve provider-failure retry instructions when a metadata backfill reaches its row limit, and keep reported cursors aligned with saved progress after an empty candidate batch.
- Use unique temporary checkpoint files and clean them up on write failure while preserving the previous checkpoint.

### Validation

- Passed all 1,422 Python unit tests, including cache-outage, concurrent admission, cancellation, CLI-boundary, and checkpoint-recovery regressions. The suite still reports four dependency-deprecation or asynchronous connection-cleanup warnings.
- Passed targeted Python static checks and Git diff whitespace validation.

## [0.10.1] - 2026-09-12

### Added

- Added a French interface under `/fr/` across the public home, country, disease, report, research, situation, download, and policy pages.
- Added French translations for shared navigation, search, statistics, chart metrics, categories, accessibility labels, and release notes.
- Added a synchronized French disease-name catalogue for directories, cards, search, research views, and epidemiological charts.
- Added French Research Radar evidence summaries generated by Model Center from the canonical English summary, with source fingerprints and deterministic alignment gates.
- Added French Research Radar discovery surfaces: article cards and detail pages, weekly briefs, Ask search, catalogue indexing, and localized RSS feeds under `/fr/research/rss*.xml`.
- New literature inserted by each successful sync now queues a targeted English-canonical plus French enrichment task automatically; transient Model Center failures remain recoverable through the normal backlog/catch-up scheduler.

### Changed

- Language switching now cycles through English, French, and Chinese while preserving the current page and query string.
- French pages use `fr-FR` number and date formatting and expose French document, Open Graph, and structured-data metadata.
- Research Radar keeps the original article title and abstract as the source of record; French summaries are additive and fall back safely for legacy records without a translation.

### Fixed

- Fixed localized links from charts, country cards, reports, research widgets, breadcrumbs, and the footer so French users remain in the French experience.
- Added safe English fallback behavior for legacy records that do not yet contain French content.
- Kept large historical weekly literature collections within the production page-size budget by rendering the first 30 records and loading the remainder from the client catalogue.
- Preserved existing English and Chinese enrichment, weekly-brief publication, and release-validation contracts while French content is populated incrementally.

### Validation

- Passed 83 focused backend tests, Astro type checking for 229 files, 17 Research Radar tests, 27 SEO tests, and a 6,749-page production build with performance and generated-link validation.

## [0.9.7] - 2026-09-11

### Added

- Added a redesigned countries coverage map with dual-layer markers for countries and provinces/regions, hover tooltips, and interactive controls for pan/zoom.
- Added open GeoJSON- and ISO-based coverage marker positioning so the map no longer depends on remote tile services and can mark unsupported geographies through explicit fallback points.

### Changed

- Reworked the countries page coverage section with a bilingual summary header, status readout, richer legend, and filter-linked map overlay behavior.
- Integrated weekly literature AI-review record updates into the release cycle, including refreshed fingerprints and review timestamps for active weekly slots.

### Fixed

- Fixed map filtering synchronization so global resets and detailed filter changes consistently drive the marker layer state.
- Hardened map placement on diverse GeoJSON geometry by validating coordinate type and falling back to computed centroids for edge geographies when explicit coordinate coverage is missing.

### Operations

- Updated `configs/literature/weekly_ai_reviews.json` for W36 and W37 to carry the latest review fingerprints and pass timestamps.

## [0.9.6] - 2026-09-10

### Added

- Added surveillance context to Research Ask: matched published disease/location evidence with GIDS reported-case curves, bilingual data links, and interpretation notes.
- Added runtime routing telemetry for the Model Center and exposed actual routability, recovery state, capacity, latency, and failure history in the control panel.
- Added bounded literature-workload probes and independent provider/model admission controls so production summary traffic is tested under a representative structured workload.
- Added explicit Australia ingestion semantics for closed-month revisions, open provisional months, completeness-based recovery, and authoritative revision flags.

### Changed

- Research Radar persistence now classifies candidates before opening transactions, writes them in stable order and bounded batches, and retries only PostgreSQL deadlock victims.
- Model Center route health now decays stale failure streaks, probes chronically failing routes for recovery, and tunes provider concurrency independently from model concurrency.
- Literature enrichment now tolerates mixed-prefix/suffix JSON responses, retries transient model-channel failures, and supports configurable per-model attempts.
- Control-plane event streams now use bounded connections with automatic client reconnects, allowing clean service maintenance without losing the event contract.
- Situation Room production release workflows are now manual-only; scheduled runs stop at a verified artifact until an operator requests deployment.

### Fixed

- Prevented missing-model-channel errors from consuming every summary retry and added a cooldown before probing the same unavailable route again.
- Prevented malformed or fenced model responses from failing otherwise recoverable literature enrichment.
- Prevented repeated SMTP authentication failures from hammering the server by applying a bounded authentication cooldown.
- Made quoted SEO metadata parse safely during static output checks.
- Kept AU crawls from silently publishing an open provisional month unless explicitly requested, while recovering materially incomplete live snapshots from approved archives.

### Operations

- Hardened automated research releases with bounded retries, deterministic persistence, source completeness checks, and clearer control-plane failure handling.
- Added focused tests for model-channel recovery, literature response parsing and retries, SMTP authentication cooldowns, AU provisional ingestion, and release automation.

## [0.9.5] - 2026-09-05

### Fixed

- Prevented autopilot-managed Research Radar articles with non-public research domains from remaining on the public boundary after newer classification evidence marks them as `animal_only`, `basic_research`, or `plant_only`.
- Reopened autopilot-published summaries when their parent article is demoted back to review, and archived them when the parent article is excluded.
- Accepted scalar or list-shaped model fields during Research Radar summary enrichment by coercing them into conservative evidence objects, avoiding avoidable summary failures when models return prose instead of `{ text, evidence, confidence }`.
- Kept transient Cloudflare Pages API failures from being misclassified as a missing production-branch configuration during release preflight.
- Kept chronically failing Model Center routes out of active summary candidates after repeated production-call failures, while allowing a successful structured health check to restore the route.
- Made partitioned download generation respect the service cgroup memory guardrail so site releases do not stall under `mem_cgroup_handle_over_high` pressure.
- Prevented site release exports from being claimed concurrently with memory-heavy Research Radar and AI tasks in the dashboard worker.
- Prevented queued site releases from being starved by newly claimed Research Radar catch-up tasks.
- Drained release subprocess output by byte chunks instead of newline-delimited reads so verbose Astro/Node commands cannot block on a full stdout pipe.
- Treated provider 403/no-access responses, including Chinese `无权访问` messages, as unavailable model routes so they are removed from the summary retry chain.
- Kept large Research Radar disease, country, and topic collection pages within release HTML budgets by rendering the first page of articles server-side and loading additional cards from the compact research catalogue on demand.
- Added compact source identifiers to Research Radar article SEO titles so long distinct article titles do not collide after SEO truncation.
- Kept Model Center provider health from being overwritten by an individual sibling model failure; provider checks now remain routable when at least one enabled model succeeds.

### Operations

- Reconciled the Research Radar catalogue after the non-public-domain fix: the blocking `animal_only` Nature Communications article was returned to review, public export validation passed, and the public Research Radar export rebuilt with 964 articles and 334 weekly briefs.
- Restored 1959 autopilot-published rows that were briefly over-revalidated by score-threshold drift during diagnosis, while keeping 13 hard public-boundary demotions in review.
- Verified the targeted Research Radar automation, enrichment, release-validation, data-release, Model Center runtime-health, site-data orchestration, and task-worker suites with 110+ passing tests.
- Verified the full Astro production build after the collection-page slimming and SEO-title disambiguation; performance-budget and SEO-output release checks pass for 4116 generated pages.
- Raised the healthy `centos_cn:qwen3.6-flash` runtime admission budget to two concurrent in-flight calls while other routes remain excluded by quota, permission, or chronic timeout state.

## [0.9.4] - 2026-09-04

### Added

- Added a bounded disease-knowledge autopilot that keeps a source-first repair backlog filled, prioritizes true content gaps before stale policy revalidation, persists schedule state, and avoids duplicate active repair work per disease.
- Added targeted source-discovery recovery for missing disease-profile sections, including per-language repair scopes, source transport backoff, schema-signature invalidation, and automatic handoff from source refresh to model repair only after evidence is ready.
- Added PubMed E-utilities as a bounded Research Radar source for controlled biomedical discovery and as a fallback when the core Crossref journal sync is temporarily unavailable.
- Added PubMed EFetch abstract enrichment for PMID-bearing Research Radar records and a dry-run-first `backfill_pubmed_abstracts.py` repair command for existing short abstracts.
- Added Model Center runtime-health telemetry, provider/model catalogue discovery, structured test toggles, runtime-route capacity fields, and a richer dashboard workspace for provider, model, and route operations.
- Added epidemic-curve comparison controls for native cadence, seasonal anomaly index, complete-calendar-year aggregation, quick date-window shortcuts, selected-only filtering, and sortable entity selection.

### Changed

- AI content governance now routes through the shared Model Center agent path, so governance reviews inherit the same admission gates, circuit breakers, route health, and failover behavior as other AI workflows.
- Knowledge publication gates now treat missing required evidence as an automation state instead of a manual-review shortcut; public disease pages surface an `automating` state while source enrichment is still in progress.
- Disease-knowledge scoring now weights required profile fields ahead of optional display fields, supports aggregate ontology profile detection, and preserves existing valid fields during narrowed repairs.
- Task workers now use adaptive AI concurrency, separate knowledge-source concurrency, model-recovery wakeups for stranded repairs, per-disease serialization for knowledge tasks, graceful restart requeueing, and clearer runtime heartbeat metadata.
- Research Radar discovery accounting now tracks Crossref, Europe PMC, PubMed, optional providers, fallback usage, and source errors separately without advancing Crossref checkpoints during PubMed fallback.
- Research Radar AI enrichment now scans deeper through already-processed high-ranking records so lower-ranked articles with complete abstracts are not starved by the default batch size.
- Research Radar AI enrichment now processes article batches concurrently, uses a shorter per-route model timeout, and schedules one-minute catch-up runs while summary batches remain full.
- Research Radar AI enrichment now includes any review/published article with a sufficiently detailed abstract, regardless of `open_access_status`, and rotates active Model Center route shards across concurrent summary requests.
- Research Radar evidence graph pages now load graph relationships from a dedicated static JSON endpoint instead of embedding the full graph in every localized HTML page.

### Fixed

- Prevented non-public aggregate or summary catalogue rows from entering knowledge source/model repair loops, archiving any retained briefs that no longer belong on public disease pages.
- Prevented stale source-gap, transport-error, or obsolete schema-policy metadata from revoking a previously valid disease profile without a fresh matching source certificate.
- Hardened disease-knowledge source collection against adapter timeouts, cancellation, metadata-only source leakage, repeated surveillance-note overlays, and citation/quality repair retries that would otherwise persist unsupported content.
- Improved epidemic-curve tooltips, source-series selection, mixed-cadence comparison behavior, and table rendering so selected source projections and annual aggregates stay aligned with the plotted data.
- Avoided worker restart storms when another healthy worker owns the Redis singleton lease, while preserving bounded memory limits and stale-task recovery semantics.
- Fixed a Research Radar enrichment stall where top-ranked records with completed summaries could make a scheduled AI batch select zero articles even when eligible unsummarized articles existed later in the queue.
- Kept Research Radar summary generation moving when weekly AI review fails closed during a combined enrichment run; weekly review still records its own failures without failing the summary task.
- Recovered malformed Chinese summary JSON from the canonical English summary contract when a validated English summary is already available.
- Queued Research Radar tasks atomically at creation time so fast workers cannot claim a new task before the scheduler finishes marking it queued.
- Kept transient model-route failures, connection outages, and English-summary dependency misses from consuming article quality-attempt budgets so eligible abstracts remain in the retry queue.
- Serialized Research Radar public export writes with Astro site builds so continuous summary catch-up cannot mutate `research/index.json` midway through a production build.

### Operations

- Added configuration defaults and documentation for knowledge automation, adaptive AI worker concurrency, PubMed discovery, and Crossref/PubMed fallback policy.
- Added focused coverage for knowledge automation, Model Center runtime health, PubMed clients and normalization, literature provider failure isolation, adaptive task-worker config, evidence-gated repairs, and epidemic-curve annual aggregation.
- Backfilled PubMed abstracts for 189 existing published/review records, restarted the task runtime, and verified a control-plane-triggered enrichment task generated 6 summaries from 8 selected articles after the queue-selection fix.
- Restarted the production task runtime after the summary catch-up fixes and verified consecutive control-plane/scheduler enrichment batches of 32 articles with zero summary-generation failures after the malformed-JSON fallback was deployed.
- Reopened 39 transient Research Radar summary failures that were caused by model connectivity or bilingual dependency issues, then restarted the production worker and scheduler with route-shard rotation enabled.
- Verified the full Astro production build after moving the graph payload out of HTML; performance and SEO release checks pass with the larger Research Radar catalogue.

## [0.9.3] - 2026-09-02

### Added

- Added a bounded quality-repair prompt for evidence-backed disease-knowledge fields. When a generated target field fails deterministic quality checks but the evidence manifest explicitly supports it, the model receives the failed fields, evidence fragments, and prior JSON for one constrained repair pass.

### Changed

- New automatic disease-knowledge repairs now use a source-first workflow by default: refresh and assess targeted evidence before scheduling a model-center generation follow-up.
- Quality repair preserves all previously valid target fields, allowing the model to modify only the rejected evidence-backed fields.

### Fixed

- Prevented repeated model calls for fields without supporting evidence; unsupported fields remain in review instead of being regenerated from incomplete context.
- Preserved safe worker handoff during restart: the worker stops claiming new tasks, drains active work, releases its lease, and the replacement worker resumes queued recoverable work.

### Operations

- Restarted the production task worker after the workflow update and verified model-center, Redis, task leases, source-refresh follow-ups, and automatic recoverable-task requeueing.
- Added focused coverage for the supported-field quality-repair path and verified knowledge and AI-governance test suites.

## [0.9.2] - 2026-09-01

### Added

- Added AI content governance for exception-only queues, including model-center JSON review prompts, auditable decision metadata, confidence gates, and safe fallback-to-hold behavior.
- Added automatic knowledge-base repair discovery and queueing through the AI control plane, with targeted model-center refresh tasks for incomplete or stale disease profiles.
- Added API endpoints for knowledge repair runs and AI content governance runs, including bounded controls for failed knowledge retries, Research Radar article/summary review, knowledge-source review, and learning-suggestion mapping.
- Added AI-assisted Research Radar article review so high-confidence review-band articles can be automatically published or excluded before summary publication gates run.

### Changed

- Disease-knowledge repair now supports language-targeted retry, so single-language failures can regenerate only `en` or `zh` instead of rewriting an already healthy paired brief.
- Research Radar governance now separates deterministic article gates, model-reviewed article decisions, model-reviewed summary decisions, and source-fingerprint checks into an auditable sequence.
- Disease learning suggestions can now resolve obvious placeholders and high-confidence standard disease mappings automatically instead of remaining in a manual pending queue.
- Lowered task-worker idle log noise and added memory release after task completion to keep long-running model and ingestion workers steadier.
- Standardized service logging format across AI agents, report generation, workflow execution, ingestion, release scheduling, settings, and alerts.

### Fixed

- Failed AI knowledge repair tasks that hit recoverable model-center errors or partial-language publication gates can be requeued automatically up to their retry limit.
- Data-release retry classification now treats transient OpenSSL EOF preflight diagnostics as retryable while preserving missing Cloudflare production-branch configuration as a permanent blocker.
- Dashboard standalone release startup now prunes older retained releases after switching to the active build.
- Added weekly AI-review evidence for `2026-W36`, preserving the bilingual-mismatch decision as an explicit editorial-review signal.

### Operations

- Requeued the active recoverable knowledge repair failures and verified the queue was back to no failed tasks before release handoff.
- Ran a live AI governance smoke test: mapped the pending HIV learning suggestion to the standard catalogue, rejected placeholder unknown-disease suggestions, published three high-confidence Research Radar articles, published one eligible summary, and held ambiguous knowledge-source rows.
- Added focused tests for AI content governance, knowledge repair candidate selection, worker memory release, worker idle logging, and release retry classification.
- Updated the website package release version to 0.9.2.

## [0.9.1] - 2026-08-30

### Added

- Added first-class Chinese public routes for home, countries, diseases, reports, Situation Room, Research Radar, downloads, legal pages, and Changelog, with localized canonical URLs and navigation.
- Added the China provincial monthly-source framework, including province source configuration, 31 adapter registrations, documentation, tests, and Control Center/public metadata plumbing.
- Expanded Chinese province history from the initial 24-category subset to all 49 non-total PHSM ProvinceReport categories, with distinct non-additive parent/subtype series and six restored standard disease concepts.
- Added jurisdiction-aware country and region classification data for public coverage, source labels, API metadata, and the Control Center jurisdiction picker.

### Changed

- Refreshed the public website experience across the home, countries, downloads, about, copyright, terms, and changelog pages, including offline country flag synchronization for stable static builds.
- Promoted source-series-first data handling through APIs, static-site generation, disease/country charts, downloads, and coverage summaries so canonical projections and source-only series remain visibly distinct.
- Extended reusable ECDC, Singapore CDA, and Canada CNDSS ingestion paths with stronger source-contract review, attribution, scheduled checks, and fail-closed refresh behavior.

### Fixed

- Hardened disease-series policy, ontology validation, country coverage, classification, and generated API contract tests around annual/monthly handoffs, jurisdiction metadata, and source-only mappings.
- Made province history and official monthly-report parsing fail closed on unknown disease or province labels, and added auditable accounting for totals, blanks, and duplicates.
- Improved chart and map behavior for sparse source data, hidden projections, mixed cadence series, and localized country/disease pages.
- Kept local Playwright run output and generated flag assets out of release commits while preserving deterministic static-site builds.

### Operations

- Added dedicated validation coverage for China provinces, ECDC Atlas baselines, Singapore CDA, Canada CNDSS, dashboard jurisdiction selection, and source-series-first site exports.
- Updated the website package release version to 0.9.1.

## [0.9.0] - 2026-08-29

### Added

- Completed the shared ECDC annual baseline across all 30 EU/EEA countries and added the historical United Kingdom baseline. This release adds 23 countries, 1,265 reviewed contracts, and 20,334 observations across 1,193 source series with values; the complete 31-country ECDC collection now contains 27,843 source observations.
- Added Canada's PHAC CNDSS national annual source with 70 reviewed contracts and 3,671 observations across 69 source series from 1924 through 2023 under the Open Government Licence – Canada.
- Integrated all new countries into source-series storage, Control Center selection and automation, APIs, bilingual provenance, coverage, downloads, country pages, and static-site generation.

### Changed

- Added 24 independent daily publication checks: 23 staggered ECDC checks in each country's local timezone and a 09:25 America/Toronto CNDSS availability check. Existing higher-frequency national feeds remain separate from ECDC baselines.
- Canonicalized the historical ECDC United Kingdom source geography `UK` to platform country code `GB`, while retaining the upstream code in provenance. England-only laboratory notifications remain a separate future regional source.
- Published Canada as PHAC's national aggregate reported counts, not an all-jurisdiction completeness claim. Disease/year coverage varies, including unavailable Manitoba data for 44 disease contracts in 2023; null cells remain unknown.

### Fixed

- Corrected Canada's viral meningitis to the active D134 concept and retained historical non-A/non-B hepatitis as a related source-only series instead of projecting it to a deprecated concept.
- Made bounded Canada refreshes rebuild the full reviewed 1924–2023 current snapshot and fail closed when the official last year changes, requiring explicit contract review before a new annual release is accepted.
- Kept all-null Canada influenza contract 58 registered as upstream-pending rather than falsely advertising an available series, while preserving 645 published zeroes as observations.

### Operations

- Independently reviewed Thailand's DOE DDS route and left it in source research: the public dashboard is a limited current-week snapshot, national exports and zero-reporting require login, and no explicit public reuse licence was found. No placeholder Thailand data or partial adapter was added.
- Recorded the required ECDC attribution and preserved source-only composite categories, asynchronous disease updates, explicit zeroes, and missing cells without double counting.

## [0.8.3] - 2026-08-28

### Added

- Added Spain, Italy, Portugal, Poland, Czechia, Greece, and Romania as publicly supported ECDC annual-baseline countries across ingestion, source-series storage, Control Center, APIs, downloads, provenance, coverage, and bilingual country pages.
- Registered 385 reviewed country-specific source contracts and imported 6,618 annual observations across 375 source series with values from 1990 through 2025.

### Changed

- Added staggered daily Control Center publication checks in each country's local timezone while preserving ECDC's asynchronous topic coverage, explicit zeroes, and missing cells as unknown.
- Normalized the ECDC `EL` source geography to the platform's ISO `GR` identity for Greece while retaining `EL` in source provenance.

### Fixed

- Withheld in-progress current-calendar-year Atlas cells from the closed annual baseline, preventing partial Czechia 2026 data from being presented as a completed year.
- Made each refreshed ECDC history window authoritative in both CSV and database storage, so a cell withdrawn by ECDC becomes unknown instead of leaving a stale local value.

## [0.8.2] - 2026-08-28

### Added

- Added France as a publicly supported annual-baseline country across ECDC ingestion, source-series storage, Control Center source selection and automation, API metadata, country coverage, downloads, provenance, and static-site generation.
- Added a reusable ECDC Surveillance Atlas adapter with 55 reviewed source contracts and 891 France observations across 51 published series from 1990 through 2025.

### Changed

- Preserved ECDC country/year gaps as unknown and explicit published zeroes as zero; four registered France series currently have no country values and remain empty rather than being fabricated.
- Kept aggregate Ebola/Marburg and unmapped arenavirus categories source-only, excluded non-case datasets, and excluded hepatitis B totals because the current Atlas contract exposes a total rate but no total case count.
- Recorded the required attribution: “Data provided by ECDC based on data reported by EU/EEA Member States.”

### Operations

- Added an enabled Control Center publication check at 09:50 Europe/Paris each day for annual revisions.
- Corrected roadmap governance: Austria remains public-release gated pending clear AGES redistribution permission, and the current UKHSA causative-agent series is labelled as England laboratory notifications rather than UK or England-and-Wales case counts.

## [0.8.1] - 2026-08-28

### Added

- Added Singapore as a publicly supported country across ingestion, source-series storage, Control Center source selection, API metadata, country coverage, About/source provenance, downloads, and static-site generation.
- Added the official 2012–2022 data.gov.sg CSV history and 2023+ CDA annual workbook pipeline, with 2023 weekly CDA PDFs as a fail-closed fallback.
- Added 76 registered Singapore source series covering 39 source categories and a safe temporal-handoff projection for equivalent historical and successor series with strictly non-overlapping validity windows.

### Changed

- Enabled Singapore public release by explicit operator authorization while preserving separate provenance: historical CSV reuse follows the Singapore Open Data Licence, and current CDA records retain the source-terms status requiring written permission.
- Marked Singapore as Supported on the public coverage map and exposed both Singapore ontology sources through the weekly Control Center scope.
- Promoted reviewed exact and narrower Singapore mappings to canonical projection; the historical aggregate encephalitis category remains source-only and is never projected automatically.

### Operations

- Singapore full refresh starts in 2012, refreshes the most recent 12 weeks incrementally, preserves explicit zeroes, never fills absent source rows, and stores raw SHA-256/source-contract provenance.
- Added an enabled Control Center publication-check schedule at 09:30 Asia/Singapore each day so new weekly CDA workbooks and bounded revisions are discovered automatically.
- CDA content is not described as open-licensed. Operators remain responsible for ensuring their explicit publication authorization satisfies CDA terms before production redistribution.

## [0.8.0] - 2026-08-27

### Added

- Evidence-bounded AI review for weekly Research Radar briefs, with deterministic preflight, strict output schemas, fingerprint invalidation, concurrency protection, and a clear separation from editorial approval.
- Dry-run-first governance and recovery commands for editorial backlog, metadata coverage, stale ingest runs, and abandoned background tasks.
- Optional bounded and resumable Springer Nature, Elsevier, bioRxiv/medRxiv, and publisher RSS connectors. Paid publisher APIs remain disabled until credentials and operating terms are supplied.
- Redis-backed worker ownership, task leases and heartbeats, stale-task recovery, runtime health inspection, and guarded maintenance commands.
- Email-delivery feedback processing for subscription suppression and campaign delivery accounting.

### Changed

- Source health is now derived from complete per-provider run evidence, not source configuration alone, and exposes safe reason codes and next actions.
- Literature catch-up reports explicit capacity, resume thresholds, and persistence failures without mutating overdue schedules from read paths.
- OpenAlex and Unpaywall metadata backfill is coverage-targeted, bounded, independently resumable, and defaults to dry-run.
- Research summaries use a canonical English evidence set before Chinese generation, preventing cross-language claim drift.
- Public release checks now enforce weekly AI-review evidence where present, canonical/hreflang consistency, valid generated redirects, scalable compressed-HTML budgets, and safer graph payloads.
- Runtime startup and systemd ownership checks prevent duplicate API, worker, or scheduler processes and ensure the scheduler depends on a ready worker.

### Fixed

- Exercised all 21 enabled country and regional schedules through Control Center. All completed successfully; the run covered 32,684 reported records and 172 source workbook events with no source errors. A valid China incremental no-op was separately confirmed against current database coverage.
- Fixed CDC NHSS HIV ingestion by sending a transparent provider-specific crawler identity accepted by the official endpoint.
- Fixed OAI exact-limit checkpoint rollover, optional-source checkpoint preservation, and bioRxiv partial-page resume boundaries.
- Fixed metadata backfill selection and target-completion boundaries, model-centre bootstrap query storms, and catch-up schedule drift after restart.
- Fixed stale ingest runs that lacked task ownership, while keeping cleanup locked, bounded, and fail-closed.
- Fixed legacy Situation Room URLs by generating real 301 rules instead of static redirect pages.

### Operations

- Database migration `0011_ingest_task_binding` adds task ownership to literature ingest runs and must be applied before using stale-ingest reconciliation.
- Springer Nature and Elsevier are intentionally off by default. Current curated records are fully covered by DOI/Crossref in the audited publisher subsets, while PubMed/Europe PMC provide partial biomedical coverage; publisher APIs can be enabled later if discovery scope requires them.
- Production-writing maintenance commands require an explicit `--apply`; backlog governance additionally requires a fresh plan hash and bounded post-run projection.
