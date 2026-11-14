# Changelog

This file records release-level changes to the GIDS application and its data operations. Public-facing multilingual notes are also available in the website Changelog.

## [Unreleased]

## [0.19.4] - 2026-11-14

### Fixed

- Rebuild Research collection pages from current frozen JSON and templates.
  The previous template release removed collection routes even though its
  build omitted those pages. Validate all current geographic, disease, topic
  and weekly routes before publication, including bibliographic-only diseases.
- Keep Compare selector headings inside their panels and enable the independent
  shared theme on English, Chinese and French Compare routes.
- Prune excluded directories before scanning publication source fingerprints,
  preserving the exact fingerprint contract. A local three-run comparison reduced
  median fingerprint time from 0.848s to 0.248s; this is not an end-to-end release
  benchmark.

- Reuse exact cached Research recommendation rankings when only other candidates
  change. Merge affected candidates with unchanged top results, and fall back to
  a full calculation when targets, winners or cache validity change. Randomized
  regression tests verify the complete output against full recomputation.
- Make an explicit planned `full` publication render all main-site and Report
  pages freshly, bypassing whole-build and HTML-page reuse while retaining
  validation gates and recovery of already prepared publication artifacts.

Publication evidence and verification results are recorded in
[the acceptance record](docs/acceptance/DATA_UPDATES_20261114.md).

## [0.19.3] - 2026-11-14

### Fixed

- Isolate Brazil SINAN DBF parsing in bounded spawn processes so CPU work cannot
  starve the publication worker. Cancellation terminates and joins parsers and
  removes their temporary files; database writes remain in the parent.
- Skip legacy article HTML caches when Research uses its independent publisher;
  report workspace preparation and timing instead of leaving progress at zero.
- Avoid decoding unused historical metadata in Japanese quality checks while
  preserving quality decisions, stored provenance and revision snapshots.
- Preserve completed deployment checkpoints when resuming a frozen publication
  after a source update, avoiding duplicate builds and Pages uploads.
- Stop inheriting retired static HTML routes when composing a new Papers
  publication. Preserve current article and supporting asset references.
- Deploy the main-site data route for the independently published changelog JSON;
  its missing route prevented the default daily activity chart from appearing.
- Respect the origin cache policy for the exact theme compatibility stylesheet
  URL, removing the zone's four-hour browser-cache override on that entry point.

### Operations

- Add reviewed Papers generation cleanup with a minimum 48-hour retention window,
  protection of shared dependencies and current articles, an enforced publication
  maintenance window, resumable batch receipts and final object verification.
- Add reusable live acceptance scripts for cross-host publication identity and
  multilingual daily update counts. Public release confirmation now also checks
  the main site's website-history JSON route.
- Complete cleanup of 838 stale Pages deployments and 35,964 expired Papers
  objects (2,763,282,763 bytes), preserving current and retained dependencies.
- Deploy source `c1c99f6` through the planned template-only workflow, with public
  verification and database confirmation. The final run took 12m 14s versus
  22m 34s for the preceding run; the 10-minute target remains unmet. Workspace
  preparation took 51.596s; Pages uploaded 35 files and reused 4,369.

Operational deployment and real-network acceptance evidence are recorded in
[the acceptance record](docs/acceptance/DATA_UPDATES_20261114.md).

## [0.19.2] - 2026-11-14

### Fixed

- Keep Compare disease and geographic-area controls inside the layout on narrow
  screens, including French labels. Restore child-area deep links and refresh
  selections from the current JSON catalogue.
- Expose available lower-level administrative units in comparison, seasonality
  and Situation selectors, using source metadata rather than two-letter filters.
  Use neutral geographic terminology in English, Chinese and French while
  preserving existing URLs, identifiers and API fields.
- Preserve missing values in dashboard chart tooltips and correct QR format-bit
  placement so exported chart links decode correctly.
- Preserve missing historical values and source provenance during database
  rebuilds; fix a missing NNDSS source constant that could silently skip rows.
- Report publication input changes before expensive rendering and preserve the
  specific cause when a required preparation receipt cannot be saved.

### Changed

- Apply the core-code reorganization in reviewed batches: separate parsing,
  query contracts, pure policy and presentation from transaction, lock, task
  and publication ownership. Keep legacy entry points and explicit test seams.
- Keep the shared header/footer, map footer suppression, independent version
  JSON and daily data-update counts. Editorial changelog changes no longer
  invalidate the reusable code-only publication workspace.
- Ignore dependency directories and symlinks, Vite caches and local worktrees;
  retain source tests and fixtures in Git.

