# Public site 0.18.2 — 2026-11-11

后续逐项复核与再次修订见 [公开站问题闭环报告](PUBLIC_SITE_ISSUE_CLOSURE_2026-11-11.md)。本文保留本次初始发布的时间、计数和证据。

## Release status

Published and verified on the production Main, Papers and Reports hosts.
App version: **0.18.2**. Pages deployment:
`https://3ffdef6f.globalidv2.pages.dev` (project `globalidv2`, production branch `master`).

Release task: `c6433552-87ca-423e-ba11-4b00776366e6`.
This maintenance release suppresses subscription dispatch.

## Disease literature

The disease surveillance page links to a dedicated Papers library, for example
`https://papers.globalinfectiousdisease.com/zh/research/diseases/influenza/`.
English, Chinese and French libraries contain both reviewed publications and
bibliographic records; the interface distinguishes their evidence availability.
Filters cover title/author/journal/identifier, country, topic, study type, years,
access, record type and publication status. Annual publication counts reflect all
matching records. PubMed, Europe PMC and Scholar links include visible, copyable
queries. These starting queries do not claim systematic-review completeness.

Each page displays 24 records, with previous/next and page selection. The URL
retains filters and the page number; changing filters resets the page. A slim
search index supplies filtering and trend counts. Complete cards are loaded only
from the immutable shards needed by the selected page, with bounded concurrency,
cached requests and retryable failures. Old cards are hidden while another page
loads or fails.

Library HTML, indexes and card shards move into the existing Papers R2 bundle.
Main Pages retains the surveillance pages and redirects former library URLs.
Papers reuses the main build's CSS, fonts, header, footer, theme and React runtime;
only the library layout adds styles. Content-addressed card shards upload before
mutable indexes, which upload before library HTML. Retired shards remain available
for 48 hours so cached indexes can finish loading.

## Public-site audit fixes

The original findings are in [the UX audit](PUBLIC_SITE_UX_AUDIT_2026-11-10.md).

| Finding | Resulting behavior |
| --- | --- |
| Common aliases and compound searches | Flu aliases rank influenza correctly; compound country/disease searches open focused Ask queries. |
| Stale comparison countries | Changing diseases removes invalid countries and announces the change. |
| Misleading chart source captions | Runtime captions and observed date ranges follow selected series and geography. |
| Language-switch state loss | Query parameters and fragments survive language changes. |
| Ask fetched the worldwide catalogue | Recognized entities select a relevant complete collection before filtering. |
| Search failure looked like an empty result | Explicit failure state and retry action. |
| Homepage and map counts differed | Both use the same deduplicated 30-day Situation scope. |
| Download totals and units were ambiguous | Current-file totals are separate from full history, with consistent decimal MB. |
| Unnatural or untranslated interface copy | Chinese labels, loading states, research enums and instructions revised. |
| Theme differed across hosts | Main, Papers and Reports share a parent-domain theme cookie. |
| Weak text contrast | Light/dark contrast improved in affected public views. |
| Relationship chips lacked targets | Chips identify the actual related entity. |
| Research count understated coverage | Counts include complete reviewed and bibliographic coverage. |

The shared GIDS loader uses a lightweight globe/orbit motif in SVG and CSS,
accessible status text and a reduced-motion mode. Public data views use the same
component and localized messages.

Live profiling also identified a literature-export hotspot: disease-title
association ran every alias regex against every title. An absent-literal guard
now skips impossible regex matches while retaining boundary, negation and
specificity checks. A 1,000-title sample from the real catalogue produced identical
associations; matcher median CPU time fell from 1.283 to 0.103 seconds (12.46 times
faster over three runs). This measures this function, not total export or page-load
time. The final generation regression run passed all 98 tests. The comparison is recorded in
[title-match-prefilter.json](benchmarks/public_site_v0182_2026-11-11/title-match-prefilter.json).

## Verification before publication

- Astro check: 417 files, zero errors and zero warnings.
- Chart unit tests: 67 passed, including independent SVG rendering of the lean
  chart registries; research unit tests: 34 passed; public presentation
  tests: 23 passed; Papers routing/sitemap tests: 8 passed.
- Release regression tests: 120 passed; Papers storage/publishing tests: 35 passed.
- Title matching and cache tests: 92 passed. The cache stores pure title
  associations only; candidate eligibility, integrity and database relationships
  are checked afresh. Changed titles, disease names/slugs, aliases and matching
  implementation invalidate cached results. Deleted or ineligible entries are
  pruned; damaged caches are safely recomputed.
- Browser scoped-collection tests: 6 passed, including public noindex records,
  historical seeds, explicit withdrawals, complete membership and unchanged bytes.
- Stable monthly library shards and complete per-disease cache keys: 13 passed.
- Production-bundle routing browser tests: 2 passed at 390 and 1280 pixels.
- Desktop/mobile browser scenarios cover search retry, aliases, comparison
  reconciliation, language continuity, cross-host theme, disease library routing,
  database queries, page loading/retry, filter reset, year validation and scoped Ask.
- Five accessibility scans across home, subscription and article views reported
  no violations; loader layout and reduced motion were also inspected.

Evidence and screenshots: [public_site_v0182_2026-11-11](benchmarks/public_site_v0182_2026-11-11/).
One development-server restart delayed hydration; the affected scenario passed
when rerun after readiness. A theme-test teardown race was fixed by awaiting
pending request handlers, and the theme scenarios passed again.

