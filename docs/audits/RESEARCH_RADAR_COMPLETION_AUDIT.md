# Research Radar completion audit

Audit date: 2026-08-17 (UTC)

This document checks the Research Radar proposal item by item against the
implemented repository, the live local database, and the generated static
site.  “Complete” means implemented and locally accepted.  Actions requiring
production credentials, a real recipient audience, or a named human reviewer
are listed separately and are not represented as completed.

## Proposal checklist

| Proposal area | Status | Acceptance evidence |
| --- | --- | --- |
| Product and navigation | Complete | `/research/` is a first-class navigation destination and identifies the module as GIDS Research Radar. |
| Source strategy | Complete | Crossref and Europe PMC are primary sources; OpenAlex and Unpaywall enrich stored records; WHO IRIS OAI-PMH supplies official-guidance metadata; a bounded publisher RSS client supplies Online First metadata without following article links. |
| Core journal scope | Complete | `configs/literature/journals.json` contains 31 unique, checksum-valid ISSNs with non-empty journal names. |
| Incremental synchronization | Complete | Crossref uses index time, cursor pagination, upsert, and a boundary record-ID checkpoint that safely resumes when many records share one timestamp. Controlled discovery rotates bounded disease, pathogen, MeSH, vaccine, and AMR queries. |
| Deduplication | Complete with safe deviation | Stable order is DOI → PMID → PMCID → OpenAlex ID → exact stable article ID. Fuzzy title merging is deliberately not automatic because a false merge is harder to recover than a duplicate review candidate. |
| Metadata enrichment | Complete | The resumable OpenAlex/Unpaywall backfill preserves editorial state and stores only bounded provider allowlists. Live local counts: 1,475 OpenAlex IDs and 1,476 Unpaywall payloads among 1,480 articles. |
| Relevance and taxonomy | Complete | Version 5 classification combines lexical aliases with controlled Europe PMC/OpenAlex metadata. It covers disease, country, pathogen, pathogen type, public-health topic, study type, population, and human/One Health/research-domain gates. All 1,480 stored records are version 5. |
| Discovery ranking | Complete | GIDS Discovery Score exposes component values, weights, and contributions and does not claim to be a scientific quality score. Monitoring relation levels map to exact/context/candidate contributions without renormalizing incomplete weights. |
| Editorial and publication gate | Complete | Only published, bilingual, quality-gated records cross the public boundary; private abstracts/provider payloads do not. Preprints are isolated and integrity-blocked records fail closed. |
| Research homepage | Complete | Highlights, Latest Publications, Surveillance-linked Research, New Reviews & Guidelines, Emerging Topics, seven-day metrics, methodology, and transparent trend caveats are rendered. |
| Filters and catalogue | Complete | Disease, country, pathogen type, population, study type, journal, publisher, date, OA, peer-review status, and topic filters use a compact catalogue that retains every client-side filter field. |
| Article evidence pages | Complete | Bibliography, authors, publisher, article type, DOI/PMID, OA, structured bilingual summary, limitations, GIDS interpretation, related surveillance/research, integrity/version relations, source links, and AI provenance are present. |
| Disease/country/topic pages | Complete | Static facet pages, date-descending disease collections, related surveillance, publication timelines, and guidance/vaccine-policy evidence markers are generated. |
| Surveillance integration | Complete | Signal links distinguish exact, context, and historical context; exact evidence is limited to a 730-day window. The current Ebola signal has four current exact articles and no false evidence gap; the 1978 article remains historical context. |
| Evidence gaps | Complete | Gaps are created only after relation discovery and are reopened/closed as automatic links change. Current public snapshot has zero unresolved gaps; this is a data result, not a disabled feature. |
| Weekly Research Brief | Complete with human boundary | Bilingual weekly pages and a replay-safe email campaign are generated only from published bilingual structured summaries with article/DOI sources. Current briefs state that they are automatically compiled and not editorially reviewed; no expert identity is fabricated. |
| Ask GIDS Research | Complete | Deterministic bilingual retrieval ranks title, tags, structured entities, and bilingual summaries; it separates exact/background evidence, de-duplicates identifiers, and emits numbered source citations and safety boundaries. |
| Knowledge graph | Complete | The public graph contains article, disease, country, topic, study-design, pathogen, pathogen-type, population, intervention, and policy nodes with auditable edges and confidence/provenance rules. |
| RSS and subscriptions | Complete | All-research and scoped disease/country/topic/study/review/peer-review feeds are static and sitemap-listed. The current build contains 85 valid RSS documents. Subscription confirmation, preferences, weekly audience selection, idempotent campaigns, queueing, and delivery processing are implemented. |
| Preprints | Complete | Preprints have a separate warning page, RSS feed, sitemap route, editorial gate, and detail-page boundary. The current public count is zero, so the empty state is the correct output. |
| Retractions/corrections | Complete | Integrity events publish only safe projections for records that crossed the public boundary. Retractions and expressions of concern cannot remain indexable; corrections retain an auditable history. |
| SEO and sharing | Complete | Only bilingual, value-added evidence pages are indexable. Research sitemaps, JSON-LD, breadcrumbs, canonical/social metadata, and one 1200×630 card per public article are generated. |
| Compliance | Complete | No PDFs or graphical abstracts are hosted; RSS ignores body content; public artifacts exclude abstracts, full text, raw payloads, and provider payloads; DOI and original/OA links remain visible. |
| Operations | Complete | Scheduled sync/enrichment/gap jobs are enabled locally. Backfill, reclassification, release validation, digest dry-run, checkpoint recovery, and build-input consistency procedures are documented and tested. |

## Final accepted snapshot

- Database: 1,480 articles; 1,480 classification-v5 records; 1,475 OpenAlex IDs; 1,476 Unpaywall enrichments.
- Public release: 60 peer-reviewed evidence pages (44 current v5 records and 16 explicitly labelled historical seeds), 0 published preprints, 0 integrity alerts, 0 unresolved evidence gaps.
- Static outputs: 994 pages total; 60 article pages; 60 social cards at 1200×630; 85 valid Research RSS documents; 272 Research sitemap URLs; 60 compact catalogue rows.
- Publisher RSS live acceptance: both enabled Nature feeds returned 16/16 records with DOI and no feed error. The BMJ candidate is disabled because its advertised HTTPS endpoint currently downgrades to an HTTP-only feed.
- Release invariant: `scripts/validate_research_release.py` passes, and the Astro build now fails if `src/data/research/index.json` changes while static routes are being generated.

## Verification commands

```text
PYTHONPATH=. venv/bin/python -m pytest -q tests/unit/test_literature*.py tests/unit/test_research_digest_dispatch.py
# 111 passed

npm run test:research
# 13 passed

npm run test:seo
# 22 passed

npm run check
# 0 errors (16 existing hints)

cd cloudflare/subscriptions && npm test && npm run typecheck
# 69 passed; typecheck passed

cd astro-site && npm run build:astro
# 994 pages; performance budget passed
```

## Intentional external boundaries

The following are not silently performed by a repository-completion task:

- no production Cloudflare deployment or migration application;
- no real subscriber campaign creation or email delivery (the dispatcher was
  accepted in dry-run mode);
- no publisher RSS activation in the production environment (the global
  feature flag remains fail-closed until an operator approves the enabled
  feed set);
- no expert reviewer/byline is invented; a real reviewer can be recorded only
  after an actual editorial review;
- no HTML article crawling, PDF storage, graphical-abstract copying, or fuzzy
  title auto-merge.