Validation and actual deployment status are recorded separately in
[the implementation record](docs/architecture/CORE_CODE_REORGANIZATION_PROGRESS.md)
and [the acceptance record](docs/acceptance/DATA_UPDATES_20261114.md).

## [0.19.1] - 2026-11-14

### Changed

- Retain content-verified source-data snapshots for template publication and
  build code-only releases in a private Astro workspace. Background exports
  no longer overwrite those inputs or force repeated full data releases.
  Legacy installations require one normal release to establish the snapshot.
- Preserve the independent Reports publication during template-only releases;
  publish report-template changes through `reports_only` or a full release.
- Show range-specific data-update and website-version totals beside the daily
  chart, and remove the explanatory paragraphs from the public updates page.

### Fixed

- Use UTC consistently for the public update range and daily counts, including
  timestamps with timezone offsets. Reject incomplete or invalid date ranges.
- Keep missing monthly archives from appearing as zero publication activity;
  retain known records with an explicit refresh error.
- Bound Literature gap association lookups to 10,000 article IDs per query to
  avoid PostgreSQL parameter limits. Read only the six article fields needed
  by the projection, leaving large raw payloads out of the query.
- Let Literature monitoring recognize verified full-site Research commits and
  use its actual publication interval when judging pending-data delays.
- Verify Papers v3 public canaries in full-site research publication before
  recording public success, including retries after pointer commit.
- Bind prepared-publication recovery to the actual
  `.publication/starting-baseline.json` receipt produced by baseline capture.
  Regression coverage now uses the real capture helper and rejects changed
  or missing baseline receipts.

### Validation and release scope

- Both public monthly update archives and the index matched completed,
  acknowledged database publications at 01:35 UTC on November 14: 2,218
  Surveillance, 96 Situation and 33 Literature records; Knowledge had none.
- The previous template publication was blocked by a changed local data
  snapshot. This release uses the publication plan and task workflow to build
  a fresh baseline; deployment and test results are tracked in
  [the acceptance record](docs/acceptance/DATA_UPDATES_20261114.md).
- Multi-source operation is partial: source-specific failures and a large
  Literature review backlog remain visible. Successful channel publication
  does not imply that all sources or historical content have passed acceptance.

## [0.19.0] - 2026-11-13

This release introduces the GIDS data-line and control-plane migration,
Papers v3 and the independent theme. Production acceptance is tracked
separately; the November 14 record corrects the earlier broad completion claim.

### Added

- Split collection into durable `collect → organize → store` stages for
  Surveillance, Situation, Literature and Knowledge. A successful store records
  only its affected scope in `publish_dirty`; public JSON publication runs later
  from a frozen candidate.
- Added the independent Literature JSON channel with immutable shards, an
  atomic current pointer, release receipts and sampled public-object checks.
- Added authenticated control-plane views and API contracts for overview,
  collection lines and summaries, model routes and queues, realtime publication
  queues and runs, task workers, runtime heartbeats and action items.
- Added the independent theme v1 compatibility stylesheet and its readiness,
  receipt and public-host verification flow.
- Added Papers projection v3: one multilingual record plus shared templates and
  verified assets can render all three locales without copying every article
  body into a new generation.

### Changed

- Replaced fused ingestion-and-publication work with queued workers and a
  dedicated AI worker. Durable model work items now carry leases, retries,
  cooldowns and route ownership across worker restarts.
- Realtime data releases publish only JSON and advance a channel pointer after
  immutable-object and manifest verification. Static Pages releases remain a
  separate workflow, and retries acknowledge only the exact frozen dirty set.
- Papers and the public site now share stable research URLs, generation-bound
  assets and compatibility headers. Existing stable links continue to resolve
  while the active renderer reports `projection-v3`.
- The public updates page filters an inclusive UTC date range and charts
  retained publication records for each day in that range.
- Runtime recovery is owner-scoped: managed drains recover confirmed-dead
  leases, preserve resumable work and verify API, scheduler, worker and AI
  heartbeats before reopening the queue.

### Fixed

- Prevented stale release retries from overwriting newer JSON or research
  pointers, and kept failed publication markers pending for a later retry.
- Corrected quoted greater-than attributes in paper templates and preserved
  required shared assets during projection rebuilds.
- Made dashboard polling and task/source hooks follow the current API envelope,
  while retaining the existing task and collection routes during migration.

### Validation

- Dashboard: 24 Vitest files, 134 tests, TypeScript checking and OpenAPI
  consistency all pass.
- Worker tests (23), Python target tests (45) and control-plane HTTP/runtime
  tests pass; `git diff --check` is clean.