## Incremental literature updates

Article pages already use per-article content fingerprints and verified page
bundles. Template or shared-style changes require rebuilding affected templates;
ordinary literature additions can reuse unchanged pages. Library cache keys now
include the complete per-disease projection, including bibliographic-only records
and published preprints. The main surveillance page's literature count shares
that dependency, so new records cannot leave a stale count behind.

Title associations now have an atomic, checksummed local cache. A real
1,000-title cold/warm comparison returned identical output: 0.1485 seconds cold
versus a 0.0220-second warm median, including cache read/write and verification.
This is a component measurement, not an end-to-end release time.
The real release retry logged **202,280 reused associations and 76 computed**;
see [the live cache evidence](benchmarks/public_site_v0182_2026-11-11/title-cache-live-release.json).
Topic matching also skips absent literal terms before retaining the original
word-boundary check. A 1,000-title comparison returned identical topics and
reduced median CPU time from 0.664 to 0.036 seconds; see
[the topic benchmark](benchmarks/public_site_v0182_2026-11-11/title-topic-prefilter.json).
Changing that matching implementation deliberately invalidates its disease-title
cache once on the next export.

Database eligibility scanning and the normal Astro release still run. R2 skips
unchanged objects by content identity. This is incremental computation, rendering
and upload reuse; it does not claim to eliminate every build or publication step.

Card shards are grouped by publication month, with at most 24 cards in each
physical shard. Adding or backfilling a record changes that disease's index and
the affected month's shards; other months and diseases retain identical bytes.
Undated records have a separate bucket. The browser still displays exactly 24
results per page and fetches only the shards required for those results.

The production follow-up repaired stale browser Ask collections from the frozen
canonical snapshot without another database export or Astro render. This updated
424 scoped JSON files and reused 48,210 other local bundle objects. All 48,634
local entries were checked against the committed remote R2 manifest with no
checksum or size mismatch. Retained older social cards account for additional
remote-manifest entries. See
[the incremental publication receipt](benchmarks/public_site_v0182_2026-11-11/papers-collection-repair-publication.json).

## Production verification

The normal release task completed at `2026-11-10T18:57:50Z` (02:57:50 China time).
Subscription dispatch was suppressed. Production `release.json` matches release
`20261110T172935Z-1b4cdc3f3d90` and build digest
`3053f283aa60b52e887e7a0c3b0752554894896da558b52da0cd64eb7dd6641e`.
The [release receipt](benchmarks/public_site_v0182_2026-11-11/release-receipt.json)
records the exact identity; browser verification was performed independently of
the release job's optional built-in website verification.

All build gates passed: SEO inspected 32,200 pages without errors, and performance
inspected 32,228 HTML files. The largest route resource graph is **391,586 gzip
bytes**, below the unchanged 395,000-byte cap. A feature-specific chart registry
removed unused legend/mark-area components; heatmaps retain their continuous
colour mapping. The original build checks caught broken country/RSS links, a
truncated-title collision and the oversized chart graph before any publication;
each issue was corrected and the complete gates rerun. See
[build gates](benchmarks/public_site_v0182_2026-11-11/build-gates.json).

The final production browser checks passed:

- Public smoke: 11 checks at 390/1440 pixels, with no page errors or overflow.
  Version, aliases, compound-search routing, comparison language/query state,
  download labels and two-way theme continuity across all three hosts passed.
  Homepage and Situation both showed 227 places for the same data version.
- Disease library: 390/1280 pixels, 5,002 records, 24 visible cards per page.
  The first page read only 2 of 462 physical card shards (about 16.6 KB encoded);
  the next page added one shard (about 5.3 KB). The slim filtering index was
  approximately 0.97 MB encoded, fetched once. Filters reset to page one and
  publication-trend counts matched the complete filtered result set.
- Production-bundle tests also exercised a page spanning November/October and
  matched all 24 rendered titles to its 3 actual Papers shards. All three CSS
  files were byte-identical to the same Main build. The library HTML and catalogue
  are absent from the Main Pages bundle.
- Ask: the actual response contains **984 articles**, matching the current
  canonical reviewed collection and the library's evidence-summary count.
  “日本流感” uses one disease collection and zero worldwide catalogue requests;
  it returns 9 exact matches and cites JP NIID Weekly surveillance.
- Legacy library/catalogue URLs redirect to Papers; Chinese disease RSS returns
  HTTP 200; content-addressed card shards have immutable cache headers.

Production verification found two issues that local interception could not prove:
the R2 bucket initially lacked CORS, and the new scoped Ask reader reached legacy
133-record browser collections. The bucket now applies
[the production read-only CORS policy](../cloudflare/papers/cors.json). The
generator now refreshes complete disease/country/topic browser collections on
each export, and the existing snapshot was repaired and incrementally published.
The original failure evidence is retained; the final full browser rerun passed.

Cloudflare Speed Brain refused four speculative prefetches with explicit
`cf-speculation-refused` headers and empty bodies. These were recorded separately;
actual document navigation to the same URLs returned HTTP 200. No real resource
failures remain in the final smoke results.

Final evidence:
[public smoke](benchmarks/public_site_v0182_2026-11-11/production-public-smoke.json),
[literature and Ask](benchmarks/public_site_v0182_2026-11-11/production-literature.json),
[CORS](benchmarks/public_site_v0182_2026-11-11/production-cors.json),
[routing](benchmarks/public_site_v0182_2026-11-11/production-routing.json).
