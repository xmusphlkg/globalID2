# Papers hosting channel (R2)

Article and disease-literature HTML keep `/_astro/` references on the Papers host. The splitter copies
referenced CSS, scripts, hydration modules and their transitive fonts/imports
into the R2 bundle; missing dependencies fail the split. Assets upload before
HTML, and the preceding generation remains available for cached pages. Navigation,
logos and non-Papers data still resolve to the main site. New manifests carry
`local_build_assets: true`; their asset checksums are verified before skipping
the legacy Pages compatibility deployment. Legacy manifests still require the
exact verified previous Pages artifact.


Research article pages outgrew Cloudflare Pages’ free **20,000-file** ceiling.
Public hosting is therefore three channels:

| Channel | Content | Target |
| --- | --- | --- |
| Pages main | Countries, disease surveillance, situation, research overview and other collection pages, `_astro`, scripts | Cloudflare Pages (`globalidv2`) |
| GitHub downloads | Time-partitioned CSV/JSON/XLSX | `globalID2_data_download` `main` |
| R2 papers | Article and disease-literature HTML (en/zh/fr), slim disease literature catalogues, card PNGs, `site-data/research/*` | R2 `globalid-papers` @ `papers.globalinfectiousdisease.com` |

## Disease literature pages

The Papers bucket also needs a bucket-level CORS policy for main-site Ask
requests. Pages `_headers` files do not configure R2 responses. The checked-in
policy is [cloudflare/papers/cors.json](../cloudflare/papers/cors.json); apply it
with `wrangler r2 bucket cors set globalid-papers --file ../cloudflare/papers/cors.json`
from `astro-site`, then check the response using an `Origin` header for the main
site. Only production GIDS origins and GET/HEAD are allowed. See the
[Cloudflare R2 CORS documentation](https://developers.cloudflare.com/r2/buckets/cors/).

Browser disease/country/topic Ask collections must be generated alongside the
global browser index. `scripts/repair_browser_research_collections.py` can rebuild
them from the existing canonical research snapshot without a database export or
Astro render. Synchronize only those three directories into the existing Papers
bundle, refresh its manifest, and run the normal incremental R2 publisher. Do not
rerun the splitter against an already-split main bundle.

The complete disease literature interface is published on the existing Papers
host, beside the article summaries:

- `/{locale/}research/diseases/{slug}/` — the disease-specific page, with English
  at `/research/diseases/{slug}/` and Chinese/French at `/zh/` and `/fr/`.
- `/research/catalogue/diseases/{disease_id}.json` — a compact schema-v2 index
  for complete-catalogue filtering, sorting and publication trends. IDs are
  lowercase in URLs. It carries the record IDs, searchable/filterable fields,
  dates and shard references, rather than every full card or evidence summary.
- `/research/catalogue/diseases/{disease_id}/{YYYYMM}{page_index}-{content_hash}.json`
  — content-addressed card shards, each with at most 24 records. Pages are grouped
  by publication month (`000000` for undated records), so adding a paper only
  replaces its disease index and the affected month’s pages; historical months
  and other diseases keep their existing shard URLs. The browser
  loads only the shards needed for the current result page and reuses fetched
  shards while paging. Reviewed cards link to existing article summaries;
  bibliographic cards link to their original sources.

Disease surveillance remains on the main site at `/{locale/}diseases/{slug}/`.
Its literature link points to the Papers disease library. Legacy main-host
literature URLs redirect to the same path on Papers; canonical URLs, alternate
languages and sitemaps identify the Papers host directly.

The disease library uses the existing `ReportLayout` → `BaseLayout` and shared
`global.css`, header, footer, typography, theme and loading component. Its
component stylesheet adds only library-specific layouts, filters and trend
presentation. The split step copies the referenced shared CSS, React island,
loader styles, scripts and transitive dependencies from that same build to
Papers `/_astro/`; it does not maintain a second design system or fetch a later
main-site stylesheet at runtime. The catalogue JSON and its HTML are staged in
the same Papers bundle. New card shards are uploaded before the index and old
shard URLs are retained for 48 hours so cached indexes can still resolve them.
Main-site navigation and surveillance fetches continue to use the main origin.

## Release flow

1. Generate site data and build the full Astro `dist` (unchanged for local `npm run build`).
2. `scripts/split_papers_bundle.py` moves papers paths into `exports/papers-site/`, keeps build assets local while sending navigation to the main origin, appends redirects/CORS, and enforces the Pages file budget.
3. Digest and deploy use the **slim** `dist` only.
4. `scripts/publish_papers_r2.py` incrementally syncs `exports/papers-site` to R2, in parallel with GitHub downloads / raw archive. It lists object metadata, verifies local SHA-256 checksums, and compares single-PUT ETags with local MD5 hashes. Matching objects transfer no body bytes, including objects left by an interrupted attempt.
5. Cloudflare Pages deploy + production verification, then dirty-marker commit.

The report directory and report pages use a separate R2 bucket and direct
custom domain, like this Papers channel. See [`REPORTS_HOSTING.md`](REPORTS_HOSTING.md)
for the object-key layout, publication, and cadence-based generation.

## Configuration

Per-article sharing cards are temporarily disabled in
`shared/research-publishing.json` (`article_social_cards_enabled: false`). Article
pages use the main site's single `og-default.png` instead. The shared switch
controls PNG generation, incremental build paths, bundle splitting and the R2
upload boundary, including older staged bundles. It is included in build cache
fingerprints so the next build replaces cached article metadata.

Disabling cards does not delete existing R2 cards: prior manifest entries are
retained for cached-page compatibility, without uploading their bodies again.
Existing storage usage therefore does not immediately fall. Set the shared
value to `true` and rebuild/publish to restore per-article cards.

```text
DATA_RELEASE__PAGES_FILE_BUDGET=20000
DATA_RELEASE__PAGES_FILE_WARN=18000
DATA_RELEASE__R2_PAPERS_ENABLED=true
DATA_RELEASE__MAIN_SITE_ORIGIN=https://globalinfectiousdisease.com
R2_PAPERS_BUCKET=globalid-papers
R2_PAPERS_PUBLIC_BASE_URL=https://papers.globalinfectiousdisease.com
R2_ACCESS_KEY_ID=…
R2_SECRET_ACCESS_KEY=…
# optional; defaults to CLOUDFLARE_ACCOUNT_ID
R2_ACCOUNT_ID=
R2_ENDPOINT_URL=
# bounded concurrency, 1–32; persistent HTTP connection pool
R2_PAPERS_UPLOAD_WORKERS=32
```

Create an R2 S3 API token (Object Read & Write on `globalid-papers`) and put the
access key pair in `.env`. The Cloudflare account API token alone is enough to
list buckets for operations, but the publisher signs S3 PutObject requests with
the R2 access key.

## Control panel

Publish → preflight shows:

- Repository access (GitHub downloads)
- Hosting access (Pages)
- Papers storage (R2)
- Pages file budget
- Build tools / repository boundary

After preflight, a three-card channel strip summarizes Pages main, GitHub
downloads, and R2 papers readiness.

## Operator notes

- The splitter retains unchanged rewritten HTML only when its source hash,
  output hash, rewrite implementation and origins all match. Removed paths are
  reconciled locally; `papers-manifest.json` never includes itself.
- The uploader holds a per-bucket process lock, limits pending futures to twice
  the worker count, and retries transient transport/429/5xx failures. A failed
  upload drains in-flight requests without advancing the final manifest.
- Public verification precedes deletion of obsolete keys declared in the prior
  manifest. The new manifest is written last. This is a completion marker;
  independent objects are updated in place, not switched atomically as a site.
- Browser research shards use record-derived boundaries and content hashes in
  filenames, preserving the existing ordered-list client contract. The uploader
  finishes shard uploads before updating `research/index.json` and
  `research/catalogue.json`. Generation retains retired shards for 48 hours
  after their last active reference, including legacy numbered shards during
  migration, so cached indexes keep resolving. Cleanup remains manifest-scoped.
- For legacy bundles referencing main-site hashed `/_astro/` files, publish a compatibility
  Pages artifact containing the prior site plus those files before uploading R2
  HTML. Verify the new asset bytes and CORS on the public origin first. Unchanged
  assets skip this extra deployment. This prevents new R2 pages referencing
  unavailable styles while the final Pages deployment is still pending.
- The complete completion manifest retains its JSON schema and all fields, but
  uses a deterministic gzip HTTP representation when smaller. The S3 signature
  covers `Content-Encoding`, encoded length and encoded payload; readers accept
  old plain JSON and decoded or encoded gzip responses. Ordinary object bytes
  and their ETags are unchanged. R2 supports this metadata on
  [PutObject](https://developers.cloudflare.com/r2/api/s3/api/).
- Repeat a successful publication to measure an unchanged run: the summary
  should report `uploaded: 0` and `bytes_uploaded: 0`. Object listings still
  transfer metadata; no existing HTML, PNG or JSON bodies are downloaded.
- The public probe also uses HTTPX. It retries transient transport/429/5xx
  failures, falls back to a one-byte range GET when HEAD is unsupported, and
  retains the HTTP status in failure diagnostics. Authentication/403 failures
  remain terminal; verification is never skipped to complete a release.

- Main-site `_redirects` send `/research/articles/*`, `/research/diseases/*`,
  their locale variants, `/research/catalogue/diseases/*`, cards, and
  `/site-data/research/*` to the papers host with 301.
- Main-site `_headers` allow the papers origin to load `/_astro/*`, `/scripts/*`,
  and logos cross-origin. Papers pages also fetch `/site-data/*` and
  `/release.json` from the main origin (search index, release badge, site
  status), so those paths need the same CORS rule. The split step adds any
  missing path and leaves existing rules alone.
- R2 objects are uploaded with `Cache-Control` because a custom domain on R2
  does not read Pages `_headers`: HTML `max-age=0, must-revalidate`, JSON
  5 minutes, `papers-manifest.json` `no-store`, everything else one day.
- R2 cannot attach per-object security headers. Add a Cloudflare Transform Rule
  (Modify Response Header) on `papers.globalinfectiousdisease.com` that sets
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and
  `Referrer-Policy: strict-origin-when-cross-origin`. This is a dashboard
  setting, not part of the release, so check it once after the first publish.
- Article and disease-library URLs without a trailing slash are redirected by the main site to the
  same path on the papers host, where the R2 key has the trailing slash. Add a
  Transform/Redirect Rule on the papers host that appends `/` to extensionless
  paths, or check that `/research/articles/<slug>` and
  `/research/diseases/<slug>` resolve after the first publish.
- Failed releases retain generated bundles for checksummed retries and write
  diagnostic inventories under `exports/release-failures/<task_uuid>/`. Local
  artifacts are not rolled back after a channel has already been published.
- Literature-only releases still publish R2 papers; they may skip GitHub
  downloads and the raw archive.