- Production canaries return HTTP 200 for Surveillance, Situation and Literature
  JSON, the three locale Papers v3 routes and the six main-site locale/country
  routes. Theme v1 readiness and its public CSS probe return `ready=true`.
- The initial Papers v3 cutover generation contained 8,352 articles in three locales and
  is published under generation
  `318580a640b1885153b721990f21eee324cd015af92783da50603d76a29a07c0`.

### Compatibility and operational notes

- Existing public HTML, stable research URLs, download formats and previously
  published immutable objects remain available. The public site still uses its
  same-origin JSON worker; the abandoned independent CDN design is not part of
  this release.
- Release readiness remains a commit gate until the working tree is clean and
  the production failure-fence acceptance record is saved. Runtime queue counts
  and model throughput are live observations rather than fixed capacity
  promises.

## [0.18.4] - 2026-11-12

- Restore stable, short public research URLs across navigation, search, canonical metadata and sitemaps. Retire generation-specific page URLs with HTTP 410 while keeping data and assets bound to their publication generation.
- Invalidate incremental site builds and publication checkpoints when shared research URL rules or the Papers Worker change, preventing retries from restoring obsolete links.
- Remove one-time paper migration transfer tools and the obsolete standalone template activation command. Document the dashboard’s unified research publication flow.

## [0.18.3] - 2026-11-12

### Research publication infrastructure

- Store article content as one reusable multilingual JSON record per paper and shared locale templates. The Worker reconstructs complete HTML with verified metadata and generation-bound assets; style changes no longer require uploading every article body.
- Connect full-site and independent literature updates to one conditional research publication pointer. Freeze candidates before publication, reuse committed object receipts, and keep retries from overwriting newer data.
- Add an audited migration cleanup that verifies all replacement article digests, checks three languages across the public hosts, preserves local originals, and removes only replaced static pages and inactive prototype objects. Historical immutable releases and shared assets remain available for existing links.

### Research and public data

- Serve current research indexes as static Pages content and article/library assets directly from Papers R2. Partition catalogue and evidence payloads, load only relevant facets, and retain versioned compatibility routes for cached clients.
- Compress static Papers uploads, reuse checksummed artifacts, retain older referenced assets and shards, and enforce a conservative bucket-size budget before publication.
- Publish downloadable surveillance files from the same frozen snapshot as live JSON, with release-bound manifests, resumable publication and acknowledgement only after both channels succeed.
- Add compact, independently refreshed homepage summaries and a bootstrap manifest to reduce initial data loading; improve notice layouts, map loading feedback, footer navigation and text contrast.

### Background operations

- Add bounded literature abstract repair and a resumable systemd backfill coordinator. Repaired records can return to review while historical exclusions require an explicit reopening decision.
- Serialize AI failure-progress writes before acquiring database connections to avoid exhausting dispatcher pools under contention.
- Close SMTP connections after TLS/authentication setup failures and propagate authentication cooldown errors when the caller requests failure reporting.

### Fixed

- Completed tasks now persist and display 100% progress, including older successful JSON publications; failed and cancelled tasks retain their actual progress. Late progress callbacks cannot overwrite terminal task progress.
- Realtime publication separates cancelled tasks from failed-task history and explains when a later publication succeeded. Overview labels clarify that failures include history rather than only current incidents.
- Public data monitoring retries one transient transport, 429 or server error within the existing total deadline. A recovered incident remains visible as a warning; persistent, integrity and other errors still alert normally. The 24-hour failure count uses the failure completion time.
- The systemd monitoring check no longer reports a failed service for a warning-only result; the warning remains visible, and critical conditions and unexpected process errors still fail the check.

### Added

- The control panel has a realtime publication workspace for Surveillance and Situation, with separate queue/backlog views, version and integrity receipts, task history, manual JSON publication and fresh retries. The site release center and shared task list identify JSON channels separately from template and report publication.
- Automatic JSON publication can be paused per channel with durable cross-process state. Scheduler reconciliation, retries and continuation respect the pause; existing tasks and explicit manual runs remain available. Monitoring shows intentional pauses without treating their pending/retry age as an outage, while retaining integrity and execution alarms.

## [0.18.2] - 2026-11-11

