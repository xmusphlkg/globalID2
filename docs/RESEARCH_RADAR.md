# GIDS Research Radar

Research Radar connects current infectious-disease literature to the existing
GIDS disease catalogue and surveillance pages. It is an additive domain: source
metadata, editorial state, and public site projections remain separate from
surveillance observations.

## Data flow

1. The `sync_literature` task reads Crossref records by index date from the 31
   journal registry entries in `configs/literature/journals.json`. A stable
   timestamp-boundary resume token prevents capped runs from replaying or
   starving records that share the same Crossref index time. A separate capped,
   rotating controlled-query plan searches disease aliases, named pathogens,
   MeSH terms, vaccines, and antimicrobial-resistance terms in Crossref and
   Europe PMC; its nested checkpoint prevents a large vocabulary from making a
   single run unbounded.
2. WHO IRIS OAI-PMH supplies licensed Dublin Core metadata for official
   guidance and technical documents. The client is pinned to the reviewed WHO
   HTTPS endpoint, reads metadata only, never follows document links, resumes
   within a capped OAI page without loss, and isolates provider failure from the
   core journal sync. Europe PMC enriches identifiers and biomedical metadata, OpenAlex supplies
   controlled topics/keywords/concepts, and Unpaywall supplies validated lawful
   open-access locations. Provider failures remain isolated and observable.
3. A transparent lexical pass and a controlled-metadata second pass link
   diseases, ISO countries, topics, and study types. Every link retains its
   confidence, matched terms, and provenance. Controlled pathogen, pathogen
   type, population, and human/animal/plant/basic/One-Health domain evidence is
   stored alongside those links. Animal-only and basic-laboratory candidates
   cannot auto-publish; plant-only candidates are excluded. The discovery
   score helps ordering; it is not a quality score.
4. Stable upserts deduplicate in the order DOI, PMID, PMCID, OpenAlex ID, then
   the exact stable article ID. Overlapping incremental windows are therefore
   idempotent without risking false merges from fuzzy title similarity. Each
   run records its own watermark, counts, and error state.
5. The versioned autopilot publishes or excludes records that pass deterministic
   quality gates. Editors see only the exception band and can override or lock
   any publication decision.
6. The established site data export writes only published, integrity-safe
   records with both English and Chinese structured summaries to
   `astro-site/src/data/research`. Metadata-only records remain withheld rather
   than creating thin indexable pages. A fail-closed release validator runs
   before files cross the public boundary.

## Evidence enrichment and knowledge graph

Research Radar has two deliberately separate advanced-result paths:

- The public knowledge graph is deterministic. It connects published articles
  to diseases, pathogens, countries, topics, study designs, populations,
  settings, interventions, and policy concepts using versioned,
  confidence-bearing links. Population/setting statements are admitted only
  from published bilingual structured summaries; model output cannot silently
  create untraceable public nodes or edges.
- Model enrichment uses the existing Model Center routing, caching,
  retry, and provider controls to create English and Chinese structured summary
  drafts. It is limited to open-access records by default, grounded in one
  article at a time, and removes fields with long verbatim overlap. Summaries
  that pass the configured quality, evidence-trace, source-fingerprint, and
  required-field gates publish automatically; the rest remain private as
  exceptions.

Generation stores the model/provider, source fingerprint, field-level evidence
classes, confidence, quality score, token usage, and editorial decision. Prompt
and raw provider payloads are not copied into the public site projection.

## Surveillance evidence bridge

The release projection connects the latest eligible Situation Room snapshot to
the published literature catalogue without creating a second signal detector.
The relationship is deterministic and has two explicit levels:

- `exact_disease_geography` requires classifier confidence of at least `0.78`
  for both the disease and a geography named by the signal, a verifiable
  publication date, and publication within the 730-day evidence window.
- `disease_context` requires the same disease threshold but does not claim that
  the article studies the signal geography, validates the signal, or explains
  its cause.

An evidence gap means that the current published Research Radar catalogue lacks
an exact disease-and-geography match. If disease-level context exists it is
reported as a geography-depth gap; otherwise it is a catalogue-depth gap. These
labels never assert that relevant research is absent outside GIDS.

Situation Room visibility remains authoritative. A shadow snapshot is visible
only as a development preview and in control-panel readiness metrics; it is not
rendered into the production Research Radar page. Enabling public Situation
Room output and completing the normal release makes the same projection public.

## Evidence-gap discovery lifecycle

