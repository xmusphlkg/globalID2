# GIDS Research Radar

Research Radar connects current infectious-disease literature to the existing
GIDS disease catalogue and surveillance pages. It is an additive domain: source
metadata, editorial state, and public site projections remain separate from
surveillance observations.

## Data flow

1. The `sync_literature` task reads Crossref records by index date from the core
   journal registry in `configs/literature/journals.json`.
2. Europe PMC enriches matching DOI records with PMID, PMCID, abstract metadata,
   and open-access status.
3. Transparent first-pass rules link diseases, countries, topics, and study
   types. The discovery score helps ordering; it is not a quality score.
4. DOI-first upserts make overlapping incremental windows idempotent. Each run
   records its own watermark, counts, and error state.
5. The versioned autopilot publishes or excludes records that pass deterministic
   quality gates. Editors see only the exception band and can override or lock
   any publication decision.
6. The established site data export writes only published, integrity-safe
   records to `astro-site/src/data/research`. The normal release job then builds
   and deploys the public pages.

## Evidence enrichment and knowledge graph

Research Radar has two deliberately separate advanced-result paths:

- The public knowledge graph is deterministic. It connects published articles
  to diseases, countries, topics, and study designs using the same versioned,
  confidence-bearing classifier links stored during ingestion. Model output
  cannot create public nodes or edges.
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
  for both the disease and a geography named by the signal.
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
   source snapshot, risk context, geography, and a transparent provider query
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
5. Only a published article with a confirmed exact relationship closes the
   public coverage gap. Rejected relationships are explicitly suppressed from
   future public projections.

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
venv/bin/python scripts/run_literature_autopilot.py --dry-run
```

## Public collections and feeds

The published catalogue now projects several first-class collections from the
same release artifact:

- disease evidence hubs at `/research/diseases/{slug}/`;
- geographic collections at `/research/countries/{code}/`;
- public-health topic collections at `/research/topics/{topic}/`;
- factual ISO-week briefs at `/research/weekly/{week}/`;
- the latest 50 published records at `/research/rss.xml`.

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
- Retractions and expressions of concern cannot be published. Corrections and
  integrity changes are recorded as status events.
- A DOI or lawful open-access URL points readers to the original source.

## Operations

Apply Alembic migrations through `0008_literature_evidence_gaps` before enabling the module. Manual
sync is available when `LITERATURE__ENABLED=true`. Recurring sync additionally
requires `LITERATURE__SCHEDULE_ENABLED=true`; its default cadence is six hours.
Use a monitored contact address in `LITERATURE__CONTACT_EMAIL` so Crossref
requests identify the operator.

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