- Accelerated disease-title association during literature export by skipping regex checks for absent literal aliases, preserving existing boundary, negation and specificity rules.
- Cached unchanged title associations with policy-safe invalidation, tracked complete per-disease literature dependencies, and grouped card shards by publication month so ordinary additions reuse unrelated pages and older shards.
- Added dedicated disease literature pages on the Papers R2 host, with real pagination, on-demand card shards, filters, publication trends and transparent PubMed/Europe PMC search strategies.
- Added a lightweight GIDS signal-orbit loading animation with accessible status text and reduced-motion support across public data views.
- Improved common disease aliases and compound searches, comparison selections, language-switch continuity and shared theme preferences across the public hosts.
- Scoped chart source captions and observation dates to the selected series, aligned homepage and Situation counts to the same 30-day window, and clarified current-file versus full-history download totals.
- Reduced focused Ask queries to relevant research collections, added recoverable search errors, and improved Chinese interface labels, evidence relationships and text contrast.

## [0.18.1] - 2026-11-07

- Added a multilingual website and data update history with monthly archives, channel filters, published coverage metrics and links to release notes. Successful acknowledged publication markers are projected into small JSON files by a separate minute timer, without rebuilding the site or blocking data publication.
- Fixed stale events during month switches and independent refresh error handling. Failed refreshes retain known events, slow requests are bounded, and monthly records are validated before rendering.
- Code-only releases recognize the hand-authored changelog and acknowledgements as source files while keeping generated data snapshots strictly unchanged; updating release notes no longer forces a data regeneration.
- Restored the compact download dialog with date/format filters and scrollable file cards. The dialog code and per-scope catalogue load on demand; chart data actions open it directly, with archive dates kept separate from live curve updates.
- Restored the homepage's real China/Japan influenza curves, country/disease navigation, download selection, source citations, subdivision shortcuts and weekly/monthly outbreak archives while retaining independent JSON updates. Per-article social cards remain disabled.

## [0.18.0] - 2026-11-07

### Added

- Surveillance and Situation publish versioned JSON independently of static HTML. Data changes trigger bounded publication queues with durable reconciliation and retries; unchanged observations and notice snapshots do not create repeated publication work.
- Same-origin data delivery uses immutable, checksum-addressed R2 objects and an atomic current-version pointer. Browsers pin one data version per page for summaries, charts, maps and lazy details.
- A read-only real-time publication monitor checks queue age, task outcomes, live manifests and sampled object integrity. Local observation logs and a systemd timer track rollout without sending notifications.

### Performance and correctness

- Dense precise disease updates (16 or more target diseases) now use the distinct-source-identity query while retaining the exact target filter. On one read-only snapshot, JP-25's actual 68-disease update fell from 57.831 to 3.871 seconds (14.9×); all 38,856 normalized rows and the complete merged country projection matched exactly. Smaller target sets retain their selective query path.
- Complete-country projection resolves each distinct source identity once, reducing JP-25 category lookups from 60,319 to 112. Paired read-only query measurements reduced source reading from about 49.25 to 5.09 seconds; five alternating pure-computation runs reduced median CPU from 3.959 to 2.757 seconds.
- Eight-country full projection took 19.7 seconds; generating all 504 public JSON objects into empty output directories took 27.682 seconds. All public object hashes matched the preceding complete export. A synthetic precise correction took 4.608 seconds. These local measurements exclude network publication and acknowledgement.
- Mapping activation invalidates old and new country scopes in the same transaction. Frozen publication candidates, conditional pointer writes and exact change-ID acknowledgement prevent stale retries from overwriting newer data or losing concurrent updates.

### Changed

- Static template and content releases reuse verified split artifacts when inputs and outputs are unchanged; release identity is stamped after rendering. Surveillance and Situation JSON changes publish independently. Astro shares one prerender process by default with page concurrency 8; source checks and bounded SEO readers run alongside rendering while manifests and routing retain one writer.
- Large generated research and country JSON is read as build data instead of imported into Vite/TypeScript, keeping it outside the compilation graph.

