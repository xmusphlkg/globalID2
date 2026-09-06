# Changelog

This file records release-level changes to the GIDS application and its data operations. Public-facing bilingual notes are also available in the website Changelog.

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
