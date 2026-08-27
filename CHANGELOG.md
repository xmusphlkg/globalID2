# Changelog

This file records release-level changes to the GIDS application and its data operations. Public-facing bilingual notes are also available in the website Changelog.

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