- Build planning persists compact country/disease directories and shares verified summaries with static rendering. Unchanged histories are no longer repeatedly parsed for ranking, aliases or indexability, and candidate links load full records only when rendering their detail pages.
- Historical Situation weeks, months and outbreak JSON join incremental page caching with per-period dependencies and adjacent-period navigation. RSS feeds share a facet index and sitemap endpoints share one build catalogue. Parsed JSON uses bounded file-identity caches; route directories keep compact summaries instead of retaining every surveillance history.
- Site builds run independent validation gates in bounded Node processes, cache country/disease pages in all three locales, and report per-stage timings and rebuild reasons. Unchanged page-cache files retain their identity; verified asset-reference caches avoid reparsing unchanged HTML during restoration and bundle splitting.
- Release exporters share one PostgreSQL read-only snapshot while collection continues writing. Website builds and uploads release the snapshot before running; publication acknowledges only frozen curves and selected change IDs.
- Split download projection and encoding, research visualizations and integrity alerts, and release build/deployment stages into focused modules while retaining existing entry points. Added a reproducible large-script audit.
- Surveillance publication records semantic observation changes and coalesces them into bounded country batches (`DATA_RELEASE__BATCH_MAX_COUNTRIES`, default 8), with durable continuation. Legacy scopes remain conservative and atomic.
- Download generation reuses existing split windows before constructing XLSX files. Page caches follow route import dependencies, with stable fingerprints across staging and committing unchanged content.
- Raw-source archival runs as an independent low-priority task. Publication retries reuse validated in-place data/build stages, and report input validation runs before expensive exports.
- The control panel supports code-only publication using an attested successful data snapshot; pending data markers remain untouched.
- Source and page dependency checks reuse Git object identities and persistent local file fingerprints. Isolated report builds reuse unchanged report bundles and recover missing cached assets with a full rebuild.
- Papers/Reports publication reuses build-time checksum receipts and remote object metadata, avoiding local body reads for unchanged files while preserving interrupted-upload recovery and checksum validation for changed files.

### Added

- Subscription preferences can be managed through short-lived email links, including list-level pause and resume and an all-lists unsubscribe. Published report, Research Radar, and reviewed official-notice updates have opt-in dispatchers with durable outboxes and scheduled reconciliation.

### Fixed

- Knowledge catalogue quality assessment yields between diseases so realtime JSON publication, cancellation and worker heartbeats can progress during governance reconciliation without changing catalogue results.
- Situation runtime JSON routes and static cache planning share the same mode predicate. Pages deployments no longer require or restore outbreak JSON that belongs to the independent data channel; legacy and standalone builds retain their static output.
- French country/disease surveillance titles retain their full country suffix within the SEO title limit; full disease names remain visible in the page heading.
- Weekly Situation pages use source-notice dates for publication metadata instead of treating every map export as a historical-page modification. Country/disease ranking discards losing candidates during selection, and sitemap directories avoid unbounded full-history retention that exhausted the 6 GiB Astro heap.
- SEO validation detaches retained title and canonical strings from full HTML documents to keep catalogue audits within the bounded checker heap.
- Taiwan NIDSS collection bounds retries and export windows during TLS outages, limits manual historical backfills to 12 missing months per task, preserves transport provenance, and excludes unverified historical zeroes and observations outside reviewed series validity.
- Preserve collection cancellation semantics, drain failed literature-provider and enrichment requests, and keep non-resumable jobs running during controlled worker shutdown. Retry model work on another eligible route when possible and record otherwise-empty timeout errors.
- Mark a recovered release's retry metadata as succeeded while retaining its historical failure details.
- Bound control-panel shutdown when long-lived event streams prevent Next.js from exiting during a service restart.
- Cross-country download updates replace only selected countries and preserve other countries' verified published rows, including source observations and explicitly counted legacy projections. Disease input fingerprints now use country dependency summaries rather than serializing entire cross-country histories.
- Control-panel publication preflight allows bounded Git/Cloudflare probes to finish instead of aborting after the ordinary 10-second read timeout.
- Surveillance downloads retain complete observation provenance instead of replacing it with lossy public-curve snapshots. Missing untouched download artifacts fail explicitly rather than publishing broken manifest entries.
- Report SEO titles retain full coverage dates so weekly reports in the same month have distinct titles in all three languages.
- Research Radar review now receives the catalogue's peer-review and integrity statuses and the summary's intended language. Existing holds are reconsidered once under the corrected review context, without bypassing evidence or peer-review gates.
- Subscription changes now require confirmation without overwriting active preferences; confirmation and unsubscribe links resist automated link scanning, and queued delivery checks subscriber status before sending. Campaign claims, provider feedback and bounded retries reduce duplicate or unwanted email.

## [0.17.5] - 2026-11-06

### Changed

- Data refreshes reuse verified unchanged pages, per-page audit facts and bundle asset dependencies. Cache finalization copies only changed page bundles; missing or changed dependencies invalidate reuse.
- Partitioning preserves unchanged images and JSON files, consumes strong build-time checksum receipts, and rewrites changed HTML on multiple CPU cores with a single artifact writer.
- Static rendering uses eight parallel Astro workers. The build pins Astro 7.4.7 and its compatible project-local Node 22.23.4 runtime.
- Release preparation preserves validation gates and destination reconciliation while avoiding repeated rendering and partitioning of unchanged artifacts.

## [0.17.4] - 2026-11-05

### Fixed