The bridge now has a durable, reviewable acquisition loop rather than a
build-only warning:

1. `refresh_from_snapshot` reconciles the latest eligible Situation Room
   snapshot into one persistent gap per signal and disease. It records priority,
   source report, review priority, attributable risk assessment when present,
   geography, and a transparent provider query
   plan.
2. The `discover_literature_gaps` task sends bounded disease-and-geography
   searches to Crossref and Europe PMC. Disease-only fallback runs only when an
   exact query is sparse.
3. Results pass through the same normalization and classifier used by the
   journal sync. Candidates are ranked by relation level, confidence, discovery
   score, and publication date; only the configured top candidates per gap are
   retained for a new review batch.
4. New records and relationships are evaluated by
   `research-radar-autopilot.v1`. High-confidence peer-reviewed records are
   confirmed and published automatically. Weak links, incomplete metadata,
   preprints, integrity flags, future dates, and borderline scores are rejected
   or retained as private exceptions according to the policy.
5. Only a published article with a confirmed, date-verifiable, current-window
   exact relationship closes the public coverage gap. Older matches remain
   clearly labelled historical context. Rejected relationships are explicitly
   suppressed from future public projections.

The lifecycle states are `open`, `searching`, `review`, `no_results`, `error`,
`covered`, `dismissed`, and `inactive`. Searches have a retry watermark and all
runs write counts/errors to the normal literature ingest-run log. Manual and
scheduled executions use the existing task worker, schedule registry, and task
audit interfaces. Candidate relationships outside the configured per-gap queue
limit are retained internally as `deprioritized` until autopilot evaluates
them; final automatic decisions retain the original ranking metadata.

Control-plane endpoints:

- `GET /api/v1/research-radar/gaps`
- `POST /api/v1/research-radar/gaps/refresh`
- `POST /api/v1/research-radar/gaps/discover`
- `PATCH /api/v1/research-radar/gaps/{gap_id}`
- `PATCH /api/v1/research-radar/evidence-links/{link_id}`
- `POST /api/v1/research-radar/automation/run`

Provider query contracts follow Crossref `/works` bibliographic queries with
publication-date/type filters and Europe PMC `/search` core results with
`FIRST_PDATE` bounds. The exact generated query is stored on each gap so an
editor can audit what was searched.

## Autopilot policy and exception review

Autopilot is enabled with `LITERATURE__AUTOPILOT_ENABLED=true`. It has four
independent, configurable gates:

- article publication requires current integrity state, a non-future date,
  peer review, a stable DOI/PMID/PMCID, title, journal, authors, and either a
  confirmed signal relationship or the configured discovery/disease score;
- exact signal links default to confidence `0.78`, while disease-context links
  default to `0.82`; context never closes a geographic evidence gap;
- weak candidate relationships are automatically rejected, and clearly
  irrelevant records below the exclusion score are removed from the exception
  queue;
- model summaries default to quality `0.90` and additionally require a current
  source fingerprint, valid field-level evidence traces, minimum field
  confidence, and all critical summary fields. Failed generations retry up to
  `LITERATURE__AI_MAX_QUALITY_ATTEMPTS` times before entering the exception
  queue; a changed source fingerprint automatically reopens an auto-published
  summary for regeneration.

Every decision stores the policy version, action, timestamp, actor, thresholds,
and reasons. Article changes also write a normal status event. Explicit control-
plane edits set `editorial_locked` and always take precedence. Disabling
`LITERATURE__AUTOPILOT_ENABLED` is the kill switch; it stops new decisions but
does not erase the audit trail or silently revert published content.

Use a no-write simulation before changing thresholds:

```bash
venv/bin/python scripts/run_literature_autopilot.py
```

Dry-run is the default. Persisting deterministic publish/exclude/defer/archive
decisions requires the explicit `--apply` flag. Deferred future-dated articles
and summaries waiting on an article decision retain `review` status plus an
auditable `autopilot.decision=defer` marker; summaries whose parent was excluded
move to `archived`. These objects remain available for re-evaluation and audit
but no longer consume the actionable human-review budget.

```bash
venv/bin/python scripts/run_literature_autopilot.py --apply
```

When classifier aliases, controlled metadata rules, or their version changes,
rehearse and then backfill stored records before the next public release. This
path makes no provider requests and preserves editorial publication decisions:

```bash
PYTHONPATH=. venv/bin/python scripts/reclassify_literature.py --dry-run
PYTHONPATH=. venv/bin/python scripts/reclassify_literature.py
```