- Taiwan NIDSS monthly ODS imports now recognize pre-2000 years. Invalid disease exports are isolated and audited while valid peers continue; retained cached observations remain explicitly marked as fallback data.
- Ingestion restores missing surveillance-series definitions from the validated local registry within the observation transaction. Existing mappings are preserved, and unknown definitions remain subject to the quality gate.
- Report sitemaps build from report data only instead of loading the full surveillance and research corpus. Isolated report builds use the same 6 GiB Node heap allowance as the main site.
- Report publication records each execution stage and applies the existing bounded automatic retry policy to transient R2 upload failures. Build, validation and credential failures remain terminal.

## [0.17.3] - 2026-11-04

### Changed

- Data publication now completes when upload/deploy commands succeed. Automatic Papers, Reports, main-site and download public-URL checks are skipped; release failures retain generated artifacts without rollback or full artifact backup copies. Explicit download audits remain available with `--verify-remote`.
- Downloads now export into a persistent Git worktree, retain stable manifests, and skip upload, file validation and remote probes for an unchanged successfully published version. Git history and publication receipts remain available for retries; interrupted commits remain retryable. Full releases freeze exact database change-log IDs so late commits cannot be lost behind a timestamp boundary.
- Site export consumes database streams in batches, reducing per-row async overhead while preserving bounded memory and output order.
- Raw archive publication copies independent files with eight bounded workers by default, retaining per-file checksum verification and fsync. All copies must succeed before metadata, commits or pushes; failed partial runs remain recoverable.
- Large download snapshots upload their objects in resumable 1 GiB batches before the public branch is advanced, avoiding GitHub's per-push limit while keeping the snapshot switch atomic.
- Scheduled report readiness and cadence inference use eligible public national source-series observations instead of the legacy projection.

### Fixed

- Data releases now outrank other urgent tasks and pause managed collection work at database-session boundaries. Existing transactions finish before publication; paused tasks resume in place after success, failure, or cancellation instead of making publication wait for entire collection/model workflows.
- Research article Situation context merges repeated countries and source events within each disease/evidence relationship. Historical geographic context has its own label; precise and disease-only matches remain separate.
- Public research pages share width, spacing and title scales; copyright and terms use the interface font, and subscription panels use the shared flat surface treatment.
- Papers bundles now carry their referenced build assets and transitive fonts/scripts, uploading assets before HTML and retaining the preceding generation. New self-contained bundles no longer require the missing previous Pages snapshot; legacy bundles retain the verified compatibility-deployment gate.

## [0.17.2] - 2026-11-03

### Added

- Japan report generation now has a scheduled control-panel workflow, an AI author/reviewer pass, and a curated library of 12 evidence-bound ECharts templates. Priority diseases can include up to 52 periods of historical context; changes in source or reporting scope are shown as separate segments.
- Country report archives and report data are published on `reports.globalinfectiousdisease.com` from a dedicated R2 bundle, with its own directory, sitemap, robots policy, preflight, and public-host verification.
- Reports can be refreshed independently with `--reports-only`; unchanged detail files are reused. Incremental Astro builds retain shared hashed assets needed by cached pages.
- Research article pages group related Situation signals by disease and geography, keeping exact geographic matches distinct from disease-only context.

- Situation reads ECDC public health events (`ecdc_events`) as a new source: outbreak pages, seasonal mosquito-borne surveillance and risk assessments in production, which ECDC updates daily and the weekly CDTR only summarizes later. The adapter pages the public listing newest first and keeps the latest version of each event per ISO week. Page bodies are read in full and go through the same two-model review as every other source. The source appears in the control-panel Situation workspace without frontend changes.

### Changed

- The control panel can queue a reports-only release. An approved fresh report can trigger publication automatically, with periodic reconciliation after missed events; reports-only releases can run alongside collection work while release jobs remain serialized.
- Japan's scheduled import processes a recent cache window and newly downloaded files, refreshes recent source weeks, and batches observation writes. Explicit local and forced imports retain their historical selection.
- Report preparation and observation processing move expensive work off the worker's event loop; report aggregation avoids repeated per-group scans. Transient task-claim errors no longer terminate the worker.
- Full Pages releases retain a verified deployable snapshot for later shared-asset compatibility deployments. A compatibility deployment checks the snapshot against the live release ID and content digest and stops if no matching artifact is available.

### Fixed

- Main-site public shared assets allow cross-origin access by both Papers and Reports; the production response-header rule also supplies the header.
- The migration for quarterly and annual report types is applied to production. Report generation and publication remain subject to source availability and review gates.

### Validation and operational limits

- Two reports-only production runs with unchanged content completed in 91.2 and 64.8 seconds; each reused all 960 R2 objects and uploaded zero bytes. These timings include export, build, upload checks and public verification, and are not guarantees for changed content.
- A prior new-report release completed in 166.1 seconds. Unchanged report releases still render the report routes before reusing unchanged output files.
- Full main-site publication remains blocked until the exact previous Pages artifact is restored as a verified snapshot; the reports-only path is independent of this requirement.

## [0.17.1] - 2026-11-02

### Added

- Public hosting is three channels: Cloudflare Pages for the main site, GitHub for download partitions, and Cloudflare R2 (`papers.globalinfectiousdisease.com`) for research article pages, cards, and `site-data/research`. Release splits the Astro `dist`, enforces a Pages file-count budget, and incrementally syncs papers assets to R2 before deploy.
- The papers host has its own `robots.txt` and `sitemap.xml`. Article URLs are no longer listed in the main-site sitemaps. Canonical, hreflang, JSON-LD, social-card, RSS and search-index URLs for articles all point at the papers host through one module (`src/lib/papers.ts`).
- The control-panel Publish workspace shows a three-channel strip and preflight rows for papers storage and the Pages file budget. A failed release writes a `dist-inventory.json` under `exports/release-failures/<task>/` before artifacts are rolled back.
- R2 uploads carry a `Cache-Control` policy (HTML revalidates, JSON five minutes, the manifest is never cached), because an R2 custom domain does not read the Pages `_headers` file.
- The main-site `_headers` allow the papers origin to load `/_astro/*`, `/scripts/*`, logos, `/site-data/*` and `/release.json` cross-origin. Release adds any missing path instead of skipping the file when one rule already exists.
- Incremental Astro builds keep rendered article bundles in `data/cache/research-pages`, since release moves them out of `dist`.
- Model center probes native PDF support: it sends a PDF carrying a random marker and records whether the model read it, rechecked after 24 hours. An operator override (`runtime_native_files`) still wins.
- The ECDC Communicable Disease Threats Report adapter reads the archive back to the collection start date, not only the latest RSS issues. Africa CDC and PAHO collection page further, and WHO periodicals stop on the raw page so a page of policy articles no longer hides older disease articles.
- A worker that starts releases every model-work lease left by its predecessor, so a restart does not hold route permits until they expire.

### Changed

- Outbreak grouping and the public Situation export look back 1,100 days, so a routine short refresh no longer drops history.
- Documents are read with pdfium behind one lock; two threads inside pdfium crashed the worker.
- Anthropic routes use the model center's stored `/v1` root correctly (no `/v1/v1/messages`), and native calls set an explicit timeout so a large token ceiling does not trip the SDK's streaming guard.
- Peer review reads the first JSON object in a reply; prose or a second object after the payload no longer corrupts it.
- A published research article keeps its status when the classifier later returns "review". Only a hard exclusion, an integrity change or an editorial action changes it. Unchanged re-upserts no longer reopen automatic exclusions, and a summary whose parent is excluded is deferred instead of archived permanently, with earlier parent-caused archives restored.
- AI content governance holds content in review until what the reviewers read changes.
- The French research labels cover Meta-analysis, Commentary, Preprint and Prion, and `check-i18n` fails when a taxonomy label has no translation.
- The SEO output check understands wildcard redirects, ignores article URLs hosted on the papers host, and reports main-site sitemap entries that still redirect there. Weekly Research Radar links to articles go straight to the papers host.

### Fixed

- The data-release service read `data_release` settings one level too deep, so the R2 preflight and publish step would have raised `AttributeError`.
- The R2 bucket check signed a different path from the one it requested and always reported the bucket unreachable.
- Publishing an empty papers bundle would have deleted every object in the bucket. It is now refused.
- A failure while writing the release failure inventory can no longer hide the original error or skip restoring the backups.
- The Situation map loads markers from `/situation/map-data.json`, so the three-year history stays inside the page HTML gzip budget.

### Removed

- The runtime-route fields `runtime_provider_min_capacity`, `runtime_provider_auto_concurrency`, `runtime_provider_success_streak` and `runtime_provider_backoff_remaining_seconds` from the API and the control panel.

## [0.16.2] - 2026-11-01

### Added