## Public collections and feeds

The published catalogue now projects several first-class collections from the
same release artifact:

- disease evidence hubs at `/research/diseases/{slug}/`;
- geographic collections at `/research/countries/{code}/`;
- public-health topic collections at `/research/topics/{topic}/`;
- factual ISO-week briefs at `/research/weekly/{week}/`;
- a separately reviewed, prominently labelled preprint collection at
  `/research/preprints/`;
- integrity notices at `/research/integrity/`;
- an interactive provenance-bearing graph at `/research/graph/` and bilingual
  evidence retrieval at `/research/ask/`;
- full and scoped RSS feeds for diseases, countries, topics, study types,
  reviews/guidelines, peer-reviewed records, and preprints.

Article pages include deterministic related-research recommendations with
stable identifier deduplication. ISO-week briefs cite the exact released
articles behind each finding, separate monitoring context from literature
evidence, disclose evidence gaps, and identify themselves as automated rather
than editor-reviewed unless a named human completes the content-bound review
workflow below. Ask GIDS likewise separates exact evidence, background
evidence, and gaps; it does not make causal, clinical, or disease-risk claims.

Disease hubs place monthly GIDS reported records beside Research Radar
publication volume on independent scales. This is a navigation/comparison
timeline, not a causal model. Future-dated publication metadata is excluded from
recent metrics, topic movement, and weekly briefs until its date arrives.

## Copyright and integrity boundary

- The public export contains bibliographic metadata, source links, GIDS tags,
  and original GIDS summary fields only.
- Raw abstracts are retained internally for classification and are never copied
  into public JSON.
- External model processing is opt-in. With the default
  `LITERATURE__AI_REQUIRE_OPEN_ACCESS=true`, only records identified as open
  access are eligible for model enrichment.
- PDFs, publisher figures, tables, and graphical abstracts are not downloaded
  or hosted.
- Publisher RSS/Atom is discovery-only: the ingester reads the trusted feed's
  title, identifier/DOI, article link, journal, and publication timestamp. It
  deliberately ignores feed summaries/content and never follows article links
  or downloads full text.
- Retractions and expressions of concern cannot be published. Corrections and
  integrity changes are recorded as status events and projected through a
  minimal-field integrity-notice stream. Private source/event payloads never
  cross that boundary.
- A DOI or lawful open-access URL points readers to the original source.

## Operations

Apply Alembic migrations through `0008_literature_evidence_gaps` before enabling the module. Manual
sync is available when `LITERATURE__ENABLED=true`. Recurring sync additionally
requires `LITERATURE__SCHEDULE_ENABLED=true`; its default cadence is 15 minutes.
Use a monitored contact address in `LITERATURE__CONTACT_EMAIL` so Crossref
requests identify the operator.

### Weekly brief human review

Generated weekly briefs remain labelled
`automatically_compiled_not_editorially_reviewed` by default. A real reviewer
must inspect the cited findings, monitoring context, and evidence gaps, then run
the review CLI with their name and role. The command is a dry-run unless
`--apply` is supplied:

```bash
PYTHONPATH=. venv/bin/python scripts/review_research_weekly_brief.py \
  --week 2026-W33 \
  --reviewer-name "Full reviewer name" \
  --reviewer-role "Infectious disease editor" \
  --attest-reviewed
```

After checking the dry-run output, repeat with `--apply`. The v2 registry binds
the signature to a SHA-256 fingerprint of the exact public evidence. Any later
change to a cited finding, monitoring relation, or evidence gap invalidates the
old review automatically and restores the automated/not-reviewed label. The
registry update is atomic; replacing an existing week additionally requires
`--replace-existing`. Do not use service accounts, model names, placeholders,
or invented reviewer identities.

### Weekly brief AI quality review

AI review is a separate quality-control signal, never an editorial signature.
It receives only public `cited_findings`, `monitoring_context`,
`evidence_gaps`, and `methodology`; browsing, retrieval, outside knowledge,
abstracts, private notes, and database rows are excluded. Deterministic checks
run first. The model must return one bounded JSON object using an issue-code
allowlist. Prose, unknown codes, malformed output, unavailable routes, and
missing credentials all fail closed; raw output and reasoning are not stored.

The feature is off by default. A configured Model Center route can run it on
the existing `ENRICH_LITERATURE` worker cadence:

```dotenv
LITERATURE__WEEKLY_AI_REVIEW_ENABLED=true
LITERATURE__AI_ENRICHMENT_SCHEDULE_ENABLED=true
```

The task mode is `weekly_ai_review`, or
`summaries_and_weekly_ai_review` when summary enrichment is also enabled. The
manual CLI is dry-run unless `--apply` is supplied:

```bash
PYTHONPATH=. venv/bin/python scripts/ai_review_research_weekly_briefs.py --week 2026-W33
```

A pass is shown as `ai_reviewed` with an explicit “not editorial review”
disclosure. Content changes invalidate it, and a matching human review always
wins. Failures keep the public unreviewed label and fail a scheduled worker
task with `weekly_brief_ai_review_failed_closed`, so health cannot appear green
while the route is failing; site generation remains available.

### Crossref capacity and catch-up

The curated 31-journal stream is globally ordered, not divided into 31
independent quotas. The Crossref client keeps a proportional look-ahead page for
each journal and performs a k-way merge by index timestamp and stable record ID.
This preserves the global `MAX_RECORDS_PER_RUN=300` boundary and fair resume
semantics without fetching 300 records from every journal and discarding most
of them. Checkpoints expose `records_prefetched`, `lookahead_records`,
`pages_fetched`, `fetch_efficiency_ratio`, and per-journal page state.

Production observation on 2026-08-17 showed roughly 300 records per 21 minutes
at the active index boundary, while a bounded run took about 3–5 minutes. That
evidence supports the 15-minute normal cadence; increasing the record cap would
also require proportionally increasing the Europe PMC/OpenAlex/Unpaywall budget
and was therefore not used as the default fix. When a scheduled run remains
truncated, `LITERATURE__CATCH_UP_ENABLED=true` atomically pulls the next run
forward to `LITERATURE__CATCH_UP_INTERVAL_MINUTES` (default 5). The existing
active-task check still permits only one sync at a time.

Accelerated catch-up is also subject to editorial backpressure. Before pulling
the schedule forward, the worker counts the distinct article IDs currently in
article review or summary review. It uses a set union, so an article with an
article exception and two bilingual summary exceptions still counts once; it
does not reuse historical autopilot counters. Records explicitly marked with
`metadata.autopilot.decision=defer` or
`generation_metadata.autopilot.decision=defer` are inactive, as are archived
summary statuses and explicit `archive` decisions. Missing or unknown decision
metadata remains active fail-safe; if either the article or one of its summaries
is still active, the article remains in the union. The worker conservatively adds
the configured maximum records for the next run to that observed count. If the
projected upper bound reaches
`LITERATURE__CATCH_UP_MAX_EXCEPTION_BACKLOG` (default `500`), or if the read-only
count fails, it does not schedule the five-minute follow-up. The already
persisted normal 15-minute run remains in place, so source ingestion slows but
does not stop completely. Task results expose only integer
`catch_up_backlog_observed_count`, limit/projected counts, the strict
`catch_up_resume_below_backlog` boundary, any
`catch_up_required_backlog_reduction`, the boolean
`catch_up_paused_backpressure`, and stable `catch_up_status` /
`catch_up_next_action_code` values; titles, IDs, and error text are never
included. Because equality is fail-closed, catch-up resumes only when the
observed backlog is strictly below `limit - max_records_per_run`. The configured
limit must therefore exceed one maximum batch.

The earlier `next_run_at` is stored in `scheduled_job_states`. Scheduler startup
loads that timestamp and an overdue run remains due immediately after a restart.
Status/dashboard reads never roll an overdue persisted timestamp forward; a
read path must not consume scheduled work. A false atomic advance is also
verified against persisted state and reported as either `already_scheduled` or
`schedule_persistence_unavailable`, never as a silent success.

Each run records `source_catch_up_required` and
`source_remaining_index_span_seconds`; the checkpoint records the equivalent
`catch_up_required` and `remaining_index_span_seconds`. Catch-up is complete only
when `source_truncated=0`. After that, the next run continues at the committed
watermark; it does not reopen the former two-day overlap. Crossref index dates
are update watermarks, and the inclusive second-resolution boundary already
provides a small replay without consuming the batch with two days of duplicates.
`LITERATURE__INDEX_OVERLAP_DAYS` remains a deprecated compatibility setting and
defaults to `0`; use an explicit manual `since` only for a deliberate historical
replay. Do not increase the health source-lag threshold to make a backlog appear
healthy: source watermark lag remains the authoritative freshness SLO. If repeated truncated runs do not
reduce the remaining index span, stop automatic catch-up and investigate
Crossref errors, worker duration, enrichment throttles, or an abnormal source
volume increase before changing limits.