- The Situation alert timeline is drawn like a commit graph. Alerts run newest first, each country has a coloured line, and a line only takes a column while that country is active, then bends into the gap when it ends. A small legend names the colours.
- The Situation timeline shows the latest 30 alerts and a "show earlier" button for the rest. The row for the selected place is always shown.
- Places in the Situation export carry coordinates from a gazetteer when the report names an admin area, so points land where the report says instead of at the country centre.
- A control-panel CI job runs the fixture-only Playwright suite with no password set and uploads screenshots, so reviewers can see the panel without logging in. A Situation page smoke test checks the timeline and the filter-to-map flow.
- A model route can be limited to one or more clock windows, including a window that crosses midnight. The schedule uses an IANA timezone (default Asia/Shanghai). Outside that window the route is not selected and automatic health probes skip it, so a night-discount model stays idle during the day. An empty schedule remains available all day.

### Changed

- The Situation map re-centres and re-zooms to the visible places whenever the filters change, leaves the left panel clear, and resets to the world view.
- The Situation filters are two columns and the legend is a single line with plain age bands.
- Timeline lane colours were changed to separate them better from the map's age colours.
- Completion requests no longer send a token cap. Route `max_tokens`, `runtime_completion_max_tokens`, probe budgets, and agent output budgets are not forwarded. Anthropic calls still send that API's required ceiling because the protocol rejects a request without the field. Health probes wait up to 180 seconds by default, and at most 600.
- The task worker claims ordinary work, data release, and AI parent tasks on separate lanes. AI parent slots follow the sum of routable per-model capacity. A release still blocks crawl writes and AI while it runs.
- A gateway that rejects an optional parameter (temperature, thinking, response format, and the same class of fields) is retried once without that field. Assistant text is read from the answer content after `<think>` / `<thinking>` blocks are removed, so a new reasoning model does not need its own parser.

### Removed

- The model-center control for a maximum completion length.

## [0.16.1] - 2026-10-31

### Changed

- The public Situation page is now a map that fills the viewport under the site header. Filters, the place list, and the alert timeline float over the map instead of sitting in a document column.
- Opening a place joins the official alert to the rest of the catalogue: the national surveillance series (latest value, change, and a short sparkline), the disease page, the country page, and research for that disease and country. Clicking the country itself opens the same join when there is no alert. Country, disease, and country-disease pages link back onto the map.
- Reports from the last 30 days breathe on the map. A faster pulse is the last 7 days. Older points stay still. The pulse encodes recency, not severity, and it stays off when the visitor prefers reduced motion.

## [0.16.0] - 2026-10-29

### Added

- A model route's deep-thinking switch is configurable from the control panel (`thinking_mode`: provider default, on, off) and shared by health probes and production calls, instead of being hard-coded for one provider.
- Peer review draws its author and reviewer from a weighted pool of every active route (`ReviewRoutePool`) instead of one fixed pair, so review keeps running when a route cools down or fails, and load spreads toward routes with spare capacity and a better recent success rate. Knowledge and literature backlog drains share the same pool, with up to 192 reviews in flight and an automatic retry on other routes if failover ever puts both roles on one model.

### Changed

- Situation now reads each report in full and extracts alerts (one disease in one place, with stated counts, dates, one quoted sentence and a link) instead of enriching titles. Two models review every extraction; PDFs go to models that read files natively (with an OCR fallback for image-only pages) and as extracted text to the rest.
- Alerts of one outbreak across several countries share one timeline. Periodic roundups with no unusual event become hidden monitoring products. Full text stays internal.
- Export schema is `gids.situation_alerts.v1` (`map.json`, `outbreaks/`, `weekly/`, `monitoring.json`); the release gate validates it.
- The control panel's Events page is rebuilt on the alert model: a reports tab (per-notice extraction, input mode, alert count) and an outbreaks tab replace the old notices/events/digests tabs. The AI model-call log is paginated and filterable by provider and result instead of showing only the latest 100 attempts.
- A completion's token budget is no longer capped by agent code; only a limit set on the model route itself applies, so a long structured reply from a reasoning model is no longer cut off mid-object.
- The dashboard services stop cleanly on a systemd restart or deploy: a `SIGTERM` stop no longer reports itself as a failure.

### Removed

- Title-based AI tables (assessments, events, digests, keyword entities) dropped in migration `0021_situation_alerts`; reports are re-read from their full text.
- The standalone `scripts/export_international_news.py` exporter, `global_sources/export_static.py`, and the v3 docs and backtest file.

### Validation

- Python suite: 1,861 tests pass (1 unrelated e2e flake under concurrent load reproduced as a pass in isolation).
- Dashboard: `tsc` clean, 10 Vitest tests pass; generated OpenAPI client refreshed to match the new routes.
- Astro: style, i18n, and SEO checks pass; production build 6,932 pages.

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