### Optional publisher Online First feeds

Crossref remains the primary incremental source. To reduce the Online First
delay for a small set of journals, set `LITERATURE__PUBLISHER_RSS_ENABLED=true`.
The only URLs eligible for polling are the HTTPS feeds declared in
`configs/literature/publisher_feeds.json`; each entry must have a unique
`feed_id`, a trusted journal/ISSN, and an exact `allowed_hosts` entry. Redirects
outside that host list are rejected. Treat changes to this file as a source
allowlist change requiring review—do not accept user-supplied feed URLs.

The poller sends `If-None-Match` and `If-Modified-Since` when a feed supplies
validators. Per-feed validators and stable entry IDs are committed inside the
completed literature ingest checkpoint under `checkpoint.rss`. If
`LITERATURE__MAX_PUBLISHER_RSS_RECORDS` truncates a feed batch, the new validator
is intentionally withheld until the remaining stable IDs have been consumed;
this prevents a later `304 Not Modified` from skipping entries. Feed failures
are isolated and recorded in the run counts/checkpoint, while the last committed
validator remains available for recovery.

RSS records pass through the same DOI/PMID/PMCID/OpenAlex/article-ID
deduplication, classification, integrity, editorial-review, and publication
gates as Crossref records. RSS does not assert open-access rights and cannot by
itself make a record public. Relevant bounds are:

- `LITERATURE__MAX_PUBLISHER_RSS_RECORDS` (default `50`)
- `LITERATURE__PUBLISHER_RSS_CONCURRENCY` (default `3`)
- `LITERATURE__PUBLISHER_RSS_MAX_FEED_BYTES` (default `2000000`)
- `LITERATURE__PUBLISHER_RSS_SEEN_ID_LIMIT` (default `2000`)

Optional metadata enrichment is fail-open to the editorial-safe boundary.
Europe PMC, Unpaywall, and OpenAlex failures are isolated per provider so a
successful Crossref batch can still be normalized and stored; later providers
continue even if an earlier one fails. Run counts always include the integer
`enrichment_errors`, provider-specific error counters, and the bounded
`enrichment_failed_providers` name list. They never include request URLs,
exception text, or response payloads. A record that otherwise qualifies for
automatic publication is held in `review` when the enrichment set is degraded,
while an existing current record keeps its editorial publication state.
Integrity exclusions still take precedence. Crossref failure remains fatal for
the ingest run because it is the primary incremental source.

Every new core ingest audit row records its owning `task_uuid` both in a
dedicated indexed field and in the initial checkpoint. If task recovery proves
that exact task's worker lease expired, it terminalizes only that task's still
running ingest row before requeueing the task. Legacy rows without an ownership
key are never inferred from worker or timestamp proximity; operators inspect
and reconcile them with the default-dry-run
`scripts/reconcile_stale_literature_ingest_runs.py` command documented in the
health runbook.

### Official public-health guidance metadata

`LITERATURE__OFFICIAL_GUIDANCE_ENABLED=true` enables bounded discovery from
the WHO Institutional Repository for Information Sharing at the pinned
`https://iris.who.int/server/oai/request` endpoint. Only OAI headers and Dublin
Core fields are parsed. The client does not download PDFs or follow landing-page
links. Descriptive metadata is treated as an abstract source only when the
record declares a recognized open licence; otherwise it remains private source
metadata. Capped pages resume with the same request token plus consumed record
IDs, and the committed state is stored at `checkpoint.official_guidance`.

WHO records use the same classifier, DOI-first deduplication, bilingual-summary,
integrity, editorial, and public release gates as journal articles. An OAI
record therefore cannot become public merely because WHO supplied it. The
default per-run cap is controlled by `LITERATURE__MAX_OFFICIAL_GUIDANCE_RECORDS`
(`60`).

### Controlled high-recall discovery

`LITERATURE__CONTROLLED_DISCOVERY_ENABLED=true` rotates a deterministic query
plan across disease aliases, controlled pathogen names, MeSH expressions,
vaccines, vaccine safety, and AMR. Query strings, plan fingerprint, selected
batch IDs, retries, and next offset are retained in
`checkpoint.controlled_discovery`. Per-run query and record caps prevent this
high-recall path from replacing the curated core-journal path with an unbounded
search. Results from both providers enter the same exact-identifier deduplication
and semantic classification pipeline.

### Existing-library metadata backfill

The OpenAlex/Unpaywall backfill command is dry-run by default. It reads only
DOI-bearing records and reports prospective changes without writing the
database or checkpoint:

```bash
PYTHONPATH=. venv/bin/python scripts/backfill_literature_metadata.py --limit 100
```

Use a small explicit apply canary before a controlled full run. Each invocation
plans against explicit coverage targets (95% by default), skips provider
metadata already present, and shrinks the request batch to the remaining
coverage deficit. Apply mode commits one batch at a time and records independent
provider cursors plus the last committed database ID in
`data/cache/literature_metadata_backfill.json`; rerunning the same command
resumes automatically. A provider failure exits non-zero and leaves that
provider's cursor before the affected batch while allowing another successful
provider cursor to advance safely.
Run only one apply process per checkpoint/database at a time.
The CLI enforces a workspace-wide non-blocking apply lock, including when two
operators choose different checkpoint files; a concurrent writer fails before
opening a database session.

```bash
PYTHONPATH=. venv/bin/python scripts/backfill_literature_metadata.py \
  --apply --limit 100 --batch-size 25 --concurrency 2 --min-interval-seconds 0.1
PYTHONPATH=. venv/bin/python scripts/backfill_literature_metadata.py \
  --apply --limit 500 --batch-size 50 --concurrency 2 --min-interval-seconds 0.1
```

The CLI never performs an unbounded invocation: `--limit` defaults to 500.
The limit counts DOI-bearing rows that are actually missing metadata from at
least one active provider; already-covered rows do not consume the budget.
Repeat the second command until `target_reached=true` or the provider pass ends
with an operator action code.

Override a target for a controlled catch-up with `--openalex-target 0.98` or
`--unpaywall-target 0.98`. Results expose `coverage_before`, `coverage_after`,
per-provider deficits, `target_reached`, and a stable `next_action_code`. A full
pass that remains below target ends as `completed_below_target` so repeated
provider misses do not spin forever; inspect identifier quality or provider
coverage before beginning a new pass.

Use `--providers openalex` or `--providers unpaywall` for an isolated provider
run. Provider selections are part of the checkpoint identity; use a separate
`--checkpoint-file` or `--no-resume` when changing that selection. The command
preserves publication status, featured state, classifier links, summaries, and
editorial metadata. It updates only stable OpenAlex ID, lawful OA evidence,
source URLs, and bounded internal provider metadata. OpenAlex persistence is
allowlisted to topics, institutions, author country codes, citation counts, and
bounded referenced/related work IDs; abstract indexes, full text, and unbounded
provider payloads are discarded.

Model enrichment additionally requires a tested Model Center route and
`LITERATURE__AI_ENRICHMENT_ENABLED=true`. Set
`LITERATURE__AI_ENRICHMENT_SCHEDULE_ENABLED=true` to process the next eligible
batch continuously (default configured cadence: one hour). Keep
`LITERATURE__AI_REQUIRE_OPEN_ACCESS=true` unless the operator has separately
reviewed source terms and model-processing rights. Summaries that fail any
automatic gate remain visible in the control panel for exceptional review.

Targeted gap discovery is enabled with
`LITERATURE__GAP_DISCOVERY_ENABLED=true`. Recurring discovery additionally
requires `LITERATURE__GAP_DISCOVERY_SCHEDULE_ENABLED=true`; its default cadence
is 12 hours. `LITERATURE__GAP_DISCOVERY_CANDIDATE_LIMIT` caps the ranked review
relationships retained per gap (default 12), while
`LITERATURE__GAP_DISCOVERY_RECORDS_PER_GAP` bounds each provider fetch. Operators
can reconcile or run discovery without the API using:

```bash
venv/bin/python scripts/discover_literature_gaps.py --refresh-only
venv/bin/python scripts/discover_literature_gaps.py --limit 8
```

The data release mechanism already includes the generated Research Radar files.
Literature task completion joins the same automatic release trigger when a
public decision actually changes. A no-op scheduled enrichment run does not
cause a release. No separate publishing command is needed.

For a local release rehearsal, regenerate the projection and run the fail-closed
validator before the Astro build:

```bash
PYTHONPATH=. venv/bin/python scripts/export_literature_site_data.py
PYTHONPATH=. venv/bin/python scripts/validate_research_release.py
```
